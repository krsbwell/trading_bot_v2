# Fix: WFO live parameter tuner had zero out-of-sample validation — overfit params were live on GBP_CAD (2026-08-11) — DONE

## Why
User asked why the bot lost 9 of its last 10 real closed trades (checked
`data/live_state_forex.json`'s `closed_trades`, sorted by `close_time`:
confirmed 9/10, all clean `sl` exits, no logging bug). Told to fix it, not
just diagnose it — proceeding without further check-ins per explicit
instruction.

Traced the losses by pair: EUR_JPY 0/4, CHF_JPY 0/2, GBP_CAD 1/4 since
2026-07-31 (only 15 trades total logged — thin sample). Ran the same
fresh walk-forward methodology that got USD_CAD/EUR_AUD paused on
2026-08-05 (train=1500/test=750/step=500, 4500 M30 bars, real OANDA data)
against all three. Unlike USD_CAD/EUR_AUD's "OOS PF collapsed to 0 across
multiple windows" signature, EUR_JPY/CHF_JPY/GBP_CAD's OOS PF is noisy
(swings from 0 to 11+ across 1-7-trade windows) but doesn't consistently
collapse — **not enough evidence to pull them from FOREX_PAIRS**, so they
were left active (see Review below, no config.FOREX_PAIRS/FOREX_WATCH
change made).

While checking each pair's tuning, found something more concrete:
`data/wfo_params.json`'s GBP_CAD entry (`fitted_at: 2026-07-30`) claimed
an **83.3% win rate off just 6 in-sample trades**. Read
`engine/wfo_optimizer.py::WFOOptimizer.run()` (the function the Sunday
02:00 UTC live scheduler job calls, `main.py:1215`): it grid-searches 216
parameter combinations, ranks them by `_composite_score` (profit-factor x
(0.5+win_rate)) **on the same data the combos are scored from**, and saves
whichever one scores highest, with only a 5-trade floor — no
out-of-sample check anywhere. With 216 combos scored against the same
window, some combo is virtually guaranteed to look great by chance alone.
GBP_CAD's fit is exactly that: every real GBP_CAD trade placed under
those params since 2026-07-30 has lost except one (1W/3L, -$10.42).

`engine/wfo_optimizer.py` had **zero test coverage** before this fix
(`tests/` has no `test_wfo_optimizer.py` prior to today).

## What changed
- `engine/wfo_optimizer.py::WFOOptimizer.run()`: rewritten to split the
  training window into an earlier FIT slice and a later HOLDOUT slice
  (`config.WFO_HOLDOUT_FRAC`, default 0.3 — 30% held out). Stage 1 ranks
  all 216 combos on the FIT slice only, same as before. Stage 2 takes the
  top `WFO_HOLDOUT_TOP_N` (default 8) fit-stage candidates and re-scores
  each on the untouched HOLDOUT slice; only saves the one with the best
  holdout composite score, and only if it clears `WFO_HOLDOUT_MIN_TRADES`
  (default 3) holdout trades AND a positive holdout composite score
  (PF > 0.5). If nothing clears that bar, **the previously-saved params
  are left untouched** rather than overwritten with an unvalidated combo
  — the live bot keeps running on whatever it had (or config defaults, if
  it never had a fit) instead of a fresh guess.
- Saved state's `win_rate`/`total_trades`/`total_pnl` now reflect the
  **holdout** evaluation, not the in-sample one — this is what the
  dashboard's Learning panel displays, so it now shows honestly
  OOS-validated numbers instead of the prettier in-sample ones (no
  dashboard code change needed — `panels.py` just renders whatever's in
  the state dict, already keyed the same way).
- `config.py`: added `WFO_HOLDOUT_FRAC` (0.3), `WFO_HOLDOUT_TOP_N` (8),
  `WFO_HOLDOUT_MIN_TRADES` (3) — same per-feature-knob pattern as every
  other tunable in this file, documented inline with the "why" above.
- `tests/test_wfo_optimizer.py` (new, 4 tests): an in-sample winner that
  craters out-of-sample must be rejected in favor of a combo that
  genuinely holds up; saved stats must come from holdout, not fit-stage;
  a pair where nothing validates must keep its existing saved params
  (not get wiped to nothing); a training window too small to form a
  meaningful holdout slice must decline to refit rather than validate
  against noise.

## Live data changes (data/, gitignored — not code)
- **GBP_CAD's overfit `wfo_params.json` entry removed.** Forced a real
  refit through the fixed code against real OANDA data (1500 M30 bars):
  **no combination validated out-of-sample** — confirms the 83%/6-trade
  fit was noise, not edge. GBP_CAD now falls back to `config.py`
  defaults (min_score=55, CCI=20, MACD 12/26/9, etc.) until a future
  weekly refit actually finds something that holds up. Backed up the
  pre-fix file to `data/wfo_params.json.bak_before_holdout_fix_20260811`
  before touching it.
