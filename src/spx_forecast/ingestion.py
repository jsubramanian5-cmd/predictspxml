"""Bounded downloads, immutable raw snapshots, explicit provenance and imports."""
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests
from lxml import html
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .reference import CBOE_SOURCE, CBOE_SPX_URL, RECONCILIATION_COLUMNS, reconcile, splice_cboe_tail

URLS = {
    "spx": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500",
    "vix": "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
    "vix9d": "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX9D_History.csv",
    "weekly": "https://www.cboe.com/index_settlement_values/weeklys_settlement_values/",
    "monthly": "https://www.cboe.com/index_settlement_values/",
    "specifications": "https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications/",
    "holidays": "https://www.cboe.com/about/hours/us-options/",
    "calendar": "https://cdn.cboe.com/resources/options/Cboe2026OPTIONSCalendar.pdf",
}


PRICE_IMPORTS = {"spx": "SP500.csv", "vix": "VIX_History.csv",
                 "vix9d": "VIX9D_History.csv"}


def official_url(url):
    host = urlparse(str(url)).hostname or ""
    return host == "cboe.com" or host.endswith(".cboe.com") or host == "fred.stlouisfed.org"


def cboe_url(url):
    host = urlparse(str(url)).hostname or ""
    return host == "cboe.com" or host.endswith(".cboe.com")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def portable_path(path):
    """Record repo-relative POSIX paths so manifests do not depend on the checkout location."""
    path = Path(path)
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


