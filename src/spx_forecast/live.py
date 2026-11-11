"""Helpers for the scheduled live-forecast run: exit codes and an append-only status history."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

OK, ERROR, WARNING, SUPPRESSED = 0, 1, 2, 3
HISTORY_COLUMNS = ["checked_at", "horizon", "timing_status", "status"]


def exit_code(statuses, refresh_results=None, warnings=()):
    """1 = hard error (raised by the caller); 3 = a horizon missed its issue window; 2 = a refresh failed
    or overlapping history changed, or any other warning (e.g. calendar nearly out); 0 = ran cleanly (pending/waiting statuses are normal between contracts)."""
    if any(str(s).startswith("suppressed") for s in list(statuses.status) + list(statuses.timing_status)):
        return SUPPRESSED
    if warnings:
        return WARNING
    for report in (refresh_results or {}).values():
        if report.get("status") != "ok" or report.get("overlapping_mismatches", 0):
            return WARNING
    return OK


def append_history(path, statuses):
    path = Path(path)
    frame = statuses[HISTORY_COLUMNS]
    frame.to_csv(path, mode="a", header=not path.exists(), index=False)


def load_selected(output):
    """Selected model per horizon from the last completed backtest; the live run never re-selects."""
    path = Path(output) / "run_metadata.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run the notebook backtest once to select models")
    selected = json.loads(path.read_text()).get("selected") or {}
    if not selected:
        raise ValueError("run_metadata.json has no selected model; complete the backtest first")
    return selected


# Columns are an interface for downstream strategies: append new ones at the end and bump SCHEMA_VERSION;
# never rename, reorder or remove existing ones.
SCHEMA_VERSION = "1"
DAILY_COLUMNS = ["as_of_date", "horizon", "forecast_status", "note", "reference_date", "expiration",
                 "settlement_type", "reference_close", "p10_level", "median_level", "p90_level",
                 "issued_at", "model_version", "p10_bps", "median_bps", "p90_bps", "schema_version"]


def settlement_time(expiration, settlement_type, timezone="America/New_York"):
    """When a contract's settlement value is fixed: the 09:30 open for SET AM, the 16:00 close otherwise."""
    offset = pd.Timedelta(hours=9, minutes=30) if settlement_type == "SET_AM" else pd.Timedelta(hours=16)
    return (pd.Timestamp(expiration).normalize() + offset).tz_localize(timezone).tz_convert("UTC")


def write_csv(frame, path):
    """Replace `path` in one step, so the viewer and the strategy never read a half-written file."""
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def daily_forecast_table(forecasts, statuses, as_of, issued_by=None):
    """Consumer-facing end-of-day table: every issued forecast whose contract has not expired, plus one
    explanatory row for each horizon that has none (so a missing forecast is never silent).

    `issued_by` is the run's clock: a replay of an earlier time must not list forecasts issued after it, and a
    contract that has already settled on its expiration day (SET AM at the open, SPXW PM at the close) is no
    longer listed. Without it the check is by date only."""
    as_of = pd.Timestamp(as_of).normalize()
    if issued_by is not None:
        clock = pd.Timestamp(issued_by)
        settles = [settlement_time(e, s) for e, s in zip(forecasts.expiration, forecasts.settlement_type)]
        forecasts = forecasts.loc[(pd.to_datetime(forecasts.actual_issue_time, utc=True) <= clock)
                                  & (pd.Series(settles, index=forecasts.index, dtype="datetime64[ns, UTC]") > clock)]
    rows = []
    for horizon in ("weekly", "monthly"):
        mine = forecasts.loc[forecasts.horizon.eq(horizon) & (pd.to_datetime(forecasts.expiration) >= as_of)]
        for record in mine.sort_values("expiration").itertuples():
            rows.append(dict(as_of_date=as_of.date(), horizon=horizon, forecast_status="issued", note="",
                             reference_date=record.reference_date, expiration=record.expiration,
                             settlement_type=record.settlement_type, reference_close=round(record.reference_close, 2),
                             p10_level=round(record.lower, 2), median_level=round(record.median, 2),
                             p90_level=round(record.upper, 2), issued_at=record.actual_issue_time,
                             model_version=record.model_version,
                             # Log-return basis points from the reference close, so a consumer entering at a
                             # different spot can apply the same distribution to its own price.
                             p10_bps=round(10000 * np.log(record.lower / record.reference_close), 1),
                             median_bps=round(10000 * np.log(record.median / record.reference_close), 1),
                             p90_bps=round(10000 * np.log(record.upper / record.reference_close), 1),
                             schema_version=SCHEMA_VERSION))
        if mine.empty:
            status = statuses.loc[statuses.horizon.eq(horizon), "status"]
            rows.append(dict(as_of_date=as_of.date(), horizon=horizon, forecast_status="no_forecast",
                             note=str(status.iloc[0]) if len(status) else "no status recorded",
                             schema_version=SCHEMA_VERSION))
    return pd.DataFrame(rows, columns=DAILY_COLUMNS)


