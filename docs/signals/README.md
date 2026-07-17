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

The **Pilot** column cross-links to the `pilots/catalog.py` Pilot that packages this
module as a standalone, copyable strategy in the Pilots PWA (`webapp/`) — see
[`docs/AUTOPILOT_PLAN.md`](../AUTOPILOT_PLAN.md). **Backtest** is the honest join to a
`STRATEGY_REGISTRY` adapter (`scripts/refresh_validations.py`) that gives that Pilot a
real, PBO/DSR-gated performance curve; `—` means the module's inputs (macro DTO,
point-in-time fundamentals, point-in-time news, an external forecast target) can't be
honestly reconstructed from price/volume alone, so the Pilot stays curveless
(`validation_strategy_id=None`) rather than borrowing a fabricated backtest
(CONSTRAINT #4). A backtest existing does not guarantee `deployable=True` — several of
these honestly fail the PBO/DSR/Sharpe/MaxDD gate on real data; the gate is never
loosened to force a green check.

The **Backtest** column's parenthetical `deployable=` status reflects the 2026-07
strategy-validation-fixes series ([PR #310](https://github.com/kevinmarko/Stockpy/pull/310),
[#311](https://github.com/kevinmarko/Stockpy/pull/311),
[#314](https://github.com/kevinmarko/Stockpy/pull/314)); see
[`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for the full
rollup and each linked `docs/signals/<name>.md`'s own **Backtest Validation** section
for the before/after metrics and reasoning.

| Module | Weight | File | Description | Pilot | Backtest |
|--------|--------|------|--------------|-------|----------|
| [`macro_regime`](macro_regime.md) | 45.0 | `signals/macro_regime.py` | Rules-based macro regime gate (RISK ON / NEUTRAL / RECESSION / CREDIT EVENT) + sector rotation | Regime Navigator (`regime-navigator`) | — (macro DTO, not price-only) |
| [`edge_garch`](edge_garch.md) | 35.0 | `signals/edge_garch.py` | Mathematical edge ratio × GJR-GARCH tail-risk vol | Edge & Volatility (`edge-garch`) | `garch_vol_target` (covers the GARCH-veto half only; `deployable=True`) |
| [`dividend_quality`](dividend_quality.md) | 25.0 | `signals/dividend_quality.py` | Dividend sustainability (payout ratio gate) | Dividend Income (`dividend-income`) | `dividend_yield_edgar_pit` (real SEC EDGAR PIT backtest; `deployable=False` — data-coverage ceiling, see doc) |
| [`rsi_extremes`](rsi_extremes.md) | 20.0 | `signals/rsi_extremes.py` | RSI-14 overbought / oversold | RSI Reversal (`rsi-reversal`) | `rsi14_extremes` (`deployable=False` — genuinely weak net-of-cost edge, see doc) |
| [`graham_value`](graham_value.md) | 15.0 | `signals/graham_value.py` | Graham Number intrinsic value vs current price | Deep Value (`deep-value`) | `deep_value_edgar_pit` (real SEC EDGAR PIT backtest; `deployable=False` — data-coverage ceiling, see doc) |
| [`macd_momentum`](macd_momentum.md) | 15.0 | `signals/macd_momentum.py` | MACD crossover, gated by Aroon chop filter | MACD Trend (`macd-trend`, shared with `aroon_trend`) | `macd_trend` (`deployable=True`) |
| [`aroon_trend`](aroon_trend.md) | 15.0 | `signals/aroon_trend.py` | Aroon Oscillator trend direction + chop filter | MACD Trend (`macd-trend`, shared with `macd_momentum`) | `macd_trend` (`deployable=True`) |
| [`timeseries_momentum`](timeseries_momentum.md) | 15.0 | `signals/timeseries_momentum.py` | Moskowitz/Ooi/Pedersen 12-month TSMOM with vol scaling | Trend Follower (`trend-following`) | `timeseries_momentum` (`deployable=True`, 2026-07 fix) |
| [`cross_sectional_momentum`](cross_sectional_momentum.md) | 15.0 | `signals/cross_sectional_momentum.py` | Jegadeesh-Titman 12−1M cross-sectional rank | Momentum Leaders (`cross-sectional-momentum`) | `cross_sectional_momentum` (`deployable=True`, 2026-07 fix) |
| [`multifactor`](multifactor.md) | 15.0 | `signals/multifactor.py` | Fama-French Value + Quality + Low-Vol + Size composite | Multifactor (`multifactor`) | `multifactor_lowvol_size` (Low-Vol + Size sleeve only — see `docs/signals/multifactor.md`; `deployable=True`, 2026-07 fix) |
| [`forecast_alignment`](forecast_alignment.md) | 10.0 | `signals/forecast_alignment.py` | ARIMA/MC/HW/CNN-LSTM ensemble directional consensus | Forecast Aligned (`forecast-aligned`) | — (external forecast target, not price-only) |
| [`relative_strength`](relative_strength.md) | 10.0 | `signals/relative_strength.py` | Stock return vs SPY 12-month excess return | Relative Strength (`relative-strength`) | `relative_strength_xsec` (`deployable=True`, 2026-07 fix) |
| [`sortino_drawdown`](sortino_drawdown.md) | 10.0 | `signals/sortino_drawdown.py` | Sortino Ratio quality reward + max drawdown penalty | Risk-Adjusted (`risk-adjusted`) | `sortino_drawdown` (`deployable=True`, 2026-07 fix) |
| [`rsi2_mean_reversion`](rsi2_mean_reversion.md) | 10.0 | `signals/rsi2_mean_reversion.py` | Connors RSI(2) long-only mean reversion (regime-gated) | Dip Buyer (`dip-buyer`) | `rsi2_mean_reversion` (`deployable=False` — genuinely weak net-of-cost edge, see doc) |
| [`news_catalyst`](news_catalyst.md) | 10.0 | `signals/news_catalyst.py` | FinBERT / lexicon headline sentiment (earnings-proximity gated) | News Catalyst (`news-catalyst`) | — (point-in-time news, not price-only) |
| [`lgbm_ranker`](lgbm_ranker.md) | 0.10 | `signals/lgbm_ranker.py` | LightGBM cross-sectional rank (dormant — contributes 0.0 until a model is trained) | — (dormant; no Pilot until it passes the model DSR gate) | — |
| [`regime_multiplier`](regime_multiplier.md) | **0.0** | `signals/regime_multiplier.py` | HMM risk-on probability carried as Kelly-size scalar only | — (a sizing multiplier, not alpha — structurally can't back a Pilot) | — |

Also see the ensemble/blend Pilots, which combine several modules rather than joining
one: `Multifactor` above is single-module; `Balanced Blend` (`balanced-blend`, every
module at its default weight) has no single-module row here; `Value & Quality`
(`value-quality`, `graham_value` + `dividend_quality` + `multifactor`) is likewise a
curated multi-module blend, but its Pilot IS joined to a narrower honest proxy backtest
— `value_quality_edgar_pit` (Value(1/PB) + Quality(ROE+OpMargin) over the same EDGAR PIT
universe as `graham_value`/`dividend_quality` above, `deployable=False` — same
data-coverage ceiling class, see `docs/VALIDATION_STRATEGY_FIX_LOG.md`) — see
`pilots/catalog.py`.

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
