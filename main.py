"""
Trading Bot — entry point.

Paper mode:             set MODE = "paper" in config.py — pure internal
                        simulation, no OANDA orders placed
Demo mode (default):    set MODE = "demo" in config.py — real orders on
                        the OANDA practice account
Live mode:              set MODE = "live" in config.py — real orders on
                        the real-money OANDA account

Dashboard opens at:     http://localhost:8050
"""
import logging
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import config
from connectors.oanda_connector import OandaConnector
from connectors.news_connector import is_news_blackout
from connectors.forexfactory_connector import is_news_blackout as ff_is_news_blackout
from engine.signal_engine import SignalEngine
from engine.indicators import atr as _calc_atr
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
from learning import shadow_outcomes
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

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_log_formatter = logging.Formatter(
    "%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S",
)
_log_file_handler = RotatingFileHandler(
    # backupCount raised 3 -> 20 (2026-07-20): confirmed today that 3 backups
    # (20MB total) isn't enough headroom — the 2026-07-19 scheduler-freeze
    # evidence this handler exists to catch (see tasks/todo_scheduler_freeze.md)
    # had already rotated out before it could be analyzed, just from one day's
    # normal volume plus a handful of restarts. 20 backups (~100MB) gives a
    # much longer window for a future freeze's heartbeat/lock-timing logs to
    # actually survive until someone looks at them.
    _LOG_DIR / "main.log", maxBytes=5 * 1024 * 1024, backupCount=20,
)
_log_file_handler.setFormatter(_log_formatter)
_log_console_handler = logging.StreamHandler()
_log_console_handler.setFormatter(_log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[_log_console_handler, _log_file_handler])
logger = logging.getLogger(__name__)


# ── Globals initialised in main() ─────────────────────────────────────────────
_oanda_connector   = None
_paper_trader      = None
_forex_engine      = None
_pattern_learner   = PatternLearner()
_trade_manager_fx  = None
_last_candle_hour  = None   # UTC hour of last on_candle_close() run (prevents restart duplicates)


# ══════════════════════════════════════════════════════════════════════════════
# Bot tick — runs on every H1 candle close
# ══════════════════════════════════════════════════════════════════════════════

_HEARTBEAT_PATH = Path(__file__).parent / "data" / "heartbeat.txt"


def _write_heartbeat() -> None:
    """
    Record that the scheduler thread is alive and firing, independent of the
    Dash dashboard (which runs in a thread inside this same process and goes
    down with it — see bugs_process_death_no_logging memory: a full process
    death takes the dashboard's health banner down too, so it can't be relied
    on to detect this). A separate watchdog script (scripts/watchdog.py) can
    read this file's mtime/contents from outside this process entirely.
    """
    try:
        _HEARTBEAT_PATH.write_text(datetime.now(timezone.utc).isoformat())
    except Exception:
        pass


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
    _write_heartbeat()   # scheduler fired — record this before any early return below

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

    # ── Update open trades ─────────────────────────────────────────────────────
    if config.MODE == "paper" and _paper_trader:
        _tick_paper_trades()
    elif config.MODE in ("demo", "live"):
        _tick_live_trades()

    # ── Weekend close — after the normal SL/TP tick above, before any new
    # signal can open, so a trade that legitimately hit SL/TP this candle
    # keeps its real close reason, and nothing new opens after this point
    # (SESSION_END_UTC already blocks new entries regardless). ─────────────
    _weekend_close_check()

    # ── Signal scan — active pairs (trade + alert) ───────────────────────────
    watch_pairs = getattr(config, "FOREX_WATCH", [])
    logger.info(
        "  Scanning %d trade pairs + %d watch pairs …",
        len(config.FOREX_PAIRS), len(watch_pairs),
    )

    _scan_forex_pairs(config.FOREX_PAIRS, _forex_engine)

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


def _scan_forex_pairs(pairs: list, engine) -> None:
    """
    Run _process_pair for every active forex pair, isolating exceptions per
    pair so a failure on one pair can never silently skip the pairs after it
    for the rest of the cycle (see bugs_scheduler_reliability memory —
    this loop previously had no exception isolation at all).

    Records a per-pair "last scanned" timestamp in dashboard state, whether
    or not the pair produced a signal — this is what the dashboard's bot
    health indicator watches to detect a pair silently going dark.
    """
    for pair in pairs:
        try:
            _process_pair(pair, "forex", engine)
            state.record_pair_scan(pair)
        except Exception:
            logger.error("Signal processing failed for %s — skipping this cycle", pair, exc_info=True)


