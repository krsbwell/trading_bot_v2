"""
Builds the main Plotly chart figure:
  - Candlestick OHLC with 3 EMA overlays
  - CCI subplot (shaded ±100 zones)
  - MACD histogram subplot
  - S/R zone horizontal bands
  - Open trade lines (entry / SL / TP1-3)
  - Pivot labels (HH, HL, LH, LL)
"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from engine.indicators import ema as calc_ema, atr, cci as calc_cci, macd_histogram
import config

# EMA colours
_EMA_COLORS = {
    "short": "#ffd700",   # yellow
    "mid":   "#ff9900",   # orange
    "long":  "#ff3366",   # red
}

# Dark theme colours
_BG    = "#0d1117"
_PANEL = "#161b22"
_BULL  = "#00ff88"
_BEAR  = "#ff3366"
_TEXT  = "#e6edf3"
_MUTED = "#7d8590"


def build_chart(
    df: pd.DataFrame,
    pair: str,
    tf: str         = "H1",
    ema_periods: tuple = (20, 50, 200),
    sr_zones: list     = None,
    open_trades: list  = None,
    pivots: dict       = None,
) -> go.Figure:
    """
    Build the full 3-row figure: [candlestick | CCI | MACD].

    df           : OHLCV DataFrame indexed by datetime
    pair         : label for the chart title
    ema_periods  : (short, mid, long)
    sr_zones     : list of zone dicts from strategy_market_structure
    open_trades  : list of trade dicts from state (for line annotations)
    pivots       : dict from detect_pivots (for HH/HL labels)
    """
    if df is None or df.empty:
        return _empty_figure(f"No data for {pair}", pair, tf)

    sr_zones    = sr_zones    or []
    open_trades = open_trades or []
    pivots      = pivots      or {"highs": [], "lows": []}

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.60, 0.20, 0.20],
        vertical_spacing=0.005,
    )

    # ── Candlestick ───────────────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"],
        low=df["low"], close=df["close"],
        increasing_line_color=_BULL, decreasing_line_color=_BEAR,
        increasing_fillcolor=_BULL, decreasing_fillcolor=_BEAR,
        name="OHLC", showlegend=False,
    ), row=1, col=1)

    # ── EMA lines ─────────────────────────────────────────────────────────────
    labels = ("short", "mid", "long")
    for period, label in zip(ema_periods, labels):
        if period and period < len(df):
            ema_vals = calc_ema(df["close"], period)
            fig.add_trace(go.Scatter(
                x=df.index, y=ema_vals,
                mode="lines", line=dict(color=_EMA_COLORS[label], width=1.5),
                name=f"EMA {period}", legendgroup="ema",
                showlegend=True,
            ), row=1, col=1)

    # ── S/R zone bands ────────────────────────────────────────────────────────
    for zone in sr_zones:
        color = "rgba(0,255,136,0.06)" if zone["type"] == "support" else "rgba(255,51,102,0.06)"
        fig.add_hrect(
            y0=zone["lower"], y1=zone["upper"],
            fillcolor=color, line_width=0,
            row=1, col=1,
        )

    # ── Open trade lines ──────────────────────────────────────────────────────
    for trade in open_trades:
        entry = trade.get("entry")
        sl    = trade.get("sl")
        tp1   = trade.get("tp1")
        tp2   = trade.get("tp2")
        tp3   = trade.get("tp3")
        x0, x1 = df.index[0], df.index[-1]

        def _hline(y, color, dash, label):
            fig.add_shape(type="line", x0=x0, x1=x1, y0=y, y1=y,
                          line=dict(color=color, dash=dash, width=1),
                          row=1, col=1)
            fig.add_annotation(x=x1, y=y, text=label, xanchor="left",
                                font=dict(color=color, size=10),
                                showarrow=False, row=1, col=1)

        if entry: _hline(entry, "#ffffff", "dash",   "Entry")
        if sl:    _hline(sl,    _BEAR,     "dot",    "SL")
        if tp1:   _hline(tp1,   _BULL,     "dashdot","TP1")
        if tp2:   _hline(tp2,   _BULL,     "dashdot","TP2")
        if tp3:   _hline(tp3,   _BULL,     "dashdot","TP3")

    # ── CCI subplot ───────────────────────────────────────────────────────────
    if len(df) > config.CCI_PERIOD:
        cci_vals = calc_cci(df["high"], df["low"], df["close"], config.CCI_PERIOD)
        cci_color = [_BULL if v >= 0 else _BEAR for v in cci_vals.fillna(0)]
        fig.add_trace(go.Bar(
            x=df.index, y=cci_vals,
            marker_color=cci_color, name="CCI", showlegend=False,
        ), row=2, col=1)
        for level, dash in [(100, "dot"), (-100, "dot")]:
            fig.add_hline(y=level, line=dict(color=_MUTED, dash=dash, width=0.8), row=2, col=1)
        fig.add_hline(y=0, line=dict(color=_MUTED, width=0.6), row=2, col=1)

    # ── MACD histogram ────────────────────────────────────────────────────────
    if len(df) > config.MACD_SLOW:
        hist = macd_histogram(df["close"], config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
        hist_color = [_BULL if v >= 0 else _BEAR for v in hist.fillna(0)]
        fig.add_trace(go.Bar(
            x=df.index, y=hist,
            marker_color=hist_color, name="MACD Hist", showlegend=False,
        ), row=3, col=1)
        fig.add_hline(y=0, line=dict(color=_MUTED, width=0.6), row=3, col=1)

    # ── Watermark ─────────────────────────────────────────────────────────────
    tf_label = {"H1": "1H", "H4": "4H", "D": "D"}.get(tf, tf)
    fig.add_annotation(
        text=f"{pair}  {tf_label}",
        x=df.index[len(df) // 2],
        y=(df["high"].max() + df["low"].min()) / 2,
        xref="x", yref="y",
        showarrow=False,
        font=dict(size=38, color="rgba(230,237,243,0.05)"),
    )

    # ── Layout ────────────────────────────────────────────────────────────────
    fig.update_layout(
        paper_bgcolor=_BG,
        plot_bgcolor=_PANEL,
        font=dict(color=_TEXT, family="IBM Plex Sans"),
        xaxis_rangeslider_visible=False,
        legend=dict(
            orientation="h",
            y=1.0, x=0,
            xanchor="left", yanchor="bottom",
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=10, color=_TEXT),
            itemclick=False, itemdoubleclick=False,
        ),
        margin=dict(l=8, r=64, t=52, b=20),
        hovermode="x",
        hoversubplots="axis",
        dragmode="pan",
        uirevision=f"{pair}_{tf}",
        newshape=dict(
            line=dict(color="#ffd700", width=2),
            fillcolor="rgba(255,215,0,0.08)",
        ),
    )
    for i in range(1, 4):
        fig.update_xaxes(
            gridcolor="#21262d", zeroline=False, showgrid=True,
            showspikes=True, spikemode="across", spikedash="solid",
            spikecolor="rgba(200,200,200,0.6)", spikethickness=1,
            spikesnap="cursor",
            row=i, col=1,
        )
        fig.update_yaxes(
            gridcolor="#21262d", zeroline=False, showgrid=True,
            side="right",
            showspikes=True, spikemode="across", spikedash="solid",
            spikecolor="rgba(200,200,200,0.6)", spikethickness=1,
            row=i, col=1,
        )

    return fig


def _empty_figure(msg: str, pair: str = "", tf: str = "") -> go.Figure:
    tf_label = {"H1": "1H", "H4": "4H", "D": "D"}.get(tf, tf)
    annotations = [dict(text=msg, x=0.5, y=0.5, xref="paper", yref="paper",
                        showarrow=False, font=dict(size=16, color=_MUTED))]
    if pair:
        annotations.append(dict(
            text=f"{pair}  {tf_label}", x=0.5, y=0.5,
            xref="paper", yref="paper", showarrow=False,
            font=dict(size=38, color="rgba(230,237,243,0.04)"),
        ))
    fig = go.Figure()
    fig.update_layout(
        paper_bgcolor=_BG, plot_bgcolor=_PANEL,
        font=dict(color=_TEXT),
        annotations=annotations,
        margin=dict(l=8, r=64, t=28, b=20),
        yaxis=dict(side="right"),
    )
    return fig
