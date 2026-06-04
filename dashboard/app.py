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
    Dash, Input, Output, State, ALL,
    callback_context, html, dcc, no_update,
)

import config
from dashboard import state
from dashboard.panels import (
    signal_monitor_panel, open_trades_panel,
    account_stats_bar, trade_log_drawer,
)
from dashboard.chart_builder_twlc import build_chart_data

logger = logging.getLogger(__name__)

# ── Style helpers ─────────────────────────────────────────────────────────────

_TF_LABELS = {"H1": "1H", "H4": "4H", "D": "D"}

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
    ("h-line",    "—",    "Horizontal line — click chart to place"),
    ("trend",     "╱",    "Trend line — click 2 points; drag endpoints to edit"),
    ("pips",      "📏",   "Measure — click and drag to measure price range"),
    ("box",       "Rect", "Rectangle — click and drag to draw"),
    ("square",    "Sqr",  "Square — click and drag (equal sides)"),
    ("long-pos",  "↑ L",  "Long Position — click chart to place entry"),
    ("short-pos", "↓ S",  "Short Position — click chart to place entry"),
]
_DRAW_TOOL_MODES = [m for m, _, _ in _DRAW_TOOL_META]

_POS_BTN_COLORS = {
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

# 9 preset colors + custom picker
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
    {"period": 34,  "color": "#ffd700", "visible": True, "width": 1},
    {"period": 100, "color": "#ff9900", "visible": True, "width": 1},
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

def _pair_options():
    return [{"label": p, "value": p}
            for p in config.FOREX_PAIRS + config.CRYPTO_PAIRS]

def _sep():
    return html.Div(style={"width": "1px", "height": "22px",
                            "background": "#30363d", "alignSelf": "center"})


# ── Layout ────────────────────────────────────────────────────────────────────

app.layout = html.Div(
    id="root",
    style={"backgroundColor": "#DBDBDB", "minHeight": "100vh",
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
            html.Div(style={"marginLeft": "auto", "display": "flex", "gap": "0.5rem"},
                     children=[
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
                html.Div(id="signal-monitor"),
                html.Div(id="open-trades"),
            ], id="left-col", style={"flex": "0 0 25%", "overflowY": "auto",
                       "maxHeight": "760px", "paddingRight": "0.5rem", "minWidth": 0}),

            # Right (75%)
            html.Div([

                # ── Toolbar ───────────────────────────────────────────────────
                html.Div([
                    dcc.Dropdown(
                        id="pair-select", options=_pair_options(),
                        value=config.FOREX_PAIRS[0], clearable=False,
                        style={"width": "170px", "fontSize": "0.85rem"},
                    ),
                    *[html.Button(_TF_LABELS[tf], id=f"tf-btn-{tf}", n_clicks=0,
                                  style=_tf_btn_style(tf == "H1"))
                      for tf in ["H1", "H4", "D"]],
                    _sep(),
                    html.Button("MA", id="ma-settings-btn", n_clicks=0,
                                title="Moving Average Settings", style=_tf_btn_style()),
                    _sep(),
                    # Drawing style panel toggle
                    html.Button("Style ▾", id="draw-style-panel-btn", n_clicks=0,
                                title="Drawing style settings",
                                style=_tf_btn_style()),
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
                    html.Div(style={"marginLeft": "auto"}),
                    html.Button("⏻", id="shutdown-toggle-btn", n_clicks=0,
                                title="Auto-shutdown settings",
                                style={**_tf_btn_style(), "fontSize": "0.85rem",
                                       "padding": "4px 8px"}),
                    html.Button("⛶", id="chart-expand-btn", n_clicks=0,
                                title="Expand / collapse chart",
                                style={**_tf_btn_style(), "fontSize": "1rem",
                                       "padding": "4px 8px"}),
                ], style={"display": "flex", "gap": "0.4rem", "flexWrap": "wrap",
                           "alignItems": "center", "marginBottom": "6px"}),

                # ── Drawing Style Panel (collapsed by default) ────────────────
                html.Div(
                    id="draw-style-panel",
                    style={"display": "none", "flexDirection": "column", "gap": "0.5rem",
                           "background": "#161b22", "borderRadius": "4px",
                           "padding": "0.65rem 0.85rem", "marginBottom": "6px",
                           "border": "1px solid #30363d"},
                    children=[
                        html.Span("Drawing Style",
                                  style={"color": "#8b949e", "fontSize": "0.72rem",
                                         "fontWeight": 600, "letterSpacing": "0.05em"}),
                        # Thickness row
                        html.Div([
                            html.Span("Thickness",
                                      style={"color": "#8b949e", "fontSize": "0.72rem",
                                             "minWidth": "62px", "alignSelf": "center"}),
                            *[html.Button(
                                _DRAW_WIDTH_LABELS[w],
                                id={"type": "draw-width-btn", "index": w},
                                n_clicks=0, title=f"Line width {w}px",
                                style=_width_btn_style(w == 1),
                              ) for w in _DRAW_WIDTHS],
                        ], style={"display": "flex", "alignItems": "center", "gap": "0.35rem"}),
                        # Line style row
                        html.Div([
                            html.Span("Line Style",
                                      style={"color": "#8b949e", "fontSize": "0.72rem",
                                             "minWidth": "62px", "alignSelf": "center"}),
                            *[html.Button(
                                _DRAW_STYLE_LABELS[s],
                                id={"type": "draw-style-btn", "index": s},
                                n_clicks=0, title=s.capitalize(),
                                style=_style_btn_style(s == "solid"),
                              ) for s in _DRAW_STYLES],
                        ], style={"display": "flex", "alignItems": "center", "gap": "0.35rem"}),
                        # Color row
                        html.Div([
                            html.Span("Color",
                                      style={"color": "#8b949e", "fontSize": "0.72rem",
                                             "minWidth": "62px", "alignSelf": "center"}),
                            *[html.Button(
                                "",
                                id={"type": "draw-color-btn", "index": c},
                                n_clicks=0, title=c,
                                style=_swatch_style(c, c == _DRAW_DEFAULT_COLOR),
                              ) for c in _DRAW_COLORS],
                            # Custom colour picker
                            html.Div([
                                html.Span("Custom:", style={"color": "#8b949e",
                                           "fontSize": "0.7rem", "marginRight": "4px"}),
                                dcc.Input(
                                    id="draw-color-custom",
                                    type="color",
                                    value=_DRAW_DEFAULT_COLOR,
                                    style={"width": "32px", "height": "22px",
                                           "border": "1px solid #30363d",
                                           "borderRadius": "3px", "cursor": "pointer",
                                           "padding": "0", "background": "none"},
                                ),
                            ], style={"display": "flex", "alignItems": "center",
                                      "marginLeft": "0.35rem"}),
                        ], style={"display": "flex", "alignItems": "center",
                                  "gap": "0.3rem", "flexWrap": "wrap"}),
                    ],
                ),

                # ── MA Settings panel (collapsed by default) ──────────────────
                html.Div(
                    id="ma-settings-panel",
                    style={"display": "none", "flexDirection": "column", "gap": "0.4rem",
                           "background": "#161b22", "borderRadius": "4px",
                           "padding": "0.6rem 0.75rem", "marginBottom": "6px",
                           "border": "1px solid #30363d"},
                    children=[
                        html.Span("Moving Average Settings",
                                  style={"color": "#8b949e", "fontSize": "0.75rem",
                                         "fontWeight": 600}),
                        html.Div(id="ma-settings-panel-rows"),
                    ],
                ),

                # ── Auto-Shutdown Panel (collapsed by default) ────────────────
                html.Div(
                    id="shutdown-panel",
                    style={"display": "none", "flexDirection": "column", "gap": "0.4rem",
                           "background": "#161b22", "borderRadius": "4px",
                           "padding": "0.65rem 0.85rem", "marginBottom": "6px",
                           "border": "1px solid #3d1010"},
                    children=[
                        html.Span("Auto-Shutdown",
                                  style={"color": "#ff9900", "fontSize": "0.72rem",
                                         "fontWeight": 600, "letterSpacing": "0.05em"}),
                        html.Div([
                            html.Button("Disabled", id="shutdown-enable-btn", n_clicks=0,
                                        style={**_tf_btn_style(False), "minWidth": "74px"}),
                            html.Span("Shutdown at:",
                                      style={"color": "#8b949e", "fontSize": "0.72rem",
                                             "alignSelf": "center", "marginLeft": "0.5rem"}),
                            dcc.Input(
                                id="shutdown-time-input", type="time", value="16:00",
                                style={"background": "#21262d", "color": "#e6edf3",
                                       "border": "1px solid #30363d", "borderRadius": "3px",
                                       "padding": "0.2rem 0.4rem", "fontSize": "0.82rem",
                                       "width": "100px"},
                            ),
                            html.Span("Warn (min):",
                                      style={"color": "#8b949e", "fontSize": "0.72rem",
                                             "alignSelf": "center", "marginLeft": "0.5rem"}),
                            dcc.Input(
                                id="shutdown-warn-input", type="number", value=5,
                                min=1, max=60, step=1, debounce=True,
                                style={"width": "56px", "background": "#21262d",
                                       "color": "#e6edf3", "border": "1px solid #30363d",
                                       "borderRadius": "3px", "padding": "0.2rem 0.35rem",
                                       "fontSize": "0.82rem"},
                            ),
                        ], style={"display": "flex", "alignItems": "center", "gap": "0.35rem",
                                  "flexWrap": "wrap"}),
                        html.Div(id="shutdown-status",
                                 style={"color": "#ff9900", "fontSize": "0.72rem",
                                        "fontFamily": "monospace"}),
                    ],
                ),

                # Chart area — chart + floating trade panel overlay
                html.Div(
                    id="chart-area-wrapper",
                    style={"position": "relative"},
                    children=[

                        # TradingView chart
                        html.Div(id="tvlw-chart",
                                 style={"height": "680px", "width": "100%",
                                        "background": "#0d1117", "borderRadius": "4px"}),

                        # ── Floating Quick Trade Panel ─────────────────────
                        html.Div(
                            id="quick-trade-panel",
                            style={
                                "position": "absolute",
                                "top": "8px", "left": "8px",
                                "zIndex": "50",
                                "background": "rgba(22,27,34,0.90)",
                                "backdropFilter": "blur(6px)",
                                "WebkitBackdropFilter": "blur(6px)",
                                "border": "1px solid rgba(48,54,61,0.85)",
                                "borderRadius": "7px",
                                "padding": "0.45rem 0.6rem",
                                "minWidth": "200px",
                                "boxShadow": "0 4px 16px rgba(0,0,0,0.55)",
                                "userSelect": "none",
                            },
                            children=[
                                # Drag handle bar
                                html.Div([
                                    html.Span("⠿", **{"className": "apex-drag-handle"},
                                              style={"color": "#555", "fontSize": "15px",
                                                     "cursor": "grab", "lineHeight": "1",
                                                     "marginRight": "6px"}),
                                    html.Span("TRADE",
                                              style={"color": "#555", "fontSize": "0.62rem",
                                                     "fontWeight": 700,
                                                     "letterSpacing": "0.12em"}),
                                ], **{"className": "apex-drag-handle"},
                                   style={"display": "flex", "alignItems": "center",
                                          "marginBottom": "0.4rem", "cursor": "grab"}),

                                # BUY | Spread/Lot | SELL | result
                                html.Div([
                                    html.Button(
                                        "▼ SELL", id="quick-sell-btn", n_clicks=0,
                                        style={"background": "#3d0010", "color": "#ff3366",
                                               "border": "1px solid #ff3366", "borderRadius": "4px",
                                               "padding": "0.3rem 0.85rem", "cursor": "pointer",
                                               "fontSize": "0.82rem", "fontWeight": 700},
                                    ),
                                    # Center: spread + lot
                                    html.Div([
                                        html.Div([
                                            html.Span("SPREAD", style={
                                                "color": "#8b949e", "fontSize": "0.58rem",
                                                "letterSpacing": "0.08em", "fontWeight": 700,
                                                "display": "block", "textAlign": "center",
                                            }),
                                            html.Span(id="spread-display", children="—", style={
                                                "color": "#e6edf3",
                                                "fontFamily": "'JetBrains Mono', monospace",
                                                "fontSize": "0.8rem", "fontWeight": 700,
                                                "display": "block", "textAlign": "center",
                                            }),
                                        ]),
                                        html.Div([
                                            html.Span("Lot", style={
                                                "color": "#8b949e", "fontSize": "0.65rem",
                                                "marginRight": "0.25rem",
                                            }),
                                            dcc.Input(
                                                id="lot-size-input", type="number",
                                                value=0.01, min=0.001, step=0.001, debounce=False,
                                                style={
                                                    "width": "60px", "background": "#21262d",
                                                    "color": "#e6edf3",
                                                    "border": "1px solid #30363d",
                                                    "borderRadius": "3px",
                                                    "padding": "0.12rem 0.3rem",
                                                    "fontSize": "0.78rem",
                                                },
                                            ),
                                        ], style={"display": "flex", "alignItems": "center",
                                                  "justifyContent": "center",
                                                  "marginTop": "0.2rem"}),
                                    ], style={"padding": "0 0.6rem", "textAlign": "center"}),

                                    html.Button(
                                        "▲ BUY", id="quick-buy-btn", n_clicks=0,
                                        style={"background": "#003d1f", "color": "#00ff88",
                                               "border": "1px solid #00ff88", "borderRadius": "4px",
                                               "padding": "0.3rem 0.85rem", "cursor": "pointer",
                                               "fontSize": "0.82rem", "fontWeight": 700},
                                    ),
                                ], style={"display": "flex", "alignItems": "center",
                                          "marginBottom": "0.3rem"}),

                                # Trade result message
                                html.Div(id="quick-trade-result",
                                         style={"color": "#e6edf3", "fontSize": "0.72rem",
                                                "marginTop": "0.15rem", "lineHeight": "1.3"}),

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
        html.Div(id="alert-overlay-container", children=[
            html.Button(id="alert-cancel-btn",  style={"display": "none"}),
            html.Button(id="alert-confirm-btn", style={"display": "none"}),
        ]),

        # Intervals
        dcc.Interval(id="interval-1s",  interval=1_000,  n_intervals=0),
        dcc.Interval(id="interval-5s",  interval=5_000,  n_intervals=0),
        dcc.Interval(id="interval-60s", interval=60_000, n_intervals=0),

        # Stores
        dcc.Store(id="drawer-visible",    data=False),
        dcc.Store(id="selected-tf",       data="H1"),
        dcc.Store(id="alert-action",      data=None),
        dcc.Store(id="chart-data-store",  data=None),
        dcc.Store(id="draw-mode-store",   data=None),
        dcc.Store(id="draw-color-store",  data=_DRAW_DEFAULT_COLOR),
        dcc.Store(id="draw-width-store",  data=1),
        dcc.Store(id="draw-style-store",  data="solid"),
        dcc.Store(id="open-trades-store", data=[]),
        dcc.Store(id="shutdown-config",
                  data={"enabled": False, "time": "16:00", "warn_minutes": 5}),
        # EMA settings: current active settings for the visible chart
        dcc.Store(id="ema-settings-store", data=_ema_defaults()),
        # EMA settings persisted per pair+TF in browser localStorage
        dcc.Store(id="ema-per-pair-store", storage_type="local", data={}),
        # Order form: holds {direction, pair} while confirmation form is open
        dcc.Store(id="order-form-store", data=None),

        # Dummy targets for clientside callbacks
        html.Div(id="trades-chart-dummy", style={"display": "none"}),
        html.Div(id="chart-dummy",        style={"display": "none"}),
        html.Div(id="draw-dummy",        style={"display": "none"}),
        html.Div(id="draw-color-dummy",  style={"display": "none"}),
        html.Div(id="draw-width-dummy",  style={"display": "none"}),
        html.Div(id="draw-style-dummy",  style={"display": "none"}),
        html.Div(id="draw-lock-dummy",   style={"display": "none"}),
        html.Div(id="draw-dup-dummy",    style={"display": "none"}),
    ],
)


# ═══════════════════════════════════════════════════════════════════════════════
# Callbacks
# ═══════════════════════════════════════════════════════════════════════════════

_CANDLE_COUNT = {"H1": 4500, "H4": 1500, "D": 500}


# ── Chart data → store ────────────────────────────────────────────────────────

@app.callback(
    Output("chart-data-store", "data"),
    Input("interval-60s",        "n_intervals"),
    Input("pair-select",         "value"),
    Input("selected-tf",         "data"),
    Input("ema-settings-store",  "data"),
    prevent_initial_call=False,
)
def update_chart(n_intervals, pair, tf, ema_settings):
    if not pair or not tf:
        return {"candlestick": [], "emas": [], "cci": [], "macd": [],
                "pair": "", "tf": ""}

    state.update(chart_pair=pair, chart_tf=tf)

    ctx         = callback_context
    by_interval = bool(
        ctx.triggered and
        ctx.triggered[0]["prop_id"].split(".")[0] == "interval-60s"
    )

    is_forex  = "_" in pair
    connector = (state.get_key("forex_connector") if is_forex
                 else state.get_key("crypto_connector"))

    # Granularity: OANDA uses "H1"/"H4"/"D"; Alpaca uses "1Hour"/"4Hour"/"1Day"
    if is_forex:
        gran = tf                                     # "H1", "H4", "D"
    else:
        gran = {"H1": "1Hour", "H4": "4Hour", "D": "1Day"}.get(tf, "1Hour")

    df = state.get_candles(pair, tf)

    if df is None:
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

    result = build_chart_data(df, pair, tf=tf,
                              ema_periods=ema_periods,
                              ema_colors=ema_colors,
                              ema_widths=ema_widths)
    # Expose account balance so the position tool can compute position size in JS
    acct = state.get_key("account", {})
    result["accountBalance"] = acct.get("balance") or acct.get("cash", 500.0)

    # Open trades for this pair → chart draws Entry/SL/TP price lines
    all_trades = state.get_key("open_trades", []) or []
    result["open_trades"] = [
        {
            "direction": t.get("direction", "long"),
            "entry":     float(t.get("entry", 0) or 0),
            "sl":        float(t.get("sl", 0) or 0),
            "tp":        float(t.get("tp1", 0) or t.get("tp", 0) or 0),
        }
        for t in all_trades
        if t.get("pair") == pair and t.get("entry")
    ]
    return result


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
    Input("tf-btn-H1", "n_clicks"),
    Input("tf-btn-H4", "n_clicks"),
    Input("tf-btn-D",  "n_clicks"),
    prevent_initial_call=True,
)
def select_tf(h1, h4, d):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    return ctx.triggered[0]["prop_id"].split(".")[0].replace("tf-btn-", "")


@app.callback(
    Output("tf-btn-H1", "style"),
    Output("tf-btn-H4", "style"),
    Output("tf-btn-D",  "style"),
    Input("selected-tf", "data"),
)
def highlight_active_tf(tf):
    return (_tf_btn_style(tf == "H1"),
            _tf_btn_style(tf == "H4"),
            _tf_btn_style(tf == "D"))


# ── MA settings panel toggle ──────────────────────────────────────────────────

@app.callback(
    Output("ma-settings-panel", "style"),
    Output("ma-settings-btn",   "style"),
    Input("ma-settings-btn",    "n_clicks"),
    State("ma-settings-panel",  "style"),
    prevent_initial_call=True,
)
def toggle_ma_panel(_, ps):
    is_open  = (ps or {}).get("display") != "none"
    new_disp = "none" if is_open else "flex"
    return {**(ps or {}), "display": new_disp}, _tf_btn_style(not is_open)


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
        key      = f"{pair}|{tf}" if pair and tf else None
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

    # Persist under current pair+TF key
    key = f"{pair}|{tf}" if pair and tf else None
    if key:
        per_pair[key] = settings

    return settings, per_pair


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
        all_modes = _DRAW_TOOL_MODES + ["cancel"]
        return no_update, [_draw_btn_style(m, False) for m in all_modes]
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
            var deleted = window._apexDeleteSelected ? window._apexDeleteSelected() : false;
            if (!deleted) {
                // Nothing was selected — cancel current draw mode
                if (window._apexSetDrawMode) window._apexSetDrawMode(null);
            }
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


# ── Drawing colour ────────────────────────────────────────────────────────────

@app.callback(
    Output("draw-color-store", "data"),
    Output({"type": "draw-color-btn", "index": ALL}, "style"),
    Input( {"type": "draw-color-btn", "index": ALL}, "n_clicks"),
    State("draw-color-store", "data"),
    prevent_initial_call=True,
)
def select_draw_color(_clicks, current):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, [_swatch_style(c, c == current) for c in _DRAW_COLORS]
    try:
        color = json.loads(ctx.triggered[0]["prop_id"].split(".")[0]).get("index", current)
    except Exception:
        color = current
    return color, [_swatch_style(c, c == color) for c in _DRAW_COLORS]


@app.callback(
    Output("draw-color-store", "data", allow_duplicate=True),
    Input("draw-color-custom", "value"),
    prevent_initial_call=True,
)
def select_draw_color_custom(custom_val):
    if not custom_val:
        return no_update
    return custom_val


app.clientside_callback(
    "function(c){ if(window._apexSetDrawColor) window._apexSetDrawColor(c); return ''; }",
    Output("draw-color-dummy", "children"),
    Input("draw-color-store", "data"),
    prevent_initial_call=True,
)


# ── Drawing line width ────────────────────────────────────────────────────────

@app.callback(
    Output("draw-width-store", "data"),
    Output({"type": "draw-width-btn", "index": ALL}, "style"),
    Input( {"type": "draw-width-btn", "index": ALL}, "n_clicks"),
    State("draw-width-store", "data"),
    prevent_initial_call=True,
)
def select_draw_width(_, current):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, [_width_btn_style(w == current) for w in _DRAW_WIDTHS]
    w = int(json.loads(ctx.triggered[0]["prop_id"].split(".")[0]).get("index", current))
    return w, [_width_btn_style(ww == w) for ww in _DRAW_WIDTHS]


app.clientside_callback(
    "function(w){ if(window._apexSetDrawWidth) window._apexSetDrawWidth(w); return ''; }",
    Output("draw-width-dummy", "children"),
    Input("draw-width-store", "data"),
    prevent_initial_call=True,
)


# ── Drawing style panel toggle ────────────────────────────────────────────────

@app.callback(
    Output("draw-style-panel", "style"),
    Output("draw-style-panel-btn", "style"),
    Input("draw-style-panel-btn", "n_clicks"),
    State("draw-style-panel", "style"),
    prevent_initial_call=True,
)
def toggle_draw_style_panel(_, ps):
    is_open  = (ps or {}).get("display") != "none"
    new_disp = "none" if is_open else "flex"
    return {**(ps or {}), "display": new_disp}, _tf_btn_style(not is_open)


# ── Drawing line style (solid / dashed / dotted) ──────────────────────────────

@app.callback(
    Output("draw-style-store", "data"),
    Output({"type": "draw-style-btn", "index": ALL}, "style"),
    Input( {"type": "draw-style-btn", "index": ALL}, "n_clicks"),
    State("draw-style-store", "data"),
    prevent_initial_call=True,
)
def select_draw_style(_, current):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, [_style_btn_style(s == current) for s in _DRAW_STYLES]
    style = json.loads(ctx.triggered[0]["prop_id"].split(".")[0]).get("index", current)
    return style, [_style_btn_style(s == style) for s in _DRAW_STYLES]


app.clientside_callback(
    "function(s){ if(window._apexSetDrawStyle) window._apexSetDrawStyle(s); return ''; }",
    Output("draw-style-dummy", "children"),
    Input("draw-style-store", "data"),
    prevent_initial_call=True,
)


# ── Lock selected drawing ─────────────────────────────────────────────────────

app.clientside_callback(
    "function(n){ if(n && window._apexLockSelected) window._apexLockSelected(); return ''; }",
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
    Output("spread-display", "children"),
    Input("interval-5s",  "n_intervals"),
    Input("pair-select",  "value"),
)
def update_spread(_, pair):
    if not pair or "_" not in pair:
        return "—"
    connector = state.get_key("forex_connector")
    if connector is None:
        return "—"
    try:
        _bid, _ask, spread = connector.get_spread(pair)
        pip  = 0.01 if "JPY" in pair else 0.0001
        pips = round(spread / pip, 1)
        return f"{pips} pips"
    except Exception:
        return "—"


# ── Order form: open / close ──────────────────────────────────────────────────
# BUY or SELL clicked → store direction+pair; Cancel/Confirm → clear store.

@app.callback(
    Output("order-form-store", "data"),
    Input("quick-buy-btn",     "n_clicks"),
    Input("quick-sell-btn",    "n_clicks"),
    Input("order-cancel-btn",  "n_clicks"),
    Input("order-confirm-btn", "n_clicks"),
    State("pair-select", "value"),
    prevent_initial_call=True,
)
def set_order_form(buy_n, sell_n, cancel_n, confirm_n, pair):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    triggered = ctx.triggered[0]["prop_id"].split(".")[0]
    if triggered in ("order-cancel-btn", "order-confirm-btn"):
        return None
    if not pair:
        return no_update
    direction = "long" if triggered == "quick-buy-btn" else "short"
    return {"direction": direction, "pair": pair}


# ── Order form: populate fields when store changes ────────────────────────────

@app.callback(
    Output("order-form-panel",  "style"),
    Output("order-form-title",  "children"),
    Output("order-form-title",  "style"),
    Output("order-entry-input", "value"),
    Output("order-sl-input",    "value"),
    Output("order-tp1-input",   "value"),
    Output("order-tp2-input",   "value"),
    Output("order-tp3-input",   "value"),
    Output("order-lot-input",   "value"),
    Output("order-confirm-btn", "style"),
    Input("order-form-store",   "data"),
    State("lot-size-input",     "value"),
    prevent_initial_call=True,
)
def populate_order_form(form_data, default_lot):
    _hidden = {"display": "none"}
    _no = no_update

    if not form_data:
        return _hidden, _no, _no, _no, _no, _no, _no, _no, _no, _no

    direction = form_data["direction"]
    pair      = form_data["pair"]
    is_forex  = "_" in pair
    connector = state.get_key("forex_connector" if is_forex else "crypto_connector")

    entry_val = sl_val = tp1_val = tp2_val = tp3_val = None
    lot_val = default_lot or 0.01

    if connector:
        try:
            gran = "H1" if is_forex else "1Hour"
            df   = connector.get_candles(pair, gran, 50)
            if df is not None and not df.empty:
                entry_val = round(float(df["close"].iloc[-1]), 5)
                try:
                    from engine.indicators import atr as _atr
                    atr_val = float(_atr(df["high"], df["low"], df["close"], 14).iloc[-1])
                    sl_dist = atr_val * 1.5
                except Exception:
                    sl_dist = 0.005 if is_forex else entry_val * 0.015
                sl_val = round(
                    entry_val - sl_dist if direction == "long" else entry_val + sl_dist, 5
                )
                from risk.risk_manager import get_tp_levels, calculate_position_size
                tp       = get_tp_levels(entry_val, sl_val, direction)
                tp1_val  = round(tp["tp1"], 5)
                tp2_val  = round(tp["tp2"], 5)
                tp3_val  = round(tp["tp3"], 5)
                if not default_lot:
                    account = state.get_key("account", {})
                    balance = account.get("balance") or account.get("cash", 500.0)
                    inst    = "forex" if is_forex else "crypto"
                    lot_val = calculate_position_size(balance, entry_val, sl_val, inst, pair)
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

    return (panel_style, f"{arrow}  {pair}", title_style,
            entry_val, sl_val, tp1_val, tp2_val, tp3_val, lot_val, confirm_style)


# ── Order form: confirm → place trade ────────────────────────────────────────

@app.callback(
    Output("quick-trade-result", "children"),
    Output("quick-trade-result", "style"),
    Input("order-confirm-btn",  "n_clicks"),
    State("order-form-store",   "data"),
    State("order-entry-input",  "value"),
    State("order-sl-input",     "value"),
    State("order-tp1-input",    "value"),
    State("order-tp2-input",    "value"),
    State("order-tp3-input",    "value"),
    State("order-lot-input",    "value"),
    prevent_initial_call=True,
)
def confirm_order(n, form_data, entry, sl, tp1, tp2, tp3, lot):
    if not n or not form_data:
        return no_update, no_update

    direction = form_data["direction"]
    pair      = form_data["pair"]

    if any(v is None for v in (entry, sl, tp1, lot)):
        return ("✗ Fill in Entry, SL, TP1 and Lot Size",
                {"color": "#ff3366", "fontSize": "0.78rem"})

    entry = float(entry)
    sl    = float(sl)
    tp1   = float(tp1)
    tp2   = float(tp2) if tp2 is not None else entry
    tp3   = float(tp3) if tp3 is not None else entry
    lot   = float(lot)

    paper_trader = state.get_key("paper_trader")
    if paper_trader is None:
        return ("✗ Paper trader unavailable (live mode: use broker)",
                {"color": "#ff3366", "fontSize": "0.78rem"})

    trade_id = paper_trader.open_trade(
        pair=pair, direction=direction,
        entry_price=entry, sl=sl,
        tp_levels={"tp1": tp1, "tp2": tp2, "tp3": tp3},
        size=lot,
    )
    state.update(
        open_trades   = list(paper_trader.open_trades),
        closed_trades = list(paper_trader.closed_trades),
    )

    if not trade_id:
        return ("✗ Rejected (duplicate pair or max trades)",
                {"color": "#ff3366", "fontSize": "0.78rem"})

    is_forex = "_" in pair
    pip      = 0.01 if "JPY" in pair else 0.0001
    sl_pips  = round(abs(entry - sl) / pip)
    arrow    = "▲ LONG" if direction == "long" else "▼ SHORT"
    dec      = 3 if "JPY" in pair else 5
    clr      = "#00ff88" if direction == "long" else "#ff3366"
    msg      = (f"{arrow}  {pair}  @{entry:.{dec}f}  "
                f"SL {sl:.{dec}f} ({sl_pips}p)  TP1 {tp1:.{dec}f}  Lot {lot}")
    return msg, {"color": clr, "fontSize": "0.78rem"}


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
            "direction": t.get("direction", "long"),
            "entry":     float(t.get("entry", 0) or 0),
            "sl":        float(t.get("sl", 0) or 0),
            "tp":        float(t.get("tp1", 0) or t.get("tp", 0) or 0),
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
        _conn_pill("OANDA",  state.get_key("oanda_ok")),
        _conn_pill("ALPACA", state.get_key("alpaca_ok")),
    ]


# ── Signal monitor ────────────────────────────────────────────────────────────

@app.callback(
    Output("signal-monitor", "children"),
    Input("interval-60s",    "n_intervals"),
)
def update_signals(_):
    return signal_monitor_panel(state.get_key("signals", {}))


# ── Open trades + account stats ───────────────────────────────────────────────

@app.callback(
    Output("open-trades",   "children"),
    Output("account-stats", "children"),
    Input("interval-5s",    "n_intervals"),
)
def update_trades_and_account(_):
    s       = state.get()
    trades  = s.get("open_trades",   [])
    account = s.get("account",       {})
    closed  = s.get("closed_trades", [])
    mode    = s.get("mode",          "paper")

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

    return (open_trades_panel(trades, mode),
            account_stats_bar(account, daily_pnl, wr_all, wr_20, t_today, mode))


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
        state.update(alert_confirmed=True); state.clear_alert()
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
            {"height": "80vh", "width": "100%", "background": "#0d1117",
             "borderRadius": "4px"},
        )
    return (
        {"flex": "0 0 75%", "paddingLeft": "0.25rem", "minWidth": 0},
        {"flex": "0 0 25%", "overflowY": "auto",
         "maxHeight": "760px", "paddingRight": "0.5rem", "minWidth": 0},
        {"height": "680px", "width": "100%", "background": "#0d1117",
         "borderRadius": "4px"},
    )


def _hidden_alert_btns():
    return [
        html.Button(id="alert-cancel-btn",  style={"display": "none"}),
        html.Button(id="alert-confirm-btn", style={"display": "none"}),
    ]


@app.callback(
    Output("drawer-visible",   "data"),
    Output("trade-log-drawer", "children"),
    Input("drawer-toggle-btn", "n_clicks"),
    Input("drawer-close-btn",  "n_clicks"),
    State("drawer-visible",    "data"),
    prevent_initial_call=True,
)
def toggle_drawer(open_clicks, close_clicks, visible):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update
    new_vis = not visible if "toggle" in ctx.triggered[0]["prop_id"] else False
    s = state.get()
    return new_vis, trade_log_drawer(
        s.get("closed_trades", []), s.get("suggestions", []),
        s.get("ml_stats", {}), visible=new_vis,
    )


@app.callback(
    Output("pair-select", "value"),
    Input({"type": "signal-row", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def select_pair_from_signal(n_clicks_list):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    # Guard: n_clicks = 0 or None means signal-monitor was re-rendered (not a real click)
    if not ctx.triggered[0].get("value"):
        return no_update
    return json.loads(
        ctx.triggered[0]["prop_id"].split(".")[0]
    ).get("index", no_update)


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

    if config.ALPACA_API_KEY and config.ALPACA_SECRET:
        try:
            from connectors.alpaca_connector import AlpacaConnector
            state.update(crypto_connector=AlpacaConnector())
            logger.info("Standalone: Alpaca connector ready")
        except Exception as exc:
            logger.warning("Standalone: Alpaca connector failed — %s", exc)

    app.run(debug=True, port=8050)
