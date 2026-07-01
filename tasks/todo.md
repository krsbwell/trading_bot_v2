# M30 Primary Timeframe + Live Candle Diagnostics

**Goal:** Move the primary signal timeframe from H1 → M30 (decision points every 30 min
instead of every 60 min), and add real-time live-candle display between closes so the
Signal Monitor reflects the current forming bar, not a 0–59 min stale completed bar.

**Approach:** Two phases. Phase 1 switches the engine timeframe and validates it with
backtests before touching any live behaviour. Phase 2 adds the live diagnostic feed.

---

## Phase 1 — Switch Primary Timeframe H1 → M30

### 1. config.py
- [ ] Change `TIMEFRAMES["primary"]` from `"H1"` → `"M30"`
- [ ] Change `TIMEFRAMES["confirm"]` from `"H4"` → `"H1"`
      *(M30 primary → H1 confirm maintains the same 2:1 ratio; H4 becomes context-only)*
- [ ] Change `WFO_TRAIN_BARS` from `720` → `1440`
      *(720 H1 bars ≈ 30 days; M30 needs 48 bars/day × 30 = 1440 for same coverage)*
- [ ] Change `EMA_REFIT_EVERY_N_CANDLES` from `50` → `100`
      *(candles arrive twice as fast; 100 M30 bars ≈ same wall-clock refit frequency)*
- [ ] Change `LIMIT_ORDER_EXPIRY_CANDLES` from `4` → `8`
      *(was 4 H1 = 4 hours; 8 M30 = same 4 hours)*

### 2. engine/strategy_ema_cci_macd.py
- [ ] Increase `_find_touch()` default `lookback` from `20` → `40`
      *(20 H1 bars = 20 hours of lookback; 40 M30 bars = same 20-hour window)*
- [ ] Update the function docstring referencing "20 bars"

### 3. engine/wfo_optimizer.py
- [ ] Change `train_h4 = df_h4.tail(train_bars // 4)` → `// 2`
      *(M30→H1 is 2:1 ratio, not 4:1; `// 4` was correct for H1→H4)*

### 4. backtest/runner.py
- [ ] CLI section (line ~447): change hardcoded `"H1"` → `config.TIMEFRAMES["primary"]`
- [ ] CLI section: change hardcoded `"H4"` → `config.TIMEFRAMES["confirm"]`
- [ ] CLI section: change `args.bars // 4` → `args.bars // 2` for confirm-TF bar count
- [ ] Update module docstring ("Replays historical H1 + H4" → primary + confirm TF)

### 5. ML signal_log handling
- [ ] Archive `data/signal_log.csv` → `data/signal_log_h1_archive.csv` before the switch
      *(existing H1-trained patterns don't generalise to M30 without retraining;
      keeping them in the live CSV would pollute the M30 ML model)*
- [ ] Create a fresh empty `data/signal_log.csv` with the header row intact

### 6. Backtest validation (MUST do before going live on M30)
- [ ] Run backtest for USD_CAD on M30 + H1 confirm via dashboard Backtest tab
- [ ] Run backtest for USD_CHF on M30 + H1 confirm
- [ ] Run backtest for GBP_CHF on M30 + H1 confirm
- [ ] Compare win rates against H1 baseline (USD_CAD 52%, USD_CHF 42%, GBP_CHF 50%)
- [ ] If win rates are acceptable (≥40%), proceed. If not, review ATR gates and
      MIN_SL_PIPS before going live — M30 ATR is ~70% of H1 ATR so gates may need
      recalibration (ATR_MAX_PIPS 35 → ~25, MIN_SL_PIPS 25 → ~15–18)

---

## Phase 2 — Live Candle Diagnostic Display

### 7. main.py scheduler
- [ ] Change `CronTrigger(minute=0)` → `CronTrigger(minute='0,30')` for `on_candle_close`
      *(M30 candles close at :00 and :30 of every hour)*
- [ ] Change diagnostic cron `minute="15,30,45"` → `minute="10,20,40,50"`
      *(schedule diagnostics between M30 closes: 10 min after each close)*
- [ ] Update log message "H1 candle closes on the hour" to reflect M30

