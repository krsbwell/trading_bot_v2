"""
Health-check view over memory.agent_memory's orchestrator-run bookkeeping.
Read-only reporting — takes no action itself, same as
monitoring/system_health.py and this project's existing
scripts/watchdog.py (which does the actual alerting, unrelated mechanism,
not duplicated here).
"""
from memory import agent_memory

# A long streak of NO_TRADE is expected and valid (see engine/decision.py's
# docstring) — this is informational, not a problem flag by itself.
_LONG_STREAK_INFO_THRESHOLD = 50


def status_for_pair(pair: str) -> dict:
    last = agent_memory.last_run(pair)
    streak = agent_memory.consecutive_no_trade_streak(pair)
    return {
        "pair": pair,
        "last_run": last,
        "consecutive_no_trade_streak": streak,
        "long_streak_note": (
            "Long NO_TRADE streak — expected/valid on its own, worth a look "
            "alongside data/adaptive.db decisions if it persists for a long time."
            if streak >= _LONG_STREAK_INFO_THRESHOLD else None
        ),
    }
