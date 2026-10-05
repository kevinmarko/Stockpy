# Plan: daemon writes symbol ratings once per trading day

## Context
Symbol-rating auto-drop is live: a symbol not held is excluded after 5 consecutive BAD rating cycles. Today only `main.py` writes ratings, once per weekday at about 08:47 ET (the DB shows exactly one cycle of 30 rows per day). The daemon computes the same scores hourly, but #1105 switched its writes off (`_DAEMON_RECORDS_SYMBOL_RATINGS = False`). Writing every hour would make "5 cycles" mean about 5 hours.

When step 5.5 archives `main.py`, nobody will write ratings and auto-drop will freeze. **Operator decision (2026-10-05): the daemon writes ratings once per trading day.**

## Design
Replace the constant with a cadence gate, `_daemon_should_record_ratings(now_utc) -> bool`, in `pipeline/production_steps.py`. It returns True only when both of these hold:
1. **The US market is open now:** `engine.advisory_agent.is_us_market_open_now(now_utc)`. This is the holiday- and early-close-aware check the broker step already uses (`main_orchestrator.py:584`), so the write is skipped on weekends and holidays and uses intraday-fresh scores.
2. **No rating cycle has been recorded yet today (ET).** A new read-only store method, `SymbolRatingStore.has_cycle_since(utc_start)`, checks for a row with `timestamp >= utc_start AND cycle_id <> 'manual_reinclude'`. `utc_start` is midnight ET today, converted to naive UTC to match how `record_ratings` stores timestamps.

Effect:
- **While `main.py` still runs:** its 08:47 ET write lands before the market opens at 09:30. The daemon finds today's cycle and skips, so there's no change on normal days. If `main.py` fails or doesn't run, the first daemon cycle during market hours fills the gap, so ratings stay at one per day.
- **After step 5.5:** the first daemon cycle during market hours each trading day writes the one daily cycle, which keeps auto-drop at about 5 trading days.

Concurrency: daemon cycles are single-flight (a second one gets a 409), and `main.py` runs before the open, so the gate can't double-write.

Failure handling (CONSTRAINT #6): any exception in the gate means "don't write" and logs a warning, and the cycle continues. A store read error therefore skips the write and never duplicates a cycle. The call site keeps its existing try/except.

## Changes
- `pipeline/production_steps.py`:
  - remove `_DAEMON_RECORDS_SYMBOL_RATINGS`;
  - add `_daemon_should_record_ratings` with a comment explaining the cadence;
  - the call site becomes `if _daemon_should_record_ratings(datetime.now(timezone.utc)): _record_symbol_ratings(...)`, inside the existing try/except;
  - log at INFO when it writes and when it skips (`already recorded today` / `market closed`).
- `rating/symbol_rating_store.py`: add the `has_cycle_since(utc_start: datetime) -> bool` read method. It also works on a read-only store.

## Tests
- `tests/test_symbol_rating_wiring.py`: replace `TestDaemonRatingWritesStayOff` with gate tests, monkeypatching `is_us_market_open_now` and using the store in the isolated test DB:
  - market closed → False;
  - open with no row today → True;
  - open with a row written earlier today ET (e.g. 08:47 ET) → False;
  - a row from yesterday only → True;
  - only a `manual_reinclude` row today → True;
  - the store raises → False;
  - ET-midnight boundary: 23:30 ET yesterday doesn't count for today.
  - An AST guard that the `_record_symbol_ratings` call is under `if _daemon_should_record_ratings(...)`.
- Store test for `has_cycle_since` in the existing symbol-rating store test file.

## Docs
- `docs/known_issues/daemon_strategy_score_always_nan.md`: update the "rating writes" section from "off" to the once-per-trading-day gate.
- `.claude/shrink_step5_retire_main_py_implementation_plan.md`: mark the step 5.5 rating prerequisite as resolved.
- `docs/architecture/signal-engines.md` or `orchestration-entrypoints.md`, wherever the rating store is described: one line on the cadence.
- PR artifacts: `.claude/daemon_daily_symbol_ratings_{implementation_plan,task,walkthrough}.md`. No CLAUDE.md change.

## Verification
- Targeted tests: `tests/test_symbol_rating_wiring.py`, the store tests, `tests/test_production_steps_symbol_rating.py`, then `make ci` and the ruff gate. Run an honesty-auditor pass on the diff.
- PR on branch `daemon-daily-symbol-ratings`, merged once green. Then **ask before** restarting the daemon.
- After the restart: during market hours the log should say "already recorded today", and `symbol_rating_events` should still have exactly one cycle for today (main.py's).
