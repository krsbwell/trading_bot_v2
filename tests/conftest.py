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


@pytest.fixture(autouse=True)
def _isolate_signal_lifecycle(tmp_path, monkeypatch):
    """
    Phase 2B (2026-10-07) — signal_engine.run() and main.py's _process_pair()
    both call into engine.signal_lifecycle on every real (non no_audit) call,
    same risk class as the signal_log.csv incident above: any test that
    exercises either path would otherwise append to the live bot's
    data/signal_lifecycle.csv and advance data/signal_id_seq.json's counter.
    _initialised is reset too — it's a module-level latch (see _ensure_file())
    that would otherwise skip writing the header into the new tmp path.
    """
    import engine.signal_lifecycle as _sl
    monkeypatch.setattr(_sl, "_LOG_PATH", tmp_path / "test_signal_lifecycle.csv")
    monkeypatch.setattr(_sl, "_SEQ_PATH", tmp_path / "test_signal_id_seq.json")
    monkeypatch.setattr(_sl, "_initialised", False)
