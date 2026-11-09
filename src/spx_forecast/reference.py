"""Live reference-close source: FRED SP500 where available, Cboe SPX history only for later dates.

FRED publishes the previous day's close about 11:30 New York time, after the 08:00 issue cutoff, so live
forecasts could never use it. Cboe's SPX_History.csv carries the same close the evening it prints. FRED
remains the source for the backtest and for every date it already covers; Cboe fills only the tail. When
FRED catches up, the two are compared and any difference is reported (never silently reconciled).
"""
import pandas as pd

CBOE_SPX_URL = "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/SPX_History.csv"
FRED_SOURCE, CBOE_SOURCE = "fred", "cboe_spx_history"
TOLERANCE = 0.005
RECONCILIATION_COLUMNS = ["date", "fred", "cboe", "difference"]


def splice_cboe_tail(fred, cboe):
    """FRED rows unchanged; Cboe rows added only for dates after FRED's last row."""
    fred = fred.copy()
    fred["source"] = FRED_SOURCE
    tail = cboe.loc[(cboe.date > fred.date.max()) & (cboe.value > 0)].copy()
    tail["source"] = CBOE_SOURCE
    return pd.concat([fred, tail], ignore_index=True).sort_values("date").reset_index(drop=True)


def reconcile(fred, cboe, tolerance=TOLERANCE):
    """Dates where both sources have a close and they differ by more than `tolerance` index points."""
    both = fred.loc[fred.value > 0, ["date", "value"]].merge(
        cboe.loc[cboe.value > 0, ["date", "value"]], on="date", suffixes=("_fred", "_cboe"))
    both["difference"] = (both.value_fred - both.value_cboe).round(4)
    out = both.loc[both.difference.abs() > tolerance].rename(columns={"value_fred": "fred", "value_cboe": "cboe"})
    return out[RECONCILIATION_COLUMNS].reset_index(drop=True)


def recent_mismatch_warning(reconciliation, now, days=30):
    """Warning text when FRED and Cboe disagree on a date within the last `days` days, else None."""
    if reconciliation.empty:
        return None
    cutoff = pd.Timestamp(now).tz_convert(None).normalize() - pd.Timedelta(days=days)
    recent = reconciliation.loc[pd.to_datetime(reconciliation.date) >= cutoff]
    if recent.empty:
        return None
    worst = recent.loc[recent.difference.abs().idxmax()]
    return (f"FRED and Cboe SPX closes differ on {len(recent)} date(s) in the last {days} days; largest "
            f"{worst.difference:+.2f} points on {pd.Timestamp(worst.date).date()} (see artifacts/spx_reconciliation.csv)")
