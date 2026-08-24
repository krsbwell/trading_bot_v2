"""
Structured decision object for the adaptive strategy path.

engine.signal_engine.SignalEngine.run() already returns a well-defined
dict for every strategy (score, ema_score, structure_score, pa_score,
stop_loss, tp_levels, ml_win_prob, market_structure, ...) — this is not a
replacement for that, and existing strategies keep returning plain dicts
unchanged. `Decision` is a typed adapter used specifically by
engine.strategy_adaptive / the agents+memory layer, so the extra fields
that only the adaptive path produces (named market regime, model version,
human-readable reasoning) have a stable shape instead of living as loose
dict keys.

Confidence must come from an actual model/score — see `Decision.unrated()`
below for the explicit "no valid confidence available" case, so a missing
prediction is never silently rendered as 0.0 (which looks like "confident
this is bad" rather than "no opinion").
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone


VALID_ACTIONS = ("BUY", "SELL", "HOLD", "EXIT", "NO_TRADE")


@dataclass
class Decision:
    pair: str
    action: str                       # one of VALID_ACTIONS
    confidence: "float | None"        # 0.0-1.0, or None if genuinely unrated
    regime: str                       # engine.market_regime.REGIMES value
    model_version: "str | None"       # learning.model_registry version tag, if ML-scored
    stop_loss: "float | None" = None
    take_profit: "float | None" = None
    reasoning: dict = field(default_factory=dict)   # e.g. {"ema_score": .., "regime_confidence": ..}
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        if self.action not in VALID_ACTIONS:
            raise ValueError(f"Decision.action must be one of {VALID_ACTIONS}, got {self.action!r}")
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"Decision.confidence must be in [0, 1] or None, got {self.confidence!r}")

    @classmethod
    def unrated(cls, pair: str, regime: str, reason: str) -> "Decision":
        """NO_TRADE with explicitly no confidence value — for when the
        model/pipeline cannot produce one (e.g. insufficient data), rather
        than fabricating a 0.0 that would misleadingly read as a confident
        rejection."""
        return cls(pair=pair, action="NO_TRADE", confidence=None, regime=regime,
                    model_version=None, reasoning={"reason": reason})

    def as_dict(self) -> dict:
        return {
            "pair": self.pair,
            "action": self.action,
            "confidence": self.confidence,
            "regime": self.regime,
            "model_version": self.model_version,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "reasoning": dict(self.reasoning),
            "timestamp": self.timestamp,
        }
