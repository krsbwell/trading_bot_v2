# Todo: Ground-up trend-following strategy (2026-08-28)

## Context / diagnosis (done — see chat for full numbers)
- [x] Pulled real trade history from data/signal_log.csv: 47 live trades Apr15–Jul30, 38.3% WR, net +289 pips / +$60.63, almost entirely carried by USD_CAD; other pairs flat/negative
- [x] Confirmed zero real trades have executed in all of August (79 signals, all shadow would_win/would_lose/expired/skipped) — layered gates (min score, ML-probability gate, H4 gate, news, cooldown) have collectively throttled live firing to nothing
- [x] Confirmed 46/47 real trades were already H4-trend-aligned — "fighting the trend" isn't the mechanism
- [x] Confirmed current live strategy (engine/strategy_ema_cci_macd.py) is a countertrend EMA-pullback system with an ADX>28 hard gate that explicitly SKIPS strong-trend regimes — the opposite of what a stacked-MA trend strategy wants
- [x] User decision: new strategy will be shadow-tested (logged, zero risk) before ever going live, not swapped in directly

## Build: new trend-stack strategy module
- [ ] New file `engine/strategy_trend_stack.py` — separate from strategy_ema_cci_macd.py, does not touch the live path
- [ ] Direction filter: EMA20 > EMA50 > EMA200 (M30) = long bias; EMA20 < EMA50 < EMA200 = short bias; anything else = no trade
- [ ] Entry rule (kept to 1–2 conditions, no 7-factor scoring): pullback to EMA20 with the stack still aligned, OR fresh stack alignment forming — pick one, keep it simple
- [ ] Exit rule: trend-following (trail stop behind EMA50 or ATR-based trail), not the existing fixed 3-tier TP ladder
- [ ] Stop loss: structural (beyond EMA50 or recent swing), not the current mid-EMA ±1pip rule built for mean reversion

## Validate before shadow-testing
- [ ] Backtest on real OANDA history for all active pairs, M30
- [ ] Real train/holdout split (not the same in-sample-only mistake from the 8/11 WFO bug) — report WR, PF, max drawdown, pips on the HOLDOUT portion only
- [ ] Show you the actual numbers — go/no-go decision before shadow phase, not after

## Shadow phase (only if backtest numbers are real, not just "positive")
- [ ] Wire into main.py as a parallel, non-trading signal path (logs what it would do, same shadow-outcome mechanism already in data_collector.py) — does not touch the live strategy or open real/paper trades
- [ ] Run for an agreed window (proposed: 2 weeks) on 1–2 pairs
- [ ] Compare shadow results to backtest — if they diverge a lot, that's a signal the backtest overfit, not a green light

## Review
(to fill in after implementation)

---

# Incident: weekend-close broker desync (2026-09-02)

Found while answering "what trade is still open?" — dashboard's "1 open" is
OANDA's own live count, not the bot's tracker, and the two had drifted apart.

## What happened
- [x] EUR_AUD trade 168 opened 2026-08-28 20:30 UTC, long, entry 1.61787
- [x] `_weekend_close_check()`'s hard-deadline close call for it returned
      from `connector.close_trade()` without raising, so local state marked
      it closed flat at $0 (`weekend_close`) — but OANDA has no fill or
      reject transaction for it at all; the close never actually reached
      the broker
- [x] Position stayed open, unmanaged (no trailing, no TP monitoring, dashboard
      blind to it) for 5 days until its original static SL finally triggered
      for real: -$4.75, at 2026-09-02 21:59 UTC (confirmed via OANDA
      transaction id 198, mid-investigation — a close attempt I made moments
      earlier failed with TRADE_DOESNT_EXIST because it had just stopped out)
- [x] main.py's `_reconcile_live_positions()` exists to catch exactly this
      but only runs once at startup and only logs a warning — never
      surfaced, and the log line itself has since rotated out of retention

## Fixes applied
- [x] `TradeManager.close_trade()` now re-queries the broker's open trades
      after calling `connector.close_trade()` and raises rather than
      finalizing locally if the broker still reports the trade open —
      `trade/trade_manager.py`
- [x] New `TradeManager.reconcile_orphaned_trades()` — mirror image of the
      existing `reconcile_open_trades()`; adopts any broker-open trade with
      no local record (using the broker's own entry/units/SL) so it's
      actively managed again, flagged `reconciled_orphan` — `trade/trade_manager.py`
- [x] Wired into `_tick_live_trades()` (runs every tick, not just startup)
      and into `_weekend_close_check()`'s demo/live branch before it iterates
      pairs — `main.py`
- [x] 9 new tests (`TestTradeManagerReconcileOrphanedTrades`,
      `TestCloseTradeVerifiesBrokerState`) — full suite: 498/498 passing
- [x] Corrected the historical record for trade 168 in
      `data/live_state_forex.json` (real exit 1.61523, real pnl -$4.75,
      reason `sl`) so stats/ML training aren't trained on a fabricated $0 close
- [x] Saved memory: `bugs_weekend_close_desync`

## Still open
- [ ] Live bot process (running since 8/29, PID chain under Task Scheduler's
      TradingBot_v2) needs a restart to pick up the fix + the corrected JSON
      — not yet restarted, pending user confirmation (no trade currently
      open, so this is a safe moment)
- [ ] Root cause of *why* the original OANDA close call silently produced
      no fill and no reject transaction is still unconfirmed (most likely a
      transient failure during Friday's rollover window) — that day's
      detailed logs already rotated out of retention before this
      investigation, so it can't be pinned down further after the fact
