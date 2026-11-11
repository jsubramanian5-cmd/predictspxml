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


def _frame(frozen):
    rows = []
    for stage, date, actual in [('development', '2023-12-22', 4800), ('final_test', '2024-01-05', 4900),
                                 ('final_test', '2025-01-03', 5900)]:
        for model, prediction in [(frozen['weekly']['benchmark'], actual-30), (frozen['weekly']['ml'], actual+20)]:
            rows.append(dict(horizon='weekly', stage=stage, expiration=pd.Timestamp(date),
                             model=model_key(model), actual_level=actual, median=prediction))
    return pd.DataFrame(rows)


def test_interactive_actual_prediction_charts_carry_the_values_shown_on_hover():
    pytest.importorskip('plotly')
    from spx_forecast.plots import interactive_actual_vs_prediction, interactive_yearly_actual_vs_prediction
    frozen = {'weekly': dict(benchmark=dict(family='scaled'), ml=dict(family='har'))}
    frame = _frame(frozen)
    figure = interactive_actual_vs_prediction(frame, frozen, 'final_test')
    actual, benchmark, ml = figure.data  # weekly only: monthly has no finalists and shows a pending note
    assert list(actual.y) == [4900, 5900] and actual.name == 'Weekly · Actual settlement'
    assert list(benchmark.y) == [4870, 5870] and list(benchmark.customdata) == [-30, -30]
    assert list(ml.y) == [4920, 5920] and list(ml.customdata) == [20, 20]
    assert 'Benchmark median' in benchmark.name and 'HAR-RV' in ml.name and 'vs actual' in ml.hovertemplate
    assert figure.layout.hovermode == 'x unified'
    assert any('Pending' in note.text for note in figure.layout.annotations)
    # By year: one panel per year, legend entries shown once, an empty year marked pending.
    yearly = interactive_yearly_actual_vs_prediction(frame.loc[frame.stage.eq('final_test')], frozen, 'weekly',
                                                     (2024, 2025, 2026), (2026,))
    assert len(yearly.data) == 6 and [trace.showlegend for trace in yearly.data] == [True] * 3 + [False] * 3
    assert {trace.yaxis for trace in yearly.data} == {'y', 'y2'}
    titles = [note.text for note in yearly.layout.annotations]
    assert any('2026' in t and 'post-holdout extension' in t for t in titles) and 'Pending: no scored expirations' in titles
    frame.loc[frame.model.eq(model_key(frozen['weekly']['ml'])), 'actual_level'] = 9999
    with pytest.raises(ValueError, match='Models disagree'):
        interactive_actual_vs_prediction(frame, frozen, 'final_test')
