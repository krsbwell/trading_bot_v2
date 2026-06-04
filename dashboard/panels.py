"""
Dash component builders for each of the 5 dashboard panels.
All functions take plain data (dicts / lists) and return html/dcc components.
"""
from dash import html, dcc
import plotly.graph_objects as go

# ── Colour constants ──────────────────────────────────────────────────────────
_BULL  = "#00ff88"
_BEAR  = "#ff3366"
_GOLD  = "#ffd700"
_PANEL = "#161b22"
_CARD  = "#21262d"
_TEXT  = "#e6edf3"
_MUTED = "#7d8590"
_BG    = "#0d1117"

_mono = {"fontFamily": "'JetBrains Mono', monospace"}
_sans = {"fontFamily": "'IBM Plex Sans', sans-serif"}


# ═══════════════════════════════════════════════════════════════════════════════
# Panel 2 — Signal Monitor
# ═══════════════════════════════════════════════════════════════════════════════

def signal_monitor_panel(signals: dict) -> html.Div:
    """
    signals: {pair: {"score": int, "direction": str, "status": str}}
    """
    rows = []
    for pair, info in sorted(signals.items(), key=lambda x: x[1]["score"], reverse=True):
        score     = info.get("score", 0)
        direction = info.get("direction", "—")
        status    = info.get("status", "NEUTRAL")

        bar_color = _BULL if score >= 70 else (_GOLD if score >= 50 else "#3d444d")
        arrow     = "▲" if direction == "long" else ("▼" if direction == "short" else "—")
        arrow_col = _BULL if direction == "long" else (_BEAR if direction == "short" else _MUTED)
        badge_col = (_BULL   if status == "SIGNAL"   else
                     _GOLD   if status == "WATCHING" else
                     "#38b6ff" if status == "SCANNING" else _MUTED)

        rows.append(html.Tr([
            html.Td(pair, style={"color": _TEXT, "padding": "0.4rem 0.5rem",
                                  "fontSize": "0.85rem", "cursor": "pointer",
                                  **_mono}),
            html.Td([
                html.Div(style={"background": bar_color, "height": "6px",
                                 "borderRadius": "3px",
                                 "width": f"{min(score, 100)}%",
                                 "minWidth": "4px"}),
                html.Span(f" {score}", style={"color": _MUTED, "fontSize": "0.75rem",
                                               "marginLeft": "4px", **_mono}),
            ], style={"padding": "0.4rem 0.5rem", "width": "140px"}),
            html.Td(arrow, style={"color": arrow_col, "fontSize": "1.1rem",
                                   "padding": "0.4rem 0.5rem", "textAlign": "center"}),
            html.Td(html.Span(status, style={"color": badge_col,
                                              "fontSize": "0.7rem",
                                              "fontWeight": 700}),
                    style={"padding": "0.4rem 0.5rem"}),
        ], id={"type": "signal-row", "index": pair},
            style={"cursor": "pointer",
                   "borderBottom": "1px solid #21262d"}))

    return html.Div([
        html.H4("Signal Monitor", style={"color": _TEXT, "margin": "0 0 0.75rem",
                                          **_sans}),
        html.Table(rows, style={"width": "100%", "borderCollapse": "collapse"}),
    ], style=_panel_style())


# ═══════════════════════════════════════════════════════════════════════════════
# Panel 3 — Open Trades
# ═══════════════════════════════════════════════════════════════════════════════

