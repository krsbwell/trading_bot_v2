"""
2026-08-13 strategy overhaul — tests for the new PRIMARY_TF_PER_PAIR
mechanism and the pair/strategy routing changes made alongside it (see
tasks/todo.md for the full investigation: exhaustive WFO search found no
validated edge for any of the 5 then-active pairs on EMA-bounce/M30;
breakout_retest survived a real fit/holdout split at H4 on 6 of 8 pairs
tried; NZD_USD/GBP_CAD/CHF_JPY switched to breakout_retest at H4, EUR_JPY
paused pending real evidence).

primary_tf_for() mirrors the already-existing confirm_tf_for() pattern
(config.py, CONFIRM_TF_PER_PAIR) — no dedicated tests existed for that
one either, so this file establishes the pattern for both going forward.

2026-08-16 update: completed the holdout treatment the 08-13 decision left
unfinished. AUD_JPY promoted onto breakout_retest@H4; EUR_AUD promoted
onto trend_retest@H4 (its first-ever live pair); CHF_JPY switched from
breakout_retest to trend_retest@H4 (its own holdout numbers favored
trend_retest in two independent runs 4 days apart). See tasks/todo.md.
"""
import config
from backtest.runner import confirm_tf_ratio
from engine.strategy_dispatch import resolve_strategy


class TestPrimaryTfFor:
    def test_pairs_with_no_override_use_the_global_default(self):
        assert config.primary_tf_for("GBP_USD") == config.TIMEFRAMES["primary"]
        assert config.primary_tf_for("EUR_JPY") == config.TIMEFRAMES["primary"]
        assert config.primary_tf_for("SOME_UNLISTED_PAIR") == config.TIMEFRAMES["primary"]

    def test_the_five_switched_pairs_resolve_to_h4(self):
        for pair in ("NZD_USD", "GBP_CAD", "CHF_JPY", "AUD_JPY", "EUR_AUD"):
            assert config.primary_tf_for(pair) == "H4"

    def test_switched_pairs_have_a_daily_confirm_not_h4(self):
        """H4 can't confirm itself — these pairs must have moved off the
        global H4 confirm default too, or primary == confirm is degenerate."""
        for pair in ("NZD_USD", "GBP_CAD", "CHF_JPY", "AUD_JPY", "EUR_AUD"):
            primary = config.primary_tf_for(pair)
            confirm = config.confirm_tf_for(pair)
            assert primary != confirm, (
                f"{pair}: primary and confirm TF are both {primary!r} — "
                "degenerate, confirm can't validate against itself"
            )
            assert confirm == "D"

    def test_eur_aud_no_longer_has_the_old_h1_override(self):
        """2026-08-16: EUR_AUD's 07-23 M30-era H1 confirm override was
        replaced by "D" now that it runs H4 primary — the old M30/H1
        finding doesn't apply to a completely different strategy+TF."""
        assert config.confirm_tf_for("EUR_AUD") == "D"

    def test_gbp_usd_unchanged_still_m30_h4(self):
        """The one pair with a real, longstanding live track record —
        deliberately untouched by this overhaul."""
        assert config.primary_tf_for("GBP_USD") == "M30"
        assert config.confirm_tf_for("GBP_USD") == "H4"


class TestConfirmTfRatioRespectsPerPairPrimary:
    """confirm_tf_ratio() computes primary-TF-bars-per-confirm-TF-bar — used
    for WFO window alignment and the dashboard's bar-count fetch. Before
    2026-08-13 it always divided by the *global* primary TF regardless of
    which pair was asked about; for a pair now running H4 primary / D
    confirm, that would have silently used M30's minute count instead of
    H4's, misaligning every window."""

    def test_h4_primary_daily_confirm_ratio_is_six(self):
        # 1440 min/day / 240 min/H4-bar = 6
        assert confirm_tf_ratio("NZD_USD") == 6
        assert confirm_tf_ratio("GBP_CAD") == 6
        assert confirm_tf_ratio("CHF_JPY") == 6
        assert confirm_tf_ratio("AUD_JPY") == 6
        assert confirm_tf_ratio("EUR_AUD") == 6

    def test_m30_primary_h4_confirm_ratio_is_still_eight(self):
        # 240 / 30 = 8 — unchanged pairs must not regress
        assert confirm_tf_ratio("GBP_USD") == 8
        assert confirm_tf_ratio("EUR_JPY") == 8

    def test_no_pair_given_falls_back_to_global_timeframes(self):
        assert confirm_tf_ratio(None) == confirm_tf_ratio(pair=None)


class TestStrategyRoutingForSwitchedPairs:
    def test_nzd_gbpcad_gbpusd_aud_jpy_route_to_breakout_retest(self):
        from engine.strategy_breakout_retest import check_buy_signal as br_buy
        for pair in ("NZD_USD", "GBP_CAD", "GBP_USD", "AUD_JPY"):
            buy_fn, _, _, _ = resolve_strategy(pair)
            assert buy_fn is br_buy, f"{pair} should route to breakout_retest"

    def test_chf_jpy_and_eur_aud_route_to_trend_retest(self):
        """2026-08-16: CHF_JPY switched off breakout_retest (its own
        holdout numbers favored trend_retest in two independent runs);
        EUR_AUD promoted straight onto trend_retest — its first-ever live
        pair. See tasks/todo.md."""
        from engine.strategy_trend_retest import check_buy_signal as tr_buy
        for pair in ("CHF_JPY", "EUR_AUD"):
            buy_fn, _, _, _ = resolve_strategy(pair)
            assert buy_fn is tr_buy, f"{pair} should route to trend_retest"

    def test_eur_jpy_routes_to_default_ema_bounce_not_breakout_retest(self):
        """EUR_JPY was paused, not switched — it has no STRATEGY_OVERRIDE
        entry, so it should fall back to the default (ema_bounce), same as
        before this change. Confirms the pause didn't accidentally also
        reassign its strategy."""
        from engine.strategy_ema_cci_macd import check_buy_signal as ema_buy
        buy_fn, _, _, _ = resolve_strategy("EUR_JPY")
        assert buy_fn is ema_buy