class Store:
    def __init__(self, config):
        self.config = config
        self.manifest = []
        self.session = requests.Session()
        retry = Retry(total=config.retries, connect=config.retries, read=config.retries,
                      backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers["User-Agent"] = "spx-forecast/0.1 research notebook"

    def fetch(self, name, url):
        if not official_url(url):
            raise ValueError("Only FRED and Cboe market sources are permitted")
        pointer = self.config.raw / f"{name}.latest.json"
        if pointer.exists() and not self.config.refresh:
            meta = json.loads(pointer.read_text())
            data = (self.config.raw / meta["snapshot"]).read_bytes()
            if digest(data) != meta["sha256"]:
                raise ValueError(f"Corrupt cached snapshot: {name}")
        else:
            response = self.session.get(url, timeout=self.config.timeout_seconds)
            response.raise_for_status()
            if not official_url(response.url):
                raise ValueError("Redirect left permitted source hosts")
            data = response.content
            stamp = datetime.now(timezone.utc).isoformat()
            sha = digest(data)
            snapshot = f"{name}-{sha}.raw"
            (self.config.raw / snapshot).write_bytes(data)
            meta = dict(dataset=name, source_url=url, resolved_url=response.url,
                        retrieved_at=stamp, published_at=None,
                        http_last_modified=response.headers.get("Last-Modified"),
                        sha256=sha, snapshot=snapshot)
            # Last-Modified is a file timestamp, not an observation publication time.
            pointer.write_text(json.dumps(meta, indent=2))
        self.manifest.append(meta)
        return data, meta

    def downloaded_prices(self, name, path, url=None):
        """Snapshot a user-supplied download; receipt is not publication time."""
        data = path.read_bytes()
        sha = digest(data)
        snapshot = f"{name}-{sha}.raw"
        (self.config.raw / snapshot).write_bytes(data)
        receipt = self.config.raw / f"{name}-{sha}.manual.json"
        if receipt.exists():
            meta = json.loads(receipt.read_text())
        else:
            meta = dict(dataset=name, source_url=url or URLS[name], sha256=sha,
                        snapshot=snapshot, import_path=portable_path(path),
                        source_method="user_supplied_download",
                        provenance="User-supplied file attributed to official download URL",
                        retrieved_at=datetime.now(timezone.utc).isoformat(), published_at=None)
            receipt.write_text(json.dumps(meta, indent=2))
        self.manifest.append(meta)
        return data, meta

    def imported(self, name, path):
        data = path.read_bytes()
        sha = digest(data)
        snapshot = f"{name}-{sha}.raw"
        (self.config.raw / snapshot).write_bytes(data)
        frame = pd.read_csv(io.BytesIO(data))
        if "source_url" not in frame or not frame.source_url.map(official_url).all():
            raise ValueError(f"{name}: each imported row needs an official source_url")
        retrieved = datetime.now(timezone.utc).isoformat()
        self.manifest.append(dict(dataset=name, source_url=portable_path(path),
                                  official_urls=sorted(frame.source_url.unique()),
                                  retrieved_at=retrieved,
                                  published_at=None, sha256=sha, snapshot=snapshot))
        frame['retrieved_at'] = retrieved
        frame['sha256'] = sha
        frame['source_published_at'] = None
        return frame


def prices(data, meta, name):
    frame = pd.read_csv(io.BytesIO(data), na_values=["."])
    frame.columns = frame.columns.str.strip().str.lower()
    date_column = next(c for c in frame if c in ("date", "observation_date"))
    value_column = "sp500" if name == "spx" else "close"
    if value_column not in frame:
        value_column = name  # Some Cboe historical series have DATE,VIX9D.
    result = pd.DataFrame({"date": pd.to_datetime(frame[date_column]).dt.normalize(),
                           "value": pd.to_numeric(frame[value_column], errors="coerce")})
    result["published_at"] = pd.NaT
    for field in ("source_url", "retrieved_at", "sha256"):
        result[field] = meta[field]
    return result


def weekly_table(data, meta):
    """Use only explicitly dated SPXW records, never a close-price proxy."""
    tables = pd.read_html(io.StringIO(data.decode("utf-8")))
    found = []
    for table in tables:
        table.columns = [str(c).strip().lower() for c in table.columns]
        if not {"trading symbol", "expiration date", "settlement value"} <= set(table):
            continue
        table = table.loc[table["trading symbol"].astype(str).str.strip().eq("SPXW")].copy()
        found.append(pd.DataFrame({"expiration": pd.to_datetime(table["expiration date"]),
                                   "settlement": pd.to_numeric(table["settlement value"], errors="coerce"),
                                   "source_value": table["settlement value"].astype(str),
                                   "horizon": "weekly", "settlement_type": "SPXW_PM"}))
    if not found:
        raise ValueError("No explicitly dated SPXW settlement table found")
    result = pd.concat(found, ignore_index=True)
    result["published_at"] = pd.NaT
    for key in ("source_url", "retrieved_at", "sha256"):
        result[key] = meta[key]
    return result


def weekly_archive(path, store):
    """Read dated SPXW records without inferring end-of-week eligibility."""
    archive_data, archive_meta = store.downloaded_prices("weekly", path)
    frames, profile, review = [], [], []
    with ZipFile(io.BytesIO(archive_data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for entry in manifest["years"]:
            year = int(entry["year"])
            if year < pd.Timestamp(store.config.start).year or year > pd.Timestamp(store.config.end).year:
                continue
            source = entry["source"]
            parsed_url = urlparse(source)
            if not cboe_url(source) or parsed_url.path != f"/index_settlement_values/get_sv_data/W/{year}/":
                raise ValueError("Unexpected weekly archive source URL")
            raw = archive.read(entry["file"])
            member_hash = digest(raw)
            snapshot = f"weekly_member-{member_hash}.raw"
            (store.config.raw / snapshot).write_bytes(raw)
            meta = dict(dataset=f"weekly_{year}", source_url=source, sha256=member_hash,
                        snapshot=snapshot, retrieved_at=archive_meta["retrieved_at"],
                        archive_sha256=archive_meta["sha256"], archive_member=entry["file"],
                        source_method="user_supplied_download", published_at=None)
            store.manifest.append(meta)
            payload = json.loads(raw)["data"]
            if isinstance(payload, str):
                frame = weekly_table(payload.encode("utf-8"), meta)
            elif isinstance(payload, list):
                table = pd.DataFrame(payload)
                table = table.loc[table.trading_symbol.astype(str).str.strip().eq("SPXW")]
                frame = pd.DataFrame({"expiration": pd.to_datetime(table.expiration_date),
                                      "settlement": pd.to_numeric(table.settlement_value),
                                      "horizon": "weekly", "settlement_type": "SPXW_PM"})
                frame["published_at"] = pd.NaT
                for field in ("source_url", "retrieved_at", "sha256"):
                    frame[field] = meta[field]
            else:
                raise ValueError("Unsupported weekly archive payload")
            if not frame.expiration.dt.year.eq(year).all():
                raise ValueError("Archive expiration does not match source year")
            unresolved = frame.loc[frame.settlement.isna()].copy()
            if len(unresolved):
                unresolved["reason"] = "Nonnumeric or annotated source value requires review"
                review.append(unresolved)
                frame = frame.loc[frame.settlement.notna()].copy()
            profile.append(dict(year=year, rows=len(frame), start=frame.expiration.min(),
                                end=frame.expiration.max(), excluded_for_review=len(unresolved), source_url=source))
            frames.append(frame)
    if not frames:
        raise ValueError("No weekly archive years in configured range")
    result = merge_settlements(frames)
    result = result.loc[result.expiration.between(pd.Timestamp(store.config.start),
                                                 pd.Timestamp(store.config.end))].reset_index(drop=True)
    pd.DataFrame(profile).to_csv(store.config.output / "weekly_archive_coverage.csv", index=False)
    (pd.concat(review, ignore_index=True) if review else pd.DataFrame(
        columns=["expiration", "source_value", "source_url", "reason"])).to_csv(
            store.config.output / "weekly_archive_review.csv", index=False)
    return result


def monthly_archive(path, store):
    """Parse raw SET sources; month headings alone never establish expiration dates."""
    data, archive_meta = store.downloaded_prices("monthly", path)
    dated, unresolved, profile = [], [], []
    with ZipFile(io.BytesIO(data)) as archive:
        references = json.loads(archive.read("monthly-set-am-history.json"))["records"]
        years = sorted({int(record["source_archive_year"]) for record in references})
        for year in years:
            if not pd.Timestamp(store.config.start).year <= year <= pd.Timestamp(store.config.end).year:
                continue
            source = f"https://www.cboe.com/index_settlement_values/get_sv_data/S/{year}/"
            if any(record["source"] != source for record in references
                   if int(record["source_archive_year"]) == year):
                raise ValueError("Unexpected monthly archive source URL")
            member = f"source/{year}.json"
            raw = archive.read(member)
            sha = digest(raw)
            snapshot = f"monthly_member-{sha}.raw"
            (store.config.raw / snapshot).write_bytes(raw)
            meta = dict(dataset=f"monthly_{year}", source_url=source, sha256=sha,
                        snapshot=snapshot, retrieved_at=archive_meta["retrieved_at"],
                        archive_sha256=archive_meta["sha256"], archive_member=member,
                        source_method="user_supplied_download", published_at=None)
            store.manifest.append(meta)
            payload = json.loads(raw)["data"]
            year_dated, year_pending = [], []
            if isinstance(payload, list):
                for row in payload:
                    if str(row.get("trading_symbol", "")).strip() != "SPX" or str(
                            row.get("settlement_symbol", "")).strip() != "SET":
                        continue
                    expiration = pd.Timestamp(row["expiration_date"])
                    if pd.isna(expiration) or expiration.year != year:
                        raise ValueError("Monthly expiration does not match source year")
                    year_dated.append(dict(expiration=expiration,
                                           settlement=pd.to_numeric(row["settlement_value"])))
            elif isinstance(payload, str):
                document = html.fromstring(payload)
                for heading in document.xpath("//h4"):
                    title = " ".join(heading.text_content().split())
                    month = pd.to_datetime(title, format="%B %Y Settlement Values").to_period("M")
                    if month.year != year:
                        raise ValueError("Monthly heading does not match archive year")
                    tables = heading.xpath("following-sibling::table[1]")
                    if not tables:
                        raise ValueError("Monthly heading has no following settlement table")
                    for row in tables[0].xpath(".//tr"):
                        cells = [" ".join(cell.text_content().split()) for cell in row.xpath("./td")]
                        if len(cells) >= 2 and cells[0] == "S&P 500 (SET)":
                            year_pending.append(dict(month=str(month), source_value=cells[1],
                                                     settlement=pd.to_numeric(cells[1], errors="coerce"),
                                                     reason="Exact expiration date absent from source"))
            else:
                raise ValueError("Unsupported monthly archive payload")
            common = {key: meta[key] for key in ("source_url", "retrieved_at", "sha256")}
            dated.extend(dict(**row, **common, horizon="monthly", settlement_type="SET_AM",
                              published_at=pd.NaT) for row in year_dated)
            unresolved.extend(dict(**row, **common) for row in year_pending)
            profile.append(dict(year=year, dated_records=len(year_dated),
                                month_only_records=len(year_pending), source_url=source))
    frame = pd.DataFrame(dated, columns=["expiration", "settlement", "source_url", "retrieved_at",
                                         "sha256", "horizon", "settlement_type", "published_at"])
    frame = merge_settlements([frame]) if len(frame) else frame
    frame["expiration"] = pd.to_datetime(frame.expiration)
    frame = frame.loc[frame.expiration.between(pd.Timestamp(store.config.start), pd.Timestamp(store.config.end))]
    pending = pd.DataFrame(unresolved, columns=["month", "source_value", "settlement", "reason",
                                               "source_url", "retrieved_at", "sha256"])
    pending.to_csv(store.config.output / "monthly_archive_undated.csv", index=False)
    pd.DataFrame(profile).to_csv(store.config.output / "monthly_archive_coverage.csv", index=False)
    return frame.reset_index(drop=True), pending


def resolve_monthly_dates(dated, pending, contracts, calendar):
    """Join month-only SET values to independently verified standard AM dates."""
    if pending.empty:
        return dated, pending, pd.DataFrame()
    eligible = contracts.eligible.astype(str).str.lower().map(
        {'true': True, 'false': False, '1': True, '0': False})
    if eligible.isna().any():
        raise ValueError('Monthly date resolution requires explicit contract eligibility')
    mapping = contracts.loc[contracts.horizon.eq('monthly') & eligible].copy()
    if mapping.empty:
        return dated, pending, pd.DataFrame()
    if not mapping.settlement_type.eq('SET_AM').all() or not mapping.source_url.map(cboe_url).all():
        raise ValueError('Monthly date resolution requires official standard AM contracts')
    mapping['expiration'] = pd.to_datetime(mapping.expiration).dt.normalize()
    mapping['month'] = pd.to_datetime(mapping.nominal_expiration).dt.to_period('M').astype(str)
    if not mapping.expiration.dt.to_period('M').astype(str).eq(mapping.month).all():
        raise ValueError('Monthly contract expiration must match its nominal month')
    if mapping.month.duplicated().any():
        raise ValueError('Ambiguous monthly contract date mapping')
    sessions = pd.to_datetime(calendar.loc[
        calendar.is_session.astype(str).str.lower().isin(['true', '1']), 'date'])
    if not mapping.expiration.isin(sessions).all():
        raise ValueError('Monthly date mapping must identify verified regular sessions')
    mapping['date_source_url'] = mapping.source_url
    mapping['date_source_import_sha256'] = mapping.get('sha256', pd.NA)
    mapping['date_source_pdf_sha256'] = mapping.get('annual_pdf_sha256', pd.NA)
    columns = ['month', 'expiration', 'date_source_url', 'date_source_import_sha256', 'date_source_pdf_sha256']
    joined = pending.merge(mapping[columns], on='month', how='left', validate='many_to_one')
    values = pd.to_numeric(joined.settlement, errors='coerce')
    usable = joined.expiration.notna() & np.isfinite(values) & values.gt(0)
    resolved = joined.loc[usable].copy()
    resolved['horizon'] = 'monthly'
    resolved['settlement_type'] = 'SET_AM'
    resolved['published_at'] = pd.NaT
    resolved['date_resolution'] = 'verified_standard_AM_contract_and_calendar'
    resolved = resolved.drop(columns='reason')
    unresolved = joined.loc[~usable, pending.columns].copy()
    combined = merge_settlements([frame for frame in (dated, resolved) if not frame.empty]) if (
        not dated.empty or not resolved.empty) else dated
    return combined, unresolved, resolved


def validate_settlements(frame):
    required = {"horizon", "expiration", "settlement_type", "settlement", "source_url"}
    if not required <= set(frame):
        raise ValueError(f"Settlements missing columns: {required - set(frame)}")
    frame = frame.copy()
    frame["expiration"] = pd.to_datetime(frame.expiration).dt.normalize()
    frame["settlement"] = pd.to_numeric(frame.settlement, errors="raise")
    good_type = frame.apply(lambda r: (r.horizon, r.settlement_type) in
                            [("weekly", "SPXW_PM"), ("monthly", "SET_AM")], axis=1)
    if not good_type.all() or not (np.isfinite(frame.settlement) & frame.settlement.gt(0)).all():
        raise ValueError("Settlement labels need positive official SPXW_PM or SET_AM values")
    if not frame.source_url.map(cboe_url).all():
        raise ValueError("Settlement provenance must be official")
    if frame.duplicated(["horizon", "expiration"]).any():
        raise ValueError("Duplicate settlement keys; reconcile sources before importing")
    if "published_at" not in frame:
        frame["published_at"] = pd.NaT
    frame["published_at"] = pd.to_datetime(frame.published_at, utc=True)
    return frame


def merge_settlements(frames):
    """Reconcile equivalent official records before enforcing unique settlement keys.

    Web parsers produce Timestamp/float columns while CSV imports can contain strings.
    Normalize those representations before comparing records; retain a verified known
    publication time in preference to an unknown time for the same official value.
    Conflicting values still fail validation and require source reconciliation.
    """
    merged = pd.concat(frames, ignore_index=True)
    merged['expiration'] = pd.to_datetime(merged.expiration, format='mixed').dt.normalize()
    merged['settlement'] = pd.to_numeric(merged.settlement, errors='raise')
    merged['published_at'] = pd.to_datetime(merged.get('published_at', pd.NaT), utc=True)
    merged = merged.sort_values('published_at', na_position='last').drop_duplicates(
        ['horizon', 'expiration', 'settlement_type', 'settlement'])
    return validate_settlements(merged)


def collect(config):
    config.prepare()
    store, datasets, issues = Store(config), {}, []
    archive = config.imports / "cboe-weeklys-settlement-values-all-years.zip"
    if archive.exists():
        # Malformed supplied archives fail visibly instead of falling back to network.
        datasets["settlements_archive"] = weekly_archive(archive, store)
    pending = pd.DataFrame()
    monthly = config.imports / "cboe-monthly-set-am-history.zip"
    if monthly.exists():
        datasets["settlements_monthly_archive"], pending = monthly_archive(monthly, store)
    for name, url in URLS.items():
        if name == "weekly" and "settlements_archive" in datasets:
            continue
        if name == "monthly" and "settlements_monthly_archive" in datasets:
            continue
        try:
            local = config.imports / PRICE_IMPORTS[name] if name in PRICE_IMPORTS else None
            if local is not None and local.exists():
                data, meta = store.downloaded_prices(name, local)
            else:
                data, meta = store.fetch(name, url)
            if name in ("spx", "vix", "vix9d"):
                datasets[name] = prices(data, meta, name)
                datasets[name] = datasets[name].loc[datasets[name].date.between(pd.Timestamp(config.start), pd.Timestamp(config.end))].reset_index(drop=True)
            elif name == "weekly":
                datasets["settlements_web"] = weekly_table(data, meta)
            elif name == "monthly":
                issues.append({"dataset": name, "status": "pending",
                               "reason": "SET page snapshot retained; exact historical settlement dates require verified CSV"})
        except (requests.RequestException, ValueError, KeyError, StopIteration) as exc:
            issues.append({"dataset": name, "status": "pending", "reason": str(exc)[:260]})
            if not any(m["dataset"] == name for m in store.manifest):
                store.manifest.append(dict(dataset=name, source_url=url, status="failed",
                                           attempted_at=datetime.now(timezone.utc).isoformat(),
                                           error=str(exc)[:260], sha256=None))
    cboe_path = config.imports / "SPX_History.csv"
    reconciliation = pd.DataFrame(columns=RECONCILIATION_COLUMNS)
    if config.live_cboe_spx and "spx" in datasets and cboe_path.exists():
        cboe_data, cboe_meta = store.downloaded_prices("spx_cboe", cboe_path, url=CBOE_SPX_URL)
        cboe = prices(cboe_data, cboe_meta, "spx")
        cboe = cboe.loc[cboe.date.between(pd.Timestamp(config.start), pd.Timestamp(config.end))].reset_index(drop=True)
        reconciliation = reconcile(datasets["spx"], cboe)
        datasets["spx"] = splice_cboe_tail(datasets["spx"], cboe)
        added = int(datasets["spx"].source.eq(CBOE_SOURCE).sum())
        issues.append({"dataset": "spx_reference", "status": "ok",
                       "reason": f"{added} SPX close(s) after FRED's last observation come from Cboe SPX_History.csv"})
    reconciliation.to_csv(config.output / "spx_reconciliation.csv", index=False)
    for name in ("calendar", "contracts", "upcoming_contracts", "settlements", "publications"):
        path = config.imports / f"{name}.csv"
        if path.exists():
            # Invalid imports are errors, not silent pending/fallback states.
            datasets[name] = store.imported(name + "_import", path)
    if not pending.empty and 'contracts' in datasets and 'calendar' in datasets:
        datasets['settlements_monthly_archive'], pending, resolved = resolve_monthly_dates(
            datasets['settlements_monthly_archive'], pending, datasets['contracts'], datasets['calendar'])
        resolved.to_csv(config.output / 'monthly_archive_date_resolutions.csv', index=False)
        pending.to_csv(config.output / 'monthly_archive_undated.csv', index=False)
    if len(pending):
        issues.append(dict(dataset='monthly_archive', status='pending',
                           reason=f'{len(pending)} month-only SET values lack verified dates or usable values'))
    labels = [datasets[k] for k in ("settlements_web", "settlements_archive", "settlements_monthly_archive", "settlements")
              if k in datasets and not datasets[k].empty]
    if labels:
        datasets["settlements"] = merge_settlements(labels)
    for name, frame in datasets.items():
        frame.to_parquet(config.normalized / f"{name}.parquet", index=False)
    pd.DataFrame(store.manifest).to_json(config.output / "source_manifest.json", orient="records", indent=2)
    return datasets, pd.DataFrame(issues, columns=["dataset", "status", "reason"]), store.manifest


def audit(datasets, sessions=None, observations=None):
    rows = []
    for name in ("spx", "vix", "vix9d", "settlements", "calendar", "contracts"):
        if name not in datasets:
            rows.append(dict(dataset=name, rows=0, missing_sessions=pd.NA,
                             start=pd.NaT, end=pd.NaT, duplicate_records=pd.NA,
                             valid_settlements=0 if name == "settlements" else pd.NA,
                             status="unavailable"))
    for name, frame in datasets.items():
        date_col = next((c for c in ("date", "expiration") if c in frame), None)
        dates = pd.to_datetime(frame[date_col]) if date_col else pd.Series(dtype="datetime64[ns]")
        missing = pd.NA
        if name in ("spx", "vix", "vix9d") and sessions is not None and len(dates):
            expected = sessions[(sessions >= dates.min()) & (sessions <= dates.max())]
            valid_dates = pd.DatetimeIndex(frame.loc[frame.value.gt(0), "date"])
            missing = len(expected.difference(valid_dates))
        keys = [c for c in ("horizon", date_col) if c and c in frame]
        rows.append(dict(dataset=name, rows=len(frame), start=dates.min(), end=dates.max(),
                         missing_sessions=missing, duplicate_records=int(frame.duplicated(keys).sum()) if keys else 0,
                         valid_settlements=int(frame.settlement.gt(0).sum()) if "settlement" in frame else pd.NA,
                         usable_forecasts=pd.NA))
    for horizon in ("weekly", "monthly"):
        obs = observations.get(horizon, pd.DataFrame()) if observations else pd.DataFrame()
        rows.append(dict(dataset=horizon + " observations", rows=len(obs),
                         usable_forecasts=int(obs.target.notna().sum()) if "target" in obs else 0))
    return pd.DataFrame(rows)
