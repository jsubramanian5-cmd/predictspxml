# Live forecast run

`scripts/run_live_forecast.py` is the scheduled entry point. Run it from the repository root:

```powershell
$env:PYTHONPATH = "src"   # unless the package is installed
python -m scripts.run_live_forecast            # normal run
python -m scripts.run_live_forecast --skip-refresh --now 2026-11-09T00:46:00Z   # replay without downloads
```

## Order (each step depends on the one before)

1. **Refresh prices.** FRED SP500 and Cboe VIX/VIX9D are downloaded and *merged* into
   `data/imports/`. Existing rows are never rewritten (FRED serves only ~10 years); overlapping values
   that differ are counted and reported.
2. **Rebuild upcoming contracts** (`upcoming_contracts.csv`) from the pinned calendar, as of today.
   It depends on `contracts.csv`, so refresh that first whenever a new settlement archive is imported
   (`python -m scripts.build_contract_import`).
3. **Load** all imports (`workflow.prepare` with `end` = today).
4. **Forecast once** with the models selected by the last backtest (`artifacts/run_metadata.json`);
   this run never re-tunes or re-selects. Each horizon forecasts its next contract whose 08:00 issue
   cutoff has not passed. Results are appended to `artifacts/forecast_status_history.csv`.

Steps 1-2 only need to be repeated, not changed, between runs; a rerun never overwrites an issued forecast.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Ran cleanly. `pending`/`waiting_for_inputs`/`not_yet_eligible` are normal between contracts. |
| 1 | Error (details in `.runtime/live_forecast.log`). |
| 2 | A price refresh failed, or overlapping history changed. Forecasts used the files already on disk. |
| 3 | A horizon missed its issue window (`suppressed_*`). Worth an alert. |

## Scheduling

Inputs are end-of-day values, so **one run per weekday morning is enough**. The issue window runs from
20:00 New York on the reference session to 08:00 on the next session. Run once at about 07:30 New York
time, Monday to Friday: that is the last point at which the day's closes can be in, and it leaves 30
minutes before the cutoff. A Friday reference session is therefore issued on Monday morning. Reruns never
overwrite an issued forecast, so a second run (for example 20:30 the evening before) is optional.

Windows Task Scheduler example (not registered by this repo; the trigger uses the machine's local
time zone, so convert 07:30 New York if the machine is elsewhere):

```powershell
schtasks /Create /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 07:30 /TN "spx-live-forecast" /TR "cmd /c cd /d C:\Dev\predictspxml && set PYTHONPATH=src && <path-to>\python.exe -m scripts.run_live_forecast"
```

## Known limits

- The pinned calendar ends 2026-12-31; import the next annual calendar before then.
- FRED's SP500 publication lag is unverified. On the evening of 2026-11-08 FRED still had no value for
  the 2026-11-06 close, so a Friday reference may not be available by Monday 08:00 and would then be
  suppressed. Until the value arrives the status names the missing series; it is not substituted with
  Cboe settlement values.
- Upcoming-contract eligibility is calendar-derived and tagged `calendar_derived_unverified`.
