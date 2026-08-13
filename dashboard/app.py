"""
Apex Trading Bot — Dashboard  http://localhost:8050
"""
import copy
import json
import logging
import os
import signal
import threading
from datetime import datetime as _dt

import pandas as pd
from dash import (
    Dash, Input, Output, State, ALL, ctx,
    callback_context, html, dcc, no_update,
)

import config
from dashboard import state
from dashboard.panels import (
    signal_monitor_panel, open_trades_panel, pending_orders_panel,
    account_stats_bar, trade_log_drawer, learning_panel,
)
from dashboard.chart_builder_twlc import build_chart_data

logger = logging.getLogger(__name__)

# ── Style helpers ─────────────────────────────────────────────────────────────

_TF_LABELS = {"M5": "5m", "M15": "15m", "M30": "30m", "H1": "1H", "H4": "4H", "D": "D", "W": "W"}
_ALL_TFS   = ["M5", "M15", "M30", "H1", "H4", "D", "W"]

def _tf_btn_style(active: bool = False) -> dict:
    return {
        "background":   "#1f6feb" if active else "#21262d",
        "color":        "#ffffff" if active else "#e6edf3",
        "border":       f"1px solid {'#388bfd' if active else '#30363d'}",
        "borderRadius": "3px", "padding": "0.3rem 0.6rem",
        "cursor": "pointer", "fontSize": "0.8rem",
        "fontWeight": "600" if active else "400",
    }

# ── Drawing constants ─────────────────────────────────────────────────────────

_DRAW_TOOL_META = [
    ("h-line",    "—",   "Horizontal line — click chart to place"),
    ("v-line",    "│",   "Vertical line — click chart to place"),
    ("trend",     "╱",   "Trend line — click 2 points; drag endpoints to edit"),
    ("pips",
     html.Img(
         src=(
             "data:image/svg+xml,%3Csvg xmlns%3D'http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg'"
             " viewBox%3D'0 0 16 16' width%3D'13' height%3D'13'%3E"
             "%3Ccircle cx%3D'3.5' cy%3D'3.5' r%3D'3' fill%3D'%238b949e'%2F%3E"
             "%3Ccircle cx%3D'12.5' cy%3D'12.5' r%3D'3' fill%3D'%238b949e'%2F%3E"
             "%3Cline x1%3D'6' y1%3D'6' x2%3D'10' y2%3D'10'"
             " stroke%3D'%238b949e' stroke-width%3D'1.5'%2F%3E%3C%2Fsvg%3E"
         ),
         style={"width": "13px", "height": "13px", "verticalAlign": "middle"},
     ),
     "Measure — click and drag to measure price range"),
    ("box",       "▭",   "Rectangle — click and drag to draw"),
    ("square",    "□",   "Square — click and drag (equal sides)"),
    ("fib",       "φ",   "Fibonacci retracement — drag from swing high to low"),
    ("circle",    "○",   "Circle — click center and drag to set radius"),
    ("long-pos",  "↑ L", "Long Position — click chart to place entry"),
    ("short-pos", "↓ S", "Short Position — click chart to place entry"),
    ("text",      "T",   "Text label — click to place text on chart"),
]
_DRAW_TOOL_MODES = [m for m, _, _ in _DRAW_TOOL_META]

_POS_BTN_COLORS = {
    # Each badge keeps its own dark chip background regardless of page theme,
    # so text stays vivid (bright-on-dark), not the light-theme text-safe variant.
    "long-pos":  ("#003d1f", "#00ff88"),
    "short-pos": ("#3d0010", "#ff3366"),
}

def _draw_btn_style(mode_name: str, active: bool = False) -> dict:
    if mode_name in _POS_BTN_COLORS:
        bg, fc = _POS_BTN_COLORS[mode_name]
        return {
            "background":   "#1f6feb" if active else bg,
            "color":        "#ffffff" if active else fc,
            "border":       f"1px solid {'#388bfd' if active else fc}",
            "borderRadius": "3px", "padding": "0.3rem 0.6rem",
            "cursor": "pointer", "fontSize": "0.8rem",
            "fontWeight": "600" if active else "400",
        }
    return _tf_btn_style(active)

# 9 preset colors + custom picker — a content palette (like the chart's own
# drawing-tool swatches), left vivid regardless of the dashboard chrome theme.
_DRAW_COLORS = [
    "#ffffff",   # White
    "#111111",   # Black
    "#ff3366",   # Red
    "#00ff88",   # Green
    "#38b6ff",   # Blue
    "#ffd700",   # Yellow
    "#ff9900",   # Orange
    "#cc44ff",   # Purple
    "#ff69b4",   # Pink
]
_DRAW_DEFAULT_COLOR = "#ffd700"

_DRAW_WIDTHS = [1, 2, 3]
_DRAW_WIDTH_LABELS = {1: "Thin", 2: "Medium", 3: "Thick"}

_DRAW_STYLES = ["solid", "dashed", "dotted"]
_DRAW_STYLE_LABELS = {"solid": "Solid", "dashed": "Dashed", "dotted": "Dotted"}

def _swatch_style(color: str, active: bool = False) -> dict:
    return {
        "width": "20px", "height": "20px",
        "borderRadius": "50%", "background": color,
        "border": f"2px solid {'#e6edf3' if active else 'rgba(48,54,61,0.5)'}",
        "cursor": "pointer", "padding": "0", "flexShrink": "0", "outline": "none",
        "boxShadow": "0 0 0 2px rgba(255,215,0,0.5)" if active else "none",
    }

def _width_btn_style(active: bool = False) -> dict:
    return {**_tf_btn_style(active),
            "fontFamily": "monospace", "padding": "0.2rem 0.45rem",
            "letterSpacing": "-1px", "fontSize": "0.9rem"}

def _style_btn_style(active: bool = False) -> dict:
    return {**_tf_btn_style(active), "fontFamily": "monospace",
            "padding": "0.2rem 0.55rem", "fontSize": "0.8rem"}

# ── EMA / Moving Average settings ─────────────────────────────────────────────

_EMA_DEFAULTS = [
    {"period": 20,  "color": "#ffd700", "visible": True, "width": 1},
    {"period": 50,  "color": "#ff9900", "visible": True, "width": 1},
    {"period": 200, "color": "#ff3366", "visible": True, "width": 1},
]

def _ema_defaults():
    return copy.deepcopy(_EMA_DEFAULTS)

def _vis_btn_style(visible: bool) -> dict:
    return {**_tf_btn_style(visible), "fontSize": "0.7rem", "padding": "0.15rem 0.35rem"}

def _build_ma_rows(settings):
    rows = []
    for i, s in enumerate(settings):
        rows.append(html.Div([
            html.Span(f"EMA {i+1}",
                      style={"color": s.get("color", "#ffd700"), "fontSize": "0.8rem",
                             "fontWeight": 700, "minWidth": "44px"}),
            html.Span("Per:", style={"color": "#8b949e", "fontSize": "0.75rem"}),
            dcc.Input(
                id={"type": "ema-period-input", "index": i},
                type="number", value=s.get("period", 34),
                min=2, max=500, step=1, debounce=True,
                style={"width": "58px", "background": "#21262d", "color": "#e6edf3",
                       "border": "1px solid #30363d", "borderRadius": "3px",
                       "padding": "0.2rem 0.35rem", "fontSize": "0.8rem"},
            ),
            # Line width
            *[html.Button(
                _DRAW_WIDTH_LABELS[w],
                id={"type": "ema-width-btn", "index": f"{i}-{w}"},
                n_clicks=0, title=f"Width {w}px",
                style=_width_btn_style(w == s.get("width", 1)),
              ) for w in _DRAW_WIDTHS],
            # Colour swatches
            *[html.Button(
                "",
                id={"type": "ema-color-btn", "index": f"{i}-{c[1:]}"},
                n_clicks=0, title=c,
                style=_swatch_style(c, c == s.get("color", "#ffd700")),
              ) for c in _DRAW_COLORS],
            # Visibility
            html.Button(
                "ON" if s.get("visible", True) else "OFF",
                id={"type": "ema-vis-btn", "index": i},
                n_clicks=0, title="Toggle visibility",
                style=_vis_btn_style(s.get("visible", True)),
            ),
        ], style={"display": "flex", "alignItems": "center", "gap": "0.3rem",
                  "flexWrap": "wrap"}))
    return rows


# ── Order-form helpers ────────────────────────────────────────────────────────

def _input_style():
    return {
        "width": "100%", "background": "#21262d", "color": "#e6edf3",
        "border": "1px solid #30363d", "borderRadius": "3px",
        "padding": "0.2rem 0.4rem", "fontSize": "0.82rem",
    }


def _order_field(label: str, field_id: str, color: str = "#e6edf3"):
    return html.Div([
        html.Span(label, style={"color": color, "fontSize": "0.72rem",
                                 "display": "block", "marginBottom": "2px"}),
        dcc.Input(id=field_id, type="number", debounce=True,
                  style=_input_style()),
    ])


# ── App instance ──────────────────────────────────────────────────────────────

app = Dash(
    __name__,
    suppress_callback_exceptions=True,
    title="Apex",
    assets_folder="assets",
)

# ── Layout helpers ────────────────────────────────────────────────────────────

def _fmt_pair(p: str) -> str:
    """Display EUR_USD as EUR/USD."""
    return p.replace("_", "/")


def _traded_pair_options():
    """Both the main chart's pair-select and the Backtest/Walk-Forward
    dropdown, scoped to config.FOREX_PAIRS only — the pairs actually placing
    real orders. FOREX_WATCH pairs (signals shown, no trades) are no longer
    selectable in either dropdown as of 2026-08-13, at user request — audited
    both dcc.Dropdown instances in this file (only these two exist) rather
    than fixing one and leaving the other stale. Watch pairs still surface
    on their own in the Signal Monitor panel (state["signals"], populated by
    main.py's own pair loop, not this list) and in the bulk WFO-refit job
    (dashboard/app.py's ml-wfo-btn handler) — both intentionally still cover
    the full roster, unrelated to pair-selection UI."""
    return [{"label": _fmt_pair(p), "value": p} for p in config.FOREX_PAIRS]

def _sep():
    return html.Div(style={"width": "1px", "height": "22px",
                            "background": "#30363d", "alignSelf": "center"})


# ── Layout ────────────────────────────────────────────────────────────────────

