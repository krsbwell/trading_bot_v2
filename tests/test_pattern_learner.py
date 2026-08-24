"""
learning/pattern_learner.py — the real ML system behind the bot's
"learning" (see tasks/todo.md 2026-08-22 for why this file, not another
hand-coded strategy, became the focus). No dedicated test file existed for
this module before now, despite it being the actual learning mechanism —
covering both the pre-existing training/prediction path and the
2026-08-22 additions (pattern_name-derived features, per-pair feature).
"""
import numpy as np
import pandas as pd
import pytest

from learning.pattern_learner import (
    CATEGORICAL_FEATURES, FEATURES, NUMERIC_FEATURES, PatternLearner,
    _derive_pattern_features, _encode_categoricals, _load_model_cached,
    _model_cache, _pattern_name_to_features, _PAIR_ENCODING,
)


# ── _pattern_name_to_features ────────────────────────────────────────────

def test_empty_pattern_name_is_all_zero():
    result = _pattern_name_to_features("")
    assert result == {"has_bullish_pattern": 0.0, "has_bearish_pattern": 0.0, "pattern_count": 0.0}


def test_nan_pattern_name_does_not_raise():
    result = _pattern_name_to_features(float("nan"))
    assert result["pattern_count"] == 0.0


def test_single_bullish_pattern():
    result = _pattern_name_to_features("bullish_pin_bar")
    assert result == {"has_bullish_pattern": 1.0, "has_bearish_pattern": 0.0, "pattern_count": 1.0}


def test_single_bearish_pattern():
    result = _pattern_name_to_features("shooting_star")
    assert result == {"has_bullish_pattern": 0.0, "has_bearish_pattern": 1.0, "pattern_count": 1.0}


def test_multiple_pipe_joined_patterns():
    result = _pattern_name_to_features("bullish_pin_bar|bullish_engulfing")
    assert result == {"has_bullish_pattern": 1.0, "has_bearish_pattern": 0.0, "pattern_count": 2.0}


def test_mixed_bullish_and_bearish_both_flagged():
    """Real data can have both flagged in the same row (rare, but the
    detector isn't mutually exclusive) — both flags should be set, not
    just one clobbering the other."""
    result = _pattern_name_to_features("bullish_pin_bar|shooting_star")
    assert result["has_bullish_pattern"] == 1.0
    assert result["has_bearish_pattern"] == 1.0
    assert result["pattern_count"] == 2.0


def test_unknown_pattern_name_counted_but_not_flagged():
    result = _pattern_name_to_features("some_future_pattern_not_in_either_set")
    assert result["has_bullish_pattern"] == 0.0
    assert result["has_bearish_pattern"] == 0.0
    assert result["pattern_count"] == 1.0


# ── _derive_pattern_features ─────────────────────────────────────────────

def test_derive_pattern_features_adds_columns():
    df = pd.DataFrame({"pattern_name": ["bullish_pin_bar", "", "shooting_star|bearish_engulfing"]})
    out = _derive_pattern_features(df)
    assert list(out["has_bullish_pattern"]) == [1.0, 0.0, 0.0]
    assert list(out["has_bearish_pattern"]) == [0.0, 0.0, 1.0]
    assert list(out["pattern_count"]) == [1.0, 0.0, 2.0]


def test_derive_pattern_features_no_op_without_pattern_name_column():
    df = pd.DataFrame({"other_col": [1, 2, 3]})
    out = _derive_pattern_features(df)
    assert "has_bullish_pattern" not in out.columns   # must not raise, just skip


def test_derive_pattern_features_does_not_mutate_input():
    df = pd.DataFrame({"pattern_name": ["hammer"]})
    _derive_pattern_features(df)
    assert "has_bullish_pattern" not in df.columns


# ── pair feature / encoding ──────────────────────────────────────────────

def test_pair_is_a_categorical_feature():
    assert "pair" in CATEGORICAL_FEATURES
    assert "pair" in FEATURES


def test_pair_encoding_covers_all_active_and_watch_pairs():
    import config
    for pair in list(config.FOREX_PAIRS) + list(config.FOREX_WATCH):
        assert pair in _PAIR_ENCODING, f"{pair} missing from _PAIR_ENCODING"


