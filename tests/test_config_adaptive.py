"""
config.py's adaptive-strategy additions (ADAPTIVE_STRATEGY, LEARNING_ADAPTIVE,
MODEL_REGISTRY, RESEARCH, AGENTS_CONFIG, adaptive_strategy_params_for(),
_load_yaml_overrides()) — tasks/todo.md 2026-08-21 Phases 1 & 4.
"""
import sys
import types

import config


def test_adaptive_strategy_enabled_scoped_to_one_pair():
    """2026-08-23 — turned on for real forward-testing (see tasks/todo.md),
    but the rollout-safety property that matters is limited blast radius,
    not "off": only EUR_AUD is actually routed to it, every other pair's
    strategy is unaffected by this flag."""
    assert config.ADAPTIVE_STRATEGY["enabled"] is True
    assert config.STRATEGY_OVERRIDE.get("EUR_AUD") == "adaptive"
    for pair in ("NZD_USD", "GBP_CAD", "GBP_USD", "CHF_JPY", "AUD_JPY"):
        assert config.STRATEGY_OVERRIDE.get(pair, "ema_bounce") != "adaptive"


def test_research_disabled_by_default():
    assert config.RESEARCH["enabled"] is False
    assert config.RESEARCH["news_source"] is None


def test_agents_config_execute_off_by_default():
    assert config.AGENTS_CONFIG["orchestrator_also_execute"] is False


def test_adaptive_strategy_params_for_merges_per_pair_override(monkeypatch):
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY_PARAMS_PER_PAIR, "EUR_USD", {"atr_sl_mult": 2.0})
    params = config.adaptive_strategy_params_for("EUR_USD")
    assert params["atr_sl_mult"] == 2.0
    assert params["atr_tp_mult"] == config.ADAPTIVE_STRATEGY["atr_tp_mult"]   # untouched key still inherited


def test_adaptive_strategy_params_for_unlisted_pair_uses_global():
    params = config.adaptive_strategy_params_for("XXX_YYY")
    assert params == config.ADAPTIVE_STRATEGY


def test_load_yaml_overrides_does_not_raise_without_pyyaml():
    # This environment doesn't have pyyaml installed — must degrade
    # silently, not crash config.py on import.
    config._load_yaml_overrides()


def test_load_yaml_overrides_merges_known_keys_with_fake_yaml_module(tmp_path, monkeypatch):
    """Injects a minimal fake `yaml` module so the merge logic itself can
    be verified without requiring the real pyyaml dependency to be
    installed in this environment."""
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "strategy.yaml").write_text(
        "enabled: true\natr_sl_mult: 2.5\nnot_a_real_key: 999\n", "utf-8"
    )

    fake_yaml = types.ModuleType("yaml")
    fake_yaml.safe_load = lambda f: _parse_simple_yaml(f.read())
    monkeypatch.setitem(sys.modules, "yaml", fake_yaml)
    monkeypatch.setattr(config, "__file__", str(tmp_path / "config.py"))

    # Reset to known defaults before merging, since ADAPTIVE_STRATEGY is a
    # module-level dict other tests may have mutated via monkeypatch.setitem
    # (those revert automatically, but this test wants a clean baseline).
    original = dict(config.ADAPTIVE_STRATEGY)
    try:
        config._load_yaml_overrides()
        assert config.ADAPTIVE_STRATEGY["enabled"] is True
        assert config.ADAPTIVE_STRATEGY["atr_sl_mult"] == 2.5
        assert "not_a_real_key" not in config.ADAPTIVE_STRATEGY
    finally:
        config.ADAPTIVE_STRATEGY.clear()
        config.ADAPTIVE_STRATEGY.update(original)


def _parse_simple_yaml(text: str) -> dict:
    """Minimal key: value parser — enough for this test's flat yaml
    fixture, not a real yaml implementation."""
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value == "true":
            result[key] = True
        elif value == "false":
            result[key] = False
        else:
            try:
                result[key] = float(value) if "." in value else int(value)
            except ValueError:
                result[key] = value
    return result
