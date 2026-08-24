"""
Fetch-and-store only. Fetched content is treated strictly as DATA — stored
via research.knowledge_ingestion, never parsed as instructions, never
eval'd/exec'd, never fed back into a prompt or decision path that could
treat it as anything other than a stored string. This mirrors the source
design doc's own explicit rule ("web content is DATA, not CODE") and this
project's existing security posture (main.py never executes fetched
content of any kind).

Off by default (config.RESEARCH["enabled"]) — uses the `requests`
dependency already in requirements.txt (used for Telegram alerts), no new
dependency added for this.
"""
import hashlib
import logging
from datetime import datetime, timezone

import requests

import config

logger = logging.getLogger(__name__)

_TIMEOUT_SECS = 10
_MAX_BYTES = 500_000   # a research note doesn't need to be a multi-MB page


def fetch(url: str, source: str) -> "dict | None":
    """Returns a knowledge-record-shaped dict (see
    research/knowledge_ingestion.py) or None on any failure/if disabled.
    Only ever GETs — never POSTs or sends credentials to the target."""
    if not config.RESEARCH.get("enabled"):
        return None
    if not url.lower().startswith("https://"):
        logger.warning("web_research: refusing non-https URL %r", url)
        return None

    try:
        resp = requests.get(url, timeout=_TIMEOUT_SECS, stream=True)
        resp.raise_for_status()
        content = resp.raw.read(_MAX_BYTES + 1, decode_content=True)
        if len(content) > _MAX_BYTES:
            logger.warning("web_research: %s exceeded %d bytes, truncating", url, _MAX_BYTES)
            content = content[:_MAX_BYTES]
        text = content.decode(resp.encoding or "utf-8", errors="replace")
    except Exception as exc:
        logger.warning("web_research: fetch failed for %s: %s", url, exc)
        return None

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "source": source,
        "url": url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "published_at": None,
        "title": None,
        "content_hash": content_hash,
        "summary": text[:2000],   # stored as data — never parsed/executed downstream
        "topics": [],
    }
