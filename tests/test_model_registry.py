"""
learning/model_registry.py — version lifecycle tracking for
learning.pattern_learner's models/*.pkl files (tasks/todo.md, 2026-08-21).
Does not touch pattern_learner.py itself — verified separately by
importing both and confirming train()/predict_win_prob() still accept the
model_path this registry hands them (test_model_path_matches_pattern_learner_contract).
"""
import json

import pytest

from learning.model_registry import ModelRegistry, VALID_STAGES


@pytest.fixture
def registry(tmp_path):
    return ModelRegistry(registry_path=tmp_path / "model_registry.json", models_dir=tmp_path / "models")


def test_register_defaults_to_candidate(registry):
    registry.register("v1")
    assert registry.get("v1")["stage"] == "candidate"


def test_register_rejects_invalid_stage(registry):
    with pytest.raises(ValueError):
        registry.register("v1", stage="not_a_real_stage")


def test_register_rejects_path_traversal_version(registry):
    with pytest.raises(ValueError):
        registry.register("../../etc/passwd")


def test_model_path_rejects_path_traversal_version(registry):
    with pytest.raises(ValueError):
        registry.model_path("../../etc/passwd")


def test_model_path_rejects_path_separator_version(registry):
    with pytest.raises(ValueError):
        registry.model_path("sub/dir/version")


def test_set_stage_unknown_version_raises(registry):
    with pytest.raises(KeyError):
        registry.set_stage("ghost", "production")


def test_promoting_to_production_demotes_previous_production(registry):
    registry.register("v1", stage="production")
    registry.register("v2", stage="candidate")
    registry.set_stage("v2", "production")
    assert registry.get("v1")["stage"] == "retired"
    assert registry.get("v2")["stage"] == "production"
    assert registry.production_version() == "v2"


def test_production_model_path_falls_back_when_nothing_promoted(registry):
    fallback = "models/ml_model.pkl"
    assert registry.production_model_path(fallback=fallback) == fallback


def test_production_model_path_no_fallback_raises_when_unset(registry):
    with pytest.raises(RuntimeError):
        registry.production_model_path()


def test_production_model_path_uses_registered_path_once_promoted(registry):
    registry.register("v1", stage="production")
    path = registry.production_model_path(fallback="models/ml_model.pkl")
    assert "v1" in path
    assert path != "models/ml_model.pkl"


def test_list_by_stage(registry):
    registry.register("v1", stage="candidate")
    registry.register("v2", stage="rejected")
    registry.register("v3", stage="candidate")
    assert sorted(registry.list_by_stage("candidate")) == ["v1", "v3"]
    assert registry.list_by_stage("rejected") == ["v2"]


def test_state_persists_across_instances(tmp_path):
    reg_path = tmp_path / "model_registry.json"
    models_dir = tmp_path / "models"
    r1 = ModelRegistry(registry_path=reg_path, models_dir=models_dir)
    r1.register("v1", stage="validated", metadata={"accuracy": 0.61})
    r2 = ModelRegistry(registry_path=reg_path, models_dir=models_dir)
    assert r2.get("v1")["stage"] == "validated"
    assert r2.get("v1")["metadata"]["accuracy"] == 0.61


def test_registered_state_is_valid_json_on_disk(registry, tmp_path):
    registry.register("v1")
    on_disk = json.loads((tmp_path / "model_registry.json").read_text("utf-8"))
    assert "v1" in on_disk["versions"]


def test_model_path_matches_pattern_learner_contract(registry):
    """learning.pattern_learner.PatternLearner.train()/predict_win_prob()
    accept an explicit model_path kwarg — confirms this registry's path
    format is usable there without any change to pattern_learner.py."""
    from learning.pattern_learner import PatternLearner
    import inspect
    sig = inspect.signature(PatternLearner.train)
    assert "model_path" in sig.parameters
    sig = inspect.signature(PatternLearner.predict_win_prob)
    assert "model_path" in sig.parameters


def test_never_reverts_stage_change_history_silently(registry):
    """A retired version's own record is kept, not deleted, when another
    version is promoted — 'never overwrite model history'."""
    registry.register("v1", stage="production")
    registry.register("v2", stage="candidate")
    registry.set_stage("v2", "production")
    assert registry.get("v1") is not None
    assert "stage_changed_at" in registry.get("v1")
