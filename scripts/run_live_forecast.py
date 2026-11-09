"""Scheduled live forecast: refresh inputs, rebuild upcoming contracts, then issue forecasts once.

Order matters: prices first, then upcoming contracts (they depend on contracts.csv), then the load and
one availability check. Models come from the last backtest (`artifacts/run_metadata.json`); this run never
re-selects or re-tunes. Exit codes: 0 ran cleanly, 1 error (including an ended calendar), 2 warning (refresh problem or calendar
nearly out), 3 a horizon missed its window.
Run from the repository root:  python -m scripts.run_live_forecast   (needs the package installed or PYTHONPATH=src)
"""
import argparse
import dataclasses
import json
import logging
import sys
import traceback
from pathlib import Path

import pandas as pd

from scripts.build_upcoming_contracts import build as build_upcoming
from spx_forecast import workflow
from spx_forecast.config import Config
from spx_forecast.live import ERROR, append_history, exit_code, load_selected, write_daily_forecast
from spx_forecast.reference import recent_mismatch_warning
from spx_forecast.refresh import refresh_prices

log = logging.getLogger('live_forecast')


def run(now=None, skip_refresh=False):
    """`now` overrides the clock for replays. By default the issue time is read just before the forecast step,
    after the data load: input receipt times are stamped during the load, and a time captured earlier would
    make the first run after new data look a few seconds too early."""
    started = now if now is not None else pd.Timestamp.now(tz='UTC')
    config = dataclasses.replace(Config(), end=str(started.date()), reference_date=None, live_cboe_spx=True)
    config.prepare()
    refresh_results = {}
    if skip_refresh:
        log.info('1/4 price refresh skipped')
    else:
        refresh_results = refresh_prices(config.imports, timeout=config.timeout_seconds)
        log.info('1/4 price refresh: %s', json.dumps(refresh_results))
    _, receipt, through = build_upcoming(started.date(), imports=config.imports, output=config.output)
    log.info('2/4 upcoming contracts: %d rows through %s (calendar ends %s)', receipt['rows'], through.date(),
             receipt['calendar_end'])
    warnings = []
    if receipt['warning']:
        warnings.append(receipt['warning'])
        log.warning('CALENDAR: %s', receipt['warning'])
    state = workflow.prepare(config)
    mismatch = recent_mismatch_warning(pd.read_csv(config.output / 'spx_reconciliation.csv'), started)
    if mismatch:
        warnings.append(mismatch)
        log.warning('SPX RECONCILIATION: %s', mismatch)
    log.info('3/4 data loaded; sources pending: %d', int((state['issues'].status == 'pending').sum()))
    state['selected'] = load_selected(config.output)
    issued_at = now if now is not None else pd.Timestamp.now(tz='UTC')
    records, statuses = workflow.forecast_once(config, state, issued_at)
    append_history(config.output / 'forecast_status_history.csv', statuses)
    daily_path, daily = write_daily_forecast(config.output, pd.read_csv(config.output / 'forecasts.csv'), statuses, issued_at)
    log.info('daily forecast CSV: %s (%d row(s), %d issued)', daily_path, len(daily), int(daily.forecast_status.eq('issued').sum()))
    for row in statuses.itertuples():
        log.info('4/4 %s: %s | %s', row.horizon, row.timing_status, row.status)
    return exit_code(statuses, refresh_results, warnings)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--skip-refresh', action='store_true', help='Do not download prices (use files in data/imports)')
    parser.add_argument('--now', help='ISO timestamp with timezone, for backfills and tests; default is the current time')
    args = parser.parse_args()
    Path('.runtime').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler('.runtime/live_forecast.log')])
    now = pd.Timestamp(args.now) if args.now else None
    if now is not None and now.tzinfo is None:
        parser.error('--now needs a timezone, e.g. 2026-11-09T00:46:00Z')
    try:
        code = run(now, args.skip_refresh)
    except Exception:
        log.error('live forecast failed:\n%s', traceback.format_exc())
        code = ERROR
    log.info('exit code %d', code)
    sys.exit(code)


if __name__ == '__main__':
    main()
