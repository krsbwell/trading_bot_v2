import logging
from typing import Optional

import numpy as np
import pandas as pd

import config
from engine.indicators import ema, atr, cci, macd_full, adx

logger = logging.getLogger(__name__)

# _cache[pair][timeframe] = (short_period, mid_period, long_period, last_fit_candle_count)
_cache: dict = {}

# Latest per-pair condition diagnostics — written by check_buy/sell_signal,
# read by signal_engine to pass to the audit logger.
_buy_diag:  dict = {}
_sell_diag: dict = {}


def get_last_diag(pair: str, direction: str) -> dict:
    """Return the diagnostic payload from the last buy or sell evaluation for pair."""
    if direction == "long":
        return dict(_buy_diag.get(pair, {}))
    return dict(_sell_diag.get(pair, {}))

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

    When config.EMA_AUTOFIT_ENABLED is False (default since 2026-08-02),
    returns fixed periods from config.EMA_FIXED_PERIODS instead of
    auto-fitting — avoids curve-fitting and constant behavior drift.
    """
    # Fixed EMA periods — no auto-fitting
    if not getattr(config, 'EMA_AUTOFIT_ENABLED', False):
        short, mid, long_ = config.EMA_FIXED_PERIODS
        return short, mid, long_

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
        logger.debug("Too few scoreable periods for %s %s — using defaults", pair, timeframe)
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
    lookback: int = 40,
    band_mult: float = 0.25,
) -> Optional[int]:
    """
    Scan the last `lookback` candles for an EMA touch.
    direction "long"  → look for low touching EMA from above (within band_mult×ATR)
    direction "short" → look for high touching EMA from below (within band_mult×ATR)
    Returns the index position in df (absolute), or None.
    Default lookback=40 M30 bars ≈ 20 hours (was 20 H1 bars = same 20-hour window).
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
        band = band_mult * atr_a[i]
        if direction == "long"  and abs(lows[i]  - ema_a[i]) <= band:
            return i
        if direction == "short" and abs(highs[i] - ema_a[i]) <= band:
            return i
    return None


# ── Signal checks ─────────────────────────────────────────────────────────────

