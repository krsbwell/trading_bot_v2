"""
Dash component builders for each of the 5 dashboard panels.
All functions take plain data (dicts / lists) and return html/dcc components.
"""
from dash import html, dcc
import plotly.graph_objects as go
from risk.risk_manager import get_quote_to_usd_rate as _get_quote_to_usd_rate

# ── Colour constants ──────────────────────────────────────────────────────────
_BULL  = "#00ff88"
_BEAR  = "#ff3366"
_GOLD  = "#ffd700"
_PANEL = "#161b22"
_CARD  = "#21262d"
_TEXT  = "#e6edf3"
_MUTED = "#7d8590"
_BG    = "#0f0f0f"

_mono = {"fontFamily": "'JetBrains Mono', monospace"}
_sans = {"fontFamily": "'IBM Plex Sans', sans-serif"}


# ═══════════════════════════════════════════════════════════════════════════════
# Panel 2 — Signal Monitor
# ═══════════════════════════════════════════════════════════════════════════════

def _trend_dot(trend: str) -> html.Span:
    """Small coloured dot for TF trend: bull=green, bear=red, neutral=grey."""
    col = _BULL if trend == "bull" else (_BEAR if trend == "bear" else "#3d444d")
    return html.Span("●", style={"color": col, "fontSize": "0.7rem"})


_REJECT_LABELS = {
    "NO_TOUCH":        "No EMA touch",
    "CONDITIONS_WEAK": "Weak conditions",
    "H4_NEUTRAL":      "H4 ranging",
}


