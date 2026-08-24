"""
Market regime classifier — labels current market conditions from measurable
data (ADX, EMA slope, ATR-percentile volatility) rather than any single
indicator deciding alone.

Used as an additional feature/context for engine.strategy_adaptive — it
never gates or blocks a trade by itself. Structure/price-action scoring,
confluence scoring, and the ATR volatility gates in engine.signal_engine
remain exactly as they are for every strategy, including this one.

Built as part of the adaptive-strategy integration, see tasks/todo.md
(2026-08-21). Reuses engine.indicators.adx/ema/atr — no new indicator math
beyond a small EMA-slope helper below.
"""
import numpy as np
import pandas as pd

import config
from engine.indicators import adx, ema, atr as calc_atr

REGIMES = (
    "TRENDING_UP", "TRENDING_DOWN", "RANGING",
    "HIGH_VOLATILITY", "LOW_VOLATILITY", "BREAKOUT", "UNKNOWN",
)


def _ema_slope_pct(close: pd.Series, period: int, lookback: int = 5) -> float:
    """EMA value's % change over `lookback` bars — a cheap trend-direction/
    strength proxy that doesn't require a second indicator."""
    e = ema(close, period)
    if len(e) < lookback + 1:
        return 0.0
    prev, last = float(e.iloc[-lookback - 1]), float(e.iloc[-1])
    if np.isnan(prev) or np.isnan(last) or prev == 0:
        return 0.0
    return (last - prev) / abs(prev) * 100


def classify_regime(df_primary: pd.DataFrame, df_confirm: "pd.DataFrame | None" = None,
                     adx_period: int = 14, ema_period: int = 34) -> dict:
    """
    Classify the current regime from `df_primary` (the strategy's own
    timeframe). `df_confirm` (higher timeframe) is optional — used only to
    flag BREAKOUT when the primary TF has swung against a higher timeframe
    that hasn't caught up yet.

    Returns {"regime": one of REGIMES, "confidence": 0-1, "adx": float|None,
             "atr_pct": float|None, "ema_slope_pct": float|None}.

    Never raises — insufficient data returns UNKNOWN / confidence 0.0,
    matching this codebase's existing convention of strategy check_*_signal
    functions returning 0.0 rather than raising on short data.
    """
    if df_primary is None or len(df_primary) < max(adx_period, ema_period) + 5:
        return {"regime": "UNKNOWN", "confidence": 0.0, "adx": None,
                "atr_pct": None, "ema_slope_pct": None}

    try:
        adx_s = adx(df_primary["high"], df_primary["low"], df_primary["close"], adx_period)
        adx_val = float(adx_s.iloc[-1])
    except Exception:
        adx_val = float("nan")

    atr_s = calc_atr(df_primary["high"], df_primary["low"], df_primary["close"], 14)
    atr_val = float(atr_s.iloc[-1])
    close = float(df_primary["close"].iloc[-1])
    atr_pct = (atr_val / close * 100) if close else 0.0

    # Volatility percentile vs this pair's own recent ATR history — "high"/
    # "low" is relative, not an absolute pip threshold, so the same logic
    # works unmodified across every pair.
    atr_hist = atr_s.dropna()
    atr_percentile = float((atr_hist <= atr_val).mean()) if len(atr_hist) >= 20 else 0.5

    slope = _ema_slope_pct(df_primary["close"], ema_period)
    adx_threshold = getattr(config, "ADX_THRESHOLD", 28)

    if np.isnan(adx_val):
        regime, confidence = "UNKNOWN", 0.0
    elif atr_percentile >= 0.85:
        regime, confidence = "HIGH_VOLATILITY", round(min(1.0, atr_percentile), 3)
    elif atr_percentile <= 0.15:
        regime, confidence = "LOW_VOLATILITY", round(min(1.0, 1 - atr_percentile), 3)
    elif adx_val >= adx_threshold:
        regime = "TRENDING_UP" if slope > 0 else "TRENDING_DOWN"
        confidence = round(min(1.0, adx_val / (adx_threshold * 2)), 3)
    else:
        regime = "RANGING"
        confidence = round(min(1.0, (adx_threshold - adx_val) / adx_threshold), 3)

    # BREAKOUT override — primary TF is trending but sharply against the
    # confirm TF's own (weaker) slope, i.e. a fresh move the higher
    # timeframe hasn't confirmed yet.
    if (df_confirm is not None and len(df_confirm) >= ema_period + 5
            and regime in ("TRENDING_UP", "TRENDING_DOWN")):
        confirm_slope = _ema_slope_pct(df_confirm["close"], ema_period)
        disagree = (slope > 0) != (confirm_slope > 0)
        if disagree and abs(slope) > abs(confirm_slope) * 2:
            regime = "BREAKOUT"

    return {
        "regime": regime,
        "confidence": confidence,
        "adx": None if np.isnan(adx_val) else round(adx_val, 2),
        "atr_pct": round(atr_pct, 4),
        "ema_slope_pct": round(slope, 4),
    }