app.layout = html.Div(
    id="root",
    style={"backgroundColor": "#0f0f0f", "minHeight": "100vh",
           "fontFamily": "'IBM Plex Sans', sans-serif", "color": "#e6edf3"},
    children=[

        # ── Header ────────────────────────────────────────────────────────────
        html.Div([
            html.Span("Apex", style={"fontWeight": 700, "fontSize": "1.1rem",
                                     "color": "#e6edf3", "letterSpacing": "0.05em"}),
            html.Span(f"  {config.MODE.upper()} MODE", id="mode-badge",
                      style={"color": "#ffd700" if config.MODE == "paper" else "#00ff88",
                             "fontSize": "0.75rem", "fontWeight": 700,
                             "border": "1px solid", "borderRadius": "3px",
                             "padding": "1px 8px", "marginLeft": "0.75rem"}),
            # Connector health pills — updated every 5 s
            html.Div(id="connector-status",
                     style={"display": "flex", "gap": "0.4rem", "marginLeft": "0.75rem",
                            "alignItems": "center"}),
            html.Div(id="halted-banner",
                     style={"display": "none", "color": "#ff3366", "fontWeight": 700,
                            "marginLeft": "1rem", "border": "1px solid #ff3366",
                            "borderRadius": "3px", "padding": "2px 10px"},
                     children="⛔ TRADING HALTED — 3% drawdown breached"),
            html.Div(style={"marginLeft": "auto", "display": "flex", "gap": "0.5rem",
                             "alignItems": "center"},
                     children=[
                html.Button("▶ Scan Now", id="scan-now-btn", n_clicks=0,
                            title="Run signal scan immediately (don't wait for the hour)",
                            style={"background": "#003d1f", "color": "#00ff88",
                                   "border": "1px solid #00ff88", "borderRadius": "4px",
                                   "padding": "0.35rem 0.75rem", "cursor": "pointer",
                                   "fontSize": "0.82rem", "fontWeight": 700}),
                html.Span(id="scan-status", children="",
                          style={"color": "#8b949e", "fontSize": "0.75rem"}),
                html.Button("Backtest", id="backtest-btn", n_clicks=0,
                            style={"background": "#21262d", "color": "#38b6ff",
                                   "border": "1px solid #30363d", "borderRadius": "4px",
                                   "padding": "0.35rem 0.75rem", "cursor": "pointer"}),
                html.Button("Signals", id="sig-quality-btn", n_clicks=0,
                            style={"background": "#21262d", "color": "#ffd700",
                                   "border": "1px solid #30363d", "borderRadius": "4px",
                                   "padding": "0.35rem 0.75rem", "cursor": "pointer"}),
                html.Button("Learning", id="learning-btn", n_clicks=0,
                            style={"background": "#21262d", "color": "#00ff88",
                                   "border": "1px solid #30363d", "borderRadius": "4px",
                                   "padding": "0.35rem 0.75rem", "cursor": "pointer"}),
                html.Button("Trade Log", id="drawer-toggle-btn",
                            style={"background": "#21262d", "color": "#e6edf3",
                                   "border": "1px solid #30363d", "borderRadius": "4px",
                                   "padding": "0.35rem 0.75rem", "cursor": "pointer"}),
            ]),
        ], style={"display": "flex", "alignItems": "center", "padding": "0.75rem 1rem",
                  "borderBottom": "1px solid #21262d", "background": "#161b22"}),

        # ── Main grid ─────────────────────────────────────────────────────────
        html.Div([
            # Left (25%)
            html.Div([
                # ── Adjustable signal threshold — compact control row ─────
                html.Div([
                    html.Div([
                        html.Span("Min Score",
                                  style={"color": "#8b949e", "fontSize": "0.7rem",
                                         "marginRight": "6px"}),
                        html.Button("▼", id="min-score-dec", n_clicks=0,
                                    title="Decrease by 1",
                                    style={"background": "#21262d", "color": "#e6edf3",
                                           "border": "1px solid #30363d", "borderRadius": "3px",
                                           "padding": "1px 6px", "cursor": "pointer",
                                           "fontSize": "0.7rem", "lineHeight": "1.4",
                                           "marginRight": "2px"}),
                        dcc.Input(
                            id="min-score-input",
                            type="number", min=1, max=100,
                            value=config.MIN_CONFLUENCE_SCORE,
                            debounce=True,
                            style={"width": "46px", "background": "#0f0f0f",
                                   "color": "#ffd700", "fontWeight": 700,
                                   "border": "1px solid #30363d", "borderRadius": "3px",
                                   "textAlign": "center", "fontSize": "0.8rem",
                                   "padding": "2px 4px"},
                        ),
                        html.Button("▲", id="min-score-inc", n_clicks=0,
                                    title="Increase by 1",
                                    style={"background": "#21262d", "color": "#e6edf3",
                                           "border": "1px solid #30363d", "borderRadius": "3px",
                                           "padding": "1px 6px", "cursor": "pointer",
                                           "fontSize": "0.7rem", "lineHeight": "1.4",
                                           "marginLeft": "2px", "marginRight": "6px"}),
                        html.Span(id="min-score-label",
                                  style={"color": "#8b949e", "fontSize": "0.68rem"}),
                    ], style={"display": "flex", "alignItems": "center",
                              "gap": "0px"}),
                    # Hidden slider kept for backward compat with existing callbacks
                    html.Div(dcc.Slider(
                        id="min-score-slider",
                        min=1, max=100, step=1,
                        value=config.MIN_CONFLUENCE_SCORE,
                        marks={}, tooltip={"always_visible": False},
                        className="apex-slider",
                    ), style={"display": "none"}),
                ], style={"background": "#161b22", "borderRadius": "4px",
                           "padding": "0.5rem 0.75rem", "marginBottom": "0.5rem"}),
                html.Div(id="signal-monitor"),
                html.Div(id="open-trades"),
                html.Div(id="pending-trades"),
                html.Div(id="news-panel", style={"marginTop": "0.5rem"}),

                # ── Edit Trade panel (hidden until EDIT clicked) ───────────
                html.Div(
                    id="edit-trade-panel",
                    style={"display": "none", "background": "#161b22",
                           "borderRadius": "4px", "padding": "0.75rem",
                           "marginTop": "0.5rem", "border": "1px solid #ffd700"},
                    children=[
                        html.Div([
                            html.Span(id="edit-trade-title",
                                      style={"color": "#ffd700", "fontWeight": 700,
                                             "fontSize": "0.9rem"}),
                            html.Button("×", id="edit-cancel-btn", n_clicks=0,
                                        style={"background": "transparent", "border": "none",
                                               "color": "#8b949e", "cursor": "pointer",
                                               "fontSize": "1.1rem", "marginLeft": "auto",
                                               "padding": "0"}),
                        ], style={"display": "flex", "alignItems": "center",
                                  "marginBottom": "0.5rem"}),
                        html.Div([
                            _order_field("Stop Loss", "edit-sl-input",   "#ff3366"),
                            _order_field("TP 1",      "edit-tp1-input",  "#00ff88"),
                            _order_field("TP 2",      "edit-tp2-input",  "#00ff88"),
                            _order_field("TP 3",      "edit-tp3-input",  "#00ff88"),
                        ], style={"display": "grid",
                                  "gridTemplateColumns": "repeat(2, 1fr)",
                                  "gap": "0.4rem", "marginBottom": "0.5rem"}),
                        html.Button("↑ Move SL to Breakeven",
                                    id="edit-breakeven-btn", n_clicks=0,
                                    style={"width": "100%", "background": "#21262d",
                                           "color": "#ffd700",
                                           "border": "1px solid #ffd700",
                                           "borderRadius": "3px", "padding": "0.3rem",
                                           "cursor": "pointer", "fontSize": "0.78rem",
                                           "marginBottom": "0.4rem"}),
                        html.Button("✓ Update Trade",
                                    id="edit-confirm-btn", n_clicks=0,
                                    style={"width": "100%", "background": "#003d1f",
                                           "color": "#00ff88",
                                           "border": "1px solid #00ff88",
                                           "borderRadius": "3px", "padding": "0.3rem",
                                           "cursor": "pointer", "fontSize": "0.82rem",
                                           "fontWeight": 700}),
                        html.Div(id="edit-result",
                                 style={"color": "#8b949e", "fontSize": "0.75rem",
                                        "marginTop": "0.3rem"}),
                    ],
                ),
            ], id="left-col", style={"flex": "0 0 25%", "overflowY": "auto",
                       "maxHeight": "760px", "paddingRight": "0.5rem", "minWidth": 0}),

            # Right (75%)
            html.Div([

                # ── Toolbar ───────────────────────────────────────────────────
                # MA and Style panels are position:absolute inside their own
                # relative wrapper so they drop down as compact popups anchored
                # to the button, not spanning the full chart width.
                html.Div([
                    dcc.Dropdown(
                        id="pair-select", options=_traded_pair_options(),
                        value=config.FOREX_PAIRS[0], clearable=False,
                        style={"width": "170px", "fontSize": "0.85rem"},
                    ),
                    *[html.Button(_TF_LABELS[tf],
                                  id={"type": "tf-btn", "index": tf}, n_clicks=0,
                                  style=_tf_btn_style(tf == "M30"))
                      for tf in _ALL_TFS],
                    _sep(),
                    # MA button + compact dropdown panel anchored below it
                    html.Div([
                        html.Button("MA", id="ma-settings-btn", n_clicks=0,
                                    title="Moving Average Settings",
                                    style=_tf_btn_style()),
                        html.Div(
                            id="ma-settings-panel",
                            style={"display": "none", "position": "absolute",
                                   "top": "100%", "left": 0, "zIndex": 300,
                                   "flexDirection": "column", "gap": "0.4rem",
                                   "background": "#161b22",
                                   "borderRadius": "0 4px 4px 4px",
                                   "padding": "0.6rem 0.75rem", "minWidth": "340px",
                                   "border": "1px solid #30363d",
                                   "boxShadow": "0 6px 20px rgba(0,0,0,0.6)"},
                            children=[
                                html.Span("Moving Average Settings",
                                          style={"color": "#8b949e", "fontSize": "0.75rem",
                                                 "fontWeight": 600}),
                                html.Div(id="ma-settings-panel-rows"),
                            ],
                        ),
                    ], style={"position": "relative"}),
                    html.Button("⚙ Indicators", id="ind-settings-btn", n_clicks=0,
                                title="Indicator settings (CCI / MACD)",
                                style={**_tf_btn_style(),
                                       "color": "#38b6ff",
                                       "border": "1px solid #38b6ff"}),
                    _sep(),
                    # Drawing tool toggles
                    *[html.Button(lbl,
                        id={"type": "draw-tool-btn", "index": mode},
                        n_clicks=0, title=title, style=_draw_btn_style(mode),
                      ) for mode, lbl, title in _DRAW_TOOL_META],
                    html.Button("✕",
                        id={"type": "draw-tool-btn", "index": "cancel"},
                        n_clicks=0, title="Delete selected / cancel mode",
                        style=_tf_btn_style(),
                    ),
                    _sep(),
                    # Lock / Duplicate / Clear
                    html.Button("L", id="draw-lock-btn", n_clicks=0,
                                title="Lock / unlock selected drawing",
                                style={**_tf_btn_style(), "fontFamily": "monospace",
                                       "padding": "0.2rem 0.5rem"}),
                    html.Button("+", id="draw-dup-btn", n_clicks=0,
                                title="Duplicate selected drawing",
                                style={**_tf_btn_style(), "fontFamily": "monospace",
                                       "padding": "0.2rem 0.5rem", "color": "#38b6ff",
                                       "border": "1px solid #38b6ff"}),
                    html.Button("🗑", id="draw-clear-btn", n_clicks=0,
                                title="Clear all drawings",
                                style={**_tf_btn_style(), "fontSize": "0.85rem"}),
                    _sep(),
                    html.Button("Candles", id="chart-type-candle-btn", n_clicks=0,
                                title="Candlestick chart",
                                style=_tf_btn_style(True)),
                    html.Button("HA", id="chart-type-ha-btn", n_clicks=0,
                                title="Heikin-Ashi chart",
                                style=_tf_btn_style(False)),
                    html.Div(style={"marginLeft": "auto"}),
                    # Shutdown button + compact dropdown panel, right-aligned
                    html.Div([
                        html.Button("⏻", id="shutdown-toggle-btn", n_clicks=0,
                                    title="Auto-shutdown settings",
                                    style={**_tf_btn_style(), "fontSize": "0.85rem",
                                           "padding": "4px 8px"}),
                        html.Div(
                            id="shutdown-panel",
                            style={"display": "none", "position": "absolute",
                                   "top": "100%", "right": 0, "zIndex": 300,
                                   "flexDirection": "column", "gap": "0.4rem",
                                   "background": "#161b22",
                                   "borderRadius": "4px 0 4px 4px",
                                   "padding": "0.65rem 0.85rem", "minWidth": "340px",
                                   "border": "1px solid #3d1010",
                                   "boxShadow": "0 6px 20px rgba(0,0,0,0.6)"},
                            children=[
                                html.Span("Auto-Shutdown",
                                          style={"color": "#ff9900", "fontSize": "0.72rem",
                                                 "fontWeight": 600, "letterSpacing": "0.05em"}),
                                html.Div([
                                    html.Button("Disabled", id="shutdown-enable-btn",
                                                n_clicks=0,
                                                style={**_tf_btn_style(False),
                                                       "minWidth": "74px"}),
                                    html.Span("Shutdown at:",
                                              style={"color": "#8b949e", "fontSize": "0.72rem",
                                                     "alignSelf": "center",
                                                     "marginLeft": "0.5rem"}),
                                    dcc.Input(
                                        id="shutdown-time-input", type="time", value="16:00",
                                        style={"background": "#21262d", "color": "#e6edf3",
                                               "border": "1px solid #30363d",
                                               "borderRadius": "3px",
                                               "padding": "0.2rem 0.4rem",
                                               "fontSize": "0.82rem", "width": "100px"},
                                    ),
                                    html.Span("Warn (min):",
                                              style={"color": "#8b949e", "fontSize": "0.72rem",
                                                     "alignSelf": "center",
                                                     "marginLeft": "0.5rem"}),
                                    dcc.Input(
                                        id="shutdown-warn-input", type="number", value=5,
                                        min=1, max=60, step=1, debounce=True,
                                        style={"width": "56px", "background": "#21262d",
                                               "color": "#e6edf3",
                                               "border": "1px solid #30363d",
                                               "borderRadius": "3px",
                                               "padding": "0.2rem 0.35rem",
                                               "fontSize": "0.82rem"},
                                    ),
                                ], style={"display": "flex", "alignItems": "center",
                                          "gap": "0.35rem", "flexWrap": "wrap"}),
                                html.Div(id="shutdown-status",
                                         style={"color": "#ff9900", "fontSize": "0.72rem",
                                                "fontFamily": "monospace"}),
                            ],
                        ),
                    ], style={"position": "relative"}),
                    html.Button("⛶", id="chart-expand-btn", n_clicks=0,
                                title="Expand / collapse chart",
                                style={**_tf_btn_style(), "fontSize": "1rem",
                                       "padding": "4px 8px"}),
                ], style={"display": "flex", "gap": "0.4rem", "flexWrap": "wrap",
                           "alignItems": "center", "marginBottom": "6px"}),

                # Chart area — chart + floating trade panel overlay
                html.Div(
                    id="chart-area-wrapper",
                    style={"position": "relative"},
                    children=[

                        # TradingView chart
                        html.Div(id="tvlw-chart",
                                 style={"height": "680px", "width": "100%",
                                        "background": "#0f0f0f", "borderRadius": "4px",
                                        "overflow": "hidden"}),

                        # ── Floating Quick Trade Widget (TradingView-style) ──
                        html.Div(
                            id="quick-trade-panel",
                            style={
                                "position": "absolute",
                                "top": "8px", "left": "8px",
                                "zIndex": "50",
                                "background": "rgba(13,17,23,0.82)",
                                "backdropFilter": "blur(8px)",
                                "WebkitBackdropFilter": "blur(8px)",
                                "border": "1px solid rgba(48,54,61,0.6)",
                                "borderRadius": "6px",
                                "padding": "6px 8px 8px",
                                "userSelect": "none",
                                "boxShadow": "0 4px 20px rgba(0,0,0,0.6)",
                                "minWidth": "0",
                            },
                            children=[
                                # Thin drag handle at top
                                html.Div(
                                    "⠿",
                                    **{"className": "apex-drag-handle"},
                                    style={
                                        "textAlign": "center", "color": "#444",
                                        "fontSize": "13px", "lineHeight": "1",
                                        "cursor": "grab", "marginBottom": "5px",
                                        "letterSpacing": "2px",
                                    },
                                ),
                                # SELL price | BUY price
                                html.Div([
                                    # SELL side
                                    html.Div([
                                        html.Div(
                                            id="quick-bid-price",
                                            children="—",
                                            style={
                                                "fontFamily": "'JetBrains Mono', monospace",
                                                "fontSize": "0.78rem", "fontWeight": 700,
                                                "color": "#ff3366", "textAlign": "center",
                                                "lineHeight": "1.2", "marginBottom": "3px",
                                            },
                                        ),
                                        html.Button(
                                            "▼ SELL", id="quick-sell-btn", n_clicks=0,
                                            style={
                                                "background": "#3d0010", "color": "#ff3366",
                                                "border": "1px solid #ff3366",
                                                "borderRadius": "4px",
                                                "padding": "5px 14px",
                                                "cursor": "pointer",
                                                "fontSize": "0.8rem", "fontWeight": 700,
                                                "width": "100%",
                                            },
                                        ),
                                    ], style={"textAlign": "center"}),

                                    # Tiny spread between buttons
                                    html.Div(
                                        id="spread-display",
                                        children="—",
                                        style={
                                            "color": "#555", "fontSize": "0.6rem",
                                            "textAlign": "center", "padding": "0 5px",
                                            "alignSelf": "center", "whiteSpace": "nowrap",
                                        },
                                    ),

                                    # BUY side
                                    html.Div([
                                        html.Div(
                                            id="quick-ask-price",
                                            children="—",
                                            style={
                                                "fontFamily": "'JetBrains Mono', monospace",
                                                "fontSize": "0.78rem", "fontWeight": 700,
                                                "color": "#00ff88", "textAlign": "center",
                                                "lineHeight": "1.2", "marginBottom": "3px",
                                            },
                                        ),
                                        html.Button(
                                            "▲ BUY", id="quick-buy-btn", n_clicks=0,
                                            style={
                                                "background": "#003d1f", "color": "#00ff88",
                                                "border": "1px solid #00ff88",
                                                "borderRadius": "4px",
                                                "padding": "5px 14px",
                                                "cursor": "pointer",
                                                "fontSize": "0.8rem", "fontWeight": 700,
                                                "width": "100%",
                                            },
                                        ),
                                    ], style={"textAlign": "center"}),

                                ], style={
                                    "display": "flex", "alignItems": "flex-end",
                                    "gap": "4px",
                                }),

                                # Trade result message
                                html.Div(id="quick-trade-result",
                                         style={"color": "#e6edf3", "fontSize": "0.7rem",
                                                "marginTop": "4px", "lineHeight": "1.3",
                                                "textAlign": "center"}),

                                # Hidden lot-size-input kept for backward compat with callbacks
                                dcc.Input(
                                    id="lot-size-input", type="number",
                                    value=0.01, min=0.001, step=0.001,
                                    style={"display": "none"},
                                ),

                                # Order confirmation form (hidden until BUY/SELL clicked)
                                html.Div(
                                    id="order-form-panel",
                                    style={"display": "none"},
                                    children=[
                                        html.Div(
                                            style={"borderTop": "1px solid #30363d",
                                                   "marginTop": "0.45rem",
                                                   "paddingTop": "0.45rem"},
                                        ),
                                        html.Div([
                                            html.Span(id="order-form-title", children="",
                                                      style={"fontWeight": 700,
                                                             "fontSize": "0.85rem"}),
                                            html.Button("✕", id="order-cancel-btn", n_clicks=0,
                                                        style={"background": "transparent",
                                                               "border": "none",
                                                               "color": "#8b949e",
                                                               "cursor": "pointer",
                                                               "fontSize": "1rem",
                                                               "marginLeft": "auto",
                                                               "padding": "0"}),
                                        ], style={"display": "flex", "alignItems": "center",
                                                  "marginBottom": "0.45rem"}),
                                        # ── Order type toggle ─────────────────
                                        html.Div([
                                            html.Span("Type:",
                                                      style={"color": "#8b949e",
                                                             "fontSize": "0.7rem",
                                                             "alignSelf": "center",
                                                             "marginRight": "0.4rem"}),
                                            html.Button("Market", id="order-type-market-btn",
                                                        n_clicks=0,
                                                        style={**_tf_btn_style(True),
                                                               "fontSize": "0.72rem",
                                                               "padding": "2px 10px"}),
                                            html.Button("Limit",  id="order-type-limit-btn",
                                                        n_clicks=0,
                                                        style={**_tf_btn_style(False),
                                                               "fontSize": "0.72rem",
                                                               "padding": "2px 10px"}),
                                        ], style={"display": "flex", "alignItems": "center",
                                                  "marginBottom": "0.45rem"}),
                                        # ── Limit price (hidden for market orders) ──
                                        html.Div(
                                            id="order-limit-row",
                                            style={"display": "none"},
                                            children=[
                                                _order_field("Limit Price",
                                                             "order-limit-input",
                                                             "#38b6ff"),
                                            ],
                                        ),
                                        html.Div([
                                            _order_field("Entry",     "order-entry-input"),
                                            _order_field("Stop Loss", "order-sl-input",  "#ff3366"),
                                            _order_field("TP 1",      "order-tp1-input", "#00ff88"),
                                            _order_field("TP 2",      "order-tp2-input", "#00ff88"),
                                            _order_field("TP 3",      "order-tp3-input", "#00ff88"),
                                            _order_field("Lot Size",  "order-lot-input", "#ffd700"),
                                        ], style={
                                            "display": "grid",
                                            "gridTemplateColumns": "repeat(2, 1fr)",
                                            "gap": "0.4rem",
                                            "marginBottom": "0.5rem",
                                        }),
                                        html.Button(
                                            id="order-confirm-btn", n_clicks=0,
                                            children="✓ Place Market Order",
                                            style={
                                                "background": "#003d1f", "color": "#00ff88",
                                                "border": "1px solid #00ff88",
                                                "borderRadius": "4px",
                                                "padding": "0.3rem 0.9rem",
                                                "cursor": "pointer",
                                                "fontSize": "0.82rem", "fontWeight": 700,
                                                "width": "100%",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),

            ], id="chart-col", style={"flex": "0 0 75%", "paddingLeft": "0.25rem",
                                      "minWidth": 0}),

        ], style={"display": "flex", "padding": "0.75rem 1rem"}),

        html.Div(id="account-stats"),
        html.Div(id="trade-log-drawer",
                 children=trade_log_drawer([], [], {}, visible=False)),
        html.Div(id="drawer-backdrop", n_clicks=0,
                 style={"display": "none", "position": "fixed", "top": 0, "left": 0,
                        "width": "100%", "height": "100%",
                        "background": "rgba(0,0,0,0.35)", "zIndex": 999}),
        html.Div(id="alert-overlay-container", children=[
            html.Button(id="alert-cancel-btn",  style={"display": "none"}),
            html.Button(id="alert-confirm-btn", style={"display": "none"}),
        ]),

        # Intervals
        dcc.Interval(id="interval-1s",   interval=1_000,   n_intervals=0),
        dcc.Interval(id="interval-5s",   interval=5_000,   n_intervals=0),
        dcc.Interval(id="interval-60s",  interval=60_000,  n_intervals=0),
        dcc.Interval(id="interval-5min", interval=300_000, n_intervals=0),

        # Stores
        dcc.Store(id="drawer-visible",    data=False),
        dcc.Store(id="selected-tf",       data="M30"),
        dcc.Store(id="chart-type-store",  data="candlestick"),
        dcc.Store(id="alert-action",      data=None),
        dcc.Store(id="chart-data-store",  data=None),
        dcc.Store(id="draw-mode-store",   data=None),
        dcc.Store(id="ma-outside-click",  data=0),
        dcc.Store(id="open-trades-store",     data=[]),
        dcc.Store(id="edit-trade-store",      data=None),
        dcc.Store(id="edit-pending-store",    data=None),
        dcc.Store(id="trade-modify-store",    data=None),
        dcc.Store(id="backtest-result-store", data=None),

        # Backtest panel (hidden overlay)
        html.Div(
            id="backtest-panel",
            # height (not just maxHeight) is deliberate: maxHeight alone is a
            # ceiling that only kicks in once content demands it — before any
            # backtest has run, the panel's content (tabs + config row) is
            # short, so the box renders small and its overflowY:auto clips
            # the pair dropdown's popup menu, which needs ~480px of room to
            # show all 13 pairs without its own nested scrollbar. An explicit
            # height reserves that room unconditionally, from the start.
            style={"display": "none", "position": "fixed", "top": "20px",
                   "left": "50%", "transform": "translateX(-50%)",
                   "width": "700px", "height": "85vh", "maxHeight": "92vh",
                   "overflowY": "auto",
                   "background": "#161b22", "border": "1px solid #30363d",
                   "borderRadius": "8px", "padding": "1.5rem", "zIndex": 2000,
                   "boxShadow": "0 8px 32px rgba(0,0,0,0.6)"},
            children=[
                html.Div([
                    html.H4("Backtest", style={"color": "#38b6ff", "margin": 0}),
                    html.Button("×", id="backtest-close-btn", n_clicks=0,
                                style={"background": "transparent", "border": "none",
                                       "color": "#8b949e", "cursor": "pointer",
                                       "fontSize": "1.3rem", "marginLeft": "auto",
                                       "padding": "0"}),
                ], style={"display": "flex", "alignItems": "center",
                           "marginBottom": "0.75rem"}),
                # Tab row
                html.Div([
                    html.Button("Backtest", id="bt-tab-bt",  n_clicks=0,
                                style={**_tf_btn_style(True), "fontSize": "0.8rem"}),
                    html.Button("Walk-Forward", id="bt-tab-wf", n_clicks=0,
                                style={**_tf_btn_style(False), "fontSize": "0.8rem"}),
                ], style={"display": "flex", "gap": "0.4rem", "marginBottom": "1rem"}),
                dcc.Store(id="bt-mode-store", data="backtest"),
                # Config row
                html.Div([
                    html.Span("Pair:", style={"color": "#8b949e", "fontSize": "0.8rem",
                                              "alignSelf": "center"}),
                    dcc.Dropdown(id="bt-pair-select",
                                 options=_traded_pair_options(),
                                 value=config.FOREX_PAIRS[0], clearable=False,
                                 maxHeight=480,
                                 style={"width": "220px", "fontSize": "0.85rem"}),
                    html.Span("Bars:", style={"color": "#8b949e", "fontSize": "0.8rem",
                                              "alignSelf": "center", "marginLeft": "0.5rem"}),
                    dcc.Input(id="bt-bars-input", type="number", value=1500,
                              min=200, max=4500, step=100, debounce=True,
                              style={"width": "70px", "background": "#21262d",
                                     "color": "#e6edf3", "border": "1px solid #30363d",
                                     "borderRadius": "3px", "padding": "0.2rem 0.4rem",
                                     "fontSize": "0.8rem"}),
                    html.Button("▶ Run", id="backtest-run-btn", n_clicks=0,
                                style={"background": "#003d1f", "color": "#00ff88",
                                       "border": "1px solid #00ff88", "borderRadius": "4px",
                                       "padding": "0.3rem 0.9rem", "cursor": "pointer",
                                       "fontSize": "0.82rem", "fontWeight": 700,
                                       "marginLeft": "auto"}),
                ], style={"display": "flex", "gap": "0.4rem", "alignItems": "center",
                           "marginBottom": "0.5rem", "flexWrap": "wrap"}),
                # Walk-forward extra config (shown only in WF mode via JS/display)
                html.Div(id="wf-config-row", style={"display": "none"}, children=[
                    html.Span("Train:", style={"color": "#8b949e", "fontSize": "0.8rem",
                                               "alignSelf": "center"}),
                    dcc.Input(id="wf-train-input", type="number", value=1500,
                              min=500, max=4000, step=250, debounce=True,
                              style={"width": "65px", "background": "#21262d",
                                     "color": "#e6edf3", "border": "1px solid #30363d",
                                     "borderRadius": "3px", "padding": "0.2rem 0.4rem",
                                     "fontSize": "0.8rem"}),
                    html.Span("Test:", style={"color": "#8b949e", "fontSize": "0.8rem",
                                              "alignSelf": "center", "marginLeft": "0.3rem"}),
                    # min=500: with an H4 confirm TF (8 M30 bars per H4 bar), a
                    # 250-bar test window only yields ~31 confirm bars — below
                    # the strategy's own 50-bar minimum, so every window would
                    # silently fail. 500 bars -> ~62 confirm bars, safely clears it.
                    dcc.Input(id="wf-test-input", type="number", value=750,
                              min=500, max=2000, step=250, debounce=True,
                              style={"width": "65px", "background": "#21262d",
                                     "color": "#e6edf3", "border": "1px solid #30363d",
                                     "borderRadius": "3px", "padding": "0.2rem 0.4rem",
                                     "fontSize": "0.8rem"}),
                    html.Span("Step:", style={"color": "#8b949e", "fontSize": "0.8rem",
                                              "alignSelf": "center", "marginLeft": "0.3rem"}),
                    dcc.Input(id="wf-step-input", type="number", value=500,
                              min=250, max=1000, step=250, debounce=True,
                              style={"width": "65px", "background": "#21262d",
                                     "color": "#e6edf3", "border": "1px solid #30363d",
                                     "borderRadius": "3px", "padding": "0.2rem 0.4rem",
                                     "fontSize": "0.8rem"}),
                ], ),
                html.Div(id="backtest-status",
                         style={"color": "#8b949e", "fontSize": "0.8rem",
                                "marginBottom": "0.5rem", "marginTop": "0.5rem"}),
                dcc.Loading(
                    id="backtest-loading",
                    type="circle",
                    color="#38b6ff",
                    children=html.Div(id="backtest-results"),
                ),
            ],
        ),
        html.Div(id="backtest-backdrop", n_clicks=0,
                 style={"display": "none", "position": "fixed", "top": 0, "left": 0,
                        "width": "100%", "height": "100%",
                        "background": "rgba(0,0,0,0.45)", "zIndex": 1999}),

        # Signal Quality panel (hidden overlay)
        html.Div(
            id="sig-quality-panel",
            style={"display": "none", "position": "fixed", "top": "60px",
                   "left": "50%", "transform": "translateX(-50%)",
                   "width": "700px", "maxHeight": "82vh", "overflowY": "auto",
                   "background": "#161b22", "border": "1px solid #30363d",
                   "borderRadius": "8px", "padding": "1.5rem", "zIndex": 2000,
                   "boxShadow": "0 8px 32px rgba(0,0,0,0.6)"},
            children=[
                html.Div([
                    html.H4("Signal Quality", style={"color": "#ffd700", "margin": 0}),
                    html.Button("×", id="sig-quality-close-btn", n_clicks=0,
                                style={"background": "transparent", "border": "none",
                                       "color": "#8b949e", "cursor": "pointer",
                                       "fontSize": "1.3rem", "marginLeft": "auto",
                                       "padding": "0"}),
                ], style={"display": "flex", "alignItems": "center",
                           "marginBottom": "1rem"}),
                dcc.Loading(type="circle", color="#ffd700",
                            children=html.Div(id="sig-quality-content")),
            ],
        ),
        html.Div(id="sig-quality-backdrop", n_clicks=0,
                 style={"display": "none", "position": "fixed", "top": 0, "left": 0,
                        "width": "100%", "height": "100%",
                        "background": "rgba(0,0,0,0.45)", "zIndex": 1999}),

        # ── Learning panel (Adaptive / WFO / XGBoost) ────────────────────────
        html.Div(
            id="learning-panel",
            style={"display": "none", "position": "fixed", "top": "60px",
                   "left": "50%", "transform": "translateX(-50%)",
                   "width": "750px", "maxHeight": "82vh", "overflowY": "auto",
                   "background": "#161b22", "border": "1px solid #30363d",
                   "borderRadius": "8px", "padding": "1.5rem", "zIndex": 2000,
                   "boxShadow": "0 8px 32px rgba(0,0,0,0.6)"},
            children=[
                html.Div([
                    html.H4("Learning Status", style={"color": "#00ff88", "margin": 0}),
                    html.Button("×", id="learning-close-btn", n_clicks=0,
                                style={"background": "transparent", "border": "none",
                                       "color": "#8b949e", "cursor": "pointer",
                                       "fontSize": "1.3rem", "marginLeft": "auto",
                                       "padding": "0"}),
                ], style={"display": "flex", "alignItems": "center",
                           "marginBottom": "1rem"}),
                dcc.Loading(type="circle", color="#00ff88",
                            children=html.Div(id="learning-content")),
                # Action buttons — static so their IDs exist at startup
                html.Div([
                    html.Button("Retrain Now", id="ml-retrain-btn", n_clicks=0,
                                style={"background": "#003d1f", "color": "#00ff88",
                                       "border": "1px solid #00ff88", "borderRadius": "4px",
                                       "padding": "0.3rem 0.8rem", "cursor": "pointer",
                                       "fontSize": "0.8rem", "fontWeight": 600}),
                    html.Button("Run WFO Now", id="ml-wfo-btn", n_clicks=0,
                                style={"background": "#001a2e", "color": "#38b6ff",
                                       "border": "1px solid #38b6ff", "borderRadius": "4px",
                                       "padding": "0.3rem 0.8rem", "cursor": "pointer",
                                       "fontSize": "0.8rem", "fontWeight": 600}),
                ], style={"display": "flex", "gap": "0.5rem", "marginTop": "1rem"}),
                html.Div(id="ml-action-status",
                         style={"color": "#ffd700", "fontSize": "0.8rem", "marginTop": "0.6rem",
                                "minHeight": "1.2em"}),
            ],
        ),
        html.Div(id="learning-backdrop", n_clicks=0,
                 style={"display": "none", "position": "fixed", "top": 0, "left": 0,
                        "width": "100%", "height": "100%",
                        "background": "rgba(0,0,0,0.45)", "zIndex": 1999}),

        # ── Close Trade Confirmation Modal ────────────────────────────────────
        html.Div(
            id="close-trade-modal",
            style={
                "display": "none",
                "position": "fixed",
                "top": "50%", "left": "50%",
                "transform": "translate(-50%, -50%)",
                "background": "#161b22",
                "border": "1px solid #ff3366",
                "borderRadius": "8px",
                "padding": "1.25rem 1.5rem",
                "zIndex": 3000,
                "minWidth": "320px",
                "boxShadow": "0 8px 32px rgba(0,0,0,0.7)",
            },
            children=[
                html.Div([
                    html.Span("Close Trade", style={"color": "#ff3366", "fontWeight": 700,
                                                     "fontSize": "1rem"}),
                    html.Button("×", id="close-modal-cancel-btn", n_clicks=0,
                                style={"background": "transparent", "border": "none",
                                       "color": "#8b949e", "cursor": "pointer",
                                       "fontSize": "1.3rem", "marginLeft": "auto",
                                       "padding": "0"}),
                ], style={"display": "flex", "alignItems": "center",
                           "marginBottom": "0.85rem"}),
                html.Div(id="close-modal-details",
                         style={"marginBottom": "1rem", "fontSize": "0.82rem",
                                "lineHeight": "1.6"}),
                html.Div([
                    html.Button("✓ Confirm Close", id="close-modal-confirm-btn", n_clicks=0,
                                style={"background": "#3d0010", "color": "#ff3366",
                                       "border": "1px solid #ff3366", "borderRadius": "4px",
                                       "padding": "0.4rem 1rem", "cursor": "pointer",
                                       "fontSize": "0.85rem", "fontWeight": 700,
                                       "marginRight": "0.5rem"}),
                    html.Button("Cancel", id="close-modal-cancel-btn-2", n_clicks=0,
                                style={"background": "#21262d", "color": "#e6edf3",
                                       "border": "1px solid #30363d", "borderRadius": "4px",
                                       "padding": "0.4rem 1rem", "cursor": "pointer",
                                       "fontSize": "0.85rem"}),
                ], style={"display": "flex", "alignItems": "center"}),
                # Semi-transparent backdrop
            ],
        ),
        # Backdrop for close modal
        html.Div(
            id="close-modal-backdrop",
            n_clicks=0,
            style={"display": "none", "position": "fixed", "top": 0, "left": 0,
                   "width": "100%", "height": "100%", "background": "rgba(0,0,0,0.5)",
                   "zIndex": 2999},
        ),
        dcc.Store(id="close-trade-store", data=None),

        # ── Edit Trade Floating Modal ─────────────────────────────────────────
        html.Div(
            id="edit-trade-modal",
            style={
                "display": "none",
                "position": "fixed",
                "top": "50%", "left": "50%",
                "transform": "translate(-50%, -50%)",
                "background": "#161b22",
                "border": "1px solid #ffd700",
                "borderRadius": "8px",
                "padding": "1.25rem 1.5rem",
                "zIndex": 3000,
                "minWidth": "340px",
                "boxShadow": "0 8px 32px rgba(0,0,0,0.7)",
            },
            children=[
                html.Div([
                    html.Span(id="edit-modal-title",
                              style={"color": "#ffd700", "fontWeight": 700,
                                     "fontSize": "0.95rem"}),
                    html.Button("×", id="edit-modal-cancel-btn", n_clicks=0,
                                style={"background": "transparent", "border": "none",
                                       "color": "#8b949e", "cursor": "pointer",
                                       "fontSize": "1.3rem", "marginLeft": "auto",
                                       "padding": "0"}),
                ], style={"display": "flex", "alignItems": "center",
                           "marginBottom": "0.85rem"}),
                html.Div([
                    _order_field("Stop Loss", "edit-modal-sl-input",   "#ff3366"),
                    _order_field("TP 1",      "edit-modal-tp1-input",  "#00ff88"),
                    _order_field("TP 2",      "edit-modal-tp2-input",  "#00ff88"),
                    _order_field("TP 3",      "edit-modal-tp3-input",  "#00ff88"),
                ], style={"display": "grid", "gridTemplateColumns": "repeat(2, 1fr)",
                           "gap": "0.4rem", "marginBottom": "0.6rem"}),
                html.Button("↑ Move SL to Breakeven",
                            id="edit-modal-breakeven-btn", n_clicks=0,
                            style={"width": "100%", "background": "#21262d",
                                   "color": "#ffd700", "border": "1px solid #ffd700",
                                   "borderRadius": "3px", "padding": "0.3rem",
                                   "cursor": "pointer", "fontSize": "0.78rem",
                                   "marginBottom": "0.4rem"}),
                html.Button("✓ Update Trade",
                            id="edit-modal-confirm-btn", n_clicks=0,
                            style={"width": "100%", "background": "#003d1f",
                                   "color": "#00ff88", "border": "1px solid #00ff88",
                                   "borderRadius": "3px", "padding": "0.3rem",
                                   "cursor": "pointer", "fontSize": "0.82rem",
                                   "fontWeight": 700}),
                html.Div(id="edit-modal-result",
                         style={"color": "#8b949e", "fontSize": "0.75rem",
                                "marginTop": "0.3rem"}),
            ],
        ),
        html.Div(
            id="edit-modal-backdrop",
            n_clicks=0,
            style={"display": "none", "position": "fixed", "top": 0, "left": 0,
                   "width": "100%", "height": "100%", "background": "rgba(0,0,0,0.4)",
                   "zIndex": 2999},
        ),

        # ── Edit Pending Order Floating Modal ─────────────────────────────────
        html.Div(
            id="edit-pending-modal",
            style={
                "display": "none",
                "position": "fixed",
                "top": "50%", "left": "50%",
                "transform": "translate(-50%, -50%)",
                "background": "#161b22",
                "border": "1px solid #38b6ff",
                "borderRadius": "8px",
                "padding": "1.25rem 1.5rem",
                "zIndex": 3000,
                "minWidth": "340px",
                "boxShadow": "0 8px 32px rgba(0,0,0,0.7)",
            },
            children=[
                html.Div([
                    html.Span(id="edit-pending-modal-title",
                              style={"color": "#38b6ff", "fontWeight": 700,
                                     "fontSize": "0.95rem"}),
                    html.Button("×", id="edit-pending-modal-cancel-btn", n_clicks=0,
                                style={"background": "transparent", "border": "none",
                                       "color": "#8b949e", "cursor": "pointer",
                                       "fontSize": "1.3rem", "marginLeft": "auto",
                                       "padding": "0"}),
                ], style={"display": "flex", "alignItems": "center",
                           "marginBottom": "0.85rem"}),
                html.Div([
                    _order_field("Limit Price", "edit-pending-modal-limit-input", "#38b6ff"),
                    _order_field("Stop Loss",   "edit-pending-modal-sl-input",    "#ff3366"),
                    _order_field("TP 1",        "edit-pending-modal-tp1-input",   "#00ff88"),
                    _order_field("TP 2",        "edit-pending-modal-tp2-input",   "#00ff88"),
                    _order_field("TP 3",        "edit-pending-modal-tp3-input",   "#00ff88"),
                ], style={"display": "grid", "gridTemplateColumns": "repeat(2, 1fr)",
                           "gap": "0.4rem", "marginBottom": "0.6rem"}),
                html.Button("✓ Update Order",
                            id="edit-pending-modal-confirm-btn", n_clicks=0,
                            style={"width": "100%", "background": "#003d1f",
                                   "color": "#00ff88", "border": "1px solid #00ff88",
                                   "borderRadius": "3px", "padding": "0.3rem",
                                   "cursor": "pointer", "fontSize": "0.82rem",
                                   "fontWeight": 700}),
                html.Div(id="edit-pending-modal-result",
                         style={"color": "#8b949e", "fontSize": "0.75rem",
                                "marginTop": "0.3rem"}),
            ],
        ),
        html.Div(
            id="edit-pending-modal-backdrop",
            n_clicks=0,
            style={"display": "none", "position": "fixed", "top": 0, "left": 0,
                   "width": "100%", "height": "100%", "background": "rgba(0,0,0,0.4)",
                   "zIndex": 2999},
        ),

        # ── News Event Detail Modal ───────────────────────────────────────────
        html.Div(
            id="news-event-modal",
            style={
                "display": "none", "position": "fixed",
                "top": "50%", "left": "50%",
                "transform": "translate(-50%, -50%)",
                "background": "#161b22", "border": "1px solid #30363d",
                "borderRadius": "8px", "padding": "1.25rem 1.5rem",
                "zIndex": 3001, "minWidth": "420px", "maxWidth": "540px",
                "boxShadow": "0 8px 32px rgba(0,0,0,0.75)",
                "fontFamily": "'IBM Plex Sans', sans-serif",
            },
            children=[
                html.Div([
                    html.Span(id="news-modal-title",
                              style={"color": "#e6edf3", "fontWeight": 700,
                                     "fontSize": "0.92rem", "flex": 1}),
                    html.Button("×", id="news-modal-close-btn", n_clicks=0,
                                style={"background": "transparent", "border": "none",
                                       "color": "#8b949e", "cursor": "pointer",
                                       "fontSize": "1.4rem", "padding": "0",
                                       "lineHeight": 1}),
                ], style={"display": "flex", "alignItems": "center",
                           "marginBottom": "0.25rem"}),
                html.Span(id="news-modal-time",
                          style={"color": "#8b949e", "fontSize": "0.73rem",
                                 "display": "block", "marginBottom": "1rem"}),
                html.Div(id="news-modal-content"),
            ],
        ),
        html.Div(
            id="news-event-modal-backdrop",
            n_clicks=0,
            style={"display": "none", "position": "fixed", "top": 0, "left": 0,
                   "width": "100%", "height": "100%", "background": "rgba(0,0,0,0.5)",
                   "zIndex": 3000},
        ),

        dcc.Store(id="shutdown-config",
                  data={"enabled": False, "time": "16:00", "warn_minutes": 5}),
        # EMA settings: current active settings for the visible chart
        dcc.Store(id="ema-settings-store", data=_ema_defaults()),
        # EMA settings persisted per pair+TF in browser localStorage
        dcc.Store(id="ema-per-pair-store", storage_type="local", data={}),
        # Order form: holds {direction, pair} while confirmation form is open
        dcc.Store(id="order-form-store", data=None),
        # ── Indicator settings ─────────────────────────────────────────────
        # Computational params only (CCI length/source, MACD fast/slow/signal)
        # per pair key — updating this triggers chart rebuild
        dcc.Store(id="ind-comp-store", data={}),
        dcc.Store(id="news-events-store", data=[]),

        # Dummy targets for clientside callbacks
        html.Div(id="trades-chart-dummy",  style={"display": "none"}),
        html.Div(id="trade-modify-dummy", style={"display": "none"}),
        html.Div(id="chart-dummy",        style={"display": "none"}),
        html.Div(id="latest-pair-dummy",  style={"display": "none"}),
        html.Div(id="draw-dummy",        style={"display": "none"}),
        html.Div(id="draw-lock-dummy",   style={"display": "none"}),
        html.Div(id="draw-dup-dummy",    style={"display": "none"}),
        html.Div(id="ind-panel-dummy",   style={"display": "none"}),
    ],
)


# ═══════════════════════════════════════════════════════════════════════════════
# Callbacks
# ═══════════════════════════════════════════════════════════════════════════════

_CANDLE_COUNT = {"M5": 500, "M15": 500, "M30": 500, "H1": 500, "H4": 500, "D": 365, "W": 104}


# ── Chart data → store ────────────────────────────────────────────────────────

@app.callback(
    Output("chart-data-store", "data"),
    Input("interval-60s",        "n_intervals"),
    Input("pair-select",         "value"),
    Input("selected-tf",         "data"),
    Input("ema-settings-store",  "data"),
    Input("ind-comp-store",      "data"),   # fires when JS changes CCI/MACD params
    Input("chart-type-store",    "data"),
    prevent_initial_call=False,
)
def update_chart(n_intervals, pair, tf, ema_settings, ind_comp, chart_type):
    if not pair or not tf:
        return {"candlestick": [], "emas": [], "cci": [], "macd": [],
                "pair": "", "tf": ""}

    state.update(chart_pair=pair, chart_tf=tf)
    # Candle granularity in seconds — used to floor trade timestamps so markers
    # land exactly on the candle that contains the trade event.
    _tf_secs = {"M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D": 86400, "W": 604800}
    _cand_secs = _tf_secs.get(tf, 3600)

    ctx         = callback_context
    by_interval = bool(
        ctx.triggered and
        ctx.triggered[0]["prop_id"].split(".")[0] == "interval-60s"
    )

    connector = state.get_key("forex_connector")
    gran      = tf   # OANDA accepts TF keys directly

    df = state.get_candles(pair, tf)

    # Force a fresh fetch when pair/TF was just selected (not a background interval tick).
    # This ensures stale cached data from previous sessions is replaced immediately.
    by_pair_change = bool(
        ctx.triggered and
        ctx.triggered[0]["prop_id"].split(".")[0] in ("pair-select", "selected-tf")
    )

    if by_pair_change:
        # Evict stale cache entry so the fetch below always runs
        state.cache_candles(pair, tf, None)
        df = None

    if df is None or by_pair_change:
        if connector is not None:
            try:
                df = connector.get_candles(pair, gran, _CANDLE_COUNT.get(tf, 500))
                state.cache_candles(pair, tf, df)
            except Exception as exc:
                logger.error("Chart candle fetch failed %s %s: %s", pair, gran, exc)
    elif by_interval and connector is not None:
        try:
            fresh = connector.get_live_candles(pair, gran, 3)
            if fresh is not None and not fresh.empty:
                df = pd.concat(
                    [df[~df.index.isin(fresh.index)], fresh]
                ).sort_index()
                state.cache_candles(pair, tf, df)
        except Exception as exc:
            logger.debug("Live candle refresh skipped for %s: %s", pair, exc)

    settings = ema_settings or _ema_defaults()
    vis      = [s for s in settings if s.get("visible", True)]
    if vis:
        ema_periods = tuple(s["period"] for s in vis)
        ema_colors  = [s.get("color", "#ffd700") for s in vis]
        ema_widths  = [s.get("width", 1) for s in vis]
    else:
        ema_periods, ema_colors, ema_widths = (), [], []

    # Extract per-chart indicator params from the comp store (keyed by pair)
    _ind = (ind_comp or {}).get(pair, {})
    result = build_chart_data(
        df, pair, tf=tf,
        ema_periods=ema_periods,
        ema_colors=ema_colors,
        ema_widths=ema_widths,
        cci_length   = _ind.get("cci_length"),
        cci_src      = _ind.get("cci_src"),
        cci_ma_type   = _ind.get("cci_ma_type"),
        cci_ma_length = _ind.get("cci_ma_length"),
        cci_bb_mult   = _ind.get("cci_bb_mult"),
        macd_fast    = _ind.get("macd_fast"),
        macd_slow    = _ind.get("macd_slow"),
        macd_signal_ = _ind.get("macd_signal"),
        show_rsi     = bool(_ind.get("show_rsi",    False)),
        rsi_period   = int(_ind.get("rsi_period",   14)),
        show_bb      = bool(_ind.get("show_bb",     False)),
        bb_period    = int(_ind.get("bb_period",    20)),
        bb_mult      = float(_ind.get("bb_mult",    2.0)),
        show_volume  = bool(_ind.get("show_volume", False)),
        show_stoch   = bool(_ind.get("show_stoch",  False)),
        stoch_k      = int(_ind.get("stoch_k",      14)),
        stoch_d      = int(_ind.get("stoch_d",      3)),
        stoch_smooth = int(_ind.get("stoch_smooth", 3)),
        chart_type   = chart_type or "candlestick",
    )
    # Expose account balance so the position tool can compute position size in JS
    acct = state.get_key("account", {})
    result["accountBalance"] = acct.get("balance") or acct.get("cash", 500.0)

    # Open trades for this pair → chart draws Entry/SL/TP1/TP2/TP3 price lines
    all_trades = state.get_key("open_trades", []) or []
    result["open_trades"] = [
        {
            "id":        t.get("id", ""),
            "direction": t.get("direction", "long"),
            "entry":     float(t.get("entry", 0) or 0),
            "sl":        float(t.get("sl", 0) or 0),
            "tp1":       float(t.get("tp1", 0) or 0),
            "tp2":       float(t.get("tp2", 0) or 0),
            "tp3":       float(t.get("tp3", 0) or 0),
            "open_time": (int(t["open_time"].timestamp()) // _cand_secs * _cand_secs)
                         if t.get("open_time") and hasattr(t["open_time"], "timestamp") else 0,
        }
        for t in all_trades
        if t.get("pair") == pair and t.get("entry")
    ]

    # Closed trades for this pair → chart draws historical entry/exit arrows
    all_closed = state.get_key("closed_trades", []) or []
    result["closed_trades"] = [
        {
            "direction":   t.get("direction", "long"),
            "entry":       float(t.get("entry", 0) or 0),
            "exit_price":  float(t.get("exit_price", 0) or 0),
            "realised_pnl": float(t.get("realised_pnl", 0) or 0),
            "open_time":  (int(t["open_time"].timestamp())  // _cand_secs * _cand_secs)
                          if t.get("open_time")  and hasattr(t["open_time"],  "timestamp") else 0,
            "close_time": (int(t["close_time"].timestamp()) // _cand_secs * _cand_secs)
                          if t.get("close_time") and hasattr(t["close_time"], "timestamp") else 0,
            "close_reason": t.get("close_reason", ""),
        }
        for t in all_closed
        if t.get("pair") == pair and t.get("entry") and t.get("exit_price")
    ][-50:]  # last 50 closed trades max

    # Signal levels overlay — entry/SL/TP from the last qualifying signal for this pair.
    # Suppressed if the signal was stored before the last closed trade on this pair
    # (prevents stale TP/SL lines from persisting after a trade exits).
    sig = (state.get_key("signal_details") or {}).get(pair)
    if sig and sig.get("score", 0) >= 50:
        sig_time  = sig.get("_stored_at")   # datetime set by update_signal_detail()
        last_close_time = None
        for t in reversed(all_closed):
            if t.get("pair") == pair and t.get("close_time"):
                last_close_time = t["close_time"]
                break
        show_sig = True
        if sig_time and last_close_time:
            from datetime import timezone as _tz
            st = sig_time   if sig_time.tzinfo   else sig_time.replace(tzinfo=_tz.utc)
            lt = last_close_time if last_close_time.tzinfo else last_close_time.replace(tzinfo=_tz.utc)
            show_sig = st > lt
        if show_sig:
            result["signal_levels"] = {
                "entry":     sig.get("entry"),
                "sl":        sig.get("stop_loss"),
                "tp1":       (sig.get("tp_levels") or {}).get("tp1"),
                "tp2":       (sig.get("tp_levels") or {}).get("tp2"),
                "tp3":       (sig.get("tp_levels") or {}).get("tp3"),
                "direction": sig.get("direction"),
            }

    return result


# ── Track the user's latest pair selection, purely client-side ────────────────
# Defense-in-depth for _apexUpdateChart: update_chart() below does a real OANDA
# fetch, so under load a response could in principle arrive after the user has
# already switched to a different pair. window._apexLatestPair (set here) and
# window._apexLatestTf (set by a native capturing click listener in chart.js,
# not a Dash clientside callback — see the comment there) let
# _apexUpdateChart detect and discard an already-superseded payload. NOTE:
# this was NOT the cause of the "zoom resets when switching TF and back" bug
# investigated 2026-07-20 — that turned out to be _suppressZoomSave in
# chart.js (an implicit chart auto-fit clobbering the saved zoom during
# load(), unrelated to response ordering). Kept as a real, if lower-probability,
# safety net against genuine slow/out-of-order responses.
app.clientside_callback(
    """
    function(pair) { window._apexLatestPair = pair || ''; return ''; }
    """,
    Output("latest-pair-dummy", "children"),
    Input("pair-select", "value"),
)

# ── Push chart data to TVLC ───────────────────────────────────────────────────

app.clientside_callback(
    """
    function(data) {
        if (!data) return '';
        if (window._apexUpdateChart) {
            window._apexUpdateChart(data);
        } else {
            var n = 0;
            var t = setInterval(function() {
                n++;
                if (window._apexUpdateChart) {
                    clearInterval(t);
                    window._apexUpdateChart(data);
                } else if (n > 40) clearInterval(t);
            }, 150);
        }
        return '';
    }
    """,
    Output("chart-dummy", "children"),
    Input("chart-data-store", "data"),
)


# ── TF buttons ────────────────────────────────────────────────────────────────

@app.callback(
    Output("selected-tf", "data"),
    Input({"type": "tf-btn", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def select_tf(clicks):
    if not ctx.triggered:
        return no_update
    prop = ctx.triggered[0]["prop_id"]
    return json.loads(prop.split(".")[0])["index"]


@app.callback(
    Output({"type": "tf-btn", "index": ALL}, "style"),
    Input("selected-tf", "data"),
)
def highlight_active_tf(tf):
    return [_tf_btn_style(t == (tf or "M30")) for t in _ALL_TFS]


# ── Chart type (Candles / Heikin-Ashi) ───────────────────────────────────────

@app.callback(
    Output("chart-type-store",      "data"),
    Output("chart-type-candle-btn", "style"),
    Output("chart-type-ha-btn",     "style"),
    Input("chart-type-candle-btn",  "n_clicks"),
    Input("chart-type-ha-btn",      "n_clicks"),
    prevent_initial_call=True,
)
def select_chart_type(n_candle, n_ha):
    triggered = ctx.triggered_id if ctx.triggered else "chart-type-candle-btn"
    is_ha = triggered == "chart-type-ha-btn"
    ct = "heikin_ashi" if is_ha else "candlestick"
    return ct, _tf_btn_style(not is_ha), _tf_btn_style(is_ha)


# ── MA settings panel toggle ──────────────────────────────────────────────────

@app.callback(
    Output("ma-settings-panel", "style"),
    Output("ma-settings-btn",   "style"),
    Input("ma-settings-btn",    "n_clicks"),
    Input("ma-outside-click",   "data"),
    State("ma-settings-panel",  "style"),
    prevent_initial_call=True,
)
def toggle_ma_panel(_, _close, ps):
    ctx      = callback_context
    trigger  = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else ""
    is_open  = (ps or {}).get("display") != "none"
    if trigger == "ma-outside-click":
        new_open = False        # always close on outside click
    else:
        new_open = not is_open  # button: toggle
    disp = "flex" if new_open else "none"
    return {**(ps or {}), "display": disp}, _tf_btn_style(new_open)


# ── Populate MA settings rows ─────────────────────────────────────────────────

@app.callback(
    Output("ma-settings-panel-rows", "children"),
    Input("ema-settings-store", "data"),
    prevent_initial_call=False,
)
def rebuild_ma_rows(settings):
    return _build_ma_rows(settings or _ema_defaults())


# ── EMA settings: update (period/colour/width/vis) + persist per pair+TF ─────
# Also handles pair/TF change → loads saved settings from localStorage store.

@app.callback(
    Output("ema-settings-store",  "data"),
    Output("ema-per-pair-store",  "data"),
    Input({"type": "ema-period-input", "index": ALL}, "value"),
    Input({"type": "ema-color-btn",    "index": ALL}, "n_clicks"),
    Input({"type": "ema-width-btn",    "index": ALL}, "n_clicks"),
    Input({"type": "ema-vis-btn",      "index": ALL}, "n_clicks"),
    Input("pair-select", "value"),
    Input("selected-tf", "data"),
    State("ema-settings-store",  "data"),
    State("ema-per-pair-store",  "data"),
    prevent_initial_call=True,
)
def update_and_save_ema_settings(periods, _cc, _wc, _vc,
                                  pair, tf, current, per_pair):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update

    per_pair  = dict(per_pair or {})
    triggered = ctx.triggered[0]["prop_id"].split(".")[0]

    # ── Pair or TF changed → load saved settings ──────────────────────────
    if triggered in ("pair-select", "selected-tf"):
        key      = pair if pair else None
        settings = per_pair.get(key, _ema_defaults()) if key else _ema_defaults()
        return settings, no_update   # don't overwrite per_pair on load

    # ── EMA setting changed ───────────────────────────────────────────────
    settings = [dict(s) for s in (current or _ema_defaults())]

    try:
        tid    = json.loads(triggered)
        t_type = tid.get("type", "")
        t_idx  = tid.get("index", "")
    except (json.JSONDecodeError, AttributeError):
        return no_update, no_update

    if t_type == "ema-period-input":
        i = int(t_idx)
        if i < len(settings) and periods and i < len(periods) and periods[i] is not None:
            try:
                settings[i]["period"] = max(2, min(500, int(periods[i])))
            except (ValueError, TypeError):
                pass

    elif t_type == "ema-color-btn":
        parts = str(t_idx).split("-", 1)
        if len(parts) == 2:
            i, c = int(parts[0]), "#" + parts[1]
            if i < len(settings):
                settings[i]["color"] = c

    elif t_type == "ema-width-btn":
        parts = str(t_idx).split("-", 1)
        if len(parts) == 2:
            i, w = int(parts[0]), int(parts[1])
            if i < len(settings):
                settings[i]["width"] = w

    elif t_type == "ema-vis-btn":
        i = int(t_idx)
        if i < len(settings):
            settings[i]["visible"] = not settings[i].get("visible", True)

    # Persist under current pair key (shared across all TFs for this pair)
    key = pair if pair else None
    if key:
        per_pair[key] = settings

    return settings, per_pair


# ── Order type toggle (Market ↔ Limit) ───────────────────────────────────────

@app.callback(
    Output("order-type-market-btn", "style", allow_duplicate=True),
    Output("order-type-limit-btn",  "style", allow_duplicate=True),
    Output("order-limit-row",       "style", allow_duplicate=True),
    Output("order-form-store",      "data",  allow_duplicate=True),
    Output("order-confirm-btn",     "children", allow_duplicate=True),
    Input("order-type-market-btn",  "n_clicks"),
    Input("order-type-limit-btn",   "n_clicks"),
    State("order-form-store",       "data"),
    prevent_initial_call=True,
)
def toggle_order_type(market_n, limit_n, form_data):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update, no_update, no_update
    btn = ctx.triggered[0]["prop_id"].split(".")[0]
    is_limit = (btn == "order-type-limit-btn")

    active   = {**_tf_btn_style(True),  "fontSize": "0.72rem", "padding": "2px 10px"}
    inactive = {**_tf_btn_style(False), "fontSize": "0.72rem", "padding": "2px 10px"}
    limit_row_style = {"display": "block"} if is_limit else {"display": "none"}

    # Update the form store with the selected order type
    new_data = dict(form_data or {})
    new_data["order_type"] = "limit" if is_limit else "market"

    return (
        inactive if is_limit else active,
        active   if is_limit else inactive,
        limit_row_style,
        new_data,
        "✓ Place Limit Order" if is_limit else "✓ Place Market Order",
    )


# ── Drawing tool toggle ───────────────────────────────────────────────────────

@app.callback(
    Output("draw-mode-store", "data"),
    Output({"type": "draw-tool-btn", "index": ALL}, "style"),
    Input( {"type": "draw-tool-btn", "index": ALL}, "n_clicks"),
    State("draw-mode-store", "data"),
    prevent_initial_call=True,
)
def toggle_draw_mode(_, current):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, [_draw_btn_style(m) for m in _DRAW_TOOL_MODES + ["cancel"]]
    btn_idx = json.loads(
        ctx.triggered[0]["prop_id"].split(".")[0]
    ).get("index")
    if btn_idx == "cancel":
        # Always clear the store too (not just the button styles) — the
        # clientside callback's isCancel branch always deactivates drawMode
        # client-side now (see draw-dummy callback below), so this must
        # match or the store goes stale: a later State("draw-mode-store")
        # read (e.g. the next tool-button click) would still see the old
        # mode and misinterpret which tool is "currently active".
        all_modes = _DRAW_TOOL_MODES + ["cancel"]
        return None, [_draw_btn_style(m, False) for m in all_modes]
    mode = None if current == btn_idx else btn_idx
    all_modes = _DRAW_TOOL_MODES + ["cancel"]
    return mode, [_draw_btn_style(m, mode == m and m != "cancel") for m in all_modes]


# ── Apply draw mode / delete selected / clear all (clientside) ────────────────
# ✕ button: try to delete selected drawing first; if nothing selected, cancel mode.

app.clientside_callback(
    """
    function(mode, clearN, cancelN) {
        var ctx   = window.dash_clientside && window.dash_clientside.callback_context;
        var trg   = ctx ? ctx.triggered : [];

        var isClear = trg.some(function(t) {
            return t.prop_id === 'draw-clear-btn.n_clicks';
        });

        var isCancel = trg.some(function(t) {
            var pp = t.prop_id;
            var dot = pp.lastIndexOf('.');
            try {
                var id = JSON.parse(pp.substring(0, dot));
                return id && id.type === 'draw-tool-btn' && id.index === 'cancel';
            } catch(e) { return false; }
        });

        if (isClear) {
            if (window._apexClearDrawings) window._apexClearDrawings();
            return '';
        }

        if (isCancel) {
            // Delete whatever's selected (if anything) AND always deactivate
            // the current draw tool — a freshly-placed drawing is
            // auto-selected, so "only cancel mode if nothing was selected"
            // meant pressing X right after placing a line just deleted the
            // line and silently left the tool armed, while the toolbar
            // (painted unconditionally inactive on cancel, see
            // toggle_draw_mode() in app.py) showed nothing active — next
            // double-click would then place another drawing instead of
            // reaching the indicator-pane toggle. Bug found 2026-07-26.
            if (window._apexDeleteSelected) window._apexDeleteSelected();
            if (window._apexSetDrawMode) window._apexSetDrawMode(null);
            return '';
        }

        if (window._apexSetDrawMode) window._apexSetDrawMode(mode);
        return '';
    }
    """,
    Output("draw-dummy", "children"),
    Input("draw-mode-store", "data"),
    Input("draw-clear-btn",  "n_clicks"),
    Input({"type": "draw-tool-btn", "index": "cancel"}, "n_clicks"),
    prevent_initial_call=True,
)


# ── Lock selected drawing ─────────────────────────────────────────────────────

app.clientside_callback(
    """function(n){
        if (!n) return '';
        if (window._apexLockSelected) {
            var ok = window._apexLockSelected();
            if (!ok) {
                var btn = document.getElementById('draw-lock-btn');
                if (btn) {
                    var orig = btn.textContent;
                    btn.textContent = 'Select first';
                    btn.style.color = '#ff3366';
                    setTimeout(function(){ btn.textContent = orig; btn.style.color = ''; }, 1800);
                }
            }
        }
        return '';
    }""",
    Output("draw-lock-dummy", "children"),
    Input("draw-lock-btn", "n_clicks"),
    prevent_initial_call=True,
)


# ── Duplicate selected drawing ────────────────────────────────────────────────

app.clientside_callback(
    "function(n){ if(n && window._apexDuplicateSelected) window._apexDuplicateSelected(); return ''; }",
    Output("draw-dup-dummy", "children"),
    Input("draw-dup-btn", "n_clicks"),
    prevent_initial_call=True,
)


# ── Indicator settings panel toggle ──────────────────────────────────────────

app.clientside_callback(
    """
    function(n) {
        if (!n) return '';
        if (window._apexShowIndPanel) {
            // Toggle: hide if already visible, show if hidden
            var el = document.getElementById('apex-ind-panel');
            if (el && el.style.display !== 'none') {
                if (window._apexHideIndPanel) window._apexHideIndPanel();
            } else {
                window._apexShowIndPanel();
            }
        }
        return '';
    }
    """,
    Output("ind-panel-dummy", "children"),
    Input("ind-settings-btn", "n_clicks"),
    prevent_initial_call=True,
)


# ── Auto-shutdown panel toggle ────────────────────────────────────────────────

@app.callback(
    Output("shutdown-panel", "style"),
    Output("shutdown-toggle-btn", "style"),
    Input("shutdown-toggle-btn", "n_clicks"),
    State("shutdown-panel", "style"),
    prevent_initial_call=True,
)
def toggle_shutdown_panel(_, ps):
    is_open  = (ps or {}).get("display") != "none"
    new_disp = "none" if is_open else "flex"
    btn_style = {**_tf_btn_style(not is_open),
                 "fontSize": "0.85rem", "padding": "4px 8px", "color": "#ff9900",
                 "border": "1px solid #ff9900"}
    return {**(ps or {}), "display": new_disp}, btn_style


# ── Shutdown enable/disable toggle ────────────────────────────────────────────

@app.callback(
    Output("shutdown-config", "data"),
    Output("shutdown-enable-btn", "children"),
    Output("shutdown-enable-btn", "style"),
    Input("shutdown-enable-btn",  "n_clicks"),
    Input("shutdown-time-input",  "value"),
    Input("shutdown-warn-input",  "value"),
    State("shutdown-config", "data"),
    prevent_initial_call=True,
)
def update_shutdown_config(enable_clicks, time_val, warn_val, cfg):
    ctx  = callback_context
    cfg  = dict(cfg or {})
    trg  = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else ""

    if trg == "shutdown-enable-btn":
        cfg["enabled"] = not cfg.get("enabled", False)
    if trg == "shutdown-time-input" and time_val:
        cfg["time"] = time_val
    if trg == "shutdown-warn-input" and warn_val is not None:
        cfg["warn_minutes"] = int(warn_val)

    enabled   = cfg.get("enabled", False)
    btn_label = "Enabled" if enabled else "Disabled"
    btn_style = {
        **_tf_btn_style(enabled),
        "minWidth": "74px",
        "color": "#00ff88" if enabled else "#e6edf3",
        "border": f"1px solid {'#00ff88' if enabled else '#30363d'}",
    }
    return cfg, btn_label, btn_style


# ── Auto-shutdown check (every 60 s) ─────────────────────────────────────────

@app.callback(
    Output("shutdown-status", "children"),
    Input("interval-60s", "n_intervals"),
    State("shutdown-config", "data"),
    prevent_initial_call=False,
)
def check_shutdown(_, cfg):
    cfg = cfg or {}
    if not cfg.get("enabled"):
        return ""
    shutdown_time_str = cfg.get("time", "16:00")
    warn_min = int(cfg.get("warn_minutes", 5))
    try:
        now   = _dt.now()
        parts = shutdown_time_str.split(":")
        target = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
        diff   = (target - now).total_seconds()
        if diff <= 0:
            threading.Thread(target=_do_shutdown, daemon=True).start()
            return f"Shutting down…"
        if diff <= warn_min * 60:
            mins = int(diff // 60) + 1
            return f"⚠ Shutdown in {mins} min"
        return f"Shutdown at {shutdown_time_str}"
    except Exception:
        return ""


def _do_shutdown():
    import time as _time
    _time.sleep(1)
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except Exception:
        os._exit(0)


# ── Spread display ────────────────────────────────────────────────────────────

@app.callback(
    Output("spread-display",   "children"),
    Output("quick-bid-price",  "children"),
    Output("quick-ask-price",  "children"),
    Output("quick-buy-btn",    "children"),
    Output("quick-sell-btn",   "children"),
    Input("interval-5s",  "n_intervals"),
    Input("pair-select",  "value"),
)
def update_spread(_, pair):
    blank = "—", "—", "—", "▲ BUY", "▼ SELL"
    if not pair:
        return blank
    connector = state.get_key("forex_connector")
    if connector is None:
        return blank
    try:
        q   = connector.get_current_quote(pair)
        bid = q.get("bid", 0)
        ask = q.get("ask", 0)
        sp  = q.get("spread_pips", 0)
        if not ask:
            return blank

        # ── Real-time SL/TP check for paper trades ────────────────────────────
        if state.get_key("mode") == "paper":
            pt = state.get_key("paper_trader")
            if pt and bid and ask:
                try:
                    closed = pt.tick_check(pair, float(bid), float(ask))
                    if closed:
                        state.update(open_trades=list(pt.open_trades),
                                     closed_trades=list(pt.closed_trades))
                except Exception:
                    pass

        dec        = 3 if "JPY" in pair else 5
        spread_txt = f"{sp:.1f}p"
        return spread_txt, f"{bid:.{dec}f}", f"{ask:.{dec}f}", "▲ BUY", "▼ SELL"
    except Exception:
        return blank


# ── Order form: open / close ──────────────────────────────────────────────────
# BUY or SELL clicked → store direction+pair; Cancel/Confirm → clear store.

@app.callback(
    Output("order-form-store", "data", allow_duplicate=True),
    Input("quick-buy-btn",     "n_clicks"),
    Input("quick-sell-btn",    "n_clicks"),
    Input("order-cancel-btn",  "n_clicks"),
    Input("order-confirm-btn", "n_clicks"),
    State("pair-select",       "value"),
    State("order-form-store",  "data"),
    prevent_initial_call=True,
)
def set_order_form(buy_n, sell_n, cancel_n, confirm_n, pair, form_data):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    triggered = ctx.triggered[0]["prop_id"].split(".")[0]
    if triggered in ("order-cancel-btn", "order-confirm-btn"):
        return None
    if not pair:
        return no_update
    direction = "long" if triggered == "quick-buy-btn" else "short"
    # Preserve order_type (Market/Limit) across a BUY/SELL re-click — this
    # used to always reset to {"direction", "pair"} only, silently
    # discarding a prior Limit selection back to market with no visual
    # indication the toggle had reverted.
    new_data = dict(form_data or {})
    new_data["direction"] = direction
    new_data["pair"]      = pair
    return new_data


# ── Order form: populate fields when store changes ────────────────────────────

@app.callback(
    Output("order-form-panel",  "style"),
    Output("order-form-title",  "children"),
    Output("order-form-title",  "style"),
    Output("order-entry-input", "value"),
    Output("order-sl-input",    "value", allow_duplicate=True),
    Output("order-tp1-input",   "value", allow_duplicate=True),
    Output("order-tp2-input",   "value", allow_duplicate=True),
    Output("order-tp3-input",   "value", allow_duplicate=True),
    Output("order-lot-input",   "value", allow_duplicate=True),
    Output("order-confirm-btn", "style"),
    Output("order-confirm-btn", "children", allow_duplicate=True),
    Output("order-type-market-btn", "style", allow_duplicate=True),
    Output("order-type-limit-btn",  "style", allow_duplicate=True),
    Output("order-limit-row",       "style", allow_duplicate=True),
    Output("order-limit-input",     "value"),
    Input("order-form-store",   "data"),
    State("lot-size-input",     "value"),
    prevent_initial_call=True,
)
def populate_order_form(form_data, default_lot):
    _hidden = {"display": "none"}
    _no = no_update

    if not form_data:
        return _hidden, _no, _no, _no, _no, _no, _no, _no, _no, _no, _no, _no, _no, _no, _no

    direction = form_data["direction"]
    pair      = form_data["pair"]
    connector = state.get_key("forex_connector")

    # Restore the Market/Limit toggle to match the persisted order_type —
    # without this, a Limit selection surviving a BUY/SELL re-click (see
    # set_order_form) would submit correctly but the toggle would visually
    # show Market again, silently lying about what's about to be placed.
    is_limit = form_data.get("order_type", "market") == "limit"
    _active   = {**_tf_btn_style(True),  "fontSize": "0.72rem", "padding": "2px 10px"}
    _inactive = {**_tf_btn_style(False), "fontSize": "0.72rem", "padding": "2px 10px"}
    market_btn_style = _inactive if is_limit else _active
    limit_btn_style  = _active   if is_limit else _inactive
    limit_row_style  = {"display": "block"} if is_limit else {"display": "none"}

    entry_val = sl_val = tp1_val = tp2_val = tp3_val = None
    lot_val = default_lot or 0.01

    if connector:
        try:
            # Use live bid/ask for the entry price (BUY=ask, SELL=bid)
            quote     = connector.get_current_quote(pair)
            entry_val = round(
                float(quote["ask"] if direction == "long" else quote["bid"]) or 0,
                3 if "JPY" in pair else 5,
            ) or None

            # Fetch recent candles for ATR-based SL
            df = connector.get_candles(pair, "H1", 50)
            if df is not None and not df.empty:
                if not entry_val:
                    entry_val = round(float(df["close"].iloc[-1]), 5)
                try:
                    from engine.indicators import atr as _atr
                    atr_val = float(_atr(df["high"], df["low"], df["close"], 14).iloc[-1])
                    sl_dist = atr_val * 1.5
                except Exception:
                    sl_dist = 0.005
                sl_val = round(
                    entry_val - sl_dist if direction == "long" else entry_val + sl_dist,
                    3 if "JPY" in pair else 5,
                )
                from risk.risk_manager import get_tp_levels, calculate_position_size, get_quote_to_usd_rate
                tp      = get_tp_levels(entry_val, sl_val, direction, pair)
                tp1_val = round(tp["tp1"], 3 if "JPY" in pair else 5)
                tp2_val = round(tp["tp2"], 3 if "JPY" in pair else 5)
                tp3_val = round(tp["tp3"], 3 if "JPY" in pair else 5)
                if not default_lot:
                    account = state.get_key("account", {})
                    balance = account.get("balance") or account.get("cash", 500.0)
                    q2u     = get_quote_to_usd_rate(pair, connector)
                    lot_val = calculate_position_size(balance, entry_val, sl_val, pair, q2u)
        except Exception as exc:
            logger.warning("Order form populate failed for %s: %s", pair, exc)

    is_long  = direction == "long"
    accent   = "#00ff88" if is_long else "#ff3366"
    arrow    = "▲ BUY" if is_long else "▼ SELL"

    panel_style = {
        "display":      "block",
        "background":   "#161b22",
        "border":       f"1px solid {accent}",
        "borderRadius": "4px",
        "padding":      "0.75rem",
        "marginTop":    "0.4rem",
    }
    title_style = {"color": accent, "fontWeight": 700, "fontSize": "0.9rem"}
    confirm_style = {
        "background":   "#003d1f" if is_long else "#3d0010",
        "color":        accent,
        "border":       f"1px solid {accent}",
        "borderRadius": "4px",
        "padding":      "0.35rem 1rem",
        "cursor":       "pointer",
        "fontSize":     "0.85rem",
        "fontWeight":   700,
    }
    confirm_label = "✓ Place Limit Order" if is_limit else "✓ Place Market Order"
    # Sane starting point instead of a static 0 — the field previously had
    # no default at all, which is why it always showed 0 and, since nothing
    # ever read it either, a Limit order silently used the live-refreshing
    # Entry price instead of any price the user actually chose.
    limit_val = entry_val

    return (panel_style, f"{arrow}  {pair.replace('_', '/')}", title_style,
            entry_val, sl_val, tp1_val, tp2_val, tp3_val, lot_val, confirm_style,
            confirm_label, market_btn_style, limit_btn_style, limit_row_style, limit_val)


# ── Order form: recalc SL/TP/Lot when Limit Price changes ────────────────────
# Entry only ever reflected the live market price at the moment the form
# opened — for a Limit order the actual fill level is Limit Price, which can
# be far from Entry (that's the whole point of a limit order). Without this,
# SL/TP/Lot stayed anchored to Entry's defaults even after the user moved
# Limit Price, which could leave e.g. a long's SL sitting above its own
# limit price once the two diverged enough.

@app.callback(
    Output("order-sl-input",  "value", allow_duplicate=True),
    Output("order-tp1-input", "value", allow_duplicate=True),
    Output("order-tp2-input", "value", allow_duplicate=True),
    Output("order-tp3-input", "value", allow_duplicate=True),
    Output("order-lot-input", "value", allow_duplicate=True),
    Input("order-limit-input", "value"),
    State("order-form-store",  "data"),
    State("lot-size-input",    "value"),
    prevent_initial_call=True,
)
def recalc_from_limit_price(limit_price, form_data, default_lot):
    _no5 = (no_update,) * 5
    if not form_data or form_data.get("order_type") != "limit" or not limit_price:
        return _no5

    direction = form_data.get("direction", "long")
    pair      = form_data.get("pair", "")
    connector = state.get_key("forex_connector")
    if not pair or not connector:
        return _no5

    try:
        limit_price = float(limit_price)
        dec = 3 if "JPY" in pair else 5
        df  = connector.get_candles(pair, "H1", 50)
        if df is None or df.empty:
            return _no5

        from engine.indicators import atr as _atr
        try:
            atr_val = float(_atr(df["high"], df["low"], df["close"], 14).iloc[-1])
            sl_dist = atr_val * 1.5
        except Exception:
            sl_dist = 0.005

        sl_val = round(
            limit_price - sl_dist if direction == "long" else limit_price + sl_dist,
            dec,
        )
        from risk.risk_manager import get_tp_levels, calculate_position_size, get_quote_to_usd_rate
        tp = get_tp_levels(limit_price, sl_val, direction, pair)
        tp1_val = round(tp["tp1"], dec)
        tp2_val = round(tp["tp2"], dec)
        tp3_val = round(tp["tp3"], dec)

        lot_val = no_update
        if not default_lot:
            account = state.get_key("account", {})
            balance = account.get("balance") or account.get("cash", 500.0)
            q2u     = get_quote_to_usd_rate(pair, connector)
            lot_val = calculate_position_size(balance, limit_price, sl_val, pair, q2u)

        return sl_val, tp1_val, tp2_val, tp3_val, lot_val
    except Exception as exc:
        logger.warning("Limit-price recalc failed for %s: %s", pair, exc)
        return _no5


# ── Order form: confirm → place trade ────────────────────────────────────────

@app.callback(
    Output("quick-trade-result", "children"),
    Output("quick-trade-result", "style"),
    Input("order-confirm-btn",  "n_clicks"),
    State("order-form-store",   "data"),
    State("order-entry-input",  "value"),
    State("order-limit-input",  "value"),
    State("order-sl-input",     "value"),
    State("order-tp1-input",    "value"),
    State("order-tp2-input",    "value"),
    State("order-tp3-input",    "value"),
    State("order-lot-input",    "value"),
    prevent_initial_call=True,
)
def confirm_order(n, form_data, entry, limit_price, sl, tp1, tp2, tp3, lot):
    if not n or not form_data:
        return no_update, no_update

    direction  = form_data["direction"]
    pair       = form_data["pair"]
    order_type = form_data.get("order_type", "market")

    if any(v is None for v in (entry, sl, tp1, lot)):
        return ("✗ Fill in Entry, SL, TP1 and Lot Size",
                {"color": "#ff3366", "fontSize": "0.78rem"})
    if order_type == "limit" and not limit_price:
        return ("✗ Set a Limit Price",
                {"color": "#ff3366", "fontSize": "0.78rem"})

    entry = float(entry)
    limit_price = float(limit_price) if limit_price else entry
    sl    = float(sl)
    tp1   = float(tp1)
    tp2   = float(tp2) if tp2 is not None else entry
    tp3   = float(tp3) if tp3 is not None else entry
    lot   = float(lot)

    tp_levels    = {"tp1": tp1, "tp2": tp2, "tp3": tp3}
    paper_trader = state.get_key("paper_trader")
    tm           = state.get_key("trade_manager_fx")
    size_used    = lot

    if paper_trader is not None:
        if order_type == "limit":
            trade_id = paper_trader.open_limit_order(
                pair=pair, direction=direction,
                limit_price=limit_price, sl=sl,
                tp_levels=tp_levels, size=lot,
            )
            order_label = "LIMIT"
        else:
            trade_id = paper_trader.open_trade(
                pair=pair, direction=direction,
                entry_price=entry, sl=sl,
                tp_levels=tp_levels, size=lot,
            )
            order_label = "MARKET"
        state.update(
            open_trades    = list(paper_trader.open_trades),
            closed_trades  = list(paper_trader.closed_trades),
            pending_orders = list(paper_trader.pending_orders),
        )
        if not trade_id:
            return ("✗ Rejected (duplicate pair or max trades)",
                    {"color": "#ff3366", "fontSize": "0.78rem"})

    elif tm is not None:
        # Real demo/live order. TradeManager sizes itself off account risk %
        # internally (no caller-supplied size), so the Lot Size field above
        # only applies to paper mode here.
        signal = {
            "pair": pair, "direction": direction, "entry": entry,
            "stop_loss": sl, "tp_levels": tp_levels,
            "score": 100,   # manual click — bypass the automated min-score gate
        }
        if order_type == "limit":
            trade_id = tm.open_limit_order(signal, limit_price=limit_price)
            state.update(pending_orders=list(tm.pending_orders.values()))
            if not trade_id:
                return ("✗ Rejected (duplicate pair, max trades, or drawdown halt)",
                        {"color": "#ff3366", "fontSize": "0.78rem"})
            order_label = "LIMIT"
            size_used   = tm.pending_orders[trade_id]["size"]
        else:
            trade_id = tm.open_trade(signal)
            state.update(
                open_trades   = list(tm.open_trades.values()),
                closed_trades = list(tm.closed_trades),
            )
            if not trade_id:
                return ("✗ Rejected (duplicate pair, max trades, or drawdown halt)",
                        {"color": "#ff3366", "fontSize": "0.78rem"})
            order_label = "MARKET"
            size_used   = tm.open_trades[trade_id]["size"]

    else:
        return ("✗ No trader available",
                {"color": "#ff3366", "fontSize": "0.78rem"})

    # No lingering confirmation banner here on success — Open Trades/Pending
    # Trades already show the real thing accurately, and with several orders
    # placed in a session this panel would otherwise just accumulate stale
    # recaps under the BUY/SELL buttons, which is exactly what this panel
    # isn't for. Errors above still return their own message, since a
    # rejection needs feedback right where the attempt was made.
    return "", {}


# ── Close trade: CLOSE button → show modal ───────────────────────────────────

@app.callback(
    Output("close-trade-store",    "data"),
    Output("close-trade-modal",    "style"),
    Output("close-modal-backdrop", "style"),
    Output("close-modal-details",  "children"),
    Input({"type": "close-trade-btn", "index": ALL}, "n_clicks"),
    Input("close-modal-cancel-btn",   "n_clicks"),
    Input("close-modal-cancel-btn-2", "n_clicks"),
    Input("close-modal-confirm-btn",  "n_clicks"),
    Input("close-modal-backdrop",     "n_clicks"),
    State("pair-select", "value"),
    prevent_initial_call=True,
)
def toggle_close_modal(close_clicks, cancel1, cancel2, confirm, backdrop_n, pair):
    _modal_show = {
        "display": "block", "position": "fixed",
        "top": "50%", "left": "50%",
        "transform": "translate(-50%, -50%)",
        "background": "#161b22", "border": "1px solid #ff3366",
        "borderRadius": "8px", "padding": "1.25rem 1.5rem",
        "zIndex": 3000, "minWidth": "320px",
        "boxShadow": "0 8px 32px rgba(0,0,0,0.7)",
    }
    _modal_hide   = {**_modal_show, "display": "none"}
    _back_show    = {"display": "block", "position": "fixed", "top": 0, "left": 0,
                     "width": "100%", "height": "100%",
                     "background": "rgba(0,0,0,0.5)", "zIndex": 2999}
    _back_hide    = {**_back_show, "display": "none"}

    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update, no_update

    triggered = ctx.triggered[0]["prop_id"].split(".")[0]

    # Cancel, confirm, or backdrop click → close modal
    if triggered in ("close-modal-cancel-btn", "close-modal-cancel-btn-2",
                     "close-modal-confirm-btn", "close-modal-backdrop"):
        return None, _modal_hide, _back_hide, no_update

    # CLOSE button on a trade card clicked
    try:
        tid_data = json.loads(triggered)
    except (json.JSONDecodeError, AttributeError):
        return no_update, no_update, no_update, no_update

    if tid_data.get("type") != "close-trade-btn":
        return no_update, no_update, no_update, no_update

    # Find the trade that was clicked
    n_vals = ctx.triggered[0].get("value", 0)
    if not n_vals:
        return no_update, no_update, no_update, no_update

    tid    = tid_data.get("index", "")
    trades = state.get_key("open_trades") or []
    t      = next((x for x in trades if x.get("id") == tid), None)
    if not t:
        return no_update, no_update, no_update, no_update

    pair_v    = t.get("pair", "")
    direction = t.get("direction", "")
    entry     = t.get("entry", 0)
    dec       = 3 if "JPY" in pair_v else 5
    dir_col   = "#00ff88" if direction == "long" else "#ff3366"

    # Live price for current P&L display — prefer the price-stream cache
    # (sub-second) over a REST call, same reasoning as update_trades_and_account
    connector = state.get_key("forex_connector")
    cur_price = t.get("last_price", entry)
    live = state.get_live_price(pair_v)
    if live:
        cur_price = live["mid"]
    elif connector:
        try:
            q = connector.get_current_quote(pair_v)
            cur_price = q.get("mid") or q.get("ask") or cur_price
        except Exception:
            pass

    from risk.risk_manager import get_quote_to_usd_rate
    diff     = (cur_price - entry) if direction == "long" else (entry - cur_price)
    units    = t.get("size", 0) * t.get("remaining", 1.0)
    q2u      = get_quote_to_usd_rate(pair_v, connector)
    floating = round(diff * units * q2u, 4)
    realised = t.get("realised_pnl", 0)
    total_pnl = realised + floating
    pnl_col   = "#00ff88" if total_pnl >= 0 else "#ff3366"

    details = html.Div([
        html.Div([
            html.Span("Pair:  ",     style={"color": "#8b949e", "fontSize": "0.78rem"}),
            html.Span(pair_v.replace("_", "/"),
                      style={"color": "#e6edf3", "fontWeight": 700,
                             "fontSize": "0.82rem", "fontFamily": "monospace"}),
        ]),
        html.Div([
            html.Span("Direction:  ", style={"color": "#8b949e", "fontSize": "0.78rem"}),
            html.Span(direction.upper(),
                      style={"color": dir_col, "fontWeight": 700, "fontSize": "0.82rem"}),
        ]),
        html.Div([
            html.Span("Entry:  ",    style={"color": "#8b949e", "fontSize": "0.78rem"}),
            html.Span(f"{entry:.{dec}f}",
                      style={"color": "#38b6ff", "fontFamily": "monospace",
                             "fontSize": "0.82rem"}),
        ]),
        html.Div([
            html.Span("Current:  ", style={"color": "#8b949e", "fontSize": "0.78rem"}),
            html.Span(f"{cur_price:.{dec}f}",
                      style={"color": "#e6edf3", "fontFamily": "monospace",
                             "fontSize": "0.82rem"}),
        ]),
        html.Div([
            html.Span("P&L:  ",     style={"color": "#8b949e", "fontSize": "0.78rem"}),
            html.Span(f"${total_pnl:+.2f}",
                      style={"color": pnl_col, "fontWeight": 700, "fontFamily": "monospace",
                             "fontSize": "0.9rem"}),
        ]),
    ])

    return {"id": tid}, _modal_show, _back_show, details


# ── Close trade: Confirm → execute manual_close ───────────────────────────────

@app.callback(
    Output("open-trades",  "children", allow_duplicate=True),
    Output("account-stats","children", allow_duplicate=True),
    Input("close-modal-confirm-btn", "n_clicks"),
    State("close-trade-store",       "data"),
    State("pair-select",             "value"),
    prevent_initial_call=True,
)
def confirm_close_trade(n, store, pair):
    if not n or not store:
        return no_update, no_update

    tid = store.get("id", "")
    pt  = state.get_key("paper_trader")
    tm  = state.get_key("trade_manager_fx")
    if not pt and not tm:
        return no_update, no_update

    # Find current price for the exit
    trades = state.get_key("open_trades") or []
    t      = next((x for x in trades if x.get("id") == tid), None)
    if not t:
        return no_update, no_update

    pair_v     = t.get("pair", "")
    connector  = state.get_key("forex_connector")
    exit_price = t.get("last_price", t.get("entry", 0))
    live = state.get_live_price(pair_v)
    if live:
        exit_price = live["mid"]
    elif connector:
        try:
            q = connector.get_current_quote(pair_v)
            exit_price = q.get("mid") or q.get("ask") or exit_price
        except Exception:
            pass

    # Real demo/live trades live in TradeManager, not the paper trader — route
    # by which manager actually holds this trade_id rather than by mode, since
    # both objects can coexist (e.g. mid-migration or in tests).
    if tm and tid in tm.open_trades:
        tm.close_trade(tid, float(exit_price), reason="manual")
        state.update(
            open_trades   = list(tm.open_trades.values()),
            closed_trades = list(tm.closed_trades),
        )
    elif pt:
        pt.manual_close(tid, float(exit_price))
        state.update(
            open_trades   = list(pt.open_trades),
            closed_trades = list(pt.closed_trades),
        )
    else:
        return no_update, no_update

    # Update cooldown for this pair
    from datetime import datetime as _dt_now, timezone as _tz
    cooldowns = dict(state.get_key("trade_cooldowns", {}))
    cooldowns[pair_v] = _dt_now.now(_tz.utc)
    state.update(trade_cooldowns=cooldowns)

    import config as _cfg
    s = state.get()
    trades_now = list(s.get("open_trades", []))
    account    = s.get("account", {})
    closed_all = s.get("closed_trades", [])
    mode       = s.get("mode", "paper")

    from datetime import date, datetime as _dt2
    from dashboard.panels import open_trades_panel, account_stats_bar
    today     = date.today()
    daily_pnl = sum(t.get("realised_pnl", 0) for t in closed_all
                    if isinstance(t.get("close_time"), _dt2)
                    and t["close_time"].date() == today)
    wr_all = (sum(1 for t in closed_all if t.get("realised_pnl", 0) > 0) / len(closed_all)
              if closed_all else 0.0)
    last20 = closed_all[-20:]
    wr_20  = (sum(1 for t in last20 if t.get("realised_pnl", 0) > 0) / len(last20)
              if last20 else 0.0)
    t_today = sum(1 for t in closed_all
                  if isinstance(t.get("close_time"), _dt2) and t["close_time"].date() == today)

    return (
        open_trades_panel(trades_now, mode),
        account_stats_bar(account, daily_pnl, wr_all, wr_20, t_today, mode,
                          profit_factor  = account.get("profit_factor", 0.0),
                          avg_latency_ms = account.get("avg_latency_ms")),
    )


# ── Edit trade modal: EDIT button → show floating modal ──────────────────────

@app.callback(
    Output("edit-trade-store",    "data"),
    Output("edit-trade-modal",    "style"),
    Output("edit-modal-backdrop", "style"),
    Output("edit-modal-title",    "children"),
    Output("edit-modal-sl-input",  "value"),
    Output("edit-modal-tp1-input", "value"),
    Output("edit-modal-tp2-input", "value"),
    Output("edit-modal-tp3-input", "value"),
    Input({"type": "edit-trade-btn", "index": ALL}, "n_clicks"),
    Input("edit-modal-cancel-btn",  "n_clicks"),
    Input("edit-modal-confirm-btn", "n_clicks"),
    Input("edit-modal-breakeven-btn","n_clicks"),
    Input("edit-modal-backdrop",    "n_clicks"),
    prevent_initial_call=True,
)
def toggle_edit_modal(edit_clicks, cancel_n, confirm_n, be_n, backdrop_n):
    _show = {
        "display": "block", "position": "fixed",
        "top": "50%", "left": "50%",
        "transform": "translate(-50%, -50%)",
        "background": "#161b22", "border": "1px solid #ffd700",
        "borderRadius": "8px", "padding": "1.25rem 1.5rem",
        "zIndex": 3000, "minWidth": "340px",
        "boxShadow": "0 8px 32px rgba(0,0,0,0.7)",
    }
    _hide     = {**_show, "display": "none"}
    _back_show = {"display": "block", "position": "fixed", "top": 0, "left": 0,
                   "width": "100%", "height": "100%",
                   "background": "rgba(0,0,0,0.4)", "zIndex": 2999}
    _back_hide = {**_back_show, "display": "none"}
    _blank = (None, _hide, _back_hide, "", None, None, None, None)

    ctx = callback_context
    if not ctx.triggered:
        return (no_update,) * 8

    triggered = ctx.triggered[0]["prop_id"].split(".")[0]
    tval      = ctx.triggered[0].get("value")

    # Cancel/confirm/breakeven/backdrop → close modal (only when actually clicked, i.e. value > 0)
    if triggered in ("edit-modal-cancel-btn", "edit-modal-confirm-btn",
                     "edit-modal-breakeven-btn", "edit-modal-backdrop"):
        return _blank if tval else (no_update,) * 8

    # Pattern-matched edit button fired with n_clicks=0 (DOM rebuild by interval) → ignore
    if not tval:
        return (no_update,) * 8

    try:
        tid_data = json.loads(triggered)
    except (json.JSONDecodeError, AttributeError):
        return (no_update,) * 8

    if tid_data.get("type") != "edit-trade-btn":
        return _blank

    tid    = tid_data.get("index", "")
    trades = state.get_key("open_trades") or []
    t      = next((x for x in trades if x.get("id") == tid), None)
    if not t:
        return _blank

    pair  = t.get("pair", "")
    dec   = 3 if "JPY" in pair else 5
    title = f"Edit  {pair.replace('_', '/')}  {t.get('direction','').upper()}"
    return (
        {"id": tid},
        _show, _back_show, title,
        round(float(t.get("sl",  0)), dec),
        round(float(t.get("tp1", 0)), dec),
        round(float(t.get("tp2", 0)), dec),
        round(float(t.get("tp3", 0)), dec),
    )


@app.callback(
    Output("edit-modal-result",  "children"),
    Output("open-trades",  "children", allow_duplicate=True),
    Input("edit-modal-confirm-btn",    "n_clicks"),
    Input("edit-modal-breakeven-btn",  "n_clicks"),
    State("edit-trade-store",          "data"),
    State("edit-modal-sl-input",       "value"),
    State("edit-modal-tp1-input",      "value"),
    State("edit-modal-tp2-input",      "value"),
    State("edit-modal-tp3-input",      "value"),
    prevent_initial_call=True,
)
def confirm_edit_modal(confirm_n, be_n, store, sl, tp1, tp2, tp3):
    ctx = callback_context
    if not ctx.triggered or not store:
        return no_update, no_update

    triggered = ctx.triggered[0]["prop_id"].split(".")[0]
    if not ctx.triggered[0].get("value"):
        return no_update, no_update

    tid = store.get("id", "")
    pt  = state.get_key("paper_trader")
    tm  = state.get_key("trade_manager_fx")
    mgr = tm if (tm and tid in tm.open_trades) else pt
    if not mgr:
        return "✗ Trader unavailable", no_update

    from dashboard.panels import open_trades_panel

    def _refresh_trades():
        if mgr is tm:
            state.update(open_trades=list(tm.open_trades.values()),
                         closed_trades=list(tm.closed_trades))
        else:
            state.update(open_trades=list(pt.open_trades),
                         closed_trades=list(pt.closed_trades))
        return state.get_key("open_trades") or []

    if triggered == "edit-modal-breakeven-btn":
        ok = mgr.move_to_breakeven(tid)
        trades = _refresh_trades()
        return ("✓ SL moved to breakeven" if ok else "✗ Trade not found",
                open_trades_panel(trades, state.get_key("mode", "paper")))

    if triggered == "edit-modal-confirm-btn":
        ok = mgr.modify_trade(
            tid,
            sl  = float(sl)  if sl  is not None else None,
            tp1 = float(tp1) if tp1 is not None else None,
            tp2 = float(tp2) if tp2 is not None else None,
            tp3 = float(tp3) if tp3 is not None else None,
        )
        trades = _refresh_trades()
        return ("✓ Trade updated" if ok else "✗ Trade not found",
                open_trades_panel(trades, state.get_key("mode", "paper")))

    return no_update, no_update


# ── Edit pending order modal: EDIT button → show floating modal ──────────────

@app.callback(
    Output("edit-pending-store",           "data"),
    Output("edit-pending-modal",           "style"),
    Output("edit-pending-modal-backdrop",  "style"),
    Output("edit-pending-modal-title",     "children"),
    Output("edit-pending-modal-limit-input", "value"),
    Output("edit-pending-modal-sl-input",  "value"),
    Output("edit-pending-modal-tp1-input", "value"),
    Output("edit-pending-modal-tp2-input", "value"),
    Output("edit-pending-modal-tp3-input", "value"),
    Input({"type": "edit-pending-btn", "index": ALL}, "n_clicks"),
    Input("edit-pending-modal-cancel-btn",  "n_clicks"),
    Input("edit-pending-modal-confirm-btn", "n_clicks"),
    Input("edit-pending-modal-backdrop",    "n_clicks"),
    prevent_initial_call=True,
)
def toggle_edit_pending_modal(edit_clicks, cancel_n, confirm_n, backdrop_n):
    _show = {
        "display": "block", "position": "fixed",
        "top": "50%", "left": "50%",
        "transform": "translate(-50%, -50%)",
        "background": "#161b22", "border": "1px solid #38b6ff",
        "borderRadius": "8px", "padding": "1.25rem 1.5rem",
        "zIndex": 3000, "minWidth": "340px",
        "boxShadow": "0 8px 32px rgba(0,0,0,0.7)",
    }
    _hide      = {**_show, "display": "none"}
    _back_show = {"display": "block", "position": "fixed", "top": 0, "left": 0,
                   "width": "100%", "height": "100%",
                   "background": "rgba(0,0,0,0.4)", "zIndex": 2999}
    _back_hide = {**_back_show, "display": "none"}
    _blank = (None, _hide, _back_hide, "", None, None, None, None, None)

    ctx = callback_context
    if not ctx.triggered:
        return (no_update,) * 9

    triggered = ctx.triggered[0]["prop_id"].split(".")[0]
    tval      = ctx.triggered[0].get("value")

    # Cancel/confirm/backdrop → close modal (only when actually clicked, i.e. value > 0)
    if triggered in ("edit-pending-modal-cancel-btn", "edit-pending-modal-confirm-btn",
                     "edit-pending-modal-backdrop"):
        return _blank if tval else (no_update,) * 9

    # Pattern-matched edit button fired with n_clicks=0 (DOM rebuild by interval) → ignore
    if not tval:
        return (no_update,) * 9

    try:
        oid_data = json.loads(triggered)
    except (json.JSONDecodeError, AttributeError):
        return (no_update,) * 9

    if oid_data.get("type") != "edit-pending-btn":
        return _blank

    oid    = oid_data.get("index", "")
    pt     = state.get_key("paper_trader")
    orders = list(pt.pending_orders) if pt else []
    o      = next((x for x in orders if x.get("id") == oid), None)
    if not o:
        return _blank

    pair  = o.get("pair", "")
    dec   = 3 if "JPY" in pair else 5
    title = f"Edit Pending  {pair.replace('_', '/')}  {o.get('direction','').upper()}"
    return (
        {"id": oid},
        _show, _back_show, title,
        round(float(o.get("limit_price", 0)), dec),
        round(float(o.get("sl",  0)), dec),
        round(float(o.get("tp1", 0)), dec),
        round(float(o.get("tp2", 0)), dec),
        round(float(o.get("tp3", 0)), dec),
    )


@app.callback(
    Output("edit-pending-modal-result", "children"),
    Output("pending-trades",            "children", allow_duplicate=True),
    Input("edit-pending-modal-confirm-btn", "n_clicks"),
    State("edit-pending-store",             "data"),
    State("edit-pending-modal-limit-input", "value"),
    State("edit-pending-modal-sl-input",    "value"),
    State("edit-pending-modal-tp1-input",   "value"),
    State("edit-pending-modal-tp2-input",   "value"),
    State("edit-pending-modal-tp3-input",   "value"),
    prevent_initial_call=True,
)
def confirm_edit_pending(confirm_n, store, limit_price, sl, tp1, tp2, tp3):
    if not confirm_n or not store:
        return no_update, no_update

    oid = store.get("id", "")
    pt  = state.get_key("paper_trader")
    tm  = state.get_key("trade_manager_fx")
    kwargs = dict(
        limit_price = float(limit_price) if limit_price is not None else None,
        sl          = float(sl)  if sl  is not None else None,
        tp1         = float(tp1) if tp1 is not None else None,
        tp2         = float(tp2) if tp2 is not None else None,
        tp3         = float(tp3) if tp3 is not None else None,
    )

    if tm and oid in tm.pending_orders:
        # Real order: cancel-and-replace under a NEW id (OANDA limit orders
        # can't be edited in place) — modify_pending_order() returns that
        # new id (or None on failure), unlike paper's plain bool.
        new_oid = tm.modify_pending_order(oid, **kwargs)
        pending = list(tm.pending_orders.values())
        state.update(pending_orders=pending)
        return ("✓ Order updated" if new_oid else "✗ Order not found",
                pending_orders_panel(pending, state.get_key("mode", "paper")))

    if not pt:
        return "✗ Trader unavailable", no_update

    ok = pt.modify_pending_order(oid, **kwargs)
    pending = list(pt.pending_orders)
    state.update(pending_orders=pending)
    return ("✓ Order updated" if ok else "✗ Order not found",
            pending_orders_panel(pending, state.get_key("mode", "paper")))


# ── Sync open trades to chart (immediate on trade placed + every 5 s) ────────

@app.callback(
    Output("open-trades-store", "data"),
    Input("order-confirm-btn", "n_clicks"),
    Input("interval-5s", "n_intervals"),
    State("pair-select", "value"),
    prevent_initial_call=False,
)
def sync_trades_store(_, __, pair):
    all_trades = state.get_key("open_trades", []) or []
    return [
        {
            "id":        t.get("id", ""),
            "direction": t.get("direction", "long"),
            "entry":     float(t.get("entry", 0) or 0),
            "sl":        float(t.get("sl", 0) or 0),
            "tp1":       float(t.get("tp1", 0) or 0),
            "tp2":       float(t.get("tp2", 0) or 0),
            "tp3":       float(t.get("tp3", 0) or 0),
        }
        for t in all_trades
        if t.get("pair") == pair and t.get("entry")
    ]


app.clientside_callback(
    """
    function(trades) {
        if (window._apexUpdateTrades) window._apexUpdateTrades(trades || []);
        return '';
    }
    """,
    Output("trades-chart-dummy", "children"),
    Input("open-trades-store", "data"),
    prevent_initial_call=True,
)


# ── Chart-drag SL/TP modification ────────────────────────────────────────────

@app.callback(
    Output("trade-modify-dummy", "children"),
    Input("trade-modify-store",  "data"),
    prevent_initial_call=True,
)
def handle_chart_trade_modify(data):
    """Receives SL/TP drag events from chart.js and persists via paper_trader
    or (for real demo/live trades) TradeManager, whichever holds this id."""
    if not data:
        return no_update
    tid = data.get("id", "")
    pt  = state.get_key("paper_trader")
    tm  = state.get_key("trade_manager_fx")
    mgr = tm if (tm and tid in tm.open_trades) else pt
    if not mgr:
        return no_update
    sl  = data.get("sl")
    tp1 = data.get("tp1")
    tp2 = data.get("tp2")
    tp3 = data.get("tp3")
    ok = mgr.modify_trade(
        tid,
        sl  = float(sl)  if sl  is not None else None,
        tp1 = float(tp1) if tp1 is not None else None,
        tp2 = float(tp2) if tp2 is not None else None,
        tp3 = float(tp3) if tp3 is not None else None,
    )
    if ok:
        if mgr is tm:
            state.update(open_trades=list(tm.open_trades.values()),
                         closed_trades=list(tm.closed_trades))
        else:
            state.update(open_trades=list(pt.open_trades),
                         closed_trades=list(pt.closed_trades))
    return ""


# ── Connector health pills (5 s) ─────────────────────────────────────────────

def _conn_pill(label: str, ok):
    if ok is True:
        color, dot = "#00ff88", "●"
    elif ok is False:
        color, dot = "#ff3366", "●"
    else:
        color, dot = "#8b949e", "○"    # None = not tested yet
    return html.Span(
        f"{dot} {label}",
        style={"color": color, "fontSize": "0.72rem", "fontWeight": 600,
               "letterSpacing": "0.03em"},
    )


@app.callback(
    Output("connector-status", "children"),
    Input("interval-5s", "n_intervals"),
)
def update_connector_status(_):
    return [
        _conn_pill("OANDA", state.get_key("oanda_ok")),
    ]


# ── Min-score threshold control (▼ / input / ▲ buttons) ─────────────────────

@app.callback(
    Output("min-score-input",  "value"),
    Output("min-score-slider", "value"),
    Output("min-score-label",  "children"),
    Input("min-score-dec",   "n_clicks"),
    Input("min-score-inc",   "n_clicks"),
    Input("min-score-input", "value"),
    State("min-score-input", "value"),
    prevent_initial_call=True,
)
def update_min_score(dec_n, inc_n, inp_val, cur_val):
    triggered = ctx.triggered_id
    try:
        base = int(cur_val or config.MIN_CONFLUENCE_SCORE)
    except (TypeError, ValueError):
        base = config.MIN_CONFLUENCE_SCORE

    if triggered == "min-score-dec":
        new_val = max(1, base - 1)
    elif triggered == "min-score-inc":
        new_val = min(100, base + 1)
    else:
        # Input box changed — validate
        try:
            new_val = max(1, min(100, int(inp_val or base)))
        except (TypeError, ValueError):
            new_val = base

    state.update(min_score=new_val)
    return new_val, new_val, f"/ 100"


# ── Signal monitor ────────────────────────────────────────────────────────────


# ── Scan Now: trigger a full signal scan without waiting for the hourly tick ──

@app.callback(
    Output("scan-status",  "children"),
    Output("scan-status",  "style"),
    Input("scan-now-btn",  "n_clicks"),
    prevent_initial_call=True,
)
def trigger_scan(n):
    if not n:
        return no_update, no_update

    import threading as _th

    def _run():
        """Run the full signal scan in a background thread."""
        try:
            forex_conn  = state.get_key("forex_connector")
            from engine.signal_engine import SignalEngine
            from engine.strategy_ema_cci_macd import clear_cache

            clear_cache()  # ensure fresh EMA fit

            tasks = []
            if forex_conn:
                eng = SignalEngine(forex_conn.get_candles)
                tasks += [(p, "forex", eng) for p in config.FOREX_PAIRS]

            effective_min = max(
                state.get_key("min_score", config.MIN_CONFLUENCE_SCORE),
                config.MIN_CONFLUENCE_SCORE,
            )

            for pair, market, engine in tasks:
                try:
                    sig = engine.run(pair, market)
                    if market == "forex":
                        # Scan Now didn't previously clear the bot-health
                        # "never scanned" warning even though it successfully
                        # scanned the pair — same tracking main.py's scheduled
                        # scan uses, so clicking Scan Now now actually
                        # resolves the health banner instead of leaving it
                        # stuck until the next scheduled cycle.
                        state.record_pair_scan(pair)
                    if sig is None:
                        state.update_signal(pair, 0, "—")
                        continue
                    if sig.get("no_signal"):
                        state.update_signal(pair, 0, "—",
                                            timeframe=sig.get("timeframe", "H1"))
                        state.update_signal_detail(pair, sig)
                        continue

                    score = sig["score"]
                    state.update_signal(pair, score, sig["direction"],
                                        timeframe=sig.get("timeframe", "H1"))
                    state.update_signal_detail(pair, sig)

                    # ── Fire alert + open trade if signal qualifies ──────────
                    if (score >= effective_min
                            and sig.get("entry") and sig.get("stop_loss")
                            and sig.get("tp_levels")
                            and config.MODE == "paper"):
                        pt = state.get_key("paper_trader")
                        if pt:
                            existing = pt.get_open_trade(pair)
                            if not existing or config.ALLOW_MULTIPLE_PER_PAIR:
                                from risk.risk_manager import calculate_position_size
                                acct    = state.get_key("account", {})
                                balance = acct.get("balance") or 500.0
                                size    = calculate_position_size(
                                    balance, sig["entry"], sig["stop_loss"], pair)
                                sig["size"]         = size
                                sig["risk_dollar"]  = round(balance * 0.01, 2)
                                state.set_pending_alert(sig)
                                try:
                                    from alerts.audio_alert import play_alert
                                    play_alert(sig["direction"])
                                except Exception:
                                    pass
                                pt.open_trade(
                                    pair=pair,
                                    direction=sig["direction"],
                                    entry_price=sig["entry"],
                                    sl=sig["stop_loss"],
                                    tp_levels=sig["tp_levels"],
                                    size=size,
                                )
                                state.update(
                                    open_trades   = list(pt.open_trades),
                                    closed_trades = list(pt.closed_trades),
                                )
                                logger.info(
                                    "Scan Now triggered trade: %s %s score=%d",
                                    pair, sig["direction"], score,
                                )
                            else:
                                logger.info(
                                    "Scan Now: %s qualifies (score=%d) but trade already open",
                                    pair, score,
                                )

                except Exception as exc:
                    logger.warning("Scan failed for %s: %s", pair, exc)

        except Exception as exc:
            logger.error("Scan Now failed: %s", exc)

    _th.Thread(target=_run, daemon=True, name="scan-now").start()

    from datetime import datetime as _dt2
    ts = _dt2.now().strftime("%H:%M:%S")
    return (f"scanning… {ts}",
            {"color": "#ffd700", "fontSize": "0.75rem"})


# ── Backtest tab switcher ─────────────────────────────────────────────────────

@app.callback(
    Output("bt-mode-store",    "data"),
    Output("bt-tab-bt",        "style"),
    Output("bt-tab-wf",        "style"),
    Output("wf-config-row",    "style"),
    Input("bt-tab-bt",         "n_clicks"),
    Input("bt-tab-wf",         "n_clicks"),
    prevent_initial_call=True,
)
def switch_bt_tab(n_bt, n_wf):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update, no_update
    triggered = ctx.triggered[0]["prop_id"].split(".")[0]
    _wf_mode  = triggered == "bt-tab-wf"
    _s_active = {**_tf_btn_style(True),  "fontSize": "0.8rem"}
    _s_idle   = {**_tf_btn_style(False), "fontSize": "0.8rem"}
    wf_row_style = {"display": "flex", "gap": "0.4rem",
                    "alignItems": "center", "marginBottom": "0.5rem",
                    "flexWrap": "wrap"} if _wf_mode else {"display": "none"}
    return (
        "walk_forward" if _wf_mode else "backtest",
        _s_idle   if _wf_mode else _s_active,
        _s_active if _wf_mode else _s_idle,
        wf_row_style,
    )


# ── Signal Quality panel show / hide + data load ─────────────────────────────

@app.callback(
    Output("sig-quality-panel",    "style"),
    Output("sig-quality-content",  "children"),
    Output("sig-quality-backdrop", "style"),
    Input("sig-quality-btn",          "n_clicks"),
    Input("sig-quality-close-btn",    "n_clicks"),
    Input("sig-quality-backdrop",     "n_clicks"),
    State("sig-quality-panel",        "style"),
    prevent_initial_call=True,
)
def toggle_sig_quality_panel(open_n, close_n, backdrop_n, current_style):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update
    triggered = ctx.triggered[0]["prop_id"].split(".")[0]
    _show = {**(current_style or {}), "display": "block"}
    _hide = {**(current_style or {}), "display": "none"}
    if triggered != "sig-quality-btn":
        return _hide, no_update, {**_BACK_HIDE, "zIndex": 1999}
    from engine.signal_audit import signal_quality_analysis
    from dashboard.panels import signal_quality_panel
    analysis = signal_quality_analysis()
    return _show, signal_quality_panel(analysis), {**_BACK_SHOW, "zIndex": 1999}


# ── Learning panel show / hide + data load ───────────────────────────────────

@app.callback(
    Output("learning-panel",    "style"),
    Output("learning-content",  "children"),
    Output("learning-backdrop", "style"),
    Input("learning-btn",          "n_clicks"),
    Input("learning-close-btn",    "n_clicks"),
    Input("learning-backdrop",     "n_clicks"),
    State("learning-panel",        "style"),
    prevent_initial_call=True,
)
def toggle_learning_panel(open_n, close_n, backdrop_n, current_style):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update
    triggered = ctx.triggered[0]["prop_id"].split(".")[0]
    _show = {**(current_style or {}), "display": "block"}
    _hide = {**(current_style or {}), "display": "none"}
    if triggered != "learning-btn":
        return _hide, no_update, {**_BACK_HIDE, "zIndex": 1999}
    from engine.adaptive_params import adaptive_params
    from engine.wfo_optimizer import wfo_optimizer
    from learning.pattern_learner import PatternLearner
    learner      = PatternLearner()
    adaptive_sum = adaptive_params.summary()
    wfo_sum      = wfo_optimizer.summary()
    importance   = learner.get_feature_importance()
    calib_rows   = learner.calibration_data(n=20)
    from learning.pattern_learner import _load_closed as _lc, LOG_PATH as _LP
    _df          = _lc(_LP)
    sample_count = len(_df) if _df is not None else 0
    # Read accuracy + n_samples fresh (disk / recomputed) rather than trusting
    # state["ml_stats"], which is only updated by main.py right after a live
    # retrain and can otherwise sit stale or zero (e.g. a freshly-started
    # process that hasn't retrained yet) — same reasoning as `importance` above.
    ml_stats_val = {**(state.get_key("ml_stats") or {}),
                    "accuracy": learner.get_accuracy(), "n_samples": sample_count}
    return _show, learning_panel(adaptive_sum, wfo_sum, ml_stats_val, importance,
                                 calibration_rows=calib_rows, sample_count=sample_count), \
           {**_BACK_SHOW, "zIndex": 1999}


# ── Learning action buttons — Retrain Now / Seed from Backtest ────────────────

@app.callback(
    Output("ml-action-status", "children"),
    Input("ml-retrain-btn",    "n_clicks"),
    Input("ml-wfo-btn",        "n_clicks"),
    prevent_initial_call=True,
)
def ml_action(retrain_n, wfo_n):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    triggered = ctx.triggered[0]["prop_id"].split(".")[0]

    from learning.pattern_learner import PatternLearner
    learner = PatternLearner()

    if triggered == "ml-retrain-btn":
        result = learner.force_train()
        return result or "Retrain complete."

    connector = state.get_key("forex_connector")
    if connector is None:
        # Bot may not be running — create a temporary connector from env credentials
        try:
            from connectors.oanda_connector import OandaConnector
            connector = OandaConnector()
        except Exception as exc:
            return f"Error: cannot connect to OANDA — {exc}"

    pairs = config.FOREX_PAIRS + getattr(config, "FOREX_WATCH", [])

    def _candles_fn(pair, gran, count):
        return connector.get_candles(pair, gran, count)

    if triggered == "ml-wfo-btn":
        import threading
        from engine.wfo_optimizer import wfo_optimizer
        from alerts.telegram_alert import send_wfo_started, send_wfo_complete

        send_wfo_started(pairs)

        def _run_wfo():
            wfo_optimizer.run_all_pairs(_candles_fn, pairs, market="forex")
            send_wfo_complete(wfo_optimizer.summary())

        threading.Thread(target=_run_wfo, daemon=True).start()
        return f"WFO started for {len(pairs)} pair(s) — running in background. Telegram notification will follow when done."

    return no_update


# ── Backtest panel show / hide ────────────────────────────────────────────────

_BACK_SHOW = {"display": "block", "position": "fixed", "top": 0, "left": 0,
              "width": "100%", "height": "100%", "background": "rgba(0,0,0,0.45)"}
_BACK_HIDE = {**_BACK_SHOW, "display": "none"}


@app.callback(
    Output("backtest-panel",    "style"),
    Output("backtest-backdrop", "style"),
    Input("backtest-btn",          "n_clicks"),
    Input("backtest-close-btn",    "n_clicks"),
    Input("backtest-backdrop",     "n_clicks"),
    State("backtest-panel",        "style"),
    prevent_initial_call=True,
)
def toggle_backtest_panel(open_n, close_n, backdrop_n, current_style):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update
    triggered = ctx.triggered[0]["prop_id"].split(".")[0]
    _show = {**(current_style or {}), "display": "block"}
    _hide = {**(current_style or {}), "display": "none"}
    if triggered == "backtest-btn":
        return _show, {**_BACK_SHOW, "zIndex": 1999}
    return _hide, {**_BACK_HIDE, "zIndex": 1999}


# ── Clear results when pair changes (prevents stale data showing for new pair) ─

@app.callback(
    Output("backtest-status",  "children", allow_duplicate=True),
    Output("backtest-results", "children", allow_duplicate=True),
    Input("bt-pair-select",    "value"),
    prevent_initial_call=True,
)
def clear_backtest_on_pair_change(_pair):
    return "", []


# ── Backtest run ──────────────────────────────────────────────────────────────

@app.callback(
    Output("backtest-status",  "children"),
    Output("backtest-results", "children"),
    Input("backtest-run-btn",  "n_clicks"),
    State("bt-pair-select",    "value"),
    State("bt-bars-input",     "value"),
    State("bt-mode-store",     "data"),
    State("wf-train-input",    "value"),
    State("wf-test-input",     "value"),
    State("wf-step-input",     "value"),
    prevent_initial_call=True,
)
def run_backtest_callback(n, pair, bars, mode, wf_train, wf_test, wf_step):
    if not n or not pair:
        return no_update, no_update

    connector = state.get_key("forex_connector")
    if connector is None:
        return "Error: connector not available.", no_update

    market   = "forex"
    gran_h1  = config.primary_tf_for(pair)
    gran_h4  = config.confirm_tf_for(pair)
    from backtest.runner import confirm_tf_ratio
    confirm_ratio = confirm_tf_ratio(pair)
    balance  = state.get_key("account", {}).get("balance", 500.0)

    # ── Walk-Forward mode ─────────────────────────────────────────────────────
    if mode == "walk_forward":
        train = int(wf_train or 1000)
        test  = int(wf_test  or 250)
        step  = int(wf_step  or 250)
        total = train + test
        try:
            # 6 windows max, so fetch train + test + (6 × step) bars
            fetch_bars = total + min(step * 6, 2000)
            df_h1 = connector.get_candles(pair, gran_h1, fetch_bars)
            df_h4 = connector.get_candles(pair, gran_h4, max(200, fetch_bars // confirm_ratio))
        except Exception as exc:
            return f"Data fetch failed: {exc}", html.Div()

        from backtest.runner import run_walk_forward
        from dashboard.panels import walk_forward_results
        wf_res = run_walk_forward(pair, df_h1, df_h4,
                                  train_bars=train, test_bars=test,
                                  step_bars=step,
                                  starting_balance=balance, market=market)
        if "error" in wf_res:
            return f"Walk-forward error: {wf_res['error']}", html.Div()

        n_win = len(wf_res.get("windows", []))
        status = (f"Walk-forward complete — {n_win} windows  "
                  f"Avg OOS PF={wf_res.get('avg_pf_oos',0):.2f}  "
                  f"Stability={wf_res.get('avg_stability',0):.2f}")
        return status, walk_forward_results(wf_res)

    # ── Standard backtest mode ─────────────────────────────────────────────────
    bars = int(bars or 1500)
    try:
        df_h1   = connector.get_candles(pair, gran_h1, bars)
        df_h4   = connector.get_candles(pair, gran_h4, max(200, bars // confirm_ratio))
    except Exception as exc:
        return f"Data fetch failed: {exc}", html.Div()

    # Daily candles for d_trend — best-effort, only used as a logged feature
    # (not a trading gate), so a failed fetch just leaves it at "neutral".
    df_d = None
    try:
        df_d = connector.get_candles(pair, "D", 200)
    except Exception:
        pass

    from backtest.runner import run_backtest
    res    = run_backtest(pair, df_h1, df_h4,
                          starting_balance=balance,
                          min_score=state.get_key("min_score", config.MIN_CONFLUENCE_SCORE),
                          market=market,
                          df_d=df_d)

    if "error" in res:
        return f"Backtest error: {res['error']}", no_update

    # ── Summary cards ─────────────────────────────────────────────────────────
    wr    = res["win_rate"]
    dd    = res["max_drawdown"]
    pf    = res.get("profit_factor", 0.0)
    wr_c  = "#00ff88" if wr >= 0.5 else "#ff3366"
    dd_c  = "#ff3366" if dd >= 0.10 else ("#ffd700" if dd >= 0.05 else "#00ff88")
    pf_c  = "#00ff88" if pf >= 1.5 else ("#ffd700" if pf >= 1.0 else "#ff3366")

    def _card(label, val, color="#e6edf3"):
        return html.Div([
            html.Div(val,   style={"color": color,   "fontWeight": 700, "fontSize": "1.1rem"}),
            html.Div(label, style={"color": "#8b949e","fontSize": "0.7rem", "marginTop": "2px"}),
        ], style={"background": "#21262d", "borderRadius": "4px",
                  "padding": "0.5rem 0.75rem", "textAlign": "center", "flex": "1"})

    summary = html.Div([
        _card("Signals",   str(res["total_signals"]), "#38b6ff"),
        _card("Trades",    str(res["total_trades"]),  "#e6edf3"),
        _card("Win Rate",  f"{wr:.0%}",               wr_c),
        _card("Profit Factor", f"{pf:.2f}",           pf_c),
        _card("Total P&L", f"${res['total_pnl']:+.2f}",
              "#00ff88" if res["total_pnl"] >= 0 else "#ff3366"),
        _card("Max DD",    f"{dd:.1%}",               dd_c),
        _card("Final Bal", f"${res['final_balance']:.2f}", "#ffd700"),
    ], style={"display": "flex", "gap": "0.4rem", "marginBottom": "1rem",
               "flexWrap": "wrap"})

    # ── Equity curve mini-chart ────────────────────────────────────────────────
    import plotly.graph_objects as go
    eq   = res.get("equity_curve", [])
    fig  = go.Figure()
    if eq:
        fig.add_trace(go.Scatter(
            y=[p["nav"] for p in eq],
            name="NAV", line=dict(color="#38b6ff", width=1.5)
        ))
        fig.update_layout(
            plot_bgcolor="#0f0f0f", paper_bgcolor="#0f0f0f",
            font=dict(color="#e6edf3", size=9),
            margin=dict(l=8, r=8, t=4, b=4),
            height=160,
            showlegend=False,
            xaxis=dict(showgrid=False, showticklabels=False),
            yaxis=dict(gridcolor="#21262d", tickfont=dict(size=8)),
        )
    eq_chart = dcc.Graph(figure=fig, config={"displayModeBar": False},
                         style={"borderRadius": "4px", "overflow": "hidden",
                                "marginBottom": "0.75rem"}) if eq else html.Div()

    # ── Last 20 trades ─────────────────────────────────────────────────────────
    def _exit_label(t: dict) -> tuple[str, str]:
        """Return (label, color) that describes how the trade actually closed."""
        reason = (t.get("close_reason") or "?").lower()
        tp1_hit = t.get("tp1_hit", False)
        pnl     = t.get("realised_pnl", 0)
        if reason == "sl":
            if tp1_hit and pnl > 0:
                return "BE", "#38b6ff"      # stopped at breakeven after TP1 → profit
            if tp1_hit and pnl >= 0:
                return "BE", "#8b949e"      # breakeven (flat)
            return "SL", "#ff3366"          # true stop loss → loss
        label_map = {"tp1": ("TP1", "#00ff88"), "tp2": ("TP2", "#00cc66"),
                     "tp3": ("TP3", "#00aa44"), "manual": ("MAN", "#ffd700")}
        return label_map.get(reason, (reason.upper(), "#8b949e"))

    trade_rows = []
    for t in res["trades"][-20:]:
        pnl = t.get("realised_pnl", 0)
        dec = 3 if "JPY" in pair else 5
        lbl, lbl_color = _exit_label(t)
        trade_rows.append(html.Tr([
            html.Td(lbl,
                    style={"padding":"0.2rem 0.4rem","fontSize":"0.72rem",
                           "color": lbl_color, "fontWeight": 700}),
            html.Td(t.get("direction","?")[:1].upper(),
                    style={"padding":"0.2rem 0.4rem","fontSize":"0.72rem",
                           "color":"#00ff88" if t.get("direction")=="long" else "#ff3366"}),
            html.Td(f"{t.get('entry',0):.{dec}f}",
                    style={"padding":"0.2rem 0.4rem","fontSize":"0.7rem","color":"#8b949e"}),
            html.Td(f"{t.get('exit_price',0):.{dec}f}",
                    style={"padding":"0.2rem 0.4rem","fontSize":"0.7rem","color":"#8b949e"}),
            html.Td(f"${pnl:+.2f}",
                    style={"padding":"0.2rem 0.4rem","fontSize":"0.72rem","fontWeight":700,
                           "color":"#00ff88" if pnl>=0 else "#ff3366"}),
        ]))

    trade_table = html.Table(
        [html.Thead(html.Tr([
            html.Th(h, style={"padding":"0.2rem 0.4rem","fontSize":"0.7rem",
                               "color":"#8b949e","textAlign":"left",
                               "borderBottom":"1px solid #21262d"})
            for h in ["Reason","Dir","Entry","Exit","P&L"]
        ])),
         html.Tbody(trade_rows)],
        style={"width":"100%","borderCollapse":"collapse"}
    ) if trade_rows else html.Div("No trades generated.", style={"color":"#8b949e"})

    status = (f"Backtest complete — {res['bars']} bars  "
              f"({df_h1.index[0].date()} → {df_h1.index[-1].date()})")
    return status, html.Div([summary, eq_chart, trade_table])


@app.callback(
    Output("signal-monitor",  "children"),
    Input("interval-60s",     "n_intervals"),
    Input("min-score-slider", "value"),
)
def update_signals(_, threshold):
    if threshold is not None:
        state.update(min_score=int(threshold))

    missed = state.get_key("missed_signals", [])
    missed_banner = []
    if missed:
        from datetime import datetime as _dt3
        items = []
        for m in reversed(missed[-3:]):   # show last 3 missed
            at = _dt3.fromtimestamp(m["_missed_at"]).strftime("%H:%M:%S")
            items.append(html.Div(
                f"⚠ MISSED {m.get('direction','').upper()} {m.get('pair','—')} "
                f"score={m.get('score',0)} @ {at}",
                style={"fontSize": "0.78rem", "color": "#ffd700",
                       "fontFamily": "'JetBrains Mono', monospace",
                       "marginBottom": "2px"},
            ))
        missed_banner = [html.Div(
            children=items,
            style={
                "background": "#2a1a00", "border": "1px solid #ffd700",
                "borderRadius": "4px", "padding": "6px 10px",
                "marginBottom": "8px",
            },
        )]

    panel = signal_monitor_panel(
        state.get_key("signals", {}),
        signal_details=state.get_key("signal_details", {}),
        last_scan_time=state.get_key("last_scan_time"),
        pair_scan_times=state.get_key("pair_scan_times", {}),
    )
    return missed_banner + [panel] if missed_banner else panel


# ── Open trades + account stats ───────────────────────────────────────────────

@app.callback(
    Output("open-trades",    "children"),
    Output("pending-trades", "children"),
    Output("account-stats",  "children"),
    Input("interval-5s",     "n_intervals"),
)
def update_trades_and_account(_):
    s = state.get()
    pt = s.get("paper_trader")
    try:
        if pt is not None:
            trades  = list(pt.open_trades)
            closed  = list(pt.closed_trades)
            pending = list(pt.pending_orders)
            state.update(open_trades=trades, closed_trades=closed, pending_orders=pending)
            acc_raw = pt.get_account()
            account = {
                "balance":          acc_raw["balance"],
                "nav":              acc_raw.get("nav", acc_raw["balance"]),
                "unrealized_pnl":   acc_raw.get("unrealized_pnl", 0),
                "open_trade_count": acc_raw.get("open_trade_count", len(trades)),
                "profit_factor":    pt.profit_factor(),
                "avg_latency_ms":   pt.avg_latency_ms(),
            }
        else:
            trades  = list(s.get("open_trades", []))
            closed  = list(s.get("closed_trades", []))
            pending = list(s.get("pending_orders", []))
            account = s.get("account", {})
    except Exception as _acct_exc:
        logger.error("update_trades_and_account account read failed: %s", _acct_exc, exc_info=True)
        trades  = list(s.get("open_trades", []))
        closed  = list(s.get("closed_trades", []))
        pending = list(s.get("pending_orders", []))
        account = s.get("account", {})
    mode    = s.get("mode",    "paper")

    # ── Live P&L: update last_price for each open trade ───────────────────────
    # Forex pairs prefer the OANDA price-stream cache (sub-second, no network
    # call here) — falls back to a REST quote only if the stream hasn't
    # ticked that pair recently (e.g. not streamed, or reconnecting).
    current_prices = {}
    if trades:
        forex_conn = s.get("forex_connector")
        for t in trades:
            pair = t.get("pair", "")
            if pair in current_prices:
                continue
            live = state.get_live_price(pair)
            if live:
                t["last_price"]      = live["mid"]
                current_prices[pair] = live["mid"]
                continue
            if forex_conn is None:
                continue
            try:
                q = forex_conn.get_current_quote(pair)
                mid = q.get("mid") or 0
                if mid:
                    t["last_price"]  = mid          # update in-memory for P&L calc
                    current_prices[pair] = mid
            except Exception:
                pass

    # Demo/live account dict comes from a broker snapshot taken once per
    # 30-min candle-close cycle (main.py::_refresh_account), so a trade
    # opened mid-cycle shows $0 unrealised for up to 30 minutes even though
    # the sidebar above already computed its real P&L from the same
    # current_prices this tick just fetched. Recompute it here so both
    # panels agree and both update every 5s — mirrors PaperTrader._calc_
    # unrealized()'s exact formula for consistency with paper mode.
    if mode in ("demo", "live") and trades:
        from risk.risk_manager import get_quote_to_usd_rate
        unreal_total = 0.0
        for t in trades:
            px     = t.get("last_price", t.get("entry", 0))
            entry  = t.get("entry", 0)
            units  = t.get("size", 0) * t.get("remaining", 1.0)
            diff   = (px - entry) if t.get("direction") == "long" else (entry - px)
            q2u    = get_quote_to_usd_rate(t.get("pair", ""), forex_conn)
            unreal_total += diff * units * q2u
        account = dict(account)
        account["unrealized_pnl"]   = round(unreal_total, 4)
        account["nav"]              = round(account.get("balance", 0) + unreal_total, 2)
        account["open_trade_count"] = len(trades)

    if closed:
        wins_all = sum(1 for t in closed if t.get("realised_pnl", 0) > 0)
        wr_all   = wins_all / len(closed)
        last20   = closed[-20:]
        wins_20  = sum(1 for t in last20 if t.get("realised_pnl", 0) > 0)
        wr_20    = wins_20 / len(last20)
    else:
        wr_all = wr_20 = 0.0

    from datetime import date, datetime as _dt
    today     = date.today()
    t_today   = sum(1 for t in closed
                    if isinstance(t.get("close_time"), _dt)
                    and t["close_time"].date() == today)
    daily_pnl = sum(t.get("realised_pnl", 0) for t in closed
                    if isinstance(t.get("close_time"), _dt)
                    and t["close_time"].date() == today)

    return (open_trades_panel(trades, mode, current_prices=current_prices),
            pending_orders_panel(pending, mode),
            account_stats_bar(account, daily_pnl, wr_all, wr_20, t_today, mode,
                              profit_factor  = account.get("profit_factor", 0.0),
                              avg_latency_ms = account.get("avg_latency_ms")))


# ── Pending order: CANCEL button ──────────────────────────────────────────────

@app.callback(
    Output("pending-trades", "children", allow_duplicate=True),
    Input({"type": "cancel-limit-btn", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def cancel_pending_order(clicks):
    ctx = callback_context
    if not ctx.triggered or not ctx.triggered[0].get("value"):
        return no_update

    try:
        oid = json.loads(ctx.triggered[0]["prop_id"].split(".")[0])["index"]
    except (json.JSONDecodeError, AttributeError, KeyError):
        return no_update

    pt  = state.get_key("paper_trader")
    tm  = state.get_key("trade_manager_fx")
    mgr = tm if (tm and oid in tm.pending_orders) else pt
    if not mgr:
        return no_update

    mgr.cancel_limit_order(oid)
    pending = list(tm.pending_orders.values()) if mgr is tm else list(pt.pending_orders)
    state.update(pending_orders=pending)
    return pending_orders_panel(pending, state.get_key("mode", "paper"))


# ── Edit trade (sidebar panel): now superseded by the floating modal.
# This callback only handles the sidebar cancel button — the panel stays hidden.
# All EDIT logic is in toggle_edit_modal / confirm_edit_modal above.

@app.callback(
    Output("edit-trade-panel",  "style"),
    Output("edit-trade-title",  "children"),
    Output("edit-sl-input",     "value"),
    Output("edit-tp1-input",    "value"),
    Output("edit-tp2-input",    "value"),
    Output("edit-tp3-input",    "value"),
    Input("edit-cancel-btn",    "n_clicks"),
    prevent_initial_call=True,
)
def open_edit_form(cancel_n):
    # Sidebar panel is permanently hidden — modal handles editing.
    return {"display": "none"}, "", None, None, None, None


@app.callback(
    Output("edit-result", "children"),
    Output("open-trades",  "children", allow_duplicate=True),
    Input("edit-confirm-btn",   "n_clicks"),
    Input("edit-breakeven-btn", "n_clicks"),
    State("edit-trade-store",   "data"),
    State("edit-sl-input",      "value"),
    State("edit-tp1-input",     "value"),
    State("edit-tp2-input",     "value"),
    State("edit-tp3-input",     "value"),
    prevent_initial_call=True,
)
def confirm_edit(confirm_n, be_n, store, sl, tp1, tp2, tp3):
    ctx = callback_context
    if not ctx.triggered or not store:
        return no_update, no_update

    triggered = ctx.triggered[0]["prop_id"].split(".")[0]
    tid       = store.get("id", "")
    pt        = state.get_key("paper_trader")
    if not pt:
        return "✗ Paper trader unavailable", no_update

    if triggered == "edit-breakeven-btn":
        ok = pt.move_to_breakeven(tid)
        state.update(open_trades=list(pt.open_trades),
                     closed_trades=list(pt.closed_trades))
        trades = state.get_key("open_trades") or []
        return ("✓ SL moved to breakeven" if ok else "✗ Trade not found",
                open_trades_panel(trades, state.get_key("mode", "paper")))

    # Confirm edit
    if triggered == "edit-confirm-btn":
        ok = pt.modify_trade(
            tid,
            sl  = float(sl)  if sl  is not None else None,
            tp1 = float(tp1) if tp1 is not None else None,
            tp2 = float(tp2) if tp2 is not None else None,
            tp3 = float(tp3) if tp3 is not None else None,
        )
        state.update(open_trades=list(pt.open_trades),
                     closed_trades=list(pt.closed_trades))
        trades = state.get_key("open_trades") or []
        return ("✓ Trade updated" if ok else "✗ Trade not found",
                open_trades_panel(trades, state.get_key("mode", "paper")))

    return no_update, no_update


@app.callback(
    Output("halted-banner", "style"),
    Input("interval-5s",    "n_intervals"),
)
def update_halt_banner(_):
    return ({"display": "block", "color": "#ff3366", "fontWeight": 700,
             "marginLeft": "1rem", "border": "1px solid #ff3366",
             "borderRadius": "3px", "padding": "2px 10px"}
            if state.get_key("halted", False) else {"display": "none"})


@app.callback(
    Output("root", "className"),
    Input("interval-1s", "n_intervals"),
)
def update_root_class(_):
    alert = state.get_key("pending_alert")
    if alert is None:
        return ""
    from alerts.visual_alert import border_pulse_class
    return border_pulse_class(alert.get("direction", "long"))


@app.callback(
    Output("alert-overlay-container", "children"),
    Input("interval-1s",        "n_intervals"),
    Input("alert-cancel-btn",   "n_clicks"),
    Input("alert-confirm-btn",  "n_clicks"),
    prevent_initial_call=False,
)
def update_alert(_, cancel_clicks, confirm_clicks):
    ctx       = callback_context
    triggered = ctx.triggered[0]["prop_id"] if ctx.triggered else ""

    if "cancel" in triggered:
        state.update(alert_cancelled=True); state.clear_alert()
        return _hidden_alert_btns()
    if "confirm" in triggered:
        state.update(alert_confirmed=True); state.clear_alert()
        return _hidden_alert_btns()

    signal = state.get_key("pending_alert")
    if signal is None:
        return _hidden_alert_btns()

    countdown = state.tick_countdown()
    if countdown == 5:
        try:
            from alerts.audio_alert import play_reminder
            play_reminder(signal.get("direction", "long"))
        except Exception:
            pass

    if countdown <= 0 and not state.get_key("alert_confirmed"):
        state.update(alert_confirmed=True)
        state.clear_alert()   # trade already opened in main.py — just dismiss
        return _hidden_alert_btns()

    from alerts.visual_alert import build_alert_overlay
    return build_alert_overlay(signal, countdown)


@app.callback(
    Output("chart-col",  "style"),
    Output("left-col",   "style"),
    Output("tvlw-chart", "style"),
    Input("chart-expand-btn", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_expand(n):
    if n and n % 2 == 1:
        return (
            {"flex": "0 0 100%", "paddingLeft": 0, "minWidth": 0},
            {"display": "none"},
            {"height": "80vh", "width": "100%", "background": "#0f0f0f",
             "borderRadius": "4px", "overflow": "hidden"},
        )
    return (
        {"flex": "0 0 75%", "paddingLeft": "0.25rem", "minWidth": 0},
        {"flex": "0 0 25%", "overflowY": "auto",
         "maxHeight": "760px", "paddingRight": "0.5rem", "minWidth": 0},
        {"height": "680px", "width": "100%", "background": "#0f0f0f",
         "borderRadius": "4px", "overflow": "hidden"},
    )


def _hidden_alert_btns():
    return [
        html.Button(id="alert-cancel-btn",  style={"display": "none"}),
        html.Button(id="alert-confirm-btn", style={"display": "none"}),
    ]


@app.callback(
    Output("drawer-visible",   "data"),
    Output("trade-log-drawer", "children"),
    Output("drawer-backdrop",  "style"),
    Input("drawer-toggle-btn", "n_clicks"),
    Input("drawer-close-btn",  "n_clicks"),
    Input("drawer-backdrop",   "n_clicks"),
    State("drawer-visible",    "data"),
    prevent_initial_call=True,
)
def toggle_drawer(open_clicks, close_clicks, backdrop_n, visible):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update
    triggered = ctx.triggered[0]["prop_id"].split(".")[0]
    new_vis = not visible if triggered == "drawer-toggle-btn" else False
    s = state.get()
    from engine.signal_audit import signal_frequency_by_week, watching_signal_stats
    back_style = {**_BACK_SHOW, "zIndex": 999} if new_vis else {**_BACK_HIDE, "zIndex": 999}

    # If equity curve is empty (e.g. fresh restart), rebuild from closed trades on the fly
    eq_curve = s.get("equity_curve", [])
    if not eq_curve:
        pt = s.get("paper_trader")
        start_bal = getattr(pt, "_start_balance", 500.0) if pt else 500.0
        closed = sorted(
            [t for t in s.get("closed_trades", []) if t.get("close_time")],
            key=lambda t: t["close_time"],
        )
        running = start_bal
        for t in closed:
            running = round(running + t.get("realised_pnl", 0), 2)
            ct = t["close_time"]
            try:
                unix_ts = int(ct.timestamp()) if hasattr(ct, "timestamp") else int(ct)
                eq_curve.append({"t": unix_ts, "balance": running, "nav": running})
            except Exception:
                pass

    return new_vis, trade_log_drawer(
        s.get("closed_trades", []), s.get("suggestions", []),
        s.get("ml_stats", {}), visible=new_vis,
        equity_curve=eq_curve,
        signal_freq=signal_frequency_by_week(),
        watching_counts=watching_signal_stats(),
    ), back_style


@app.callback(
    Output("pair-select", "value"),
    Input({"type": "signal-row",    "index": ALL}, "n_clicks"),
    Input({"type": "open-trade-row","index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def select_pair_from_panel(sig_clicks, trade_clicks):
    ctx = callback_context
    if not ctx.triggered or not ctx.triggered[0].get("value"):
        return no_update
    triggered_id = json.loads(ctx.triggered[0]["prop_id"].split(".")[0])
    t_type = triggered_id.get("type")
    idx    = triggered_id.get("index", "")

    if t_type == "signal-row":
        return idx  # index IS the pair

    if t_type == "open-trade-row":
        # index is the trade_id; look up the pair
        trades = state.get_key("open_trades") or []
        for t in trades:
            if t.get("id") == idx or t.get("pair") == idx:
                return t.get("pair", no_update)

    return no_update


# ── News Events Panel ─────────────────────────────────────────────────────────

_IMPACT_COLORS = {"high": "#ff4444", "medium": "#ffa500", "low": "#8b949e"}


def _render_news_panel(events: list):
    """Render upcoming economic events as a compact card. Returns (html.Div, serialized_events)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    if not events:
        return html.Div(
            "No upcoming events in the next 24 hours",
            style={"color": "#8b949e", "fontSize": "0.73rem",
                   "padding": "0.4rem 0.6rem",
                   "background": "#161b22", "borderRadius": "4px",
                   "border": "1px solid #30363d", "marginTop": "0.3rem"},
        ), []

    sorted_events = sorted(events, key=lambda e: e["time"])[:10]
    serialized = []
    rows = []
    for i, ev in enumerate(sorted_events):
        ev_time = ev["time"]
        delta_m = (ev_time - now).total_seconds() / 60
        if delta_m >= 0:
            time_label = f"in {int(delta_m)}m" if delta_m < 60 else ev_time.strftime("%H:%M")
        else:
            time_label = f"{int(-delta_m)}m ago"

        impact   = ev.get("impact", "low")
        color    = _IMPACT_COLORS.get(impact, "#8b949e")
        pairs_s  = ", ".join(ev.get("pairs", [])[:2]) or ev.get("currency", "")
        forecast = ev.get("forecast", "")
        prev     = ev.get("previous", "")
        extra    = f"F:{forecast}  P:{prev}" if forecast or prev else ""

        serialized.append({
            "event":     ev.get("event", ""),
            "country":   ev.get("country", ""),
            "currency":  ev.get("currency", ""),
            "impact":    impact,
            "time":      ev_time.strftime("%H:%M UTC"),
            "pairs":     ev.get("pairs", []),
            "forecast":  forecast,
            "consensus": ev.get("consensus", forecast),
            "previous":  prev,
            "actual":    ev.get("actual", ""),
        })

        rows.append(html.Div([
            html.Span(ev_time.strftime("%H:%M"), style={
                "color": "#8b949e", "fontSize": "0.70rem", "minWidth": "36px"}),
            html.Span(time_label, style={
                "color": "#adbac7", "fontSize": "0.67rem", "minWidth": "42px"}),
            html.Span("●", style={"color": color, "fontSize": "0.6rem", "marginRight": "2px"}),
            html.Div([
                html.Span(ev.get("event", "")[:32], style={
                    "color": "#e6edf3", "fontSize": "0.72rem",
                    "overflow": "hidden", "whiteSpace": "nowrap", "textOverflow": "ellipsis",
                    "display": "block"}),
                html.Span(extra, style={
                    "color": "#8b949e", "fontSize": "0.63rem"}) if extra else None,
            ], style={"flex": 1, "minWidth": 0}),
            html.Span(pairs_s, style={
                "color": "#8b949e", "fontSize": "0.64rem", "textAlign": "right",
                "minWidth": "55px", "whiteSpace": "nowrap"}),
        ], id={"type": "news-row", "index": i}, n_clicks=0,
           style={"display": "flex", "gap": "5px", "alignItems": "center",
                  "padding": "3px 0", "borderBottom": "1px solid #21262d",
                  "cursor": "pointer", "borderRadius": "3px"})
        )

    source = events[0].get("source", "ForexFactory") if events else "ForexFactory"
    source = events[0].get("source", "ForexFactory") if events else "ForexFactory"
    panel = html.Div([
        html.Div([
            html.Span("📅 Upcoming Events", style={
                "color": "#ffd700", "fontWeight": 700, "fontSize": "0.78rem"}),
            html.Span(f"via {source}", style={
                "color": "#30363d", "fontSize": "0.65rem", "marginLeft": "6px"}),
        ], style={"display": "flex", "alignItems": "baseline",
                  "marginBottom": "0.3rem"}),
        html.Div(rows),
    ], style={
        "background": "#0f0f0f", "borderRadius": "4px",
        "border": "1px solid #30363d", "padding": "0.5rem 0.6rem",
    })
    return panel, serialized


@app.callback(
    Output("news-panel",        "children"),
    Output("news-events-store", "data"),
    Input("interval-5min", "n_intervals"),
    Input("interval-60s",  "n_intervals"),
    prevent_initial_call=False,
)
def update_news_panel(_5min, _60s):
    _empty_store = []
    # Primary: ForexFactory public XML feed (no API key needed)
    try:
        from connectors.forexfactory_connector import get_upcoming_events as ff_events
        events = ff_events(hours=24)
        if events:
            panel, store = _render_news_panel(events)
            return panel, store
    except Exception as exc:
        logger.warning("ForexFactory news fetch failed: %s", exc)

    # Fallback: Finnhub (requires paid plan — will show error if free tier)
    if config.FINNHUB_API_KEY:
        try:
            from connectors.news_connector import _fetch_events
            raw = _fetch_events()
            if raw:
                traded = set(config.FOREX_PAIRS + getattr(config, "FOREX_WATCH", []))
                events = [e for e in raw if any(p in traded for p in e.get("pairs", []))]
                panel, store = _render_news_panel(events)
                return panel, store
        except Exception:
            pass

    return html.Div(
        "No upcoming events — ForexFactory calendar unavailable",
        style={"color": "#8b949e", "fontSize": "0.72rem",
               "padding": "0.4rem 0.6rem", "background": "#161b22",
               "borderRadius": "4px", "border": "1px solid #30363d",
               "marginTop": "0.3rem"},
    ), _empty_store


# ── News event detail modal ───────────────────────────────────────────────────

_COUNTRY_NAMES = {
    "USD": "United States", "EUR": "Europe",     "GBP": "United Kingdom",
    "JPY": "Japan",         "CAD": "Canada",      "AUD": "Australia",
    "NZD": "New Zealand",   "CHF": "Switzerland",
}
_COUNTRY_FLAGS = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵",
    "CAD": "🇨🇦", "AUD": "🇦🇺", "NZD": "🇳🇿", "CHF": "🇨🇭",
}


def _news_detail_row(label: str, value):
    return html.Div([
        html.Span(label, style={"color": "#8b949e", "fontSize": "0.76rem",
                                "minWidth": "90px", "display": "inline-block"}),
        html.Span(value) if isinstance(value, str) else value,
    ], style={"display": "flex", "alignItems": "center", "marginBottom": "0.45rem"})


@app.callback(
    Output("news-event-modal",         "style"),
    Output("news-event-modal-backdrop","style"),
    Output("news-modal-title",         "children"),
    Output("news-modal-time",          "children"),
    Output("news-modal-content",       "children"),
    Input({"type": "news-row", "index": ALL}, "n_clicks"),
    Input("news-modal-close-btn",           "n_clicks"),
    Input("news-event-modal-backdrop",      "n_clicks"),
    State("news-events-store", "data"),
    prevent_initial_call=True,
)
def show_news_event_detail(row_clicks, _close, _backdrop, events):
    _show = {
        "display": "block", "position": "fixed",
        "top": "50%", "left": "50%",
        "transform": "translate(-50%, -50%)",
        "background": "#161b22", "border": "1px solid #30363d",
        "borderRadius": "8px", "padding": "1.25rem 1.5rem",
        "zIndex": 3001, "minWidth": "420px", "maxWidth": "540px",
        "boxShadow": "0 8px 32px rgba(0,0,0,0.75)",
        "fontFamily": "'IBM Plex Sans', sans-serif",
    }
    _hide      = {**_show, "display": "none"}
    _back_show = {"display": "block", "position": "fixed", "top": 0, "left": 0,
                  "width": "100%", "height": "100%",
                  "background": "rgba(0,0,0,0.5)", "zIndex": 3000}
    _back_hide = {**_back_show, "display": "none"}

    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update, no_update, no_update

    triggered = ctx.triggered[0]["prop_id"].split(".")[0]

    if triggered in ("news-modal-close-btn", "news-event-modal-backdrop"):
        return _hide, _back_hide, no_update, no_update, no_update

    # News row clicked — identify index
    try:
        tid = json.loads(triggered)
        idx = int(tid.get("index", 0))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return no_update, no_update, no_update, no_update, no_update

    if not any(row_clicks):
        return no_update, no_update, no_update, no_update, no_update

    ev_list = events or []
    if idx >= len(ev_list):
        return no_update, no_update, no_update, no_update, no_update

    ev     = ev_list[idx]
    impact = ev.get("impact", "low")
    impact_col = _IMPACT_COLORS.get(impact, "#8b949e")
    imp_text_col = "#000" if impact == "medium" else "#fff"
    country  = ev.get("country", "") or ev.get("currency", "")
    flag     = _COUNTRY_FLAGS.get(country, "")
    cname    = _COUNTRY_NAMES.get(country, country)
    currency = ev.get("currency", country)
    previous = ev.get("previous", "") or "—"
    consensus = ev.get("consensus", "") or ev.get("forecast", "") or "—"
    actual   = ev.get("actual", "") or "—"

    actual_style = {"color": "#e6edf3", "fontSize": "0.80rem"}
    if ev.get("actual"):
        actual_style = {**actual_style,
                        "background": "rgba(255,100,100,0.18)",
                        "padding": "1px 7px", "borderRadius": "3px"}

    content = html.Div([
        # Details column
        html.Div([
            html.Div("Details", style={"color": "#adbac7", "fontWeight": 700,
                                        "fontSize": "0.80rem", "marginBottom": "0.6rem",
                                        "borderBottom": "1px solid #21262d",
                                        "paddingBottom": "0.3rem"}),
            _news_detail_row("Impact:", html.Span(impact.upper(), style={
                "background": impact_col, "color": imp_text_col,
                "padding": "2px 8px", "borderRadius": "3px",
                "fontSize": "0.72rem", "fontWeight": 700})),
            _news_detail_row("Country:", f"{cname} {flag}"),
            _news_detail_row("Currency:", currency),
        ], style={"flex": 1, "background": "#0f0f0f", "borderRadius": "6px",
                  "padding": "0.75rem 0.9rem", "border": "1px solid #21262d"}),

        # Latest Release column
        html.Div([
            html.Div("Latest Release", style={"color": "#adbac7", "fontWeight": 700,
                                               "fontSize": "0.80rem", "marginBottom": "0.6rem",
                                               "borderBottom": "1px solid #21262d",
                                               "paddingBottom": "0.3rem"}),
            _news_detail_row("Previous:", html.Span(previous, style={
                "color": "#e6edf3", "fontSize": "0.80rem"})),
            _news_detail_row("Consensus:", html.Span(consensus, style={
                "color": "#e6edf3", "fontSize": "0.80rem"})),
            _news_detail_row("Actual:", html.Span(actual, style=actual_style)),
        ], style={"flex": 1, "background": "#0f0f0f", "borderRadius": "6px",
                  "padding": "0.75rem 0.9rem", "border": "1px solid #21262d"}),
    ], style={"display": "flex", "gap": "0.75rem"})

    return _show, _back_show, ev.get("event", ""), ev.get("time", ""), content


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if config.OANDA_API_KEY and config.OANDA_ACCOUNT_ID:
        try:
            from connectors.oanda_connector import OandaConnector
            state.update(forex_connector=OandaConnector())
            logger.info("Standalone: Oanda connector ready")
        except Exception as exc:
            logger.warning("Standalone: Oanda connector failed — %s", exc)
    else:
        logger.warning("Standalone: OANDA credentials not set — chart will show no data")

    app.run(debug=True, port=8050)
