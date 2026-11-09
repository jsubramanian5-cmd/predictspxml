"""Synthetic integration tests; no synthetic data is exported as a market result."""
import json
from zipfile import ZipFile

import numpy as np
import pandas as pd

from spx_forecast import backtest
from spx_forecast.config import Config
from spx_forecast.forecast import publish_forecast
from spx_forecast.ingestion import Store, audit, collect, digest, merge_settlements, monthly_archive, prices, weekly_archive, weekly_table
import pytest
import requests
from spx_forecast.features import FEATURES


def test_monthly_archive_uses_raw_values_and_never_infers_dates(tmp_path):
    config = Config(raw=tmp_path/'raw', normalized=tmp_path/'normalized',
                    imports=tmp_path/'imports', output=tmp_path/'out')
    config.prepare()
    path = config.imports/'cboe-monthly-set-am-history.zip'
    references = [dict(source_archive_year=y,
                       source=f'https://www.cboe.com/index_settlement_values/get_sv_data/S/{y}/',
                       settlement_value=99999) for y in (2019, 2020)]
    older = '<h4>April 2019 Settlement Values</h4><table><tr><td>S&amp;P 500 (SET)</td><td>2905.2</td></tr></table>'
    def write_archive(date='2020-04-17'):
        with ZipFile(path, 'w') as archive:
            archive.writestr('monthly-set-am-history.json', json.dumps(dict(records=references)))
            archive.writestr('source/2019.json', json.dumps(dict(data=older)))
            archive.writestr('source/2020.json', json.dumps(dict(data=[
                dict(trading_symbol='SPX', settlement_symbol='SET', expiration_date=date, settlement_value=2800),
                dict(trading_symbol='SPXW', settlement_symbol='SPX', expiration_date=date, settlement_value=2790)])))
    write_archive()
    dated, pending = monthly_archive(path, Store(config))
    assert dated.settlement.tolist() == [2800]
    assert dated.expiration.tolist() == [pd.Timestamp('2020-04-17')]
    assert dated.settlement_type.tolist() == ['SET_AM']
    assert dated.published_at.isna().all()
    assert pending.month.tolist() == ['2019-04']
    assert pending.settlement.tolist() == [2905.2]
    assert 'expiration' not in pending
    # Summary values are deliberately wrong: raw responses control the values.
    write_archive('2019-04-18')
    with pytest.raises(ValueError, match='Monthly expiration does not match source year'):
        monthly_archive(path, Store(config))


def test_weekly_archive_reads_both_formats_and_quarantines_annotations(tmp_path):
    config = Config(raw=tmp_path/'raw', normalized=tmp_path/'normalized',
                    imports=tmp_path/'imports', output=tmp_path/'out')
    config.prepare()
    path = config.imports/'cboe-weeklys-settlement-values-all-years.zip'
    html = '''<table><tr><th>Trading Symbol</th><th>Expiration Date</th><th>Settlement Value</th></tr>
    <tr><td> SPXW </td><td>08/09/2019</td><td>2900</td></tr>
    <tr><td>SPXW</td><td>08/12/2019</td><td>2883.75*</td></tr>
    <tr><td>XSP</td><td>08/09/2019</td><td>290</td></tr></table>'''
    entries = [dict(year=y, file=f'{y}.json',
                    source=f'https://www.cboe.com/index_settlement_values/get_sv_data/W/{y}/')
               for y in (2019, 2020)]
    with ZipFile(path, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(dict(years=entries)))
        archive.writestr('2019.json', json.dumps(dict(data=html)))
        archive.writestr('2020.json', json.dumps(dict(data=[
            dict(trading_symbol='SPXW', expiration_date='2020-12-24', settlement_value=3703.06),
            dict(trading_symbol='XSP', expiration_date='2020-12-24', settlement_value=370.306)])))
    store = Store(config)
    frame = weekly_archive(path, store)
    assert frame.settlement.tolist() == [2900, 3703.06]
    assert frame.expiration.tolist() == [pd.Timestamp('2019-08-09'), pd.Timestamp('2020-12-24')]
    assert frame.published_at.isna().all()
    review = pd.read_csv(config.output/'weekly_archive_review.csv')
    assert review.source_value.tolist() == ['2883.75*']
    assert all((config.raw/m['snapshot']).exists() for m in store.manifest)
    # Supplied source references must remain on the expected Cboe archive endpoint.
    entries[0]['source'] = 'https://example.com/2019'
    with ZipFile(path, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(dict(years=entries)))
    with pytest.raises(ValueError, match='Unexpected weekly archive source'):
        weekly_archive(path, Store(config))


