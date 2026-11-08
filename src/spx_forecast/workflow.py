"""Small notebook-facing orchestration, with explicit independent horizon states."""
import json
import pickle

import pandas as pd

from .backtest import develop, history_plan, holdout, metrics, model_key, promote, training_rows
from .calendar import OfficialCalendar, contract_observations, availability
from .features import FEATURES, build_observations, input_times, price_features
from .forecast import FORECAST_COLUMNS, dependency_versions, publish_forecast
from .ingestion import audit, collect
from .models import QuantileModel, candidates


def prepare(config):
    datasets, issues, manifest = collect(config)
    calendar, schedule, daily = None, pd.DataFrame(), pd.DataFrame()
    observations = {h: pd.DataFrame() for h in ("weekly", "monthly")}
    missing = [name for name in ("calendar", "contracts", "spx", "vix", "vix9d") if name not in datasets]
    # Publication-time supplements are verified official imports, never guessed dates.
    if "publications" in datasets:
        pubs = datasets["publications"].copy()
        pubs["date"] = pd.to_datetime(pubs.date)
        if pubs.duplicated(["dataset", "date"]).any():
            raise ValueError("Duplicate publication supplement")
        for name in ("spx", "vix", "vix9d"):
            if name in datasets:
                mapping = pubs.loc[pubs.dataset.eq(name)].set_index("date").published_at
                datasets[name]["published_at"] = pd.to_datetime(datasets[name].date.map(mapping), utc=True)
                datasets[name].to_parquet(config.normalized / f"{name}.parquet", index=False)
    if "calendar" in datasets:
        calendar = OfficialCalendar(datasets["calendar"])
    if calendar is not None and 'contracts' in datasets:
        schedule = contract_observations(datasets['contracts'], calendar)
    if not missing:
        daily = price_features(datasets, calendar)
        observations = build_observations(schedule, daily, datasets, calendar)
    plans = {}
    for horizon in observations:
        if missing:
            plans[horizon] = dict(status="pending", reason="Missing verified inputs: " + ", ".join(missing))
        else:
            plans[horizon] = history_plan(horizon, observations[horizon], datasets["contracts"], calendar, datasets, config.end)
    audit_table = audit(datasets, calendar.sessions if calendar else None, observations)
    for horizon, obs in observations.items():
        if not obs.empty:
            obs.to_parquet(config.normalized / f"observations_{horizon}.parquet", index=False)
    issues.to_csv(config.output / "source_status.csv", index=False)
    audit_table.to_csv(config.output / "data_audit.csv", index=False)
    return dict(datasets=datasets, issues=issues, manifest=manifest, calendar=calendar,
                schedule=schedule, daily=daily, observations=observations, plans=plans, audit=audit_table,
                development={}, frozen={}, final={}, selected={}, statuses={})


def split_summary(state):
    """Visible counts for required training years and the two evaluation stages."""
    rows = []
    for horizon, plan in state['plans'].items():
        if plan['status'] != 'ready':
            rows.append(dict(Horizon=horizon.title(), Stage='Pending', Period=plan['reason'],
                             Outcomes=pd.NA, Configurations=pd.NA))
            continue
        observations = state['observations'][horizon]
        periods = [
            ('Required initial training', plan['training_start'], plan['development_start']-pd.Timedelta(days=1), 'Refit for each forecast'),
            ('Training + development evaluation', plan['development_start'], plan['test_start']-pd.Timedelta(days=1), str(len(candidates(horizon)))),
            ('Training + final test evaluation', plan['test_start'], plan['test_end'], '2 frozen finalists'),
        ]
        for stage, start, end, models in periods:
            count = int((observations.expiration.between(start, end) & observations.target.notna()).sum())
            rows.append(dict(Horizon=horizon.title(), Stage=stage, Period=f'{start.year}-{end.year}',
                             Outcomes=count, Configurations=models))
    return pd.DataFrame(rows)


def development(config, state, progress=None):
    for horizon, plan in state["plans"].items():
        if plan["status"] != "ready":
            state["statuses"][horizon] = plan["reason"]
        elif not config.run_expensive_backtest:
            state["statuses"][horizon] = "pending: expensive backtest disabled; no promotion or forecast"
        elif dependency_versions()["xgboost"] == "MISSING":
            state["statuses"][horizon] = "pending: install pinned XGBoost before evaluating the full candidate set"
        else:
            prediction, frozen = develop(state["observations"][horizon], plan, horizon, config.seed, progress=progress)
            state["development"][horizon], state["frozen"][horizon] = prediction, frozen
            state['statuses'][horizon] = f'Completed training/development: {len(prediction)} predictions; finalist configurations frozen'
    # Write development-only choices before any holdout is accessed.
    (config.output / "frozen_development.json").write_text(json.dumps(state["frozen"], indent=2, default=str))


