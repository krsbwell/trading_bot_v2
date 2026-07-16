"""
Logging layer for the signal and learning engines.

Lifecycle:
  1. signal fires    → record_signal(trade_id, signal, size, risk)  [cached in memory]
  2. signal skipped  → record_skip(signal)                          [written immediately]
  3. trade closes    → record_close(trade_id, outcome, ...)         [cache + outcome → CSV]

All CSV writes are append-only; the header is written on first creation.
"""
import csv
import logging
import os
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
# Resolved relative to the project root so they work regardless of cwd.
_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNAL_LOG = os.path.join(_ROOT, "data", "signal_log.csv")

SIGNAL_FIELDS = [
    "timestamp", "pair", "market", "direction",
    "timeframe_primary", "timeframe_confirm",
    "entry_price", "stop_loss", "tp1", "tp2", "tp3", "tp_level_hit",
    "position_size", "risk_dollar", "risk_pct",
    "confluence_score", "ema_score", "structure_score", "pa_score",
    "pattern_name", "candle_body_ratio", "upper_wick_ratio", "lower_wick_ratio",
    "cci_at_signal", "macd_hist_at_signal", "ema_short_period", "ema_mid_period",
    "atr_pips", "h4_trend", "d_trend",
    "market_structure", "was_at_sr_zone", "bos_confirmed",
    "ml_win_prob_at_entry", "outcome", "pnl_pips", "pnl_dollar",
    "hold_hours", "session", "hour_utc",
]

# In-memory store: trade_id → {signal, position_size, risk_dollar, open_time}
_pending: dict[str, dict] = {}


# ── Public API ────────────────────────────────────────────────────────────────

def record_signal(
    trade_id: str,
    signal: dict,
    position_size: float,
    risk_dollar: float,
) -> None:
    """
    Cache signal features when a trade opens.
    The complete row is written to CSV when the trade closes via record_close().
    """
    _pending[trade_id] = {
        "signal":        signal,
        "position_size": position_size,
        "risk_dollar":   risk_dollar,
        "open_time":     datetime.now(timezone.utc),
    }
    logger.debug("Pending signal cached — trade_id=%s  pair=%s", trade_id, signal.get("pair"))


def record_close(
    trade_id: str,
    outcome: str,          # "win" | "loss"
    pnl_pips: float,
    pnl_dollar: float,
    hold_hours: float,
    tp_level_hit: str,     # "tp1" | "tp2" | "tp3" | "sl" | "manual"
    log_path: str = SIGNAL_LOG,
) -> None:
    """
    Write a complete row to signal_log.csv for a closed trade.
    Merges the cached signal features with the outcome data.
    """
    pending = _pending.pop(trade_id, None)
    if pending is None:
        logger.warning("record_close: no cached signal for trade_id=%s — skipping log", trade_id)
        return

    row = _build_row(
        signal       = pending["signal"],
        position_size = pending["position_size"],
        risk_dollar  = pending["risk_dollar"],
        outcome      = outcome,
        pnl_pips     = pnl_pips,
        pnl_dollar   = pnl_dollar,
        hold_hours   = hold_hours,
        tp_level_hit = tp_level_hit,
        timestamp    = datetime.now(timezone.utc),
    )
    _write_row(log_path, SIGNAL_FIELDS, row)
    logger.info(
        "Trade logged — trade_id=%s  pair=%s  outcome=%s  pnl=$%.2f",
        trade_id, pending["signal"].get("pair"), outcome, pnl_dollar,
    )


def record_skip(signal: dict, log_path: str = SIGNAL_LOG) -> None:
    """
    Log a signal that was scored but not traded (gate-blocked, below threshold,
    watching, etc.) with outcome='skipped'. Feeds the shadow-outcome resolver
    (learning/shadow_outcomes.py), which later walks forward through candles
    to label these 'would_win'/'would_lose' — near-miss and rejected setups
    reveal whether the gates that blocked them were actually right to.

    Best-effort: called from _process_pair on every live scan, so a logging
    failure here must never interrupt real signal processing.
    """
    if not signal.get("entry") or not signal.get("stop_loss"):
        return   # nothing to replay later without a real entry/SL (e.g. ATR-gated)
    try:
        row = _build_row(
            signal        = signal,
            position_size = 0,
            risk_dollar   = 0,
            outcome       = "skipped",
            pnl_pips      = 0,
            pnl_dollar    = 0,
            hold_hours    = 0,
            tp_level_hit  = "",
            timestamp     = datetime.now(timezone.utc),
        )
        _write_row(log_path, SIGNAL_FIELDS, row)
        logger.debug("Skipped signal logged — pair=%s  score=%s", signal.get("pair"), signal.get("score"))
    except Exception as exc:
        logger.debug("record_skip failed for %s: %s", signal.get("pair"), exc)


