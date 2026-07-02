"""
Breakout-retest strategy — genuinely different entry mechanic from EMA-bounce
and the shelved trend-follow experiment (see project_trend_follow_experiment
memory). Where those fire on price *reaching* a level (EMA touch), this fires
on price *leaving* a level (break of structure) and then confirming the break
was real by retesting it and holding.

Reuses existing structure/price-action detection rather than building new:
  - engine.strategy_market_structure: detect_pivots, classify_structure,
    detect_bos_choch (the break-of-structure event itself), get_sr_zones-style
    level identification (we use the raw pivot level directly here)
  - engine.strategy_price_action: detect_patterns (the rejection candle at
    the retest)

Design choices (see tasks/todo.md for full rationale):
  - No ADX gate. Breakouts happen as a trend is starting — ADX is often still
    rising through the EMA-bounce/trend-follow threshold during the
    breakout+retest window, not yet past it. Gating here would likely miss
    the entry.
  - Same session gate as the other two strategies, for consistency.
  - No H4/confirm-TF alignment gate (unlike the other two strategies) — kept
    out for this first pass to isolate the core breakout-retest mechanic;
    can be added later if backtest results suggest it would help.
"""
import logging

import numpy as np
import pandas as pd

import config
from engine.indicators import atr, macd_full
from engine.strategy_market_structure import detect_pivots, classify_structure, detect_bos_choch
from engine.strategy_price_action import detect_patterns, _BULLISH, _BEARISH

logger = logging.getLogger(__name__)

_buy_diag:  dict = {}
_sell_diag: dict = {}


def get_last_diag(pair: str, direction: str) -> dict:
    if direction == "long":
        return dict(_buy_diag.get(pair, {}))
    return dict(_sell_diag.get(pair, {}))


def _get_pip(pair: str) -> float:
    return 0.01 if "JPY" in pair.upper() else 0.0001


