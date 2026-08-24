"""
Risk facade — pure pass-through to risk.risk_manager. Never recomputes or
overrides anything it returns; this exists only so agents/orchestrator_agent
calls risk logic through the same `agents.*` surface as everything else,
while risk.risk_manager remains the sole, unmodified authority on
RISK_PER_TRADE / MAX_OPEN_TRADES / TRADING_HALTED, exactly as it already
is for every other strategy in this codebase.
"""
from risk.risk_manager import calculate_position_size, get_quote_to_usd_rate, validate_pre_trade


def check_trade(score: int, open_trade_count: int, pair: str, open_pairs: list) -> "tuple[bool, str]":
    return validate_pre_trade(score, open_trade_count, pair, open_pairs)


def position_size(account_balance: float, entry: float, stop_loss: float,
                   pair: str, connector=None) -> int:
    quote_to_usd = get_quote_to_usd_rate(pair, connector=connector)
    return calculate_position_size(account_balance, entry, stop_loss, pair, quote_to_usd)
