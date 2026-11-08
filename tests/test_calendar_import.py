"""Regression checks for source-backed calendar transcription boundary cases."""
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.build_calendar_import import build_frames, reconcile_prices
from spx_forecast.calendar import OfficialCalendar


def reviewed_frames():
    review = json.loads((Path(__file__).parents[1]/'docs/calendar_review.json').read_text())
    sources = {year: dict(source_url=f'https://www.cboe.com/{year}.pdf', archive_url='',
                           annual_pdf_sha256=data['pdf_sha256'], source_snapshot='synthetic reference')
               for year, data in review['years'].items()}
    return build_frames(review, sources)


def test_reviewed_calendar_handles_exceptional_closures_and_holiday_changes():
    frame, expirations = reviewed_frames()
    calendar = OfficialCalendar(frame)
    assert len(frame) == 4018
    for date in ['2018-12-05', '2025-01-09', '2022-06-20', '2026-06-19']:
        assert pd.Timestamp(date) not in calendar.sessions
    # These are sessions despite later holiday rules or an early closing time.
    for date in ['2021-06-18', '2021-12-31', '2018-07-03', '2026-11-27']:
        assert pd.Timestamp(date) in calendar.sessions
    assert calendar.next_session('2018-12-04') == pd.Timestamp('2018-12-06')
    assert calendar.next_session('2025-01-08') == pd.Timestamp('2025-01-10')
    lookup = expirations.set_index('month').expiration
    assert lookup['2019-04'] == pd.Timestamp('2019-04-18')
    assert lookup['2022-04'] == pd.Timestamp('2022-04-14')
    assert lookup['2025-04'] == pd.Timestamp('2025-04-17')
    assert lookup['2026-06'] == pd.Timestamp('2026-06-18')


def test_transcription_rejects_expiration_on_closed_session():
    review = dict(years={'2026': dict(holidays=['06-19'], standard_monthly_days=[
        16, 20, 20, 17, 15, 19, 17, 21, 18, 16, 20, 18])}, exceptional_closures=[])
    sources = {'2026': dict(source_url='https://www.cboe.com/2026.pdf')}
    with pytest.raises(ValueError, match='outside verified regular sessions'):
        build_frames(review, sources)


def test_price_reconciliation_distinguishes_missing_sessions_from_extra_vix_dates(tmp_path):
    calendar = OfficialCalendar(pd.DataFrame(dict(
        date=pd.date_range('2025-01-08', '2025-01-10'), is_session=[True, False, True],
        source_url='https://www.cboe.com/')))
    (tmp_path/'SP500.csv').write_text('observation_date,SP500\n2025-01-08,5000\n2025-01-09,.\n2025-01-10,5010\n')
    (tmp_path/'VIX_History.csv').write_text('DATE,CLOSE\n2025-01-08,18\n2025-01-09,19\n2025-01-10,20\n')
    (tmp_path/'VIX9D_History.csv').write_text('DATE,CLOSE\n2025-01-08,17\n2025-01-10,18\n')
    results = reconcile_prices(calendar, tmp_path)
    assert not any(row['missing_sessions'] for row in results)
    assert results[1]['observations_on_closed_dates'] == ['2025-01-09']
    assert results[0]['observations_on_closed_dates'] == []
