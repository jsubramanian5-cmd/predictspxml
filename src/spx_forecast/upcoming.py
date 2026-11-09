"""Calendar-derived eligibility for contracts that have not settled yet.

`contracts.csv` only holds contracts backed by an official settlement record. A live forecast needs the
*next* contract, which has no record yet. These rows apply the same calendar classification as the
historical builder, are written to a separate file, and are tagged `listing_basis`; they never replace
or relax a historical row. A contract that later settles moves into `contracts.csv`, and the merge below
then drops the upcoming copy.
"""
import pandas as pd

from .calendar import classify_week

LISTING_BASIS = "calendar_derived_unverified"
COLUMNS = ["horizon", "nominal_expiration", "expiration", "scheduled_settlement_date", "settlement_type",
           "eligible", "classification", "source_url", "calendar_source_url", "annual_pdf_sha256",
           "settlement_source_sha256", "rule_source_url", "listing_basis"]


class CalendarRunwayError(ValueError):
    """The verified exchange calendar no longer covers the present."""


def check_calendar_runway(calendar, as_of, warn_days=60):
    """Fail loudly when the pinned calendar has ended; warn while fewer than `warn_days` remain.

    Returns dict(calendar_end, runway_days, warning). `warning` is None when there is enough runway.
    """
    end = calendar.frame.date.max().normalize()
    runway = int((end - pd.Timestamp(as_of).normalize()).days)
    if runway < 0:
        raise CalendarRunwayError(
            f"Verified calendar ended {end.date()}, {-runway} days before {pd.Timestamp(as_of).date()}: "
            f"import and review the {end.year + 1} Cboe annual calendar (docs/imports.md) before forecasting")
    warning = None
    if runway < warn_days:
        warning = (f"Verified calendar ends {end.date()} ({runway} days left, warning below {warn_days}): "
                   f"import the {end.year + 1} Cboe annual calendar or forecasts stop after that date")
    return dict(calendar_end=str(end.date()), runway_days=runway, warning=warning)


def upcoming_contracts(calendar, standard, last_nominal, through, rule_source_url):
    """Weekly end-of-week and standard monthly rows after `last_nominal[horizon]`, up to `through`."""
    through = pd.Timestamp(through).normalize()
    if through > calendar.frame.date.max():
        raise ValueError("Upcoming contracts cannot extend beyond the verified calendar")
    standard = standard.copy()
    standard["expiration"] = pd.to_datetime(standard.expiration)
    standard_dates = set(standard.expiration)
    month_ends = set(calendar.sessions.to_series().groupby(calendar.sessions.to_period("M")).max())
    daily = calendar.frame.set_index("date")
    rows = []
    first_friday = pd.Timestamp(last_nominal["weekly"]).normalize() + pd.Timedelta(days=7)
    for friday in pd.date_range(first_friday, through, freq="W-FRI"):
        sessions = calendar.sessions[(calendar.sessions >= friday - pd.Timedelta(days=4)) & (calendar.sessions <= friday)]
        if not len(sessions):
            raise ValueError("No verified session in a nominal EOW week")
        actual = sessions[-1]
        reference = daily.loc[actual]
        classification = classify_week(actual, standard_dates, month_ends)
        eligible = classification == "end_of_week"
        url = reference.get("annual_calendar_source_url", reference.source_url)
        rows.append(dict(horizon="weekly", nominal_expiration=friday, expiration=actual if eligible else friday,
                         scheduled_settlement_date=actual, settlement_type="SPXW_PM", eligible=eligible,
                         classification=classification, source_url=url, calendar_source_url=url,
                         annual_pdf_sha256=reference.get("annual_pdf_sha256", ""), settlement_source_sha256="",
                         rule_source_url=rule_source_url, listing_basis=LISTING_BASIS))
    last_month = pd.Timestamp(last_nominal["monthly"]).to_period("M")
    for row in standard.loc[standard.expiration.le(through)].itertuples(index=False):
        month = pd.Period(row.month, freq="M")
        if month <= last_month:
            continue
        nominal = pd.date_range(month.start_time, month.end_time, freq="W-FRI")[2]
        rows.append(dict(horizon="monthly", nominal_expiration=nominal, expiration=row.expiration,
                         scheduled_settlement_date=row.expiration, settlement_type="SET_AM", eligible=True,
                         classification="standard_monthly_AM", source_url=row.source_url,
                         calendar_source_url=row.source_url, annual_pdf_sha256=getattr(row, "annual_pdf_sha256", ""),
                         settlement_source_sha256="", rule_source_url=rule_source_url, listing_basis=LISTING_BASIS))
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    return pd.DataFrame(rows)[COLUMNS].sort_values(["horizon", "nominal_expiration"]).reset_index(drop=True)


def merge_contracts(contracts, upcoming):
    """Settled-record contracts win; upcoming rows only fill periods not yet in `contracts`."""
    contracts = contracts.copy()
    if "listing_basis" not in contracts:
        contracts["listing_basis"] = "settlement_record"
    if upcoming is None or upcoming.empty:
        return contracts
    upcoming = upcoming.copy()
    contracts_key = pd.to_datetime(contracts.nominal_expiration).dt.normalize()
    upcoming["nominal_expiration"] = pd.to_datetime(upcoming.nominal_expiration).dt.normalize()
    known = set(zip(contracts.horizon, contracts_key))
    keep = [(h, n) not in known for h, n in zip(upcoming.horizon, upcoming.nominal_expiration)]
    return pd.concat([contracts, upcoming.loc[keep]], ignore_index=True)