def test_manual_price_downloads_work_offline_and_preserve_receipt(tmp_path, monkeypatch):
    config = Config(raw=tmp_path/'raw', normalized=tmp_path/'normalized',
                    imports=tmp_path/'imports', output=tmp_path/'out',
                    start='2024-01-01', end='2024-12-31', refresh=True)
    config.prepare()
    (config.imports/'SP500.csv').write_text('observation_date,SP500\n2023-12-29,4700\n2024-01-02,4750\n2024-01-03,.\n')
    for filename in ('VIX_History.csv', 'VIX9D_History.csv'):
        (config.imports/filename).write_text('DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2024,14,15,12,13\n')

    def offline(self, name, url):
        assert name not in ('spx', 'vix', 'vix9d')
        raise requests.ConnectionError('offline fixture')

    monkeypatch.setattr(Store, 'fetch', offline)
    datasets, issues, manifest = collect(config)
    assert datasets['spx'].value.iloc[0] == 4750
    assert pd.isna(datasets['spx'].value.iloc[1])
    assert datasets['vix9d'].value.tolist() == [13]
    assert datasets['spx'].published_at.isna().all()
    assert not set(issues.dataset) & {'spx', 'vix', 'vix9d'}
    original = next(m for m in manifest if m['dataset'] == 'spx')
    assert original['source_method'] == 'user_supplied_download'
    _, _, repeated = collect(config)
    assert next(m for m in repeated if m['dataset'] == 'spx')['retrieved_at'] == original['retrieved_at']


def test_cached_requests_verify_hash_without_network(tmp_path):
    config = Config(raw=tmp_path / 'raw', normalized=tmp_path / 'normalized',
                    imports=tmp_path / 'imports', output=tmp_path / 'out')
    config.prepare()
    data = b'observation_date,SP500\n2024-01-02,4750\n2024-01-03,.\n'
    meta = dict(dataset='spx', snapshot='spx.raw', sha256=digest(data),
                source_url='https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500',
                retrieved_at='2024-01-04T12:00Z')
    (config.raw / 'spx.raw').write_bytes(data)
    (config.raw / 'spx.latest.json').write_text(json.dumps(meta))
    fetched, metadata = Store(config).fetch('spx', meta['source_url'])
    result = prices(fetched, metadata, 'spx')
    assert result.value.iloc[0] == 4750
    assert pd.isna(result.value.iloc[1])
    assert result.published_at.isna().all()


def test_weekly_adapter_excludes_other_symbols():
    html = b'''<table><tr><th>Trading Symbol</th><th>Expiration Date</th><th>Settlement Value</th></tr>
    <tr><td>SPXW</td><td>2024-03-28</td><td>5250</td></tr>
    <tr><td>XSP</td><td>2024-03-28</td><td>525</td></tr></table>'''
    metadata = dict(source_url='https://www.cboe.com/index_settlement_values/weeklys_settlement_values/',
                    retrieved_at='2024-03-29T12:00Z', sha256=digest(html))
    result = weekly_table(html, metadata)
    assert result.settlement.tolist() == [5250]
    assert result.settlement_type.tolist() == ['SPXW_PM']


