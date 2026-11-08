# Execution and validation record

Date: 2026-11-06 (America/New_York). Repository instructions in the existing `AGENTS.md`
were read before implementation and were not modified.

## Resume check: 2026-11-07

### Visible training/test runs and actual-vs-prediction charts (later on 2026-11-07)

- Renamed notebook section 6 to **Training and development runs** and section 7 to
  **Final test runs**. Added a six-row split/count table, compact per-model result
  tables, successful development status messages and throttled progress logs showing
  expirations completed, configuration counts and available training sample sizes.
- Progress callbacks are optional. Existing callers remain supported; the chronological
  isolation test checks callback cutoffs and verifies identical results with/without
  progress reporting. Models still refit per forecast using prior available labels.
- Added development and final-test **actual vs predicted settlement** figures, each
  with separate weekly/monthly panels. Actual values are a dark solid line with markers;
  benchmark medians use a blue dashed line; ML medians use an orange dotted line.
  Only matched horizon/stage rows are plotted. Development-selected finalists are
  compared on development data; final-test plots use those frozen configurations.
  Original interval, coverage and width charts remain intact.
- New artifacts: `development_actual_vs_prediction.png` and
  `final_test_actual_vs_prediction.png`. Both rendered charts were visually inspected
  at their saved resolution; labels, legends, dates, samples, line patterns and units
  are readable. Table counts match 173/60 required initial training outcomes, 71/24
  development outcomes and 69/24 final-test outcomes. Earlier usable warm-up rows can
  also enter expanding fits, as disclosed in the notebook.
- Full notebook execution passed: **21 cells, 11 executed code cells, zero errors,
  seven embedded PNG outputs**. Saved progress logs reach 71/71 weekly and 24/24 monthly
  development, then 69/69 weekly and 24/24 monthly final testing. HTML export refreshed.
  Full notebook/HTML viewer inspection remains unverified; PNG QA and output-content
  checks are not represented as full viewer inspection.
- **34 tests passed**. New chart tests check stage isolation, exact actual/median series,
  finalist labels and rejection of inconsistent actual values across models. Independent
  exported-backtest validation still passes for all 1,253 rows, with unchanged scores
  and selection decisions. A pre-edit source copy is retained under
  `.runtime/notebook_backups/spx_forecast.before_run_display.ipynb`.
- Existing open kernels cache imported modules; reopening the updated notebook and
  restarting its kernel is required before running the new callback-enabled cells.

### Follow-up on user-reported HTTP 500 (later on 2026-11-07)

- User reports a remaining 500 after the launcher correction. This remaining failure
  has not been reproduced; requested the terminal traceback rather than assuming the
  previous missing-assets error explains it.
- Tested the exact `scripts/start_jupyter.py` entry point, rather than just its derived
  environment. Notebook page, `/lab`, contents API and kernelspec API returned 200;
  authenticated Python kernel creation returned 201 and the temporary kernel/server
  were stopped afterward. No server auth token was printed in tool output.
- Added persistent `.runtime/jupyter-launch.log` with URL token redaction so the next
  user launch captures the actual traceback. A separate port 8899 is documented to
  distinguish a new launch from any previously open browser/server session.
  User-facing resolution remains unconfirmed until their failing request is inspected.

### JupyterLab HTTP 500 fixed (later on 2026-11-07)

- Reproduced HTTP **500** at `/lab/tree/notebooks/spx_forecast.ipynb` with the originally
  recommended direct venv launch. The venv inherits Python packages from Anaconda but
  defaults to `.venv/share/jupyter/lab`, which has no web assets. Logs confirm missing
  application assets and `jinja2.exceptions.TemplateNotFound: index.html`.
- Added `scripts/start_jupyter.py`. When own-environment assets are absent, it locates
  the installed base interpreter's JupyterLab application and data directories, and uses
  local writable runtime/IPython directories. Explicit user environment overrides and
  normal Jupyter authentication remain intact. Added `--check` and updated README commands.