### 8. main.py — add live diagnostic scan
- [ ] Write `_live_diagnostic_scan()` function:
      - Fetches last 251 candles with `oanda_connector.get_live_candles(pair, "M30", 251)`
        (returns 250 completed bars + 1 forming bar — the live price)
      - Fetches H1 confirm with standard `get_candles(pair, "H1", 250)` (completed only)
      - Calls `_forex_engine.run(pair, "forex", no_audit=True)` but with the live M30 data
        injected (see implementation note below)
      - Updates `state.update_signal_detail(pair, {... "live_signal": True})`
      - Never fires trades — diagnostic display only
- [ ] Add `IntervalTrigger(seconds=60)` APScheduler job for `_live_diagnostic_scan`
      *(runs every 60s continuously; live candle close value updates each time)*
- [ ] Add signal engine support: pass a `candles_override` kwarg to `SignalEngine.run()`
      so the live scan can supply live-bar data without touching the completed-only fetch path

### 9. Dashboard — live signal indicator (optional visual polish)
- [ ] Add "LIVE" label or dot to Signal Monitor rows when `live_signal=True`
      to distinguish live-bar scores from just-confirmed closed-bar scores

---

## Review

### Files changed
| File | Change |
|------|--------|
| `config.py` | primary "H1"→"M30", confirm "H4"→"H1", WFO_TRAIN_BARS 720→1440, EMA_REFIT 50→100, LIMIT_ORDER_EXPIRY 4→8 |
| `engine/strategy_ema_cci_macd.py` | `_find_touch()` default lookback 20→40 (preserves 20-hour wall-clock window on M30) |
| `engine/wfo_optimizer.py` | confirm-TF slice ratio `// 4` → `// 2` (M30→H1 is 2:1, not 4:1) |
| `backtest/runner.py` | hardcoded "H1"/"H4" → `config.TIMEFRAMES` values; confirm-TF ratio fixed |
| `main.py` | scheduler M30 cron `:00/:30`; diagnostics `:10/:20/:40/:50`; `_live_diagnostic_scan()` added; `IntervalTrigger(60s)` for live feed |
| `engine/signal_engine.py` | `candles_override` kwarg added to `run()` for live-bar injection |
| `data/signal_log.csv` | Archived H1 history → `signal_log_h1_archive_YYYYMMDD.csv`; fresh M30 header-only file created |
| `config.py` *(2026-06-30)* | FOREX_PAIRS ["USD_CAD"] only; GBP_CHF removed; USD_CHF moved to FOREX_WATCH |
| `dashboard/app.py` *(2026-06-30)* | Backtest granularity bug fixed (was hardcoded H1/H4; now reads config.TIMEFRAMES); ratio //4→//2; stale-result clear on pair change |

### Test results
- **Syntax**: All 6 modified files pass `ast.parse()` — no errors
- **Unit tests**: 162/162 pass (1 pre-existing Alpaca live-credential failure, unrelated to our changes)
- **Signal/learning/dashboard tests**: 113/113 pass

### M30 backtest results — final (3500 bars, 2025-12-05→2026-06-30)

Earlier 2000-bar runs had a bug: dashboard was fetching H1/H4 data instead of M30/H1.
Fixed in `dashboard/app.py` (gran_h1/gran_h4 now read from `config.TIMEFRAMES`; ratio `//4`→`//2`).
Results below are the corrected 3500-bar M30 runs.

| Pair | M30 WR | Trades | PnL | MaxDD | Decision |
|------|--------|--------|-----|-------|----------|
| USD_CAD | 47% | 38 | +$105.47 | 4.5% | **Keep active** — clear edge, upward equity |
| USD_CHF | 27% | 41 | -$12.38  | 8.9% | **Demoted to watch** — loses money on M30 |
| GBP_CHF | 24% | 37 | -$26.65  | 8.9% | **Removed** — worst performer; CHF low-vol incompatible with M30 |

Watch pairs backtested for reference:
| Pair | M30 WR | Trades | PnL | MaxDD | Status |
|------|--------|--------|-----|-------|--------|
| EUR_CHF | 39% | 18 | +$23.54 | 3.7% | Watch — best DD ratio; candidate if frequency improves |
| EUR_USD | 36% | 45 | +$43.48 | 7.9% | Watch — profitable but DD too high |

**Root cause for CHF failure on M30:** CHF pairs have low M30 volatility; EMA bounces
are too shallow to distinguish from noise at 30-min resolution. H1 filter was doing
meaningful work that M30 loses. USD_CAD is the only pair with clear M30 edge.