def check_buy_signal(pair: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame,
                     adaptive: dict | None = None) -> float:
    """
    7-condition buy check. Returns 0–25 (proportional to conditions met).

    Conditions:
      c1 — H1 close above short EMA (price in upward momentum zone)
      c2 — H4 close above short EMA (higher-TF alignment, NOT a hard gate)
      c3 — Recent EMA touch / bounce within lookback window (20 bars)
      c4 — CCI was oversold at or near the touch (standard: < -60; trend-cont: < -threshold)
      c5 — CCI has recovered (> -30) — momentum turning
      c6 — MACD histogram majority positive (≥macd_bars_needed of last 3 bars)
      c7 — MACD histogram is RISING (momentum building, not fading)

    All 7 are scored proportionally.
    adaptive: dict from AdaptiveParams.get(pair) — adjusts cci_threshold,
              touch_band_mult, and macd_bars_needed based on recent win rate.
    """
    if len(df_h1) < 50 or len(df_h4) < 50:
        return 0.0

    ap               = adaptive or {}
    cci_threshold    = ap.get("cci_threshold",    20)
    touch_band_mult  = ap.get("touch_band_mult",  0.25)
    touch_lookback   = ap.get("touch_lookback",   40)
    macd_bars_needed = ap.get("macd_bars_needed", 1)
    cci_period       = ap.get("CCI_PERIOD",       config.CCI_PERIOD)
    macd_fast        = ap.get("MACD_FAST",        config.MACD_FAST)
    macd_slow        = ap.get("MACD_SLOW",        config.MACD_SLOW)
    macd_signal_p    = ap.get("MACD_SIGNAL",      config.MACD_SIGNAL)
    adx_threshold    = ap.get("adx_threshold",    config.ADX_THRESHOLD)

    # ── Session gate — London + New York only (07:00–17:00 UTC) ──────────────
    last_bar_hour = df_h1.index[-1].hour
    if not (config.SESSION_START_UTC <= last_bar_hour < config.SESSION_END_UTC):
        return 0.0

    # ── ADX regime gate — skip when market is strongly trending ──────────────
    adx_h1  = adx(df_h1["high"], df_h1["low"], df_h1["close"], 14)
    adx_val = float(adx_h1.iloc[-1])
    if np.isnan(adx_val) or adx_val > adx_threshold:
        return 0.0   # trending market — EMA bounce unreliable

    short, mid, long_ = get_best_emas(pair, config.TIMEFRAMES["primary"], df_h1)
    short_h4, _, _   = get_best_emas(pair, config.confirm_tf_for(pair),   df_h4)

    short_ema_h1  = ema(df_h1["close"], short)
    short_ema_h4  = ema(df_h4["close"], short_h4)
    atr_h1        = atr(df_h1["high"], df_h1["low"], df_h1["close"], 14)
    cci_h1        = cci(df_h1["high"], df_h1["low"], df_h1["close"], cci_period)
    _, _, macd_hist = macd_full(df_h1["close"], macd_fast, macd_slow, macd_signal_p)

    close_h1 = df_h1["close"].iloc[-1]
    close_h4 = df_h4["close"].iloc[-1]

    c1 = close_h1 > short_ema_h1.iloc[-1]
    c2 = close_h4 > short_ema_h4.iloc[-1]   # H4 trend alignment
    # ── H4 gate: hard-block when H4_GATE_BLOCKING=True, soft-score when False ─
    if not c2 and config.h4_gate_blocking_for(pair):
        _buy_diag[pair] = dict(c1=c1, c2=False, c3=False, c4=False, c5=False,
                               c6=False, c7=False, cci_at_touch_val=None,
                               cci_current=float(cci_h1.iloc[-1]),
                               macd_hist_val=float(macd_hist.iloc[-1]))
        return 0.0   # H4 counter-trend — skip entirely

    c3_idx = _find_touch(df_h1, short_ema_h1, atr_h1, "long",
                         lookback=touch_lookback, band_mult=touch_band_mult)
    c3 = c3_idx is not None

    c4 = c5 = c6 = c7 = False
    cci_at_touch_val = None
    if c3:
        cci_win = cci_h1.iloc[max(0, c3_idx - 1):c3_idx + 2]
        cci_at_touch_val = float(cci_win.min())
        # CCI must be below -cci_threshold at touch (adaptive: tighter when win rate is low)
        c4 = cci_at_touch_val < -cci_threshold
        # ── CCI-touch gate: hard-block when CCI_TOUCH_GATE_BLOCKING=True ─────
        if not c4 and config.CCI_TOUCH_GATE_BLOCKING:
            _buy_diag[pair] = dict(c1=c1, c2=c2, c3=c3, c4=False, c5=False,
                                   c6=False, c7=False, cci_at_touch_val=cci_at_touch_val,
                                   cci_current=float(cci_h1.iloc[-1]),
                                   macd_hist_val=float(macd_hist.iloc[-1]))
            return 0.0   # CCI not oversold at touch — skip entirely
        c5 = cci_h1.iloc[-1] > -30
        # MACD: require macd_bars_needed of last 3 bars positive
        if len(macd_hist) >= 3:
            last3 = macd_hist.iloc[-3:]
            c6 = bool((last3 > 0).sum() >= macd_bars_needed)
        else:
            c6 = bool(macd_hist.iloc[-1] > 0)
        # MACD momentum: histogram is rising (not just positive but building)
        c7 = len(macd_hist) >= 2 and float(macd_hist.iloc[-1]) > float(macd_hist.iloc[-2])

    # c2 is soft-scored (H4_GATE_BLOCKING=False since 2026-08-02) — score all 7 conditions
    passed = sum([c1, c2, c3, c4, c5, c6, c7])
    score  = round(25 * passed / 7)
    logger.debug(
        "BUY %s  c1=%s c2(H4gate)=True c3=%s c4=%s(cci_touch=%.1f,thr=-%d) "
        "c5=%s c6=%s(macd≥%d) c7=%s → %d",
        pair, c1, c3, c4, cci_at_touch_val or 0, cci_threshold,
        c5, c6, macd_bars_needed, c7, score,
    )

    # Attach diagnostic payload for the audit system (consumed by signal_engine)
    _buy_diag[pair] = dict(
        c1=c1, c2=c2, c3=c3, c4=c4, c5=c5, c6=c6, c7=c7,
        cci_at_touch_val=cci_at_touch_val,
        cci_current=float(cci_h1.iloc[-1]),
        macd_hist_val=float(macd_hist.iloc[-1]),
    )
    return float(score)


