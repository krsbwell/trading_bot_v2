"""
XAU_USD (gold) trend-pullback strategy.

Distinct entry mechanic from EMA-bounce/breakout-retest — reconciled from two
independently-sourced descriptions of the same underlying idea (a 200 EMA
regime filter + 50 EMA pullback retest + RSI dip-and-hook confirmation),
reviewed 2026-08-01 (see tasks/todo.md). Fires only when the market is
genuinely trending (ADX > threshold), same rationale as
strategy_trend_follow.py's inverted ADX gate — a pullback-in-trend entry
needs a real trend to pull back within, unlike EMA-bounce's ranging-market
mean-reversion.

Timeframe is deliberately NOT hardcoded to either source's stated TF (one
said M15, the other Daily) — whatever primary/confirm candles the caller
supplies is what this runs on, so both the bot's existing M30/H4 cadence
and a Daily variant can be backtested before committing to one (user's
call, 2026-08-01).
"""
import logging

import numpy as np
import pandas as pd

import config
from engine.indicators import ema, atr, rsi, adx
from engine.strategy_ema_cci_macd import _find_touch, _get_pip

logger = logging.getLogger(__name__)

_buy_diag:  dict = {}
_sell_diag: dict = {}


def get_last_diag(pair: str, direction: str) -> dict:
    """Return the diagnostic payload from the last buy or sell evaluation for pair."""
    if direction == "long":
        return dict(_buy_diag.get(pair, {}))
    return dict(_sell_diag.get(pair, {}))


def check_buy_signal(pair: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame,
                     adaptive: dict | None = None) -> float:
    """
    5-condition buy check (c2 is a hard gate, remaining 4 scored 0-25).

    Conditions:
      c1 — H1 close above the 200 EMA (bullish regime)
      c2 — H4 close above the 200 EMA (higher-TF regime alignment, HARD GATE
           when config.h4_gate_blocking_for(pair) is True — same convention
           as every other strategy module)
      c3 — Recent pullback touch of the 50 EMA within the lookback window
      c4 — RSI(14) dipped to <=40 at/near the touch (doc-sourced "dip zone")
      c5 — RSI has since hooked back above the 50 midline (buyers resuming)
      c6 — RSI still rising (momentum building, not fading) — mirrors the
           MACD-rising condition (c7) in strategy_ema_cci_macd.py
    """
    if len(df_h1) < 210 or len(df_h4) < 210:
        return 0.0

    ap                  = adaptive or {}
    ema_trend_period    = ap.get("gold_ema_trend",       200)
    ema_pullback_period = ap.get("gold_ema_pullback",    50)
    rsi_period          = ap.get("gold_rsi_period",      14)
    rsi_buy_extreme     = ap.get("gold_rsi_buy_extreme", 40)
    touch_lookback      = ap.get("touch_lookback",       40)
    touch_band_mult     = ap.get("touch_band_mult",      0.25)
    adx_threshold       = ap.get("adx_threshold",        config.ADX_THRESHOLD)

    # ── Session gate — intraday bars only. Daily+ candles are timestamped at
    # the broker's daily close (OANDA: 21:00 UTC), which sits outside every
    # FX session window by construction — applying this gate there would
    # zero out 100% of Daily/Weekly bars regardless of any other condition.
    if len(df_h1) >= 2 and (df_h1.index[-1] - df_h1.index[-2]) < pd.Timedelta(hours=20):
        last_bar_hour = df_h1.index[-1].hour
        if not (config.SESSION_START_UTC <= last_bar_hour < config.SESSION_END_UTC):
            return 0.0

    # ── ADX regime gate — fire only when trending (opposite of EMA-bounce) ───
    adx_h1  = adx(df_h1["high"], df_h1["low"], df_h1["close"], 14)
    adx_val = float(adx_h1.iloc[-1])
    if np.isnan(adx_val) or adx_val <= adx_threshold:
        return 0.0   # no real trend to pull back within

    ema_trend_h1 = ema(df_h1["close"], ema_trend_period)
    ema_trend_h4 = ema(df_h4["close"], ema_trend_period)
    ema_pull_h1  = ema(df_h1["close"], ema_pullback_period)
    atr_h1       = atr(df_h1["high"], df_h1["low"], df_h1["close"], 14)
    rsi_h1       = rsi(df_h1["close"], rsi_period)

    close_h1 = df_h1["close"].iloc[-1]
    close_h4 = df_h4["close"].iloc[-1]

    c1 = close_h1 > ema_trend_h1.iloc[-1]
    c2 = close_h4 > ema_trend_h4.iloc[-1]
    if not c2 and config.h4_gate_blocking_for(pair):
        _buy_diag[pair] = dict(c1=c1, c2=False, c3=False, c4=False, c5=False, c6=False,
                               rsi_at_touch=None, rsi_current=float(rsi_h1.iloc[-1]))
        return 0.0

    c3_idx = _find_touch(df_h1, ema_pull_h1, atr_h1, "long",
                         lookback=touch_lookback, band_mult=touch_band_mult)
    c3 = c3_idx is not None

    c4 = c5 = c6 = False
    rsi_at_touch = None
    if c3:
        rsi_win = rsi_h1.iloc[max(0, c3_idx - 1):c3_idx + 2]
        rsi_at_touch = float(rsi_win.min())
        c4 = rsi_at_touch <= rsi_buy_extreme
        c5 = float(rsi_h1.iloc[-1]) > 50
        c6 = len(rsi_h1) >= 2 and float(rsi_h1.iloc[-1]) > float(rsi_h1.iloc[-2])

    passed = sum([c1, c3, c4, c5, c6])
    score  = round(25 * passed / 5)
    logger.debug(
        "GOLD BUY %s  c1=%s c2(H4gate)=True c3=%s c4=%s(rsi_touch=%.1f,thr=%d) "
        "c5=%s c6=%s -> %d",
        pair, c1, c3, c4, rsi_at_touch or 0, rsi_buy_extreme, c5, c6, score,
    )

    _buy_diag[pair] = dict(
        c1=c1, c2=c2, c3=c3, c4=c4, c5=c5, c6=c6,
        rsi_at_touch=rsi_at_touch, rsi_current=float(rsi_h1.iloc[-1]),
    )
    return float(score)