def _trending_structure_gate_applies(pair: str, market_structure: str) -> bool:
    """
    EMA-bounce is a mean-reversion strategy — it has 0% historical WR in
    trending markets (uptrend: 0/9, downtrend: 0/3 in signal_log), so its
    signals are blocked when market_structure is trending.

    This does NOT apply to breakout-retest (or any future non-EMA-bounce
    strategy) — that strategy's whole premise is trading a break of
    structure INTO a trend, so blocking on "trending structure" would kill
    most of its legitimate signals. Bug found 2026-07-03: this gate used to
    apply unconditionally to every pair, which silently blocked most of
    GBP_USD's breakout-retest signals after it was wired to that strategy —
    see bugs_scheduler_reliability memory.
    """
    pair_strategy = getattr(config, "STRATEGY_OVERRIDE", {}).get(pair, "ema_bounce")
    return pair_strategy == "ema_bounce" and market_structure in ("uptrend", "downtrend")


def _process_pair(pair: str, market: str, engine) -> None:
    if engine is None:
        return

    _t_signal_start = time.perf_counter()
    signal = engine.run(pair, market, ml_win_prob=None)

    # ML win-prob for the tiered block gate below. When config.ML_SCORE_BOOST_ENABLED
    # is on, signal_engine.py already computed this pre-score (engine/signal_engine.py)
    # and attached it to the signal — reuse it rather than predicting twice. Otherwise
    # (current default) fall back to the original post-hoc computation here.
    ml_prob = None
    if signal and not signal.get("no_signal"):
        ml_prob = signal.get("ml_win_prob")
        if ml_prob is None and _pattern_learner:
            try:
                from learning.pattern_learner import FEATURES
                from learning.data_collector import _get_session
                features = {f: (signal.get(f) or 0) for f in FEATURES}
                # signal dict stores the final score under "score", not "confluence_score" —
                # the generic comprehension above silently left this at 0 otherwise.
                features["confluence_score"] = signal.get("score", 0)
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
    # Sidebar "SIGNAL" badge must reflect the actual gate this pair has to clear —
    # WFO can set a stricter per-pair min_score than the global slider, and that
    # check happens inside signal_engine.run() regardless of the slider's value.
    _pair_min_score = wfo_optimizer.get_params(pair).get("min_score", config.MIN_CONFLUENCE_SCORE)
    _slider_min     = state.get_key("min_score", config.MIN_CONFLUENCE_SCORE)
    _effective_threshold = max(_pair_min_score, _slider_min, 40)
    state.update_signal(pair, score, signal["direction"], timeframe=tf, threshold=_effective_threshold)
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
        record_skip(signal)   # log for shadow-outcome resolution regardless of watching status
        return

    def _block(reason: str) -> None:
        """Stamp the signal detail with a trade_blocked_reason for the dashboard,
        and log the rejected signal for shadow-outcome resolution — covers every
        gate below (ML/structure/session/news/duplicate/pre-trade/cooldown)."""
        state.update_signal_detail(pair, {**signal, "trade_blocked_reason": reason})
        record_skip(signal)

    # ── ML tiered gate ───────────────────────────────────────────────────────
    # Signals scoring 55–64 are marginal — only trade when ML backs them up.
    # Signals 65+ have demonstrated edge regardless of ML probability.
    # Gate is skipped when ML model hasn't trained yet (ml_prob is None).
    if score < 65 and ml_prob is not None:
        if ml_prob < 0.50:
            _ml_reason = f"ML gate: P(win)={ml_prob:.2f} below 0.50 for score {score}"
            logger.info("ML GATE BLOCKED: %s %s — %s", pair, signal["direction"], _ml_reason)
            _audit_blocked(
                pair=pair, timeframe=signal.get("timeframe", "H1"),
                direction=signal["direction"],
                ema_score=signal.get("ema_score", 0),
                structure_score=signal.get("structure_score", 0),
                pa_score=signal.get("pa_score", 0),
                confluence_score=score,
                result="BLOCKED", reject_reason=_ml_reason,
            )
            _block(_ml_reason)
            return

    # ── Gate-blocked signals (H4 counter-trend / ATR out of range) ──────────
    if signal.get("gate_blocked"):
        _gate_reason = f"Gate: {signal['gate_blocked']}"
        logger.info(
            "GATE BLOCKED: %s %s score=%d — %s (signal visible, no trade)",
            pair, signal["direction"], score, signal["gate_blocked"],
        )
        _audit_blocked(
            pair=pair, timeframe=signal.get("timeframe", "H1"),
            direction=signal["direction"],
            ema_score=signal.get("ema_score", 0),
            structure_score=signal.get("structure_score", 0),
            pa_score=signal.get("pa_score", 0),
            confluence_score=score,
            result="BLOCKED", reject_reason=_gate_reason,
        )
        _block(_gate_reason)
        return

    # ── Trending structure filter — EMA-bounce only ───────────────────────────
    _mkt_struct = signal.get("market_structure", "")
    if _trending_structure_gate_applies(pair, _mkt_struct):
        _struct_reason = f"Trending structure ({_mkt_struct}) — EMA bounce has no edge in trends"
        logger.info("STRUCTURE BLOCKED: %s %s score=%d — %s", pair, signal["direction"], score, _struct_reason)
        _audit_blocked(
            pair=pair, timeframe=signal.get("timeframe", "H1"),
            direction=signal["direction"],
            ema_score=signal.get("ema_score", 0),
            structure_score=signal.get("structure_score", 0),
            pa_score=signal.get("pa_score", 0),
            confluence_score=score,
            result="BLOCKED", reject_reason=_struct_reason,
        )
        _block(_struct_reason)
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
        _ff_reason = f"News: {_ff_event}"
        logger.info("NEWS BLOCKED (FF): %s — '%s'", pair, _ff_event)
        _audit_blocked(
            pair=pair, timeframe=signal.get("timeframe", "H1"),
            direction=signal["direction"],
            ema_score=signal.get("ema_score", 0),
            structure_score=signal.get("structure_score", 0),
            pa_score=signal.get("pa_score", 0),
            confluence_score=score,
            result="BLOCKED", reject_reason=_ff_reason,
        )
        _block(_ff_reason)
        return
    # Finnhub: supplement if API key is configured
    if config.FINNHUB_API_KEY:
        _news_blocked, _event_name = is_news_blackout(pair)
        if _news_blocked:
            _fh_reason = f"News: {_event_name}"
            logger.info("NEWS BLOCKED (Finnhub): %s — '%s'", pair, _event_name)
            _audit_blocked(
                pair=pair, timeframe=signal.get("timeframe", "H1"),
                direction=signal["direction"],
                ema_score=signal.get("ema_score", 0),
                structure_score=signal.get("structure_score", 0),
                pa_score=signal.get("pa_score", 0),
                confluence_score=score,
                result="BLOCKED", reject_reason=_fh_reason,
            )
            _block(_fh_reason)
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
    size    = calculate_position_size(balance, signal["entry"], signal["stop_loss"], pair)

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
                trade_id=trade_id,   # matches record_signal()'s id so record_close() finds it
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
        manager   = _trade_manager_fx
        if manager:
            actual_id = manager.open_trade(signal)
            _sync_live_state()
            try:
                if tg_enabled() and actual_id:
                    t_opened = manager.get_open_trade(pair)
                    if t_opened:
                        tg_trade_opened(t_opened)
            except Exception:
                pass
        else:
            logger.warning("No trade manager for %s — signal logged but not traded", pair)

    if actual_id:
        logger.info("Trade opened: %s", actual_id)
    elif config.MODE == "paper":
        # open_trade returned None — PaperTrader's secondary duplicate guard fired
        logger.warning("PAPER TRADER rejected trade for %s (secondary duplicate guard)", pair)


