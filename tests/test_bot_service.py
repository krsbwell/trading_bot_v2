"""
Tests for bot_service.py's supervise() — added 2026-08-12 alongside the fix
for the missing bot_service.py file (see bugs_bot_service_missing memory).
Uses a fake Popen so no real processes are spawned.
"""
import logging
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# bot_service attaches a RotatingFileHandler to the REAL logs/bot_service.log at
# import time (module level), and supervise() logs every start/restart -- so
# importing it plainly made these tests append fake entries to the live
# supervisor's log. Swap the handler class for a NullHandler for the duration
# of the import only; tests/conftest.py's _isolate_bot_service_log covers
# the case where another module imported it first.
import logging.handlers as _lh
_real_rfh = _lh.RotatingFileHandler
_lh.RotatingFileHandler = lambda *a, **k: logging.NullHandler()
try:
    import bot_service
finally:
    _lh.RotatingFileHandler = _real_rfh


class _FakeProc:
    def __init__(self, exit_code):
        self._exit_code = exit_code

    def wait(self):
        return self._exit_code


def _fake_popen_factory(exit_codes):
    """Returns a popen-shaped callable that yields FakeProcs with the given
    exit codes in order, then keeps repeating the last code if called more
    often than len(exit_codes)."""
    codes = list(exit_codes)
    calls = []

    def _popen(args, cwd=None):
        calls.append((args, cwd))
        code = codes.pop(0) if codes else _popen.last_code
        _popen.last_code = code
        return _FakeProc(code)

    _popen.last_code = exit_codes[-1] if exit_codes else 0
    _popen.calls = calls
    return _popen


def test_clean_exit_stops_immediately_no_restart(monkeypatch):
    monkeypatch.setattr(bot_service.time, "sleep", lambda *_: None)
    popen = _fake_popen_factory([0])

    code = bot_service.supervise("python.exe", "main.py", max_restarts=5,
                                  restart_delay_secs=0, popen=popen)

    assert code == 0
    assert len(popen.calls) == 1


def test_crash_triggers_restart_until_clean_exit(monkeypatch):
    sleeps = []
    monkeypatch.setattr(bot_service.time, "sleep", lambda s: sleeps.append(s))
    popen = _fake_popen_factory([1, 1, 0])  # crash, crash, then clean

    code = bot_service.supervise("python.exe", "main.py", max_restarts=10,
                                  restart_delay_secs=15, popen=popen)

    assert code == 0
    assert len(popen.calls) == 3
    assert sleeps == [15, 15]  # slept between the two restarts, not after the clean exit


def test_max_restarts_caps_attempts_and_gives_up(monkeypatch):
    monkeypatch.setattr(bot_service.time, "sleep", lambda *_: None)
    popen = _fake_popen_factory([1, 1, 1, 1, 1])  # always crashes

    code = bot_service.supervise("python.exe", "main.py", max_restarts=3,
                                  restart_delay_secs=0, popen=popen)

    assert code == 1  # last observed exit code, not silently swallowed
    assert len(popen.calls) == 3  # never exceeds max_restarts


def test_launches_with_correct_args_and_cwd(monkeypatch):
    monkeypatch.setattr(bot_service.time, "sleep", lambda *_: None)
    popen = _fake_popen_factory([0])

    bot_service.supervise("C:/venv/python.exe", "main.py", popen=popen)

    args, cwd = popen.calls[0]
    assert args == ["C:/venv/python.exe", "main.py"]
    assert cwd == str(bot_service._ROOT)
