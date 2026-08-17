# Runtime Bug Hunter Fixes — Walkthrough

## Summary of Changes

A comprehensive quality audit using `scripts/bug_hunter.py`, `scripts/auditor/stockpy_codebase_auditor.py`, `scripts/preflight_check.py`, and the targeted test suite revealed several subtle runtime edge cases and developer-environment leaks. All identified issues have been remediated, verified, and documented.

---

### 1. Test Session Settings Isolation (`conftest.py`)
- **Problem**: When running tests in a developer environment where `.env` contains live credentials or custom flags (`STATE_API_TOKEN`, `VALIDATION_HARNESS_OOS_GATE_ENABLED`), `Settings()` in `conftest.py` loaded those live secrets and copied them onto the global `settings` singleton, causing unauthenticated API tests (`test_state_api.py`, `test_pilots_api.py`) to fail with 401 Unauthorized.
- **Solution**: Changed `Settings()` in `conftest.py` to `Settings(_env_file=None)`, ensuring clean default values across all test suites.

### 2. Preflight Gate Canonical Database Resolution (`scripts/preflight_check.py`, `db_config.py`, `tests/test_preflight.py`)
- **Problem**: `check_db_exists()` previously looked only at `_REPO_ROOT / "quant_platform.db"`, which failed on new worktrees where the canonical database is located under `settings.LOCAL_DATA_ROOT / "quant_platform.db"`.
- **Solution**: Updated `check_db_exists()` to resolve the database path canonically from `settings.DATABASE_URL` / `settings.LOCAL_DATA_ROOT` with legacy repo-root fallback. Made `db_config.resolve_database_url()` dynamically read `settings.LOCAL_DATA_ROOT`. Updated `TestDbExists` in `tests/test_preflight.py` to verify both canonical and fallback paths.

### 3. Universe Engine Wikipedia Scraper Hardening (`universe_engine.py`)
- **Problem**: Passing raw HTML to `pd.read_html(resp.text)` triggered pandas deprecation warnings.
- **Solution**: Wrapped HTML in `io.StringIO(resp.text)` to eliminate deprecation warnings while maintaining strict dead-letter fallback and exception contracts.

### 4. CPCV OOS Metric NaN Handling (`validation/metrics.py`, `validation/autonomous_backtest_runner.py`)
- **Problem**: `validation/metrics.py` previously injected `-999.0` sentinels into `oos_sharpes`, causing `mean_oos_sharpe` to distort to `-999.0` instead of `np.nan` when evaluating degenerate trials.
- **Solution**: Preserved genuine `np.nan` in `oos_sharpes` and computed `mean_oos_sharpe` using nan-filtered distributions, strictly conforming to CONSTRAINT #4 (never fabricate metrics).

### 5. Undeclared Environment Variable & Module Docstrings (`settings.py`, `.env.example`, 6 Modules)
- **Problem**: `NO_VENV_REEXEC` in `scripts/_bootstrap.py` was not declared in `settings.py` or `.env.example`. 6 modules lacked top-level docstrings.
- **Solution**: Added `NO_VENV_REEXEC: bool = False` to `settings.py` and documented in `.env.example`. Added complete module docstrings across `broker_live_execution_mcp.py`, `execution/almgren_chriss_router.py`, `ml/transformer_vol_forecaster.py`, `numba_backtest_loop.py`, `sizing/hrp_cvar_optimizer.py`, and `validation/synthetic_diffusion_engine.py`.

---

## Verification Results

### Automated Test Suite Runs
- `pytest tests/test_state_api.py`: **28 passed / 28**
- `pytest tests/test_preflight.py`: **140 passed / 140**
- `pytest tests/test_dead_letter_resilience.py`: **34 passed / 34**
- `pytest tests/test_validation_lgbm.py`: **2 passed / 2**
- `pytest -v tests/test_validation_*.py -m "not network"`: **90 passed / 90**
- `npm run --prefix webapp typecheck`: **0 TypeScript errors**
- `python3 scripts/auditor/stockpy_codebase_auditor.py --root . --fail-on HIGH`: **0 Critical, 0 High, 0 Medium**
