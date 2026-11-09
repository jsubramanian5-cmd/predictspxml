"""Helpers for the scheduled live-forecast run: exit codes and an append-only status history."""
import json
from pathlib import Path

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


DAILY_COLUMNS = ["as_of_date", "horizon", "forecast_status", "note", "reference_date", "expiration",
                 "settlement_type", "reference_close", "p10_level", "median_level", "p90_level",
                 "issued_at", "model_version"]


def daily_forecast_table(forecasts, statuses, as_of):
    """Consumer-facing end-of-day table: every issued forecast whose contract has not expired, plus one
    explanatory row for each horizon that has none (so a missing forecast is never silent)."""
    as_of = pd.Timestamp(as_of).normalize()
    rows = []
    for horizon in ("weekly", "monthly"):
        mine = forecasts.loc[forecasts.horizon.eq(horizon) & (pd.to_datetime(forecasts.expiration) >= as_of)]
        for record in mine.sort_values("expiration").itertuples():
            rows.append(dict(as_of_date=as_of.date(), horizon=horizon, forecast_status="issued", note="",
                             reference_date=record.reference_date, expiration=record.expiration,
                             settlement_type=record.settlement_type, reference_close=round(record.reference_close, 2),
                             p10_level=round(record.lower, 2), median_level=round(record.median, 2),
                             p90_level=round(record.upper, 2), issued_at=record.actual_issue_time,
                             model_version=record.model_version))
        if mine.empty:
            status = statuses.loc[statuses.horizon.eq(horizon), "status"]
            rows.append(dict(as_of_date=as_of.date(), horizon=horizon, forecast_status="no_forecast",
                             note=str(status.iloc[0]) if len(status) else "no status recorded"))
    return pd.DataFrame(rows, columns=DAILY_COLUMNS)


def write_daily_forecast(output, forecasts, statuses, now, timezone="America/New_York"):
    """Write artifacts/daily/forecast_YYYY-MM-DD.csv (New York date) and artifacts/latest_forecast.csv."""
    day = pd.Timestamp(now).tz_convert(timezone).normalize().tz_localize(None)
    table = daily_forecast_table(forecasts, statuses, day)
    folder = Path(output) / "daily"
    folder.mkdir(parents=True, exist_ok=True)
    dated = folder / f"forecast_{day.date()}.csv"
    table.to_csv(dated, index=False)
    table.to_csv(Path(output) / "latest_forecast.csv", index=False)
    return dated, table
