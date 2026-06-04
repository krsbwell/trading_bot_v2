"""
Trading Bot — entry point.

Paper mode (default):   python main.py
Live mode:              set MODE = "live" in config.py, then python main.py

Dashboard opens at:     http://localhost:8050
"""
import logging
import threading
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from connectors.oanda_connector import OandaConnector
from connectors.alpaca_connector import AlpacaConnector
from engine.signal_engine import SignalEngine
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
from dashboard import state

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
    logger.info("── Candle close %s ──────────────────────────────────────────",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

    # ── Account refresh ───────────────────────────────────────────────────────
    _refresh_account()

    # ── Update open paper trades ──────────────────────────────────────────────
    if config.MODE == "paper" and _paper_trader:
        _tick_paper_trades()

    # ── Signal scan ───────────────────────────────────────────────────────────
    logger.info("  Scanning %d forex + %d crypto pairs …",
                len(config.FOREX_PAIRS), len(config.CRYPTO_PAIRS))

    for pair in config.FOREX_PAIRS:
        _process_pair(pair, "forex", _forex_engine)

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

    # ML win-prob is only useful after 50 closed trades; dummy features before that
    ml_prob = (_pattern_learner.predict_win_prob(_dummy_features())
               if _pattern_learner else None)

    signal = engine.run(pair, market, ml_win_prob=ml_prob)

    if signal is None:
        state.update_signal(pair, 0, "—")
        return

    score = signal["score"]
    state.update_signal(pair, score, signal["direction"])
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

    # Watching / scanning signal — show on dashboard but don't trade or record
    if score < config.MIN_CONFLUENCE_SCORE:
        if not signal.get("watching", False):
            record_skip(signal)
        return

    # ≥ 70: fire alert + open trade
    try:
        play_alert(signal["direction"])
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
        tp        = signal["tp_levels"]
        actual_id = _paper_trader.open_trade(
            pair=pair,
            direction=signal["direction"],
            entry_price=signal["entry"],
            sl=signal["stop_loss"],
            tp_levels=tp,
            size=size,
        )
        _sync_paper_state()
    else:
        manager   = _trade_manager_fx if market == "forex" else _trade_manager_cx
        if manager:
            actual_id = manager.open_trade(signal)
        else:
            logger.warning("No trade manager for %s — signal logged but not traded", pair)

    if actual_id:
        logger.info("Trade opened: %s", actual_id)


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
            df   = connector.get_candles(pair, gran, 2)
            if df is None or len(df) < 1:
                continue
            last = df.iloc[-1]
            _paper_trader.update(pair, last["high"], last["low"], last["close"])
            _sync_paper_state()
        except Exception as exc:
            logger.error("Paper tick failed for %s: %s", pair, exc)


def _refresh_account() -> None:
    if config.MODE == "paper" and _paper_trader:
        acc = _paper_trader.get_account()
        state.update(account={
            "balance":          acc["balance"],
            "nav":              acc.get("nav", acc["balance"]),
            "unrealized_pnl":   acc.get("unrealized_pnl", 0),
            "open_trade_count": len(_paper_trader.open_trades),
        })
        return
    # Live: fetch from broker
    if _oanda_connector:
        try:
            acc = _oanda_connector.get_account_summary()
            state.update(account=acc)
        except Exception as exc:
            logger.error("Account refresh failed: %s", exc)


def _sync_paper_state() -> None:
    if _paper_trader:
        state.update(
            open_trades    = list(_paper_trader.open_trades),
            closed_trades  = list(_paper_trader.closed_trades),
            pending_orders = list(_paper_trader.pending_orders),
        )


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


def _dummy_features() -> dict:
    from learning.pattern_learner import FEATURES
    return {f: 0 for f in FEATURES}


# ══════════════════════════════════════════════════════════════════════════════
# Initialisation
# ══════════════════════════════════════════════════════════════════════════════

def _init_connectors() -> tuple:
    oanda, alpaca = None, None

    if config.OANDA_API_KEY and config.OANDA_ACCOUNT_ID:
        try:
            oanda = OandaConnector()
            # Quick connectivity test — fetch 1 candle
            test_df = oanda.get_candles("EUR_USD", "H1", 1)
            state.update(oanda_ok=True)
            logger.info("✓ Oanda  connected (%s)  last EUR_USD close=%.5f",
                        config.OANDA_ENV, test_df["close"].iloc[-1] if not test_df.empty else 0)
        except Exception as exc:
            state.update(oanda_ok=False)
            logger.error("✗ Oanda  connection FAILED: %s", exc)
    else:
        state.update(oanda_ok=False)
        logger.warning("✗ Oanda  credentials missing in .env — forex disabled")

    if config.ALPACA_API_KEY and config.ALPACA_SECRET:
        try:
            alpaca = AlpacaConnector()
            # Quick connectivity test — fetch 1 BTC/USD bar
            test_df = alpaca.get_candles("BTC/USD", "1Hour", 1)
            state.update(alpaca_ok=True)
            logger.info("✓ Alpaca connected (paper=%s)  last BTC/USD close=%.2f",
                        config.MODE == "paper",
                        test_df["close"].iloc[-1] if not test_df.empty else 0)
        except Exception as exc:
            state.update(alpaca_ok=False)
            logger.error("✗ Alpaca connection FAILED: %s", exc)
    else:
        state.update(alpaca_ok=False)
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
        logger.info("Paper trader: balance $%.2f", _paper_trader.balance)
    else:
        if _oanda_connector:
            _trade_manager_fx = TradeManager(_oanda_connector, "forex")
        if _alpaca_connector:
            _trade_manager_cx = TradeManager(_alpaca_connector, "crypto")

    # ── Signal engines ────────────────────────────────────────────────────────
    _forex_engine, _crypto_engine = _init_engines(_oanda_connector, _alpaca_connector)

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

    # ── Scheduler ─────────────────────────────────────────────────────────────
    scheduler = BlockingScheduler(timezone="UTC")
    # H1 candle closes on the hour
    scheduler.add_job(on_candle_close, CronTrigger(minute=0), id="h1_close")
    logger.info("Scheduler ready — next trigger at the top of the hour")
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
