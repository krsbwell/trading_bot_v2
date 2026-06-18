"""
Backtesting runner for Apex Trading Bot.

Replays historical H1 + H4 candle data through the signal engine bar-by-bar,
simulates trades via PaperTrader, and writes a results CSV.

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
)
from engine.strategy_market_structure import (
    detect_pivots, classify_structure, detect_bos_choch, get_sr_zones, score_structure,
)
from engine.strategy_price_action import detect_patterns, score_price_action
from engine.confluence_scorer import score_signal
from risk.risk_manager import get_tp_levels, calculate_position_size
from trade.paper_trader import PaperTrader

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
) -> dict:
    """
    Run a full backtest for one pair.

    Returns a dict with:
        trades          : list of closed trade dicts
        equity_curve    : list of {bar_idx, balance} snapshots
        win_rate        : float
        total_pnl       : float
        max_drawdown    : float
        total_signals   : int
        signal_log      : list of {bar_idx, direction, score, entry, sl, tp1}
    """
    if len(df_h1) < _WARMUP + 50 or len(df_h4) < 50:
        logger.warning("Backtest %s: insufficient data (%d H1, %d H4)", pair, len(df_h1), len(df_h4))
        return {"error": "insufficient_data"}

    pt          = PaperTrader(starting_balance, save_path=Path("data/_bt_tmp_state.json"))
    equity      = []
    signal_log  = []
    peak        = starting_balance

    for i in range(_WARMUP, len(df_h1)):
        bar_time = df_h1.index[i]

        # Build H1 slice up to and including bar i
        slice_h1 = df_h1.iloc[:i + 1].copy()

        # Build matching H4 slice: bars whose index <= current H1 bar time
        slice_h4 = df_h4[df_h4.index <= bar_time].copy()
        if len(slice_h4) < 50:
            continue

        # Tick open trades against current candle
        row = df_h1.iloc[i]
        pt.update(pair, float(row["high"]), float(row["low"]), float(row["close"]))

        equity.append({"bar_idx": i, "balance": pt.balance, "nav": pt.balance + pt._calc_unrealized()})
        peak = max(peak, pt.balance)

        # Check for new signal (skip if already open on this pair)
        if pt.get_open_trade(pair):
            continue

        buy_score  = check_buy_signal(pair, slice_h1, slice_h4)
        sell_score = check_sell_signal(pair, slice_h1, slice_h4)

        if buy_score == sell_score == 0:
            continue

        if buy_score >= sell_score:
            direction, ema_score = "long", buy_score
        else:
            direction, ema_score = "short", sell_score

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

        final_score = score_signal(ema_score, struct_score, pa_score)

        if final_score < min_score:
            continue

        # Session filter: skip trades outside London/NY overlap (07:00–21:00 UTC)
        bar_hour = bar_time.hour if hasattr(bar_time, 'hour') else 12
        if not (config.SESSION_START_UTC <= bar_hour < config.SESSION_END_UTC):
            continue

        # Place simulated trade
        stop_loss = get_stop_loss(pair, slice_h1, direction)
        tp_levels = get_tp_levels(entry, stop_loss, direction)
        size      = calculate_position_size(pt.balance, entry, stop_loss, market, pair)
        if size <= 0:
            continue

        pt.open_trade(pair=pair, direction=direction, entry_price=entry,
                      sl=stop_loss, tp_levels=tp_levels, size=size)

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

    # Clean up temp state file
    try:
        Path("data/_bt_tmp_state.json").unlink(missing_ok=True)
    except Exception:
        pass

    return {
        "pair":           pair,
        "bars":           len(df_h1),
        "total_signals":  len(signal_log),
        "total_trades":   total,
        "wins":           wins,
        "win_rate":       round(wr, 4),
        "total_pnl":      round(tot_pnl, 2),
        "final_balance":  round(pt.balance, 2),
        "max_drawdown":   round(max_dd, 4),
        "trades":         closed,
        "equity_curve":   equity,
        "signal_log":     signal_log,
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
    parser.add_argument("--pair",    default="EUR_USD")
    parser.add_argument("--bars",    type=int, default=2000)
    parser.add_argument("--balance", type=float, default=500.0)
    parser.add_argument("--output",  default="data/backtest_results.csv")
    args = parser.parse_args()

    from connectors.oanda_connector import OandaConnector
    conn = OandaConnector()

    logger.info("Fetching %d H1 + %d H4 bars for %s …", args.bars, args.bars // 4, args.pair)
    df_h1 = conn.get_candles(args.pair, "H1", args.bars)
    df_h4 = conn.get_candles(args.pair, "H4", args.bars // 4)

    res = run_backtest(args.pair, df_h1, df_h4,
                       starting_balance=args.balance,
                       market="forex" if "_" in args.pair else "crypto")

    if "error" in res:
        logger.error("Backtest failed: %s", res["error"])
    else:
        logger.info(
            "Backtest %s  bars=%d  signals=%d  trades=%d  WR=%.0f%%  "
            "PnL=$%.2f  MaxDD=%.1f%%  FinalBal=$%.2f",
            res["pair"], res["bars"], res["total_signals"], res["total_trades"],
            res["win_rate"] * 100, res["total_pnl"],
            res["max_drawdown"] * 100, res["final_balance"],
        )
        save_results_csv(res, args.output)
