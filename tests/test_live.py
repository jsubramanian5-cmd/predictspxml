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
