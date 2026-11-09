"""Refresh price imports by merging new rows into the existing history files.

FRED serves only a rolling ~10 years of SP500, so a fresh download must never replace the file: existing
rows are kept verbatim, only dates after the file's last date are appended, and any overlapping value that
differs is counted and reported (never silently rewritten).
"""
import io
import os
from pathlib import Path

import pandas as pd
import requests

from .ingestion import URLS, official_url

SPECS = {
    "spx": dict(file="SP500.csv", date="observation_date", value="SP500", format=None),
    "vix": dict(file="VIX_History.csv", date="DATE", value="CLOSE", format="%m/%d/%Y"),
    "vix9d": dict(file="VIX9D_History.csv", date="DATE", value="CLOSE", format="%m/%d/%Y"),
}


def _read(text):
    frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    frame.columns = [c.strip() for c in frame.columns]
    return frame


def _number(value):
    value = str(value).strip()
    return None if value in ("", ".") else float(value)


def merge_history(existing_text, new_text, date_column, value_column, date_format=None):
    """Return (merged_csv_text, report). Existing rows win; only later dates are appended."""
    existing, new = _read(existing_text), _read(new_text)
    if set(existing.columns) != set(new.columns) or date_column not in existing or value_column not in existing:
        raise ValueError(f"Unexpected columns: expected {list(existing.columns)}, got {list(new.columns)}")
    for frame in (existing, new):
        frame["__date"] = pd.to_datetime(frame[date_column], format=date_format)
        if frame.__date.duplicated().any():
            raise ValueError("Duplicate dates in price history")
    new[value_column] = new[value_column].replace(".", "")
    last = existing.__date.max()
    overlap = existing.merge(new, on="__date", suffixes=("", "_new"))
    mismatches = sum(_number(a) != _number(b) and not (
        _number(a) is not None and _number(b) is not None and abs(_number(a) - _number(b)) < 1e-9)
        for a, b in zip(overlap[value_column], overlap[value_column + "_new"]))
    appended = new.loc[new.__date > last]
    merged = pd.concat([existing, appended], ignore_index=True).sort_values("__date")
    text = merged.drop(columns="__date").to_csv(index=False, lineterminator="\n")
    report = dict(appended_rows=len(appended), overlapping_mismatches=int(mismatches),
                  last_date=str(merged.__date.max().date()), previous_last_date=str(last.date()))
    return text, report


def refresh_prices(imports, session=None, timeout=15):
    """Download each official price series and merge it into data/imports. Per-series failures are reported."""
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "spx-forecast/0.1 research notebook")
    results = {}
    for name, spec in SPECS.items():
        path = Path(imports) / spec["file"]
        try:
            url = URLS[name]
            if not official_url(url):
                raise ValueError("Only FRED and Cboe market sources are permitted")
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            if not official_url(response.url):
                raise ValueError("Redirect left permitted source hosts")
            existing = path.read_bytes().decode("utf-8")
            text, report = merge_history(existing, response.content.decode("utf-8"),
                                         spec["date"], spec["value"], spec["format"])
            if report["appended_rows"]:
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(text.encode("utf-8"))
                os.replace(temporary, path)
            results[name] = dict(status="ok", **report)
        except (requests.RequestException, ValueError, OSError, KeyError) as exc:
            results[name] = dict(status="failed", error=str(exc)[:200])
    return results