def _weekend_close_check() -> None:
    """
    Two-phase Friday close so nothing carries weekend gap risk (OANDA FX
    closes after Friday's session and reopens Sunday evening — a large
    weekend news gap can blow straight through a normal SL):

    1. Profit-seeking window (config.WEEKEND_CLOSE_PROFIT_WINDOW_HOURS
       before SESSION_END_UTC): close a trade as soon as price has moved
       favorably past its entry — "try to end trades in profit as best as
       possible" (user request 2026-07-29) — but leave it open otherwise,
       there's still time for it to turn.
    2. Hard deadline (SESSION_END_UTC itself): force-close whatever's left,
       unconditionally, regardless of P&L. This guarantee is absolute; the
       profit-seeking above is best-effort on top of it, never a reason to
       skip it.

    Naturally idempotent, no extra state needed: once a pair's trade is
    closed, get_open_trade()/open_pairs() return nothing on the next tick,
    and the existing session-entry gate (SESSION_START_UTC <= hour <
    SESSION_END_UTC) already blocks anything new from opening again until
    Monday — so this doesn't need to track "already ran this week" itself.
    """
    if not getattr(config, "WEEKEND_CLOSE_ENABLED", True):
        return
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() != config.WEEKEND_CLOSE_WEEKDAY_UTC:
        return
    profit_window_start = config.SESSION_END_UTC - getattr(config, "WEEKEND_CLOSE_PROFIT_WINDOW_HOURS", 0)
    if now_utc.hour < profit_window_start:
        return
    hard_deadline = now_utc.hour >= config.SESSION_END_UTC

    connector = _oanda_connector
    if connector is None:
        return

    def _in_profit(direction: str, entry: float, price: float) -> bool:
        return price > entry if direction == "long" else price < entry

    if config.MODE == "paper" and _paper_trader:
        for pair in config.FOREX_PAIRS:
            t = _paper_trader.get_open_trade(pair)
            if not t:
                continue
            try:
                df = connector.get_candles(pair, config.TIMEFRAMES["primary"], 1)
                if df is None or len(df) < 1:
                    continue
                price = float(df.iloc[-1]["close"])
                profitable = _in_profit(t["direction"], t["entry"], price)
                if not hard_deadline and not profitable:
                    continue   # still time before the deadline, not in profit yet — hold
                reason = "weekend_close" if hard_deadline and not profitable else "weekend_close_profit"
                if _paper_trader.manual_close(t["id"], price, reason=reason):
                    logger.info("WEEKEND CLOSE (%s): %s trade %s closed at %.5f",
                                "in-profit" if profitable else "deadline", pair, t["id"], price)
                    _sync_paper_state()
            except Exception as exc:
                logger.error("Weekend close failed for %s: %s", pair, exc)
    elif config.MODE in ("demo", "live") and _trade_manager_fx:
        for pair in list(_trade_manager_fx.open_pairs()):
            t = _trade_manager_fx.get_open_trade(pair)
            if not t:
                continue
            try:
                price = None
                try:
                    df = connector.get_candles(pair, config.TIMEFRAMES["primary"], 1)
                    if df is not None and len(df) >= 1:
                        price = float(df.iloc[-1]["close"])
                except Exception:
                    pass
                profitable = price is not None and _in_profit(t["direction"], t["entry"], price)
                if not hard_deadline and not profitable:
                    continue
                reason = "weekend_close" if hard_deadline and not profitable else "weekend_close_profit"
                _trade_manager_fx.close_trade(t["id"], reason=reason)
                logger.info("WEEKEND CLOSE (%s): %s trade %s closed ahead of the weekend",
                            "in-profit" if profitable else "deadline", pair, t["id"])
                _sync_live_state()
            except Exception as exc:
                logger.error("Weekend close failed for %s: %s", pair, exc)


