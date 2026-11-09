"""Build calendar-derived rows for contracts that have not settled yet (live forecasting only).

Writes data/imports/upcoming_contracts.csv, separate from the settlement-backed contracts.csv. Rows are
tagged `listing_basis=calendar_derived_unverified`: eligibility follows the hash-pinned Cboe annual calendar
and the same classification as the historical builder, but no Cboe listing/settlement record exists yet.
Rerun after each settlement refresh; contracts that now appear in contracts.csv are dropped at load time.
"""
import argparse
import json
from pathlib import Path

import pandas as pd

from scripts.build_calendar_import import build_frames, load_sources
from spx_forecast.calendar import OfficialCalendar, contract_observations
from spx_forecast.ingestion import digest
from spx_forecast.upcoming import LISTING_BASIS, check_calendar_runway, merge_contracts, upcoming_contracts


def build(as_of, lookahead_days=56, imports=Path('data/imports'), output=Path('artifacts'), warn_days=60):
    calendar_review = json.loads(Path('docs/calendar_review.json').read_text())
    policy = json.loads(Path('docs/contract_review.json').read_text())
    frame, standard = build_frames(calendar_review, load_sources(imports, calendar_review))
    existing = pd.read_csv(imports/'calendar.csv', parse_dates=['date'])
    # Same guard as the historical builder: no edited calendar may diverge from the pinned review.
    pd.testing.assert_frame_equal(existing[['date', 'is_session']], frame[['date', 'is_session']])
    calendar = OfficialCalendar(frame)
    runway = check_calendar_runway(calendar, as_of, warn_days)  # raises once the calendar has ended
    contracts = pd.read_csv(imports/'contracts.csv', parse_dates=['nominal_expiration', 'expiration'])
    last_nominal = contracts.groupby('horizon').nominal_expiration.max().to_dict()
    through = min(pd.Timestamp(as_of) + pd.Timedelta(days=lookahead_days), calendar.frame.date.max())
    upcoming = upcoming_contracts(calendar, standard, last_nominal, through, policy['eow_definition_source'])
    # Fail here, not at load time, if the combined schedule would be invalid.
    schedule = contract_observations(merge_contracts(contracts, upcoming), calendar)
    upcoming.to_csv(imports/'upcoming_contracts.csv', index=False)
    receipt = dict(**runway, as_of=str(as_of), through=str(through.date()), rows=len(upcoming),
                   eligible_rows=int(upcoming.eligible.sum()) if len(upcoming) else 0,
                   upcoming_sha256=digest((imports/'upcoming_contracts.csv').read_bytes()),
                   last_settled_nominal={h: str(d.date()) for h, d in last_nominal.items()},
                   listing_basis=LISTING_BASIS, scheduled_observations=len(schedule),
                   limitations=['Eligibility is derived from the pinned Cboe annual calendar; no listing or '
                                'settlement record exists for these contracts yet.',
                                'Historical contracts.csv rows are unchanged and take precedence.'])
    (output/'upcoming_contracts_validation.json').write_text(json.dumps(receipt, indent=2))
    return upcoming, receipt, through


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', default=str(pd.Timestamp.now().date()))
    parser.add_argument('--lookahead-days', type=int, default=56)
    args = parser.parse_args()
    upcoming, receipt, through = build(args.as_of, args.lookahead_days)
    if receipt['warning']:
        print('WARNING: ' + receipt['warning'])
    print(upcoming[['horizon', 'nominal_expiration', 'expiration', 'eligible', 'classification']].to_string(index=False))
    print(f'Wrote {len(upcoming)} upcoming rows through {through.date()}')


if __name__ == '__main__':
    main()