def open_trades_panel(trades: list, mode: str, current_prices: dict = None) -> html.Div:
    """
    trades       : list of trade dicts
    mode         : "paper" | "live"
    current_prices : {pair: price} for live PnL
    """
    current_prices = current_prices or {}
    badge_color    = _GOLD if mode == "paper" else _BULL
    badge_label    = "PAPER MODE" if mode == "paper" else "LIVE"

    cards = []
    for t in trades:
        pair      = t.get("pair", "—")
        direction = t.get("direction", "long")
        entry     = t.get("entry", 0)
        pnl       = t.get("realised_pnl", 0) + t.get("unrealised", 0)
        tp1       = t.get("tp1", entry)
        tp3       = t.get("tp3", entry)
        remaining = t.get("remaining", 1.0)
        pnl_col   = _BULL if pnl >= 0 else _BEAR
        dir_col   = _BULL if direction == "long" else _BEAR

        # Progress toward TP: % of distance from entry to tp3 covered
        price = current_prices.get(pair, entry)
        if direction == "long":
            progress = max(0, min(100, (price - entry) / max(tp3 - entry, 1e-8) * 100))
        else:
            progress = max(0, min(100, (entry - price) / max(entry - tp3, 1e-8) * 100))

        cards.append(html.Div([
            html.Div([
                html.Span(pair, style={"color": _TEXT, "fontWeight": 600, **_mono}),
                html.Span(f"  {direction.upper()}",
                          style={"color": dir_col, "fontSize": "0.8rem", "marginLeft": "6px"}),
                html.Span(f"  Entry: {entry:.5f}",
                          style={"color": _MUTED, "fontSize": "0.8rem", "marginLeft": "8px",
                                 **_mono}),
                html.Span(f"  {remaining*100:.0f}% open",
                          style={"color": _MUTED, "fontSize": "0.75rem", "marginLeft": "8px"}),
            ], style={"display": "flex", "alignItems": "center", "marginBottom": "0.4rem"}),

            html.Div([
                html.Span(f"P&L: {pnl:+.2f}", style={"color": pnl_col, "fontWeight": 700,
                                                        **_mono}),
                html.Div(style={
                    "flex": 1, "height": "6px", "background": "#3d444d",
                    "borderRadius": "3px", "margin": "0 0.75rem",
                    "overflow": "hidden",
                }, children=[
                    html.Div(style={
                        "width": f"{progress:.1f}%", "height": "100%",
                        "background": _BULL, "borderRadius": "3px",
                        "transition": "width 0.5s ease",
                    })
                ]),
                html.Button("CLOSE", id={"type": "close-trade-btn", "index": t.get("id", "")},
                            style={"background": _BEAR, "color": "#fff",
                                   "border": "none", "borderRadius": "3px",
                                   "padding": "0.2rem 0.6rem", "cursor": "pointer",
                                   "fontSize": "0.75rem"}),
            ], style={"display": "flex", "alignItems": "center"}),

        ], style={"background": _CARD, "borderRadius": "4px",
                  "padding": "0.6rem 0.75rem", "marginBottom": "0.4rem"}))

    if not cards:
        cards = [html.Div("No open trades", style={"color": _MUTED, "textAlign": "center",
                                                     "padding": "1rem"})]

    return html.Div([
        html.Div([
            html.H4("Open Trades", style={"color": _TEXT, "margin": 0, **_sans}),
            html.Span(badge_label, style={"color": badge_color, "fontSize": "0.7rem",
                                           "fontWeight": 700, "marginLeft": "0.75rem",
                                           "border": f"1px solid {badge_color}",
                                           "borderRadius": "3px", "padding": "1px 6px"}),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "0.75rem"}),
        html.Div(cards),
    ], style=_panel_style())


# ═══════════════════════════════════════════════════════════════════════════════
# Panel 4 — Account Stats Bar
# ═══════════════════════════════════════════════════════════════════════════════