class TestPairListsReflectThePause:
    def test_forex_pairs_has_six_not_four(self):
        """2026-08-16: AUD_JPY and EUR_AUD promoted off watch — see
        tasks/todo.md for the holdout data behind both."""
        assert len(config.FOREX_PAIRS) == 6
        assert "EUR_JPY" not in config.FOREX_PAIRS

    def test_eur_jpy_moved_to_watch_not_dropped_entirely(self):
        """Paused, not rejected — still gets signals shown, matching how
        USD_CAD was paused earlier in this project."""
        assert "EUR_JPY" in config.FOREX_WATCH

    def test_the_five_switched_pairs_are_still_in_forex_pairs(self):
        for pair in ("NZD_USD", "GBP_CAD", "CHF_JPY", "GBP_USD", "AUD_JPY", "EUR_AUD"):
            assert pair in config.FOREX_PAIRS

    def test_aud_jpy_and_eur_aud_no_longer_on_watch(self):
        assert "AUD_JPY" not in config.FOREX_WATCH
        assert "EUR_AUD" not in config.FOREX_WATCH


class TestTrendRetestParamsPerPair:
    """2026-08-16 — trend_retest's first-ever live wiring. Verifies its
    tuned per-pair knobs actually reach the live signal path and the
    backtest auto-resolve path, and don't leak onto pairs running a
    different strategy (same clobber-risk class as the 2026-07-20 WFO/
    adaptive-params merge-override bug — see bugs_wfo_adaptive_merge_override
    memory)."""

    def test_chf_jpy_and_eur_aud_have_tuned_params(self):
        assert config.TREND_RETEST_PARAMS_PER_PAIR["CHF_JPY"] == {
            "h4_bias_period": 20, "retest_band_mult": 0.30,
        }
        assert config.TREND_RETEST_PARAMS_PER_PAIR["EUR_AUD"] == {
            "h4_bias_period": 20, "retest_band_mult": 0.45,
        }

    def test_backtest_runner_applies_tuned_params_on_auto_resolve(self, monkeypatch):
        """run_backtest()'s auto-resolve path (buy_fn/sell_fn/adaptive all
        omitted, matching how the dashboard's Backtest button calls it)
        must pass CHF_JPY's tuned combo into trend_retest, not its bare
        defaults (h4_bias_period=50, retest_band_mult=0.3)."""
        import backtest.runner as runner_mod
        seen = {}

        def _spy_buy(pair, df_h1, df_h4, adaptive=None):
            seen["adaptive"] = adaptive
            return 0.0

        monkeypatch.setattr(
            "engine.strategy_dispatch.resolve_strategy",
            lambda pair: (_spy_buy, _spy_buy, lambda *a, **k: 0.0, lambda *a, **k: {}),
        )

        import pandas as pd
        import numpy as np
        idx = pd.date_range("2026-01-01", periods=300, freq="4h")
        df = pd.DataFrame({
            "open": np.linspace(1.0, 1.1, 300), "high": np.linspace(1.001, 1.101, 300),
            "low": np.linspace(0.999, 1.099, 300), "close": np.linspace(1.0, 1.1, 300),
        }, index=idx)

        runner_mod.run_backtest("CHF_JPY", df, df)

        assert seen["adaptive"] == {"h4_bias_period": 20, "retest_band_mult": 0.30}

    def test_explicit_adaptive_dict_is_not_overridden(self, monkeypatch):
        """A caller that explicitly passes adaptive= (e.g. the 08-16 grid
        search) keeps full control — the auto-default must not clobber it."""
        import backtest.runner as runner_mod
        seen = {}

        def _spy_buy(pair, df_h1, df_h4, adaptive=None):
            seen["adaptive"] = adaptive
            return 0.0

        monkeypatch.setattr(
            "engine.strategy_dispatch.resolve_strategy",
            lambda pair: (_spy_buy, _spy_buy, lambda *a, **k: 0.0, lambda *a, **k: {}),
        )

        import pandas as pd
        import numpy as np
        idx = pd.date_range("2026-01-01", periods=300, freq="4h")
        df = pd.DataFrame({
            "open": np.linspace(1.0, 1.1, 300), "high": np.linspace(1.001, 1.101, 300),
            "low": np.linspace(0.999, 1.099, 300), "close": np.linspace(1.0, 1.1, 300),
        }, index=idx)

        runner_mod.run_backtest("CHF_JPY", df, df, adaptive={"h4_bias_period": 99})

        assert seen["adaptive"] == {"h4_bias_period": 99}

    def test_params_dont_leak_onto_a_breakout_retest_pair(self):
        """AUD_JPY runs breakout_retest, not trend_retest — its resolved
        strategy shouldn't get trend_retest's params merged in."""
        assert config.STRATEGY_OVERRIDE.get("AUD_JPY") != "trend_retest"
