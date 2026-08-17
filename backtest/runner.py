"""
Backtesting runner for Apex Trading Bot.

Replays historical primary-TF + confirm-TF candle data through the signal engine
bar-by-bar, simulates trades via PaperTrader, and writes a results CSV.
Primary / confirm TFs are read from config.TIMEFRAMES (currently M30 + H1).

Usage from project root:
    python -m backtest.runner --pair EUR_USD --bars 2000 --output data/bt_results.csv

Or import and call run_backtest() programmatically (used by the dashboard).
"""
import argparse
import csv
import logging
import os
import random as _random
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import config
from engine.strategy_ema_cci_macd import (
    check_buy_signal, check_sell_signal, get_best_emas, get_stop_loss,
    clear_cache as _clear_ema_cache,
)
from engine.strategy_breakout_retest import clear_cache as _clear_breakout_cache
from engine.strategy_trend_follow import clear_cache as _clear_trend_cache
from engine.strategy_gold_trend import clear_cache as _clear_gold_cache
from engine.strategy_trend_retest import (
    clear_cache as _clear_trend_retest_cache,
    get_tp_levels_structure as _tp_levels_structure,
)
from engine.strategy_market_structure import (
    detect_pivots, classify_structure, detect_bos_choch, get_sr_zones, score_structure,
)
from engine.strategy_price_action import detect_patterns, score_price_action
from engine.confluence_scorer import score_signal
from engine.indicators import cci as _cci_fn, macd_histogram as _macd_h_fn, atr as _atr_fn, ema as _ema_fn
from risk.risk_manager import get_tp_levels, calculate_position_size
from trade.paper_trader import PaperTrader


_STRUCT_MAP = {"bullish": "uptrend", "bearish": "downtrend", "ranging": "ranging"}

_TF_MINUTES = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D": 1440, "W": 10080}


def random_signal_fns(fire_rate: float, min_score: "int | float" = 60, seed: int = 42):
    """
    Control strategy for A/B testing a real strategy's entry logic: random
    direction, random timing, no signal logic at all. Same session gate /
    stop-loss / take-profit mechanics apply via run_backtest as any other
    strategy (pass the real strategy's own stop_loss_fn/tp_fn alongside
    this) — only entry *timing and direction* are randomized. Answers one
    question honestly: does this strategy's entry logic carry real
    information, or would the same risk/exit rules do just as well picking
    trades at random? Added 2026-08-12 — see tasks/todo.md.

    fire_rate: per-bar probability of firing a signal at all. Calibrate to
    another strategy's real trade frequency over the same window (e.g.
    trend_retest's total_trades / bars) for a fair, apples-to-apples
    comparison — an uncalibrated rate just tests "some rate of random
    trades," not "as many opportunities as the real strategy got."
    Deterministic given `seed`, so the same window always produces the same
    random trades (reproducible, not re-rolled every run).
    """
    rng = _random.Random(seed)
    _pending: dict = {}

    def _buy(pair, df_h1, df_h4, adaptive=None):
        key  = (pair, df_h1.index[-1])
        roll = rng.random()
        if roll < fire_rate / 2:
            _pending[key] = "long"
            return float(min_score)
        _pending[key] = "short" if roll < fire_rate else None
        return 0.0

    def _sell(pair, df_h1, df_h4, adaptive=None):
        key = (pair, df_h1.index[-1])
        return float(min_score) if _pending.pop(key, None) == "short" else 0.0

    return _buy, _sell


