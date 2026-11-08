# SPX settlement forecasting

Repository: [jsubramanian5-cmd/predictspxml](https://github.com/jsubramanian5-cmd/predictspxml).
Source code, notebooks, input archives/CSVs, immutable snapshots, normalized data,
results and saved model artifacts are versioned for cross-machine continuation.
Virtual environments, runtime state, caches and credentials are excluded.

On another machine, clone once:

```powershell
git clone https://github.com/jsubramanian5-cmd/predictspxml.git
cd predictspxml
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
.\.venv\Scripts\python -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python scripts/start_jupyter.py
```

After that, use `git pull origin main` from a clean working tree to get updates.
Reinstall `requirements.lock` if it changed. Data imports/snapshots are byte-preserved
by `.gitattributes`, so source hashes stay valid across platforms. The included lock
targets Windows x64/Python 3.13; other platforms need compatible dependency resolution.
Existing executed notebooks can be opened without rerunning fits.

The main interface is [`notebooks/spx_forecast.ipynb`](notebooks/spx_forecast.ipynb).
It audits FRED/Cboe inputs, constructs settlement-return labels, compares quantile models
with historical benchmarks, runs chronological development/final testing and issues
weekly SPXW PM/monthly SET AM ranges when verified data and timing permit.

## Setup and execution

Tested host: Windows x64, Python 3.13.9. Use Python 3.13 for the included transitive lock.
All model fits use CPU; XGBoost uses one thread. From the repository root in PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
.\.venv\Scripts\python -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python scripts/start_jupyter.py
```

If `py` is unavailable, replace it with the full Python executable path. On this host
the interpreter is `C:/Users/JKSub/anaconda3/python.exe`. The checked-in lock freezes the
installed runtime's transitive closure. XGBoost 3.1.3 was installed through the user's
Anaconda environment and now passes the CPU quantile integration test. A clean online
installation of the complete lock is still unverified. See [`VALIDATION.md`](VALIDATION.md).

The launcher supports this host's venv inheriting Anaconda packages. If the venv has no
JupyterLab web files, it selects the installed base interpreter's application directory
and includes its Jupyter data directory. Runtime files stay under `.runtime/`. It retains
normal Jupyter authentication and existing explicit environment overrides.
Use `python scripts/start_jupyter.py --check` to inspect the application directory without
starting a server. Extra JupyterLab options can be appended, for example `--no-browser`.
Startup/errors are saved in `.runtime/jupyter-launch.log` with URL tokens redacted.
If a browser tab still points at an older server, use a separate port:
`python scripts/start_jupyter.py --ServerApp.port=8899 --ServerApp.port_retries=0`
and open the new URL printed by that launch.

If an older direct launch gives **500 Internal Server Error** with `TemplateNotFound:
index.html`, stop that server with Ctrl+C in its terminal, then use the launcher above.
No reinstall or `jupyter lab build` is required when Anaconda's web assets already exist.

Run all notebook cells from a fresh kernel. The single configuration cell controls dates,
paths, refresh, seed and the expensive backtest (enabled by default). To execute and preview:

Training and evaluation are visible in **section 6, Training and development runs**, and
**section 7, Final test runs**. Section 6 shows the required initial training years,
development/test periods and outcome counts. Both stages print progress as expirations
finish, including model counts and training sample sizes, then show compact score tables.
Models refit on prior available labels for each forecast; they do not have a single
epoch-based training run. Restart the kernel after notebook/module updates.

Both sections include **actual vs predicted settlement** charts for weekly and monthly
outcomes, with actual settlements, benchmark median and ML median forecasts shown
separately. The development chart uses development-selected finalists; the final-test
chart uses the frozen finalists on the later holdout. The original 80% interval-band
charts remain in section 9. New chart files are
`artifacts/development_actual_vs_prediction.png` and
`artifacts/final_test_actual_vs_prediction.png`.

For an already executed view, open `notebooks/spx_forecast.executed.ipynb`. If an older
notebook tab is still open, reopen the updated source before restarting and running all.

```powershell
.\.venv\Scripts\python -m nbconvert --execute --to notebook --ExecutePreprocessor.timeout=1800 --output spx_forecast.executed.ipynb notebooks/spx_forecast.ipynb
.\.venv\Scripts\python -m nbconvert --to html notebooks/spx_forecast.executed.ipynb --output-dir artifacts
```

The user-requested short form works when Jupyter is on PATH:

```bash
jupyter nbconvert --execute --to notebook --output spx_forecast.executed.ipynb notebooks/spx_forecast.ipynb
```

## Required official inputs

The calendar has been built from visually reviewed Cboe annual documents for 2016–2026
plus exceptional-closure notices. Regenerate it with
`.\.venv\Scripts\python scripts/build_calendar_import.py` after supplying the documented
source archives. Evidence and pinned transcriptions are in `docs/calendar_review.json`;
the receipt is `artifacts/calendar_validation.json`. This records session existence,
including early-close sessions, rather than intraday trading hours. Historical contract
eligibility is generated separately with
`.\.venv\Scripts\python -m scripts.build_contract_import`. The resulting schedule
classifies the requested EOW and standard monthly targets, rather than asserting
strike-level tradeability or historical liquidity. See `docs/contract_review.json`.

Manual downloads can be used offline: place unchanged `SP500.csv`, `VIX_History.csv`
and `VIX9D_History.csv` in `data/imports/`. These take precedence over automatic price
downloads even when refresh is enabled. Receipt timestamps are recorded at first ingestion,
not inferred from file modification times. See `docs/imports.md` for provenance limits.

Automatic downloads: FRED `SP500`, Cboe VIX/VIX9D CSVs, the public dated SPXW weekly
settlement table and source documentation snapshots. No keys are required. Requests use
timeouts, bounded retries and content-addressed raw snapshots. Set `refresh=True` to
attempt fresh retrieval; a failed refresh is reported, never silently treated as fresh data.

Import verified Cboe historical `calendar.csv`, `contracts.csv` and `settlements.csv` in
`data/imports/` using [`docs/imports.md`](docs/imports.md). The supplied archives and
reviewed calendars now provide both target schedules and settlement coverage needed
for the 2017–2025 training/development/final-test periods. Undated 2015 SET records stay
outside those periods. Current web pages alone do not establish historical rules.
Do not replace settlement labels with SPX closes. Each unsupported horizon remains pending,
while source audits and other supported sections continue. Synthetic data exists only in tests.

## Evaluation and publication

Separate horizon models fit 10th/50th/90th percentiles. Weekly XGBoost evaluates all eight
requested configurations; monthly has one. Linear quantile regression, empirical and
VIX9D/VIX-scaled benchmarks use training-window data only. Crossings are sorted and counted.
Five complete training years, at least two development years and the latest two complete
calendar years before configured `end` are mandatory. Configurations freeze before final testing.
ML promotion requires a lower paired final-test interval score and coverage at least as
close to 80%; otherwise the benchmark remains selected. Wilson coverage intervals assume
independent outcomes. Overlap, serial dependence, history revisions and unknown historical
publication timing limit interpretation. Holdout-based promotion is not a fresh unbiased test.

Forecast publication starts at reference-session 20:00 New York time and requires inputs
by next-session 08:00. Historical unknown input timing uses that explicit cutoff assumption;
live unknown timing uses actual snapshot receipt. Late FRED delivery can suppress a forecast.
The forecast cell makes one check and preserves any existing issued row and issue timestamp.
Run one notebook writer at a time; concurrent forecast publishers are not supported.

## Layout and outputs

- `src/spx_forecast/`: ingestion, official calendar, features, models, backtests, forecasting and plots.
- `tests/`: synthetic fixtures and focused correctness tests.
- `notebooks/`: source and executed notebooks.
- `data/raw/`: immutable raw snapshots and latest-cache pointers; `data/normalized/`: Parquet.
- `artifacts/`: forecast/status CSVs, backtest predictions, metrics, audit, source manifests,
  frozen development choices, metadata, charts and HTML preview.
- `artifacts/models/`: fitted trusted-local model pickles plus feature order, versions,
  configurations, training cutoffs and source hashes. Never load untrusted pickles.

Pending runs export schemas/statuses without fabricated forecasts, metrics or fitted models.
`scripts/build_notebook.py` regenerates the source notebook using nbformat.
