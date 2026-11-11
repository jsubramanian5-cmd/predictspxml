"""Portable notebook figures; pending panels never contain simulated observations."""
import matplotlib.pyplot as plt
import json
import numpy as np
import pandas as pd
from .backtest import model_key

BLUE, ORANGE = "#2864a0", "#c47b20"


def label(model):
    config = json.loads(model)
    family = config['family']
    if family == 'linear':
        return f"Linear α={config['alpha']}"
    if family == 'xgb':
        return f"XGB depth={config['max_depth']}, trees={config['n_estimators']}, L2={config['reg_lambda']}"
    return {'empirical': 'Historical quantiles', 'scaled': 'Volatility-scaled quantiles',
            'garch': 'GARCH(1,1)-scaled quantiles', 'har': 'HAR-RV-scaled quantiles'}[family]


def style():
    plt.rcParams.update({"figure.figsize": (10, 4), "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.alpha": 0.2, "figure.dpi": 110})


def pending(ax, message):
    ax.text(0.5, 0.5, message, ha="center", va="center", wrap=True, transform=ax.transAxes)
    ax.set_xticks([])
    ax.set_yticks([])


def coverage(datasets, calendar=None):
    fig, axes = plt.subplots(3, 1, figsize=(10, 6), constrained_layout=True)
    for ax, name in zip(axes, ("spx", "vix", "vix9d")):
        frame = datasets.get(name)
        ax.set_title(f"{name.upper()} source coverage and gaps")
        if frame is None or frame.empty:
            pending(ax, "Pending: source download unavailable; coverage unknown")
            continue
        valid = frame.loc[frame.value.gt(0)]
        ax.scatter(valid.date, np.ones(len(valid)), s=3, color=BLUE, label="Observed positive level")
        if calendar is not None:
            expected = calendar.sessions[(calendar.sessions >= frame.date.min()) & (calendar.sessions <= frame.date.max())]
            missing = expected.difference(pd.DatetimeIndex(valid.date))
            ax.scatter(missing, np.zeros(len(missing)), marker="x", color=ORANGE, label="Missing regular session")
        ax.set_yticks([0, 1], ["Missing", "Observed"])
        ax.set_xlabel("Session date")
        ax.legend(loc="upper left", fontsize=9)
    return fig


def outcomes(predictions, horizon, model=None):
    fig, ax = plt.subplots(layout="constrained")
    data = predictions.loc[predictions.horizon.eq(horizon)] if "horizon" in predictions else pd.DataFrame()
    if model and len(data):
        data = data.loc[data.model.eq(model)]
    ax.set_title(f"{horizon.title()} official settlements and 80% forecast intervals")
    ax.set_xlabel("Expiration date")
    ax.set_ylabel("SPX index points")
    if data.empty:
        pending(ax, "Pending: verified history / completed backtest required")
    else:
        data = data.sort_values("expiration")
        ax.fill_between(data.expiration, data.lower, data.upper, alpha=0.2, color=BLUE, label="10th–90th percentiles")
        ax.plot(data.expiration, data["median"], color=BLUE, lw=1, label="Median forecast")
        ax.scatter(data.expiration, data.actual_level, color=ORANGE, s=12, label="Official settlement")
        ax.legend(fontsize=9)
    return fig


