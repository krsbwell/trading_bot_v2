"""
Model version registry for learning.pattern_learner.PatternLearner.

Wraps the existing models/*.pkl files with a candidate/testing/validated/
production/retired/rejected lifecycle (data/model_registry.json) — does
NOT change how PatternLearner trains or predicts.
learning.pattern_learner.PatternLearner.train()/predict_win_prob()/
get_accuracy()/get_feature_importance() already accept an explicit
model_path (see learning/pattern_learner.py), so registering a version is
just recording which .pkl file is at which lifecycle stage; no changes to
pattern_learner.py's training/prediction code were needed for this.

Built as part of the adaptive-strategy integration, see tasks/todo.md
(2026-08-21). A candidate never automatically becomes production — that's
always an explicit set_stage() call, matching the source design doc's
"a candidate model must NOT automatically replace the production model"
rule.
"""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_REGISTRY_PATH = _ROOT / "data" / "model_registry.json"
_MODELS_DIR = _ROOT / "models"

VALID_STAGES = ("candidate", "testing", "validated", "production", "retired", "rejected")

# model_path() builds a filesystem path directly from `version` — this
# repo doesn't call register()/model_path() with untrusted input today,
# but constraining the character set is a cheap, correct guard against a
# version string like "../../etc/passwd" ever writing outside
# models/registry/, regardless of where a future caller gets it from.
_VALID_VERSION = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _validate_version(version: str) -> None:
    if not _VALID_VERSION.match(version):
        raise ValueError(
            f"invalid model version {version!r} — must match {_VALID_VERSION.pattern} "
            "(alphanumeric, underscore, dot, hyphen only, no path separators)"
        )


class ModelRegistry:
    """Persisted to data/model_registry.json — same load/save pattern as
    engine.adaptive_params.AdaptiveParams."""

    def __init__(self, registry_path: Path = _REGISTRY_PATH, models_dir: Path = _MODELS_DIR):
        self._path = registry_path
        self._models_dir = models_dir
        self._state = self._load()

    def _load(self) -> dict:
        try:
            if self._path.exists():
                return json.loads(self._path.read_text("utf-8"))
        except Exception as exc:
            logger.warning("ModelRegistry._load failed: %s", exc)
        return {"versions": {}}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._state, indent=2), "utf-8")
        except Exception as exc:
            logger.warning("ModelRegistry._save failed: %s", exc)

    def model_path(self, version: str) -> str:
        """Where a given version's .pkl lives — models/registry/<version>.pkl.
        Pass this to PatternLearner.train(model_path=...) to produce it."""
        _validate_version(version)
        return str(self._models_dir / "registry" / f"{version}.pkl")

    def register(self, version: str, stage: str = "candidate", metadata: "dict | None" = None) -> None:
        _validate_version(version)
        if stage not in VALID_STAGES:
            raise ValueError(f"stage must be one of {VALID_STAGES}, got {stage!r}")
        self._state.setdefault("versions", {})[version] = {
            "stage": stage,
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        self._save()

    def set_stage(self, version: str, stage: str) -> None:
        if stage not in VALID_STAGES:
            raise ValueError(f"stage must be one of {VALID_STAGES}, got {stage!r}")
        if version not in self._state.get("versions", {}):
            raise KeyError(f"unknown model version {version!r} — call register() first")

        # Promoting to production demotes the previous production version
        # to retired. Never overwrites or deletes its record/file — "never
        # overwrite model history" per the source design doc.
        if stage == "production":
            for v, info in self._state["versions"].items():
                if v != version and info.get("stage") == "production":
                    info["stage"] = "retired"
                    info["stage_changed_at"] = datetime.now(timezone.utc).isoformat()

        self._state["versions"][version]["stage"] = stage
        self._state["versions"][version]["stage_changed_at"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def production_version(self) -> "str | None":
        for v, info in self._state.get("versions", {}).items():
            if info.get("stage") == "production":
                return v
        return None

    def production_model_path(self, fallback: "str | None" = None) -> str:
        """Path to use for live predictions. Falls back to the given path
        (normally learning.pattern_learner.MODEL_PATH, the existing
        unversioned model already live today) when nothing has been
        promoted to production yet — a strict addition, not a behavior
        change, until something is actually promoted."""
        v = self.production_version()
        if v is None:
            if fallback is None:
                raise RuntimeError("No production model registered and no fallback given")
            return fallback
        return self.model_path(v)

    def get(self, version: str) -> "dict | None":
        return self._state.get("versions", {}).get(version)

    def list_by_stage(self, stage: str) -> list:
        return [v for v, info in self._state.get("versions", {}).items() if info.get("stage") == stage]
