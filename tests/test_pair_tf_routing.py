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
"""
import config
from backtest.runner import confirm_tf_ratio
from engine.strategy_dispatch import resolve_strategy


class TestPrimaryTfFor:
    def test_pairs_with_no_override_use_the_global_default(self):
        assert config.primary_tf_for("GBP_USD") == config.TIMEFRAMES["primary"]
        assert config.primary_tf_for("EUR_JPY") == config.TIMEFRAMES["primary"]
        assert config.primary_tf_for("SOME_UNLISTED_PAIR") == config.TIMEFRAMES["primary"]

    def test_the_three_switched_pairs_resolve_to_h4(self):
        for pair in ("NZD_USD", "GBP_CAD", "CHF_JPY"):
            assert config.primary_tf_for(pair) == "H4"

    def test_switched_pairs_have_a_daily_confirm_not_h4(self):
        """H4 can't confirm itself — these 3 pairs must have moved off the
        global H4 confirm default too, or primary == confirm is degenerate."""
        for pair in ("NZD_USD", "GBP_CAD", "CHF_JPY"):
            primary = config.primary_tf_for(pair)
            confirm = config.confirm_tf_for(pair)
            assert primary != confirm, (
                f"{pair}: primary and confirm TF are both {primary!r} — "
                "degenerate, confirm can't validate against itself"
            )
            assert confirm == "D"

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

    def test_m30_primary_h4_confirm_ratio_is_still_eight(self):
        # 240 / 30 = 8 — unchanged pairs must not regress
        assert confirm_tf_ratio("GBP_USD") == 8
        assert confirm_tf_ratio("EUR_JPY") == 8

    def test_no_pair_given_falls_back_to_global_timeframes(self):
        assert confirm_tf_ratio(None) == confirm_tf_ratio(pair=None)


class TestStrategyRoutingForSwitchedPairs:
    def test_the_three_switched_pairs_route_to_breakout_retest(self):
        from engine.strategy_breakout_retest import check_buy_signal as br_buy
        for pair in ("NZD_USD", "GBP_CAD", "CHF_JPY"):
            buy_fn, _, _, _ = resolve_strategy(pair)
            assert buy_fn is br_buy, f"{pair} should route to breakout_retest"

    def test_gbp_usd_still_routes_to_breakout_retest(self):
        from engine.strategy_breakout_retest import check_buy_signal as br_buy
        buy_fn, _, _, _ = resolve_strategy("GBP_USD")
        assert buy_fn is br_buy

    def test_eur_jpy_routes_to_default_ema_bounce_not_breakout_retest(self):
        """EUR_JPY was paused, not switched — it has no STRATEGY_OVERRIDE
        entry, so it should fall back to the default (ema_bounce), same as
        before this change. Confirms the pause didn't accidentally also
        reassign its strategy."""
        from engine.strategy_ema_cci_macd import check_buy_signal as ema_buy
        buy_fn, _, _, _ = resolve_strategy("EUR_JPY")
        assert buy_fn is ema_buy


class TestPairListsReflectThePause:
    def test_forex_pairs_has_four_not_five(self):
        assert len(config.FOREX_PAIRS) == 4
        assert "EUR_JPY" not in config.FOREX_PAIRS

    def test_eur_jpy_moved_to_watch_not_dropped_entirely(self):
        """Paused, not rejected — still gets signals shown, matching how
        USD_CAD/EUR_AUD were paused earlier in this project."""
        assert "EUR_JPY" in config.FOREX_WATCH

    def test_the_three_switched_pairs_are_still_in_forex_pairs(self):
        for pair in ("NZD_USD", "GBP_CAD", "CHF_JPY", "GBP_USD"):
            assert pair in config.FOREX_PAIRS
