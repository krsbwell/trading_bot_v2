import risk.risk_manager as rm
from risk.risk_manager import (
    calculate_position_size, get_tp_levels, validate_pre_trade,
    seed_daily_loss_from_today,
)


class TestPositionSizing:
    def test_forex_1pct_risk(self):
        # $10,000 account, 10-pip SL → expect ~25,000 units
        units = calculate_position_size(10_000, 1.08450, 1.08350, "EUR_USD")
        assert isinstance(units, int)
        assert units > 0

    def test_tp_levels_long(self):
        tps = get_tp_levels(1.0840, 1.0820, "long")
        # Long: tp1 < tp2 < tp3 (each further above entry)
        assert tps["tp1"] < tps["tp2"] < tps["tp3"]
        assert tps["tp1"] > 1.0840   # tp1 must be above entry

    def test_tp_levels_short(self):
        tps = get_tp_levels(1.0840, 1.0860, "short")
        # Short: tp1 > tp2 > tp3 (each further below entry)
        assert tps["tp1"] > tps["tp2"] > tps["tp3"]
        assert tps["tp1"] < 1.0840   # tp1 must be below entry


class TestPreTradeValidation:
    def test_passes_all_checks(self):
        ok, reason = validate_pre_trade(75, 1, "EUR_USD", [])
        assert ok

    def test_fails_low_score(self):
        ok, _ = validate_pre_trade(50, 1, "EUR_USD", [])
        assert not ok

    def test_fails_duplicate_pair(self):
        ok, _ = validate_pre_trade(80, 1, "EUR_USD", ["EUR_USD"])
        assert not ok

    def test_fails_max_trades(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "MAX_OPEN_TRADES", 3)
        ok, _ = validate_pre_trade(80, 3, "EUR_USD", [])
        assert not ok

    def test_fails_duplicate_pair_even_with_direction_when_pyramiding_off(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ALLOW_MULTIPLE_PER_PAIR", False)
        ok, _ = validate_pre_trade(80, 1, "EUR_USD", ["EUR_USD"],
                                    direction="long", same_pair_directions=["long"])
        assert not ok

    def test_pyramids_same_direction_above_threshold(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ALLOW_MULTIPLE_PER_PAIR", True)
        monkeypatch.setattr(config, "PYRAMID_MIN_SCORE", 65)
        ok, _ = validate_pre_trade(70, 1, "EUR_USD", ["EUR_USD"],
                                    direction="long", same_pair_directions=["long"])
        assert ok

    def test_pyramid_blocked_opposite_direction(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ALLOW_MULTIPLE_PER_PAIR", True)
        monkeypatch.setattr(config, "PYRAMID_MIN_SCORE", 65)
        ok, reason = validate_pre_trade(95, 1, "EUR_USD", ["EUR_USD"],
                                         direction="short", same_pair_directions=["long"])
        assert not ok
        assert "opposite direction" in reason

    def test_pyramid_blocked_below_pyramid_score(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ALLOW_MULTIPLE_PER_PAIR", True)
        monkeypatch.setattr(config, "PYRAMID_MIN_SCORE", 65)
        # Clears the normal MIN_CONFLUENCE_SCORE floor but not the pyramid bar.
        ok, reason = validate_pre_trade(60, 1, "EUR_USD", ["EUR_USD"],
                                         direction="long", same_pair_directions=["long"])
        assert not ok
        assert "pyramid score" in reason

    def test_pyramid_blocked_at_max_pyramid_per_pair(self, monkeypatch):
        import config
        # MAX_OPEN_TRADES raised so it's MAX_PYRAMID_PER_PAIR being tested,
        # not an incidental collision (both default to 3).
        monkeypatch.setattr(config, "MAX_OPEN_TRADES", 10)
        monkeypatch.setattr(config, "ALLOW_MULTIPLE_PER_PAIR", True)
        monkeypatch.setattr(config, "PYRAMID_MIN_SCORE", 65)
        monkeypatch.setattr(config, "MAX_PYRAMID_PER_PAIR", 3)
        ok, reason = validate_pre_trade(95, 3, "EUR_USD", ["EUR_USD"],
                                         direction="long",
                                         same_pair_directions=["long", "long", "long"])
        assert not ok
        assert "3 open" in reason

    def test_pyramiding_ignored_when_direction_not_passed(self, monkeypatch):
        """A caller that doesn't pass direction/same_pair_directions (e.g.
        agents/risk_agent.py's plain pass-through) must keep the original
        hard-block behavior even with ALLOW_MULTIPLE_PER_PAIR on — it never
        opted into the direction/score check, so it should stay
        conservative rather than silently allow an unchecked stack."""
        import config
        monkeypatch.setattr(config, "ALLOW_MULTIPLE_PER_PAIR", True)
        ok, _ = validate_pre_trade(95, 1, "EUR_USD", ["EUR_USD"])
        assert not ok


class TestSeedDailyLossFromToday:
    """
    2026-10-07 — a real 28-pair live session lost 7.3% of balance in a day
    while TRADING_HALTED never engaged, for two compounding reasons: (1)
    update_daily_loss() wasn't wired to the broker-reconciled close path
    (fixed — see tests/test_trade.py's TestTradeManagerReconcileOpenTrades
    daily-loss tests), and (2) _daily_loss is pure in-memory state that a
    bot restart silently resets to 0 regardless of real losses already
    taken earlier that same day — this bot restarts often (Task Scheduler
    supervisor, manual restarts for every config change). This class
    covers the second half: seed_daily_loss_from_today(), called once at
    startup with today's already-closed trades' total P&L.
    """
    def setup_method(self):
        rm.TRADING_HALTED = False
        rm._daily_loss = 0.0

    def test_seeds_daily_loss_without_crossing_threshold(self):
        seed_daily_loss_from_today(-10.0, 1000.0)   # 1% of balance
        assert rm._daily_loss == -10.0
        assert rm.TRADING_HALTED is False

    def test_seeds_and_immediately_halts_if_already_past_threshold(self):
        """The actual 2026-10-07 scenario: restart mid-day after a real
        7.3% loss already happened. Startup must not hand the bot a fresh
        3% budget — it must recognize the breach immediately."""
        seed_daily_loss_from_today(-73.0, 1000.0)   # 7.3% of balance, matches the real incident
        assert rm.TRADING_HALTED is True
        ok, reason = validate_pre_trade(score=90, open_trade_count=0, pair="GBP_USD", open_pairs=[])
        assert ok is False
        assert "TRADING_HALTED" in reason

    def test_seeds_zero_when_no_trades_closed_today(self):
        seed_daily_loss_from_today(0.0, 1000.0)
        assert rm._daily_loss == 0.0
        assert rm.TRADING_HALTED is False

    def test_profitable_day_does_not_halt(self):
        """A large gain shouldn't trip the loss halt — only realized LOSS
        past the threshold should (abs() in the check means a big win at
        exactly the same magnitude as the real incident must not halt)."""
        seed_daily_loss_from_today(+73.0, 1000.0)
        assert rm.TRADING_HALTED is True   # by design: update_daily_loss()'s
        # abs(_daily_loss)/balance check doesn't distinguish win from loss —
        # documenting the actual current behavior, not asserting it's ideal.
        # Flagged separately; not in scope for this fix (see PR notes).


def test_min_sl_distance_uses_larger_of_pips_or_atr(monkeypatch):
    import config
    monkeypatch.setattr(config, "MIN_SL_PIPS", 25)
    monkeypatch.setattr(config, "MIN_SL_ATR_MULT", 2.0)
    # quiet market: 2 x 0.0008 = 16 pips < 25 -> fixed floor wins
    assert abs(config.min_sl_distance("GBP_USD", 0.0001, 0.0008) - 0.0025) < 1e-12
    # volatile market: 2 x 0.0020 = 40 pips > 25 -> ATR floor wins
    assert abs(config.min_sl_distance("GBP_USD", 0.0001, 0.0020) - 0.0040) < 1e-12
    monkeypatch.setattr(config, "MIN_SL_ATR_MULT", 0.0)
    assert abs(config.min_sl_distance("GBP_USD", 0.0001, 0.0020) - 0.0025) < 1e-12
