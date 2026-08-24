"""
CRUD for the `regime_history` table — a snapshot per
engine.market_regime.classify_regime() call, kept so drift detection and
later analysis have real history to compare against instead of nothing
(there is none today; the ADX gate elsewhere in this codebase checks the
current bar only, never persists a series of regime calls).
"""
from datetime import datetime, timezone
from pathlib import Path

from database.models import get_connection, init_schema


def insert_regime_snapshot(pair: str, timeframe: str, regime_info: dict,
                            db_path: "Path | str | None" = None) -> int:
    """`regime_info` is engine.market_regime.classify_regime()'s return dict."""
    conn = get_connection(db_path)
    try:
        init_schema(conn)
        cur = conn.execute(
            """INSERT INTO regime_history
               (pair, timeframe, regime, confidence, adx, atr_pct, ema_slope_pct, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pair, timeframe, regime_info["regime"], regime_info.get("confidence"),
                regime_info.get("adx"), regime_info.get("atr_pct"), regime_info.get("ema_slope_pct"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_regime_history(pair: str, timeframe: str, limit: int = 200,
                        db_path: "Path | str | None" = None) -> list:
    conn = get_connection(db_path)
    try:
        init_schema(conn)
        rows = conn.execute(
            """SELECT * FROM regime_history WHERE pair = ? AND timeframe = ?
               ORDER BY created_at DESC LIMIT ?""",
            (pair, timeframe, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
