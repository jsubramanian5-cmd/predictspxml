"""Weekly and monthly actual-vs-prediction charts for 2024-2026.

2024-2025 come from the saved final-test run. 2026 lies after the frozen test window, so it is
scored here with the same frozen finalists, refit before each forecast on labels available then.
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from spx_forecast import plots  # noqa: E402
from spx_forecast.backtest import expanding  # noqa: E402

YEARS, EXTENSION_YEARS = (2024, 2025, 2026), (2026,)


def main():
    from spx_forecast.config import Config
    seed = Config().seed
    frozen = json.loads((ROOT/'artifacts/frozen_development.json').read_text())
    saved = pd.read_csv(ROOT/'artifacts/backtest_predictions.csv', parse_dates=['expiration'])
    saved = saved.loc[saved.stage.eq('final_test')]
    for horizon in ('weekly', 'monthly'):
        observations = pd.read_parquet(ROOT/f'data/normalized/observations_{horizon}.parquet')
        scorable = observations.loc[observations.expiration.dt.year.eq(2026) & observations.target.notna()
                                    & observations.issue_time.notna()]
        spec = frozen[horizon]
        extension = expanding(observations, scorable, [spec['benchmark'], spec['ml']],
                              spec['volatility_threshold'], seed, 'post_holdout')
        extension['expiration'] = pd.to_datetime(extension.expiration)
        combined = pd.concat([saved.loc[saved.horizon.eq(horizon)], extension], ignore_index=True)
        out = ROOT/f'artifacts/{horizon}_actual_vs_prediction_by_year'
        combined.to_csv(out.with_suffix('.csv'), index=False)
        figure = plots.yearly_actual_vs_prediction(combined, frozen, horizon, YEARS, EXTENSION_YEARS)
        figure.savefig(out.with_suffix('.png'), bbox_inches='tight', dpi=130)
        print(horizon, {y: int(combined.loc[combined.expiration.dt.year.eq(y)].expiration.nunique()) for y in YEARS})


if __name__ == '__main__':
    main()
