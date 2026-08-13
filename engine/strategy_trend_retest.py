"""
Trend-retest strategy — "trade with the trend": H4 sets a hard directional
bias, entries only come from a break-of-structure *in that same direction*
being retested and confirmed by price action. Built 2026-08-12 from user-
supplied reference material (break-and-retest SOPs, a simple 3-confluence
forex strategy) after live EMA-bounce underperformed badly (18.8% WR / -$34.60
over 16 real trades) — see tasks/todo.md for the full design discussion and
web-research validation.

Genuinely different from both existing strategies:
  - strategy_ema_cci_macd ("EMA-bounce"): mean-reversion, fires on price
    *touching* an EMA, hard-blocked during real trends (ADX gate). Opposite
    premise from "trade with the trend."
  - strategy_breakout_retest: same break+retest+price-action mechanic this
    module is built on, but with no higher-timeframe trend gate at all —
    any BOS in either direction qualifies. This module adds that gate as a
    hard requirement (validated as documented best practice, not a guess —
    see tasks/todo.md sources) and demotes MACD from an equal-weight
    condition to a small bonus, matching the reference material's "trend +
    location + confirmation are required, everything else is optional"
    philosophy. Deliberately a new module rather than an in-place edit to
    strategy_breakout_retest.py — GBP_USD is live on that exact behavior
    today; this gets backtested standalone before anything live changes.

Conditions:
  HARD GATE 1 — H4 trend bias: H4 close vs its EMA(mid period, same fixed
                period used everywhere else — config.EMA_FIXED_PERIODS) must
                agree with the signal direction. No signal at all otherwise
                (0.0 returned immediately) — matches the documented
                multi-timeframe principle "if higher-timeframe conditions
                are not aligned, no trade is allowed" (see tasks/todo.md
                sources), not soft-scored like EMA-bounce's equivalent c2.
  HARD GATE 2 — Break of Structure in that same direction within the recent
                window (reuses strategy_market_structure, identical
                detection to strategy_breakout_retest).
  c1 — Retest: current bar has returned to within band×ATR of the broken
       level.
  c2 — Break not invalidated: close hasn't fallen back through the level
       beyond tolerance.
  c3 — Price-action rejection candle at the retest (reuses
       strategy_price_action's existing pattern detector).
  BONUS — MACD histogram still favors the direction. Adds a small amount to
          the score but is never required — matches "indicators are
          optional confluence, not gates" from the reference material.

c1/c2/c3 are the "3 required confluences" (trend + location + confirmation)
once both hard gates pass; MACD is the only non-required component.
"""
import logging

import numpy as np
import pandas as pd

import config
from engine.indicators import atr, ema, macd_full
from engine.strategy_market_structure import detect_pivots, classify_structure, detect_bos_choch
from engine.strategy_price_action import detect_patterns, _BULLISH, _BEARISH

logger = logging.getLogger(__name__)

_buy_diag:  dict = {}
_sell_diag: dict = {}

# Required-condition weight vs the MACD bonus. c1/c2/c3 (retest, validity,
# price-action) split the bulk of the score; MACD alignment can add a little
# on top but a signal with all 3 required conditions and no MACD confluence
# still scores well above a signal missing one of the required 3.
_REQUIRED_POINTS = 21   # of 25 max, split across c1/c2/c3
_BONUS_POINTS    = 4    # MACD alignment bonus


def get_last_diag(pair: str, direction: str) -> dict:
    if direction == "long":
        return dict(_buy_diag.get(pair, {}))
    return dict(_sell_diag.get(pair, {}))


def _get_pip(pair: str) -> float:
    """1 pip = 0.01 for JPY pairs and XAU_USD (OANDA pipLocation -2), 0.0001 for all other FX pairs."""
    p = pair.upper()
    return 0.01 if ("JPY" in p or "XAU" in p) else 0.0001


def _h4_bias(df_h4: pd.DataFrame, adaptive: dict | None = None) -> str:
    """H4 close vs an EMA — period defaults to config.EMA_FIXED_PERIODS' mid
    (so out of the box this isn't a new indicator, just a new hard gate
    built from one that already exists), but is overridable via
    adaptive["h4_bias_period"] — this strategy was built from user-supplied
    reference material and never tuned, unlike ema_bounce/breakout_retest
    which both have months of WFO/adaptive-params history behind them; this
    knob exists so it can get a real, fair tuning pass instead of being
    judged on defaults alone. See tasks/todo.md 2026-08-12. Returns "bull",
    "bear", or "neutral" (insufficient data)."""
    ap     = adaptive or {}
    period = ap.get("h4_bias_period", config.EMA_FIXED_PERIODS[1])
    if len(df_h4) < period:
        return "neutral"
    ema_val = float(ema(df_h4["close"], period).iloc[-1])
    close   = float(df_h4["close"].iloc[-1])
    if np.isnan(ema_val):
        return "neutral"
    return "bull" if close > ema_val else "bear"


