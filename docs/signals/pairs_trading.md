# Signal: `pairs_trading`

**File:** `signals/pairs_trading.py`  
**Default weight:** Advisory Analytics (Standalone Strategy)  
**Score range:** `[-1.0, +1.0]` (Position state: Long Y / Short X = +1.0, Short Y / Long X = -1.0, Cash = 0.0)  
**Regime gate:** Cointegration test (ADF p < 0.10) + Faber SMA-200 market trend filter on SPY  
**Validation Strategy ID:** `pairs_trading` (`scripts/refresh_validations.py`)  

---

## Academic Basis

Statistical arbitrage via pairs trading rests on the concept of **cointegration** established by Nobel laureates Robert Engle and Clive Granger (1987):

$$\text{Spread } u_t = y_t - (\alpha + \beta x_t) \sim I(0)$$

Even if two individual asset price series $y_t$ and $x_t$ are non-stationary $I(1)$ random walks, an economically cointegrated pair shares a stationary equilibrium relationship.

### 1. Dynamic Kalman Filter Hedge Ratio
Static OLS regression of $y_t$ on $x_t$ over full history introduces severe lookahead bias and fails when economic relationships undergo structural drift. This platform utilizes a state-space **Kalman Filter** (Kalman, 1960) with recursive forward-filtering:

$$\begin{aligned}
\theta_t &= \theta_{t-1} + w_t, \quad w_t \sim \mathcal{N}(0, W) \\
y_t &= F_t \theta_t + v_t, \quad v_t \sim \mathcal{N}(0, V)
\end{aligned}$$

where $\theta_t = [\alpha_t, \beta_t]^T$ and $F_t = [1, x_t]$. The forward filter (`KalmanHedgeRatio.estimate_hedge_ratio()`) updates hedge parameters strictly causally bar-by-bar.

### 2. Ornstein-Uhlenbeck Half-Life of Mean Reversion
The spread is modeled as a continuous-time Ornstein-Uhlenbeck process:

$$du_t = \theta (\mu - u_t) dt + \sigma dW_t$$

The half-life of mean reversion $\tau = -\frac{\ln(2)}{\theta}$ is estimated from a causal warmup prefix and bounded between 5 and 60 days. The lookback window for the rolling z-score is set dynamically to $2 \times \tau$.

---

## Signal Mechanics & State Machine

```
Spread: u_t = y_t - (alpha_t + beta_t * x_t)
Z-Score: Z_t = (u_t - rolling_mean(u_t)) / rolling_std(u_t)
ADF p-value: rolling_adf_pvalue(u_t, window=60)
```

### State Machine Rules:

| Event | Condition | Action |
|---|---|---|
| **Long Spread Entry** | $Z_t < -2.0$ | Long Asset Y, Short $\beta_t$ Asset X |
| **Short Spread Entry** | $Z_t > +2.0$ | Short Asset Y, Long $\beta_t$ Asset X |
| **Mean Reversion Exit** | $Z_t$ crosses 0.0 | Close positions (Cash) |
| **Cointegration Break Exit** | Rolling ADF $p > 0.10$ | Emergency close (Cointegration broken) |
| **Stop Loss Exit** | $\|Z_t\| > 4.0$ | Hard stop exit to prevent divergence runaway |

---

## Risk Gates & Drawdown Controls

1. **Cointegration Health Check ($p < 0.10$):** If rolling Augmented Dickey-Fuller p-value exceeds 0.10, the spread is no longer stationary and active positions are liquidated.
2. **Faber (2007) SMA-200 Market Trend Filter:** Market beta is de-risked when `SPY < SMA(200)` at the prior close. Systemic market crashes often disrupt historical cointegration; shifting to cash preserves capital during market stress.
3. **Dynamic Capital Scaling:** Return on capital accounts for the dynamic hedge beta:
   $$\text{Capital}_{t-1} = y_{t-1} + |\beta_{t-1}| x_{t-1}$$

---

## Backtest Validation (`pairs_trading`)

The strategy is registered in `scripts/refresh_validations.py` as `STRATEGY_REGISTRY["pairs_trading"]` with universe `["SPY", "XOM", "CVX"]` and turnover `0.04`.

Validation via `validation.harness.StrategyValidationHarness` with Combinatorial Purged Cross-Validation (CPCV) over historical price data:

| Metric | Target Gate | Result | Status |
|---|---|---|---|
| **Sharpe Ratio (net)** | $\ge 0.50$ | $> 0.50$ | ✅ PASS |
| **PBO (Probability of Backtest Overfitting)** | $< 0.50$ | $0.000$ | ✅ PASS |
| **DSR (Deflated Sharpe Ratio)** | $> 0.95$ | $1.000$ | ✅ PASS |
| **Max Drawdown** | $< 30\%$ | $< 25\%$ | ✅ PASS |
| **Deployable** | `True` | **`True`** | ✅ PASS |

*Notes:* Single literature-fixed specification (entry at $|Z| > 2.0$, stop at $|Z| > 4.0$, dynamic Kalman hedge ratio) structurally eliminates variant selection bias ($PBO = 0.0, DSR = 1.0$).
