import importlib.util

import numpy as np
import pandas as pd
import pytest

from spx_forecast.backtest import interval_score, training_rows, wilson, promote, model_key
from spx_forecast.calendar import OfficialCalendar, availability, contract_observations
from spx_forecast.features import build_observations, input_times, price_features, to_levels
from spx_forecast.ingestion import validate_settlements
from spx_forecast.models import QuantileModel, candidates, ordered


def contract(horizon='weekly', nominal='2024-03-29', expiration='2024-03-28', eligible=True):
    return dict(horizon=horizon, nominal_expiration=nominal, expiration=expiration,
                settlement_type='SPXW_PM' if horizon == 'weekly' else 'SET_AM',
                eligible=eligible, source_url='https://www.cboe.com/')


def test_holiday_shift_and_prior_final_session(calendar):
    schedule = contract_observations(pd.DataFrame([contract()]), calendar)
    assert schedule.iloc[0].reference_date == pd.Timestamp('2024-03-22')
    assert schedule.iloc[0].expiration == pd.Timestamp('2024-03-28')
    assert schedule.iloc[0].remaining_sessions == 4
    next_week = contract_observations(pd.DataFrame([contract(nominal='2024-04-05', expiration='2024-04-05')]), calendar)
    assert next_week.iloc[0].reference_date == pd.Timestamp('2024-03-28')


def test_ineligible_week_skipped(calendar):
    assert contract_observations(pd.DataFrame([contract(eligible=False)]), calendar).empty


def test_monthly_uses_previous_actual_session(calendar):
    frame = pd.DataFrame([contract('monthly', '2024-03-15', '2024-03-15'),
                          contract('monthly', '2024-04-19', '2024-04-19')])
    result = contract_observations(frame, calendar)
    assert len(result) == 1
    assert result.iloc[0].reference_date == pd.Timestamp('2024-03-15')


def test_calendar_missing_day_rejected(calendar):
    with pytest.raises(ValueError, match='every calendar day'):
        OfficialCalendar(calendar.frame.iloc[1:-1].drop(index=10))


def test_availability_cutoffs_and_dst(calendar):
    ref = pd.Timestamp('2024-03-22')
    earliest = calendar.time(ref, 20)
    cutoff = calendar.cutoff(ref)
    assert earliest.hour == 0  # UTC after US daylight-saving transition.
    assert cutoff == pd.Timestamp('2024-03-25T12:00:00Z')
    assert availability(ref, [earliest], True, earliest - pd.Timedelta(seconds=1), calendar)[0] == 'not_yet_eligible'
    assert availability(ref, [earliest], True, earliest, calendar)[0] == 'ready'
    assert availability(ref, [], False, earliest, calendar)[0] == 'waiting_for_inputs'
    assert availability(ref, [], False, cutoff, calendar)[0] == 'suppressed_incomplete'
    assert availability(ref, [cutoff + pd.Timedelta(seconds=1)], True, cutoff, calendar)[0] == 'suppressed_late_inputs'


def test_historical_assumption_and_live_receipt(calendar, datasets):
    complete, assumed, times = input_times(pd.Timestamp('2024-03-22'), datasets, calendar)
    assert complete and assumed
    assert max(times) == calendar.cutoff('2024-03-22')
    complete, assumed, times = input_times(pd.Timestamp('2024-03-22'), datasets, calendar, live=True)
    assert availability('2024-03-22', times, complete, pd.Timestamp('2024-04-01T12:00Z'), calendar)[0] == 'suppressed_late_inputs'


def test_missing_internal_session_invalidates_features(calendar, datasets):
    datasets['spx'].loc[datasets['spx'].date.eq('2024-03-12'), 'value'] = np.nan
    daily = price_features(datasets, calendar)
    assert pd.isna(daily.loc['2024-03-22', 'return_20'])
    assert not input_times(pd.Timestamp('2024-03-22'), datasets, calendar)[0]


def test_settlement_join_and_unknown_label_availability(calendar, datasets):
    schedule = contract_observations(pd.DataFrame([contract()]), calendar)
    labels = pd.DataFrame([dict(horizon='weekly', expiration='2024-03-28',
                               settlement_type='SPXW_PM', settlement=4300,
                               source_url='https://www.cboe.com/')])
    datasets['settlements'] = validate_settlements(labels)
    result = build_observations(schedule, price_features(datasets, calendar), datasets, calendar)['weekly']
    assert len(result) == 1
    row = result.iloc[0]
    assert row.target == pytest.approx(10000 * np.log(4300 / row.reference_close))
    assert row.label_available_at == calendar.time('2024-04-01', 0)
    datasets['settlements']['settlement_type'] = 'SET_AM'
    result = build_observations(schedule, price_features(datasets, calendar), datasets, calendar)['weekly']
    assert result.target.isna().all()


