"""
Pytest configuration — test isolation fixtures applied automatically to all tests.
"""
import pytest
import trade.paper_trader as _pt_mod


@pytest.fixture(autouse=True)
def _isolate_paper_trader(tmp_path, monkeypatch):
    """
    Redirect PaperTrader's default save path to a per-test temp file so tests
    never inherit state from data/paper_state.json (the live bot's state file).
    """
    monkeypatch.setattr(_pt_mod, "_DEFAULT_SAVE_PATH", tmp_path / "test_paper_state.json")
