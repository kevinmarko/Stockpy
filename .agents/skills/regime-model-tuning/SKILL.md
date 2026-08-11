---
name: regime-model-tuning
description: Tuning and diagnosing the HMM regime model and VRP calculations. Use when adjusting regime rules, debugging macro states, or fixing HMM anomalies.
---

# Regime Model Tuning Skill

This skill provides guidelines for tuning the Hidden Markov Model (HMM) regime definitions and Volatility Risk Premium (VRP) gates.

## 1. Running Regime Status

To evaluate current regime and macro states, use the macro engine:
```bash
python3 main_orchestrator.py
```
This triggers the `macro_engine.py` and `regime/hmm_regime.py` modules.

## 2. Regime and VRP Thresholds

When tuning regime thresholds or troubleshooting blocked trades, ensure these constraints are met:
- **VRP Gate for Options Selling**: The options premium selling recommender is gated by VRP regime rules.
  - `true_ivr > 50`
  - `VRP > 0.02`
  - `VIX < 30`
  - No `CREDIT EVENT`
- If these conditions are not met, the recommender MUST return `Cash/Wait`. Do not override this behavior.

## 3. Macro Kill Switch Thresholds

The global kill switch uses macro data to pause trading. Monitor these explicit thresholds:
- **Sahm Rule Level**: (If applicable to your rules)
- **VIX Level**: `> 30` triggers a halt or gates options selling.
- **HY-OAS (High Yield Option-Adjusted Spread)**: Usually gates trades when spiking.

## 4. Common Failure Modes & Fixes

**Failure Mode: Options Strategy Unintentionally Trading During High VIX**
- **Symptom:** During a backtest or live simulation, an options-selling strategy opens positions while `VIX >= 30`.
- **Fix:** Ensure the `VRP > 0.02` and `VIX < 30` gate is actively checking the daily regime object before emitting an entry signal in the `StrategyEngine`.

**Failure Mode: HMM Stuck in Single State**
- **Symptom:** The HMM regime output does not transition out of a "High Volatility" or "Low Volatility" state despite obvious market shifts.
- **Fix:** Retrain the HMM model on a longer lookback period (e.g., 10+ years of daily SPY/VIX data) or adjust the transition probability matrix constraints. Ensure `macro_engine.py` is receiving fresh data.