- Using that environment, a temporary authenticated loopback server returned **200** for
  both the notebook page and `/api/contents/notebooks/spx_forecast.ipynb?content=0`.
  The temporary server was stopped afterward. No auth token or token-bearing URL was
  printed. Browser rendering is not implied by HTTP/API checks; notebook computation
  already passed the earlier fresh-kernel execution.

### November 6 close-file check (later on 2026-11-07)

- Found `data/imports/sp500-close-2026-11-06.json`. Its value 7376.86 and embedded
  SPXW row exactly match the November 6 Cboe PM settlement already present in the
  supplied weekly archive. This is not a FRED SP500 observation; no source substitution
  or manual alteration of `SP500.csv` was performed. Receipt:
  `artifacts/reference_close_check.json`.
- Rechecked the official FRED SP500 page through the web tool: latest displayed date
  is still November 5, updated November 6; next listed release is November 9. The local
  SP500 CSV is unchanged and has the same previously recorded content hash. Asking
  for a fresh download before publication could not resolve this gap.
- Live forecasts remain unissued. The current pipeline's FRED reference-price policy,
  input cutoff and verified future-contract requirements have not been relaxed.

### XGBoost and full historical backtests completed (later on 2026-11-07)

- Confirmed user-installed **XGBoost 3.1.3**, importable by the project venv from the
  Anaconda environment. Regression suite: **33 passed, no skips**, including the CPU
  quantile integration test. Earlier missing-package notes below are historical.
- Executed the full source notebook from a fresh kernel. All 11 code cells completed
  with no error outputs and five embedded PNGs. HTML preview was refreshed. Verified
  both notebook schemas and output counts with `scripts/verify_notebook.py`.
- Completed **1,253 historical prediction rows**: development = 71 weekly outcomes
  across 13 configurations (923 rows) and 24 monthly outcomes across six configurations
  (144 rows); final test = 69 weekly and 24 monthly outcomes across two frozen finalists
  per horizon (138 and 48 rows). Development is 2022–2023; final test is 2024–2025.
- Weekly development chose XGBoost depth 2 / 100 trees / L2 50 as its ML finalist;
  monthly chose XGBoost depth 1 / 50 trees / L2 50. Both benchmarks were volatility-scaled
  historical quantiles. Choices were exported before accessing the final holdout.
- Weekly final-test benchmark interval score = **662.4751 bps**, coverage = **54/69
  (78.26%)**; XGBoost = **672.9873 bps**, coverage = **54/69**. Monthly benchmark score
  = **1331.5715 bps**, coverage = **19/24 (79.17%)**; XGBoost = **1403.3698 bps**,
  coverage = **21/24 (87.50%)**. Lower interval score is better. Neither ML finalist met
  the promotion rule, so the scaled benchmark is selected for both horizons.
- Independently checked exported score arithmetic, coverage/miss rates, return/price
  conversions, ordered quantiles, unique keys, per-model development/final cohorts,
  official settlement joins, training cutoffs, development winners, final finalist keys,
  promotion decisions and fitted-model metadata. Receipt: `artifacts/backtest_validation.json`;
  reproduction: `python scripts/validate_backtest.py`. This does not independently refit
  every estimator or establish live trading returns. Both selected local models and their
  training/source metadata are saved under `artifacts/models/`.
- The populated PNG figures were visually inspected. Chart labels, units, forecast
  bands, observed settlements, coverage intervals and score comparisons are readable.
  Full notebook/HTML viewer inspection remains unverified due the previously documented
  browser-access limitation; no screenshot of the complete notebook is claimed.
- **No live forecast issued.** The configured November 6 reference still lacks its SP500
  close, and the schedule currently stops at November 6 rather than asserting future
  listed expirations. November 6 is not a standard monthly reference session. These live
  requirements remain separate from completed historical evaluation.
- Historical input/label publication assumptions, revised-history risk, serial dependence,
  small monthly holdout (24 outcomes), and final-test-based promotion limit interpretation.
  No additional untouched test or demonstrated trading profitability is claimed. A clean
  lock installation is still unverified even though the current environment runs.