def account_stats_bar(account: dict, daily_pnl: float, win_rate_all: float,
                       win_rate_20: float, trades_today: int, mode: str) -> html.Div:
    balance    = account.get("balance", 0)
    nav        = account.get("nav", balance)
    open_count = account.get("open_trade_count", 0)
    unreal     = account.get("unrealized_pnl", 0)

    # Drawdown as % of session balance
    drawdown_pct = max(0, -daily_pnl / max(balance, 1)) * 100
    draw_color   = (_BEAR if drawdown_pct >= 2.5 else
                    (_GOLD if drawdown_pct >= 1.5 else _BULL))

    dpnl_col = _BULL if daily_pnl >= 0 else _BEAR
    mode_col = _GOLD if mode == "paper" else _BULL

    stats = [
        _stat("Balance",    f"${balance:,.2f}"),
        _stat("NAV",        f"${nav:,.2f}"),
        _stat("Daily P&L",  f"${daily_pnl:+,.2f}", dpnl_col),
        _stat("Unrealised", f"${unreal:+,.2f}", _BULL if unreal >= 0 else _BEAR),
        _stat("Win Rate",   f"{win_rate_all:.0%} / {win_rate_20:.0%}",
              _BULL if win_rate_all >= 0.50 else _BEAR),
        _stat("Trades",     f"{trades_today} today / {open_count} open"),
        # Drawdown meter
        html.Div([
            html.Span("Drawdown", style={"color": _MUTED, "fontSize": "0.75rem",
                                          "display": "block", "marginBottom": "2px"}),
            html.Div(style={"background": "#21262d", "height": "8px",
                             "borderRadius": "4px", "width": "100px",
                             "overflow": "hidden"},
                     children=[html.Div(style={
                         "width": f"{min(drawdown_pct / 3 * 100, 100):.1f}%",
                         "height": "100%", "background": draw_color,
                         "borderRadius": "4px", "transition": "width 0.5s",
                     })]),
            html.Span(f"{drawdown_pct:.1f}%", style={"color": draw_color,
                                                       "fontSize": "0.75rem", **_mono}),
        ], style={"padding": "0 1rem"}),
        # Mode badge
        html.Div([
            html.Span(mode.upper(), style={
                "color": mode_col, "fontWeight": 700,
                "border": f"1px solid {mode_col}", "borderRadius": "3px",
                "padding": "2px 8px", "fontSize": "0.75rem",
            }),
        ], style={"padding": "0 0.5rem"}),
    ]

    return html.Div(stats, style={
        "display": "flex", "alignItems": "center", "flexWrap": "wrap",
        "background": _PANEL, "borderTop": "1px solid #21262d",
        "padding": "0.6rem 1rem", "gap": "0.5rem",
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Panel 5 — Trade Log Drawer
# ═══════════════════════════════════════════════════════════════════════════════

def trade_log_drawer(
    closed_trades: list,
    suggestions: list,
    ml_stats: dict,
    visible: bool = False,
) -> html.Div:
    trades_content = _trade_table(closed_trades)
    suggestion_cards = _suggestion_cards(suggestions)
    ml_content = _ml_block(ml_stats)

    return html.Div([
        html.Div([
            html.H4("Trade Log", style={"color": _TEXT, "margin": 0, **_sans}),
            html.Button("✕", id="drawer-close-btn",
                        style={"background": "transparent", "border": "none",
                               "color": _MUTED, "cursor": "pointer",
                               "fontSize": "1.2rem", "marginLeft": "auto"}),
        ], style={"display": "flex", "alignItems": "center",
                  "marginBottom": "1rem", "borderBottom": "1px solid #21262d",
                  "paddingBottom": "0.5rem"}),

        html.Div([trades_content], style={"marginBottom": "1.5rem"}),
        html.Div([ml_content],    style={"marginBottom": "1.5rem"}),
        html.Div(suggestion_cards),
    ], style={
        "position": "fixed", "top": 0, "right": 0, "bottom": 0,
        "width": "420px", "background": _PANEL,
        "boxShadow": "-4px 0 20px rgba(0,0,0,0.5)",
        "padding": "1.5rem", "overflowY": "auto",
        "zIndex": 1000, "transform": "translateX(0)" if visible else "translateX(100%)",
        "transition": "transform 0.3s ease",
    })


# ── Sub-builders ─────────────────────────────────────────────────────────────

def _trade_table(trades: list) -> html.Table:
    if not trades:
        return html.Div("No closed trades yet.", style={"color": _MUTED})

    header = html.Tr([
        html.Th(h, style={"color": _MUTED, "padding": "0.3rem 0.4rem",
                           "fontSize": "0.75rem", "fontWeight": 600,
                           "textAlign": "left", "borderBottom": "1px solid #21262d"})
        for h in ["Pair", "Dir", "Entry", "Exit", "P&L", "Pattern", "Score", "Outcome"]
    ])
    rows = []
    for t in trades[-50:]:    # show last 50
        pnl    = t.get("realised_pnl", 0)
        pnl_c  = _BULL if pnl >= 0 else _BEAR
        exit_p = t.get("exit_price", "—")
        rows.append(html.Tr([
            html.Td(t.get("pair", "—"),
                    style={"padding": "0.25rem 0.4rem", "fontSize": "0.8rem",
                           "color": _TEXT, **_mono}),
            html.Td(t.get("direction", "—")[:1].upper(),
                    style={"color": _BULL if t.get("direction") == "long" else _BEAR,
                           "padding": "0.25rem 0.4rem", "fontSize": "0.8rem"}),
            html.Td(f"{t.get('entry',0):.4f}",
                    style={"padding": "0.25rem 0.4rem", "fontSize": "0.75rem",
                           "color": _MUTED, **_mono}),
            html.Td(f"{exit_p:.4f}" if isinstance(exit_p, float) else str(exit_p),
                    style={"padding": "0.25rem 0.4rem", "fontSize": "0.75rem",
                           "color": _MUTED, **_mono}),
            html.Td(f"${pnl:+.2f}", style={"color": pnl_c, "padding": "0.25rem 0.4rem",
                                             "fontSize": "0.8rem", **_mono}),
            html.Td((t.get("pattern_name") or "—")[:16],
                    style={"padding": "0.25rem 0.4rem", "fontSize": "0.75rem",
                           "color": _MUTED}),
            html.Td(str(t.get("score", "—")),
                    style={"padding": "0.25rem 0.4rem", "fontSize": "0.75rem",
                           "color": _MUTED, **_mono}),
            html.Td(t.get("close_reason", "—"),
                    style={"padding": "0.25rem 0.4rem", "fontSize": "0.75rem",
                           "color": _BULL if (t.get("close_reason","")
                                              in ("tp1","tp2","tp3")) else _BEAR}),
        ], style={"borderBottom": "1px solid #21262d"}))

    return html.Table([header] + rows,
                      style={"width": "100%", "borderCollapse": "collapse"})


def _suggestion_cards(suggestions: list) -> list:
    if not suggestions:
        return [html.Div("No suggestions yet — keep trading!",
                         style={"color": _MUTED, "fontSize": "0.85rem"})]
    colors = {"boost": _BULL, "warning": _BEAR, "optimize": _GOLD}
    icons  = {"boost": "↑", "warning": "⚠", "optimize": "⚙"}
    cards  = []
    for s in suggestions:
        c = colors.get(s["type"], _MUTED)
        i = icons.get(s["type"], "•")
        cards.append(html.Div([
            html.Span(f"{i} ", style={"color": c}),
            html.Span(s["text"], style={"color": _TEXT, "fontSize": "0.85rem"}),
        ], style={
            "background": _CARD, "borderLeft": f"3px solid {c}",
            "borderRadius": "0 4px 4px 0", "padding": "0.6rem 0.75rem",
            "marginBottom": "0.4rem",
        }))
    return cards


def _ml_block(ml_stats: dict) -> html.Div:
    acc     = ml_stats.get("accuracy")
    feats   = ml_stats.get("top_features", [])
    n       = ml_stats.get("n_samples", 0)

    if acc is None:
        return html.Div([
            html.H5("ML Engine", style={"color": _TEXT, "margin": "0 0 0.4rem", **_sans}),
            html.Div(f"Training begins after {n}/50 closed trades",
                     style={"color": _MUTED, "fontSize": "0.85rem"}),
        ])

    return html.Div([
        html.H5("ML Engine", style={"color": _TEXT, "margin": "0 0 0.4rem", **_sans}),
        html.Div(f"Accuracy: {acc:.1%}   |   {n} trades",
                 style={"color": _BULL, "fontSize": "0.85rem", **_mono,
                        "marginBottom": "0.3rem"}),
        html.Div("Top features: " + " > ".join(feats[:3]),
                 style={"color": _MUTED, "fontSize": "0.8rem"}),
    ])


# ── Utilities ────────────────────────────────────────────────────────────────

def _stat(label: str, value: str, value_color: str = _TEXT) -> html.Div:
    return html.Div([
        html.Span(label, style={"color": _MUTED, "fontSize": "0.72rem",
                                  "display": "block"}),
        html.Span(value, style={"color": value_color, "fontWeight": 600,
                                  "fontSize": "0.9rem", **_mono}),
    ], style={"padding": "0 0.75rem", "borderRight": "1px solid #21262d"})


def _panel_style() -> dict:
    return {
        "background": _PANEL, "borderRadius": "6px",
        "padding": "1rem", "border": "1px solid #21262d",
        "marginBottom": "0.75rem",
    }
