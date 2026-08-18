# CPCV NaN Honesty Fix — Task Tracker

- [x] Fix `probability_of_backtest_overfitting()` NaN-comparison bug (`validation/metrics.py`) <!-- id: 1 -->
- [x] Fix IS-Sharpe `-999.0` sentinel + fabricated `0.0` mean in `validation/autonomous_backtest_runner.py::run_cpcv()` <!-- id: 2 -->
- [x] Reapply 5 accurate module docstrings from #786 <!-- id: 3 -->
- [x] Correct `ml/transformer_vol_forecaster.py` docstring (drop false quantile claim) <!-- id: 4 -->
- [x] Add regression tests (`tests/test_pbo.py`, `tests/test_autonomous_backtest_runner.py`) <!-- id: 5 -->
- [x] Update `docs/architecture/ml-and-reports.md` and `docs/architecture/validation-and-signals.md` <!-- id: 6 -->
- [x] Run targeted + full offline test suite, ruff genuine-bug lint <!-- id: 7 -->
- [x] Commit and open Pull Request <!-- id: 8 --> (#793)
- [x] Comment on #786 pointing to this PR, then close #786 as superseded <!-- id: 9 -->
