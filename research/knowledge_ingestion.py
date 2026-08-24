"""
Writes a knowledge-record dict (the shape research.web_research.fetch()
and research.news_collector items use) into memory.knowledge_memory /
database.knowledge, deduping on content_hash — matches the source design
doc's own knowledge-record schema (source/url/retrieved_at/published_at/
title/content_hash/summary/topics).
"""
from memory import knowledge_memory


def ingest(record: dict) -> "int | None":
    """Returns the new row id, or None if this content_hash was already
    stored (not an error — see database.knowledge.insert_knowledge)."""
    return knowledge_memory.store(
        source=record["source"],
        content_hash=record["content_hash"],
        url=record.get("url"),
        published_at=record.get("published_at"),
        title=record.get("title"),
        summary=record.get("summary"),
        topics=record.get("topics"),
    )


def ingest_many(records: list) -> int:
    """Returns how many were newly stored (excludes dedup skips)."""
    return sum(1 for r in records if ingest(r) is not None)