def actual_vs_prediction(predictions, frozen, stage):
    """Compare out-of-sample median predictions with matched actual settlements."""
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), constrained_layout=True)
    stage_name = 'Development' if stage == 'development' else 'Final test'
    for ax, horizon in zip(axes, ('weekly', 'monthly')):
        data = predictions.loc[predictions.horizon.eq(horizon) & predictions.stage.eq(stage)] if len(predictions) else pd.DataFrame()
        ax.set_xlabel('Expiration date')
        ax.set_ylabel('SPX index points')
        if data.empty or horizon not in frozen:
            ax.set_title(f'{horizon.title()} {stage_name.lower()}: actual vs predicted settlement')
            pending(ax, 'Pending: completed evaluation required')
            continue
        if data.groupby('expiration').actual_level.nunique().gt(1).any():
            raise ValueError('Models disagree about actual settlement values')
        actual = data.drop_duplicates('expiration').sort_values('expiration')
        first, last = actual.expiration.min(), actual.expiration.max()
        ax.set_title(f'{horizon.title()} {stage_name.lower()}: actual vs predicted settlement '
                     f'({first.year}-{last.year}; n={len(actual)})')
        ax.plot(actual.expiration, actual.actual_level, color='#222222', marker='o', ms=3,
                lw=1.5, label='Actual settlement', zorder=3)
        for kind, color, linestyle in [('benchmark', BLUE, '--'), ('ml', ORANGE, ':')]:
            key = model_key(frozen[horizon][kind])
            selected = data.loc[data.model.eq(key)].sort_values('expiration')
            if not selected.empty:
                name = 'Benchmark' if kind == 'benchmark' else 'ML'
                ax.plot(selected.expiration, selected['median'], color=color, linestyle=linestyle,
                        lw=1.5, label=f'{name} median: {label(key)}')
        # Keep the first and final scored expiration visible alongside calendar ticks.
        middle = pd.date_range(first + pd.DateOffset(months=3), last - pd.DateOffset(months=3), freq='6MS')
        ticks = sorted({first, last, *middle})
        ax.set_xticks(ticks, [day.strftime('%b %Y') for day in ticks])
        ax.legend(loc='upper left', fontsize=9)
    fig.suptitle(f'{stage_name}: out-of-sample actual vs prediction', fontsize=14)
    return fig


def comparisons(metrics):
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
    axes[0].set_title("Mean 80% interval score (lower is better)")
    axes[0].set_xlabel("Log-return basis points")
    axes[1].set_title("Actual coverage with 95% Wilson intervals")
    axes[1].set_xlabel("Coverage fraction; target = 0.80")
    if metrics.empty:
        for ax in axes:
            pending(ax, "Pending: no verified backtest metrics")
        return fig
    data = metrics.loc[metrics.regime.eq("all")].copy()
    data["label"] = [f"{r.horizon}: {label(r.model)}" for r in data.itertuples()]
    y = np.arange(len(data))
    axes[0].barh(y, data.interval_score_bps, color=BLUE)
    axes[1].errorbar(data.coverage, y,
                     xerr=[data.coverage - data.coverage_wilson_lower, data.coverage_wilson_upper - data.coverage],
                     fmt="o", color=BLUE, capsize=3)
    axes[1].axvline(0.8, ls="--", color="#333333")
    axes[1].set_xlim(0, 1)
    for ax in axes:
        ax.set_yticks(y, data.label, fontsize=9)
    return fig


def widths(predictions):
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), constrained_layout=True)
    for ax, horizon in zip(axes, ("weekly", "monthly")):
        ax.set_title(f"{horizon.title()} 80% interval widths")
        ax.set_xlabel("Expiration date")
        ax.set_ylabel("SPX index points")
        data = predictions.loc[predictions.horizon.eq(horizon)] if "horizon" in predictions else pd.DataFrame()
        if data.empty:
            pending(ax, "Pending: no supported interval widths")
        else:
            for i, (model, group) in enumerate(data.groupby("model")):
                ax.plot(group.expiration, group.upper - group.lower, label=label(model), lw=1)
            ax.legend(fontsize=9)
    return fig


