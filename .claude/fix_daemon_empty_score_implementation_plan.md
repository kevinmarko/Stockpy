# Fix: daemon's strategy Score is always NaN

## Context
Every daemon snapshot since 2026-08-13 (805/805) has `score = NaN` for every symbol, although `StrategyEngine.evaluate_security()` computes an integer score (`strategy_engine.py:513`) and `score_components` is populated. Root cause: `pipeline/production_steps.py` (~line 2059) NaN-fills `dashboard_df['Score']` as if it were advisory-only metadata, and `eval_results` / the write-back loop never copy `strategy_output['Score']`.

Visible effects: Symbol Detail "Score" row empty, HTML report "Advisory Score" column empty, `DailySignals.Score` NaN (breaks the MCP top-signals `ORDER BY "Score"`), and missing rows get a fabricated `0.0` in the snapshot (`main_orchestrator.py:931`, `float(... or 0.0)`).

Hidden side effect: `_record_symbol_ratings` skips NaN scores, so the daemon has never written symbol ratings. Auto-drop is ON live (5 BAD cycles in a row). Turning ratings on in the hourly daemon would make drops happen within hours. **Operator decision: keep daemon rating writes off** (today's effective behavior). Freeze-allowed: bug fix plus observability, no trading-behavior change.

## Changes (branch `fix-daemon-empty-score` off origin/main)
1. **`pipeline/production_steps.py`**
   - Add `'Score': strategy_output.get('Score')` to `eval_results[ticker]`.
   - New module-level helper `_apply_strategy_score_column(dashboard_df, eval_results)`. It writes numeric Score per symbol, NaN for tickers that were never evaluated (dead-lettered or zero price), and keeps a genuine 0. Called after the eval loop, alongside the existing write-back. This follows the `_apply_sector_heat_factor` / `_record_symbol_ratings` helper pattern.
   - Drop `Score` from the advisory-metadata NaN pre-fill and fix that comment (the other four columns stay advisory-only).
   - Add `_DAEMON_RECORDS_SYMBOL_RATINGS = False` and gate the `_record_symbol_ratings` call on it (call stays inside its try/except). The comment explains why: the daemon cadence would compress auto-drop to hours, and enabling it is deferred to step 5.5.
2. **`main_orchestrator.py:931`**: `"score": _safe_float_or_none(row.get("Score"))`. This turns NaN/absent into JSON `null` (no fabricated 0.0) and keeps the snapshot strict JSON. The helper is already imported from `reporting/state_snapshot.py`.
3. **`config.py`** Score comment: populated on both paths now.

## Tests
- New `tests/test_strategy_eval_step_score_column.py`: helper unit tests (int score round-trips, unevaluated → NaN, genuine 0 → 0.0, empty df), plus an AST guard that `StrategyEvalStep.run()` calls the helper and no longer NaN-fills Score.
- `tests/test_state_snapshot_parity.py`: orchestrator score round-trips when present and is `null` (not 0.0) when absent.
- `tests/test_symbol_rating_wiring.py`: pin `_DAEMON_RECORDS_SYMBOL_RATINGS is False`, and check that the call is gated by it. The existing try/except AST guard still passes.

## Docs
- New `docs/known_issues/daemon_strategy_score_always_nan.md` (root cause, impact, fix, the deliberate rating-write deferral) plus a row in `docs/known_issues/README.md`.
- Note in `.claude/shrink_step5_retire_main_py_implementation_plan.md`: once main.py is archived nobody writes ratings, so auto-drop goes stale unless the daemon gate is flipped with a once-per-trading-day cadence.
- PR artifacts: `.claude/fix_daemon_empty_score_{implementation_plan,task,walkthrough}.md`. No CLAUDE.md change.

## Verification
- `pytest tests/test_strategy_eval_step_score_column.py tests/test_state_snapshot_parity.py tests/test_symbol_rating_wiring.py tests/test_production_steps_symbol_rating.py tests/test_main_orchestrator.py`, then `/verify` (offline suite) before commit; honesty-auditor pass on the diff.
- PR, merge after green. Then **ask before** restarting the live daemon (`launchctl kickstart -k gui/$UID/com.investyo.stack`). After the next cycle, confirm `state_snapshot.json` signals carry numeric scores and the `symbol_rating_events` count from the daemon is unchanged.

## Follow-ups (not in this PR)
- **ADVISORY_ONLY co-write bug** (operator confirmed no manual mode switches): a settings save writes `ADVISORY_ONLY` alongside `SECTOR_HEAT_ENABLED` (23× since 9/29). Next fix.
- Odd `symbol_rating_events` batches on 10/03 10:08:55 (many cycle_ids in the same second, all score 55.0). Investigate separately.