def _tick_paper_trades() -> None:
    """Feed the latest candle to PaperTrader so SL/TP is evaluated."""
    for pair in config.FOREX_PAIRS:
        if not _paper_trader.get_open_trade(pair):
            continue
        connector = _oanda_connector
        if connector is None:
            continue
        try:
            gran = config.TIMEFRAMES["primary"]
            # Only fetch the extra candle history ATR needs when the feature is
            # actually on — keeps today's API-call volume unchanged otherwise.
            count   = config.ATR_TRAILING_PERIOD + 10 if config.ATR_TRAILING_ENABLED else 2
            df      = connector.get_candles(pair, gran, count)
            if df is None or len(df) < 1:
                continue
            last    = df.iloc[-1]
            atr_value = None
            if config.ATR_TRAILING_ENABLED and len(df) >= config.ATR_TRAILING_PERIOD:
                try:
                    atr_value = float(_calc_atr(df["high"], df["low"], df["close"],
                                                 config.ATR_TRAILING_PERIOD).iloc[-1])
                except Exception as exc:
                    logger.debug("ATR calc failed for %s: %s", pair, exc)
            closed_before = {t["id"] for t in _paper_trader.closed_trades}
            _paper_trader.update(pair, last["high"], last["low"], last["close"], atr_value=atr_value)
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


