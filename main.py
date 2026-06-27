"""
Trading Bot — entry point.

Paper mode (default):   python main.py
Live mode:              set MODE = "live" in config.py, then python main.py

Dashboard opens at:     http://localhost:8050
"""
import logging
import threading
import time
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from connectors.oanda_connector import OandaConnector
from connectors.alpaca_connector import AlpacaConnector
from connectors.news_connector import is_news_blackout
from connectors.forexfactory_connector import is_news_blackout as ff_is_news_blackout
from engine.signal_engine import SignalEngine
from engine.adaptive_params import adaptive_params
from engine.wfo_optimizer import wfo_optimizer
from trade.paper_trader import PaperTrader
from trade.trade_manager import TradeManager
from risk.risk_manager import (
    validate_pre_trade, calculate_position_size,
    update_daily_loss, reset_daily_state, TRADING_HALTED,
)
from learning.data_collector import record_signal, record_close, record_skip, clear_pending
from learning.pattern_learner import PatternLearner
from learning.feedback_loop import generate_suggestions, should_run as feedback_should_run
from alerts.audio_alert import play_alert
from alerts.telegram_alert import (
    send_signal as tg_signal,
    send_trade_opened as tg_trade_opened,
    send_trade_closed as tg_trade_closed,
    send_drawdown_halt as tg_halt,
    is_configured as tg_enabled,
)
from dashboard import state
from engine.signal_audit import log_signal as _audit_blocked

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Globals initialised in main() ─────────────────────────────────────────────
_oanda_connector   = None
_alpaca_connector  = None
_paper_trader      = None
_forex_engine      = None
_crypto_engine     = None
_pattern_learner   = PatternLearner()
_trade_manager_fx  = None
_trade_manager_cx  = None
_last_candle_hour  = None   # UTC hour of last on_candle_close() run (prevents restart duplicates)


# ══════════════════════════════════════════════════════════════════════════════
# Bot tick — runs on every H1 candle close
# ══════════════════════════════════════════════════════════════════════════════