### Contract schedule and monthly date resolution (later on 2026-11-07)

- Reviewed the supplied listing notices and pilot report, including relevant rendered
  pages, and primary SEC release 34-62911 and Cboe RG17-054 through browsing. The policy
  and pinned local-document hashes are in `docs/contract_review.json`. The pilot report's
  stale 2019 expiry boilerplate was identified and is not sole proof of eligibility.
- Built `data/imports/contracts.csv`: **695 rows**, consisting of **377 eligible EOW
  PM periods**, **130 eligible standard monthly AM periods**, **130 excluded standard
  monthly collisions** and **58 excluded month-end collisions** (including quarter-end).
  Classification comes from the documented target family, not from SPXW symbol alone.
  All included EOW dates have explicit source settlement records; no missing record is
  converted to an ineligible week. Three dated EOW examples in C2024102804 reconcile.
- The weekly schedule starts at nominal Friday 2016-01-15; the two earlier weeks require
  prior-week calendar dates outside the verified span and are separately disclosed.
  The builder stops at 2026-11-06 and does not assert future listings. This establishes
  target settlement-date eligibility, not strike-level chain availability, liquidity or
  exact historical listing timestamps. Receipts: `artifacts/contract_validation.json`
  and `artifacts/contract_coverage.csv`.
- Resolved **48** 2016–2019 month-only SET values using the independently transcribed
  standard AM contract dates. Preserved separate value-source and date-source hashes,
  with publication timestamps still unknown. Together with **82** dated records this
  supplies **130** monthly labels. **12** undated 2015 values remain outside the supported
  calendar/required split; no dates are guessed for them.
- Preparation now reports **ready history for both horizons**: five training years
  2017–2021, development 2022–2023, final test 2024–2025. The supported observation frames
  contain 344 weekly and 118 monthly historical outcomes after feature/timing checks.
  This is data readiness, not a completed model comparison or evidence of performance.
- Regression suite: **32 passed, 1 skipped**. New checks cover exclusion of PM records
  from other expiry families, holiday-adjusted month-end collisions, missing EOW labels
  raising instead of becoming false eligibility, unknown monthly dates remaining pending,
  AM/PM mismatches, duplicate-month ambiguity, closed-session rejection and provenance.
- XGBoost remains missing; the full requested candidate evaluation still cannot run.
  The November 6 SP500 reference close is still absent, and live timing/future listing
  requirements remain distinct from historical readiness.

### Calendar transcription completed (later on 2026-11-07)

- PyMuPDF 2.1 is now installed in the user's Anaconda environment and importable from
  the project venv. Rendered and visually inspected all eleven single-page calendar
  PDFs for 2016–2026, including legends and month grids. Page images/text are retained
  under `.runtime/calendar_review/pages/`; the immutable reviewed hashes and manual
  transcriptions are in `docs/calendar_review.json`.
- Added dated Cboe exceptional-closure notices C2018120300 (2018-12-05) and
  C2024123101 (2025-01-09). Both were read through the web tool; no local notice PDF
  hash is invented. The latter permits GTH but explicitly excludes RTH and curb trading.
- Built `data/imports/calendar.csv`: **4,018 contiguous calendar dates**, **2,765
  regular sessions**, 2016-01-01 through 2026-12-31. Early-close days remain sessions;
  intraday hours are not asserted. Original Cboe source URLs, archived retrieval URLs,
  annual PDF hashes and source member references remain attached to each date.
- All **2,513** positive SP500 observations in its overlapping 2016-11-07–2026-11-05
  range agree with the calendar, with no missing sessions or observations on closed
  dates. The 96 blank SP500 values are explained by closures. VIX9D similarly has no
  missing reviewed sessions. VIX has no missing sessions and 33 extra observations on
  RTH-closed dates; these are retained in source data, disclosed, and excluded by feature
  reindexing. Reviewed Cboe GTH references are recorded in the receipt.
