"""
Query/write facade over database.market (the `regime_history` table).
Also the baseline source for monitoring/drift_detector.py — a baseline is
just "the regime-confidence distribution from the earliest N snapshots on
record for this pair/timeframe", computed on demand rather than a second
stored copy.
"""
from pathlib import Path

from database.market import get_regime_history, insert_regime_snapshot

# Below this many stored snapshots, there isn't a meaningful baseline yet —
# used by monitoring/drift_detector.py to report "insufficient data"
# instead of guessing off a handful of points.
MIN_SNAPSHOTS_FOR_BASELINE = 30


def record(pair: str, timeframe: str, regime_info: dict,
           db_path: "Path | str | None" = None) -> int:
    return insert_regime_snapshot(pair, timeframe, regime_info, db_path=db_path)


def history(pair: str, timeframe: str, n: int = 200,
            db_path: "Path | str | None" = None) -> list:
    return get_regime_history(pair, timeframe, limit=n, db_path=db_path)


def has_baseline(pair: str, timeframe: str, db_path: "Path | str | None" = None) -> bool:
    return len(history(pair, timeframe, n=MIN_SNAPSHOTS_FOR_BASELINE, db_path=db_path)) >= MIN_SNAPSHOTS_FOR_BASELINE


def baseline_confidence_stats(pair: str, timeframe: str,
                               db_path: "Path | str | None" = None) -> "dict | None":
    """Mean/stdev of stored regime confidence, oldest MIN_SNAPSHOTS_FOR_BASELINE
    snapshots on record — None if there isn't enough history yet."""
    rows = get_regime_history(pair, timeframe, limit=100000, db_path=db_path)
    if len(rows) < MIN_SNAPSHOTS_FOR_BASELINE:
        return None
    oldest = sorted(rows, key=lambda r: r["created_at"])[:MIN_SNAPSHOTS_FOR_BASELINE]
    vals = [r["confidence"] for r in oldest if r["confidence"] is not None]
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    variance = sum((v - mean) ** 2 for v in vals) / len(vals)
    return {"mean": mean, "stdev": variance ** 0.5, "n": len(vals)}
