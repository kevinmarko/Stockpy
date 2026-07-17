# Signal: `cross_sectional_momentum`

**File:** `signals/cross_sectional_momentum.py`  
**Default weight:** 15.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active  
**Hook pattern:** Two-phase `pre_compute` / `compute`  
**Pilot:** Momentum Leaders (`cross-sectional-momentum`, `pilots/catalog.py`) — backed by a
real, PBO/DSR-gated backtest (`cross_sectional_momentum` in
`scripts/refresh_validations.py`, a 30-name liquid large-cap cross-section).

---

## Rationale

Cross-sectional momentum (XS momentum) ranks assets against each other and bets on
recent winners over recent losers. This is the "classic" momentum anomaly:

> **Reference:** Jegadeesh, N., & Titman, S. (1993). "Returns to Buying Winners and
> Selling Losers: Implications for Stock Market Efficiency." *The Journal of Finance*,
> 48(1), 65–91.

Jegadeesh-Titman (JT) showed that stocks in the top decile of 12-month returns
outperform those in the bottom decile by approximately 1% per month over 3–12 month
holding periods. This is consistently the most replicated finding in empirical asset
pricing.

**XS vs. TSMOM:** The `timeseries_momentum` module asks "is this stock trending up
relative to its own history?" The `cross_sectional_momentum` module asks "is this stock
trending up *compared to its peers in the universe?*" The two signals are positively
correlated but not identical; running both captures independent information.

**Why a 1-month skip?** JT and subsequent literature (including Moskowitz et al. 2012)
find that including the most recent month reverses the momentum signal — short-term
micro-structure noise creates a negative autocorrelation at the 1-month horizon
(Jegadeesh 1990). The skip is implemented in `main_orchestrator.compute_xsec_momentum_ranks()`
using `skip_days=22` and `lookback_days=252`.

---

## Two-Phase Hook

```
pre_compute(universe_df, context):
    1. Compute 12−1M return for each symbol in the universe.
    2. Rank all symbols by return → percentile rank ∈ [0, 1].
    3. Store rank in context.xsec_percentile_ranks[symbol].
    (Runs ONCE per cycle — not once per ticker)

compute(row, context):
    rank = context.xsec_percentile_ranks.get(symbol, 0.5)
    score = 2 * (rank - 0.5)    # ∈ [-1, +1]; median rank = 0.0
```

The two-phase pattern is required because cross-sectional ranking needs the full
universe simultaneously. Doing it inside `compute()` (called once per ticker) would
force re-ranking the entire universe N times — both inefficient and potentially
non-deterministic if the universe list is mutable.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| Universe has < 3 tickers | `pre_compute` still runs but ranks have low information content (a 3-stock rank is essentially random). The module should be disabled (`DISABLED_SIGNAL_MODULES`) for single-ticker or very-small universes. |
| `XSec_12_1M` column missing (< 253 bars) | Symbol's rank defaults to 0.5 (median) → score = 0.0. The module does not fabricate a rank. |
| All tickers in universe show the same return | All ranks = 0.5 → all scores = 0.0. Correct behaviour. |
| New symbol enters universe | First cycle will have rank from its available history; if < 253 bars, defaults to 0.5. Second cycle onward uses full 12-month window. |
| Microcap ticker with wide bid-ask spread | The return computation uses closing prices (not mid-prices), which may embed a stale-price bias for illiquid names. The multifactor module excludes microcaps from cross-sectional z-scoring; XS momentum does not, so use with caution for sub-$300M market cap names. |

---

## Config Requirements

The `config.COLUMN_SCHEMA` must include:

```python
{"header": "XSec 12-1M Return", "key": "XSec_12_1M", "format": "percent"},
{"header": "XSec Momentum Rank", "key": "XSec_Momentum_Rank", "format": "percent"},
```

These are written by `main_orchestrator.py` after `run_pre_compute()` is called.

---

## Empirical Notes

- JT (1993) documented the anomaly in US equities 1965–1989; it has replicated across
  international markets, asset classes (commodities, bonds, FX), and time periods
  through at least 2020.
- **Momentum crashes**: the anomaly has two well-documented crash episodes — April 2009
  (momentum stocks were heavily short from the bear market) and July–August 2020
  (growth-to-value rotation). In both cases, the `macro_regime` module was in RISK ON,
  meaning the crash was genuinely hard to predict from macro data alone.
- **Minimum universe size for meaningful signal**: the academic literature suggests ≥ 20
  stocks for XS momentum to be statistically meaningful. The default universe of 4 tickers
  (AAPL, MSFT, JNJ, AGNC) is below this threshold; XS momentum with this universe should
  be treated as a tiebreaker, not a primary driver. Expand the universe via `WATCHLIST`
  or `watchlist.txt` for better cross-sectional signal.

---

## Backtest Validation (`cross_sectional_momentum`, 2026-07)

The `cross_sectional_momentum` adapter (30-name universe, top-half/top-tertile
equal-weight book) was fully invested at full market beta with no drawdown control —
MaxDD 37.9%, failing the harness's `<30%` gate despite already-passing Sharpe (0.848)
and PBO (0.067).

**Fix:** `SPY` was added to the adapter's `STRATEGY_REGISTRY` universe as a
benchmark-only trend-filter input (excluded from the tradeable book and from `y`,
mirroring `relative_strength_xsec`'s existing SPY-splitting pattern). The book now
de-risks to cash whenever `SPY < SPY.rolling(200).mean()` (Faber 2007).

| Metric | Before | After | Gate |
|---|---|---|---|
| Sharpe | 0.848 | 0.872 | > 0.50 ✅ |
| PBO | 0.067 | 0.156 | < 0.50 ✅ |
| DSR | 1.000 | 1.000 | > 0.95 ✅ |
| MaxDD | 37.9% | **20.2%** | < 30% ✅ (was FAIL) |
| `deployable` | False | **True** | |

See [PR #311](https://github.com/kevinmarko/Stockpy/pull/311) and
[`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for the
full 12-strategy series this fix was part of.
