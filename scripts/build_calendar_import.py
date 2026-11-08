"""Build a dated RTH calendar from reviewed, hash-pinned Cboe calendar PDFs.

No generic holiday rules or third-Friday computation substitute for transcription.
Run --render to retain PDF page images/text for renewed visual review.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from spx_forecast.calendar import OfficialCalendar
from spx_forecast.ingestion import cboe_url


def load_sources(imports, review, render=False):
    sources = {}
    archives = (
        ('cboe-options-calendars-2016-2019.zip', 'source-manifest.json'),
        ('cboe-historical-calendars-and-listing-records.zip', 'calendar-manifest.json'),
    )
    for filename, manifest_name in archives:
        with ZipFile(imports / filename) as archive:
            for entry in json.loads(archive.read(manifest_name)):
                year = str(entry['year'])
                if year not in review['years']:
                    continue
                raw = archive.read(entry['file'])
                sha = hashlib.sha256(raw).hexdigest()
                if sha != review['years'][year]['pdf_sha256']:
                    raise ValueError(f'{year}: PDF changed; renewed visual review required')
                supplied_url = entry['source']
                original = entry.get('original_source', supplied_url)
                match = re.fullmatch(r'https://web\.archive\.org/web/[^/]+/(https?://.+)', original)
                if match:
                    original = match.group(1)
                if not cboe_url(original):
                    raise ValueError(f'{year}: original calendar source is not Cboe')
                sources[year] = dict(source_url=original, archive_url=supplied_url,
                                     annual_pdf_sha256=sha, source_snapshot=f'{filename}!{entry["file"]}')
                if render:
                    import pymupdf
                    root = Path('.runtime/calendar_review/pages')
                    root.mkdir(parents=True, exist_ok=True)
                    with pymupdf.open(stream=raw, filetype='pdf') as document:
                        if len(document) != 1:
                            raise ValueError(f'{year}: expected one reviewed PDF page')
                        document[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).save(root / f'{year}.png')
                        (root / f'{year}.txt').write_text(document[0].get_text(), encoding='utf-8')
    if set(sources) != set(review['years']):
        raise ValueError('Missing reviewed source years')
    return sources


def build_frames(review, sources):
    rows, expirations = [], []
    for year, reviewed in sorted(review['years'].items()):
        holidays = {pd.Timestamp(f'{year}-{date}') for date in reviewed['holidays']}
        if len(holidays) != len(reviewed['holidays']):
            raise ValueError('Duplicate transcribed holidays')
        if any(day.year != int(year) or day.weekday() >= 5 for day in holidays):
            raise ValueError('Holiday transcription must identify weekday closures in its year')
        source = sources[year]
        for day in pd.date_range(f'{year}-01-01', f'{year}-12-31'):
            reason = 'weekend' if day.weekday() >= 5 else 'exchange_holiday' if day in holidays else 'regular_session_including_early_closes'
            rows.append(dict(date=day, is_session=day.weekday() < 5 and day not in holidays,
                             reason=reason, annual_calendar_source_url=source['source_url'], **source))
        if len(reviewed['standard_monthly_days']) != 12:
            raise ValueError('Need exactly twelve transcribed standard expirations per year')
        for month, day in enumerate(reviewed['standard_monthly_days'], 1):
            expirations.append(dict(month=f'{year}-{month:02}', expiration=pd.Timestamp(int(year), month, day),
                                    **source))
    frame = pd.DataFrame(rows)
    for exceptional in review['exceptional_closures']:
        date = pd.Timestamp(exceptional['date'])
        selected = frame.date.eq(date)
        if selected.sum() != 1 or not cboe_url(exceptional['source_url']):
            raise ValueError('Exceptional closure must have a covered date and Cboe source')
        frame.loc[selected, 'is_session'] = False
        frame.loc[selected, 'reason'] = exceptional['reason']
        frame.loc[selected, 'source_url'] = exceptional['source_url']
        frame.loc[selected, 'closure_notice_reference'] = exceptional['reference_id']
    calendar = OfficialCalendar(frame)
    expiration_frame = pd.DataFrame(expirations)
    if not expiration_frame.expiration.isin(calendar.sessions).all():
        raise ValueError('Transcribed standard expiration falls outside verified regular sessions')
    return frame, expiration_frame


def reconcile_prices(calendar, imports):
    """Check supplied observations against dates, never derive closures from prices."""
    records = []
    for filename, date_col, value_col in (
            ('SP500.csv', 'observation_date', 'SP500'),
            ('VIX_History.csv', 'DATE', 'CLOSE'),
            ('VIX9D_History.csv', 'DATE', 'CLOSE')):
        frame = pd.read_csv(imports / filename, na_values=['.'])
        frame['date'] = pd.to_datetime(frame[date_col])
        frame['value'] = pd.to_numeric(frame[value_col], errors='raise')
        start = max(frame.date.min(), calendar.frame.date.min())
        end = min(frame.date.max(), calendar.frame.date.max())
        available = set(frame.loc[frame.value.gt(0) & frame.date.between(start, end), 'date'])
        expected = set(calendar.sessions[(calendar.sessions >= start) & (calendar.sessions <= end)])
        records.append(dict(file=filename, start=str(start.date()), end=str(end.date()),
                            expected_sessions=len(expected), positive_values=len(available),
                            missing_sessions=sorted(str(day.date()) for day in expected - available),
                            observations_on_closed_dates=sorted(str(day.date()) for day in available - expected)))
    return records


def reconcile_standard_expirations(expirations, imports):
    """Independently compare transcribed markings with explicitly dated SET records."""
    lookup = expirations.set_index('month').expiration
    checked, mismatches = 0, []
    with ZipFile(imports/'cboe-monthly-set-am-history.zip') as archive:
        for member in archive.namelist():
            if not re.fullmatch(r'source/\d{4}\.json', member):
                continue
            payload = json.loads(archive.read(member))['data']
            if not isinstance(payload, list):
                continue
            for row in payload:
                if row.get('trading_symbol') != 'SPX' or row.get('settlement_symbol') != 'SET':
                    continue
                date = pd.Timestamp(row['expiration_date'])
                month = str(date.to_period('M'))
                if month not in lookup:
                    continue
                checked += 1
                if lookup[month] != date:
                    mismatches.append(dict(source=member, actual=str(date.date()),
                                           transcribed=str(lookup[month].date())))
    return dict(checked_dated_SET_records=checked, mismatches=mismatches)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render', action='store_true')
    args = parser.parse_args()
    imports, output = Path('data/imports'), Path('artifacts')
    review = json.loads(Path('docs/calendar_review.json').read_text())
    sources = load_sources(imports, review, render=args.render)
    frame, expirations = build_frames(review, sources)
    calendar = OfficialCalendar(frame)
    price_checks = reconcile_prices(calendar, imports)
    expiration_check = reconcile_standard_expirations(expirations, imports)
    # VIX can be published during GTH on dates without an RTH session. Keep those
    # source observations, disclose them, and let feature reindexing exclude them.
    if any(check['missing_sessions'] or (check['file'] != 'VIX_History.csv' and
           check['observations_on_closed_dates']) for check in price_checks) or expiration_check['mismatches']:
        output.mkdir(exist_ok=True)
        (output/'calendar_price_discrepancies.json').write_text(json.dumps(price_checks, indent=2))
        raise ValueError('Calendar/price discrepancies require review; calendar.csv not written')
    output.mkdir(exist_ok=True)
    frame.to_csv(imports/'calendar.csv', index=False)
    expirations.to_csv(output/'standard_monthly_expirations.csv', index=False)
    receipt = dict(start=str(frame.date.min().date()), end=str(frame.date.max().date()),
                   rows=len(frame), sessions=len(calendar.sessions), sources=sources,
                   price_checks=price_checks, standard_expiration_check=expiration_check,
                   review_file='docs/calendar_review.json',
                   limitations=['RTH existence only; early-close days remain sessions.',
                                'Annual calendars supplemented with two dated mourning notices.',
                                'No weekly contract eligibility inferred from trading sessions.',
                                'VIX observations on RTH-closed dates remain in raw data but are excluded from session features.'],
                   gth_reference_urls=[
                       'https://cdn.cboe.com/resources/schedule_update/2022/Cboe-Holiday-Reminder-Closed-on-Monday-May-30-2022.pdf',
                       'https://cdn.cboe.com/resources/regulation/rule_filings/approved/2024/SR-CBOE-2024-031.pdf'])
    (output/'calendar_validation.json').write_text(json.dumps(receipt, indent=2))
    print(f'Wrote {len(frame)} days and {len(calendar.sessions)} sessions to data/imports/calendar.csv')
    print(f'Wrote {len(expirations)} transcribed standard monthly dates; price reconciliation passed')


if __name__ == '__main__':
    main()