def test_matching_web_and_csv_settlements_normalize_before_deduplication():
    web = pd.DataFrame([dict(horizon='weekly', expiration=pd.Timestamp('2024-03-28'),
                             settlement_type='SPXW_PM', settlement=5250.0,
                             published_at=pd.NaT, source_url='https://www.cboe.com/')])
    imported = pd.DataFrame([dict(horizon='weekly', expiration='2024-03-28',
                                  settlement_type='SPXW_PM', settlement='5250',
                                  published_at='2024-03-28T20:30:00Z',
                                  source_url='https://www.cboe.com/')])
    merged = merge_settlements([web, imported])
    assert len(merged) == 1
    assert merged.iloc[0].published_at == pd.Timestamp('2024-03-28T20:30:00Z')
    assert merged.iloc[0].settlement == 5250.0
    imported['settlement'] = '5251'
    with pytest.raises(ValueError, match='Duplicate settlement keys'):
        merge_settlements([web, imported])


def test_empty_data_audit_preserves_coverage_and_duplicate_columns():
    result = audit({})
    assert {'start', 'end', 'missing_sessions', 'duplicate_records', 'valid_settlements',
            'usable_forecasts'} <= set(result)
    assert result.start.isna().all() and result.end.isna().all()
    assert result.loc[result.dataset.eq('spx'), 'duplicate_records'].isna().all()


def test_development_freezes_and_final_training_remains_chronological(monkeypatch, train_frame):
    obs = train_frame.iloc[:24].copy()
    obs['horizon'] = 'monthly'
    obs['expiration'] = pd.date_range('2020-03-20', periods=24, freq='QS')
    obs['reference_date'] = obs.expiration - pd.Timedelta(days=30)
    obs['issue_time'] = pd.to_datetime(obs.reference_date, utc=True) + pd.Timedelta(hours=20)
    obs['label_available_at'] = pd.to_datetime(obs.expiration, utc=True) + pd.Timedelta(days=1)
    obs['reference_close'] = 5000.0
    obs['settlement'] = 5000*np.exp(obs.target/10000)
    obs['settlement_type'] = 'SET_AM'
    obs['timing_assumed'] = True
    obs['label_timing_assumed'] = True
    monkeypatch.setattr(backtest, 'candidates', lambda horizon: [dict(family='empirical'),
                                                               dict(family='scaled'),
                                                               dict(family='linear', alpha=0.1)])
    plan = dict(training_start=pd.Timestamp('2020-01-01'), development_start=pd.Timestamp('2022-01-01'),
                test_start=pd.Timestamp('2024-01-01'), test_end=pd.Timestamp('2025-12-31'))
    progress = []
    dev, frozen = backtest.develop(obs, plan, 'monthly', 42, progress=progress.append)
    assert progress[-1]['completed'] == progress[-1]['total']
    assert all(item['training_cutoff'] <= item['issue_time'] for item in progress)
    assert all(item['stage'] == 'development' for item in progress)
    frozen_before = json.dumps(frozen, default=str, sort_keys=True)
    final = backtest.holdout(obs, frozen, 42)
    assert json.dumps(frozen, default=str, sort_keys=True) == frozen_before
    assert (final.training_cutoff <= final.issue_time).all()
    assert set(final.model) == {backtest.model_key(frozen['benchmark']), backtest.model_key(frozen['ml'])}
    assert len(final) == 16
    assert backtest.promote(final, frozen) in [frozen['benchmark'], frozen['ml']]
    poisoned = obs.copy()
    poisoned.loc[poisoned.expiration.ge('2024-01-01'), 'target'] = 1e9
    again, again_frozen = backtest.develop(poisoned, plan, 'monthly', 42)
    pd.testing.assert_frame_equal(dev, again)
    assert frozen_before == json.dumps(again_frozen, default=str, sort_keys=True)


