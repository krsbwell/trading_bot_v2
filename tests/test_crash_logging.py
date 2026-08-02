"""
Tests for crash_logging.py — added 2026-08-12 after a second silent process
death (2026-08-11) left logs/main.log with zero trace of why main.py
stopped, same failure mode as the 2026-07-17 death in
bugs_process_death_no_logging. Root cause: Python's default
sys.excepthook/threading.excepthook write straight to stderr, bypassing the
`logging` module (and thus the RotatingFileHandler) entirely.
"""
import logging
import sys
import threading

import crash_logging


def _logger(caplog):
    logger = logging.getLogger("test_crash_logging")
    logger.setLevel(logging.CRITICAL)
    caplog.set_level(logging.CRITICAL, logger="test_crash_logging")
    return logger


def test_log_uncaught_exception_logs_critical_with_traceback(caplog):
    logger = _logger(caplog)
    try:
        raise ValueError("boom")
    except ValueError:
        crash_logging.log_uncaught_exception(logger, *sys.exc_info())

    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelno == logging.CRITICAL
    assert "UNCAUGHT EXCEPTION" in rec.message
    assert rec.exc_info is not None
    assert rec.exc_info[0] is ValueError


def test_log_uncaught_exception_passes_keyboardinterrupt_through(caplog, monkeypatch):
    """A manual Ctrl+C must NOT get logged as a critical crash — it should
    fall through to the real default excepthook unchanged."""
    logger = _logger(caplog)
    called = {}
    monkeypatch.setattr(sys, "__excepthook__",
                         lambda t, v, tb: called.setdefault("hit", (t, v, tb)))

    try:
        raise KeyboardInterrupt()
    except KeyboardInterrupt:
        crash_logging.log_uncaught_exception(logger, *sys.exc_info())

    assert "hit" in called
    assert called["hit"][0] is KeyboardInterrupt
    assert len(caplog.records) == 0, "KeyboardInterrupt must not be logged as a crash"


def test_log_thread_exception_logs_critical_with_thread_name(caplog):
    logger = _logger(caplog)

    class _FakeArgs:
        pass

    fake_thread = threading.Thread(name="wfo-refit")
    args = _FakeArgs()
    args.thread = fake_thread
    try:
        raise RuntimeError("thread died")
    except RuntimeError:
        args.exc_type, args.exc_value, args.exc_traceback = sys.exc_info()

    crash_logging.log_thread_exception(logger, args)

    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelno == logging.CRITICAL
    assert "wfo-refit" in rec.message
    assert rec.exc_info[0] is RuntimeError


def test_log_thread_exception_handles_missing_thread_ref(caplog):
    """args.thread can be None in real threading.excepthook calls — must not
    crash the crash-logger itself."""
    logger = _logger(caplog)

    class _FakeArgs:
        pass

    args = _FakeArgs()
    args.thread = None
    try:
        raise RuntimeError("thread died")
    except RuntimeError:
        args.exc_type, args.exc_value, args.exc_traceback = sys.exc_info()

    crash_logging.log_thread_exception(logger, args)

    assert len(caplog.records) == 1
    assert "'?'" in caplog.records[0].message or "?" in caplog.records[0].message


def test_install_wires_both_hooks_and_they_actually_log(caplog):
    """Not just 'some new callable got assigned' — prove the installed
    sys.excepthook really does route an exception through logger.critical."""
    logger = _logger(caplog)
    orig_excepthook = sys.excepthook
    orig_thread_hook = threading.excepthook
    try:
        crash_logging.install(logger)
        assert sys.excepthook is not orig_excepthook
        assert threading.excepthook is not orig_thread_hook

        try:
            raise RuntimeError("boom")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())

        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.CRITICAL
        assert caplog.records[0].exc_info[0] is RuntimeError
    finally:
        sys.excepthook = orig_excepthook
        threading.excepthook = orig_thread_hook