def write_daily_forecast(output, forecasts, statuses, now, timezone="America/New_York"):
    """Write artifacts/daily/forecast_YYYY-MM-DD.csv (New York date) and artifacts/latest_forecast.csv.

    latest_forecast.csv only moves forward: a replay of an earlier day writes its dated file and leaves it alone."""
    day = pd.Timestamp(now).tz_convert(timezone).normalize().tz_localize(None)
    table = daily_forecast_table(forecasts, statuses, day, issued_by=now)
    folder = Path(output) / "daily"
    folder.mkdir(parents=True, exist_ok=True)
    dated = folder / f"forecast_{day.date()}.csv"
    write_csv(table, dated)
    latest = Path(output) / "latest_forecast.csv"
    if not latest_is_newer(latest, day):
        write_csv(table, latest)
    return dated, table


def latest_is_newer(path, day):
    """True when latest_forecast.csv already describes a later day than `day`."""
    if not path.exists():
        return False
    try:
        current = pd.to_datetime(pd.read_csv(path, usecols=["as_of_date"]).as_of_date, errors="coerce").max()
    except (ValueError, pd.errors.EmptyDataError):
        return False
    return pd.notna(current) and current > day


SCORECARD_COLUMNS = ["horizon", "reference_date", "expiration", "issued_at", "reference_close", "p10_level",
                     "median_level", "p90_level", "status", "actual_level", "actual_source", "inside_80",
                     "error_points", "error_bps", "interval_score_bps"]
OFFICIAL, CLOSE_PROXY = "official_settlement", "cboe_close_proxy"