def on_candle_close() -> None:
    """
    Main scheduled job. Runs every hour (on the hour).
    1. Update account state in dashboard
    2. Tick open paper trades (SL/TP check)
    3. Run signal engine for every pair
    4. Fire alerts + open trades for qualifying signals
    5. Re-train ML model if due
    6. Refresh suggestion cards if due
    7. Midnight UTC: reset daily drawdown counter
    """
    global _last_candle_hour
    now_close = datetime.now(timezone.utc)
    current_hour = now_close.replace(minute=0, second=0, microsecond=0)

    # Guard against restart duplicates: if this hour was already fully processed
    # (e.g. bot restarted mid-hour), skip to avoid duplicate audit entries + double alerts.
    if _last_candle_hour is not None and current_hour <= _last_candle_hour:
        logger.info(
            "Candle close %s skipped — hour already processed (last=%s)",
            current_hour.strftime("%H:%M UTC"), _last_candle_hour.strftime("%H:%M UTC"),
        )
        return
    _last_candle_hour = current_hour

    logger.info("── Candle close %s ──────────────────────────────────────────",
                now_close.strftime("%Y-%m-%d %H:%M UTC"))
    state.update(last_scan_time=now_close)

    # ── Account refresh ───────────────────────────────────────────────────────
    _refresh_account()

    # ── Update open paper trades ──────────────────────────────────────────────
    if config.MODE == "paper" and _paper_trader:
        _tick_paper_trades()

    # ── Signal scan — active pairs (trade + alert) ───────────────────────────
    watch_pairs = getattr(config, "FOREX_WATCH", [])
    logger.info(
        "  Scanning %d trade pairs + %d watch pairs + %d crypto …",
        len(config.FOREX_PAIRS), len(watch_pairs), len(config.CRYPTO_PAIRS),
    )

    for pair in config.FOREX_PAIRS:
        _process_pair(pair, "forex", _forex_engine)

    # Watch-only pairs: scan signals for dashboard display but never open trades
    if _forex_engine:
        for pair in watch_pairs:
            try:
                sig = _forex_engine.run(pair, "forex", no_audit=True)
                if sig is None:
                    state.update_signal(pair, 0, "—")
                elif sig.get("no_signal"):
                    state.update_signal(pair, 0, "—",
                                        timeframe=sig.get("timeframe", "H1"))
                    state.update_signal_detail(pair, sig)
                else:
                    state.update_signal(pair, sig["score"], sig["direction"],
                                        timeframe=sig.get("timeframe", "H1"))
                    state.update_signal_detail(pair, sig)
                    logger.info("  [WATCH] %-12s %5s  score=%d",
                                pair, sig["direction"], sig["score"])
            except Exception as exc:
                logger.warning("Watch scan failed for %s: %s", pair, exc)

    for pair in config.CRYPTO_PAIRS:
        _process_pair(pair, "crypto", _crypto_engine)

    # Summary of scores after scan
    sigs = state.get_key("signals", {})
    active = [(p, v) for p, v in sigs.items() if v.get("score", 0) > 0]
    if active:
        logger.info("  Signals: %s",
                    "  ".join(f"{p} {v['direction']}={v['score']}" for p, v in active))
    else:
        logger.info("  No qualifying signals this candle (all scores 0)")

    # ── ML retrain ────────────────────────────────────────────────────────────
    if _pattern_learner.should_retrain():
        logger.info("PatternLearner: retraining…")
        report = _pattern_learner.train()
        if report:
            _update_ml_stats()

    # ── Feedback suggestions ──────────────────────────────────────────────────
    if feedback_should_run():
        logger.info("FeedbackLoop: generating suggestions…")
        suggestions = generate_suggestions()
        state.update(suggestions=suggestions)
        logger.info("  %d suggestions generated", len(suggestions))

    # ── Midnight reset ────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    if now.hour == 0 and now.minute < 5:
        acct    = state.get_key("account", {})
        balance = acct.get("balance", 500.0)
        reset_daily_state(balance)
        logger.info("Daily drawdown counter reset")


