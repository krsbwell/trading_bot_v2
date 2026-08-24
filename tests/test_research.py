"""
research/ package — built for the adaptive-strategy integration
(tasks/todo.md, 2026-08-21 Phase 4). Reverses the Finnhub-removal decision
from e75b937 in file-structure terms only — news_collector.py has no
registered source, so it always returns [] regardless of config, matching
"not resurrecting Finnhub without a specific ask" from that plan.
Everything here is disabled by default (config.RESEARCH["enabled"]=False)
and every test confirms that default produces empty/no-op results.
"""
import pytest

import config
import research.economic_calendar as economic_calendar
import research.knowledge_ingestion as knowledge_ingestion
import research.market_research as market_research
import research.news_collector as news_collector
import research.web_research as web_research
from database.models import init_schema


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    db_path = tmp_path / "adaptive_test.db"
    init_schema(db_path=db_path)
    import database.models as models
    monkeypatch.setattr(models, "DEFAULT_DB_PATH", db_path)
    yield db_path


# ── news_collector ───────────────────────────────────────────────────────

def test_news_disabled_by_default_returns_empty():
    assert news_collector.fetch_headlines("EUR_USD") == []


def test_news_enabled_but_no_source_returns_empty(monkeypatch):
    monkeypatch.setitem(config.RESEARCH, "enabled", True)
    assert news_collector.fetch_headlines("EUR_USD") == []


def test_news_enabled_unknown_source_returns_empty_not_raise(monkeypatch):
    monkeypatch.setitem(config.RESEARCH, "enabled", True)
    monkeypatch.setitem(config.RESEARCH, "news_source", "not_a_real_source")
    assert news_collector.fetch_headlines("EUR_USD") == []


def test_news_registered_source_gets_called(monkeypatch):
    monkeypatch.setitem(config.RESEARCH, "enabled", True)
    monkeypatch.setitem(config.RESEARCH, "news_source", "fake")
    monkeypatch.setitem(news_collector._SOURCES, "fake",
                         lambda pair, hours: [{"title": "test headline"}])
    result = news_collector.fetch_headlines("EUR_USD")
    assert result == [{"title": "test headline"}]


def test_news_source_exception_degrades_to_empty(monkeypatch):
    monkeypatch.setitem(config.RESEARCH, "enabled", True)
    monkeypatch.setitem(config.RESEARCH, "news_source", "broken")

    def _boom(pair, hours):
        raise RuntimeError("api down")
    monkeypatch.setitem(news_collector._SOURCES, "broken", _boom)
    assert news_collector.fetch_headlines("EUR_USD") == []


# ── web_research ─────────────────────────────────────────────────────────

def test_web_research_disabled_by_default_returns_none():
    assert web_research.fetch("https://example.com", source="test") is None


def test_web_research_refuses_non_https(monkeypatch):
    monkeypatch.setitem(config.RESEARCH, "enabled", True)
    assert web_research.fetch("http://example.com", source="test") is None


# ── knowledge_ingestion ──────────────────────────────────────────────────

def test_ingest_stores_and_dedups():
    record = {"source": "test", "content_hash": "abc", "title": "x"}
    first = knowledge_ingestion.ingest(record)
    second = knowledge_ingestion.ingest(record)
    assert first is not None
    assert second is None


def test_ingest_many_counts_only_new():
    records = [
        {"source": "test", "content_hash": "a"},
        {"source": "test", "content_hash": "b"},
        {"source": "test", "content_hash": "a"},   # duplicate
    ]
    assert knowledge_ingestion.ingest_many(records) == 2


# ── market_research ──────────────────────────────────────────────────────

def test_market_research_disabled_by_default_returns_empty():
    assert market_research.get_context("EUR_USD") == {}


def test_market_research_enabled_merges_calendar_and_news(monkeypatch):
    monkeypatch.setitem(config.RESEARCH, "enabled", True)
    monkeypatch.setattr(market_research, "get_upcoming_events", lambda hours: [{"title": "NFP"}])
    monkeypatch.setattr(market_research, "fetch_headlines", lambda pair, hours: [{"title": "headline"}])
    context = market_research.get_context("EUR_USD")
    assert context["upcoming_events"] == [{"title": "NFP"}]
    assert context["headlines"] == [{"title": "headline"}]
    assert "news_blackout" not in context   # opt-in flag was off


def test_market_research_news_blackout_only_when_opted_in(monkeypatch):
    monkeypatch.setitem(config.RESEARCH, "enabled", True)
    monkeypatch.setitem(config.RESEARCH, "news_blackout_enabled", True)
    monkeypatch.setattr(market_research, "get_upcoming_events", lambda hours: [])
    monkeypatch.setattr(market_research, "fetch_headlines", lambda pair, hours: [])
    monkeypatch.setattr(market_research, "is_news_blackout", lambda pair: (True, "NFP"))
    context = market_research.get_context("EUR_USD")
    assert context["news_blackout"] == {"blocked": True, "reason": "NFP"}


def test_market_research_never_raises_on_calendar_failure(monkeypatch):
    monkeypatch.setitem(config.RESEARCH, "enabled", True)

    def _boom(hours):
        raise RuntimeError("network down")
    monkeypatch.setattr(market_research, "get_upcoming_events", _boom)
    monkeypatch.setattr(market_research, "fetch_headlines", lambda pair, hours: [])
    context = market_research.get_context("EUR_USD")   # must not raise
    assert "upcoming_events" not in context


# ── economic_calendar (re-export sanity) ────────────────────────────────

def test_economic_calendar_reexports_existing_connector_functions():
    from connectors.forexfactory_connector import get_upcoming_events as real_fn
    assert economic_calendar.get_upcoming_events is real_fn