- **EUR_JPY, CHF_JPY**: forced refits found no validating combination
  either. Both already had no WFO entry (never tuned since their
  2026-07-29 promotion — global defaults only), so this is a
  confirmation of the status quo, not a change: still running config
  defaults. Every pair's `should_refit()` will keep retrying weekly
  (Sunday 02:00 UTC, background thread, ~20 min/pair — confirmed this
  doesn't block the signal loop) until something actually validates.

## Explicitly NOT changed
- `config.FOREX_PAIRS` / `FOREX_WATCH`: no pair removed. The fresh
  walk-forward diagnostic didn't show the "OOS PF collapsed to 0" pattern
  that justified pausing USD_CAD/EUR_AUD — EUR_JPY/CHF_JPY/GBP_CAD's
  noisy-but-not-dead OOS numbers don't clear that bar. Revisit once more
  live trades accumulate (still well under the ~30-trade trust threshold
  used elsewhere in this project).
- Did not touch `MIN_CONFLUENCE_SCORE`, any gate, `TP_RR_PER_PAIR`,
  `BREAKEVEN_*`, or any strategy module — this fix is scoped to the
  parameter-tuning pipeline that feeds those knobs, not the knobs or
  gates themselves.

## Verification
Full suite: 261/261 pass (257 pre-existing + 4 new). Forced live refits
run against real OANDA practice-account data (not synthetic), confirming
the new validation logic behaves the same way on real data as in the
unit tests (rejects, doesn't crash, doesn't silently accept). `ast.parse`
clean on both changed files.

## Review — what this does and doesn't answer about "why 9/10 losses"
This is a real, confirmed structural bug (unvalidated live parameter
tuning actively serving an overfit config to a currently-active pair) and
it's fixed with test coverage, not just a one-off patch. It is **not**
the sole explanation for the 9/10 losing stretch — GBP_CAD accounts for
1 of those 9 losses; EUR_JPY (4 losses) and CHF_JPY (2 losses) were never
WFO-tuned in the first place, so this bug didn't touch them. The honest
remaining explanation for most of the losses is what was already
flagged when this was first reported: several active pairs trade
infrequently enough (3-7 trades per multi-week backtest window) that an
11-day live losing stretch across multiple such pairs is within normal
variance for strategies with a genuine ~35-45% expected win rate — not
provably "the edge is gone," but not provably fine either. Flagging per
[[feedback_dont_overclaim_fixes]]: the fix here removes one confirmed
source of bad live performance (GBP_CAD's noise-fit params); it does not
guarantee the next 10 trades go better, and there isn't yet a live
sample large enough to say whether EUR_JPY/CHF_JPY belong on the active
roster at all — that needs more real trades or a larger backtest sample,
not more tuning of the same recent window.

---

# Change: Z-score mean reversion strategy — wire up + backtest (2026-08-06) — REJECTED, code deleted

## Context
Session froze mid-work. Recovered state: `engine/strategy_zscore_meanrev.py`
already written (untracked, complete — check_buy_signal/check_sell_signal/
get_stop_loss/get_last_diag/clear_cache, same shape as the other strategy
modules) and `config.py` already has the 4 new knobs
(ZSCORE_PERIOD/ZSCORE_THRESHOLD/ZSCORE_H4_ADX_THRESHOLD/ZSCORE_ATR_SL_MULT,
modified but uncommitted). Neither change is wired into anything yet — the
module isn't importable from strategy_dispatch.py or the backtest CLI, so it
can't be tested or run live. Live bot (main.py) itself is running fine and
was never frozen — confirmed via logs/main.log heartbeats continuing to the
current minute.

Strategy idea (from module docstring): volatility-normalized Z-score entry
instead of EMA's cci_threshold, so ONE shared parameter set works across the
whole pair roster with no per-pair WFO tuning. Docstring is explicit this is
"a hypothesis to backtest honestly, not an answer to trust on citation
alone" — so the plan below stops at backtesting, no live config changes
(no `config.STRATEGY_OVERRIDE` entry for any pair) unless results hold up,
matching how trend_follow ([[project_trend_follow_experiment]]) and ATR
trailing/breakeven ([[project_atr_trailing_and_live_gap]]) were evaluated.

## Todo
- [x] Register `zscore_meanrev` in `engine/strategy_dispatch.py` STRATEGY_FNS
- [x] Add `zscore_meanrev` as a `--strategy` choice in `backtest/runner.py`'s CLI
- [x] Isolated all work on branch `zscore-meanrev-experiment` — main untouched,
      nothing committed yet (safety net per user request 2026-08-06: benchmark
      before committing anything)
- [x] Ran baseline backtest (each pair's CURRENT live strategy per
      `STRATEGY_OVERRIDE`) — 2000 bars, same window used for the challenger run
- [x] Ran zscore_meanrev backtest, same 5 pairs, same bar count
- [x] Reported comparison back to user — see Review below
- [ ] **BLOCKED on user decision** (see Review) before any further code
      changes, config.STRATEGY_OVERRIDE change, or commit

## Review

### Baseline (current live strategy per pair, 2000 bars each)
| Pair | Strategy | Trades | WR | PF | PnL | MaxDD |
|---|---|---|---|---|---|---|
| NZD_USD | ema_bounce | 11 | 36% | 1.60 | +$15.87 | 5.5% |
| GBP_CAD | ema_bounce | 14 | 36% | 1.24 | +$9.69 | 4.3% |
| GBP_USD | breakout_retest | 13 | 46% | 1.89 | +$27.42 | 2.8% |
| CHF_JPY | ema_bounce | 13 | 46% | 1.63 | +$22.80 | 2.8% |
| EUR_JPY | ema_bounce | 15 | 27% | 0.82 | -$8.42 | 6.6% |

### Challenger (zscore_meanrev, identical pairs/window)
`python -m backtest.runner --strategy zscore_meanrev` returned **0 trades on
all 5 pairs** — not because the Z-score condition never fires (it does, 62–121
candidate bars per pair had |Z|≥2 with H4 not opposing), but because the raw
score these 4 conditions produce tops out at 25 (`round(25 * passed/4)`, same
formula shape as ema_bounce's `round(25 * passed/7)`), and even added to
structure+price-action score, the combined `final_score` never reached
`config.MIN_CONFLUENCE_SCORE = 55` for a single candidate in this window:

| Pair | Candidate bars | Max final_score seen | Needed |
|---|---|---|---|
| NZD_USD | 62 | 49 | 55 |
| GBP_CAD | 117 | **54** | 55 |
| GBP_USD | 121 | **54** | 55 |
| CHF_JPY | 95 | 49 | 55 |
| EUR_JPY | 91 | 42 | 55 |

Not a code bug — confirmed by direct instrumentation, not just the CLI's
"signals=0" line. GBP_CAD and GBP_USD came within 1 point of the gate, so this
reads as a scoring-ceiling/calibration problem rather than "no candidates
exist." Given to the user 2026-08-06 for a decision on how (or whether) to
proceed. User chose: re-test at a longer window (4500 bars) before touching
the strategy's logic, rather than any of the 3 code-change options offered.

### Re-run at 4500 bars (user's choice, same 5 pairs)

Baseline:
| Pair | Strategy | Trades | WR | PF | PnL | MaxDD |
|---|---|---|---|---|---|---|
| NZD_USD | ema_bounce | 29 | 34% | 1.25 | +$21.71 | 5.5% |
| GBP_CAD | ema_bounce | 34 | 59% | 3.34 | +$162.42 | 3.5% |
| GBP_USD | breakout_retest | 22 | 50% | 2.37 | +$67.08 | 3.1% |
| CHF_JPY | ema_bounce | 27 | 41% | 1.57 | +$41.42 | 3.5% |
| EUR_JPY | ema_bounce | 38 | 37% | 1.25 | +$28.12 | 6.6% |

Challenger (zscore_meanrev):
| Pair | Trades | WR | PF | PnL | Candidates | Max final_score |
|---|---|---|---|---|---|---|
| NZD_USD | 0 | — | — | $0.00 | 184 | 54 (need 55) |
| GBP_CAD | 0 | — | — | $0.00 | 267 | 54 (need 55) |
| GBP_USD | 1 | 0% | 0.00 | -$5.00 | 331 | 57 (cleared once) |
| CHF_JPY | 0 | — | — | $0.00 | 243 | 54 (need 55) |
| EUR_JPY | 0 | — | — | $0.00 | 224 | 54 (need 55) |

Longer window did NOT change the conclusion — more candidate bars (184–331 vs
62–121 at 2000 bars), but the scoring ceiling is the same 25-raw-point cap
regardless of sample size, so the result was always going to look like this
until the formula changes. GBP_USD's single trade that did clear the gate
lost.

### Final decision (2026-08-06): discarded, not just shelved
User called it: worse than the live baseline on every pair, with no verified
research behind the docstring's SSRN citation (that citation predates this
session — could not be confirmed as real research vs. an unverified claim
carried over from a prior, unrecoverable session). Rather than leave inert
code around (the gold_trend/trend_follow treatment), user asked to remove it
entirely: `engine/strategy_zscore_meanrev.py` deleted, the 4 `ZSCORE_*` knobs
removed from `config.py`, `engine/strategy_dispatch.py` and
`backtest/runner.py` reverted to their pre-2026-08-06 state. Branch
`zscore-meanrev-experiment` deleted. Nothing from this experiment is in the
codebase anymore — this section is kept only as a record of what was tried
and why it didn't hold up.

Pivot: user redirected investigation to the Walk-Forward dashboard results
for NZD_USD and USD_CAD (both showing wildly unstable per-window OOS PF —
some windows 0.00, one window 27–30 PF off 2-3 trades) as evidence the
*existing* ema_bounce strategy's backtest/WFO numbers may not reflect real
live performance either. See next section.

---

# Investigation: WFO headline metrics vs real live performance (2026-08-06)

## Finding 1 — `avg_pf_oos`/`avg_stability` is an unweighted mean, dominated by
## the smallest-trade-count windows (confirmed in code, not just the screenshots)

`backtest/runner.py:563-564` (`run_walk_forward`):
```python
avg_pf_oos  = round(sum(w["pf_oos"]    for w in windows_out) / len(windows_out), 2)
avg_stab    = round(sum(w["stability"]  for w in windows_out) / len(windows_out), 3)
```
Plain arithmetic mean across windows, no weighting by `trade_count_oos`. A
window with 2-3 trades and one lucky winner produces PF 27-30 and gets equal
weight to a window with a stable PF near 1. That's exactly what's in the two
screenshots the user shared:
- **NZD_USD** (Train 1500/Test 750/Step 500, 4500 bars): windows had 3, 3, 2,
  5, 9 trades; OOS PF was 27.31, 4.52, 1.43, **0.00**, 0.28. Dashboard
  headline: "Avg OOS PF 6.71" — driven almost entirely by the 3-trade 27.31
  window.
- **USD_CAD**: windows had 2, 1, 2, 3, 5 trades; OOS PF was 1.09, **0.00**,
  0.69, 30.57, **0.00**. Headline: "Avg OOS PF 6.47" — 2 of 5 windows are
  100% losers, headline hides it.

This is a real bug — the headline metric is not a trustworthy summary
statistic with trade counts this small, and nothing tells the user that.

## Finding 2 — real live performance, not extrapolated: checked `data/signal_log.csv`

Filtered to `source == "live"` and `outcome in ("win","loss")` (excludes the
`would_win`/`would_lose` shadow-diagnostic rows — see
[[bugs_shadow_outcome_duplication]] — and `expired`/`skipped`). This is every
*actual* closed live trade in the log:

| Date | Pair | Dir | Score | Outcome | pips | $ |
|---|---|---|---|---|---|---|
| 07-08 | EUR_AUD | long | 56 | loss | -25.0 | -5.34 |
| 07-10 | USD_CAD | long | 66 | loss | -25.0 | -5.37 |
| 07-15 | GBP_CAD | long | 56 | **win** | 52.6 | +9.00 |
| 07-21 | NZD_USD | long | 56 | loss | -25.0 | -5.42 |
| 07-27 | NZD_USD | long | 56 | loss | -25.0 | -5.70 |
| 07-28 | EUR_AUD | short | 56 | loss | -25.0 | -1.14 |
| 07-29 | GBP_USD | short | 60 | **win** | 31.6 | +7.75 |
| 07-29 | NZD_USD | short | 56 | loss | -25.0 | -5.67 |
| 07-30 | CHF_JPY | long | 66 | loss | -25.0 | -5.75 |

**9 trades over 3 weeks (2026-07-08 → 2026-07-30). 2 wins, 7 losses = 22% WR,
net -$17.64.** That's the user's "1 win per 4 losses" complaint, confirmed
with real numbers, not a vibe. 7 of 7 losses hit *exactly* -25.0 pips — the
stop, not a partial/managed exit — meaning price essentially never came back
in favor after entry on any of these. And every single trade's score sits at
56, 60, or 66 — right at or barely over `MIN_CONFLUENCE_SCORE = 55` — none
of the 9 real trades had a strong (70+) score.

This is a small sample (9 trades) so it isn't proof the strategy is broken,
but it's a large enough gap from what the same-pair backtests show (this
session's own baseline backtest found NZD_USD 34-36% WR, GBP_CAD 36-59% WR,
GBP_USD 46-50% WR over thousands of bars) that it's worth treating as a real
signal, not noise, especially paired with Finding 1 showing the WFO tooling
itself has a metric that overstates confidence.

## Open questions to run down next (not started — checking in before proceeding)
- [x] Why does every real live trade score in the 56-66 band — answered, see Finding 3
- [x] Why 7/7 losses hit the stop exactly, with none managed/partial — answered, see Finding 4
- [ ] Fix `avg_pf_oos`/`avg_stability` to weight by trade count (or at least
      surface per-window trade counts more prominently in the dashboard so
      a 3-trade PF-27 window can't visually dominate a 15-trade PF-1 window)
- [ ] Pull a longer live-trade sample if/when more accumulates — 9 trades
      is thin for a hard verdict either way

## Finding 4 — the "dynamic" EMA-bounce stop-loss is, in practice, a flat
## 25-pip floor almost every time, because EMA-touch entries start close to
## the EMA by construction

`engine/strategy_ema_cci_macd.py:379-410` (`get_stop_loss`): primary rule is
"SL = mid_ema ± 1 pip", with `config.MIN_SL_PIPS = 25` (`min_sl_pips_for`)
as a floor via `sl = min(sl, entry - min_dist)` (long) — i.e. whichever is
*wider* wins, floor or EMA-distance.

Checked entry/SL against ATR for every real trade:

| Pair | Dir | ATR(pips) | SL distance | Outcome |
|---|---|---|---|---|
| EUR_AUD | long | 14.91 | 25.0 (floor) | loss |
| USD_CAD | long | 6.19 | 25.0 (floor) | loss |
| GBP_CAD | long | 12.11 | **30.6 (EMA, wider than floor)** | **win** |
| NZD_USD | long | 5.74 | 25.0 (floor) | loss |
| NZD_USD | long | 5.86 | 25.0 (floor) | loss |
| EUR_AUD | short | 8.85 | 25.0 (floor) | loss |
| NZD_USD | short | 5.39 | 25.0 (floor) | loss |
| CHF_JPY | long | 15.62 | 25.0 (floor) | loss |

(GBP_USD's win excluded — that pair runs `breakout_retest`, a different
`get_stop_loss`, not this one.)

**6 of 7 `ema_bounce` trades hit the exact 25-pip floor regardless of ATR
ranging 5.4–15.6 pips** — because an EMA-touch/bounce entry is, by the
strategy's own definition, close to the EMA at entry time, so "SL just
beyond the EMA" is almost always tighter than 25 pips and gets overridden
by the floor. The floor isn't a bug — `MIN_SL_PIPS`'s comment says "25
validated by backtest (20 caught H1 noise)" — but that claim predates this
session and hasn't been re-verified here; flagging per
[[feedback_dont_overclaim_fixes]] rather than re-asserting it as settled.
The practical effect either way: for this strategy shape, "dynamic,
ATR/EMA-scaled stop" is close to fiction — it's a flat 25-pip stop dressed
up as adaptive, and every real loss is a full, unmanaged stop-out because
price never travels toward TP1 (larger than 25 pips away) before reversing.

Notably, the *one* `ema_bounce` trade whose EMA-based distance (30.6 pips)
beat the floor was also the *only* `ema_bounce` win in the sample. n=7,
too small to call this proven, but it's a concrete, testable hypothesis:
floor-clamped trades may be structurally worse bets than trades where the
EMA genuinely sits further from price. Worth a real backtest (e.g. compare
current MIN_SL_PIPS=25 against a lower/removed floor, or filter for
EMA-distance-clamped vs not) before touching anything live.

## Finding 3 — answered: no hidden override; 56-66 IS close to the real ceiling,
## and this reopens/refreshes [[bugs_shadow_outcome_duplication]]'s stale finding

Checked `adaptive_params`/`wfo_optimizer` — no per-pair override lowers the
gate below the global `MIN_CONFLUENCE_SCORE = 55`; all 9 real trades scoring
55+ is the real, uniformly-applied threshold, not a bug.

Checked the full score distribution across all 159 live rows (not just the 9
real trades): mean 45.9, max observed **66** — against a theoretical ceiling
of 75 (`ema_score` maxes at 25, `structure_score` at 20, `pa_score` at 30).
Only ~18% of raw signals ever reach 55 at all. So 56-66 isn't suppressed —
it's genuinely close to what the top of the distribution looks like in this
market data. `MIN_CONFLUENCE_SCORE = 55` sits so close to the practical
ceiling that almost nothing that clears it does so by a wide margin.

That reopens [[bugs_shadow_outcome_duplication]]'s 2026-07-17 finding ("no
visible positive relationship between confluence score and win rate"),
which that memory explicitly flagged as due for a refresh once enough
post-dedup-fix data existed — it now has (159 live rows now vs. the ~161
mixed live+seed rows the original number came from, before the 2026-07-24
seed-data-exclusion fix cleaned the pool further). Re-ran it:

Real+shadow combined win rate by score band (fresh data):
| Band | WR | n |
|---|---|---|
| <40 | 51.4% | 37 |
| 40-54 | 40.0% | 80 |
| 55-64 | 56.5% | 23 |
| 65+ | 0.0% | 2 |

**But the REAL trades only** (the ones that actually executed, in the only
bands real trades ever land in):
| Band | WR | n |
|---|---|---|
| 55-64 | 28.6% | 7 |
| 65+ | 0.0% | 2 |

The shadow-outcome estimate for the 55-64 band (56.5%) and what actually
happened when those trades were really taken (28.6%) disagree by a lot.
Shadow outcomes are resolved with simplified TP1-vs-SL logic, not full live
trade management (partial exits, trailing, breakeven) — see
[[project_atr_trailing_and_live_gap]] for what's actually wired live. That
gap between "predicted by the shadow model" and "what really happened" is
itself a lead, but n=7 real trades is too thin to call this settled — stated
per [[feedback_dont_overclaim_fixes]].

---

# Change: XAU_USD (gold) support + trend-pullback strategy (2026-08-01) — SHELVED, see review below

## Why
User wants to add XAU_USD to the bot. Before touching config/pairs, established
(via discussion) that gold needs its own strategy rather than reusing
EMA-bounce/breakout-retest as-is — those are FX-session mean-reversion/structure
recipes, gold's regime (risk-sentiment/real-yields driven, trends harder than
any FX pair on the roster) doesn't obviously match either.

User supplied a "white paper" transcript (from a YouTube video) describing an
XAU/USD momentum system. Stripped of jargon, it's: 200 EMA regime filter + 50
EMA pullback retest + RSI(14) 40-60 zone momentum-hook confirmation, with a
fixed 15-pip stop / 30-pip target (2:1) and breakeven at 1:1, on M15.

Assessed against the existing codebase:
- This entry shape (EMA touch + oscillator confirmation) is structurally the
  same recipe as `engine/strategy_trend_follow.py` (CCI/MACD instead of RSI,
  same-regime-inverted ADX gate) — shelved 2026-07-02 for FX because forcing
  a mean-reversion recipe into "trending" FX regimes didn't help
  ([[project_trend_follow_experiment]]). Gold is exactly the persistently-
  trending case that idea was never tested against, so it's a stronger
  candidate here than starting from scratch.
- The document's fixed 15/30 pip stop doesn't fit how any strategy in this
  bot sizes risk — every existing strategy uses dynamic sizing (EMA distance,
  1.5xATR fallback, see `strategy_ema_cci_macd.get_stop_loss` at
  `engine/strategy_ema_cci_macd.py:369`), never a flat pip count. Gold's
  per-bar volatility is much higher than FX: a flat $1.50 stop is likely to
  get chopped by noise. Plan to keep the doc's *entry* logic but let stops
  inherit the bot's existing dynamic sizing instead of hardcoding 15/30.
- Blocking bug found along the way: `_get_pip()` is duplicated in
  `engine/strategy_ema_cci_macd.py:27` and
  `engine/strategy_breakout_retest.py:53`, both hardcode
  `0.01 if "JPY" else 0.0001` — XAU_USD would silently get the wrong pip
  size (gold quotes ~2 decimals, not 4) and every SL/TP distance computed
  from it would be wrong by orders of magnitude. Must fix before any gold
  strategy can size stops correctly.

A second source (a different YouTube video's transcript, "Powerful XAUUSD
Gold Trading Strategy") was reviewed and rejected as not usable: it
references a named third-party paid indicator ("the 10X trading system")
with no disclosed formula/inputs, has no stop-loss or take-profit defined
at all, relies on discretionary concepts ("hidden demand level", "bottom
of a consolidation") that aren't quantifiable rules, and its "81% win rate
over 319 trades" claim has no stated methodology, date range, or drawdown
— unverifiable, so it shouldn't move confidence either way. Not
incorporated into the plan below; noted here so this evaluation doesn't
need to be redone.

## Plan
- [x] Fix `_get_pip()` (both copies) to add a real XAU_USD branch instead of
      the JPY-or-else binary; confirm the actual pip/precision OANDA reports
      for XAU_USD via `connectors/oanda_connector.py` rather than guessing.
      DONE 2026-08-01: fixed `strategy_ema_cci_macd.py:27` and
      `strategy_breakout_retest.py:53` (both now `0.01` for XAU_USD, same as
      JPY). Also found and fixed a third copy of the same bug while in
      there: `oanda_connector.py:239`'s `_fmt()` (order-submission price
      formatting) had the identical JPY-or-else binary — would have sent
      XAU_USD prices with 5 decimals and gotten rejected by OANDA with
      `PRICE_PRECISION_EXCEEDED`. Now 2 decimals for XAU_USD (OANDA's
      documented `displayPrecision`), 3 for JPY, 5 otherwise. Full 257-test
      suite still passes. Pip value used (0.01, pipLocation -2) is OANDA's
      documented XAU_USD convention, not independently re-verified against
      a live instruments API call — flag if a real order ever gets
      precision-rejected.
      **Note for step 2**: `config.MIN_SL_PIPS = 25` (global,
      `config.py:208`) would give gold a minimum stop distance of just
      $0.25 (25 x 0.01) — almost certainly far too tight for XAU_USD's
      actual volatility. Don't reuse the global value for gold; needs its
      own minimum, informed by the backtest, not guessed now.
- [ ] Add `engine/strategy_gold_trend.py`: 200 EMA regime filter, 50 EMA
      pullback-touch trigger (reuse `_find_touch`-style logic already in
      strategy_ema_cci_macd.py rather than reimplementing), RSI(14)
      confirmation harmonized from two independent sources: dip to <=40
      (buys) / >=60 (sells), then a clean cross back through the 50
      midline in the trend direction (more precise than doc 1's "40-60
      zone... breaks back outward", same underlying idea as doc 3's
      "drop to 40, hook up above 50"). Stop-loss: backtest BOTH the bot's
      existing dynamic/ATR pattern AND doc 3's alternative (behind the
      previous week's swing high/low) — don't assume either wins without
      a number. Take-profit via `risk_manager.get_tp_levels` (respects
      per-pair R:R override pattern already established via
      `config.TP_RR_PER_PAIR`), not the documents' fixed pip numbers.
      Timeframe: build primary/confirm TF as parameters (same pattern as
      `config.TIMEFRAMES`/`CONFIRM_TF_PER_PAIR`), not hardcoded — user's
      call (2026-08-01, asked directly): backtest both the bot's existing
      M30/H4 cadence AND Daily, decide off actual numbers rather than
      picking a timeframe from either source or guessing.
      Third source reviewed 2026-08-01 ("beginner-friendly gold trading
      strategy", Daily-chart framing): confirms the EMA-trend +
      RSI-pullback-hook shape independently (single 50 EMA vs doc 1's
      200/50 pair), added swing-high/low stop-loss idea above, and
      confirmed `risk_manager.calculate_position_size` already handles
      1-2% risk sizing correctly for any pip size — its `pip` variable
      cancels out algebraically (`sl_pips x pip = sl_distance` always),
      so no fix was needed there despite also containing a JPY-or-else
      pip binary at `risk_manager.py:15`.
- [x] Wire into `engine/signal_engine.py` via `STRATEGY_OVERRIDE` the same
      way `breakout_retest` is wired for GBP_USD — new pair, new strategy,
      no changes to any existing pair's logic.
      DONE 2026-08-01: `engine/strategy_gold_trend.py` created (6-condition
      buy/sell check, 200/50 EMA + RSI dip-and-hook, reuses `_find_touch`
      from strategy_ema_cci_macd.py per the plan). `config.STRATEGY_OVERRIDE["XAU_USD"]
      = "gold_trend"` added (inert — XAU_USD isn't in FOREX_PAIRS/FOREX_WATCH
      yet). Wired into `engine/strategy_dispatch.py`'s `STRATEGY_FNS` and
      `backtest/runner.py`'s cache-clear list + CLI `--strategy` choices.
      Also added `--primary-tf`/`--confirm-tf` CLI override flags to
      `backtest/runner.py` (previously hardcoded to `config.TIMEFRAMES`,
      no way to run a Daily backtest at all) — needed for the "backtest
      both M30/H4 and Daily" decision. 257/257 tests still pass.

      **Two real infra findings while wiring this up, both fixed:**
      1. `engine/signal_engine.py` had a 4th (previously unfound) copy of
         the JPY-or-else pip binary at line ~181, used to gate the live
         ATR volatility filter (`ATR_MIN_PIPS`/`ATR_MAX_PIPS`) and the
         `MIN_SL_PIPS` fallback. Fixed to recognize XAU_USD. Also added
         `config.ATR_MIN_PIPS_PER_PAIR`/`ATR_MAX_PIPS_PER_PAIR` (mirroring
         the CONFIRM_TF_PER_PAIR idiom) — the global 5-35 pip bound would
         have permanently tripped ATR_TOO_HIGH for gold (real fetched
         XAU_USD H1 ATR sample: $11.40-$23.22, i.e. ~1140-2320 pips at
         gold's 0.01 pip size). XAU_USD placeholder: 200-4000 pips,
         grounded in that one real sample, NOT backtest-validated — retune
         once real trade data exists. `backtest/runner.py`'s own duplicate
         pip binary (line 137, cosmetic ATR-pips display only, not a gate)
         fixed too.
      2. `risk.calculate_position_size()` returns `int(units)` — gold's
         realistic stop distances (~$10-30, vs. FX's ~0.001-0.005) combined
         with a small test balance make `units` a fraction of a troy ounce,
         which truncates to **zero**, silently blocking every trade
         regardless of signal quality. Confirmed directly: a $500-balance
         backtest produced 27 real qualifying signals (score >= confluence
         threshold) but 0 trades — every one hit `size <= 0`. Re-ran at a
         $5000 notional balance (evaluation-only workaround, NOT a live
         sizing decision) and got real trades through — see results below.
         **Not fixed** — the real fix is a business decision (how much
         capital a gold trade should risk, and whether OANDA allows
         fractional units for XAU_USD) not a code bug; needs your input
         before touching `calculate_position_size`.

      **Also found, unrelated to code**: this OANDA practice account has
      no metals/XAU_USD in its 68-instrument tradeable list at all —
      `AccountInstruments` returns `INSTRUMENT_NOT_TRADEABLE` for XAU_USD.
      Historical candle data (`get_candles`) works fine (separate,
      unrestricted endpoint) so backtesting is unaffected, but real
      order placement (demo/live) will fail until metals/CFD trading is
      enabled on the account itself — likely an OANDA account-settings
      change, possibly a different account type. This blocks step "wire
      into live" regardless of backtest results; flag to OANDA/account
      settings before ever flipping XAU_USD out of paper mode.

- [ ] Add `XAU_USD` to config in **watch-only** form first (signals shown,
      no trades) — do not add to `FOREX_PAIRS`/active trading yet.
- [x] Backtest `strategy_gold_trend` against real OANDA XAU_USD history —
      FULL 4-VARIANT SWEEP DONE 2026-08-01 (dynamic/swing stop x M30-H4/
      Daily-Weekly), all at $5000 notional to sidestep the position-sizing
      truncation issue above (evaluation only, not a live sizing decision):

      | Variant          | Trades | WR    | PF   | PnL      | MaxDD |
      |------------------|--------|-------|------|----------|-------|
      | dynamic, M30/H4  | 16     | 18.8% | 0.63 | -$171.02 | 8.3%  |
      | swing,   M30/H4  | 2      | 0%    | 0.00 | -$41.31  | 1.8%  |
      | dynamic, D/W     | 2      | 0%    | 0.00 | -$45.17  | 1.2%  |
      | swing,   D/W     | 8      | 12.5% | 0.48 | -$113.41 | 3.2%  |

      **Every variant is net-losing.** Samples are all well under the
      ~30-trade trust threshold ([[project_pair_config]]), so this isn't
      a statistically airtight rejection — but 4/4 losing with no variant
      even close to breakeven is a real, consistent signal, not noise
      that a bigger sample would plausibly flip. Conclusion: the entry
      mechanic as directly reconciled from the two source documents (200/
      50 EMA regime+pullback, RSI 40/60 dip-and-hook, fixed periods) does
      NOT show an edge on XAU_USD with these default parameters. Whether
      different parameters (ADX threshold, RSI extremes, EMA periods) or
      a completely different mechanic (EMA-bounce/breakout-retest applied
      to gold, per the original plan's "nearly free" comparison) would do
      better is untested — did not sweep parameters or run the comparison
      to avoid unilaterally p-hacking a small sample without a checkpoint;
      awaiting user direction on whether that's worth pursuing.

      **Extended sweep 2026-08-01 (user asked for more timeframes + param
      tuning after the 4-variant table above came back all-negative):**

      Two more timeframe combos added (dynamic + swing stop each):
      | Variant         | Trades | WR    | PF   | PnL      |
      |-----------------|--------|-------|------|----------|
      | dynamic, H1/H4  | 7      | 28.6% | 1.12 | +$21.72  |
      | swing,   H1/H4  | 0      | —     | —    | $0       |
      | dynamic, H4/D   | 0      | —     | —    | $0       |
      | swing,   H4/D   | 0      | —     | —    | $0       |

      H1/H4 dynamic was the first positive number in the whole sweep
      (small sample, 7 trades) — used as a second base for one-factor-at-
      a-time parameter tuning (ADX threshold, RSI extremes, EMA periods),
      alongside M30/H4 dynamic (the highest-N base, 16 trades):

      | Base    | Variation      | Trades | WR    | PF   | PnL      |
      |---------|----------------|--------|-------|------|----------|
      | M30/H4  | adx=20         | 25     | 32.0% | 1.13 | +$78.76  |
      | M30/H4  | adx=24         | 23     | 26.1% | 0.79 | -$122.94 |
      | M30/H4  | rsi=35/65      | 14     | 21.4% | 0.77 | -$88.07  |
      | M30/H4  | rsi=45/55      | 16     | 25.0% | 0.89 | -$49.54  |
      | M30/H4  | ema=100/20     | 20     | 25.0% | 0.86 | -$72.87  |
      | M30/H4  | ema=150/34     | 14     | 14.3% | 0.50 | -$200.65 |
      | H1/H4   | **adx=20**     | **32** | **37.5%** | **1.80** | **+$529.48** |
      | H1/H4   | adx=24         | 13     | 23.1% | 0.89 | -$32.39  |
      | H1/H4   | rsi=35/65      | 7      | 28.6% | 1.12 | +$21.72  |
      | H1/H4   | rsi=45/55      | 7      | 28.6% | 1.12 | +$21.72  |
      | H1/H4   | ema=100/20     | 21     | 19.1% | 0.59 | -$224.72 |
      | H1/H4   | ema=150/34     | 14     | 21.4% | 0.63 | -$150.42 |

      **One standout: H1 primary / H4 confirm, ADX threshold loosened to
      20 (default 28), dynamic stop — 32 trades, 37.5% WR, PF 1.80,
      +$529.48, 4.6% MaxDD.** First result in the entire sweep (18 backtest
      variants total across both tables) that clears the ~30-trade sample
      bar AND looks genuinely good, not just barely positive.

      **Important caveat, stated plainly rather than oversold**: this
      emerged from a one-factor-at-a-time sweep of ~18 variations where
      every other combination was flat or losing. Finding one clear winner
      out of that many tries is exactly the situation where a real signal
      and a lucky multiple-comparisons artifact look identical — this
      result has NOT been validated out-of-sample (different date range)
      or checked in combination with the other parameter changes (only
      ADX was varied here; RSI/EMA stayed at defaults). Don't promote this
      to paper/live off this number alone — same "don't overclaim" standard
      as everything else in this project ([[feedback_dont_overclaim_fixes]]).
      rsi=35/65 and rsi=45/55 producing byte-identical results to the H1/H4
      baseline (both 7 trades) is not a bug — confirmed the adaptive dict
      does reach check_buy/sell_signal correctly (M30/H4's RSI variations
      DID change trade count); it means none of this window's actual
      touch-point RSI values fell in the 35-45/55-65 boundary zone being
      tested, so the same touches qualified either way at this sample size.

      **Out-of-sample validation 2026-08-01 — the standout did NOT hold
      up.** Split one 4999-bar H1 fetch (+ matching 1249-bar H4, same
      calendar span) at its calendar midpoint into two non-overlapping
      ~5-month windows (2025-09-25 to 2026-02-27, and 2026-02-27 to
      2026-07-31) and re-ran the exact H1/H4, ADX=20, dynamic-stop config
      on each independently:

      | Window                          | Trades | WR    | PF   | PnL      |
      |----------------------------------|--------|-------|------|----------|
      | Newer half (overlaps original test) | 24  | 37.5% | 2.07 | +$459.55 |
      | Older half (genuinely out-of-sample) | 15 | 20.0% | 0.66 | -$137.55 |

      Same config, same instrument, non-overlapping period, opposite sign.
      This is the textbook signature of overfitting a small multiple-
      comparisons search rather than a real edge — confirms the caveat
      raised when this result first appeared. **Verdict: the 200/50 EMA +
      RSI dip-and-hook mechanic (as reconciled from the two source
      documents) does not show a robust, generalizing edge on XAU_USD.**
      Every clean baseline (4 variants) lost money; the one parameter
      combination that looked good in-sample failed the very next check.
      Not recommending further tuning of this specific mechanic — see
      tasks/todo.md's open question on whether to try EMA-bounce/
      breakout-retest against gold instead (the original pre-YouTube-
      sources plan) or shelve gold for now.

      **Comparison against the bot's two proven FX strategies, 2026-08-01**
      (the original pre-YouTube-sources plan — "nearly free" once the pip
      fix was in): also loses, at both timeframes tried, no exceptions:

      | Strategy         | TF     | Trades | WR    | PF   | PnL      |
      |------------------|--------|--------|-------|------|----------|
      | EMA-bounce       | M30/H4 | 48     | 25.0% | 0.84 | -$206.45 |
      | EMA-bounce       | H1/H4  | 53     | 18.9% | 0.54 | -$637.63 |
      | breakout-retest  | M30/H4 | 15     | 20.0% | 0.65 | -$164.69 |
      | breakout-retest  | H1/H4  | 12     | 16.7% | 0.47 | -$223.90 |

      EMA-bounce at M30/H4 has the largest sample of anything tested today
      (48 trades, clears the ~30-trade threshold) and is the closest to
      breakeven (PF 0.84) of any config tried all day — still a real loser,
      not close enough to call promising, and given the H1/H4 ADX=20
      lesson just above, no further parameter tuning attempted on these
      without a specific reason to expect it'd generalize any better.

      **Cumulative verdict after today's full search** (custom gold_trend
      mechanic: 4 baselines + ~18 parameter variations + 1 out-of-sample
      check; EMA-bounce/breakout-retest: 2 timeframes each) — every single
      configuration lost money except one, and that one failed out-of-
      sample validation. No strategy/timeframe/parameter combination tried
      today shows a real, validated edge on XAU_USD. This is a broad
      enough search that "haven't found the right knob yet" is a weaker
      explanation than "this instrument doesn't suit any of these three
      entry mechanics at all, at least not over this ~5-11 month data
      window." Not continuing to sweep further without new direction —
      see conversation for what's next.

      **Two more real bugs found and fixed while running this sweep**
      (both would have affected any future Daily-bar or parameter-sweep
      backtest, not just gold):
      1. `backtest/runner.py`'s `run_backtest()` calls
         `stop_loss_fn(pair, slice_h1, direction)` WITHOUT forwarding the
         `adaptive` dict passed into `run_backtest()` itself — so the
         `gold_stop_method` override silently never reached
         `get_stop_loss()`, and the first "swing" test above actually
         re-ran "dynamic" (identical results were the tell). Not changed
         (would affect every strategy's stop-loss adaptive params, wider
         blast radius than today's scope) — worked around by passing a
         closure-wrapped `stop_loss_fn` instead for this sweep. Flagging
         for a future fix rather than doing it unprompted.
      2. `backtest/runner.py` has its OWN second, independent session gate
         (`# Session filter: skip trades outside London/NY overlap`,
         applied after `final_score >= min_score`) — separate from the
         one inside each strategy module. Fixing the gate inside
         `strategy_gold_trend.py` alone still left every Daily/Weekly
         backtest at zero signals because of this second copy. Fixed with
         the same "skip for bars >=20h apart" rule.
- [ ] Only promote to active (real paper trades) if the backtest clears the
      same bar as every other pair: ~30+ trade sample, positive PnL, PF
      that holds up — not just a good-looking small sample.

## Explicitly not doing (yet)
- Not adding XAU_USD to live/real-money trading — paper/watch only until a
  backtest sample exists, consistent with how every other pair here was
  vetted.
- Not adopting the document's fixed 15/30 pip risk numbers — using the bot's
  existing dynamic stop-sizing instead.
- Not touching `FOREX_PAIRS`, `STRATEGY_OVERRIDE` for any existing pair, or
  any other strategy module.

## Review — shelved 2026-08-01

**Decision: gold is shelved.** Removed `"XAU_USD": "gold_trend"` from
`config.STRATEGY_OVERRIDE` — XAU_USD was never in `FOREX_PAIRS`/
`FOREX_WATCH` (never got past backtesting per the plan above), so this was
the only actual "list" it was ever added to. 257/257 tests pass after
removal.

**Why**: exhaustive same-day search found no validated edge. Summary of
everything tried (full detail in the entries above):
- Custom `strategy_gold_trend.py` (200/50 EMA + RSI dip-and-hook, reconciled
  from two YouTube-sourced documents): 4 clean baselines (dynamic/swing
  stop x M30-H4/Daily-Weekly) all lost money. ~18-variant one-factor-at-a-
  time parameter sweep (ADX threshold, RSI extremes, EMA periods) found
  exactly one apparent winner (H1/H4, ADX=20: PF 1.80, +$529). Out-of-
  sample validation on a non-overlapping calendar period flipped it to
  PF 0.66, -$137 — confirmed overfitting, not edge.
- The bot's two existing, live-proven FX strategies (EMA-bounce,
  breakout-retest) also both lost money against XAU_USD at both M30/H4
  and H1/H4.
- Net: every configuration tested lost money except one, and that one
  failed validation the moment it was checked out-of-sample. Broad enough
  a search that "wrong parameters" is a weaker explanation than "none of
  these three entry mechanics suit this instrument" (at least over the
  ~5-11 month windows tested).

**What stays in the repo** (left in place, deliberately not deleted, all
inert with `XAU_USD` out of `STRATEGY_OVERRIDE`):
- `engine/strategy_gold_trend.py` — the module itself, in case gold is
  revisited with a genuinely different mechanic later.
- Its entry in `engine/strategy_dispatch.py`'s `STRATEGY_FNS` and
  `backtest/runner.py`'s CLI `--strategy gold_trend` choice / cache-clear
  list — unreachable without a `STRATEGY_OVERRIDE` entry or explicit
  `--strategy gold_trend` flag.
- The `--primary-tf`/`--confirm-tf` CLI override flags added to
  `backtest/runner.py` — general infra, useful for any future non-default-
  timeframe backtest, not gold-specific.
- All the pip-size fixes (`_get_pip` in both strategy files, `_fmt` in
  `oanda_connector.py`, the `_pip_size` copy in `signal_engine.py`, the
  `ATR_MIN/MAX_PIPS_PER_PAIR` and `MIN_SL_PIPS_PER_PAIR` config overrides)
  — these are real, general correctness fixes (the ATR-gate and order-
  formatting bugs would have bitten any future 2-decimal-precision
  instrument, not just gold) and are harmless with gold inactive; not
  reverting.
- **Not fixed, flagged for later if ever relevant**: `calculate_position_size()`'s
  `int(units)` truncation (blocks any instrument with FX-small stop
  distances relative to account balance) and `run_backtest()` not
  forwarding `adaptive` to `stop_loss_fn`.
- **Not this bot's problem to fix, but worth remembering**: this OANDA
  practice account has no metals instruments enabled at all
  (`INSTRUMENT_NOT_TRADEABLE`) — moot now, but relevant if gold (or any
  metal) ever comes back.

---

# Change: Add MODE="demo" — real OANDA orders on the practice account (2026-07-30) — LIVE-AFFECTING, PENDING APPROVAL

## Why
User connected MyFXBook to their OANDA account and found real trade history
appears there — correcting my earlier claim that OANDA demo accounts don't
record history. Root cause of "no history at all": the bot currently runs
`MODE="paper"` (`config.py:5`), which uses `PaperTrader` — pure internal
simulation, per its own docstring ("simulates broker execution with no real
orders", `trade/paper_trader.py:2`). No orders are ever sent to OANDA at
all, demo or otherwise, so there's nothing for OANDA to report to MyFXBook.
User's ask: bot should place real automated orders on the OANDA demo/
practice account so OANDA records them and MyFXBook mirrors that history.

Key finding: real order placement already exists and is fully wired —
`connectors/oanda_connector.py`'s `place_market_order`/`close_trade`/
`set_sl_tp`/`get_open_trades`, used by `trade/trade_manager.py`'s
`TradeManager`, invoked from `main.py` — currently gated purely behind
`config.MODE == "live"`. Problem: today's "live" mode also flips
`OANDA_ENV` to `"live"` and swaps in `OANDA_LIVE_API_KEY`/
`OANDA_LIVE_ACCOUNT_ID` (`config.py:260-263`) — i.e. it targets real-money
trading, not the practice account. No existing mode places real orders
against the practice/demo account only.

Also noticed: `.env`'s `OANDA_LIVE_ACCOUNT_ID` is currently set to the
exact same value as `OANDA_ACCOUNT_ID` (101-001-9445956-001) — almost
certainly a placeholder, not a real live account ID. Not touching this
file/value as part of this change (see "explicitly not doing" below).

User's decision on naming (asked directly via AskUserQuestion): three
modes — `"demo"` places real orders on the OANDA practice account,
`"live"` places real orders on the real-money account, `"paper"` (kept)
stays pure internal simulation with zero OANDA order calls.

## Plan
- [x] `config.py`: change `MODE` value from `"paper"` to `"demo"`; update
      the comment to document all 3 valid values. No change needed to the
      `OANDA_ENV`/`OANDA_API_KEY`/`OANDA_ACCOUNT_ID` ternaries at
      `config.py:260-263` — they already fall through to practice
      credentials for any `MODE` other than `"live"`, so `"demo"` already
      resolves correctly there with zero changes.
- [x] `main.py`: 3 spots gate the *real* trading path strictly on
      `config.MODE == "live"` and need to also treat `"demo"` as real
      trading (everywhere else already uses `if MODE=="paper": ... else:
      ...`, so `"demo"` falls into the existing TradeManager branch
      automatically — verified lines 446/531/557/574/618/813/1008/1017-19/
      1060 need no change):
  - line 139 (`_tick_live_trades` dispatch)
  - line 638 (weekend-close real-trade branch)
  - line 850 (`_reconcile_live_positions` startup guard)
- [x] Update `main.py`'s top-of-file module docstring (currently says
      "Live mode: set MODE = \"live\"...") to also document `"demo"` mode.
- [x] Run the full test suite — confirm zero regressions. (Tests
      instantiate `PaperTrader`/`TradeManager` directly and never
      reference `config.MODE`, so expected to be inert — will verify, not
      assume.)
- [x] Explicit flag, not silent: once `MODE="demo"` ships, the bot places
      REAL orders on the OANDA practice account starting the next
      scheduler tick after restart. Recommend restarting deliberately
      (paper-mode's open trades/state won't carry over — `TradeManager`
      keeps separate state in `data/live_state_forex.json`) and watching
      the first few trades closely.

## Review
`config.py`: `MODE` changed `"paper"` → `"demo"`, comment expanded to
document all 3 values. `main.py`: 3 exact-match `config.MODE == "live"` /
`!= "live"` checks changed to `in ("demo", "live")` / `not in ("demo",
"live")` (candle-close trade tick, weekend-close real-trade branch,
startup broker-reconciliation guard) — everywhere else in the file already
used `if MODE=="paper": ... else: ...`, which routes `"demo"` into the
existing `TradeManager`/`OandaConnector` real-order path with no code
change. Module docstring updated to document all 3 modes. Total diff: 2
lines in `config.py`, 3 one-word changes + a docstring update in
`main.py` — no new abstractions, no behavior change for `"paper"` or
`"live"` (byte-identical to before for both).

**Verification**: `ast.parse()` (UTF-8) on both files — clean. Full test
suite: 237/237 pass, zero regressions (tests never reference
`config.MODE`, as expected). Not yet verified: an actual live restart
placing a real order against the OANDA practice account — that requires
restarting the bot process, which I have not done. Recommend restarting
deliberately and watching the first trade/log line (`_init_connectors()`
logs "✓ Oanda connected (practice)...", and the account-refresh path now
calls `_oanda_connector.get_account_summary()` for real broker balance
instead of `PaperTrader`'s simulated one) to confirm before trusting it
unattended.

**Not touched, by design**: `.env`'s `OANDA_LIVE_ACCOUNT_ID` (still a
placeholder duplicate of the practice ID) — irrelevant until actually
going real-money live; the price-stream SL/TP tick (still paper-only,
correct since broker-side orders carry their own attached SL/TP).

## Explicitly NOT doing (flag for user)
- Not touching `.env`'s duplicate `OANDA_LIVE_ACCOUNT_ID` — that's a
  real-money live credential; only touch it once actually ready to go
  live for real, with the user's own correct value.
- Not changing the price-stream SL/TP tick logic (currently wired only
  for `PaperTrader`, `main.py:1060`) — this is correct as-is: real broker
  orders carry their own server-side attached stop-loss/take-profit
  (`takeProfitOnFill` set at order placement in
  `oanda_connector.py::place_market_order`), so OANDA enforces SL/TP
  itself. No client-side tick-based check is needed for demo/live trades.

---

# Fix: 4 trade-blocking gates weren't writing structured BLOCKED audit rows (2026-07-29)

## Why
User asked why they get Telegram signal alerts with almost never a
corresponding open trade. Real answer (confirmed against `data/
signal_audit.csv`, 2026-06-08 to now): mostly WATCHING-tagged alerts
(28 in that window) — these are explicit, by-design, never meant to trade.
Telegram is never sent at all for a TRIGGERED signal that gets blocked by
a gate, so those aren't the source of the user's confusion — but while
tracing this, found `_process_pair()`'s gate chain in `main.py` was
inconsistent about writing a structured `result="BLOCKED"` row via
`_audit_blocked()` (`engine.signal_audit.log_signal`): some gates did,
some only called the internal `_block()` helper (dashboard stamp +
`record_skip()` for shadow-outcome resolution, no CSV audit trail).
Confirmed via `signal_audit.csv`: 95 TRIGGERED rows but only 19 explicitly
tagged BLOCKED, even though ~63 of those 95 never became real trades
(32 total trades ever opened) — most of that gap was invisible in the
structured log, only visible in raw text logs.

**Correction to my own initial read**: first guessed the trending-structure
filter was one of the gap gates — wrong, checked the actual code and it
already calls `_audit_blocked()`. The real 4 gates missing it: the ML
tiered gate, the H4/ATR `gate_blocked` passthrough, and both news-blackout
checks (ForexFactory + Finnhub). Session filter, structure filter,
duplicate guard, pre-trade/max-open, and cooldown all already had it.

## What changed
`main.py`: added the same `_audit_blocked(pair=..., result="BLOCKED",
reject_reason=...)` call (matching the existing pattern from every other
gate) to all 4 gaps, immediately before their existing `_block()` call.
No behavior change to trading decisions — purely fills in the audit trail
so `signal_audit.csv`'s BLOCKED count and reject_reason breakdown actually
reflect every gate, not just 5 of 9.

## Verification
Full test suite: 237/237 pass. `main.py` syntax-checked.

---

# Change: commit the live roster to breakout-retest (trend-only), drop EMA-bounce routing, screen full major/minor universe for replacements (2026-07-29) — LIVE-AFFECTING, IN PROGRESS

## Why
User decision, made deliberately after reviewing the strategy tradeoffs and
the bot's full 11-gate filter stack: EMA-bounce is architecturally
incompatible with trending markets (hard-gated off above ADX 28 AND during
uptrend/downtrend structure, 0/9 + 0/3 historical WR in trends per
main.py's own comment). Breakout-retest is the trend-following mechanic.
Decision: standardize the live roster on breakout-retest, stop trading
consolidation/sideways conditions entirely, and replace any pair that
doesn't hold up under breakout-retest with a minor pair that does — rather
than leaving unconverted pairs defaulted onto EMA-bounce (confirmed for the
user: `strategy_dispatch.resolve_strategy()` defaults any pair NOT in
`config.STRATEGY_OVERRIDE` to `"ema_bounce"`, so leaving the code in place
without re-routing every pair would silently keep trading chop on whichever
pairs never got switched).

## Plan
- [ ] Add a consolidation/regime filter to `engine/strategy_breakout_retest.py`:
      compute the same 34/100/200 EMA ribbon the chart already shows,
      require its ATR-normalized spread to clear a minimum before honoring
      any break-of-structure — without this, "no longer trade sideways
      markets" isn't actually true for this strategy (nothing today stops
      it firing on a fakeout BOS inside chop). New config flag
      `BREAKOUT_CONSOLIDATION_FILTER_ENABLED` (default False until proven)
      + `BREAKOUT_MIN_MA_SPREAD_ATR` threshold (tuned via backtest, not
      guessed).
- [ ] Re-run full test suite — confirm zero regressions.
- [ ] Backtest breakout-retest (filter ON) against every pair in the
      current roster: active (USD_CAD, NZD_USD, EUR_AUD, GBP_CAD, GBP_USD)
      + watch (EUR_CHF, AUD_JPY, EUR_CAD, CHF_JPY, EUR_GBP, CAD_CHF,
      NZD_CHF). Compare against each pair's current EMA-bounce baseline
      (re-run fresh, not from memory — numbers drift day to day).
- [ ] Screen the full major/minor 8-currency universe (USD/EUR/GBP/JPY/
      CHF/AUD/CAD/NZD, 28 possible crosses) under breakout-retest —
      not just the ones already vetted under EMA-bounce, since a genuinely
      different entry mechanic can favor different pairs (already proven:
      GBP_USD/GBP_CAD split the same day under this exact dynamic). Found
      one pair with **zero** backtest history under any strategy so far:
      **EUR_JPY** — every other of the 28 crosses appears somewhere in the
      active/watch/rejected/removed tables in `project_pair_config` memory.
      True exotics beyond these 8 currencies (SGD/ZAR/TRY/MXN/etc.) are
      NOT in scope yet — flagged for the user before going further than
      the established major/minor universe, given spread/liquidity
      questions those introduce that this bot has never evaluated.
- [ ] Decide per pair: convert to breakout-retest if it backtests better
      than current EMA-bounce baseline; if not, look for a same-currency
      or nearby minor replacement that does; if genuinely nothing works,
      flag explicitly rather than silently leaving it on EMA-bounce.
- [ ] Update `config.STRATEGY_OVERRIDE`/`FOREX_PAIRS`/`FOREX_WATCH` to
      reflect the final roster. Every active pair should end up explicitly
      routed to breakout-retest (or flagged as a known exception with the
      user's sign-off) — no pair should be trading live by falling through
      to the EMA-bounce default silently.
- [ ] `config.py`'s `TIMEFRAMES["primary"]` confirmed already "M30" —
      matches the user's ask for a fast 15/30-minute primary TF, no change
      needed there.

## Result: roster-wide breakout-retest conversion REJECTED (2026-07-29)
Backtested breakout-retest (with the new consolidation filter ON) against
all 5 active + 7 watch pairs + EUR_JPY (first-ever backtest for that pair
under any strategy), 3500 bars each:

| Pair | EMA-bounce PF / PnL | Breakout-retest PF / PnL | Winner |
|---|---|---|---|
| USD_CAD | 2.43 / $67.66 | 0.89 / -$3.96 | EMA-bounce |
| NZD_USD | 1.88 / $46.81 | 0.39 / -$18.20 | EMA-bounce |
| EUR_AUD | 2.02 / $58.40 | 0.45 / -$23.77 | EMA-bounce |
| GBP_CAD | 3.95 / $132.79 | 0.99 / -$0.25 | EMA-bounce |
| GBP_USD | 1.64 / $49.90 | 2.32 / $47.57 | Breakout-retest (already live) |
| EUR_CHF | 1.56 / $17.01 | 0.70 / -$6.02 | EMA-bounce |
| AUD_JPY | 1.21 / $17.29 | 1.14 / $4.68 | EMA-bounce |
| EUR_CAD | 1.00 / $0.24 | 1.07 / $2.28 | Tie (both ~breakeven) |
| CHF_JPY | 1.67 / $51.37 | 1.10 / $3.41 | EMA-bounce |
| EUR_GBP | 2.49 / $22.79 | 1.32 / $4.82 | EMA-bounce |
| CAD_CHF | 1.15 / $2.96 | 2.03 / $5.13 | Sample too small (4 trades) to trust |
| NZD_CHF | 1.74 / $11.04 | 0.82 / -$4.45 | EMA-bounce |
| EUR_JPY | 1.67 / $41.85 | 0.61 / -$14.81 | EMA-bounce |

GBP_USD remains the **only** pair where breakout-retest wins — matches
everything already known about it. Every other pair, including all 4 other
currently-active ones, does clearly worse under breakout-retest, several by
a wide margin (GBP_CAD's PF drops from 3.95 to 0.99). Consistent with
GBP_CAD's prior standalone breakout-retest rejection (PF 0.44 at 4999 bars,
[[project_pair_config]]) — the new consolidation filter modestly improved
that specific number (0.44→0.99) but nowhere near enough to compete with
its EMA-bounce baseline.

**Decision: do NOT convert the roster.** Keep the existing per-pair split
(GBP_USD on breakout-retest, everything else on EMA-bounce) — this is
exactly what was already live before this investigation started, so no
`STRATEGY_OVERRIDE` change was made. The consolidation filter code stays in
`strategy_breakout_retest.py` (dormant — `BREAKOUT_CONSOLIDATION_FILTER_ENABLED
= False`) in case a future breakout-retest candidate pair needs it; isolated
test on GBP_USD showed it neither helps nor hurts there (identical 16
trades, PF 2.30 vs 2.32 filter off/on) since its real entries never
happened to fire during compressed-ribbon conditions in this window.

User initially misread the table (thought GBP_USD's $47 breakout-retest PnL
was the bot's overall ceiling, not one pair's one-strategy result) —
corrected: combined PnL across the 5 originally-active pairs using each
pair's actual live strategy is ~$353 over the same window, with GBP_CAD's
PF 3.95 being a genuinely strong result. Reframed the ask from "which
strategy should the whole bot run" to "trade frequency," which the roster
promotion below addresses directly.

## Change: CHF_JPY + EUR_JPY promoted from watch to active (2026-07-29) — LIVE-AFFECTING
User's real goal, once clarified: more trade frequency (specifically so win
rate becomes statistically trustworthy — can't judge a win rate off too few
trades). Lowest-risk lever available: `FOREX_WATCH` pairs generate signals
but `main.py:151` explicitly never opens trades on them ("scan signals for
dashboard display but never open trades") — so pairs with real backtested
edge were sitting idle contributing zero frequency. Promoted the two
watch-list pairs with both a large-enough sample to trust (established
~30-trade rule) and solid PF from today's fresh numbers: CHF_JPY (32
trades, PF 1.67, $51.37) and EUR_JPY (25 trades, PF 1.67, $41.85, first
time ever backtested). Left AUD_JPY (27 trades, PF 1.21 — thinner edge),
EUR_CAD (re-run came back ~breakeven, PF 1.00 — worse than its original
2026-07-02 number), EUR_GBP/CAD_CHF/NZD_CHF (all still under ~15 trades) on
watch — none cleared the same bar.

**What changed**: `config.py` — `FOREX_PAIRS` gained `"CHF_JPY"` and
`"EUR_JPY"`; `FOREX_WATCH` lost `"CHF_JPY"` (EUR_JPY was never on watch —
first-ever addition to the pair universe). No code changes needed —
`dashboard/state.py`, `main.py`'s scan loop, and the WFO optimizer's pair
list all derive from `config.FOREX_PAIRS`/`FOREX_WATCH` dynamically; JPY
pip-size/decimal handling is already generic (`"JPY" in pair`) throughout,
already exercised by AUD_JPY on watch. Both new pairs default to EMA-bounce
(not in `STRATEGY_OVERRIDE`) and will get picked up by next Sunday's WFO
refit automatically (`main.py:1037` filters to `FOREX_PAIRS` pairs still on
EMA-bounce).

**Verification**: full test suite, 237/237 pass, no changes needed to any
test (nothing hardcodes the pair list length/contents).

## Not yet decided / will flag before finalizing
- Whether WFO-style parameter tuning needs to be built for breakout-retest
  before trusting it across a wider pair count (today it runs fixed
  constants: `retest_band_mult`, plus the new spread threshold) — WFO
  currently only tunes EMA-bounce and explicitly skips
  `STRATEGY_OVERRIDE` pairs.
- Whether EMA-bounce should be fully decommissioned (deleted) once every
  pair converts, or kept dormant as a fallback — not deleting anything yet,
  only changing routing, until the full roster's numbers are in.

---

# Fix: double-click places a drawing instead of toggling indicator panes when a draw tool is left active (2026-07-26)

## Plan
- [x] `dashboard/assets/chart.js`: add `if (drawMode) return;` guard to the
  `chart.subscribeDblClick(...)` handler (~line 3576), matching the guard
  pattern used by every other drawing/drag handler in this file, so a
  double-click while a draw tool (e.g. H-Line) is active doesn't fall
  through into `_toggleIndicatorPanes()` behavior conflicting with drawing.
- [x] `node --check` on the file — passes.
- [x] Note in review section: this doesn't change drawing-tool behavior
  itself (placing a line while the tool is active is correct) — it only
  stops the indicator-toggle handler from being in the mix while a tool is
  selected. Workaround for right now: click the H-Line button (or ✕/cancel)
  to deactivate the tool, then double-click to toggle panes.

## Root cause (revised — my first pass below was incomplete)
User pushed back: "it's not active on the toolbar" — meaning the H-Line
button wasn't visibly highlighted when this happened, which my first fix
(the `subscribeDblClick` guard alone) didn't explain. Traced further:

The real bug is in the ✕ ("cancel") toolbar button, which is documented as
"try to delete selected drawing first; if nothing selected, cancel mode."
Placing a new drawing auto-selects it (`selectDrawing()` runs right after
creation). So the very next ✕ press — the natural way to try to exit a
draw tool right after using it — hits the "something is selected" branch:
it deletes the just-placed line and, by the existing `if (!deleted)` guard,
never calls `_apexSetDrawMode(null)`. `drawMode` stays `'h-line'` client-side.

Meanwhile `dashboard/app.py::toggle_draw_mode()`'s cancel branch
unconditionally painted every toolbar button inactive AND returned
`no_update` for `draw-mode-store`'s data — so the toolbar visually goes
dark regardless of which of the two things actually happened server-side,
while the client-side `drawMode` variable (the actual thing that gates
click handling) stays armed. Next double-click gets consumed by the
still-active placement handler instead of the indicator-pane toggle.

(The `subscribeDblClick` guard from my first pass is still valid and stays
in — it's a real gap relative to every other handler in the file — but it
wasn't the actual cause of what you were seeing; this ✕-button desync was.)

## What changed
- `dashboard/assets/chart.js`: `if (drawMode) return;` guard added to
  `chart.subscribeDblClick(...)` (kept from the first pass — harmless and
  consistent with the rest of the file, just not sufficient on its own).
- `dashboard/app.py`'s clientside `isCancel` branch: now always calls
  `_apexDeleteSelected()` **and** `_apexSetDrawMode(null)`, instead of only
  deactivating the tool when nothing was selected. ✕ now reliably means
  "delete anything selected, and always exit the current draw tool" —
  matching what the toolbar has always unconditionally displayed.
- `dashboard/app.py::toggle_draw_mode()`'s cancel branch: returns `None`
  (not `no_update`) for `draw-mode-store`'s data, so the server-side store
  can't go stale relative to the client-side `drawMode` it's supposed to
  mirror — a stale store value would otherwise mislead the *next* tool
  button's own `mode = None if current == btn_idx else btn_idx` toggle
  logic.

## Review
Both files changed minimally — no new abstractions, same existing patterns
(`_apexSetDrawMode`/`_apexDeleteSelected` already existed; just changed how
`isCancel` combines them). Behavior change: pressing ✕ while a drawing is
selected now also exits the active draw tool (previously it only deleted
the selection and silently kept the tool armed) — this is the fix, not a
side effect; there's no remaining workflow where you'd want ✕ to delete a
selection while keeping a placement tool armed, since each drawing already
has its own inline delete button for that. `python -c "import ast; ...
"` and `node --check` both pass. No test suite coverage exists for either
the dashboard callback wiring or chart.js (frontend/browser-driven, same
gap as every other UI-only change in this file) — a live click-through
(select H-Line, place one, press ✕, confirm toolbar + double-click-to-hide
both work immediately after) is recommended before closing this out.

---

# Fix: indicator quick-hide reappearing on TF switch (2026-07-24) — real fix this time

## Why
User: the double-click-to-hide-CCI/MACD bug from 2026-07-22 is back —
"why are the indicators reappearing after I double click to remove the
indicators and after a few they reappear back on the chart, I thought we
fixed this issue already." Also: "I don't like when I'm working and the
whole chart shifts back to what I suppose is a default chart" (same
phenomenon — the price pane visibly resizes when the sub-panes reappear).
Asked for verification across all timeframes this time.

## Root cause: the 2026-07-22 fix only covered part of the problem
That fix stopped the same-pair/TF periodic 60s refresh from undoing the
hide, by skipping a redundant `_loadIndSettings()` reload when
`_indSettings` was already populated. But the hide itself was still only
an **in-memory mutation** — never written to `localStorage`. Every
timeframe switch goes through `init()`, which unconditionally reloads
`_indSettings` fresh from `localStorage` — where the hide never existed.
So switching timeframes (exactly what "verify on all timeframes" would
immediately surface) silently undid it every time, which is exactly what
the user was hitting while actually using the chart.

## What changed (`dashboard/assets/chart.js`)
Replaced the in-memory-only toggle with a `localStorage`-backed flag
(`apex_ind_panes_hidden_<pair>`, checked live rather than trusted from a
possibly-stale local variable):
- `_toggleIndicatorPanes()`: hide sets the flag directly (doesn't touch
  the real per-pair settings key, so the Indicator Settings modal's
  "Visible" checkbox still correctly reflects the real saved preference,
  not the quick-hide — same separation the original design intended);
  restore clears the flag and reloads the real settings fresh.
- New `_enforceIndPanesHidden()`: re-applies the hide on top of whatever
  `_indSettings` currently holds. Called from **both** `init()`'s settings
  load (covers pair/TF switches) **and** `_apexApplyIndSettings()` (covers
  every chart-data refresh, including the periodic 60s tick) — one
  invariant enforced at every point settings get (re)loaded, rather than
  trying to coordinate one-time state transitions across two async code
  paths.
- `_cciChanged()`/`_macdChanged()` (the Indicator Settings modal's own
  change handlers) now clear the flag at the top — so an explicit,
  deliberate settings-panel edit always wins over a stale quick-hide,
  instead of a later refresh silently re-hiding an indicator the user just
  turned back on.
- Removed the old `_indPanesHidden`/`_indPanesSavedVisible` in-memory
  variables entirely (including the `init()` reset that caused this bug in
  the first place) — replaced by the flag existing or not, checked live,
  which can't drift out of sync with reality the way a cached boolean can.

## Verification (explicitly across all timeframes, per the ask)
Standalone test instance (port 8051, live instance on 8050 untouched),
via Playwright, using rendered `<canvas>` count as a proxy for
pane-visibility (cross-checked visually against screenshots at both ends
of the sequence — 30m right after hiding and W at the end of the cycle,
both confirmed showing the price pane at full height with no sub-panes):

1. **All 7 timeframes** (5m/15m/30m/1H/4H/D/W): hid once, then clicked
   through every other timeframe in sequence with no re-clicking — stayed
   hidden on all 7. This is the scenario that was actually broken before.
2. **Real periodic refresh**: hid, waited a genuine 65s for the live
   `interval-60s` tick to fire — stayed hidden.
3. **Settings-panel override**: confirmed the "Visible" checkbox correctly
   still shows checked while the chart is quick-hidden (state properly
   separated); explicitly unchecked then rechecked CCI via the panel —
   chart correctly followed the panel action each time; switched
   timeframe afterward — stayed visible (flag correctly cleared, no
   lingering re-hide).

Zero console/page errors across all of the above. `node --check` passes.
No Python files touched this round; full suite (237/237) still passes as
a sanity check.

---

# Remove: "Seed from Backtest" feature entirely (2026-07-24)

## Why
User: since seed data can never feed suggestions or training anymore
(previous entry), what does the button actually do — "if it's not doing
anything to improve the bot then we don't need it taking up space."
Traced the full effect: it runs backtests (real compute cost), writes rows
that are now permanently unused, then calls `force_train()` internally —
which, since training now filters to `source=="live"`, trains on exactly
the same data `force_train()` would use if called directly via "Retrain
Now". The button had become 100% redundant with an existing simpler one,
plus wasted work.

## What changed
- `dashboard/app.py`: removed the "Seed from Backtest + Retrain" button
  from the Learning panel layout, its `Input` in `ml_action()`'s callback
  signature, and the `_run_seed()` branch/thread-start (the trailing
  `if triggered == "ml-wfo-btn":` branch now just falls through to a
  final `return no_update`).
- `learning/pattern_learner.py`: removed `seed_from_backtest()` entirely.
  No test coverage referenced it; only the now-removed dashboard button
  called it.
- `backtest/runner.py::run_backtest()`: removed the `seed_rows` list
  construction and its `"seed_rows"` key from the return dict — this was
  built on *every* backtest call (WFO, walk-forward, dashboard, CLI — the
  whole session's worth of backtesting today) purely to feed the
  now-deleted method. All the same data is still available via the
  existing `"trades"` key if ever needed again.
- Updated the now-stale comments in `data_collector.py`, `feedback_loop.py`,
  `pattern_learner.py` that referenced the removed method by name.

## Verification
- Full test suite: 237/237 pass.
- Full repo grep for `seed_from_backtest`/`seed_rows`/`ml-seed-btn`: zero
  matches.
- Playwright: opened the Learning panel on a live standalone instance —
  "Seed from Backtest" is gone, "Retrain Now" and "Run WFO Now" both still
  present and functional, zero console errors.

## Found a second staleness bug while verifying (fixed in the same pass)
The Learning panel's "Training samples: X / 50" display reads
`ml_stats.get("n_samples")` — but `toggle_learning_panel()` already computes
a correct, freshly-read `sample_count` from disk and passes it as a
*separate* parameter that the rendering code never actually uses for that
display (only as a fallback in one "remaining trades" calculation).
Caught this because the verification screenshot showed "Training samples:
0 / 50" on a fresh test instance, when the real number is 93 — same root
problem as yesterday's accuracy-display bug (trusting possibly-stale
`state["ml_stats"]` instead of a value already computed fresh nearby).
Fixed by merging `"n_samples": sample_count` into `ml_stats_val` in the
callback, same pattern as the `"accuracy"` override. Re-verified via
Playwright: now correctly shows "Training samples: 93 / 50 min".

---

# Fix: seed data permanently excluded from suggestions + ML training (2026-07-24)

## Why
Follow-up to both the ML-suggestions investigation and the accuracy-display
fix below. User: "fix them all, and in the future how can we make sure that
we are not using old seed data be it the ML or the signal triggering
trades."

## Correction to my own earlier analysis (important)
I initially told the user "127 of 131 ML training samples are old
backtest-seeded/shadow-resolved rows." **That was wrong** — I conflated
two different things. `would_win`/`would_lose` outcomes can *only* ever
come from `learning/shadow_outcomes.py` resolving a real
`record_skip()`-logged row against real subsequent candles;
`seed_from_backtest()` never writes those outcomes (only `win`/`loss`).
Rechecked properly: of the 131-sample pool, **93 were genuinely live**
(4 real executed trades + 89 real shadow-resolved near-misses) and only
**38 were actually seeded** backtest replay. Corrected this with the user
before implementing anything, since the wrong number would have led to
solving the wrong problem.

## The real, permanent fix: an explicit `source` column
Root design gap: nothing in `signal_log.csv`'s schema distinguished "this
row came from the live bot evaluating a real market condition" from "this
row came from replaying old historical bars through a backtest." Every
consumer (`feedback_loop.py`, `pattern_learner.py`) had to *infer* this
from a proxy (`position_size > 0`), which is fragile and easy to get
wrong (I got it wrong myself, above).

- `learning/data_collector.py`: added `"source"` to `SIGNAL_FIELDS`, plus
  `SOURCE_LIVE = "live"` / `SOURCE_SEED = "seed"` constants.
  `_build_row()` (used by both `record_close()` and `record_skip()`) now
  always stamps `source="live"` — this is the single choke point for
  every real signal the bot ever evaluates, so this can't be forgotten at
  a future call site.
- `learning/pattern_learner.py::seed_from_backtest()` now stamps
  `source="seed"` on every row it writes.
- `learning/feedback_loop.py::_load_closed()` and
  `learning/pattern_learner.py::_load_closed()` both now filter to
  `source == "live"` before doing anything else — seed data is excluded
  from suggestions and training **unconditionally**, not just "until
  cleaned up this once." A missing `source` column (old file format)
  degrades safely to "exclude everything" rather than crashing or
  silently including stale data.
- `main.py::_update_ml_stats()`'s sample count now calls
  `pattern_learner._load_closed()` directly instead of its own separate
  (and now-inconsistent) inline filter, so the displayed count always
  matches what's actually used for training.
- **Found the same confirm-TF ratio bug in `seed_from_backtest()`** (4th
  place today, after `run_walk_forward()`, `wfo_optimizer.py`, and the
  backtest CLI): hardcoded `gran_h4 = TIMEFRAMES.get("confirm", "H4")` and
  `bars // 2 + 20` (stale 2:1 ratio). Fixed to use
  `config.confirm_tf_for(pair)` / `confirm_tf_ratio(pair)`, consistent
  with every other fixed call site.
- Backfilled the existing 135-row `signal_log.csv` with `source`, using
  the same classification logic now enforced going forward (backup:
  `signal_log.csv.bak_before_source_backfill`).

## Side effect worth knowing about
The dashboard's "Seed from Backtest + Retrain" button still runs backtests
and writes rows (useful for reference/audit), but those rows can now
**never** feed live suggestions or ML training again — seed data is
permanently excluded, not just excluded until enough real data
accumulates. This was a deliberate reading of "make sure we are not using
old seed data... in the future" as unconditional. It does mean the
original bootstrap use case (cold-starting a brand-new pair with zero live
history) no longer works via this button — if that's needed later, it
would need a deliberate, separate design (e.g. a time-boxed exception),
not implemented here since it wasn't asked for.

## Verification
- Full test suite: 237/237 pass. Fixed `tests/test_learning.py`'s
  `_synthetic_log()` helper to stamp `source="live"` (10 tests were
  failing because the synthetic rows it builds now got excluded by the
  new filter along with everything else lacking a `source` field).
- `generate_suggestions()` on the real (backfilled) log now returns
  **0 suggestions** — correct and honest: only 4 real executed trades
  exist, each a different pair, all below the 5-trade minimum. The
  USD_CHF/asian/london noise is gone.
- `pattern_learner._load_closed()` now returns exactly **93** rows (was
  131). Retrained: **AUC 0.552 ± 0.155** on the clean population — above
  random, a real (if still low-confidence given the sample size)
  improvement over the old contaminated 0.434.

---

# Fix: ML Engine accuracy stuck showing "not yet trained" despite 131 training samples (2026-07-24)

## Why
User asked why the dashboard says "Training begins after 131/50 closed
trades" when 131 is already well past the 50 threshold — that phrasing
implies training hasn't started, which should be impossible once n≥50.

## Root cause (two bugs stacked)
1. `learning/pattern_learner.py::train()` computes a real cross-validated
   accuracy (`cv_mean`, ROC-AUC) but only ever logs it as part of a
   formatted string — never stores or persists it anywhere retrievable.
2. `main.py::_update_ml_stats()` (called right after every successful live
   retrain) hardcoded `"accuracy": None` unconditionally, regardless of
   whether training actually happened. Confirmed via the rotated log file
   (`logs/main.log.1`) that training genuinely already succeeded today at
   12:17:52-54: "PatternLearner: XGBoost trained on 131 samples
   features=18 AUC=0.434±0.079 pos_weight=1.0" — the model trained and
   saved correctly; only the dashboard's display of it was broken.

The dashboard's `_ml_block()` checks `if acc is None: show "Training
begins after {n}/50"` — since accuracy was always hardcoded to `None`,
this message would show forever no matter how many times the model
actually retrained.

## What changed
- `learning/pattern_learner.py::train()` — now persists `cv_mean` into the
  saved joblib file as `"accuracy"`, alongside the existing `model`/
  `features` keys.
- `learning/pattern_learner.py` — new `get_accuracy(model_path)` method,
  same shape as the existing `get_feature_importance()` (loads from disk,
  returns `None` gracefully — including for models saved before this fix,
  which won't have the key).
- `main.py::_update_ml_stats()` — now calls `_pattern_learner.get_accuracy()`
  instead of hardcoding `None`. (Also removed a dead `importance =
  learner.get_feature_importance()` fetch in this function that was never
  used — replaced by the accuracy fetch it needed instead.)
- `dashboard/app.py::toggle_learning_panel()` — now overrides `ml_stats`'s
  `"accuracy"` with a value read fresh from the saved model
  (`learner.get_accuracy()`) rather than trusting `state["ml_stats"]`,
  which only gets updated by `main.py` right after a live retrain and can
  otherwise sit stale — same reasoning already applied to `importance` in
  this same callback.

## Verification
- Full test suite: 237/237 pass.
- Forced a retrain (`PatternLearner().force_train()`) and confirmed
  `get_accuracy()` returned `0.434...`, matching the logged AUC exactly
  (was `None` before the retrain, since the on-disk model predates this fix).

## The number itself is the real finding
Now that it's visible, the model's actual accuracy is AUC=0.434±0.079 —
**below 0.5, no better than random**. This isn't surprising given the
2026-07-24 "ML suggestions" investigation earlier today: 127 of the 131
training samples are old backtest-seeded/shadow-resolved rows, not real
trades (only 4 real closed trades exist in the whole log right now). The
model has essentially never learned from actual live performance yet.
This reinforces the still-open recommendation from that earlier
investigation: `learning/feedback_loop.py` and
`learning/pattern_learner.py::_load_closed()` should probably distinguish
real trades (`position_size > 0`) from seeded/shadow rows before either
generating suggestions or training, rather than pooling everything as if
it were equally reliable. Not yet actioned — flagged for the user's call.

---

# Change: Scope B — stripped crypto/Alpaca from shared risk/trade code (2026-07-24) — LIVE-AFFECTING

## Why
Follow-up to the Scope A crypto removal below. User approved the deeper
cleanup this time.

## What changed
- `risk/risk_manager.py::calculate_position_size()` — dropped the
  `instrument_type` param entirely (was `'forex'` → int units / `'crypto'`
  → float qty branch); now always computes forex units, return type
  `-> int`. Updated all 5 callers: `backtest/runner.py`, `main.py`,
  `dashboard/app.py` (2 call sites), `trade/trade_manager.py`.
- `tests/test_risk.py` — removed `test_crypto_1pct_risk`; fixed
  `test_forex_1pct_risk`'s 4th arg (was literally passing the string
  `"forex"` where `pair` now goes — changed to a real pair, `"EUR_USD"`).
- `tests/test_trade.py` — removed the `live_state_crypto.json`
  non-existence assertion.
- **Found beyond the original Scope B checklist**: `trade/trade_manager.py`
  had 5 more `if self.market == "forex": ... else: ...` branches whose
  `else` called Alpaca-specific connector methods
  (`place_market_order(symbol=, qty=, side=)`, `close_position()`,
  `get_account()`) that never existed on `OandaConnector` — dead code
  since Scope A guarantees `self.market` is always `"forex"` now. Removed
  all 5 dead branches (order placement, ATR-trailing condition, partial
  close, manual close, account fetch) since they referenced a deleted
  class's calling convention and were unreachable. `self.market` itself
  (used for state-file naming, e.g. `live_state_forex.json`) was kept —
  it's a broker/state-namespace label, not crypto-specific.
- Docstring/comment sweep for accuracy: `trade/trade_manager.py`'s module
  docstring and class docstring (no longer claim "works with either
  OandaConnector or AlpacaConnector" / "on both brokers" / "two independent
  TradeManager instances"), `trade/paper_trader.py`'s `open_trade()`
  docstring, `engine/signal_engine.py`'s `run()` docstring,
  `backtest/runner.py`'s CLI `market=` argument (was a dead
  `"forex" if "_" in args.pair else "crypto"` ternary, now just `"forex"`).

## Verification
- Full repo grep for `crypto`/`Alpaca`/`CRYPTO` (case-insensitive, all
  `.py` files): zero matches — completely clean.
- Full test suite: 237 passed (238 - 1 removed crypto test, zero new
  failures).
- Manually verified `calculate_position_size(10000, 1.0845, 1.0835,
  "EUR_USD")` still returns a correct int unit count with the new
  4-arg signature.

## Review
The dead-branch removal in `trade/trade_manager.py` went beyond what was
explicitly itemized in the original Scope B plan (which only named the
`risk_manager.py` signature change + 2 test files + "sweep docstrings").
Judged safe to include because: (1) every removed `else` branch was
provably unreachable — `self.market` can only ever be `"forex"` since
Scope A removed the only code path that ever constructed a
non-forex `TradeManager`; (2) each dead branch called a method
(`close_position`, `get_account`, Alpaca-style `place_market_order`) that
doesn't exist on `OandaConnector`, so it would have raised if ever
reached, never silently done something wrong; (3) this is squarely within
"remove all crypto trading," just a layer deeper than the original grep
sweep caught. Live order-placement/close code was touched carefully —
every change is a straight unwrap of an always-true `if`, not a logic
change.

---

# Change: Removed crypto/Alpaca trading — Forex-only bot (2026-07-24) — LIVE-AFFECTING

## Why
User: "we're optimizing for Forex and remove all crypto trading because
this bot will only trade Forex." `config.CRYPTO_PAIRS` was already `[]`
(crypto hasn't traded live since a prior backtest showed no edge — BTC
-19% WR, ETH also rejected), so this removed dormant infrastructure, not
live trading behavior. User confirmed Scope A only (core removal) — left
the generic `"forex"`/`"crypto"` market-label parameter in shared
risk/trade code alone (Scope B, declined) since it's harmless and also
exercised by forex's own code path.

## What changed
- `connectors/alpaca_connector.py` — deleted entirely (300 lines)
- `config.py` — removed `CRYPTO_PAIRS`, `ALPACA_API_KEY`, `ALPACA_SECRET`,
  `ALPACA_BASE_URL`
- `main.py` — removed `_alpaca_connector`/`_crypto_engine`/
  `_trade_manager_cx` globals, the Alpaca branch in `_init_connectors()`
  (now returns just `oanda`, not a tuple), the crypto branch in
  `_init_engines()`, the crypto pair scan loop, crypto branches in
  `_tick_paper_trades()`/`_tick_live_trades()`/`_sync_live_state()`/
  TradeManager init, related log lines. `_process_pair()`'s
  `instrument_type` resolution simplified to always "forex" (no more
  live-crypto call site to make it conditional on).
- `dashboard/app.py` — removed the ALPACA connection-status pill,
  every `crypto_connector`/`is_forex`-ternary fallback branch (7 call
  sites: spread quote, order-form populate, live P&L for trade cards,
  connector status, Scan Now's background task builder, the backtest
  callback's connector/granularity/confirm-ratio resolution, the account
  price-refresh loop), the standalone `__main__` Alpaca init block
- `dashboard/state.py` — removed `alpaca_ok`, `crypto_connector` keys,
  `CRYPTO_PAIRS` from the initial `signals` dict comprehension
- `tests/test_connectors.py` — removed `TestAlpacaConnectorStructural`
  and `TestAlpacaConnectorLive` (14 tests) + the module docstring/
  `alpaca_required` fixture mentions; kept as pure-Oanda test file

## Verification
- Full test suite: 238 passed (was 252 — exactly the 14 removed Alpaca
  tests, zero new failures)
- Grepped the whole repo for remaining `crypto`/`Alpaca`/`CRYPTO`
  mentions — only Scope B's generic market-label parameters remain
  (`risk_manager.py`, `trade_manager.py`, `paper_trader.py` docstrings,
  `backtest/runner.py`'s CLI `market=` arg, `signal_engine.py` docstring)
- Booted a standalone dashboard instance (port 8051, live instance on
  8050 untouched) via Playwright: page loads with no console/page errors,
  confirmed "ALPACA" no longer appears anywhere in the rendered page text,
  header now shows only "○ OANDA", screenshot confirms clean layout

## Review
Straightforward removal of already-dormant infrastructure — nothing here
changes live trading behavior since `CRYPTO_PAIRS` was already empty. The
per-pair confirm-TF label fix from earlier today (EUR_AUD showing "H1:"
vs other pairs' "H4:") was visually re-confirmed working correctly in the
same screenshot, incidental proof that today's larger refactor didn't
regress it.

---

# Fix: backtest/WFO silently used the wrong strategy for GBP_USD (2026-07-24) — LIVE-AFFECTING

## Why
Found while testing the CCI-touch gate (below): `run_backtest()` defaulted
`buy_fn`/`sell_fn`/`stop_loss_fn` to the EMA-bounce functions directly, so
every caller that didn't explicitly override them — the dashboard's
Backtest and Walk-Forward buttons, `run_walk_forward()`'s own internal
grid-search calls, and the **weekly live WFO refit job**
(`engine/wfo_optimizer.py`) — silently tested/tuned GBP_USD against
EMA-bounce, even though GBP_USD actually runs `strategy_breakout_retest`
live (`config.STRATEGY_OVERRIDE`). Worse: `strategy_breakout_retest`
doesn't even read most of what WFO's grid searches over (`CCI_PERIOD`,
`MACD_*`, `cci_threshold`, `touch_lookback`, `adx_threshold` — its own
`check_buy_signal` only reads `retest_band_mult`, not in the WFO grid at
all), so GBP_USD's stored WFO params were doubly meaningless.

## What changed
- New `engine/strategy_dispatch.py`: the per-pair strategy dispatch table
  (`STRATEGY_FNS`) and `resolve_strategy(pair)` resolver, extracted out of
  `engine/signal_engine.py` (which had its own private copy) so both the
  live signal engine and the backtester can share one source of truth
  without a circular import (this module has zero dependency on either).
- `engine/signal_engine.py`: now imports `resolve_strategy` from the new
  module instead of defining its own; behavior unchanged.
- `backtest/runner.py::run_backtest()`: `buy_fn`/`sell_fn`/`stop_loss_fn`
  now default to `None` and auto-resolve per `pair` via
  `strategy_dispatch.resolve_strategy()` when not explicitly passed.
  Explicit overrides (the CLI's `--strategy` flag, tests using stub
  functions) are unaffected — this only changes what happens when nothing
  is passed, which every affected caller does.
- Checked all tests that pass `pair="GBP_USD"` to `run_backtest()` before
  changing the default — only one (`test_repeated_calls_with_different_
  pairs_dont_cross_contaminate`), and it only asserts EUR_USD's own
  results are unaffected by an interleaved GBP_USD call, not what strategy
  GBP_USD itself used. Safe.
- **Verified end-to-end**: `run_backtest("GBP_USD", df_h1, df_h4)` with no
  strategy args now produces byte-identical results (17 trades, PF=2.57,
  PnL=$58.85) to an explicit `breakout_retest` call — confirms the
  resolution now actually works, not just "doesn't crash."
- All 252 tests pass.

## Follow-through: re-fit GBP_USD's WFO params
Triggered `wfo_optimizer.run_all_pairs(..., ["GBP_USD"], ...)` manually
rather than waiting for the weekly cycle. Old params (fit 2026-06-25,
almost certainly against EMA-bounce): `min_score=55`, WR=33.3%, 9 trades,
composite score=0.999. New fit (correctly against breakout_retest):
`min_score=50`, WR=63.6%, 11 trades, composite score=4.0. Saved to
`data/wfo_params.json` — takes effect automatically (same as it does for
every pair, weekly, with no manual gate).

**Validated over a longer window before trusting the IS fit** (3500 M30
bars, real backtest, not just WFO's own shorter in-sample window):

| min_score | WR | PF | PnL | MaxDD | trades |
|---|---|---|---|---|---|
| 55 (old) | 50.0% | 2.29 | $47.43 | 3.1% | 16 |
| 50 (new) | 45.5% | 1.94 | $79.71 | 7.1% | 33 |

Real trade-off, not a clean win: +68% PnL and more trade volume, but PF
drops and MaxDD more than doubles. Flagged to the user rather than treating
the WFO fit as automatically correct — this is just WFO now correctly
targeting the right strategy, not a manually-reviewed change like the
other three today. Left as-is (WFO's own output already live) pending the
user's call on whether to keep it or pin back to 55.

## Review
Root-cause fix, not a per-call patch — one shared resolver used
everywhere, so this class of bug can't recur at a new call site the way it
would have if each caller (dashboard, WFO, run_walk_forward) had been
individually patched.

---

# Test: CCI-at-touch (c4) hard gate — tested, rejected (2026-07-24)

## Why
While explaining a marginal USD_CAD trade to the user (confluence_score 56,
just 1 point above the 55 threshold, entered despite failing c4 — CCI
reached only -19.67 against a required -30), offered to test whether
making c4 a hard gate (like c2/H4) would improve results, the same way
every other condition-strictness question has been tested this week.

## What was tested
Added `config.CCI_TOUCH_GATE_BLOCKING` (default `False`, no live behavior
change) + a hard-block check in both `check_buy_signal`/`check_sell_signal`
in `engine/strategy_ema_cci_macd.py`, mirroring the existing c2/H4 hard-gate
pattern. Backtested soft-scored (current) vs hard-gated across all 5
active pairs, 3500 M30 bars each, under today's live settings:

| Pair | Current (soft-scored) | Hard gate |
|---|---|---|
| USD_CAD | PF 2.54, PnL $74.47, 22 trades | PF 1.76, PnL $36.11, 17 trades — worse |
| GBP_CAD | PF 3.47, PnL $128.51, 26 trades | PF 3.30, PnL $36.53, 8 trades — PF ~same, PnL collapses |
| NZD_USD | PF 1.85, PnL $45.32, 20 trades | PF 2.52, PnL $45.76, 14 trades — PF up, PnL flat |
| EUR_AUD | PF 2.42, PnL $75.32, 27 trades | PF 1.76, PnL $21.91, 14 trades — worse |
| GBP_USD | n/a — see below | n/a |

**GBP_USD row excluded**: same methodology gap as the H4-gate test two
entries below — the sweep script doesn't pass GBP_USD's real
`strategy_breakout_retest` functions into `run_backtest()`, so it silently
tested EMA-bounce instead. `CCI_TOUCH_GATE_BLOCKING` isn't referenced
anywhere in `strategy_breakout_retest.py`, so the real live GBP_USD
strategy is unaffected by this flag regardless — not worth re-running.

## Result: rejected
Even NZD_USD — the best candidate, since c4 is its own historically
weakest condition too — barely moves total PnL despite a real PF gain,
because the hard gate cuts trade count by 30%+ across every pair. Same
shape of result as ATR trailing, breakeven, and the Daily-trend gate
(see [[project_strategy_research_2026_07_22]]): another confirmation
filter reduces trade volume without a matching quality improvement.
**Not shipped.** `CCI_TOUCH_GATE_BLOCKING` stays at its default `False` —
zero live behavior change. Flag and hard-gate code left in place
(commented rationale in `config.py`) in case a different mechanism is
worth trying later, same convention as `ATR_TRAILING_ENABLED`.

---

# Change: NZD_USD given its own H4-gate override — loosened (2026-07-24) — LIVE-AFFECTING

## Why
A finding from the 2026-07-22 research pass flagged the H4/confirm-TF
alignment gate as too strict specifically for NZD_USD (old PF 1.21->1.51
under then-current H1-confirm settings), not yet implemented. Since both
the confirm TF (H1->H4) and TP R:R changed on 2026-07-23, re-verified the
finding fresh under today's actual settings rather than trust the stale
number.

Backtested `H4_GATE_BLOCKING` True vs False across all 5 active pairs,
3500 M30 bars each, current confirm-TF/TP-R:R settings:

| Pair | Blocking=True (current) | Blocking=False (loosened) |
|---|---|---|
| USD_CAD | PF 2.54, PnL $74.47 | PF 1.54, PnL $44.07 — worse |
| GBP_CAD | PF 3.47, PnL $128.51 | PF 2.36, PnL $110.35 — worse |
| NZD_USD | PF 1.31, PnL $15.85 | PF 1.84, PnL $45.07 — better |
| EUR_AUD | PF 2.42, PnL $75.32 | PF 1.29, PnL $26.28 — worse, MaxDD also 6.2%->9.9% |
| GBP_USD | inconclusive — see below | inconclusive |

NZD_USD clearly benefits from loosening; every other EMA-bounce pair gets
meaningfully worse. Confirms this needs to be a per-pair override, not a
global flip (a global flip would help NZD_USD but hurt 3 of the other 4
active pairs).

**GBP_USD row is uninformative, methodology issue caught mid-test**: my
sweep script called `run_backtest()` without passing GBP_USD's real
`strategy_breakout_retest` functions, so it silently ran the default
EMA-bounce strategy instead. Confirmed via `grep` that
`H4_GATE_BLOCKING`/`h4_gate_blocking_for` isn't referenced anywhere in
`strategy_breakout_retest.py` at all, so the real live GBP_USD strategy is
unaffected by this flag regardless — not worth re-running the test.

**Separate bug found while investigating this** (not fixed, flagged only):
the dashboard's "Backtest" button (`dashboard/app.py`'s standard backtest
callback) always uses `run_backtest()`'s default EMA-bounce strategy
functions — it never resolves `config.STRATEGY_OVERRIDE`, so backtesting
GBP_USD from the dashboard silently tests the wrong strategy. Doesn't
affect any change shipped today (nothing here relies on that button for
GBP_USD), but worth fixing separately.

## What changed
- `config.py`: new `H4_GATE_BLOCKING_PER_PAIR = {"NZD_USD": False}` dict +
  `h4_gate_blocking_for(pair)` resolver — same pattern as
  `CONFIRM_TF_PER_PAIR`/`TP_RR_PER_PAIR`.
- `engine/strategy_ema_cci_macd.py`: both `check_buy_signal`/
  `check_sell_signal`'s H4-gate check switched from
  `config.H4_GATE_BLOCKING` to `config.h4_gate_blocking_for(pair)`.
  `engine/strategy_trend_follow.py`'s two references left untouched — that
  strategy is shelved/backtest-only, never wired live.
- Verified end-to-end via the real CLI: `python -m backtest.runner --pair
  NZD_USD --bars 3500` -> WR=45%, PF=1.85, PnL=$45.42, trades=20 — matches
  the sweep's loosened-gate result exactly (tiny rounding only).
  `h4_gate_blocking_for("NZD_USD")` returns `False`,
  `h4_gate_blocking_for("USD_CAD")` returns `True`.
- All 252 tests pass.

## Review
Third live-trading-affecting change from this session's 3-item backlog,
same shape as the previous two: backtest first, only ship if it actually
improves the specific pair, use a per-pair override rather than a global
change when other pairs would be hurt by it.

---

# Fix: EUR_USD stale-signal duplication + pnl_pips never populated (2026-07-24)

## Fix 1: EUR_USD stale-signal duplication (root cause found)

**Root cause**: `tests/test_learning.py::test_record_skip_does_not_affect_pending`
called `record_skip(_signal())` with no `log_path` override — every test
suite run appended one EUR_USD row (the test fixture's hardcoded
entry=1.08/sl=1.078/tp1=1.082/score=80) directly into the real production
`data/signal_log.csv`. The module docstring even states "All CSV I/O uses
tmp_path so the real data/ directory is never touched" — this was the one
line that violated it. Every other `record_skip`/`record_close` call in
the file correctly passes `log_path=log` (a tmp_path). Confirmed: all 13
EUR_USD rows in the file matched the test fixture's exact fingerprint
(cci_at_signal=-120.5, pattern_name='bullish_pin_bar') — zero real EUR_USD
signals, which makes sense since EUR_USD isn't in `FOREX_PAIRS` or
`FOREX_WATCH` and was never reachable from the live scan loop.

**Fixed**: added the missing `tmp_path`/`log_path` to that one test.
Verified by running the full suite and confirming zero new EUR_USD rows
land in `data/signal_log.csv` afterward (previously exactly one per run).
Backed up (`data/signal_log.csv.bak_before_eurusd_cleanup`) and removed
the 13 pollution rows from the production file.

## Fix 2: pnl_pips never populated

Three separate write paths into `signal_log.csv`, two were broken:

1. **Live paper-trade closes** (`trade/paper_trader.py::_log_outcome`) —
   already correct, computes real pip distance from entry/exit. Not the
   source of the bug.
2. **Backtest-seeded rows** (`learning/pattern_learner.py::seed_from_backtest`,
   fed by `backtest/runner.py`'s `seed_rows`) — `pnl_pips` was hardcoded to
   `0`, and `entry_price`/`stop_loss`/`tp1`/`tp2`/`tp3` were hardcoded to
   `""`, even though every closed backtest trade already carries all of
   this. Fixed: `run_backtest()`'s `seed_rows` now includes real
   `entry_price`/`stop_loss`/`tp1-3`/`tp_level_hit`/`pnl_pips` (same
   formula as `paper_trader.py`'s live calc); `seed_from_backtest()` now
   reads them instead of hardcoding blanks.
3. **Shadow-resolved rows** (`learning/shadow_outcomes.py`, would_win/
   would_lose) — deliberately only ever determined outcome, never
   magnitude (module docstring: "answers 'would this setup have worked at
   all', not 'what would the exact P&L have been'"). Enhanced
   `_resolve_one()` to also return the pip distance to whichever level
   (TP1 or SL) was actually touched — same "first touch" simplification as
   the outcome itself, still not the full TP1/TP2/TP3 sequence, but a real
   number instead of a placeholder 0. `resolve_pending()` now writes it
   into the `pnl_pips` column alongside the outcome.

**New tests**: `tests/test_shadow_outcomes.py` (10 tests) — this module had
zero test coverage before this change; covers would_win/would_lose/expired
resolution, the SL-first same-candle tie convention, and end-to-end
`resolve_pending()` CSV writes, since I was changing its return contract
(`str | None` → `tuple[str | None, float | None]`).

**Data cleanup** (all backed up first):
- Backfilled `pnl_pips` for the 87 already-resolved would_win/would_lose
  rows directly from their stored entry/sl/tp1 (no need to re-fetch
  candles — the outcome was already determined, this just derives the
  matching magnitude).
- Found and fixed a related, previously-unknown bug while doing this:
  ~83 of the 87 pre-existing seeded win/loss rows (USD_CAD/USD_CHF) were
  near-exact triplicates — same `(pair, timestamp, direction)` 3× each
  with tiny floating-point differences in `pnl_dollar`, almost certainly
  from `seed_from_backtest` being run 3 times before the 2026-07-22
  determinism fix (see that fix's entry below — backtests weren't
  reproducible before it). Deduplicated to one row per unique
  `(pair, timestamp, direction)`, keeping the first occurrence — same
  underlying problem as the EUR_USD bug (inflated/non-independent ML
  training examples), different mechanism.
- Removed the 15 stale pre-fix USD_CAD seed rows (permanently missing
  price levels, un-backfillable) and regenerated them via
  `PatternLearner.seed_from_backtest(["USD_CAD"], ...)` using the fixed
  code — all 24 fresh rows have real price levels and non-zero
  `pnl_pips`. Left USD_CHF's old rows alone (not an active/watched pair,
  not worth the live OANDA fetch to re-seed).
- `data/signal_log.csv`: 192 -> 130 rows net (removed pollution +
  duplicates, added freshly-reseeded USD_CAD). Backups:
  `signal_log.csv.bak_before_eurusd_cleanup`,
  `signal_log.csv.bak_before_reseed`.

All 252 tests pass (was 242 — +10 new).

---

# Change: EUR_AUD given its own confirm-TF override, back to H1 (2026-07-23) — LIVE-AFFECTING

## Why
EUR_AUD was the one pair that declined under the global H4-confirm switch
below (PF 2.42->1.63, PnL $75.32->$33.87). User asked for a per-pair
override, on the explicit condition that it only ships if it actually
improves EUR_AUD's results.

## What changed
- `config.py`: new `CONFIRM_TF_PER_PAIR = {"EUR_AUD": "H1"}` dict (same
  pattern as `STRATEGY_OVERRIDE`/`BREAKEVEN_PER_PAIR`/`TP_RR_PER_PAIR`) and
  a `confirm_tf_for(pair)` resolver — `CONFIRM_TF_PER_PAIR` wins if set,
  else falls back to the global `TIMEFRAMES["confirm"]`.
- `backtest/runner.py`: `confirm_tf_ratio()` now takes an optional `pair`
  argument and resolves through `confirm_tf_for()` when given one; used in
  `run_walk_forward()` (already had `pair` in scope) and the CLI (which
  also had its own separate hardcoded `args.bars // 2` confirm-bar-count
  assumption — fixed to `args.bars // confirm_tf_ratio(args.pair)`).
- Every other confirm-TF call site switched from `config.TIMEFRAMES
  ["confirm"]` to `config.confirm_tf_for(pair)`: `engine/signal_engine.py`
  (live scan), `main.py` (live mid-bar diagnostic scan),
  `engine/wfo_optimizer.py` (both the weekly refit's fetch-count line and
  its `.tail()` slice — both now pass `pair` into `confirm_tf_ratio()` too),
  `dashboard/app.py` (both Walk-Forward and standard backtest modes share
  the same fixed `gran_h4`/`confirm_ratio` variables, now pair-aware).
- `engine/strategy_ema_cci_macd.py` and `engine/strategy_trend_follow.py`:
  the `get_best_emas(pair, config.TIMEFRAMES["confirm"], df_h4)` cache-key
  label (4 call sites) now uses `config.confirm_tf_for(pair)` too, so the
  EMA auto-fit log line correctly says "EUR_AUD H1" instead of a stale
  "EUR_AUD H4" — confirmed in the verification run below.
- `dashboard/panels.py`: the Signal Monitor's "H4:" column label and the
  "H4✓/H4✗/H4?" gate badge are now pair-aware (`config.confirm_tf_for(pair)`)
  so they don't silently go back to lying for whichever pair has an
  override — same mislabeling class this whole investigation started from.
- All 242 relevant tests pass (3 unrelated `TestAlpacaConnectorLive`
  failures are a live external API returning `internal server error` —
  confirmed transient by re-running, unrelated to any file touched here).

## Verification
Ran `python -m backtest.runner --pair EUR_AUD --bars 3500` through the real
CLI (not a manual test script) to prove the full per-pair resolver chain
works end-to-end: log line confirmed `EMA fit EUR_AUD H1` (not H4), and the
result — `WR=41% PF=2.42 PnL=$75.32 MaxDD=6.2%` — reproduces the original
pre-switch H1-confirm baseline exactly. This is a clear, backtest-confirmed
improvement over EUR_AUD being forced onto H4 (PF 1.63, PnL $33.87), so the
override ships per the user's stated condition. `confirm_tf_for("EUR_AUD")`
returns `"H1"`, `confirm_tf_for("USD_CAD")` returns `"H4"` — every other
active pair is unaffected.

## Review
EUR_AUD is now effectively back to its pre-2026-07-23 behavior while every
other active pair keeps the new H4 confirm. The resolver is intentionally
the same shape as the existing per-pair override dicts, so a future pair
needing its own confirm TF is a one-line config addition, not new plumbing.

---

# Change: confirm TF switched from H1 to real H4 (2026-07-23) — LIVE-AFFECTING

## Why
User asked (after noticing the Signal Monitor's "H4" column) whether a real
H4 confirm timeframe would work better than whatever it's currently using,
with instructions to test first and only switch if it actually improved
results. Investigation found `config.TIMEFRAMES["confirm"]` was set to
`"H1"`, not H4 — a leftover from an earlier retune (comment: "was H4 —
maintains 2:1 confirm:primary ratio"). Every "H4"-named variable/label in
the codebase (`h4_trend`, "C2 H4 aligned", the dashboard's "H4:" column)
had silently been reading H1 data since that change — a 2:1 primary:confirm
ratio, much tighter than the classic ~8:1 (M30→H4) multi-timeframe spacing.

Backtested real H4 vs the current H1 confirm across all 5 active pairs,
3500 M30 bars each (same primary data both ways, only the confirm-TF
dataset's granularity changed):

| Pair | H1 confirm (current) | H4 confirm (proposed) |
|---|---|---|
| USD_CAD | WR 39.3% PF 1.57 PnL $45.30 DD 5.2% (28 trades) | WR 45.8% PF 2.31 PnL $69.83 DD 3.2% (24 trades) |
| GBP_CAD | WR 47.4% PF 2.24 PnL $108.47 DD 4.2% (38 trades) | WR 64.3% PF 3.91 PnL $152.33 DD 3.7% (28 trades) |
| NZD_USD | WR 30.4% PF 1.17 PnL $10.96 DD 6.1% (23 trades) | WR 33.3% PF 1.25 PnL $15.31 DD 5.7% (21 trades) |
| EUR_AUD | WR 40.7% PF 2.42 PnL $75.32 DD 6.2% (27 trades) | WR 33.3% PF 1.63 PnL $33.87 DD 5.1% (24 trades) — declined |
| GBP_USD | WR 29.5% PF 1.01 PnL $1.45 DD 9.5% (44 trades) | WR 34.2% PF 1.32 PnL $32.64 DD 7.3% (38 trades) |

4 of 5 pairs improved, several substantially (GBP_CAD PF +75%, USD_CAD PF
+47%, GBP_USD PF +31%). EUR_AUD declined (PF -33%) — the same pair that was
the outlier for the 2026-07-22 TP R:R change too. Net clearly positive —
adopted globally. GBP_USD improved despite running `strategy_breakout_retest`
(whose own entry logic ignores confirm-TF content beyond a length check) —
its change comes entirely from `run_backtest()`'s own structure-score bonus
(BOS detection on the confirm-TF series), which every strategy shares
regardless of which one is active for a pair.

## What changed
- `config.py`: `TIMEFRAMES["confirm"]` "H1" -> "H4", with the rationale and
  backtest table in the inline comment.
- **Bug found and fixed while implementing**: three places hardcoded the old
  M30:confirm 2:1 bar-count ratio instead of deriving it from
  `config.TIMEFRAMES`. With a real 8:1 M30:H4 ratio, these would have
  either silently misaligned walk-forward windows or (for the dashboard's
  WFO tab) made the lowest allowed "Test" window size produce zero valid
  windows:
  - `backtest/runner.py`: added `confirm_tf_ratio()` (derives the ratio
    from `config.TIMEFRAMES` via a small `_TF_MINUTES` lookup) and used it
    in `run_walk_forward()`'s train/test confirm-TF window slicing
    (previously hardcoded `i // 2`).
  - `engine/wfo_optimizer.py`: both `run_all_pairs()`'s confirm-TF fetch
    count and `run()`'s `.tail()` slice were hardcoded to `train_bars // 2`
    — now use `confirm_tf_ratio()`. (`run()`'s weekly background refit
    wasn't actually going to crash — its `.tail()` call would've just
    silently returned fewer rows than intended — but it was quietly wrong.)
  - `dashboard/app.py`: the Walk-Forward tab's `confirm_ratio = 2 if
    is_forex else 4` now calls `confirm_tf_ratio()` for the forex path
    (crypto keeps its independent fixed 4:1 — untouched, unaffected by this
    change).
  - `dashboard/app.py`: `wf-test-input`'s `min` raised from 250 to 500 —
    at the new 8:1 ratio, a 250-bar test window only yields ~31 confirm
    bars, below the strategy's own 50-bar minimum, so every window would
    silently fail. 500 bars -> ~62 confirm bars, safely clears it. Inline
    comment explains why.
  - `backtest/runner.py`: `run_walk_forward()`'s "No valid windows
    produced" error now names the actual cause and a fix when it's an
    undersized test window, instead of a bare unexplained message.
- Verified end-to-end (not just unit tests): ran `run_walk_forward()`
  directly against real OANDA H4 data both with an undersized test window
  (confirmed the new error message fires correctly: *"test_bars=250 yields
  only ~31 confirm-TF bars per window — need >= 50; try test_bars >= 400"*)
  and with the real UI-default window size (750 bars — produced 2 valid
  windows with real trade counts, no error).
- All 242 existing tests pass unmodified.

## Not done (flagged, not actioned)
EUR_AUD's decline mirrors the exact pattern from the 2026-07-22 TP R:R
change, where a per-pair override (`TP_RR_PER_PAIR`) was eventually built
for it. The same shape of fix would apply here (a `CONFIRM_TF_PER_PAIR`
override), but it's a larger change than the TP one — it would touch the
live candle-fetch call in `engine/signal_engine.py`, `main.py`'s live
diagnostic scan, and the backtest CLI, not just a single scoring function.
Not built without being asked first.

## Review
Net-positive, backtest-confirmed change adopted per explicit instruction
("only switch if there is an improvement, then go ahead and make the
changes"). The three hardcoded-ratio fixes were not part of the original
ask but were necessary — without them the WFO/walk-forward tooling would
have been silently broken or produced misaligned windows the next time
anyone used it. Caught by actually running `run_walk_forward()` against
real data rather than trusting the unit tests alone (none of them exercise
this window-alignment math with real bar counts).

---

# Fix: double-click indicator hide reverted itself on every chart refresh (2026-07-22)

## Why
User reported: double-clicking the chart background to hide CCI/MACD panes
worked, but the panes silently reappeared "whenever the chart refreshes" and
also seemingly when drawing an element or an H-line.

## Root cause
`_toggleIndicatorPanes()` (`chart.js:4868`, the double-click handler) flips
`_indSettings.cci.visible`/`.macd.visible` in memory only — it never calls
`_saveIndSettings()`. `dashboard/app.py:1208`'s `interval-60s` timer pushes
fresh candle data to the chart every 60 seconds regardless of user action,
which runs `chart.js`'s `load()`, which unconditionally called
`_apexApplyIndSettings()` → `_loadIndSettings()` (`chart.js:5661-5662`) —
re-reading indicator settings from `localStorage` and clobbering the
in-memory hide every single time, even though the pair/TF never changed.
The "drawing an element" trigger isn't real — the same 60s timer just
happened to fire in the background while the user was mid-draw.

## What changed
- `dashboard/assets/chart.js:5661`: `_apexApplyIndSettings()` now only
  calls `_loadIndSettings()` when `_indSettings` is still unpopulated
  (first load). A genuine pair/TF switch already reloads settings fresh via
  `init()`'s own `_loadIndSettings()` call (`chart.js:3629`, which also
  resets `_indPanesHidden` — see comment at `chart.js:3573`), so this
  doesn't affect that path; it only stops the redundant same-pair reload
  that was wiping the double-click toggle.
- No Python changes. `node --check` confirms no syntax errors; no automated
  test coverage exists for dashboard JS (frontend-only, browser-driven
  logic) — needs a manual visual pass: double-click to hide CCI/MACD, wait
  60+ seconds for the live-data timer, confirm panes stay hidden.

## Review
One-line guard, no behavior change for pair/TF switches (still reloads
fresh, as intended) or for the indicator-settings-panel flows (they save
and re-apply directly, never depending on this reload). Same class of bug
as [[bugs_indicator_settings_wiring]] — a visibility flag getting silently
re-synced from a stale source of truth.

---

# Change: EUR_AUD gets its own TP R:R override — 1.0R/3.0R/4.5R (2026-07-22) — LIVE-AFFECTING

## Why
EUR_AUD was the one pair (of 5) that got worse under the global TP change
below (PF 2.09→1.83). User asked to test EUR_AUD specifically to see if it
needed its own profile. Ran a 7-way sweep on real OANDA data, 3500 bars:

| Config | WR | PF | PnL | MaxDD | Trades |
|---|---|---|---|---|---|
| Current global (1.5/2.5/3.5) | 42.4% | 1.83 | $69.27 | 7.6% | 33 |
| Old global (1.0/2.5/4.0) | 42.4% | 2.09 | $71.99 | 7.1% | 33 |
| TP1→1.0, TP3 stays 3.5 | 42.4% | 2.09 | $71.99 | 7.1% | 33 |
| TP1 1.5, TP3→4.0 | 42.4% | 1.83 | $69.27 | 7.6% | 33 |
| **Wider all (1.0/3.0/4.5)** | 42.9% | **2.63** | **$88.49** | **6.2%** | 28 |
| Tighter all (0.75/2.0/3.0) | 43.2% | 1.78 | $52.48 | 5.3% | 37 |
| TP1 0.75, rest unchanged | 42.4% | 2.54 | $79.20 | 5.3% | 33 |

TP1 at 1.5R was specifically what hurt this pair — any config with TP1=1.0
recovers the exact pre-change baseline (byte-identical trades, confirming
TP3's 4.0→3.5 change never mattered here). 1.0R/3.0R/4.5R beat every other
option on every metric, including the old global default. Adopted as a
per-pair override.

## What changed
- `risk/risk_manager.py::get_tp_levels()`: gained a `pair` parameter,
  looks up `config.TP_RR_PER_PAIR.get(pair, (1.5, 2.5, 3.5))` for the
  R-multiples instead of hardcoding them. Docstring updated.
- `config.py`: new `TP_RR_PER_PAIR = {"EUR_AUD": (1.0, 3.0, 4.5)}` dict,
  same pattern as `STRATEGY_OVERRIDE`/`BREAKEVEN_PER_PAIR`.
- Call sites updated to pass `pair` (all three already had it in scope,
  confirmed by reading each before changing): `backtest/runner.py:328`,
  `engine/signal_engine.py:272` (also covers the live trade path via the
  reused `_watch_tp` variable at line 372), `dashboard/app.py:1963`.
- `trade/trade_manager.py` imports `get_tp_levels` but never calls it
  directly — nothing to update there.
- All 242 existing tests pass unmodified — they call `get_tp_levels()`
  without a `pair` arg, which defaults to `""`, which isn't in
  `TP_RR_PER_PAIR`, so they keep getting the global 1.5/2.5/3.5 default.
- Manually verified end-to-end: `get_tp_levels(1.00, 0.99, "long",
  "EUR_AUD")` → 1.01/1.03/1.045 (1.0/3.0/4.5R), `get_tp_levels(1.00, 0.99,
  "long", "USD_CAD")` → 1.015/1.025/1.035 (global default), confirming the
  override only fires for the listed pair.

## Review
Small, additive, backward-compatible change — one new config dict, one new
optional function parameter, three call sites updated to pass an argument
they already had in scope. No behavior change for any pair except EUR_AUD.

---

# Change: TP R:R updated to 1.5R / 2.5R / 3.5R (2026-07-22) — LIVE-AFFECTING

## Why
User asked to test and, if it improved results, adopt TP1=1.5R / TP2=2.5R /
TP3=3.5R (was 1.0R/2.5R/4.0R). Backtested on real OANDA data across all 5
active pairs, 3500 bars each (using the now-fixed deterministic
`run_backtest()` — see the entry below this one for why that fix mattered
here specifically: the *previous* TP sweep earlier today ran before that
fix existed, so this comparison was redone clean rather than trusted from
memory):

| Pair | Current PF/PnL | Requested (1.5/2.5/3.5) PF/PnL |
|---|---|---|
| USD_CAD | 1.86 / $54.89 | 1.81 / $57.92 |
| GBP_CAD | 2.30 / $103.53 | 2.29 / $115.30 |
| NZD_USD | 1.20 / $11.46 | 1.21 / $13.72 |
| EUR_AUD | 2.09 / $71.99 | 1.83 / $69.27 — the one pair that got worse |
| GBP_USD | 2.26 / $47.93 | 2.56 / $58.41 — clearly better |

4 of 5 pairs improved on PnL; profit factor improved or held flat on 3 of
them. Only EUR_AUD declined (PF 2.09→1.83; PnL itself barely moved,
-$2.72). Every pair's max drawdown ticked up slightly (a later TP1 means
more open-risk time before the first 40% locks in), but not enough to
offset the gains. Net positive across the roster — adopted.

## What changed
- `risk/risk_manager.py::get_tp_levels()`: TP1/TP2/TP3 multiples changed
  from 1.0R/2.5R/4.0R to 1.5R/2.5R/3.5R. Docstring updated with the
  backtest rationale.
- `tests/test_trade.py`: one test
  (`TestPaperTraderATRTrailing::test_atr_trailing_only_advances_never_retreats`)
  had a hardcoded candle high (1.0870) that, for the fixture's entry/SL
  (1.0800/1.0780), exactly coincided with the *new* TP3 level — same
  arithmetic coincidence the class's own comment already warned about for
  the *old* TP3 (1.0880), just landing in a different spot now. Adjusted
  the price to 1.0868 (stays clear of the new 1.0870 TP3) and updated the
  two stale comments that cited the old TP3 price (1.0880/1.0720 →
  1.0870/1.0730). No other tests were affected — the shared `_tp()` test
  helper calls the real `get_tp_levels()`, so everything else picked up
  the new levels automatically.
- Dashboard R:R displays (`dashboard/panels.py`) compute R:R live from
  actual trade data, not hardcoded to the old ratios — nothing to update
  there.

## Review
242/242 tests pass. This is a **live-trading-affecting change** — every
new trade opened by paper or live mode from now on uses the new TP levels;
in-progress trades opened before this change keep whatever TP levels they
were assigned at open time (stored per-trade, not recalculated).
**How to notice a recurrence/regression**: watch realised PF/PnL on
paper trades over the next couple of weeks and compare against this
table's backtested expectation per pair; if EUR_AUD's live results
degrade further than this backtest suggested, that's the pair to
reconsider first.

---

# Fix: backtester never computed real d_trend + found/fixed a bigger determinism bug (2026-07-22)

## Why
Follow-up to the research pass below: user asked to fix the flagged
infrastructure gap so D-trend-alignment (and any other daily-timeframe idea)
becomes actually testable. `backtest.runner.run_backtest()` only ever
fetched H1 and H4 candles, so `d_trend` in every recorded signal silently
stayed at its `"neutral"` fallback default forever.

## What changed

**1. `backtest/runner.py::run_backtest()` gained an optional `df_d` param.**
When supplied, `d_trend` is computed the same way `engine/signal_engine.py`
does live — close vs EMA20 on the last *fully closed* daily candle (looked
up via `Series.asof(bar_time.normalize() - 1 day)`, so it's impossible for
a bar to see a same-day-or-later daily candle — no lookahead). When omitted,
behavior is byte-identical to before (`d_trend` stays `"neutral"`) — fully
backward compatible, confirmed via test.
- `_bt_sig_features()` now takes a `d_trend` param instead of hardcoding the
  literal.
- New helpers `_prep_daily_trend()` / `_d_trend_at()` do the precompute-once
  + per-bar lookup.
- CLI (`backtest/runner.py`'s `__main__` block) and the dashboard's
  "Standard backtest mode" callback (`dashboard/app.py`) now fetch 200 Daily
  candles and pass them through — best-effort, a failed fetch just leaves
  `d_trend` at `"neutral"` (it's a logged feature, never a trading gate, so
  there's nothing to hard-fail on). `run_walk_forward()` / WFO's grid search
  / `pattern_learner.py`'s seeding call were deliberately left unchanged —
  none of them need `d_trend`, and `df_d` being optional means they're
  unaffected either way.

**2. Found a second, more consequential bug while verifying the first fix
was behavior-neutral**: called `run_backtest()` twice in one process with
byte-identical arguments (same pair, same candle data) and got **different
trade counts and different total PnL both times** — nothing to do with
`d_trend`. Root cause: `engine/strategy_ema_cci_macd.py`'s module-level EMA
auto-fit cache (`_cache`) persists across calls and isn't scoped to a single
backtest run, so a second call for the same pair can silently reuse an EMA
period fit computed against a different bar range than its own current
progression. Confirmed by calling `clear_cache()` between the two runs —
made them identical.

This matters beyond just my testing: **`run_walk_forward()` and WFO's grid
search both call `run_backtest()` many times for the same pair, by design**
(that's the whole point of a sweep/grid search) — every one of those calls
after the first could have been silently contaminated by leftover cache
state from the previous call, for as long as this bug has existed. Not
saying any specific past WFO result is wrong — no way to retroactively
verify that — but the mechanism for it being wrong was real and live in
every multi-call backtest comparison, including several run this session
(ATR trailing sweep, breakeven sweep, TP R:R sweep).

**Fix**: `run_backtest()` now calls `clear_cache()` on all three strategy
modules (`strategy_ema_cci_macd`, `strategy_breakout_retest`,
`strategy_trend_follow` — the latter two only clear diagnostics dicts,
harmless either way, but included for consistency) as the very first thing
it does. This guarantees every call starts from clean state regardless of
what ran before it in the same process — callers no longer need to
remember to clear anything themselves.

## Tests added
`tests/test_backtest_dtrend_and_determinism.py` (7 new tests):
- Repeated identical calls now produce identical results (the actual bug).
- A different pair backtested in between doesn't contaminate the original.
- `d_trend` stays `"neutral"` without `df_d` (backward compat).
- `d_trend` correctly resolves `"bull"`/`"bear"` with a clearly-trending
  synthetic `df_d`.
- `df_d` never changes which trades are taken or their PnL — confirmed
  identical trade count/PnL with and without it, only the logged `d_trend`
  field differs.
- Too-short `df_d` (<20 bars) falls back to `"neutral"` without crashing.

## Review
242 tests pass (235 → 242). Re-verified end-to-end against real OANDA data
after the determinism fix: `d_trend` now shows a genuine bull/bear mix
(14/13 on a 27-trade USD_CAD run) instead of 100% neutral, trade
count/PnL are byte-identical with vs. without `df_d` (confirms d_trend
truly doesn't influence trading decisions, only what gets logged), and
repeated calls with identical arguments now return identical results.
**How to notice a recurrence**: if a future backtest sweep script shows
suspicious result drift between configs that shouldn't affect trade
timing, check whether `run_backtest()` is still clearing caches at entry
(`git blame`/grep for the three `_clear_*_cache()` calls near the top of
the function) before assuming the strategy change itself is responsible.

## Follow-up (same day): D-trend alignment hypothesis tested — rejected

Added an `require_d_trend_alignment` param to `run_backtest()` (off by
default, EXPERIMENTAL, not wired into any live strategy file) — when on,
a signal only opens if `d_trend` agrees with its direction. Backtested on
all 5 active pairs, 3500 bars each, gate off vs on:

| Pair | Off: PF / PnL | On: PF / PnL |
|---|---|---|
| USD_CAD | 1.87 / $55.42 | 2.50 / $64.36 — better |
| GBP_CAD | 2.24 / $98.62 | 1.91 / $47.83 — worse |
| NZD_USD | 1.21 / $12.36 | 0.88 / **-$5.82** — flips to a net loser |
| EUR_AUD | 2.09 / $71.99 | 1.06 / $4.31 — PnL down 94% |
| GBP_USD | 2.26 / $47.93 | 1.17 / $4.48 — PnL down 91%, trades nearly halved |

Only USD_CAD improved; the other four got meaningfully worse, two
severely. Same shape of result as the ATR-trailing and breakeven
rejections: an intuitively appealing extra filter reduces trade count
without a matching quality improvement — Daily trend often lags or
diverges from the H4 trend already required, so demanding both to agree
mostly just removes trades that were fine. **Rejected — `require_d_trend_alignment` stays off, not adopted anywhere.** One pair improving isn't
enough to justify it given the broader pattern (same reasoning already
applied to keeping GBP_CAD off breakout-retest despite it testing well on
other pairs).

## Not done here (out of scope for this pass)
The EUR_USD stale-signal-duplication bug flagged in the research pass below
is still open (unrelated file, different root cause, not requested this
round).

---

# Strategy improvement research pass (2026-07-22) — no change adopted, real findings below

## Why
User asked directly: given fast analysis and full repo access, why hasn't the
strategy's win rate been improved — proactively research genuinely new
angles, not just react to specific requests like the ATR-trailing/breakeven
work earlier.

## What was checked

**1. Shadow signal-log mining (`data/signal_log.csv`, 178 rows, 120 after
exact-dup removal).** Found a real bug in the process: **EUR_USD has 10 rows
spanning 2026-06-30→2026-07-22 with the exact identical entry/SL/TP prices
(1.08000/1.078/1.082)** — a stale/cached signal getting re-logged as "new"
every scan for three weeks, all tagged `would_win`, inflating that slice to a
fake 100% win rate. EUR_USD isn't even an active or watched pair anymore, so
this doesn't affect live trading, but it silently corrupts this file for
analysis purposes. Root cause not yet dug into — likely the same class of
issue as [[bugs_shadow_outcome_duplication]] (a shadow-tracked setup that
never resolves keeps re-logging). Worth a follow-up if this file is going to
keep being used for analysis.

Separately found: **`pnl_pips` is 0.0 for effectively every row** —
49/49 `would_win`, 38/38 `would_lose`, and most real `win`/`loss` rows too.
Only the categorical outcome label is usable; magnitude-of-outcome analysis
isn't possible from this file as currently populated.

After removing the contaminated EUR_USD rows and restricting to the 5
currently active pairs, two patterns that looked promising on the raw data
(NZD_USD 18.8% WR "underperforming" its 36% promotion backtest; short-side
54.5% WR vs long-side 41.5%) **both evaporated once filtered to signals that
actually cleared each pair's own WFO min_score (55)** — the qualifying
sample shrank to 27 rows total across all 5 pairs over 2.5 months, direction
skew reversed, and NZD_USD's real qualifying sample was just 3 signals.
**Conclusion: there isn't enough live signal volume yet for reliable
pattern-mining from logs — real backtesting against price history has far
more statistical power right now.**

**2. TP R:R multiple sweep** (`risk_manager.get_tp_levels`, currently
1.0R/2.5R/4.0R — never touched by WFO's grid search, only ever hand-tuned).
Backtested on USD_CAD/GBP_USD/GBP_CAD, 3500 bars each, same candle data
across all variants per pair:

| Config | USD_CAD PF/PnL | GBP_USD PF/PnL | GBP_CAD PF/PnL |
|---|---|---|---|
| current (1.0/2.5/4.0) | 1.82 / $52.98 | 2.26 / $47.93 | 2.11 / $93.42 |
| old pre-tighten (1.5/3.0/5.0) | 1.32 / $24.64 | 1.64 / $22.73 | 1.88 / $65.90 |
| tighter (0.75/2.0/3.0) | 1.54 / $43.32 | 2.24 / $38.12 | 2.02 / $69.29 |
| TP1 later (1.3/2.5/4.0) | 1.73 / $52.40 | 2.44 / $54.43 | 2.00 / $88.74 |

Confirms the earlier hand-tightening from 1.5/3.0/5.0 to the current values
was a genuine improvement (this is the first time it's been backtest-
validated rather than just asserted in a code comment). Neither new variant
tested (tighter, later-TP1) beats current cleanly across all three pairs —
TP1-later wins on GBP_USD, loses on GBP_CAD, roughly flat on USD_CAD.
**No change adopted — current R:R levels hold up.**

**3. D-trend alignment as a signal gate** (`d_trend` is computed and logged
today but never gates anything, unlike `h4_trend` which is a hard gate).
Pulled real backtest trade logs (122 trades across 4 pairs) to check —
**found every single trade shows `d_trend='neutral'`**, which is itself the
finding: `backtest.runner.run_backtest()` only ever fetches H1 and H4 candle
data, never Daily, so `d_trend` inside the backtest silently stays at its
uninitialized default the whole time (matches `engine/signal_engine.py:110`'s
`h4_trend = d_trend = "neutral"` fallback). This means **this hypothesis is
currently untestable** without first threading real Daily-candle data through
the backtester — a real infrastructure gap, not a dead-end finding. Confirmed
`d_trend` isn't used as a decision input anywhere in
`engine/strategy_ema_cci_macd.py` today (display/logging only), so this gap
doesn't invalidate the TP R:R results above — those don't depend on d_trend.

## Recommendation
No strategy or config change made this pass — everything tested either
confirmed the current setup is already good (TP R:R) or ruled itself out as
untestable/unreliable given current data (D-trend, shadow-log mining). Two
concrete, scoped follow-ups worth doing, neither started here:
1. Root-cause the EUR_USD stale-signal duplication (data-integrity bug,
   doesn't affect live trading but corrupts analysis).
2. Wire real Daily-candle fetching into `backtest.runner.run_backtest()` so
   D-trend-alignment (and any other daily-timeframe idea) can actually be
   backtested — currently impossible to test honestly.

## Review
All numbers from real `backtest.runner.run_backtest()` runs against live
OANDA candle data (same methodology as the ATR-trailing and breakeven
validations), not estimates. No files changed — this was a research-only
pass; `config.py` and strategy files are untouched.

---



## Why
After trying the `#9c9c9c` light theme below, user found it "doesn't seem
compatible with the other chart settings" and asked to revert to the
previous dark theme — but with background `#0f0f0f` instead of the original
`#0d1117` — and to remove the chart's grid lines entirely.

## What changed
- Reverted all 7 files touched by the light-theme work (`dashboard/assets/chart.js`,
  `style.css`, `indicators.css`, `dashboard/app.py`, `dashboard/panels.py`,
  `alerts/visual_alert.py`, `dashboard/chart_builder_twlc.py`) back to the
  original dark palette, with the page/chart background at `#0f0f0f` instead
  of the original `#0d1117`. Used this session's own original grep output
  (captured before any edits) as ground truth to correctly restore the two
  near-duplicate pairs that don't reverse cleanly with a single find/replace:
  `#58a6ff` vs `#38b6ff` (both blue, used at different specific lines in
  `chart.js`/`indicators.css`) and `#7d8590` vs `#8b949e` (both muted gray,
  same issue in `chart.js`). A blind single-value revert would have gotten
  a handful of these wrong.
- `chart.js`'s chart init: `grid: { vertLines, horzLines }` changed from a
  color (`#21262d`) to `visible: false` on both — removes grid lines
  entirely rather than just recoloring them.
- **Kept, not reverted**: black candle borders (`borderUpColor`/
  `borderDownColor: '#000000'`) and the white-drawing-tool-fill alpha-floor
  fix in `_hexToRgba()` — user only asked to revert the background/palette
  and remove grid lines, not these two separate fixes from earlier the same
  day.

## Review
`node --check` clean on `chart.js`; `python -m py_compile` clean on all
touched `.py` files. Full test suite: 235/235 pass (no Python trade-logic
files touched — this is dashboard-only). Verified visually end-to-end with
Playwright against a standalone instance on port 8051 (never touched the
live session on 8050): dark background confirmed at the new `#0f0f0f`, no
grid lines visible on chart or indicator panes, candle borders still visible,
settings modal and indicator panel both correctly dark again.

---

# Dashboard theme change (#9c9c9c background) + breakeven+10 validation (2026-07-21)

## Why
User asked for two things: (1) a lighter dashboard background (#9c9c9c) to ease
eye strain, black candle borders, and a fix for drawing-tool white elements
rendering gray; (2) more active trade management — specifically enabling the
ATR trailing stop and bumping breakeven-at-TP1 from 3 to 10 pips, since the
current "set and forget until TP/SL" behavior felt too passive. Full plan at
`C:\Users\deeja.000\.claude\plans\i-wanted-a-bot-reactive-seahorse.md`.

## Part 1: Dashboard theme (implemented)
- Swept every hardcoded hex literal across `dashboard/assets/chart.js`,
  `dashboard/assets/style.css`, `dashboard/assets/indicators.css`,
  `dashboard/app.py`, `dashboard/panels.py`, `alerts/visual_alert.py`, and
  `dashboard/chart_builder_twlc.py` to a new light-gray palette (bg `#9c9c9c`,
  panels `#b5b5b5`/`#c4c4c4`, text `#12161c`, muted `#454b52`, accent blue
  `#0b5fa8`, bull-text-green `#0f8a4d`). Bear red and gold were already
  contrast-safe and kept unchanged. `dashboard/chart_builder.py` left alone —
  confirmed dead code, not imported anywhere.
- Deliberately did **not** sweep two categories: (a) the drawing-tool preset
  color swatches (`chart.js`'s `_SETTINGS_COLORS`/`_SWATCH_COLORS`,
  `app.py`'s `_DRAW_COLORS`, `panels.py`'s per-pair chart-line palette) — these
  are user-facing content palettes, not chrome, and must stay vivid regardless
  of theme; (b) badges with their own independently-dark background
  (`_POS_BTN_COLORS` long/short position chips, P&L floating tooltips) — text
  stays vivid since it's bright-on-dark by design, unrelated to page theme.
  Caught and fixed one regression where the initial mechanical sweep had
  darkened green/blue inside `app.py`'s `_DRAW_COLORS` and `_POS_BTN_COLORS`
  before this distinction was applied.
- Also fixed several knock-on contrast issues the background swap alone would
  have silently broken: the chart crosshair line and several translucent
  white UI accents (entry-price line, selection highlights, dividers, active
  color-swatch ring) were tuned for the old dark background and would have
  become nearly invisible on the new light one — flipped their polarity.
- `chart.js:3564` — candle `borderUpColor`/`borderDownColor` changed from
  matching the body color to `#000000` (was previously no visible border).
- White-drawing-tool bug: traced to `_hexToRgba()` (`chart.js:151-157`) — at
  the 4-10% alpha used for translucent box fills, an achromatic color has no
  hue to read through the blend and just looks like a faint shade of the
  background. Added an alpha floor (0.20) specifically for low-saturation
  colors; strokes/text were already rendering correctly at full opacity.
  Note: the bug is only reachable via the post-draw settings-popup color
  picker — a pre-draw color picker exists in the code (`_apexSetDrawColor`)
  but is dead/unwired; left as-is per user's choice not to wire it up now.

### Verification
Ran a standalone second dashboard instance on port 8051 (`dashboard.app.app`
imported directly), never touching the live session on port 8050. Used
Playwright to screenshot the main dashboard, the indicator settings panel, and
the drawing-tool settings modal, and to interactively draw a box, set its
color to white via the settings popup, and confirm the computed fill color
(`rgba(255,255,255,0.2)`) reads visibly white rather than gray. All panels
legible, candle borders visible, long/short position badges retain strong
bright-on-dark contrast.

## Part 2: Trade management — validated and rejected, no code changed
- **ATR trailing stop**: `tasks/todo.md`'s own prior entry (below) already
  backtested this on 2026-07-20 — 10 multiplier/period combinations, every
  one worse than baseline on USD_CAD. Per user's decision this round, did not
  revisit; skipped entirely rather than build a volatility-tiered variant of
  a mechanism already shown not to work here.
- **Breakeven-at-TP1, 3→10 pips**: `config.py`'s existing comment claimed
  this was "validated by backtest" as off, but no prior session record of
  that test could be found (unlike the ATR entry, which has full numbers).
  Ran the same style of validation directly: `backtest.runner.run_backtest()`
  on USD_CAD and GBP_USD (3500 M30 bars each, identical candle data across
  all three config states per pair):

  | | USD_CAD (WR/PF/PnL/MaxDD/trades) | GBP_USD (WR/PF/PnL/MaxDD/trades) |
  |---|---|---|
  | BE off (current default) | 44.4% / 1.80 / $51.76 / 3.5% / 27 | 52.9% / 2.26 / $47.93 / 3.1% / 17 |
  | BE on, buffer=3 | 58.3% / 1.49 / $39.29 / 4.2% / 36 | 55.6% / 1.46 / $19.34 / 4.8% / 18 |
  | BE on, buffer=10 (requested) | 54.5% / 1.18 / $18.47 / 4.3% / 44 | 52.6% / 1.29 / $13.59 / 5.9% / 19 |

  Enabling breakeven raises win rate on both pairs (more trades convert to
  small wins) but lowers profit factor and total P&L on both — and the
  requested 10-pip buffer is worse than the already-rejected 3-pip version on
  every metric, not better. The existing "validated off" config comment
  checks out. Presented this to the user; they chose not to enable it.
- **No files changed for Part 2** — `config.py`'s `BREAKEVEN_ENABLED`,
  `BREAKEVEN_BUFFER_PIPS`, and `ATR_TRAILING_ENABLED` remain exactly as they
  were before this session (confirmed via `git diff`).
- **In-progress-trade persistence** (user's hard requirement going into this):
  confirmed already solid — `TradeManager`/`PaperTrader` both persist to disk
  on every mutation (fixed in an earlier session, see the "TradeManager had no
  disk persistence" entry below), and since no trade-state schema changed
  this round, there was nothing to re-verify for backward compatibility.

## Review
Part 1 (theme) touched 7 dashboard/alert files, JS-syntax-checked
(`node --check`) and Python-compile-checked (`python -m py_compile`) after
every file, then visually verified end-to-end with Playwright against an
isolated standalone instance. Part 2 made no code changes — the two requested
trade-management changes were both checked against this bot's own historical
and freshly-run backtest data and found to underperform the current
(validated) defaults, so nothing was enabled. No regressions possible from
Part 2 since nothing was touched; `git diff` confirms `config.py` and all
trade-management files are unchanged by this session.

**How to notice a recurrence / follow-up**: if breakeven or ATR trailing is
revisited later, don't re-litigate the same fixed-multiplier/fixed-buffer
mechanism — both prior findings (this entry and the ATR entry below) point at
the same root cause: a static cushion that helps win rate but gives back more
open profit than it saves. A genuinely different mechanism (e.g. scaling the
buffer/multiplier by measured volatility, or partial-size-dependent trailing)
would be the next thing worth testing, not another fixed-number sweep.

---

# Todo: Validate ATR-adaptive trailing stop on USD_CAD via backtest

## Why
ATR trailing (config.ATR_TRAILING_ENABLED, added earlier today) is off by
default with untested numbers (multiplier=2.0, period=14). User asked to
validate before enabling anywhere real, starting with USD_CAD — the
strongest-performing active pair (46% WR, PF=2.08, WFO-tuned 55.6% WR/30d,
per config.py comments).

## Discovery before starting
`backtest/runner.py::run_backtest()` calls `pt.update(pair, high, low, close)`
every bar but **never computes or passes `atr_value`**. `PaperTrader.update()`
defaults `atr_value=None`, and ATR trailing only engages when `atr_value` is
truthy — so as it stands today, the backtester cannot exercise ATR trailing
at all, even with the flag on. This has to be wired first or the "test" would
silently be a no-op.

## Plan
- [x] Add ATR computation to `run_backtest()`'s per-bar loop (using
      `engine.indicators.atr()` on `slice_h1`, period=`config.ATR_TRAILING_PERIOD`)
      and pass it into `pt.update(...)`. Only compute when
      `config.ATR_TRAILING_ENABLED` is True, matching the zero-cost-when-off
      convention already used in `main.py::_tick_paper_trades()`. **Found
      already done** (2026-07-20) — this wiring was sitting as an uncommitted
      change in the working tree from before this session; the discovery step
      was complete but the actual validation below had never been run.
- [x] Run baseline: USD_CAD, `ATR_TRAILING_ENABLED=False` (today's default),
      3500 M30 bars (matching what config.py's existing pair-roster comment
      numbers were measured with) — **WR=50.0% PF=2.05 PnL=$67.09 MaxDD=3.4%
      trades=26 finalBal=$567.09**, this is the reference to beat.
- [x] Temporarily flip `ATR_TRAILING_ENABLED=True` in-process (not committed)
      and re-run the identical backtest (same fetched candle data, both runs)
      on USD_CAD — **WR=45.8% PF=1.75 PnL=$46.06 MaxDD=5.0% trades=24
      finalBal=$546.06** at the untested defaults (multiplier=2.0, period=14).
- [x] Compare the two runs side by side — ATR trailing is worse on every
      single metric: WR -4.2pp, PF -0.30, PnL -$21.03, MaxDD +1.6pp, and 2
      fewer trades taken.
- [x] Went further than the plan asked, since the single-config result was
      unambiguous enough to warrant checking whether it was just bad defaults:
      swept multiplier ∈ {1.0, 1.5, 2.0, 2.5, 3.0} × period ∈ {14, 21}, same
      candle data. **Every one of the 10 combinations underperforms the
      baseline on PF and PnL** — the best case (mult=1.0, period=14: WR=50.0%
      PF=1.93 PnL=$58.87 MaxDD=3.3%) still loses to baseline PF (2.05) and
      PnL ($67.09). Tighter multipliers trend better than wider ones, but
      none cross back over baseline. This isn't a bad-parameter-choice
      problem — ATR trailing structurally doesn't suit USD_CAD's current
      edge (a mean-reversion EMA-bounce setup where the existing fixed
      `TRAILING_STOP_PIPS=15` combined with the TP1/TP2/TP3 partial-close
      ladder already captures the trade well; a wider/adaptive trail mostly
      just gives back more open profit before closing).
- [x] Reverted the temporary in-process config flip — `config.py` itself was
      never edited (`ATR_TRAILING_ENABLED` still reads `False` in the file);
      confirmed via `git diff` showing no change to that line. No
      `ATR_TRAILING_PER_PAIR` proposed — results don't support enabling this
      anywhere, not just globally.
- [x] Recommendation: **reject**. Leave `ATR_TRAILING_ENABLED=False`. Don't
      revisit without a genuinely different trailing mechanism (e.g.
      volatility-scaled TP distances instead of SL trail distance) — this
      isn't a tuning problem, the whole approach underperforms here.

## Review
Backtested on real OANDA USD_CAD data (3500 M30 bars, 2026-04-08 →
2026-07-20), using the codebase's own `backtest.runner.run_backtest()` so
the result reflects the exact same signal/scoring/trade-management path the
live bot uses — not a simplified proxy. 11 total backtest runs (1 baseline +
10 ATR configs), all on identical candle data for a clean A/B/…/K
comparison. `ATR_TRAILING_ENABLED` stays `False`; no code or config changed
by this task. Full Python test suite (217 tests) still passes.

---

# Todo: Fix stuck Signal Quality metrics (4% trigger rate, avg score 10.9, C4 bottleneck)

## Why
Dashboard's Signal Quality panel looked flat across two Sunday WFO cycles.
Investigation found the metrics were actually *declining* week over week
(avg score 10.9 all-time → 8.9 last 2 weeks → 7.4 last week), traced to a
merge-order bug that silently discarded WFO's tuned CCI threshold on every
pair, a WFO job that rarely finishes all 4 pairs before a scheduler-freeze
kills it, and a dead ML score-boost code path. Full root-cause writeup in
the approved plan: `C:\Users\deeja.000\.claude\plans\i-am-not-happy-valiant-map.md`.

## Plan
- [x] Fix `AdaptiveParams.get()` (`engine/adaptive_params.py`) to return `{}`
      instead of `dict(BASE_PARAMS)` for a pair with no saved fit — this was
      silently overwriting WFO's `cci_threshold` in the
      `{**_wfo, **_ap}` merge in `engine/signal_engine.py`.
- [x] Added `tests/test_adaptive_params.py` (4 tests) covering the fix.
- [x] Sorted `_wfo_job()`'s pair list by `fitted_at` ascending in `main.py`
      so stale/never-fit pairs (GBP_CAD had never been fit) run first and
      survive a mid-run scheduler freeze more often.
- [x] Added `config.ML_SCORE_BOOST_ENABLED` (default `False`).
- [x] Wired a real `ml_win_prob` into `engine/signal_engine.py::run()`
      *before* `score_signal()` is called (previously only ever computed
      post-score, so the score-boost branch in `confluence_scorer.py` was
      dead code — it could only ever be used for the post-hoc block gate).
      Gated behind the new flag; falls back to the pre-existing `None`
      behavior when off.
- [x] Updated `main.py::_process_pair` to reuse `signal["ml_win_prob"]` when
      signal_engine already computed it, instead of predicting twice. Also
      fixed a pre-existing bug in the fallback path: the feature dict built
      `confluence_score` from `signal.get("confluence_score")`, but the
      signal dict stores that value under the key `"score"` — it was always
      silently sending `0` to the model. Fixed in the fallback branch, which
      is what's actually live today (flag defaults off).
- [x] All 215 tests pass (`pytest tests/ -q`).
- [x] Added a dated note to `config.py`'s pair-roster comment block pointing
      at the merge-order fix and `data/wfo_params.json` as the source of
      truth, rather than rewriting the WR/PF/DD figures (those need a fresh
      backtest re-run to update honestly, not just a text edit).
- [x] **Backtest validation done 2026-07-20 — see the dedicated section below**
      ("Fix: ML score-boost validation"). Result: reject, stays off.
- [ ] **Operational, not done here — needs the user to run it**: once this
      is deployed and the process restarted, manually trigger a catch-up WFO
      run (dashboard "WFO" button) for NZD_USD, EUR_AUD, and GBP_CAD so they
      don't wait for next Sunday.

## Review

**What changed and why:** Three bugs compounded to keep the Signal Quality
dashboard flat: (1) `AdaptiveParams.get()` always returned a dict containing
`cci_threshold=20`, even for pairs with no adaptive fit, and since it's
merged in after WFO's params it always won — so WFO's actual findings
(cci_threshold=30 for USD_CAD, 15 for EUR_AUD) never reached live scoring,
directly explaining "ran WFO twice, nothing changed." (2) The Sunday WFO job
is a ~2-hour sequential grid search across 4 pairs and has been getting
killed mid-run by the (separately, already-tracked) scheduler-freeze bug —
fixed pair ordering so the crash doesn't always cost the same tail pairs.
(3) The ML score-boost path in `confluence_scorer.py` could never fire
because `ml_win_prob` was always `None` at scoring time — now computed
before scoring when the new flag is on, though the flag stays off pending a
trustworthy validation path (see plan items above).

**Security/correctness check:** No secrets touched. `adaptive_params.get()`
change verified to have exactly one live call site
(`engine/signal_engine.py:129`); `.summary()` (dashboard display) reads
`self._state` directly and is unaffected. The two pairs with real saved
adaptive state (EUR_USD, USD_CHF — both inactive) are unaffected since their
`get()` result is unchanged when real state exists. `ML_SCORE_BOOST_ENABLED`
defaults `False`, so no live trading behavior changed from this session's
work without an explicit opt-in. Full test suite (215 tests) passes.

**How to notice a recurrence:** Watch `data/signal_audit.csv`'s C4 pass rate
and overall trigger rate over the next several days — expect movement away
from the flat ~30%/~4% seen the past two weeks, since C4 is now evaluated
against WFO's actual tuned threshold. If it's still flat after a process
restart + a completed WFO cycle, check `data/wfo_params.json`'s
`cci_threshold` per pair against what `engine/adaptive_params.py::get()`
would return for that pair, and confirm the merge in
`engine/signal_engine.py:126-130` is picking up the WFO value, not silently
reverting again.

**Requires a live process restart** to take effect — `adaptive_params` and
`signal_engine` are loaded at process start, same as the shadow-outcome
dedup fix from 2026-07-17.

**Follow-up verification (same day, 2026-07-20):** restarted process at
03:07 local; confirmed the merge fix live by calling
`wfo_optimizer.get_params()`/`adaptive_params.get()` directly — USD_CAD now
resolves `cci_threshold=30` (was silently 20), NZD_USD/EUR_AUD resolve `15`.
Dashboard aggregate hadn't visibly moved yet at that point, but that's
expected — only 5 scans had run since restart against a 1900+ row all-time
average; not a sign the fix failed.

---

# Fix: Signal Monitor sidebar showed "SIGNAL" for pairs that couldn't actually trade

## Why
User noticed several pairs badged "SIGNAL" in the sidebar (e.g. NZD_USD at
score 52, slider at 51) with no trade ever opening. Root cause: two
independently-computed thresholds. `dashboard/state.py::update_signal()`
labels a pair "SIGNAL" purely by comparing score to the global slider. But
the actual trade-eligibility gate lives inside `engine/signal_engine.py::run()`,
which checks score against that *pair's own* WFO-tuned `min_score` — a
number the sidebar never looked at. Checked `data/wfo_params.json`: USD_CAD,
NZD_USD, EUR_AUD, GBP_USD all currently have WFO `min_score=55`, well above
the 51 slider — so those pairs were structurally held to a stricter bar than
the badge implied, silently returning `"watching"` from `signal_engine.run()`
before ever reaching the trade path.

## Fix applied 2026-07-20
- `dashboard/state.py::update_signal()`: added an optional `threshold` param;
  when supplied, used instead of the raw global slider for the SIGNAL/
  WATCHING/SCANNING cutoff. Falls back to prior (slider-only) behavior when
  omitted, so other call sites are unaffected.
- `main.py::_process_pair`: now computes
  `max(wfo_optimizer.get_params(pair).get("min_score", config.MIN_CONFLUENCE_SCORE), slider, 40)`
  — the actual effective bar this pair has to clear — and passes it as
  `threshold` on the one call site that drives the live trading path.
- Added 2 regression tests in `tests/test_alerts_dashboard.py`.

## Scope note (deliberately not touched)
Four other `state.update_signal(...)` call sites (`main.py:160,787,831`,
`dashboard/app.py:2702`) still use the old slider-only behavior — these are
the `FOREX_WATCH` (watch-only, never trade) and diagnostic-scan paths, not
the real trade-execution path this fix targets. Left alone to keep the
change scoped; revisit if watch-pair badges turn out to cause the same
confusion.

## Review
215→217 tests pass. No behavior change to trade execution itself — this
only corrects what the sidebar displays to match the gate that was already
silently in effect. Requires a live process restart to take effect (same as
the merge-order fix above).

---

# Fix: chart zoom/pan resets when switching timeframes and back

## Why
User reported this as a repeat complaint ("asked several times already"):
zoom in on a chart, switch to another timeframe, switch back — the zoom is
gone, back to the default view. Per user's go-ahead, installed Playwright
(`pip install playwright` + `playwright install chromium`, in `venv/`) to
click-test this directly instead of guessing from source, since this
codebase has no browser automation and this exact class of chart bug
previously took 3 blind rounds to fix (see feedback_chart_svg_overlay
memory). Ran a standalone second instance of the dashboard on port 8051
(`dashboard.app.app` imported directly, not touching the user's live
process on 8050) so testing never touched the live session.

## Investigation (several false starts, kept here for the record)
1. First hypothesis: the saved zoom range falls outside the 500-candle
   window the backend fetches per TF. Real, worth guarding, but NOT the
   cause — added anyway (see fix 3 below).
2. Second hypothesis: a genuine race where a slow chart-data-store response
   for an abandoned TF gets applied after the user's already switched away.
   Built a client-side "what did the user actually just click" guard to
   detect and discard superseded payloads. Testing kept show the guard not
   catching anything — turned out this whole theory was built on a
   measurement artifact: Playwright/CDP was delivering console messages to
   the test script in delayed batches, making a response that actually
   applied ~400ms after its own click *look* like it arrived 2-8 seconds
   later, right when the next click happened. Cross-checked with
   `performance.now()` timestamps embedded in the log messages themselves
   (not wall-clock arrival time) to catch this.
3. **Actual root cause**, found once (2) was ruled out: `load()` in
   `dashboard/assets/chart.js` calls `S.candle.setData(...)` on a freshly
   (re)created chart series every time the TF changes. lightweight-charts
   fires its own implicit auto-fit `visibleTimeRangeChange` event in
   response — and the save-zoom listener (already subscribed from `init()`)
   was persisting *that* transient, wide, auto-fit range to
   `localStorage['apex_zoom_PAIR_TF']` a few hundred ms before `load()`'s own
   explicit restore-saved-zoom logic ran and read it back — so the restore
   was reading a value already clobbered earlier in the same `load()` call.
   Confirmed directly: saved zoom was unchanged immediately after switching
   TF (proving it's not disturbed by teardown/destroy), then confirmed the
   restore step logs the *already-wrong* value as `savedRange` at read time.

## Fix applied 2026-07-20
- **The real fix**: added `_suppressZoomSave` flag in `chart.js`. `load()`
  sets it `true` at the start and `false` only after its own view-setting
  logic (explicit restore or default-view fallback) finishes; the
  `subscribeVisibleTimeRangeChange` save handler skips writing to
  localStorage while the flag is set. This stops the implicit auto-fit event
  from ever being persisted, so the explicit restore always reads back the
  real, last-genuine-user-interaction zoom.
- Added a candle-window-overlap check before trusting a saved zoom range
  (guards a real, different scenario: panning deep into history before
  switching TF) — not the bug's cause, kept as a legitimate secondary
  improvement, with console diagnostics (`console.warn`/`console.debug`) so
  future reports come with real evidence instead of "it just doesn't work."
- Added a defense-in-depth staleness guard in `_apexUpdateChart`
  (`window._apexLatestPair` / `window._apexLatestTf`, the latter tracked via
  a native capturing `click` listener rather than a Dash clientside callback
  so it can't itself be subject to any Dash-side batching) — genuinely
  doesn't explain this bug, but is a real, cheap safety net against a slow
  or out-of-order `chart-data-store` response under heavier load, so kept.
- **Unrelated but found along the way**: `update_signals()` (renders the
  *entire* Signal Monitor sidebar) was wired to `interval-1s` — a full
  re-render every second, forever, for no reason (the `_1s` param was
  unused in the function body). Removed; it now only updates on
  `interval-60s` + the slider, same as its actual data-freshness needs.
  This was a real, independently-worth-fixing server-load issue found while
  investigating (initially, wrongly) suspected request-queueing delays.

## Review
Verified end-to-end with Playwright against the standalone test instance:
zoomed on M30, hopped through H1 → H4 → M30 → H1 → M30, confirmed the M30
zoom key was byte-identical to what was saved before the first switch, at
both 1s and 3s gaps between clicks. `node --check` clean on `chart.js`;
217 Python tests still pass (JS-only change, no Python test coverage for
it). Playwright is now installed in `venv/` for future UI verification —
no need to guess-and-ship for chart/dashboard changes going forward.
**Requires a live process restart** (`dashboard/app.py`'s new clientside
callback needs the Python process reloaded) **and users should hard-refresh
their browser** to fetch the updated `chart.js` (Dash asset URLs are
normally cache-busted automatically on file change, but worth doing
explicitly this once).

---

# Fix: TradeManager (live mode) had no disk persistence

## Why
Flagged 2026-07-18 in the ATR-trailing session: `PaperTrader` persists to
`data/paper_state.json` after every mutation, but `TradeManager` (used when
`config.MODE == "live"`) was in-memory only — a restart while live would
lose track of open positions, and `_reconcile_live_positions()`'s startup
mismatch check was comparing broker trades against an always-empty local
set, so it would warn on every single trade after every restart regardless
of whether anything was actually wrong.

## Fix applied 2026-07-20
- `trade/trade_manager.py`: `TradeManager.__init__` gained a `save_path`
  param (same convention as `PaperTrader`: `None` → default
  `data/live_state_<market>.json`, `False` → disable disk I/O). Loads
  `open_trades`/`closed_trades` from disk on construction if a save file
  exists.
- Added `_save_state()`/`_load_state()`, an independent copy of
  `PaperTrader`'s tiered atomic-write strategy (atomic rename → delete-then-
  rename → direct overwrite, handling the Windows AV/editor file-lock case)
  rather than a shared helper — kept `paper_trader.py` untouched to avoid
  risking that already-tested class for this. Reused `PaperTrader`'s small
  `_ser`/`_deser` datetime-serialization helpers via import instead of
  duplicating those.
- Save calls added at every mutation point: `open_trade()` (new position),
  `on_candle_close()` (after processing a pair's trades — covers both
  partial fills like TP1/TP2 and full closes in one call), `close_trade()`
  (manual close).
- `tests/conftest.py`: added an autouse fixture redirecting
  `trade.trade_manager._DEFAULT_SAVE_DIR` to a per-test tmp path, mirroring
  the existing `PaperTrader` isolation fixture — needed so the 20 pre-existing
  `TradeManager(...)` test call sites (which don't pass `save_path`) don't
  start reading/writing the real `data/` directory now that a default path
  exists.
- Added `tests/test_trade.py::TestTradeManagerPersistence` (8 tests): open
  trade / partial close / full close / manual close all survive a simulated
  restart (new `TradeManager` instance, same path), `save_path=False` writes
  nothing, missing/corrupt state files start empty instead of erroring,
  default path is per-market (`live_state_forex.json` vs `_crypto.json`).

## Scope note (found, not fixed — pre-existing, unrelated to persistence)
`close_trade()` (manual close) deletes from `open_trades` without ever
calling `_finalize_close()` — no `closed_trades` entry, no `realised_pnl`
computed, no `learning.data_collector.record_close()` call. This means a
manually-closed live trade currently vanishes without a trade-log or ML
record, unlike every other close path. Pre-existing behavior, not something
this task's scope (add persistence) should silently change — persistence
faithfully persists this gap too (a manual close just won't appear in
`closed_trades` after a restart either, matching pre-restart behavior).
Worth its own follow-up.

## Review
230 tests pass (222 → 230, 8 new). `config.MODE` is currently `"paper"`, so
this code path isn't exercised by the live bot yet — verified entirely
through the new test suite plus manual reasoning about the mutation points,
not against a real broker. Confirmed no stray `data/live_state_*.json` was
created during any of this work (isolation fixture verified working).
**How to notice a problem**: first time `MODE` is ever flipped to `"live"`,
check `logs/main.log` for `"TradeManager(forex) restored"` /
`"TradeManager(crypto) restored"` on startup (or "no such file" on a truly
fresh start) and confirm `_reconcile_live_positions()`'s log lines say
"Reconcile OK" for pre-existing broker trades instead of the old
always-warns behavior.

---

# Fix: ML score-boost validation

## Why
`config.ML_SCORE_BOOST_ENABLED` (added earlier today, default `False`) had
no validation path — `backtest/runner.py::run_backtest()` called
`score_signal()` directly with 3 args, no `ml_win_prob`, so the flag had
zero effect on any backtest regardless of its value. Needed the backtester
actually wired before a real on/off comparison was possible.

## Fix applied 2026-07-20
- `backtest/runner.py`: added a lazy `_ml_model()` singleton (imports
  `PatternLearner` only when the flag is on, keeping xgboost/sklearn off the
  import path for every normal run). Before scoring, when
  `config.ML_SCORE_BOOST_ENABLED`, builds the same feature dict
  `_bt_sig_features()` already computes for ML training-data seeding (just
  moved earlier, with the pre-boost raw score `ema_score+struct_score+pa_score`
  standing in for `confluence_score` — matching what `signal_log.csv`'s
  training rows actually contain, since boost never fired historically), calls
  `predict_win_prob()`, and feeds the result into `score_signal(...)` exactly
  like the live wiring added earlier today. A prediction failure falls back to
  `ml_win_prob=None` (no boost/penalty) rather than crashing the backtest.
- Added `tests/test_backtest_ml_boost.py` (5 tests, using `run_backtest()`'s
  existing overridable `buy_fn`/`sell_fn` params to trigger a deterministic
  signal without needing candle data tuned to the real strategy): flag-off
  never constructs the model, flag-on calls it with the expected feature
  keys, a high probability boosts the score, a low one penalizes it, and a
  model exception doesn't propagate.

## Validation run
USD_CAD, 3500 M30 bars (2026-04-08 → 2026-07-20), same fetched candle data
for both runs, using the actual live model (`models/ml_model.pkl`, last
retrained today):

| | WR | PF | PnL | MaxDD | Trades |
|---|---|---|---|---|---|
| ML boost OFF (current default) | 46.2% | 1.98 | $64.27 | 3.5% | 26 |
| ML boost ON | 38.1% | 1.47 | $25.88 | 4.8% | 21 |

Worse on every metric — lower win rate, lower PF, less than half the PnL,
higher drawdown, and fewer trades taken (the penalty branch, ×0.70 when
`ml_win_prob < 0.35`, is evidently firing often enough to push some
signals below `min_score` that would otherwise have qualified).

**Important caveat, stated rather than glossed over**: this has real
train/test leakage — `models/ml_model.pkl` was trained on
`data/signal_log.csv`, which overlaps the same mid-April–July window being
replayed here. This result shows what the *current* model does to
*this* backtest, not a clean out-of-sample verdict on the ML-boost concept
in general. It's also consistent with the earlier, independent finding in
[[bugs_shadow_outcome_duplication]] — win rate by score band was flat-to-
declining on cleaned data, i.e. this confluence score (and by extension a
model trained to predict outcomes from it) doesn't cleanly separate winners
from losers in this dataset yet.

## Recommendation
**Reject for now.** `ML_SCORE_BOOST_ENABLED` stays `False`. Don't revisit
without either (a) a materially larger/cleaner training set than what
`signal_log.csv` has today, or (b) a genuinely out-of-sample check (a live
paper-trading observation window with the flag on, judged only on trades
opened after that window starts) — not another backtest against overlapping
training data.

## Review
235 tests pass (230 → 235, 5 new). `config.py`'s `ML_SCORE_BOOST_ENABLED`
untouched (still `False` in the file — confirmed via `git diff`). No change
to live trading behavior. `backtest/runner.py`'s ML path only activates
when the flag is explicitly set, so every other consumer of `run_backtest()`
(the dashboard's backtest button, `run_walk_forward()`, WFO's grid search)
is unaffected by this change while the flag stays off — verified by the
"never constructed when flag off" test, and by the flag-off baseline run
above completing in the same ~25s as pre-change runs (the flag-on run took
~60s for the same 3500 bars, from `predict_win_prob()` reloading the model
from disk on every candidate signal — acceptable for this validation, worth
knowing if the flag is ever turned on for routine WFO runs, which each
backtest hundreds of parameter combinations).

---

# Fix: Indicator Settings modal audit (CCI / RSI / BB / Volume / Stochastic / Profiles)

## Why
`bugs_indicator_settings_wiring` memory flagged the modal as "not audited
exhaustively" after fixing two reported bugs (MACD histogram opacity, BB
visibility fighting itself) — only those two specifically-reported controls
had been checked. Everything else was unverified.

## Method
Checked every field in `_IND_DEFAULTS` (chart.js) against two known failure
patterns: (1) saved into `_indSettings.X` and rendered in the panel, but
never actually consumed anywhere outside the panel-building code (dead
setting), and (2) two independent, disconnected sources fighting over the
same visual property (the BB/Volume bug's pattern). Traced each setting from
UI control → save handler → either client-side `applyOptions()`
(`_applyIndVisualSettings()`) or server round-trip
(`_pushIndCompParams()` → `ind-comp-store` → `update_chart()` →
`build_chart_data()`). Verified fixes end-to-end with Playwright against a
standalone test instance (network capture + direct DOM/state inspection),
same approach as the chart zoom-persistence fix earlier today.

## Bugs found and fixed

**1. CCI's "MA Type", "MA Length", and "BB Multiplier" controls were completely
dead.** All three are exposed in the CCI settings tab, save into
`_indSettings.cci.{maType,maLength,bbMult}`, and render correctly showing
the user's selection — but `_pushIndCompParams()`'s payload never included
them, so they never reached the server. `dashboard/chart_builder_twlc.py`
computed the CCI smoothing MA using
`getattr(config, "CCI_SMOOTH_TYPE", "SMA")` /
`getattr(config, "CCI_SMOOTH_LENGTH", 14)` /
`getattr(config, "CCI_BB_MULT", 2.0)` — and **none of those three config
attributes exist**, so this was unconditionally always `"SMA"` / `14` / `2.0`
regardless of what the user picked. Changing "MA Type" from SMA to EMA (or
any other option) visibly did nothing, forever.
- Fixed: added `cci_ma_type`/`cci_ma_length`/`cci_bb_mult` to the
  `_pushIndCompParams()` payload (`chart.js`), threaded them through
  `update_chart()` (`app.py`) into `build_chart_data()`
  (`chart_builder_twlc.py`), replacing the dead `getattr(config, ...)`
  fallbacks with the real per-request values (still defaulting to
  `"SMA"`/`14`/`2.0` when unset, same as before — just now actually
  overridable).
- Verified via real network capture: the `ind-comp-store` request body sent
  to Dash now contains `"cci_ma_type":"EMA"` after changing the dropdown.

**2. "Load Profile" silently dropped RSI/BB/Volume/Stochastic settings, and
crashed the modal on the next tab switch.** "Save" persists the entire
`_indSettings` object (all 6 indicator groups). "Load" rebuilt
`_indSettings` from scratch using only 2 of those 6 keys:
```js
_indSettings = { cci: {...}, macd: {...} };   // rsi/bb/volume/stoch just gone
```
Since `_renderRSITab()`/`_renderBBTab()`/`_renderVolumeTab()`/
`_renderStochTab()` all read `_indSettings.X` **without** a defensive
fallback (unlike `_applyIndVisualSettings()`, which uses `_indSettings.X ||
{}`), this wasn't just a silent reset — the next time the modal rendered
any of those four tabs after a Load, it threw a JS error
(`Cannot read properties of undefined`).
- Fixed: Load now rebuilds all 6 groups the same way the (already-correct)
  page-load path `_loadIndSettings()` does — each merged over
  `_IND_DEFAULTS` so a profile saved before a field existed still works.
- Verified: loaded a profile with `rsi.visible=true`, confirmed the checkbox
  reflects it and re-rendering the RSI tab afterward throws no error
  (previously reproduced the crash directly from the pre-fix code before
  writing the fix, to make sure the test was real).

## Checked and confirmed already correct (no changes)
- MACD: fast/slow/signal periods (server-computed) and all visual options
  (color/width/style/opacity/show-toggles) — already fixed in an earlier
  session, still correct.
- RSI: period, OB/OS levels, color — all wired end-to-end. No "Width"
  control is exposed in the UI at all, so there's nothing to be dead.
- Bollinger Bands (the standalone overlay, distinct from CCI's internal BB):
  period, mult, and all colors — fully wired.
- Volume: single visibility toggle — fully wired.
- Stochastic: %K/%D/smooth periods (server-computed), OB/OS levels, colors —
  fully wired, including the price-line fix already applied in an earlier
  session.
- RSI/Stochastic pane creation being gated on `data.ind_toggles` (a
  server-echoed value) rather than directly on `_indSettings.X.visible`:
  confirmed this is **not** the BB/Volume bug's pattern — `ind_toggles.rsi`
  really is just `_indSettings.rsi.visible` round-tripped through the
  server (`show_rsi` in the `ind-comp-store` payload → echoed back as
  `ind_toggles.rsi`), not a second independent source. Toggling RSI/Stoch
  visibility has a one-request-round-trip lag before the pane actually
  appears/disappears (could feel mildly sluggish under load), but it
  doesn't fight itself or silently revert. Left as-is — correctly designed,
  just architecturally indirect.

## Review
235 tests pass (no new Python tests — this is JS/dashboard-only, same as the
earlier chart zoom fix). `node --check` clean on `chart.js`. Both fixes
verified end-to-end with Playwright against a standalone test instance
(port 8051, not the live session) — network-level capture for the CCI fix,
direct state/DOM inspection for the Profile-load fix (confirmed the crash
reproduces against the pre-fix code, then confirmed it's gone after).
**Requires a live process restart + browser hard-refresh** to pick up the
`chart_builder_twlc.py`/`app.py`/`chart.js` changes.

---

# Review: GBP_USD breakout-retest, first checkpoint since promotion

## Why
`project_pair_config` memory recorded an explicit plan on 2026-07-02 to
check back "around 2026-07-10" and compare GBP_USD's live breakout-retest
results against the 4999-bar backtest baseline that justified promoting it
(PF 1.94, WR 45%, PnL $42.63, 20 trades, 3.1% DD). That never happened —
10 days overdue by the time this got picked back up.

## What the data actually shows

**Real closed trades since promotion: 0.** `data/paper_state.json` has
exactly one `GBP_USD` entry in `closed_trades` — and it closed
2026-06-24, nine days *before* GBP_USD was ever switched to breakout-retest
(2026-07-02). It's a leftover EMA-bounce trade, not evidence about the
strategy being reviewed. There is exactly one GBP_USD trade *open* right
now, opened today (2026-07-20) — too new to say anything about.

**Signal activity since promotion** (`data/signal_audit.csv`, filtered to
`>= 2026-07-02`): 352 scans, only 3 reached `TRIGGERED` (2026-07-16 short
@64, and two on 2026-07-20 short @57 — the second likely the same setup
re-logged a scan cycle apart). **Only 1 of those 3 became a real trade**
(today's). The other 2 (including the 07-16 score-64 signal, comfortably
above whatever this pair's effective min-score is) got logged instead as
`would_win` shadow outcomes in `data/signal_log.csv` — meaning something in
`main.py`'s post-audit gate chain (most likely the ML tiered gate, since
score 64 is just under the 65 threshold where that gate applies) blocked
them before a trade opened. Couldn't confirm the exact gate — the relevant
`logs/main.log*` window from 07-16 had already rotated out before this
review (the same class of gap Job 2's `backupCount` fix addresses going
forward, coincidentally).

**Broader near-miss sample** (`signal_log.csv`, all 8 GBP_USD rows since
promotion, not just the 3 that reached TRIGGERED — this table includes
lower-scoring skipped signals too): 4 `would_win` / 4 `would_lose` → 50%
shadow win rate. Small sample, but not a red flag.

**Fresh 4999-bar backtest, re-run today** (per `project_pair_config`'s own
explicit guidance — never trust a stale cached number, results drift
day-to-day):

| | 2026-07-02 (promotion-time) | 2026-07-20 (today, fresh) |
|---|---|---|
| WR | 45% | 48% |
| PF | 1.94 | 1.87 |
| PnL | $42.63 | $47.81 |
| Trades | 20 | 23 |
| MaxDD | 3.1% | 3.1% |

The backtested edge **still holds up** on the latest 4999 bars — if
anything, marginally better than the number that justified promoting it in
the first place.

## Why "0 real trades in 18 days" isn't itself a red flag
23 trades over 4999 M30 bars (~104 days) implies roughly one trade every
4.5 days — over an 18-day window that's ~4 expected. Getting 3 *triggers*
in that window is well within normal small-sample variance of that rate.
The real gap is trigger→trade conversion (1 of 3, not 3 of 3) — and that's
structural, not a symptom of this pair specifically: `backtest.runner.py`
has no ML tiered gate, no cooldown, no max-open-trades cap, no duplicate-
trade protection — it's a pure signal-to-trade simulation. Live has all of
those. Any strategy will show a real trade-open rate below what the
backtest implies; this isn't unique to breakout-retest or to GBP_USD, and
isn't something this review should try to "fix" by loosening a gate.

## Recommendation
**Keep GBP_USD on breakout-retest. Don't revert to EMA-bounce** (which was
only PF 1.11 on this pair — thin, the whole reason breakout-retest was
tried). Nothing in this data suggests a problem: the backtest edge holds up
fresh, and the low live trade count is consistent with the strategy's own
low-frequency character plus expected backtest-vs-live gate differences,
not a sign something's broken.

**Set a more realistic next checkpoint** instead of repeating the same
too-early mistake: at ~1 trade/4.5 days, reaching even 10 real closed
trades (nowhere near `project_pair_config`'s own "~30+ is trustworthy, <15
is not" bar, but enough for a first real look) needs roughly 6-7 more
weeks, not one. Suggest checking back around **2026-09-01** for an
actual closed-trade WR/PF comparison, and treating anything sooner as "too
early to tell" the same honest way this review is.

## Review
No code changes — this was a data/decision review, not a bug fix. One
backtest run (`python -m backtest.runner --pair GBP_USD --bars 4999
--strategy breakout_retest`), output saved to `data/backtest_results.csv`
(overwrites on each run — not treated as a durable artifact). All figures
above pulled directly from `data/paper_state.json`, `data/signal_audit.csv`,
and `data/signal_log.csv`, not from memory or prior session notes.

---

# Feature: Real limit orders for demo/live mode via One-Click Buy/Sell (2026-07-31) — LIVE-AFFECTING, PENDING APPROVAL

## Why
User's One-Click Buy/Sell panel already places real market orders correctly
as of today's earlier fix (TradeManager.open_trade routing). User now wants
the same panel's "Limit" order type to also work for real demo/live trades
— tested and confirmed working weeks ago, but only ever in paper mode;
demo/live as a real trading path didn't exist until yesterday's MODE="demo"
switch, so this was never built for it, not a regression from today's work.
Must work in both demo and eventual real-money live mode — same code path,
gated only by which OANDA account credentials MODE resolves to (no new
mode-specific branching needed beyond what already exists).

Confirmed via code: `connectors/oanda_connector.py` has zero limit-order
capability today (`place_market_order` only). `TradeManager` has zero
`pending_orders` concept (storage, fill-detection, cancel) — `PaperTrader`
has all of this already (`open_limit_order`, `cancel_limit_order`,
`modify_pending_order`, `_check_limit_fills`) and stays the reference
implementation for parity. `oandapyV20.endpoints.orders` (already an
installed dependency, no new package needed) exposes `OrderCreate`,
`OrderCancel`, `OrderDetails`, `OrdersPending` — everything needed exists.

Key structural difference from paper mode: a paper limit order only "fills"
when our own code checks candle high/low against it
(`PaperTrader._check_limit_fills`, driven by `_tick_paper_trades()`). A
real OANDA limit order fills **on the broker's server**, automatically,
the instant price reaches it — independent of whether our bot is even
awake at that moment. So this isn't "port the paper logic" — it's "place
the order and later ask OANDA whether it filled," which is a materially
different, more reconciliation-heavy design than every other fix today.

## Plan
- [x] `connectors/oanda_connector.py`:
  - `place_limit_order(instrument, units, limit_price, sl_price, tp_prices, expiry_time=None)`
    — `OrderCreate` with `order.type="LIMIT"`, `price=limit_price`,
    `stopLossOnFill`/`takeProfitOnFill` same as `place_market_order`.
    `timeInForce`: use `"GTD"` with a `gtdTime` computed from
    `config.LIMIT_ORDER_EXPIRY_CANDLES` × the primary timeframe (M30) —
    lets OANDA enforce expiry server-side, which is *more* robust than
    paper's client-side "missed N candles" counter (doesn't depend on our
    bot being alive to expire it — directly relevant after this morning's
    lesson about state drifting from broker reality while unwatched).
    Returns the OANDA **orderID** (distinct from a tradeID — the order
    hasn't filled yet).
  - `cancel_order(order_id)` — `OrderCancel`.
  - `get_pending_orders()` — `OrdersPending`, for the reconciliation poll
    below.
- [x] `trade/trade_manager.py`:
  - Add `self.pending_orders: dict[str, dict]` (mirrors `open_trades`'
    shape), included in `_save_state()`/`_load_state()` — schema addition
    to `data/live_state_forex.json`, backward-compatible (`.get(...,  {})`
    on load for files saved before this change).
  - `open_limit_order(signal, limit_price)` — calls
    `connector.place_limit_order(...)`, stores the pending record keyed by
    orderID.
  - `cancel_limit_order(order_id)` — calls `connector.cancel_order`,
    removes local record.
  - `reconcile_pending_orders()` — polls `connector.get_pending_orders()`
    each cycle; any local pending order no longer listed there has either
    filled or expired — check `connector.get_open_trades()` to tell which,
    and promote to `open_trades` (fill) or drop (expiry/cancel) accordingly.
- [x] `main.py`: call `_trade_manager_fx.reconcile_pending_orders()` from
  inside `_tick_live_trades()` (now correctly running every 30 min after
  today's dedup fix — a pending-order fill sitting undetected for hours
  is exactly the CHF_JPY failure mode from this morning, applied to a new
  code path). Also: `_sync_live_state()` (main.py:817-827) currently only
  pushes `open_trades`/`closed_trades` into dashboard state — needs
  `pending_orders=...` added too, or a filled/pending real order simply
  won't render in the Pending Trades panel regardless of everything else
  working. Confirmed `pending_orders_panel()` itself needs no changes —
  it already reads a plain dict list generically, not paper-specific.
- [x] `dashboard/app.py`:
  - `confirm_order`'s limit branch: replace today's rejection message with
    a real call to `tm.open_limit_order(...)`.
  - `cancel_pending_order` callback: same `tm`/`pt` routing pattern as the
    close/edit fixes earlier today (currently hard-paper-only, hits the
    same "if not pt: return no_update" gap).
  - Pending Trades panel: confirm it already reads `state["pending_orders"]`
    generically (not paper-specific) — likely yes already, given it renders
    from a plain list today, but verify rather than assume.
- [x] Tests: new coverage in `tests/test_trade.py` for
  `TradeManager.open_limit_order`/`cancel_limit_order`/
  `reconcile_pending_orders` (fill case, expiry case, cancel-before-fill
  case), using the existing `MockForexConnector` pattern.
- [x] Full test suite — confirm zero regressions (237 passing today).

## Review

**What changed**: `connectors/oanda_connector.py` gained `place_limit_order()`
(OANDA `LIMIT` order, `GTD` expiry so the broker itself enforces the window
rather than depending on our process being alive to cancel it —
`config.LIMIT_ORDER_EXPIRY_CANDLES × 30min`, same 8-candle/4h default as
paper, unchanged per no objection raised), `cancel_order()`, and
`get_pending_orders()`. `trade/trade_manager.py` gained a `pending_orders`
dict (persisted, backward-compatible load for pre-existing state files),
`open_limit_order()` (same validation/sizing as `open_trade()`, plus a
pending-pair duplicate check `open_trade()` itself doesn't need since it
has no pending concept), `cancel_limit_order()`, and
`reconcile_pending_orders()` — polls the broker each tick; anything no
longer pending gets matched against `get_open_trades()` by
instrument+direction (broker order IDs and trade IDs aren't the same
sequence) and either promoted to `open_trades` or dropped as
expired/cancelled. `main.py` wires the reconcile call into
`_tick_live_trades()` (runs every 30 min, same cadence as everything else
post-dedup-fix) and adds `pending_orders` to `_sync_live_state()`.
`dashboard/app.py`: the quick-trade Limit branch now calls
`tm.open_limit_order()` instead of rejecting; `cancel_pending_order`
routes to whichever manager (paper or real) actually holds the order,
same pattern as today's earlier close/edit fixes.

**Verification**: 10 new tests covering open/reject-low-score/
reject-duplicate-pending-pair/cancel/cancel-not-found/still-pending-no-op/
promote-on-fill/drop-on-expiry/persistence-across-restart/backward-compat-
load-of-old-state-files-with-no-pending_orders-key. Full suite: 247/247
passing (237 prior + 10 new), zero regressions.

**Follow-up same day**: user tested this and hit a real bug — a Limit order
placed via One-Click Buy/Sell filled immediately as a MARKET order instead.
Root cause was unrelated to the new backend: `set_order_form` (BUY/SELL
click handler, `dashboard/app.py`) rebuilt `order-form-store`'s data from
scratch as `{"direction", "pair"}` on every click, silently discarding
whatever `order_type` the Market/Limit toggle had previously set — so
selecting Limit and then re-clicking BUY/SELL (to refresh price, switch
direction, etc.) reverted to market with no visual indication. Fixed by
merging into existing store data instead of replacing it, and — since the
toggle buttons/limit-row visibility weren't restored either — added that
sync to `populate_order_form()` too, so the UI can no longer visually show
"Market" while a preserved `order_type: "limit"` would actually submit as
limit (or vice versa). Verified via `dashboard.app` import (Dash validates
duplicate-Output declarations at callback-registration time, not just
Python syntax — required adding `allow_duplicate=True` to
`toggle_order_type`'s outputs once `populate_order_form` also started
writing to them).

Also built `TradeManager.modify_pending_order()` (was flagged as
deliberately out of scope, then requested same day). Real limit orders
can't be edited in place on OANDA — this cancels the existing broker order
and places a new one with the merged parameters, returning the NEW
order_id (unlike `PaperTrader`'s in-place bool-returning version) since
callers must re-key whatever they were tracking the old id under. Dashboard's
`confirm_edit_pending` now routes to whichever manager holds the order and
handles both return conventions.

**Verification**: 13 new tests total (10 from the initial build + 3 for
modify_pending_order: new-id-returned, partial-update-preserves-other-
fields, not-found). Full suite: 250/250 passing, zero regressions.

**Requires a live process restart** to take effect, same as every other
fix today — `TradeManager`/`main.py`/dashboard callbacks are loaded at
process start.

## Open design questions before starting (need your call)
- **Expiry window**: paper defaults to 4 missed candles (~2h on M30).  Same
  default OK for real orders, or different for demo/live specifically?
- **Reconciliation cadence**: tied to the 30-min candle-close cycle (same
  as everything else, simplest, consistent with today's dedup fix) — or
  does a limit order's fill need to be noticed faster than that? Real
  broker-side SL/TP already don't need this (broker enforces them
  instantly regardless of our poll rate) — a filled limit order becoming
  an open trade is the thing that would sit undetected between polls.

# Fix: GBP_CAD P&L mismatch (fill-price bug) + JPY/CAD/AUD position-sizing bug (2026-08-04)

## Why
User asked me to check on 2 open trades + 1 recent loss. Investigating a
tiny (-$0.03/-$0.04) EUR_JPY/CHF_JPY loss led to comparing local trade
records against OANDA's own `get_trade_details()` ground truth, which
surfaced two separate, real bugs — not the "nothing's actually wrong"
conclusion the investigation started from.

## Bug 1: local SL/TP close records didn't match OANDA's real fill
`TradeManager._exec_close()`/`close_trade()` called the connector's
`close_trade()` (a market order) but discarded its response entirely,
assuming the fill happened exactly at the SL/TP trigger level. A market
close fills at whatever price is current when it executes — confirmed via
`get_trade_details()` on GBP_CAD trade 31: local record showed a $3.26
loss at the assumed SL price (1.88193); OANDA's actual fill was a **$2.64
gain** at 1.88597 — price had reversed past breakeven by the time the
close order actually executed (M30 candle-close detection can lag up to
30 min behind the price action that triggered it). Trade 37 showed the
same pattern, smaller magnitude (+$3.84 recorded vs +$2.34 real).

**Fix**: `_exec_close()` and `close_trade()` now read the real
`orderFillTransaction.price`/`.pl` from the broker response (added
`_real_fill()` helper) and use those for `realised_pnl`/`exit_price`
whenever present, falling back to the old assumed-price estimate only
when the response doesn't include fill data. `_exec_close()` no longer
re-raises on a failed broker call (SL/TP path always finalizes locally
either way — a raise there just meant `t["realised_pnl"]` silently never
got updated while the trade got finalized with $0.00 anyway); the manual
`close_trade()` still raises on failure since an unclosed-but-marked-closed
position would be worse (dashboard stops tracking a real open position).

## Bug 2: JPY/CAD/AUD pairs undersized ~30-150x relative to intended 1% risk
`calculate_position_size()` computed `units = risk_amount / sl_distance`
with no currency conversion — correct only when the quote currency is USD
(GBP_USD, NZD_USD — confirmed exact match against OANDA). For EUR_JPY/
CHF_JPY (quote=JPY), `sl_distance` is in JPY, not USD, so the formula
undersized positions ~150x (confirmed against OANDA ground truth: 19-unit
positions produced genuinely correct-but-tiny $0.03-0.04 real losses
against an intended $5 risk). USD_CAD/GBP_CAD (quote=CAD) and EUR_AUD
(quote=AUD) have the same bug, smaller magnitude (~30-40%, not caught
earlier because GBP_CAD's Bug-1 mismatch masked it).

**Fix**: added `get_quote_to_usd_rate(pair, connector=None)` to
`risk/risk_manager.py` — 1.0 for USD-quoted pairs (no lookup, no behavior
change), a live OANDA quote when a connector is passed (real trading), an
approximate fixed table otherwise (backtesting — doesn't need
point-in-time accuracy, just correct scale). `calculate_position_size()`
takes an optional `quote_to_usd` param, defaulting to the approximate
table when omitted. Live call sites (`main.py`, `trade_manager.py`
open_trade + limit orders, `dashboard/app.py`'s order-form previews) now
pass a live-quote rate; `backtest/runner.py` and the paper-mode "Scan Now"
path needed no changes — they call the function without the 5th arg, so
they pick up the approximate-table fallback automatically. Also fixed the
same missing conversion in `TradeManager._pnl()` (the local P&L estimate
used only when broker fill data is unavailable) — now that positions are
correctly sized, a mismatched fallback would be wrong by the same factor
in the opposite direction.

## Verification
- Confirmed both bugs against OANDA's real `get_trade_details()` /
  `realized_pl` before writing any fix (not just local-record theorizing).
- After the sizing fix, computed live quote-to-USD rates for every active
  pair and confirmed each now targets ~$5.02 real risk (1% of a $502
  balance) instead of the pre-fix $0.03-5 range depending on quote
  currency.
- Full test suite: 257/257 passing, zero regressions.
- `main.py`, `dashboard.app`, `trade.trade_manager`, `risk.risk_manager`,
  `backtest.runner` all import cleanly.

**Requires a live process restart** to take effect — same as every other
fix, `TradeManager`/`main.py`/dashboard callbacks are loaded at process
start.
