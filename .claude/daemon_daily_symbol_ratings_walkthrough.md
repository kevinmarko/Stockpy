# Walkthrough: daemon symbol ratings once per trading day

**Why.** Auto-drop removes an unheld symbol after 5 consecutive BAD rating *cycles*. Only `main.py` wrote ratings (one cycle per weekday at about 08:47 ET), and #1105 kept the daemon's writer off because the daemon runs hourly. Step 5.5 archives `main.py`, after which nothing would write ratings. Operator decision (2026-10-05): the daemon records one cycle per trading day.

**How.**
- `pipeline/production_steps.py::_daemon_should_record_ratings(now_utc)` returns True only when all of these hold:
  - `SYMBOL_RATING_ENABLED`;
  - `engine.advisory_agent.is_us_market_open_now` (holiday-aware, the same check the broker step uses);
  - `SymbolRatingStore.has_cycle_since(<midnight America/New_York today>)` is False.

  Any exception means "don't write". The `_record_symbol_ratings` call in `StrategyEvalStep.run()` sits under this gate, inside its existing try/except.
- `rating/symbol_rating_store.py::has_cycle_since` excludes `manual_reinclude` rows, counts NULL cycle ids, and accepts aware or naive-UTC input. It raises on DB errors so the gate fails closed.

**Behavior.**
- **While `main.py` runs:** its 08:47 ET cycle lands before the 09:30 open, so the daemon logs "already recorded today" and writes nothing. That is no change on normal days.
- **If `main.py` misses a day:** the first daemon cycle during market hours fills it.
- **After step 5.5:** the daemon is the daily writer.
- **No double writes:** daemon cycles are single-flight.

**Tests.**
- `tests/test_symbol_rating_wiring.py::TestDaemonDailyRatingCadence`:
  - market closed;
  - open with no cycle;
  - main.py's cycle earlier today;
  - yesterday's cycle;
  - the ET midnight boundary;
  - manual_reinclude;
  - a second same-day cycle;
  - a store error;
  - ratings disabled;
  - an AST guard.
- `tests/test_symbol_rating.py::TestHasCycleSince`.

**Audit follow-ups (honesty-auditor pass).**
- `_record_symbol_ratings` now returns the number of rows written, and the log reports that count. It previously said "recorded" even when no row was rateable.
- Disclosed limit, documented in the gate's docstring: the check and the write are not one transaction. Daemon cycles are single-flight, so the only possible overlap is a manual `main.py` run during market hours, which could add a second cycle that day.
