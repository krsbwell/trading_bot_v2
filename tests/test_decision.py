"""
engine/decision.py — the typed Decision object used by the adaptive
strategy path (tasks/todo.md, 2026-08-21).
"""
import pytest

from engine.decision import Decision, VALID_ACTIONS


def test_valid_decision_constructs():
    d = Decision(pair="EUR_USD", action="BUY", confidence=0.7,
                 regime="TRENDING_UP", model_version="v1")
    assert d.action == "BUY"
    assert d.confidence == 0.7


def test_invalid_action_raises():
    with pytest.raises(ValueError):
        Decision(pair="EUR_USD", action="MAYBE", confidence=0.5,
                  regime="RANGING", model_version=None)


def test_confidence_out_of_range_raises():
    with pytest.raises(ValueError):
        Decision(pair="EUR_USD", action="BUY", confidence=1.5,
                  regime="RANGING", model_version=None)
    with pytest.raises(ValueError):
        Decision(pair="EUR_USD", action="BUY", confidence=-0.1,
                  regime="RANGING", model_version=None)


def test_confidence_none_is_allowed():
    d = Decision(pair="EUR_USD", action="NO_TRADE", confidence=None,
                 regime="UNKNOWN", model_version=None)
    assert d.confidence is None


def test_unrated_has_no_confidence_and_no_trade_action():
    d = Decision.unrated("EUR_USD", regime="UNKNOWN", reason="insufficient data")
    assert d.action == "NO_TRADE"
    assert d.confidence is None
    assert d.reasoning["reason"] == "insufficient data"


def test_as_dict_round_trips_fields():
    d = Decision(pair="EUR_USD", action="SELL", confidence=0.6,
                 regime="TRENDING_DOWN", model_version="v2",
                 stop_loss=1.1050, take_profit=1.0950, reasoning={"x": 1})
    out = d.as_dict()
    assert out["pair"] == "EUR_USD"
    assert out["action"] == "SELL"
    assert out["stop_loss"] == 1.1050
    assert out["reasoning"] == {"x": 1}
    assert "timestamp" in out


def test_all_valid_actions_construct_without_error():
    for action in VALID_ACTIONS:
        Decision(pair="EUR_USD", action=action, confidence=None,
                 regime="UNKNOWN", model_version=None)
