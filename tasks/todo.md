# Explore a breakout-retest strategy (backtest-only)

## Context

The trend-follow experiment (see `project_trend_follow_experiment.md` memory) was
shelved — reusing EMA-bounce's touch-and-bounce entry logic in the trending regime
didn't work and actively hurt the strongest pairs. User's next idea: try a
*genuinely different* entry mechanic — breakout-retest — rather than another
flavor of the same recipe. Considered EMA-cross as a cheaper alternative first;
user chose to go straight to breakout-retest since it's more likely to find real
new edge rather than rediscover the same whipsaw problem.

**Concept:** price breaks a support/resistance zone, pulls back to retest that
broken zone (now acting as the opposite role — old resistance becomes new
support, or vice versa), and a rejection candle at the retest confirms the level
held. Enter in the breakout direction on that rejection.

**Reuse audit done first (see below) — this needs much less new code than the
"build a whole new indicator stack" fear implied:**
- `engine/strategy_market_structure.py`'s `get_sr_zones()` already identifies
  support/resistance zones from recent pivots (upper/lower/price/touches/tested)
- `detect_bos_choch()` already detects the breakout event itself (Break of
  Structure — price crossing the most recent pivot high/low)
- `engine/strategy_price_action.py`'s `detect_patterns()` already detects
  rejection candles (pin bar, engulfing, marubozu, morning/evening star) on the
  current bar
- `risk/risk_manager.get_tp_levels()` is fully generic (pure R:R math), reusable
  as-is

**Genuinely new code needed:**
1. Retest detection — has price returned to within a band of the broken zone,
   within N bars of the break, without closing back through it (i.e. the break
   is still valid)?
2. A retest-specific stop-loss — beyond the retest zone boundary, not the
   EMA-based `get_stop_loss()` in `strategy_ema_cci_macd.py` (confirmed not
   reusable as-is, but its ATR-fallback pattern is worth mirroring)

## Design decisions (documented so they can be revisited if results are odd)

- **No ADX gate.** Breakouts happen as a trend is *starting* — ADX is often
  still rising through 20-28 during the breakout+retest, not yet past the
  threshold. Gating on ADX>28 like the failed trend-follow experiment did would
  likely miss the entry window entirely. This strategy is regime-independent by
  design; the retest+rejection confirmation is the filter, not ADX.
- **Same session gate (04:00-17:00 UTC)** as the other two strategies, for
  consistency — already established this matters for these pairs' noise levels.
- **Retest window: 10 bars** (5 hours on M30) after a detected BoS — arbitrary
  first-pass choice, easy to tune later if backtest results are close but not
  quite there.
- **Retest tolerance band: 0.3×ATR** around the broken zone boundary — slightly
  wider than EMA-bounce's 0.25×ATR touch band since S/R zones are already
  ±0.5×ATR wide themselves (from `get_sr_zones()`), so the retest doesn't need
  to be pixel-perfect.

## Todo

### 1. New strategy module
- [ ] Create `engine/strategy_breakout_retest.py`:
      - `check_buy_signal(pair, df_h1, df_h4, adaptive=None)` /
        `check_sell_signal(...)` — same signature as the other two strategies
        so it plugs into `run_backtest()`'s `buy_fn`/`sell_fn` parameters
        (already generic from the trend-follow work)
      - Reuse `detect_pivots`, `classify_structure`, `detect_bos_choch`,
        `get_sr_zones` from `engine.strategy_market_structure`
      - Reuse `detect_patterns` from `engine.strategy_price_action`
      - New: `_find_retest(df, zone, direction, lookback=10, band_mult=0.3)` —
        scans recent bars for price returning to a broken zone without
        invalidating the break
      - New: `get_stop_loss(pair, df_h1, zone, direction)` — SL beyond the
        retest zone boundary, ATR-fallback pattern mirrored from
        `strategy_ema_cci_macd.get_stop_loss`
      - Separate diagnostic dicts (own module-level `_buy_diag`/`_sell_diag`)

### 2. Wire into the backtest CLI
- [ ] `backtest/runner.py`: add `"breakout_retest"` to the `--strategy` choices,
      import the new module's functions when selected (same pattern as
      `trend_follow` — no other runner changes needed, `buy_fn`/`sell_fn`
      params already exist from last session)

### 3. Run the screen
- [ ] Backtest all 12 current pairs with `--strategy breakout_retest`, 3500 M30
      bars
- [ ] Report results in the same ranked-table format as the previous two screens

### 4. Verification
- [ ] `python -m py_compile` on new/changed files
- [ ] `python -m pytest -q` — confirm no regressions
- [ ] Confirm default (`ema_bounce`) and `trend_follow` CLI paths still work
      unaffected

## Review

*(fill in after implementation, including backtest results and a
recommendation)*