def yearly_actual_vs_prediction(predictions, frozen, horizon, years, extension_years=()):
    """One panel per calendar year: actual settlements against frozen benchmark and ML medians."""
    fig, axes = plt.subplots(len(years), 1, figsize=(12, 3.8 * len(years)), constrained_layout=True)
    axes = np.atleast_1d(axes)
    data = predictions.loc[predictions.horizon.eq(horizon)] if len(predictions) and horizon in frozen else pd.DataFrame()
    for ax, year in zip(axes, years):
        year_data = data.loc[data.expiration.dt.year.eq(year)] if len(data) else data
        note = ' (post-holdout extension, frozen models)' if year in extension_years else ''
        ax.set_ylabel('SPX index points')
        if year_data.empty:
            ax.set_title(f'{horizon.title()} {year}: actual vs predicted settlement{note}')
            pending(ax, 'Pending: no scored expirations')
            continue
        actual = year_data.drop_duplicates('expiration').sort_values('expiration')
        ax.set_title(f'{horizon.title()} {year}: actual vs predicted settlement{note}; n={len(actual)}')
        ax.plot(actual.expiration, actual.actual_level, color='#222222', marker='o', ms=3, lw=1.5,
                label='Actual settlement', zorder=3)
        for kind, color, linestyle in [('benchmark', BLUE, '--'), ('ml', ORANGE, ':')]:
            selected = year_data.loc[year_data.model.eq(model_key(frozen[horizon][kind]))].sort_values('expiration')
            if not selected.empty:
                name = 'Benchmark' if kind == 'benchmark' else 'ML'
                ax.plot(selected.expiration, selected['median'], color=color, linestyle=linestyle,
                        lw=1.5, label=f'{name} median: {label(selected.model.iat[0])}')
        ax.legend(loc='upper left', fontsize=9)
    fig.suptitle(f'{horizon.title()} out-of-sample actual vs prediction by year', fontsize=14)
    return fig


def _hover_panel(fig, row, data, finalists, prefix="", legend=True):
    """Actual settlements and both finalists' medians in one subplot; hovering a date lists all three values."""
    import plotly.graph_objects as go
    if data.groupby('expiration').actual_level.nunique().gt(1).any():
        raise ValueError('Models disagree about actual settlement values')
    actual = data.drop_duplicates('expiration').sort_values('expiration')
    fig.add_trace(go.Scatter(x=actual.expiration, y=actual.actual_level, name=f'{prefix}Actual settlement',
                             mode='lines+markers', line=dict(color='#222222', width=1.5), marker=dict(size=4),
                             legendgroup='actual', showlegend=legend, hovertemplate='%{y:,.2f}'), row=row, col=1)
    for kind, color, dash in [('benchmark', BLUE, 'dash'), ('ml', ORANGE, 'dot')]:
        key = model_key(finalists[kind])
        selected = data.loc[data.model.eq(key)].sort_values('expiration')
        if selected.empty:
            continue
        name = 'Benchmark' if kind == 'benchmark' else 'ML'
        fig.add_trace(go.Scatter(x=selected.expiration, y=selected['median'], name=f'{prefix}{name} median: {label(key)}',
                                 mode='lines', line=dict(color=color, width=1.5, dash=dash),
                                 legendgroup=kind, showlegend=legend,
                                 # The miss against the settled level is what the eye cannot read off the lines.
                                 customdata=selected['median'] - selected.actual_level,
                                 hovertemplate='%{y:,.2f} (%{customdata:+,.2f} vs actual)'), row=row, col=1)


def _hover_figure(titles, heading, row_height):
    from plotly.subplots import make_subplots
    fig = make_subplots(rows=len(titles), cols=1, subplot_titles=titles, vertical_spacing=min(0.12, 0.3 / len(titles)))
    fig.update_layout(title=heading, hovermode='x unified', template='simple_white', height=row_height * len(titles) + 120,
                      legend=dict(orientation='h', yanchor='bottom', y=1.02, x=0))
    fig.update_yaxes(title_text='SPX index points')
    return fig


def _hover_pending(fig, row, message):
    axis = '' if row == 1 else str(row)
    fig.add_annotation(text=message, showarrow=False, xref=f'x{axis} domain', yref=f'y{axis} domain', x=0.5, y=0.5)