def _process_pair(pair: str, market: str, engine) -> None:
    if engine is None:
        return

    _t_signal_start = time.perf_counter()
    # Run signal engine first (no ML yet — features come from the signal itself)
    signal = engine.run(pair, market, ml_win_prob=None)

    # After signal is generated, compute ML win-prob from real features
    ml_prob = None
    if signal and not signal.get("no_signal") and _pattern_learner:
        try:
            from learning.pattern_learner import FEATURES
            from learning.data_collector import _get_session
            features = {f: (signal.get(f) or 0) for f in FEATURES}
            # session is not in the signal dict — derive from current UTC hour
            features["session"] = _get_session(datetime.now(timezone.utc))
            ml_prob = _pattern_learner.predict_win_prob(features)
            if ml_prob is not None:
                signal["ml_win_prob"] = ml_prob
        except Exception as _ml_exc:
            logger.debug("ML prediction failed: %s", _ml_exc)

    if signal is None:
        state.update_signal(pair, 0, "—")
        return

    tf = signal.get("timeframe", config.TIMEFRAMES["primary"])

    # Diagnostic signals (no_signal=True) keep dashboard diagnostics up to date
    if signal.get("no_signal"):
        state.update_signal(pair, 0, "—", timeframe=tf)
        state.update_signal_detail(pair, signal)       # store for diagnostics panel
        return

    score = signal["score"]
    state.update_signal(pair, score, signal["direction"], timeframe=tf)
    state.update_signal_detail(pair, signal)           # store full signal for chart overlay
    state.cache_candles(pair, config.TIMEFRAMES["primary"], None)  # invalidate H1 cache

    logger.info(
        "  %-12s %5s  score=%-3d  EMA=%.0f  Structure=%.0f  PA=%.0f  ML=%s",
        pair, signal["direction"], score,
        signal.get("ema_score", 0),
        signal.get("structure_score", 0),
        signal.get("pa_score", 0),
        f"{ml_prob:.2f}" if ml_prob is not None else "n/a",
    )

    # Slider can go lower than the config floor now that H4/ATR/news gates are active.
    # The floor is still enforced at the config minimum to prevent reckless lowering.
    effective_min = max(
        state.get_key("min_score", config.MIN_CONFLUENCE_SCORE),
        40,   # hard floor — never trade below 40 regardless of slider
    )
    if score < effective_min:
        if signal.get("watching"):
            # Send Telegram notification for WATCHING signals too
            try:
                if tg_enabled():
                    tg_signal(signal)
            except Exception:
                pass
        else:
            record_skip(signal)
        return

    def _block(reason: str) -> None:
        """Stamp the signal detail with a trade_blocked_reason for the dashboard."""
        state.update_signal_detail(pair, {**signal, "trade_blocked_reason": reason})

    # ── Gate-blocked signals (H4 counter-trend / ATR out of range) ──────────
    if signal.get("gate_blocked"):
        logger.info(
            "GATE BLOCKED: %s %s score=%d — %s (signal visible, no trade)",
            pair, signal["direction"], score, signal["gate_blocked"],
        )
        _block(f"Gate: {signal['gate_blocked']}")
        return

    # ── Session filter: only open trades during London / NY overlap ──────────
    now_utc = datetime.now(timezone.utc)
    in_session = config.SESSION_START_UTC <= now_utc.hour < config.SESSION_END_UTC
    if not in_session:
        _sess_reason = (
            f"Out of session ({now_utc.strftime('%H:%M')} UTC, "
            f"window {config.SESSION_START_UTC:02d}:00–{config.SESSION_END_UTC:02d}:00)"
        )
        logger.info("Session filter: %s %s score=%d — %s", pair, signal["direction"], score, _sess_reason)
        _audit_blocked(
            pair=pair, timeframe=signal.get("timeframe", "H1"),
            direction=signal["direction"],
            ema_score=signal.get("ema_score", 0),
            structure_score=signal.get("structure_score", 0),
            pa_score=signal.get("pa_score", 0),
            confluence_score=score,
            result="BLOCKED", reject_reason=_sess_reason,
        )
        _block(_sess_reason)
        return

    # ── News blackout filter ──────────────────────────────────────────────────
    # ForexFactory: free XML feed, no API key required — always active
    _ff_blocked, _ff_event = ff_is_news_blackout(pair)
    if _ff_blocked:
        logger.info("NEWS BLOCKED (FF): %s — '%s'", pair, _ff_event)
        _block(f"News: {_ff_event}")
        return
    # Finnhub: supplement if API key is configured
    if config.FINNHUB_API_KEY:
        _news_blocked, _event_name = is_news_blackout(pair)
        if _news_blocked:
            logger.info("NEWS BLOCKED (Finnhub): %s — '%s'", pair, _event_name)
            _block(f"News: {_event_name}")
            return

    # ── Duplicate / pre-trade guard ──────────────────────────────────────────
    # This is the PRIMARY duplicate-prevention layer. PaperTrader.open_trade()
    # has a secondary guard as a last resort.
    if config.MODE == "paper" and _paper_trader:
        # 1. Check for existing open trade on this pair
        existing = _paper_trader.get_open_trade(pair)
        if existing and not config.ALLOW_MULTIPLE_PER_PAIR:
            logger.info(
                "DUPLICATE BLOCKED: %s already has open %s trade (id=%s entry=%.5f)",
                pair, existing["direction"], existing["id"], existing["entry"],
            )
            _audit_blocked(
                pair=pair, timeframe=signal.get("timeframe", "H1"),
                direction=signal["direction"],
                ema_score=signal.get("ema_score", 0),
                structure_score=signal.get("structure_score", 0),
                pa_score=signal.get("pa_score", 0),
                confluence_score=score,
                result="BLOCKED",
                reject_reason=f"Pair already has open trade id={existing['id']}",
            )
            _block(f"Duplicate: already open ({existing['direction']})")
            return

        # 2. Check max-open-trades and halt flag via validate_pre_trade
        open_pairs = [t.get("pair") for t in _paper_trader.open_trades]
        ok, reason = validate_pre_trade(score, len(_paper_trader.open_trades), pair, open_pairs)
        if not ok:
            logger.info("PRE-TRADE BLOCKED: %s — %s", pair, reason)
            _audit_blocked(
                pair=pair, timeframe=signal.get("timeframe", "H1"),
                direction=signal["direction"],
                ema_score=signal.get("ema_score", 0),
                structure_score=signal.get("structure_score", 0),
                pa_score=signal.get("pa_score", 0),
                confluence_score=score,
                result="BLOCKED", reject_reason=reason,
            )
            _block(reason)
            return

        # 3. Cooldown check — prevent re-entry within TRADE_COOLDOWN_HOURS of last close
        cooldowns = state.get_key("trade_cooldowns", {})
        last_close = cooldowns.get(pair)
        if last_close:
            elapsed_h = (datetime.now(timezone.utc) - last_close).total_seconds() / 3600
            if elapsed_h < config.TRADE_COOLDOWN_HOURS:
                logger.info(
                    "COOLDOWN BLOCKED: %s last closed %.1fh ago (cooldown=%dh)",
                    pair, elapsed_h, config.TRADE_COOLDOWN_HOURS,
                )
                _audit_blocked(
                    pair=pair, timeframe=signal.get("timeframe", "H1"),
                    direction=signal["direction"],
                    ema_score=signal.get("ema_score", 0),
                    structure_score=signal.get("structure_score", 0),
                    pa_score=signal.get("pa_score", 0),
                    confluence_score=score,
                    result="BLOCKED",
                    reject_reason=f"Cooldown: last close {elapsed_h:.1f}h ago < {config.TRADE_COOLDOWN_HOURS}h",
                )
                _block(f"Cooldown {elapsed_h:.1f}h / {config.TRADE_COOLDOWN_HOURS}h")
                return

    # ≥ threshold + in session: fire alert + open trade
    try:
        play_alert(signal["direction"])
    except Exception:
        pass

    # Telegram: signal notification
    try:
        if tg_enabled():
            tg_signal(signal)
    except Exception:
        pass

    state.set_pending_alert(signal)
    logger.info("⚡ Alert fired: %s %s  score=%d", pair, signal["direction"], score)

    account = state.get_key("account", {})
    balance = account.get("balance") or account.get("cash", 500.0)
    inst    = "forex" if market == "forex" else "crypto"
    size    = calculate_position_size(balance, signal["entry"], signal["stop_loss"], inst, pair)

    trade_id  = f"{pair}-{int(datetime.now(timezone.utc).timestamp())}"
    record_signal(trade_id, signal, size, balance * 0.01)

    actual_id = None   # guard against UnboundLocalError if manager is absent
    if config.MODE == "paper" and _paper_trader:
        tp         = signal["tp_levels"]
        latency_ms = round((time.perf_counter() - _t_signal_start) * 1000, 1)
        try:
            actual_id  = _paper_trader.open_trade(
                pair=pair,
                direction=signal["direction"],
                entry_price=signal["entry"],
                sl=signal["stop_loss"],
                tp_levels=tp,
                size=size,
                extra={"latency_ms": latency_ms},
            )
            _sync_paper_state()
            logger.debug("Latency %s → %.1f ms", pair, latency_ms)
        except Exception as _open_exc:
            logger.error("TRADE OPEN FAILED for %s: %s", pair, _open_exc, exc_info=True)
        # Telegram: trade opened
        try:
            if tg_enabled() and actual_id:
                t_opened = _paper_trader.get_open_trade(pair)
                if t_opened:
                    tg_trade_opened(t_opened)
        except Exception:
            pass
    else:
        manager   = _trade_manager_fx if market == "forex" else _trade_manager_cx
        if manager:
            actual_id = manager.open_trade(signal)
        else:
            logger.warning("No trade manager for %s — signal logged but not traded", pair)

    if actual_id:
        logger.info("Trade opened: %s", actual_id)
    elif config.MODE == "paper":
        # open_trade returned None — PaperTrader's secondary duplicate guard fired
        logger.warning("PAPER TRADER rejected trade for %s (secondary duplicate guard)", pair)


