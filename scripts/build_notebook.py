"""Generate the notebook with nbformat; no hand-edited notebook JSON."""
from pathlib import Path
import nbformat as nbf

nb = nbf.v4.new_notebook()
cells = []


def md(text):
    cells.append(nbf.v4.new_markdown_cell(text))


def code(text):
    cells.append(nbf.v4.new_code_cell(text))


md("""# SPX settlement forecasting
## 1. Goal, forecast definitions, and assumptions
Estimate 10th, 50th and 90th percentiles of **official settlement** returns, separately by horizon.
Weekly: prior week's final regular session close → following week's listed end-of-week **SPXW PM** settlement.
Monthly: current standard monthly settlement session close → next standard monthly **SET AM** settlement.
Reference prices are FRED SP500 closes. Labels are Cboe records, never substituted closes.

A forecast is eligible at **20:00 America/New_York** on its reference session. All inputs must be
available by **08:00 next regular session**, otherwise publication is suppressed. The forecast cell
checks once and safely retains previously issued rows. Unknown historical input publication is assumed
by the next-session 08:00 cutoff; unknown label publication becomes trainable the following regular session.
Live inputs use observed receipt times when publication times are unknown.

Verified Cboe dated calendar/contract imports are required to resolve historical holidays and eligibility.
Each horizon proceeds independently when its requirements are met. Missing evidence produces explicit
**pending** outputs; empty panels contain no synthetic market observations. Run all cells in order.
""")
md("## 2. Configuration and environment checks\nEdit this single cell for all run parameters. Full chronological backtesting is enabled by default.")
code("""from pathlib import Path
import sys
ROOT = Path.cwd() if (Path.cwd() / 'pyproject.toml').exists() else Path.cwd().parent
sys.path.insert(0, str(ROOT / 'src'))
from spx_forecast.config import Config
CONFIG = Config(
    start='2015-01-01', end='2026-11-06', reference_date='2026-11-06',
    raw=ROOT / 'data/raw', normalized=ROOT / 'data/normalized',
    imports=ROOT / 'data/imports', output=ROOT / 'artifacts',
    seed=42, refresh=False, run_expensive_backtest=True,
    timeout_seconds=15, retries=2,
)
CONFIG.prepare()
""")
code("""import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from IPython.display import display, Markdown
from spx_forecast import workflow, plots
from spx_forecast.forecast import dependency_versions
from spx_forecast.backtest import metrics, model_key
from spx_forecast.features import FEATURES
plots.style()
pd.set_option('display.max_colwidth', 100)

def show_run_progress(event):
    if event['completed'] == 1 or event['completed'] == event['total'] or event['completed'] % 10 == 0:
        stage = 'training/development' if event['stage'] == 'development' else 'training/final test'
        print(f\"{event['horizon'].title()} {stage}: {event['completed']}/{event['total']} expirations complete; \"
              f\"{event['models']} configurations per expiration; {event['training_count']} past outcomes in latest fit\", flush=True)

def show_run_results(predictions, title):
    display(Markdown(f'### {title}'))
    if predictions.empty:
        display(Markdown('**No completed runs in this stage. Check the horizon status below.**'))
        return
    summary = metrics(predictions)
    summary = summary.loc[summary.regime.eq('all'),
                          ['horizon', 'model', 'count', 'interval_score_bps', 'coverage']].copy()
    summary['model'] = summary.model.map(plots.label)
    summary = summary.rename(columns={'horizon': 'Horizon', 'model': 'Model', 'count': 'Outcomes scored',
                                      'interval_score_bps': 'Interval score (bps)', 'coverage': '80% interval coverage'})
    display(summary.style.hide(axis='index').format({'Interval score (bps)': '{:.2f}', '80% interval coverage': '{:.1%}'}))

display(pd.Series(dependency_versions(), name='installed_version').to_frame())
display(Markdown('CPU models use one XGBoost thread. Missing model dependencies prevent model selection; source audits still run.'))
""")
md("""## 3. Source downloads and data audit
Sources: [FRED SP500](https://fred.stlouisfed.org/series/SP500),
[Cboe volatility CSVs](https://www.cboe.com/tradable-products/vix/vix-historical-data),
[SPXW settlement records](https://www.cboe.com/index_settlement_values/weeklys_settlement_values/),
[SET records](https://www.cboe.com/index_settlement_values/),
[Cboe specifications](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications/),
[holidays](https://www.cboe.com/about/hours/us-options/), and dated Cboe expiration calendars.
Raw responses are content-addressed snapshots; normalized tables are Parquet. Source manifests retain
URLs, receipt timestamps, hashes and unknown publication timestamps. HTTP Last-Modified is not an
observation publication time. See `docs/imports.md` for verified official CSV imports.
FRED supplies roughly ten years of SP500 history. No alternative market-data provider is used.
""")
code("""state = workflow.prepare(CONFIG)
display(state['issues'])
display(state['audit'])
display(pd.DataFrame(state['plans']).T)
""")
code("""# Data coverage chart
figure = plots.coverage(state['datasets'], state['calendar'])
figure.savefig(CONFIG.output / 'data_coverage.png', bbox_inches='tight')
plt.show()
""")
code("""available_sources = [name for name in ('spx', 'vix', 'vix9d') if name in state['datasets']]
display(Markdown(f'Collected daily series: **{len(available_sources)}/3**. '
                 'Unknown missing-session counts require an official calendar; they are not zeros. '
                 'Pending requirements above must be resolved before interpreting model results.'))
""")
md("""## 4. Feature and label construction
One row per horizon/reference/expiration. The target is `10000 * log(official_settlement / reference_close)`
in log-return basis points. Features: 1/5/20-session log returns; sample realized volatility over 5/20
returns annualized by √252; VIX, VIX9D and VIX9D/VIX; close/20-session mean − 1; calendar days and
remaining regular sessions. Missing internal sessions invalidate complete windows; no forward fill.
Labels join on horizon, expiration **and settlement type**. Timing assumptions are retained on each row.
""")
code("""display(pd.DataFrame({'feature_order': FEATURES}))
for horizon, frame in state['observations'].items():
    display(Markdown(f'**{horizon.title()}: {len(frame)} usable feature rows**'))
    if len(frame):
        display(frame[['reference_date', 'expiration', 'reference_close', 'settlement', 'target',
                       'issue_time', 'label_available_at', 'timing_assumed']].tail(5))
""")
md("""## 5. Benchmarks and model definitions
Separate horizon models use three XGBRegressors with `reg:quantileerror`, α = 0.10/0.50/0.90,
histogram trees, CPU, learning rate 0.03, full sampling, L1=0, seed 42. Weekly evaluates all eight
depth {1,2} × trees {100,200} × L2 {10,50} configurations, child weight 30. Monthly uses depth 1,
50 trees, L2=50, child weight 20. One development configuration is used for all three quantiles.

Comparison candidates: StandardScaler + linear QuantileRegressor (`highs`, L1 α ∈ {0.1,1,10}),
empirical historical return quantiles, and volatility-scaled historical quantiles. Scale is
`(VIX9D or VIX)/100 * sqrt(calendar_days/365)`. Historical basis-point returns are divided by
their own scales, quantiles estimated in the training window, then multiplied by the issue's scale.
All learned preprocessing is fit inside each expanding training window. Crossed quantiles are sorted
before scoring; raw crossing frequency is reported. Levels equal `reference_close * exp(return_bps/10000)`.
""")
code("""from spx_forecast.models import candidates
for horizon in ('weekly', 'monthly'):
    display(Markdown(f'**{horizon.title()} candidate configurations**'))
    display(pd.DataFrame(candidates(horizon)).fillna('—'))
""")
md("""## 6. Training and development runs
Require five complete initial training years after warm-up, at least two development years, and the
latest two complete calendar years before `end` as final testing. Evaluation periods are assigned by
expiration year. Complete official eligibility records (including ineligible weeks), features and labels
are required; gaps leave the horizon pending. Expanding fits use only labels available at each issue.
Final-test configurations are frozen and exported before the holdout cell runs.

80% interval score is `(upper−lower) + 10*(lower−actual)` for lower misses and
`+ 10*(actual−upper)` for upper misses, in log-return basis points.
Coverage and miss rates use the same forecast population. Wilson intervals are 95% and assume
independent outcomes; serial dependence/overlap can make them too narrow. Volatility regimes use the
initial training window's fixed median VIX9D (weekly) or VIX (monthly).

The run plan below separates the required complete training years from development and final testing.
Each forecast refits its models on all prior available labels; earlier usable warm-up outcomes can also
be included. Progress messages report completed expirations and the number of past outcomes in each fit.
These are expanding fits, so there is no single epoch-based training run.
""")
code("""display(Markdown('### Training, development and test split'))
display(workflow.split_summary(state))
print('START: training + development evaluation', flush=True)
workflow.development(CONFIG, state, progress=show_run_progress)
development_predictions = workflow.predictions(state, 'development')
development_metrics = metrics(development_predictions)
show_run_results(development_predictions, 'Completed training/development runs')
display(pd.Series(state['statuses'], name='horizon_status').to_frame())
display(pd.DataFrame(state['frozen']).T)
""")
code("""# Development actual vs predicted settlement chart
figure = plots.actual_vs_prediction(development_predictions, state['frozen'], 'development')
figure.savefig(CONFIG.output / 'development_actual_vs_prediction.png', bbox_inches='tight')
plt.show()
""")
md("""## 7. Final test runs
Only the development-selected benchmark and ML candidate enter the final test. Refits remain expanding:
earlier holdout labels may train later issues only after publication. This is sequential evaluation,
not a single fixed model fit. Holdout results never tune hyperparameters.
""")
code("""print('START: training + final test evaluation using frozen configurations', flush=True)
workflow.final_test(CONFIG, state, progress=show_run_progress)
final_predictions = workflow.predictions(state, 'final')
final_metrics = metrics(final_predictions)
show_run_results(final_predictions, 'Completed final test runs')
""")
code("""# Final-test actual vs predicted settlement chart
figure = plots.actual_vs_prediction(final_predictions, state['frozen'], 'final_test')
figure.savefig(CONFIG.output / 'final_test_actual_vs_prediction.png', bbox_inches='tight')
plt.show()
""")
code("""# Final model comparison chart
figure = plots.comparisons(final_metrics)
figure.savefig(CONFIG.output / 'final_model_comparisons.png', bbox_inches='tight')
plt.show()
""")
md("""### Actual vs predicted by year: 2024, 2025 and 2026
Weekly and monthly charts with one panel per year. 2024-2025 are the final-test predictions above.
2026 lies after the frozen test window, so it is a **post-holdout extension**: the same frozen finalists
are refit before each forecast using only labels available at that issue time. It never tunes anything.
""")
code("""YEAR_PANELS, EXTENSION_YEAR = (2024, 2025, 2026), 2026
extension_predictions = workflow.post_holdout_predictions(CONFIG, state, EXTENSION_YEAR)
yearly_predictions = pd.concat([final_predictions, extension_predictions], ignore_index=True)
""")
code("""# Weekly actual vs predicted settlement, 2024-2026
figure = plots.yearly_actual_vs_prediction(yearly_predictions, state['frozen'], 'weekly',
                                           YEAR_PANELS, (EXTENSION_YEAR,))
figure.savefig(CONFIG.output / 'weekly_actual_vs_prediction_by_year.png', bbox_inches='tight', dpi=130)
plt.show()
""")
code("""# Monthly actual vs predicted settlement, 2024-2026
figure = plots.yearly_actual_vs_prediction(yearly_predictions, state['frozen'], 'monthly',
                                           YEAR_PANELS, (EXTENSION_YEAR,))
figure.savefig(CONFIG.output / 'monthly_actual_vs_prediction_by_year.png', bbox_inches='tight', dpi=130)
plt.show()
""")
md("""### Forecast ranges table: 2025 and 2026
The selected model's 10th percentile, median and 90th percentile settlement levels for every scored weekly and monthly expiration, next to the official settlement. `within_80` marks settlements inside the 10th–90th range. 2026 rows are the post-holdout extension. Saved to `artifacts/forecast_ranges_2025_2026.csv`.
""")
code("""# Selected-model 10th / median / 90th percentile settlement levels, 2025 and 2026
selected_range_table = workflow.forecast_range_table(yearly_predictions, state['selected'], (2025, 2026))
selected_range_table.to_csv(CONFIG.output / 'forecast_ranges_2025_2026.csv', index=False)
display(selected_range_table)
""")
md("""## 8. Model selection and forecast generation
Promote ML only if its paired final-test interval score is lower **and** its coverage is at least as
close to 80% as the frozen benchmark's coverage. Otherwise retain the benchmark. This final-test
promotion rule makes the selected model's reported holdout performance descriptive, not a new unbiased
estimate. The issue timestamp below is actual execution time, not a backdated scheduled timestamp.
Set `reference_date` to an official weekly or monthly reference session; both need not coincide.
Rerun this cell for one new availability check. It never waits, duplicates, or changes an issued row.
""")
code("""forecast_rows, forecast_status = workflow.forecast_once(CONFIG, state)
display(forecast_status)
display(forecast_rows)
display(pd.DataFrame(state['selected']).T)
""")
md("## 9. Results, limitations, and artifact exports")
code("""backtest_predictions, all_metrics = workflow.export(CONFIG, state)
display(all_metrics)
""")
code("""# Weekly 80% settlement interval chart (hover a date to see the 10th/90th percentile levels)
selected_key = model_key(state['selected']['weekly']) if 'weekly' in state['selected'] else None
figure = plots.outcomes(final_predictions, 'weekly', selected_key)
figure.savefig(CONFIG.output / 'weekly_settlement_intervals.png', bbox_inches='tight')
plt.close(figure)
plots.interactive_outcomes(final_predictions, 'weekly', selected_key)
""")
code("""# Monthly 80% settlement interval chart (hover a date to see the 10th/90th percentile levels)
selected_key = model_key(state['selected']['monthly']) if 'monthly' in state['selected'] else None
figure = plots.outcomes(final_predictions, 'monthly', selected_key)
figure.savefig(CONFIG.output / 'monthly_settlement_intervals.png', bbox_inches='tight')
plt.close(figure)
plots.interactive_outcomes(final_predictions, 'monthly', selected_key)
""")
code("""# Interval width chart
figure.savefig(CONFIG.output / 'interval_widths.png', bbox_inches='tight')
plt.show()
""")
code("""if final_metrics.empty:
    display(Markdown('**No verified final-test model results or settlement ranges are available in this run.** '
                     'Pending panels show unavailable evidence, not observed performance.'))
else:
    for horizon, selected in state['selected'].items():
        result = final_metrics.loc[final_metrics.horizon.eq(horizon) & final_metrics.regime.eq('all') &
                                   final_metrics.model.eq(model_key(selected))].iloc[0]
        display(Markdown(f\"**{horizon.title()}**: {int(result['count'])} forecasts; \"
                         f\"coverage {result.coverage:.1%}; mean interval score {result.interval_score_bps:.2f} bps.\"))
display(pd.Series(state['statuses'], name='remaining_requirements').to_frame())
display(pd.DataFrame({'artifact': sorted(p.name for p in CONFIG.output.iterdir() if p.is_file())}))
""")
md("""Historical unknown publication timing is an explicit assumption, not measured latency. FRED delivery
can miss the required 08:00 cutoff; verified late inputs suppress publication. Downloaded history may be
revised and is not a point-in-time vintage archive. Official calendar/specification changes must be
represented in the verified imports. Only verified dated SET values enter training; unresolved early
records remain separate from the scored population.
Intervals describe conditional settlement ranges, with no guarantee of future 80% coverage.

Exports: forecast CSV/availability status, backtest predictions, metrics, source manifest and audit,
frozen configurations, run metadata and figures in `artifacts/`; normalized Parquet in `data/normalized/`;
raw snapshots in `data/raw/`; fitted model and metadata artifacts only when supported models can be fit.
See `README.md` and `docs/imports.md` for setup, execution and pending requirements.
""")
nb.cells = cells
nb.metadata = {'kernelspec': {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'},
               'language_info': {'name': 'python', 'version': '3.13'}}
nbf.validate(nb)
destination = Path('notebooks/spx_forecast.ipynb')
destination.parent.mkdir(exist_ok=True)
if destination.exists():
    previous = nbf.read(destination, as_version=4)
    if len(previous.cells) == len(nb.cells):
        for old, new in zip(previous.cells, nb.cells):
            if old.cell_type == new.cell_type:
                new['id'] = old['id']
nbf.write(nb, destination)
print(f'Validated {len(cells)} cells: {destination}')
