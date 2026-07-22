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