def test_forecast_publication_is_idempotent(tmp_path, calendar, datasets, train_frame):
    config = Config(reference_date='2024-03-22', output=tmp_path,
                    raw=tmp_path/'raw', normalized=tmp_path/'normalized', imports=tmp_path/'imports')
    config.prepare()
    # Synthetic receipt evidence is within the live reference cutoff.
    for source in datasets.values():
        source['retrieved_at'] = '2024-03-22T23:00:00Z'
    obs = train_frame.iloc[:4].copy()
    obs['horizon'] = 'weekly'
    obs['reference_date'] = pd.to_datetime(['2024-02-02', '2024-02-09', '2024-02-16', '2024-03-22'])
    obs['expiration'] = obs.reference_date + pd.Timedelta(days=7)
    obs.loc[obs.index[-1], 'expiration'] = pd.Timestamp('2024-03-28')
    obs['issue_time'] = pd.to_datetime(obs.reference_date, utc=True) + pd.Timedelta(days=1)
    obs['label_available_at'] = pd.to_datetime(obs.expiration, utc=True) + pd.Timedelta(days=1)
    obs['reference_close'] = 5000.0
    obs['settlement_type'] = 'SPXW_PM'
    obs.loc[obs.index[-1], 'target'] = np.nan
    first, status = publish_forecast('weekly', obs, {'family': 'empirical'}, config, datasets,
                                     calendar, [], pd.Timestamp('2024-03-23T00:30:00Z'))
    assert status == 'published'
    second, status = publish_forecast('weekly', obs, {'family': 'scaled'}, config, datasets,
                                      calendar, [], pd.Timestamp('2024-03-23T01:30:00Z'))
    assert status == 'already_published'
    assert first['actual_issue_time'] == second['actual_issue_time']
    assert len(pd.read_csv(tmp_path/'forecasts.csv')) == 1
    assert first['lower'] <= first['median'] <= first['upper']


def _schedule(rows):
    frame = pd.DataFrame(rows, columns=['horizon', 'reference_date', 'expiration']).astype(
        {'reference_date': 'datetime64[ns]', 'expiration': 'datetime64[ns]'})
    # Issue window closes 08:00 New York (13:00 UTC) on the next session, approximated as the next day.
    frame['cutoff'] = frame.reference_date + pd.Timedelta(days=3, hours=13)
    return frame


def test_upcoming_reference_date_picks_next_unexpired_contract_per_horizon():
    from spx_forecast.workflow import upcoming_reference_date
    schedule = _schedule([
        ('weekly', '2026-10-30', '2026-11-06'), ('weekly', '2026-11-06', '2026-11-13'),
        ('weekly', '2026-11-13', '2026-11-20'), ('monthly', '2026-10-16', '2026-11-20')])
    now = pd.Timestamp('2026-11-09T00:46:00Z')
    assert upcoming_reference_date(schedule, 'weekly', now) == pd.Timestamp('2026-11-06')
    # The 10-16 reference's issue window closed on 10-19, so no monthly target is open yet.
    assert upcoming_reference_date(schedule, 'monthly', now) is None
    assert upcoming_reference_date(schedule, 'weekly', pd.Timestamp('2026-12-01T00:00:00Z')) is None


def test_auto_reference_reports_last_listed_expiration_when_no_upcoming_contract(tmp_path):
    from spx_forecast.workflow import forecast_once
    config = Config(reference_date=None, output=tmp_path, raw=tmp_path/'raw',
                    normalized=tmp_path/'normalized', imports=tmp_path/'imports')
    config.prepare()
    state = dict(calendar=None, selected={}, statuses={}, observations={}, manifest=[], datasets={},
                 schedule=_schedule([('weekly', '2026-10-30', '2026-11-06'), ('monthly', '2026-09-18', '2026-10-16')]))
    records, status = forecast_once(config, state, pd.Timestamp('2026-11-09T00:46:00Z'))
    assert records.empty
    assert status.status.str.startswith('pending: no verified').all()
    assert 'latest listed expiration is 2026-11-06' in status.status.iloc[0]
    assert 'latest listed expiration is 2026-10-16' in status.status.iloc[1]


def _standard():
    return pd.DataFrame({'month': ['2024-03', '2024-04', '2024-05'],
                         'expiration': pd.to_datetime(['2024-03-15', '2024-04-19', '2024-05-17']),
                         'source_url': 'https://cdn.cboe.com/resources/options/Cboe2024OPTIONSCalendar.pdf'})


