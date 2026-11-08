"""Chronological development, frozen candidates, and sequential final holdout."""
import json

import numpy as np
import pandas as pd

from .features import FEATURES, price_features, to_levels
from .models import QuantileModel, candidates


def interval_score(y, lower, upper):
    y, lower, upper = map(np.asarray, (y, lower, upper))
    return upper - lower + 10 * (lower - y) * (y < lower) + 10 * (y - upper) * (y > upper)


def wilson(successes, n, z=1.959963984540054):
    if n == 0:
        return np.nan, np.nan
    p = successes / n
    center = (p + z*z/(2*n)) / (1 + z*z/n)
    radius = z * np.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
    return center - radius, center + radius


def metrics(predictions):
    if predictions.empty:
        return pd.DataFrame()
    rows = []
    for (horizon, stage, model, regime), group in predictions.groupby(["horizon", "stage", "model", "regime"], observed=True):
        rows.append(_metrics(group, horizon, stage, model, regime))
    for (horizon, stage, model), group in predictions.groupby(["horizon", "stage", "model"]):
        rows.append(_metrics(group, horizon, stage, model, "all"))
    return pd.DataFrame(rows)


def _metrics(group, horizon, stage, model, regime):
    lower_miss = group.target < group.q10
    upper_miss = group.target > group.q90
    covered = ~(lower_miss | upper_miss)
    lo, hi = wilson(int(covered.sum()), len(group))
    return dict(horizon=horizon, stage=stage, model=model, regime=regime,
                interval_score_bps=float(interval_score(group.target, group.q10, group.q90).mean()),
                coverage=float(covered.mean()), lower_miss_rate=float(lower_miss.mean()),
                upper_miss_rate=float(upper_miss.mean()),
                median_width_points=float((group.upper - group.lower).median()),
                median_absolute_error_points=float((group.actual_level - group['median']).abs().median()),
                median_absolute_error_bps=float((group.target - group.q50).abs().median()),
                count=len(group), coverage_wilson_lower=lo, coverage_wilson_upper=hi,
                crossing_frequency=float(group.crossed.mean()))


def training_rows(observations, issue_time):
    return observations.loc[observations.target.notna() &
                            (observations.label_available_at <= issue_time) &
                            (observations.issue_time < issue_time)].copy()


def history_plan(horizon, observations, contracts, calendar, datasets, end):
    """Reject incomplete periods instead of silently shortening the requested test."""
    last_year = pd.Timestamp(end).year - 1
    test_start = pd.Timestamp(last_year - 1, 1, 1)
    test_end = pd.Timestamp(last_year, 12, 31)
    reasons = []
    if observations.empty:
        return {"status": "pending", "reason": "No usable verified observations"}
    warm_features = price_features(datasets, calendar)
    required_features = [c for c in FEATURES if c not in ("calendar_days", "remaining_sessions")]
    complete_dates = warm_features.loc[warm_features[required_features].notna().all(axis=1)].index
    if not len(complete_dates):
        return {"status": "pending", "reason": "No complete feature windows after warm-up"}
    earliest = complete_dates.min()
    first_year = earliest.year + (earliest > pd.Timestamp(earliest.year, 1, 1))
    # The first usable session may be Jan 2/3: allow the year if no prior regular
    # session exists that year and complete feature windows are already available.
    year_sessions = calendar.sessions[calendar.sessions.year == earliest.year]
    if len(year_sessions) and earliest == year_sessions[0]:
        first_year = earliest.year
    dev_start = pd.Timestamp(first_year + 5, 1, 1)
    if test_start.year - dev_start.year < 2:
        reasons.append(f"Need five complete training years and >=2 development years before {test_start.year}; first complete usable year is {first_year}")
    start = pd.Timestamp(first_year, 1, 1)
    try:
        calendar.require_span(start, test_end + pd.Timedelta(days=7))
    except ValueError as exc:
        reasons.append(str(exc))
    contract = contracts.loc[contracts.horizon.eq(horizon)].copy()
    nominal = pd.to_datetime(contract.nominal_expiration)
    if horizon == "weekly":
        expected = pd.date_range(start, test_end, freq="W-FRI").to_period("W-SUN")
        actual = pd.PeriodIndex(nominal, freq="W-SUN")
    else:
        expected = pd.period_range(start, test_end, freq="M")
        actual = pd.PeriodIndex(nominal, freq="M")
    missing = expected.difference(actual)
    if len(missing):
        reasons.append(f"{len(missing)} contract periods lack explicit official eligibility records")
    enabled = contract.eligible.astype(str).str.lower().isin(["true", "1"])
    contract_dates = pd.to_datetime(contract.expiration)
    required = set(contract_dates[enabled & contract_dates.between(start, test_end)])
    available = set(observations.loc[observations.target.notna(), "expiration"])
    if required - available:
        reasons.append(f"{len(required - available)} eligible contracts missing labels/features/valid issue timing")
    for year in range(first_year, last_year + 1):
        if not any(date.year == year for date in required):
            reasons.append(f"No eligible {horizon} contracts in {year}")
    # Missing daily inputs are disclosed even when no forecast reference hits the gap.
    sessions = calendar.sessions[(calendar.sessions >= start) & (calendar.sessions <= test_end)]
    for name in ("spx", "vix", "vix9d"):
        source = datasets[name]
        valid = pd.DatetimeIndex(source.loc[source.value.gt(0), "date"])
        gap = sessions.difference(valid)
        if len(gap):
            reasons.append(f"{name}: {len(gap)} missing regular sessions in required history")
    return dict(status="pending" if reasons else "ready", reason="; ".join(reasons),
                training_start=start, development_start=dev_start,
                test_start=test_start, test_end=test_end)


