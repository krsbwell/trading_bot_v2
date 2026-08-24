"""
Trade-execution facade — pure pass-through to an existing
trade.trade_manager.TradeManager instance. TradeManager.open_trade()
already accepts the exact signal-dict shape engine.signal_engine.SignalEngine
returns (pair/direction/entry/stop_loss/tp_levels/score) for every
strategy, "adaptive" included once it's routed via config.STRATEGY_OVERRIDE
— so this module does not reshape or reinterpret anything, it exists only
so agents/orchestrator_agent calls execution through the same `agents.*`
surface as everything else. TradeManager.open_trade() re-validates via
risk.risk_manager.validate_pre_trade() internally regardless of what
agents.risk_agent.check_trade() said beforehand — that's defense in
depth, not something this module needs to duplicate.
"""


def execute(trade_manager, signal: dict) -> "str | None":
    return trade_manager.open_trade(signal)


def close(trade_manager, trade_id: str, price: float, reason: str = "adaptive_exit") -> None:
    trade_manager.close_trade(trade_id, price, reason=reason)