- Wrote **132** visually transcribed standard monthly expiration dates, including
  holiday-adjusted dates. All **82** explicitly dated SET records in the supplied monthly
  source responses agree. This lookup has not been promoted to weekly contract eligibility
  or used to assign dates to older monthly values in this step.
- Reproduction: `python scripts/build_calendar_import.py` (optional `--render` needs
  PyMuPDF). Receipt: `artifacts/calendar_validation.json`; lookup:
  `artifacts/standard_monthly_expirations.csv`. Script refuses changed source hashes or
  unexplained reconciliation mismatches. No generic holiday rule library is used.
- Regression suite: **28 passed, 1 skipped**. New checks cover mourning closures,
  the Juneteenth transition, 2021-12-31 being open, early-close sessions, adjusted
  monthly dates, rejection of expirations on closed dates and extra GTH VIX observations.
- `contracts.csv` remains absent; monthly pre-2020 values still await a verified date
  join in the importer. XGBoost and the November 6 SP500 close remain unavailable.
  Older sections below describe prior blockers that this step has resolved.

### Early calendar archive received (later on 2026-11-07)

- Found `data/imports/cboe-options-calendars-2016-2019.zip` with all four requested
  annual PDFs. ZIP CRC checks passed; member sizes and SHA-256 hashes match the
  supplied manifest. PDF headers and EOF markers are present. Manifest references
  archived Cboe URLs on the Internet Archive; this is supplied provenance, not a fresh
  independent download. Receipt: `artifacts/early_calendar_archive_check.json`.
- Together with the earlier bundle, calendar source documents now cover 2016–2026.
  The missing annual-document gap is resolved. Calendar dates/legend markings still
  need extraction and verification; no complete `calendar.csv` has yet been generated.
  These integrity checks do not establish historical contract eligibility or resolve
  exceptional closures. Earlier sections stating those calendar documents were absent
  describe the prior state.

### Monthly and reference archives (later on 2026-11-07)

- Added ingestion of `cboe-monthly-set-am-history.zip`, recomputing values from raw
  source JSON/HTML rather than trusting its derived summary. Source references and
  content hashes are retained with explicit user-supplied provenance.
- Imported **82 dated SET AM labels**, 2020-01-17 through 2026-10-16. Retained **60
  month-only values**, January 2015–December 2019, separately without assigning dates.
  See `artifacts/monthly_archive_coverage.csv` and `artifacts/monthly_archive_undated.csv`.
- The calendar/listing ZIP contains calendars for **2020–2026** and several listing
  notices/current reference pages. Its README disclaims complete historical contract
  eligibility; earlier calendars are absent. PDF contents have not been parsed locally:
  PDF extraction/rendering tools remain unavailable. File byte counts, signatures and
  hashes are checked by `scripts/check_reference_archive.py`; receipt is
  `artifacts/reference_archive_inventory.json`. A reference entry reports a failed 403
  download and that document is absent, rather than treated as available.
- No `calendar.csv` or `contracts.csv` has been fabricated from generic weekday rules
  or settlement-presence assumptions. Both horizons remain pending. The early undated
  SET values need authoritative expiration dates; XGBoost and the November 6 SP500
  close are still unavailable.
- Regression suite: **25 passed, 1 skipped**. Added checks for monthly raw-source
  precedence, AM/PM filtering, preservation of month-only values without dates and
  rejection of expiration/source-year mismatches.

### Weekly archive import (later on 2026-11-07)

- Found `data/imports/cboe-weeklys-settlement-values-all-years.zip`, with source
  references to year-specific Cboe W settlement endpoints. Added ingestion for both
  HTML-in-JSON and structured JSON records, preserving supplied provenance and hashes.
- Imported 1,940 explicit positive SPXW PM labels within configured 2015–2026 dates,
  from 2015-01-02 through 2026-11-06. Years before the configured start are not used.
  No duplicate/conflicting settlement keys passed validation. One annotated 2019-08-12
  value is excluded pending footnote review; original source bytes are preserved.
  Coverage and exclusions: `artifacts/weekly_archive_coverage.csv` and
  `artifacts/weekly_archive_review.csv`.