def model_key(config):
    return json.dumps(config, sort_keys=True, separators=(",", ":"))


def expanding(observations, evaluation, configurations, threshold, seed, stage, progress=None):
    rows = []
    for completed, (_, row) in enumerate(evaluation.iterrows(), 1):
        train = training_rows(observations, row.issue_time)
        if train.empty:
            raise ValueError("No labels available at evaluation issue")
        for config in configurations:
            model = QuantileModel(config, seed).fit(train)
            forecast, crossing = model.predict(row.to_frame().T)
            q = forecast[0]
            levels = to_levels(row.reference_close, q)
            vol = row.vix9d if row.horizon == "weekly" else row.vix
            rows.append(dict(horizon=row.horizon, reference_date=row.reference_date,
                             issue_time=row.issue_time, expiration=row.expiration,
                             settlement_type=row.settlement_type, reference_close=row.reference_close,
                             target=row.target, actual_level=row.settlement,
                             q10=q[0], q50=q[1], q90=q[2], lower=levels[0], median=levels[1], upper=levels[2],
                             crossed=bool(crossing[0]), model=model_key(config), stage=stage,
                             regime="above" if vol > threshold else "below_or_equal",
                             timing_assumed=bool(row.timing_assumed),
                             training_assumed_inputs=bool(train.timing_assumed.any()),
                             training_assumed_labels=bool(train.label_timing_assumed.any()),
                             training_count=len(train), training_cutoff=train.label_available_at.max()))
        if progress is not None:
            progress(dict(horizon=row.horizon, stage=stage, completed=completed, total=len(evaluation),
                          models=len(configurations), training_count=len(train), issue_time=row.issue_time,
                          training_cutoff=train.label_available_at.max(), expiration=row.expiration))
    return pd.DataFrame(rows)


def develop(observations, plan, horizon, seed, progress=None):
    selected = observations.loc[observations.expiration.between(plan["development_start"], plan["test_start"] - pd.Timedelta(days=1))]
    initial = observations.loc[observations.expiration.between(plan["training_start"], plan["development_start"] - pd.Timedelta(days=1))]
    threshold = float(initial["vix9d" if horizon == "weekly" else "vix"].median())
    configs = candidates(horizon)
    prediction = expanding(observations, selected, configs, threshold, seed, "development", progress=progress)
    scores = metrics(prediction)
    overall = scores.loc[scores.regime.eq("all")].sort_values(["interval_score_bps", "model"])
    benchmark_keys = {model_key(c) for c in configs if c["family"] in ("empirical", "scaled")}
    benchmark = json.loads(overall.loc[overall.model.isin(benchmark_keys)].iloc[0].model)
    ml = json.loads(overall.loc[~overall.model.isin(benchmark_keys)].iloc[0].model)
    # Both families and a single common configuration for all quantiles are frozen here.
    return prediction, dict(benchmark=benchmark, ml=ml, volatility_threshold=threshold, plan=plan)


def holdout(observations, frozen, seed, progress=None):
    plan = frozen["plan"]
    evaluation = observations.loc[observations.expiration.between(plan["test_start"], plan["test_end"])]
    return expanding(observations, evaluation, [frozen["benchmark"], frozen["ml"]],
                     frozen["volatility_threshold"], seed, "final_test", progress=progress)


def promote(final_predictions, frozen):
    score = metrics(final_predictions)
    score = score.loc[score.regime.eq("all")].set_index("model")
    benchmark, ml = (score.loc[model_key(frozen[k])] for k in ("benchmark", "ml"))
    if int(benchmark["count"]) != int(ml["count"]) or int(ml["count"]) == 0:
        raise ValueError("Promotion requires paired, nonempty final-test forecasts")
    improve = ml.interval_score_bps < benchmark.interval_score_bps
    calibrated = abs(ml.coverage - 0.8) <= abs(benchmark.coverage - 0.8)
    return frozen["ml"] if improve and calibrated else frozen["benchmark"]
