import json

import pandas as pd
import pytest

from spx_forecast.live import OK, SUPPRESSED, WARNING, append_history, exit_code, load_selected
from spx_forecast.refresh import merge_history

FRED_OLD = "observation_date,SP500\n2016-11-23,2204.72\n2016-11-24,\n2016-11-25,2213.35\n"


def test_merge_keeps_existing_rows_and_appends_only_later_dates():
    new = "observation_date,SP500\n2016-11-25,2213.35\n2016-11-28,2201.72\n2016-11-29,.\n"
    text, report = merge_history(FRED_OLD, new, 'observation_date', 'SP500')
    assert text == FRED_OLD + "2016-11-28,2201.72\n2016-11-29,\n"
    assert report == dict(appended_rows=2, overlapping_mismatches=0, last_date='2016-11-29',
                          previous_last_date='2016-11-25')


def test_merge_reports_but_never_rewrites_revised_history():
    new = "observation_date,SP500\n2016-11-23,2999.00\n2016-11-28,2201.72\n"
    text, report = merge_history(FRED_OLD, new, 'observation_date', 'SP500')
    assert '2204.72' in text and '2999.00' not in text
    assert report['overlapping_mismatches'] == 1 and report['appended_rows'] == 1


def test_merge_handles_cboe_dates_and_rejects_unexpected_columns():
    old = "DATE,OPEN,HIGH,LOW,CLOSE\n11/05/2026,1,2,0.5,17.14\n"
    new = "DATE,OPEN,HIGH,LOW,CLOSE\n11/05/2026,1,2,0.5,17.14\n11/06/2026,1,2,0.5,25.64\n"
    text, report = merge_history(old, new, 'DATE', 'CLOSE', '%m/%d/%Y')
    assert text.endswith("11/06/2026,1,2,0.5,25.64\n") and report['appended_rows'] == 1
    with pytest.raises(ValueError, match='Unexpected columns'):
        merge_history(old, "date,close\n11/06/2026,1\n", 'DATE', 'CLOSE', '%m/%d/%Y')


def _statuses(timing, status):
    return pd.DataFrame([dict(checked_at=pd.Timestamp('2026-11-09T00:46:00Z'), horizon='weekly',
                              timing_status=timing, status=status)])


def test_exit_codes_separate_normal_waiting_from_missed_windows_and_refresh_problems():
    waiting = _statuses('waiting_for_inputs', 'pending: no eligible contract with complete features')
    assert exit_code(waiting) == OK
    assert exit_code(_statuses('suppressed_late_inputs', 'suppressed_late_inputs')) == SUPPRESSED
    assert exit_code(waiting, {'spx': {'status': 'failed', 'error': 'timeout'}}) == WARNING
    assert exit_code(waiting, {'spx': {'status': 'ok', 'overlapping_mismatches': 2}}) == WARNING
    assert exit_code(waiting, {'spx': {'status': 'ok', 'overlapping_mismatches': 0}}) == OK
    # A missed window outranks a refresh warning.
    assert exit_code(_statuses('suppressed_incomplete', 'suppressed_incomplete'),
                     {'spx': {'status': 'failed'}}) == SUPPRESSED


def test_history_is_append_only_with_one_header(tmp_path):
    path = tmp_path / 'history.csv'
    append_history(path, _statuses('waiting_for_inputs', 'pending'))
    append_history(path, _statuses('ready', 'published'))
    frame = pd.read_csv(path)
    assert list(frame.status) == ['pending', 'published'] and list(frame.columns)[:2] == ['checked_at', 'horizon']