- The archive includes Monday–Friday expirations. Labels do not establish end-of-week
  listing eligibility. Calendar/contracts still block both horizons; monthly SET AM history
  remains absent. XGBoost is absent and SP500 still ends 2026-11-05.
- Regression suite: **24 passed, 1 skipped**. Synthetic archive tests cover both schemas,
  other-symbol exclusion, whitespace, explicit dates, annotated-value quarantine, raw
  snapshots and rejection of unexpected source hosts/endpoints.

### Manual-download check (later on 2026-11-07)

- Found all three manually downloaded price CSVs and added offline ingestion with
  content-addressed snapshots, explicit user-supplied provenance and stable first-receipt
  timestamps. Regression suite: **23 passed, 1 skipped**; XGBoost remains absent.
- SP500: 2,609 rows, 2,513 positive values, 2016-11-07 through 2026-11-05;
  96 blank values. VIX: 9,311 positive values, 1990-01-02 through 2026-11-06.
  VIX9D: 3,985 positive values, 2011-01-04 through 2026-11-06.
- No duplicate/invalid dates, nonpositive values or nonmissing nonfinite values were
  detected. Blank SP500 values cannot be classified as missing trading sessions without
  the official calendar. Receipt: `artifacts/manual_download_check.json`.
- Required imports still absent: `calendar.csv`, `contracts.csv`, `settlements.csv`.
  Calendar coverage, historical eligibility and official settlement labels remain blockers.
  The November 6 reference close is absent from SP500, so the configured live reference
  cannot run either. No live receipt is backdated to a historical issue cutoff.
- With November/December 2016 warm-up, the earliest potentially complete usable year is
  2017: five training years (2017–2021), development (2022–2023), final test (2024–2025).
  Actual completeness still requires the missing official calendar and contract records.

The notes below describe the earlier state before the manual downloads were supplied.

- Rechecked the existing repository instructions and import directory. No verified official
  CSV imports have been added. The local VIX download probe still failed with an unreachable
  remote server, and XGBoost is still absent. The original network/data blockers remain.
- Fixed settlement reconciliation: web Timestamp/float records and equivalent CSV
  string records now normalize before deduplication. A known publication timestamp is
  retained when available. Conflicting official values still raise a validation error.
- Fixed the empty-data audit schema so coverage dates and duplicate counts remain visible
  as unknown even when every source is unavailable.
- Added regression tests for both fixes. Current result: **22 passed, 1 skipped**;
  the skipped check remains the unavailable XGBoost CPU integration test.
- Regenerated the executed notebook, HTML preview, audits and validation receipt after
  these changes. Data coverage and model results remain pending as described below.
- Retried preview discovery: the browser access host is still unavailable. Native Edge
  was discovered, but selecting its window for inspection failed because it was minimized;
  no notebook/table layout was successfully captured. Full viewer inspection remains open.

The earlier execution record below is retained to distinguish original checks from this
resume check. Setup and import instructions remain in `README.md` and `docs/imports.md`.

## Executed checks

- Python 3.13.9 (Anaconda, Windows x64), local `.venv` using existing system packages.
- Editable package installation passed with `pip install --no-deps --no-build-isolation -e .`.
- `python -m pytest -q`: **20 passed, 1 skipped**. Tests use explicitly identified synthetic
  fixtures only. They cover holiday shifts, monthly references, ineligible contracts,
  settlement join/type/duplicates, missing windows, timing/cutoffs/DST, future-label leakage,
  training-only scaling, quantile ordering, price conversion, interval scoring/Wilson intervals,
  insufficient history, ML promotion, frozen development choices, sequential holdout isolation,
  cached ingestion, HTML parsing and idempotent forecast publication.
- XGBoost CPU quantile integration test skipped: XGBoost is not installed locally and
  installation failed with an unreachable network (`WinError 10051`). Linear and historical
  benchmark fit/predict/backtest paths were exercised in tests; no synthetic result is
  presented as market evidence.