def check_sell_signal(pair: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame,
                      adaptive: dict | None = None) -> float:
    """Mirror of check_buy_signal — see its docstring for condition detail."""
    if len(df_h1) < 210 or len(df_h4) < 210:
        return 0.0

    ap                  = adaptive or {}
    ema_trend_period    = ap.get("gold_ema_trend",        200)
    ema_pullback_period = ap.get("gold_ema_pullback",     50)
    rsi_period          = ap.get("gold_rsi_period",       14)
    rsi_sell_extreme    = ap.get("gold_rsi_sell_extreme", 60)
    touch_lookback      = ap.get("touch_lookback",        40)
    touch_band_mult     = ap.get("touch_band_mult",       0.25)
    adx_threshold       = ap.get("adx_threshold",         config.ADX_THRESHOLD)

    if len(df_h1) >= 2 and (df_h1.index[-1] - df_h1.index[-2]) < pd.Timedelta(hours=20):
        last_bar_hour = df_h1.index[-1].hour
        if not (config.SESSION_START_UTC <= last_bar_hour < config.SESSION_END_UTC):
            return 0.0

    adx_h1  = adx(df_h1["high"], df_h1["low"], df_h1["close"], 14)
    adx_val = float(adx_h1.iloc[-1])
    if np.isnan(adx_val) or adx_val <= adx_threshold:
        return 0.0

    ema_trend_h1 = ema(df_h1["close"], ema_trend_period)
    ema_trend_h4 = ema(df_h4["close"], ema_trend_period)
    ema_pull_h1  = ema(df_h1["close"], ema_pullback_period)
    atr_h1       = atr(df_h1["high"], df_h1["low"], df_h1["close"], 14)
    rsi_h1       = rsi(df_h1["close"], rsi_period)

    close_h1 = df_h1["close"].iloc[-1]
    close_h4 = df_h4["close"].iloc[-1]

    c1 = close_h1 < ema_trend_h1.iloc[-1]
    c2 = close_h4 < ema_trend_h4.iloc[-1]
    if not c2 and config.h4_gate_blocking_for(pair):
        _sell_diag[pair] = dict(c1=c1, c2=False, c3=False, c4=False, c5=False, c6=False,
                                rsi_at_touch=None, rsi_current=float(rsi_h1.iloc[-1]))
        return 0.0

    c3_idx = _find_touch(df_h1, ema_pull_h1, atr_h1, "short",
                         lookback=touch_lookback, band_mult=touch_band_mult)
    c3 = c3_idx is not None

    c4 = c5 = c6 = False
    rsi_at_touch = None
    if c3:
        rsi_win = rsi_h1.iloc[max(0, c3_idx - 1):c3_idx + 2]
        rsi_at_touch = float(rsi_win.max())
        c4 = rsi_at_touch >= rsi_sell_extreme
        c5 = float(rsi_h1.iloc[-1]) < 50
        c6 = len(rsi_h1) >= 2 and float(rsi_h1.iloc[-1]) < float(rsi_h1.iloc[-2])

    passed = sum([c1, c3, c4, c5, c6])
    score  = round(25 * passed / 5)
    logger.debug(
        "GOLD SELL %s  c1=%s c2(H4gate)=True c3=%s c4=%s(rsi_touch=%.1f,thr=%d) "
        "c5=%s c6=%s -> %d",
        pair, c1, c3, c4, rsi_at_touch or 0, rsi_sell_extreme, c5, c6, score,
    )

    _sell_diag[pair] = dict(
        c1=c1, c2=c2, c3=c3, c4=c4, c5=c5, c6=c6,
        rsi_at_touch=rsi_at_touch, rsi_current=float(rsi_h1.iloc[-1]),
    )
    return float(score)


