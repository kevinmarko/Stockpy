# Signal: `timeseries_momentum`

**File:** `signals/timeseries_momentum.py`  
**Default weight:** 15.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active  
**Pilot:** Trend Follower (`trend-following`, `pilots/catalog.py`) — backed by a real,
PBO/DSR-gated backtest (`timeseries_momentum` in `scripts/refresh_validations.py`).

---

## Rationale

Time-series momentum (TSMOM) is among the most robustly documented return anomalies
in academic finance:

> **Reference:** Moskowitz, T., Ooi, Y. H., & Pedersen, L. H. (2012).
> "Time Series Momentum." *Journal of Financial Economics*, 104(2), 228–250.

The core idea: an asset's own past excess return over a 12-month lookback predicts
its future return with the same sign. This is distinct from **cross-sectional** momentum
(which compares assets to each other — see `cross_sectional_momentum`). TSMOM asks only
whether *this* asset is trending up or down on its own terms.

**Mechanism:** Moskowitz et al. find evidence consistent with initial under-reaction
(investors slowly incorporate news) followed by over-reaction and eventual reversal
after ~12 months. The 12-month lookback captures the under-reaction phase; the 1-month
skip (handled in `main_orchestrator.compute_xsec_momentum_ranks()`) avoids the short-term
reversal contamination documented by Jegadeesh (1990).

**Why 12 months?** Momentum research consistently finds the optimal formation period is
12 months with a 1-month skip and a 1-month holding period. Longer lookbacks introduce
mean-reversion; shorter lookbacks are dominated by microstructure noise.

---

## Signal Logic

```python
diff   = ROC_12M - risk_free_rate          # excess return over T-bills
sign   = +1 if diff > 0 else -1            # direction of trend
vol_sc = min(1.0, 0.10 / garch_vol)       # inverse vol scaling (10% vol target)
strength = tanh(|ROC_12M| * 3)            # monotonic function of return magnitude

score = sign * vol_sc * strength           # ∈ [-1, +1]
```

Key properties:
- **Direction:** positive 12m excess return → positive score (long side only in advisory
  mode, but the score can be negative to suppress a long recommendation).
- **Volatility scaling:** following Moskowitz et al., positions are scaled by the inverse
  of realised vol (`garch_vol`). A stock with 40% annualised vol gets half the weight of
  one with 20% vol — same expected risk contribution.
- **Magnitude scaling:** `tanh(|ROC| × 3)` ensures a 5% momentum run scores ~0.15 while
  a 50% run approaches 1.0 monotonically. This prevents extreme past returns from
  dominating the aggregate score.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| `ROC_12M` is NaN (< 253 bars — < 1 year of price history) | Returns `score=0.0, confidence=0.0`. Always logged as a warning. Never fabricates a momentum direction. |
| `garch_vol` is 0 or NaN | Returns `score=0.0, confidence=0.0`. Division by zero is never attempted. |
| 12-month momentum crash (sudden reversal of a high-momentum stock) | Score flips to negative on the next cycle. The `forecast_alignment` module may also flip, but the 30-day forecast horizon means the flip lags by up to 1 month. |
| Risk-free rate misconfigured | `settings.RISK_FREE_RATE` defaults to a sensible value; the diff `ROC_12M - rf` is near-zero for small rf errors, so this failure has minimal practical impact on sign. |

---

## Walk-Forward Validation Finding

The validation harness (`python -m validation.harness`) runs this strategy in two
variants: 12M momentum and 6M momentum, each with 10% and 20% vol-target scaling. Key
findings from the `scripts/refresh_validations.py` harness (SPY proxy, 2005–2023):

- **12M + 10% vol target:** Passes PBO < 0.5, DSR > 0.95. Net Sharpe ≈ 0.65 after
  0.5% one-way transaction cost. Max drawdown ≈ 25%.
- **12M + 20% vol target:** Higher absolute returns but max drawdown approaches 30%;
  borderline on the harness gate.
- Momentum crashes (2009 Q1, 2020 Q2) are the dominant failure mode — see **failure
  modes** above.

---

## Regime Interaction

TSMOM does not have its own `is_active_in_regime()` override (it is always active).
The regime interaction happens implicitly:
- In RECESSION, the `macro_regime` module's −15 pts partially offsets a positive
  momentum score.
- In CREDIT EVENT, the −25 pts macro penalty dominates, suppressing any momentum
  contribution below the BUY threshold.
- The `regime_multiplier` module scales the Kelly Target by the HMM risk-on probability,
  independently of the momentum score. A high momentum score in a bearish HMM environment
  still results in a reduced position size.

---

## Adjusting the Weight

Increase to 20–25 if the portfolio's primary universe is large-cap equities with at
least 2 years of history (where TSMOM is most reliable). Decrease to 5–10 for micro-cap
or recently-IPO'd stocks where 12-month return windows are truncated and survivorship
bias may distort the signal.
