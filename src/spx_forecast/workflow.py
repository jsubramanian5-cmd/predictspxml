"""Small notebook-facing orchestration, with explicit independent horizon states."""
import dataclasses
import json
import pickle
import warnings

import pandas as pd

from .backtest import develop, expanding, history_plan, holdout, metrics, model_key, promote, training_rows
from .calendar import OfficialCalendar, contract_observations, availability
from .features import FEATURES, build_observations, input_times, price_features
from .forecast import FORECAST_COLUMNS, dependency_versions, publish_forecast, read_forecasts
from .ingestion import audit, collect
from .models import QuantileModel, candidates
from .upcoming import merge_contracts


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
        schedule = contract_observations(merge_contracts(datasets['contracts'], datasets.get('upcoming_contracts')), calendar)
    if not missing:
        daily = price_features(datasets, calendar)
        observations = build_observations(schedule, daily, datasets, calendar)
    plans = {}
    for horizon in observations:
        if missing:
            plans[horizon] = dict(status="pending", reason="Missing verified inputs: " + ", ".join(missing))
        else:
            plans[horizon] = history_plan(horizon, observations[horizon], datasets["contracts"], calendar, datasets,
                                          config.end, daily=daily)
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


def post_holdout_predictions(config, state, year):
    """Score a year after the frozen test window with the frozen finalists, refit before each forecast."""
    frames = []
    for horizon, frozen in state["frozen"].items():
        observations = state["observations"][horizon]
        scorable = observations.loc[observations.expiration.dt.year.eq(year) & observations.target.notna()
                                    & observations.issue_time.notna()]
        if scorable.empty:
            continue
        frames.append(expanding(observations, scorable, [frozen["benchmark"], frozen["ml"]],
                                frozen["volatility_threshold"], config.seed, "post_holdout"))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def forecast_range_table(predictions, selected, years):
    """10th/50th/90th percentile settlement levels of each horizon's selected model, with actuals."""
    rows = []
    for horizon, config in selected.items():
        frame = predictions.loc[predictions.horizon.eq(horizon) & predictions.model.eq(model_key(config))
                                & pd.to_datetime(predictions.expiration).dt.year.isin(years)]
        rows.append(frame)
    if not rows:
        return pd.DataFrame()
    table = pd.concat(rows, ignore_index=True)
    table = table.assign(expiration=pd.to_datetime(table.expiration).dt.date,
                         reference_date=pd.to_datetime(table.reference_date, format='mixed').dt.date,
                         within_80=table.actual_level.between(table.lower, table.upper))
    table = table.rename(columns={"lower": "p10_level", "median": "median_level", "upper": "p90_level"})
    columns = ["horizon", "reference_date", "expiration", "reference_close", "p10_level", "median_level",
               "p90_level", "actual_level", "within_80"]
    table = table.sort_values(["horizon", "expiration"], ascending=[False, True])[columns]
    return table.round({c: 2 for c in ("reference_close", "p10_level", "median_level", "p90_level", "actual_level")}).reset_index(drop=True)


def predictions(state, stage):
    values = list(state[stage].values())
    return pd.concat(values, ignore_index=True) if values else pd.DataFrame()


def upcoming_reference_date(schedule, horizon, now):
    """Reference session of the horizon's next contract that is unexpired and whose issue window is still open."""
    if schedule.empty:
        return None
    now = pd.Timestamp(now)
    rows = schedule.loc[schedule.horizon.eq(horizon) & (pd.to_datetime(schedule.expiration) > now.tz_convert(None).normalize())]
    if "cutoff" in rows:
        rows = rows.loc[pd.to_datetime(rows.cutoff, utc=True) >= now]
    return None if rows.empty else pd.Timestamp(rows.sort_values("expiration").reference_date.iloc[0])


MISSED = "suppressed_missed_window"


def last_checked(path):
    """When each horizon was last checked, from the status file the coming check is about to replace."""
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return {}
    if not {"horizon", "checked_at"} <= set(frame):
        return {}
    times = pd.to_datetime(frame.checked_at, utc=True, errors="coerce")
    return {h: t for h, t in zip(frame.horizon, times) if pd.notna(t)}


def missed_windows(schedule, horizon, issued, since, now):
    """Contracts whose issue window closed in (since, now] without a forecast.

    `upcoming_reference_date` moves on as soon as a cutoff passes, so a window that closed unissued would
    otherwise be reported as an ordinary wait for the following contract. Only windows that closed since the
    previous check count: each miss is reported once, and contracts from before the first run never are.
    """
    if schedule.empty or since is None or "cutoff" not in schedule:
        return []
    rows = schedule.loc[schedule.horizon.eq(horizon)]
    cutoff = pd.to_datetime(rows.cutoff, utc=True)
    rows = rows.loc[(cutoff > since) & (cutoff <= pd.Timestamp(now))]
    done = set(pd.to_datetime(issued.loc[issued.horizon.eq(horizon), "expiration"]).dt.normalize())
    return [f"{MISSED}: {horizon} contract expiring {pd.Timestamp(row.expiration).date()} (reference "
            f"{pd.Timestamp(row.reference_date).date()}) passed its issue cutoff without a forecast"
            for row in rows.sort_values("expiration").itertuples() if pd.Timestamp(row.expiration).normalize() not in done]