def test_pair_encoding_values_are_unique():
    assert len(set(_PAIR_ENCODING.values())) == len(_PAIR_ENCODING)


def test_encode_categoricals_handles_unknown_pair_without_raising():
    df = pd.DataFrame({"pair": ["SOME_NEW_PAIR"]})
    out = _encode_categoricals(df)
    assert out["pair"].iloc[0] == 0.0   # unmapped -> fillna(0), same as every other categorical


# ── market_structure encoding matches the real classify_structure() output ─

def test_market_structure_encoding_matches_classify_structure_values():
    """FIXED 2026-08-22 — the encoding used to expect "uptrend"/"downtrend",
    but engine.strategy_market_structure.classify_structure() actually
    returns "bullish"/"bearish"/"ranging". The mismatch silently collapsed
    every trending row to the same code as ranging (fillna(0) default) —
    confirmed against real data (222/223 logged rows showed "ranging").
    This test pins the encoding to the function's real contract so it
    can't silently drift back out of sync."""
    from engine.strategy_market_structure import classify_structure
    from learning.pattern_learner import _ENCODINGS

    encoding = _ENCODINGS["market_structure"]
    # Every value classify_structure() can actually return must be a key.
    assert classify_structure({"highs": [], "lows": []}) in encoding      # "ranging"
    assert "bullish" in encoding
    assert "bearish" in encoding
    assert encoding["bullish"] != encoding["ranging"]
    assert encoding["bearish"] != encoding["ranging"]
    assert encoding["bullish"] != encoding["bearish"]


# ── end-to-end train()/predict_win_prob() with the new features ─────────

def _synthetic_signal_log(n=60, seed=3) -> pd.DataFrame:
    """Enough rows to clear PatternLearner.MIN_SAMPLES (50), with a
    genuine (if crude) relationship between features and outcome so
    training doesn't degenerate, using real column names/values
    data_collector.py actually writes."""
    rng = np.random.default_rng(seed)
    rows = []
    patterns = ["bullish_pin_bar", "shooting_star", "bullish_pin_bar|bullish_engulfing", ""]
    pairs = ["GBP_USD", "NZD_USD", "GBP_CAD"]
    for i in range(n):
        pattern = patterns[i % len(patterns)]
        bullish = "bullish" in pattern
        outcome = "win" if (bullish and rng.random() > 0.3) or (not bullish and rng.random() > 0.7) else "loss"
        rows.append({
            "pair": pairs[i % len(pairs)], "direction": "long" if bullish else "short",
            "confluence_score": rng.uniform(50, 90), "ema_score": rng.uniform(0, 25),
            "structure_score": rng.uniform(0, 45), "pa_score": rng.uniform(0, 30),
            "pattern_name": pattern,
            "candle_body_ratio": rng.uniform(0, 1), "upper_wick_ratio": rng.uniform(0, 1),
            "lower_wick_ratio": rng.uniform(0, 1),
            "cci_at_signal": rng.uniform(-100, 100), "macd_hist_at_signal": rng.uniform(-0.001, 0.001),
            "was_at_sr_zone": rng.choice([0, 1]), "bos_confirmed": rng.choice([0, 1]),
            "atr_pips": rng.uniform(10, 40), "hour_utc": rng.integers(0, 24),
            "h4_trend": "bull" if bullish else "bear", "d_trend": "neutral",
            "market_structure": "uptrend" if bullish else "downtrend",
            "session": "london_ny",
            "outcome": outcome, "source": "live",
        })
    return pd.DataFrame(rows)


def test_train_picks_up_new_features_in_avail_columns(tmp_path, monkeypatch):
    log_path = tmp_path / "signal_log.csv"
    _synthetic_signal_log().to_csv(log_path, index=False)
    model_path = tmp_path / "model.pkl"

    learner = PatternLearner()
    summary = learner.train(log_path=str(log_path), model_path=str(model_path))
    assert summary is not None   # enough rows, must actually train

    import joblib
    saved = joblib.load(model_path)
    cols = saved["features"]
    assert "has_bullish_pattern" in cols
    assert "has_bearish_pattern" in cols
    assert "pattern_count" in cols
    assert "pair" in cols