def final_test(config, state, progress=None):
    for horizon, frozen in state["frozen"].items():
        prediction = holdout(state["observations"][horizon], frozen, config.seed, progress=progress)
        state["final"][horizon] = prediction
        state["selected"][horizon] = promote(prediction, frozen)
        state["statuses"][horizon] = "evaluated; selected " + model_key(state["selected"][horizon])


def predictions(state, stage):
    values = list(state[stage].values())
    return pd.concat(values, ignore_index=True) if values else pd.DataFrame()


def forecast_once(config, state, now=None):
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    records, statuses = [], []
    for horizon in ("weekly", "monthly"):
        calendar = state["calendar"]
        schedule = state["schedule"]
        matching = schedule.loc[schedule.horizon.eq(horizon) & schedule.reference_date.eq(pd.Timestamp(config.reference_date))] if not schedule.empty else pd.DataFrame()
        if calendar and len(matching):
            complete, assumed, times = input_times(pd.Timestamp(config.reference_date), state["datasets"], calendar, live=True)
            timing_status, _ = availability(config.reference_date, times, complete, now, calendar)
        else:
            timing_status = "pending: verified contract/calendar or reference inputs unavailable"
        if horizon not in state["selected"]:
            status = timing_status + "; model pending: " + state["statuses"].get(horizon, "development/final evaluation unavailable")
            record = None
        else:
            record, status = publish_forecast(horizon, state["observations"][horizon], state["selected"][horizon],
                                              config, state["datasets"], calendar, state["manifest"], now)
        if record:
            records.append(record)
        statuses.append(dict(horizon=horizon, checked_at=now, timing_status=timing_status, status=status))
    if not (config.output / "forecasts.csv").exists():
        pd.DataFrame(columns=FORECAST_COLUMNS).to_csv(config.output / "forecasts.csv", index=False)
    frame = pd.DataFrame(statuses)
    frame.to_csv(config.output / "forecast_status.csv", index=False)
    return pd.DataFrame(records, columns=FORECAST_COLUMNS), frame


def export(config, state):
    frames = [predictions(state, stage) for stage in ("development", "final")]
    nonempty = [f for f in frames if not f.empty]
    prediction_columns = ['horizon', 'reference_date', 'issue_time', 'expiration', 'settlement_type',
                          'reference_close', 'target', 'actual_level', 'q10', 'q50', 'q90', 'lower',
                          'median', 'upper', 'crossed', 'model', 'stage', 'regime', 'timing_assumed',
                          'training_assumed_inputs', 'training_assumed_labels', 'training_count', 'training_cutoff']
    combined = pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame(columns=prediction_columns)
    combined.to_csv(config.output / "backtest_predictions.csv", index=False)
    score = metrics(combined)
    if score.empty:
        score = pd.DataFrame(columns=['horizon', 'stage', 'model', 'regime', 'interval_score_bps', 'coverage',
                                     'lower_miss_rate', 'upper_miss_rate', 'median_width_points',
                                     'median_absolute_error_points', 'median_absolute_error_bps', 'count',
                                     'coverage_wilson_lower', 'coverage_wilson_upper', 'crossing_frequency'])
    score.to_csv(config.output / "metrics.csv", index=False)
    trained = {}
    for horizon, selected in state["selected"].items():
        cutoff = min(pd.Timestamp.now(tz="UTC"), pd.Timestamp(config.end, tz="UTC") + pd.Timedelta(days=1))
        train = training_rows(state['observations'][horizon], cutoff)
        model = QuantileModel(selected, config.seed).fit(train)
        model_dir = config.output / 'models'
        model_dir.mkdir(exist_ok=True)
        info = dict(feature_order=FEATURES, configuration=selected, training_cutoff=train.label_available_at.max(),
                    fitted_as_of=cutoff, training_count=len(train), dependency_versions=dependency_versions(),
                    source_hashes=[m.get('sha256') for m in state['manifest'] if m.get('sha256')])
        (model_dir / f'{horizon}-selected.pkl').write_bytes(pickle.dumps(model))
        (model_dir / f'{horizon}-selected.json').write_text(json.dumps(info, default=str, indent=2))
        trained[horizon] = info
    metadata = dict(feature_order=FEATURES, configuration=vars(config), dependency_versions=dependency_versions(),
                    plans=state["plans"], selected=state["selected"], statuses=state["statuses"],
                    source_manifest=state["manifest"],
                    assumptions=["Unknown historical input publication: next-session 08:00 America/New_York",
                                 "Unknown settlement publication: following regular session 00:00 America/New_York",
                                 "95% Wilson coverage intervals assume independent forecast outcomes; overlapping returns violate this",
                                 "Current downloaded history may contain revisions; no vintage archive is implied"],
                    model_artifacts=trained or "Pending: no verified selected model")
    (config.output / "run_metadata.json").write_text(json.dumps(metadata, indent=2, default=str))
    return combined, score
