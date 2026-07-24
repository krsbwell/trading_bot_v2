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
