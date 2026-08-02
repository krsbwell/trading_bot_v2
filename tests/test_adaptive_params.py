"""
Tests for engine/adaptive_params.py, focused on the get() merge-safety fix:
get() must return {} (not BASE_PARAMS) for a pair with no saved fit, so that
merging WFO params on top of it in signal_engine.py doesn't get silently
clobbered by base defaults. See engine/adaptive_params.py::get() docstring.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.adaptive_params import AdaptiveParams, BASE_PARAMS


def _ap(tmp_path):
    return AdaptiveParams(save_path=tmp_path / "adaptive_state.json")


def test_get_returns_empty_dict_for_unfitted_pair(tmp_path):
    ap = _ap(tmp_path)
    assert ap.get("USD_CAD") == {}


def test_get_returns_real_tier_params_after_force_tier(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADAPTIVE_PARAMS_ENABLED", True)  # tier logic itself, independent of the current global default
    ap = _ap(tmp_path)
    ap.force_tier("EUR_USD", win_rate=0.20)  # < 30% -> "strict" tier
    params = ap.get("EUR_USD")
    assert params["cci_threshold"] == 50
    assert params != BASE_PARAMS  # strict tier differs from base


def test_get_returns_empty_dict_when_disabled(tmp_path, monkeypatch):
    import config
    ap = _ap(tmp_path)
    ap.force_tier("EUR_USD", win_rate=0.20)
    monkeypatch.setattr(config, "ADAPTIVE_PARAMS_ENABLED", False)
    assert ap.get("EUR_USD") == {}


def test_get_does_not_mutate_shared_base_params(tmp_path):
    ap = _ap(tmp_path)
    result = ap.get("GBP_CAD")
    result["cci_threshold"] = 999
    assert BASE_PARAMS["cci_threshold"] == 20  # untouched
