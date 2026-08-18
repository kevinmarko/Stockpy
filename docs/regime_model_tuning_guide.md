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
