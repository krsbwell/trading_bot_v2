"""
Standalone watchdog for main.py's heartbeat file.

Deliberately independent of main.py and the Dash dashboard thread it hosts —
both go down together if the process dies (see bugs_process_death_no_logging
memory), so nothing running *inside* that process can be trusted to report
its own death. This script only needs data/heartbeat.txt and Telegram config;
it has no import dependency on main.py.

Run on a schedule external to the bot itself, e.g. Windows Task Scheduler:
    <venv>/Scripts/python.exe <repo>/scripts/watchdog.py
every 10-15 minutes is a reasonable interval given a 60-minute stale threshold.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from alerts.telegram_alert import send_bot_down, send_bot_recovered, is_configured

_HEARTBEAT_PATH = Path(__file__).parent.parent / "data" / "heartbeat.txt"
_STATE_PATH     = Path(__file__).parent.parent / "data" / "watchdog_state.json"


def _load_state() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text())
    except Exception:
        return {"alerted": False}


def _save_state(state: dict) -> None:
    try:
        _STATE_PATH.write_text(json.dumps(state))
    except Exception:
        pass


def check() -> None:
    if not is_configured():
        print("Telegram not configured — nothing to alert with, exiting.")
        return

    state = _load_state()

    if not _HEARTBEAT_PATH.exists():
        print("No heartbeat file yet — main.py may never have started. Skipping.")
        return

    try:
        last_beat = datetime.fromisoformat(_HEARTBEAT_PATH.read_text().strip())
    except Exception as exc:
        print(f"Couldn't parse heartbeat file: {exc}")
        return

    stale_minutes = (datetime.now(timezone.utc) - last_beat).total_seconds() / 60

    if stale_minutes > config.WATCHDOG_STALE_MINUTES:
        if not state.get("alerted"):
            print(f"Heartbeat stale ({stale_minutes:.0f} min) — sending BOT DOWN alert.")
            send_bot_down(stale_minutes)
            state["alerted"] = True
            _save_state(state)
        else:
            print(f"Heartbeat still stale ({stale_minutes:.0f} min) — already alerted.")
    else:
        if state.get("alerted"):
            print("Heartbeat fresh again — sending BOT RECOVERED alert.")
            send_bot_recovered()
        state["alerted"] = False
        _save_state(state)
        print(f"Heartbeat OK ({stale_minutes:.1f} min old).")


if __name__ == "__main__":
    check()
