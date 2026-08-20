# Regime Model Tuning Guide

## Theoretical Foundation
The regime model is based on the Hamilton (1989) Gaussian Hidden Markov Model (HMM). It seeks to identify latent market regimes (e.g., bull, bear, high volatility, low volatility) from observable market data like returns and realized volatility.

## Hyperparameter Definitions, Covariance Structures, EM Convergence, and Regularization
- **Hyperparameters:** The model requires specifying the number of latent states.
- **Covariance Structures:** The model may assume full or diagonal covariance matrices for the observable variables within each state.
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
