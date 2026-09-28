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
  - Optional consolidation gate (config.BREAKOUT_CONSOLIDATION_FILTER_ENABLED,
    off by default) — added 2026-07-29 once this became the roster's
    primary trend strategy: unlike ADX, a compressed 34/100/200 EMA ribbon
    stays compressed through the exact window where a fakeout BOS happens
    inside chop, so it catches what the ADX gate's design note above says
    ADX itself can't reliably catch here.
"""
import logging

import numpy as np
import pandas as pd

import config
from engine.indicators import atr, ema, macd_full, rsi
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
    """1 pip = 0.01 for JPY pairs and XAU_USD (OANDA pipLocation -2), 0.0001 for all other FX pairs."""
    p = pair.upper()
    return 0.01 if ("JPY" in p or "XAU" in p) else 0.0001


def _is_consolidated(df_h1: pd.DataFrame) -> bool:
    """True when the 34/100/200 EMA ribbon is compressed (ATR-normalized
    spread below config.BREAKOUT_MIN_MA_SPREAD_ATR) — same ribbon the chart
    already plots. Needs enough bars for EMA200 to be meaningful; with too
    little data, don't block (can't judge reliably either way)."""
    if not config.BREAKOUT_CONSOLIDATION_FILTER_ENABLED or len(df_h1) < 200:
        return False
    atr_val = float(atr(df_h1["high"], df_h1["low"], df_h1["close"], 14).iloc[-1])
    if not atr_val or np.isnan(atr_val):
        return False
    emas = ribbon(df_h1)
    spread_atr = (max(emas) - min(emas)) / atr_val
    return spread_atr < config.BREAKOUT_MIN_MA_SPREAD_ATR


def ribbon(df_h1: pd.DataFrame) -> list:
    """Latest [fast, 100, 200] MA values — fast period and the 200's type
    (EMA/SMA) come from config so they can be A/B backtested."""
    close = df_h1["close"]
    slow = (close.rolling(200).mean() if config.MA_SLOW_TYPE == "sma" else ema(close, 200)).iloc[-1]
    return [ema(close, config.MA_FAST_PERIOD).iloc[-1], ema(close, 100).iloc[-1], slow]


def _momentum_ok(df_h1: pd.DataFrame, direction: str) -> bool:
    """c5 — momentum still with the breakout. "macd": histogram sign (original);
    "rsi50": RSI(14) on the trade's side of 50."""
    if config.BREAKOUT_MOMENTUM == "rsi50":
        r = rsi(df_h1["close"], 14)
        if not len(r) or np.isnan(r.iloc[-1]):
            return False
        return r.iloc[-1] > 50 if direction == "long" else r.iloc[-1] < 50
    _, _, h = macd_full(df_h1["close"], config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
    return len(h) >= 1 and (float(h.iloc[-1]) > 0 if direction == "long" else float(h.iloc[-1]) < 0)


def _ma_aligned(df_h1: pd.DataFrame, direction: str) -> bool:
    """Fast EMA (config.MA_FAST_PERIOD, 34) on the trade's side of EMA100 (the "MA cross" state) — the
    faster average has crossed in the breakout direction."""
    if len(df_h1) < 100:
        return False
    fast, slow = ribbon(df_h1)[:2]
    return fast > slow if direction == "long" else fast < slow


def _strict_entry_ok(df_h1: pd.DataFrame, direction: str, retest: bool,
                     held: bool, candle: bool) -> bool:
    """config.BREAKOUT_STRICT_ENTRY: the user's entry rules as hard
    requirements instead of optional points — a break (c1, already a hard
    gate), a real retest of the broken level, the break holding, a
    confirming candlestick pattern, and the MAs crossed in the trade
    direction. Off = original scoring behavior (any 2 of 4 conditions)."""
    if not config.BREAKOUT_STRICT_ENTRY:
        return True
    return retest and held and candle and _ma_aligned(df_h1, direction)


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

    # ── Consolidation gate (optional) — skip fakeout BOS inside chop ─────────
    if _is_consolidated(df_h1):
        _buy_diag[pair] = dict(c1=False, c2=False, c3=False, c4=False, c5=False,
                               broken_level=None, patterns=[], consolidated=True)
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

    c5 = _momentum_ok(df_h1, "long")

    if not _strict_entry_ok(df_h1, "long", c2, c3, c4):
        _buy_diag[pair] = dict(c1=c1, c2=c2, c3=c3, c4=c4, c5=c5, broken_level=broken_level,
                               patterns=bullish_patterns, strict_blocked=True)
        return 0.0

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

    if _is_consolidated(df_h1):
        _sell_diag[pair] = dict(c1=False, c2=False, c3=False, c4=False, c5=False,
                                broken_level=None, patterns=[], consolidated=True)
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

    c5 = _momentum_ok(df_h1, "short")

    if not _strict_entry_ok(df_h1, "short", c2, c3, c4):
        _sell_diag[pair] = dict(c1=c1, c2=c2, c3=c3, c4=c4, c5=c5, broken_level=broken_level,
                                patterns=bearish_patterns, strict_blocked=True)
        return 0.0

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
    min_dist = config.min_sl_distance(pair, pip, atr_val)

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
