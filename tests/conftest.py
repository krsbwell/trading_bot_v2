"""
Pytest configuration — test isolation fixtures applied automatically to all tests.
"""
import pytest
import trade.paper_trader as _pt_mod
import trade.trade_manager as _tm_mod


@pytest.fixture(autouse=True)
def _isolate_paper_trader(tmp_path, monkeypatch):
    """
    Redirect PaperTrader's default save path to a per-test temp file so tests
    never inherit state from data/paper_state.json (the live bot's state file).
    """
    monkeypatch.setattr(_pt_mod, "_DEFAULT_SAVE_PATH", tmp_path / "test_paper_state.json")


@pytest.fixture(autouse=True)
def _isolate_trade_manager(tmp_path, monkeypatch):
    """
    Same isolation for TradeManager's default save directory (added 2026-07-20
    with disk persistence) — never inherit from data/live_state_*.json.
    """
    monkeypatch.setattr(_tm_mod, "_DEFAULT_SAVE_DIR", tmp_path)