def test_load_selected_requires_a_completed_backtest(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_selected(tmp_path)
    (tmp_path / 'run_metadata.json').write_text(json.dumps({'selected': {}}))
    with pytest.raises(ValueError, match='no selected model'):
        load_selected(tmp_path)
    (tmp_path / 'run_metadata.json').write_text(json.dumps({'selected': {'weekly': {'family': 'scaled'}}}))
    assert load_selected(tmp_path) == {'weekly': {'family': 'scaled'}}


def test_any_warning_such_as_calendar_runway_gives_warning_exit_code():
    waiting = _statuses('waiting_for_inputs', 'pending')
    assert exit_code(waiting, warnings=['Verified calendar ends 2026-12-31']) == WARNING
    assert exit_code(waiting, warnings=[]) == OK
    assert exit_code(_statuses('suppressed_late_inputs', 'suppressed_late_inputs'), warnings=['x']) == SUPPRESSED


def _prices(rows, source_url='https://fred.stlouisfed.org/'):
    frame = pd.DataFrame(rows, columns=['date', 'value'])
    frame['date'] = pd.to_datetime(frame.date)
    return frame.assign(published_at=pd.NaT, source_url=source_url, retrieved_at='2026-11-06T22:00:00Z', sha256='t')


def test_cboe_fills_only_dates_after_fred_and_never_overrides_fred():
    from spx_forecast.reference import CBOE_SOURCE, FRED_SOURCE, splice_cboe_tail
    fred = _prices([('2026-11-04', 7688.62), ('2026-11-05', 7353.73)])
    cboe = _prices([('2026-11-04', 9999.0), ('2026-11-05', 9999.0), ('2026-11-06', 7376.86)])
    result = splice_cboe_tail(fred, cboe)
    assert list(result.date.dt.strftime('%m-%d')) == ['11-04', '11-05', '11-06']
    assert list(result.value) == [7688.62, 7353.73, 7376.86]
    assert list(result.source) == [FRED_SOURCE, FRED_SOURCE, CBOE_SOURCE]
    assert not result.date.duplicated().any()


def test_reconcile_reports_differences_beyond_tolerance_and_warns_only_when_recent():
    from spx_forecast.reference import reconcile, recent_mismatch_warning
    fred = _prices([('2019-02-28', 2784.49), ('2026-11-04', 7688.62), ('2026-11-05', 7353.73)])
    cboe = _prices([('2019-02-28', 2792.38), ('2026-11-04', 7688.6200), ('2026-11-05', 7353.80)])
    recon = reconcile(fred, cboe)
    assert list(recon.date.dt.strftime('%Y-%m-%d')) == ['2019-02-28', '2026-11-05']
    assert recon.difference.round(2).tolist() == [-7.89, -0.07]
    now = pd.Timestamp('2026-11-09T00:46:00Z')
    assert 'largest -0.07 points on 2026-11-05' in recent_mismatch_warning(recon, now)
    assert recent_mismatch_warning(recon.iloc[:1], now) is None  # old history differences are known, not new news
    assert recent_mismatch_warning(recon.iloc[:0], now) is None


def test_first_cboe_download_is_kept_as_supplied_then_merged_later(tmp_path):
    from spx_forecast.refresh import refresh_prices

    class Response:
        def __init__(self, text): self.content, self.url = text.encode(), 'https://cdn-api.cboe.com/x.csv'
        def raise_for_status(self): pass

    class Session:
        headers = {}
        def __init__(self, text): self.text = text
        def get(self, url, timeout=None): return Response(self.text)

    first = "DATE,SPX\r\n11/05/2026,7353.730000\r\n11/06/2026,7376.860000\r\n"
    # Only the Cboe SPX series is requested here: stub the other two as failed fetches.
    results = refresh_prices(tmp_path, Session(first))
    assert results['spx_cboe']['status'] == 'ok' and results['spx_cboe']['appended_rows'] == 2
    assert (tmp_path / 'SPX_History.csv').read_text() == "DATE,SPX\n11/05/2026,7353.730000\n11/06/2026,7376.860000\n"
    later = first + "11/09/2026,7400.000000\r\n"
    again = refresh_prices(tmp_path, Session(later))
    assert again['spx_cboe']['appended_rows'] == 1 and again['spx_cboe']['previous_last_date'] == '2026-11-06'


def _forecasts(rows):
    return pd.DataFrame(rows, columns=['horizon', 'reference_date', 'actual_issue_time', 'expiration',
                                       'settlement_type', 'reference_close', 'lower', 'median', 'upper',
                                       'model_version', 'timing_status'])


def test_daily_table_lists_active_forecasts_and_explains_missing_ones(tmp_path):
    from spx_forecast.live import DAILY_COLUMNS, daily_forecast_table, write_daily_forecast
    forecasts = _forecasts([
        ('weekly', '2026-11-06', '2026-11-09T01:52:20+00:00', '2026-11-13', 'SPXW_PM', 7376.86, 7097.7794, 7445.9049, 7684.3619, 'v1', 'x'),
        ('weekly', '2026-10-30', '2026-11-03T01:00:00+00:00', '2026-11-06', 'SPXW_PM', 7353.73, 7000.0, 7300.0, 7600.0, 'v0', 'x')])
    statuses = pd.DataFrame([dict(horizon='monthly', status='pending: not yet eligible')])
    table = daily_forecast_table(forecasts, statuses, '2026-11-09')
    assert list(table.columns) == DAILY_COLUMNS
    weekly, monthly = table.iloc[0], table.iloc[1]
    assert len(table) == 2  # the expired 11-06 forecast is not listed
    assert weekly.forecast_status == 'issued' and weekly.expiration == '2026-11-13'
    assert (weekly.p10_level, weekly.median_level, weekly.p90_level) == (7097.78, 7445.9, 7684.36)
    assert monthly.forecast_status == 'no_forecast' and monthly.note == 'pending: not yet eligible'
    # New York date, not UTC: 02:30 UTC on 11-10 is still the evening of 11-09 in New York.
    path, _ = write_daily_forecast(tmp_path, forecasts, statuses, pd.Timestamp('2026-11-10T02:30:00Z'))
    assert path.name == 'forecast_2026-11-09.csv' and (tmp_path / 'latest_forecast.csv').exists()
    assert pd.read_csv(path).shape == (2, len(DAILY_COLUMNS))


def test_scorecard_scores_when_settled_uses_proxy_only_for_weekly_and_summarises():
    import numpy as np
    from spx_forecast.live import CLOSE_PROXY, OFFICIAL, live_scorecard, scorecard_summary
    forecasts = _forecasts([
        ('weekly', '2026-11-06', '2026-11-09T02:21:00+00:00', '2026-11-13', 'SPXW_PM', 7376.86, 7097.78, 7445.90, 7684.36, 'v', 'x'),
        ('weekly', '2026-11-13', '2026-11-16T02:00:00+00:00', '2026-11-20', 'SPXW_PM', 7400.0, 7100.0, 7450.0, 7700.0, 'v', 'x'),
        ('weekly', '2026-10-30', '2026-11-03T02:00:00+00:00', '2026-11-06', 'SPXW_PM', 7353.73, 7000.0, 7300.0, 7400.0, 'v', 'x'),
        ('monthly', '2026-10-16', '2026-10-19T02:00:00+00:00', '2026-11-20', 'SET_AM', 7500.0, 7000.0, 7500.0, 8000.0, 'v', 'x')])
    settlements = pd.DataFrame([dict(horizon='weekly', expiration=pd.Timestamp('2026-11-06'), settlement_type='SPXW_PM', settlement=7376.86)])
    closes = pd.DataFrame({'date': pd.to_datetime(['2026-11-13', '2026-11-20']), 'value': [7800.0, 7390.0]})
    card = live_scorecard(forecasts, settlements, closes).set_index(['horizon', 'expiration'])
    official = card.loc[('weekly', '2026-11-06')]
    assert official.status == 'scored' and official.actual_source == OFFICIAL and official.inside_80 == True  # noqa: E712
    proxy = card.loc[('weekly', '2026-11-13')]
    assert proxy.actual_source == CLOSE_PROXY and proxy.inside_80 == False  # 7800 above the 7684.36 upper bound  # noqa: E712
    assert proxy.error_points == round(7800.0 - 7445.90, 2) and proxy.interval_score_bps > 0
    # Monthly SET AM never falls back to the close, even though a close exists for that day.
    assert card.loc[('monthly', '2026-11-20')].status == 'awaiting_settlement'
    summary = scorecard_summary(card.reset_index(), {'weekly': (0.783, 662.5)}).set_index('horizon')
    assert summary.loc['weekly', ['issued', 'scored', 'inside_80', 'proxy_actuals']].tolist() == [3, 3, 2, 2]
    assert summary.loc['weekly', 'backtest_coverage_80'] == 0.783 and summary.loc['monthly', 'scored'] == 0
