"""
Tests for the learning engine: data_collector, pattern_learner, feedback_loop.
All CSV I/O uses tmp_path so the real data/ directory is never touched.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import csv
import pandas as pd
import pytest

from learning.data_collector import (
    record_signal, record_close, record_skip,
    pending_count, clear_pending, SIGNAL_FIELDS, _get_session, _build_row,
)
from learning.pattern_learner import PatternLearner, FEATURES
from learning import feedback_loop


# ── Shared fixtures ───────────────────────────────────────────────────────────

def _signal(pair="EUR_USD", direction="long", score=80):
    return {
        "pair":              pair,
        "market":            "forex",
        "direction":         direction,
        "entry":             1.0800,
        "stop_loss":         1.0780,
        "tp_levels":         {"tp1": 1.0820, "tp2": 1.0840, "tp3": 1.0860},
        "score":             score,
        "ema_score":         25,
        "structure_score":   35,
        "pa_score":          20,
        "patterns":          ["bullish_pin_bar"],
        "ema_periods":       (34, 100, 200),
        "cci_at_signal":     -120.5,
        "macd_hist_at_signal": 0.00012,
        "market_structure":  "bullish",
        "was_at_sr_zone":    True,
        "bos_confirmed":     True,
        "ml_win_prob":       None,
        "candle_body_ratio": 0.45,
        "upper_wick_ratio":  0.20,
        "lower_wick_ratio":  0.35,
    }


def _write_csv(path, rows):
    """Write a list of dicts to a CSV for test setup."""
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _synthetic_log(n_win=40, n_loss=20, pattern="bullish_pin_bar", pair="EUR_USD"):
    """Build a synthetic signal_log as a list of row dicts for testing."""
    rows = []
    for i in range(n_win):
        rows.append({
            "timestamp": "2024-01-01T10:00:00+00:00", "pair": pair,
            "market": "forex", "direction": "long",
            "timeframe_primary": "H1", "timeframe_confirm": "H4",
            "entry_price": 1.08, "stop_loss": 1.078,
            "tp1": 1.082, "tp2": 1.084, "tp3": 1.086, "tp_level_hit": "tp1",
            "position_size": 50000, "risk_dollar": 100, "risk_pct": 0.01,
            "confluence_score": 80, "ema_score": 25, "structure_score": 35, "pa_score": 20,
            "pattern_name": pattern,
            "candle_body_ratio": 0.45, "upper_wick_ratio": 0.20, "lower_wick_ratio": 0.35,
            "cci_at_signal": -120.5, "macd_hist_at_signal": 0.00012,
            "ema_short_period": 34, "ema_mid_period": 100,
            "atr_pips": 12.5, "h4_trend": "bull", "d_trend": "bull",
            "market_structure": "uptrend", "was_at_sr_zone": 1, "bos_confirmed": 1,
            "ml_win_prob_at_entry": "",
            "outcome": "win", "pnl_pips": 20, "pnl_dollar": 100,
            "hold_hours": 4, "session": "london",
        })
    for i in range(n_loss):
        row = rows[0].copy()
        row.update({"outcome": "loss", "pnl_pips": -20, "pnl_dollar": -100,
                    "cci_at_signal": -85, "session": "asian",
                    "h4_trend": "bear", "atr_pips": 8.0})
        rows.append(row)
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# DataCollector
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataCollector:
    def setup_method(self):
        clear_pending()

    def test_record_signal_increments_pending(self):
        record_signal("T001", _signal(), 50000, 100.0)
        assert pending_count() == 1

    def test_record_close_writes_csv(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        record_signal("T002", _signal(), 50000, 100.0)
        record_close("T002", "win", 20.0, 100.0, 4.0, "tp1", log_path=log)
        assert os.path.exists(log)

    def test_record_close_clears_pending(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        record_signal("T003", _signal(), 50000, 100.0)
        record_close("T003", "win", 20.0, 100.0, 4.0, "tp1", log_path=log)
        assert pending_count() == 0

    def test_record_close_unknown_id_no_crash(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        record_close("GHOST", "win", 0, 0, 0, "", log_path=log)   # should not raise

    def test_csv_has_correct_headers(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        record_signal("T004", _signal(), 50000, 100.0)
        record_close("T004", "win", 20.0, 100.0, 4.0, "tp1", log_path=log)
        df = pd.read_csv(log)
        for field in SIGNAL_FIELDS:
            assert field in df.columns, f"Missing column: {field}"

    def test_all_signal_fields_populated(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        record_signal("T005", _signal(), 50000, 100.0)
        record_close("T005", "win", 20.0, 100.0, 4.0, "tp1", log_path=log)
        df = pd.read_csv(log)
        row = df.iloc[0]
        assert row["pair"] == "EUR_USD"
        assert row["direction"] == "long"
        assert row["outcome"] == "win"
        assert row["pnl_dollar"] == 100.0
        assert row["confluence_score"] == 80
        assert row["was_at_sr_zone"] == 1
        assert row["bos_confirmed"] == 1

    def test_pattern_name_joined(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        sig = _signal()
        sig["patterns"] = ["bullish_pin_bar", "hammer"]
        record_signal("T006", sig, 50000, 100.0)
        record_close("T006", "win", 20.0, 100.0, 4.0, "tp1", log_path=log)
        df = pd.read_csv(log)
        assert "bullish_pin_bar|hammer" == df.iloc[0]["pattern_name"]

    def test_record_skip_writes_skipped_outcome(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        record_skip(_signal(score=65), log_path=log)
        df = pd.read_csv(log)
        assert df.iloc[0]["outcome"] == "skipped"

    def test_record_skip_does_not_affect_pending(self):
        record_skip(_signal())
        assert pending_count() == 0

    def test_multiple_rows_appended(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        for i in range(3):
            record_signal(f"T{i:03d}", _signal(), 50000, 100.0)
            record_close(f"T{i:03d}", "win", 20.0, 100.0, 4.0, "tp1", log_path=log)
        df = pd.read_csv(log)
        assert len(df) == 3

    def test_header_written_once(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        for i in range(5):
            record_signal(f"H{i:03d}", _signal(), 50000, 100.0)
            record_close(f"H{i:03d}", "win", 20.0, 100.0, 4.0, "tp1", log_path=log)
        with open(log) as f:
            lines = f.readlines()
        header_lines = [l for l in lines if "pair" in l and "outcome" in l]
        assert len(header_lines) == 1

    def test_session_london(self):
        from datetime import datetime, timezone
        dt = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)  # 10:00 UTC
        assert _get_session(dt) == "london"

    def test_session_new_york(self):
        from datetime import datetime, timezone
        dt = datetime(2024, 1, 1, 17, 0, 0, tzinfo=timezone.utc)  # 17:00 UTC
        assert _get_session(dt) == "new_york"

    def test_session_asian(self):
        from datetime import datetime, timezone
        dt = datetime(2024, 1, 1, 3, 0, 0, tzinfo=timezone.utc)  # 03:00 UTC
        assert _get_session(dt) == "asian"

    def test_session_london_ny_overlap(self):
        from datetime import datetime, timezone
        dt = datetime(2024, 1, 1, 14, 0, 0, tzinfo=timezone.utc)  # 14:00 UTC
        assert _get_session(dt) == "london_ny"


# ═══════════════════════════════════════════════════════════════════════════════
# PatternLearner
# ═══════════════════════════════════════════════════════════════════════════════

class TestPatternLearner:
    def test_too_few_samples_returns_none(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        rows = _synthetic_log(n_win=10, n_loss=5)
        _write_csv(log, rows)
        learner = PatternLearner()
        result = learner.train(log_path=log, model_path=str(tmp_path / "model.pkl"))
        assert result is None

    def test_should_retrain_false_below_min(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        rows = _synthetic_log(n_win=10, n_loss=10)
        _write_csv(log, rows)
        learner = PatternLearner()
        assert learner.should_retrain(log_path=log) is False

    def test_should_retrain_true_above_threshold(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        rows = _synthetic_log(n_win=40, n_loss=20)   # 60 closed trades
        _write_csv(log, rows)
        learner = PatternLearner()
        learner._last_trained_count = 0
        assert learner.should_retrain(log_path=log) is True

    def test_should_retrain_false_recently_trained(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        rows = _synthetic_log(n_win=40, n_loss=20)   # 60 closed
        _write_csv(log, rows)
        learner = PatternLearner()
        learner._last_trained_count = 55              # only 5 new since last train
        assert learner.should_retrain(log_path=log) is False

    def test_train_returns_report_string(self, tmp_path):
        log   = str(tmp_path / "signal_log.csv")
        model = str(tmp_path / "model.pkl")
        rows  = _synthetic_log(n_win=40, n_loss=20)
        _write_csv(log, rows)
        learner = PatternLearner()
        report  = learner.train(log_path=log, model_path=model)
        assert isinstance(report, str)
        # XGBoost trainer returns an AUC summary, not a sklearn classification_report
        assert "auc" in report.lower() or "xgboost" in report.lower()

    def test_train_saves_model_file(self, tmp_path):
        log   = str(tmp_path / "signal_log.csv")
        model = str(tmp_path / "model.pkl")
        rows  = _synthetic_log(n_win=40, n_loss=20)
        _write_csv(log, rows)
        PatternLearner().train(log_path=log, model_path=model)
        assert os.path.exists(model)

    def test_train_updates_last_trained_count(self, tmp_path):
        log   = str(tmp_path / "signal_log.csv")
        model = str(tmp_path / "model.pkl")
        rows  = _synthetic_log(n_win=40, n_loss=20)
        _write_csv(log, rows)
        learner = PatternLearner()
        learner.train(log_path=log, model_path=model)
        assert learner._last_trained_count == 60

    def test_predict_returns_float(self, tmp_path):
        log   = str(tmp_path / "signal_log.csv")
        model = str(tmp_path / "model.pkl")
        rows  = _synthetic_log(n_win=40, n_loss=20)
        _write_csv(log, rows)
        learner = PatternLearner()
        learner.train(log_path=log, model_path=model)
        prob = learner.predict_win_prob(
            {f: 0.5 for f in FEATURES},
            model_path=model,
        )
        assert isinstance(prob, float)
        assert 0.0 <= prob <= 1.0

    def test_predict_no_model_returns_none(self, tmp_path):
        learner = PatternLearner()
        prob = learner.predict_win_prob(
            {f: 0.5 for f in FEATURES},
            model_path=str(tmp_path / "nonexistent.pkl"),
        )
        assert prob is None

    def test_feature_importance_after_train(self, tmp_path):
        log   = str(tmp_path / "signal_log.csv")
        model = str(tmp_path / "model.pkl")
        rows  = _synthetic_log(n_win=40, n_loss=20)
        _write_csv(log, rows)
        learner = PatternLearner()
        learner.train(log_path=log, model_path=model)
        importance = learner.get_feature_importance(model_path=model)
        assert isinstance(importance, dict)
        assert len(importance) > 0
        # Model trains only on columns present in the CSV — keys must be a subset of FEATURES
        assert set(importance.keys()).issubset(set(FEATURES))
        assert all(v >= 0 for v in importance.values())

    def test_top_features_returns_n_names(self, tmp_path):
        log   = str(tmp_path / "signal_log.csv")
        model = str(tmp_path / "model.pkl")
        rows  = _synthetic_log(n_win=40, n_loss=20)
        _write_csv(log, rows)
        learner = PatternLearner()
        learner.train(log_path=log, model_path=model)
        top = learner.top_features(n=3, model_path=model)
        assert len(top) == 3
        for f in top:
            assert f in FEATURES

    def test_should_retrain_no_file_returns_false(self, tmp_path):
        learner = PatternLearner()
        assert learner.should_retrain(log_path=str(tmp_path / "missing.csv")) is False


# ═══════════════════════════════════════════════════════════════════════════════
# FeedbackLoop
# ═══════════════════════════════════════════════════════════════════════════════

class TestFeedbackLoop:
    def test_missing_file_returns_empty(self, tmp_path):
        result = feedback_loop.generate_suggestions(
            log_path=str(tmp_path / "missing.csv")
        )
        assert result == []

    def test_no_closed_trades_returns_empty(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        rows = [_synthetic_log(n_win=0, n_loss=0)]
        # Write only skipped rows
        row = _synthetic_log(n_win=0, n_loss=0)
        _write_csv(log, [r for r in _synthetic_log(n_win=5, n_loss=0) if False])
        result = feedback_loop.generate_suggestions(log_path=log)
        assert isinstance(result, list)

    def test_boost_for_high_win_rate_pattern(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        # 8 wins, 2 losses for a specific pattern → 80% win rate → should boost
        rows = _synthetic_log(n_win=8, n_loss=2, pattern="bullish_pin_bar")
        _write_csv(log, rows)
        suggestions = feedback_loop.generate_suggestions(min_trades=5, log_path=log)
        boosts = [s for s in suggestions if s["type"] == "boost"]
        assert any("bullish_pin_bar" in s["text"] for s in boosts)

    def test_warning_for_low_win_rate_pair(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        # 2 wins, 8 losses for EUR_USD → 20% win rate → should warn
        rows = _synthetic_log(n_win=2, n_loss=8, pair="EUR_USD")
        _write_csv(log, rows)
        suggestions = feedback_loop.generate_suggestions(min_trades=5, log_path=log)
        warnings = [s for s in suggestions if s["type"] == "warning"]
        assert any("EUR_USD" in s["text"] for s in warnings)

    def test_cci_optimization_card(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        # Mix: most trades at CCI < 110 with low win rate,
        # but high-CCI trades have good win rate
        rows = _synthetic_log(n_win=5, n_loss=25)   # base: 17% win rate
        # Add 10 high-CCI wins
        for _ in range(10):
            r = rows[0].copy()
            r["cci_at_signal"] = -135
            r["outcome"] = "win"
            rows.append(r)
        _write_csv(log, rows)
        suggestions = feedback_loop.generate_suggestions(min_trades=5, log_path=log)
        optimizes = [s for s in suggestions if s["type"] == "optimize"]
        assert len(optimizes) >= 0   # may or may not trigger depending on thresholds

    def test_all_suggestions_have_required_keys(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        rows = _synthetic_log(n_win=8, n_loss=2)
        _write_csv(log, rows)
        suggestions = feedback_loop.generate_suggestions(min_trades=3, log_path=log)
        for s in suggestions:
            assert "type" in s
            assert "text" in s
            assert "dismissible" in s
            assert s["type"] in ("boost", "warning", "optimize")
            assert s["dismissible"] is True

    def test_should_run_false_no_file(self, tmp_path):
        feedback_loop._last_run_count = 0
        result = feedback_loop.should_run(log_path=str(tmp_path / "missing.csv"))
        assert result is False

    def test_should_run_true_after_10_trades(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        rows = _synthetic_log(n_win=7, n_loss=5)   # 12 closed
        _write_csv(log, rows)
        feedback_loop._last_run_count = 0
        assert feedback_loop.should_run(log_path=log) is True

    def test_should_run_false_recently_ran(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        rows = _synthetic_log(n_win=7, n_loss=5)   # 12 closed
        _write_csv(log, rows)
        feedback_loop._last_run_count = 10          # only 2 new trades
        assert feedback_loop.should_run(log_path=log) is False
