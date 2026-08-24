"""
Orchestrator run bookkeeping — the `agent_runs` table (database/models.py).
Used by agents/orchestrator_agent.py to answer "when did the adaptive
pipeline last run for this pair" and "how many NO_TRADEs in a row" without
re-deriving it from decisions every time.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from database.models import get_connection, init_schema


def record_run(pair: str, regime: "str | None", action_taken: str,
                notes: "dict | None" = None, db_path: "Path | str | None" = None) -> int:
    conn = get_connection(db_path)
    try:
        init_schema(conn)
        cur = conn.execute(
            "INSERT INTO agent_runs (pair, regime, action_taken, notes_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pair, regime, action_taken, json.dumps(notes or {}),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def last_run(pair: str, db_path: "Path | str | None" = None) -> "dict | None":
    conn = get_connection(db_path)
    try:
        init_schema(conn)
        row = conn.execute(
            "SELECT * FROM agent_runs WHERE pair = ? ORDER BY created_at DESC LIMIT 1",
            (pair,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def consecutive_no_trade_streak(pair: str, db_path: "Path | str | None" = None) -> int:
    """How many of the most-recent runs in a row were NO_TRADE/HOLD — for
    monitoring, not a gate; nothing currently blocks a pair for having a
    long streak of NO_TRADE, that's an expected, valid outcome per the
    source design doc ('a valid strategy can produce losing trades' /
    'must be able to return NO TRADE when evidence is insufficient')."""
    conn = get_connection(db_path)
    try:
        init_schema(conn)
        rows = conn.execute(
            "SELECT action_taken FROM agent_runs WHERE pair = ? ORDER BY created_at DESC LIMIT 500",
            (pair,),
        ).fetchall()
        streak = 0
        for r in rows:
            if r["action_taken"] in ("NO_TRADE", "HOLD"):
                streak += 1
            else:
                break
        return streak
    finally:
        conn.close()
