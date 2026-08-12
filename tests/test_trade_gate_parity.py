"""
Tests for the 2026-08-12 fix to main.py::_process_pair's duplicate/pre-trade/
cooldown gate — it used to run only `if config.MODE == "paper"`, so in
"demo"/"live" mode (what's actually running) a duplicate-pair rejection
produced no structured audit row and no dashboard trade_blocked_reason
stamp, and TRADE_COOLDOWN_HOURS was never enforced at all. Confirmed live:
NZD_USD 2026-08-06 11:30 UTC scored 66, TRIGGERED, and was silently
rejected (logs/main.log.3: "Trade rejected for NZD_USD: NZD_USD already has
an open trade") with zero trace in signal_audit.csv.

These tests exercise _process_pair directly in "demo" mode with a fake
TradeManager standing in for _trade_manager_fx, proving the same three
checks (duplicate/max-open/cooldown) that already worked in paper mode now
also fire — with audit logging — in demo/live mode.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone

import config
import main
from dashboard import state


class _FakeEngine:
    """Stands in for the SignalEngine passed into _process_pair — returns a
    canned, already-TRIGGERED-worthy signal every time .run() is called."""
    def __init__(self, score=66, direction="long"):
        self.score = score
        self.direction = direction

    def run(self, pair, market, ml_win_prob=None):
        return {
            "pair": pair, "market": market, "direction": self.direction,
            "timeframe": "M30", "score": self.score,
            "ema_score": 25, "structure_score": 20, "pa_score": 21,
            "entry": 0.58928, "stop_loss": 0.58678,
            "tp_levels": {"tp1": 0.59303, "tp2": 0.59553, "tp3": 0.59803},
            "patterns": [], "no_signal": False, "watching": False,
        }


class _FakeTradeManager:
    """Minimal TradeManager stand-in — dict-based open_trades like the real
    one, records whether open_trade() was ever actually called."""
    def __init__(self, existing_trade: dict | None = None):
        self.open_trades = {}
        if existing_trade:
            self.open_trades[existing_trade["id"]] = existing_trade
        self.open_trade_called = False

    def get_open_trade(self, pair):
        for t in self.open_trades.values():
            if t["pair"] == pair:
                return t
        return None

    def open_trade(self, signal):
        self.open_trade_called = True
        return "SHOULD-NOT-BE-CALLED"


def _reset_state():
    state.update(
        signals={}, min_score=config.MIN_CONFLUENCE_SCORE,
        trade_cooldowns={}, account={"balance": 500.0, "cash": 500.0},
    )


def _patch_common(monkeypatch, trade_manager, mode="demo"):
    """Wire main.py's module globals the way main() would, without running
    main() itself (which connects to OANDA, starts a scheduler, etc.)."""
    monkeypatch.setattr(config, "MODE", mode)
    monkeypatch.setattr(main, "_trade_manager_fx", trade_manager)
    monkeypatch.setattr(main, "_paper_trader", None)
    monkeypatch.setattr(main, "_pattern_learner", None)   # keeps ml_prob=None -> ML gate skipped
    monkeypatch.setattr(main, "_oanda_connector", None)

    audit_calls = []
    monkeypatch.setattr(main, "_audit_blocked",
                         lambda **kw: audit_calls.append(kw))
    skip_calls = []
    monkeypatch.setattr(main, "record_skip", lambda signal: skip_calls.append(signal))
    monkeypatch.setattr(main, "play_alert", lambda *_: None)
    monkeypatch.setattr(main, "tg_enabled", lambda: False)
    return audit_calls, skip_calls


def test_duplicate_pair_blocked_in_demo_mode_with_audit_trail(monkeypatch):
    """The exact NZD_USD 2026-08-06 scenario: a TRIGGERED signal on a pair
    that already has an open trade, in demo mode. Must be blocked AND
    audited — neither happened before this fix."""
    _reset_state()
    existing = {"id": "77", "pair": "NZD_USD", "direction": "long", "entry": 0.58833}
    tm = _FakeTradeManager(existing_trade=existing)
    audit_calls, skip_calls = _patch_common(monkeypatch, tm, mode="demo")

    main._process_pair("NZD_USD", "forex", _FakeEngine(score=66))

    assert tm.open_trade_called is False, "must not open a second position on the same pair"
    assert len(audit_calls) == 1
    assert audit_calls[0]["result"] == "BLOCKED"
    assert "already has open trade id=77" in audit_calls[0]["reject_reason"]
    assert len(skip_calls) == 1, "shadow-outcome training data must still be recorded"


def test_max_open_trades_blocked_in_demo_mode_with_audit_trail(monkeypatch):
    _reset_state()
    monkeypatch.setattr(config, "MAX_OPEN_TRADES", 1)
    tm = _FakeTradeManager(existing_trade={"id": "1", "pair": "GBP_CAD", "direction": "short", "entry": 1.88})
    audit_calls, _ = _patch_common(monkeypatch, tm, mode="demo")

    main._process_pair("NZD_USD", "forex", _FakeEngine(score=66))

    assert tm.open_trade_called is False
    assert len(audit_calls) == 1
    assert "Max open trades" in audit_calls[0]["reject_reason"]


def test_cooldown_blocked_in_demo_mode_with_audit_trail(monkeypatch):
    """Before this fix, TRADE_COOLDOWN_HOURS was never enforced at all
    outside paper mode — this is a real new protection, not just a logging
    fix, so it gets its own explicit test."""
    _reset_state()
    tm = _FakeTradeManager()
    audit_calls, _ = _patch_common(monkeypatch, tm, mode="demo")
    state.update(trade_cooldowns={"NZD_USD": datetime.now(timezone.utc) - timedelta(hours=1)})
    monkeypatch.setattr(config, "TRADE_COOLDOWN_HOURS", 4)

    main._process_pair("NZD_USD", "forex", _FakeEngine(score=66))

    assert tm.open_trade_called is False
    assert len(audit_calls) == 1
    assert "Cooldown" in audit_calls[0]["reject_reason"]


def test_clean_signal_still_opens_a_trade_in_demo_mode(monkeypatch):
    """Regression guard: the fix must not block legitimate signals — a pair
    with no existing trade, under the open-trade cap, past cooldown, must
    still open normally."""
    _reset_state()
    tm = _FakeTradeManager()
    audit_calls, _ = _patch_common(monkeypatch, tm, mode="demo")
    monkeypatch.setattr(main, "record_signal", lambda *a, **k: None)
    monkeypatch.setattr(main, "get_quote_to_usd_rate", lambda *a, **k: 1.0)
    monkeypatch.setattr(main, "calculate_position_size", lambda *a, **k: 1000)
    monkeypatch.setattr(main, "_sync_live_state", lambda: None)

    main._process_pair("NZD_USD", "forex", _FakeEngine(score=66))

    assert tm.open_trade_called is True
    assert len(audit_calls) == 0


def test_paper_mode_behavior_unchanged(monkeypatch):
    """Same duplicate scenario, but in paper mode — must behave exactly as
    it did before this fix (this path was never broken)."""
    _reset_state()

    class _FakePaperTrader:
        def __init__(self, existing):
            self.open_trades = [existing]

        def get_open_trade(self, pair):
            for t in self.open_trades:
                if t["pair"] == pair:
                    return t
            return None

    existing = {"id": "77", "pair": "NZD_USD", "direction": "long", "entry": 0.58833}
    pt = _FakePaperTrader(existing)
    monkeypatch.setattr(config, "MODE", "paper")
    monkeypatch.setattr(main, "_paper_trader", pt)
    monkeypatch.setattr(main, "_trade_manager_fx", None)
    monkeypatch.setattr(main, "_pattern_learner", None)
    audit_calls = []
    monkeypatch.setattr(main, "_audit_blocked", lambda **kw: audit_calls.append(kw))
    monkeypatch.setattr(main, "record_skip", lambda signal: None)
    monkeypatch.setattr(main, "play_alert", lambda *_: None)
    monkeypatch.setattr(main, "tg_enabled", lambda: False)

    main._process_pair("NZD_USD", "forex", _FakeEngine(score=66))

    assert len(audit_calls) == 1
    assert "already has open trade id=77" in audit_calls[0]["reject_reason"]
