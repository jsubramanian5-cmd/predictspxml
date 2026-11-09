# Live forecast run

`scripts/run_live_forecast.py` is the scheduled entry point. Run it from the repository root:

```powershell
$env:PYTHONPATH = "src"   # unless the package is installed
python -m scripts.run_live_forecast            # normal run
python -m scripts.run_live_forecast --skip-refresh --now 2026-11-09T00:46:00Z   # replay without downloads
```

## Order (each step depends on the one before)

1. **Refresh prices.** FRED SP500, Cboe VIX/VIX9D and Cboe `SPX_History.csv` are downloaded and *merged*
   into `data/imports/`. Existing rows are never rewritten (FRED serves only ~10 years); overlapping values
   that differ are counted and reported.
2. **Rebuild upcoming contracts** (`upcoming_contracts.csv`) from the pinned calendar, as of today.
   It depends on `contracts.csv`, so refresh that first whenever a new settlement archive is imported
   (`python -m scripts.build_contract_import`).
3. **Load** all imports (`workflow.prepare` with `end` = today, `live_cboe_spx=True`). The SPX series is FRED
   plus Cboe closes for dates **after FRED's last observation** (see "Reference close source").
4. **Forecast once** with the models selected by the last backtest (`artifacts/run_metadata.json`);
   this run never re-tunes or re-selects. Each horizon forecasts its next contract whose 08:00 issue
   cutoff has not passed. Results are appended to `artifacts/forecast_status_history.csv`.

Steps 1-2 only need to be repeated, not changed, between runs; a rerun never overwrites an issued forecast.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Ran cleanly. `pending`/`waiting_for_inputs`/`not_yet_eligible` are normal between contracts. |
| 1 | Error (details in `.runtime/live_forecast.log`). Includes a verified calendar that has already ended. |
| 2 | A price refresh failed, overlapping history changed, or the verified calendar ends within 60 days. Forecasts still ran with what was on disk. |
| 3 | A horizon missed its issue window (`suppressed_*`). Worth an alert. |

## Reference close source

FRED publishes a day's SP500 close about 11:30 New York time the next business day (observed: file updated
2026-11-06 11:26 ET with the 11-05 close), after the 08:00 issue cutoff, so a FRED-only live run could never
issue. Live runs therefore fill SPX dates after FRED's last row from Cboe's `SPX_History.csv`, which prints
the same evening (observed 2026-11-06 ~21:11 ET). FRED stays the source for the backtest and for every date
it covers; the live setting is `Config.live_cboe_spx` (off by default, on in the runner).

- Each published forecast records `reference_close_source` (`fred` or `cboe_spx_history`) in its model JSON;
  the `source` column of `data/normalized/spx.parquet` marks each row.
- When FRED catches up, both series are compared. Differences above 0.005 points are written to
  `artifacts/spx_reconciliation.csv`; one dated within the last 30 days makes the run exit 2 with
  `SPX RECONCILIATION:` in the log. A forecast already issued is never revised. Historically 2,504 of 2,513
  shared days match exactly and 9 differ (largest 7.89 points, 2019-02-28).

## Scheduling

An input counts as available when this pipeline *first sees* it, so run after the sources print. Cboe
posts the day's closes by about 21:15 New York time; the issue window runs from 20:00 on the reference
session to 08:00 on the next session. Run **twice per weekday**: once at about 22:30 New York time (the
normal issue) and once at 07:30 as a fallback if the evening run was missed. Reruns never overwrite an issued
forecast, so the second run is harmless. A Friday reference session is issued Friday evening or Monday morning.

Windows Task Scheduler example (not registered by this repo; triggers use the machine's local time zone,
so convert 22:30 and 07:30 New York if the machine is elsewhere):

```powershell
schtasks /Create /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 22:30 /TN "spx-live-forecast-evening" /TR "cmd /c cd /d C:\Dev\predictspxml && set PYTHONPATH=src && <path-to>\python.exe -m scripts.run_live_forecast"
schtasks /Create /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 07:30 /TN "spx-live-forecast-morning" /TR "cmd /c cd /d C:\Dev\predictspxml && set PYTHONPATH=src && <path-to>\python.exe -m scripts.run_live_forecast"
```

## Known limits

- The pinned calendar ends 2026-12-31. The run warns (exit 2, `CALENDAR:` in the log and `calendar_runway_days` in
  `artifacts/upcoming_contracts_validation.json`) when fewer than 60 days remain, and fails (exit 1) once it has ended.
  Import and review the next annual calendar before then.
- The live reference close can come from Cboe, a different series from FRED's. They agree on all but ~0.4%
  of days, but a forecast issued from a Cboe close is not strictly the same input the backtest used.
- Upcoming-contract eligibility is calendar-derived and tagged `calendar_derived_unverified`.