def check_buy_signal(pair: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame,
                     adaptive: dict | None = None) -> float:
    """
    Conditions:
      c1 — Bullish Break of Structure within the last 5 bars (hard gate —
           nothing to retest without a recent break)
      c2 — Current bar's low has returned to within band×ATR of the broken
           resistance level (the retest)
      c3 — The break hasn't been invalidated — close hasn't fallen back
           below the broken level beyond tolerance
      c4 — A bullish rejection candle at the current bar (pin bar, engulfing,
           hammer, marubozu, morning star)
      c5 — MACD histogram positive (momentum still favors the breakout
           direction)
    """
    if len(df_h1) < 50 or len(df_h4) < 50:
        return 0.0

    ap               = adaptive or {}
    retest_band_mult = ap.get("retest_band_mult", 0.3)

    # ── Session gate — same window as the other two strategies ───────────────
    last_bar_hour = df_h1.index[-1].hour
    if not (config.SESSION_START_UTC <= last_bar_hour < config.SESSION_END_UTC):
        return 0.0

    pivots    = detect_pivots(df_h1)
    structure = classify_structure(pivots)
    bos       = detect_bos_choch(df_h1, pivots, structure)

    # ── Hard gate — must have a recent bullish break to retest ───────────────
    c1 = bos["bos"] and bos["bos_direction"] == "bullish" and bool(pivots["highs"])
    if not c1:
        _buy_diag[pair] = dict(c1=False, c2=False, c3=False, c4=False, c5=False,
                               broken_level=None, patterns=[])
        return 0.0

    broken_level = pivots["highs"][-1]
    atr_val      = float(atr(df_h1["high"], df_h1["low"], df_h1["close"], 14).iloc[-1])
    band         = retest_band_mult * atr_val

    low_now   = float(df_h1["low"].iloc[-1])
    close_now = float(df_h1["close"].iloc[-1])

    c2 = abs(low_now - broken_level) <= band
    c3 = close_now >= broken_level - band

    patterns          = detect_patterns(df_h1)
    bullish_patterns   = [p for p in patterns if p in _BULLISH]
    c4 = len(bullish_patterns) >= 1

    _, _, macd_hist = macd_full(df_h1["close"], config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
    c5 = len(macd_hist) >= 1 and float(macd_hist.iloc[-1]) > 0

    passed = sum([c2, c3, c4, c5])
    score  = round(25 * passed / 4)
    logger.debug(
        "BREAKOUT-RETEST BUY %s  c1(bos)=True c2(retest)=%s c3(valid)=%s "
        "c4(pattern)=%s(%s) c5(macd>0)=%s -> %d",
        pair, c2, c3, c4, bullish_patterns, c5, score,
    )

    _buy_diag[pair] = dict(c1=c1, c2=c2, c3=c3, c4=c4, c5=c5,
                           broken_level=broken_level, patterns=bullish_patterns)
    return float(score)


def check_sell_signal(pair: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame,
                      adaptive: dict | None = None) -> float:
    """Mirror of check_buy_signal — bearish break of a support level, retested from above."""
    if len(df_h1) < 50 or len(df_h4) < 50:
        return 0.0

    ap               = adaptive or {}
    retest_band_mult = ap.get("retest_band_mult", 0.3)

    last_bar_hour = df_h1.index[-1].hour
    if not (config.SESSION_START_UTC <= last_bar_hour < config.SESSION_END_UTC):
        return 0.0

    pivots    = detect_pivots(df_h1)
    structure = classify_structure(pivots)
    bos       = detect_bos_choch(df_h1, pivots, structure)

    c1 = bos["bos"] and bos["bos_direction"] == "bearish" and bool(pivots["lows"])
    if not c1:
        _sell_diag[pair] = dict(c1=False, c2=False, c3=False, c4=False, c5=False,
                                broken_level=None, patterns=[])
        return 0.0

    broken_level = pivots["lows"][-1]
    atr_val      = float(atr(df_h1["high"], df_h1["low"], df_h1["close"], 14).iloc[-1])
    band         = retest_band_mult * atr_val

    high_now  = float(df_h1["high"].iloc[-1])
    close_now = float(df_h1["close"].iloc[-1])

    c2 = abs(high_now - broken_level) <= band
    c3 = close_now <= broken_level + band

    patterns          = detect_patterns(df_h1)
    bearish_patterns   = [p for p in patterns if p in _BEARISH]
    c4 = len(bearish_patterns) >= 1

    _, _, macd_hist = macd_full(df_h1["close"], config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
    c5 = len(macd_hist) >= 1 and float(macd_hist.iloc[-1]) < 0

    passed = sum([c2, c3, c4, c5])
    score  = round(25 * passed / 4)
    logger.debug(
        "BREAKOUT-RETEST SELL %s  c1(bos)=True c2(retest)=%s c3(valid)=%s "
        "c4(pattern)=%s(%s) c5(macd<0)=%s -> %d",
        pair, c2, c3, c4, bearish_patterns, c5, score,
    )

    _sell_diag[pair] = dict(c1=c1, c2=c2, c3=c3, c4=c4, c5=c5,
                            broken_level=broken_level, patterns=bearish_patterns)
    return float(score)


def get_stop_loss(pair: str, df_h1: pd.DataFrame, direction: str) -> float:
    """
    SL beyond the retested zone boundary (the level that must hold for the
    breakout to remain valid), not the EMA-based level strategy_ema_cci_macd
    uses. Same ATR-fallback / minimum-distance pattern as that module.
    """
    pivots   = detect_pivots(df_h1)
    atr_val  = float(atr(df_h1["high"], df_h1["low"], df_h1["close"], 14).iloc[-1])
    entry    = float(df_h1["close"].iloc[-1])
    pip      = _get_pip(pair)
    min_dist = pip * config.MIN_SL_PIPS

    if direction == "long":
        broken_level = pivots["highs"][-1] if pivots["highs"] else entry - atr_val
        sl = broken_level - 0.3 * atr_val - pip
        if sl >= entry:
            sl = entry - max(atr_val * 1.5, min_dist)
        sl = min(sl, entry - min_dist)
    else:
        broken_level = pivots["lows"][-1] if pivots["lows"] else entry + atr_val
        sl = broken_level + 0.3 * atr_val + pip
        if sl <= entry:
            sl = entry + max(atr_val * 1.5, min_dist)
        sl = max(sl, entry + min_dist)

    decimals = 3 if "JPY" in pair.upper() else 5
    return round(sl, decimals)


def clear_cache() -> None:
    """Reset diagnostics — used in tests."""
    _buy_diag.clear()
    _sell_diag.clear()
