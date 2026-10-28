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

Round 4b (user 2026-09-28: right-pane panels, trade log SL/dates, "is the score broken?"):
- [x] Trade log: Opened/Closed (UTC), SL (sl_initial; old trades show the
      final SL), newest first, Outcome coloured by P&L, score 100 shown as
      "manual", candle patterns in a hover tooltip (Pattern column was always
      empty). New trades store sl_initial. + test.
- [x] Per-Pair Avg R bug: used |entry - CURRENT sl| x size, unconverted
      (trailed stops inflated R: GBP_USD +3.32R; JPY pairs ~0R). Now
      P&L / risk_dollar. + test. 529 passed.
- [ ] Signal Frequency + Watching Signals panels: recommended remove /
      replace with near-miss would-win/would-lose; waiting on the user.
- Score floor backtest (1.5y, isolated runs; net $ GBP_USD / EUR_USD / AUD_JPY):
    floor40  65.4 / 49.5 / -87.3  (n 184/175/282, PF 1.11/1.09/0.89, AUD_JPY DD 34.7%)
    floor55  76.7 / 41.0 / 144.7  (n 53/44/78,  PF 1.55/1.39/1.59)
    floor65 -10.5 / 23.1 /   3.3  (n 2/4/5, almost no trades)
  -> The floor WORKS as a cut-off (40-54 setups are clearly worse) but the
     score does NOT rank setups above it (live: no link between 56 and 66).
- CORRECTION: round-3 step 4/5 comparison was contaminated. The variant
  script set config switches in reused worker processes without resetting,
  so later "base" jobs inherited earlier variants' switches (AUD_JPY base ==
  addon_ma exactly). Rerunning with per-job reset; base must reproduce
  floor55 (76.7 / 41.0 / 144.7). The pair screen and floor runs were not
  affected (they set their keys explicitly on every job).
- CLEAN step 4/5 rerun (base reproduces floor55 exactly -> isolation OK);
  net $ GBP_USD / EUR_USD / AUD_JPY = total:
    base (add-ons @55)  76.7 / 41.0 / 144.7 = 262
    no_addons           89.3 / 31.2 /  90.8 = 211   add-ons +51 (GBP_USD -13, DD 7.2 -> 10.1%)
    seq_retest          35.4 / 37.8 / 135.4 = 209   REJECT
    addon_ma            82.8 / 30.4 / 125.7 = 239   REJECT
    runner (no TP3)     76.8 / 42.9 / 133.8 = 254   REJECT (slightly worse)
    all3                41.2 / 28.8 / 119.6 = 190   REJECT
  Same conclusions as the contaminated run: all four flags stay off.

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

# Round 5: trade volume + chart labels (2026-10-07)

User's framing: too few trades close for the bot's numbers to mean
anything yet ("2027 will get here before 100 trades"). Asked for (1)
multi-trade per pair/pair confirmed already built, (2) more pairs, (3)
breakeven that at least covers commission, (4) indicator pane labels, (5)
whether my own GBP/USD chart read differs from the bot's live strategy.

- [x] Confirmed `ALLOW_MULTIPLE_PER_PAIR=True` / `MAX_PYRAMID_PER_PAIR=3`
      / multi-pair (`MAX_OPEN_TRADES=5`) were already live — no code
      change needed there. Real bottleneck was `FOREX_PAIRS` only having 3
      pairs after the 09-28 re-screen; confirmed via a closed-trade overlap
      scan that pyramiding has fired 0 times in the 10 days since
      PYRAMID_MIN_SCORE dropped to 55 (too few signals across 3 pairs for
      two same-pair/same-direction entries to ever overlap).
- [x] `FOREX_PAIRS`: re-added all 25 pairs the 09-28 re-screen rejected
      (user's explicit call after I surfaced the backtest numbers) —
      28 pairs total now. MODE stays "demo" (practice account). Dated
      comment in config.py documents the override and preserves the 09-28
      PF/DD numbers unchanged (re-adding ≠ re-validating).
      KNOWN GAP: the 25 re-added pairs have no STRATEGY_OVERRIDE entry, so
      they default to EMA-bounce — the strategy the 2026-08-13 WFO search
      found no live edge for on any pair. The 09-28 screen's actual
      per-pair winner (breakout_retest vs trend_retest) wasn't saved to
      disk, so restoring it means re-running that screen, not a config
      edit. Flagged for a follow-up, not done now (minimal-change rule).
- [x] `learning/pattern_learner.py::_PAIR_ENCODING`: appended codes 15-28
      for the 14 newly-active pairs missing from the ML feature encoding
      (caught by test_pattern_learner.py, not spotted by inspection first
      — appended only, per the file's own "never reorder/remove" rule).
- [x] `tests/test_pair_tf_routing.py`: updated the two tests that asserted
      FOREX_PAIRS == the post-rescreen 3-pair list — they now check the 3
      validated pairs are still present and FOREX_WATCH is still empty,
      without claiming the other 25 are profitable.
- [x] Breakeven +2 pips: ran a real A/B backtest (BREAKEVEN_ENABLED=False
      vs True+BREAKEVEN_BUFFER_PIPS=2) on all 3 currently-live pairs,
      4999 M30 bars each, live-routed strategy per pair, via
      backtest.runner.run_backtest() — config.py untouched (BE stays off).
      Results (trades/WR/PF/PnL, BE off -> +2 pips):
        GBP_USD  14 trades  50.0%->64.3% WR   PF 2.71->2.28   $45.08->$33.07
        EUR_USD   5 trades 100.0%->100.0% WR  PF 53.78->30.57 $53.78->$30.57
        AUD_JPY  18 trades  38.9%->44.4% WR   PF 1.25->1.21   $14.01->$11.82
      Same direction as the 2026-07-21 rejection: WR up, PF/PnL down on
      every pair, confirmed specifically at the +2 pip buffer the user
      asked for, not just the old test's buffer value. Recommended: leave
      BREAKEVEN_ENABLED=False. Not enabled — waiting on user's call with
      these numbers in hand.
- [x] Chart: added a small white "MACD" / "RSI" label to the upper-left of
      each sub-pane via `LightweightCharts.createTextWatermark()` (same
      API the big center "GBP_USD 30m" watermark on the candle pane
      already uses), only for the two panes actually shown in the
      screenshot — CCI/Stochastic panes weren't asked for and weren't
      touched. Watermark ref is nulled when a pane's series are removed
      (toggle off) so the next toggle-on recreates it instead of reusing a
      stale reference into a collapsed pane.
- [x] Compared my manual GBP/USD chart read against engine/strategy_trend_retest.py
      (GBP_USD's actual live strategy since 09-28): genuinely different
      mechanism. I used EMA200/50 crossover + MACD as the trigger level;
      the live strategy requires an H4 trend-bias hard gate + an actual
      market-structure break (swing high/low BOS, not an EMA cross) +
      retest + price-action rejection candle, with MACD only a small bonus
      rather than a requirement. Declined to hand-write a new strategy
      from one screenshot — every strategy in this file went live only
      after a fit/holdout-validated backtest, and one chart isn't that;
      offered a backtestable hypothesis instead (see chat).
- [x] Tests: full suite, 532 passed (0 skipped) after the two fixes above.
- Security: no new endpoints, no secrets touched. FOREX_PAIRS/strategy
  changes only affect which pairs the already-demo-mode bot scans; no
  change to OANDA credentials, dashboard binding, or any write path. The
  A/B backtest script (scratchpad, not committed) only read OANDA candle
  data and config in-process — never touched config.py on disk or the
  live running bot.
- NOTE: the live bot process (started 10/4) won't see the FOREX_PAIRS/
  pattern_learner changes until restarted — config.py is read once at
  import. Dashboard pair dropdown reads config.FOREX_PAIRS dynamically
  (confirmed, app.py/panels.py/state.py), so once restarted every affected
  surface picks the new 28-pair list up automatically, no other code
  changes needed.

# Phase 2A: trace the 214 TRIGGERED signals (2026-10-07)

Investigation only, no code changes. Matched each TRIGGERED row in
signal_audit.csv against real trades (live_state_forex.json + signal_log.csv
live/backfill rows) and main.py-level BLOCKED rows, by pair+direction+time
within 10 min.

- [x] Result: 214 = 54 executed + 43 blocked + 117 unaccounted.
      117 splits into 76 before 2026-07-31 (no ground-truth trade record
      survives that far back — unverifiable, not a proven bug) and 41
      on/after 2026-07-31 (ground truth exists and still shows nothing —
      the real mystery).
- [x] Eliminated for the 41: TradeManager's own silent-reject paths (0
      log hits across main.log + 20 rotated backups), MAX_OPEN_TRADES (0-1
      trades open at each timestamp, nowhere near the cap), pyramid/
      duplicate-pair gate (no existing open trade on that pair at any of
      the 41 timestamps). Every gate that CAN silently reject already
      calls _audit_blocked() when it does — read every line of that path.
- [x] Root cause not found — main.log's rotated backups only cover
      2026-10-04 onward (confirmed via file mtimes), so the runtime
      behavior for Aug/Sep no longer exists anywhere to inspect. Reported
      as genuinely unresolved rather than guessed at.
- [x] Found 2 real secondary bugs: (1) signal_audit.csv's c1-c6 columns
      are named for strategy_ema_cci_macd specifically (c2_h4_aligned,
      c4_cci_at_touch, etc.) but get populated with trend_retest's/
      breakout_retest's own differently-meaning c1-c5 values for pairs
      running those strategies — mislabeled, not just incomplete. (2) no
      audit row exists at the actual moment of trade execution — the only
      way to know a TRIGGERED signal resolved is to cross-reference a
      different file after the fact, and only if that file still has the
      record.

# Phase 2B: signal lifecycle instrumentation (2026-10-07)

User's assignment, following directly from 2A's finding that the real
problem is observability, not (provably) decision logic. Explicit
constraints: no strategy logic, entry thresholds, risk parameters, or ML
behavior change — this is logging-only. Objective: every signal_id's last
row in the new log IS its documented terminal state, so Phase 2C never
again has to say "we think N signals disappeared."

Design decisions confirmed with user: signal_id assigned only when a
strategy scores > 0 (not for the 80% pure NO_TOUCH scans — "signal" means
"the strategy found something," matching the lifecycle diagram's own
wording); sequence number is a global monotonic counter, never resets.

- [x] New `engine/signal_lifecycle.py`: `next_signal_id(pair, timeframe)`
      (format `SIG-{YYYYMMDD}-{PAIRNOUNDERSCORE}-{TF}-{NNNNNN}`, counter
      persisted in `data/signal_id_seq.json`), `log_event(...)` (appends
      one row to `data/signal_lifecycle.csv`), `format_diagnostics(strategy,
      diag)` (strategy-specific pipe-joined key=value string — see below),
      `terminal_states()` (returns {signal_id: last row} for Phase 2C).
- [x] Fix the c1-c6 mislabeling the RIGHT way: don't touch legacy
      signal_audit.csv's schema (other code may depend on it, and it's a
      historical record — rewriting it would be the same mistake in
      reverse). Instead `format_diagnostics()` builds correctly-labeled
      strings per strategy from each strategy's own get_last_diag() output:
        ema_bounce:       above_ema, h4_aligned, touch, cci_at_touch,
                          cci_recovery, macd_confirm, macd_rising (c1-c7,
                          confirmed against strategy_ema_cci_macd.py's own
                          source — c6=hist above threshold, c7=hist rising)
        trend_retest:     h4_bias, retest, not_invalidated, price_action,
                          ma_aligned, macd_bonus
        breakout_retest:  bos, retest, not_invalidated, price_action,
                          macd_momentum
- [x] `engine/signal_engine.py::run()`: generate signal_id once final_score
      is computed and > 0 (before the TRIGGERED/WATCHING/SCANNING split),
      attach to every signal dict returned from that point on. Log
      SIGNAL_CREATED (signal_status = whichever of TRIGGERED/WATCHING/
      SCANNING/ATR-gate-blocked it resolves to) at each existing _do_audit
      call site — reuses the call sites, doesn't change what they decide.
      When result ends up TRIGGERED, also log a second TRIGGERED-stage row.
- [x] `main.py::_process_pair()`: thread `signal_id = signal.get("signal_id")`
      through. At each existing gate-reject point (score floor/ML/H4-ATR/
      news/same-candle/risk-duplicate-pyramid-halt/cooldown) log
      GATE_BLOCKED with the exact existing reject_reason string — same
      call sites as the current _audit_blocked() calls, additive only.
      Right before the alert fires (all gates cleared), log GATE_APPROVED.
      Right before PaperTrader.open_trade()/TradeManager.open_trade() is
      called, log OPEN_TRADE_CALLED. Right after, log EXECUTED (trade_id=
      actual_id, execution_status = PAPER_EXECUTED/DEMO_EXECUTED/
      LIVE_EXECUTED from config.MODE at that instant) or EXEC_FAILED
      (failure_reason = EXCEPTION if one was caught, else ORDER_REJECTED).
- [x] Tests: signal_lifecycle.py's own unit tests (ID format/uniqueness/
      monotonic counter persists across restart, log_event row shape,
      format_diagnostics per strategy, terminal_states()). Plus a
      regression check that wiring this in does NOT change any existing
      test's pass/fail outcome — this is additive logging only, the full
      existing suite must stay green unchanged.
