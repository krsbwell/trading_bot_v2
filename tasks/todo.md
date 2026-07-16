# Todo: Default chart TF → M30, add cancel button for pending limit orders

## 1. Change default chart timeframe from 1H to 30M
- [x] [dashboard/app.py:405](dashboard/app.py#L405) — initial highlight on the TF button row: `tf == "H1"` → `tf == "M30"`
- [x] [dashboard/app.py:770](dashboard/app.py#L770) — `dcc.Store(id="selected-tf", data="H1")` → `data="M30"`
- [x] [dashboard/app.py:1362](dashboard/app.py#L1362) — fallback in `highlight_active_tf`: `tf or "H1"` → `tf or "M30"`
- Note: leaves `TIMEFRAMES["primary"]` in config.py untouched (already M30) — this only changes which TF the *chart view* opens on, not signal generation.

## 2. Add a separate Pending Trades panel with a cancel button
A pending limit order isn't an open trade yet (hasn't triggered) — it gets its own panel, not folded into Open Trades. Backend already supports cancelling (`paper_trader.cancel_limit_order(order_id)` exists and works), just never exposed in the UI.

- [x] [dashboard/panels.py](dashboard/panels.py) — new `pending_orders_panel(orders, mode)` function (styled like `open_trades_panel`): one card per pending order showing pair, direction, limit price, SL, TP1/TP2/TP3, size, candles-until-expiry, and a CANCEL button (`id={"type": "cancel-limit-btn", "index": order_id}`)
- [x] [dashboard/app.py:337](dashboard/app.py#L337) — added `html.Div(id="pending-trades")` right after the existing `html.Div(id="open-trades")`
- [x] [dashboard/app.py](dashboard/app.py) — `update_trades_and_account` (the `interval-5s` callback that already refreshes open trades) also outputs `pending-trades` children from `pt.pending_orders`, so the panel stays live
- [x] [dashboard/app.py](dashboard/app.py) — new callback `cancel_pending_order`: CANCEL button click → `pt.cancel_limit_order(order_id)` → `state.update(pending_orders=...)` → re-render `pending-trades` panel children
- [x] No changes needed to `open_trades_panel` itself — pending orders are fully separate

## 2b. Add ability to edit a pending order before it triggers
Backend has no equivalent to `modify_trade()` for pending orders yet — needs to be added. Unlike an open trade, a pending order's *entry* (limit price) is also still changeable, not just SL/TP.

- [x] [trade/paper_trader.py](trade/paper_trader.py) — new `modify_pending_order(order_id, limit_price=None, sl=None, tp1=None, tp2=None, tp3=None) -> bool` mirroring `modify_trade`, applied to `self.pending_orders` instead of `self.open_trades`
- [x] [dashboard/panels.py](dashboard/panels.py) — added an EDIT button to each pending-order card (`id={"type": "edit-pending-btn", "index": order_id}`), next to CANCEL
- [x] [dashboard/app.py](dashboard/app.py) — dedicated edit modal for pending orders (`edit-pending-modal-*` ids) with Limit Price / SL / TP1 / TP2 / TP3 inputs — kept separate from the existing open-trade edit modal since the field set differs (limit price editable here, not there)
- [x] Wired EDIT click (`toggle_edit_pending_modal`) → opens modal pre-filled with the order's current values; Confirm (`confirm_edit_pending`) → `pt.modify_pending_order(...)` → re-renders `pending-trades` panel

## 3. Verify
- [x] `python -m pytest -q` — 185 passed (10 pre-existing failures are live Alpaca API calls needing real credentials, unrelated to this change)
- [x] Restarted the live bot (`main.py`) so the running dashboard picks up the new code; confirmed via `/_dash-layout` that `selected-tf` now defaults to `"M30"` and that `pending-trades`, `edit-pending-modal`, `edit-pending-store` all exist in the served layout
- [x] Exercised the real `PaperTrader`/`pending_orders_panel` code (isolated instance, `save_path=False`, no disk writes) end-to-end: open limit order → panel renders CANCEL+EDIT with correct data → `modify_pending_order` changes limit price/SL while leaving TP1 untouched → `cancel_limit_order` removes it → fed a candle that would have filled the *original* limit price and confirmed no trade opened (order was actually gone, not just hidden)
- Not independently confirmed by clicking through a real browser (no Playwright/chromium-cli available in this environment) — recommend a quick manual click-through next time the dashboard is open, particularly the modal open/close/backdrop interactions which are harder to fully prove from the Python side.

## Review

**1. Default timeframe** — three one-line defaults changed from `H1` to `M30` (button highlight, the `dcc.Store` initial value, and the highlight-fallback). Purely a chart-view default; signal generation was already M30 (`config.TIMEFRAMES["primary"]`), untouched.

**2. Pending Trades panel** — new `pending_orders_panel()` in `panels.py`, structurally identical to `open_trades_panel()` (same card styling, same color/mono helpers) but simpler: no live P&L or progress bar since nothing has been risked yet. Wired into the existing 5-second refresh cycle (`update_trades_and_account`) alongside Open Trades and Account Stats, so it stays live without a new polling loop. CANCEL calls the pre-existing `paper_trader.cancel_limit_order()` — that logic already existed, it just had no UI hook.

**3. Edit pending order** — added `modify_pending_order()` to `paper_trader.py`, mirroring the existing `modify_trade()` but also allowing the limit price itself to change (an open trade's entry is fixed once filled; a pending order's isn't). Reused the existing floating-modal UI pattern from the open-trade EDIT button, as a separate modal (`edit-pending-modal-*`) since the field set differs (adds Limit Price, drops the Move-to-Breakeven button which only makes sense once a trade is actually open).

**Notable side effect**: verifying this required restarting the user's live-running bot process (a plain `python main.py` in a VS Code terminal, not supervised by `run.ps1`) so it would load the new code — this was confirmed with the user first. Paper-trader state persists to disk, so no trade history was lost, but any pending scan state was momentarily reset.

---

# Todo: Raise signal discard threshold from 35 to 40 (fewer weak Telegram alerts)

Currently any signal scoring ≥35 gets `watching: True` and triggers a Telegram notification ([engine/signal_engine.py:207-220](engine/signal_engine.py#L207-L220)). Raising the discard cutoff to 40 means scores 35-39 are dropped entirely (no dashboard entry, no Telegram), same as sub-35 today.

- [x] [engine/signal_engine.py:207](engine/signal_engine.py#L207) — `if final_score < 35:` → `if final_score < 40:`
- [x] [engine/signal_engine.py:219](engine/signal_engine.py#L219) — reject_reason string `f"Score {final_score} < 35 discard threshold"` → `< 40 discard threshold`
- [x] [engine/confluence_scorer.py:14-15](engine/confluence_scorer.py#L14-L15) — update docstring ranges: `35-59: WATCHING` → `40-59: WATCHING`, `< 35: discard` → `< 40: discard`
- [x] [dashboard/state.py:152](dashboard/state.py#L152) — `"SCANNING" if score >= 35 else "NEUTRAL"` → `>= 40` (dashboard status label now matches the new discard floor)
- [x] Left [dashboard/panels.py](dashboard/panels.py) `>= 35` color-threshold checks (lines 893, 918, 1039) alone — those are just gold/red coloring cutoffs for already-logged historical scores, not the live discard gate
- [x] Checked `tests/` — no test asserts behavior at score 35-39 or references the discard threshold by name; nothing to update
- [x] Ran `python -m pytest -q` — 185 passed, same 10 pre-existing Alpaca-live-credential failures as before (unrelated)
- [ ] Note for user: if `main.py` is currently running live, this change needs a restart to take effect

## Review

Four one-line changes, all raising the same cutoff from 35 to 40 so they stay consistent with each other:

1. **[engine/signal_engine.py:207](engine/signal_engine.py#L207)** — the actual gate: signals scoring 35-39 now return `None` (fully discarded) instead of a `watching: True` dict. This is what stops them from reaching `main.py`'s Telegram-alert branch.
2. **[engine/signal_engine.py:219](engine/signal_engine.py#L219)** — updated the audit-log rejection reason string to match.
3. **[engine/confluence_scorer.py:14-15](engine/confluence_scorer.py#L14-L15)** — docstring only, no behavior change; kept in sync so it doesn't lie about the actual cutoff.
4. **[dashboard/state.py:152](dashboard/state.py#L152)** — the dashboard's own `SCANNING`/`NEUTRAL` status label threshold, bumped to match. Without this it would have been dead code (scores 35-39 can no longer reach `update_signal` with a nonzero score since `signal_engine` now returns `None` for them), but keeping it aligned avoids a stale, misleading constant sitting next to the real one.

**Net effect**: scores 35-39, which previously triggered a "watching" Telegram message, are now silently discarded — same treatment as sub-35 scores today. Scores ≥40 are unaffected.

**Not changed**: [dashboard/panels.py](dashboard/panels.py) color-threshold checks (lines 893, 918, 1039) — those only decide gold-vs-red text color for scores already written to history/logs; they don't gate anything live, so touching them wasn't necessary and risked scope creep.

**Security check**: no new user input, no new external calls, no secrets touched — this is a pure numeric-constant change in existing gating logic. Nothing to flag.