def check_sell_signal(pair: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame,
                      adaptive: dict | None = None) -> float:
    """
    7-condition sell check. Returns 0–25. Mirror of check_buy_signal.
    H4 is a HARD GATE — returns 0.0 immediately if H4 is counter-trend (bull).

    Conditions:
      c1 — H1 close below short EMA
      c2 — H4 close below short EMA (HARD GATE — must pass or signal discarded)
      c3 — Recent EMA touch from below within 20 bars
      c4 — CCI overbought at touch (> +cci_threshold)
      c5 — CCI has fallen back (< +30)
      c6 — MACD histogram majority negative (≥macd_bars_needed of last 3 bars)
      c7 — MACD histogram is FALLING (momentum building to downside)
    adaptive: dict from AdaptiveParams.get(pair) — adjusts cci_threshold,
              touch_band_mult, and macd_bars_needed based on recent win rate.
    """
    if len(df_h1) < 50 or len(df_h4) < 50:
        return 0.0

    ap               = adaptive or {}
    cci_threshold    = ap.get("cci_threshold",    20)
    touch_band_mult  = ap.get("touch_band_mult",  0.25)
    touch_lookback   = ap.get("touch_lookback",   40)
    macd_bars_needed = ap.get("macd_bars_needed", 1)
    cci_period       = ap.get("CCI_PERIOD",       config.CCI_PERIOD)
    macd_fast        = ap.get("MACD_FAST",        config.MACD_FAST)
    macd_slow        = ap.get("MACD_SLOW",        config.MACD_SLOW)
    macd_signal_p    = ap.get("MACD_SIGNAL",      config.MACD_SIGNAL)
    adx_threshold    = ap.get("adx_threshold",    config.ADX_THRESHOLD)

    # ── Session gate — London + New York only (07:00–17:00 UTC) ──────────────
    last_bar_hour = df_h1.index[-1].hour
    if not (config.SESSION_START_UTC <= last_bar_hour < config.SESSION_END_UTC):
        return 0.0

    # ── ADX regime gate — skip when market is strongly trending ──────────────
    adx_h1  = adx(df_h1["high"], df_h1["low"], df_h1["close"], 14)
    adx_val = float(adx_h1.iloc[-1])
    if np.isnan(adx_val) or adx_val > adx_threshold:
        return 0.0   # trending market — EMA bounce unreliable

    short, mid, long_ = get_best_emas(pair, config.TIMEFRAMES["primary"], df_h1)
    short_h4, _, _   = get_best_emas(pair, config.confirm_tf_for(pair),   df_h4)

    short_ema_h1  = ema(df_h1["close"], short)
    short_ema_h4  = ema(df_h4["close"], short_h4)
    atr_h1        = atr(df_h1["high"], df_h1["low"], df_h1["close"], 14)
    cci_h1        = cci(df_h1["high"], df_h1["low"], df_h1["close"], cci_period)
    _, _, macd_hist = macd_full(df_h1["close"], macd_fast, macd_slow, macd_signal_p)

    close_h1 = df_h1["close"].iloc[-1]
    close_h4 = df_h4["close"].iloc[-1]

    c1 = close_h1 < short_ema_h1.iloc[-1]
    c2 = close_h4 < short_ema_h4.iloc[-1]   # H4 trend alignment
    # ── H4 gate: hard-block when H4_GATE_BLOCKING=True, soft-score when False ─
    if not c2 and config.h4_gate_blocking_for(pair):
        _sell_diag[pair] = dict(c1=c1, c2=False, c3=False, c4=False, c5=False,
                                c6=False, c7=False, cci_at_touch_val=None,
                                cci_current=float(cci_h1.iloc[-1]),
                                macd_hist_val=float(macd_hist.iloc[-1]))
        return 0.0   # H4 counter-trend — skip entirely

    c3_idx = _find_touch(df_h1, short_ema_h1, atr_h1, "short",
                         lookback=touch_lookback, band_mult=touch_band_mult)
    c3 = c3_idx is not None

    c4 = c5 = c6 = c7 = False
    cci_at_touch_val = None
    if c3:
        cci_win = cci_h1.iloc[max(0, c3_idx - 1):c3_idx + 2]
        cci_at_touch_val = float(cci_win.max())
        # CCI must be above +cci_threshold at touch (adaptive: tighter when win rate is low)
        c4 = cci_at_touch_val > cci_threshold
        # ── CCI-touch gate: hard-block when CCI_TOUCH_GATE_BLOCKING=True ─────
        if not c4 and config.CCI_TOUCH_GATE_BLOCKING:
            _sell_diag[pair] = dict(c1=c1, c2=c2, c3=c3, c4=False, c5=False,
                                    c6=False, c7=False, cci_at_touch_val=cci_at_touch_val,
                                    cci_current=float(cci_h1.iloc[-1]),
                                    macd_hist_val=float(macd_hist.iloc[-1]))
            return 0.0   # CCI not overbought at touch — skip entirely
        c5 = cci_h1.iloc[-1] < 30
        # MACD: require macd_bars_needed of last 3 bars negative
        if len(macd_hist) >= 3:
            last3 = macd_hist.iloc[-3:]
            c6 = bool((last3 < 0).sum() >= macd_bars_needed)
        else:
            c6 = bool(macd_hist.iloc[-1] < 0)
        # MACD momentum: histogram is falling (building bearish momentum)
        c7 = len(macd_hist) >= 2 and float(macd_hist.iloc[-1]) < float(macd_hist.iloc[-2])

    # c2 is soft-scored (H4_GATE_BLOCKING=False since 2026-08-02) — score all 7 conditions
    passed = sum([c1, c2, c3, c4, c5, c6, c7])
    score  = round(25 * passed / 7)
    logger.debug(
        "SELL %s  c1=%s c2(H4gate)=True c3=%s c4=%s(cci_touch=%.1f,thr=+%d) "
        "c5=%s c6=%s(macd≥%d) c7=%s → %d",
        pair, c1, c3, c4, cci_at_touch_val or 0, cci_threshold,
        c5, c6, macd_bars_needed, c7, score,
    )

    _sell_diag[pair] = dict(
        c1=c1, c2=True, c3=c3, c4=c4, c5=c5, c6=c6, c7=c7,
        cci_at_touch_val=cci_at_touch_val,
        cci_current=float(cci_h1.iloc[-1]),
        macd_hist_val=float(macd_hist.iloc[-1]),
    )
    return float(score)


# ── Stop loss ─────────────────────────────────────────────────────────────────

def get_stop_loss(pair: str, df_h1: pd.DataFrame, direction: str) -> float:
    """
    Place SL just beyond the mid EMA (the dynamic support/resistance level).

    Rules applied in order:
      1. Primary: SL = mid_ema ± 1pip
      2. Validation: SL MUST be on the loss side of entry.
         If the mid EMA is on the wrong side (inverted), fall back to 1.5×ATR14.
      3. Minimum distance: config.MIN_SL_PIPS from entry to filter noise stop-outs.
    """
    _, mid, _ = get_best_emas(pair, config.TIMEFRAMES["primary"], df_h1)
    mid_val   = float(ema(df_h1["close"], mid).iloc[-1])
    entry     = float(df_h1["close"].iloc[-1])
    pip       = _get_pip(pair)
    min_pips  = config.MIN_SL_PIPS     # minimum SL distance in pips
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
    """Reset EMA cache and diagnostics — used in tests and Scan Now."""
    _cache.clear()
    _buy_diag.clear()
    _sell_diag.clear()