def test_upcoming_contracts_are_calendar_classified_and_tagged(calendar):
    from spx_forecast.calendar import contract_observations
    from spx_forecast.upcoming import LISTING_BASIS, merge_contracts, upcoming_contracts
    last = {'weekly': pd.Timestamp('2024-03-01'), 'monthly': pd.Timestamp('2024-02-16')}
    rows = upcoming_contracts(calendar, _standard(), last, '2024-04-12', 'https://www.sec.gov/rule.pdf')
    weekly, monthly = (rows.loc[rows.horizon.eq(h)].set_index('nominal_expiration') for h in ('weekly', 'monthly'))
    assert (rows.listing_basis == LISTING_BASIS).all() and (rows.settlement_source_sha256 == '').all()
    assert list(weekly.index.strftime('%m-%d')) == ['03-08', '03-15', '03-22', '03-29', '04-05', '04-12']
    assert weekly.classification['2024-03-15'] == 'standard_monthly_collision'
    # 2024-03-29 is a closed Friday and the Thursday is the month's last session.
    assert weekly.classification['2024-03-29'] == 'end_of_month_collision' and not weekly.eligible['2024-03-29']
    assert weekly.eligible[['2024-03-08', '2024-03-22', '2024-04-05', '2024-04-12']].all()
    assert list(monthly.expiration.dt.strftime('%m-%d')) == ['03-15']  # 04-19 lies beyond `through`
    settled = pd.DataFrame([dict(horizon='monthly', nominal_expiration='2024-02-16', expiration='2024-02-16',
                                 settlement_type='SET_AM', eligible=True, source_url='https://www.cboe.com/')])
    schedule = contract_observations(merge_contracts(settled, rows), calendar)
    # The settled February row only anchors March's reference session; it has no predecessor itself.
    assert set(schedule.listing_basis) == {LISTING_BASIS}
    assert schedule.loc[schedule.horizon.eq('monthly')].reference_date.iloc[0] == pd.Timestamp('2024-02-16')


def test_settled_contracts_take_precedence_over_upcoming_rows(calendar):
    from spx_forecast.upcoming import merge_contracts, upcoming_contracts
    last = {'weekly': pd.Timestamp('2024-03-01'), 'monthly': pd.Timestamp('2024-02-16')}
    upcoming = upcoming_contracts(calendar, _standard(), last, '2024-03-22', 'https://www.sec.gov/rule.pdf')
    settled = pd.DataFrame([dict(horizon='weekly', nominal_expiration='2024-03-08', expiration='2024-03-08',
                                 settlement_type='SPXW_PM', eligible=True, source_url='https://www.cboe.com/')])
    merged = merge_contracts(settled, upcoming)
    weekly = merged.loc[merged.horizon.eq('weekly')]
    assert len(weekly) == len(upcoming.loc[upcoming.horizon.eq('weekly')])  # 03-08 appears once
    assert weekly.loc[pd.to_datetime(weekly.nominal_expiration).eq('2024-03-08')].listing_basis.item() == 'settlement_record'


def test_upcoming_contracts_cannot_extend_beyond_verified_calendar(calendar):
    from spx_forecast.upcoming import upcoming_contracts
    with pytest.raises(ValueError, match='beyond the verified calendar'):
        upcoming_contracts(calendar, _standard(), {'weekly': pd.Timestamp('2025-12-05'),
                           'monthly': pd.Timestamp('2025-11-21')}, '2026-01-30', 'https://www.sec.gov/rule.pdf')


def test_calendar_runway_warns_then_fails_loudly(calendar):
    from spx_forecast.upcoming import CalendarRunwayError, check_calendar_runway
    # The fixture calendar ends 2025-12-31.
    ok = check_calendar_runway(calendar, '2025-06-30', warn_days=60)
    assert ok['warning'] is None and ok['runway_days'] == 184 and ok['calendar_end'] == '2025-12-31'
    soon = check_calendar_runway(calendar, '2025-11-09', warn_days=60)
    assert soon['runway_days'] == 52 and '2026 Cboe annual calendar' in soon['warning']
    assert check_calendar_runway(calendar, '2025-12-31')['runway_days'] == 0
    with pytest.raises(CalendarRunwayError, match='ended 2025-12-31, 5 days before 2026-01-05'):
        check_calendar_runway(calendar, '2026-01-05')
