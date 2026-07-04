"""
Regression tests for the 2026-07-03 silent-failure incident (bot went dark
for hours — no trades, no logs, no visible symptom besides a flat equity
curve). See bugs_scheduler_reliability memory for the full root-cause
writeup. These tests target the 2 bugs that lived in main.py; the 3rd
(missing OANDA HTTP timeout) is covered in test_connectors.py.

Scope note: these tests raise confidence in these 2 specific failure modes.
They do not, and cannot, prove main.py has no other bugs — most of it still
has no test coverage at all.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# Bug 1 — one pair's exception must not skip every pair after it in the loop
# ═══════════════════════════════════════════════════════════════════════════════

class TestScanForexPairsExceptionIsolation:
    def test_exception_on_one_pair_does_not_skip_later_pairs(self, monkeypatch):
        import main

        calls = []

        def _fake_process_pair(pair, market, engine):
            calls.append(pair)
            if pair == "EUR_AUD":
                raise RuntimeError("simulated failure, e.g. a hung/bad API response")

        monkeypatch.setattr(main, "_process_pair", _fake_process_pair)
        monkeypatch.setattr(main.state, "record_pair_scan", lambda pair: None)

        # This is the exact pattern from the incident: pairs after the one
        # that throws (GBP_CAD, GBP_USD) must still be attempted.
        main._scan_forex_pairs(["USD_CAD", "NZD_USD", "EUR_AUD", "GBP_CAD", "GBP_USD"], engine=object())

        assert calls == ["USD_CAD", "NZD_USD", "EUR_AUD", "GBP_CAD", "GBP_USD"], (
            "every pair must be attempted even though EUR_AUD raised — "
            "this loop previously had no exception isolation at all, so an "
            "exception on any pair silently skipped every pair after it"
        )

    def test_successful_pairs_are_recorded_in_scan_state(self, monkeypatch):
        """
        record_pair_scan is what the dashboard's bot-health indicator reads —
        a pair that fails should NOT be recorded as successfully scanned
        (that would hide the failure from the health indicator too).
        """
        import main

        recorded = []

        def _fake_process_pair(pair, market, engine):
            if pair == "GBP_CAD":
                raise RuntimeError("simulated failure")

        monkeypatch.setattr(main, "_process_pair", _fake_process_pair)
        monkeypatch.setattr(main.state, "record_pair_scan", lambda pair: recorded.append(pair))

        main._scan_forex_pairs(["USD_CAD", "GBP_CAD", "GBP_USD"], engine=object())

        assert recorded == ["USD_CAD", "GBP_USD"], (
            "GBP_CAD raised, so it must not be marked as successfully scanned"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Bug 2 — trending-structure gate must only apply to ema_bounce pairs
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrendingStructureGateScoping:
    def test_blocks_ema_bounce_pair_in_trending_structure(self, monkeypatch):
        import main, config
        monkeypatch.setattr(config, "STRATEGY_OVERRIDE", {}, raising=False)

        assert main._trending_structure_gate_applies("USD_CAD", "uptrend") is True
        assert main._trending_structure_gate_applies("USD_CAD", "downtrend") is True

    def test_does_not_block_ema_bounce_pair_when_ranging(self, monkeypatch):
        import main, config
        monkeypatch.setattr(config, "STRATEGY_OVERRIDE", {}, raising=False)

        assert main._trending_structure_gate_applies("USD_CAD", "ranging") is False

    def test_does_not_block_breakout_retest_pair_in_trending_structure(self, monkeypatch):
        """
        The actual bug: breakout-retest's entire premise is trading a break
        of structure INTO a trend. Before this fix, GBP_USD's legitimate
        breakout-retest signals were blocked by a gate written for a
        different strategy entirely.
        """
        import main, config
        monkeypatch.setattr(config, "STRATEGY_OVERRIDE", {"GBP_USD": "breakout_retest"}, raising=False)

        assert main._trending_structure_gate_applies("GBP_USD", "uptrend") is False
        assert main._trending_structure_gate_applies("GBP_USD", "downtrend") is False

    def test_still_blocks_other_ema_bounce_pairs_when_override_exists_elsewhere(self, monkeypatch):
        """A STRATEGY_OVERRIDE entry for one pair must not accidentally exempt every pair."""
        import main, config
        monkeypatch.setattr(config, "STRATEGY_OVERRIDE", {"GBP_USD": "breakout_retest"}, raising=False)

        assert main._trending_structure_gate_applies("GBP_CAD", "uptrend") is True