def pending_count() -> int:
    """Number of trades currently awaiting a close log."""
    return len(_pending)


def clear_pending() -> None:
    """Reset the in-memory cache (used in tests)."""
    _pending.clear()


# ── Row builder ───────────────────────────────────────────────────────────────

def _build_row(
    signal: dict,
    position_size: float,
    risk_dollar: float,
    outcome: str,
    pnl_pips: float,
    pnl_dollar: float,
    hold_hours: float,
    tp_level_hit: str,
    timestamp: datetime,
) -> dict:
    ema_periods  = signal.get("ema_periods") or (0, 0, 0)
    patterns     = signal.get("patterns") or []
    tp_levels    = signal.get("tp_levels") or {}

    return {
        "timestamp":            timestamp.isoformat(),
        "pair":                 signal.get("pair", ""),
        "market":               signal.get("market", ""),
        "direction":            signal.get("direction", ""),
        "timeframe_primary":    config.TIMEFRAMES.get("primary", "H1"),
        "timeframe_confirm":    config.TIMEFRAMES.get("confirm", "H4"),
        "entry_price":          signal.get("entry", ""),
        "stop_loss":            signal.get("stop_loss", ""),
        "tp1":                  tp_levels.get("tp1", ""),
        "tp2":                  tp_levels.get("tp2", ""),
        "tp3":                  tp_levels.get("tp3", ""),
        "tp_level_hit":         tp_level_hit,
        "position_size":        position_size,
        "risk_dollar":          round(risk_dollar, 2),
        "risk_pct":             0.01,
        "confluence_score":     signal.get("score", ""),
        "ema_score":            signal.get("ema_score", ""),
        "structure_score":      signal.get("structure_score", ""),
        "pa_score":             signal.get("pa_score", ""),
        "pattern_name":         "|".join(patterns),
        "candle_body_ratio":    round(signal.get("candle_body_ratio", 0), 4),
        "upper_wick_ratio":     round(signal.get("upper_wick_ratio", 0), 4),
        "lower_wick_ratio":     round(signal.get("lower_wick_ratio", 0), 4),
        "cci_at_signal":        round(signal.get("cci_at_signal", 0), 4) if signal.get("cci_at_signal") is not None else "",
        "macd_hist_at_signal":  round(signal.get("macd_hist_at_signal", 0), 6) if signal.get("macd_hist_at_signal") is not None else "",
        "ema_short_period":     ema_periods[0] if ema_periods else "",
        "ema_mid_period":       ema_periods[1] if ema_periods else "",
        "atr_pips":             round(signal.get("atr_pips", 0), 2) if signal.get("atr_pips") is not None else "",
        "h4_trend":             signal.get("h4_trend", ""),
        "d_trend":              signal.get("d_trend", ""),
        "market_structure":     signal.get("market_structure", ""),
        "was_at_sr_zone":       int(bool(signal.get("was_at_sr_zone", False))),
        "bos_confirmed":        int(bool(signal.get("bos_confirmed", False))),
        "ml_win_prob_at_entry": signal.get("ml_win_prob", ""),
        "outcome":              outcome,
        "pnl_pips":             round(pnl_pips, 1),
        "pnl_dollar":           round(pnl_dollar, 2),
        "hold_hours":           round(hold_hours, 2),
        "session":              _get_session(timestamp),
        "hour_utc":             timestamp.hour,
    }


# ── Session helper ────────────────────────────────────────────────────────────

def _get_session(dt: datetime) -> str:
    """Classify UTC hour into the dominant trading session."""
    h = dt.hour
    if 8 <= h < 13:
        return "london"
    if 13 <= h < 16:
        return "london_ny"   # overlap — highest liquidity
    if 16 <= h < 21:
        return "new_york"
    return "asian"            # 21-8 UTC


# ── CSV writer ────────────────────────────────────────────────────────────────

def _write_row(path: str, fields: list, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow({k: data.get(k, "") for k in fields})
