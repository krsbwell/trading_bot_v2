"""
CRUD for the `knowledge` table — external research items (economic
calendar entries, news, etc. — see research/knowledge_ingestion.py),
stored strictly as data with source metadata, per the source design doc's
own "web content is DATA, not CODE" rule and its knowledge-record schema
(source/URL/retrieved_at/published_at/title/content_hash/summary/topics).
Deduplicates on content_hash (UNIQUE constraint) — insert_knowledge() is
safe to call repeatedly with the same item.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from database.models import get_connection, init_schema


def insert_knowledge(source: str, content_hash: str, url: "str | None" = None,
                      published_at: "str | None" = None, title: "str | None" = None,
                      summary: "str | None" = None, topics: "list | None" = None,
                      db_path: "Path | str | None" = None) -> "int | None":
    """Returns the new row's id, or None if content_hash already exists
    (dedup — not an error, matches this codebase's "skip, don't fail" bias
    for expected duplicate conditions, e.g. learning/data_collector.py's
    _is_duplicate_open_skip)."""
    conn = get_connection(db_path)
    try:
        init_schema(conn)
        try:
            cur = conn.execute(
                """INSERT INTO knowledge
                   (source, url, retrieved_at, published_at, title, content_hash, summary, topics_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (source, url, datetime.now(timezone.utc).isoformat(), published_at,
                 title, content_hash, summary, json.dumps(topics or [])),
            )
            conn.commit()
            return cur.lastrowid
        except Exception as exc:   # sqlite3.IntegrityError on duplicate content_hash
            if "UNIQUE" in str(exc):
                return None
            raise
    finally:
        conn.close()


def get_knowledge(source: "str | None" = None, limit: int = 100,
                   db_path: "Path | str | None" = None) -> list:
    conn = get_connection(db_path)
    try:
        init_schema(conn)
        query = "SELECT * FROM knowledge WHERE 1=1"
        params: list = []
        if source is not None:
            query += " AND source = ?"
            params.append(source)
        query += " ORDER BY retrieved_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
