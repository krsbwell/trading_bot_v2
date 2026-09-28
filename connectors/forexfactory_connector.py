"""
ForexFactory economic calendar connector — the sole news-blackout source
as of 2026-08-14 (the Finnhub supplement, connectors/news_connector.py,
was removed: its configured key's plan didn't include the economic-
calendar endpoint, confirmed via a direct 403 test, so it never
contributed a real check — see tasks/todo.md).

Uses FF's public XML feeds — no login required:
  https://nfs.faireconomy.media/ff_calendar_thisweek.xml   (confirmed live)
  https://nfs.faireconomy.media/ff_calendar_nextweek.xml   (404s as of
    2026-08-14 — confirmed via a direct test, not just log noise; left in
    place since it's a harmless once-an-hour failed request and may be a
    temporary rename on ForexFactory's end, not necessarily permanent.
    thisweek.xml alone already covers the real blackout window this feeds
    — a 30-min-before/15-min-after check never needs more than ~7 days
    of lookahead anyway.)

Events are returned as:
  {event, country, currency, impact, time (UTC datetime), pairs}

Times in the FF feed are Eastern Time (ET). We convert to UTC by assuming
ET = UTC-5 (EST) or UTC-4 (EDT). Python's dateutil handles this if available;
otherwise we apply a fixed -4h offset (valid during US DST, Apr–Nov).
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional
import xml.etree.ElementTree as ET
import urllib.request

import config

logger = logging.getLogger(__name__)

# ── Which pairs an event affects ─────────────────────────────────────────────
# Derived from the pair name: an event in currency X affects every pair with
# X on either side. Replaced a hand-maintained currency->pairs table on
# 2026-09-28 that had gone stale: AUD_JPY, NZD_USD, CHF_JPY, EUR_JPY and
# GBP_CAD (for GBP/CAD news) were missing, so those pairs traded straight
# through BoJ / RBA / RBNZ / BoC / SNB releases with no blackout at all.
_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"}


def _affects(currency: str, pair: str) -> bool:
    return currency in pair.upper().split("_")


def _pairs_for(currency: str) -> list[str]:
    tracked = config.FOREX_PAIRS + getattr(config, "FOREX_WATCH", [])
    return [p for p in tracked if _affects(currency, p)]

_IMPACT_MAP = {"High": "high", "Medium": "medium", "Low": "low", "": "low"}

# nextweek.xml removed 2026-09-28 — 404 every hour since at least 08-14, and
# the extra request adds to FF's rate limit (a 429 on thisweek.xml was seen
# 2026-09-27). thisweek.xml covers the 30-min-before/15-min-after window.
_FF_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
]

CACHE_MINUTES       = 60
RETRY_AFTER_FAIL_MIN = 5    # a failed fetch keeps the last good calendar and retries soon
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


def _fetch() -> list[dict] | None:
    """Events from the feed, or None if no feed could be read (so the caller
    can tell "no events this week" apart from "fetch failed")."""
    events = []
    ok = False
    for url in _FF_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                root = ET.fromstring(resp.read())
            ok = True
            for ev in root.findall("event"):
                country  = (ev.findtext("country") or "").upper()
                currency = country  # FF uses currency codes directly (USD, EUR, etc.)
                if currency not in _CURRENCIES:
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
                    "pairs":     _pairs_for(currency),   # display only; blackout uses _affects()
                    "forecast":  ev.findtext("forecast") or "",
                    "consensus": ev.findtext("forecast") or "",
                    "previous":  ev.findtext("previous") or "",
                    "actual":    ev.findtext("actual") or "",
                    "source":    "ForexFactory",
                })
        except Exception as exc:
            logger.warning("ForexFactory fetch failed (%s): %s", url, exc)

    if not ok:
        return None
    events.sort(key=lambda e: e["time"])
    logger.info("ForexFactory: fetched %d events", len(events))
    return events


DISK_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "ff_calendar_cache.json")
DISK_CACHE_MAX_AGE_DAYS = 7   # thisweek.xml covers one week


def _save_disk(events: list[dict]) -> None:
    try:
        rows = [{**e, "time": e["time"].isoformat()} for e in events]
        tmp = DISK_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(), "events": rows}, f)
        os.replace(tmp, DISK_CACHE)
    except Exception as exc:
        logger.warning("ForexFactory disk cache write failed: %s", exc)


def _load_disk() -> list[dict]:
    """Last good calendar from disk, so a restart isn't unprotected while
    FF rate-limits (429) the first fetch. Ignored if older than a week."""
    try:
        with open(DISK_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        if datetime.now(timezone.utc) - datetime.fromisoformat(data["fetched_at"]) > timedelta(days=DISK_CACHE_MAX_AGE_DAYS):
            return []
        return [{**e, "time": datetime.fromisoformat(e["time"])} for e in data["events"]]
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return []


def _refresh_if_needed() -> None:
    """Before 2026-09-28 a failed fetch replaced the cache with [] for 60 min,
    which silently switched the news blackout OFF for that hour."""
    global _cached, _cache_expiry
    now = datetime.now(timezone.utc)
    with _cache_lock:
        if _cache_expiry is None or now >= _cache_expiry:
            fresh = _fetch()
            if fresh is not None:
                _cached = fresh
                _cache_expiry = now + timedelta(minutes=CACHE_MINUTES)
                _save_disk(fresh)
            else:
                if not _cached:
                    _cached = _load_disk()
                _cache_expiry = now + timedelta(minutes=RETRY_AFTER_FAIL_MIN)
                if _cached:
                    logger.warning("ForexFactory unavailable — news blackout using last good "
                                   "calendar (%d events), retrying in %d min",
                                   len(_cached), RETRY_AFTER_FAIL_MIN)
                else:
                    logger.error("ForexFactory unavailable and no calendar cached — NEWS "
                                 "BLACKOUT INACTIVE, retrying in %d min", RETRY_AFTER_FAIL_MIN)


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
            and any(_affects(ev["currency"], p) for p in traded)
        ]


def is_news_blackout(pair: str, before_min: int = 30, after_min: int = 15) -> tuple[bool, str]:
    """
    Return (True, event_name) if a high-impact FF event is within the blackout window.
    """
    _refresh_if_needed()
    now = datetime.now(timezone.utc)
    with _cache_lock:
        events = list(_cached)
    for ev in events:
        if ev["impact"] != "high":
            continue
        if not _affects(ev["currency"], pair):
            continue
        delta = (ev["time"] - now).total_seconds() / 60
        if -after_min <= delta <= before_min:
            return True, ev["event"]
    return False, ""
