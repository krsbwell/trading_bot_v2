"""
Signal Audit Logger — records every evaluated trade setup so missed signals
can be investigated after the fact.

Output: data/signal_audit.csv
Columns:
    timestamp, pair, timeframe, direction,
    ema_score, structure_score, pa_score, confluence_score,
    c1, c2_h4, c3_touch, c4_cci_oversold, c5_cci_recovery, c6_macd,
    cci_at_touch, cci_current, macd_hist, h4_trend, d_trend,
    result, reject_reason
"""

import csv
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

_AUDIT_PATH = Path("data/signal_audit.csv")
_FIELDNAMES = [
    "timestamp", "pair", "timeframe", "direction",
    "ema_score", "structure_score", "pa_score", "confluence_score",
    "c1_above_ema", "c2_h4_aligned", "c3_touch", "c4_cci_at_touch",
    "c5_cci_recovery", "c6_macd",
    "cci_at_touch_val", "cci_current", "macd_hist_val",
    "h4_trend", "d_trend",
    "result",       # TRIGGERED | WATCHING | SCANNING | BLOCKED | NO_SIGNAL
    "reject_reason",
]
_lock = Lock()
_initialised = False


def _ensure_file() -> None:
    global _initialised
    if _initialised:
        return
    _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _AUDIT_PATH.exists():
        with open(_AUDIT_PATH, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=_FIELDNAMES).writeheader()
    _initialised = True


def log_signal(
    *,
    pair: str,
    timeframe: str,
    direction: str,
    ema_score: float,
    structure_score: float,
    pa_score: float,
    confluence_score: int,
    # EMA/CCI/MACD condition flags (None if not evaluated)
    c1: bool = None,
    c2_h4: bool = None,
    c3_touch: bool = None,
    c4_cci_at_touch: bool = None,
    c5_cci_recovery: bool = None,
    c6_macd: bool = None,
    cci_at_touch_val: float = None,
    cci_current: float = None,
    macd_hist_val: float = None,
    h4_trend: str = "neutral",
    d_trend: str = "neutral",
    result: str = "NO_SIGNAL",
    reject_reason: str = "",
) -> None:
    """Write one audit row. Non-blocking — errors are logged, not raised."""
    try:
        _ensure_file()
        row = {
            "timestamp":          datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "pair":               pair,
            "timeframe":          timeframe,
            "direction":          direction,
            "ema_score":          f"{ema_score:.2f}" if ema_score is not None else "",
            "structure_score":    f"{structure_score:.2f}" if structure_score is not None else "",
            "pa_score":           f"{pa_score:.2f}" if pa_score is not None else "",
            "confluence_score":   confluence_score if confluence_score is not None else "",
            "c1_above_ema":       _b(c1),
            "c2_h4_aligned":      _b(c2_h4),
            "c3_touch":           _b(c3_touch),
            "c4_cci_at_touch":    _b(c4_cci_at_touch),
            "c5_cci_recovery":    _b(c5_cci_recovery),
            "c6_macd":            _b(c6_macd),
            "cci_at_touch_val":   f"{cci_at_touch_val:.2f}" if cci_at_touch_val is not None else "",
            "cci_current":        f"{cci_current:.2f}" if cci_current is not None else "",
            "macd_hist_val":      f"{macd_hist_val:.6f}" if macd_hist_val is not None else "",
            "h4_trend":           h4_trend,
            "d_trend":            d_trend,
            "result":             result,
            "reject_reason":      reject_reason,
        }
        with _lock:
            with open(_AUDIT_PATH, "a", newline="") as f:
                csv.DictWriter(f, fieldnames=_FIELDNAMES).writerow(row)
    except Exception as exc:
        logger.warning("signal_audit.log_signal failed: %s", exc)


def _b(val) -> str:
    if val is None:
        return ""
    return "1" if val else "0"


# ── Analytics helpers ─────────────────────────────────────────────────────────

