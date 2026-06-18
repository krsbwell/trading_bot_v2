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

def _trend_dot(trend: str) -> html.Span:
    """Small coloured dot for TF trend: bull=green, bear=red, neutral=grey."""
    col = _BULL if trend == "bull" else (_BEAR if trend == "bear" else "#3d444d")
    return html.Span("●", style={"color": col, "fontSize": "0.7rem"})


def signal_monitor_panel(signals: dict, signal_details: dict = None) -> html.Div:
    """
    signals        : {pair: {"score": int, "direction": str, "status": str}}
    signal_details : {pair: full signal dict with sub-scores, h4_gate, trends}
    """
    import config as _cfg
    _watch = set(getattr(_cfg, "FOREX_WATCH", []))
    details = signal_details or {}
    rows = []

    for pair, info in sorted(signals.items(), key=lambda x: x[1]["score"], reverse=True):
        is_watch = pair in _watch
        score     = info.get("score", 0)
        direction = info.get("direction", "—")
        status    = info.get("status", "NEUTRAL")
        # TF from signal_details takes priority (engine sets it); fallback to "H1"
        tf        = (details.get(pair) or {}).get("timeframe", info.get("timeframe", "H1"))

        bar_color = _BULL if score >= 70 else (_GOLD if score >= 50 else "#3d444d")
        arrow     = "▲" if direction == "long" else ("▼" if direction == "short" else "—")
        arrow_col = _BULL if direction == "long" else (_BEAR if direction == "short" else _MUTED)
        badge_col = (_BULL     if status == "SIGNAL"   else
                     _GOLD     if status == "WATCHING" else
                     "#38b6ff" if status == "SCANNING" else _MUTED)

        # ── Diagnostic row from stored signal detail ─────────────────────────
        det       = details.get(pair, {})
        ema_s     = det.get("ema_score",       0)
        str_s     = det.get("structure_score", 0)
        pa_s      = det.get("pa_score",        0)
        h4_gate   = det.get("h4_gate",   {}) or {}
        h4_passed = h4_gate.get("passed")
        h4_trend  = det.get("h4_trend",  "neutral")
        d_trend   = det.get("d_trend",   "neutral")

        gate_col  = (_BULL if h4_passed is True  else
                     _BEAR if h4_passed is False else _MUTED)
        gate_lbl  = ("H4✓" if h4_passed is True  else
                     "H4✗" if h4_passed is False else "H4?")

        diag_row = html.Tr([
            html.Td([
                # Sub-scores
                html.Span(f"EMA:{ema_s:.0f} ",
                          style={"color": _GOLD if ema_s > 0 else _MUTED,
                                 "fontSize": "0.65rem", **_mono}),
                html.Span(f"STR:{str_s:.0f} ",
                          style={"color": "#38b6ff" if str_s > 0 else _MUTED,
                                 "fontSize": "0.65rem", **_mono}),
                html.Span(f"PA:{pa_s:.0f}",
                          style={"color": _BULL if pa_s > 0 else _MUTED,
                                 "fontSize": "0.65rem", **_mono}),
            ]),
            html.Td([
                # H4 gate + TF trend alignment dots
                html.Span(gate_lbl + " ",
                          style={"color": gate_col, "fontSize": "0.65rem",
                                 "fontWeight": 700, "marginRight": "4px"}),
                html.Span("D:", style={"color": _MUTED, "fontSize": "0.62rem"}),
                _trend_dot(d_trend),
                html.Span(" 4H:", style={"color": _MUTED, "fontSize": "0.62rem"}),
                _trend_dot(h4_trend),
                html.Span(" H1:", style={"color": _MUTED, "fontSize": "0.62rem"}),
                _trend_dot("bull" if direction == "long" else
                           "bear" if direction == "short" else "neutral"),
            ]),
            html.Td(), html.Td(),
        ], style={"background": "#0d1117", "borderBottom": "1px solid #21262d"})

        pair_color  = _MUTED if is_watch else _TEXT
        pair_label  = pair.replace("_", "/")          # EUR_USD → EUR/USD for display
        rows.append(html.Tr([
            html.Td([
                html.Div([
                    html.Span(pair_label,
                              style={"color": pair_color, "fontSize": "0.85rem", **_mono}),
                    html.Span(" W", style={"color": "#8b949e", "fontSize": "0.65rem",
                                           "fontWeight": 700,
                                           "display": "inline" if is_watch else "none"}),
                ]),
                html.Span(tf, style={"color": "#38b6ff", "fontSize": "0.65rem",
                                     "fontWeight": 700, "letterSpacing": "0.05em"}),
            ], style={"padding": "0.4rem 0.5rem", "cursor": "pointer"}),
            html.Td([
                html.Div(style={"background": bar_color, "height": "6px",
                                 "borderRadius": "3px",
                                 "width": f"{min(score, 100)}%",
                                 "minWidth": "4px"}),
                html.Span(f" {score}", style={"color": _MUTED, "fontSize": "0.75rem",
                                               "marginLeft": "4px", **_mono}),
            ], style={"padding": "0.4rem 0.5rem", "width": "130px"}),
            html.Td(arrow, style={"color": arrow_col, "fontSize": "1.1rem",
                                   "padding": "0.4rem 0.5rem", "textAlign": "center"}),
            html.Td(html.Span(status, style={"color": badge_col, "fontSize": "0.7rem",
                                              "fontWeight": 700}),
                    style={"padding": "0.4rem 0.5rem"}),
        ], id={"type": "signal-row", "index": pair},
            style={"cursor": "pointer", "borderBottom": "1px solid #161b22"}))
        rows.append(diag_row)

    return html.Div([
        html.H4("Signal Monitor", style={"color": _TEXT, "margin": "0 0 0.75rem", **_sans}),
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
        # Compute live floating P&L from current market price (updated every 5s)
        realised  = t.get("realised_pnl", 0)
        cur_price = current_prices.get(pair) or t.get("last_price", entry)
        units_rem = t.get("size", 0) * t.get("remaining", 1.0)
        diff = (cur_price - entry) if direction == "long" else (entry - cur_price)
        floating  = round(diff * units_rem, 4)
        pnl       = realised + floating
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

        sl         = t.get("sl",  0)
        tp1        = t.get("tp1", 0)
        size       = t.get("size", 0)
        tid        = t.get("id", pair)
        dec        = 3 if "JPY" in pair else 5
        pair_label = pair.replace("_", "/")   # EUR_USD → EUR/USD

        # Duration
        open_time = t.get("open_time")
        if open_time and hasattr(open_time, "timestamp"):
            from datetime import datetime as _dt2, timezone as _tz
            dur_s = int((_dt2.now(_tz.utc) - open_time.replace(tzinfo=_tz.utc)
                         if open_time.tzinfo is None else
                         _dt2.now(_tz.utc) - open_time).total_seconds())
            dur_h, dur_m = divmod(dur_s // 60, 60)
            dur_label = f"{dur_h}h {dur_m}m" if dur_h else f"{dur_m}m"
        else:
            dur_label = "—"

        cards.append(html.Div([
            # Header row: pair (clickable) + direction + duration
            html.Div([
                html.Button(
                    pair_label,
                    id={"type": "open-trade-row", "index": tid},
                    n_clicks=0,
                    title="Click to load this pair in the chart",
                    style={"background": "transparent", "border": "none", "padding": 0,
                           "color": dir_col, "fontWeight": 700, "fontSize": "0.88rem",
                           "cursor": "pointer", **_mono},
                ),
                html.Span(f" {direction.upper()}",
                          style={"color": dir_col, "fontSize": "0.78rem", "marginLeft": "6px"}),
                html.Span(f"  {remaining*100:.0f}% open",
                          style={"color": _MUTED, "fontSize": "0.72rem", "marginLeft": "8px"}),
                html.Span(f"  ⏱ {dur_label}",
                          style={"color": _MUTED, "fontSize": "0.7rem", "marginLeft": "6px"}),
            ], style={"display": "flex", "alignItems": "center", "marginBottom": "0.3rem"}),

            # Details: entry / SL / TP1 / size
            html.Div([
                html.Span(f"Entry {entry:.{dec}f}",
                          style={"color": "#38b6ff", "fontSize": "0.75rem", **_mono}),
                html.Span(f"  SL {sl:.{dec}f}",
                          style={"color": _BEAR, "fontSize": "0.75rem", **_mono}),
                html.Span(f"  TP1 {tp1:.{dec}f}",
                          style={"color": _BULL, "fontSize": "0.75rem", **_mono}),
                html.Span(f"  Lot {size}",
                          style={"color": _MUTED, "fontSize": "0.72rem", "marginLeft": "6px"}),
            ], style={"marginBottom": "0.3rem"}),

            # P&L bar + close button
            html.Div([
                html.Span(f"P&L {pnl:+.2f}", style={"color": pnl_col, "fontWeight": 700,
                                                        "fontSize": "0.82rem", **_mono}),
                html.Div(style={
                    "flex": 1, "height": "5px", "background": "#3d444d",
                    "borderRadius": "3px", "margin": "0 0.6rem", "overflow": "hidden",
                }, children=[
                    html.Div(style={
                        "width": f"{progress:.1f}%", "height": "100%",
                        "background": _BULL, "borderRadius": "3px",
                        "transition": "width 0.5s ease",
                    })
                ]),
                html.Button("EDIT", id={"type": "edit-trade-btn", "index": tid},
                            n_clicks=0,
                            style={"background": "#21262d", "color": "#ffd700",
                                   "border": "1px solid #ffd700", "borderRadius": "3px",
                                   "padding": "0.2rem 0.5rem", "cursor": "pointer",
                                   "fontSize": "0.72rem", "marginRight": "4px"}),
                html.Button("CLOSE", id={"type": "close-trade-btn", "index": tid},
                            style={"background": _BEAR, "color": "#fff",
                                   "border": "none", "borderRadius": "3px",
                                   "padding": "0.2rem 0.5rem", "cursor": "pointer",
                                   "fontSize": "0.72rem"}),
            ], style={"display": "flex", "alignItems": "center"}),

        ], style={"background": _CARD, "borderRadius": "4px",
                  "padding": "0.55rem 0.7rem", "marginBottom": "0.4rem"}))

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

def equity_curve_chart(equity_curve: list) -> html.Div:
    """Plotly equity curve from state['equity_curve'] snapshots."""
    import plotly.graph_objects as go
    if not equity_curve:
        return html.Div("No equity data yet — starts after the first hourly tick.",
                        style={"color": _MUTED, "fontSize": "0.8rem",
                               "padding": "0.5rem 0"})
    from datetime import datetime
    times   = [datetime.fromtimestamp(p["t"]).strftime("%m/%d %H:%M")
               for p in equity_curve]
    balance = [p["balance"] for p in equity_curve]
    nav     = [p.get("nav", p["balance"]) for p in equity_curve]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=times, y=nav,    name="NAV",
                             line=dict(color="#38b6ff", width=1.5)))
    fig.add_trace(go.Scatter(x=times, y=balance, name="Balance",
                             line=dict(color="#ffd700", width=1, dash="dot")))
    fig.update_layout(
        plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
        font=dict(color="#e6edf3", size=10),
        margin=dict(l=8, r=8, t=8, b=8),
        height=180,
        legend=dict(orientation="h", x=0, y=1.15,
                    font=dict(size=9), bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(showgrid=False, tickfont=dict(size=8)),
        yaxis=dict(gridcolor="#21262d", tickfont=dict(size=8)),
    )
    return dcc.Graph(figure=fig, config={"displayModeBar": False},
                     style={"borderRadius": "4px", "overflow": "hidden"})


def analytics_block(closed_trades: list) -> html.Div:
    """Per-pair win rate and average R:R from closed trades."""
    if not closed_trades:
        return html.Div()
    from collections import defaultdict
    stats = defaultdict(lambda: {"wins": 0, "total": 0, "rr_sum": 0.0})
    for t in closed_trades:
        pair = t.get("pair", "?")
        pnl  = t.get("realised_pnl", 0)
        stats[pair]["total"] += 1
        if pnl > 0:
            stats[pair]["wins"] += 1
        # Approximate RR: pnl vs initial risk (size × |entry − sl|)
        size = t.get("size", 1)
        entry = t.get("entry", 0)
        sl    = t.get("sl", entry)
        risk  = abs(entry - sl) * size
        if risk > 0:
            stats[pair]["rr_sum"] += pnl / risk

    rows = []
    for pair, s in sorted(stats.items()):
        wr  = s["wins"] / s["total"] if s["total"] else 0
        avg_rr = s["rr_sum"] / s["total"] if s["total"] else 0
        wr_col = _BULL if wr >= 0.5 else _BEAR
        rows.append(html.Tr([
            html.Td(pair,                style={"padding":"0.2rem 0.4rem","fontSize":"0.78rem",**_mono}),
            html.Td(f"{s['total']}",     style={"padding":"0.2rem 0.4rem","fontSize":"0.75rem","color":_MUTED}),
            html.Td(f"{wr:.0%}",         style={"padding":"0.2rem 0.4rem","fontSize":"0.75rem","color":wr_col}),
            html.Td(f"{avg_rr:+.2f}R",  style={"padding":"0.2rem 0.4rem","fontSize":"0.75rem",
                                                 "color":_BULL if avg_rr>0 else _BEAR}),
        ]))

    return html.Div([
        html.H6("Per-Pair Analytics", style={"color": _MUTED, "margin": "0 0 0.4rem",
                                              "fontSize": "0.75rem", **_sans}),
        html.Table([
            html.Thead(html.Tr([
                html.Th(h, style={"color":_MUTED,"padding":"0.2rem 0.4rem","fontSize":"0.7rem",
                                   "textAlign":"left","borderBottom":"1px solid #21262d"})
                for h in ["Pair", "Trades", "Win%", "Avg R"]
            ])),
            html.Tbody(rows),
        ], style={"width":"100%","borderCollapse":"collapse"}),
    ])


def trade_log_drawer(
    closed_trades: list,
    suggestions: list,
    ml_stats: dict,
    visible: bool = False,
    equity_curve: list = None,
) -> html.Div:
    trades_content   = _trade_table(closed_trades)
    suggestion_cards = _suggestion_cards(suggestions)
    ml_content       = _ml_block(ml_stats)
    eq_chart         = equity_curve_chart(equity_curve or [])
    analytics        = analytics_block(closed_trades)

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

        # Equity curve
        html.Div([
            html.H6("Equity Curve", style={"color": _MUTED, "margin": "0 0 0.4rem",
                                            "fontSize": "0.75rem", **_sans}),
            eq_chart,
        ], style={"marginBottom": "1.5rem"}),

        # Per-pair analytics
        html.Div([analytics], style={"marginBottom": "1.5rem"}),

        html.Div([trades_content], style={"marginBottom": "1.5rem"}),
        html.Div([ml_content],    style={"marginBottom": "1.5rem"}),
        html.Div(suggestion_cards),
    ], style={
        "position": "fixed", "top": 0, "right": 0, "bottom": 0,
        "width": "460px", "background": _PANEL,
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
