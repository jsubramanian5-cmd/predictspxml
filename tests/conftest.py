"""Synthetic fixtures used only in tests, never notebook market outputs."""
import numpy as np
import pandas as pd
import pytest

from spx_forecast.calendar import OfficialCalendar
from spx_forecast.features import FEATURES


@pytest.fixture
def calendar():
    dates = pd.date_range('2023-01-01', '2025-12-31')
    frame = pd.DataFrame({'date': dates, 'is_session': dates.weekday < 5,
                          'source_url': 'https://www.cboe.com/about/hours/us-options/'})
    # Explicit test closure to exercise a Friday holiday shift.
    frame.loc[frame.date.eq('2024-03-29'), 'is_session'] = False
    return OfficialCalendar(frame)


@pytest.fixture
def datasets(calendar):
    dates = calendar.sessions
    data = {}
    for name, base in [('spx', 4000), ('vix', 20), ('vix9d', 18)]:
        data[name] = pd.DataFrame({'date': dates, 'value': base + np.arange(len(dates)) * 0.01,
                                   'published_at': pd.NaT, 'retrieved_at': '2024-04-01T10:00:00Z',
                                   'source_url': 'https://www.cboe.com/', 'sha256': 'test-only'})
    return data


@pytest.fixture
def train_frame():
    rng = np.random.default_rng(42)
    frame = pd.DataFrame(rng.normal(size=(40, len(FEATURES))), columns=FEATURES)
    frame['target'] = np.linspace(-200, 300, 40)
    frame['scale'] = np.linspace(0.01, 0.03, 40)
    return frame
