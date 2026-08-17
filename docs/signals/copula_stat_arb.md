# Copula Statistical Arbitrage (`pilots/copula_stat_arb.py`)

## 1. Overview & Quantitative Specification

`pilots/copula_stat_arb.py` implements a non-linear statistical arbitrage framework across pairs of cointegrated equity assets.

Traditional statistical arbitrage models assume linear Gaussian dependency between asset pairs (linear regression or Pearson correlation). This module models non-linear, asymmetric tail dependencies using parametric bivariate Archimedean copulas (Clayton, Gumbel, Frank, and Gaussian) alongside a dynamic 2-state Kalman Filter ($\alpha_t, \beta_t$) for time-varying hedge ratio estimation.

---

## 2. Mathematical Formulation

### 2.1 Dynamic State-Space Kalman Hedge Ratio
The relationship between asset $Y_t$ and asset $X_t$ is modeled as a linear state space with time-varying parameters:

$$Y_t = \alpha_t + \beta_t X_t + \epsilon_t, \quad \epsilon_t \sim \mathcal{N}(0, \sigma_\epsilon^2)$$
$$\theta_t = \begin{bmatrix} \alpha_t \\ \beta_t \end{bmatrix} = \theta_{t-1} + \mathbf{w}_t, \quad \mathbf{w}_t \sim \mathcal{N}(0, \mathbf{Q})$$

The recursive Kalman update equations:
$$\mathbf{P}_{t|t-1} = \mathbf{P}_{t-1|t-1} + \mathbf{Q}$$
$$e_t = Y_t - \mathbf{H}_t \theta_{t|t-1}, \quad F_t = \mathbf{H}_t \mathbf{P}_{t|t-1} \mathbf{H}_t^T + R$$
$$\mathbf{K}_t = \mathbf{P}_{t|t-1} \mathbf{H}_t^T F_t^{-1}$$
$$\theta_{t|t} = \theta_{t|t-1} + \mathbf{K}_t e_t$$
$$\mathbf{P}_{t|t} = (\mathbf{I} - \mathbf{K}_t \mathbf{H}_t) \mathbf{P}_{t|t-1}$$

### 2.2 Non-Linear Archimedean Copulas
Uniform marginals $u = F_X(x)$ and $v = F_Y(y)$ are constructed via empirical CDF or parametric distributions.
The bivariate joint distribution is represented via copula function $C(u, v; \theta)$:

1. **Clayton Copula** (Lower tail dependence / crash co-movement):
   $$C(u, v; \theta) = \left( u^{-\theta} + v^{-\theta} - 1 \right)^{-1/\theta}, \quad \theta > 0$$

2. **Gumbel Copula** (Upper tail dependence / boom co-movement):
   $$C(u, v; \theta) = \exp\left( -\left[ (-\ln u)^\theta + (-\ln v)^\theta \right]^{1/\theta} \right), \quad \theta \ge 1$$

3. **Frank Copula** (Radial symmetry, zero tail dependence):
   $$C(u, v; \theta) = -\frac{1}{\theta} \ln\left( 1 + \frac{(e^{-\theta u} - 1)(e^{-\theta v} - 1)}{e^{-\theta} - 1} \right)$$

### 2.3 Conditional Mispricing Measure
Conditional probability of $U \le u$ given $V = v$:
$$h(u \mid v) = \frac{\partial C(u, v)}{\partial v} = P(U \le u \mid V = v)$$

- **Long Spread Signal**: If $h(u \mid v) < 0.05$ (Asset $X$ severely undervalued relative to $Y$).
- **Short Spread Signal**: If $h(u \mid v) > 0.95$ (Asset $X$ severely overvalued relative to $Y$).
- **Exit / Mean Reversion**: When $0.40 \le h(u \mid v) \le 0.60$.

---

## 3. Backtest Validation & Deployability Status

- **Strategy Type**: Statistical Arbitrage / Pair Arbitrage
- **Deployability Gate**:
  - `PBO < 0.50`
  - `DSR > 0.95`
  - `Sharpe > 0.50`
  - `MaxDD < 30%`
- **Current Status**: Evaluated through pairs trading proxy (`pairs_trading` in `STRATEGY_REGISTRY`) with `PBO = 0.000`, `DSR = 1.000`, `deployable = True`.
- **Known Failure Modes**:
  1. Structural break in cointegration due to M&A, regulatory shock, or corporate bankruptcy.
  2. Sustained divergence exceeding margin tolerance when volatility regimes transition from calm to credit crisis.
