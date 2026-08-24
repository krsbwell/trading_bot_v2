"""
Query/write facade over database.experiences (the `decisions` table) —
the adaptive strategy's "experience memory". Every Decision it produces
gets recorded here, including NO_TRADE ones, not just the ones that became
trades — matching how learning/shadow_outcomes.py already tracks what the
other strategies chose not to take.
"""
from pathlib import Path

from database.experiences import get_decisions, insert_decision
from engine.decision import Decision


def record(decision: Decision, db_path: "Path | str | None" = None) -> int:
    return insert_decision(decision.as_dict(), db_path=db_path)


def recent(pair: "str | None" = None, action: "str | None" = None, n: int = 20,
           db_path: "Path | str | None" = None) -> list:
    return get_decisions(pair=pair, action=action, limit=n, db_path=db_path)


def recent_trade_decisions(pair: "str | None" = None, n: int = 20,
                            db_path: "Path | str | None" = None) -> list:
    """Decisions that actually resulted in a BUY/SELL, excluding
    NO_TRADE/HOLD — useful when a caller only wants ones that could have a
    linked trade_outcomes row."""
    buys  = get_decisions(pair=pair, action="BUY",  limit=n, db_path=db_path)
    sells = get_decisions(pair=pair, action="SELL", limit=n, db_path=db_path)
    combined = sorted(buys + sells, key=lambda d: d["created_at"], reverse=True)
    return combined[:n]
