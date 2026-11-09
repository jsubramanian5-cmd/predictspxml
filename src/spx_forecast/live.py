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
