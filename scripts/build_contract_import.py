"""Build source-backed EOW/standard-monthly target eligibility; no strike-level claims."""
import argparse
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from scripts.build_calendar_import import build_frames, load_sources
from spx_forecast.calendar import OfficialCalendar, classify_week, contract_observations
from spx_forecast.config import Config
from spx_forecast.ingestion import Store, digest, weekly_archive


def build_contracts(calendar, standard, weekly_labels, as_of, policy):
    as_of = pd.Timestamp(as_of).normalize()
    calendar.require_span(calendar.frame.date.min(), as_of)
    standard = standard.copy()
    standard['expiration'] = pd.to_datetime(standard.expiration)
    standard_dates = set(standard.expiration)
    month_ends = set(calendar.sessions.to_series().groupby(calendar.sessions.to_period('M')).max())
    labels = weekly_labels.loc[weekly_labels.horizon.eq('weekly') &
                               weekly_labels.settlement_type.eq('SPXW_PM')].copy()
    labels['expiration'] = pd.to_datetime(labels.expiration)
    if labels.expiration.duplicated().any():
        raise ValueError('Duplicate weekly label dates')
    labels = labels.set_index('expiration')
    daily = calendar.frame.set_index('date')
    rows, boundary, missing = [], [], []
    for friday in pd.date_range(calendar.frame.date.min(), as_of, freq='W-FRI'):
        monday = friday - pd.Timedelta(days=4)
        if monday - pd.Timedelta(days=7) < calendar.frame.date.min():
            boundary.append(dict(nominal_expiration=str(friday.date()),
                                 reason='Prior-week reference outside verified calendar'))
            continue
        sessions = calendar.sessions[(calendar.sessions >= monday) & (calendar.sessions <= friday)]
        if not len(sessions):
            raise ValueError('No verified session in a nominal EOW week')
        actual = sessions[-1]
        reference = daily.loc[actual]
        classification = classify_week(actual, standard_dates, month_ends)
        eligible = classification == 'end_of_week'
        if eligible and actual not in labels.index:
            missing.append(str(actual.date()))
            continue
        source_url = labels.loc[actual, 'source_url'] if eligible else reference.source_url
        rows.append(dict(horizon='weekly', nominal_expiration=friday,
                         expiration=actual if eligible else friday,
                         scheduled_settlement_date=actual, settlement_type='SPXW_PM', eligible=eligible,
                         classification=classification, source_url=source_url,
                         calendar_source_url=reference.annual_calendar_source_url,
                         annual_pdf_sha256=reference.annual_pdf_sha256,
                         settlement_source_sha256=labels.loc[actual, 'sha256'] if eligible else '',
                         rule_source_url=policy['eow_definition_source']))
    if missing:
        raise ValueError('EOW dates require explicit archive records, not ineligibility assumptions: ' + ', '.join(missing))
    for row in standard.loc[standard.expiration.le(as_of)].itertuples(index=False):
        month = pd.Period(row.month, freq='M')
        # Nominal Friday identifies the monthly period only; actual dates are reviewed.
        nominal = pd.date_range(month.start_time, month.end_time, freq='W-FRI')[2]
        rows.append(dict(horizon='monthly', nominal_expiration=nominal, expiration=row.expiration,
                         scheduled_settlement_date=row.expiration, settlement_type='SET_AM', eligible=True,
                         classification='standard_monthly_AM', source_url=row.source_url,
                         calendar_source_url=row.source_url, annual_pdf_sha256=row.annual_pdf_sha256,
                         settlement_source_sha256='',
                         rule_source_url=policy['third_friday_pm_distinction_source']))
    contracts = pd.DataFrame(rows).sort_values(['horizon', 'nominal_expiration']).reset_index(drop=True)
    contract_observations(contracts, calendar)
    return contracts, boundary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', default=Config().end)
    args = parser.parse_args()
    imports, output = Path('data/imports'), Path('artifacts')
    calendar_review = json.loads(Path('docs/calendar_review.json').read_text())
    policy = json.loads(Path('docs/contract_review.json').read_text())
    sources = load_sources(imports, calendar_review)
    frame, standard = build_frames(calendar_review, sources)
    existing = pd.read_csv(imports/'calendar.csv', parse_dates=['date'])
    # Do not let an edited calendar diverge from the hash-pinned source review.
    pd.testing.assert_frame_equal(existing[['date', 'is_session']], frame[['date', 'is_session']])
    with ZipFile(imports/'cboe-historical-calendars-and-listing-records.zip') as archive:
        for entry in policy['reviewed_local_documents']:
            if digest(archive.read(entry['file'])) != entry['sha256']:
                raise ValueError('Listing document changed; renewed review required')
    config = Config(end=args.as_of)
    config.prepare()
    labels = weekly_archive(imports/'cboe-weeklys-settlement-values-all-years.zip', Store(config))
    calendar = OfficialCalendar(frame)
    contracts, boundary = build_contracts(calendar, standard, labels, args.as_of, policy)
    for entry in policy['reviewed_local_documents']:
        for date in entry.get('explicit_eow_dates', []):
            if pd.Timestamp(date) <= pd.Timestamp(args.as_of):
                matches = contracts.loc[contracts.horizon.eq('weekly') & contracts.expiration.eq(pd.Timestamp(date))]
                if len(matches) != 1 or not matches.eligible.all():
                    raise ValueError(f'Explicit listing notice does not reconcile: {date}')
    contracts.to_csv(imports/'contracts.csv', index=False)
    summary = contracts.groupby(['horizon', 'classification', 'eligible']).size().reset_index(name='rows')
    summary.to_csv(output/'contract_coverage.csv', index=False)
    receipt = dict(as_of=args.as_of, rows=len(contracts), contracts_sha256=digest((imports/'contracts.csv').read_bytes()),
                   summary=summary.to_dict('records'), boundary_weeks_not_asserted=boundary,
                   policy='docs/contract_review.json', calendar_review='docs/calendar_review.json',
                   limitations=[policy['scope'], 'No future or unknown listing dates inferred.',
                                'Historical listing timestamps and strike-level availability are not verified.'])
    (output/'contract_validation.json').write_text(json.dumps(receipt, indent=2))
    print(summary.to_string(index=False))
    print(f'Wrote {len(contracts)} contract-period rows; {len(boundary)} initial boundary weeks separately disclosed')


if __name__ == '__main__':
    main()