def _tick_paper_trades() -> None:
    """Feed the latest candle to PaperTrader so SL/TP is evaluated."""
    for pair in config.FOREX_PAIRS + config.CRYPTO_PAIRS:
        if not _paper_trader.get_open_trade(pair):
            continue
        connector = (_oanda_connector if "_" in pair else _alpaca_connector)
        if connector is None:
            continue
        try:
            gran = config.TIMEFRAMES["primary"]
            # Alpaca uses different TF strings ("1Hour" not "H1")
            if "_" not in pair:
                _tf_map = {"H1": "1Hour", "H4": "4Hour", "D": "1Day"}
                gran = _tf_map.get(gran, "1Hour")
            df      = connector.get_candles(pair, gran, 2)
            if df is None or len(df) < 1:
                continue
            last    = df.iloc[-1]
            closed_before = {t["id"] for t in _paper_trader.closed_trades}
            _paper_trader.update(pair, last["high"], last["low"], last["close"])
            _sync_paper_state()
            # Detect newly closed trades: update cooldown + Telegram
            newly_closed = [t for t in _paper_trader.closed_trades
                            if t["id"] not in closed_before]
            if newly_closed:
                cooldowns = dict(state.get_key("trade_cooldowns", {}))
                for t in newly_closed:
                    cooldowns[t["pair"]] = datetime.now(timezone.utc)
                    logger.info(
                        "Cooldown started for %s (trade %s closed, %dh cooldown)",
                        t["pair"], t["id"], config.TRADE_COOLDOWN_HOURS,
                    )
                    adaptive_params.update(t["pair"], _paper_trader.closed_trades)
                state.update(trade_cooldowns=cooldowns)
            if tg_enabled():
                for t in newly_closed:
                    try:
                        tg_trade_closed(t)
                    except Exception:
                        pass
        except Exception as exc:
            logger.error("Paper tick failed for %s: %s", pair, exc)


