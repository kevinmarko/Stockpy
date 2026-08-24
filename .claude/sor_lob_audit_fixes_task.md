# Task tracker: options_sor.py / lob_simulator.py audit fixes

Branch: `fix-sor-lob-simulator-audit-findings`

- [x] Fix #1: `options_sor.py:558` — wrap `active_leg["delta"]` in `abs()`.
- [x] Regression test for #1 (`tests/test_options_sor.py`) — confirmed fails
      pre-fix, passes post-fix.
- [x] Fix #2: `lob_simulator.py` no-depth `mu_cancel` fallback — unit
      consistency (per-share normalization instead of raw events/sec).
- [x] Fix #3: `lob_simulator.py` primary-branch `mu_cancel` — use
      `total_sizes["CANCEL"]` (canceled shares) instead of event count.
- [x] Regression tests for #2/#3 (`tests/test_lob_simulator.py`) — both
      confirmed fail pre-fix, pass post-fix.
- [x] Confirmed existing `test_compute_lob_arrival_rates_synthetic_exact`
      (`mu_cancel == 0.20`) unaffected by Fix #3 (its CANCEL records all have
      `size=1.0`, so `total_sizes == counts`).
- [x] Fix #4: corrected `pilots/lob_simulator.py` module docstring's
      CONSTRAINT #4 claim to disclose zero production callers of
      `compute_lob_arrival_rates()` and the live endpoint's fixed-constant
      behavior.
- [x] Confirmed item #4 full-wiring is genuinely out of scope (no L2/L3/
      bid-ask-size data source anywhere in the data layer) rather than
      assumed.
- [x] Updated `docs/architecture/execution.md`'s `options_sor.py` and
      `lob_simulator.py` bullets.
- [x] New `docs/known_issues/lob_simulator_uncalibrated_live_arrival_rates.md`.
- [x] Added row to `docs/known_issues/README.md`.
- [x] Full targeted suite green: `pytest tests/test_options_sor.py
      tests/test_lob_simulator.py -q`.
- [x] PR artifacts (this file + implementation plan + walkthrough) committed
      under `.claude/`.
- [x] Flag items #5/#6 as a follow-up via `spawn_task`.
- [x] Open PR `fix-sor-lob-simulator-audit-findings` → `main`.
