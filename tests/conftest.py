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


@pytest.fixture(autouse=True)
def _isolate_signal_log(tmp_path, monkeypatch):
    """
    Trade closes now really write to the ML log (2026-09-27 id-mismatch
    fix), so no test may ever append to data/signal_log.csv — a test
    polluting that file already caused a stale-signal bug once (2026-07-24).
    """
    import learning.data_collector as _dc
    monkeypatch.setattr(_dc, "SIGNAL_LOG", str(tmp_path / "test_signal_log.csv"))
