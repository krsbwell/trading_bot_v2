"""
ForexFactory economic calendar connector.

Uses FF's public XML feeds — no login required:
  https://nfs.faireconomy.media/ff_calendar_thisweek.xml
  https://nfs.faireconomy.media/ff_calendar_nextweek.xml

Events are returned in the same dict format as news_connector.py so the
dashboard can use either source transparently:
  {event, country, currency, impact, time (UTC datetime), pairs}

Times in the FF feed are Eastern Time (ET). We convert to UTC by assuming
ET = UTC-5 (EST) or UTC-4 (EDT). Python's dateutil handles this if available;
otherwise we apply a fixed -4h offset (valid during US DST, Apr–Nov).
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional
import xml.etree.ElementTree as ET
import urllib.request

import config

logger = logging.getLogger(__name__)

# ── Currency → pair mapping (mirrors news_connector) ─────────────────────────
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

_IMPACT_MAP = {"High": "high", "Medium": "medium", "Low": "low", "": "low"}

_FF_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.xml",
]

CACHE_MINUTES = 60
_cache_lock  = threading.Lock()
_cached: list[dict] = []
_cache_expiry: Optional[datetime] = None


def _et_to_utc(date_str: str, time_str: str) -> Optional[datetime]:
    """
    Parse ForexFactory date/time strings to UTC.
    date_str: "Jun 18, 2025"
    time_str: "8:30am" | "All Day" | "Tentative" | ""
    """
    dt_naive = None
    for fmt in ("%b %d, %Y", "%m-%d-%Y"):
        try:
            dt_naive = datetime.strptime(date_str, fmt)
            break
        except ValueError:
            continue
    if dt_naive is None:
        return None

    if time_str and time_str.lower() not in ("all day", "tentative", ""):
        try:
            t = datetime.strptime(time_str.strip().lower(), "%I:%M%p")
            dt_naive = dt_naive.replace(hour=t.hour, minute=t.minute)
        except ValueError:
            pass

    # Determine EDT vs EST: EDT (UTC-4) Mar 2nd Sun – Nov 1st Sun
    year = dt_naive.year
    try:
        import time as _time
        # Use mktime trick: assume ET and see if DST is in effect
        import calendar as _cal
        # Approximate: EDT Apr 1 – Oct 31
        month = dt_naive.month
        is_edt = 3 < month < 11 or (month == 3 and dt_naive.day >= 8) or (month == 11 and dt_naive.day < 1)
        offset = 4 if is_edt else 5
    except Exception:
        offset = 4  # default EDT

    return (dt_naive + timedelta(hours=offset)).replace(tzinfo=timezone.utc)


def _fetch() -> list[dict]:
    events = []
    for url in _FF_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                root = ET.fromstring(resp.read())
            for ev in root.findall("event"):
                country  = (ev.findtext("country") or "").upper()
                currency = country  # FF uses currency codes directly (USD, EUR, etc.)
                if currency not in _CURRENCY_PAIRS:
                    continue
                impact   = _IMPACT_MAP.get(ev.findtext("impact") or "", "low")
                ev_time  = _et_to_utc(ev.findtext("date") or "", ev.findtext("time") or "")
                if ev_time is None:
                    continue
                events.append({
                    "event":     ev.findtext("title") or "",
                    "country":   country,
                    "currency":  currency,
                    "impact":    impact,
                    "time":      ev_time,
                    "pairs":     _CURRENCY_PAIRS.get(currency, []),
                    "forecast":  ev.findtext("forecast") or "",
                    "consensus": ev.findtext("forecast") or "",
                    "previous":  ev.findtext("previous") or "",
                    "actual":    ev.findtext("actual") or "",
                    "source":    "ForexFactory",
                })
        except Exception as exc:
            logger.warning("ForexFactory fetch failed (%s): %s", url, exc)

    events.sort(key=lambda e: e["time"])
    logger.info("ForexFactory: fetched %d events", len(events))
    return events


def _refresh_if_needed() -> None:
    global _cached, _cache_expiry
    now = datetime.now(timezone.utc)
    with _cache_lock:
        if _cache_expiry is None or now >= _cache_expiry:
            _cached = _fetch()
            _cache_expiry = now + timedelta(minutes=CACHE_MINUTES)


def get_upcoming_events(hours: int = 24) -> list[dict]:
    """
    Return events from the past 2 hours through the next `hours` hours
    that affect a traded pair. Including recent past events lets the dashboard
    show "X min ago" context for events that just fired.
    """
    _refresh_if_needed()
    now    = datetime.now(timezone.utc)
    past   = now - timedelta(hours=2)
    future = now + timedelta(hours=hours)
    traded = set(config.FOREX_PAIRS + getattr(config, "FOREX_WATCH", []))
    with _cache_lock:
        return [
            ev for ev in _cached
            if past <= ev["time"] <= future
            and any(p in traded for p in ev["pairs"])
        ]


def is_news_blackout(pair: str, before_min: int = 30, after_min: int = 15) -> tuple[bool, str]:
    """
    Return (True, event_name) if a high-impact FF event is within the blackout window.
    Mirrors news_connector.is_news_blackout() interface.
    """
    _refresh_if_needed()
    now = datetime.now(timezone.utc)
    with _cache_lock:
        events = list(_cached)
    for ev in events:
        if ev["impact"] != "high":
            continue
        if pair not in ev["pairs"]:
            continue
        delta = (ev["time"] - now).total_seconds() / 60
        if -after_min <= delta <= before_min:
            return True, ev["event"]
    return False, ""
