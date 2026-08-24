"""
Thin adapter over the EXISTING connectors.forexfactory_connector — this
project already has an economic calendar (get_upcoming_events,
is_news_blackout), so this module does not reimplement one. It exists
only so agents.research_agent / research.market_research have one
consistent `research.*` import surface instead of some callers reaching
into connectors.forexfactory_connector directly and others not.
"""
from connectors.forexfactory_connector import get_upcoming_events, is_news_blackout

__all__ = ["get_upcoming_events", "is_news_blackout"]
