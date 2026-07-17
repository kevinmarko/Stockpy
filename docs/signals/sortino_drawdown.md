# Signal: `sortino_drawdown`

**File:** `signals/sortino_drawdown.py`  
**Default weight:** 10.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active  
**Pilot:** Risk-Adjusted (`risk-adjusted`, `pilots/catalog.py`) — backed by a real,
PBO/DSR-gated backtest (`sortino_drawdown` in `scripts/refresh_validations.py`): a rolling
504-day (2-year) trailing Sortino/drawdown gate on SPY mirroring this module's exact
thresholds (Sortino > 2.0, drawdown < -25%).

---

## Rationale

This module rewards stocks with high risk-adjusted return quality (Sortino Ratio) and
penalises stocks recovering from deep drawdowns. Together they answer: "Has this stock
historically delivered good returns for the *downside* risk taken, and is it currently
deep in a hole?"

**Sortino Ratio** (Sortino & van der Meer, 1991):
```
Sortino = (portfolio_return - target_return) / downside_deviation
```
Unlike Sharpe, which penalises both upside and downside volatility, Sortino only
penalises **downside** deviation (returns below target). This better captures the
asymmetric risk preferences of a long-only investor.

**Max Drawdown** (MDD) is the peak-to-trough decline from a rolling high. A stock with
a current drawdown exceeding −25% is capital-impaired: either the fundamentals
deteriorated, or it was caught in a systematic selloff. Either way, recovery to break-even
requires a +33% gain — a meaningful asymmetric handicap.

---

## Signal Logic

| Condition | Points |
|-----------|--------|
| `sortino_ratio > 2.0` | +10 pts — elite risk-adjusted returns |
| `0 < sortino_ratio ≤ 2.0` | 0 pts — acceptable but not notable |
| `sortino_ratio ≤ 0` | 0 pts — losing money; penalised by other modules |
| `max_drawdown < −0.25` | −10 pts — recovery from a significant loss |
| `max_drawdown ≥ −0.25` | 0 pts |

**Normalization:** raw points / 10.0.

Note: the two conditions are **additive**. A stock with `sortino > 2.0` AND
`max_drawdown > −0.25` scores +10 pts. A stock with `sortino > 2.0` AND
`max_drawdown < −0.25` (currently recovering) scores 0 pts (the +10 from Sortino and
−10 from drawdown cancel out). This is intentional: high Sortino tells you about history;
deep current drawdown tells you about present capital damage.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| Sortino Ratio NaN (insufficient return history) | 0 pts. Module does not penalise lack of history. |
| Max Drawdown NaN | 0 pts. Conservative: no drawdown penalty when drawdown is unknown. |
| Sortino > 2.0 during a regime where the stock happened to avoid a market crash | This can produce a spuriously high Sortino in look-back windows that don't include stress periods. The strategy harness (`validation/stress_scenarios.py`) stress-tests this explicitly for options strategies. |
| Drawdown threshold at −25%: is it too generous? | For blue-chip large-caps, −25% in a non-recession environment is significant. For growth stocks (30–40% normal drawdown range), the threshold may fire too frequently. Consider adjusting via `REGIME_SIGNAL_WEIGHTS` to reduce this module's weight for growth stocks if the universe includes high-beta names. |

---

## Empirical Notes

- The Sortino > 2.0 threshold is demanding: it corresponds to approximately a 20% annual
  return with a 10% downside deviation — top-decile performance among large-cap equities.
  In practice, only 10–20% of stocks in the default universe exceed this level in any
  given 12-month trailing window, making it a meaningful differentiator.
- The −25% drawdown threshold was chosen to match the maximum drawdown gate in the
  strategy validation harness (30%). A stock currently 25%+ below its trailing high is
  close to the validation harness's risk gate, which is a useful consistency check.
- This module's 10.0 weight makes it a tiebreaker / risk-quality overlay rather than a
  primary driver. In a balanced signal environment (all modules near zero), a high Sortino
  and low drawdown can tilt the final score from HOLD to BUY.

---

## Backtest Validation (`sortino_drawdown`, 2026-07)

The `sortino_drawdown` adapter's own 504-day (2-year) trailing drawdown gate reacted
too slowly — by the time a 2-year trailing drawdown hits -25%, most of the drawdown has
already happened. MaxDD 38.5%, failing the harness's `<30%` gate despite already-passing
Sharpe (0.608) and PBO (0.156).

**Fix:** a Faber (2007) SMA-200 trend filter was ANDed into all 3 existing variants'
long conditions (`SortinoDD_HighSortino`, `SortinoDD_DrawdownGate`,
`SortinoDD_Combined`), on top of — not replacing — the existing Sortino/drawdown logic.
A 200-day moving average reacts to a sustained downtrend within weeks, closing the
structural blind spot of the 2-year trailing-drawdown gate. Variant names/count
preserved unchanged (a pre-existing test suite pins those exact keys).

| Metric | Before | After | Gate |
|---|---|---|---|
| Sharpe | 0.608 | 0.668 | > 0.50 ✅ |
| PBO | 0.156 | 0.178 | < 0.50 ✅ |
| DSR | 0.984 | 0.982 | > 0.95 ✅ |
| MaxDD | 38.5% | **26.6%** | < 30% ✅ (was FAIL) |
| `deployable` | False | **True** | |

See [PR #310](https://github.com/kevinmarko/Stockpy/pull/310) and
[`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for the
full 12-strategy series this fix was part of.
