"""
Process supervisor for main.py — this is what Windows Task Scheduler's
"TradingBot_v2" task actually launches at boot.

Added 2026-08-12. Before this, TradingBot_v2's registered action pointed at
"bot_service.py" — a file that never existed anywhere in this repo's git
history. The task ran on every boot (boot trigger, State=Ready) and silently
did nothing every single time; Get-ScheduledTaskInfo even failed with "the
system cannot find the file specified." Combined with run.ps1 (a *manual*
restart-on-crash loop that only helps while someone has it open in a
terminal) and scripts/watchdog.py (Task-Scheduler-run every 10 min, but
alert-only — it emails/Telegrams that the bot is down, it never restarts
it), there was no actual unattended auto-recovery path at all. A crash meant
the bot stayed dead until a human noticed (up to WATCHDOG_STALE_MINUTES=60
minutes later at best) and restarted it by hand — which is exactly what
happened 2026-08-11.

This restarts main.py whenever it exits for any reason other than a clean
shutdown (exit code 0), with a short backoff between attempts, and logs
every start/stop/restart to logs/bot_service.log — a persistent file, unlike
run.ps1's console-only "restarting in 15s..." messages which vanish the
moment that terminal closes.

Usage (what the Task Scheduler action should be — see the "How to apply"
section in bugs_bot_service_missing memory for the exact PowerShell used to
fix the registered task):
    <repo>/venv/Scripts/python.exe <repo>/bot_service.py main.py
"""
import logging
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

_ROOT = Path(__file__).parent
_LOG_DIR = _ROOT / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("bot_service")
logger.setLevel(logging.INFO)
if not logger.handlers:  # guard against duplicate handlers if imported twice
    _handler = RotatingFileHandler(
        _LOG_DIR / "bot_service.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    _handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))
    logger.addHandler(_handler)
    logger.addHandler(logging.StreamHandler(sys.stdout))


def supervise(python_exe: str, script: str, *, max_restarts: int = 50,
              restart_delay_secs: int = 15, popen=subprocess.Popen) -> int:
    """
    Launch `python_exe script` repeatedly until it exits with code 0 (clean
    shutdown, e.g. Ctrl+C) or max_restarts is reached. Returns the final exit
    code observed. `popen` is injectable so this is testable without
    actually spawning processes — must behave like subprocess.Popen (a
    callable taking an args list + cwd=, returning an object with .wait()).
    """
    attempt = 0
    exit_code = 1
    while attempt < max_restarts:
        attempt += 1
        logger.info("Starting %s %s — attempt %d/%d", python_exe, script, attempt, max_restarts)
        proc = popen([python_exe, script], cwd=str(_ROOT))
        exit_code = proc.wait()
        if exit_code == 0:
            logger.info("%s exited cleanly (code 0) — not restarting.", script)
            return 0
        logger.warning("%s exited with code %s — restarting in %ds (attempt %d/%d)",
                        script, exit_code, restart_delay_secs, attempt, max_restarts)
        time.sleep(restart_delay_secs)
    logger.critical("Max restarts (%d) reached without a clean exit — giving up. "
                     "Manual intervention required.", max_restarts)
    return exit_code


def main() -> None:
    script = sys.argv[1] if len(sys.argv) > 1 else "main.py"
    code = supervise(sys.executable, script)
    sys.exit(code)


if __name__ == "__main__":
    main()
