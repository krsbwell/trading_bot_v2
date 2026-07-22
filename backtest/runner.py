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
from engine.strategy_market_structure import (
    detect_pivots, classify_structure, detect_bos_choch, get_sr_zones, score_structure,
)
from engine.strategy_price_action import detect_patterns, score_price_action
from engine.confluence_scorer import score_signal
from engine.indicators import cci as _cci_fn, macd_histogram as _macd_h_fn, atr as _atr_fn, ema as _ema_fn
from risk.risk_manager import get_tp_levels, calculate_position_size
from trade.paper_trader import PaperTrader


_STRUCT_MAP = {"bullish": "uptrend", "bearish": "downtrend", "ranging": "ranging"}

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
        _pip   = 0.01 if "JPY" in pair else 0.0001
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
    buy_fn=check_buy_signal,
    sell_fn=check_sell_signal,
    stop_loss_fn=get_stop_loss,
    df_d: "pd.DataFrame | None" = None,
    require_d_trend_alignment: bool = False,
) -> dict:
    """
    Run a full backtest for one pair.

    buy_fn/sell_fn/stop_loss_fn: signal/stop-loss functions with the same
    signatures as strategy_ema_cci_macd's. Default to EMA-bounce; pass
    engine.strategy_trend_follow's or engine.strategy_breakout_retest's
    functions to backtest those strategies instead.

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

        # Session filter: skip trades outside London/NY overlap (07:00–21:00 UTC)
        bar_hour = bar_time.hour if hasattr(bar_time, 'hour') else 12
        if not (config.SESSION_START_UTC <= bar_hour < config.SESSION_END_UTC):
            continue

        # Place simulated trade
        stop_loss = stop_loss_fn(pair, slice_h1, direction)
        tp_levels = get_tp_levels(entry, stop_loss, direction)
        size      = calculate_position_size(pt.balance, entry, stop_loss, market, pair)
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

    # Build seed rows: closed trade signal features + outcome for PatternLearner seeding
    seed_rows = []
    for t in closed:
        sig = t.get("_sig")
        if not sig:
            continue
        seed_rows.append({
            **sig,
            "outcome":      "win" if t.get("realised_pnl", 0) > 0 else "loss",
            "realised_pnl": round(t.get("realised_pnl", 0), 4),
        })

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
        "seed_rows":      seed_rows,
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
          "avg_pf_oos":   ...,
          "avg_stability": ...,
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

    while i + total_needed <= len(df_h1) and window_num < max_windows:
        window_num += 1
        train_df = df_h1.iloc[i : i + train_bars]
        test_df  = df_h1.iloc[i + train_bars : i + train_bars + test_bars]

        # Align confirm-TF data for each window (M30→H1 is 2:1; was 4:1 for H1→H4)
        train_h4 = df_h4.iloc[i // 2 : (i + train_bars) // 2] if len(df_h4) > (i + train_bars) // 2 else df_h4
        test_h4  = df_h4.iloc[(i + train_bars) // 2 : (i + total_needed) // 2] if len(df_h4) > (i + total_needed) // 2 else df_h4

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
        return {"pair": pair, "windows": [], "error": "No valid windows produced"}

    avg_pf_oos  = round(sum(w["pf_oos"]    for w in windows_out) / len(windows_out), 2)
    avg_stab    = round(sum(w["stability"]  for w in windows_out) / len(windows_out), 3)
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
                        choices=["ema_bounce", "trend_follow", "breakout_retest"],
                        help="ema_bounce (default, ranging-market mean-reversion), "
                             "trend_follow (fires only when ADX > threshold), or "
                             "breakout_retest (break of structure + retest confirmation)")
    args = parser.parse_args()

    if args.strategy == "trend_follow":
        from engine.strategy_trend_follow import check_buy_signal as _buy_fn, check_sell_signal as _sell_fn
        _sl_fn = get_stop_loss
    elif args.strategy == "breakout_retest":
        from engine.strategy_breakout_retest import (
            check_buy_signal as _buy_fn, check_sell_signal as _sell_fn, get_stop_loss as _sl_fn,
        )
    else:
        _buy_fn, _sell_fn, _sl_fn = check_buy_signal, check_sell_signal, get_stop_loss

    from connectors.oanda_connector import OandaConnector
    conn = OandaConnector()

    primary = config.TIMEFRAMES["primary"]
    confirm = config.TIMEFRAMES["confirm"]
    logger.info("Fetching %d %s + %d %s bars for %s (strategy=%s) …",
                args.bars, primary, args.bars // 2, confirm, args.pair, args.strategy)
    df_h1 = conn.get_candles(args.pair, primary, args.bars)
    df_h4 = conn.get_candles(args.pair, confirm, args.bars // 2)

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
                       market="forex" if "_" in args.pair else "crypto",
                       buy_fn=_buy_fn, sell_fn=_sell_fn, stop_loss_fn=_sl_fn,
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
