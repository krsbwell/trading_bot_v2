"""
Single-instance guard for main.py — added 2026-08-26 after finding 6
main.py-related processes running at once: 2 launched directly, 2 more
launched directly the day before, plus bot_service.py's own supervised
pair — none of them stopped by anything when the bot was "restarted" by
hand. The oldest zombie (2 days old) had been running the whole time and
was racing the live process to manage the same open trade — repeated
TRADE_DOESNT_EXIST errors closing trade 132 in logs/main.log are its
fingerprint. See bugs_duplicate_bot_instances memory.

Uses an OS-level exclusive file lock (msvcrt.locking) rather than a PID
file. A PID file can go stale — if the process dies without cleaning up,
the stale file falsely blocks every future start until someone deletes it
by hand. An OS lock is tied to an open file handle: Windows releases it
automatically the instant that handle is torn down, whether the process
exits cleanly, crashes, or is killed with Stop-Process -Force. No stale
lock ever needs manual cleanup.
"""
import logging
import msvcrt
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_LOCK_PATH = Path(__file__).parent / "main.lock"


class AlreadyRunningError(RuntimeError):
    """Raised when another main.py instance already holds the lock."""


def acquire(lock_path: Path = DEFAULT_LOCK_PATH):
    """
    Acquire the single-instance lock and return the open file handle.
    Raises AlreadyRunningError if another live process already holds it.
    Caller owns the handle — pass it to release() on shutdown (or just let
    process exit; the OS releases it either way).
    """
    fh = open(lock_path, "a+")
    # "a+" positions the stream at EOF, and msvcrt.locking locks the byte
    # range starting at the current position — without this seek, two
    # opens of a file that already has bytes in it would lock two
    # different byte offsets and never conflict. Always lock byte 0.
    fh.seek(0)
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fh.close()
        raise AlreadyRunningError(
            f"Another main.py instance already holds {lock_path} — refusing "
            "to start a second one (see bugs_duplicate_bot_instances memory)."
        )
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    logger.info("Single-instance lock acquired: %s (pid=%d)", lock_path, os.getpid())
    return fh


def release(fh) -> None:
    """Release a lock handle returned by acquire(). Safe to call with None."""
    if fh is None:
        return
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    fh.close()
