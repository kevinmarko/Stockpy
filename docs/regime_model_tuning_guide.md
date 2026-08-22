# Regime Model Tuning Guide

## Theoretical Foundation
The regime model is based on the Hamilton (1989) Gaussian Hidden Markov Model (HMM). It seeks to identify latent market regimes (e.g., bull, bear, high volatility, low volatility) from observable market data like returns and realized volatility.

## Hyperparameter Definitions, Covariance Structures, EM Convergence, and Regularization
- **Hyperparameters:** The model requires specifying the number of latent states.
- **Covariance Structures:** `HMMRegimeDetector`'s `covariance_type` (`settings.HMM_COVARIANCE_TYPE`) supports all four of hmmlearn's structures:
  - `diag` (**default**) — one variance per feature per state (no cross-feature correlation). Safe, well-tested, and the recommended default.
  - `full` — a full covariance matrix per state (models cross-feature correlation). More expressive, more parameters to fit; also safe.
  - `spherical` — a single scalar variance per state, broadcast across all features. As of a 2026-08 fix (see `docs/known_issues/hmm_regime_state_mislabeling_spherical_tied.md`), state labeling ("bull"/"sideways"/"bear") is correct; safe to use.
  - `tied` — a single covariance matrix shared across ALL states. As of the same fix, state *labeling* is correct, but `tied` has a separate, structural limitation for regime detection specifically: forcing one shared covariance directly conflicts with distinguishing regimes whose defining characteristic IS different variance (e.g. calm vs. turbulent). Verified empirically on synthetic bull/bear data (`tests/test_hmm_synthetic.py::test_risk_on_probability_higher_in_calm_regime_across_covariance_types` deliberately excludes `tied` and documents why): the EM fit collapsed to a single dominant state for both a purely-calm and a purely-turbulent window, reproducibly across every `random_state`/`n_inits` combination tried. **`tied` is not recommended for volatility-regime detection** even though its labeling is now correct — prefer `diag` (default), `full`, or `spherical`.
  `scripts/audit_regime_model.py --compare`'s AIC/BIC comparison grid is hardcoded to `["diag", "full"]` and does not sweep `spherical`/`tied` (`--cov` does not affect the `--compare` grid — it only overrides the single covariance type used for that same invocation's walk-forward run and full diagnostics). To audit `spherical`/`tied` directly, run a plain (non-`--compare`) invocation with `--cov spherical` or `--cov tied` and inspect its diagnostics/state labels.
- **EM Convergence:** The model is estimated using the Expectation-Maximization (EM) algorithm. Convergence is determined by a tolerance threshold on the log-likelihood or a maximum number of iterations.
- **Regularization:** Regularization techniques might be used to prevent overfitting, such as imposing priors on the covariance matrices or transition probabilities.

## Walk-Forward Backtesting Methodology and Volatility Monotonicity Gate
- **Walk-Forward Backtesting:** The model's performance is evaluated using a walk-forward approach, where the model is trained on a rolling window and its out-of-sample predictions are used to form a trading strategy.
- **Volatility Monotonicity Gate:** A rule to ensure that higher volatility states correspond to higher risk or specific regime classifications.

## CLI Usage
To audit the regime model, use the following CLI command:
```bash
python -m scripts.audit_regime_model
```

## Testing
`tests/test_audit_regime_model.py` covers the audit CLI itself (distinct from
`tests/test_regime_diagnostics.py`, which covers the underlying
`validation/regime_diagnostics.py` walk-forward/comparison engine this script
calls): a `typing.get_type_hints()` regression check on `load_historical_data`
and `main` (added 2026-08 after a missing `Tuple`/`Optional` import shipped in
PR #791 and broke `main`'s CI — see `docs/architecture/testing.md`'s entry for
why `from __future__ import annotations` doesn't shield this class of bug from
ruff's `F821` check); `load_historical_data()`'s SQLite read path against a
real temporary DB (happy path, `BAMLH0A0HYM2` absent → `credit_series is None`,
missing DB file, empty `price_bars`/`macro_history`); and an argparse-only
smoke test for the `--compare`/`--json`/`--states`/`--cov`/`--output` flags.
