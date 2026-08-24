"""
Research-context facade — wraps research.market_research (see
research/ package, Phase 4 of tasks/todo.md's adaptive-strategy plan).
Import is deferred to call time and wrapped defensively so this module
degrades to an empty context rather than failing if the research package
is disabled (config.RESEARCH["enabled"]=False, the default) or its
optional dependencies aren't configured — additive context only, per the
same "additive only, doesn't gate a trade" rule research/ itself follows.
"""
import logging

logger = logging.getLogger(__name__)


def get_context(pair: str) -> dict:
    """Returns {} when research is disabled/unavailable — never raises,
    so a caller can always safely merge this into a Decision.reasoning
    dict without a None-check."""
    try:
        from research.market_research import get_context as _get_context
    except Exception as exc:
        logger.debug("research_agent: research package unavailable (%s)", exc)
        return {}

    try:
        return _get_context(pair) or {}
    except Exception as exc:
        logger.warning("research_agent: get_context failed for %s: %s", pair, exc)
        return {}