def live_scorecard(forecasts, settlements, closes):
    """One row per issued forecast, scored once its settlement is known.

    The actual is the official Cboe settlement record when the data has it. A weekly (SPXW PM) contract
    settles to the index close, so until the official record is imported the Cboe SPX close for that day is used
    and labelled `cboe_close_proxy`. Monthly SET AM settles to the opening-based SOQ, never the close, so a
    monthly forecast stays `awaiting_settlement` until its official record exists.

    `closes` must be Cboe closes (date, value). If it carries a `source` column (the spliced live SPX series)
    only its Cboe rows are used, so a FRED value is never scored under the Cboe label; None means no proxy.
    """
    from .backtest import interval_score
    from .reference import CBOE_SOURCE
    rows = []
    official = settlements.assign(expiration=pd.to_datetime(settlements.expiration).dt.normalize())
    if closes is None:
        closes = pd.DataFrame({"date": pd.to_datetime([]), "value": []})
    if "source" in closes:
        closes = closes.loc[closes.source.eq(CBOE_SOURCE)]
    closes = closes.loc[closes.value > 0].set_index("date").value
    for f in forecasts.sort_values(["horizon", "expiration"]).itertuples():
        expiration = pd.Timestamp(f.expiration).normalize()
        record = dict(horizon=f.horizon, reference_date=f.reference_date, expiration=str(expiration.date()),
                      issued_at=f.actual_issue_time, reference_close=round(f.reference_close, 2),
                      p10_level=round(f.lower, 2), median_level=round(f.median, 2), p90_level=round(f.upper, 2),
                      status="awaiting_settlement", actual_level=None, actual_source="", inside_80=None,
                      error_points=None, error_bps=None, interval_score_bps=None)
        match = official.loc[official.horizon.eq(f.horizon) & official.expiration.eq(expiration)
                             & official.settlement_type.eq(f.settlement_type), "settlement"]
        actual, source = (float(match.iloc[-1]), OFFICIAL) if len(match) else (None, "")
        if actual is None and f.horizon == "weekly" and expiration in closes.index:
            actual, source = float(closes[expiration]), CLOSE_PROXY
        if actual is not None:
            bps = lambda level: 10000 * np.log(level / f.reference_close)
            record.update(status="scored", actual_level=round(actual, 2), actual_source=source,
                          inside_80=bool(f.lower <= actual <= f.upper),
                          error_points=round(actual - f.median, 2), error_bps=round(bps(actual) - bps(f.median), 1),
                          interval_score_bps=round(float(interval_score(bps(actual), bps(f.lower), bps(f.upper))), 1))
        rows.append(record)
    return pd.DataFrame(rows, columns=SCORECARD_COLUMNS)


def scorecard_summary(scorecard, baseline=None):
    """Per-horizon rollup. With only a few scored weeks the coverage interval is wide: read it that way."""
    from .backtest import wilson
    baseline = baseline or {}
    rows = []
    for horizon in ("weekly", "monthly"):
        mine = scorecard.loc[scorecard.horizon.eq(horizon)]
        scored = mine.loc[mine.status.eq("scored")]
        hits = int(scored.inside_80.astype(bool).sum()) if len(scored) else 0
        low, high = wilson(hits, len(scored))
        rows.append(dict(horizon=horizon, issued=len(mine), scored=len(scored), awaiting=len(mine) - len(scored),
                         inside_80=hits, coverage_80=round(hits / len(scored), 3) if len(scored) else None,
                         coverage_wilson_low=None if np.isnan(low) else round(low, 3),
                         coverage_wilson_high=None if np.isnan(high) else round(high, 3),
                         median_abs_error_points=round(float(scored.error_points.abs().median()), 2) if len(scored) else None,
                         mean_interval_score_bps=round(float(scored.interval_score_bps.mean()), 1) if len(scored) else None,
                         proxy_actuals=int(scored.actual_source.eq(CLOSE_PROXY).sum()) if len(scored) else 0,
                         backtest_coverage_80=baseline.get(horizon, (None, None))[0],
                         backtest_mean_interval_score_bps=baseline.get(horizon, (None, None))[1]))
    return pd.DataFrame(rows)


def backtest_baseline(output, selected):
    """(coverage, mean interval score) of each horizon's selected model on the backtest final test."""
    path = Path(output) / "metrics.csv"
    if not path.exists():
        return {}
    from .backtest import model_key
    metrics = pd.read_csv(path)
    baseline = {}
    for horizon, config in selected.items():
        row = metrics.loc[metrics.horizon.eq(horizon) & metrics.stage.eq("final_test") & metrics.regime.eq("all")
                          & metrics.model.eq(model_key(config))]
        if len(row):
            baseline[horizon] = (round(float(row.coverage.iloc[0]), 3), round(float(row.interval_score_bps.iloc[0]), 1))
    return baseline


def write_scorecard(output, forecasts, settlements, closes, selected=None):
    card = live_scorecard(forecasts, settlements, closes)
    write_csv(card, Path(output) / "live_scorecard.csv")
    summary = scorecard_summary(card, backtest_baseline(output, selected or {}))
    write_csv(summary, Path(output) / "live_scorecard_summary.csv")
    return card, summary