def signal_monitor_panel(signals: dict, signal_details: dict = None,
                         last_scan_time=None, pair_scan_times: dict = None,
                         learning_health: dict = None) -> html.Div:
    """
    signals          : {pair: {"score": int, "direction": str, "status": str}}
    signal_details   : {pair: full signal dict with sub-scores, h4_gate, trends}
    last_scan_time   : datetime of most recent scan cycle start (shown in header)
    pair_scan_times  : {pair: datetime} of each active pair's last successfully
                       COMPLETED scan — distinct from last_scan_time, which only
                       tells you the cycle started, not that every pair in it
                       finished. Used to detect a pair silently going dark
                       (e.g. an earlier pair's exception killing the rest of
                       the loop — see bugs_scheduler_reliability memory).
    """
    import config as _cfg
    from datetime import datetime as _dt, timezone as _tz
    _watch = set(getattr(_cfg, "FOREX_WATCH", []))
    details = signal_details or {}
    pair_scan_times = pair_scan_times or {}

    # ── Bot health check — flag any active (trading) pair whose scan is stale ──
    _STALE_MIN = 45   # 1.5× the 30-min scan cadence; buffer for slow API calls
    _now = _dt.now(_tz.utc)
    _stale_pairs = []
    for _p in getattr(_cfg, "FOREX_PAIRS", []):
        _last = pair_scan_times.get(_p)
        if _last is None:
            _stale_pairs.append((_p, "never scanned"))
        else:
            _age_min = (_now - _last).total_seconds() / 60
            if _age_min > _STALE_MIN:
                _stale_pairs.append((_p, f"{_age_min:.0f} min ago"))

    health_banner = None
    if _stale_pairs:
        health_banner = html.Div([
            html.Span("⚠ BOT HEALTH: ", style={"color": "#ffd700", "fontWeight": 700,
                                                 "fontSize": "0.75rem", **_mono}),
            html.Span(
                "  ".join(f"{p.replace('_','/')} ({age})" for p, age in _stale_pairs)
                + f"  — not scanned in the last {_STALE_MIN} min, check the bot process",
                style={"color": "#ffd700", "fontSize": "0.72rem", **_mono},
            ),
        ], style={"background": "#2a1a00", "border": "1px solid #ffd700",
                   "borderRadius": "4px", "padding": "6px 10px", "marginBottom": "8px"})

    # ── Learning health — is every closed bot trade reaching the ML log? ─────
    learning_line = None
    if learning_health:
        _unlogged = learning_health.get("unlogged") or []
        _last = learning_health.get("last_logged")
        if _unlogged:
            learning_line = html.Div(
                f"⚠ LEARNING GAP: {len(_unlogged)} closed trade(s) not logged for ML "
                f"({', '.join(map(str, _unlogged[:5]))}) — check logs/main.log",
                style={"color": "#ff3366", "background": "#2a0010", "border": "1px solid #ff3366",
                       "borderRadius": "4px", "padding": "6px 10px", "marginBottom": "8px",
                       "fontSize": "0.72rem", "fontWeight": 700, **_mono})
        else:
            learning_line = html.Div(
                f"ML log: {learning_health.get('logged', 0)} trade outcome(s) recorded"
                + (f", last {_last:%Y-%m-%d %H:%M} UTC" if hasattr(_last, "strftime") else ""),
                style={"color": "#3d444d", "fontSize": "0.65rem", "marginBottom": "6px", **_mono})

    active_rows  = []   # pairs with a long/short direction
    neutral_rows = []   # pairs returning no_signal (score = 0, direction = "—")

    for pair, info in sorted(signals.items(), key=lambda x: x[1]["score"], reverse=True):
        is_watch  = pair in _watch
        score     = info.get("score", 0)
        direction = info.get("direction", "—")
        status    = info.get("status", "NEUTRAL")
        det       = details.get(pair, {}) or {}

        confirm_lbl = _cfg.confirm_tf_for(pair)   # usually "H4", per-pair overridable

        if direction not in ("long", "short"):
            # ── Compact neutral row ──────────────────────────────────────────
            reject    = det.get("reject_reason", "")
            h4_t      = det.get("h4_trend", "neutral")
            d_t       = det.get("d_trend",  "neutral")
            reason_lbl = _REJECT_LABELS.get(reject, reject or "—")
            pair_lbl   = pair.replace("_", "/")
            neutral_rows.append(html.Tr([
                html.Td([
                    html.Span(pair_lbl, style={"color": "#3d444d", "fontSize": "0.8rem",
                                               **_mono}),
                    html.Span(" W" if is_watch else "",
                              style={"color": "#3d444d", "fontSize": "0.65rem"}),
                ], style={"padding": "0.25rem 0.5rem"}),
                html.Td(
                    html.Span(reason_lbl, style={"color": "#3d444d",
                                                 "fontSize": "0.68rem", **_mono}),
                    style={"padding": "0.25rem 0.5rem"},
                ),
                html.Td([
                    html.Span("D:", style={"color": "#3d444d", "fontSize": "0.62rem"}),
                    _trend_dot(d_t),
                    html.Span(f" {confirm_lbl}:", style={"color": "#3d444d", "fontSize": "0.62rem"}),
                    _trend_dot(h4_t),
                ], style={"padding": "0.25rem 0.5rem", "textAlign": "center"}),
                html.Td(
                    html.Span("NEUTRAL", style={"color": "#3d444d", "fontSize": "0.68rem",
                                                "fontWeight": 700}),
                    style={"padding": "0.25rem 0.5rem"},
                ),
            ], style={"borderBottom": "1px solid #161b22"}))
            continue

        # ── Active signal row ────────────────────────────────────────────────
        tf        = det.get("timeframe", info.get("timeframe", "H1"))
        bar_color = _BULL if score >= 70 else (_GOLD if score >= 50 else "#3d444d")
        arrow     = "▲" if direction == "long" else "▼"
        arrow_col = _BULL if direction == "long" else _BEAR
        badge_col = (_BULL     if status == "SIGNAL"   else
                     _GOLD     if status == "WATCHING" else
                     "#38b6ff" if status == "SCANNING" else _MUTED)

        ema_s     = det.get("ema_score",       0)
        str_s     = det.get("structure_score", 0)
        pa_s      = det.get("pa_score",        0)
        ml_prob   = det.get("ml_win_prob")
        h4_gate   = det.get("h4_gate",   {}) or {}
        h4_passed = h4_gate.get("passed")
        h4_trend  = det.get("h4_trend",  "neutral")
        d_trend   = det.get("d_trend",   "neutral")
        gate_col  = (_BULL if h4_passed is True  else
                     _BEAR if h4_passed is False else _MUTED)
        gate_lbl  = (f"{confirm_lbl}✓" if h4_passed is True  else
                     f"{confirm_lbl}✗" if h4_passed is False else f"{confirm_lbl}?")

        ml_col  = (_BULL if (ml_prob or 0) >= 0.55 else
                   _GOLD if (ml_prob or 0) >= 0.45 else _BEAR)
        ml_cell = (html.Span(f"  ML:{ml_prob:.0%}", style={"color": ml_col,
                              "fontSize": "0.65rem", **_mono})
                   if ml_prob is not None else
                   html.Span("  ML:—", style={"color": _MUTED,
                             "fontSize": "0.65rem", **_mono}))

        diag_row = html.Tr([
            html.Td([
                html.Span(f"EMA:{ema_s:.0f} ",
                          style={"color": _GOLD if ema_s > 0 else _MUTED,
                                 "fontSize": "0.65rem", **_mono}),
                html.Span(f"STR:{str_s:.0f} ",
                          style={"color": "#38b6ff" if str_s > 0 else _MUTED,
                                 "fontSize": "0.65rem", **_mono}),
                html.Span(f"PA:{pa_s:.0f}",
                          style={"color": _BULL if pa_s > 0 else _MUTED,
                                 "fontSize": "0.65rem", **_mono}),
                ml_cell,
            ]),
            html.Td([
                html.Span(gate_lbl + " ",
                          style={"color": gate_col, "fontSize": "0.65rem",
                                 "fontWeight": 700, "marginRight": "4px"}),
                html.Span("D:", style={"color": _MUTED, "fontSize": "0.62rem"}),
                _trend_dot(d_trend),
                html.Span(" 4H:", style={"color": _MUTED, "fontSize": "0.62rem"}),
                _trend_dot(h4_trend),
                html.Span(" H1:", style={"color": _MUTED, "fontSize": "0.62rem"}),
                _trend_dot("bull" if direction == "long" else "bear"),
            ]),
            html.Td(), html.Td(),
        ], style={"background": "#0f0f0f", "borderBottom": "1px solid #21262d"})

        pair_color    = _MUTED if is_watch else _TEXT
        pair_label    = pair.replace("_", "/")
        block_reason  = det.get("trade_blocked_reason")
        badge_content = [html.Span(status, style={"color": badge_col, "fontSize": "0.7rem",
                                                   "fontWeight": 700})]
        if block_reason:
            # Shorten to category prefix (before first colon/parenthesis) for display
            cat = block_reason.split(":")[0].split("(")[0].strip()
            badge_content.append(html.Span(f"⊘ {cat}", style={
                "color": "#ff9900", "fontSize": "0.62rem", "fontWeight": 700,
                "display": "block", "marginTop": "2px",
                "background": "rgba(255,153,0,0.12)",
                "border": "1px solid rgba(255,153,0,0.35)",
                "borderRadius": "3px", "padding": "0 4px",
            }))
        active_rows.append(html.Tr([
            html.Td([
                html.Div([
                    html.Span(pair_label,
                              style={"color": pair_color, "fontSize": "0.85rem", **_mono}),
                    html.Span(" W", style={"color": "#7d8590", "fontSize": "0.65rem",
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
            html.Td(badge_content, style={"padding": "0.4rem 0.5rem"}),
        ], id={"type": "signal-row", "index": pair},
            style={"cursor": "pointer", "borderBottom": "1px solid #161b22"}))
        active_rows.append(diag_row)

    # 2026-10-08 (user's call, 28-pair roster) — neutral/no-touch pairs no
    # longer render here at all. At 28 pairs, most of them sit at NO_TOUCH
    # most of the time (confirmed: ~88% of all scans across the whole
    # roster — see signal_lifecycle_report.py output), so showing all 28
    # every tick buried the handful actually worth looking at under two
    # dozen "NEUTRAL" rows. Only pairs that cleared NO_TOUCH/CONDITIONS_
    # WEAK/H4_NEUTRAL (i.e. the strategy found *something*, whether or not
    # it went on to trade) show here now — that's active_rows, unchanged.
    # Deliberately no expand/show-all control (explicit user choice, not
    # an oversight) — bot-health visibility for a specific quiet pair
    # still comes from the ⚠ BOT HEALTH banner above (stale-scan check)
    # or logs/main.log, not this panel. _n_hidden is a plain count, not
    # interactive, just enough to say "yes, the other 24 are still being
    # scanned, they're just quiet" instead of looking like 3 pairs exist.
    _n_hidden = len(neutral_rows)
    all_rows = active_rows
    scan_ts = ""
    if last_scan_time:
        try:
            scan_ts = last_scan_time.strftime("%H:%M")
        except Exception:
            scan_ts = str(last_scan_time)[:5]

    header = html.Div([
        html.H4("Signal Monitor", style={"color": _TEXT, "margin": "0", **_sans}),
        html.Span(f"Last scan {scan_ts} UTC" if scan_ts else "",
                  style={"color": _MUTED, "fontSize": "0.65rem", "marginLeft": "auto",
                         "alignSelf": "center"}),
    ], style={"display": "flex", "justifyContent": "space-between",
              "marginBottom": "0.75rem"})

    # Distinguish "no scan data exists yet" from "scans happened, every
    # pair came back quiet" — all_rows alone can't tell these apart now
    # that neutral pairs are filtered out of it; signals (the raw input,
    # pre-filter) still can. Showing "Waiting for first scan…" for the
    # second case would be actively misleading, not just empty.
    if all_rows:
        table_or_empty = html.Table(all_rows, style={"width": "100%", "borderCollapse": "collapse"})
    elif signals:
        table_or_empty = html.Div("All pairs quiet — no touch this cycle",
                                   style={"color": _MUTED, "fontSize": "0.8rem",
                                          "textAlign": "center", "padding": "0.75rem 0"})
    else:
        table_or_empty = html.Div("Waiting for first scan…",
                                   style={"color": _MUTED, "fontSize": "0.8rem",
                                          "textAlign": "center", "padding": "0.75rem 0"})
    # Suppressed when all_rows is empty — the "All pairs quiet" placeholder
    # above already says this, a separate footer would just repeat it.
    quiet_footer = (
        html.Div(f"{_n_hidden} pair{'s' if _n_hidden != 1 else ''} quiet (no touch) — hidden",
                 style={"color": "#3d444d", "fontSize": "0.65rem",
                        "textAlign": "center", "padding": "0.4rem 0", **_mono})
        if (_n_hidden and all_rows) else None
    )
    body = [header, table_or_empty]
    if quiet_footer:
        body.append(quiet_footer)
    if learning_line:
        body = [learning_line] + body
    if health_banner:
        body = [health_banner] + body
    return html.Div(body, style=_panel_style())


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
        q2u  = _get_quote_to_usd_rate(pair)
        floating  = round(diff * units_rem * q2u, 4)
        pnl       = realised + floating
        # Reconciled-orphan trades (adopted from the broker with no local
        # record — see bugs_weekend_close_desync memory) have tp1/tp2/tp3
        # explicitly set to None, not just absent: `.get("tp3", entry)`
        # only substitutes its default for a *missing* key, so an explicit
        # None sailed straight through into `tp3 - entry` below and crashed
        # this whole callback every 5s, blanking the entire trades/stats
        # panel. Treat None the same as "unset" everywhere it's used, and
        # show "—" instead of a formatted number for an unset TP.
        tp1   = t.get("tp1")
        tp2   = t.get("tp2")
        tp3   = t.get("tp3")
        remaining = t.get("remaining", 1.0)
        pnl_col   = _BULL if pnl >= 0 else _BEAR
        dir_col   = _BULL if direction == "long" else _BEAR

        # Progress toward TP: % of distance from entry to tp3 covered.
        # No progress bar to show if TP3 was never set (orphan adoption).
        price = current_prices.get(pair, entry)
        if tp3 is None:
            progress = 0
        elif direction == "long":
            progress = max(0, min(100, (price - entry) / max(tp3 - entry, 1e-8) * 100))
        else:
            progress = max(0, min(100, (entry - price) / max(entry - tp3, 1e-8) * 100))

        sl         = t.get("sl",  0)
        size       = t.get("size", 0)
        tid        = t.get("id", pair)
        dec        = 3 if "JPY" in pair else 5
        pair_label = pair.replace("_", "/")   # EUR_USD → EUR/USD
        tp1_label  = f"{tp1:.{dec}f}" if tp1 is not None else "—"
        tp2_label  = f"{tp2:.{dec}f}" if tp2 is not None else "—"
        tp3_label  = f"{tp3:.{dec}f}" if tp3 is not None else "—"

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

        signal_score = t.get("score", "—")
        tf_label     = t.get("timeframe", "H1")

        # Risk % and approx reward
        sl_dist    = abs(entry - sl) if sl else 0
        tp1_dist   = abs(tp1 - entry) if tp1 else 0
        risk_pct   = 1.0  # always 1% per rules
        reward_pct = round(tp1_dist / sl_dist * risk_pct, 2) if sl_dist > 0 else 0

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
                html.Span(f"  {remaining*100:.0f}% rem",
                          style={"color": _MUTED, "fontSize": "0.72rem", "marginLeft": "8px"}),
                html.Span(f"  ⏱ {dur_label}",
                          style={"color": _MUTED, "fontSize": "0.7rem", "marginLeft": "6px"}),
                html.Span(f"  {tf_label}",
                          style={"color": "#38b6ff", "fontSize": "0.65rem",
                                 "fontWeight": 700, "marginLeft": "4px"}),
            ], style={"display": "flex", "alignItems": "center", "marginBottom": "0.3rem"}),

            # Row 1: Entry / SL / TP1 / Lot
            html.Div([
                html.Span(f"Entry {entry:.{dec}f}",
                          style={"color": "#38b6ff", "fontSize": "0.75rem", **_mono}),
                html.Span(f"  SL {sl:.{dec}f}",
                          style={"color": _BEAR, "fontSize": "0.75rem", **_mono}),
                html.Span(f"  TP1 {tp1_label}",
                          style={"color": _BULL, "fontSize": "0.75rem", **_mono}),
                html.Span(f"  Lot {size}",
                          style={"color": _MUTED, "fontSize": "0.72rem", "marginLeft": "6px"}),
            ], style={"marginBottom": "0.2rem"}),

            # Row 2: TP2 / TP3 / Current price / Risk:Reward
            html.Div([
                html.Span(f"TP2 {tp2_label}",
                          style={"color": _BULL, "fontSize": "0.72rem", **_mono,
                                 "opacity": "0.7"}),
                html.Span(f"  TP3 {tp3_label}",
                          style={"color": _BULL, "fontSize": "0.72rem", **_mono,
                                 "opacity": "0.7"}),
                html.Span(f"  Now {cur_price:.{dec}f}",
                          style={"color": "#c9d1d9", "fontSize": "0.72rem", **_mono,
                                 "marginLeft": "6px"}),
                html.Span(f"  R:R 1:{reward_pct:.1f}",
                          style={"color": _MUTED, "fontSize": "0.7rem", "marginLeft": "6px"}),
                html.Span(f"  Sc:{signal_score}",
                          style={"color": _GOLD, "fontSize": "0.68rem", "marginLeft": "6px",
                                 **_mono}),
            ], style={"marginBottom": "0.3rem"}),

            # P&L bar + action buttons
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
                            n_clicks=0,
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
# Panel 3b — Pending Trades (unfilled limit orders)
# ═══════════════════════════════════════════════════════════════════════════════

def pending_orders_panel(orders: list, mode: str) -> html.Div:
    """
    orders : list of pending limit-order dicts (paper_trader.pending_orders)
    mode   : "paper" | "live"
    """
    import config as _cfg
    expiry_candles = getattr(_cfg, "LIMIT_ORDER_EXPIRY_CANDLES", 8)
    badge_color    = _GOLD if mode == "paper" else _BULL
    badge_label    = "PAPER MODE" if mode == "paper" else "LIVE"

    cards = []
    for o in orders:
        pair       = o.get("pair", "—")
        direction  = o.get("direction", "long")
        limit      = o.get("limit_price", 0)
        sl         = o.get("sl", 0)
        # tp2/tp3 (and, less commonly, tp1) can be None here — a limit
        # order placed with a TP left blank — same None-vs-missing-key
        # trap as open_trades_panel above; show "—" rather than crashing.
        tp1        = o.get("tp1")
        tp2        = o.get("tp2")
        tp3        = o.get("tp3")
        size       = o.get("size", 0)
        oid        = o.get("id", pair)
        dec        = 3 if "JPY" in pair else 5
        pair_label = pair.replace("_", "/")
        dir_col    = _BULL if direction == "long" else _BEAR
        tp1_label  = f"{tp1:.{dec}f}" if tp1 is not None else "—"
        tp2_label  = f"{tp2:.{dec}f}" if tp2 is not None else "—"
        tp3_label  = f"{tp3:.{dec}f}" if tp3 is not None else "—"

        candles_left = max(0, expiry_candles - o.get("candles_missed", 0))

        cards.append(html.Div([
            html.Div([
                html.Span(pair_label,
                          style={"color": dir_col, "fontWeight": 700, "fontSize": "0.88rem",
                                 **_mono}),
                html.Span(f" {direction.upper()}",
                          style={"color": dir_col, "fontSize": "0.78rem", "marginLeft": "6px"}),
                html.Span("  LIMIT",
                          style={"color": "#38b6ff", "fontSize": "0.65rem", "fontWeight": 700,
                                 "marginLeft": "4px"}),
                html.Span(f"  expires in {candles_left} candle{'s' if candles_left != 1 else ''}",
                          style={"color": _MUTED, "fontSize": "0.7rem", "marginLeft": "8px"}),
            ], style={"display": "flex", "alignItems": "center", "marginBottom": "0.3rem"}),

            html.Div([
                html.Span(f"Limit {limit:.{dec}f}",
                          style={"color": "#38b6ff", "fontSize": "0.75rem", **_mono}),
                html.Span(f"  SL {sl:.{dec}f}",
                          style={"color": _BEAR, "fontSize": "0.75rem", **_mono}),
                html.Span(f"  TP1 {tp1_label}",
                          style={"color": _BULL, "fontSize": "0.75rem", **_mono}),
                html.Span(f"  Lot {size}",
                          style={"color": _MUTED, "fontSize": "0.72rem", "marginLeft": "6px"}),
            ], style={"marginBottom": "0.2rem"}),

            html.Div([
                html.Span(f"TP2 {tp2_label}",
                          style={"color": _BULL, "fontSize": "0.72rem", **_mono,
                                 "opacity": "0.7"}),
                html.Span(f"  TP3 {tp3_label}",
                          style={"color": _BULL, "fontSize": "0.72rem", **_mono,
                                 "opacity": "0.7"}),
            ], style={"marginBottom": "0.3rem"}),

            html.Div([
                html.Button("EDIT", id={"type": "edit-pending-btn", "index": oid},
                            n_clicks=0,
                            style={"background": "#21262d", "color": "#ffd700",
                                   "border": "1px solid #ffd700", "borderRadius": "3px",
                                   "padding": "0.2rem 0.5rem", "cursor": "pointer",
                                   "fontSize": "0.72rem", "marginRight": "4px"}),
                html.Button("CANCEL", id={"type": "cancel-limit-btn", "index": oid},
                            n_clicks=0,
                            style={"background": _BEAR, "color": "#fff",
                                   "border": "none", "borderRadius": "3px",
                                   "padding": "0.2rem 0.5rem", "cursor": "pointer",
                                   "fontSize": "0.72rem"}),
            ], style={"display": "flex", "alignItems": "center"}),

        ], style={"background": _CARD, "borderRadius": "4px",
                  "padding": "0.55rem 0.7rem", "marginBottom": "0.4rem"}))

    if not cards:
        cards = [html.Div("No pending orders", style={"color": _MUTED, "textAlign": "center",
                                                        "padding": "1rem"})]

    return html.Div([
        html.Div([
            html.H4("Pending Trades", style={"color": _TEXT, "margin": 0, **_sans}),
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
                       win_rate_20: float, trades_today: int, mode: str,
                       profit_factor: float = 0.0,
                       avg_latency_ms: float | None = None) -> html.Div:
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
    pf_col   = _BULL if profit_factor >= 1.5 else (_GOLD if profit_factor >= 1.0 else _BEAR)
    pf_str   = f"{profit_factor:.2f}" if profit_factor else "—"
    lat_str  = f"{avg_latency_ms:.0f} ms" if avg_latency_ms is not None else "—"

    stats = [
        _stat("Balance",    f"${balance:,.2f}", large=True),
        _stat("NAV",        f"${nav:,.2f}"),
        _stat("Daily P&L",  f"${daily_pnl:+,.2f}", dpnl_col, large=True),
        _stat("Unrealised", f"${unreal:+,.2f}", _BULL if unreal >= 0 else _BEAR),
        _stat("Win Rate",   f"{win_rate_all:.0%} / {win_rate_20:.0%}",
              _BULL if win_rate_all >= 0.50 else _BEAR),
        _stat("Profit Factor", pf_str, pf_col),
        _stat("Latency",    lat_str, _MUTED),
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
        plot_bgcolor="#0f0f0f", paper_bgcolor="#0f0f0f",
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


def trade_log_drawer(
    closed_trades: list,
    suggestions: list,
    ml_stats: dict,
    visible: bool = False,
    equity_curve: list = None,
) -> html.Div:
    # "Signal Frequency (weekly)", "Watching Signals (Near-Miss)" and
    # "Per-Pair Analytics" panels removed 2026-09-28 (user's call).
    trades_content   = _trade_table(closed_trades)
    suggestion_cards = _suggestion_cards(suggestions)
    ml_content       = _ml_block(ml_stats)
    eq_chart         = equity_curve_chart(equity_curve or [])

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

        html.Div([trades_content], style={"marginBottom": "1.5rem"}),
        html.Div([ml_content],    style={"marginBottom": "1.5rem"}),
        html.Div(suggestion_cards),
    ], style={
        "position": "fixed", "top": 0, "right": 0, "bottom": 0,
        # wide enough for all 10 trade-log columns without sideways
        # scrolling (was 460px); never wider than the window
        "width": "940px", "maxWidth": "100vw", "boxSizing": "border-box",
        "background": _PANEL,
        "boxShadow": "-4px 0 20px rgba(0,0,0,0.5)",
        "padding": "1.5rem", "overflowY": "auto",
        "zIndex": 1000, "transform": "translateX(0)" if visible else "translateX(100%)",
        "transition": "transform 0.3s ease",
    })


# ── Sub-builders ─────────────────────────────────────────────────────────────

TRADE_LOG_PAGE_SIZE = 15


def _iso(v) -> str:
    """open/close times may be datetimes or ISO strings (from the state file)."""
    if not v:
        return ""
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _px(v, pair: str) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v:.3f}" if "JPY" in pair else f"{v:.5f}"


def _sl_initial(t: dict):
    """The SL the trade STARTED with; old trades only have the final
    (possibly trailed) SL."""
    sig = t.get("ml_signal") or {}
    return t.get("sl_initial", sig.get("stop_loss", t.get("sl")))


def trade_log_rows(trades: list) -> list[dict]:
    """Display rows for the paginated trade log, newest first (all trades)."""
    rows = []
    for t in reversed(trades):
        pnl  = t.get("realised_pnl", 0) or 0
        pair = t.get("pair", "—")
        o, c = _iso(t.get("open_time")), _iso(t.get("close_time"))
        rows.append({
            "opened":  f"{o[5:10]} {o[11:16]}" if o else "—",   # MM-DD HH:MM (UTC)
            "closed":  f"{c[5:10]} {c[11:16]}" if c else "—",
            "pair":    pair,
            "dir":     (t.get("direction") or "—")[:1].upper(),
            "entry":   _px(t.get("entry"), pair),
            "sl":      _px(_sl_initial(t), pair),
            "exit":    _px(t.get("exit_price"), pair),
            "pnl":     f"${pnl:+.2f}",
            # 100 = manual dashboard order, not a computed score
            "score":   "manual" if t.get("score") == 100 else str(t.get("score", "—")),
            "outcome": t.get("close_reason", "—"),
            "win":     1 if pnl > 0 else 0,        # styling only (not a column)
        })
    return rows


_CSV_COLUMNS = [
    "id", "pair", "direction", "open_time_utc", "close_time_utc", "entry", "sl_initial",
    "sl_final", "tp1", "tp2", "tp3", "exit_price", "size", "risk_dollar", "pnl_dollar",
    "r_multiple", "score", "close_reason", "patterns",
]


def _csv_safe(v):
    """Spreadsheet formula-injection guard: a text cell starting with = + @
    (or tab/CR) can execute as a formula in Excel. Numbers pass through."""
    if isinstance(v, str) and v[:1] in ("=", "+", "@", "\t", "\r"):
        return "'" + v
    return v


def trade_log_csv(trades: list) -> str:
    """Full-detail CSV of ALL closed trades (oldest first), for export."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_CSV_COLUMNS)
    for t in trades:
        pnl, risk = t.get("realised_pnl", 0) or 0, t.get("risk_dollar") or 0
        sig = t.get("ml_signal") or {}
        w.writerow([_csv_safe(x) for x in (
            t.get("id", ""), t.get("pair", ""), t.get("direction", ""),
            _iso(t.get("open_time")), _iso(t.get("close_time")),
            t.get("entry", ""), _sl_initial(t), t.get("sl", ""),
            t.get("tp1", ""), t.get("tp2", ""), t.get("tp3", ""), t.get("exit_price", ""),
            t.get("size", ""), round(risk, 2) if risk else "", round(pnl, 2),
            round(pnl / risk, 2) if risk else "",
            "manual" if t.get("score") == 100 else t.get("score", ""),
            t.get("close_reason", ""), "|".join(sig.get("patterns") or []),
        )])
    return buf.getvalue()


def _trade_table(trades: list) -> html.Div:
    if not trades:
        return html.Div("No closed trades yet.", style={"color": _MUTED})
    from dash import dash_table

    cols = [("Opened (UTC)", "opened"), ("Closed", "closed"), ("Pair", "pair"), ("Dir", "dir"),
            ("Entry", "entry"), ("SL", "sl"), ("Exit", "exit"), ("P&L", "pnl"),
            ("Score", "score"), ("Outcome", "outcome")]
    table = dash_table.DataTable(
        id="trade-log-table",
        columns=[{"name": n, "id": i} for n, i in cols],
        data=trade_log_rows(trades),
        page_action="native",                 # page 2, 3... instead of one long list
        page_size=TRADE_LOG_PAGE_SIZE,
        style_as_list_view=True,
        style_table={"width": "100%"},
        style_header={"backgroundColor": _PANEL, "color": _MUTED, "fontWeight": 600,
                      "fontSize": "0.75rem", "border": "none",
                      "borderBottom": "1px solid #21262d", "textAlign": "left"},
        style_cell={"backgroundColor": _PANEL, "color": _MUTED, "fontSize": "0.75rem",
                    "fontFamily": "'JetBrains Mono', 'Consolas', monospace",
                    "padding": "0.3rem 0.4rem", "textAlign": "left", "whiteSpace": "nowrap",
                    "border": "none", "borderBottom": "1px solid #21262d"},
        style_data_conditional=[
            {"if": {"column_id": "pair"}, "color": _TEXT},
            {"if": {"column_id": "dir", "filter_query": '{dir} = "L"'}, "color": _BULL},
            {"if": {"column_id": "dir", "filter_query": '{dir} = "S"'}, "color": _BEAR},
            # coloured by result, not label: an "sl" exit on a trailed stop can be a win
            {"if": {"column_id": ["pnl", "outcome"], "filter_query": "{win} = 1"}, "color": _BULL},
            {"if": {"column_id": ["pnl", "outcome"], "filter_query": "{win} = 0"}, "color": _BEAR},
        ],
        css=[{"selector": ".previous-next-container, .page-number",
              "rule": f"color: {_TEXT};"}],
    )
    export_btn = html.Button(
        "⬇ Export CSV", id="trade-log-export-btn", n_clicks=0,
        title="Download every closed trade (all pages, full detail) as CSV",
        style={"background": "transparent", "border": "1px solid #30363d", "color": _TEXT,
               "borderRadius": "4px", "padding": "3px 10px", "fontSize": "0.75rem",
               "cursor": "pointer"})
    return html.Div([
        html.Div([
            html.H6(f"Closed Trades ({len(trades)})",
                    style={"color": _MUTED, "margin": 0, "fontSize": "0.75rem", **_sans}),
            html.Div(export_btn, style={"marginLeft": "auto"}),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "0.4rem"}),
        table,
    ])


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

def _stat(label: str, value: str, value_color: str = _TEXT, large: bool = False) -> html.Div:
    return html.Div([
        html.Span(label, style={"color": _MUTED, "fontSize": "0.67rem",
                                  "display": "block", "letterSpacing": "0.03em"}),
        html.Span(value, style={"color": value_color, "fontWeight": 700,
                                  "fontSize": "1.0rem" if large else "0.88rem", **_mono}),
    ], style={"padding": "0 0.8rem", "borderRight": "1px solid #21262d"})


def signal_quality_panel(analysis: dict) -> html.Div:
    """
    Render signal quality diagnostics: KPIs, score histogram, condition pass rates,
    per-pair table, and what-if threshold simulation.
    """
    if "error" in analysis:
        return html.Div(analysis["error"], style={"color": _BEAR, "padding": "1rem"})

    total    = analysis.get("total", 0)
    trig     = analysis.get("triggered", 0)
    tr_rate  = analysis.get("trigger_rate", 0.0)
    avg_s    = analysis.get("avg_score", 0.0)
    bottleneck = analysis.get("bottleneck", "—")

    tr_color = _BULL if tr_rate >= 0.15 else (_GOLD if tr_rate >= 0.08 else _BEAR)
    as_color = _BULL if avg_s >= 50 else (_GOLD if avg_s >= 35 else _BEAR)

    def _chip(label, val, color):
        return html.Div([
            html.Div(val,   style={"color": color, "fontWeight": 700, "fontSize": "1.05rem"}),
            html.Div(label, style={"color": _MUTED, "fontSize": "0.68rem", "marginTop": "1px"}),
        ], style={"background": _CARD, "borderRadius": "4px",
                  "padding": "0.45rem 0.7rem", "textAlign": "center"})

    kpis = html.Div([
        _chip("Evaluations",   str(total),          _TEXT),
        _chip("Triggered",     str(trig),            _BULL),
        _chip("Trigger Rate",  f"{tr_rate:.0%}",     tr_color),
        _chip("Avg Score",     f"{avg_s:.1f}",       as_color),
        _chip("Bottleneck",    bottleneck,            _BEAR),
    ], style={"display": "flex", "gap": "0.4rem", "marginBottom": "0.75rem", "flexWrap": "wrap"})

    # ── Score distribution bar chart ─────────────────────────────────────────
    sd = analysis.get("score_dist", [])
    if sd:
        labels = [d["label"] for d in sd]
        counts = [d["count"] for d in sd]
        # Color bars: green ≥50, gold 35–49, red <35
        bar_colors = [
            _BULL if int(d["label"].split("–")[0]) >= 50 else
            (_GOLD if int(d["label"].split("–")[0]) >= 35 else _BEAR)
            for d in sd
        ]
        fig_score = go.Figure(go.Bar(x=labels, y=counts,
                                     marker_color=bar_colors, opacity=0.85))
        fig_score.update_layout(
            plot_bgcolor=_BG, paper_bgcolor=_BG,
            font=dict(color=_TEXT, size=9),
            margin=dict(l=8, r=8, t=16, b=8),
            height=140,
            title=dict(text="Score Distribution", font=dict(size=10, color=_MUTED), x=0),
            xaxis=dict(showgrid=False, tickfont=dict(size=8)),
            yaxis=dict(gridcolor=_CARD, tickfont=dict(size=8)),
            showlegend=False,
        )
        score_chart = dcc.Graph(figure=fig_score, config={"displayModeBar": False},
                                style={"borderRadius": "4px", "overflow": "hidden",
                                       "marginBottom": "0.5rem"})
    else:
        score_chart = html.Div()

    # ── Condition pass-rate horizontal bars ───────────────────────────────────
    conds = analysis.get("cond_rates", [])
    if conds:
        c_labels = [c["cond"]                         for c in conds]
        c_rates  = [round(c["rate"] * 100, 1)         for c in conds]
        c_colors = [
            _BEAR if c["rate"] < 0.5 else (_GOLD if c["rate"] < 0.75 else _BULL)
            for c in conds
        ]
        fig_cond = go.Figure(go.Bar(
            x=c_rates, y=c_labels, orientation="h",
            marker_color=c_colors, opacity=0.85,
            text=[f"{r:.0f}%" for r in c_rates], textposition="outside",
            textfont=dict(size=9, color=_TEXT),
        ))
        fig_cond.update_layout(
            plot_bgcolor=_BG, paper_bgcolor=_BG,
            font=dict(color=_TEXT, size=9),
            margin=dict(l=8, r=48, t=16, b=8),
            height=180,
            title=dict(text="Condition Pass Rates", font=dict(size=10, color=_MUTED), x=0),
            xaxis=dict(range=[0, 115], showgrid=False, ticksuffix="%", tickfont=dict(size=8)),
            yaxis=dict(showgrid=False, tickfont=dict(size=9)),
            showlegend=False,
        )
        cond_chart = dcc.Graph(figure=fig_cond, config={"displayModeBar": False},
                               style={"borderRadius": "4px", "overflow": "hidden",
                                      "marginBottom": "0.5rem"})
    else:
        cond_chart = html.Div()

    # ── Daily frequency chart ─────────────────────────────────────────────────
    daily = analysis.get("daily", [])
    if daily:
        days       = [d["date"]     for d in daily]
        trig_daily = [d["triggered"] for d in daily]
        scan_daily = [d["scanning"]  for d in daily]
        fig_daily  = go.Figure()
        fig_daily.add_trace(go.Bar(x=days, y=scan_daily,  name="Scanning",
                                   marker_color="#30363d"))
        fig_daily.add_trace(go.Bar(x=days, y=trig_daily,  name="Triggered",
                                   marker_color=_BULL))
        fig_daily.update_layout(
            barmode="stack",
            plot_bgcolor=_BG, paper_bgcolor=_BG,
            font=dict(color=_TEXT, size=9),
            margin=dict(l=8, r=8, t=16, b=8),
            height=130,
            title=dict(text="Daily Signal Activity", font=dict(size=10, color=_MUTED), x=0),
            xaxis=dict(showgrid=False, tickfont=dict(size=8)),
            yaxis=dict(gridcolor=_CARD, tickfont=dict(size=8)),
            legend=dict(orientation="h", y=1.2, font=dict(size=8)),
        )
        daily_chart = dcc.Graph(figure=fig_daily, config={"displayModeBar": False},
                                style={"borderRadius": "4px", "overflow": "hidden",
                                       "marginBottom": "0.75rem"})
    else:
        daily_chart = html.Div()

    # ── What-if simulation ─────────────────────────────────────────────────────
    what_if = analysis.get("what_if", [])
    wi_items = []
    for wi in what_if:
        extra = wi["extra_signals"]
        new_rate = round((trig + extra) / total, 3) if total else 0
        color = _BULL if extra > 5 else (_GOLD if extra > 0 else _MUTED)
        wi_items.append(html.Div([
            html.Span(wi["label"],
                      style={"color": _MUTED, "fontSize": "0.72rem", "flex": "1"}),
            html.Span(f"+{extra} signals → {new_rate:.0%} rate",
                      style={"color": color, "fontSize": "0.72rem", "fontWeight": 700}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "padding": "0.2rem 0", "borderBottom": f"1px solid {_CARD}"}))

    what_if_section = html.Div([
        html.Div("What-If: Threshold Relaxation",
                 style={"color": _MUTED, "fontSize": "0.75rem",
                        "fontWeight": 600, "marginBottom": "0.3rem"}),
        *wi_items,
    ], style={"marginBottom": "0.75rem"}) if wi_items else html.Div()

    # ── Per-pair table ─────────────────────────────────────────────────────────
    _th = {"padding": "0.2rem 0.4rem", "fontSize": "0.68rem", "color": _MUTED,
           "textAlign": "left", "borderBottom": f"1px solid {_CARD}"}
    _td = {"padding": "0.18rem 0.4rem", "fontSize": "0.7rem"}

    pair_rows = []
    for ps in analysis.get("pair_stats", []):
        freq = ps["triggered"] / ps["total"] if ps["total"] else 0
        pair_rows.append(html.Tr([
            html.Td(ps["pair"],                style={**_td, "color": "#38b6ff"}),
            html.Td(str(ps["total"]),          style={**_td, "color": _MUTED}),
            html.Td(str(ps["triggered"]),
                    style={**_td, "color": _BULL if ps["triggered"] > 2 else _TEXT,
                           "fontWeight": 700}),
            html.Td(f"{freq:.0%}",
                    style={**_td, "color": _BULL if freq >= 0.15 else
                           (_GOLD if freq >= 0.08 else _BEAR)}),
            html.Td(f"{ps['avg_score']:.1f}",
                    style={**_td, "color": _BULL if ps["avg_score"] >= 50 else
                           (_GOLD if ps["avg_score"] >= 35 else _BEAR)}),
        ]))

    pair_table = html.Table([
        html.Thead(html.Tr([
            html.Th(h, style=_th)
            for h in ["Pair", "Evals", "Triggered", "Rate", "Avg Score"]
        ])),
        html.Tbody(pair_rows),
    ], style={"width": "100%", "borderCollapse": "collapse"})

    return html.Div([
        kpis,
        score_chart,
        cond_chart,
        daily_chart,
        what_if_section,
        pair_table,
    ])


def _panel_style() -> dict:
    return {
        "background": _PANEL, "borderRadius": "6px",
        "padding": "1rem", "border": "1px solid #21262d",
        "marginBottom": "0.75rem",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Walk-Forward Results
# ═══════════════════════════════════════════════════════════════════════════════

def walk_forward_results(wf: dict) -> html.Div:
    """Render walk-forward validation results: summary chips + IS/OOS chart + window table."""
    windows = wf.get("windows", [])
    if not windows:
        return html.Div("No windows produced.", style={"color": _MUTED})

    avg_pf   = wf.get("avg_pf_oos", 0.0)
    avg_stab = wf.get("avg_stability", 0.0)
    best_ms  = wf.get("best_global_params", {}).get("min_score", "—")

    pf_color   = _BULL if avg_pf >= 1.5 else (_GOLD if avg_pf >= 1.0 else _BEAR)
    stab_color = _BULL if avg_stab >= 0.8 else (_GOLD if avg_stab >= 0.6 else _BEAR)

    def _chip(label, val, color):
        return html.Div([
            html.Div(val,   style={"color": color, "fontWeight": 700, "fontSize": "1.05rem"}),
            html.Div(label, style={"color": _MUTED, "fontSize": "0.68rem", "marginTop": "1px"}),
        ], style={"background": _CARD, "borderRadius": "4px",
                  "padding": "0.45rem 0.7rem", "textAlign": "center"})

    summary = html.Div([
        _chip("Avg OOS PF",   f"{avg_pf:.2f}",  pf_color),
        _chip("Stability",    f"{avg_stab:.2f}", stab_color),
        _chip("Best Score",   str(best_ms),      "#38b6ff"),
        _chip("Windows",      str(len(windows)), _TEXT),
    ], style={"display": "flex", "gap": "0.4rem", "marginBottom": "0.75rem",
              "flexWrap": "wrap"})

    # IS vs OOS line chart
    win_nums = [w["window"]  for w in windows]
    pf_is    = [w["pf_is"]   for w in windows]
    pf_oos   = [w["pf_oos"]  for w in windows]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=win_nums, y=pf_is, name="IS PF",
        line=dict(color="#38b6ff", width=1.5, dash="dash"),
        mode="lines+markers", marker=dict(size=4),
    ))
    fig.add_trace(go.Scatter(
        x=win_nums, y=pf_oos, name="OOS PF",
        line=dict(color="#ff8c00", width=2),
        mode="lines+markers", marker=dict(size=5),
    ))
    fig.add_hline(y=1.0, line_dash="dot", line_color="#ff3366",
                  annotation_text="PF=1.0", annotation_font_size=9)
    fig.update_layout(
        plot_bgcolor=_BG, paper_bgcolor=_BG,
        font=dict(color=_TEXT, size=9),
        margin=dict(l=8, r=8, t=8, b=8),
        height=150,
        legend=dict(orientation="h", y=1.15, font=dict(size=8)),
        xaxis=dict(showgrid=False, title=dict(text="Window", font=dict(size=8)),
                   tickfont=dict(size=8)),
        yaxis=dict(gridcolor=_CARD, title=dict(text="PF", font=dict(size=8)),
                   tickfont=dict(size=8)),
    )
    chart = dcc.Graph(figure=fig, config={"displayModeBar": False},
                      style={"borderRadius": "4px", "overflow": "hidden",
                             "marginBottom": "0.75rem"})

    # Window table
    _th = {"padding": "0.2rem 0.4rem", "fontSize": "0.68rem", "color": _MUTED,
           "textAlign": "left", "borderBottom": f"1px solid {_CARD}"}
    _td = {"padding": "0.18rem 0.4rem", "fontSize": "0.7rem"}

    rows = []
    for w in windows:
        stab  = w.get("stability", 0.0)
        sc    = _BULL if stab >= 0.8 else (_GOLD if stab >= 0.6 else _BEAR)
        oos_c = _BULL if w["pf_oos"] >= 1.0 else _BEAR
        rows.append(html.Tr([
            html.Td(str(w["window"]),                style={**_td, "color": _MUTED}),
            html.Td(f"{w['train_range'][0]} → {w['train_range'][1]}",
                    style={**_td, "color": _MUTED, "fontSize": "0.65rem"}),
            html.Td(f"{w['test_range'][0]} → {w['test_range'][1]}",
                    style={**_td, "color": _MUTED, "fontSize": "0.65rem"}),
            html.Td(f"{w['pf_is']:.2f}",            style={**_td, "color": "#38b6ff"}),
            html.Td(f"{w['pf_oos']:.2f}",           style={**_td, "color": oos_c, "fontWeight": 700}),
            html.Td(f"{stab:.2f}",                  style={**_td, "color": sc}),
            html.Td(str(w.get("trade_count_oos", 0)), style={**_td, "color": _TEXT}),
            html.Td(f"{w.get('win_rate_oos', 0):.0%}", style={**_td, "color": _TEXT}),
            html.Td(str(w.get("best_params", {}).get("min_score", "—")),
                    style={**_td, "color": "#ffd700"}),
        ]))

    table = html.Table([
        html.Thead(html.Tr([
            html.Th(h, style=_th)
            for h in ["#", "Train", "Test", "IS PF", "OOS PF", "Stability",
                      "Trades", "Win%", "Score"]
        ])),
        html.Tbody(rows),
    ], style={"width": "100%", "borderCollapse": "collapse"})

    return html.Div([summary, chart, table])


# ═══════════════════════════════════════════════════════════════════════════════
# Panel 6 — Learning Status (Adaptive / WFO / XGBoost)
# ═══════════════════════════════════════════════════════════════════════════════

def learning_panel(
    adaptive_summary: dict,
    wfo_summary: dict,
    ml_stats: dict | None,
    importance: dict | None,
    calibration_rows: list | None = None,
    sample_count: int = 0,
) -> html.Div:
    """Shows current state of all three learning systems: Adaptive, WFO, XGBoost."""

    _td = {"padding": "0.3rem 0.5rem", "fontSize": "0.78rem"}
    _th = {**_td, "color": _MUTED, "textAlign": "left",
           "borderBottom": "1px solid #30363d", "fontWeight": 600}
    sections = []

    # ── Adaptive Parameters ───────────────────────────────────────────────────
    sections.append(html.H5("Adaptive Parameters",
                             style={"color": _GOLD, "margin": "0 0 0.5rem 0"}))
    if adaptive_summary:
        rows = []
        for pair, s in sorted(adaptive_summary.items()):
            wr = s.get("win_rate_pct", 0)
            if   wr >= 60: tier_col, tier_lbl = _BULL, "LOOSE"
            elif wr >= 40: tier_col, tier_lbl = _MUTED, "BASE"
            elif wr >= 30: tier_col, tier_lbl = _GOLD, "TIGHT"
            else:           tier_col, tier_lbl = _BEAR, "STRICT"
            rows.append(html.Tr([
                html.Td(pair.replace("_", "/"), style={**_td, "color": _TEXT, **_mono}),
                html.Td(f"{wr:.0f}%",           style={**_td, "color": tier_col, "fontWeight": 700}),
                html.Td(tier_lbl,                style={**_td, "color": tier_col}),
                html.Td(f"±{s['cci_threshold']}",        style={**_td, "color": "#38b6ff"}),
                html.Td(f"{s['touch_band_mult']:.2f}×ATR", style={**_td, "color": _TEXT}),
                html.Td(f"{s['macd_bars_needed']}/3",    style={**_td, "color": _TEXT}),
            ]))
        sections.append(html.Table([
            html.Thead(html.Tr([html.Th(h, style=_th) for h in
                                ["Pair", "Win%", "Tier", "CCI", "Band", "MACD"]])),
            html.Tbody(rows),
        ], style={"width": "100%", "borderCollapse": "collapse", "marginBottom": "1.5rem"}))
    else:
        sections.append(html.P(
            "No adaptive data yet — accumulates after 5 closed trades per pair.",
            style={"color": _MUTED, "fontSize": "0.8rem", "marginBottom": "1.5rem"},
        ))

    # ── WFO Best Params ───────────────────────────────────────────────────────
    sections.append(html.H5("Walk-Forward Optimizer",
                             style={"color": "#38b6ff", "margin": "0 0 0.5rem 0"}))
    if wfo_summary:
        from datetime import datetime as _dt
        rows = []
        for pair, s in sorted(wfo_summary.items()):
            fitted_str = s.get("fitted_at", "—")
            try:
                fitted_str = _dt.fromisoformat(fitted_str).strftime("%m/%d %H:%M")
            except Exception:
                pass
            wr_col  = _BULL if s.get("win_rate_pct", 0) >= 50 else _BEAR
            adx_val = s.get("adx_threshold")
            adx_str = str(adx_val) if adx_val is not None else "—"
            adx_col = "#ffd700" if adx_val and adx_val <= 22 else ("#38b6ff" if adx_val and adx_val == 28 else "#ff9966")
            rows.append(html.Tr([
                html.Td(pair.replace("_", "/"),          style={**_td, "color": _TEXT, **_mono}),
                html.Td(fitted_str,                       style={**_td, "color": _MUTED, "fontSize": "0.7rem"}),
                html.Td(str(s.get("CCI_PERIOD", "—")),   style={**_td, "color": "#38b6ff"}),
                html.Td(s.get("MACD", "—"),               style={**_td, "color": "#38b6ff"}),
                html.Td(str(s.get("min_score", "—")),    style={**_td, "color": _GOLD, "fontWeight": 700}),
                html.Td(adx_str,                          style={**_td, "color": adx_col, "fontWeight": 700}),
                html.Td(f"{s.get('win_rate_pct', 0):.0f}%", style={**_td, "color": wr_col}),
                html.Td(str(s.get("total_trades", 0)),   style={**_td, "color": _TEXT}),
            ]))
        sections.append(html.Table([
            html.Thead(html.Tr([html.Th(h, style=_th) for h in
                                ["Pair", "Last Fit", "CCI", "MACD F/S/Sig",
                                 "Min Score", "ADX Thr", "Win%", "Trades"]])),
            html.Tbody(rows),
        ], style={"width": "100%", "borderCollapse": "collapse", "marginBottom": "1.5rem"}))
    else:
        sections.append(html.P(
            "Not yet fitted — runs automatically every Sunday 02:00 UTC.",
            style={"color": _MUTED, "fontSize": "0.8rem", "marginBottom": "1.5rem"},
        ))

    # ── XGBoost ML Model ──────────────────────────────────────────────────────
    sections.append(html.H5("XGBoost ML Model",
                             style={"color": _BULL, "margin": "0 0 0.5rem 0"}))
    n_samples = (ml_stats or {}).get("n_samples", 0)
    top_feats = (ml_stats or {}).get("top_features", [])
    sections.append(html.Div([
        html.Span("Training samples: ", style={"color": _MUTED, "fontSize": "0.8rem"}),
        html.Span(str(n_samples),        style={"color": _TEXT, "fontWeight": 700,
                                                 "fontSize": "0.8rem", **_mono}),
        html.Span(" / 50 min",           style={"color": _MUTED, "fontSize": "0.75rem"}),
    ], style={"marginBottom": "0.5rem"}))

    if importance:
        items = list(importance.items())[:12]
        max_v = max(v for _, v in items) if items else 1.0
        bars  = []
        for name, val in items:
            pct = (val / max_v) * 100 if max_v > 0 else 0
            col = _BULL if name in top_feats[:3] else "#38b6ff"
            bars.append(html.Div([
                html.Span(name, style={"color": _MUTED, "fontSize": "0.72rem",
                                       "minWidth": "170px", "display": "inline-block",
                                       **_mono}),
                html.Div(
                    html.Div(style={"width": f"{pct:.0f}%", "height": "100%",
                                    "background": col, "borderRadius": "4px"}),
                    style={"display": "inline-block", "verticalAlign": "middle",
                           "width": "180px", "background": "#21262d",
                           "height": "8px", "borderRadius": "4px"},
                ),
                html.Span(f"  {val:.4f}", style={"color": _MUTED, "fontSize": "0.68rem",
                                                   **_mono}),
            ], style={"display": "flex", "alignItems": "center",
                      "gap": "0.4rem", "marginBottom": "3px"}))
        sections.append(html.Div(bars))
    elif n_samples >= 50:
        sections.append(html.P("Model trained — importance not loaded.",
                                style={"color": _MUTED, "fontSize": "0.8rem"}))
    else:
        remaining = max(0, 50 - sample_count) if sample_count else max(0, 50 - n_samples)
        sections.append(html.P(
            f"Model not trained yet — {remaining} more closed trades needed "
            f"(or use Seed from Backtest to bootstrap immediately).",
            style={"color": _MUTED, "fontSize": "0.8rem"},
        ))

    # ── Calibration strip ─────────────────────────────────────────────────────
    sections.append(html.H5("Model Calibration (last 20 signals)",
                             style={"color": _MUTED, "margin": "1.5rem 0 0.5rem 0",
                                    "fontSize": "0.85rem"}))
    if calibration_rows:
        cal_rows = []
        for r in calibration_rows:
            prob    = r.get("predicted_prob")
            actual  = r.get("actual", "")
            is_win  = actual == "win"
            dir_col = _BULL if r.get("direction") == "long" else _BEAR
            if prob is None:
                prob_cell = html.Td("—", style={**_td, "color": _MUTED})
            else:
                prob_pct = prob * 100
                prob_col = _BULL if prob_pct >= 60 else (_GOLD if prob_pct >= 45 else _BEAR)
                prob_cell = html.Td(f"{prob_pct:.0f}%",
                                    style={**_td, "color": prob_col, "fontWeight": 700, **_mono})
            outcome_dot = "●" if is_win else "○"
            outcome_col = _BULL if is_win else _BEAR
            cal_rows.append(html.Tr([
                html.Td(r.get("pair", "").replace("_", "/"),
                        style={**_td, "color": _TEXT, **_mono}),
                html.Td(r.get("direction", ""),
                        style={**_td, "color": dir_col}),
                html.Td(str(r.get("score", "")),
                        style={**_td, "color": _MUTED, **_mono}),
                prob_cell,
                html.Td(outcome_dot,
                        style={**_td, "color": outcome_col, "fontSize": "1rem"}),
            ]))
        sections.append(html.Table([
            html.Thead(html.Tr([html.Th(h, style=_th) for h in
                                ["Pair", "Dir", "Score", "Pred%", "Win?"]])),
            html.Tbody(cal_rows),
        ], style={"width": "100%", "borderCollapse": "collapse"}))
    else:
        sections.append(html.P(
            "No calibration data yet — open the panel after trades have closed.",
            style={"color": _MUTED, "fontSize": "0.78rem"},
        ))

    return html.Div(sections)
