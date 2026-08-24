"""
Pluggable news-source interface. No source is wired up here — this
project's prior news integration (Finnhub) was deliberately removed as
dead code this month (see e75b937, "Remove Finnhub news integration
(dead)"), so this module does not resurrect it. It exists as scaffolding
for research/market_research.py's aggregation to call, and returns []
whenever nothing is actually configured, rather than failing.

To add a real source later: register a `fetch(pair, hours) -> list[dict]`
callable in _SOURCES under the name config.RESEARCH["news_source"] expects,
using an API key read from config.py/.env (never hard-coded — see this
project's existing config.TELEGRAM_BOT_TOKEN / OANDA_API_KEY pattern).
Each returned item should be shaped for research.knowledge_ingestion.ingest()
(source/url/published_at/title/summary/topics).
"""
import logging

import config

logger = logging.getLogger(__name__)

# Real source fetchers get registered here, e.g.
# _SOURCES["finnhub"] = _fetch_finnhub — none are wired up today.
_SOURCES: dict = {}


def fetch_headlines(pair: "str | None" = None, hours: int = 24) -> list:
    """Returns [] whenever research is disabled, no source is configured,
    or the configured source isn't actually registered — never raises."""
    if not config.RESEARCH.get("enabled"):
        return []
    source = config.RESEARCH.get("news_source")
    if not source:
        return []
    fetcher = _SOURCES.get(source)
    if fetcher is None:
        logger.warning("news_collector: news_source=%r has no registered fetcher — returning []", source)
        return []
    try:
        return fetcher(pair=pair, hours=hours) or []
    except Exception as exc:
        logger.warning("news_collector: %s fetch failed: %s", source, exc)
        return []