def _backfill_equity_curve() -> None:
    """Reconstruct equity history from closed trades on startup.
    Called once before _refresh_account() so the full trade history is visible
    in the dashboard immediately after restart rather than starting blank.
    """
    if not _paper_trader or not _paper_trader.closed_trades:
        return
    trades = sorted(
        [t for t in _paper_trader.closed_trades if t.get("close_time")],
        key=lambda t: t["close_time"],
    )
    if not trades:
        return
    balance = _paper_trader._start_balance
    points = []
    for t in trades:
        balance = round(balance + t.get("realised_pnl", 0), 2)
        ct = t["close_time"]
        try:
            unix_ts = int(ct.timestamp()) if hasattr(ct, "timestamp") else int(ct)
        except Exception:
            continue
        points.append({"t": unix_ts, "balance": balance, "nav": balance})
    if points:
        state.update(equity_curve=points)
        logger.info("Equity curve backfilled: %d historical points (%.2f → %.2f)",
                    len(points), _paper_trader._start_balance, balance)


def _refresh_account() -> None:
    if config.MODE == "paper" and _paper_trader:
        acc = _paper_trader.get_account()
        state.update(account={
            "balance":          acc["balance"],
            "nav":              acc.get("nav", acc["balance"]),
            "unrealized_pnl":   acc.get("unrealized_pnl", 0),
            "open_trade_count": len(_paper_trader.open_trades),
            "profit_factor":    _paper_trader.profit_factor(),
            "avg_latency_ms":   _paper_trader.avg_latency_ms(),
        })
        state.append_equity(acc["balance"], acc.get("nav", acc["balance"]))
        return
    # Live: fetch from broker
    if _oanda_connector:
        try:
            acc = _oanda_connector.get_account_summary()
            state.update(account=acc)
            state.append_equity(acc.get("balance", 0), acc.get("nav", 0))
        except Exception as exc:
            logger.error("Account refresh failed: %s", exc)


