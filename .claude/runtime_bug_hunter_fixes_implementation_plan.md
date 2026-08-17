# Runtime Bug Hunter Fixes — Implementation Plan

This plan documents the resolution of five distinct issues discovered during the automated Bug Hunter and live network quality audit.

## User Review Required

> [!NOTE]
> All changes preserve existing API signatures and calculation contracts while fixing regressions and hardening error boundaries.

## Proposed Changes

---

### 1. Test Session Settings Isolation

#### [MODIFY] [conftest.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_unknown_runtime_bug/conftest.py)
- Update `conftest.py` singleton reset to initialize with `_defaults = Settings(_env_file=None)`. This ensures local `.env` secrets (`STATE_API_TOKEN`, custom flags) do not leak into default test assertions.

---

### 2. Preflight Gate Database Resolution

#### [MODIFY] [scripts/preflight_check.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_unknown_runtime_bug/scripts/preflight_check.py)
- Update `check_db_exists()` to resolve the canonical SQLite path via `db_config.resolve_database_url()` and `settings.LOCAL_DATA_ROOT`, with fallback to `_REPO_ROOT / "quant_platform.db"`.
- Update `tests/test_preflight.py` test cases to verify canonical and fallback resolution.

---

### 3. Universe Engine Wikipedia Changes Table Handling

#### [MODIFY] [universe_engine.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_unknown_runtime_bug/universe_engine.py)
- Use `pd.read_html(io.StringIO(resp.text))` to prevent pandas deprecation warning while maintaining exception contracts and stale-cache fallback.

---

### 4. CPCV OOS Metric NaN Handling

#### [MODIFY] [validation/metrics.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_unknown_runtime_bug/validation/metrics.py)
- In `run_cpcv_evaluation()`, store `oos_sr` directly in `oos_sharpes` without forcing NaN to `-999.0`.
- Compute `mean_oos_sharpe` handling `NaN` cleanly (evaluating to `np.nan` when all trials are degenerate).
- Update `validation/autonomous_backtest_runner.py` similarly.

---

### 5. Settings and Environment Declarations

#### [MODIFY] [settings.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_unknown_runtime_bug/settings.py)
- Declare `NO_VENV_REEXEC: bool = False` in `Settings`.

#### [MODIFY] [.env.example](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_unknown_runtime_bug/.env.example)
- Document `NO_VENV_REEXEC` in `.env.example`.

---

### 6. Missing Module Docstrings

#### [MODIFY] [broker_live_execution_mcp.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_unknown_runtime_bug/broker_live_execution_mcp.py)
#### [MODIFY] [execution/almgren_chriss_router.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_unknown_runtime_bug/execution/almgren_chriss_router.py)
#### [MODIFY] [ml/transformer_vol_forecaster.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_unknown_runtime_bug/ml/transformer_vol_forecaster.py)
#### [MODIFY] [numba_backtest_loop.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_unknown_runtime_bug/numba_backtest_loop.py)
#### [MODIFY] [sizing/hrp_cvar_optimizer.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_unknown_runtime_bug/sizing/hrp_cvar_optimizer.py)
#### [MODIFY] [validation/synthetic_diffusion_engine.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_unknown_runtime_bug/validation/synthetic_diffusion_engine.py)
- Add comprehensive module-level docstrings describing roles and contracts.

---

### 7. Documentation & Incident Logs

#### [MODIFY] [docs/incident_log.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/investigate_unknown_runtime_bug/docs/incident_log.md)

---

## Verification Plan

### Automated Tests
```bash
# 1. Targeted tests for modified components
pytest tests/test_preflight.py
pytest tests/test_state_api.py
pytest tests/test_dead_letter_resilience.py
pytest tests/test_validation_lgbm.py
pytest -v tests/test_validation_*.py -m "not network"

# 2. Codebase static auditor & typecheck
python3 scripts/auditor/stockpy_codebase_auditor.py --root . --fail-on HIGH
npm run --prefix webapp typecheck
```
