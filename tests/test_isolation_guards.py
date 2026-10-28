"""
Regression guards for test isolation (2026-10-27, Phase 5D).

Found while running the full suite: tests were touching the LIVE bot's runtime
files --
  * logs/bot_service.log   (test_bot_service.py's supervise() calls, via the
                            RotatingFileHandler bot_service attaches at import)
  * data/adaptive_state.json (test_learning.py's TestFeedbackLoop, via the
                            module-level adaptive_params singleton's force_tier)
These tests fail if either path can write to the real file again. They
fingerprint the real files (size + mtime) around the risky call; nothing here
modifies or deletes any real file.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
import logging.handlers as _lh
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
REAL_BOT_LOG = ROOT / "logs" / "bot_service.log"
REAL_ADAPTIVE = ROOT / "data" / "adaptive_state.json"


def _fingerprint(path: Path):
    if not path.exists():
        return None
    st = path.stat()
    return (st.st_size, st.st_mtime_ns)


def _import_bot_service():
    """Import without letting the module open the real log file."""
    if "bot_service" in sys.modules:
        return sys.modules["bot_service"]
    real = _lh.RotatingFileHandler
    _lh.RotatingFileHandler = lambda *a, **k: logging.NullHandler()
    try:
        import bot_service
    finally:
        _lh.RotatingFileHandler = real
    return bot_service


class _Proc:
    def __init__(self, code):
        self._code = code

    def wait(self):
        return self._code


def test_supervise_does_not_write_the_real_bot_service_log(monkeypatch):
    bs = _import_bot_service()
    monkeypatch.setattr(bs.time, "sleep", lambda *_: None)
    codes = [1, 1, 0]
    before = _fingerprint(REAL_BOT_LOG)
    assert bs.supervise("python.exe", "main.py", popen=lambda args, cwd=None: _Proc(codes.pop(0))) == 0
    assert _fingerprint(REAL_BOT_LOG) == before, "a test appended to the live logs/bot_service.log"


def test_bot_service_logger_has_no_real_file_handler_during_tests():
    bs = _import_bot_service()
    real = str(REAL_BOT_LOG.resolve())
    for h in bs.logger.handlers:
        assert not (isinstance(h, logging.FileHandler)
                    and Path(h.baseFilename).resolve() == Path(real)), \
            "bot_service logger still writes to the real log file"


def test_adaptive_singleton_is_redirected_to_tmp(tmp_path):
    from engine.adaptive_params import adaptive_params as ap
    assert Path(ap._path).resolve() != REAL_ADAPTIVE.resolve()
    assert Path(ap._path).parent == tmp_path


def test_force_tier_never_touches_the_real_adaptive_state():
    """The exact call learning.feedback_loop makes for a low-win-rate pair."""
    from engine.adaptive_params import adaptive_params as ap
    before = _fingerprint(REAL_ADAPTIVE)
    ap.force_tier("EUR_USD", 0.10)
    ap._save()
    assert _fingerprint(REAL_ADAPTIVE) == before, "a test wrote the live data/adaptive_state.json"
    assert Path(ap._path).exists(), "the write should have landed in the temp file instead"


_LEAK_MARKER = "ZZ_ISOLATION_PROBE"


def test_adaptive_state_mutation_part_1_sets_a_marker():
    from engine.adaptive_params import adaptive_params as ap
    ap._state[_LEAK_MARKER] = {"tier": "probe"}
    assert _LEAK_MARKER in ap._state


def test_adaptive_state_mutation_part_2_does_not_see_it():
    """Runs right after part 1 (file order): per-test state copy => no leakage."""
    from engine.adaptive_params import adaptive_params as ap
    assert _LEAK_MARKER not in ap._state


def test_feedback_loop_warning_path_is_isolated(tmp_path):
    """End-to-end through the exact code path that caused the leak: a low-win-rate
    pair makes generate_suggestions() call adaptive_params.force_tier()."""
    from learning import feedback_loop
    from tests.test_learning import _synthetic_log, _write_csv   # same helpers test_learning.py uses
    log = str(tmp_path / "signal_log.csv")
    _write_csv(log, _synthetic_log(n_win=2, n_loss=8, pair="EUR_USD"))   # 20% win rate -> force_tier
    before = _fingerprint(REAL_ADAPTIVE)
    suggestions = feedback_loop.generate_suggestions(min_trades=5, log_path=log)
    assert any(x["type"] == "warning" and "EUR_USD" in x["text"] for x in suggestions),         "scenario must actually reach the force_tier branch or this proves nothing"
    assert _fingerprint(REAL_ADAPTIVE) == before
    from engine.adaptive_params import adaptive_params as ap
    assert Path(ap._path).exists(), "force_tier's save should have landed in the temp file"
