"""
Read-only query facade over this project's EXISTING trade storage
(data/live_state_<market>.json, written by trade/trade_manager.py) plus
database.trades for the adaptive strategy's own outcomes once it has any.

Deliberately does not read/write data/signal_log.csv here — that's
learning/data_collector.py's job and it already has its own read helpers
(learning/pattern_learner.py's _load_closed, learning/feedback_loop.py's
_load_closed). This module only adds what didn't already exist: querying
closed trades, and (once populated) joining them with the regime they
happened in via database.trades.win_rate_by_regime().
"""
import json
from pathlib import Path

from database.trades import get_trade_outcomes, win_rate_by_regime  # noqa: F401 (re-exported)

_ROOT = Path(__file__).parent.parent
_DEFAULT_STATE_PATH = _ROOT / "data" / "live_state_forex.json"


def recent_closed_trades(n: int = 20, state_path: "Path | str" = _DEFAULT_STATE_PATH) -> list:
    """Most-recent-first slice of the existing closed_trades list — read
    only, never writes to this file (trade/trade_manager.py owns it)."""
    path = Path(state_path)
    if not path.exists():
        return []
    try:
        state = json.loads(path.read_text("utf-8"))
    except Exception:
        return []
    closed = state.get("closed_trades", [])
    ordered = sorted(closed, key=lambda t: t.get("close_time") or "", reverse=True)
    return ordered[:n]


def adaptive_trade_outcomes(pair: "str | None" = None, n: int = 200,
                             db_path: "Path | str | None" = None) -> list:
    """Trades the adaptive strategy specifically produced (see
    database/trades.py — empty until it's enabled and has traded)."""
    return get_trade_outcomes(pair=pair, limit=n, db_path=db_path)