def _sync_paper_state() -> None:
    if _paper_trader:
        state.update(
            open_trades    = list(_paper_trader.open_trades),
            closed_trades  = list(_paper_trader.closed_trades),
            pending_orders = list(_paper_trader.pending_orders),
        )


def _reconcile_live_positions() -> None:
    """
    On live-mode startup, compare local TradeManager state with broker open trades.
    Logs any discrepancies so the user can decide whether to close/ignore them.
    Does NOT auto-create trades — avoids duplicate position risk.
    """
    if config.MODE != "live" or not _oanda_connector:
        return
    try:
        broker_trades = _oanda_connector.get_open_trades()
        if not broker_trades:
            logger.info("Reconcile: broker has no open trades")
            return

        local_ids = set()
        if _trade_manager_fx:
            local_ids.update(_trade_manager_fx.open_trades.keys())

        for bt in broker_trades:
            if bt["id"] not in local_ids:
                logger.warning(
                    "RECONCILE — broker trade %s (%s, %d units @ %.5f) "
                    "not in local state — manual review required",
                    bt["id"], bt["instrument"], bt["units"], bt["price"],
                )
            else:
                logger.info("Reconcile OK — trade %s found locally", bt["id"])
    except Exception as exc:
        logger.error("Reconcile failed: %s", exc)


def _update_ml_stats() -> None:
    importance = _pattern_learner.get_feature_importance()
    top        = _pattern_learner.top_features(3)
    try:
        import pandas as pd
        df = pd.read_csv("data/signal_log.csv")
        n  = len(df[df["outcome"].isin(["win", "loss"])])
    except Exception:
        n = 0
    state.update(ml_stats={"accuracy": None, "top_features": top, "n_samples": n})


def _diagnostic_scan() -> None:
    """
    Runs between H1 closes (:15, :30, :45) to refresh dashboard signal state.
    Updates scores and rejection reasons for all pairs — NEVER opens trades.
    """
    now_utc = datetime.now(timezone.utc)
    state.update(last_scan_time=now_utc)
    logger.info("Diagnostic scan %s", now_utc.strftime("%H:%M UTC"))
    if not _forex_engine:
        return
    all_pairs = config.FOREX_PAIRS + getattr(config, "FOREX_WATCH", [])
    for pair in all_pairs:
        try:
            sig = _forex_engine.run(pair, "forex", no_audit=True)
            if sig is None:
                continue
            tf = sig.get("timeframe", config.TIMEFRAMES["primary"])
            if sig.get("no_signal"):
                state.update_signal(pair, 0, "—", timeframe=tf)
                state.update_signal_detail(pair, sig)
            else:
                state.update_signal(pair, sig["score"], sig["direction"], timeframe=tf)
                state.update_signal_detail(pair, sig)
        except Exception as exc:
            logger.debug("Diagnostic scan %s: %s", pair, exc)


# ══════════════════════════════════════════════════════════════════════════════
# Initialisation
# ══════════════════════════════════════════════════════════════════════════════

