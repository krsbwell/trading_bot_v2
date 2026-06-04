"""
Tests for alerts and dashboard state.
Audio tests check the graceful fallback path (no real audio device needed).
Dashboard state tests verify thread-safe read/write.
Visual alert tests check component structure.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import threading


# ═══════════════════════════════════════════════════════════════════════════════
# Audio alert
# ═══════════════════════════════════════════════════════════════════════════════

class TestAudioAlert:
    def test_module_imports(self):
        import alerts.audio_alert as aa
        assert hasattr(aa, "play_alert")
        assert hasattr(aa, "play_reminder")
        assert hasattr(aa, "is_ready")

    def test_play_alert_no_crash_without_audio(self):
        """play_alert must never raise even when pygame / files are missing."""
        import alerts.audio_alert as aa
        aa._mixer_ready = False   # force re-init attempt
        aa._buy_sound   = None
        aa._sell_sound  = None
        aa.play_alert("long")     # should not raise
        aa.play_alert("short")

    def test_play_reminder_no_crash(self):
        import alerts.audio_alert as aa
        aa.play_reminder("long")  # should not raise

    def test_is_ready_returns_bool(self):
        import alerts.audio_alert as aa
        result = aa.is_ready()
        assert isinstance(result, bool)


# ═══════════════════════════════════════════════════════════════════════════════
# Visual alert — Dash component structure
# ═══════════════════════════════════════════════════════════════════════════════

class TestVisualAlert:
    def _signal(self):
        return {
            "pair":           "EUR_USD",
            "direction":      "long",
            "entry":          1.0800,
            "stop_loss":      1.0780,
            "tp_levels":      {"tp1": 1.0820, "tp2": 1.0840, "tp3": 1.0860},
            "size":           50000,
            "risk_dollar":    100.0,
            "score":          84,
            "ema_score":      25,
            "structure_score": 35,
            "pa_score":       24,
            "patterns":       ["bullish_pin_bar", "hammer"],
        }

    def test_build_overlay_returns_html_div(self):
        from dash import html
        from alerts.visual_alert import build_alert_overlay
        overlay = build_alert_overlay(self._signal(), countdown=8)
        assert isinstance(overlay, html.Div)

    def test_overlay_has_correct_id(self):
        from alerts.visual_alert import build_alert_overlay
        overlay = build_alert_overlay(self._signal(), countdown=8)
        assert overlay.id == "alert-overlay"

    def test_border_pulse_long(self):
        from alerts.visual_alert import border_pulse_class
        assert border_pulse_class("long")  == "pulse-long"

    def test_border_pulse_short(self):
        from alerts.visual_alert import border_pulse_class
        assert border_pulse_class("short") == "pulse-short"

    def test_overlay_short_signal(self):
        from dash import html
        from alerts.visual_alert import build_alert_overlay
        sig = self._signal()
        sig["direction"] = "short"
        overlay = build_alert_overlay(sig, countdown=3)
        assert isinstance(overlay, html.Div)

    def test_countdown_zero_shows_now(self):
        from alerts.visual_alert import build_alert_overlay
        # Should not raise
        build_alert_overlay(self._signal(), countdown=0)


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard state — thread-safe store
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardState:
    def setup_method(self):
        from dashboard import state
        state.clear_alert()
        state.update(halted=False)

    def test_get_returns_dict(self):
        from dashboard import state
        s = state.get()
        assert isinstance(s, dict)

    def test_get_key_returns_value(self):
        from dashboard import state
        state.update(mode="paper")
        assert state.get_key("mode") == "paper"

    def test_get_key_missing_returns_default(self):
        from dashboard import state
        assert state.get_key("nonexistent_key", "default") == "default"

    def test_update_persists(self):
        from dashboard import state
        state.update(halted=True)
        assert state.get_key("halted") is True
        state.update(halted=False)

    def test_set_pending_alert(self):
        from dashboard import state
        import config
        sig = {"pair": "EUR_USD", "direction": "long", "score": 80}
        state.set_pending_alert(sig)
        assert state.get_key("pending_alert") is not None
        assert state.get_key("alert_countdown") == config.ALERT_DELAY_SECONDS
        assert state.get_key("alert_confirmed") is False
        state.clear_alert()

    def test_clear_alert(self):
        from dashboard import state
        sig = {"pair": "EUR_USD", "direction": "long"}
        state.set_pending_alert(sig)
        state.clear_alert()
        assert state.get_key("pending_alert") is None
        assert state.get_key("alert_countdown") == 0

    def test_tick_countdown_decrements(self):
        from dashboard import state
        sig = {"pair": "EUR_USD", "direction": "long"}
        state.set_pending_alert(sig)
        import config
        initial = config.ALERT_DELAY_SECONDS
        new_val  = state.tick_countdown()
        assert new_val == initial - 1
        state.clear_alert()

    def test_tick_countdown_floors_at_zero(self):
        from dashboard import state
        state.update(alert_countdown=0)
        assert state.tick_countdown() == 0

    def test_update_signal(self):
        from dashboard import state
        state.update_signal("EUR_USD", 80, "long")
        sig = state.get_key("signals", {}).get("EUR_USD", {})
        assert sig["score"]     == 80
        assert sig["direction"] == "long"
        assert sig["status"]    == "SIGNAL"

    def test_update_signal_watching(self):
        from dashboard import state
        state.update_signal("GBP_USD", 60, "short")
        sig = state.get_key("signals", {}).get("GBP_USD", {})
        assert sig["status"] == "WATCHING"

    def test_update_signal_neutral(self):
        from dashboard import state
        state.update_signal("EUR_USD", 30, "—")
        sig = state.get_key("signals", {}).get("EUR_USD", {})
        assert sig["status"] == "NEUTRAL"

    def test_cache_and_retrieve_candles(self):
        from dashboard import state
        import pandas as pd
        df = pd.DataFrame({"open": [1], "close": [1], "high": [1], "low": [1]})
        state.cache_candles("EUR_USD", "H1", df)
        result = state.get_candles("EUR_USD", "H1")
        assert result is not None
        assert len(result) == 1

    def test_thread_safe_concurrent_writes(self):
        from dashboard import state

        errors = []
        def worker(i):
            try:
                state.update(halted=(i % 2 == 0))
                state.get()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert errors == []


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard panels — component structure
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardPanels:
    def _signals(self):
        return {
            "EUR_USD": {"score": 82, "direction": "long",  "status": "SIGNAL"},
            "GBP_USD": {"score": 55, "direction": "short", "status": "WATCHING"},
            "AUD_USD": {"score": 20, "direction": "—",     "status": "NEUTRAL"},
        }

    def _account(self):
        return {"balance": 10_000, "nav": 10_050, "unrealized_pnl": 50,
                "open_trade_count": 1}

    def test_signal_monitor_returns_div(self):
        from dash import html
        from dashboard.panels import signal_monitor_panel
        panel = signal_monitor_panel(self._signals())
        assert isinstance(panel, html.Div)

    def test_open_trades_empty(self):
        from dash import html
        from dashboard.panels import open_trades_panel
        panel = open_trades_panel([], "paper")
        assert isinstance(panel, html.Div)

    def test_open_trades_with_trade(self):
        from dash import html
        from dashboard.panels import open_trades_panel
        trades = [{"id": "T1", "pair": "EUR_USD", "direction": "long",
                   "entry": 1.08, "tp1": 1.082, "tp2": 1.084, "tp3": 1.086,
                   "sl": 1.078, "size": 50000, "remaining": 1.0, "realised_pnl": 0}]
        panel = open_trades_panel(trades, "paper")
        assert isinstance(panel, html.Div)

    def test_account_stats_bar(self):
        from dash import html
        from dashboard.panels import account_stats_bar
        bar = account_stats_bar(self._account(), 50.0, 0.65, 0.60, 3, "paper")
        assert isinstance(bar, html.Div)

    def test_trade_log_drawer(self):
        from dash import html
        from dashboard.panels import trade_log_drawer
        drawer = trade_log_drawer([], [], {}, visible=False)
        assert isinstance(drawer, html.Div)

    def test_trade_log_with_data(self):
        from dash import html
        from dashboard.panels import trade_log_drawer
        trades = [{"pair": "EUR_USD", "direction": "long", "entry": 1.08,
                   "exit_price": 1.082, "realised_pnl": 100, "pattern_name": "hammer",
                   "score": 80, "close_reason": "tp1"}]
        suggestions = [{"type": "boost", "text": "Good pattern", "dismissible": True}]
        ml_stats    = {"accuracy": 0.72, "top_features": ["confluence_score", "ema_score"], "n_samples": 60}
        drawer = trade_log_drawer(trades, suggestions, ml_stats, visible=True)
        assert isinstance(drawer, html.Div)