def interactive_actual_vs_prediction(predictions, frozen, stage):
    """Plotly version of `actual_vs_prediction`: hover a date for the settlement and each finalist's median."""
    stage_name = 'Development' if stage == 'development' else 'Final test'
    panels = []
    for horizon in ('weekly', 'monthly'):
        data = predictions.loc[predictions.horizon.eq(horizon) & predictions.stage.eq(stage)] if len(predictions) else pd.DataFrame()
        panels.append((horizon, data if horizon in frozen else pd.DataFrame()))
    titles = [f'{h.title()} {stage_name.lower()}: actual vs predicted settlement'
              + (f' (n={d.expiration.nunique()})' if len(d) else '') for h, d in panels]
    fig = _hover_figure(titles, f'{stage_name}: out-of-sample actual vs prediction', 330)
    for row, (horizon, data) in enumerate(panels, 1):
        if data.empty:
            _hover_pending(fig, row, 'Pending: completed evaluation required')
        else:
            # The two horizons have different finalists, so each legend entry names its horizon.
            _hover_panel(fig, row, data, frozen[horizon], prefix=f'{horizon.title()} · ')
    return fig


def interactive_yearly_actual_vs_prediction(predictions, frozen, horizon, years, extension_years=()):
    """Plotly version of `yearly_actual_vs_prediction`: one hoverable panel per calendar year."""
    data = predictions.loc[predictions.horizon.eq(horizon)] if len(predictions) and horizon in frozen else pd.DataFrame()
    panels = [(year, data.loc[data.expiration.dt.year.eq(year)] if len(data) else data) for year in years]
    titles = [f'{horizon.title()} {year}: actual vs predicted settlement'
              + (' (post-holdout extension, frozen models)' if year in extension_years else '')
              + (f'; n={d.expiration.nunique()}' if len(d) else '') for year, d in panels]
    fig = _hover_figure(titles, f'{horizon.title()} out-of-sample actual vs prediction by year', 300)
    legend = True
    for row, (year, year_data) in enumerate(panels, 1):
        if year_data.empty:
            _hover_pending(fig, row, 'Pending: no scored expirations')
        else:
            _hover_panel(fig, row, year_data, frozen[horizon], legend=legend)
            legend = False  # the same three series repeat in every year
    return fig


def interactive_outcomes(predictions, horizon, model=None):
    """Plotly version of `outcomes`: hovering a date shows the 10th/90th percentile levels, median and actual."""
    import plotly.graph_objects as go
    data = predictions.loc[predictions.horizon.eq(horizon)] if len(predictions) and "horizon" in predictions else pd.DataFrame()
    if model and len(data):
        data = data.loc[data.model.eq(model)]
    fig = go.Figure(layout=dict(title=f"{horizon.title()} official settlements and 80% forecast intervals",
                                xaxis_title="Expiration date", yaxis_title="SPX index points",
                                hovermode="x unified", template="simple_white"))
    if data.empty:
        fig.add_annotation(text="Pending: verified history / completed backtest required", showarrow=False,
                           xref="paper", yref="paper", x=0.5, y=0.5)
        return fig
    data = data.sort_values("expiration")
    fig.add_trace(go.Scatter(x=data.expiration, y=data.lower, name="10th percentile", mode="lines",
                             line=dict(width=0), hovertemplate="%{y:,.2f}"))
    fig.add_trace(go.Scatter(x=data.expiration, y=data.upper, name="90th percentile", mode="lines",
                             line=dict(width=0), fill="tonexty", fillcolor="rgba(40,100,160,0.2)",
                             hovertemplate="%{y:,.2f}"))
    fig.add_trace(go.Scatter(x=data.expiration, y=data["median"], name="Median forecast", mode="lines",
                             line=dict(color=BLUE, width=1), hovertemplate="%{y:,.2f}"))
    fig.add_trace(go.Scatter(x=data.expiration, y=data.actual_level, name="Official settlement", mode="markers",
                             marker=dict(color=ORANGE, size=6), hovertemplate="%{y:,.2f}"))
    return fig
