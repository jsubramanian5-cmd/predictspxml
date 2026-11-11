"""GARCH and HAR-RV scales, checked on synthetic returns only."""
import numpy as np
import pandas as pd
import pytest

from spx_forecast.models import QuantileModel, candidates
from spx_forecast.volatility import (MIN_RETURNS, garch_fit, garch_total_variance, har_total_variance,
                                     volatility_scales)


def _garch_returns(count, persistence=0.95, share=0.1, long_run=1.0, seed=7):
    rng = np.random.default_rng(seed)
    alpha, beta = persistence * share, persistence * (1 - share)
    returns, variance = np.empty(count), long_run
    for i in range(count):
        returns[i] = np.sqrt(variance) * rng.standard_normal()
        variance = long_run * (1 - persistence) + alpha * returns[i] ** 2 + beta * variance
    return returns


def test_garch_recovers_persistence_and_forecasts_revert_to_the_long_run_level():
    squared = _garch_returns(6000) ** 2
    persistence, share = garch_fit(squared)
    assert 0.92 < persistence < 0.98 and 0.05 < share < 0.16
    # After a shock the next sessions are forecast above average, and the excess fades with the horizon.
    shocked = np.append(squared, 25 * squared.mean())
    week, quarter = (garch_total_variance(shocked, n, (persistence, share)) / n for n in (5, 63))
    assert week > quarter > shocked.mean() * 0.99
    calm = np.append(squared, np.zeros(60))
    assert garch_total_variance(calm, 5, (persistence, share)) / 5 < calm.mean()


def test_har_matches_constant_variance_and_respects_its_floor():
    rng = np.random.default_rng(3)
    squared = (1.2 * rng.standard_normal(3000)) ** 2
    assert har_total_variance(squared, 5, 5) == pytest.approx(5 * 1.44, rel=0.25)
    assert har_total_variance(squared, 21, 21) == pytest.approx(21 * 1.44, rel=0.25)
    assert np.isnan(har_total_variance(squared[:MIN_RETURNS], 5, 5))  # too little history to fit
    # A long dead-calm stretch cannot drive the forecast to zero or below.
    quiet = np.append(squared, np.zeros(30))
    assert har_total_variance(quiet, 5, 5) >= 5 * 0.1 * quiet.mean()


def _closes(count=900):
    sessions = pd.bdate_range('2020-01-01', periods=count)
    return pd.Series(4000 * np.exp(np.cumsum(_garch_returns(count, seed=11) / 100)), index=sessions)


def test_scales_use_no_data_after_the_reference_session_and_wait_for_enough_history():
    closes = _closes()
    sessions = closes.index
    observations = pd.DataFrame({
        'horizon': ['weekly', 'weekly', 'monthly', 'weekly'],
        'reference_date': [sessions[100], sessions[600], sessions[600], sessions[800]],
        'remaining_sessions': [5, 5, 21, 4]})
    scales = volatility_scales(observations, closes)
    assert scales.iloc[0].isna().all()  # under a year of returns
    assert (scales.iloc[1:] > 0).all().all()
    # A month spans more sessions than a week from the same reference date.
    assert (scales.iloc[2] > scales.iloc[1]).all()
    # Rewriting every close after session 600 changes nothing about the forecasts made on it.
    altered = closes.copy()
    altered.iloc[601:] *= np.linspace(1.0, 1.6, len(altered) - 601)
    again = volatility_scales(observations, altered)
    pd.testing.assert_frame_equal(scales.iloc[:3], again.iloc[:3])
    assert not np.allclose(scales.iloc[3], again.iloc[3])
    # Same answer whether or not earlier reference dates were estimated first (the warm start is only a start).
    alone = volatility_scales(observations.iloc[[3]], closes)
    np.testing.assert_allclose(alone.iloc[0], scales.iloc[3], rtol=1e-3)


def test_garch_and_har_models_scale_quantiles_and_skip_rows_without_a_scale(train_frame):
    frame = train_frame.copy()
    frame['garch_scale'] = np.linspace(0.01, 0.02, len(frame))
    frame['har_scale'] = np.linspace(0.02, 0.01, len(frame))
    frame.loc[frame.index[:5], ['garch_scale', 'har_scale']] = np.nan
    for family, column in (('garch', 'garch_scale'), ('har', 'har_scale')):
        model = QuantileModel({'family': family}).fit(frame)
        usable = frame.iloc[5:]
        expected = np.quantile(usable.target / usable[column], [0.1, 0.5, 0.9]) * frame[column].iloc[-1]
        np.testing.assert_allclose(model.predict(frame.iloc[-1:])[0][0], expected)
        with pytest.raises(ValueError, match='volatility scale'):
            model.predict(frame.iloc[:1])
        with pytest.raises(ValueError, match='No training rows'):
            QuantileModel({'family': family}).fit(frame.iloc[:5])


def test_garch_and_har_are_candidates_for_both_horizons():
    for horizon in ('weekly', 'monthly'):
        families = [config['family'] for config in candidates(horizon)]
        assert families.count('garch') == families.count('har') == 1
