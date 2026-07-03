# Signal Module Documentation

Each file in this directory documents one registered `SignalModule` from the `signals/`
package. Every entry covers: academic rationale, signal logic, failure modes, and the
empirical finding (or design choice) that justified its default weight.

The weighted sum of all active modules produces the `final_score` consumed by
`StrategyEngine.evaluate_security()`. Weights are in `settings.SIGNAL_WEIGHTS` and
can be overridden per-regime via `settings.REGIME_SIGNAL_WEIGHTS`.

See [`docs/architecture.md`](../architecture.md) for the full data-flow context.

---

## Module Index

| Module | Weight | File | Description |
|--------|--------|------|-------------|
| [`macro_regime`](macro_regime.md) | 45.0 | `signals/macro_regime.py` | Rules-based macro regime gate (RISK ON / NEUTRAL / RECESSION / CREDIT EVENT) + sector rotation |
| [`edge_garch`](edge_garch.md) | 35.0 | `signals/edge_garch.py` | Mathematical edge ratio × GJR-GARCH tail-risk vol |
| [`dividend_quality`](dividend_quality.md) | 25.0 | `signals/dividend_quality.py` | Dividend sustainability (payout ratio gate) |
| [`rsi_extremes`](rsi_extremes.md) | 20.0 | `signals/rsi_extremes.py` | RSI-14 overbought / oversold |
| [`graham_value`](graham_value.md) | 15.0 | `signals/graham_value.py` | Graham Number intrinsic value vs current price |
| [`macd_momentum`](macd_momentum.md) | 15.0 | `signals/macd_momentum.py` | MACD crossover, gated by Aroon chop filter |
| [`aroon_trend`](aroon_trend.md) | 15.0 | `signals/aroon_trend.py` | Aroon Oscillator trend direction + chop filter |
| [`timeseries_momentum`](timeseries_momentum.md) | 15.0 | `signals/timeseries_momentum.py` | Moskowitz/Ooi/Pedersen 12-month TSMOM with vol scaling |
| [`cross_sectional_momentum`](cross_sectional_momentum.md) | 15.0 | `signals/cross_sectional_momentum.py` | Jegadeesh-Titman 12−1M cross-sectional rank |
| [`multifactor`](multifactor.md) | 15.0 | `signals/multifactor.py` | Fama-French Value + Quality + Low-Vol + Size composite |
| [`forecast_alignment`](forecast_alignment.md) | 10.0 | `signals/forecast_alignment.py` | ARIMA/MC/HW/CNN-LSTM ensemble directional consensus |
| [`relative_strength`](relative_strength.md) | 10.0 | `signals/relative_strength.py` | Stock return vs SPY 12-month excess return |
| [`sortino_drawdown`](sortino_drawdown.md) | 10.0 | `signals/sortino_drawdown.py` | Sortino Ratio quality reward + max drawdown penalty |
| [`rsi2_mean_reversion`](rsi2_mean_reversion.md) | 10.0 | `signals/rsi2_mean_reversion.py` | Connors RSI(2) long-only mean reversion (regime-gated) |
| [`news_catalyst`](news_catalyst.md) | 10.0 | `signals/news_catalyst.py` | FinBERT / lexicon headline sentiment (earnings-proximity gated) |
| [`lgbm_ranker`](lgbm_ranker.md) | 0.10 | `signals/lgbm_ranker.py` | LightGBM cross-sectional rank (dormant — contributes 0.0 until a model is trained) |
| [`regime_multiplier`](regime_multiplier.md) | **0.0** | `signals/regime_multiplier.py` | HMM risk-on probability carried as Kelly-size scalar only |

---

## Score Contribution Summary

The maximum possible score contribution per module = `score × weight` where `score ∈ [-1, +1]`.
The `final_score` from `SignalAggregator.aggregate()` is the weighted sum of active modules'
contributions.

| Tier | Modules | Max |Δ| contribution |
|------|---------|---------------------|
| Dominant | macro_regime, edge_garch | ±45, ±35 pts |
| Strong | dividend_quality, rsi_extremes | ±25, ±20 pts |
| Supporting | graham_value, macd_momentum, aroon_trend, timeseries_momentum, cross_sectional_momentum, multifactor | ±15 pts each |
| Tiebreaker | forecast_alignment, relative_strength, sortino_drawdown, rsi2_mean_reversion, news_catalyst | ±10 pts each |
| Sizing-only | regime_multiplier | 0 pts (Kelly scalar) |

---

## Adding a New Signal Module

1. Create `signals/<name>.py` implementing `SignalModule` ABC.
2. Add `global_registry.register(<YourClass>())` at module bottom.
3. Add `import signals.<name>` to `signals/__init__.py`.
4. Add `"<name>": <default_weight>` to `settings.SIGNAL_WEIGHTS` default.
5. Add `{"header": "...", "key": "...", "format": "..."}` entries to `config.COLUMN_SCHEMA` for any new output columns.
6. Create `docs/signals/<name>.md` using the template structure of any existing file.
7. Update this index table.
8. Write tests in `tests/test_<name>.py` covering: score range, regime gate, NaN inputs, `pre_compute` (if two-phase).
9. Add a Gravity audit step in `Gravity AI Review Suite.py`.
