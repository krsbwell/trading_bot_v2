"""
run_walk_forward()'s avg_pf_oos/avg_stability — fixed 2026-08-13. Was a flat
mean across windows regardless of trade count (found 2026-08-06, tasks/todo.md
"WFO headline metrics vs real live performance", Finding 1): a 2-trade window
that got lucky (PF 15-19) got equal weight to an 8-trade window sitting at
PF ~1, so the dashboard's headline "Avg OOS PF" card could read as a strong
edge when most of the real evidence said otherwise — exactly what showed up
in real dashboard screenshots the user shared (NZD_USD "Avg OOS PF 7.22"
driven by two ~2-trade windows next to two total-wipeout windows).

run_backtest() is monkeypatched at the module boundary so each window's
IS/OOS trades are exactly controlled — precisely testing real strategy
output through a synthetic price series can't guarantee specific PF/trade-
count combinations, and the averaging math is the only thing under test
here, not the strategies themselves.
"""
import numpy as np
import pandas as pd
import pytest

import backtest.runner as bt


def _df(n):
    idx = pd.date_range("2024-01-01", periods=n, freq="30min")
    close = np.linspace(1.0800, 1.0850, n)
    return pd.DataFrame({"open": close, "high": close + 0.0005,
                         "low": close - 0.0005, "close": close}, index=idx)


def _trades(pnls):
    return [{"realised_pnl": p} for p in pnls]


class TestWalkForwardWeightedAveraging:
    def test_avg_pf_oos_weighted_by_trade_count_not_flat_mean(self, monkeypatch):
        """2 windows: window 1 has 2 OOS trades at PF 19.0 (a lucky outlier),
        window 2 has 8 OOS trades at PF 1.0 (roughly breakeven, most of the
        real evidence). A flat mean would report 10.0 — the fix should
        report the trade-weighted 4.6, which is much closer to what the
        higher-sample-size window actually showed."""
        calls = [
            {"total_trades": 3, "trades": _trades([4, 2, -3])},                    # IS window 1, PF=2.0
            {"total_trades": 2, "trades": _trades([95, -5])},                      # OOS window 1, PF=19.0
            {"total_trades": 4, "trades": _trades([2, -2, 1, -1])},                # IS window 2, PF=1.0
            {"total_trades": 8, "trades": _trades([1, 1, 1, 1, -1, -1, -1, -1])},  # OOS window 2, PF=1.0
        ]
        call_iter = iter(calls)

        def _stub_run_backtest(pair, df_h1, df_h4, **kwargs):
            res = dict(next(call_iter))
            res["win_rate"] = 0.5
            return res

        monkeypatch.setattr(bt, "run_backtest", _stub_run_backtest)

        df_h1 = _df(30)   # train=10, test=10, step=10 -> exactly 2 windows
        df_h4 = _df(30)
        result = bt.run_walk_forward(
            "EUR_USD", df_h1, df_h4,
            train_bars=10, test_bars=10, step_bars=10,
            param_grid={"min_score": [50]},   # exactly 1 IS + 1 OOS call per window
        )

        assert len(result["windows"]) == 2
        assert result["windows"][0]["pf_oos"] == 19.0
        assert result["windows"][0]["trade_count_oos"] == 2
        assert result["windows"][1]["pf_oos"] == 1.0
        assert result["windows"][1]["trade_count_oos"] == 8

        # Old flat-mean behavior would give (19.0 + 1.0) / 2 = 10.0.
        assert result["avg_pf_oos"] != 10.0
        # New weighted behavior: (19.0*2 + 1.0*8) / 10 = 4.6
        assert result["avg_pf_oos"] == 4.6

        # Stability: window 1 = 19.0/2.0 = 9.5, window 2 = 1.0/1.0 = 1.0
        # Flat mean would be 5.25; weighted: (9.5*2 + 1.0*8)/10 = 2.7
        assert result["avg_stability"] == 2.7

    def test_zero_trade_window_contributes_nothing_not_scored_as_pf_zero(self, monkeypatch):
        """A window with 0 OOS trades isn't evidence of a bad edge — it's no
        evidence at all. It must not drag the average down as if it were a
        real PF-0.00 result."""
        calls = [
            {"total_trades": 3, "trades": _trades([4, 2, -3])},   # IS window 1
            {"total_trades": 0, "trades": []},                    # OOS window 1 — no trades at all
            {"total_trades": 4, "trades": _trades([2, -2, 1, -1])},  # IS window 2
            {"total_trades": 5, "trades": _trades([3, 2, 1, -1, -1])},  # OOS window 2, PF=6.0
        ]
        call_iter = iter(calls)

        def _stub_run_backtest(pair, df_h1, df_h4, **kwargs):
            res = dict(next(call_iter))
            res["win_rate"] = 0.5
            return res

        monkeypatch.setattr(bt, "run_backtest", _stub_run_backtest)

        df_h1 = _df(30)
        df_h4 = _df(30)
        result = bt.run_walk_forward(
            "EUR_USD", df_h1, df_h4,
            train_bars=10, test_bars=10, step_bars=10,
            param_grid={"min_score": [50]},
        )

        assert result["windows"][0]["trade_count_oos"] == 0
        assert result["windows"][0]["pf_oos"] == 0.0
        # Only window 2's 5 trades carry any weight -> avg == window 2's own PF exactly.
        assert result["avg_pf_oos"] == result["windows"][1]["pf_oos"]

    def test_all_windows_zero_trades_no_crash_no_misleading_average(self, monkeypatch):
        calls = [
            {"total_trades": 3, "trades": _trades([4, 2, -3])},
            {"total_trades": 0, "trades": []},
            {"total_trades": 4, "trades": _trades([2, -2, 1, -1])},
            {"total_trades": 0, "trades": []},
        ]
        call_iter = iter(calls)

        def _stub_run_backtest(pair, df_h1, df_h4, **kwargs):
            res = dict(next(call_iter))
            res["win_rate"] = 0.0
            return res

        monkeypatch.setattr(bt, "run_backtest", _stub_run_backtest)

        df_h1 = _df(30)
        df_h4 = _df(30)
        result = bt.run_walk_forward(
            "EUR_USD", df_h1, df_h4,
            train_bars=10, test_bars=10, step_bars=10,
            param_grid={"min_score": [50]},
        )
        assert result["avg_pf_oos"] == 0.0
        assert result["avg_stability"] == 0.0
