"""
Aggregates economic_calendar + news_collector into one research-context
dict — additive only. Nothing here blocks a trade; agents.research_agent
folds this straight into a Decision's reasoning dict. A pair can still opt
into gating on it via the EXISTING
connectors.forexfactory_connector.is_news_blackout() (config.RESEARCH
["news_blackout_enabled"]), which this module surfaces but does not call
itself — gating is a deliberate choice made by the caller, not implied by
merely having research enabled.
"""
import logging

import config
from research.economic_calendar import get_upcoming_events, is_news_blackout
from research.news_collector import fetch_headlines

logger = logging.getLogger(__name__)


def get_context(pair: str) -> dict:
    """Returns {} when research is disabled — callers should treat an
    empty dict and a populated one identically (both are valid, additive
    context), never require this to be non-empty."""
    if not config.RESEARCH.get("enabled"):
        return {}

    context: dict = {}
    try:
        context["upcoming_events"] = get_upcoming_events(hours=24)
    except Exception as exc:
        logger.debug("market_research: calendar fetch failed for %s: %s", pair, exc)

    if config.RESEARCH.get("news_blackout_enabled"):
        try:
            blocked, reason = is_news_blackout(pair)
            context["news_blackout"] = {"blocked": blocked, "reason": reason}
        except Exception as exc:
            logger.debug("market_research: news_blackout check failed for %s: %s", pair, exc)

    headlines = fetch_headlines(pair=pair, hours=24)
    if headlines:
        context["headlines"] = headlines

    return context
