"""
learning/reinforcement_learning.py — scaffold only, tasks/todo.md
2026-08-21 Phase 5. No algorithm is trained; every test confirms the
honest-status-reporting contract (INSUFFICIENT_DATA / NOT_IMPLEMENTED),
never a fabricated trained result.
"""
import json

import pytest

from database.experiences import insert_decision
from database.models import init_schema
from database.trades import insert_trade_outcome
from learning.reinforcement_learning import get_experience_tuples, train_step


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "adaptive_test.db"
    init_schema(db_path=p)
    return p


def _decision_dict(action="BUY", regime="TRENDING_UP"):
    return {"pair": "EUR_USD", "action": action, "confidence": 0.6, "regime": regime,
            "model_version": "v1", "reasoning": {"ema_score": 15}}


def test_experience_tuples_empty_with_no_trades(db_path):
    assert get_experience_tuples(db_path=db_path) == []


def test_experience_tuples_built_from_decision_and_outcome_join(db_path):
    decision_id = insert_decision(_decision_dict(), db_path=db_path)
    insert_trade_outcome("EUR_USD", "long", pnl=5.0, outcome="win",
                          decision_id=decision_id, db_path=db_path)
    tuples = get_experience_tuples(db_path=db_path)
    assert len(tuples) == 1
    assert tuples[0]["action"] == "BUY"
    assert tuples[0]["reward"] == 5.0
    assert tuples[0]["state"] == {"ema_score": 15}


def test_train_step_reports_insufficient_data_below_floor(db_path):
    decision_id = insert_decision(_decision_dict(), db_path=db_path)
    insert_trade_outcome("EUR_USD", "long", pnl=5.0, outcome="win",
                          decision_id=decision_id, db_path=db_path)
    result = train_step(db_path=db_path, min_samples=10)
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["n_samples"] == 1
    assert result["min_required"] == 10


def test_train_step_reports_not_implemented_above_floor(db_path):
    for i in range(5):
        decision_id = insert_decision(_decision_dict(), db_path=db_path)
        insert_trade_outcome("EUR_USD", "long", pnl=1.0, outcome="win",
                              decision_id=decision_id, db_path=db_path)
    result = train_step(db_path=db_path, min_samples=5)
    assert result["status"] == "NOT_IMPLEMENTED"
    assert result["n_samples"] == 5
    # Must never claim a trained result exists.
    assert "model" not in result
    assert "weights" not in result
    assert "policy" not in result


def test_train_step_uses_config_default_when_min_samples_omitted(db_path):
    import config
    result = train_step(db_path=db_path)
    assert result["min_required"] == config.LEARNING_ADAPTIVE["min_samples_for_rl_train_step"]


def test_experience_tuples_handles_malformed_reasoning_json_gracefully(db_path, monkeypatch):
    decision_id = insert_decision(_decision_dict(), db_path=db_path)
    insert_trade_outcome("EUR_USD", "long", pnl=1.0, outcome="win",
                          decision_id=decision_id, db_path=db_path)
    # Corrupt the stored reasoning_json directly to simulate bad data.
    from database.models import get_connection
    conn = get_connection(db_path)
    conn.execute("UPDATE decisions SET reasoning_json = ? WHERE id = ?", ("not valid json", decision_id))
    conn.commit()
    conn.close()

    tuples = get_experience_tuples(db_path=db_path)   # must not raise
    assert tuples[0]["state"] == {}
