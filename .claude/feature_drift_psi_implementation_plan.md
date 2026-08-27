# Feature Drift PSI Detection Implementation Plan

## Goal
Implement input/feature-distribution drift detection (PSI) to detect when the distribution of features used by quantitative models shifts significantly, potentially invalidating model assumptions.

## Proposed Changes

### 1. New Module: validation/covariate_drift.py
- **compute_psi(reference, current, n_buckets=10) -> float**: Compute Population Stability Index between a reference and current distribution.
- **PSIResult**: Frozen dataclass with `drift_detected`, `psi`, `feature`, `details`. Degrades gracefully (psi=None, drift_detected=False) on short input sequences or zero-variance buckets.
- **Constants**: `PSI_ALERT_THRESHOLD = 0.25`
- **adapt_symbol_history_to_windows(df, column, reference_size=60, recent_size=20)**: Helper to split historical data into reference and current windows.
- **check_and_alert_feature_drift(df, columns, send_alert_fn=None)**: Calculate PSI across features and dispatch alerts via provided callback if threshold exceeded.

### 2. Configuration: settings.py
- Add `FEATURE_DRIFT_PSI_ENABLED: bool = Field(default=False, description="Enable Population Stability Index check for feature drift")` to settings.

### 3. Preflight Check: scripts/preflight_check.py
- Add `check_feature_drift()`: Runs PSI drift checks on key strategy features if enabled.
- Logs warnings (does not fail preflight) when drift is detected.

### 4. Tests: tests/test_covariate_drift.py
- Tests for `compute_psi` (known shifts, identical distributions).
- Tests for graceful degradation on short input or zero variance.
- Tests for `check_and_alert_feature_drift` alert dispatching logic.

### 5. Documentation
- Update `docs/architecture/validation-and-signals.md` to cover covariate drift and PSI.
- Sync `CLAUDE.md` and `AGENTS.md`.

## Verification Plan
- Run `pytest tests/test_covariate_drift.py`.
- Run `scripts/preflight_check.py` to ensure it warns when drift is present and flag is enabled, but doesn't block otherwise.