def signal_frequency_by_week() -> dict[str, list[dict]]:
    """
    Read signal_audit.csv and return weekly trigger counts per pair.
    Returns: {pair: [{week: "YYYY-Www", count: N}, ...]}
    """
    if not _AUDIT_PATH.exists():
        return {}
    try:
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        with open(_AUDIT_PATH, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("result") != "TRIGGERED":
                    continue
                try:
                    dt = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                    week = dt.strftime("%G-W%V")
                    counts[row["pair"]][week] += 1
                except Exception:
                    pass
        result = {}
        for pair, weeks in counts.items():
            result[pair] = [{"week": w, "count": c}
                            for w, c in sorted(weeks.items())]
        return result
    except Exception as exc:
        logger.warning("signal_frequency_by_week failed: %s", exc)
        return {}


def watching_signal_stats() -> dict:
    """
    Return counts of WATCHING signals (scored but not triggered) per pair.
    These are potential missed setups — high score but below MIN_CONFLUENCE_SCORE.
    """
    if not _AUDIT_PATH.exists():
        return {}
    try:
        counts: dict[str, int] = defaultdict(int)
        with open(_AUDIT_PATH, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("result") == "WATCHING":
                    counts[row["pair"]] += 1
        return dict(counts)
    except Exception as exc:
        logger.warning("watching_signal_stats failed: %s", exc)
        return {}


def signal_quality_analysis() -> dict:
    """
    Full signal quality diagnostics from the audit CSV.

    Returns:
        total           : int — total rows
        triggered       : int
        trigger_rate    : float — triggered / total
        avg_score       : float — mean confluence_score
        score_dist      : list[{label, count}] — bucketed by 10s
        cond_rates      : list[{cond, passed, total, rate}] sorted by rate asc
        bottleneck      : str  — condition with lowest pass rate
        daily           : list[{date, triggered, scanning, no_signal, blocked}]
        pair_stats      : list[{pair, total, triggered, avg_score}] sorted by triggered desc
        what_if         : list[{label, extra_signals}] — simulated unlock at c4/c6 thresholds
    """
    if not _AUDIT_PATH.exists():
        return {"error": "signal_audit.csv not found"}

    try:
        rows = []
        with open(_AUDIT_PATH, newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception as exc:
        return {"error": str(exc)}

    if not rows:
        return {"error": "No data in signal_audit.csv"}

    total     = len(rows)
    triggered = sum(1 for r in rows if r.get("result") == "TRIGGERED")

    scores = []
    for r in rows:
        try:
            scores.append(float(r["confluence_score"]))
        except (ValueError, KeyError):
            pass

    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    # Score distribution (buckets of 10)
    bucket_counts: dict[int, int] = defaultdict(int)
    for s in scores:
        bucket_counts[(int(s) // 10) * 10] += 1
    score_dist = [
        {"label": f"{b}–{b+9}", "count": bucket_counts[b]}
        for b in sorted(bucket_counts)
    ]

    # Condition pass rates
    _CONDS = [
        ("c1_above_ema",    "C1 Price>EMA"),
        ("c2_h4_aligned",   "C2 H4 aligned"),
        ("c3_touch",        "C3 EMA touch"),
        ("c4_cci_at_touch", "C4 CCI at touch"),
        ("c5_cci_recovery", "C5 CCI recovery"),
        ("c6_macd",         "C6 MACD confirm"),
    ]
    cond_rates = []
    for col, label in _CONDS:
        vals = [r[col] for r in rows if r.get(col) in ("0", "1")]
        if vals:
            passed = vals.count("1")
            cond_rates.append({
                "cond":   label,
                "passed": passed,
                "total":  len(vals),
                "rate":   round(passed / len(vals), 3),
            })
    cond_rates.sort(key=lambda x: x["rate"])
    bottleneck = cond_rates[0]["cond"] if cond_rates else "—"

    # Daily breakdown
    daily_map: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        try:
            dt  = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
            day = dt.strftime("%Y-%m-%d")
            daily_map[day][r.get("result", "NO_SIGNAL")] += 1
        except Exception:
            pass
    daily = [
        {
            "date":      day,
            "triggered": daily_map[day].get("TRIGGERED", 0),
            "scanning":  daily_map[day].get("SCANNING",  0),
            "watching":  daily_map[day].get("WATCHING",  0),
            "no_signal": daily_map[day].get("NO_SIGNAL", 0),
            "blocked":   daily_map[day].get("BLOCKED",   0),
        }
        for day in sorted(daily_map)
    ]

    # Per-pair stats
    pair_map: dict[str, dict] = defaultdict(lambda: {"total": 0, "triggered": 0, "scores": []})
    for r in rows:
        p = r.get("pair", "?")
        pair_map[p]["total"] += 1
        if r.get("result") == "TRIGGERED":
            pair_map[p]["triggered"] += 1
        try:
            pair_map[p]["scores"].append(float(r["confluence_score"]))
        except (ValueError, KeyError):
            pass
    pair_stats = sorted([
        {
            "pair":      p,
            "total":     d["total"],
            "triggered": d["triggered"],
            "avg_score": round(sum(d["scores"]) / len(d["scores"]), 1) if d["scores"] else 0.0,
        }
        for p, d in pair_map.items()
    ], key=lambda x: x["triggered"], reverse=True)

    # What-if: how many extra signals if c4 or c6 threshold relaxed
    # (rows where only c4 fails → relaxing c4 unlocks them)
    c4_only_fail = sum(
        1 for r in rows
        if r.get("c4_cci_at_touch") == "0" and r.get("c6_macd") in ("0", "1")
        and r.get("c3_touch") == "1"   # touch still required
    )
    c6_only_fail = sum(
        1 for r in rows
        if r.get("c6_macd") == "0" and r.get("c4_cci_at_touch") in ("0", "1")
        and r.get("c3_touch") == "1"
    )
    both_fail = sum(
        1 for r in rows
        if r.get("c4_cci_at_touch") == "0" and r.get("c6_macd") == "0"
        and r.get("c3_touch") == "1"
    )
    what_if = [
        {"label": "Relax C4 only (CCI threshold)",  "extra_signals": c4_only_fail - both_fail},
        {"label": "Relax C6 only (MACD threshold)", "extra_signals": c6_only_fail - both_fail},
        {"label": "Relax both C4 + C6",             "extra_signals": c4_only_fail + c6_only_fail - both_fail},
    ]

    return {
        "total":        total,
        "triggered":    triggered,
        "trigger_rate": round(triggered / total, 3) if total else 0.0,
        "avg_score":    avg_score,
        "score_dist":   score_dist,
        "cond_rates":   cond_rates,
        "bottleneck":   bottleneck,
        "daily":        daily,
        "pair_stats":   pair_stats,
        "what_if":      what_if,
    }