def _tick_live_trades() -> None:
    """
    Live-mode counterpart to _tick_paper_trades() — feeds the latest candle
    to TradeManager so TP1/TP2/TP3, breakeven, and ATR trailing are actually
    evaluated for real broker trades. Previously TradeManager.on_candle_close()
    was never called anywhere, so live trades only ever had their initial
    SL (attached at order placement) — nothing else would have fired.
    """
    for manager in (_trade_manager_fx,):
        if manager is None:
            continue
        for pair in manager.open_pairs():
            connector = _oanda_connector
            if connector is None:
                continue
            try:
                gran = config.TIMEFRAMES["primary"]
                count = config.ATR_TRAILING_PERIOD + 10 if config.ATR_TRAILING_ENABLED else 2
                df    = connector.get_candles(pair, gran, count)
                if df is None or len(df) < 1:
                    continue
                last = df.iloc[-1]
                atr_value = None
                if config.ATR_TRAILING_ENABLED and len(df) >= config.ATR_TRAILING_PERIOD:
                    try:
                        atr_value = float(_calc_atr(df["high"], df["low"], df["close"],
                                                     config.ATR_TRAILING_PERIOD).iloc[-1])
                    except Exception as exc:
                        logger.debug("ATR calc failed for %s: %s", pair, exc)
                closed_before = {t["id"] for t in manager.closed_trades}
                manager.on_candle_close(
                    pair,
                    {"high": last["high"], "low": last["low"], "close": last["close"]},
                    atr_value=atr_value,
                )
                _sync_live_state()
                newly_closed = [t for t in manager.closed_trades if t["id"] not in closed_before]
                if newly_closed:
                    cooldowns = dict(state.get_key("trade_cooldowns", {}))
                    for t in newly_closed:
                        cooldowns[t["pair"]] = datetime.now(timezone.utc)
                        logger.info(
                            "Cooldown started for %s (trade %s closed, %dh cooldown)",
                            t["pair"], t["id"], config.TRADE_COOLDOWN_HOURS,
                        )
                        adaptive_params.update(t["pair"], manager.closed_trades)
                    state.update(trade_cooldowns=cooldowns)
                if tg_enabled():
                    for t in newly_closed:
                        try:
                            tg_trade_closed(t)
                        except Exception:
                            pass
            except Exception as exc:
                logger.error("Live tick failed for %s: %s", pair, exc)


def _sync_live_state() -> None:
    """Push TradeManager's open/closed trades into dashboard state — mirrors
    _sync_paper_state(). Without this, live trades wouldn't appear on the
    dashboard even though they're real broker positions."""
    open_trades, closed_trades = [], []
    for manager in (_trade_manager_fx,):
        if manager is None:
            continue
        open_trades.extend(manager.open_trades.values())
        closed_trades.extend(manager.closed_trades)
    state.update(open_trades=open_trades, closed_trades=closed_trades)


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
    if config.MODE not in ("demo", "live") or not _oanda_connector:
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
    accuracy = _pattern_learner.get_accuracy()
    top      = _pattern_learner.top_features(3)
    try:
        from learning.pattern_learner import _load_closed, LOG_PATH
        df = _load_closed(LOG_PATH)
        n  = len(df) if df is not None else 0
    except Exception:
        n = 0
    state.update(ml_stats={"accuracy": accuracy, "top_features": top, "n_samples": n})


