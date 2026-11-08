"""Target-family classification and independently verified monthly date joins."""
import pandas as pd
import pytest

from scripts.build_contract_import import build_contracts
from spx_forecast.calendar import OfficialCalendar
from spx_forecast.ingestion import resolve_monthly_dates


def contract_inputs():
    dates = pd.date_range('2024-03-01', '2024-04-30')
    frame = pd.DataFrame(dict(date=dates, is_session=dates.weekday < 5,
                              source_url='https://www.cboe.com/calendar.pdf',
                              annual_calendar_source_url='https://www.cboe.com/calendar.pdf',
                              annual_pdf_sha256='synthetic-calendar'))
    frame.loc[frame.date.eq('2024-03-29'), 'is_session'] = False
    calendar = OfficialCalendar(frame)
    standard = pd.DataFrame(dict(month=['2024-03', '2024-04'],
                                  expiration=pd.to_datetime(['2024-03-15', '2024-04-19']),
                                  source_url='https://www.cboe.com/calendar.pdf',
                                  annual_pdf_sha256='synthetic-calendar'))
    # All weekdays have synthetic SPXW labels, including non-EOW expiry families.
    labels = pd.DataFrame(dict(expiration=calendar.sessions, horizon='weekly', settlement_type='SPXW_PM',
                               source_url='https://www.cboe.com/settlements/', sha256='synthetic-labels'))
    policy = dict(eow_definition_source='https://www.sec.gov/synthetic-rule',
                  third_friday_pm_distinction_source='https://www.cboe.com/synthetic-rule')
    return calendar, standard, labels, policy


def test_contract_builder_excludes_monthly_and_month_end_even_with_pm_labels():
    calendar, standard, labels, policy = contract_inputs()
    contracts, boundary = build_contracts(calendar, standard, labels, '2024-04-26', policy)
    weekly = contracts.loc[contracts.horizon.eq('weekly')].set_index('nominal_expiration')
    assert weekly.loc['2024-03-15', 'classification'] == 'standard_monthly_collision'
    assert not weekly.loc['2024-03-15', 'eligible']
    assert weekly.loc['2024-03-29', 'classification'] == 'end_of_month_collision'
    assert not weekly.loc['2024-03-29', 'eligible']
    assert weekly.loc['2024-03-29', 'scheduled_settlement_date'] == pd.Timestamp('2024-03-28')
    assert weekly.loc['2024-03-29', 'expiration'] == pd.Timestamp('2024-03-29')
    assert weekly.loc['2024-03-22', 'eligible']
    assert weekly.index.max() == pd.Timestamp('2024-04-26')
    assert len(boundary) == 2
    assert contracts.loc[contracts.horizon.eq('monthly'), 'settlement_type'].eq('SET_AM').all()


def test_missing_eow_record_never_becomes_an_ineligible_week():
    calendar, standard, labels, policy = contract_inputs()
    labels = labels.loc[labels.expiration.ne('2024-04-12')]
    with pytest.raises(ValueError, match='require explicit archive records'):
        build_contracts(calendar, standard, labels, '2024-04-26', policy)


def test_monthly_dates_resolve_only_from_verified_am_contracts(calendar):
    contracts = pd.DataFrame([dict(horizon='monthly', nominal_expiration='2024-03-15',
                                   expiration='2024-03-15', settlement_type='SET_AM', eligible=True,
                                   source_url='https://www.cboe.com/calendar.pdf', sha256='contract-file',
                                   annual_pdf_sha256='calendar-pdf')])
    pending = pd.DataFrame([dict(month=month, settlement=5000, source_value='5000',
                                 reason='Date missing', source_url='https://www.cboe.com/SET/',
                                 retrieved_at='2026-11-07T20:00Z', sha256='settlement-file')
                            for month in ['2024-03', '2022-01']])
    dated = pd.DataFrame(columns=['horizon', 'expiration', 'settlement_type', 'settlement',
                                  'source_url', 'published_at'])
    combined, remaining, resolved = resolve_monthly_dates(dated, pending, contracts, calendar.frame)
    assert combined.expiration.tolist() == [pd.Timestamp('2024-03-15')]
    assert combined.settlement.tolist() == [5000]
    assert combined.published_at.isna().all()
    assert remaining.month.tolist() == ['2022-01']
    assert resolved.date_source_pdf_sha256.tolist() == ['calendar-pdf']
    assert resolved.date_source_import_sha256.tolist() == ['contract-file']
    assert resolved.sha256.tolist() == ['settlement-file']
    contracts['settlement_type'] = 'SPXW_PM'
    with pytest.raises(ValueError, match='standard AM contracts'):
        resolve_monthly_dates(dated, pending, contracts, calendar.frame)


def test_monthly_date_resolution_rejects_ambiguous_or_closed_dates(calendar):
    pending = pd.DataFrame([dict(month='2024-03', settlement=5000, source_value='5000',
                                 reason='Date missing', source_url='https://www.cboe.com/SET/',
                                 retrieved_at='2026-11-07T20:00Z', sha256='synthetic')])
    dated = pd.DataFrame()
    row = dict(horizon='monthly', nominal_expiration='2024-03-15', expiration='2024-03-29',
               settlement_type='SET_AM', eligible=True, source_url='https://www.cboe.com/')
    with pytest.raises(ValueError, match='verified regular sessions'):
        resolve_monthly_dates(dated, pending, pd.DataFrame([row]), calendar.frame)
    row['expiration'] = '2024-03-15'
    with pytest.raises(ValueError, match='Ambiguous monthly'):
        resolve_monthly_dates(dated, pending, pd.DataFrame([row, row]), calendar.frame)