def confirm_tf_ratio(pair: str | None = None) -> int:
    """
    How many primary-TF bars fit in one confirm-TF bar, e.g. M30 primary /
    H4 confirm = 8. Derived from config.TIMEFRAMES (or config.confirm_tf_for(pair)
    when a per-pair override exists in config.CONFIRM_TF_PER_PAIR) so
    window-alignment math (run_walk_forward below, and the dashboard's WFO
    bar-count fetch) stays correct if the confirm timeframe ever changes —
    this was hardcoded to a stale "2" (M30→H1) in two places until the
    2026-07-23 switch to a real H4 confirm, which would have silently
    misaligned WFO's train/test windows had it not been caught.
    """
    confirm_tf  = config.confirm_tf_for(pair)  if pair else config.TIMEFRAMES["confirm"]
    primary_tf  = config.primary_tf_for(pair)  if pair else config.TIMEFRAMES["primary"]
    primary_min = _TF_MINUTES.get(primary_tf, 30)
    confirm_min = _TF_MINUTES.get(confirm_tf, 240)
    return max(1, confirm_min // primary_min)


_ml_model_singleton = None


def _ml_model():
    """
    Lazy-loaded PatternLearner, only imported/constructed when
    config.ML_SCORE_BOOST_ENABLED is actually on — keeps xgboost/sklearn off
    the import path for every normal (flag-off) backtest run.
    """
    global _ml_model_singleton
    if _ml_model_singleton is None:
        from learning.pattern_learner import PatternLearner
        _ml_model_singleton = PatternLearner()
    return _ml_model_singleton


def _bt_session(dt) -> str:
    h = dt.hour if hasattr(dt, "hour") else 12
    if 13 <= h < 16: return "london_ny"
    if  8 <= h < 13: return "london"
    if 16 <= h < 21: return "new_york"
    return "asian"


def _prep_daily_trend(df_d: "pd.DataFrame | None"):
    """
    Precompute a (daily_close, daily_ema20) pair of Series for fast per-bar
    d_trend lookups. Returns None if df_d is missing or too short — callers
    then fall back to the "neutral" default, same as before this existed.
    """
    if df_d is None or len(df_d) < 20:
        return None
    try:
        return df_d["close"], _ema_fn(df_d["close"], 20)
    except Exception:
        return None


def _d_trend_at(daily_prep, bar_time) -> str:
    """
    d_trend as of the last FULLY CLOSED daily candle strictly before
    bar_time's calendar day — never the still-forming "today" bar, so this
    can't leak lookahead into the backtest.
    """
    if daily_prep is None:
        return "neutral"
    d_close, d_ema = daily_prep
    try:
        cutoff    = pd.Timestamp(bar_time).normalize() - pd.Timedelta(days=1)
        close_val = d_close.asof(cutoff)
        ema_val   = d_ema.asof(cutoff)
        if pd.isna(close_val) or pd.isna(ema_val):
            return "neutral"
        return "bull" if close_val > ema_val else "bear"
    except Exception:
        return "neutral"


def _bt_sig_features(
    pair: str, direction: str, bar_time,
    slice_h1: pd.DataFrame, slice_h4: pd.DataFrame,
    ema_score: float, struct_score: float, pa_score: float, final_score: float,
    structure: str, sr_zones: list, bos_ok: bool, patterns: list, entry: float,
    d_trend: str = "neutral",
) -> dict:
    """Compute the full ML feature set at signal time for seeding PatternLearner."""
    try:
        _cci   = float(_cci_fn(slice_h1["high"], slice_h1["low"], slice_h1["close"]).iloc[-1])
    except Exception:
        _cci = 0.0
    try:
        _macd  = float(_macd_h_fn(slice_h1["close"]).iloc[-1])
    except Exception:
        _macd = 0.0
    try:
        _atr_v = float(_atr_fn(slice_h1["high"], slice_h1["low"], slice_h1["close"]).iloc[-1])
        _pip   = 0.01 if ("JPY" in pair or "XAU" in pair) else 0.0001
        _atr_p = _atr_v / _pip
    except Exception:
        _atr_p = 0.0

    _last  = slice_h1.iloc[-1]
    _rng   = _last["high"] - _last["low"]
    _body  = abs(_last["close"] - _last["open"])
    _cbr   = _body / _rng if _rng > 0 else 0.0
    _uw    = (_last["high"] - max(_last["open"], _last["close"])) / _rng if _rng > 0 else 0.0
    _lw    = (min(_last["open"], _last["close"]) - _last["low"]) / _rng if _rng > 0 else 0.0

    try:
        _h4_ema   = float(_ema_fn(slice_h4["close"], 20).iloc[-1])
        _h4_close = float(slice_h4["close"].iloc[-1])
        _h4_trend = "bull" if _h4_close > _h4_ema else "bear"
    except Exception:
        _h4_trend = "neutral"

    _was_sr = any(z.get("lower", 0) <= entry <= z.get("upper", 0) for z in (sr_zones or []))

    return {
        "pair":                pair,
        "direction":           direction,
        "bar_time":            bar_time.isoformat() if hasattr(bar_time, "isoformat") else str(bar_time),
        "confluence_score":    final_score,
        "ema_score":           ema_score,
        "structure_score":     struct_score,
        "pa_score":            pa_score,
        "cci_at_signal":       round(_cci,  4),
        "macd_hist_at_signal": round(_macd, 6),
        "atr_pips":            round(_atr_p, 2),
        "candle_body_ratio":   round(_cbr, 4),
        "upper_wick_ratio":    round(_uw,  4),
        "lower_wick_ratio":    round(_lw,  4),
        "h4_trend":            _h4_trend,
        "d_trend":             d_trend,
        "market_structure":    _STRUCT_MAP.get(structure, "ranging"),
        "was_at_sr_zone":      int(_was_sr),
        "bos_confirmed":       int(bos_ok),
        "session":             _bt_session(bar_time),
        "pattern_name":        "|".join(patterns),
    }

logger = logging.getLogger(__name__)

# Minimum warm-up bars before we start evaluating signals
_WARMUP = 60


def run_backtest(
    pair: str,
    df_h1: pd.DataFrame,
    df_h4: pd.DataFrame,
    starting_balance: float = 500.0,
    min_score: int = config.MIN_CONFLUENCE_SCORE,
    market: str = "forex",
    adaptive: dict | None = None,
    buy_fn=None,
    sell_fn=None,
    stop_loss_fn=None,
    tp_fn=None,
    df_d: "pd.DataFrame | None" = None,
    require_d_trend_alignment: bool = False,
) -> dict:
    """
    Run a full backtest for one pair.

    buy_fn/sell_fn/stop_loss_fn: signal/stop-loss functions with the same
    signatures as strategy_ema_cci_macd's. Default to None, which resolves
    per `pair` via engine.strategy_dispatch.resolve_strategy() — the same
    config.STRATEGY_OVERRIDE-driven dispatch the live signal engine uses
    (e.g. GBP_USD -> breakout_retest). Pass explicit functions to force a
    specific strategy regardless of pair (used by the CLI's --strategy flag
    and by tests). Before this default existed (2026-07-24), every caller
    that didn't explicitly pass these — the dashboard's Backtest and
    Walk-Forward buttons, run_walk_forward()'s own internal calls, and the
    weekly WFO refit job — silently backtested/tuned GBP_USD against
    EMA-bounce instead of the breakout_retest strategy it actually runs
    live, making its WFO-tuned params meaningless (breakout_retest doesn't
    even read the same adaptive keys WFO searches over).

    tp_fn: optional take-profit function, signature (entry, stop_loss,
    direction, pair, df_h1) -> {"tp1", "tp2", "tp3"}. Defaults to None, which
    uses risk_manager.get_tp_levels (the live fixed-R:R behavior, unchanged)
    — pass engine.strategy_trend_retest.get_tp_levels_structure (or any
    matching function) to backtest a structure-based-target variant instead.
    Added 2026-08-12 alongside strategy_trend_retest.py specifically so its
    TP approach could be tested, not assumed. See tasks/todo.md.

    df_d: optional Daily-granularity candles for the same pair/period. When
    omitted, d_trend in the recorded signal features stays "neutral" for
    every trade (the pre-existing behavior — d_trend isn't a decision input
    anywhere in the strategies, only a logged/display feature, so omitting
    it doesn't change which trades are taken). When supplied, d_trend is
    computed from the last fully-closed daily candle before each bar, same
    method engine/signal_engine.py uses live (close vs EMA20).

    require_d_trend_alignment: EXPERIMENTAL, off by default, untested in
    live trading — when True, a signal only opens a trade if d_trend agrees
    with its direction (long needs "bull", short needs "bear"; "neutral"
    never qualifies either way). Requires df_d to be meaningful — with no
    df_d, d_trend is always "neutral" and this blocks every trade. Purely
    for backtesting this specific hypothesis; not wired into any live
    strategy file.

    Returns a dict with:
        trades          : list of closed trade dicts
        equity_curve    : list of {bar_idx, balance} snapshots
        win_rate        : float
        total_pnl       : float
        max_drawdown    : float
        total_signals   : int
        signal_log      : list of {bar_idx, direction, score, entry, sl, tp1}
    """
    if buy_fn is None or sell_fn is None or stop_loss_fn is None:
        from engine.strategy_dispatch import resolve_strategy
        _resolved_buy, _resolved_sell, _resolved_sl, _ = resolve_strategy(pair)
        buy_fn        = buy_fn        or _resolved_buy
        sell_fn       = sell_fn       or _resolved_sell
        stop_loss_fn  = stop_loss_fn  or _resolved_sl
        # If the pair auto-resolves to trend_retest and the caller didn't
        # supply their own adaptive dict, apply its live-tuned per-pair
        # params (config.TREND_RETEST_PARAMS_PER_PAIR) instead of silently
        # falling back to trend_retest's untuned defaults — otherwise a
        # dashboard-triggered Backtest/Walk-Forward run for CHF_JPY/EUR_AUD
        # would test different params than what's actually live. Scoped to
        # the auto-resolve path only — explicit callers (CLI --strategy,
        # grid-search research code) keep full control of `adaptive`.
        # Added 2026-08-16 — see tasks/todo.md.
        if adaptive is None and config.STRATEGY_OVERRIDE.get(pair) == "trend_retest":
            adaptive = dict(config.TREND_RETEST_PARAMS_PER_PAIR.get(pair, {}))

    # Strategy modules keep module-level state across calls (EMA auto-fit
    # cache in particular) that otherwise leaks between repeated backtests
    # for the same pair in one process — e.g. run_walk_forward()'s grid
    # search and WFO both call this function many times back-to-back for
    # the same pair. Without clearing first, a later call can silently
    # reuse an EMA fit computed against a different bar range than its own,
    # making results non-reproducible even with byte-identical arguments
    # (confirmed: two back-to-back calls with the exact same df_h1/df_h4
    # produced different trade counts and PnL before this fix).
    _clear_ema_cache()
    _clear_breakout_cache()
    _clear_trend_cache()
    _clear_gold_cache()
    _clear_trend_retest_cache()

    if len(df_h1) < _WARMUP + 50 or len(df_h4) < 50:
        logger.warning("Backtest %s: insufficient data (%d primary, %d confirm)", pair, len(df_h1), len(df_h4))
        return {"error": "insufficient_data"}

    pt          = PaperTrader(starting_balance, save_path=False)  # no disk I/O during backtest
    equity      = []
    signal_log  = []
    peak        = starting_balance
    daily_prep  = _prep_daily_trend(df_d)

    for i in range(_WARMUP, len(df_h1)):
        bar_time = df_h1.index[i]
        d_trend  = _d_trend_at(daily_prep, bar_time)

        # Build H1 slice up to and including bar i
        slice_h1 = df_h1.iloc[:i + 1].copy()

        # Build matching H4 slice: bars whose index <= current H1 bar time
        slice_h4 = df_h4[df_h4.index <= bar_time].copy()
        if len(slice_h4) < 50:
            continue

        # Tick open trades against current candle
        row = df_h1.iloc[i]
        atr_value = None
        if config.ATR_TRAILING_ENABLED and len(slice_h1) >= config.ATR_TRAILING_PERIOD:
            try:
                atr_value = float(_atr_fn(slice_h1["high"], slice_h1["low"], slice_h1["close"],
                                           config.ATR_TRAILING_PERIOD).iloc[-1])
            except Exception:
                atr_value = None
        pt.update(pair, float(row["high"]), float(row["low"]), float(row["close"]), atr_value=atr_value)

        equity.append({"bar_idx": i, "balance": pt.balance, "nav": pt.balance + pt._calc_unrealized()})
        peak = max(peak, pt.balance)

        # Check for new signal (skip if already open on this pair)
        if pt.get_open_trade(pair):
            continue

        buy_score  = buy_fn(pair, slice_h1, slice_h4, adaptive=adaptive)
        sell_score = sell_fn(pair, slice_h1, slice_h4, adaptive=adaptive)

        if buy_score == sell_score == 0:
            continue

        if buy_score >= sell_score:
            direction, ema_score = "long", buy_score
        else:
            direction, ema_score = "short", sell_score

        if require_d_trend_alignment:
            _wants = "bull" if direction == "long" else "bear"
            if d_trend != _wants:
                continue

        pivots_h1    = detect_pivots(slice_h1)
        structure    = classify_structure(pivots_h1)
        pivots_h4    = detect_pivots(slice_h4)
        bos_h4       = detect_bos_choch(slice_h4, pivots_h4, classify_structure(pivots_h4))
        sr_zones     = get_sr_zones(slice_h1, pivots_h1)
        entry        = float(slice_h1["close"].iloc[-1])
        bos_ok       = bos_h4["bos"] and bos_h4["bos_direction"] == direction
        struct_score = score_structure(structure, direction, entry, sr_zones, bos_ok)

        patterns = detect_patterns(slice_h1)
        pa_score = max(0.0, score_price_action(patterns, direction))

        # ML score boost (config.ML_SCORE_BOOST_ENABLED, off by default — see
        # config.py). Features must be computed before scoring so a real
        # prediction can influence final_score, same as engine/signal_engine.py's
        # live wiring. "confluence_score" here is the pre-boost raw sum
        # (ema+struct+pa), matching what a live signal's feature row looked
        # like historically (boost never fired before today, so signal_log.csv's
        # training data has confluence_score == the unboosted sum) — patched to
        # the true final_score below, after scoring, for the seed row that
        # actually gets saved.
        ml_win_prob = None
        if getattr(config, "ML_SCORE_BOOST_ENABLED", False):
            _provisional = _bt_sig_features(
                pair, direction, bar_time, slice_h1, slice_h4,
                ema_score, struct_score, pa_score, ema_score + struct_score + pa_score,
                structure, sr_zones, bos_ok, patterns, entry, d_trend,
            )
            try:
                ml_win_prob = _ml_model().predict_win_prob(_provisional)
            except Exception:
                ml_win_prob = None

        final_score = score_signal(ema_score, struct_score, pa_score, ml_win_prob)

        if final_score < min_score:
            continue

        # Session filter: skip trades outside London/NY overlap (07:00–21:00 UTC).
        # Intraday bars only — Daily+ candles are timestamped at the broker's
        # daily close (OANDA: 21:00 UTC), outside every FX session window by
        # construction, which would zero out 100% of Daily/Weekly backtests
        # regardless of the strategy's own decision (found 2026-08-01 testing
        # strategy_gold_trend.py at Daily — same fix applied inside that
        # module's own session gate, this is a second, independent copy).
        if len(slice_h1) < 2 or (slice_h1.index[-1] - slice_h1.index[-2]) < pd.Timedelta(hours=20):
            bar_hour = bar_time.hour if hasattr(bar_time, 'hour') else 12
            if not (config.SESSION_START_UTC <= bar_hour < config.SESSION_END_UTC):
                continue

        # Place simulated trade
        stop_loss = stop_loss_fn(pair, slice_h1, direction)
        if tp_fn is None:
            tp_levels = get_tp_levels(entry, stop_loss, direction, pair)
        else:
            tp_levels = tp_fn(entry, stop_loss, direction, pair, slice_h1)
        size      = calculate_position_size(pt.balance, entry, stop_loss, pair)
        if size <= 0:
            continue

        sig_feat = _bt_sig_features(
            pair, direction, bar_time,
            slice_h1, slice_h4,
            ema_score, struct_score, pa_score, final_score,
            structure, sr_zones, bos_ok, patterns, entry, d_trend,
        )
        pt.open_trade(pair=pair, direction=direction, entry_price=entry,
                      sl=stop_loss, tp_levels=tp_levels, size=size,
                      extra={"_sig": sig_feat})

        signal_log.append({
            "bar_idx":  i,
            "time":     bar_time.isoformat(),
            "direction": direction,
            "score":    final_score,
            "entry":    entry,
            "sl":       stop_loss,
            "tp1":      tp_levels["tp1"],
        })

    # Close any remaining open positions at last bar price
    last_price = float(df_h1["close"].iloc[-1])
    for t in list(pt.open_trades):
        pt.manual_close(t["id"], last_price)

    closed  = pt.closed_trades
    total   = len(closed)
    wins    = sum(1 for t in closed if t.get("realised_pnl", 0) > 0)
    wr      = wins / total if total else 0.0
    tot_pnl = sum(t.get("realised_pnl", 0) for t in closed)

    # Max drawdown from equity curve
    max_dd = 0.0
    peak_eq = starting_balance
    for pt_eq in equity:
        peak_eq   = max(peak_eq, pt_eq["nav"])
        dd        = (peak_eq - pt_eq["nav"]) / peak_eq if peak_eq > 0 else 0
        max_dd    = max(max_dd, dd)

    return {
        "pair":           pair,
        "bars":           len(df_h1),
        "total_signals":  len(signal_log),
        "total_trades":   total,
        "wins":           wins,
        "win_rate":       round(wr, 4),
        "total_pnl":      round(tot_pnl, 2),
        "profit_factor":  _calc_profit_factor(closed),
        "final_balance":  round(pt.balance, 2),
        "max_drawdown":   round(max_dd, 4),
        "trades":         closed,
        "equity_curve":   equity,
        "signal_log":     signal_log,
    }


def _calc_profit_factor(trades: list) -> float:
    """Gross profit / gross loss from a list of closed trade dicts."""
    gross_profit = sum(t.get("realised_pnl", 0) for t in trades if t.get("realised_pnl", 0) > 0)
    gross_loss   = abs(sum(t.get("realised_pnl", 0) for t in trades if t.get("realised_pnl", 0) < 0))
    if gross_loss == 0:
        return round(gross_profit, 2) if gross_profit else 0.0
    return round(gross_profit / gross_loss, 2)


def run_walk_forward(
    pair: str,
    df_h1: pd.DataFrame,
    df_h4: pd.DataFrame,
    train_bars: int = 1500,
    test_bars: int  = 750,
    step_bars: int  = 500,
    starting_balance: float = 500.0,
    market: str = "forex",
    param_grid: dict | None = None,
) -> dict:
    """
    Sliding-window walk-forward optimisation.

    For each window:
      1. Train  = df_h1[i : i+train_bars]  — grid-search min_score by IS profit factor
      2. Test   = df_h1[i+train_bars : i+train_bars+test_bars]  — evaluate best params OOS
      3. Advance i by step_bars

    Returns:
        {
          "pair": ...,
          "windows": [
            {
              "window": 1,
              "train_range": ("2024-01-01", "2024-04-01"),
              "test_range":  ("2024-04-01", "2024-07-01"),
              "best_params": {"min_score": 55},
              "pf_is":  1.8,
              "pf_oos": 1.4,
              "win_rate_oos": 0.56,
              "trade_count_oos": 12,
              "stability": 0.78,       # oos/is — closer to 1.0 = less overfit
            }, ...
          ],
          "avg_pf_oos":   ...,   # weighted by trade_count_oos, not a flat
                                 # mean across windows — see 2026-08-13 fix
          "avg_stability": ...,  # same weighting
          "best_global_params": {"min_score": ...},
        }
    """
    if param_grid is None:
        param_grid = {"min_score": [50, 58, 65]}   # 3 values keeps total runs ~< 25

    total_needed = train_bars + test_bars
    if len(df_h1) < total_needed:
        return {"error": f"Need at least {total_needed} primary-TF bars, got {len(df_h1)}"}

    max_windows = 6   # cap to keep runtime under ~60 s in a sync Dash callback
    windows_out = []
    i = 0
    window_num = 0
    _ratio = confirm_tf_ratio(pair)   # primary-TF bars per confirm-TF bar (e.g. 8 for M30/H4)

    while i + total_needed <= len(df_h1) and window_num < max_windows:
        window_num += 1
        train_df = df_h1.iloc[i : i + train_bars]
        test_df  = df_h1.iloc[i + train_bars : i + train_bars + test_bars]

        # Align confirm-TF data for each window — confirm has 1/_ratio as many
        # bars as primary over the same real time span.
        train_h4 = df_h4.iloc[i // _ratio : (i + train_bars) // _ratio] if len(df_h4) > (i + train_bars) // _ratio else df_h4
        test_h4  = df_h4.iloc[(i + train_bars) // _ratio : (i + total_needed) // _ratio] if len(df_h4) > (i + total_needed) // _ratio else df_h4

        # ── In-sample grid search ─────────────────────────────────────────────
        best_is_pf    = -1.0
        best_params   = {"min_score": param_grid["min_score"][0]}

        for score_thresh in param_grid["min_score"]:
            res = run_backtest(pair, train_df, train_h4,
                               starting_balance=starting_balance,
                               min_score=score_thresh,
                               market=market)
            if "error" in res or res.get("total_trades", 0) < 3:
                continue
            pf = _calc_profit_factor(res.get("trades", []))
            if pf > best_is_pf:
                best_is_pf  = pf
                best_params = {"min_score": score_thresh}

        # ── Out-of-sample evaluation ──────────────────────────────────────────
        oos_res = run_backtest(pair, test_df, test_h4,
                               starting_balance=starting_balance,
                               min_score=best_params["min_score"],
                               market=market)
        if "error" in oos_res:
            i += step_bars
            continue

        pf_oos     = _calc_profit_factor(oos_res.get("trades", []))
        stability  = round(pf_oos / best_is_pf, 3) if best_is_pf > 0 else 0.0

        try:
            train_start = str(train_df.index[0].date())
            train_end   = str(train_df.index[-1].date())
            test_start  = str(test_df.index[0].date())
            test_end    = str(test_df.index[-1].date())
        except Exception:
            train_start = train_end = test_start = test_end = ""

        windows_out.append({
            "window":         window_num,
            "train_range":    (train_start, train_end),
            "test_range":     (test_start,  test_end),
            "best_params":    best_params,
            "pf_is":          round(best_is_pf, 2),
            "pf_oos":         round(pf_oos, 2),
            "win_rate_oos":   round(oos_res.get("win_rate", 0), 3),
            "trade_count_oos": oos_res.get("total_trades", 0),
            "stability":      stability,
        })

        i += step_bars

    if not windows_out:
        min_test_bars = 50 * _ratio
        hint = (f" (test_bars={test_bars} yields only ~{test_bars // _ratio} confirm-TF "
                f"bars per window — need >= 50; try test_bars >= {min_test_bars})"
                if test_bars // _ratio < 50 else "")
        return {"pair": pair, "windows": [], "error": "No valid windows produced" + hint}

    # Weighted by each window's OOS trade count, not a flat mean across
    # windows — found 2026-08-06 (tasks/todo.md, "WFO headline metrics vs
    # real live performance", Finding 1): a flat mean gives a 2-trade window
    # that got lucky (PF 15-19) equal weight to an 8-trade window sitting at
    # PF ~1, so the headline "Avg OOS PF" can read as a strong edge when
    # most of the real evidence says otherwise. A window with 0 OOS trades
    # contributes nothing (weight 0) rather than being scored as a PF-0.00
    # data point, which was also wrong — no trades isn't evidence of a bad
    # edge, it's no evidence at all.
    total_oos_trades = sum(w["trade_count_oos"] for w in windows_out)
    if total_oos_trades > 0:
        avg_pf_oos = round(
            sum(w["pf_oos"] * w["trade_count_oos"] for w in windows_out) / total_oos_trades, 2)
        avg_stab = round(
            sum(w["stability"] * w["trade_count_oos"] for w in windows_out) / total_oos_trades, 3)
    else:
        avg_pf_oos = 0.0
        avg_stab   = 0.0
    # Best global params = most frequently selected min_score across windows
    from collections import Counter
    score_counts = Counter(w["best_params"]["min_score"] for w in windows_out)
    best_global  = {"min_score": score_counts.most_common(1)[0][0]}

    return {
        "pair":              pair,
        "windows":           windows_out,
        "avg_pf_oos":        avg_pf_oos,
        "avg_stability":     avg_stab,
        "best_global_params": best_global,
    }


def save_results_csv(results: dict, path: str = "data/backtest_results.csv") -> None:
    """Write signal log and closed trades to a CSV file."""
    os.makedirs("data", exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "direction", "score", "entry", "sl", "tp1"])
        for s in results.get("signal_log", []):
            writer.writerow([s["time"], s["direction"], s["score"],
                             s["entry"], s["sl"], s["tp1"]])
    logger.info("Backtest results saved → %s", path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-8s  %(message)s")

    parser = argparse.ArgumentParser(description="Apex backtester")
    parser.add_argument("--pair",    default="USD_CAD")
    parser.add_argument("--bars",    type=int, default=2000)
    parser.add_argument("--balance", type=float, default=500.0)
    parser.add_argument("--output",  default="data/backtest_results.csv")
    parser.add_argument("--strategy", default="ema_bounce",
                        choices=["ema_bounce", "trend_follow", "breakout_retest", "gold_trend",
                                 "trend_retest", "random"],
                        help="ema_bounce (default, ranging-market mean-reversion), "
                             "trend_follow (fires only when ADX > threshold), "
                             "breakout_retest (break of structure + retest confirmation), "
                             "gold_trend (XAU_USD 200/50 EMA + RSI pullback), "
                             "trend_retest (H4-trend-gated break+retest+price-action, "
                             "2026-08-12 — see tasks/todo.md), or "
                             "random (control: random direction/timing at trend_retest's own "
                             "signal frequency, same SL/TP — isolates whether entry timing "
                             "itself has any edge)")
    parser.add_argument("--tp-mode", default="fixed_rr", choices=["fixed_rr", "structure"],
                        help="fixed_rr (default, config.TP_RR_PER_PAIR) or structure "
                             "(next opposing pivot level, strategy_trend_retest.get_tp_levels_structure "
                             "— falls back to fixed_rr per-level when no pivot qualifies)")
    parser.add_argument("--primary-tf", default=None,
                        help="Override config.TIMEFRAMES['primary'] (e.g. 'D' for Daily). "
                             "Needed to A/B a gold_trend backtest at Daily vs the bot's "
                             "default M30/H4 cadence — see tasks/todo.md 2026-08-01.")
    parser.add_argument("--confirm-tf", default=None,
                        help="Override config.confirm_tf_for(pair) (e.g. 'W' for Weekly, "
                             "pairing with --primary-tf D).")
    parser.add_argument("--fire-rate", type=float, default=0.02,
                        help="--strategy random only: per-bar probability of firing a "
                             "signal. Default is an arbitrary placeholder — pass a rate "
                             "calibrated to a real strategy's trades/bars over the same "
                             "window for a fair comparison, not the default.")
    args = parser.parse_args()

    if args.strategy == "trend_follow":
        from engine.strategy_trend_follow import check_buy_signal as _buy_fn, check_sell_signal as _sell_fn
        _sl_fn = get_stop_loss
    elif args.strategy == "breakout_retest":
        from engine.strategy_breakout_retest import (
            check_buy_signal as _buy_fn, check_sell_signal as _sell_fn, get_stop_loss as _sl_fn,
        )
    elif args.strategy == "gold_trend":
        from engine.strategy_gold_trend import (
            check_buy_signal as _buy_fn, check_sell_signal as _sell_fn, get_stop_loss as _sl_fn,
        )
    elif args.strategy == "trend_retest":
        from engine.strategy_trend_retest import (
            check_buy_signal as _buy_fn, check_sell_signal as _sell_fn, get_stop_loss as _sl_fn,
        )
    elif args.strategy == "random":
        from engine.strategy_trend_retest import get_stop_loss as _sl_fn
        _buy_fn, _sell_fn = random_signal_fns(args.fire_rate)
    else:
        _buy_fn, _sell_fn, _sl_fn = check_buy_signal, check_sell_signal, get_stop_loss

    _tp_fn = _tp_levels_structure if args.tp_mode == "structure" else None

    from connectors.oanda_connector import OandaConnector
    conn = OandaConnector()

    primary   = args.primary_tf or config.primary_tf_for(args.pair)
    confirm   = args.confirm_tf or config.confirm_tf_for(args.pair)
    _ratio    = max(1, _TF_MINUTES.get(confirm, 240) // _TF_MINUTES.get(primary, 30))
    _cf_bars  = max(50, args.bars // _ratio)
    logger.info("Fetching %d %s + %d %s bars for %s (strategy=%s) …",
                args.bars, primary, _cf_bars, confirm, args.pair, args.strategy)
    df_h1 = conn.get_candles(args.pair, primary, args.bars)
    df_h4 = conn.get_candles(args.pair, confirm, _cf_bars)

    # Daily candles for d_trend — 200 bars comfortably covers even the
    # largest bar counts this CLI is normally run with; best-effort, a
    # failed/short fetch just leaves d_trend at "neutral" (unchanged from
    # before this existed), it's a logged feature, not a trading gate.
    df_d = None
    try:
        df_d = conn.get_candles(args.pair, "D", 200)
    except Exception as exc:
        logger.warning("Daily candle fetch failed (d_trend will stay 'neutral'): %s", exc)

    res = run_backtest(args.pair, df_h1, df_h4,
                       starting_balance=args.balance,
                       market="forex",
                       buy_fn=_buy_fn, sell_fn=_sell_fn, stop_loss_fn=_sl_fn, tp_fn=_tp_fn,
                       df_d=df_d)

    if "error" in res:
        logger.error("Backtest failed: %s", res["error"])
    else:
        logger.info(
            "Backtest %s  bars=%d  signals=%d  trades=%d  WR=%.0f%%  "
            "PF=%.2f  PnL=$%.2f  MaxDD=%.1f%%  FinalBal=$%.2f",
            res["pair"], res["bars"], res["total_signals"], res["total_trades"],
            res["win_rate"] * 100, res.get("profit_factor", 0),
            res["total_pnl"], res["max_drawdown"] * 100, res["final_balance"],
        )
        save_results_csv(res, args.output)
