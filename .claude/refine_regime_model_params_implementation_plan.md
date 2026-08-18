# Refine Regime Model Parameters, Feature Engineering, Risk Gates & Diagnostics

This implementation plan refines and tunes the platform's Gaussian Hidden Markov Model (HMM) regime detector ([`regime/hmm_regime.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/regime/hmm_regime.py)), expands feature engineering capabilities, calibrates downstream macro risk gates ([`dto_models.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/dto_models.py), [`execution/risk_gate.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/execution/risk_gate.py)), and delivers an empirical validation & diagnostic suite ([`validation/regime_diagnostics.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/validation/regime_diagnostics.py), [`scripts/audit_regime_model.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/scripts/audit_regime_model.py)).

---

## User Review Required

> [!NOTE]
> All new parameters ship with default values that strictly preserve current production behavior and ensure 100% backward compatibility.

- **Covariance Structure**: `settings.HMM_COVARIANCE_TYPE` default remains `"diag"` for backward compatibility, with full support added for `"full"` (which achieves lower AIC/BIC in empirical testing), `"spherical"`, and `"tied"`.
- **Feature Matrix Expansion**: `build_feature_matrix` maintains its 4-feature signature (`spy_return`, `realized_vol_20d`, `vix_level`, `yield_curve_spread`) while supporting optional credit spread (`BAMLH0A0HYM2`) and vol-term spread features.
- **DTO Threshold Configuration**: Hardcoded class constants in `MacroEconomicDTO` (`HMM_RISK_ON_DOWNGRADE_THRESHOLD`, `HMM_RISK_OFF_AGREEMENT_THRESHOLD`, `KILLSWITCH_VIX_THRESHOLD_AGREED`, `KILLSWITCH_SAHM_THRESHOLD_AGREED`) will be dynamically sourced from `settings` with fallback to their existing defaults.

---

## Proposed Changes

### 1. Settings & Configuration (`settings.py`, `gui/env_io.py`)

#### [MODIFY] [`settings.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/settings.py)
- Add new configurable settings under `# --- HMM regime detector ---`:
  - `HMM_COVARIANCE_TYPE: str = Field(default="diag", description="Covariance type for Gaussian HMM: diag, full, spherical, tied.")`
  - `HMM_N_ITER: int = Field(default=150, description="Max EM iterations for Gaussian HMM fitting.")`
  - `HMM_TOL: float = Field(default=1e-4, description="Convergence threshold for Gaussian HMM EM algorithm.")`
  - `HMM_RISK_ON_DOWNGRADE_THRESHOLD: float = Field(default=0.30, description="Threshold below which RISK ON is downgraded to NEUTRAL.")`
  - `HMM_RISK_OFF_AGREEMENT_THRESHOLD: float = Field(default=0.70, description="Threshold above which HMM confirms recession for faster kill switch.")`
  - `HMM_CREDIT_SPREAD_FEATURE_ENABLED: bool = Field(default=False, description="Include HY OAS credit spread in HMM feature matrix.")`
  - `HMM_VOL_TERM_SPREAD_FEATURE_ENABLED: bool = Field(default=False, description="Include 20D-60D vol term structure in HMM feature matrix.")`

#### [MODIFY] [`gui/env_io.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/gui/env_io.py)
- Add new non-secret tunables (`HMM_COVARIANCE_TYPE`, `HMM_N_ITER`, `HMM_TOL`, `HMM_RISK_ON_DOWNGRADE_THRESHOLD`, `HMM_RISK_OFF_AGREEMENT_THRESHOLD`, `HMM_CREDIT_SPREAD_FEATURE_ENABLED`, `HMM_VOL_TERM_SPREAD_FEATURE_ENABLED`) to `ALLOWED_KEYS`.

---

### 2. HMM Regime Engine (`regime/hmm_regime.py`, `regime/__init__.py`)

#### [MODIFY] [`regime/hmm_regime.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/regime/hmm_regime.py)
- **`build_feature_matrix()`**:
  - Add optional parameters `credit_spread_series: Optional[pd.Series] = None` and `include_vol_term_spread: bool = False`.
  - Ensure zero lookahead: contemporaneous `.rolling()` windows and datetime normalization.
