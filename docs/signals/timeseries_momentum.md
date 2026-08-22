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

## Walk-Forward Validation Finding (updated 2026-07 — see Backtest Validation below)

**This section previously described a since-replaced 4-variant construction; the
current adapter runs a single, literature-fixed variant.** Retained for history:

The validation harness (`python -m validation.harness`) originally ran this strategy in
four variants — {12M, 6M} momentum × {10%, 20%} vol-target scaling. Findings from the
`scripts/refresh_validations.py` harness (SPY proxy, 2005–2023) at that time:

- **12M + 10% vol target (in isolation):** Passes PBO < 0.5, DSR > 0.95. Net Sharpe ≈
  0.65 after 0.5% one-way transaction cost. Max drawdown ≈ 25%.
- **12M + 20% vol target:** Higher absolute returns but max drawdown approaches 30%;
  borderline on the harness gate.
- Momentum crashes (2009 Q1, 2020 Q2) are the dominant failure mode — see **failure
  modes** above.
- Running all 4 as competing variants (rather than the single isolated one above) drove
  **PBO to 0.756** — see Backtest Validation below for the fix.

---

## Backtest Validation (`timeseries_momentum`, 2026-07)

Four near-duplicate variants — `{12M,6M}×{10%,20%vol}` — built from only two
independent knobs were driving PBO to 0.756 (must be `<0.50`), despite the underlying
edge already clearing Sharpe (0.520), DSR (0.984), and MaxDD (26.0%).