def forecast_once(config, state, now=None):
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    records, statuses = [], []
    previous_checks = last_checked(config.output / "forecast_status.csv")
    issued = read_forecasts(config.output / "forecasts.csv")
    for horizon in ("weekly", "monthly"):
        calendar = state["calendar"]
        schedule = state["schedule"]
        missed = []
        if config.reference_date is None:
            missed = missed_windows(schedule, horizon, issued, previous_checks.get(horizon), now)
            reference = upcoming_reference_date(schedule, horizon, now)
            if reference is None:
                latest = schedule.loc[schedule.horizon.eq(horizon), "expiration"].max() if not schedule.empty else None
                reason = (f"pending: no verified {horizon} contract expires after {now.date()}; latest listed expiration is "
                          f"{pd.Timestamp(latest).date() if latest is not None and pd.notna(latest) else 'none'} "
                          "(extend data/imports/contracts.csv with the next official listing)")
                statuses.append(dict(horizon=horizon, checked_at=now, timing_status=reason,
                                     status="; next contract: ".join(missed + [reason])))
                continue
            horizon_config = dataclasses.replace(config, reference_date=str(reference.date()))
        else:
            horizon_config = config
        matching = schedule.loc[schedule.horizon.eq(horizon) & schedule.reference_date.eq(pd.Timestamp(horizon_config.reference_date))] if not schedule.empty else pd.DataFrame()
        if calendar and len(matching):
            complete, assumed, times = input_times(pd.Timestamp(horizon_config.reference_date), state["datasets"], calendar, live=True)
            timing_status, _ = availability(horizon_config.reference_date, times, complete, now, calendar)
        else:
            timing_status = "pending: verified contract/calendar or reference inputs unavailable"
        if horizon not in state["selected"]:
            status = timing_status + "; model pending: " + state["statuses"].get(horizon, "development/final evaluation unavailable")
            record = None
        else:
            record, status = publish_forecast(horizon, state["observations"][horizon], state["selected"][horizon],
                                              horizon_config, state["datasets"], calendar, state["manifest"], now)
        if record:
            records.append(record)
        # The missed contract leads the status so the run exits 3; the timing column stays about the next contract.
        statuses.append(dict(horizon=horizon, checked_at=now, timing_status=timing_status,
                             status="; next contract: ".join(missed + [status])))
    if not (config.output / "forecasts.csv").exists():
        pd.DataFrame(columns=FORECAST_COLUMNS).to_csv(config.output / "forecasts.csv", index=False)
    frame = pd.DataFrame(statuses)
    # A replay of an earlier time reports its statuses but leaves the newer status file in place: its
    # checked_at is also where the next real run starts looking for missed issue windows.
    if not any(checked > now for checked in previous_checks.values()):
        frame.to_csv(config.output / "forecast_status.csv", index=False)
    return pd.DataFrame(records, columns=FORECAST_COLUMNS), frame


def previous_selection(output):
    """Models selected by the last completed backtest, as recorded on disk ({} when there is none)."""
    path = output / "run_metadata.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text()).get("selected") or {}
    except json.JSONDecodeError:
        return {}


def export(config, state):
    frames = [predictions(state, stage) for stage in ("development", "final")]
    nonempty = [f for f in frames if not f.empty]
    prediction_columns = ['horizon', 'reference_date', 'issue_time', 'expiration', 'settlement_type',
                          'reference_close', 'target', 'actual_level', 'q10', 'q50', 'q90', 'lower',
                          'median', 'upper', 'crossed', 'model', 'stage', 'regime', 'timing_assumed',
                          'training_assumed_inputs', 'training_assumed_labels', 'training_count', 'training_cutoff']
    combined = pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame(columns=prediction_columns)
    score = metrics(combined)
    if score.empty:
        score = pd.DataFrame(columns=['horizon', 'stage', 'model', 'regime', 'interval_score_bps', 'coverage',
                                     'lower_miss_rate', 'upper_miss_rate', 'median_width_points',
                                     'median_absolute_error_points', 'median_absolute_error_bps', 'count',
                                     'coverage_wilson_lower', 'coverage_wilson_upper', 'crossing_frequency'])
    if not state["selected"] and previous_selection(config.output):
        # The live run forecasts with the selection in run_metadata.json and scores against metrics.csv. A run
        # that evaluated nothing (backtest disabled or every horizon pending) must not replace them with blanks.
        warnings.warn("This run selected no model: the previous backtest's run_metadata.json, metrics.csv and "
                      "backtest_predictions.csv were kept unchanged.", stacklevel=2)
        return combined, score
    combined.to_csv(config.output / "backtest_predictions.csv", index=False)
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
