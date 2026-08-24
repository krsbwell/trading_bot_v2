"""
ATR-based stop-loss / take-profit distance calculator — one reusable
function instead of each strategy re-deriving its own ATR stop logic.

Note: ATR-based *trailing* stops were already tried and backtest-rejected
on 2026-07-18 (see tasks/todo.md / project_atr_trailing_and_live_gap
memory — higher WR but worse PF/PnL). This module is a different use —
the *initial* SL/TP distance for engine.strategy_adaptive, computed once
at entry, not a trailing mechanism — and must be backtested on its own
merits before any live pair uses it. It is not assumed to work because
the earlier trailing experiment didn't.

The multiplier/period knobs here are deliberately configurable (see
config.ADAPTIVE_STRATEGY) rather than hard-coded, per this project's
existing per-pair-override convention (STRATEGY_OVERRIDE, TP_RR_PER_PAIR,
etc.) — a single universal ATR multiplier is not assumed to be correct
across every pair.

risk.risk_manager remains the sole enforcer of RISK_PER_TRADE and the
existing min-stop-distance floor (config.min_sl_pips_for) — this module
only proposes a distance; it never bypasses that.
"""
import config
from engine.indicators import atr as calc_atr


def _pip_size(pair: str) -> float:
    """Matches the pip-size convention used throughout engine/*.py and
    risk/risk_manager.py: 0.01 for JPY/XAU pairs, 0.0001 otherwise."""
    p = pair.upper()
    return 0.01 if ("JPY" in p or "XAU" in p) else 0.0001


def compute_atr_stops(df, direction: str, pair: str,
                       period: int = 14,
                       sl_mult: float = 1.5,
                       tp_mult: float = 3.0) -> dict:
    """
    Compute an ATR-based SL/TP pair for a prospective entry at the last
    close of `df`.

    direction : "long" | "short"
    Returns {"entry": float, "stop_loss": float, "take_profit": float,
             "atr": float, "atr_pips": float, "sl_pips": float}
    or None if there isn't enough data / ATR is unavailable (mirrors this
    codebase's existing "return 0.0 / None on insufficient data" pattern
    rather than raising).
    """
    if df is None or len(df) < period + 1:
        return None

    atr_series = calc_atr(df["high"], df["low"], df["close"], period)
    atr_val = float(atr_series.iloc[-1])
    if atr_val != atr_val or atr_val <= 0:   # NaN or non-positive
        return None

    entry = float(df["close"].iloc[-1])
    pip = _pip_size(pair)

    sl_distance = atr_val * sl_mult
    tp_distance = atr_val * tp_mult

    # Enforce this pair's existing minimum stop distance — same floor
    # every other strategy's get_stop_loss() already respects, so an
    # adaptive-strategy trade is never allowed a tighter stop than any
    # other strategy would be.
    min_dist = pip * config.min_sl_pips_for(pair)
    sl_distance = max(sl_distance, min_dist)

    if direction == "long":
        stop_loss = entry - sl_distance
        take_profit = entry + tp_distance
    else:
        stop_loss = entry + sl_distance
        take_profit = entry - tp_distance

    decimals = 3 if "JPY" in pair.upper() else 5
    return {
        "entry": entry,
        "stop_loss": round(stop_loss, decimals),
        "take_profit": round(take_profit, decimals),
        "atr": atr_val,
        "atr_pips": round(atr_val / pip, 1),
        "sl_pips": round(sl_distance / pip, 1),
    }