def test_predict_win_prob_accepts_pattern_and_pair_features(tmp_path):
    log_path = tmp_path / "signal_log.csv"
    _synthetic_signal_log().to_csv(log_path, index=False)
    model_path = tmp_path / "model.pkl"

    learner = PatternLearner()
    learner.train(log_path=str(log_path), model_path=str(model_path))

    features = {
        "pair": "GBP_USD", "direction": "long", "confluence_score": 70,
        "ema_score": 20, "structure_score": 30, "pa_score": 15,
        "has_bullish_pattern": 1.0, "has_bearish_pattern": 0.0, "pattern_count": 1.0,
        "candle_body_ratio": 0.6, "upper_wick_ratio": 0.1, "lower_wick_ratio": 0.1,
        "cci_at_signal": 50, "macd_hist_at_signal": 0.0005,
        "was_at_sr_zone": 1, "bos_confirmed": 1, "atr_pips": 20, "hour_utc": 14,
        "h4_trend": "bull", "d_trend": "neutral", "market_structure": "uptrend",
        "session": "london_ny",
    }
    prob = learner.predict_win_prob(features, model_path=str(model_path))
    assert prob is not None
    assert 0.0 <= prob <= 1.0


# ── _load_model_cached ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_model_cache():
    """The cache is process-wide (module-level dict) — clear around every
    test so one test's cached model_path can't leak into another."""
    _model_cache.clear()
    yield
    _model_cache.clear()


def test_load_model_cached_raises_file_not_found_for_missing_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        _load_model_cached(str(tmp_path / "does_not_exist.pkl"))


def test_load_model_cached_reuses_cache_on_unchanged_mtime(tmp_path, monkeypatch):
    import joblib
    path = tmp_path / "model.pkl"
    joblib.dump({"model": "v1", "features": [], "accuracy": 0.5}, path)

    calls = {"n": 0}
    real_load = joblib.load

    def _counting_load(p):
        calls["n"] += 1
        return real_load(p)

    monkeypatch.setattr(joblib, "load", _counting_load)

    first = _load_model_cached(str(path))
    second = _load_model_cached(str(path))
    assert first["model"] == "v1"
    assert second["model"] == "v1"
    assert calls["n"] == 1   # second call was a cache hit, no disk read


def test_load_model_cached_reloads_after_file_actually_changes(tmp_path):
    import joblib
    import time
    path = tmp_path / "model.pkl"
    joblib.dump({"model": "v1", "features": [], "accuracy": 0.5}, path)
    first = _load_model_cached(str(path))
    assert first["model"] == "v1"

    time.sleep(0.05)   # ensure a distinct mtime on platforms with coarse resolution
    joblib.dump({"model": "v2", "features": [], "accuracy": 0.6}, path)
    second = _load_model_cached(str(path))
    assert second["model"] == "v2"   # picked up the retrain, not stale cache


def test_get_accuracy_uses_cache_without_raising_on_missing_model(tmp_path):
    learner = PatternLearner()
    assert learner.get_accuracy(model_path=str(tmp_path / "nope.pkl")) is None


def test_get_feature_importance_uses_cache_without_raising_on_missing_model(tmp_path):
    learner = PatternLearner()
    assert learner.get_feature_importance(model_path=str(tmp_path / "nope.pkl")) is None


def test_predict_win_prob_missing_new_features_defaults_gracefully(tmp_path):
    """A caller that doesn't supply the new fields (e.g. old code not yet
    updated) must not crash — same 'missing -> 0' contract predict_win_prob
    already has for every other feature."""
    log_path = tmp_path / "signal_log.csv"
    _synthetic_signal_log().to_csv(log_path, index=False)
    model_path = tmp_path / "model.pkl"

    learner = PatternLearner()
    learner.train(log_path=str(log_path), model_path=str(model_path))

    minimal_features = {"pair": "GBP_USD", "direction": "long", "confluence_score": 70}
    prob = learner.predict_win_prob(minimal_features, model_path=str(model_path))
    assert prob is not None
    assert 0.0 <= prob <= 1.0
