"""Independently check exported backtest arithmetic, cohorts and selection decisions."""
import json
from pathlib import Path

import numpy as np
import pandas as pd


def main():
    root = Path('artifacts')
    predictions = pd.read_csv(root/'backtest_predictions.csv')
    scores = pd.read_csv(root/'metrics.csv')
    metadata = json.loads((root/'run_metadata.json').read_text())
    frozen = json.loads((root/'frozen_development.json').read_text())
    if predictions.empty or scores.empty:
        raise ValueError('No completed market-data backtest to validate')
    for column in ['issue_time', 'training_cutoff']:
        predictions[column] = pd.to_datetime(predictions[column], utc=True)
    predictions['expiration'] = pd.to_datetime(predictions.expiration)
    assert not predictions.duplicated(['horizon', 'stage', 'model', 'expiration']).any()
    assert (predictions.training_cutoff <= predictions.issue_time).all()
    numeric = ['target', 'q10', 'q50', 'q90', 'lower', 'median', 'upper', 'actual_level', 'reference_close']
    assert np.isfinite(predictions[numeric].to_numpy()).all()
    assert (predictions.q10 <= predictions.q50).all() and (predictions.q50 <= predictions.q90).all()
    np.testing.assert_allclose(predictions.target,
                               10000*np.log(predictions.actual_level/predictions.reference_close), atol=1e-8)
    for quantile, level in [('q10', 'lower'), ('q50', 'median'), ('q90', 'upper')]:
        np.testing.assert_allclose(predictions[level],
                                   predictions.reference_close*np.exp(predictions[quantile]/10000), atol=1e-8)

    # Recompute scores directly rather than calling the application's metrics helper.
    for row in scores.itertuples(index=False):
        cohort = predictions.loc[predictions.horizon.eq(row.horizon) & predictions.stage.eq(row.stage) &
                                 predictions.model.eq(row.model)]
        if row.regime != 'all':
            cohort = cohort.loc[cohort.regime.eq(row.regime)]
        below, above = cohort.target.lt(cohort.q10), cohort.target.gt(cohort.q90)
        score = (cohort.q90-cohort.q10 + 10*np.maximum(cohort.q10-cohort.target, 0) +
                 10*np.maximum(cohort.target-cohort.q90, 0)).mean()
        assert len(cohort) == row.count
        np.testing.assert_allclose([row.interval_score_bps, row.coverage, row.lower_miss_rate, row.upper_miss_rate],
                                   [score, (~(below | above)).mean(), below.mean(), above.mean()], atol=1e-9)

    selections, counts = {}, {}
    for horizon in ('weekly', 'monthly'):
        observations = pd.read_parquet(f'data/normalized/observations_{horizon}.parquet')
        plan = metadata['plans'][horizon]
        assert plan['status'] == 'ready'
        development_scores = scores.loc[scores.horizon.eq(horizon) & scores.stage.eq('development') &
                                        scores.regime.eq('all')].sort_values(['interval_score_bps', 'model'])
        is_benchmark = development_scores.model.map(lambda key: json.loads(key)['family'] in ('empirical', 'scaled'))
        for kind, mask in [('benchmark', is_benchmark), ('ml', ~is_benchmark)]:
            assert frozen[horizon][kind] == json.loads(development_scores.loc[mask].iloc[0].model)
        for stage, start, end in [
                ('development', pd.Timestamp(plan['development_start']), pd.Timestamp(plan['test_start'])-pd.Timedelta(days=1)),
                ('final_test', pd.Timestamp(plan['test_start']), pd.Timestamp(plan['test_end']))]:
            cohort = predictions.loc[predictions.horizon.eq(horizon) & predictions.stage.eq(stage)]
            expected = set(observations.loc[observations.expiration.between(start, end) &
                                            observations.target.notna(), 'expiration'])
            for model, group in cohort.groupby('model'):
                assert set(group.expiration) == expected
            expected_models = 13 if horizon == 'weekly' else 6
            assert cohort.model.nunique() == (expected_models if stage == 'development' else 2)
            counts[f'{horizon}_{stage}'] = dict(observations=len(expected), model_configurations=cohort.model.nunique(),
                                               predictions=len(cohort))
            # Join predictions back to the actual settlement type/date, not a close proxy.
            source = observations.set_index('expiration')
            np.testing.assert_allclose(cohort.actual_level, source.loc[cohort.expiration, 'settlement'], atol=1e-8)
            assert cohort.settlement_type.to_numpy().tolist() == source.loc[cohort.expiration, 'settlement_type'].tolist()
        final_scores = scores.loc[scores.horizon.eq(horizon) & scores.stage.eq('final_test') & scores.regime.eq('all')]
        benchmark_key = json.dumps(frozen[horizon]['benchmark'], sort_keys=True, separators=(',', ':'))
        ml_key = json.dumps(frozen[horizon]['ml'], sort_keys=True, separators=(',', ':'))
        assert set(final_scores.model) == {benchmark_key, ml_key}
        final_scores = final_scores.set_index('model')
        benchmark, ml = final_scores.loc[benchmark_key], final_scores.loc[ml_key]
        promote = ml.interval_score_bps < benchmark.interval_score_bps and abs(ml.coverage-0.8) <= abs(benchmark.coverage-0.8)
        expected_selection = frozen[horizon]['ml' if promote else 'benchmark']
        assert metadata['selected'][horizon] == expected_selection
        selections[horizon] = dict(selected=expected_selection, promoted_ml=bool(promote),
                                   benchmark_score=float(benchmark.interval_score_bps), ml_score=float(ml.interval_score_bps),
                                   benchmark_coverage=float(benchmark.coverage), ml_coverage=float(ml.coverage))
        model_info = json.loads((root/f'models/{horizon}-selected.json').read_text())
        assert model_info['configuration'] == expected_selection
        assert pd.Timestamp(model_info['training_cutoff']) <= pd.Timestamp(model_info['fitted_as_of'])
    receipt = dict(prediction_rows=len(predictions), metrics_rows=len(scores), counts=counts, selections=selections,
                   checks='Arithmetic, units, quantile ordering, per-model cohorts, outcome joins, training cutoffs, development winners, final-only finalists and promotion decisions passed.',
                   limitations=['Historical publication timing assumptions remain; current history is not a vintage archive.',
                                'This does not independently refit every estimator or establish live trading performance.',
                                'Final-test-based promotion is not an additional untouched performance test.'])
    (root/'backtest_validation.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