def _init_connectors() -> tuple:
    oanda, alpaca = None, None

    if config.OANDA_API_KEY and config.OANDA_ACCOUNT_ID:
        try:
            oanda = OandaConnector()
            # Quick connectivity test — fetch 1 candle
            _test_pair = config.FOREX_PAIRS[0]
            test_df = oanda.get_candles(_test_pair, "H1", 1)
            state.update(oanda_ok=True)
            logger.info("✓ Oanda  connected (%s)  last %s close=%.5f",
                        config.OANDA_ENV, _test_pair, test_df["close"].iloc[-1] if not test_df.empty else 0)
        except Exception as exc:
            state.update(oanda_ok=False)
            logger.error("✗ Oanda  connection FAILED: %s", exc)
    else:
        state.update(oanda_ok=False)
        logger.warning("✗ Oanda  credentials missing in .env — forex disabled")

    if config.CRYPTO_PAIRS and config.ALPACA_API_KEY and config.ALPACA_SECRET:
        try:
            alpaca = AlpacaConnector()
            test_df = alpaca.get_candles(config.CRYPTO_PAIRS[0], "1Hour", 1)
            state.update(alpaca_ok=True)
            logger.info("✓ Alpaca connected (paper=%s)  last %s close=%.2f",
                        config.MODE == "paper", config.CRYPTO_PAIRS[0],
                        test_df["close"].iloc[-1] if not test_df.empty else 0)
        except Exception as exc:
            state.update(alpaca_ok=False)
            logger.error("✗ Alpaca connection FAILED: %s", exc)
    else:
        state.update(alpaca_ok=False)
        if not config.CRYPTO_PAIRS:
            logger.info("Alpaca skipped — CRYPTO_PAIRS is empty")
        else:
            logger.warning("✗ Alpaca credentials missing in .env — crypto disabled")

    return oanda, alpaca