- [x] Security check + review summary, per usual.

## Review — Phase 2B

What changed: one new file (`engine/signal_lifecycle.py`, ~200 lines),
~15 small additive call sites in `engine/signal_engine.py` and
`main.py::_process_pair()` (every one placed next to an existing decision,
never inside one), one new autouse test fixture (`conftest.py`), one new
test file (21 tests). Nothing in `config.py`, no strategy module touched.

Verified, not assumed:
- Full existing suite (532 tests) passes **unchanged** after wiring this
  in — the concrete evidence that no decision logic moved, not just a
  claim.
- Smoke-tested against real live OANDA data (not just unit tests) before
  touching the live bot: confirmed signal_id allocation, SIGNAL_CREATED
  logging, and diagnostics formatting all fire correctly end-to-end.
- Caught a real bug during that smoke test: strategy diag dicts mix plain
  Python `bool` with numpy's `bool` scalar (confirmed on real data —
  `strategy_ema_cci_macd`'s c1 came back `numpy.bool`, c2-c7 plain Python
  `bool`, in the same dict). `isinstance(v, bool)` silently missed the
  numpy one, which is not a `bool` subclass despite sharing numpy's
  display name "bool" — would have produced a diagnostics string mixing
  "True"/"False" and "1"/"0" in the same row. Fixed to match on type name
  instead of isinstance; added a regression test using real `numpy.bool_`.
- Restarted the live bot (same lock-holder-survives-the-scheduled-task-
  stop issue as the morning's pair-expansion restart — found and killed
  the actual PID again, verified the lock was free before restarting).
  Confirmed clean startup, no new errors, heartbeat current.

Known limitation, stated plainly: this closes the gap going forward only.
The 41 historically-unaccounted TRIGGERED signals from Phase 2A remain
unexplained — the logs that could have proven a root cause don't exist
anymore. Nothing in Phase 2B retroactively recovers that.

# Phase 2C: collect 28-pair data + reporting tool (2026-10-07)

- [x] `scripts/signal_lifecycle_report.py`: reads data/signal_lifecycle.csv,
      reports signals created / triggered / gate_blocked / approved /
      open_trade_called / executed / exec_failed, "orphaned" counts
      (terminal stage == TRIGGERED / GATE_APPROVED / OPEN_TRADE_CALLED —
      the Phase 2A anomaly class, now a direct count), funnel-invariant
      sanity checks (approved+blocked should equal triggered, etc — a
      mismatch itself is flagged as a finding), and breakdowns by pair/
      timeframe/strategy/direction/execution_status. Also `--trace
      SIG-...` to print one signal's full event history in order — the
      user's explicit ask ("follow that exact signal through the entire
      lifecycle"). 7 tests, distinguishing the two counting modes
      (pipeline = "ever reached this stage" vs terminal = "stopped here")
      explicitly, since confusing them would silently invert what the
      tool reports.
- [x] Strategy consolidation decision (user's call, based on Phase 1's
      numbers — breakout_retest 14.3% fire rate / 2 historical trades vs
      ema_bounce 21.4%/42 and trend_retest 15.5%/7): EMA Bounce primary,
      Trend-Retest secondary, Breakout-Retest deprioritized but not
      deleted. AUD_JPY — the only pair ever routed to breakout_retest —
      deliberately kept on it rather than reassigned: it's one of only 3
      pairs (of 28) that passed the 09-28 re-screen, specifically on
      breakout_retest. Net effect: zero code/behavior change (AUD_JPY was
      already the only user of that strategy), documented in config.py's
      STRATEGY_OVERRIDE comment so a future session doesn't silently
      re-add pairs to it without a fresh backtest.
- [ ] Ongoing: let the 28-pair, 2-active-strategy system run and
      accumulate real signal_lifecycle.csv data. First read (minutes
      after the Phase 2B restart, ~38 signals): invariants held, 0
      orphans, 3/3 trades executed cleanly — too small a sample to mean
      anything yet. Revisit with `scripts/signal_lifecycle_report.py`
      once real volume has built up (days, not minutes) before drawing
      any conclusion about fire rates or gate behavior at 28-pair scale.
- Roadmap correction (user, 2026-10-07): Breakout-Retest is out of the
  optimization roadmap entirely, not deferred to a later phase — earlier
  wording ("Phase 4: evaluate/refine Breakout-Retest") implied it was
  still a candidate for active development later; it isn't. AUD_JPY's
  routing stays exactly where it is (existing validated decision, not a
  signal that Breakout-Retest is being adopted/developed). Current
  roadmap: Phase 2C (here) -> analyze EMA Bounce + Trend-Retest
  performance -> backtest/refine those two only -> demo/paper validation
  -> only then consider live.
- Explicit DO-NOT-TOUCH list while Phase 2C is collecting data (user,
  2026-10-07) — the measurement would be contaminated by any of: loosening
  entry conditions, lowering the score threshold, adding more pairs,
  changing risk params, changing ML gates, optimizing EMA settings or
  Trend-Retest, adding another strategy, removing AUD_JPY's Breakout-
  Retest routing, or anything framed as "increase trade frequency." No
  code changes until the user reviews `scripts/signal_lifecycle_report.py`
  output after at least one full trading day (preferably several).

# Safety fix: 3% daily-drawdown halt was non-functional (2026-10-07, same day)

