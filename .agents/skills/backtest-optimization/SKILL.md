---
name: backtest-optimization
description: Optimizes strategy parameters and validates strategy performance using the backtest harness. Use when asked to optimize a strategy, test new parameters, or fix a deployability gate.
---

# Backtest Optimization Skill

This skill guides the operator through optimizing and validating a trading strategy using the Stockpy backtest harness.

## 1. Running the Validation Harness

To run a backtest and evaluate a strategy, use the following command:

```bash
python -m validation.harness --strategy <name> --start YYYY-MM-DD --end YYYY-MM-DD
```

If you are validating an options-selling strategy, you must also pass the `--is-options` flag to trigger the tail-scenario stress gate.

## 2. Deployability Gates

A strategy is deployable ONLY IF it meets the following criteria:
- **PBO (Probability of Backtest Overfitting)**: `< 0.5`
- **DSR (Deflated Sharpe Ratio)**: `> 0.95`
- **Net Net-of-cost Sharpe**: `> 0.5`
- **Max Drawdown**: `< 30%`

For **options-selling strategies**, there is an additional stress gate:
- Max drawdown during stress windows must be `< 50%`.
- The account must survive (no blow-up) in EVERY dated shock window (OCT_2008, FEB_2018, MAR_2020, AUG_2024).

## 3. Documenting Validation

Whenever you run the harness and attempt a deployability-gate fix or record an honest failure, you MUST:
1. Document the before/after metrics (PBO/DSR/Sharpe/MaxDD) and the causal lever used in the **Backtest Validation** section of `docs/signals/<name>.md`.
2. Append an entry to `docs/VALIDATION_STRATEGY_FIX_LOG.md`.

Even if a strategy stays `deployable=False`, you must document the measured, evidence-backed reason.

## 4. Common Failure Modes & Fixes

**Failure Mode: High PBO (Overfitting)**
- **Symptom:** The validation harness reports `PBO >= 0.5`, meaning the strategy performs well in-sample but is highly likely to fail out-of-sample due to over-tuning.
- **Fix:** 
  1. Reduce the number of variant configurations (parameter sweeps) tested.
  2. Implement an empirical turnover correction.
  3. Gate the entry logic using a broad trend filter (e.g., Faber SMA-200 trend gate) to reduce noisy trades.

**Failure Mode: Options Strategy Fails Stress Test**
- **Symptom:** Max Drawdown exceeds 50% or the account blows up during a stress window like `MAR_2020`.
- **Fix:** Ensure the VRP regime rules are active: options-selling should be gated by `true_ivr > 50`, `VRP > 0.02`, `VIX < 30`, and no `CREDIT EVENT`. If gated, return `Cash/Wait`.
