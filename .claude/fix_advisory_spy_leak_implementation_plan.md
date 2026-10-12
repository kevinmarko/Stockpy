# Plan: stop the benchmark SPY leaking into the daemon's per-symbol pipeline

Branch: `fix-advisory-spy-leak` (already created from `origin/main` @ 8a2ed8f0). Tier: pipeline/runtime, so it goes through a PR. Freeze: this is a bug fix, so the freeze allows it.

## Context

The step-5 primary cutover is on hold. Its 2026-10-05 review listed "SPY leaks into AdvisoryOverlayStep" as a blocker, and step 5.5 (archive `main.py`) can't land until the cutover happens.

**Root cause.** `main_orchestrator.fetch_all_data_async` (main_orchestrator.py:279) adds `"SPY"` to the tech fetch. Four things need it:
- the HMM (`production_steps.py:280`);
- RS-vs-SPY and beta (`processing_engine.py:135, 269`);
- Dual Momentum (`production_steps.py:2552`).

The problem is that `ProcessingStep` then builds `dashboard_df` from **every** `tech_raw` key (`pe.calculate_technical_metrics(ctx.tech_raw)` → `compile_dashboard`, `production_steps.py:414-435`). So SPY becomes a full universe row even when nobody holds it or watches it. From there it reaches every downstream stage:
- **Advisory/queue.** `AdvisoryOverlayStep` evaluates it on purpose ("dashboard symbol(s) not in the cycle universe (evaluated anyway): SPY", logged every cycle). It is in the live `shadow/queue_sources/advisory.json`, and it could reach the real queue after cutover.
- **Paper broker.** The live DB has a `main_pipeline` SPY round trip: 2 orders and 1 closed trade, P&L −$4.87. **It counts toward the feature-freeze tally.** I won't touch it; whether to exclude it is your call.
- **Cross-sectional ranks.** `_compute_xsec_momentum(ctx.tech_raw)` (`production_steps.py:1922`) ranks SPY alongside the universe. `main.py`'s xsec (`pipeline/advisory_inputs.py`) uses only universe bars, so this is also a source of daemon-vs-main.py score drift.
- **Everything else.** Forecasting, strategy eval, ratings and the state snapshot each also get a SPY row.

Trend/vol already iterates `ctx.symbols`, so it's correct today.

**Outcome:** SPY stays in `tech_raw` as benchmark data. It becomes a dashboard/universe row only when it is really in `ctx.symbols` (held, watchlist, discovered or retained).

## Changes

1. **`main_orchestrator.py`**: name the injected list as a module constant, `BENCHMARK_SYMBOLS = ("SPY",)`, and use it at line 279. No behaviour change.

2. **`pipeline/production_steps.py`**: add a small helper:
   ```python
   def _drop_benchmark_only_rows(dashboard_df, universe) -> pd.DataFrame
   ```
   - It removes rows whose `Symbol` is in `BENCHMARK_SYMBOLS` but not in `universe`.
   - It logs one INFO line naming what it dropped.
   - It never raises; on bad input it returns the frame unchanged.
   - It is called in `ProcessingStep.run` right after `compile_dashboard`, before the universe-funnel diagnostic, so `dashboard_rows` is counted honestly.
   - *(This filter rule is the piece I'll hand you to write — see below.)*

3. **`pipeline/production_steps.py:1922`**: compute xsec momentum over the universe only:
   ```python
   _compute_xsec_momentum({t: ctx.tech_raw[t] for t in ctx.symbols if t in ctx.tech_raw})
   ```
   This matches main.py's cross-section.

4. **`AdvisoryOverlayStep._evaluate`**: no change to the "evaluated anyway" branch. After fix 2 it should be empty, and its warning stays as a tripwire if some other path ever leaks a row.

## Tests (`tests/test_production_steps_universe.py`, or a new `tests/test_benchmark_spy_leak.py`)

- SPY in `tech_raw` but not in `ctx.symbols` → no SPY row in `dashboard_df` after `ProcessingStep`. Its `RS vs SPY` for other rows is still populated, which proves SPY was still used as the benchmark.
- SPY in `ctx.symbols` (held) → the SPY row is kept.
- xsec ranks: SPY is absent from `XSec_Momentum_Rank` when it's not in the universe; universe ranks equal the ranks over universe-only bars.
- `AdvisoryOverlayStep` with the fixed dashboard → no SPY `Recommendation` and no "not in the cycle universe" warning.
- Existing `test_main_orchestrator.py::TestFetchAllDataAsync` (SPY still in `tech_raw`) stays green unchanged.

## Docs

- New `docs/known_issues/daemon_benchmark_spy_leak.md`, plus a line in `docs/known_issues/README.md`. It covers the root cause, the fix, the leaked paper trade and the freeze-count note.
- `.claude/shrink_step5_retire_main_py_implementation_plan.md` §5 Traps: mark the SPY leak resolved.
- `docs/architecture/signal-engines.md`: one sentence saying benchmark symbols live in `tech_raw` only.
- PR artifacts: `.claude/fix_advisory_spy_leak_walkthrough.md`.

## Verification

- `pytest tests/test_production_steps_universe.py tests/test_main_orchestrator.py tests/test_xsec_momentum_advisory_parity.py tests/test_orchestrator_e2e.py` plus the new tests; then `make ci` green.
- After merge (the operator restarts the daemon via `launchctl kickstart -k`), check that the next cycle has:
  - no SPY in `~/.stockpy_local/output/shadow/queue_sources/advisory.json`;
  - no "evaluated anyway: SPY" log line;
  - `universe_funnel.dashboard_rows == tracked_universe_total`.
- Then rerun `scripts/compare_shadow_queue.py` for the cutover decision.

## Not in scope (still your call before cutover)

- `ADVISORY_REUSE_PIPELINE_COMPUTE` forecast-reuse diffs.
- Extending the shadow review.
- Whether to exclude the leaked SPY trade from the freeze count.
