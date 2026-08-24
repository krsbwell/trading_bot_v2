"""
CRUD for the `decisions` table — one row per engine.decision.Decision the
adaptive strategy (or its orchestrator) produces. This is the "experience"
record from the source design doc: every decision, not just the ones that
became trades, so NO_TRADE decisions are kept too (needed later to learn
from what the strategy chose not to do, same spirit as
learning/shadow_outcomes.py already does for the other strategies).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from database.models import get_connection, init_schema


def insert_decision(decision: dict, db_path: "Path | str | None" = None) -> int:
    """`decision` is an engine.decision.Decision.as_dict() (or anything
    with the same keys). Returns the new row's id."""
    conn = get_connection(db_path)
    try:
        init_schema(conn)
        cur = conn.execute(
            """INSERT INTO decisions
               (pair, action, confidence, regime, model_version, stop_loss,
                take_profit, reasoning_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision["pair"], decision["action"], decision.get("confidence"),
                decision["regime"], decision.get("model_version"),
                decision.get("stop_loss"), decision.get("take_profit"),
                json.dumps(decision.get("reasoning") or {}),
                decision.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_decisions(pair: "str | None" = None, action: "str | None" = None,
                   limit: int = 100, db_path: "Path | str | None" = None) -> list:
    conn = get_connection(db_path)
    try:
        init_schema(conn)
        query = "SELECT * FROM decisions WHERE 1=1"
        params: list = []
        if pair is not None:
            query += " AND pair = ?"
            params.append(pair)
        if action is not None:
            query += " AND action = ?"
            params.append(action)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