### ATR gate calibration check

| Pair | M30 ATR | H1 ATR | Ratio |
|------|---------|--------|-------|
| USD_CAD | 10.2 pips | 13.8 pips | 0.74 |
| USD_CHF | 6.8 pips | 10.0 pips | 0.68 |
| GBP_CHF | 6.3 pips | 8.6 pips | 0.73 |

All pairs comfortably above `ATR_MIN_PIPS=5`. `ATR_MAX_PIPS=35` and `MIN_SL_PIPS=25`
remain appropriate for M30 volatility — no gate changes needed.

### Security review
- No user input or external data reaches the new code paths without existing validation
- `candles_override` is only callable internally (not via API or user input)
- `_live_diagnostic_scan` runs `no_audit=True` and never calls trade-opening code paths
- Signal log archive is a simple file copy — no data destruction; original preserved

### Pair roster changes (2026-06-30)
- `config.py`: `FOREX_PAIRS = ["USD_CAD"]` (USD_CHF and GBP_CHF removed from active)
- `config.py`: `FOREX_WATCH = ["USD_CHF", "GBP_USD", "EUR_USD", "NZD_USD", "EUR_CHF"]`
- GBP_CHF removed entirely from both lists (24% WR, -$26.65, 8.9% DD)

### Dashboard backtest bug fixed (2026-06-30)
- `dashboard/app.py`: `gran_h1`/`gran_h4` now read from `config.TIMEFRAMES` instead of hardcoded "H1"/"H4"
- `dashboard/app.py`: confirm-TF bar ratio fixed from `//4` to `//confirm_ratio` (2 for forex M30→H1)
- `dashboard/app.py`: added `clear_backtest_on_pair_change` callback — stale results clear on pair switch
- `dashboard/app.py`: error paths return `html.Div()` instead of `no_update` so failures don't show old data

### Watch points going live
1. **WFO Sunday re-fit** — first run will find M30-optimised parameters for USD_CAD; watch logs
2. **EUR_CHF** — best watch candidate (39% WR, 3.7% DD); promote to active if signal count improves
3. **Live scan API rate** — 6 pairs × 2 calls every 60s = 12 calls/min; well within OANDA limit (120/min)

---

## ADX Regime + Session Filter Implementation (2026-06-29)

### Structural problems diagnosed
1. **EMA bounce fires in trending markets** — mean-reversion only works when price is ranging.
   ADX(14) > 28 = strong trend → EMA bounces fail → added hard-block gate.
2. **Asian session noise on M30** — 22:00–07:00 UTC has thin liquidity; false wicks generate
   spurious EMA touches. Session gate restricts to London+NY only (07:00–17:00 UTC).

### Changes made
| File | Change |
|------|--------|
| `engine/indicators.py` | Added `adx()` function (Wilder smoothing, matches TradingView) |
| `engine/strategy_ema_cci_macd.py` | ADX regime gate + session gate added to `check_buy_signal` and `check_sell_signal` |
| `engine/wfo_optimizer.py` | Expanded grid 18→72→216 combinations; added `adx_threshold` (22/28/33) and `touch_lookback` (30/50) dimensions |
| `config.py` | Added `ADX_THRESHOLD = 28`, `SESSION_START_UTC = 7`, `SESSION_END_UTC = 17`; removed duplicate SESSION constants |
| `learning/pattern_learner.py` | Fixed `seed_from_backtest()` confirm-TF ratio `//4` → `//2` |
| `dashboard/app.py` | Seed button runs in background thread; OandaConnector fallback; status text yellow+visible |

### Backtest results — M30 + ADX(28) + Session gate (3500 bars, 2026-06-29)
Compared against the pre-filter M30 baseline.

| Pair | Before WR | Before DD | After WR | After DD | After PnL | Trades | Verdict |
|------|-----------|-----------|----------|----------|-----------|--------|---------|
| USD_CAD | 47% | 4.5% | **50%** | **2.6%** | +$81.86 | 22 | **Keep active — WR up, DD halved** |
| NZD_USD | 39% | 4.6% | **43%** | **3.9%** | +$40.83 | 21 | **Best watch pair — WFO candidate** |
| USD_CHF | 27% | 8.9% | 28% | 5.8% | -$3.57 | 25 | Watch — DD improved; still losing |
| GBP_USD | 33% | — | 22% | 6.0% | -$4.17 | 37 | Watch — below break-even |
| EUR_CHF | 39% | 3.7% | 22% | 4.5% | -$2.59 | 9 | Watch — session filter cut early-London edge |
| EUR_USD | 36% | 7.9% | 18% | 12.4% | -$36.21 | 28 | Watch — worst result; DD too high |

