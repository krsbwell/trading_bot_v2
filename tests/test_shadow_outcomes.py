"""
Tests for learning/shadow_outcomes.py — the resolver that walks forward
through real candles to label 'skipped' signal_log.csv rows as
would_win/would_lose/expired, and (as of 2026-07-24) also fills in pnl_pips
for those resolved rows.
"""
import os
import pandas as pd
import pytest

from learning.shadow_outcomes import resolve_pending, _resolve_one

SIGNAL_LOG_HEADER = [
    "timestamp", "pair", "direction", "entry_price", "stop_loss", "tp1",
    "timeframe_primary", "outcome", "pnl_pips",
]


def _candles(start="2026-01-01 00:00:00", n=20, freq="30min", base=1.0800, **col_overrides):
    """Flat M30 candles at `base` (inside any reasonable SL/TP band used in
    these tests) unless overridden per-index."""
    idx = pd.date_range(start, periods=n, freq=freq)
    df = pd.DataFrame({"open": base, "high": base, "low": base, "close": base}, index=idx)
    for col, values in col_overrides.items():
        for i, v in values.items():
            df.loc[idx[i], col] = v
    return df


def _row(pair="EUR_USD", direction="long", entry=1.0800, sl=1.0780, tp1=1.0820,
          timestamp="2026-01-01T00:00:00"):
    return pd.Series({
        "pair": pair, "direction": direction, "entry_price": entry,
        "stop_loss": sl, "tp1": tp1, "timeframe_primary": "M30",
        "timestamp": timestamp,
    })


class FakeConnector:
    def __init__(self, candles: pd.DataFrame):
        self._candles = candles

    def get_candles(self, pair, granularity, count):
        return self._candles


class TestResolveOne:
    def test_would_win_long_computes_correct_pnl_pips(self):
        # TP1=1.0820 touched at index 2 (high=1.0825), well before SL.
        candles = _candles(high={2: 1.0825})
        outcome, pips = _resolve_one(_row(direction="long", entry=1.0800, tp1=1.0820),
                                      FakeConnector(candles), {})
        assert outcome == "would_win"
        assert pips == pytest.approx(20.0)   # (1.0820 - 1.0800) / 0.0001

    def test_would_lose_long_computes_correct_pnl_pips(self):
        candles = _candles(low={2: 1.0775})
        outcome, pips = _resolve_one(_row(direction="long", entry=1.0800, sl=1.0780),
                                      FakeConnector(candles), {})
        assert outcome == "would_lose"
        assert pips == pytest.approx(-20.0)  # (1.0780 - 1.0800) / 0.0001

    def test_would_win_short_computes_correct_pnl_pips(self):
        candles = _candles(low={2: 1.0775})
        outcome, pips = _resolve_one(
            _row(direction="short", entry=1.0800, sl=1.0820, tp1=1.0780),
            FakeConnector(candles), {})
        assert outcome == "would_win"
        assert pips == pytest.approx(20.0)   # (1.0800 - 1.0780) / 0.0001

    def test_sl_and_tp_same_candle_sl_wins(self):
        """SL-first convention when both are touched in the same candle."""
        candles = _candles(high={2: 1.0825}, low={2: 1.0775})
        outcome, pips = _resolve_one(_row(direction="long"), FakeConnector(candles), {})
        assert outcome == "would_lose"
        assert pips == pytest.approx(-20.0)

    def test_incomplete_row_expires_with_no_pips(self):
        row = _row()
        row["tp1"] = float("nan")
        outcome, pips = _resolve_one(row, FakeConnector(_candles()), {})
        assert outcome == "expired"
        assert pips is None

    def test_neither_level_touched_expires_with_no_pips(self):
        candles = _candles(n=200)   # flat candles, never touches SL or TP1
        outcome, pips = _resolve_one(_row(), FakeConnector(candles), {})
        assert outcome == "expired"
        assert pips is None

    def test_no_candles_after_signal_returns_unresolved(self):
        """Signal too recent — nothing to walk forward through yet."""
        candles = _candles(start="2020-01-01 00:00:00", n=5)
        outcome, pips = _resolve_one(
            _row(timestamp="2026-01-01T00:00:00"), FakeConnector(candles), {})
        assert outcome is None
        assert pips is None


class TestResolvePending:
    def test_resolve_pending_writes_outcome_and_pnl_pips(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        df = pd.DataFrame([{
            **{k: "" for k in SIGNAL_LOG_HEADER},
            "timestamp": "2026-01-01T00:00:00", "pair": "EUR_USD",
            "direction": "long", "entry_price": 1.0800, "stop_loss": 1.0780,
            "tp1": 1.0820, "timeframe_primary": "M30", "outcome": "skipped",
            "pnl_pips": 0.0,
        }])
        df.to_csv(log, index=False)

        candles = _candles(high={2: 1.0825})
        resolved = resolve_pending(FakeConnector(candles), log_path=log)

        assert resolved == 1
        out = pd.read_csv(log)
        assert out.iloc[0]["outcome"] == "would_win"
        assert out.iloc[0]["pnl_pips"] == pytest.approx(20.0)

    def test_resolve_pending_no_file_returns_zero(self, tmp_path):
        log = str(tmp_path / "does_not_exist.csv")
        assert resolve_pending(FakeConnector(_candles()), log_path=log) == 0

    def test_resolve_pending_leaves_unresolved_rows_untouched(self, tmp_path):
        log = str(tmp_path / "signal_log.csv")
        df = pd.DataFrame([{
            **{k: "" for k in SIGNAL_LOG_HEADER},
            "timestamp": "2026-01-01T00:00:00", "pair": "EUR_USD",
            "direction": "long", "entry_price": 1.0800, "stop_loss": 1.0780,
            "tp1": 1.0820, "timeframe_primary": "M30", "outcome": "skipped",
            "pnl_pips": 0.0,
        }])
        df.to_csv(log, index=False)

        # No candles after the signal time -> stays unresolved, still "skipped".
        candles = _candles(start="2020-01-01 00:00:00", n=5)
        resolved = resolve_pending(FakeConnector(candles), log_path=log)

        assert resolved == 0
        out = pd.read_csv(log)
        assert out.iloc[0]["outcome"] == "skipped"
        assert out.iloc[0]["pnl_pips"] == 0.0
