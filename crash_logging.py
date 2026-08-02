"""
Uncaught-exception logging — installed by main.py so a crash anywhere in the
process (main thread or a background thread) leaves a permanent record in
the RotatingFileHandler-backed log file, not just on stderr.

Why this exists: Python's default sys.excepthook/threading.excepthook write
tracebacks straight to stderr, completely bypassing the `logging` module's
handlers. If stderr isn't captured to a persistent file (e.g. an ephemeral
terminal window), the traceback is lost the instant that window closes —
even though it printed at the time. This is exactly why a 2026-07-17 crash
was "unrecoverable cause (no file logging)" per bugs_process_death_no_logging,
and why a second silent death on 2026-08-11 again left main.log with zero
trace of why the process stopped (confirmed: no OS-level crash event in
Windows Event Viewer either, so it really was an in-process Python exit that
just never reached the log file). Routing both hooks through
`logger.critical(..., exc_info=...)` means the next crash, whatever causes
it, actually shows up in logs/main.log instead of vanishing.

Kept as its own module (rather than inline in main.py) so it's importable
and testable without pulling in main.py's OANDA/scheduler/dashboard setup.
"""
import logging
import sys
import threading


def log_uncaught_exception(logger: logging.Logger, exc_type, exc_value, exc_tb) -> None:
    """sys.excepthook replacement. Ctrl+C is passed through to the real
    default hook unchanged — only genuine crashes get the critical-log
    treatment, so a normal manual stop doesn't spam the log as an error."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    logger.critical("UNCAUGHT EXCEPTION — process is about to exit",
                     exc_info=(exc_type, exc_value, exc_tb))


def log_thread_exception(logger: logging.Logger, args) -> None:
    """threading.excepthook replacement — covers background daemon threads
    (WFO refit, shadow-outcomes resolution, etc.). Without this, an exception
    in one of those threads prints to stderr and kills only that thread,
    silently, with no persistent record and no effect on the main process —
    a different, quieter failure mode than a full process death, but the
    same underlying gap (nothing routes it through `logging`)."""
    thread_name = args.thread.name if args.thread is not None else "?"
    logger.critical("UNCAUGHT EXCEPTION in thread %r", thread_name,
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def install(logger: logging.Logger) -> None:
    """Wire both hooks. Call once, early — before starting the scheduler or
    any background threads — so nothing can crash ahead of this being live."""
    sys.excepthook = lambda t, v, tb: log_uncaught_exception(logger, t, v, tb)
    threading.excepthook = lambda args: log_thread_exception(logger, args)