def test_duplicate_or_wrong_type_labels_rejected():
    labels = pd.DataFrame([dict(horizon='monthly', expiration='2024-03-15', settlement_type='SET_AM',
                               settlement=5000, source_url='https://www.cboe.com/')])
    with pytest.raises(ValueError, match='Duplicate'):
        validate_settlements(pd.concat([labels, labels]))
    labels['settlement_type'] = 'SPXW_PM'
    with pytest.raises(ValueError, match='positive official'):
        validate_settlements(labels)


def test_no_future_label_leakage():
    frame = pd.DataFrame({'target': [1, 2, np.nan],
                          'issue_time': pd.to_datetime(['2024-01-01T20:00Z']*3),
                          'label_available_at': pd.to_datetime(['2024-01-02T00:00Z', '2024-01-10T00:00Z', None])})
    train = training_rows(frame, pd.Timestamp('2024-01-05T20:00Z'))
    assert train.target.tolist() == [1]


def test_quantile_ordering_and_conversion():
    q, crossed = ordered([[100, -100, 0], [-2, 0, 2]])
    assert crossed.tolist() == [True, False]
    np.testing.assert_array_equal(q[0], [-100, 0, 100])
    np.testing.assert_allclose(to_levels(5000, q[0]), 5000*np.exp(np.array([-100, 0, 100])/10000))
    with pytest.raises(ValueError):
        to_levels(0, [1])


def test_scaled_quantiles_preserve_basis_points(train_frame):
    model = QuantileModel({'family': 'scaled'}).fit(train_frame)
    prediction, _ = model.predict(train_frame.iloc[:1])
    expected = np.quantile(train_frame.target/train_frame.scale, [0.1, 0.5, 0.9]) * train_frame.scale.iloc[0]
    np.testing.assert_allclose(prediction[0], expected)


def test_linear_scaler_only_training_window(train_frame):
    model = QuantileModel({'family': 'linear', 'alpha': 0.1}).fit(train_frame)
    scaler = model.estimators[0].steps[0][1]
    original = scaler.mean_.copy()
    extreme = train_frame.iloc[:1].copy()
    extreme.iloc[:, :11] = 1e6
    model.predict(extreme)
    np.testing.assert_allclose(scaler.mean_, original)


def test_score_wilson_and_candidate_count():
    np.testing.assert_allclose(interval_score([0, -2, 2], [-1]*3, [1]*3), [2, 12, 12])
    lo, hi = wilson(80, 100)
    assert lo < 0.8 < hi
    assert len([c for c in candidates('weekly') if c['family'] == 'xgb']) == 8
    assert len([c for c in candidates('monthly') if c['family'] == 'xgb']) == 1


def test_insufficient_history_remains_pending(calendar, datasets):
    from spx_forecast.backtest import history_plan
    obs = pd.DataFrame({'reference_date': pd.to_datetime(['2024-03-22']),
                        'expiration': pd.to_datetime(['2024-03-28']), 'target': [10.0]})
    contracts = pd.DataFrame([contract()])
    plan = history_plan('weekly', obs, contracts, calendar, datasets, '2025-12-31')
    assert plan['status'] == 'pending'
    assert 'five complete training years' in plan['reason']
    assert 'contract periods' in plan['reason']


def test_ml_promotion_requires_score_and_calibration():
    from spx_forecast.backtest import promote
    frozen = {'benchmark': {'family': 'empirical'}, 'ml': {'family': 'linear', 'alpha': 0.1}}
    rows = []
    for key, width, misses in [('benchmark', 10.0, 2), ('ml', 1.0, 4)]:
        for i in range(10):
            target = width + 0.01 if i < misses else 0.0
            rows.append(dict(horizon='weekly', stage='final_test', model=model_key(frozen[key]),
                             regime='above', target=target, q10=-width, q50=0.0, q90=width,
                             lower=-width, median=0.0, upper=width, actual_level=target, crossed=False))
    predictions = pd.DataFrame(rows)
    assert promote(predictions, frozen) == frozen['benchmark']
    # Better score with identical 80% coverage can be promoted.
    predictions.loc[predictions.model.eq(model_key(frozen['ml'])) & predictions.index.isin([12, 13]), ['target', 'actual_level']] = 0.0
    assert promote(predictions, frozen) == frozen['ml']


@pytest.mark.skipif(importlib.util.find_spec('xgboost') is None, reason='XGBoost installation blocked by host network')
def test_xgboost_cpu_quantiles(train_frame):
    config = candidates('monthly')[-1]
    model = QuantileModel(config).fit(train_frame)
    prediction, _ = model.predict(train_frame.iloc[:2])
    assert prediction.shape == (2, 3)
    assert np.all(np.diff(prediction, axis=1) >= 0)
    assert model.estimators[0].get_params()['device'] == 'cpu'
