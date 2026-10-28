"""
Phase 5D-5 -- weekly-cycle freshness for WFOOptimizer.should_refit().

A pair is eligible again iff its `fitted_at` is earlier than the start of the
current scheduled cycle (most recent Sunday 02:00 America/New_York, the same
instant the APScheduler cron fires). Elapsed-time rules fail because fitted_at
is written AFTER the trigger. Deterministic: injected clock + explicit zone, no
sleeping, tmp paths only.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import config
from engine.wfo_optimizer import WFOOptimizer, current_cycle_start

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def _ny(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=NY)


def _wfo(tmp_path):
    return WFOOptimizer(save_path=tmp_path / "test_wfo_params.json")


def _set_fit(wfo, pair, when):
    wfo._state[pair] = {"params": {"min_score": 55},
                        "fitted_at": when.astimezone(UTC).isoformat()}


# 2026-10-25 is a Sunday (EDT); 2026-11-01 Sunday (EST, fall-back day);
# 2027-03-14 Sunday (spring-forward day).
TRIGGER_1025 = _ny(2026, 10, 25, 2, 0, 0)
TRIGGER_1101 = _ny(2026, 11, 1, 2, 0, 0)


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "WFO_SCHEDULE_TZ", "America/New_York")
    monkeypatch.setattr(config, "WFO_CYCLE_DAY_OF_WEEK", 6)
    monkeypatch.setattr(config, "WFO_CYCLE_HOUR", 2)
    monkeypatch.setattr(config, "WFO_CYCLE_MINUTE", 0)
    monkeypatch.setattr(config, "WFO_REFIT_CYCLES", 1)


# ── cycle_start itself ──────────────────────────────────────────────────────

@pytest.mark.parametrize("now,expected", [
    (_ny(2026, 10, 25, 2, 0, 0),  _ny(2026, 10, 25, 2, 0)),   # exactly at trigger
    (_ny(2026, 10, 25, 1, 59, 59), _ny(2026, 10, 18, 2, 0)),  # a second before -> previous cycle
    (_ny(2026, 10, 25, 15, 25),   _ny(2026, 10, 25, 2, 0)),
    (_ny(2026, 10, 28, 9, 0),     _ny(2026, 10, 25, 2, 0)),   # mid-week
    (_ny(2026, 10, 31, 23, 59),   _ny(2026, 10, 25, 2, 0)),   # Saturday night
])
def test_cycle_start_table(now, expected):
    assert current_cycle_start(now) == expected


def test_cycle_start_accepts_utc_input_and_returns_same_instant():
    now_utc = _ny(2026, 10, 25, 15, 25).astimezone(UTC)
    assert current_cycle_start(now_utc) == _ny(2026, 10, 25, 2, 0)


def test_cycle_start_fall_back_week_is_169_hours():
    gap = (current_cycle_start(_ny(2026, 11, 1, 3, 0)).astimezone(UTC)
           - current_cycle_start(_ny(2026, 10, 30, 3, 0)).astimezone(UTC))
    assert gap == timedelta(hours=169)


def test_cycle_start_spring_forward_weeks():
    """02:00 on 2027-03-14 does not exist; the cron fires at 07:00 UTC (= 03:00 EDT), so the
    Mar 7 -> Mar 14 gap is 168h and the FOLLOWING gap (Mar 14 -> Mar 21, 06:00 UTC) is 167h."""
    mar7 = current_cycle_start(_ny(2027, 3, 8, 12, 0)).astimezone(UTC)
    mar14 = current_cycle_start(_ny(2027, 3, 14, 12, 0)).astimezone(UTC)
    mar21 = current_cycle_start(_ny(2027, 3, 21, 12, 0)).astimezone(UTC)
    assert mar14 == datetime(2027, 3, 14, 7, 0, tzinfo=UTC)
    assert mar14 - mar7 == timedelta(hours=168)
    assert mar21 - mar14 == timedelta(hours=167)


def test_cycle_start_equals_apscheduler_fire_instants():
    """The freshness boundary must be the instant the real cron fires, incl. DST."""
    from apscheduler.triggers.cron import CronTrigger
    trig = CronTrigger(day_of_week="sun", hour=2, minute=0, timezone=NY)
    probe = datetime(2026, 10, 1, tzinfo=UTC)
    for _ in range(40):                       # ~9 months incl. both DST changes
        fire = trig.get_next_fire_time(None, probe).astimezone(UTC)
        assert current_cycle_start(fire).astimezone(UTC) == fire
        assert current_cycle_start(fire - timedelta(seconds=1)).astimezone(UTC) < fire
        probe = fire + timedelta(seconds=1)


# ── the bug: late-saved pairs must be eligible at the next trigger ───────────

def test_saved_one_second_after_trigger_is_eligible_next_sunday(tmp_path):
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", TRIGGER_1025 + timedelta(seconds=1))
    now = TRIGGER_1101                                   # next Sunday 02:00 EST
    # legacy rule: (now - last).days == 7 days + 1h - 1s -> 7 here because of DST; use a
    # no-DST pair of Sundays below to show the legacy failure; this pins the new rule.
    assert w.should_refit("EUR_USD", now=now) is True


def test_legacy_days_rule_would_have_skipped_the_no_dst_case():
    last = _ny(2026, 10, 11, 2, 0, 1)            # Sunday, saved 1s after trigger
    nxt = _ny(2026, 10, 18, 2, 0, 0)             # next Sunday trigger (both EDT)
    assert (nxt - last).days == 6                # what the old `.days >= 7` saw


def test_no_dst_week_saved_just_after_trigger_is_eligible(tmp_path):
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", _ny(2026, 10, 11, 2, 0, 1))
    assert w.should_refit("EUR_USD", now=_ny(2026, 10, 18, 2, 0, 0)) is True


def test_saved_13h_into_batch_is_eligible(tmp_path):
    """Real 10/25 batch ended 15:25; a 6.5-day tolerance would skip this pair."""
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", _ny(2026, 10, 25, 15, 25))
    assert w.should_refit("EUR_USD", now=TRIGGER_1101) is True


def test_saved_36h_into_batch_is_eligible(tmp_path):
    """3000-bar batches (28-38 h) must not break the rule."""
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", TRIGGER_1025 + timedelta(hours=36))
    assert w.should_refit("EUR_USD", now=TRIGGER_1101) is True


# ── same cycle -> fresh (no repeated refit) ─────────────────────────────────

@pytest.mark.parametrize("now", [
    _ny(2026, 10, 25, 2, 1),
    _ny(2026, 10, 25, 15, 26),
    _ny(2026, 10, 28, 9, 0),
    _ny(2026, 10, 31, 23, 59, 59),
    _ny(2026, 11, 1, 1, 59, 59),            # one second before the next trigger
])
def test_fresh_throughout_the_same_cycle(tmp_path, now):
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", _ny(2026, 10, 25, 10, 0))
    assert w.should_refit("EUR_USD", now=now) is False


def test_eligible_exactly_at_next_trigger(tmp_path):
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", _ny(2026, 10, 25, 10, 0))
    assert w.should_refit("EUR_USD", now=TRIGGER_1101) is True


def test_fitted_exactly_at_cycle_start_is_fresh(tmp_path):
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", TRIGGER_1025)
    assert w.should_refit("EUR_USD", now=TRIGGER_1025 + timedelta(hours=1)) is False


def test_one_second_before_cycle_start_is_eligible(tmp_path):
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", TRIGGER_1025 - timedelta(seconds=1))
    assert w.should_refit("EUR_USD", now=TRIGGER_1025 + timedelta(hours=1)) is True


def test_sunday_before_0200_belongs_to_previous_cycle(tmp_path):
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", _ny(2026, 10, 18, 10, 0))      # previous cycle
    assert w.should_refit("EUR_USD", now=_ny(2026, 10, 25, 1, 0)) is False
    assert w.should_refit("EUR_USD", now=_ny(2026, 10, 25, 2, 0)) is True


def test_midweek_manual_fit_is_fresh_then_eligible_next_trigger(tmp_path):
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", _ny(2026, 10, 28, 9, 0))        # Wednesday manual run
    assert w.should_refit("EUR_USD", now=_ny(2026, 10, 31, 12, 0)) is False
    assert w.should_refit("EUR_USD", now=TRIGGER_1101) is True


# ── DST boundaries ──────────────────────────────────────────────────────────

def test_november_fall_back_week(tmp_path):
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", _ny(2026, 10, 25, 2, 0, 1))
    # the gap really is 169h, so even an elapsed-168h rule would pass here ...
    assert TRIGGER_1101.astimezone(UTC) - _ny(2026, 10, 25, 2, 0, 1).astimezone(UTC) > timedelta(hours=168)
    assert w.should_refit("EUR_USD", now=TRIGGER_1101) is True
    assert w.should_refit("EUR_USD", now=TRIGGER_1101.astimezone(UTC) - timedelta(seconds=1)) is False
    # the repeated 01:00-02:00 hour on fall-back day is still the OLD cycle
    assert w.should_refit("EUR_USD", now=datetime(2026, 11, 1, 6, 30, tzinfo=UTC)) is False


def test_march_spring_forward_week(tmp_path):
    w = _wfo(tmp_path)
    prev = _ny(2027, 3, 7, 2, 0, 1)
    _set_fit(w, "EUR_USD", prev)
    fire = datetime(2027, 3, 14, 7, 0, tzinfo=UTC)         # APScheduler fire instant (02:00 -05:00)
    assert fire - prev.astimezone(UTC) < timedelta(hours=168)        # elapsed-168h would FAIL
    assert w.should_refit("EUR_USD", now=fire) is True
    assert w.should_refit("EUR_USD", now=fire - timedelta(seconds=1)) is False


def test_a_batch_saved_in_the_spring_week_is_eligible_the_following_167h_week(tmp_path):
    w = _wfo(tmp_path)
    saved = datetime(2027, 3, 14, 7, 0, 1, tzinfo=UTC)
    nxt = datetime(2027, 3, 21, 6, 0, tzinfo=UTC)              # 02:00 EDT, 167h later
    assert nxt - saved < timedelta(hours=168)                  # an elapsed-168h rule would skip it
    assert w.should_refit("EUR_USD", now=nxt) is True


# ── invalid / hostile fitted_at (fail toward eligible, as before) ──────────

@pytest.mark.parametrize("bad", ["", None, "garbage", "2026-13-45T99:00:00",
                                 "2026-10-25T10:00:00"])            # last one is naive
def test_missing_or_invalid_fitted_at_is_eligible(tmp_path, bad):
    w = _wfo(tmp_path)
    w._state["EUR_USD"] = {"params": {}, "fitted_at": bad}
    assert w.should_refit("EUR_USD", now=_ny(2026, 10, 26, 12, 0)) is True


def test_unknown_pair_is_eligible(tmp_path):
    assert _wfo(tmp_path).should_refit("NOPE_PAIR", now=TRIGGER_1025) is True


def test_far_future_fitted_at_is_eligible_and_warned(tmp_path, caplog):
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", TRIGGER_1025 + timedelta(days=30))
    with caplog.at_level("WARNING"):
        assert w.should_refit("EUR_USD", now=TRIGGER_1025 + timedelta(hours=1)) is True
    assert any("future" in r.getMessage().lower() for r in caplog.records)


def test_slightly_future_fitted_at_within_a_day_is_not_treated_as_skew(tmp_path):
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", TRIGGER_1025 + timedelta(hours=3))
    assert w.should_refit("EUR_USD", now=TRIGGER_1025 + timedelta(hours=1)) is False


def test_bad_timezone_config_fails_toward_eligible_with_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(config, "WFO_SCHEDULE_TZ", "Not/AZone")
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", TRIGGER_1025 + timedelta(hours=1))
    with caplog.at_level("WARNING"):
        assert w.should_refit("EUR_USD", now=TRIGGER_1025 + timedelta(hours=2)) is True
    assert any("freshness" in r.getMessage().lower() for r in caplog.records)


# ── WFO_REFIT_CYCLES semantics ──────────────────────────────────────────────

def test_two_cycles_skips_one_sunday_then_refits(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WFO_REFIT_CYCLES", 2)
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", _ny(2026, 10, 25, 10, 0))
    assert w.should_refit("EUR_USD", now=TRIGGER_1101) is False
    assert w.should_refit("EUR_USD", now=_ny(2026, 11, 8, 2, 0)) is True


@pytest.mark.parametrize("raw,expected", [
    (1, 1), (2, 2), (4, 4), (2.0, 2),            # accepted
    (0, 1), (-1, 1), (-7, 1),                    # zero / negative -> default
    (1.5, 1), (2.5, 1), ("2", 1), (None, 1),     # non-integers -> default
    (True, 1), (False, 1),                       # bool is not a count
    (float("nan"), 1), (float("inf"), 1),
])
def test_refit_cycles_validation(raw, expected, monkeypatch):
    from engine.wfo_optimizer import _refit_cycles
    monkeypatch.setattr(config, "WFO_REFIT_CYCLES", raw)
    assert _refit_cycles() == expected


def test_missing_refit_cycles_setting_defaults_to_one(monkeypatch):
    from engine.wfo_optimizer import _refit_cycles
    monkeypatch.delattr(config, "WFO_REFIT_CYCLES")
    assert _refit_cycles() == 1


def test_invalid_refit_cycles_logs_a_warning(monkeypatch, caplog):
    from engine.wfo_optimizer import _refit_cycles
    monkeypatch.setattr(config, "WFO_REFIT_CYCLES", 0)
    with caplog.at_level("WARNING"):
        _refit_cycles()
    assert any("WFO_REFIT_CYCLES" in r.getMessage() for r in caplog.records)


def test_legacy_refit_days_no_longer_drives_freshness(tmp_path, monkeypatch):
    """WFO_REFIT_DAYS stays in config for compatibility but is not consulted."""
    monkeypatch.setattr(config, "WFO_REFIT_DAYS", 30, raising=False)
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", _ny(2026, 10, 25, 10, 0))
    assert w.should_refit("EUR_USD", now=TRIGGER_1101) is True


# ── no persistence side effects / format unchanged ─────────────────────────

def test_should_refit_writes_nothing(tmp_path):
    w = _wfo(tmp_path)
    _set_fit(w, "EUR_USD", TRIGGER_1025)
    before = json.dumps(w._state, sort_keys=True)
    w.should_refit("EUR_USD", now=TRIGGER_1101)
    assert json.dumps(w._state, sort_keys=True) == before
    assert not (tmp_path / "test_wfo_params.json").exists()


def test_default_clock_is_real_utc_now(tmp_path):
    w = _wfo(tmp_path)
    w._state["EUR_USD"] = {"params": {}, "fitted_at": datetime.now(UTC).isoformat()}
    assert w.should_refit("EUR_USD") is False          # saved just now -> fresh


# ── run_all_pairs honours it: second batch in the same cycle skips saved pairs ──

def test_run_all_pairs_skips_pair_fitted_this_cycle(tmp_path, caplog):
    w = _wfo(tmp_path)
    w._state["EUR_CAD"] = {"params": {}, "fitted_at": datetime.now(UTC).isoformat()}
    calls = []
    with caplog.at_level("INFO"):
        w.run_all_pairs(lambda p, g, c: calls.append(p), ["EUR_CAD"])
    assert calls == []
    assert any("still fresh" in r.getMessage() for r in caplog.records)


# ── configuration drift guards ──────────────────────────────────────────────

def test_config_schedule_matches_main_py_cron():
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text("utf-8")
    m = re.search(r'CronTrigger\(day_of_week="(\w+)",\s*hour=(\d+),\s*minute=(\d+)\)', src)
    assert m, "WFO CronTrigger not found in main.py"
    dow = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}[m.group(1)]
    assert (dow, int(m.group(2)), int(m.group(3))) == (
        config.WFO_CYCLE_DAY_OF_WEEK, config.WFO_CYCLE_HOUR, config.WFO_CYCLE_MINUTE)


def test_config_defaults():
    import importlib
    cfg = importlib.import_module("config")
    # monkeypatch fixture is function-scoped; read the module defaults from source
    text = Path(cfg.__file__).read_text("utf-8")
    assert re.search(r'^WFO_SCHEDULE_TZ\s*=\s*"America/New_York"', text, re.M)
    assert re.search(r'^WFO_REFIT_CYCLES\s*=\s*1\b', text, re.M)


def test_schedule_tz_mismatch_warning(tmp_path, monkeypatch, caplog):
    import tzlocal
    monkeypatch.setattr(tzlocal, "get_localzone_name", lambda: "Europe/London")
    w = _wfo(tmp_path)
    with caplog.at_level("WARNING"):
        w.run_all_pairs(lambda p, g, c: None, [])
    assert any("WFO_SCHEDULE_TZ" in r.getMessage() for r in caplog.records)


def test_schedule_tz_match_is_silent(tmp_path, monkeypatch, caplog):
    import tzlocal
    monkeypatch.setattr(tzlocal, "get_localzone_name", lambda: "America/New_York")
    w = _wfo(tmp_path)
    with caplog.at_level("WARNING"):
        w.run_all_pairs(lambda p, g, c: None, [])
    assert not any("WFO_SCHEDULE_TZ" in r.getMessage() for r in caplog.records)
