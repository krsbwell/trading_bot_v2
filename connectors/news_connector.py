"""
Economic calendar feed — Finnhub free tier.

Fetches high/medium/low impact events for the current + next day, maps them to
the pairs we trade, and exposes two helpers:

  is_news_blackout(pair)     → True if a high-impact event fires within the
                               blackout window for any currency in that pair.
  get_upcoming_events(hours) → list of events in the next N hours, sorted by time.

Results are cached for CACHE_MINUTES so the API is called at most twice per hour.
Set FINNHUB_API_KEY in .env; leave blank to disable (all checks return False).
"""

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

# ── Currency-to-pair mapping ──────────────────────────────────────────────────
# Maps an affected currency to all traded pairs that contain it.
_CURRENCY_PAIRS: dict[str, list[str]] = {
    "USD": ["EUR_USD", "GBP_USD", "AUD_USD", "USD_CAD"],
    "GBP": ["GBP_USD", "GBP_JPY"],
    "EUR": ["EUR_USD", "EUR_AUD"],
    "JPY": ["GBP_JPY"],
    "CAD": ["USD_CAD"],
    "AUD": ["AUD_USD", "EUR_AUD"],
    "NZD": [],
    "CHF": [],
}

# ISO-2 country → currency
_COUNTRY_CURRENCY: dict[str, str] = {
    "US": "USD", "GB": "GBP", "EU": "EUR",
    "DE": "EUR", "FR": "EUR", "IT": "EUR", "ES": "EUR",
    "JP": "JPY", "CA": "CAD", "AU": "AUD",
    "NZ": "NZD", "CH": "CHF",
}

BLACKOUT_BEFORE_MINUTES = 30   # block signals this many minutes before event
BLACKOUT_AFTER_MINUTES  = 15   # block signals this many minutes after event
CACHE_MINUTES           = 30   # re-fetch from Finnhub every N minutes
_HIGH_IMPACT_ONLY       = True  # only block on high-impact; set False for med too

_cache_lock  = threading.Lock()
_cached_events: list[dict] = []
_cache_expiry: Optional[datetime] = None


def _api_key() -> str:
    return os.getenv("FINNHUB_API_KEY", "")


def _fetch_events() -> list[dict]:
    """Fetch 2 days of economic events from Finnhub. Returns [] on any error."""
    key = _api_key()
    if not key:
        return []
    try:
        import urllib.request, json
        today = datetime.now(timezone.utc).date()
        tomorrow = today + timedelta(days=2)
        url = (
            f"https://finnhub.io/api/v1/calendar/economic"
            f"?from={today}&to={tomorrow}&token={key}"
        )
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read())
        raw = data.get("economicCalendar", {}).get("result", [])
        events = []
        for ev in raw:
            country  = ev.get("country", "").upper()
            currency = _COUNTRY_CURRENCY.get(country)
            if not currency:
                continue
            impact = ev.get("impact", "low").lower()
            time_str = ev.get("time", "")
            try:
                # Finnhub returns UTC timestamps as "YYYY-MM-DD HH:MM:SS"
                ev_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
            events.append({
                "event":    ev.get("event", ""),
                "country":  country,
                "currency": currency,
                "impact":   impact,
                "time":     ev_time,
                "pairs":    _CURRENCY_PAIRS.get(currency, []),
            })
        events.sort(key=lambda e: e["time"])
        logger.info("NewsConnector: fetched %d events from Finnhub", len(events))
        return events
    except Exception as exc:
        logger.warning("NewsConnector: fetch failed — %s", exc)
        return []


def _refresh_if_needed() -> None:
    global _cached_events, _cache_expiry
    now = datetime.now(timezone.utc)
    with _cache_lock:
        if _cache_expiry is None or now >= _cache_expiry:
            _cached_events = _fetch_events()
            _cache_expiry  = now + timedelta(minutes=CACHE_MINUTES)


def is_news_blackout(pair: str) -> tuple[bool, str]:
    """
    Return (True, event_name) if a high-impact event affecting `pair` is within
    the blackout window; else (False, "").

    pair: OANDA format, e.g. "EUR_USD"
    """
    if not _api_key():
        return False, ""

    _refresh_if_needed()
    now = datetime.now(timezone.utc)

    with _cache_lock:
        events = list(_cached_events)

    for ev in events:
        if _HIGH_IMPACT_ONLY and ev["impact"] != "high":
            continue
        if pair not in ev["pairs"]:
            continue
        delta = (ev["time"] - now).total_seconds() / 60
        if -BLACKOUT_AFTER_MINUTES <= delta <= BLACKOUT_BEFORE_MINUTES:
            return True, ev["event"]

    return False, ""


def get_upcoming_events(hours: int = 12) -> list[dict]:
    """
    Return all events (any impact) occurring within the next `hours` hours,
    sorted by time. Includes only events that affect traded pairs.
    """
    if not _api_key():
        return []

    _refresh_if_needed()
    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours)

    traded_pairs = set(
        config.FOREX_PAIRS
        + getattr(config, "FOREX_WATCH", [])
    )

    with _cache_lock:
        return [
            ev for ev in _cached_events
            if now <= ev["time"] <= cutoff
            and any(p in traded_pairs for p in ev["pairs"])
        ]
