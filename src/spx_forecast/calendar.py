"""Calendars are verified dated Cboe records, not generic weekday approximations."""
import pandas as pd

from .ingestion import cboe_url

ZONE = "America/New_York"


class OfficialCalendar:
    def __init__(self, frame):
        required = {"date", "is_session", "source_url"}
        if not required <= set(frame):
            raise ValueError(f"Calendar missing {required - set(frame)}")
        self.frame = frame.copy()
        self.frame["date"] = pd.to_datetime(frame.date).dt.normalize()
        if self.frame.date.duplicated().any():
            raise ValueError("Duplicate calendar dates")
        if not self.frame.source_url.map(cboe_url).all():
            raise ValueError("Calendar requires official source URLs")
        flag = self.frame.is_session.astype(str).str.lower().map({"true": True, "false": False, "1": True, "0": False})
        if flag.isna().any():
            raise ValueError("is_session must be true/false or 1/0")
        self.frame["is_session"] = flag
        self.frame = self.frame.sort_values("date")
        self.sessions = pd.DatetimeIndex(self.frame.loc[self.frame.is_session, "date"])
        dates = pd.DatetimeIndex(self.frame.date)
        if len(dates) == 0 or not dates.equals(pd.date_range(dates.min(), dates.max(), freq="D")):
            raise ValueError("Calendar must explicitly cover every calendar day in its span")

    def require_span(self, start, end):
        if pd.Timestamp(start) < self.frame.date.min() or pd.Timestamp(end) > self.frame.date.max():
            raise ValueError(f"Official calendar does not cover {start} through {end}")

    def next_session(self, date):
        after = self.sessions[self.sessions > pd.Timestamp(date).normalize()]
        if not len(after):
            raise ValueError("Next regular session missing from official calendar")
        return after[0]

    def time(self, date, hour):
        return (pd.Timestamp(date).normalize() + pd.Timedelta(hours=hour)).tz_localize(ZONE).tz_convert("UTC")

    def cutoff(self, reference):
        return self.time(self.next_session(reference), 8)

    def previous_week_final(self, nominal_expiration):
        monday = pd.Timestamp(nominal_expiration).normalize() - pd.Timedelta(days=pd.Timestamp(nominal_expiration).weekday())
        start, end = monday - pd.Timedelta(days=7), monday - pd.Timedelta(days=1)
        self.require_span(start, end)
        valid = self.sessions[(self.sessions >= start) & (self.sessions <= end)]
        if not len(valid):
            raise ValueError("No verified prior-week regular session")
        return valid[-1]


def contract_observations(frame, calendar):
    required = {"horizon", "nominal_expiration", "expiration", "settlement_type", "eligible", "source_url"}
    if not required <= set(frame):
        raise ValueError(f"Contracts missing {required - set(frame)}")
    frame = frame.copy()
    for c in ("expiration", "nominal_expiration"):
        frame[c] = pd.to_datetime(frame[c]).dt.normalize()
    frame["eligible"] = frame.eligible.astype(str).str.lower().map({"true": True, "false": False, "1": True, "0": False})
    if frame.eligible.isna().any() or not frame.source_url.map(cboe_url).all():
        raise ValueError("Contracts need explicit eligibility and official provenance")
    if not frame.horizon.isin(["weekly", "monthly"]).all():
        raise ValueError("Unknown horizon")
    if frame.duplicated(["horizon", "nominal_expiration"]).any():
        raise ValueError("Duplicate contract period")
    rows = []
    for horizon, group in frame.groupby("horizon"):
        group = group.sort_values("nominal_expiration")
        previous = None
        for row in group.itertuples(index=False):
            if not row.eligible:
                continue
            expected = "SPXW_PM" if horizon == "weekly" else "SET_AM"
            if row.settlement_type != expected or row.expiration not in calendar.sessions:
                raise ValueError("Eligible contract must match its horizon and an official regular session")
            if horizon == "weekly":
                if row.nominal_expiration.weekday() != 4:
                    raise ValueError("Weekly nominal_expiration must identify the Friday of the contract week")
                if row.expiration.to_period("W-SUN") != row.nominal_expiration.to_period("W-SUN"):
                    raise ValueError("Weekly adjustment must be within the identified week")
                reference = calendar.previous_week_final(row.nominal_expiration)
            else:
                reference = previous
                # A missing calendar month cannot silently become a two-month forecast.
                if reference is not None and row.nominal_expiration.to_period("M") != previous.to_period("M") + 1:
                    reference = None
                previous = row.expiration
                if reference is None:
                    continue
            record = row._asdict()
            record["reference_date"] = reference
            record["calendar_days"] = (row.expiration - reference).days
            record["remaining_sessions"] = int(((calendar.sessions > reference) & (calendar.sessions <= row.expiration)).sum())
            record["earliest_issue"] = calendar.time(reference, 20)
            record["cutoff"] = calendar.cutoff(reference)
            rows.append(record)
    return pd.DataFrame(rows)


def availability(reference, available_times, complete, now, calendar):
    """One instantaneous check; never polls or waits for an input."""
    now = pd.Timestamp(now)
    if now.tzinfo is None:
        raise ValueError("Issue time must be timezone aware")
    earliest, cutoff = calendar.time(reference, 20), calendar.cutoff(reference)
    if now < earliest:
        return "not_yet_eligible", None
    if not complete:
        return ("suppressed_incomplete" if now >= cutoff else "waiting_for_inputs"), None
    times = pd.to_datetime(list(available_times), utc=True)
    if times.isna().any() or not len(times):
        return ("suppressed_incomplete" if now >= cutoff else "waiting_for_inputs"), None
    ready = max(earliest, times.max())
    if ready > cutoff:
        return "suppressed_late_inputs", None
    if now < ready:
        return "waiting_for_inputs", None
    return "ready", ready
