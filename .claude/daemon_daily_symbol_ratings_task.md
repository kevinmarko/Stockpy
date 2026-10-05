# Task: daemon writes symbol ratings once per trading day
- [x] `SymbolRatingStore.has_cycle_since` (ignores manual_reinclude, counts NULL cycle ids, raises on error)
- [x] `_daemon_should_record_ratings` gate (ratings enabled, market open, no cycle yet today ET, fail closed) replacing `_DAEMON_RECORDS_SYMBOL_RATINGS`
- [x] Tests: TestDaemonDailyRatingCadence (10), TestHasCycleSince (6)
- [x] Docs: known issue, known-issues index, step-5 plan note, testing.md
- [x] make ci (11765 passed) + ruff clean; honesty audit (count-accurate log, disclosed race)
- [x] PR, merge when green
- [ ] Ask before restarting the daemon; confirm "already recorded today" in the log during market hours
