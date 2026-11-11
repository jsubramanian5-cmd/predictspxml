import hashlib
import json
import os
import pickle

import pandas as pd

from . import __version__
from .backtest import training_rows
from .calendar import availability
from .features import FEATURES, input_times, to_levels
from .models import QuantileModel

FORECAST_COLUMNS = ["horizon", "reference_date", "actual_issue_time", "expiration", "settlement_type",
                    "reference_close", "lower", "median", "upper", "model_version", "timing_status"]


def publish_forecast(horizon, observations, selected, config, datasets, calendar, manifest, now):
    """Single check and immutable per-contract publication; reruns retain actual issue time."""
    desired = pd.Timestamp(config.reference_date)
    rows = observations.loc[observations.reference_date.eq(desired)]
    if rows.empty:
        missing = [name for name in ("spx", "vix", "vix9d") if name not in datasets or
                   not pd.to_datetime(datasets[name].date).eq(desired).any()]
        detail = f"; no {', '.join(missing)} value for {desired.date()} yet" if missing else ""
        return None, f"pending: no eligible contract with complete features for reference_date {desired.date()}{detail}"
    if len(rows) != 1:
        raise ValueError("Ambiguous forecast reference")
    row = rows.iloc[0]
    path = config.output / "forecasts.csv"
    existing = read_forecasts(path)
    match =(existing.horizon.eq(horizon) & existing.reference_date.eq(str(desired.date())) &
             existing.expiration.eq(str(row.expiration.date())))
    if match.any():
        return existing.loc[match].iloc[0].to_dict(), "already_published"
    complete, assumed, times = input_times(row.reference_date, datasets, calendar, live=True)
    status, ready = availability(row.reference_date, times, complete, now, calendar)
    if status != "ready":
        return None, status
    if pd.Timestamp(now) >= calendar.time(row.expiration, 0):
        return None, "suppressed_expiration_reached"
    train = training_rows(observations, pd.Timestamp(now))
    model = QuantileModel(selected, config.seed).fit(train)
    q, crossing = model.predict(rows)
    levels = to_levels(row.reference_close, q[0])
    metadata = dict(package_version=__version__, feature_order=FEATURES, configuration=selected,
                    training_cutoff=train.label_available_at.max(), training_count=len(train),
                    source_hashes=[m["sha256"] for m in manifest],
                    seed=config.seed, dependency_versions=dependency_versions(),
                    issue_time=pd.Timestamp(now), reference_date=desired,
                    quantile_crossed=bool(crossing[0]), input_ready_at=ready,
                    contract_basis=str(row.get('listing_basis', 'settlement_record')),
                    reference_close_source=reference_source(datasets, row.reference_date))
    encoded = json.dumps(metadata, sort_keys=True, default=str)
    version = __version__ + "+" + hashlib.sha256(encoded.encode()).hexdigest()[:12]
    model_dir = config.output / "models"
    model_dir.mkdir(exist_ok=True)
    # Artifacts are local trusted Python objects; never unpickle untrusted files.
    (model_dir / f"{horizon}-{version}.pkl").write_bytes(pickle.dumps(model))
    (model_dir / f"{horizon}-{version}.json").write_text(encoded)
    record = dict(horizon=horizon, reference_date=str(desired.date()), actual_issue_time=pd.Timestamp(now).isoformat(),
                  expiration=str(row.expiration.date()), settlement_type=row.settlement_type,
                  reference_close=row.reference_close, lower=levels[0], median=levels[1], upper=levels[2],
                  model_version=version, timing_status="receipt_verified_publication_unknown" if assumed else "publication_verified")
    append_forecast(path, record)
    return record, "published"


def read_forecasts(path):
    """Issued forecasts with exactly the float values that were written (the default CSV parser is not exact)."""
    if not path.exists() or not path.stat().st_size:
        return pd.DataFrame(columns=FORECAST_COLUMNS)
    return pd.read_csv(path, float_precision="round_trip")


def append_forecast(path, record):
    """Add one issued row, leaving the bytes of every earlier row untouched.

    Re-serializing the whole table would pass issued values through a CSV read/write cycle, which can change
    their last digit. The new file is built beside the old one and swapped in, so a reader never sees half a row.
    """
    previous = path.read_bytes() if path.exists() else b""
    if previous:
        columns = list(pd.read_csv(path, nrows=0).columns)
        if set(record) - set(columns):
            raise ValueError(f"forecasts.csv lacks columns {sorted(set(record) - set(columns))}")
        newline = "\r\n" if b"\r\n" in previous else "\n"
        if not previous.endswith(b"\n"):
            previous += newline.encode()
    else:
        columns, newline = FORECAST_COLUMNS, os.linesep
    row = pd.DataFrame([record]).reindex(columns=columns).to_csv(index=False, header=not previous, lineterminator=newline)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(previous + row.encode("utf-8"))
    temporary.replace(path)


def reference_source(datasets, reference_date):
    """Which SPX series supplied the reference close ('fred' or 'cboe_spx_history')."""
    spx = datasets.get("spx")
    if spx is None or "source" not in spx:
        return "fred"
    match = spx.loc[spx.date.eq(pd.Timestamp(reference_date)), "source"]
    return str(match.iloc[0]) if len(match) else "unknown"


def dependency_versions():
    import importlib.metadata as metadata
    versions = {}
    for package in ["numpy", "pandas", "pyarrow", "scikit-learn", "xgboost", "matplotlib",
                    "jupyterlab", "nbformat", "nbclient", "nbconvert", "requests", "ipykernel"]:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "MISSING"
    return versions
