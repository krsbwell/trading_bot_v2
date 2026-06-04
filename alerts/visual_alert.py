"""
Builds the pre-trade alert overlay as a Dash component tree.
Imported by dashboard/app.py — no Dash state held here.
"""
from dash import html


# ── Public API ────────────────────────────────────────────────────────────────

def build_alert_overlay(signal: dict, countdown: int) -> html.Div:
    """
    Return the full pre-trade overlay component.

    signal keys used:
        pair, direction, entry, stop_loss, tp_levels,
        size, risk_dollar, score, ema_score, structure_score,
        pa_score, patterns
    countdown: seconds remaining before auto-execute (0 = firing now)
    """
    direction  = signal.get("direction", "long")
    pair       = signal.get("pair", "—")
    is_long    = direction == "long"
    dir_label  = "BUY" if is_long else "SELL"
    dir_color  = "#00ff88" if is_long else "#ff3366"

    entry      = signal.get("entry", 0)
    sl         = signal.get("stop_loss", 0)
    tp         = signal.get("tp_levels", {})
    size       = signal.get("size", 0)
    risk_usd   = signal.get("risk_dollar", 0)
    score      = signal.get("score", 0)
    ema_s      = signal.get("ema_score", 0)
    struct_s   = signal.get("structure_score", 0)
    pa_s       = signal.get("pa_score", 0)
    patterns   = signal.get("patterns", [])

    rr = lambda tp_price: abs(tp_price - entry) / max(abs(entry - sl), 1e-8)

    overlay_style = {
        "position": "fixed", "top": 0, "left": 0, "right": 0, "bottom": 0,
        "backgroundColor": "rgba(0,0,0,0.75)",
        "display": "flex", "alignItems": "center", "justifyContent": "center",
        "zIndex": 9999,
    }
    card_style = {
        "backgroundColor": "#161b22",
        "border": f"2px solid {dir_color}",
        "borderRadius": "8px",
        "padding": "2rem",
        "minWidth": "480px",
        "maxWidth": "560px",
        "fontFamily": "'IBM Plex Sans', sans-serif",
        "color": "#e6edf3",
    }
    mono = {"fontFamily": "'JetBrains Mono', monospace"}

    return html.Div(
        id="alert-overlay",
        style=overlay_style,
        children=[
            html.Div(style=card_style, children=[

                # ── Header ────────────────────────────────────────────────
                html.Div([
                    html.Span("TRADE SIGNAL DETECTED",
                              style={"color": "#ffd700", "fontSize": "0.75rem",
                                     "letterSpacing": "0.12em", "fontWeight": 600}),
                    html.H2(f"{pair}  {dir_label}  H1+H4",
                            style={"color": dir_color, "margin": "0.25rem 0 1rem"}),
                ]),

                # ── Price table ───────────────────────────────────────────
                html.Table(style={"width": "100%", "borderCollapse": "collapse",
                                  "marginBottom": "1rem", **mono}, children=[
                    _trow("Entry",  f"{entry:.5f}",  "Risk",  f"${risk_usd:.2f} (1%)"),
                    _trow("Stop",   f"{sl:.5f}",     "Size",  f"{size:,.0f} units"),
                    _trow(f"TP1 1:{rr(tp.get('tp1',entry)):.1f}",
                          f"{tp.get('tp1', 0):.5f}", "close 40%", ""),
                    _trow(f"TP2 1:{rr(tp.get('tp2',entry)):.1f}",
                          f"{tp.get('tp2', 0):.5f}", "close 35%", ""),
                    _trow(f"TP3 1:{rr(tp.get('tp3',entry)):.1f}",
                          f"{tp.get('tp3', 0):.5f}", "close 25%", ""),
                ]),

                # ── Score bar ─────────────────────────────────────────────
                html.Div([
                    html.Span(f"Score: {score}/100",
                              style={"fontWeight": 700, "color": "#ffd700", **mono}),
                    html.Span(f"  [EMA:{ema_s:.0f} | Structure:{struct_s:.0f} | PA:{pa_s:.0f}]",
                              style={"color": "#7d8590", "fontSize": "0.85rem", **mono}),
                ], style={"marginBottom": "0.5rem"}),

                # ── Patterns ──────────────────────────────────────────────
                html.Div(
                    "Pattern: " + (", ".join(p.replace("_", " ").title() for p in patterns)
                                   if patterns else "None"),
                    style={"color": "#7d8590", "fontSize": "0.85rem",
                           "marginBottom": "1.5rem"},
                ),

                # ── Countdown ─────────────────────────────────────────────
                html.Div([
                    html.Span("Executing in: ", style={"color": "#7d8590"}),
                    html.Span(
                        f"[ {countdown} ]" if countdown > 0 else "[ NOW ]",
                        id="alert-countdown-display",
                        style={"color": "#ffd700", "fontWeight": 700,
                               "fontSize": "1.4rem", **mono},
                    ),
                    html.Span(" seconds", style={"color": "#7d8590"}),
                ], style={"textAlign": "center", "marginBottom": "1.5rem"}),

                # ── Buttons ───────────────────────────────────────────────
                html.Div([
                    html.Button(
                        "✕  CANCEL TRADE",
                        id="alert-cancel-btn",
                        style={
                            "backgroundColor": "#ff3366", "color": "#fff",
                            "border": "none", "borderRadius": "4px",
                            "padding": "0.75rem 1.5rem", "cursor": "pointer",
                            "fontSize": "0.95rem", "fontWeight": 700,
                            "flex": 1, "marginRight": "0.5rem",
                        },
                    ),
                    html.Button(
                        "✓  CONFIRM NOW",
                        id="alert-confirm-btn",
                        style={
                            "backgroundColor": "#00ff88", "color": "#0d1117",
                            "border": "none", "borderRadius": "4px",
                            "padding": "0.75rem 1.5rem", "cursor": "pointer",
                            "fontSize": "0.95rem", "fontWeight": 700,
                            "flex": 1,
                        },
                    ),
                ], style={"display": "flex"}),

            ])
        ],
    )


def border_pulse_class(direction: str) -> str:
    """Return CSS class name for the pulsing root border."""
    return "pulse-long" if direction == "long" else "pulse-short"


# ── Helper ────────────────────────────────────────────────────────────────────

def _trow(label1, val1, label2, val2):
    cell = {"padding": "0.2rem 0.5rem", "color": "#e6edf3"}
    lbl  = {"padding": "0.2rem 0.5rem", "color": "#7d8590", "fontSize": "0.85rem"}
    return html.Tr([
        html.Td(label1, style=lbl), html.Td(val1, style=cell),
        html.Td(label2, style=lbl), html.Td(val2, style=cell),
    ])
