"""
Per-pair strategy function dispatch — shared by the live signal engine and
the backtester so both always route a pair to the same buy/sell/stop-loss/
diagnostics functions, per config.STRATEGY_OVERRIDE. A pair not listed
there uses "ema_bounce".

Kept dependency-free of engine.signal_engine and backtest.runner (only
imports the strategy modules themselves + config) so both of those can
import from here without a circular import.
"""
import config
from engine.strategy_ema_cci_macd import (
    check_buy_signal as _ema_buy, check_sell_signal as _ema_sell,
    get_stop_loss as _ema_sl, get_last_diag as _ema_diag,
)
from engine.strategy_breakout_retest import (
    check_buy_signal as _br_buy, check_sell_signal as _br_sell,
    get_stop_loss as _br_sl, get_last_diag as _br_diag,
)
from engine.strategy_gold_trend import (
    check_buy_signal as _gt_buy, check_sell_signal as _gt_sell,
    get_stop_loss as _gt_sl, get_last_diag as _gt_diag,
)
from engine.strategy_trend_retest import (
    check_buy_signal as _tr_buy, check_sell_signal as _tr_sell,
    get_stop_loss as _tr_sl, get_last_diag as _tr_diag,
)

# All 4 (buy, sell, stop-loss, diagnostics) must route together, since a
# pair's diagnostics only exist in whichever module's check_buy/sell_signal
# actually ran for it.
STRATEGY_FNS = {
    "ema_bounce":      (_ema_buy, _ema_sell, _ema_sl, _ema_diag),
    "breakout_retest": (_br_buy, _br_sell, _br_sl, _br_diag),
    "gold_trend":      (_gt_buy, _gt_sell, _gt_sl, _gt_diag),
    # Backtest-only as of 2026-08-12 — no config.STRATEGY_OVERRIDE entry yet.
    # See tasks/todo.md for the design + validation this was built from.
    "trend_retest":    (_tr_buy, _tr_sell, _tr_sl, _tr_diag),
}


def resolve_strategy(pair: str):
    name = getattr(config, "STRATEGY_OVERRIDE", {}).get(pair, "ema_bounce")
    return STRATEGY_FNS.get(name, STRATEGY_FNS["ema_bounce"])
