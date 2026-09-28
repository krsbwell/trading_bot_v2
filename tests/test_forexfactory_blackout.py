"""
2026-09-28 news-blackout fixes (connectors/forexfactory_connector.py):
  1. Pairs affected by an event were a stale hand-kept table — AUD_JPY,
     NZD_USD, CHF_JPY, EUR_JPY never blacked out. Now: event currency on
     either side of the pair.
  2. A failed fetch cached [] for 60 min, silently disabling the blackout.
     Now the last good calendar is kept and retried in a few minutes.
No network: _fetch is monkeypatched.
"""
from datetime import datetime, timedelta, timezone

import pytest

import connectors.forexfactory_connector as ff


@pytest.fixture(autouse=True)
def _tmp_disk_cache(tmp_path, monkeypatch):
    """Never write data/ff_calendar_cache.json from tests."""
    monkeypatch.setattr(ff, "DISK_CACHE", str(tmp_path / "ff_calendar_cache.json"))


def _event(currency, minutes_from_now, impact="high"):
    return {"event": f"{currency} rate decision", "country": currency, "currency": currency,
            "impact": impact, "time": datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now),
            "pairs": [], "source": "ForexFactory"}


def _load(monkeypatch, events):
    monkeypatch.setattr(ff, "_fetch", lambda: events)
    monkeypatch.setattr(ff, "_cached", [])
    monkeypatch.setattr(ff, "_cache_expiry", None)


def test_jpy_and_aud_news_black_out_aud_jpy(monkeypatch):
    _load(monkeypatch, [_event("JPY", 10)])
    assert ff.is_news_blackout("AUD_JPY")[0] is True
    _load(monkeypatch, [_event("AUD", -5)])
    assert ff.is_news_blackout("AUD_JPY")[0] is True


def test_unrelated_currency_does_not_black_out(monkeypatch):
    _load(monkeypatch, [_event("CAD", 10)])
    assert ff.is_news_blackout("AUD_JPY")[0] is False
    assert ff.is_news_blackout("EUR_USD")[0] is False


def test_low_impact_and_outside_window_ignored(monkeypatch):
    _load(monkeypatch, [_event("USD", 10, impact="medium"), _event("USD", 120)])
    assert ff.is_news_blackout("EUR_USD")[0] is False


def test_failed_fetch_keeps_last_good_calendar(monkeypatch):
    _load(monkeypatch, [_event("USD", 10)])
    assert ff.is_news_blackout("GBP_USD")[0] is True            # good fetch cached
    monkeypatch.setattr(ff, "_fetch", lambda: None)              # feed now failing (e.g. 429)
    monkeypatch.setattr(ff, "_cache_expiry", datetime.now(timezone.utc) - timedelta(seconds=1))
    assert ff.is_news_blackout("GBP_USD")[0] is True            # still protected
    assert ff._cache_expiry <= datetime.now(timezone.utc) + timedelta(minutes=ff.RETRY_AFTER_FAIL_MIN)


def test_restart_loads_last_good_calendar_from_disk(monkeypatch):
    _load(monkeypatch, [_event("JPY", 10)])
    assert ff.is_news_blackout("AUD_JPY")[0] is True            # fetch OK -> written to disk
    # "restart": empty memory, and FF now rate-limits (429) the first fetch
    monkeypatch.setattr(ff, "_fetch", lambda: None)
    monkeypatch.setattr(ff, "_cached", [])
    monkeypatch.setattr(ff, "_cache_expiry", None)
    assert ff.is_news_blackout("AUD_JPY")[0] is True
