import logging
from typing import Optional

import numpy as np
import pandas as pd

import config
from engine.indicators import ema, atr, cci, macd_histogram

logger = logging.getLogger(__name__)

# _cache[pair][timeframe] = (short_period, mid_period, long_period, last_fit_candle_count)
_cache: dict = {}

def _get_pip(pair: str) -> float:
    """1 pip = 0.01 for JPY pairs, 0.0001 for all others."""
    return 0.01 if "JPY" in pair.upper() else 0.0001


# ── EMA auto-fit ──────────────────────────────────────────────────────────────

def _score_period(df: pd.DataFrame, period: int) -> int:
    """
    Score one EMA period: count candles where price touched within 0.2×ATR
    of the EMA and the NEXT candle reversed at least 0.5×ATR.
    """
    ema_s  = ema(df["close"], period).values
    atr_s  = atr(df["high"], df["low"], df["close"], 14).values
    lows   = df["low"].values
    highs  = df["high"].values
    closes = df["close"].values
    score  = 0

    for i in range(len(df) - 1):
        if np.isnan(ema_s[i]) or np.isnan(atr_s[i]) or atr_s[i] == 0:
            continue
        band = 0.2 * atr_s[i]
        rev  = 0.5 * atr_s[i]

        # Bullish touch: low touched EMA from above → next candle close up
        if abs(lows[i] - ema_s[i]) <= band:
            if closes[i + 1] - lows[i] >= rev:
                score += 1

        # Bearish touch: high touched EMA from below → next candle close down
        if abs(highs[i] - ema_s[i]) <= band:
            if highs[i] - closes[i + 1] >= rev:
                score += 1

    return score


def get_best_emas(pair: str, timeframe: str, df: pd.DataFrame) -> tuple[int, int, int]:
    """
    Select the 3 best-scoring EMA periods for this pair+timeframe.
    Re-fits every EMA_REFIT_EVERY_N_CANDLES candles; result is cached.
    Returns (short_period, mid_period, long_period) sorted ascending.
    """
    n = len(df)
    cached = _cache.get(pair, {}).get(timeframe)
    if cached:
        short, mid, long_, last_fit = cached
        if n - last_fit < config.EMA_REFIT_EVERY_N_CANDLES:
            return short, mid, long_

    df_fit = df.tail(200)
    min_candles_needed = 30

    scores = {
        p: _score_period(df_fit, p)
        for p in config.EMA_TEST_PERIODS
        if p < len(df_fit) - min_candles_needed
    }

    if len(scores) < 3:
        logger.warning("Too few scoreable periods for %s %s — using defaults", pair, timeframe)
        fallback = [p for p in [34, 100, 200] if p < n][:3]
        while len(fallback) < 3:
            fallback.append(fallback[-1])
        return fallback[0], fallback[1], fallback[2]

    top3 = sorted(sorted(scores, key=scores.__getitem__, reverse=True)[:3])
    short, mid, long_ = top3

    _cache.setdefault(pair, {})[timeframe] = (short, mid, long_, n)
    logger.info(
        "EMA fit %s %s → short=%d mid=%d long=%d  scores=%s",
        pair, timeframe, short, mid, long_,
        {p: scores[p] for p in top3},
    )
    return short, mid, long_


# ── Touch detection ───────────────────────────────────────────────────────────

def _find_touch(
    df: pd.DataFrame,
    ema_s: pd.Series,
    atr_s: pd.Series,
    direction: str,
    lookback: int = 12,
) -> Optional[int]:
    """
    Scan the last `lookback` candles for an EMA touch.
    direction "long"  → look for low touching EMA from above (within 0.2×ATR)
    direction "short" → look for high touching EMA from below (within 0.2×ATR)
    Returns the index position in df (absolute), or None.
    """
    n = len(df)
    lows  = df["low"].values
    highs = df["high"].values
    ema_a = ema_s.values
    atr_a = atr_s.values

    for offset in range(lookback):
        i = n - 1 - offset
        if i < 0 or np.isnan(ema_a[i]) or np.isnan(atr_a[i]):
            continue
        band = 0.2 * atr_a[i]
        if direction == "long"  and abs(lows[i]  - ema_a[i]) <= band:
            return i
        if direction == "short" and abs(highs[i] - ema_a[i]) <= band:
            return i
    return None


# ── Signal checks ─────────────────────────────────────────────────────────────

