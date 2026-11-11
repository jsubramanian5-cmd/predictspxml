import numpy as np
import pandas as pd

from .volatility import SCALE_COLUMNS, volatility_scales

FEATURES =["return_1", "return_5", "return_20", "realized_vol_5", "realized_vol_20",
            "vix", "vix9d", "vix9d_vix", "price_mean20", "calendar_days", "remaining_sessions"]


def price_features(datasets, calendar):
    daily = pd.DataFrame(index=calendar.sessions)
    for name in ("spx", "vix", "vix9d"):
        source = datasets[name]
        if source.date.duplicated().any():
            raise ValueError(f"Duplicate {name} price dates")
        daily[name] = source.set_index("date").value.reindex(daily.index)
    daily = daily.where((daily > 0) & np.isfinite(daily))
    log_price = np.log(daily.spx)
    returns = log_price.diff()
    for window in (1, 5, 20):
        # Rolling sums require every return in the window, including internal sessions.
        daily[f"return_{window}"] = returns.rolling(window, min_periods=window).sum()
    for window in (5, 20):
        daily[f"realized_vol_{window}"] = returns.rolling(window, min_periods=window).std(ddof=1) * np.sqrt(252)
    daily["vix9d_vix"] = daily.vix9d / daily.vix
    daily["price_mean20"] = daily.spx / daily.spx.rolling(20, min_periods=20).mean() - 1
    return daily


def by_date(datasets):
    """Price inputs indexed by date once, for callers that check many reference sessions."""
    return {name: datasets[name].set_index("date") for name in ("spx", "vix", "vix9d") if name in datasets}


def input_times(reference, datasets, calendar, live=False, indexed=None):
    """Account for all 21 SPX prices and both reference-session volatility levels.

    `indexed` is `by_date(datasets)` when the caller already has it; otherwise it is built here."""
    dates = calendar.sessions[calendar.sessions <= reference][-21:]
    complete, assumed, times = len(dates) == 21, False, []
    indexed = by_date(datasets) if indexed is None else indexed
    for name in ("spx", "vix", "vix9d"):
        wanted = dates if name == "spx" else [reference]
        if name not in indexed:
            complete = False
            continue
        source = indexed[name]
        for date in wanted:
            if date not in source.index or not np.isfinite(source.loc[date, "value"]) or source.loc[date, "value"] <= 0:
                complete = False
                continue
            row = source.loc[date]
            timestamp = row.get("published_at", pd.NaT)
            if pd.isna(timestamp):
                assumed = True
                # Historical assumption is explicit. For live publication, actual receipt
                # is the conservative availability evidence; never backdate a retrieval.
                timestamp = row.get("retrieved_at", pd.NaT) if live else calendar.cutoff(date)
            times.append(pd.Timestamp(timestamp) if pd.notna(timestamp) else pd.NaT)
    return complete, assumed, times


def build_observations(schedule, daily, datasets, calendar):
    if schedule.empty:
        return {h: pd.DataFrame() for h in ("weekly", "monthly")}
    obs = schedule.merge(daily.reset_index(names="reference_date"), on="reference_date", how="left", validate="many_to_one")
    obs = obs.rename(columns={"spx": "reference_close"})
    labels = datasets.get("settlements", pd.DataFrame(columns=["horizon", "expiration", "settlement_type", "settlement", "published_at"]))
    labels = labels[["horizon", "expiration", "settlement_type", "settlement", "published_at"]].copy()
    labels["expiration"] = pd.to_datetime(labels.expiration)
    obs = obs.merge(labels, on=["horizon", "expiration", "settlement_type"], how="left", validate="one_to_one")
    obs["target"] = 10000 * np.log(obs.settlement / obs.reference_close)
    timing, indexed = [], by_date(datasets)
    for row in obs.itertuples(index=False):
        complete, assumed, times = input_times(row.reference_date, datasets, calendar, indexed=indexed)
        ready = max([row.earliest_issue] + times) if complete and times else pd.NaT
        label_assumed = pd.isna(row.published_at)
        if pd.notna(row.settlement):
            label_time = calendar.time(calendar.next_session(row.expiration), 0) if label_assumed else pd.Timestamp(row.published_at)
            if label_time < calendar.time(row.expiration, 0):
                raise ValueError("Settlement publication predates expiration")
        else:
            label_time = pd.NaT
        timing.append(dict(issue_time=ready, timing_assumed=assumed,
                           label_timing_assumed=label_assumed, label_available_at=label_time,
                           features_complete=complete))
    obs = pd.concat([obs.reset_index(drop=True), pd.DataFrame(timing)], axis=1)
    valid = obs[FEATURES + ["reference_close"]].notna().all(axis=1)
    valid &= obs.features_complete & obs.issue_time.notna() & (obs.issue_time <= obs.cutoff)
    obs = obs.loc[valid].copy()
    obs["scale"] = np.where(obs.horizon.eq("weekly"), obs.vix9d, obs.vix) / 100 * np.sqrt(obs.calendar_days / 365)
    # Model-based scales for the GARCH and HAR-RV candidates; they are not model features.
    obs[SCALE_COLUMNS] = volatility_scales(obs, daily.spx)
    return {h: obs.loc[obs.horizon.eq(h)].sort_values("issue_time").reset_index(drop=True) for h in ("weekly", "monthly")}


def to_levels(reference_close, return_bps):
    if np.any(np.asarray(reference_close) <= 0):
        raise ValueError("Reference close must be positive")
    return np.asarray(reference_close) * np.exp(np.asarray(return_bps) / 10000)