User flagged a real -$32.40 / 7.3% day (6 SL losses) a few hours into the
28-pair run and asked directly: "7% lose?" Investigated rather than
reassured — this wasn't normal variance, it was a broken circuit breaker.
Explicit exception to the embargo above (user's call, asked first): this
is a risk-control fix, not a strategy change.

- [x] Root cause #1: `update_daily_loss()` was only called from ONE of
      several trade-close paths in `trade/trade_manager.py` — the bot's
      own tick-by-tick SL check. Confirmed in tonight's actual logs: all 6
      of today's losses closed via the broker-reconciliation path instead
      ("LIVE RECONCILED CLOSE ... detected via broker poll, not our own
      candle check") — the NORMAL way a stop fires, since the SL order
      lives on the broker. That path never touched `_daily_loss` at all.
      Fixed by moving the call into `_finalize_close()`, the one
      chokepoint every close path (SL, TP3, reconciled, unknown_broker_
      close, manual) already shares — one call site instead of N,
      structurally can't be missed by a future close path the way the
      original scattered version was.
- [x] Root cause #2 (found while fixing #1, same bug class): `_daily_loss`/
      `TRADING_HALTED` are pure in-memory state with no startup seeding.
      This bot restarts often — 3x today alone. Every restart silently
      handed it a fresh 3% budget regardless of real losses already taken
      earlier that calendar day. Fixed: new `risk_manager.
      seed_daily_loss_from_today()`, called once at startup in `main.py`
      right after `TradeManager` loads its disk-persisted `closed_trades`
      — sums today's (UTC) already-realized P&L and halts immediately if
      already past 3%, instead of starting counting from zero.
- [x] Flagged, NOT fixed (out of the approved scope — asking first rather
      than quietly expanding it): `alerts.telegram_alert.send_drawdown_halt`
      is imported in main.py as `tg_halt` but never actually called
      anywhere — the halt now works but fires silently, no notification.
      Also noted: the threshold check is `abs(_daily_loss)/balance`, so a
      large single-day GAIN would also trip "TRADING_HALTED" — pre-
      existing behavior inherited by the fix, not introduced by it,
      documented in a test rather than silently changed.
- [x] Also noted, not fixed: 4 of today's 6 losing pairs shared a currency
      leg (AUD_NZD x2, NZD_CAD, GBP_AUD, GBP_NZD) — correlated exposure
      across "independent" 1%-risk trades, a portfolio-construction risk
      distinct from the halt bug. Flagged for the user, no code change.
- [x] Tests: 4 new in `tests/test_trade.py` (reconciled-close path, the
      actual TRADING_HALTED-crossing-3%-blocks-new-trades behavior, and
      the unknown_broker_close fallback path, plus confirming the direct
      candle-check path still works post-refactor), 4 new in
      `tests/test_risk.py` for `seed_daily_loss_from_today()`. Full suite:
      568 passed (was 564).
- [x] Restarted the bot (same lock-holder-survives-Stop-ScheduledTask
      drill as every other restart today). Confirmed live in the real
      log: `TRADING_HALTED on startup: today's already-realized P&L
      ($-32.40) is 7.3% of balance ($445.50) — at or past the 3% daily
      halt.` New trades are blocked for the rest of today; the existing
      open EUR_AUD trade is still managed normally (TRADING_HALTED only
      gates new opens via validate_pre_trade(), not managing what's
      already open).

# Dashboard: hide quiet pairs + dropdown stale-browser diagnosis (2026-10-08)

User: dropdown still showed 3 pairs though the Signal Monitor sidebar had
all 28; also asked to stop showing all 28 pairs in the sidebar all the
time, only the ones scoring high enough to matter.

- [x] Dropdown: diagnosed, not a bug needing a code fix. `_traded_pair_
      options()` in dashboard/app.py builds the dropdown's options once,
      at `app.layout = html.Div(...)` (module-import time), unlike the
      Signal Monitor panel which is a live Dash callback re-reading state
      every refresh. The *backend* already had 28 pairs (confirmed —
      restarted twice since the FOREX_PAIRS change), the user's *browser
      tab* just hadn't reloaded since. Told the user to hard-refresh;
      also restarted the bot anyway for the panel change below, which
      resets the server-side layout fresh for any new/reloaded page load.
- [x] `dashboard/panels.py::signal_monitor_panel()`: neutral/no-touch
      pairs (score 0, direction "—") no longer render as rows — user's
      explicit choice was "fully hidden, no way to expand" (not a
      collapsible/expandable group). Only pairs the strategy found
      *something* on (cleared NO_TOUCH/CONDITIONS_WEAK/H4_NEUTRAL) show.
      Added a small non-interactive "N pairs quiet (no touch) — hidden"
      footer so the panel doesn't look like only 3-4 pairs exist — purely
      informational, not a way to drill into them (per the user's choice).
      Fixed an edge case this introduced along the way: when ALL pairs are
      quiet, active_rows is empty same as "no scan has ever run" would be
      — these are different states and were about to collapse into the
      same misleading "Waiting for first scan…" message; now distinct
      ("All pairs quiet — no touch this cycle" vs the real waiting state).
- [x] Tests: 5 new in tests/test_alerts_dashboard.py (hides neutral rows,
      footer count + pluralization, the all-quiet-vs-no-data-yet
      distinction both ways). 572/572 full suite passing.
- [x] Restarted the bot (same lock-holder drill). Noted in passing, not
      part of this change: the new daily-halt fix correctly engaged again
      TODAY (2026-10-08) — `TRADING_HALTED on startup: today's already-
      realized P&L ($-25.21) is 5.9% of balance ($424.71)`. Second
      consecutive rough day; flagged to the user, not investigated further
      this pass (would be a strategy question, inside the Phase 2C
      embargo — the halt itself is doing its job).

# unknown_broker_close investigation + fix (2026-10-08)

User asked for file-mapping + data correlation first (dashboard "Closed
Trades"/"ML Engine"/unknown_broker_close — all traced to exact files/
functions, no changes), then approved a scoped fix after the root cause
was found. See [[bugs_phantom_trade_from_order_cancel]] for full detail.

- [x] Investigation: traced "Accuracy: 50.2% | 300 trades" to
      PatternLearner._load_closed() (learning/pattern_learner.py:393) —
      92% of that sample is would_win/would_lose shadow-resolved outcomes
      of NEVER-TRADED signals, not real trades; only 26 of 318 rows are
      real win/loss, and 39 more real trades (source=backfill) are
      silently excluded. Reported, not touched (ML logic off-limits).
- [x] Investigation: correlated dashboard's 60 closed trades against
      signal_lifecycle.csv/signal_log.csv by exact trade_id — 100% match
      rate for the 12 trades the ~18h-old instrumentation has had a chance
      to observe (10 closed + 2 still open, zero orphans); the other 50
      predate the instrumentation, expected not a gap.
- [x] Root cause of unknown_broker_close: queried OANDA's live API
      directly for every recent occurrence — 5 of 6 were ORDER_CANCEL
      (INSUFFICIENT_MARGIN) transactions, not real trades. The pagination
      hypothesis from the prior session was tested against the live API
      and DISPROVEN (9-18 txns returned, nowhere near a page limit) —
      explicitly not implemented, per the user's "don't guess" instruction.
- [x] Fix: connectors/oanda_connector.py::place_market_order() ONLY.
      Reordered checks (fill success FIRST, then explicit reject, then
      explicit cancel — new — then raise on any unrecognized shape instead
      of guessing a trade_id from relatedTransactionIDs). The dangerous
      `ids[-1]` fallback is now unreachable in normal cases, not deleted
      outright (full space of OANDA response variants unaudited) — flagged
      via raise+full-response-log if ever hit.
- [x] Explicitly NOT touched, per approved scope: engine/*, strategy
      logic, entry/exit thresholds, ML logic, learning/*, lifecycle
      instrumentation, risk parameters, reconcile_open_trades() itself,
      daily-loss-halt logic.
- [x] Tests: 6 new in tests/test_connectors.py (TestOandaConnectorPlace
      MarketOrder — fill success, cancel raises, reject still raises,
      fill-takes-priority-if-both-present ordering check, unrecognized
      shape raises, empty response raises), 1 new end-to-end in
      tests/test_trade.py (open_trade() creates zero phantom entries when
      the connector raises). 579/579 full suite passing.
- [x] Restarted the bot (same lock-holder drill). Clean startup, no
      errors, pid=34744.
- [ ] Pending, per user's explicit instruction — do not evaluate strategy
      yet. Verify over time: cancelled orders show EXEC_FAILED not
      EXECUTED in signal_lifecycle.csv; no new phantom trade_ids in
      open_trades; MAX_OPEN_TRADES/duplicate-pair gates not consumed by
      phantoms; unknown_broker_close stops appearing for cancellation
      cases; genuine broker-reconciliation (real SL our candle check
      missed) keeps working unchanged.
- [ ] Pending, separate future step (user's explicit instruction — do NOT
      delete historical records): classify which of the 60 existing closed
      trades are phantom (order-cancel-originated) vs real, with an
      auditable trail, before trusting the 7.3%/5.9% daily-drawdown
      figures as real financial performance.


# Phase 5B: WFO validation diagnostics (2026-10-18)

Why: the 2026-10-18 WFO run saved 0/25 pairs (23 failed_oos). The log can't
say why. Additive logging only -- no threshold/scoring/selection change.
Files in scope: engine/wfo_optimizer.py, tests/test_wfo_optimizer.py, tasks/todo.md.

- [x] Pure helpers in wfo_optimizer.py: _result_pf (same formula as
      _composite_score), _classify_oos_rejection, _format_oos_diagnostic.
- [x] run(): count in-sample combos that returned None; record per-candidate
      (trades, pf, score) in the existing holdout loop; both inside try/except.
- [x] run(): emit ONE diagnostic INFO line in the failed_oos branch and one in
      the no_in_sample_candidate branch (wrapped in try/except; existing
      warnings, _last_fail_reason and return values untouched).
- [x] Tests: identical selection / rejection / saved params with diagnostics
      on vs. off; diagnostic content correct; no persistence change on
      rejection; diagnostic exception safety.
- [x] Focused tests, then full suite.
- [x] Read-only: WFO thread vs live trading concurrency.
- [x] Review section + security check + walkthrough.
- NOT touched: scoring, thresholds (WFO_HOLDOUT_MIN_TRADES=3 configured,
  effective 5 via _composite_score), scheduler, strategy, risk, execution.

## Review (Phase 5B)
- engine/wfo_optimizer.py: 3 pure helpers + 2 INFO diagnostic lines (failed_oos
  and no_in_sample_candidate branches) + 3 counters/collections. Every new call
  is in try/except; no existing line's logic changed.
- Reported separately (unchanged): configured holdout min 3, effective min 5
  (via _composite_score -1 floor), effective PF condition is score>0 (the
  warning text's "PF > 0.5" is not what the code enforces).
- tests/test_wfo_optimizer.py: 8 new tests (+ parametrized classifier). WFO file
  35 passed; full suite 615 passed.
- Concurrency (read-only): WFO runs in its own daemon thread; all 27 M30
  slots 06:00-18:30 UTC on 10-18 ran within 5s of schedule, none missed.
- Security: logs contain only counts/ratios, no credentials or account data;
  no new inputs, endpoints, files or dependencies.
- Deployment: needs a bot restart to take effect (NOT done). Not committed.

# Phase 5D: WFO data sufficiency and validation design (plan drafted 2026-10-27)

Context: scheduled WFO runs 10-18 and 10-25 each saved 0/25 pairs (10-25: 21
failed OOS, 2 no in-sample candidate, 2 fetch exceptions EUR_CAD + GBP_JPY,
both Cloudflare 504; 13h25m). Suspected structural cause: backtest skips every
bar until >= 50 confirm-TF candles have closed (backtest/runner.py ~L352-366),
but WFO gives the M30 holdout only 54 H4 candles -> only ~33 of 433 holdout
bars can signal. UNVERIFIED until 5D-1A. Approved order (user, 2026-10-27):

- [x] **5D-1A** Verify warmup starvation, READ-ONLY. Compute, from slice sizes
      and bar timestamps only (no backtest, no optimization, no broker calls),
      the first bar index at which `len(slice_h4) >= 50` in the fit slice and
      the holdout slice, for an M30/H4 pair and for GBP_CAD (H4/D). Explain the
      GBP_NZD 3-holdout-trade anomaly. Report active-bar counts. STOP and
      report before any code change.
- [x] **5D-1B** (B1 IMPLEMENTED + tested 2026-10-27, NOT deployed) Only if 5D-1A confirms: design the smallest safe warmup fix
      (e.g. give each slice enough prior confirm-TF context while scoring only
      its own bars). Get the design approved BEFORE implementing. Tests:
      no-lookahead (nothing after bar i's close visible), holdout isolation
      (no holdout trade/score leaks into fit ranking), active-bar count.
- [x] **5D-3** (IMPLEMENTED + tested 2026-10-27, NOT deployed) Bounded retry for transient historical candle-fetch failures.
      Helper in engine/wfo_optimizer.py around the two get_candles_fn calls
      (connector and live scans untouched). 3 attempts total, ~5s then ~20s
      backoff + jitter. Retry: HTTP 502/503/504, connection reset, connect/read
      timeout, chunked-encoding error. Never retry 400/401/403/404 or any other
      V20Error. Log each retry; re-raise the original exception after the last
      attempt so the existing `except Exception` + "raised an exception"
      counter behave as today. 429 NOT included. Read-only GETs: no order
      path, no duplicate-trade risk. Tests: fake client — retry then success,
      give up after 3, 401/400 not retried, original exception preserved.
- [x] **5D-2** (IMPLEMENTED + tested 2026-10-27, NOT deployed) Explicit validation policy. Config: WFO_HOLDOUT_MIN_TRADES = 5
      (single source of truth; remove the hidden <5 floor inside
      _composite_score from the eligibility path) and WFO_HOLDOUT_MIN_PF = 1.0.
      Keep scoring/ranking separate from eligibility: eligible = trades >= min
      AND PF >= min PF; rank eligible candidates only; accept the best eligible.
      Log a distinct reason per rejection (too_few_trades / low_pf / both /
      none_eligible). Fix warning text to match behaviour. Keep the Phase 5B
      diagnostics working. Tests: boundary cases (4/5 trades, PF 0.99/1.0),
      reason codes, ranking unchanged among eligible candidates.
- [ ] **5D-1C** Only AFTER 5D-1B: evaluate WFO_TRAIN_BARS = 3000 (single
      request; est. runtime 28-38h vs 13.4h measured). Consider 4500 (est.
      42-61h) only if holdouts are still short AND runtime is acceptable.
      Check Sunday-17:00-EDT market-open overlap and CPU contention with live
      scans (WFO shares the bot process). GBP_CAD (H4/D) sized separately.
      Decision on window size comes with measured data, not before.
- [x] **5D-5** (IMPLEMENTED + tested 2026-10-27, NOT deployed) WFO overlap protection + freshness fix. (a) Refuse to start a
      batch if a `wfo-refit` thread is alive (log it). (b) should_refit uses
      integer `.days >= 7`, so a pair saved late in one batch is skipped next
      Sunday. DESIGN WRITTEN 2026-10-27 (see "Phase 5D-5" section at end of
      file); the 6.5-day tolerance idea is WITHDRAWN (a 13.4 h batch defeats it);
      recommended rule is weekly-cycle freshness. Not implemented; awaiting approval.
      Tests for both.
- [ ] **5D-6** Full regression + controlled deployment plan: full test suite
      (watch the known test-isolation gap writing to real logs/state), a
      reduced-grid smoke run only if the user allows it, deploy Mon-Sat with no
      wfo-refit thread running and the bot flat, restart verified in logs.
      Leave bot unchanged until each stage is approved.

Unresolved / out of scope until asked: dedicated WFO process instead of a thread
in the live bot; 429 handling; misfire_grace_time for a missed Sunday run;
whether a third zero-save week is acceptable.

Rules for this phase: no code changes before the stage is approved; no
restarts mid-WFO-run; every stage ends with a review entry here.

## Review (Phase 5D-1A, 2026-10-27) — read-only, no code/config/state touched
Method: code reading + 10-25 wfo.log + a scratchpad arithmetic script that applies
the runner's exact gate (`c_end <= bar_time + p_len`, `len(slice_h4) < 50`) to
synthetic OANDA-style calendars. No backtest, no optimization, no broker calls.
No candle data is cached locally, so exact first-eligible bars on REAL data are
NOT established — results assume contiguous FX weeks and data ending at Friday close.
- Starvation CONFIRMED (arithmetic): M30/H4 holdout = 433 M30 bars + 55 H4 candles
  (hold_c starts 7 bars before hold_p). First eligible index 392; 41 of 433 bars
  (9.5%, ~0.85 trading day) can signal — NOT 33. Fit: first eligible 399, 608 of 1007 (60%).
  Float truncation: int(1440*(1-0.3))=1007, int(180*0.7)=125 (matches the log's 1007/433).
- GBP_CAD (H4/D): same defect, smaller: holdout 133 of 433 eligible (31%), fit 708 of 1007 (70%).
- GBP_NZD 3 trades in ~41 bars: NOT proven. Plausible, code-verified mechanisms:
  pyramiding on (ALLOW_MULTIPLE_PER_PAIR, MAX_PYRAMID_PER_PAIR=3) and the end-of-data
  forced close counted as a trade. Also: in most pairs all 8 holdout candidates logged
  identical trades/PF -> they share one tiny sample, not 8 independent ones.
- Proposed smallest fix (NOT implemented): optional `eval_start` param on run_backtest
  (default = _WARMUP, behaviour unchanged); fetch + prepend ~460 M30 / ~60 H4 context
  bars; fit runs on [context+fit] only; holdout runs on [context+fit+hold] with eval_start
  at holdout start. Awaiting approval for 5D-1B.

# Phase 5D-1B DESIGN (drafted 2026-10-27) — NOTHING IMPLEMENTED, awaiting approval
Scope per user: preserve historical context, keep the 1,440-bar evaluation window
(1007 fit / 433 holdout) UNCHANGED. No strategy change, no threshold change
(-> 5D-2), no window increase (-> 5D-1C), no restart.

## 1. How much context is actually needed (derived from code, not assumed)
| Requirement | Source | Value |
|---|---|---|
| Hard gate: closed confirm candles before a bar may signal | backtest/runner.py L365 `len(slice_h4) < 50`; strategy_ema_cci_macd.py L171 | 50 |
| Pivot/structure/BOS scan depth (both TFs) | strategy_market_structure.py L22 `df.tail(100)` | 100 |
| EMA/MACD/ADX/ATR convergence | engine/indicators.py: all `ewm(adjust=False)`, seeded at the SLICE start and recomputed per bar. Seed weight (1-a)^n: EMA20 (a=.095) n=100 -> 4.5e-5; ADX/ATR (a=1/14) and MACD-slow (a=.074) n=60 -> 1.2e-2, n=100 -> 6e-4 | ~100 |
| Touch lookback | strategy_ema_cci_macd.py L177 (grid, <= 40) | inside slice |
| Live slice window | config.LIVE_CANDLE_WINDOW | 250 |

=> Hard minimum 50 confirm candles (= 400 M30 bars). Scoring-parity sufficient: 100
candles per TF (= 800 M30 bars for M30/H4). Exact live window: 250 candles.
The earlier "460 M30 + 60 H4" figure is WITHDRAWN: it was 400 + the 60-bar warmup, i.e.
the hard minimum only; it ignored the 100-candle pivot depth.
Key observation: the HOLDOUT needs NO extra fetch — the fit slice already precedes it
(1007 M30 / 125 H4 candles >= 100). Only the FIT slice would need extra history.

## 2. Common mechanism (all options)
- `run_backtest(..., eval_start: int | None = None)` (keyword at the END of the signature;
  None = today's behaviour exactly). Loop becomes
  `range(max(_WARMUP, eval_start or _WARMUP), len(df))`. Slices are already
  `df.iloc[max(0,i+1-win):i+1]` and confirm is already gated by `c_end <= bar_time + p_len`,
  so context bars are visible exactly as live would see them and nothing in the future is.
  No trade can open before eval_start (those bars are never evaluated), so fit-period
  positions cannot leak into holdout statistics.
- Context-sufficiency guard: require >= WFO_CONTEXT_CONFIRM_CANDLES (100) closed confirm
  candles at the first scored bar, else report insufficient (reuse reason
  `insufficient_holdout`) instead of silently scoring starved bars (today's failure mode).
- Time-based (not proportional-count) confirm slicing: removes the 7-bar misalignment
  (hold_c started 7 M30 bars before hold_p in the 5D-1A arithmetic).
- Entry-signal accounting (additive result keys only; `total_trades` unchanged):
  `entry_signals` (opens with no same-pair position open), `addon_entries` (pyramids),
  `forced_closes` (positions still open at end of data, closed at the last price and today
  counted as trades), plus an `addon` flag on signal_log rows. Printed by the Phase 5B
  diagnostic so GBP_NZD's 3 trades can be decomposed on the next run, and so 5D-2 can
  decide whether the trade floor counts completed trades or independent entries.

## 3. Option A — Full historical context (live parity everywhere)
Fit and holdout both run on frames with >= 250 candles per TF before the scored period.
- Fetch: M30 1440 + ~2000 context (+margin) ~ 3.5k bars; H4 180 + 250 + margin ~ 440.
  Each is still ONE request (< 4,999). Fit frame = [context + fit]; holdout frame =
  [context + fit + hold].
- Fit becomes fully active (1007 of 1007 vs 608 today): fit rankings CHANGE and are no
  longer comparable with the 10-18 / 10-25 runs.
- Correctness: highest (exact live slices at every scored bar). Complexity: highest (fetch
  sizing, context trimming, fit/hold bookkeeping in run_all_pairs and run()).
- Runtime (ESTIMATE): fit evals 216x1007 = 217k vs 216x608 = 131k (+66%). 13.4h x
  [1.0-1.66] = 13.4-22.2h; holdout adds ~19 min. Regression risk: highest (changes fit ranking).
- Over-delivers: nothing past 100 candles is read by the scoring path.

## 4. Option B — Minimal context injection (RECOMMENDED), staged
B1 (recommended first, holdout-only): the fit run stays byte-identical. Holdout combos run
   on the whole 1440-bar window frames (`train_h1`, `train_h4`) with
   `eval_start = len(fit_h1)`. No new fetch, no change to the fit slice or fit ranking.
   Holdout H4 slice at its first bar: ~125 candles (>= 100); M30 slice = the full 250.
   Holdout becomes 433/433 eligible (from 41).
   Runtime (ESTIMATE): 1930 s/pair / (216 x 608 evals) ~ 14.7 ms per active bar (upper
   bound, assumes all time is active-bar time); holdout adds 8 x (433-41) = 3.1k evals
   ~ +46 s/pair ~ +19 min on 13.4h (+2.4%).
B2 (optional; decide only after B1 results): give the FIT slice 100 prior H4 candles
   (~800 M30 bars) of fetched context so it is fully active too. Same ~+66% fit runtime as A
   and changes fit ranking, so it needs its own approval and measurement.
- Files for B1: backtest/runner.py (`run_backtest` signature + loop + additive result keys);
  engine/wfo_optimizer.py (`_run_combo` pass-through of eval_start; `run()` holdout call
  site ~L405 and the time-based confirm cut; diagnostic line); config.py (one new guard
  knob WFO_CONTEXT_CONFIRM_CANDLES=100 — not a trading threshold).
  B2 additionally: run_all_pairs fetch counts (L554-556).
- Regression risk: lowest — default path unchanged, fit path unchanged, one new optional arg.

## 5. Comparison
| | A full context | B1 holdout-only | B2 = B1 + fit context |
|---|---|---|---|
| Holdout eligible bars (M30/H4) | 433/433 | 433/433 | 433/433 |
| Fit eligible bars | 1007/1007 | 608/1007 (unchanged) | 1007/1007 |
| Scoring parity with live | exact | pivot/EMA depth satisfied | pivot/EMA depth satisfied |
| Extra fetch | ~2k M30 + ~260 H4 | none | ~800 M30 + ~100 H4 |
| Runtime vs 13.4h (estimate) | 13.4-22.2h | ~13.7h | 13.4-22.2h |
| Fit ranking changes | yes | no | yes |
| Complexity / regression risk | high / high | low / low | medium / medium |

## 6. Independence and leakage review
- Fit ranking never sees holdout bars (fit frame ends at the split; for A/B2 the confirm
  frame is also cut to candles whose close <= last fit bar close). Holdout sees fit bars
  only as CONTEXT, which live also sees; no holdout trade can open before eval_start.
- Existing limit that survives this change: the holdout is used to CHOOSE among the top-8
  fit candidates, so the winning holdout score is optimistically biased (best of 8).
  Flagged for 5D-2 (acceptance rule); not changed here. On 10-25 the 8 holdout candidates
  often produced IDENTICAL trades/PF, so they were one sample, not eight.
- End-of-data forced close and pyramiding: semantics unchanged, now counted/reported.
- GBP_NZD 3-trade question: still UNRESOLVED; the new accounting will decompose it on the
  next run, or an offline replay can if backtests are authorized.

## 7. Tests required (written first, per stage)
1. eval_start=None and eval_start=_WARMUP give results identical to today (golden, plus
   the existing determinism tests).
2. No-lookahead: perturb every bar after index k; signals/entries with bar <= k identical.
3. Confirm-closure spy: stub strategy records slice_h4; every candle's close <= bar close.
4. Holdout isolation: no holdout-run trade opens before eval_start; fit-run output is
   identical whether or not holdout bars exist in the source data.
5. Context-sufficiency guard: < 100 closed confirm candles at eval_start -> insufficient,
   not scored.
6. Eligible-bar counts on synthetic calendars: M30/H4 holdout 433/433, GBP_CAD H4/D, and
   the 41 / 133 baseline reproduced with eval_start=None (documents the defect fixed).
7. Accounting: a stub strategy firing every bar -> entry_signals + addon_entries ==
   total_trades; forced_closes == positions open at the end; total_trades unchanged.
8. Slice sizes stay 1007/433; time-based confirm cut removes the -7-bar offset.
9. Existing 35 WFO tests + full suite (615) green. Deployment needs a restart (5D-6 only).

## 8. Recommendation
B1 first. It is the smallest change to the backtest engine (one optional argument), leaves
fit ranking and runtime essentially intact (~+19 min), needs no new fetches, and lifts the
holdout from 41 to 433 eligible bars. Decide B2 / A only after real post-B1 holdout trade
counts, and an offline A-vs-B1 trade-list comparison on identical data (which needs a
separate backtest authorization). Expected holdout trades after B1: ~3.6-12 per candidate
(0.4-1.3 per day x ~9 trading days, pyramids included). Many pairs will still sit near the
5-trade floor, which is what 5D-1C and 5D-2 address.

## Review (Phase 5D-1B, Option B1 IMPLEMENTED 2026-10-27) — NOT DEPLOYED, bot not restarted, nothing committed
Files: backtest/runner.py, engine/wfo_optimizer.py, config.py, tests/test_wfo_holdout_context.py (new, 29 tests),
tests/test_wfo_optimizer.py (8 fake signatures + holdout detection moved from row count to `eval_start`; fixture
disables the new guard because those tests feed degenerate 30-min frames as both TFs).
- runner.py: `confirm_candles_closed_at()` helper (same timestamp rule as the loop); `run_backtest(..., eval_start=None)`
  (default == _WARMUP, floored there); additive result keys `eval_start`, `entry_signals`, `addon_entries`,
  `forced_closes`, plus an `addon` flag on signal_log rows. Counters follow the real `open_trade()` return
  (None == rejected) and the real `manual_close()` return. total_trades / PF / WR / P&L / total_signals unchanged
  (total_signals still logs every attempted open, a pre-existing quirk, pinned by a test).
- wfo_optimizer.py: `_run_combo(eval_start=)` (kwarg omitted when None, so the fit path is byte-identical);
  holdout combos run on the WHOLE window (`train_h1`, `train_h4`) with `eval_start=split`; context guard before
  the grid search (closed confirm candles at the first holdout bar by timestamp < WFO_CONTEXT_CONFIRM_CANDLES ->
  reason `insufficient_holdout`, no 216-combo spend); reject diagnostic now appends entry_signals / addon_entries /
  forced_closes min/med/max.
- config.py: WFO_CONTEXT_CONFIRM_CANDLES = 100 (WFO-only guard, 0 disables). No threshold, window size, scoring or
  fetch-size change; fetch sizes pinned by a test (M30 1500, H4 1440//8+20).
- Tests: 29 new + 35 legacy WFO = 64 green; full suite 644 passed, exit 0 (was 615).
  Default behaviour pinned to a golden captured from the UNMODIFIED code (137 trades, PnL -114.8, PF 0.49).
  Eligibility demonstrated: legacy holdout-only 41/433 (M30/H4) and 133/433 (H4/D) -> B1 433/433 and 433/433.
  Fit stage proven unchanged: 216 calls on exactly the legacy slices (1007 bars / 125 confirm), none seeing a
  holdout bar; holdout candidate order == ranking recomputed from fit-only results.
  Guard on synthetic production-shaped frames: 126 closed H4 candles (M30/H4) and 168 D candles (H4/D) at the first
  holdout bar, vs 100 required.
  Mutation checks (temporary, reverted, files verified restored): forming-candle leak, 1-bar primary look-ahead,
  loop ignoring eval_start, guard removed -> each caught by at least 2 tests.
- Known side effect of running the suite (pre-existing isolation gap, see memory): tests/test_bot_service.py appends
  fake entries to the real logs/bot_service.log (+1,537 bytes per full run) and touches data/adaptive_state.json
  (content hash unchanged). wfo.log, wfo_params.json, live_state_forex.json, paper_state.json byte-identical.
- Remaining uncertainty: all eligibility numbers come from a modelled FX calendar, not real candles; the guard is only
  proven to PASS on that model. If real holdouts are rejected by the guard it is a skip, not a wrong score; check
  the next run's logs. GBP_NZD's 3 holdout trades still unexplained until the new counters appear in a real run.
  B1 does NOT raise fit-side activity (608/1007) — B2/A remain undecided. Deploy needs a restart (5D-6).

## Review (Phase 5D-3 + test isolation, IMPLEMENTED 2026-10-27) — NOT DEPLOYED, bot not restarted, nothing committed
### 5D-3 bounded candle-fetch retries (engine/wfo_optimizer.py only)
- New `_fetch_with_retry(get_candles_fn, pair, granularity, count)` wraps ONLY the two get_candles_fn calls in
  run_all_pairs(). `_transient_fetch_reason()` classifies: V20Error.code in {502,503,504} -> "HTTP n";
  ConnectionResetError anywhere in the exception chain (requests wraps urllib3's ProtocolError which wraps the raw
  reset only in .args) -> "connection reset"; requests Timeout / TimeoutError -> "timeout"; everything else None.
- Never retried: 400/401/403/404/405/408/429/500/501/505, refused connections, DNS, SSL, any other exception, and a
  None/empty result. Max 3 total attempts; waits 5s then 20s x U(0.8,1.2) (<= ~30s per fetch); no sleep after the
  last attempt. Sleeps block only the wfo-refit daemon thread.
- After exhaustion (or for a non-retryable error) the ORIGINAL exception object is re-raised, so the existing
  `except Exception` -> "WFO <pair> failed" ERROR + "raised an exception" counter behave exactly as before.
  Each retry logs pair, timeframe, attempt n/3 and reason; recovery and give-up are logged once.
- Only the failed fetch is retried: the other timeframe is not re-fetched and run()/the grid search is never
  retried or restarted. connectors/oanda_connector.py and main.py: zero diff. Fetch sizes unchanged (M30 1500, H4 200).
- Tests (tests/test_wfo_fetch_retry.py, 49): classification matrix, recovery on 2nd attempt makes NO 3rd request,
  3rd-attempt recovery, exhaustion raises the identical exception object after exactly 3 calls / 2 sleeps,
  non-retryable fails immediately, auth after a transient error stops retrying, backoff/jitter bounds, log content,
  batch-level wiring + summary counts, connector has no retry logic. Network is blocked (socket patched to fail) and
  sleep is recorded, not slept. Mutation checks (retry 401, 4 attempts, swallow on exhaustion, retry any
  ConnectionError, off-by-one at exhaustion) each caught by 2-5 tests; file restored + verified.
- 429 is deliberately NOT retried (not in the approved list). Read timeouts are retried along with connect timeouts.

### Test isolation (test setup only; no production code changed)
- Cause 1: `bot_service` attaches a RotatingFileHandler to the real logs/bot_service.log at import and supervise()
  logs through it -> test_bot_service.py appended fake start/restart lines (+1,537 bytes per full run).
- Cause 2 (found with a throwaway plugin that recorded and blocked every AdaptiveParams._save aimed at the real
  path): learning.feedback_loop.generate_suggestions() calls the module-level adaptive_params singleton's
  force_tier(), which saved to the live data/adaptive_state.json (tests/test_learning.py::TestFeedbackLoop, 2 tests).
  Content hash was unchanged only because the written tiers matched what was on disk; it could have persisted test
  tiers into the live file. In-memory singleton state also leaked between tests.
- Fixes: tests/conftest.py two autouse fixtures (adaptive singleton -> tmp path + per-test state copy; bot_service
  logger handlers -> NullHandler when loaded); tests/test_bot_service.py swaps the handler class during import so the
  real file is never even opened. tests/test_isolation_guards.py (7 tests) fingerprints the real files around the
  risky calls, incl. the real feedback_loop path; verified to fail when the adaptive fixture is disabled.
- Verification: full suite BEFORE the fix grew logs/bot_service.log by 1,537 bytes and touched adaptive_state.json;
  AFTER the fix a full run left logs/wfo.log, logs/bot_service.log, data/wfo_params.json, data/adaptive_state.json,
  data/live_state_forex.json, data/paper_state.json, data/signal_log.csv, data/signal_lifecycle.csv and
  data/signal_id_seq.json identical in size, mtime AND md5. No existing runtime data was deleted or reset
  (the ~3 KB of earlier fake lines in logs/bot_service.log from my two earlier suite runs remain).
- Results: focused 113 (WFO) green; full suite 700 passed, pytest exit 0 (644 -> 700: +49 retry, +7 isolation).
- Open: the stray test lines already in logs/bot_service.log are left in place (not edited). All real-candle
  behaviour of 5D-1B still unverified. Deploy requires a restart (5D-6). Next: 5D-2 (explicit validation policy).

# Phase 5D-2 DESIGN (drafted 2026-10-27) — NOTHING IMPLEMENTED, awaiting approval
Approved starting policy (user): >= 5 completed holdout trades; holdout PF >= 1.0; both checked
explicitly and independently of scoring; ranking keeps the existing composite; in-sample rules,
the 216-combo grid, EMA-Bounce, live trading and risk management all unchanged.
Evidence gathered read-only; no WFO, backtest, broker call, restart, or production edit.

## 0. What the code does today (engine/wfo_optimizer.py; verified by reading + pure-function table)
- Stage 2 loop (L532-556): `if total_trades < min_hold_trd (3): continue`, then
  `hs = _composite_score(hold)`; keep strictly-greater `hs`; reject if `best_params is None or best_hold_score <= 0` (L558).
- `_composite_score` (L144; the hidden floor is the literal `< 5` at L149) returns -1.0 for < 5 trades, else `pf*(0.5+wr)`.
  It is shared by Stage 1 (L506-507: `s > 0` builds the fit ranking) and Stage 2 (L551). Hence "configured 3, effective 5":
  a 3-4 trade candidate passes the `< 3` check, scores -1.0, and loses `hs > -1.0`... silently.
- The documented "PF > 0.5" exists only in prose: the module docstring (L25) and the warning text (L561). The real gate is `score > 0`,
  i.e. "at least one winning trade". Measured on the current code:
    5 trades, PF 0.36 (a LOSING set)  -> _composite_score 0.303  -> would be SAVED today
    5 trades, PF 0.9967               -> 1.096                    -> would be SAVED today
    4 trades, PF 50 / 3 trades PF 15  -> -1.0                      -> dropped (hidden 5 floor)
- `_result_pf` (and backtest `_calc_profit_factor`) returns GROSS PROFIT IN DOLLARS when there are no
  losses (`5 x $0.01 wins -> "PF" 0.05`). Reusing it for a `PF >= 1.0` check would reject a
  candidate with zero losses. Eligibility therefore needs its own PF definition (section 3).
- No code outside wfo_optimizer.py and its tests reads these helpers or the holdout state fields.

## 1. Answers to the five questions
1. Trade counting: `eligible_trades = result["total_trades"]` (completed trades, definition unchanged,
   INCLUDING pyramid add-ons and the end-of-data forced close). `entry_signals / addon_entries /
   forced_closes` (5D-1B) are logged next to every verdict so independent setups can be told apart; they
   do not gate anything. Known consequence, stated plainly: 5 trades can be 2 independent setups + 3 add-ons.
2. PF exactly 1.0: passes (with the trade minimum). `holdout_pf >= min_pf - 1e-9` (tolerance for float
   sums; 1.0 +/- 1 ulp must not flip a verdict). PF < 1.0 fails regardless of composite score.
   Note PF >= 1.0 is equivalent to net holdout P&L >= 0 (spread is already in the backtest fill), so PF = 1.0
   admits a break-even candidate; this is the approved threshold, flagged only so it is a conscious choice.
3. Nothing qualifies: return None before any state/disk write; existing params untouched; no ineligible
   fallback. (Same observable behaviour as today's reject path: `_last_fail_reason="failed_oos"`.)
4. Ineligible candidates never influence the winner: eligibility is a pre-filter; ranking runs over the
   eligible list only; a 4-trade PF-50 candidate cannot displace a 5-trade PF-1.0 one.
5. Reporting: see section 5 (reason codes + per-pair line + one additive batch line).

## 2. Configuration (config.py)
| Constant | Value | Note |
|---|---|---|
| WFO_HOLDOUT_MIN_TRADES | 5 (was 3) | Now enforced AS WRITTEN; the hidden floor is removed from the holdout path |
| WFO_HOLDOUT_MIN_PF | 1.0 (new) | Explicit PF eligibility floor |
| WFO_CONTEXT_CONFIRM_CANDLES | 100 | From 5D-1B, unchanged |
| in-sample floor | `_FIT_MIN_TRADES = 5`, module constant (renamed from `_EFFECTIVE_MIN_TRADES`) | NOT config: in-sample behaviour must not move |
Sanity: `min_trades = max(1, int(...))`, `min_pf = max(0.0, float(...))`; code fallbacks 5 / 1.0.
Lowering MIN_TRADES below 5 later now really admits 3-4 trade holdouts (previously impossible); the fit floor stays 5.

## 3. Functions and branches that change (all in engine/wfo_optimizer.py unless noted)
a) `_composite_value(result)`  NEW, pure: `pf*(0.5+wr)` with the legacy pf (dollar quirk deliberately
   preserved so ranking is unchanged), NO trade floor, errors -> 0.0.
b) `_composite_score(result)`  BEHAVIOUR UNCHANGED, bit-for-bit: `-1.0 if error or total_trades < _FIT_MIN_TRADES
   else _composite_value(result)`. Still the only function Stage 1 calls (L506). The hidden five-trade floor is
   therefore NOT removed from it; the holdout path simply stops depending on it. Golden-pinned (section 7).
c) `_holdout_pf(result)`  NEW: gross_profit/gross_loss; if gross_loss == 0: `inf` when gross_profit > 0,
   else 0.0 (no trades / all break-even => not eligible); errors -> 0.0. Never used for ranking or display of the legacy "PF".
d) `_holdout_eligibility(result, min_trades, min_pf)`  NEW, pure -> `(trades_ok, pf_ok)`.
   `trades_ok = total_trades >= min_trades`; `pf_ok = _holdout_pf(result) >= min_pf - 1e-9`. Eligible = both.
   Any exception => (False, False): an error can only make a candidate ineligible, never eligible.
e) Stage 2 loop (L532-556): replace L549-556 with: record per-candidate (trades_ok, pf_ok, value) for diagnostics;
   `if not (trades_ok and pf_ok): continue`; else append to `eligible`. After the loop pick
   `max(eligible, key=value)` with ties resolved to the EARLIER fit rank (== today's strict `>`).
f) Acceptance (L558): `if winner is None:` reject path (replaces `best_params is None or best_hold_score <= 0`).
   Before writing state, re-assert `_holdout_eligibility(winner)` (defence in depth; a failed assert => reject, never persist).
g) `_classify_oos_rejection` (L181; new signature `(cands, min_trades, min_pf)`) and `_format_oos_diagnostic` (L201): rewritten around the
   explicit thresholds (section 5). `_EFFECTIVE_MIN_TRADES` (L170) references removed. The warning text (L559-563) and the module docstring (L25)
   corrected to quote the real thresholds.
h) `run_all_pairs()`: additive verdict counters + one extra summary line; the existing summary string is untouched.
i) `config.py`: the two constants above. No other production file changes.
Unchanged: Stage 1, grid, top-N selection (`fit_scored[:top_n]`), `_run_combo`, B1 `eval_start`, the context guard,
fetch retries, `_save`, persisted state fields (no new keys), `should_refit`.

## 4. Order of checks
A. Pre-grid (unchanged order): `insufficient_data` (< 120 bars) -> holdout slice < 40 bars -> 5D-1B context guard.
B. Stage 1 (unchanged): grid -> `_composite_score > 0` -> sort -> `no_in_sample_candidate` if empty.
C. Stage 2, per top-N fit candidate: holdout backtest on the whole window with `eval_start` (5D-1B) ->
   (None => counted, skipped) -> eligibility (trades_ok, pf_ok) -> eligible list.
D. Ranking: eligible only, by `_composite_value`, tie -> earlier fit rank.
E. Acceptance: winner exists AND re-asserted eligible -> write state + `_save` (existing path). Otherwise reject,
   no state or disk write of any kind.

## 5. Reporting
`_last_fail_reason` keeps its four batch buckets (so the existing summary line and counters are unchanged).
New additive attribute `_last_fail_detail` (reset next to `_last_fail_reason`):
| Situation | _last_fail_reason (unchanged) | _last_fail_detail (new) |
|---|---|---|
| < 120 bars | insufficient_data | - |
| holdout slice < 40 bars | insufficient_holdout | holdout_too_small |
| < N closed confirm candles at the boundary | insufficient_holdout | insufficient_context |
| no fit candidate | no_in_sample_candidate | - |
| all holdout runs returned None | failed_oos | no_holdout_results |
| no candidate has enough trades AND none has PF >= min | failed_oos | both |
| none has enough trades; some PF ok | failed_oos | too_few_trades |
| some have enough trades; none PF ok | failed_oos | low_pf |
| trades ok on some, PF ok on others, never the same candidate | failed_oos | mixed |
| fetch failure / any other exception | counted by existing "insufficient data (candle fetch)" / "raised an exception" | - |
Per-pair INFO line (replaces the Phase 5B text): thresholds, `trades_ok=a pf_ok=b eligible=c`, failed-trades-only /
PF-only / both counts, holdout trades and holdout PF min/med/max (eligibility PF; inf shown as inf), plus the
5D-1B `entry_signals / addon_entries / forced_closes`. One additive batch line after the unchanged summary:
`WFO batch OOS breakdown: too_few_trades=a low_pf=b both=c mixed=d no_holdout_results=e | insufficient_context=f`.

## 6. Interactions with Phase 5D-1B
- Holdout counts will rise (433/433 bars eligible), so the 5-trade floor becomes reachable; that is the intent.
- Counts still include add-ons and the forced close at the end of the window; a forced close is priced at the last
  bar and feeds both the trade count and PF. Kept (user: do not redefine counting yet) but always logged.
- The context guard keeps its position (before the grid) and gains the `insufficient_context` detail.
- `eval_start`/whole-window holdout frames unchanged; the holdout is still used to CHOOSE among the top-8, so the
  winning holdout PF is optimistically biased (best of 8). Out of scope; candidate follow-up: require holdout PF >= a
  fraction of fit PF, or Bonferroni-style PF margin.
- The 8 holdout candidates frequently produce identical trades (10-25 logs): requiring PF >= 1.0 on one shared sample
  is a weaker test than it looks. Flagged, not addressed.

## 7. Tests (written first; new file tests/test_wfo_validation_policy.py)
Boundary matrix (trades x PF), via `_holdout_eligibility` and end-to-end through `run()` with a fake backtest:
- trades {0, 4, 5, 6} x PF {no wins (0), 0.99, exactly 1.0, 1.01, no losses (inf)}: eligible iff trades >= 5 and PF >= 1.
- exactly 1.0 passes: `[10,10,10,-15,-15]`; float case `[0.1,0.2,0.3,-0.3,-0.3]`-style sums that land on 0.9999999999 pass via tolerance.
- no-loss tiny profit `[0.01]*5` is ELIGIBLE (legacy `_result_pf` would say 0.05); `[0]*5` break-even NOT eligible.
- PF 0.36 / 0.99 candidates with high composite scores rejected (today they would be saved).
- MIN_TRADES / MIN_PF read from config (set 3 -> 3-trade candidate eligible; defaults pinned 5 / 1.0).
Ranking: extreme-score ineligible never wins; eligible low-score beats ineligible high-score; tie -> earlier fit rank;
only eligible candidates are ranked; exceptions inside eligibility => ineligible.
Rejection safety: seeded `_state` + on-disk file byte-identical (md5) after a rejection; `_last_save_ok is None`;
`_save` not called (spy); `run()` returns None; for each verdict (too_few / low_pf / both / mixed / none).
In-sample unchanged: golden table pinned from the current code (values above: 5-trade PF 1.0 -> 1.1, PF 0.9967 -> 1.09633,
0.3636 -> 0.30303, [0.01]*5 -> 0.075, all-loss/breakeven -> 0, 4-trade/3-trade/error -> -1.0); Stage 1 call count (216) and
fit ranking order test from 5D-1B stay green; `_composite_score` still the function Stage 1 calls (spy).
Reporting: every detail code in section 5, the thresholds and entry_signals fields in the per-pair line, the new batch line,
the unchanged legacy summary string (exact match), detail resets between pairs (no stale value).
5D-1B interplay: holdout still called with eval_start; context failure => detail insufficient_context + reason insufficient_holdout.
Legacy tests that encode the old rules and WILL need updating (expected, listed so it is deliberate):
tests/test_wfo_optimizer.py: the Phase 5B diagnostic-content test (~L736-765: "effective min=5", "PF>0.5" strings,
`verdict=low_pf`), `test_classify_oos_rejection` (param table; new signature + `mixed`), and any `_profile_fake` expectation
that depends on `score > 0`. Others (fits, saves, batch summaries) are expected to pass untouched; to be confirmed when run.

## 8. Expected impact
- Runtime: none. No extra backtests; eligibility is O(top_n = 8) arithmetic per pair; a rejection saves nothing it did not save before.
- Logging: per-pair diagnostic line count unchanged (rewritten text, a few more fields); +1 batch line per run.
- Outcomes (honest): cannot be forecast without real post-5D-1B results. PF >= 1.0 and >= 5 trades is stricter than today
  (which would save a PF 0.36 set) but the holdout is ~10x larger, so some pairs may finally qualify and some correctly will not.
  A third zero-save week is still possible and would be a legitimate outcome, not a fault.
- Persistence: state fields and `_save` path unchanged; a rejection still leaves existing params exactly as they were.
- Deploy: needs a restart (5D-6); nothing is live.

## 9. Open questions for the reviewer
1. Keep the ranking PF quirk (no-loss sets rank by dollar profit) for now, or fix in a later phase?
2. Should the forced end-of-window close count toward the 5 trades? (Kept as-is; logged.)
3. Add a fit-vs-holdout PF consistency requirement later, given best-of-8 selection bias?
4. Accept PF == 1.0 (break-even) as stated, or use a small margin such as 1.05?

## Review (Phase 5D-2, explicit holdout validation policy — IMPLEMENTED 2026-10-27) — NOT DEPLOYED, bot not restarted, nothing committed
Files (this phase): config.py, engine/wfo_optimizer.py, tests/test_wfo_validation_policy.py (new, 88 tests),
tests/test_wfo_optimizer.py (fixture -> production policy 5 / 1.0; Phase 5B diagnostic-content test and the classifier
table rewritten to the new semantics), tests/test_wfo_holdout_context.py (one threshold assertion 3 -> 5, + MIN_PF).
backtest/runner.py is unchanged by this phase (its diff is 5D-1B's).
- config.py: WFO_HOLDOUT_MIN_TRADES 3 -> 5 (now enforced as written), WFO_HOLDOUT_MIN_PF = 1.0 (new).
- wfo_optimizer.py: `_composite_score` behaviour unchanged (only its literal 5 became the named `_FIT_MIN_TRADES`; the
  hidden in-sample floor is still there; Stage 1 untouched). New pure helpers: `_composite_value` (floor-free twin, legacy
  pf quirk kept so ranking is unchanged), `_holdout_pf`, `_trades_meet`, `_pf_meets`, `_holdout_eligibility`,
  `_verdict_from_flags`, `_policy_thresholds`. Stage 2 now: backtest -> explicit eligibility -> rank ELIGIBLE only (max
  composite, tie -> earlier fit rank) -> acceptance independently re-asserts eligibility -> existing state write. No eligible
  candidate => return None before any state/disk write. `_EFFECTIVE_MIN_TRADES` removed; docstring/warning/diagnostic text
  now quote the enforced thresholds. `_last_fail_detail` added (additive); `run_all_pairs` keeps every existing bucket and
  summary string and adds one "WFO batch OOS breakdown" line.
- Results: new file 88 passed; WFO group 213 passed; FULL SUITE 793 passed, pytest exit 0 (700 -> 793); nine live runtime
  files (bot_service.log, wfo.log, wfo_params.json, adaptive_state.json, live/paper state, signal_log/lifecycle/seq)
  identical in size, mtime AND md5 across the run.
- Golden: `_composite_score` on 9 fixed cases (captured from the pre-change code before any edit) identical, incl. the
  hidden floor (-1.0 for 3/4 trades), the PF-0.36 set scoring +0.30, [0.01]*5 -> 0.075. A spy shows Stage 1 calls it exactly
  216x and the holdout path never calls it.
- Boundary results: trades {0,4,5,6} x PF {0, 0.99, 1.0, 1.01, no-loss}: eligible iff trades >= 5 and PF >= 1 (20 cases).
  5 trades PF 0.36 -> (True, False) rejected (old gate would have saved it). 5 trades PF exactly 1.0 -> eligible.
  Float case [0.3,0.3,-0.1,-0.2,-0.3] (PF 0.9999999999999998) eligible; shortfalls 1e-12 / 1e-10 pass, 2e-9 / 1e-7 / 1e-4 / 1e-2 fail.
  No-loss [0.01]*5 -> PF +inf, eligible (legacy `_result_pf` says 0.05). [0]*5 -> undefined -> rejected even with min_pf = 0.
  NaN / inf / missing / non-numeric / non-list trade data -> rejected, never treated as profitable.
- Persistence safety: for six no-eligible scenarios (too few trades, PF 0.36, PF 0, 4-trade PF-50, PF 0.9967, every holdout
  backtest erroring) `_save` is never called, the seeded on-disk file is byte-identical with identical mtime, `_state`
  unchanged, `_last_save_ok is None`. A forced "selection said yes, final check says no" run writes nothing
  (`acceptance_recheck_failed`). An exception inside eligibility makes a candidate ineligible, not eligible.
- Mutation checks (8, each temporary, file restored + diffed): PF check ignored, trade check ignored, tolerance widened to
  1e-2, tie -> later rank, re-check removed, no-loss PF as dollars, in-sample floor 3, zero/zero PF as 0.0 -> each caught
  by 1-8 tests.
- Neighbours verified: 5D-1B (`eval_start` still passed for every holdout run, context guard + `insufficient_context`
  detail) and 5D-3 (504-then-success fetch retry unchanged) tests green; isolation guards green.
- Deliberate deviations from the design text (all stricter or additive): undefined PF is NaN, not 0.0, so a 0/0 set is
  ineligible even if min_pf were set to 0; extra detail code `acceptance_recheck_failed`; the breakdown line appends
  ` other=N` only when an unrecognised detail occurs.
- Unresolved / not addressed (per decisions): ranking still uses the legacy dollar-valued PF for no-loss sets; forced
  end-of-window closes and pyramid add-ons still count toward the 5 trades (logged separately); best-of-8 holdout selection
  bias and the identical-sample issue remain (fit-vs-holdout consistency deferred). PF >= 1.0 admits break-even and is not
  evidence of a reliable edge. Real-candle behaviour of 5D-1B is still unverified, so the real acceptance rate is unknown
  and another zero-save week remains possible. Nothing is live: a restart (5D-6) is required before any of 5D-1B/3/2 runs.


# Phase 5D-5 — WFO overlap guard + weekly-cycle freshness (DESIGN ONLY, 2026-10-27) — nothing implemented

## 1. Verified facts (read from code/logs; nothing run)
- main.py:1434-1479: `_wfo_job` logs, starts a daemon `wfo-refit` thread and RETURNS. APScheduler therefore sees every
  job instance finish in microseconds, so `max_instances=1` can never block anything. Scheduler = `BlockingScheduler`
  (default thread-pool executor); cron = `CronTrigger(day_of_week="sun", hour=2, minute=0)` built WITHOUT a timezone, so it
  uses the machine zone (tzlocal = America/New_York): wfo.log line 1 shows "02:00:00 ... fired at 06:00:00 UTC". (The
  "Sundays 02:00 UTC" startup message is wrong; known item, unchanged here.)
- Two entry points reach `run_all_pairs`: the scheduler job (main.py) and the dashboard "WFO" button
  (dashboard/app.py:3112-3124, runs in the same process as a dashboard thread, uses the same module singleton). Nothing stops
  a button press while the Sunday batch runs; the 2026-10-25 batch ran 02:00 -> 15:25 EDT (13.4 h), so that window is wide.
- Shared mutable state a second batch would touch: `_state` + `wfo_params.json` (whole-file rewrite per save, `_lock` only
  guards the dict, not the sequence), and the instance-level handoff attributes `_last_save_ok`, `_last_fail_reason`,
  `_last_fail_detail`, which are only correct if exactly ONE batch is running. Two batches would also double OANDA load
  and CPU in the live-trading process and interleave wfo.log.
- `should_refit` (wfo_optimizer.py:489): `(now - fitted_at).days >= 7`. `fitted_at` is written at SAVE time (line ~713),
  i.e. after the trigger. So age at the next trigger is always 168 h minus (save time - trigger time) -> `.days == 6`
  -> skipped, for EVERY pair saved by the previous batch, even one saved one second after the trigger. By code reading
  (not yet observed in logs), a saved pair refits only every second Sunday.
- DST makes pure elapsed time worse: Sun 2026-10-25 02:00 EDT -> Sun 2026-11-01 02:00 EST is 169 h; Sun 2027-03-07/14
  spring-forward week is 167 h.

## 2. Overlap guard
Design: a non-reentrant `threading.Lock` (`self._batch_guard`, separate from `_lock`) in `WFOOptimizer`, taken with
`acquire(blocking=False)` at the very top of `run_all_pairs`, released in a `finally` that wraps the whole existing body.
- Not acquired -> log WARNING to wfo.log ("WFO batch skipped: another batch is already running (started <UTC>, running
  <h:mm>)"), return immediately. No fetches, no `run()`, no state/handoff-attribute writes, and the skipper never
  releases a lock it did not take.
- Acquired -> record `_batch_started_at`, run the unchanged body (the existing body is moved into `_run_batch_locked`,
  or simply indented under try/finally; behavior, counters and log strings otherwise identical).
- `batch_running()` read-only helper (non-blocking probe of the lock) for the dashboard.
- In-memory only: a restart clears it (nothing to go stale, no lock file). A daemon thread killed mid-batch dies with the
  process, so the lock cannot outlive it.
- No automatic stale-takeover: a "break the lock after N hours" rule would recreate the overlap it prevents. A genuinely
  hung batch is visible instead (every skipped trigger logs the running time) and cleared by restart. Connector requests
  already have timeouts.
- main.py: UNCHANGED. The scheduler job still logs "launching background re-fit" and the thread's first act is the skip line
  (slightly redundant but accurate and in wfo.log). Keeps the most safety-critical file out of this change.
- dashboard/app.py (small, needs approval): before `send_wfo_started`, if `wfo_optimizer.batch_running()` return
  "WFO already running — not started" (no Telegram). `run_all_pairs` returns `True` if it ran / `False` if skipped
  (was None; no caller uses the value) so `_run_wfo` sends `send_wfo_complete` only when the batch actually ran.
- Manual runs: covered because the guard lives in the method, not the callers. Manual and scheduled runs are mutually
  exclusive, first one wins. Limitation: process-local. A standalone `python dashboard/app.py` (debug entry,
  app.py:4044) would have its own singleton and is NOT covered; main.py's single_instance lock already prevents two bots.
  Not adding a file lock (more moving parts than the risk warrants); documented.
- Out of scope, noted: the dashboard button uses `FOREX_PAIRS + FOREX_WATCH` and ignores `STRATEGY_OVERRIDE`, unlike
  `_wfo_job` (pre-existing; would tune pairs that do not run EMA-Bounce).

## 3. Freshness — options compared
| Option | Rule | Verdict |
|---|---|---|
| Today | `.days >= 7` | Always skips pairs saved the previous week (above). |
| Elapsed 168 h | age >= 168 h | Same failure by construction (save is after the trigger) + DST. |
| Tolerance 6.5 d (this phase's earlier note) | age >= 156 h | WRONG with real batches: a pair saved >12 h after the trigger (batch ran 13.4 h) is 154.6 h old and is skipped. I am withdrawing that suggestion. |
| Tolerance 6.0 d | age >= 144 h | Works up to 24 h batches; breaks again for the 28-38 h batches 5D-1C may bring. |
| **Weekly cycle (recommended)** | eligible iff `fitted_at` < start of the current scheduled cycle | Independent of batch length and DST; matches the user's stated preference. |

Weekly-cycle rule: `cycle_start(now)` = most recent scheduled trigger instant (Sunday 02:00 in the SAME zone the cron
uses) at or before `now`. `should_refit` = `fitted_at < cycle_start(now)`. A pair is therefore refit at most once per
scheduled cycle (anything saved at/after the trigger, however late, is fresh until the next trigger) and is always
eligible at the next trigger, even if saved 36 h into the previous batch.
- Zone: `tzlocal.get_localzone()` — the identical call APScheduler's CronTrigger makes — so cron and freshness cannot
  disagree; arithmetic done on aware local datetimes (zoneinfo) so DST weeks are 167/169 h without special cases.
- Schedule constants: add `WFO_CYCLE_DAY_OF_WEEK` (Mon=0, Sun=6), `WFO_CYCLE_HOUR`, `WFO_CYCLE_MINUTE` to config.py
  (6/2/0). main.py's cron stays hardcoded, so add a drift test that parses main.py's `CronTrigger(...)` arguments and
  asserts they equal config. (Alternative: one-line main.py edit to read config — not proposed.)
- `WFO_REFIT_DAYS` kept: `weeks = max(1, round(refit_days / 7))`; cutoff = `cycle_start - (weeks-1)` calendar weeks.
  7 -> every cycle (default behavior); 14 -> every second cycle.
- Unchanged: `fitted_at` format/value, `wfo_params.json` schema, stalest-first ordering, unparseable/missing/naive
  timestamp -> eligible (today's behavior via except). New: a `fitted_at` more than 1 day in the FUTURE (clock skew)
  -> eligible + warning (today it would look fresh for weeks).
- `should_refit(pair, now=None)`: optional injectable clock for deterministic tests; production callers unchanged.
- Behavior notes to accept: (a) a manual run mid-week (e.g. Wed) marks that pair fresh for the rest of that cycle and it
  refits again at the next Sunday trigger; (b) a manual re-run in the same cycle skips already-saved pairs and processes
  only unfitted/rejected ones, which also makes it a clean "resume after a mid-batch crash"; (c) a pair that FAILED
  validation records nothing (no state-format change allowed), so a same-cycle manual run will re-attempt it (13 h-scale
  cost, 35 min per pair). Persisting attempt markers is possible but needs a state-format approval — not proposed.

## 4. Affected files and restart
engine/wfo_optimizer.py (guard, `batch_running`, `should_refit`, cycle helper), config.py (3 constants),
dashboard/app.py (button handler; optional), tests (new `tests/test_wfo_batch_guard.py`, `tests/test_wfo_freshness.py`;
existing `test_run_all_pairs_logs_and_reraises_should_refit_exception` must keep passing). main.py, connectors, backtest,
trading/risk code: untouched. No state migration. Loaded only on restart, so it rides the single 5D-6 restart
(Mon-Sat, bot flat, no `wfo-refit` thread alive); nothing changes live until then.

## 5. Tests (deterministic: threading.Event/Barrier, injected clock + ZoneInfo, no sleeps, tmp paths, fake get_candles)
Guard: (1) 2nd call while 1st blocked in fetch -> returns False, 0 fetches/0 run() calls, WARNING with start time;
(2) 10 threads on a Barrier -> exactly one executes; (3) lock released after normal completion -> next call runs;
(4) released after per-pair exception; (5) released when `should_refit` raises (exception still propagates, existing
log-and-reraise preserved); (6) released after BaseException (KeyboardInterrupt/SystemExit) in the fetch fn;
(7) skipper never releases the holder's lock (`batch_running()` still True after skip); (8) while blocked, a skipped call
leaves `_state`, `_last_*` attrs and wfo_params.json bytes/mtime untouched; (9) simulated restart (new instance) is
unlocked and reads freshness from disk; (10) manual path vs scheduled path through the same singleton: dashboard handler
returns "already running" and sends no Telegram when busy, and sends exactly one completion when it ran; (11) singleton
identity (`main` and `dashboard` import the same object); (12) existing summary/breakdown log lines unchanged.
Freshness (zone America/New_York): saved Sun 02:00:01 -> next Sun 02:00:00 eligible (fails on today's code); saved +13.4 h
eligible (fails under 6.5 d); saved +36 h eligible; same-cycle checks fresh at Sun +1 min, Sat 23:59, next Sun 01:59:59;
eligible at exactly next Sun 02:00:00; `fitted_at == cycle_start` fresh (boundary pinned); DST fall-back (2026-11-01) and
spring-forward (2027-03-14) weeks; Sunday-before-02:00 now maps to the PREVIOUS cycle; Wed manual fit then next Sunday
eligible; two `run_all_pairs` calls in one cycle -> second skips saved pairs; `WFO_REFIT_DAYS=14`; missing/empty/garbage/naive
`fitted_at` -> eligible; future `fitted_at` -> eligible; `should_refit` writes nothing; saved-record keys identical to
today; config/main.py cron drift test. Plus mutation checks (guard `acquire` removed, release removed, `<` flipped to `<=`,
.days restored) each must fail a test, and the fingerprint check that real logs/state are untouched.

## 6. Decisions requested before implementation
1. Weekly-cycle rule (recommended) over a tolerance? 2. `run_all_pairs` returns bool (None before)? 3. Approve the small
dashboard/app.py edit? 4. No automatic stale-lock takeover (hung batch = restart)? 5. `WFO_REFIT_DAYS` mapped to whole
cycles as above? 6. Accept that failed-validation attempts are not recorded (a same-cycle manual run retries them)?


## Review (Phase 5D-5, overlap guard + weekly-cycle freshness — IMPLEMENTED 2026-10-27) — NOT DEPLOYED, bot not restarted, nothing committed
Preliminary checks (both passed, no stop condition):
- Process topology: main.py:1321 hosts the dashboard in a daemon THREAD of the bot process (`dash_app.run`), and both the
  `wfo-refit` job and the dashboard button import the same module singleton `engine.wfo_optimizer.wfo_optimizer`. Not
  launched by run.ps1 / bot_service.py. Only other entry: `python dashboard/app.py` (debug dev entry, own process, own
  singleton) -> NOT covered by a process-local lock; unsupported while the bot runs. Pinned by a topology test.
- Callers of run_all_pairs: main.py (Thread target, value discarded), dashboard/app.py (button), tests (ignore value).
  Nothing depends on None, so bool is compatible. True = "this call executed the batch" (zero saves still True);
  False = skipped; exceptions still propagate.
Config semantics: new WFO_SCHEDULE_TZ="America/New_York", WFO_CYCLE_DAY_OF_WEEK=6, WFO_CYCLE_HOUR=2, WFO_CYCLE_MINUTE=0,
WFO_REFIT_CYCLES=1 (integer >= 1; missing/0/negative/non-integer/bool/NaN/inf -> 1 + warning). WFO_REFIT_DAYS is left in
config, documented LEGACY, and no longer read (nothing else read it; no test did). Cron constants must equal main.py's
CronTrigger (drift test); a startup warning fires if the machine zone != WFO_SCHEDULE_TZ.
Changed: engine/wfo_optimizer.py (guard, batch_running, on_start, current_cycle_start, _refit_cycles, should_refit(now=)),
config.py, dashboard/app.py (`_start_wfo_from_dashboard` only), tests/test_wfo_batch_guard.py (new, 30),
tests/test_wfo_freshness.py (new, 56). main.py, connectors, scoring/validation/grid/window: untouched.
Results: 86 new pass; WFO group 299 pass; FULL SUITE 879 passed (793 -> 879); nine runtime files identical in
size/mtime/md5 before and after. Mutations (10, each caught, file restored + diffed): guard bypass, release removed,
skipper releases holder's lock, skip returns True, `<`->`<=`, legacy .days, UTC zone, cycles ignored, on_start before guard,
naive timestamp treated fresh.
Corrections to the design text: the spring-forward cron instant is 07:00 UTC (02:00 EST, nonexistent locally), so
Mar 7->14 is 168 h and Mar 14->21 is 167 h (not the 167 h I stated for the first); fall-back week 169 h as stated. Python
`==` between aware datetimes in a DST gap is unreliable, so comparisons are done in UTC.
Dashboard: busy -> "WFO optimization is already running. No additional batch was started." (no Telegram); otherwise
the thread starts and the "started" notice is sent from on_start INSIDE the guard, "complete" only if run_all_pairs
returned True, so a request that loses the race announces nothing. Its immediate message says "start requested ... unless
another batch is already active" (a race loser cannot know synchronously).
Deployment checks for 5D-6: confirm no second main.py/WFO process (single_instance main.lock; no dashboard standalone);
confirm machine zone is America/New_York (tzlocal 5.4.4 reports it today); tzdata is installed in venv (2026.2) via
APScheduler/tzlocal but is not pinned in requirements.txt (not edited here).
Unchanged by design: failed attempts record nothing (only successful saves update fitted_at); no automatic lock takeover.


## Review (Phase 5D-6 Step 1, deployment readiness audit, 2026-10-28 07:1x EDT) — READ-ONLY, bot untouched
Verdict: READY WITH CONDITIONS. Nothing stopped/restarted/staged/committed; no broker contact; no config change.
- Git: branch trade-log-pagination-export @ 0a11ed6, 11 tracked files modified + 6 new test files, all uncommitted.
  Unrelated: file `0` (content "nono"), scripts/signal_lifecycle_report.py + its test, 4 stray .txt/odd-named files.
- Tests: 879 passed (103 s); 9 runtime files identical before/after (bot scans only at :00/:30; run was 07:10-07:12).
- Processes: one tree, bot_service x2 (9316, 11148) -> main.py x2 (14728, 12848), started 2026-10-24 02:15; innermost
  12848 owns port 8050 and holds main.lock. Started BEFORE every modified file -> live bot runs none of 5D-1B/3/2/5.
  Other python processes belong to unrelated projects (MTTC agent, Reinforced_Trading Bot dashboard).
- WFO: last wfo.log line is 10/25 15:25:05 "batch complete"; no later "batch starting" -> no batch running.
- Scheduler: cron built as main.py builds it -> tz America/New_York; next fires Sun 11/1 02:00 -05:00 (07:00 UTC),
  11/8, 11/15 ...; current_cycle_start == fire instant for all six. venv: tzdata 2026.2, tzlocal 5.3.1, APScheduler 3.11.2.
- Config: WFO_REFIT_DAYS has no consumer left (config comment only); WFO_REFIT_CYCLES=1; cycle constants match main.py.
- Open position (LOCAL state only): AUD_USD long 1756u, id 840, opened 10/27 10:00 UTC, SL 0.70023, TP1 0.70648,
  no TP hit, BE not set. Connector attaches stopLossOnFill + takeProfitOnFill(TP1). Broker-side verification NOT done.
- Conditions: (1) verify trade 840 + its SL/TP1 at OANDA (or wait until flat); (2) uncommitted code = no rollback point,
  back up/commit first (user decision); (3) restart before Sun 11/1 02:00 EST; (4) kill supervisors BEFORE main.py;
  (5) never launch standalone dashboard; (6) leave unrelated files out of any commit.
