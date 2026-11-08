"""Ensure actual/prediction charts keep stages and forecast families aligned."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import pytest

from spx_forecast.backtest import model_key
from spx_forecast.plots import actual_vs_prediction


def test_actual_prediction_plot_separates_stages_and_shows_both_finalists():
    benchmark, ml = dict(family='scaled'), dict(family='linear', alpha=0.1)
    frozen = {'weekly': dict(benchmark=benchmark, ml=ml)}
    rows = []
    for stage, date, actual in [('development', '2023-12-22', 4800),
                                 ('final_test', '2024-01-05', 4900)]:
        for model, prediction in [(benchmark, actual-30), (ml, actual+20)]:
            rows.append(dict(horizon='weekly', stage=stage, expiration=pd.Timestamp(date),
                             model=model_key(model), actual_level=actual, median=prediction))
    frame = pd.DataFrame(rows)
    figure = actual_vs_prediction(frame, frozen, 'final_test')
    try:
        lines = figure.axes[0].lines
        assert len(lines) == 3
        assert list(lines[0].get_ydata()) == [4900]
        assert list(lines[1].get_ydata()) == [4870]
        assert list(lines[2].get_ydata()) == [4920]
        assert lines[0].get_label() == 'Actual settlement'
        assert 'Benchmark median' in lines[1].get_label()
        assert 'ML median' in lines[2].get_label()
    finally:
        plt.close(figure)
    frame.loc[frame.model.eq(model_key(ml)) & frame.stage.eq('final_test'), 'actual_level'] = 9999
    with pytest.raises(ValueError, match='Models disagree'):
        actual_vs_prediction(frame, frozen, 'final_test')
    plt.close('all')
