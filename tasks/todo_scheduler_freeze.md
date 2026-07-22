# Todo: Instrument the recurring scheduler-freeze mystery

## Background
Today's log shows `on_candle_close` (the cron job the health banner depends on) silently missed
its 30-min fire 3 times (gaps of ~90min, ~2hrs, ~30min), each recovering right around a
"Price stream disconnected — reconnecting" log line. This isn't explained by the July 3rd fixes
(those cover REST call timeouts and per-pair exceptions; a 15s REST timeout can't cause a
90-minute freeze). Streaming reads over `oandapyV20` may not honor the client's `timeout=15`
the same way one-shot REST calls do — plausible the stream socket goes silent for a long time
before the OS finally kills it. Also plausible: the streaming thread's tick callback holds
`PaperTrader._lock` for a long time (e.g. stuck in disk I/O), and the scheduler thread blocks
waiting on that same lock. Ruled out: project directory is not under OneDrive sync, so that
common Windows file-hang cause doesn't apply.

Goal here is **instrumentation only** — enough logging to catch the mechanism red-handed next
time it happens, not a fix (we don't know the real cause yet, don't want to guess-fix it).

## Plan
- [x] [trade/paper_trader.py](trade/paper_trader.py) — wrap `self._lock` in a small instrumented
      lock class (same context-manager interface, so no call site changes) that logs a WARNING
      if acquiring takes >1s, and if held for >1s. Proves/disproves the lock-contention theory.
      **Found already implemented** (2026-07-20 review) — `_InstrumentedLock`
      exists at `trade/paper_trader.py:78-114`, code comment says "Added
      2026-07-06." This plan file's checkboxes were apparently never updated
      after the work landed. Added 5 regression tests
      (`tests/test_trade.py::TestInstrumentedLock`) since none existed.
- [x] [main.py](main.py) — track a module-level `_last_stream_tick_ts`, updated on every tick
      inside `_on_stream_price`. Proves whether the stream itself goes silent for the whole gap
      (vs staying alive while something else blocks).
      **Found already implemented** — `_stream_health["last_tick"]`
      (`main.py:965,975`), same story as above.
- [x] [main.py](main.py) — wrap the tick-check call inside `_on_stream_price` with start/end
      timing, logging a WARNING if a single tick takes >0.5s to process. Catches a slow callback
      as the culprit (e.g. synchronous disk write on every tick).
      **Found already implemented** — `main.py:983-990`.
- [x] [main.py](main.py) — add a new lightweight `_scheduler_heartbeat` job (interval 30s) that
      just logs current UTC time + seconds-since-last-stream-tick. If this job itself stops
      firing during a freeze, the whole executor pool is starved (not just one job); if it keeps
      firing while `on_candle_close` misses, that's pool/queue starvation on a specific job.
      **Found already implemented** — `main.py:1038-1048`, confirmed actively
      firing every 30s in the current live log (`logs/main.log`, 201
      HEARTBEAT lines as of this review, most recent seconds old).
- [x] Compile-check + run test suite (no behavior change expected, purely additive logging + one
      no-op job) — 222 tests pass (`pytest tests/ -q`).
- [x] Additional fix found needed while reviewing this (2026-07-20): the
      exact incident this instrumentation exists to catch *did* recur
      (2026-07-19 Sunday WFO run killed mid-flight, found during an unrelated
      investigation the same day) — but by the time this task got picked back
      up, `RotatingFileHandler`'s `backupCount=3` (20MB total) had already
      rotated that incident's heartbeat/lock logs out of existence, purely
      from one day's normal volume plus a handful of restarts. Raised
      `backupCount` to 20 (~100MB) in `main.py` so a future recurrence's
      evidence survives long enough to actually be analyzed — the
      instrumentation is worthless if its own output doesn't outlive the
      incident.
- [x] Already running live — this main.py restarted earlier today (2026-07-20)
      for unrelated fixes, so the instrumentation (plus the new backupCount)
      is already deployed. No separate restart needed for this task.

## Non-goals
- Not attempting a fix yet — don't have a confirmed root cause, only correlation.
- Not touching `oanda_connector.py`'s existing REST timeout (already correct for one-shot calls).

## Review (2026-07-20)

**What this task actually delivers**: confirmation that all 4 planned
instrumentation points were already implemented (apparently in an earlier,
unrecorded pass — this file's checkboxes just never got updated), that
they're genuinely deployed and working on the live process right now, and
one real fix (`backupCount` 3→20) so the next occurrence's evidence doesn't
get rotated away before anyone can look at it — which is exactly what
happened to the 2026-07-19 recurrence.

**What this task does NOT deliver, and can't**: an actual root cause. The
plan's own non-goals are explicit about this — instrumentation only, no fix,
because there's no confirmed mechanism yet. That's still true. Don't read
"complete" here as "the freeze is fixed" — it isn't, and this task was never
going to fix it. What changed is that the *next* time `on_candle_close`
misses its fire, `logs/main.log*` will actually contain the heartbeat
cadence, stream-tick recency, and lock-timing data needed to tell apart the
plan's two competing theories (stream socket silently dying vs. a slow
callback holding `PaperTrader._lock`) — and now it'll survive long enough
after the fact to be read.

**How to notice a recurrence**: watch for a gap in `HEARTBEAT` log lines
(should be steady every 30s) — if heartbeats also stop, the whole scheduler
executor is starved, not just `on_candle_close`. If heartbeats keep firing
but `on_candle_close`'s own "Signal ..." lines have a gap, check the
heartbeat's "last stream tick" age at that moment, and grep the same window
for "lock acquisition took" / "lock held for" / "tick_check for ... took".
That combination is what will finally answer this.

**Test backing this**: 222 tests pass, 5 new (`TestInstrumentedLock`)
specifically covering the lock wrapper's behavior (acts as a normal mutex,
doesn't deadlock, warns on slow acquire/hold, silent on the fast path). The
`main.py`-side pieces (heartbeat job, stream-tick tracking) remain untested
in isolation — consistent with the rest of `main.py`'s sparse coverage (see
[[bugs_scheduler_reliability]]) — but verified working by direct observation
of the live log instead.
