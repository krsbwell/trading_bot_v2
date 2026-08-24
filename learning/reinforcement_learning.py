"""
Reinforcement-learning SCAFFOLD only — no algorithm is implemented or
wired into any live decision. See tasks/todo.md 2026-08-21 Phase 5.

There are 19 closed trades in this project's entire history as of
2026-08-21 (data/live_state_forex.json). That is nowhere near enough to
train a real RL policy on, and this module does not pretend otherwise —
per the source design doc's own "do not fabricate performance" rule,
train_step() reports its status honestly (INSUFFICIENT_DATA below
config.LEARNING_ADAPTIVE["min_samples_for_rl_train_step"], NOT_IMPLEMENTED
above it) rather than returning a trained-looking result either way.

No new storage: (state, action, reward) tuples are derived from the
existing `decisions`/`trade_outcomes` tables (database/experiences.py,
database/trades.py) via a join, the same relational data
database.trades.win_rate_by_regime() already uses for a different
question — not a third copy of trade history.
"""
import json
import logging

import config
from database.models import DEFAULT_DB_PATH, get_connection, init_schema

logger = logging.getLogger(__name__)


def get_experience_tuples(db_path: "str | None" = None) -> list:
    """Every adaptive-strategy trade that has both a Decision and a
    resolved outcome, shaped as {"state": <reasoning dict>, "action": ..,
    "reward": <pnl>}. Empty list until the adaptive strategy has actually
    traded (config.ADAPTIVE_STRATEGY["enabled"] is False by default)."""
    conn = get_connection(db_path)
    try:
        init_schema(conn)
        rows = conn.execute(
            """SELECT d.reasoning_json AS state_json, d.action, t.pnl AS reward
               FROM trade_outcomes t JOIN decisions d ON d.id = t.decision_id"""
        ).fetchall()
    finally:
        conn.close()

    tuples = []
    for r in rows:
        try:
            state = json.loads(r["state_json"]) if r["state_json"] else {}
        except (TypeError, ValueError):
            state = {}
        tuples.append({"state": state, "action": r["action"], "reward": r["reward"]})
    return tuples


def train_step(db_path: "str | None" = None, min_samples: "int | None" = None) -> dict:
    """Never trains a real policy in this pass. Returns a status dict:

    {"status": "INSUFFICIENT_DATA", ...}  — below the configured sample floor
    {"status": "NOT_IMPLEMENTED", ...}     — enough samples exist, but no RL
                                              algorithm is wired up yet

    Deliberately never returns anything that looks like a trained result —
    there is no policy object, no weights, nothing a caller could mistake
    for "this strategy has learned something" from this pass alone.
    """
    min_samples = min_samples if min_samples is not None else config.LEARNING_ADAPTIVE["min_samples_for_rl_train_step"]
    tuples = get_experience_tuples(db_path=db_path)
    n = len(tuples)

    if n < min_samples:
        logger.info("reinforcement_learning.train_step: INSUFFICIENT_DATA (%d/%d)", n, min_samples)
        return {"status": "INSUFFICIENT_DATA", "n_samples": n, "min_required": min_samples}

    logger.info("reinforcement_learning.train_step: %d samples available but no RL algorithm implemented", n)
    return {
        "status": "NOT_IMPLEMENTED",
        "n_samples": n,
        "min_required": min_samples,
        "note": "Sample count is sufficient to consider real RL training, but no "
                "algorithm is implemented in this pass — see tasks/todo.md 2026-08-21.",
    }
