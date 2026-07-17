# Signal: `relative_strength`

**File:** `signals/relative_strength.py`  
**Default weight:** 10.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active  
**Pilot:** Relative Strength (`relative-strength`, `pilots/catalog.py`) — backed by a real,
PBO/DSR-gated backtest (`relative_strength_xsec` in `scripts/refresh_validations.py`, a
cross-sectional book long the names outrunning SPY).

---

## Rationale

Relative strength (RS) measures a stock's performance versus a benchmark (SPY) over a
lookback period. It is the foundational metric of William O'Neil's CANSLIM system and
forms the basis of IBD's RS Rating. The concept predates modern factor analysis:

> **Reference:** Levy, R. A. (1967). "Relative Strength as a Criterion for Investment
> Selection." *The Journal of Finance*, 22(4), 595–610.

RS is a **market-conditional** momentum signal: it answers whether a stock is benefiting
from (or dragging against) broad market beta. A stock with RS > 0 (outperforming SPY)
is doing something right even relative to the tailwind of a bull market; a stock with
RS < 0 in a bull market is being punished by idiosyncratic factors.

In advisory mode, RS serves as a check on `timeseries_momentum`: a stock can have
positive TSMOM (trending up vs. itself) but negative RS (underperforming the market).
That combination suggests the stock is catching a market-wide trend, not generating
alpha — a weaker case for overweighting.

---

## Signal Logic

```python
IF relative_strength > 0:   +10 pts (outperforming SPY)
IF relative_strength < 0:   −10 pts (underperforming SPY)
IF relative_strength is NaN:  0 pts
```

**Normalization:** raw points / 10.0.

`relative_strength` is computed by `processing_engine.calculate_momentum_metrics()`
as the excess return of the stock vs. SPY over a rolling 12-month window:
```
RS = stock_return_12M - spy_return_12M
```

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| SPY data unavailable | RS cannot be computed → score = 0.0. SPY is always fetched by `macro_engine.py` for HMM inputs, so this failure is rare. |
| Stock listed < 12 months ago | RS = NaN → score = 0.0. `calculate_momentum_metrics()` returns NaN explicitly (not 0) when fewer than 253 bars are available. |
| RS exactly 0.0 | Score = 0.0 — tied with market, no directional signal. |
| Bull market with market-correlated stock (high beta) | RS may be positive purely from beta exposure. The `multifactor` module's Low-Vol factor provides a counterweight: high-beta stocks score poorly on the low-vol dimension. |

---

## Empirical Notes

- The RS signal at 10.0 weight is the weakest standalone directional module in the
  system. Its primary role is as a confirmation signal: when RS, TSMOM, and XS momentum
  all agree (all positive), the aggregate momentum contribution is +35–45 pts — enough
  to tip a borderline BUY signal to STRONG BUY.
- **Sector RS bias**: defensive sectors (Consumer Staples, Utilities) systematically
  underperform during bull markets by design. These stocks will receive −10 pts from this
  module in a RISK ON regime. This is partially offset by the `macro_regime` defensive
  sector premium (+10 pts) which only fires in RECESSION — so in a normal cycle,
  defensive stocks are mildly penalised on RS, which is appropriate (buy defensives
  when you want to reduce risk, not when alpha is the objective).
- **Short-sale context**: the RS signal is directional but this platform is long-only
  advisory. A negative RS score (`−10 pts`) reduces the aggregate toward HOLD or
  RISK REDUCE, not a short signal. Never interpret a negative RS as a recommendation
  to go short.

---

## Backtest Validation (`relative_strength_xsec`, 2026-07)

The `relative_strength_xsec` adapter was a fully-invested, always-long cross-sectional
book with no drawdown control — MaxDD 46.9% (the worst in the entire registry) and
PBO 0.64, both failing their gates, despite an already-passing Sharpe (0.707).

**Fix:** a Faber (2007) SMA-200 trend gate was applied via SPY (already a
benchmark-only input in this adapter). Before committing to a variant set, the two
pre-existing variants (`RS_BeatSPY_Absolute`, `RS_TopHalf`) were *measured*, not
assumed: adding the gate alone pushed PBO to 0.956, because under a shared market-wide
gate the two variants become 0.98-correlated — genuinely the same strategy wearing two
names. Collapsed to the single surviving `RS_BeatSPY_Absolute` variant, which is what
actually earns the clean PBO=0.0/DSR=1.0 (a true single-variant book cannot suffer
selection-bias PBO).

| Metric | Before | After | Gate |
|---|---|---|---|
| Sharpe | 0.707 | 0.745 | > 0.50 ✅ |
| PBO | 0.64 | **0.000** | < 0.50 ✅ (was FAIL) |
| DSR | 1.000 | 1.000 | > 0.95 ✅ |
| MaxDD | 46.9% | **21.3%** | < 30% ✅ (was FAIL) |
| `deployable` | False | **True** | |

See [PR #311](https://github.com/kevinmarko/Stockpy/pull/311) and
[`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for the
full 12-strategy series this fix was part of.
