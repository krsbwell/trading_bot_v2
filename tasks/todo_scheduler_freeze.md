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
- [ ] [trade/paper_trader.py](trade/paper_trader.py) — wrap `self._lock` in a small instrumented
      lock class (same context-manager interface, so no call site changes) that logs a WARNING
      if acquiring takes >1s, and if held for >1s. Proves/disproves the lock-contention theory.
- [ ] [main.py](main.py) — track a module-level `_last_stream_tick_ts`, updated on every tick
      inside `_on_stream_price`. Proves whether the stream itself goes silent for the whole gap
      (vs staying alive while something else blocks).
- [ ] [main.py](main.py) — wrap the tick-check call inside `_on_stream_price` with start/end
      timing, logging a WARNING if a single tick takes >0.5s to process. Catches a slow callback
      as the culprit (e.g. synchronous disk write on every tick).
- [ ] [main.py](main.py) — add a new lightweight `_scheduler_heartbeat` job (interval 30s) that
      just logs current UTC time + seconds-since-last-stream-tick. If this job itself stops
      firing during a freeze, the whole executor pool is starved (not just one job); if it keeps
      firing while `on_candle_close` misses, that's pool/queue starvation on a specific job.
- [ ] Compile-check + run test suite (no behavior change expected, purely additive logging + one
      no-op job)
- [ ] Check with user before restarting the live bot again to load this (same consideration as
      last time — it's their real running paper-trading process)

## Non-goals
- Not attempting a fix yet — don't have a confirmed root cause, only correlation.
- Not touching `oanda_connector.py`'s existing REST timeout (already correct for one-shot calls).
