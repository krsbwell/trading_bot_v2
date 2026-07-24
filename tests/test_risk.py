from risk.risk_manager import calculate_position_size, get_tp_levels, validate_pre_trade


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

    def test_fails_max_trades(self):
        ok, _ = validate_pre_trade(80, 3, "EUR_USD", [])
        assert not ok
