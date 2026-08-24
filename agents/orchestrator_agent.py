"""
The one genuinely new piece of agents/ — everything else in this package
is a thin facade over code that already existed. See tasks/todo.md
2026-08-21 "Adaptive AI/ML Strategy — Integration Plan" Phase 3.

IMPORTANT — what this module does NOT do: it does not execute trades by
default, and it is not required for the adaptive strategy to trade.
engine.strategy_adaptive is registered in engine.strategy_dispatch exactly
like every other strategy, so once a pair is added to
config.STRATEGY_OVERRIDE, main.py's existing scheduler loop already
scores it via engine.signal_engine.SignalEngine.run() and already calls
trade.trade_manager.TradeManager.open_trade() on a real trigger — the same
path every other strategy uses today. Adding a second execution path here
would risk placing the same trade twice.

What this module actually adds: main.py's existing pipeline logs to
data/signal_log.csv (learning.data_collector) but has no idea about named
market regimes or the structured Decision object — this module is the
bridge that takes a signal dict SignalEngine already produced and records
the regime-aware "experience" (engine.decision.Decision) that only this
adaptive path produces, into memory.decision_memory /
memory.market_memory / memory.agent_memory. Call it once per pair per
scan cycle, right after SignalEngine.run(), same as data_collector's own
record_signal()/record_skip() are already called.
"""
import logging

import config
from agents import market_agent, research_agent, trade_agent
from engine.decision import Decision
from memory import agent_memory, decision_memory, market_memory

logger = logging.getLogger(__name__)


def _confidence_from_signal(signal: dict) -> "float | None":
    ml_prob = signal.get("ml_win_prob")
    if ml_prob is not None:
        return float(ml_prob)
    score = signal.get("score")
    if score is not None:
        return max(0.0, min(1.0, float(score) / 100))
    return None


def _action_from_signal(signal: "dict | None") -> str:
    if not signal or signal.get("no_signal"):
        return "NO_TRADE"
    if signal.get("watching") or signal.get("gate_blocked"):
        return "HOLD"
    direction = signal.get("direction")
    if direction == "long":
        return "BUY"
    if direction == "short":
        return "SELL"
    return "NO_TRADE"


def log_adaptive_decision(pair: str, signal: "dict | None", get_candles_fn,
                           primary_tf: "str | None" = None, confirm_tf: "str | None" = None,
                           also_execute: bool = False, trade_manager=None) -> Decision:
    """
    pair          : e.g. "EUR_USD"
    signal        : whatever engine.signal_engine.SignalEngine.run() returned
                     for this pair this cycle (None or a dict — both handled)
    get_candles_fn: passed straight through to agents.market_agent for a
                     regime classification (only fetches candles for that,
                     never re-scores the signal itself)
    also_execute  : False by default (see module docstring) — set True only
                     for a caller that is NOT also independently executing
                     via TradeManager.open_trade() itself, to avoid a
                     double order.

    Returns the Decision that was recorded (even a NO_TRADE one — every
    call produces and stores one, matching learning.shadow_outcomes'
    existing "track the near-misses too" approach for the other strategies).
    """
    context = market_agent.get_context(pair, get_candles_fn, primary_tf, confirm_tf)
    if context is None:
        decision = Decision.unrated(pair, regime="UNKNOWN", reason="candle fetch failed")
    else:
        regime_info = context["regime"]
        action = _action_from_signal(signal)
        confidence = _confidence_from_signal(signal) if signal else None
        research_context = research_agent.get_context(pair)
        reasoning = {"regime_info": regime_info}
        if signal:
            reasoning["score"] = signal.get("score")
            reasoning["ema_score"] = signal.get("ema_score")
        if research_context:
            reasoning["research"] = research_context

        decision = Decision(
            pair=pair, action=action, confidence=confidence,
            regime=regime_info["regime"],
            model_version=None,   # set by the caller if it ran learning_agent.predict_win_prob
            stop_loss=(signal or {}).get("stop_loss"),
            take_profit=((signal or {}).get("tp_levels") or {}).get("tp1"),
            reasoning=reasoning,
        )
        market_memory.record(pair, context["primary_tf"], regime_info)

    decision_memory.record(decision)
    agent_memory.record_run(pair, regime=decision.regime, action_taken=decision.action)

    if also_execute and trade_manager is not None and decision.action in ("BUY", "SELL") and signal:
        trade_id = trade_agent.execute(trade_manager, signal)
        if trade_id:
            logger.info("orchestrator_agent: executed %s %s -> trade_id=%s", pair, decision.action, trade_id)

    return decision
