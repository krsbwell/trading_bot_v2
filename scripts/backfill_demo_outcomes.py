"""
One-off: recover the demo trades whose outcomes never reached
data/signal_log.csv (2026-07-31 → 2026-09-27 trade-id mismatch, see
learning/data_collector.py's module docstring).

Each closed bot trade in data/live_state_forex.json is matched to the
TRIGGERED row in data/signal_audit.csv that opened it (same pair +
direction, latest trigger within 3h before the open). Rows are written with
source="backfill": the audit has the scores/trend/CCI/MACD but NOT the candle
pattern, wick ratios or ATR, and pattern_learner fills missing features with
0, so these rows would teach the model "no pattern" where the truth is
"unknown". They're kept out of training (pattern_learner only reads
source=="live") but count for performance stats.

Idempotent: a trade already present (same pair + close timestamp) is skipped.
Usage:  python scripts/backfill_demo_outcomes.py [--write]   (dry run by default)
"""
import csv
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from learning.data_collector import SIGNAL_FIELDS, SIGNAL_LOG, _get_session, _write_row  # noqa: E402

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE    = os.path.join(ROOT, "data", "live_state_forex.json")
AUDIT    = os.path.join(ROOT, "data", "signal_audit.csv")
DEMO_START = "2026-07-31"
SOURCE_BACKFILL = "backfill"


def _utc(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s).replace(" ", "T"))
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def main(write: bool) -> None:
    trades = json.load(open(STATE, encoding="utf-8"))["closed_trades"]
    triggers = [r for r in csv.DictReader(open(AUDIT, encoding="utf-8", errors="ignore"))
                if r["result"] == "TRIGGERED"]
    existing = set()
    if os.path.exists(SIGNAL_LOG):
        for r in csv.DictReader(open(SIGNAL_LOG, encoding="utf-8", errors="ignore")):
            existing.add((r["pair"], r["timestamp"][:19]))

    written = skipped = unmatched = 0
    for t in trades:
        if (t["open_time"] < DEMO_START or not t.get("score") or t["score"] >= 100
                or t.get("ml_logged")):
            skipped += 1          # pre-demo, adopted orphan (score 0), manual dashboard
            continue              # order (score 100) or already logged
        close_ts = _utc(t["close_time"]).isoformat()[:19]
        if (t["pair"], close_ts) in existing:
            skipped += 1
            continue
        opened = _utc(t["open_time"])
        cands = [r for r in triggers
                 if r["pair"] == t["pair"] and r["direction"] == t["direction"]
                 and opened - timedelta(hours=3) <= _utc(r["timestamp"]) <= opened + timedelta(minutes=5)]
        a = max(cands, key=lambda r: r["timestamp"]) if cands else {}
        if not a:
            unmatched += 1

        pip = 0.01 if "JPY" in t["pair"] else 0.0001
        diff = (t["exit_price"] - t["entry"]) * (1 if t["direction"] == "long" else -1)
        pnl = t.get("realised_pnl") or 0.0
        row = {
            "timestamp": close_ts, "pair": t["pair"], "market": "forex",
            "direction": t["direction"],
            "entry_price": t["entry"], "stop_loss": t["sl"],
            "tp1": t.get("tp1") or "", "tp2": t.get("tp2") or "", "tp3": t.get("tp3") or "",
            "tp_level_hit": t.get("close_reason", ""),
            "position_size": t.get("size", ""), "risk_dollar": round(t.get("risk_dollar") or 0, 2),
            "risk_pct": 0.01,
            "confluence_score": t["score"],
            "ema_score": a.get("ema_score", ""), "structure_score": a.get("structure_score", ""),
            "pa_score": a.get("pa_score", ""),
            "cci_at_signal": a.get("cci_current", ""), "macd_hist_at_signal": a.get("macd_hist_val", ""),
            "h4_trend": a.get("h4_trend", ""), "d_trend": a.get("d_trend", ""),
            "outcome": "win" if pnl > 0 else "loss",
            "pnl_pips": round(diff / pip, 1), "pnl_dollar": round(pnl, 2),
            "hold_hours": round((_utc(t["close_time"]) - opened).total_seconds() / 3600, 2),
            "session": _get_session(opened), "hour_utc": opened.hour,
            "source": SOURCE_BACKFILL,
        }
        print(f"{'WRITE' if write else 'DRY  '} {t['id']:>4} {t['pair']} {t['direction']:5} "
              f"score={t['score']} {row['outcome']:4} ${pnl:+.2f}  audit={'yes' if a else 'NO MATCH'}")
        if write:
            _write_row(SIGNAL_LOG, SIGNAL_FIELDS, row)
        written += 1
    print(f"\n{'written' if write else 'would write'}={written}  skipped={skipped}  no_audit_match={unmatched}")


if __name__ == "__main__":
    main(write="--write" in sys.argv)
