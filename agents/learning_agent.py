"""
ML-prediction facade — wraps learning.pattern_learner.PatternLearner +
learning.model_registry.ModelRegistry. Does not change how either trains
or predicts; this only resolves "which .pkl file should live prediction
use right now" (registry's production version if one is promoted,
otherwise pattern_learner's existing unversioned model — so nothing about
today's live ML boost path changes unless a version is explicitly promoted).
"""
import logging

from learning.model_registry import ModelRegistry
from learning.pattern_learner import MODEL_PATH, PatternLearner

logger = logging.getLogger(__name__)


def predict_win_prob(features: dict, registry: "ModelRegistry | None" = None,
                      learner: "PatternLearner | None" = None) -> "float | None":
    """None on any failure — same convention as
    PatternLearner.predict_win_prob itself (returns None rather than
    raising when the model file doesn't exist yet, e.g. before the first
    50 trades)."""
    registry = registry or ModelRegistry()
    learner = learner or PatternLearner()
    try:
        model_path = registry.production_model_path(fallback=MODEL_PATH)
    except Exception as exc:
        logger.debug("learning_agent: no usable model path: %s", exc)
        return None
    return learner.predict_win_prob(features, model_path=model_path)


def train_candidate(version: str, registry: "ModelRegistry | None" = None,
                     learner: "PatternLearner | None" = None, **train_kwargs) -> "dict | None":
    """Trains a new candidate version via PatternLearner.train() at the
    registry's path for `version` and registers it as 'candidate' — never
    'production'; promotion is always a separate, explicit
    registry.set_stage() call (see learning/model_registry.py)."""
    registry = registry or ModelRegistry()
    learner = learner or PatternLearner()
    model_path = registry.model_path(version)
    result = learner.train(model_path=model_path, **train_kwargs)
    registry.register(version, stage="candidate", metadata={"train_result": result})
    return result