# ── Stop loss ─────────────────────────────────────────────────────────────────

def get_stop_loss(pair: str, df_h1: pd.DataFrame, direction: str,
                  adaptive: dict | None = None) -> float:
    """
    Two backtestable methods — neither is assumed to win, that's a backtest
    question (see tasks/todo.md):

      "dynamic" (default) — SL just beyond the 50 EMA pullback level, ATR*1.5
                 fallback if the EMA sits on the wrong side of entry. Same
                 shape as strategy_ema_cci_macd.get_stop_loss, just anchored
                 to the fixed pullback EMA instead of an auto-fit "mid" EMA.
      "swing"   — SL behind the previous calendar week's swing high/low
                 (the structural stop idea from the second beginner-oriented
                 source document).

    Select via config.GOLD_STOP_METHOD, or `adaptive["gold_stop_method"]` to
    override per backtest run without touching config (used for sweeps).
    Both enforce config.min_sl_pips_for(pair) as a floor.
    """
    ap                  = adaptive or {}
    method              = ap.get("gold_stop_method", getattr(config, "GOLD_STOP_METHOD", "dynamic"))
    ema_pullback_period = ap.get("gold_ema_pullback", 50)

    entry    = float(df_h1["close"].iloc[-1])
    pip      = _get_pip(pair)
    min_dist = pip * config.min_sl_pips_for(pair)
    atr_val  = float(atr(df_h1["high"], df_h1["low"], df_h1["close"], 14).iloc[-1])

    if method == "swing":
        week_ago = df_h1.index[-1] - pd.Timedelta(days=7)
        window   = df_h1[df_h1.index >= week_ago]
        if len(window) < 5:          # not enough history for a real weekly window yet
            window = df_h1.tail(20)  # fall back to a plain recent-bar lookback
        if direction == "long":
            sl = float(window["low"].min()) - pip
        else:
            sl = float(window["high"].max()) + pip
    else:  # "dynamic"
        pull_val = float(ema(df_h1["close"], ema_pullback_period).iloc[-1])
        sl = pull_val - pip if direction == "long" else pull_val + pip

    if direction == "long":
        if sl >= entry:               # inverted — fall back to ATR
            sl = entry - max(atr_val * 1.5, min_dist)
        sl = min(sl, entry - min_dist)  # enforce minimum distance
    else:
        if sl <= entry:
            sl = entry + max(atr_val * 1.5, min_dist)
        sl = max(sl, entry + min_dist)

    decimals = 2 if "XAU" in pair.upper() else (3 if "JPY" in pair.upper() else 5)
    return round(sl, decimals)


def clear_cache() -> None:
    """Reset diagnostics — used in tests and repeated backtests in one process."""
    _buy_diag.clear()
    _sell_diag.clear()
