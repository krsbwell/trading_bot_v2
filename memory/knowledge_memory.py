"""
Query/write facade over database.knowledge — external research items
(see research/knowledge_ingestion.py). Thin pass-through; kept as its own
module so callers depend on `memory.*` uniformly rather than some callers
reaching into `database.*` directly and others not.
"""
from pathlib import Path

from database.knowledge import get_knowledge, insert_knowledge


def store(source: str, content_hash: str, url: "str | None" = None,
          published_at: "str | None" = None, title: "str | None" = None,
          summary: "str | None" = None, topics: "list | None" = None,
          db_path: "Path | str | None" = None) -> "int | None":
    return insert_knowledge(source, content_hash, url=url, published_at=published_at,
                             title=title, summary=summary, topics=topics, db_path=db_path)


def recent(source: "str | None" = None, n: int = 50,
           db_path: "Path | str | None" = None) -> list:
    return get_knowledge(source=source, limit=n, db_path=db_path)