def check_buy_signal(pair: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame) -> float:
    """
    6-condition buy check. Returns 0–25 (proportional to conditions met).

    Conditions:
      c1 — H1 close above short EMA (price in upward momentum zone)
      c2 — H4 close above short EMA (higher-TF alignment, NOT a hard gate)
      c3 — Recent EMA touch / bounce within lookback window
      c4 — CCI was oversold (< -60) at or near the touch
      c5 — CCI has recovered (> -30) — momentum turning
      c6 — MACD histogram positive in the last 3 bars

    All 6 are scored proportionally — H4 misalignment costs ~4 points, not all 25.
    """
    if len(df_h1) < 50 or len(df_h4) < 50:
        return 0.0

    short, mid, long_ = get_best_emas(pair, config.TIMEFRAMES["primary"], df_h1)

    short_ema_h1 = ema(df_h1["close"], short)
    short_ema_h4 = ema(df_h4["close"], short)
    atr_h1       = atr(df_h1["high"], df_h1["low"], df_h1["close"], 14)
    cci_h1       = cci(df_h1["high"], df_h1["low"], df_h1["close"], config.CCI_PERIOD)
    macd_hist    = macd_histogram(df_h1["close"], config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)

    close_h1 = df_h1["close"].iloc[-1]
    close_h4 = df_h4["close"].iloc[-1]

    c1 = close_h1 > short_ema_h1.iloc[-1]
    c2 = close_h4 > short_ema_h4.iloc[-1]   # H4 alignment — scored, NOT a hard gate
    c3_idx = _find_touch(df_h1, short_ema_h1, atr_h1, "long", lookback=20)
    c3 = c3_idx is not None

    c4 = c5 = c6 = False
    if c3:
        cci_win = cci_h1.iloc[max(0, c3_idx - 1):c3_idx + 2]
        c4 = float(cci_win.min()) < -60          # widened from -80
        c5 = cci_h1.iloc[-1] > -30
        # MACD: positive in any of last 3 bars (tolerates brief dips)
        c6 = bool(macd_hist.iloc[-3:].max() > 0) if len(macd_hist) >= 3 else bool(macd_hist.iloc[-1] > 0)

    passed = sum([c1, c2, c3, c4, c5, c6])
    score  = round(25 * passed / 6)
    logger.debug("BUY %s  c1=%s c2(H4)=%s c3=%s c4=%s c5=%s c6=%s → %d",
                 pair, c1, c2, c3, c4, c5, c6, score)
    return float(score)


def check_sell_signal(pair: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame) -> float:
    """
    6-condition sell check. Returns 0–25. Mirror of check_buy_signal.
    H4 is scored, NOT a hard gate.
    """
    if len(df_h1) < 50 or len(df_h4) < 50:
        return 0.0

    short, mid, long_ = get_best_emas(pair, config.TIMEFRAMES["primary"], df_h1)

    short_ema_h1 = ema(df_h1["close"], short)
    short_ema_h4 = ema(df_h4["close"], short)
    atr_h1       = atr(df_h1["high"], df_h1["low"], df_h1["close"], 14)
    cci_h1       = cci(df_h1["high"], df_h1["low"], df_h1["close"], config.CCI_PERIOD)
    macd_hist    = macd_histogram(df_h1["close"], config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)

    close_h1 = df_h1["close"].iloc[-1]
    close_h4 = df_h4["close"].iloc[-1]

    c1 = close_h1 < short_ema_h1.iloc[-1]
    c2 = close_h4 < short_ema_h4.iloc[-1]   # H4 alignment — scored, NOT a hard gate
    c3_idx = _find_touch(df_h1, short_ema_h1, atr_h1, "short", lookback=20)
    c3 = c3_idx is not None

    c4 = c5 = c6 = False
    if c3:
        cci_win = cci_h1.iloc[max(0, c3_idx - 1):c3_idx + 2]
        c4 = float(cci_win.max()) > 60           # widened from +80
        c5 = cci_h1.iloc[-1] < 30
        c6 = bool(macd_hist.iloc[-3:].min() < 0) if len(macd_hist) >= 3 else bool(macd_hist.iloc[-1] < 0)

    passed = sum([c1, c2, c3, c4, c5, c6])
    score  = round(25 * passed / 6)
    logger.debug("SELL %s  c1=%s c2(H4)=%s c3=%s c4=%s c5=%s c6=%s → %d",
                 pair, c1, c2, c3, c4, c5, c6, score)
    return float(score)


# ── Stop loss ─────────────────────────────────────────────────────────────────

def get_stop_loss(pair: str, df_h1: pd.DataFrame, direction: str) -> float:
    """
    Place SL just beyond the mid EMA (the dynamic support/resistance level).

    Rules applied in order:
      1. Primary: SL = mid_ema ± 1pip
      2. Validation: SL MUST be on the loss side of entry.
         If the mid EMA is on the wrong side (inverted), fall back to 1.5×ATR14.
      3. Minimum distance: at least 20 pips from entry to avoid noise-stop-outs.
    """
    _, mid, _ = get_best_emas(pair, config.TIMEFRAMES["primary"], df_h1)
    mid_val   = float(ema(df_h1["close"], mid).iloc[-1])
    entry     = float(df_h1["close"].iloc[-1])
    pip       = _get_pip(pair)
    min_pips  = 20                     # minimum SL distance in pips
    min_dist  = pip * min_pips

    if direction == "long":
        sl = mid_val - pip             # just below the EMA (support)
        if sl >= entry:                # EMA above entry → inverted, use ATR fallback
            atr_val = float(atr(df_h1["high"], df_h1["low"], df_h1["close"], 14).iloc[-1])
            sl = entry - max(atr_val * 1.5, min_dist)
        sl = min(sl, entry - min_dist) # enforce minimum distance
    else:
        sl = mid_val + pip             # just above the EMA (resistance)
        if sl <= entry:                # EMA below entry → inverted, use ATR fallback
            atr_val = float(atr(df_h1["high"], df_h1["low"], df_h1["close"], 14).iloc[-1])
            sl = entry + max(atr_val * 1.5, min_dist)
        sl = max(sl, entry + min_dist) # enforce minimum distance

    decimals = 3 if "JPY" in pair.upper() else 5
    return round(sl, decimals)


def clear_cache() -> None:
    """Reset EMA cache — used in tests."""
    _cache.clear()
