# Two real JS bugs: MACD histogram opacity does nothing, BB toggle reverts itself

## What was reported

1. MACD histogram Opacity slider (Indicator Settings → MACD → Histogram)
   has no visible effect on the chart.
2. Toggling BB (Bollinger Bands) "Visible" in the settings panel does
   nothing.

## Bug 1 — MACD histogram opacity/colors were never applied anywhere

`S.macd` (the MACD histogram series, `dashboard/assets/chart.js`) is
created with no `color` option at the series level at all:
```js
S.macd = chart.addSeries(LC_.HistogramSeries, {
    lastValueVisible: false, priceLineVisible: false,
}, _macdPane);
```
Its per-bar bull/bear coloring comes entirely from each data point's own
`color` field (the standard lightweight-charts histogram pattern) — but
nothing in the codebase ever computed that field from
`histColorUp`/`histColorDown`/`histOpacity`. The settings panel correctly
read the slider into `_indSettings.macd.histOpacity` and saved it, but
that value was never consumed anywhere — `_applyIndVisualSettings()`'s
MACD block only ever called `S.macd.applyOptions({ visible: !!m.showHist })`,
nothing about color.

**Fix:** added `_recolorMacdHist()` — recomputes every bar's color from
`_lastChartData.macd`'s raw values (sign determines up/down color) combined
with the current `histColorUp`/`histColorDown`/`histOpacity`, then
`setData()`s the recolored array. Called from two places:
- `_applyIndVisualSettings()` (fires when the user changes a setting)
- `load()` (fires on every periodic chart data refresh) — replacing the
  previous raw, uncolored `S.macd.setData(data.macd||[])`, so the user's
  chosen colors/opacity survive normal chart refreshes instead of
  reverting to raw data on the next update.

## Bug 2 — BB (and Volume) had two competing, disconnected visibility sources

`dashboard/assets/chart.js`'s `load()` function (runs on every chart data
refresh) was independently setting BB and Volume visibility from
`data.ind_toggles` (an older, separate toggle system, server-driven):
```js
S.bbUpper.applyOptions({ visible: !!toggles.bb });
```
Meanwhile `_applyIndVisualSettings()` (fires when the user changes the
newer Indicator Settings modal) sets visibility from
`_indSettings.bb.visible` — a completely different, client-side-only value
that never communicates back to `toggles.bb`.

Net effect: flipping "Visible" in the modal set `_indSettings.bb.visible`
correctly, and `_applyIndVisualSettings()` applied it — but the next
periodic chart data refresh (`load()`) immediately overwrote it back using
the stale `toggles.bb` value, silently reverting the change. "Flip it,
nothing happens" was actually "flip it, and it fights itself and loses" —
not a no-op, a race the modal always eventually lost.

RSI and Stochastic use `toggles.rsi`/`toggles.stoch` too, but legitimately
— those need pane creation/destruction tied to the toggle, a different
concern from a simple visibility flag on an always-present overlay series
(BB/Volume don't need pane management, they're overlays on the price pane).
Left RSI/Stoch's toggle usage untouched.

**Fix:** removed the `visible: !!toggles.bb` / `visible: !!toggles.volume`
overrides from `load()`, keeping only the `setData()` calls. Visibility is
now owned exclusively by `_applyIndVisualSettings()` /
`_indSettings.{bb,volume}.visible` — matching how CCI/MACD's line-series
visibility already worked correctly (no complaints about those).
Confirmed both default to `visible: false` already, matching the
series-creation defaults, so this doesn't change first-load behavior.

## Verification

- [x] `node --check` — OK
- [x] `python -m pytest -q` — 185 passed, no regressions (pure JS change)
- [x] Confirmed via a throwaway dashboard instance that the served
      `chart.js` contains `_recolorMacdHist` (3 occurrences: definition +
      2 call sites) and zero remaining `toggles.bb`/`toggles.volume`
      visibility overrides
- [ ] **Not visually confirmed in a real browser** — same standing
      limitation this whole session (no Playwright/chromium-cli here).
      Traced both bugs to their exact mechanism via code reading, not
      observation, so confidence is in the diagnosis being structurally
      correct rather than a screenshot match.

## Review

Both bugs share a theme with the CSS panel-cutoff saga earlier: features
that were built with a plausible-looking code path that silently never
actually connected end-to-end. The MACD opacity slider updated state that
nothing read; the BB toggle updated state that something else
immediately overwrote. Worth a broader pass at some point: are there other
settings-panel controls with the same "reads into _indSettings but nothing
applies it" or "two competing toggle sources" pattern? Did not audit CCI/
RSI/Stoch/Profiles exhaustively for the same issues this session — scoped
to the two specifically reported.
