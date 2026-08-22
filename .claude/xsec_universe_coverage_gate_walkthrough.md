# Universe-coverage visibility + fail-closed gate — Walkthrough

## The bug, in one sentence

A cross-sectional strategy's whole PBO/DSR/Sharpe/MaxDD/`deployable` verdict silently
depended on exactly which random subset of its ~500-ticker declared universe happened to
download successfully that particular run, with nothing in the report to tell you.

## How it was found and confirmed

An audit noticed `cross_sectional_momentum`/`sector_quality_rank`'s `deployable`
verdicts swinging wildly (PBO 0.11→0.69, Sharpe 0.68→1.01) across dozens of real runs in
the durable `validation_runs` DB table over ~36 hours with zero code changes. Rather than
guessing, `cross_sectional_momentum` was run three times: once inside a heavily
concurrent `--workers 6` full-registry run (whose own log showed FMP's rate-limit
cooldown engaging repeatedly), then twice more in complete isolation. All three produced
**bit-identical output** — proving the strategy logic itself is deterministic, and that
the swings come entirely from *other* concurrent runs on this shared machine hitting
FMP's cooldown at different points, each ending up with a differently-incomplete
universe.

## The fix

`scripts/refresh_validations.py`'s `_validate_single_strategy` already computes
`available` (tickers that actually downloaded) vs. `universe` (the strategy's declared
list) — that comparison is reused, not reinvented, to build a
`{"requested", "fetched", "coverage_pct", "missing"}` dict, threaded through
`StrategyValidationHarness.run(universe_coverage=...)` into `ValidationReport`. A new
`universe_coverage_ok` property (coverage `>= 90%`, `validation.thresholds.MIN_UNIVERSE_COVERAGE_PCT`)
is ANDed into `deployable` alongside the existing PBO/DSR/Sharpe/MaxDD/stress gates —
fail-closed, matching this repo's dominant CONSTRAINT #6 convention. `None` (untracked)
coverage is treated as not-applicable, preserving every existing caller's behavior
exactly.

Visibility follows the fix everywhere a validation result is read: the JSON summary
(`to_summary_dict()`), the HTML report (a new "Universe Coverage" card, real end-to-end
Jinja2-rendered and tested), the CLI pass/fail table (`Coverage` column + a
coverage-shortfall reason reported first in `_fail_reason`), and the `--json` CLI output.

## Why unconditional, not a settings flag

Unlike `VALIDATION_HARNESS_OOS_GATE_ENABLED` (which changed the computation methodology
for every run and needed an opt-in flag to avoid silently invalidating the whole
registry's recorded numbers before re-verification), this gate only changes the verdict
on a genuinely new, narrow failure mode. A fully-covered run — the normal case for an
isolated run — is bit-identical to before. Making this opt-in would just perpetuate the
exact invisible-failure-mode problem the fix exists to close.

## What's still open (disclosed, not silently dropped)

- The other 5 tiered-universe strategies (`relative_strength_xsec`,
  `multifactor_lowvol_size`, `macro_regime_pit`, `signal_replay_balanced_blend`,
  `lgbm_ranker`) carry the same unverified-coverage caveat on their existing recorded
  numbers but were not re-run as part of this PR.
- A cross-worktree FMP rate-limit coordination mechanism (to reduce how often the gate
  actually trips under concurrent load, not just make it visible when it does) is
  documented as a disclosed follow-up in the new known-issues doc, not implemented here.

## Files touched

- `validation/thresholds.py`, `validation/harness.py`,
  `reports/validation_report_template.html.j2`, `scripts/refresh_validations.py`
- `tests/test_universe_coverage_gate.py` (new), `tests/test_refresh_validations.py`
  (extended)
- `docs/architecture/validation-and-signals.md`,
  `docs/known_issues/xsec_universe_coverage_concurrency_variance.md` (new),
  `docs/VALIDATION_STRATEGY_FIX_LOG.md`, `docs/signals/cross_sectional_momentum.md`,
  `docs/signals/sector_quality_rank.md`
- `.claude/xsec_universe_coverage_gate_implementation_plan.md`,
  `.claude/xsec_universe_coverage_gate_task.md`,
  `.claude/xsec_universe_coverage_gate_walkthrough.md` (this file)

## Verification

- `tests/test_universe_coverage_gate.py` + extended `tests/test_refresh_validations.py`:
  all green (16 + 150 tests respectively).
- Targeted regression suite across every harness-adjacent test file: 300 passed.
- `ruff check . --select=F821,F822,F823,E9`: clean.
- Full offline suite: 11935 passed, 13 skipped, 0 failed.
- **Live isolated re-run** (`--strategies cross_sectional_momentum,sector_quality_rank
  --start 2005-01-01 --n-cpcv-splits 15 --n-test-splits 4 --workers 1 --json`): both
  strategies achieved **100% price-data coverage** (504/504, 100/100) despite a
  concurrent `lgbm_ranker` validation job (not started by this session) also running on
  this shared machine at the time — the new `universe_coverage`/`universe_coverage_ok`
  fields worked end-to-end against real data. Two genuine reversals surfaced, both
  documented in full with before/after tables in `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s
  2026-08-22 entry and the two strategies' own `docs/signals/*.md` addenda:
  - `cross_sectional_momentum`: PBO 0.492 (2026-08-21, unverified coverage) →
    **0.592** (coverage-verified) — flips `deployable=True → False`.
  - `sector_quality_rank`: MaxDD 34.2% (2026-08-21, unverified coverage) →
    **21.9%** (coverage-verified) — flips `deployable=False → True`.
  - Honest disclosed gap found in the process: `sector_quality_rank`'s own EDGAR-
    fundamentals fetch (separate from the FMP price fetch this fix tracks) genuinely
    timed out for one ticker (BBY) in this very run despite 100% price coverage —
    confirming this fix's coverage field is price-data-scoped, not a general
    any-missing-data tracker; documented as a disclosed, out-of-scope follow-up rather
    than silently left unstated.