def _init_engines(oanda, alpaca):
    forex_eng = SignalEngine(oanda.get_candles) if oanda else None
    # Alpaca uses different timeframe strings — adapt via lambda
    def _alpaca_get(pair, gran, count):
        tf_map = {"H1": "1Hour", "H4": "4Hour", "D": "1Day"}
        return alpaca.get_candles(pair, tf_map.get(gran, "1Hour"), count)
    crypto_eng = SignalEngine(_alpaca_get) if alpaca else None
    return forex_eng, crypto_eng


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    global _oanda_connector, _alpaca_connector, _paper_trader
    global _forex_engine, _crypto_engine
    global _trade_manager_fx, _trade_manager_cx

    logger.info("══════════════════════════════════════════════════════")
    logger.info("  Trading Bot  |  Mode: %s", config.MODE.upper())
    logger.info("  Forex pairs : %s", ", ".join(config.FOREX_PAIRS))
    logger.info("  Crypto pairs: %s", ", ".join(config.CRYPTO_PAIRS))
    logger.info("══════════════════════════════════════════════════════")

    # ── Connectors ────────────────────────────────────────────────────────────
    _oanda_connector, _alpaca_connector = _init_connectors()
    state.update(
        forex_connector  = _oanda_connector,
        crypto_connector = _alpaca_connector,
    )

    # ── Trader ────────────────────────────────────────────────────────────────
    if config.MODE == "paper":
        _paper_trader = PaperTrader(starting_balance=500.0)
        state.update(paper_trader=_paper_trader)   # expose to dashboard quick-trade
        _sync_paper_state()        # push loaded trades into state["open_trades"] immediately
        _backfill_equity_curve()   # reconstruct historical equity from closed trades
        _refresh_account()         # push current balance + append latest equity point
        logger.info("Paper trader: balance $%.2f  open_trades=%d  closed=%d",
                    _paper_trader.balance, len(_paper_trader.open_trades),
                    len(_paper_trader.closed_trades))
    else:
        if _oanda_connector:
            _trade_manager_fx = TradeManager(_oanda_connector, "forex")
        if _alpaca_connector:
            _trade_manager_cx = TradeManager(_alpaca_connector, "crypto")

    # ── Signal engines ────────────────────────────────────────────────────────
    _forex_engine, _crypto_engine = _init_engines(_oanda_connector, _alpaca_connector)

    # ── Live-mode reconciliation — compare local state vs broker open trades ──
    _reconcile_live_positions()

    # ── Generate sounds if missing ────────────────────────────────────────────
    import os
    long_wav = os.path.join("dashboard", "assets", "alert_long.wav")
    if not os.path.exists(long_wav):
        try:
            import generate_sounds
            generate_sounds.main()
        except Exception as exc:
            logger.warning("Could not generate sounds: %s", exc)

    # ── Launch dashboard in background thread ─────────────────────────────────
    from dashboard.app import app as dash_app
    def _run_dash():
        dash_app.run(host="0.0.0.0", port=8050, debug=False, use_reloader=False)
    t = threading.Thread(target=_run_dash, daemon=True, name="dashboard")
    t.start()
    logger.info("Dashboard: http://localhost:8050")

    # ── OANDA price stream — real-time SL/TP monitoring ──────────────────────
    if config.MODE == "paper" and _oanda_connector and _paper_trader:
        forex_pairs = config.FOREX_PAIRS + getattr(config, "FOREX_WATCH", [])
        if forex_pairs:
            import time as _time

            def _on_stream_price(instrument: str, bid: float, ask: float) -> None:
                """Called on every tick from the OANDA stream."""
                if not _paper_trader or not _paper_trader.get_open_trade(instrument):
                    return
                try:
                    closed = _paper_trader.tick_check(instrument, bid, ask)
                    if closed:
                        _sync_paper_state()
                        logger.info(
                            "STREAM: SL/TP closed %d trade(s) on %s  bid=%.5f ask=%.5f",
                            len(closed), instrument, bid, ask,
                        )
                except Exception as exc:
                    logger.error("Stream tick_check error: %s", exc)

            def _run_price_stream() -> None:
                """Daemon thread: stream OANDA prices, reconnect on any error."""
                while True:
                    try:
                        logger.info(
                            "Price stream: connecting for %d pairs: %s",
                            len(forex_pairs), forex_pairs,
                        )
                        _oanda_connector.stream_prices(forex_pairs, _on_stream_price)
                    except Exception as exc:
                        logger.warning(
                            "Price stream disconnected (%s) — reconnecting in 5 s", exc
                        )
                        _time.sleep(5)

            stream_thread = threading.Thread(
                target=_run_price_stream, daemon=True, name="price-stream"
            )
            stream_thread.start()
            logger.info("Real-time price stream started for %s", forex_pairs)

    # ── Scheduler ─────────────────────────────────────────────────────────────
    scheduler = BlockingScheduler(timezone="UTC")
    # H1 candle closes on the hour — full scan + trade evaluation
    scheduler.add_job(on_candle_close, CronTrigger(minute=0), id="h1_close")
    # Between-close diagnostic scans — update dashboard state only, no trades
    scheduler.add_job(_diagnostic_scan, CronTrigger(minute="15,30,45"), id="diag_scan")
    # Weekly WFO re-fit — Sunday 02:00 UTC, runs in background thread (non-blocking)
    if getattr(config, "WFO_ENABLED", True) and _oanda_connector:
        def _wfo_job():
            threading.Thread(
                target=wfo_optimizer.run_all_pairs,
                args=(_oanda_connector.get_candles, config.FOREX_PAIRS),
                kwargs={"market": "forex"},
                daemon=True,
                name="wfo-refit",
            ).start()
        scheduler.add_job(_wfo_job, CronTrigger(day_of_week="sun", hour=2, minute=0), id="wfo_refit")
        logger.info("WFO weekly re-fit scheduled — Sundays 02:00 UTC")
    state.update(last_scan_time=datetime.now(timezone.utc))
    logger.info("Scheduler ready — H1 close on :00, diagnostics at :15/:30/:45")
    logger.info("Press Ctrl+C to stop.\n")

    # Optionally run immediately on startup (useful for testing)
    try:
        on_candle_close()   # first tick immediately
    except Exception as exc:
        logger.error("Initial tick failed: %s", exc)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped cleanly.")


if __name__ == "__main__":
    main()
