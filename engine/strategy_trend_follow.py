"""
Trend-following counterpart to strategy_ema_cci_macd.

EMA-bounce hard-blocks every signal when ADX(14) > config.ADX_THRESHOLD (the
market is trending, not ranging). This module fires the *same* entry recipe
(EMA touch + CCI extreme + MACD momentum) in exactly the opposite regime —
the ADX gate below is the only condition inverted relative to
strategy_ema_cci_macd.check_buy_signal/check_sell_signal. Everything else is
identical so a backtest comparison isolates that one variable.

EMA-bounce and this module partition the full ADX range with no gap and no
overlap: ADX <= threshold -> EMA-bounce may fire; ADX > threshold -> this
module may fire.
"""
import logging

import numpy as np
import pandas as pd

import config
from engine.indicators import ema, atr, cci, macd_full, adx
from engine.strategy_ema_cci_macd import get_best_emas, _find_touch, get_stop_loss  # noqa: F401  (get_stop_loss re-exported for callers)

logger = logging.getLogger(__name__)

# Separate diagnostic dicts — never collide with strategy_ema_cci_macd's.
_buy_diag:  dict = {}
_sell_diag: dict = {}


def get_last_diag(pair: str, direction: str) -> dict:
    if direction == "long":
        return dict(_buy_diag.get(pair, {}))
    return dict(_sell_diag.get(pair, {}))


def check_buy_signal(pair: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame,
                     adaptive: dict | None = None) -> float:
    """
    Same 7-condition buy check as strategy_ema_cci_macd.check_buy_signal, with
    the ADX regime gate inverted: fires only when ADX > threshold (trending),
    where EMA-bounce is hard-blocked.
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

    # ── Session gate — same window as EMA-bounce ──────────────────────────────
    last_bar_hour = df_h1.index[-1].hour
    if not (config.SESSION_START_UTC <= last_bar_hour < config.SESSION_END_UTC):
        return 0.0

    # ── ADX regime gate — INVERTED vs EMA-bounce: only fire when trending ────
    adx_h1  = adx(df_h1["high"], df_h1["low"], df_h1["close"], 14)
    adx_val = float(adx_h1.iloc[-1])
    if np.isnan(adx_val) or adx_val <= adx_threshold:
        return 0.0   # ranging market — this is EMA-bounce's territory, not ours

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
    if not c2 and config.H4_GATE_BLOCKING:
        _buy_diag[pair] = dict(c1=c1, c2=False, c3=False, c4=False, c5=False,
                               c6=False, c7=False, cci_at_touch_val=None,
                               cci_current=float(cci_h1.iloc[-1]),
                               macd_hist_val=float(macd_hist.iloc[-1]))
        return 0.0

    c3_idx = _find_touch(df_h1, short_ema_h1, atr_h1, "long",
                         lookback=touch_lookback, band_mult=touch_band_mult)
    c3 = c3_idx is not None

    c4 = c5 = c6 = c7 = False
    cci_at_touch_val = None
    if c3:
        cci_win = cci_h1.iloc[max(0, c3_idx - 1):c3_idx + 2]
        cci_at_touch_val = float(cci_win.min())
        c4 = cci_at_touch_val < -cci_threshold
        c5 = cci_h1.iloc[-1] > -30
        if len(macd_hist) >= 3:
            last3 = macd_hist.iloc[-3:]
            c6 = bool((last3 > 0).sum() >= macd_bars_needed)
        else:
            c6 = bool(macd_hist.iloc[-1] > 0)
        c7 = len(macd_hist) >= 2 and float(macd_hist.iloc[-1]) > float(macd_hist.iloc[-2])

    passed = sum([c1, c3, c4, c5, c6, c7])
    score  = round(25 * passed / 6)
    logger.debug(
        "TREND BUY %s  c1=%s c2(H4gate)=True c3=%s c4=%s(cci_touch=%.1f,thr=-%d) "
        "c5=%s c6=%s(macd>=%d) c7=%s -> %d",
        pair, c1, c3, c4, cci_at_touch_val or 0, cci_threshold,
        c5, c6, macd_bars_needed, c7, score,
    )

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
    Same 7-condition sell check as strategy_ema_cci_macd.check_sell_signal,
    with the ADX regime gate inverted (fires only when ADX > threshold).
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

    # ── Session gate — same window as EMA-bounce ──────────────────────────────
    last_bar_hour = df_h1.index[-1].hour
    if not (config.SESSION_START_UTC <= last_bar_hour < config.SESSION_END_UTC):
        return 0.0

    # ── ADX regime gate — INVERTED vs EMA-bounce: only fire when trending ────
    adx_h1  = adx(df_h1["high"], df_h1["low"], df_h1["close"], 14)
    adx_val = float(adx_h1.iloc[-1])
    if np.isnan(adx_val) or adx_val <= adx_threshold:
        return 0.0

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
    c2 = close_h4 < short_ema_h4.iloc[-1]
    if not c2 and config.H4_GATE_BLOCKING:
        _sell_diag[pair] = dict(c1=c1, c2=False, c3=False, c4=False, c5=False,
                                c6=False, c7=False, cci_at_touch_val=None,
                                cci_current=float(cci_h1.iloc[-1]),
                                macd_hist_val=float(macd_hist.iloc[-1]))
        return 0.0

    c3_idx = _find_touch(df_h1, short_ema_h1, atr_h1, "short",
                         lookback=touch_lookback, band_mult=touch_band_mult)
    c3 = c3_idx is not None

    c4 = c5 = c6 = c7 = False
    cci_at_touch_val = None
    if c3:
        cci_win = cci_h1.iloc[max(0, c3_idx - 1):c3_idx + 2]
        cci_at_touch_val = float(cci_win.max())
        c4 = cci_at_touch_val > cci_threshold
        c5 = cci_h1.iloc[-1] < 30
        if len(macd_hist) >= 3:
            last3 = macd_hist.iloc[-3:]
            c6 = bool((last3 < 0).sum() >= macd_bars_needed)
        else:
            c6 = bool(macd_hist.iloc[-1] < 0)
        c7 = len(macd_hist) >= 2 and float(macd_hist.iloc[-1]) < float(macd_hist.iloc[-2])

    passed = sum([c1, c3, c4, c5, c6, c7])
    score  = round(25 * passed / 6)
    logger.debug(
        "TREND SELL %s  c1=%s c2(H4gate)=True c3=%s c4=%s(cci_touch=%.1f,thr=+%d) "
        "c5=%s c6=%s(macd>=%d) c7=%s -> %d",
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


def clear_cache() -> None:
    """Reset diagnostics — used in tests. EMA cache is shared with strategy_ema_cci_macd."""
    _buy_diag.clear()
    _sell_diag.clear()
