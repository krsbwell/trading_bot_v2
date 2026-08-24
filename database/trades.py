"""
CRUD for the `trade_outcomes` table — scoped to trades produced by the
adaptive strategy only (linked back to the Decision that opened them via
decision_id). This is NOT a general trade log — the existing
data/signal_log.csv (learning/data_collector.py) and
data/live_state_forex.json (trade/trade_manager.py) remain the source of
truth for every other strategy's trades, unchanged. This table stays
empty until config.ADAPTIVE_STRATEGY["enabled"] is on and a pair is
actually routed to "adaptive" — there is no data in it yet.
"""
from datetime import datetime, timezone
from pathlib import Path

from database.models import get_connection, init_schema


def insert_trade_outcome(pair: str, direction: str, pnl: float, outcome: str,
                          decision_id: "int | None" = None,
                          entry_price: "float | None" = None,
                          exit_price: "float | None" = None,
                          opened_at: "str | None" = None,
                          db_path: "Path | str | None" = None) -> int:
    conn = get_connection(db_path)
    try:
        init_schema(conn)
        cur = conn.execute(
            """INSERT INTO trade_outcomes
               (decision_id, pair, direction, entry_price, exit_price, pnl,
                outcome, opened_at, closed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (decision_id, pair, direction, entry_price, exit_price, pnl, outcome,
             opened_at, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_trade_outcomes(pair: "str | None" = None, limit: int = 200,
                        db_path: "Path | str | None" = None) -> list:
    conn = get_connection(db_path)
    try:
        init_schema(conn)
        query = "SELECT * FROM trade_outcomes WHERE 1=1"
        params: list = []
        if pair is not None:
            query += " AND pair = ?"
            params.append(pair)
        query += " ORDER BY closed_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def win_rate_by_regime(db_path: "Path | str | None" = None) -> dict:
    """Joins trade_outcomes back to the decisions table via decision_id to
    answer 'which regime has this strategy actually won in' — the kind of
    question that needs both tables, which is the whole reason this is a
    real join-capable store instead of another flat CSV. Empty dict until
    there's at least one closed adaptive-strategy trade."""
    conn = get_connection(db_path)
    try:
        init_schema(conn)
        rows = conn.execute(
            """SELECT d.regime AS regime,
                      SUM(CASE WHEN t.outcome = 'win' THEN 1 ELSE 0 END) AS wins,
                      COUNT(*) AS total
               FROM trade_outcomes t
               JOIN decisions d ON d.id = t.decision_id
               GROUP BY d.regime"""
        ).fetchall()
        return {r["regime"]: {"wins": r["wins"], "total": r["total"],
                               "win_rate": r["wins"] / r["total"] if r["total"] else None}
                for r in rows}
    finally:
        conn.close()
