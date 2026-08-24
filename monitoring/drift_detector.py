"""
Feature-distribution drift check for the adaptive strategy's market-regime
confidence — compares the current classify_regime() confidence against a
stored baseline (memory.market_memory). Reports INSUFFICIENT_BASELINE_DATA
until memory.market_memory.MIN_SNAPSHOTS_FOR_BASELINE (30) regime
snapshots exist for a pair/timeframe, rather than false-positive alerting
off a handful of points. See tasks/todo.md 2026-08-21 Phase 5.

Off by default (config.AGENTS_CONFIG["drift_detector_enabled"]) — reports
only, never changes strategy behavior itself, matching the source design
doc's own "do not automatically change the strategy merely because drift
occurs" rule.
"""
import logging

import config
from memory import market_memory

logger = logging.getLogger(__name__)

# Standard-deviations-from-baseline-mean beyond which current confidence
# counts as drifted. 2.0 is a conventional "notably unusual, not yet
# extreme" threshold — not backtested/tuned, revisit once there's enough
# real drift history to tune it against.
_DRIFT_Z_THRESHOLD = 2.0


def check_drift(pair: str, timeframe: str, current_confidence: float) -> dict:
    """Returns one of:
      {"status": "DISABLED"}
      {"status": "INSUFFICIENT_BASELINE_DATA", "n_samples": <int>}
      {"status": "OK", "z_score": <float>, "baseline_mean": <float>}
      {"status": "DRIFT_DETECTED", "z_score": <float>, "baseline_mean": <float>}
    Never raises."""
    if not config.AGENTS_CONFIG.get("drift_detector_enabled"):
        return {"status": "DISABLED"}

    if not market_memory.has_baseline(pair, timeframe):
        return {"status": "INSUFFICIENT_BASELINE_DATA",
                "n_samples": len(market_memory.history(pair, timeframe, n=market_memory.MIN_SNAPSHOTS_FOR_BASELINE))}

    stats = market_memory.baseline_confidence_stats(pair, timeframe)
    if stats is None or stats["stdev"] == 0:
        return {"status": "INSUFFICIENT_BASELINE_DATA", "n_samples": stats["n"] if stats else 0}

    z = (current_confidence - stats["mean"]) / stats["stdev"]
    status = "DRIFT_DETECTED" if abs(z) > _DRIFT_Z_THRESHOLD else "OK"
    if status == "DRIFT_DETECTED":
        logger.info("drift_detector: %s %s z=%.2f (baseline mean=%.3f)", pair, timeframe, z, stats["mean"])
    return {"status": status, "z_score": round(z, 3), "baseline_mean": round(stats["mean"], 3)}
