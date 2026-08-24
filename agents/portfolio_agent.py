"""
Portfolio-state facade — read-only queries over an existing
trade.trade_manager.TradeManager instance (never constructs its own; the
caller passes in whichever TradeManager main.py already owns).
"""


def open_trade_count(trade_manager) -> int:
    return len(trade_manager.open_trades)


def open_pairs(trade_manager) -> list:
    return [t["pair"] for t in trade_manager.open_trades.values()]


def is_pair_open(trade_manager, pair: str) -> bool:
    return pair in open_pairs(trade_manager)


def exposure_summary(trade_manager) -> dict:
    """Open trade count + pairs — enough for risk_agent.check_trade's
    inputs and for a dashboard/monitoring view later, without exposing
    TradeManager's full internal trade dicts to every caller."""
    return {
        "open_trade_count": open_trade_count(trade_manager),
        "open_pairs": open_pairs(trade_manager),
    }