def _is_consolidated(df_h1: pd.DataFrame) -> bool:
    """Same optional chop filter as strategy_breakout_retest — a compressed
    34/100/200 EMA ribbon stays compressed through fakeout BOS windows an
    H4-trend gate alone won't always catch."""
    if not config.BREAKOUT_CONSOLIDATION_FILTER_ENABLED or len(df_h1) < 200:
        return False
    atr_val = float(atr(df_h1["high"], df_h1["low"], df_h1["close"], 14).iloc[-1])
    if not atr_val or np.isnan(atr_val):
        return False
    emas = [ema(df_h1["close"], p).iloc[-1] for p in (34, 100, 200)]
    spread_atr = (max(emas) - min(emas)) / atr_val
    return spread_atr < config.BREAKOUT_MIN_MA_SPREAD_ATR


def check_buy_signal(pair: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame,
                     adaptive: dict | None = None) -> float:
    if len(df_h1) < 50 or len(df_h4) < 50:
        return 0.0

    ap               = adaptive or {}
    retest_band_mult = ap.get("retest_band_mult", 0.3)

    last_bar_hour = df_h1.index[-1].hour
    if not (config.SESSION_START_UTC <= last_bar_hour < config.SESSION_END_UTC):
        return 0.0

    # ── HARD GATE 1 — H4 trend bias must agree with this direction ──────────
    if _h4_bias(df_h4, ap) != "bull":
        _buy_diag[pair] = dict(h4_bias=False, c1=False, c2=False, c3=False, c4=False,
                               broken_level=None, patterns=[])
        return 0.0

    if _is_consolidated(df_h1):
        _buy_diag[pair] = dict(h4_bias=True, c1=False, c2=False, c3=False, c4=False,
                               broken_level=None, patterns=[], consolidated=True)
        return 0.0

    pivots    = detect_pivots(df_h1)
    structure = classify_structure(pivots)
    bos       = detect_bos_choch(df_h1, pivots, structure)

    # ── HARD GATE 2 — must have a recent bullish break to retest ────────────
    c1 = bos["bos"] and bos["bos_direction"] == "bullish" and bool(pivots["highs"])
    if not c1:
        _buy_diag[pair] = dict(h4_bias=True, c1=False, c2=False, c3=False, c4=False,
                               broken_level=None, patterns=[])
        return 0.0

    broken_level = pivots["highs"][-1]
    atr_val      = float(atr(df_h1["high"], df_h1["low"], df_h1["close"], 14).iloc[-1])
    band         = retest_band_mult * atr_val

    low_now   = float(df_h1["low"].iloc[-1])
    close_now = float(df_h1["close"].iloc[-1])

    c2 = abs(low_now - broken_level) <= band          # retest
    c3 = close_now >= broken_level - band             # not invalidated

    patterns         = detect_patterns(df_h1)
    bullish_patterns = [p for p in patterns if p in _BULLISH]
    c4 = len(bullish_patterns) >= 1                    # price-action confirmation

    _, _, macd_hist = macd_full(df_h1["close"], config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
    macd_bonus = len(macd_hist) >= 1 and float(macd_hist.iloc[-1]) > 0

    required_passed = sum([c2, c3, c4])
    score = round(_REQUIRED_POINTS * required_passed / 3) + (_BONUS_POINTS if macd_bonus else 0)
    logger.debug(
        "TREND-RETEST BUY %s  h4_bias=bull c1(bos)=True c2(retest)=%s c3(valid)=%s "
        "c4(pattern)=%s(%s) macd_bonus=%s -> %d",
        pair, c2, c3, c4, bullish_patterns, macd_bonus, score,
    )

    _buy_diag[pair] = dict(h4_bias=True, c1=c1, c2=c2, c3=c3, c4=c4, macd_bonus=macd_bonus,
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

    if _h4_bias(df_h4, ap) != "bear":
        _sell_diag[pair] = dict(h4_bias=False, c1=False, c2=False, c3=False, c4=False,
                                broken_level=None, patterns=[])
        return 0.0

    if _is_consolidated(df_h1):
        _sell_diag[pair] = dict(h4_bias=True, c1=False, c2=False, c3=False, c4=False,
                                broken_level=None, patterns=[], consolidated=True)
        return 0.0

    pivots    = detect_pivots(df_h1)
    structure = classify_structure(pivots)
    bos       = detect_bos_choch(df_h1, pivots, structure)

    c1 = bos["bos"] and bos["bos_direction"] == "bearish" and bool(pivots["lows"])
    if not c1:
        _sell_diag[pair] = dict(h4_bias=True, c1=False, c2=False, c3=False, c4=False,
                                broken_level=None, patterns=[])
        return 0.0

    broken_level = pivots["lows"][-1]
    atr_val      = float(atr(df_h1["high"], df_h1["low"], df_h1["close"], 14).iloc[-1])
    band         = retest_band_mult * atr_val

    high_now  = float(df_h1["high"].iloc[-1])
    close_now = float(df_h1["close"].iloc[-1])

    c2 = abs(high_now - broken_level) <= band
    c3 = close_now <= broken_level + band

    patterns         = detect_patterns(df_h1)
    bearish_patterns = [p for p in patterns if p in _BEARISH]
    c4 = len(bearish_patterns) >= 1

    _, _, macd_hist = macd_full(df_h1["close"], config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
    macd_bonus = len(macd_hist) >= 1 and float(macd_hist.iloc[-1]) < 0

    required_passed = sum([c2, c3, c4])
    score = round(_REQUIRED_POINTS * required_passed / 3) + (_BONUS_POINTS if macd_bonus else 0)
    logger.debug(
        "TREND-RETEST SELL %s  h4_bias=bear c1(bos)=True c2(retest)=%s c3(valid)=%s "
        "c4(pattern)=%s(%s) macd_bonus=%s -> %d",
        pair, c2, c3, c4, bearish_patterns, macd_bonus, score,
    )

    _sell_diag[pair] = dict(h4_bias=True, c1=c1, c2=c2, c3=c3, c4=c4, macd_bonus=macd_bonus,
                            broken_level=broken_level, patterns=bearish_patterns)
    return float(score)


def get_stop_loss(pair: str, df_h1: pd.DataFrame, direction: str) -> float:
    """Same convention as strategy_breakout_retest.get_stop_loss — SL beyond
    the retested zone boundary (the level that must hold for the setup to
    remain valid), ATR/min-distance fallback if structure isn't available."""
    pivots   = detect_pivots(df_h1)
    atr_val  = float(atr(df_h1["high"], df_h1["low"], df_h1["close"], 14).iloc[-1])
    entry    = float(df_h1["close"].iloc[-1])
    pip      = _get_pip(pair)
    min_dist = pip * config.min_sl_pips_for(pair)

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


def get_tp_levels_structure(entry: float, stop_loss: float, direction: str,
                            pair: str, df_h1: "pd.DataFrame | None" = None) -> dict:
    """
    Structure-based take-profit — targets the next opposing pivot level(s)
    beyond entry, instead of a flat R-multiple. This is the "Structure-based
    targets" option from the reference checklist's Targeting section (as
    opposed to "Fixed R:R" or "Liquidity zones" — liquidity-zone targeting
    isn't implemented, this bot has no real order-flow/liquidity data to
    base one on).

    Falls back to R-multiples (matching get_tp_levels' defaults) for any
    level not found beyond the minimum-R floor below — a trend-retest entry
    can be close enough to the next opposing pivot that using it directly
    would set a degenerately tight TP1, or there may simply not be 3
    distinct pivots beyond entry yet.
    """
    from risk.risk_manager import get_tp_levels as _fixed_rr_tp

    fallback = _fixed_rr_tp(entry, stop_loss, direction, pair)
    if df_h1 is None or len(df_h1) < 20:
        return fallback

    dist = abs(entry - stop_loss)
    min_r = 1.0   # a candidate level closer than 1R isn't worth using over the fallback

    pivots = detect_pivots(df_h1)
    if direction == "long":
        candidates = sorted(p for p in pivots["highs"] if p > entry + min_r * dist)
    else:
        candidates = sorted((p for p in pivots["lows"] if p < entry - min_r * dist), reverse=True)

    decimals = 3 if "JPY" in pair.upper() else 5
    levels = {}
    for key, fb_key in (("tp1", "tp1"), ("tp2", "tp2"), ("tp3", "tp3")):
        if candidates:
            levels[key] = round(candidates.pop(0), decimals)
        else:
            levels[key] = fallback[fb_key]
    return levels


def clear_cache() -> None:
    """Reset diagnostics — used in tests."""
    _buy_diag.clear()
    _sell_diag.clear()
