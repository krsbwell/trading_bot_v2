"""
monitoring/ package — read-only health/drift reporting, tasks/todo.md
2026-08-21 Phase 5. Nothing here alerts or changes strategy behavior;
that stays with the existing scripts/watchdog.py and the source design
doc's own "don't auto-change strategy on drift" rule.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import config
from database.models import init_schema
from memory import agent_memory, market_memory
from monitoring import agent_monitor, drift_detector, model_monitor, system_health


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    p = tmp_path / "adaptive_test.db"
    init_schema(db_path=p)
    import database.models as models
    monkeypatch.setattr(models, "DEFAULT_DB_PATH", p)
    return p


# ── drift_detector ───────────────────────────────────────────────────────

def test_drift_detector_disabled_by_default(db_path):
    assert drift_detector.check_drift("EUR_USD", "M30", 0.5) == {"status": "DISABLED"}


def test_drift_detector_insufficient_baseline(db_path, monkeypatch):
    monkeypatch.setitem(config.AGENTS_CONFIG, "drift_detector_enabled", True)
    result = drift_detector.check_drift("EUR_USD", "M30", 0.5)
    assert result["status"] == "INSUFFICIENT_BASELINE_DATA"


def test_drift_detector_ok_within_baseline(db_path, monkeypatch):
    monkeypatch.setitem(config.AGENTS_CONFIG, "drift_detector_enabled", True)
    for i in range(market_memory.MIN_SNAPSHOTS_FOR_BASELINE):
        conf = 0.5 + (0.01 if i % 2 == 0 else -0.01)   # small variance so stdev isn't zero
        info = {"regime": "RANGING", "confidence": conf, "adx": 15.0, "atr_pct": 0.1, "ema_slope_pct": 0.0}
        market_memory.record("EUR_USD", "M30", info)
    result = drift_detector.check_drift("EUR_USD", "M30", 0.5)   # at the baseline mean
    assert result["status"] == "OK"


def test_drift_detector_flags_large_deviation(db_path, monkeypatch):
    monkeypatch.setitem(config.AGENTS_CONFIG, "drift_detector_enabled", True)
    for i in range(market_memory.MIN_SNAPSHOTS_FOR_BASELINE):
        # small variance around 0.5 so stdev isn't zero
        conf = 0.5 + (0.01 if i % 2 == 0 else -0.01)
        info = {"regime": "RANGING", "confidence": conf, "adx": 15.0, "atr_pct": 0.1, "ema_slope_pct": 0.0}
        market_memory.record("EUR_USD", "M30", info)
    result = drift_detector.check_drift("EUR_USD", "M30", 0.99)   # wildly outside baseline
    assert result["status"] == "DRIFT_DETECTED"


# ── agent_monitor ────────────────────────────────────────────────────────

def test_agent_monitor_no_history(db_path):
    status = agent_monitor.status_for_pair("EUR_USD")
    assert status["last_run"] is None
    assert status["consecutive_no_trade_streak"] == 0
    assert status["long_streak_note"] is None


def test_agent_monitor_notes_long_streak(db_path):
    for _ in range(60):
        agent_memory.record_run("EUR_USD", regime="RANGING", action_taken="NO_TRADE")
    status = agent_monitor.status_for_pair("EUR_USD")
    assert status["consecutive_no_trade_streak"] == 60
    assert status["long_streak_note"] is not None


# ── model_monitor ────────────────────────────────────────────────────────

def test_model_monitor_no_production_model(tmp_path):
    from learning.model_registry import ModelRegistry
    registry = ModelRegistry(registry_path=tmp_path / "reg.json", models_dir=tmp_path / "models")
    result = model_monitor.status(registry=registry)
    assert result["production_version"] is None
    assert result["candidates"] == []


def test_model_monitor_reports_production_and_candidates(tmp_path):
    from learning.model_registry import ModelRegistry
    registry = ModelRegistry(registry_path=tmp_path / "reg.json", models_dir=tmp_path / "models")
    registry.register("v1", stage="production")
    registry.register("v2", stage="candidate")
    result = model_monitor.status(registry=registry)
    assert result["production_version"] == "v1"
    assert result["candidates"] == ["v2"]


# ── system_health ────────────────────────────────────────────────────────

def test_system_health_missing_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(system_health, "_HEARTBEAT_PATH", tmp_path / "no_heartbeat.txt")
    result = system_health.status()
    assert result == {"heartbeat_exists": False, "stale_minutes": None,
                       "is_stale": None, "watchdog_alerted": False}


def test_system_health_fresh_heartbeat_not_stale(tmp_path, monkeypatch):
    hb_path = tmp_path / "heartbeat.txt"
    hb_path.write_text(datetime.now(timezone.utc).isoformat())
    monkeypatch.setattr(system_health, "_HEARTBEAT_PATH", hb_path)
    monkeypatch.setattr(system_health, "_WATCHDOG_STATE_PATH", tmp_path / "no_state.json")
    result = system_health.status()
    assert result["heartbeat_exists"] is True
    assert result["is_stale"] is False


def test_system_health_stale_heartbeat_flagged(tmp_path, monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(minutes=config.WATCHDOG_STALE_MINUTES + 30)
    hb_path = tmp_path / "heartbeat.txt"
    hb_path.write_text(old.isoformat())
    monkeypatch.setattr(system_health, "_HEARTBEAT_PATH", hb_path)
    monkeypatch.setattr(system_health, "_WATCHDOG_STATE_PATH", tmp_path / "no_state.json")
    result = system_health.status()
    assert result["is_stale"] is True


def test_system_health_reports_watchdog_alerted_flag(tmp_path, monkeypatch):
    hb_path = tmp_path / "heartbeat.txt"
    hb_path.write_text(datetime.now(timezone.utc).isoformat())
    state_path = tmp_path / "watchdog_state.json"
    state_path.write_text(json.dumps({"alerted": True}))
    monkeypatch.setattr(system_health, "_HEARTBEAT_PATH", hb_path)
    monkeypatch.setattr(system_health, "_WATCHDOG_STATE_PATH", state_path)
    result = system_health.status()
    assert result["watchdog_alerted"] is True
