"""
Thread-safe shared state between the bot scheduler and the Dash dashboard.

The bot writes to this store via update() / set_pending_alert().
The dashboard reads from it inside every callback.
"""
import threading
from datetime import datetime, timezone
from typing import Any

import config

_lock = threading.Lock()

_state: dict = {
    # ── Mode ──────────────────────────────────────────────────────────────────
    "mode":    config.MODE,        # "paper" | "live"
    "halted":  False,

    # ── Connector health ──────────────────────────────────────────────────────
    "oanda_ok":  None,   # True=connected, False=error, None=not tested
    "alpaca_ok": None,

    # ── Account ───────────────────────────────────────────────────────────────
    "account": {
        "balance":          500.0,
        "nav":              500.0,
        "unrealized_pnl":   0.0,
        "open_trade_count": 0,
    },

    # ── Trades ────────────────────────────────────────────────────────────────
    "open_trades":    [],   # list of trade dicts (from PaperTrader or TradeManager)
    "closed_trades":  [],   # list of closed trade dicts
    "pending_orders": [],   # list of pending limit orders

    # ── Signals ───────────────────────────────────────────────────────────────
    # pair → {"score": int, "direction": str, "status": "SIGNAL"|"WATCHING"|"NEUTRAL"}
    "signals": {p: {"score": 0, "direction": "—", "status": "NEUTRAL"}
                for p in (config.FOREX_PAIRS
                          + getattr(config, "FOREX_WATCH", [])
                          + config.CRYPTO_PAIRS)},

    # ── Pre-trade alert ───────────────────────────────────────────────────────
    "pending_alert":    None,   # signal dict when alert is showing
    "alert_countdown":  0,
    "alert_confirmed":  False,
    "alert_cancelled":  False,
    "missed_signals":   [],     # signals that expired without user interaction

    # ── Chart ─────────────────────────────────────────────────────────────────
    "chart_pair": config.FOREX_PAIRS[0],
    "chart_tf":   config.TIMEFRAMES["primary"],

    # ── Candle cache  (pair+tf → DataFrame) ───────────────────────────────────
    "candles": {},

    # ── Connectors (set by main.py so callbacks can fetch fresh data) ─────────
    "forex_connector":  None,
    "crypto_connector": None,

    # ── Adjustable signal threshold (slider in dashboard) ────────────────────
    "min_score": config.MIN_CONFLUENCE_SCORE,   # user can override at runtime

    # ── Equity curve (list of {t, balance, nav}, appended each hourly tick) ──
    "equity_curve": [],

    # ── Signal details (full signal dict per pair, for chart overlays) ────────
    "signal_details": {},   # pair → {entry, stop_loss, tp_levels, direction, …}

    # ── Per-pair scan health — last time _process_pair completed for this
    # pair, whether or not it produced a signal. Used by the dashboard's bot
    # health indicator to detect a pair silently going dark (e.g. an
    # exception on an earlier pair in the loop previously killed every pair
    # after it, with no visible symptom until the equity curve went flat —
    # see bugs_scheduler_reliability memory). Distinct from the single global
    # "last_scan_time" below, which only tells you the loop started, not that
    # every pair in it actually completed.
    "pair_scan_times": {},   # pair → datetime (UTC) of last successful scan

    # ── Paper trader reference (for dashboard order placement) ───────────────
    "paper_trader": None,

    # ── Cooldown tracking: pair → datetime of last trade close ───────────────
    "trade_cooldowns": {},

    # ── Learning ──────────────────────────────────────────────────────────────
    "suggestions": [],
    "ml_stats":    {"accuracy": None, "top_features": [], "n_samples": 0},
}


# ── Read ──────────────────────────────────────────────────────────────────────

def get() -> dict:
    with _lock:
        return dict(_state)


def get_key(key: str, default: Any = None) -> Any:
    with _lock:
        return _state.get(key, default)


# ── Write ─────────────────────────────────────────────────────────────────────

def update(**kwargs) -> None:
    with _lock:
        _state.update(kwargs)


def record_pair_scan(pair: str) -> None:
    """Mark `pair` as successfully scanned right now — see pair_scan_times above."""
    with _lock:
        _state.setdefault("pair_scan_times", {})[pair] = datetime.now(timezone.utc)


def update_signal(pair: str, score: int, direction: str,
                  timeframe: str = "H1") -> None:
    _threshold = get_key("min_score", config.MIN_CONFLUENCE_SCORE)
    status = ("SIGNAL"   if score >= _threshold else
              "WATCHING" if score >= 50          else
              "SCANNING" if score >= 35          else "NEUTRAL")
    with _lock:
        _state["signals"][pair] = {
            "score":     score,
            "direction": direction,
            "status":    status,
            "timeframe": timeframe,
        }


def set_pending_alert(signal: dict) -> None:
    with _lock:
        _state["pending_alert"]   = signal
        _state["alert_countdown"] = config.ALERT_DELAY_SECONDS
        _state["alert_confirmed"] = False
        _state["alert_cancelled"] = False


def clear_alert(expired: bool = False) -> None:
    """Clear the active alert. If expired=True, save it to missed_signals."""
    with _lock:
        if expired and _state.get("pending_alert"):
            missed = _state.setdefault("missed_signals", [])
            import time as _t
            missed.append({**_state["pending_alert"], "_missed_at": int(_t.time())})
            _state["missed_signals"] = missed[-10:]   # keep last 10 only
        _state["pending_alert"]   = None
        _state["alert_countdown"] = 0


def tick_countdown() -> int:
    """Decrement alert countdown by 1 second. Returns new value."""
    with _lock:
        new = max(0, _state.get("alert_countdown", 0) - 1)
        _state["alert_countdown"] = new
        return new


def append_equity(balance: float, nav: float) -> None:
    """Append a timestamped equity snapshot (called after every hourly tick)."""
    import time as _time
    with _lock:
        curve = _state["equity_curve"]
        curve.append({"t": int(_time.time()), "balance": balance, "nav": nav})
        # Keep at most 10 000 points (~14 months of hourly ticks)
        if len(curve) > 10_000:
            _state["equity_curve"] = curve[-10_000:]


def update_signal_detail(pair: str, detail: dict) -> None:
    """Store the full signal dict for a pair (used for chart overlays)."""
    with _lock:
        detail["_stored_at"] = datetime.now(timezone.utc)
        _state["signal_details"][pair] = detail


def cache_candles(pair: str, tf: str, df) -> None:
    with _lock:
        _state["candles"][f"{pair}_{tf}"] = df


def get_candles(pair: str, tf: str):
    with _lock:
        return _state["candles"].get(f"{pair}_{tf}")