- **`HMMRegimeDetector`**:
  - Accept `covariance_type: str = "diag"`, `n_iter: int = 150`, `tol: float = 1e-4` in `__init__`.
  - In `identify_states_by_vol()`: Robustly calculate variance per state across all covariance types using trace / diagonal variances.
  - Add `compute_diagnostics(features_df) -> Dict[str, Any]` returning:
    - Log-Likelihood, AIC, BIC
    - Transition matrix $P_{ij}$ and expected state durations ($1 / (1 - P_{ii})$)
    - Per-state empirical return mean, volatility, and Sharpe ratio.
    - Stationary distribution $\pi$.

---

### 3. Macro Engine & DTO Models (`macro_engine.py`, `dto_models.py`)

#### [MODIFY] [`macro_engine.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/macro_engine.py)
- Initialize `HMMRegimeDetector` with `HMM_COVARIANCE_TYPE`, `HMM_N_ITER`, `HMM_TOL` from `settings`.
- In `compute_hmm_risk_on_probability`: Fetch `BAMLH0A0HYM2` credit spread from `HistoricalStore` / `DataEngine` when `HMM_CREDIT_SPREAD_FEATURE_ENABLED` is enabled and pass to `build_feature_matrix`.

#### [MODIFY] [`dto_models.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/dto_models.py)
- In `MacroEconomicDTO`: Dynamically reference `settings.HMM_RISK_ON_DOWNGRADE_THRESHOLD` and `settings.HMM_RISK_OFF_AGREEMENT_THRESHOLD` (with fallback to class defaults).

---

### 4. Regime Diagnostics & Historical Audit Tooling

#### [NEW] [`validation/regime_diagnostics.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/validation/regime_diagnostics.py)
- Modular diagnostic engine:
  - `run_walk_forward_evaluation(features_df, n_states, covariance_type, retrain_freq_days, min_fit_rows)`: Simulates exact causal forward-filtering walk-forward regime prediction across history.
  - `evaluate_state_performance(walk_forward_df)`: Calculates Annualized Return, Volatility, Sharpe, Sortino, and Max Drawdown per regime.
  - `compare_model_configurations(features_df, state_counts, covariance_types)`: Compares AIC, BIC, Log-Likelihood across models.

#### [NEW] [`scripts/audit_regime_model.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/scripts/audit_regime_model.py)
- CLI entrypoint (`python -m scripts.audit_regime_model [--compare] [--json] [--output PATH]`):
  - Fetches SPY and macro history from `HistoricalStore` / SQLite.
  - Runs full walk-forward regime diagnostic and prints formatted Markdown/ASCII tables of regime distributions, performance stats, and transition matrices.

---

### 5. Automated Tests

#### [NEW] [`tests/test_regime_diagnostics.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/tests/test_regime_diagnostics.py)
- Tests for `validation/regime_diagnostics.py` and diagnostic computations.

#### [MODIFY] [`tests/test_hmm_synthetic.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/tests/test_hmm_synthetic.py)
- Add tests for `covariance_type="full"`, `"spherical"`, `"tied"` and `compute_diagnostics()`.

#### [MODIFY] [`tests/test_macro_hmm_integration.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/tests/test_macro_hmm_integration.py)
- Add tests for dynamic threshold configuration and feature expansion flags.

---

### 6. Documentation Updates (Mandatory Step)

#### [MODIFY] [`docs/architecture/signal-engines.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/docs/architecture/signal-engines.md)
- Update `regime/hmm_regime.py` and `macro_engine.py` sections with the new hyperparameter configurations, covariance types, and diagnostic tools.

#### [MODIFY] [`CLAUDE.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/CLAUDE.md) & [`AGENTS.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/refine_regime_model_params/AGENTS.md)
- Document the new regime configuration settings and audit script.

---

## Verification Plan

### Automated Tests
```bash
# Run targeted HMM and Macro regime test suite
pytest tests/test_hmm_no_lookahead.py tests/test_hmm_synthetic.py tests/test_hmm_state_persistence.py tests/test_macro_hmm_integration.py tests/test_regime_diagnostics.py -v

# Run full project test suite
pytest -v
```

### Empirical Diagnostic Verification
```bash
# Run regime model audit script against historical SQLite data
python -m scripts.audit_regime_model --compare
```
