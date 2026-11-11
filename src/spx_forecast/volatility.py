"""Point-in-time volatility forecasts from daily SPX closes: GARCH(1,1) and HAR-RV.

Each forecast uses only returns up to and including its reference session, and is re-estimated at every
reference date. The result is a scale in log-return units over the contract's remaining sessions, used
exactly like the implied-volatility `scale`: historical returns are divided by their own scale and the
quantiles are multiplied by the new contract's scale (see `models.QuantileModel`).

Only daily closes are available, so HAR-RV uses squared daily returns as its realized-variance measure
rather than intraday realized variance.
"""
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.signal import lfilter

MIN_RETURNS = 250            # about one year of daily returns before either model is estimated
HAR_WINDOWS = (1, 5, 22)     # daily, weekly and monthly averages of squared returns
HAR_STEPS = {"weekly": 5, "monthly": 21}   # sessions ahead the HAR regression is fitted to predict
HAR_FLOOR = 0.1              # forecast variance is kept above this share of the average variance to date
GARCH_BOUNDS = [(0.5, 0.999), (0.01, 0.5)]   # persistence alpha+beta, and alpha's share of it
GARCH_START = (0.95, 0.1)
SCALE_COLUMNS = ["garch_scale", "har_scale"]


def garch_variance(squared, persistence, share):
    """Conditional variances of a variance-targeted GARCH(1,1); the last element is the next session's."""
    alpha, beta = persistence * share, persistence * (1 - share)
    long_run = squared.mean()
    drive = np.empty(len(squared) + 1)
    drive[0] = long_run
    drive[1:] = long_run * (1 - persistence) + alpha * squared
    return lfilter([1.0], [1.0, -beta], drive), long_run


def garch_fit(squared, start=GARCH_START):
    """Gaussian quasi-maximum-likelihood (persistence, alpha share), from `start`."""
    def loss(parameters):
        variance = garch_variance(squared, *parameters)[0][:-1]
        return float(np.sum(np.log(variance) + squared / variance))
    result = minimize(loss, start, method="L-BFGS-B", bounds=GARCH_BOUNDS)
    return tuple(float(v) for v in result.x)


def garch_total_variance(squared, sessions, parameters):
    """Expected sum of the next `sessions` daily variances."""
    variance, long_run = garch_variance(squared, *parameters)
    persistence = parameters[0]
    return sessions * long_run + (variance[-1] - long_run) * (1 - persistence ** sessions) / (1 - persistence)


def har_design(squared):
    """Intercept plus trailing daily/weekly/monthly mean squared returns, one row per session (NaN in warm-up)."""
    series = pd.Series(squared)
    columns = [series.rolling(window, min_periods=window).mean().to_numpy() for window in HAR_WINDOWS]
    return np.column_stack([np.ones(len(squared))] + columns)


def har_total_variance(squared, sessions, steps):
    """HAR regression of the next `steps` sessions' mean variance, applied to the next `sessions` sessions."""
    design = har_design(squared)
    cumulative = np.concatenate([[0.0], np.cumsum(squared)])
    last = len(squared) - 1
    # Rows whose forward window ends by the latest return: nothing after the reference session is used.
    rows = np.arange(max(HAR_WINDOWS) - 1, last - steps + 1)
    if len(rows) < MIN_RETURNS:
        return np.nan
    future = (cumulative[rows + steps + 1] - cumulative[rows + 1]) / steps
    coefficients = np.linalg.lstsq(design[rows], future, rcond=None)[0]
    daily = max(float(design[last] @ coefficients), HAR_FLOOR * float(squared.mean()))
    return sessions * daily


def volatility_scales(observations, closes):
    """`garch_scale` and `har_scale` for each observation row; NaN where history is too short.

    `closes` is the SPX close by regular session (NaN where missing). Returns are in percent while fitting and
    the scales are converted back to plain log-return units.
    """
    returns = (100 * np.log(closes.where(closes > 0)).diff()).dropna()
    position = {date: i for i, date in enumerate(returns.index)}
    squared_all = returns.to_numpy() ** 2
    result = pd.DataFrame(np.nan, index=observations.index, columns=SCALE_COLUMNS)
    parameters, fitted = GARCH_START, {}
    for reference in sorted(set(observations.reference_date)):
        count = position.get(reference, -1) + 1
        if count < MIN_RETURNS:
            continue
        # Warm-started from the previous reference date's estimate; the data, not the start, decides the fit.
        parameters = garch_fit(squared_all[:count], parameters)
        fitted[reference] = (count, parameters)
    for index, row in observations.iterrows():
        if row.reference_date not in fitted or not row.remaining_sessions > 0:
            continue
        count, garch = fitted[row.reference_date]
        squared, sessions = squared_all[:count], int(row.remaining_sessions)
        result.loc[index, "garch_scale"] = np.sqrt(garch_total_variance(squared, sessions, garch)) / 100
        har = har_total_variance(squared, sessions, HAR_STEPS[row.horizon])
        result.loc[index, "har_scale"] = np.sqrt(har) / 100 if np.isfinite(har) else np.nan
    return result
