# Task tracker — fix-stats-audit-findings

| # | Item | Status |
|---|------|--------|
| 1 | `compute_max_drawdown` day-1-wipeout NaN → 1.0 (`validation/stress_scenarios.py`) + 3 new tests in `tests/test_stress_runner.py` | ✅ Done |
| 2 | `probability_of_backtest_overfitting` empty-input `0.0` → `NaN` (`validation/metrics.py`) + 1 new test in `tests/test_pbo.py` + grep audit of callers | ✅ Done |
| 3 | Calmar-convention documentation: comments in `validation/harness.py` + `evaluation_engine.py`, cross-referencing sentences in `docs/architecture/validation-and-signals.md` + `docs/architecture/simulation-eval-reporting.md` | ✅ Done |
| 4 | New 3-way xsec-momentum parity test (`tests/test_xsec_momentum_advisory_parity.py`), docstring/comment fixes in `pipeline/production_steps.py` + `main.py` | ✅ Done |
| 5 | (Bonus) stale Quality-factor formula fix in `docs/signals/multifactor.md` | ✅ Done |

## Verification log

- `uv run pytest tests/test_stress_runner.py tests/test_pbo.py tests/test_xsec_momentum_advisory_parity.py tests/test_xsec_momentum.py tests/test_main_multifactor_precompute.py tests/test_metrics_cpcv_oos_aggregates.py tests/test_dsr.py tests/test_harness_calmar_degenerate_guard.py tests/test_annualization_frequency.py tests/test_equity_curve_metrics.py -q` → **109 passed**
- `uv run python -m ruff check . --select=F821,F822,F823,E9` → **all checks passed**
- `uv run pytest -m "not network and not slow" --tb=short -n auto --dist loadgroup -q` (full offline CI suite, mirrors `make ci`) → **12057 passed, 13 skipped, 0 failed**
- Manual diff review of every changed file (`git diff` per file) confirmed: zero logic/behavior changes in the two Calmar-comment files and the two xsec-momentum docstring files; the two bug-fix files (`validation/stress_scenarios.py`, `validation/metrics.py`) changed exactly the lines the plan specified.

## Execution mechanics

Implemented via 4 parallel subagents (one per numbered finding above, #5
folded into the #3 agent), each scoped to a disjoint set of files to avoid
merge conflicts. All 4 completed successfully; no manual fixups were needed
beyond the standard review pass.