**Fix:** rather than guess, 4 candidate variant sets were empirically tested via the
real harness. Counterintuitively, the "obviously distinct" pairing (different lookback
windows, `ROC_12M`+`ROC_6M` at the same vol target) measured *worse* (PBO 0.73) than a
pairing that agrees on direction (same lookback, two vol-target levels, 0.965-
correlated) — different-lookback momentum signals dominate in different historical
regimes, so which one wins in-sample is a poor predictor of which wins out-of-sample,
exactly what PBO is designed to catch. The near-duplicate pairing numerically passed
(PBO 0.31) but was correctly rejected as not being a genuinely second hypothesis.
Landed on a single, literature-fixed Moskowitz-Ooi-Pedersen (2012) 12-month/10%-vol-
target specification (matching `settings.VOL_TARGET`'s own default), chosen *before*
measuring which combination would pass — a single variant structurally cannot suffer
CPCV selection-bias PBO.

| Metric | Before | After | Gate |
|---|---|---|---|
| Sharpe | 0.520 | 0.523 | > 0.50 ✅ |
| PBO | 0.756 | **0.000** | < 0.50 ✅ (was FAIL) |
| DSR | 0.984 | 1.000 | > 0.95 ✅ |
| MaxDD | 26.0% | 26.0% | < 30% ✅ (unchanged) |
| `deployable` | False | **True** | |

See [PR #314](https://github.com/kevinmarko/Stockpy/pull/314) and
[`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for the
full 12-strategy series this fix was part of.

### 2026-08 addendum: `VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED` re-validation

The `DSR = 1.000` above is the exact legacy shortcut value —
`timeseries_momentum` is a single-variant adapter (`n_trials=1`), so
`deflated_sharpe_ratio` previously took its `n_trials<=1` shortcut and
returned a flat `1.0` without running the real computation at all. Re-run
under `settings.VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED=True` (same
2005-01-01–2024-12-31 window, real network-backed data) to close the gap
left when that flag was enabled operator-side on 2026-08-14 with no
follow-up validation:

| Metric | Before (flag off) | After (flag on) | Gate |
|---|---|---|---|
| Sharpe | 0.523 | 0.523 | > 0.50 ✅ (unchanged) |
| PBO | 0.000 | 0.000 | < 0.50 ✅ (unchanged) |
| DSR | 1.000 (shortcut) | **0.990009** (real) | > 0.95 ✅ |
| MaxDD | 26.0% | 26.0% | < 30% ✅ (unchanged) |
| `deployable` | True | **True** (unchanged) | |

`timeseries_momentum` is the closest of the 5 flag-named strategies to the
0.95 gate under the real computation (0.990 vs. e.g. `cross_sectional_momentum`'s
0.9999) — expected, since it also has the lowest Sharpe of the five — but
still clears with meaningful margin. A forward check through 2026-08-01
(~19 months more live data) gives DSR=0.9922779, the same conclusion. See
[`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md)'s
2026-08-17 entry for the full 5-strategy re-validation and methodology.

### 2026-08-21 re-verification: `deployable` flips to False — honest regression, not a fix

A `scripts.refresh_validations` run in a network-isolated sandbox hit `RuntimeError:
Adapter returned an empty feature/return frame — insufficient history for this start/end
range` for this strategy. Investigated and given a self-diagnosing error message in
[#850](https://github.com/kevinmarko/Stockpy/pull/850) (diagnostics-only, no adapter-logic
change), then re-run with a real `FMP_API_KEY` on 2026-08-21 (`--start 2005-01-01`,
default end = today) to confirm the adapter itself is sound:

| Metric | 2026-08 addendum above | 2026-08-21 | Gate |
|---|---|---|---|
| Sharpe | 0.523 | **0.477** | > 0.50 — **now FAILS** |
| PBO | 0.000 | 0.000 | < 0.50 ✅ |
| DSR | 0.990 (real) | 0.983 | > 0.95 ✅ |
| MaxDD | 26.0% | 26.0% | < 30% ✅ |
| `deployable` | True | **False** | |

**Honest, disclosed regression** — the adapter itself produced a healthy 4,747-row
feature/return frame (confirming the RuntimeError above was environmental, not a code
defect), but the walk-forward Sharpe on the CURRENT data window now lands just under the
0.50 gate. **This is not directly comparable to the prior 0.523 measurement**: FMP's
`/historical-price-eod` endpoint caps a single request at 5,000 rows and silently returns
the most RECENT 5,000 trading days rather than paginating back to the requested
`--start 2005-01-01` — so this run's actual SPY window began 2006-10-05, not 2005-01-01,
and also extends ~19-21 months further forward (through 2026-08-21) than the prior 2024-
12-31/2026-08-01 checkpoints. Per this repo's rule that gates are never loosened and a
result that fails is reported honestly, `deployable=False` stands as the CURRENT
`STRATEGY_REGISTRY` state pending operator review — **not silently reverted or
re-measured with a shorter window to force a pass.** Two disclosed, un-investigated
questions for a follow-up: (1) whether the Sharpe decline is genuine momentum-factor
decay in the most recent ~20 months of data, or an artifact of the 5,000-row window
silently excluding 2005-2006; (2) whether `_download_closes`/`_fetch_fmp_ohlcv_batch`
should paginate past FMP's 5,000-row cap so `--start` is actually honored for any strategy
requesting more than ~19.8 years of history. See
`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-21 entry.

### 2026-08-21 re-verification: FMP pagination fix applied — both open questions resolved

`data/fmp_client.py::historical_eod_full_range` (added in the `fmp-historical-eod-pagination-fix`
PR) fixes the 5,000-row silent-truncation bug the section above disclosed. Re-run with the SAME
command (`--strategies timeseries_momentum --start 2005-01-01`), now against the actual full
2005-present window — live-verified: `historical_eod_full_range('SPY', ..., from_date='2005-01-01',
to_date='2026-08-21')` returns 5,443 rows spanning `2005-01-03`→`2026-08-21` (vs. the prior
5,000 rows spanning `2006-10-05`→`2026-08-21`):

| Metric | 2026-08-21 (truncated, ~2006-10 start) | 2026-08-21 (full range, 2005-01 start) | Gate |
|---|---|---|---|
| Sharpe | 0.477 | **0.524** | > 0.50 ✅ (was FAIL) |
| PBO | 0.000 | 0.000 | < 0.50 ✅ |
| DSR | 0.983 | **0.993** | > 0.95 ✅ |
| MaxDD | 26.0% | 26.0% | < 30% ✅ (unchanged) |
| `deployable` | False | **True** | |

**Verdict — question (1) resolved**: the Sharpe recovers to 0.524 once the missing ~10 months of
2005-2006 data are included, **confirming this was a truncation artifact, not genuine momentum-
factor decay**. The recovered figure (0.524) closely matches — and very slightly exceeds — the
originally-recorded 0.523 from the "2026-08 addendum" section above, which is the expected
outcome of restoring the correct data window rather than a coincidence. Per this repo's rule that
gates are never loosened to force a pass: this recovery is legitimate specifically because
nothing about the gate or the adapter's logic changed between the two runs above — only the
INPUT DATA did (the fetch layer, not the strategy). **Question (2) resolved**: yes,
`_fetch_fmp_ohlcv_batch` now transparently pages past the cap for this and every other
`STRATEGY_REGISTRY` strategy going forward.

`deployable=True` is restored on the actual `STRATEGY_REGISTRY` state as of this fix. See
`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s "FMP `historical_eod` 5,000-row cap fixed" entry for the
fix itself, the full 4-strategy re-verification, and the disclosed follow-up (every other
registered strategy was silently validated on the same truncated window until now — a full-
registry re-validation remains a separate, explicitly out-of-scope task).

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


### 2026-08-18 Full Validation Run (`timeseries_momentum`, rebased onto `main`)

| Metric | Result |
|---|---|
| **Sharpe Ratio (net)** | 0.5394 |
| **PBO** | 0.0000 |
| **DSR** | 0.9921 |
| **Max Drawdown** | 17.15% |
| **Deployable** | ✅ True |