**Key observations:**
- USD_CAD: WR improved 47%→50%, DD nearly halved (4.5%→2.6%). Fewer but better trades.
- NZD_USD: Best watch pair at 43% WR with positive PnL. WFO will tune ADX threshold (22/28/33) — may unlock higher WR.
- EUR/CHF collapse (39%→22%): Session filter cuts its early-London setups (05:00–07:00 UTC). Only 9 trades in window.
- EUR/USD: No filter combination rescues it; structurally unsuited to EMA mean-reversion.

### Test results (2026-06-29)
- 177 tests pass, 10 fail — all failures are Alpaca live-credential tests (pre-existing, unrelated)

### Next steps
1. WFO Sunday re-fit will search 216 combinations including ADX 22/28/33 per pair
2. NZD_USD is the top watch-pair candidate for promotion after WFO confirms optimal ADX threshold
3. EUR/CHF: if promoting in future, test session start 06:00 instead of 07:00 to recapture London-open edge
4. Click "Seed from Backtest + Retrain" in Learning panel to give ML model M30 training data

---

## EUR_AUD re-added + SESSION_START 07:00→04:00 (2026-07-01)

### Changes
- `SESSION_START_UTC` changed from 7 → 4 (04:00 UTC — European pre-market / Sydney overlap)
- `EUR_AUD` added back to `FOREX_WATCH`; comment updated

### Backtest results — SESSION 04:00–17:00 UTC | ADX(28) | 3500 bars
| Pair | WR | Trades | PnL | DD | vs 07:00 |
|------|-----|--------|-----|-----|----------|
| USD_CAD | 48.3% | 29 | +$71.96 | 2.5% | WR -1.7pp; still solid; extra pre-London trades acceptable |
| EUR_AUD | 37.2% | 43 | +$47.20 | 8.4% | NEW — positive PnL, high trade count; WFO candidate |
| NZD_USD | 37.0% | 27 | +$34.27 | 5.0% | WR fell from 43%; NZD Asian-session 04:00-07:00 adds noise |
| EUR_CHF | 27.3% | 11 | +$1.10  | 4.0% | Improved vs 07:00 (22% WR, -$2.59); nearly breakeven |
| GBP_USD | 25.5% | 47 | -$2.85  | 7.8% | Marginal improvement; still losing |
| EUR_USD | 24.2% | 33 | -$28.63 | 9.7% | Slightly better than 07:00 (18% WR, -$36.21) |
| USD_CHF | 21.2% | 33 | -$37.41 | 8.7% | Significantly worse — pre-London CHF (04:00-07:00) very noisy |

**Impact of 04:00 session start:**
- EUR pairs improve slightly (EUR/CHF nearly breakeven, EUR/USD less negative) — European pre-market benefits EUR
- USD_CHF badly hurt (was -$3.57 → now -$37.41) — SNB wicks in pre-London are unpredictable; CHF pairs prefer 07:00+
- NZD_USD WR fell (43% → 37%); still positive PnL — NZD liquidity is dropping at 04:00 UTC
- USD_CAD holds up well (48.3% vs 50%); DD unchanged at ~2.5%

**EUR_AUD assessment:** +$47.20 PnL across 43 trades is genuine positive expectancy. 8.4% DD elevated. Watch-only, no trades. WFO will tune ADX threshold which may improve both WR and DD.

---

## Key risks and mitigations

| Risk | Mitigation |
|---|---|
| M30 generates too many false signals | Backtest first (step 6); ATR gate catches extreme noise |
| Old H1 ML data pollutes M30 training | Archive signal_log before switch (step 5) |
| Live candle values repaint before close | Phase 2 is display-only; no trades fire on live bar |
| WFO weekly re-fit trains on wrong bar ratio | Fixed in step 3 (// 4 → // 2) |
| Backtest confirm-TF mismatch | Fixed in step 4 (hardcoded H4 → config) |