def _diagnostic_scan() -> None:
    """
    Runs between M30 closes (:10/:20 after each close) to refresh dashboard signal state.
    Uses completed candles only. NEVER opens trades.
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


def _live_diagnostic_scan() -> None:
    """
    Runs every 60 s via IntervalTrigger to keep the Signal Monitor current
    using the in-progress M30 bar (live price), not just the last completed bar.

    Fetches 250 completed M30 bars + the forming bar via get_live_candles(),
    runs the signal engine in display-only mode (no_audit=True), and updates
    dashboard state. NEVER opens or modifies trades.
    """
    if not _forex_engine or not _oanda_connector:
        return
    all_pairs = config.FOREX_PAIRS + getattr(config, "FOREX_WATCH", [])
    for pair in all_pairs:
        try:
            # 251 candles: last row is the in-progress bar with live OHLC
            df_live = _oanda_connector.get_live_candles(
                pair, config.TIMEFRAMES["primary"], 251
            )
            if df_live is None or len(df_live) < 50:
                continue
            # Confirm TF still uses completed bars — no repainting risk there
            df_confirm = _oanda_connector.get_candles(
                pair, config.confirm_tf_for(pair), 250
            )
            if df_confirm is None or len(df_confirm) < 50:
                continue
            sig = _forex_engine.run(
                pair, "forex", no_audit=True,
                candles_override=(df_live, df_confirm),
            )
            if sig is None:
                continue
            tf = sig.get("timeframe", config.TIMEFRAMES["primary"])
            sig["live_bar"] = True   # flag so dashboard can show "LIVE" label
            if sig.get("no_signal"):
                state.update_signal(pair, 0, "—", timeframe=tf)
                state.update_signal_detail(pair, sig)
            else:
                state.update_signal(pair, sig["score"], sig["direction"], timeframe=tf)
                state.update_signal_detail(pair, sig)
        except Exception as exc:
            logger.debug("Live diagnostic scan %s: %s", pair, exc)


# ══════════════════════════════════════════════════════════════════════════════
# Initialisation
# ══════════════════════════════════════════════════════════════════════════════

def _init_connectors():
    oanda = None

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

    return oanda


def _init_engines(oanda):
    forex_eng = SignalEngine(oanda.get_candles) if oanda else None
    return forex_eng


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    global _oanda_connector, _paper_trader
    global _forex_engine
    global _trade_manager_fx

    logger.info("══════════════════════════════════════════════════════")
    logger.info("  Trading Bot  |  Mode: %s", config.MODE.upper())
    logger.info("  Forex pairs : %s", ", ".join(config.FOREX_PAIRS))
    logger.info("══════════════════════════════════════════════════════")

    # ── Connectors ────────────────────────────────────────────────────────────
    _oanda_connector = _init_connectors()
    state.update(forex_connector=_oanda_connector)

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

    # ── Signal engines ────────────────────────────────────────────────────────
    _forex_engine = _init_engines(_oanda_connector)

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
        # threaded=True — the Werkzeug dev server otherwise handles one HTTP
        # request at a time, so the browser's chart/price polling callbacks
        # queue up behind whichever request is in flight (most noticeably
        # during the ~30-min on_candle_close scan burst). threaded=True
        # spawns a new thread per incoming request instead, so those polls
        # get serviced concurrently rather than waiting in line.
        dash_app.run(host="0.0.0.0", port=8050, debug=False, use_reloader=False,
                     threaded=True)
    t = threading.Thread(target=_run_dash, daemon=True, name="dashboard")
    t.start()
    logger.info("Dashboard: http://localhost:8050")

    # ── Stream health tracking (diagnostic — added 2026-07-06) ────────────────
    # Populated by _on_stream_price below; read by _scheduler_heartbeat to help
    # pin down the recurring on_candle_close misfires (see
    # bugs_scheduler_reliability memory). Defined unconditionally so the
    # heartbeat job never hits a NameError even if streaming isn't running.
    _stream_health = {"last_tick": None}

    # ── OANDA price stream — real-time SL/TP monitoring ──────────────────────
    if config.MODE == "paper" and _oanda_connector and _paper_trader:
        forex_pairs = config.FOREX_PAIRS + getattr(config, "FOREX_WATCH", [])
        if forex_pairs:
            import time as _time

            def _on_stream_price(instrument: str, bid: float, ask: float) -> None:
                """Called on every tick from the OANDA stream."""
                _stream_health["last_tick"] = _time.time()
                # Feed the dashboard's live price cache so the UI can show
                # sub-second prices instead of polling REST every 5s — see
                # state.get_live_price(). Cheap: just an in-memory dict write.
                state.update_live_price(instrument, bid, ask)
                if not _paper_trader or not _paper_trader.get_open_trade(instrument):
                    return
                try:
                    _tick_start = _time.monotonic()
                    closed = _paper_trader.tick_check(instrument, bid, ask)
                    _tick_elapsed = _time.monotonic() - _tick_start
                    if _tick_elapsed > 0.5:
                        logger.warning(
                            "STREAM: tick_check for %s took %.2fs (thread=%s)",
                            instrument, _tick_elapsed, threading.current_thread().name,
                        )
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
    # M30 candle closes at :00 and :30 — full scan + trade evaluation
    scheduler.add_job(on_candle_close, CronTrigger(minute="0,30"), id="m30_close")
    # Between-close diagnostic scans (:10/:20 after each M30 close) — no trades
    scheduler.add_job(_diagnostic_scan, CronTrigger(minute="10,20,40,50"), id="diag_scan")
    # Live candle feed — runs every 60 s to keep Signal Monitor current mid-bar
    scheduler.add_job(_live_diagnostic_scan, IntervalTrigger(seconds=60), id="live_scan")

    # Diagnostic heartbeat (added 2026-07-06) — see bugs_scheduler_reliability
    # memory. on_candle_close has silently missed its 30-min fire several
    # times (20-90+ min gaps), recovering right around price-stream reconnect
    # log lines. This job's only purpose is to prove, next time it happens,
    # whether the scheduler's executor pool is starved entirely (this job
    # also stops firing) or just specific jobs are queued/blocked (this job
    # keeps firing on schedule while others miss) — and whether the price
    # stream itself went silent for the whole gap.
    def _scheduler_heartbeat():
        last_tick = _stream_health.get("last_tick")
        if last_tick is None:
            since_tick = "no ticks yet"
        else:
            since_tick = f"{time.time() - last_tick:.0f}s ago"
        logger.info(
            "HEARTBEAT  thread=%s  last stream tick=%s",
            threading.current_thread().name, since_tick,
        )
    scheduler.add_job(_scheduler_heartbeat, IntervalTrigger(seconds=30), id="heartbeat")

    # Shadow-outcome resolver (added 2026-07-07) — walks 'skipped' signals
    # (scored but never traded) forward through real candles to label them
    # would_win/would_lose, so the ML trains on near-misses and gate-rejected
    # setups too, not just trades actually taken (learning/shadow_outcomes.py).
    # Runs hourly in a background thread — does its own OANDA candle fetches
    # per pending pair, kept off the scheduler thread so it can never
    # contribute to an on_candle_close freeze.
    if _oanda_connector:
        def _shadow_outcomes_job():
            threading.Thread(
                target=shadow_outcomes.resolve_pending,
                args=(_oanda_connector,),
                daemon=True,
                name="shadow-outcomes",
            ).start()
        scheduler.add_job(_shadow_outcomes_job, IntervalTrigger(minutes=60), id="shadow_outcomes")

    # Weekly WFO re-fit — Sunday 02:00 UTC, runs in background thread (non-blocking)
    if getattr(config, "WFO_ENABLED", True) and _oanda_connector:
        def _wfo_job():
            # WFO only tunes strategy_ema_cci_macd params — skip pairs routed to a
            # different strategy via STRATEGY_OVERRIDE (e.g. GBP_USD/breakout_retest),
            # they'd otherwise be re-tuned weekly for a strategy they don't run.
            _override = getattr(config, "STRATEGY_OVERRIDE", {})
            _wfo_pairs = [p for p in config.FOREX_PAIRS if _override.get(p, "ema_bounce") == "ema_bounce"]
            # Run stalest (or never-fitted) pairs first — the sequential run_all_pairs
            # loop can take ~2h across a full roster and has been killed mid-run by
            # scheduler freezes, so a fixed config-order run always finished the same
            # early pairs and starved the rest. Sorting by fitted_at ascending (missing
            # entries sort first, "" < any ISO timestamp) means a crash costs the least.
            _fitted = wfo_optimizer.summary()
            _wfo_pairs.sort(key=lambda p: _fitted.get(p, {}).get("fitted_at", ""))
            threading.Thread(
                target=wfo_optimizer.run_all_pairs,
                args=(_oanda_connector.get_candles, _wfo_pairs),
                kwargs={"market": "forex"},
                daemon=True,
                name="wfo-refit",
            ).start()
        scheduler.add_job(_wfo_job, CronTrigger(day_of_week="sun", hour=2, minute=0), id="wfo_refit")
        logger.info("WFO weekly re-fit scheduled — Sundays 02:00 UTC")
    state.update(last_scan_time=datetime.now(timezone.utc))
    logger.info("Scheduler ready — M30 close on :00/:30, diagnostics at :10/:20/:40/:50, live feed every 60s")
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