- `nbformat.validate` passed for the source and saved executed notebook: **21 cells,
  11 code cells**. The executed copy has all 11 execution counts, **zero error outputs**,
  and **five embedded PNG figure outputs**. Receipt: `artifacts/notebook_validation.json`.
- Fresh-kernel execution completed through Jupyter nbconvert (which uses nbclient):

  ```powershell
  .\.venv\Scripts\python -m jupyter nbconvert --execute --to notebook --ExecutePreprocessor.timeout=1800 --output spx_forecast.executed.ipynb notebooks/spx_forecast.ipynb
  ```

  On this host, PATH included `.venv/Scripts` and `C:/Users/JKSub/anaconda3/Scripts`.
  `JUPYTER_PATH=C:/Users/JKSub/anaconda3/share/jupyter` supplied templates inherited
  through system packages. Local `.runtime/ipython` and `.runtime/jupyter` supplied
  writable kernel state. A clean installation of the lock should supply its own templates.
- HTML export completed at `artifacts/spx_forecast.executed.html`.
- Python modules compiled successfully. Exported CSV/JSON tables were read and checked
  for explicit pending states and absence of invented predictions or scores.

## Actual data coverage and model results

All eight automatic requests failed on the local execution host because outbound network
access is unavailable. **SP500: 0 rows; VIX: 0 rows; VIX9D: 0 rows; official settlements:
0 rows.** No coverage dates can be asserted. Missing-session counts are unknown without
the official calendar. No calendar, contract or settlement CSV imports were supplied.

Weekly and monthly horizons are **pending**. There are **zero historical forecasts,
zero final-test observations and zero issued forecast ranges**. No market-trained models
or performance claims were produced. Header-only forecast/prediction/metrics exports and
populated audit/status/manifests are intentional. Failed source attempts retain URLs,
attempt timestamps and errors; no content hash is fabricated for an absent response.

Official documentation and linked volatility download endpoints were inspected separately
through browsing; see `docs/source_inspection.md`. That is not a successful Python download
or verification of complete settlement coverage. Both historical Cboe contract/calendar
eligibility and complete SET history remain unresolved and require the verified imports
specified in `docs/imports.md`. The weekly page adapter has not been validated against
a live retrieved response on this host; its parser was tested with a synthetic HTML fixture.

## Inspection and environment gaps

The five exported PNG figures were opened and visually inspected: data coverage, weekly
and monthly outcomes, model score/coverage comparisons, and interval widths. Their pending
messages and unit labels are readable; they contain no fake observations. Populated-data
plot layouts cannot be verified without actual verified history.

The saved notebook's temporary HTML preview was generated. An attempt to open it in Chrome
through the browser tool returned **“The browser access host is unavailable.”** Consequently
the full saved notebook/HTML layout, especially tables at reading size, has **not** been
visually inspected in a notebook viewer. Open `artifacts/spx_forecast.executed.html` locally
or the executed notebook in JupyterLab to complete that inspection. Static PNG inspection
and table-content checks do not substitute for this missing viewer check.

The 111-distribution lock combines installed metadata with explicit missing-package pins.
XGBoost, fqdn, isoduration, uri-template, webcolors and types-python-dateutil were not installed
locally. Arrow and its PyPI typing dependency are explicitly pinned. A clean online lock
installation and XGBoost fit remain **unverified**. The lock targets Windows x64/Python 3.13;
other platforms require re-resolution. No dependency installation is claimed merely because
its version is pinned. Nbconvert also reported missing alternative text for five PNG outputs;
the Windows kernel emitted a nonfatal Proactor/selector compatibility warning.

To finish verification on a network-enabled machine: install `requirements.lock`, import
the verified official history, rerun tests (including XGBoost), execute all notebook cells,
then inspect the saved notebook and populated charts. Do not relax the five-year training,
two-year development, two-year final-test or source/timing requirements to produce numbers.
