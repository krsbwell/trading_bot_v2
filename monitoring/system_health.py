"""
Read-only view over the EXISTING health-check files
(data/heartbeat.txt, data/watchdog_state.json) — reuses
scripts/watchdog.py's own mechanism and config.WATCHDOG_STALE_MINUTES
threshold rather than inventing a second one. The actual alerting
(Telegram bot-down messages) stays exactly where it already is, in
scripts/watchdog.py — this module only reports, it does not alert.
"""
from datetime import datetime, timezone
from pathlib import Path

import config

_ROOT = Path(__file__).parent.parent
_HEARTBEAT_PATH = _ROOT / "data" / "heartbeat.txt"
_WATCHDOG_STATE_PATH = _ROOT / "data" / "watchdog_state.json"


def status() -> dict:
    """{"heartbeat_exists": bool, "stale_minutes": float|None,
        "is_stale": bool|None, "watchdog_alerted": bool}"""
    if not _HEARTBEAT_PATH.exists():
        return {"heartbeat_exists": False, "stale_minutes": None,
                "is_stale": None, "watchdog_alerted": False}

    try:
        last_beat = datetime.fromisoformat(_HEARTBEAT_PATH.read_text().strip())
        stale_minutes = (datetime.now(timezone.utc) - last_beat).total_seconds() / 60
        is_stale = stale_minutes > config.WATCHDOG_STALE_MINUTES
    except Exception:
        return {"heartbeat_exists": True, "stale_minutes": None,
                "is_stale": None, "watchdog_alerted": False}

    alerted = False
    try:
        import json
        state = json.loads(_WATCHDOG_STATE_PATH.read_text())
        alerted = bool(state.get("alerted"))
    except Exception:
        pass

    return {"heartbeat_exists": True, "stale_minutes": round(stale_minutes, 1),
            "is_stale": is_stale, "watchdog_alerted": alerted}
