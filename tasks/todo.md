# Round 4: open items after the re-screen (2026-09-28)

- [x] Merge strategy-review-2026-09-28 -> main (fast-forward, e9056b9).
- [x] News blackout: pair coverage. The hand-kept currency->pairs table was
      stale: AUD_JPY (live now), NZD_USD, CHF_JPY, EUR_JPY had NO blackout,
      and GBP_CAD only for USD news. Now derived: the event currency is on
      either side of the pair. Real feed check: AUD_JPY had 0 high-impact
      events covered under the old table, 5 AUD events this week now.
- [x] News blackout: failure handling. A failed fetch (429 seen 09-27 and
      twice today) cached [] for 60 min = blackout silently off. Now the
      last good calendar is kept (memory + data/ff_calendar_cache.json,
      max 7 days old), retried every 5 min, with a loud ERROR when there
      is no calendar at all. Dead nextweek.xml URL removed (404 since 08-14).
- [x] pyyaml installed in venv (was in requirements.txt). configs/*.yaml
      now load. Only effective difference: ADAPTIVE_STRATEGY enabled=False,
      and no pair uses adaptive, so no trading change.
- [x] Checked, no change needed: EMA re-fit quirk only feeds the H4 display
      + logged features (not trading); diagnostic scans never open trades.
- [x] Tests: tests/test_forexfactory_blackout.py (5). 528 passed.
- [x] Restarted 02:29 and 02:32 EDT. The startup fetch hit 429 both times
      (my verification request made it worse). Watching for the first
      successful fetch + disk cache write.
- Security: new disk file holds public calendar data only; tests redirect
  it to tmp; no new endpoints (one URL removed).

---

# Round 3: make the backtest trustworthy, then re-screen all pairs (2026-09-28)

User: retest major + minor pairs for inclusion. A pair that fails is
REMOVED (FOREX_REJECTED), not put on watch. Then continue the agreed order.

- [x] 1. Live vs backtest: why GBP_CAD fires ~13x more live, and NZD_USD
      went 0/3 live vs 67% in backtest. Compare trade by trade and find the
      code-path difference.
- [x] 2. Spread in backtest/runner.py itself (per-pair typical spread,
      applied at entry), so the dashboard backtest button and WFO see it too.
- [x] 3. Pair screen: all 7 majors + 21 minors x {breakout_retest,
      trend_retest}, spread included, longer history. Criteria fixed BEFORE
      running (below). Pass -> FOREX_PAIRS; fail -> FOREX_REJECTED (removed
      from watch). Any currently-active pair that fails is flagged to the
      user before removal.
- [x] 4. Sequenced entry behind a flag (retest within N bars of the break,
      confirming candle on entry bar, MA not-against for the first entry,
      full MA cross for add-ons). Backtest vs current.
- [x] 5. ATR-trailed runner instead of the hard TP3 cap, behind a flag.
      Backtest vs current.
- [ ] 6. Tests, review, security check, restart, commit.

Inclusion criteria (set before seeing results):
- Strategy picked on the FIRST half of the history only.
- Judged on the SECOND half (out-of-sample): PF after spread >= 1.3,
  >= 8 trades in that half, max drawdown <= 10%.
- First half PF after spread >= 1.1 as well (not a one-period fluke).

Progress notes (round 3):
- 1 FOUND + FIXED (live): phantom stops. _tick_live_trades fed the last
  COMPLETED primary candle, which for H4 pairs was the signal candle itself
  (for up to 4h of 30-min checks). Its rejection wick is beyond the SL by
  definition, so trades closed at market instantly and re-entered next scan
  (GBP_CAD 08-28: 4 entries at 1.88174 in 2.5h). 8 of 27 live "sl" closes
  look like this. TradeManager.on_candle_close now skips candles with
  end <= open_time. + test.
- 1 FOUND + FIXED (live): one entry per (pair, direction, signal candle).
  An H4 signal re-triggered every scan, and with pyramiding on it could
  stack 3 positions on one setup. signal_engine adds "signal_bar"; main.py
  blocks the repeat with a BLOCKED audit row. + test. Caveat: in-memory,
  so a restart mid-candle could allow one repeat.
- 1 FINDING: most "bad" live GBP_CAD/NZD_USD trades predate 08-13 (the old
  EMA-bounce strategy), so they weren't evidence against the current setup.
- 2 FIXED: backtest/runner.py now pays config.SPREAD_PIPS (London-typical)
  at entry. + test.
- 2 FOUND + FIXED (backtest): H4 LOOK-AHEAD. Confirm candles were selected
  by OPEN time <= bar time, which included the still-forming H4 candle with
  its final high/low/close. Now only candles CLOSED by the bar's close.
  + test. CHF_JPY 3000-bar PF 2.78 -> 1.76; breakout pairs ~unchanged.
- 2 FOUND + FIXED (backtest): the strategy saw ALL history per bar vs live's
  250-candle window. config.LIVE_CANDLE_WINDOW=250 now used by both (also
  made the backtest O(n) instead of O(n^2)).
- 2 FOUND (not fixed): EMA cache keyed on candle count, so live never
  re-fits EMA periods except on restart (always exactly 250 candles).
- 2 FOUND (fixing in step 4): the backtest never allowed add-on entries
  (skips any bar with an open trade on the pair); live has since 09-16.
- 3 RESULT: 3 of 28 pass -> FOREX_PAIRS = GBP_USD (trend_retest), EUR_USD
  (trend_retest), AUD_JPY (breakout_retest). Removed (incl. live GBP_CAD,
  NZD_USD, CHF_JPY) and FOREX_WATCH emptied; numbers are in config's
  rejected list. EUR_USD added to pattern_learner._PAIR_ENCODING (=14).
  Note: AUD_JPY was paused on a 3-month test (PF 0.70) but passes on 1.5y.
  Short samples mislead both ways.
  Caveat: 28 pairs x 2 strategies = 56 tries; some passes can still be luck.
  Live results on these 3 are the real test.
- 4 BUILT (flags off): backtest now applies live's add-on rules
  (validate_pre_trade + addon_ok) instead of skipping every bar with an open
  trade. SEQUENCED_RETEST: retest_since_break() accepts a pullback on any
  candle after the break candle (within the 5-candle BOS window), used by
  breakout_retest AND trend_retest. PYRAMID_REQUIRE_MA_CROSS: add-ons need
  fast EMA over EMA100 in the trade direction (live: main.py via signal
  "ma_aligned"; backtest: runner). Strict entry diagnosis (34 trades): the
  candle was never missing; retest missing 20, MA cross missing 20.
- 5 BUILT (flag off): RUNNER_NO_TP3_CAP: with the ATR trail on (it has been
  since 09-16), the final 25% ignores TP3 and exits only on the trail. Live
  (TradeManager) + backtest (PaperTrader). + tests. 523 passed.
- 4/5 EVAL: 1.5y on GBP_USD/EUR_USD/AUD_JPY, variants base / no_addons /
  seq_retest / addon_ma / runner / all3. Adopt a change only if it helps the
  3-pair total in BOTH halves. Caveat: these 3 pairs were selected on this
  same history, so gains here are optimistic.
- 4/5 RESULT (1.5y, net $ per pair GBP_USD / EUR_USD / AUD_JPY = total):
    base (add-ons @55)  76.7 / 41.0 / 125.7 = 243
    no_addons           89.3 / 31.2 /  81.7 = 202  (add-ons: +41 overall, mixed by half)
    seq_retest          35.4 / 37.8 / 135.4 = 209  REJECT
    addon_ma            82.8 / 30.4 / 125.7 = 239  REJECT (mixed)
    runner (no TP3)     76.8 / 33.1 / 116.2 = 226  REJECT
    all3                41.2 / 35.1 / 119.6 = 196  REJECT
  All four flags stay False; code kept behind them for future re-tests.
  Note: GBP_USD base DD 10.1% (add-ons push it just over the 10% cap).
  Watch this at the 2026-10-04 pyramid review.

## Review (round 3, 2026-09-28)
- Live bugs fixed: phantom stops from pre-entry candles; repeated/stacked
  entries on one H4 signal candle.
- Backtester fixed: spread modeled; H4 look-ahead removed; live-sized
  250-candle window; add-on rules match live. Backtests are now a fair
  test of what the bot actually does (and much faster).
- Pairs: full 28-pair re-screen with criteria set in advance -> GBP_USD +
  EUR_USD (trend_retest), AUD_JPY (breakout_retest). 25 pairs removed,
  watch list emptied.
- Tested and rejected: sequenced retest, MA-cross for add-ons, uncapped
  runner (switches left off).
- Security: no credentials, endpoints or user input paths added. The new
  code is config flags + pure functions over candle data; the dashboard is
  unchanged except the earlier ML-log line; data written only to local files.
- NOT live until restart (needs the user's OK, since it removes 3 live pairs).

---

# Round 2: fixes approved 2026-09-27

User's instructions (verbatim intent): fix the trade-id mismatch first, then
make open + closed trade data persist for ML; remove the 25-pip SL minimum;
re-examine entries (candlestick patterns, price action, MA cross, MA gap:
no trades in consolidation until breakout + retest); remove the ADX gate
only if necessary; lower add-on score 65 -> 55 with a 7-day review; do
A1-A4.

Correction to Phase 1: "the ADX gate blocks trending markets" was wrong
for live trading. The ADX gate only exists in ema_bounce, and no active
pair routes to ema_bounce (breakout_retest x4, trend_retest CHF_JPY,
adaptive EUR_AUD). Nothing to remove. The user's consolidation rule
already exists as BREAKOUT_CONSOLIDATION_FILTER_ENABLED, but it's OFF
and was never backtested.

- [x] 1. A1: store the ML signal snapshot on the trade record at open
      (TradeManager + PaperTrader, both disk-persisted); record_close()
      writes from that snapshot, so it survives restarts and OANDA ids.
      Mark t["ml_logged"]. Regression test with a broker-style id.
- [x] 2. Backfill the 43 demo trades since 2026-07-31 from their trade
      records + the matching TRIGGERED signal_audit row (tagged so they
      can be told apart). Missing features are left blank, not invented.
- [x] 3. A2: learning health check. Closed bot trades not logged -> log
      ERROR + dashboard banner + Telegram.
- [x] 4. A3: ML gate off (config flag) until it has enough real outcomes
      and beats the base rate out-of-sample.
- [x] 5. A4: the silent score-floor drop in main.py writes a BLOCKED
      audit row with the reason.
- [x] 6. MIN_SL_PIPS 25 -> 0 (tested, NOT applied — worse on every pair; replaced by step 11) (ATR/structure decides). Backtest before vs
      after on the active pairs and report.
- [x] 7. PYRAMID_MIN_SCORE 65 -> 55, review checkpoint 2026-10-04.
- [x] 8. Full test suite passes.
- [x] 9. Entries: backtest the consolidation filter (on/off) per pair +
      review candlestick/MA-cross/MA-gap entry logic. Report before any
      live change.
- [x] 10. Restart the bot to load the changes; verify the snapshot is on
      the next opened trade.

Progress notes:
- 1: data_collector.make_snapshot() + record_close(snapshot=) -> bool;
  TradeManager stores t["ml_signal"], logs from it, sets t["ml_logged"];
  PaperTrader gets it via `extra`; main.py no longer uses the synthetic-id
  cache. Manual dashboard orders ("manual": True) are never logged as bot
  data. session/hour_utc now come from signal time, not close time (this
  was a second, smaller bug). tests/conftest.py now redirects SIGNAL_LOG so
  tests can never write to data/signal_log.csv.
- 2: scripts/backfill_demo_outcomes.py wrote 39 rows (source="backfill",
  kept out of training because pattern/candle/ATR features are unknown).
  Backup: data/signal_log.csv.bak_before_backfill_20260927. Rerun writes 0.
- 3: TradeManager.learning_health() -> main._check_learning_health()
  (ERROR log + one Telegram per trade) -> red "LEARNING GAP" banner or a
  grey "ML log: N outcomes recorded" line on the signal panel.
- 4: config.ML_GATE_ENABLED = False (P(win) still computed + logged).
- 5: main.py score-floor drop writes a BLOCKED "Score X < floor Y" audit row.
- 7: PYRAMID_MIN_SCORE 55; checkpoint comment in config.py + memory.
- 8: 519 passed (was 515 + new tests).
- FOUND: backtest/runner.py models NO spread. Every past backtest PF is
  optimistic, and more so for tight stops.
- FOUND: in breakout_retest the retest / break-held / candle pattern / MACD
  conditions are NOT required. Each is worth points and 2 of 4 is enough,
  so trades can fire with no retest and no candle confirmation.
- 6 RESULT (3000 bars, spread deducted; halves = 1st / 2nd half of trades):
    pair      floor25 PF (halves)        floor0 PF (halves)
    NZD_USD   2.91 (3.65/2.18) n=6       1.40 (4.24/0.61) n=9
    GBP_CAD   1.88 (2.54/1.41) n=16      0.82 (1.11/0.58) n=18
    GBP_USD   5.24 (3.77/7.88) n=14      1.44 (3.79/0.70) n=16
    CHF_JPY   3.72 (0.99/inf)  n=6       2.43 (1.55/3.40) n=6
    AUD_JPY   0.70 (0.54/0.93) n=8       0.20 (0.00/0.47) n=9
    EUR_AUD   0 trades either way (adaptive strategy doesn't fire in backtest)
  Removing the floor is worse on every pair. NOT applied; MIN_SL_PIPS is
  still 25, waiting on the user's decision. Caveat: 6-18 trades per pair,
  and backtest PF is far above live (live PF 1.03), so the backtest is
  still optimistic beyond spread.
- Fixed: PaperTrader logged an ERROR for every backtest close (no snapshot);
  it now skips quietly. 519 passed.

Round 2b (user 2026-09-28: agreed to all 3 + "try 2.0xATR"):
- [x] 11. SL floor = max(25 pips, 2.0 x ATR14) behind config.MIN_SL_ATR_MULT;
      backtest vs plain 25 (spread deducted); apply only if not worse.
- [x] 12. Pause AUD_JPY + EUR_AUD (FOREX_PAIRS -> FOREX_WATCH).
- [x] 13. Backtest entry + indicator variants: consolidation filter, strict
      entry, RSI-50 vs MACD, EMA25 vs EMA34, SMA200 vs EMA200; plus EUR_AUD
      on breakout_retest. Report before any live change.
- [x] 14. Restart + verify.
- 11: config.min_sl_distance(pair, pip, atr) = max(pip floor, MIN_SL_ATR_MULT*ATR),
  used by breakout_retest / trend_retest / atr_engine. MIN_SL_ATR_MULT=0.0
  (off) until the backtest result is in. New unit test in test_risk.py.
- 12: FOREX_PAIRS = NZD_USD, GBP_CAD, GBP_USD, CHF_JPY; AUD_JPY + EUR_AUD
  moved to FOREX_WATCH (no open trades on either). Roster tests updated.
- 13 prep: config knobs (defaults = old behavior): BREAKOUT_STRICT_ENTRY,
  MA_FAST_PERIOD=34, MA_SLOW_TYPE="ema", BREAKOUT_MOMENTUM="macd". One
  ribbon() helper now shared by breakout_retest + trend_retest. 518 passed.
- 11 RESULT (spread deducted): floor25 vs max(25, 2xATR)
    GBP_USD 5.24 -> 5.24 (identical, n=14)   NZD_USD 2.91 -> 2.91 (identical, n=6)
    GBP_CAD 1.88 -> 3.32 (n 16->14, WR 44->64%, net $38 -> $66)
    CHF_JPY 3.72 -> 2.78 (n=6, same WR, net $30 -> $20)
    Portfolio net $166 -> $183. Mixed: better on 1, worse on 1, same on 2.
    Not applied yet; presenting with the entry results.
- 13 RESULT (spread deducted): base reproduces GBP_USD 5.24, so the
  defaults are confirmed unchanged.
    rsi50 : ~no change anywhere (GBP_CAD 1.88->1.86, halves worse 4.64/0.77)
    ema25 / sma200 : ZERO change. They only affect the consolidation filter
      and strict entry, which are off in those runs (a flaw in my variant
      design); consol itself barely changes anything (GBP_USD 5.24->4.66,
      others identical), so their effect through it would be marginal too.
    consol: -1 trade on GBP_USD (worse), no effect elsewhere.
    strict: kills the strategy. GBP_USD 14->0 trades, GBP_CAD 16->1,
      NZD_USD 6->5 (PF 2.91->1.95). Retest + held + candle + MA cross on the
      same bar almost never happens. CHF_JPY unaffected (trend_retest).
    EUR_AUD on breakout_retest: PF 0.16 (n=5). Stays paused.
  Decision: keep all defaults (macd, ema34, ema200, consol off, strict off).
- Backtest vs live, SAME period (M30 pairs' 3000 bars = 2026-07-01 -> 09-28):
    GBP_USD bt 14 trades PF 5.24 | live 7 trades 5W/2L +$24.5  (consistent)
    CHF_JPY bt  6 trades PF 3.72 | live 4 trades       +$26.4  (consistent)
    NZD_USD bt  6 trades 67% WR  | live 3 trades 0W    -$12.1  (NOT consistent)
    GBP_CAD (H4) bt 16 trades in 2 YEARS | live 10 trades in 8 WEEKS -$17.5
      (NOT consistent, live fires ~13x more often than the backtest says)
  The 4 remaining live pairs netted +$21.3 on 24 live trades. The losses
  came mostly from the pairs now paused/removed (EUR_AUD, EUR_JPY, AUD_JPY,
  USD_CAD).
- [x] 13.
- 11: applied MIN_SL_ATR_MULT = 2.0 (user said "continue" after my lean-yes).
- 14: restarted 2026-09-27 23:56 EDT. One lock (pid 12560), 4 pairs, 43
  closed restored, first scan ran, heartbeat OK, no errors. Pre-existing and
  unrelated: FF next-week feed 404, "no ticks yet".
  NOT yet verified live: a real trade carrying ml_signal (needs the next bot
  trade) and the dashboard ML-log line (unit-tested, not viewed in a browser).

## Review (2026-09-27/28)
Fixed / changed (all tested; 518 pass):
- Learning loop: 0 of 43 demo trades had ever reached ML data (synthetic id
  vs OANDA id + in-memory cache). Features now live on the persisted trade
  record; outcome logged at close; manual/adopted trades excluded; session
  and hour taken from signal time. 39 lost trades backfilled
  (source=backfill, not trained on).
- Learning health: red LEARNING GAP banner + ERROR log + Telegram if any bot
  trade closes unlogged; grey "ML log: N outcomes" line otherwise.
- ML gate off (anti-predictive); P(win) still logged for later evaluation.
- Score-floor drops now audited as BLOCKED.
- PYRAMID_MIN_SCORE 65 -> 55, review 2026-10-04.
- SL floor: max(25 pips, 2.0 x ATR14). Removing the floor was tested and
  rejected (worse on every pair).
- AUD_JPY + EUR_AUD paused. Live: GBP_USD, GBP_CAD, NZD_USD, CHF_JPY.
- New switches, left at the original behavior (the backtest showed no gain):
  BREAKOUT_STRICT_ENTRY, MA_FAST_PERIOD, MA_SLOW_TYPE, BREAKOUT_MOMENTUM.
- Tests can no longer write to data/signal_log.csv (conftest).
Corrections to my own earlier claims: ADX gate doesn't affect live pairs;
"80% not logged" was actually 100%.
Security: no secrets/credentials added; no new endpoints (Telegram via the
existing sender, trade IDs only); dashboard still 127.0.0.1-only; the
backfill script reads/writes local files only and keeps a .bak.
Open items:
- GBP_CAD fires ~13x more live than in backtest; NZD_USD 0/3 live vs 67% bt.
  Next: trade-by-trade live vs backtest comparison.
- backtest/runner.py has no spread model (my scripts deduct it; the runner
  and dashboard backtest button still don't).
- venv lacks pyyaml -> configs/*.yaml ignored.
- 2026-10-04: pyramid-55 review.

---

# Strategy review: why the bot isn't improving (2026-09-27)

## Baseline numbers (from data/live_state_forex.json + signal logs, not guessed)
- 43 closed live trades, 2026-07-31 → 2026-09-25: 17 W / 26 L (40% WR),
  PF 1.03, net +$2.41. Avg win $5.72 vs avg loss $3.65.
- 33/43 trades never reached TP1; 27 ended at SL.
- By pair: GBP_USD +24.5, CHF_JPY +26.4 | GBP_CAD -17.5, EUR_JPY -10.6,
  NZD_USD -12.1 (0/3), EUR_AUD -3.1.
- Score vs outcome: live score 55-59 → 11/26 wins, 60-64 → 5/11. Shadow
  (skipped) signals: 35-39 51%, 40s 41-44%, 50s 38-50%, 60+ 67% (n=12).
  The score barely separates winners from losers.
- Gates: of 16,272 audited scans only 61 were BLOCKED by guard gates.
  11,817 were NO_TOUCH (setup never formed), ~3,000 fell below the score
  threshold. The gates aren't what's throttling the bot. The setup
  definition and the score threshold are.
- Learning loop: signal_log.csv has only 9 live win/loss rows for 43 real
  trades, so the ML/adaptive layer is learning from ~20% of the bot's own
  results. Likely the main reason it "never learns".

## Plan (Phase 1 is read-only diagnosis; no live changes until you approve Phase 2)
- [x] 1. Outcome write-back traced. ROOT CAUSE: main.py:579 caches features
      under a synthetic id ("CHF_JPY-<unix ts>"), but in demo/live
      TradeManager.open_trade() returns OANDA's id ("269"). record_close()
      looks up "269", finds nothing, logs a WARNING and drops the row. Paper
      mode passed the id through, so it worked until the switch to demo.
      Last real outcome logged 2026-07-30; demo started 2026-07-31, so
      0 of 43 demo trades ever reached the learning data. Also: the cache
      (`_pending`) is in-memory only, so any restart loses it too.
      Why it wasn't caught: tests pass matching ids, and nothing compares
      "trades closed" against "outcomes logged".
- [x] 2. Component edge (275 live + shadow-resolved signals, base WR 43%):
      confluence score corr -0.03 (none), EMA +0.07, structure -0.07,
      PA -0.02, ATR +0.03. ML P(win) corr -0.10: higher ML confidence
      lines up with LOWER win rate (37% top third vs 48% bottom), so the
      ML gate blocks the slightly better trades. market_structure is
      "ranging" on 272/275 rows, was_at_sr_zone is always 1, and
      bos_confirmed is always empty, so those features carry no information.
- [x] 3. Exit analysis (OANDA M30 candles per trade): SL is a flat 25 pips
      on almost every trade (MIN_SL_PIPS floor wins over ATR). Losers moved a
      median of only 7 pips in favour before stopping out; only 5/24 would
      have reached 2R within 48h even with a wider stop. Wider stops would
      not fix this. The entries are wrong-direction about as often as
      right. Runners do exist (CHF_JPY 383 and 433 pips, EUR_AUD 142) and
      are capped by TP3.
- [x] 4. Pair verdict (live + shadow WR): USD_CAD 62%, GBP_CAD 53%,
      GBP_USD 45%, EUR_AUD 39%, CHF_JPY 36%, EUR_JPY 33%, NZD_USD 28%
      (n=47). Only the NZD_USD result is statistically meaningful; drop it.
- [ ] 5. Backtest vs live gap: NOT run this pass (findings 1-3 already
      decide the direction). Needed as the acceptance test for any new
      entry logic.
- [x] Extra findings:
      a. Two score floors disagree: signal_engine marks TRIGGERED at the
         per-pair WFO min (50), then main.py drops it at the slider floor
         (55) without writing a BLOCKED audit row. 92 unique setups
         triggered since 07-31 and 43 were traded; ~35 were silently
         dropped at 50-54.
      b. Pyramiding is on but needs score >= 65. Only 4 of 106 triggers
         scored that high, so it has fired once (EUR_AUD 08-28).
      c. The strategy is a pullback/mean-reversion entry with an ADX gate
         that deliberately BLOCKS trending markets ("ranging" on 99% of
         signals), which is the opposite of riding 250-pip moves.
      d. venv lacks pyyaml, so configs/*.yaml are ignored (code defaults).
      e. 8 trades closed with reason "unknown"; one TP2 partial close
         failed with TRADE_DOESNT_EXIST (broker closed it first).
- [ ] 6. Proposal: see "Phase 2 proposal" below. Waiting on your decision.
- [ ] 7. (Phase 2, after your OK) implement one change at a time,
      each backtested out-of-sample before going live.

## Phase 2 proposal
A. Fix now (no strategy decision needed):
   - [ ] A1. Learning loop: store signal features ON the trade record
         (already disk-persisted in live_state_forex.json) and log the
         outcome from there. Fixes both the id mismatch and the restart
         loss. Regression test uses a real broker-style id mismatch.
   - [ ] A2. Learning health check: dashboard banner + Telegram when
         "closed trades" != "outcomes logged", plus "model trained on N
         real trades, last retrained <date>".
   - [ ] A3. Turn off the ML gate until the model has >= 100 real
         outcomes AND beats base rate out-of-sample (it's anti-predictive
         now).
   - [ ] A4. Make the silent 50-54 drop write a BLOCKED audit row, and use
         one score floor everywhere.
B. Strategy (your call):
   - Keep all infrastructure (OANDA, risk 1%, trade mgmt, dashboard,
     reconciliation, audit, shadow outcomes).
   - Replace the entry engine with a trend-continuation design: 1H trend
     + market structure (HH/HL), 30M pullback-to-S/R entry, ATR-based SL
     at structure (not a flat 25 pips), TP1 >= 2R (your rule), runner
     with ATR trail instead of a hard TP3 cap, and pyramid adds on each new
     pullback in the same trend (gated on structure, not the score).
   - Acceptance test before live: out-of-sample backtest and walk-forward
     on 30M/1H, PF > 1.3 and max DD reported, against the current
     strategy as the baseline. Note: a trend-follow variant was already
     tested and shelved 2026-07-02, so this is not a guaranteed win.
     The difference this time is structure-based stops, runners and
     pyramiding.

## Honest caveats
- 43 trades is a small sample. A 40% WR with PF ~1.0 is statistically
  consistent with a strategy that has no edge, and also with a modest edge
  plus bad luck. The analysis above is designed to tell those apart.
- I can find what's broken and what has no edge. I can't promise the
  result is a profitable strategy. If the analysis shows this setup has no
  edge, I'll say so rather than tune it further.

---

# Chart: default candle position + text placement (2026-09-16)

## Finding 1 — text in the blank area after the last candle: ALREADY WORKS
Verified live with Playwright (not just code reading): selected the "T"
text tool, clicked well into the blank space past the last candle on
GBP/CAD, typed "Target zone", confirmed — it placed and rendered
correctly. `dashboard/assets/chart.js`'s `_xToTimeExtrap()` (chart.js:198)
already extrapolates a valid time for any x-coordinate, even past the
last real bar, using the chart's own pixels-per-second mapping — this is
also what the trendline/box/fib/position tools already use, so it's not
new for text specifically. One usability note, not a bug: clicking
literally at the last few pixels of the pane gets the label clipped
behind the price scale — same as it would be for any chart element that
close to the edge. No code change needed here.

## Finding 2 — default candle position leaves a gap, real bug
User's ask: on loading/switching to a pair, the latest candle should sit
near the right edge by default, not deep-left with empty space after it
requiring a manual scroll right every time.

Reproduced live: switching to a never-before-zoomed pair+TF via Playwright
does leave a real gap after the last candle (chart.js:4377-4390's "default
view" branch deliberately adds `interval * 10` — 10 bar-widths — of
padding after the last bar). That's a real but modest contributor.

Suspected bigger contributor (not yet directly reproduced — would need
the user's actual localStorage, which isn't accessible from here):
chart.js:4338-4376's saved-zoom restore. `apex_zoom_<pair>_<tf>` is saved
in localStorage per pair+TF and restored on next visit. Its only staleness
check (`rangeUsable`, added for the 2026-07-20 zoom-persistence bug) is
"does the saved range overlap the currently-loaded candle window at all"
— it does NOT check whether the saved range's right edge has drifted far
behind where the true latest bar now is. A pair not visited in a while
would restore an old view whose right edge is now well behind "now",
exactly matching "have to scroll right every time" — and since the fix
would need to persist itself, this explains why it doesn't just self-heal.

## Fix (applied — `dashboard/assets/chart.js`, ~line 4338-4394)
- [x] Shrunk the default-view forward padding: `interval * 10` ->
      `interval * 3` — last candle now sits close to the right edge
      instead of leaving a visible gap.
- [x] Extended the `rangeUsable` staleness check: a saved zoom whose `to`
      is more than 20 bars (`_STALE_ZOOM_BARS`) behind the freshly-loaded
      data's actual last bar is now discarded (logged distinctly:
      "has fallen behind real-time") and falls through to the normal
      default/current view, instead of being silently restored.
- [x] Verified live with Playwright (not just code reading):
      - Fresh, never-zoomed GBP/CAD 30m view: last candle now sits a few
        pixels from the right edge (was ~70-90px gap before the fix).
      - Artificially aged saved zoom (crafted `to` ~100 bars behind
        "now", still overlapping the loaded window): console confirmed
        `"saved zoom for apex_zoom_GBP_CAD_30m has fallen behind
        real-time — discarding and using default view"`, and the
        resulting view was identical to the fresh-view case — correctly
        discarded, not restored.
- [x] No Python changes, no bot restart needed — `chart.js` is a static
      asset served straight from disk on each page load.

## Finding 3 — first fix was incomplete; found, patched, then patched again
User reported the first two fixes didn't help (hard refresh confirmed —
"the last candle is still butting up against the price scale"). Root
cause: the staleness check only discards a saved zoom that's fallen far
BEHIND real-time (>20 bars) — it does nothing for a saved zoom that's
already close to the live edge but has too little margin (including
flush/zero margin). Any such range was being restored exactly as saved,
forever, since nothing ever corrected it.

My first attempt at a fix for this was itself broken — it only nudged a
range whose `to` was already AT OR PAST the true last bar
(`to >= lastBar.time`), which misses the common case of a saved range
sitting a *little* behind real time. I patched that too (comparing the
margin directly instead of gating on `>=`) — but this was still patching
the same fragile mechanism a third time, and the user correctly called
this out: **stop patching, fix it properly.**

## Finding 4 — the actual fix: stop trying to validate a saved view near the edge
The user then reported the SECOND patch caused a worse regression — real
screenshots showed candles not rendering at all (the view had jumped to
a time range with no real data, EMA reference lines the only thing
visible). This was almost certainly the "nudge/shift" math from the
broken first patch compounding across repeated save→read→re-save cycles
in the user's actual browser (which had accumulated a lot of state across
this whole session) — exactly the kind of bug that patching-in-place
tends to produce.

**Redesigned rather than patched further (2026-09-16d):**
- One rule, not a chain of exceptions: a saved zoom is only ever restored
  when it's CLEARLY a deliberate historical scroll (`to` more than 20 bars
  behind the true latest candle). Anything closer to "now" than that —
  behind, flush, exactly at, or any other shape a past bug might have left
  — is unconditionally rebuilt from scratch via the plain default-view
  calculation, no exceptions, no shift/nudge math at all.
- No more per-case thresholds ("stale" vs "tight") to keep tuning — those
  were two symptoms of the same underlying mistake (trying to validate
  edge-adjacent saved state instead of just not trusting it).
- **Storage key versioned** (`apex_zoom_` -> `apex_zoom_v2_`, both the read
  side in `load()` and the write side in the `subscribeVisibleTimeRangeChange`
  listener — the latter had been silently left on the old key name during
  the second patch, which would have made zoom persistence appear broken
  entirely). This guarantees every pair gets a truly clean slate,
  independent of whatever got saved by earlier buggy code versions during
  this session — no reliance on the new validation logic being able to
  correctly recognize and discard old corrupted values.

## Finding 5 — the ACTUAL root cause, plus two of my own errors (2026-09-16e)
The redesign in Finding 4 was still wrong on-screen, and the user was
right that it was never fixed. Two separate mistakes of mine:

**1. I used 3 bars when the user explicitly said 16.** They stated the
number ("move the last candle 16 candles to the left"); I substituted my
own smaller value and then kept "verifying" that wrong value. Now a single
named constant, `EDGE_MARGIN_BARS = 16` (chart.js top), used in both
places that must agree.

**2. My verification was measuring the wrong thing, which is why I kept
believing it was fixed.** I compared the last candle's x-coordinate
against `document.getElementById('tvlw-chart').clientWidth` — the chart
CONTAINER, which includes the price scale. Measured correctly, the price
scale is **68px wide**, so my confident "71px gap = healthy" was really
~3px of actual visible space, i.e. exactly the flush-against-the-scale
result the user kept reporting. Correct measurement is against
`timeScale().width()` (plot area only), converted to candles via
`timeScale().options().barSpacing`.

**The real root cause of the margin never appearing at all:**
`setVisibleRange()` works in TIME space and maps onto existing data. There
is no data after the last candle, so asking for `to = lastBar.time + N *
interval` just clamps back to the last bar — the requested whitespace can
never render, for ANY value of N. That's why three rounds of tuning the
number changed nothing visible. Fixed by switching the default view to
`setVisibleLogicalRange()` (BAR-INDEX space, which explicitly supports
indices past the end of the series — the intended API for right-hand
whitespace): `{from: lastIdx - nShow, to: lastIdx + EDGE_MARGIN_BARS}`.
Also set the chart's own `timeScale.rightOffset` to the same constant so
real-time candle appends preserve the margin instead of eroding it.

## Comprehensive verification (this time covering all shapes, not just the two I'd already guessed)
Live Playwright tests, measuring the REAL on-screen pixel gap after the
last candle (not `getVisibleRange()`, which reports the time of actual
data points and doesn't reliably reflect empty margin space — learned
this the hard way mid-verification):
- Fresh pair, no saved zoom at all: healthy ~71px gap.
- Garbage under the OLD (pre-version) key: ignored entirely, healthy gap.
- Garbage under the NEW key (nonsense huge values): discarded, healthy gap.
- Near-edge saved zoom (~5 bars behind, the exact shape of the original
  bug): always rebuilt fresh, healthy gap — not restored, not nudged.
- Deliberate deep-historical saved zoom (100 bars behind): restored
  verbatim — true live edge sits far off-screen, confirming the
  historical view was actually honored, not silently overridden.
- A real simulated mouse-drag pan: confirmed it saves under the correct
  new key, and the old key stays untouched — closes the loop on the
  read/write key mismatch from the second patch.
All 5 scenarios pass with hard assertions, not just visual inspection.

### Re-verified after the 2026-09-16e fix, with the CORRECTED measurement
Margin measured as candle-widths against `timeScale().width()` (plot area,
price scale excluded), asserted at 16 ± 1.5 candles:
- First page load, default pair, saved zooms cleared: **16.76 candles**
- Switch to CHF/JPY: 16.76 · GBP/CAD: 16.76 · EUR/AUD: 16.76 · GBP/USD: 16.76
- Switch away and back (the user's "make adjustments then go to another
  chart" case): 16.76
Identical on every pair and every transition — confirms it's a global
default, not per-pair state. Visually confirmed on screenshots too: clear
blank space between the last candle and the price scale.

## Review
- Text-in-blank-area: turned out to already work (see Finding 1 above) —
  no code change, just confirmed and documented so it doesn't get
  re-investigated later.
- Default candle position: THREE independent causes found across two
  rounds, all now fixed:
  1. Default-view padding (10->3 bars) — the always-reproducible one,
     fixed and verified in round 1.
  2. Stale saved zoom (fallen >20 bars behind real-time) — fixed and
     verified in round 1.
  3. Saved zoom with insufficient live-edge margin (including flush/zero)
     — the actual bulk of the user's real complaint; missed in round 1,
     found and fixed in round 2 after the user reported the first fix
     didn't work and confirmed they'd done a real hard refresh.
- What went wrong across rounds 1-3: I kept fixing the specific case in
  front of me (stale, then tight, then a broken tight-check) instead of
  questioning whether "try to validate then patch a saved edge-adjacent
  view" was the right approach at all. Each fix was individually verified
  live, but each verification only covered the case that fix was built
  for, not the whole space of "what could a saved zoom near the edge look
  like" — so each round's real-world testing surfaced a shape I hadn't
  covered. The user's feedback mid-fix ("stop patching, actually fix it")
  was the right call: round 4 replaced the whole mechanism with one rule
  instead of adding a fourth exception, and this time verification covered
  5 distinct scenarios (including a real user drag gesture) with hard
  assertions before reporting back, not just the 1-2 cases the current
  patch was built for.
- How you'd notice a recurrence: switch to any pair — the last candle
  should sit within ~3 bar-widths of the right edge, whether it's a pair
  you've never zoomed, one you haven't visited in a while, or one you
  looked at moments ago. If it doesn't, check the browser console for an
  "apex chart" zoom-related debug/warning message to see which path fired
  (or didn't).
- Test backing the fix: live Playwright verification for all three
  scenarios — fresh/never-zoomed, artificially-stale saved zoom, and
  artificially-flush/tight saved zoom — including a regression re-check
  of scenarios 1 and 2 after correcting the round-2 bug, to confirm the
  fix for one case didn't break the others. No automated test suite
  covers `chart.js` (static JS asset, outside the Python pytest suite),
  so this Playwright verification is the record for this change.
