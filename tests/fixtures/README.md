# tests/fixtures — hand-authored deterministic fixtures for the Pilots feature

These JSON files are **hand-authored, deterministic** stand-ins for the artifacts a
live pipeline run produces. They exist so the Pilots scoring / API / mirror tests
(`tests/test_pilots_scoring.py`, `tests/test_pilots_api.py`, `tests/test_pilots_mirror.py`)
run offline and reproducibly, **without** depending on a real `main.py` /
`scripts.refresh_validations` run or any network access.

Nothing here is generated — edit the JSON directly. Keep values small and realistic.

## Files

### `state_snapshot.json`
Mirrors the schema written by **`reporting/state_snapshot.py::write_state_snapshot`**
(and the twin `main_orchestrator.py::_write_state_snapshot`), i.e. what the pipeline
persists to `output/state_snapshot.json`.

Top-level keys: `timestamp`, `tickers`, `holdings`, `market_regime`, `vix`,
`yield_curve`, `sahm_rule`, `high_yield_oas`, `hmm_risk_on_probability`,
`kill_switch_active`, `macro_regime_gate_enabled`, `signals`.

Each entry of `signals[]` carries the full writer schema plus **two fields the Pilots
feature adds/relies on**:

- **`sector`** — a GICS sector string (e.g. `"Information Technology"`). This is the
  additive writer change owned by Agent 2 (`Recommendation.sector` → `signals[].sector`);
  it is baked into this fixture already so `pilots/scoring.py::sector_allocation` has
  data to group by regardless of Agent 2's landing order.
- **`score_components`** — a dict mapping **real `settings.SIGNAL_WEIGHTS` module names**
  to that module's **weighted contribution**, i.e.
  `score_components[m] = raw_score[m] * SIGNAL_WEIGHTS[m]`, with `raw_score[m] ∈ [-1, 1]`.
  This is the key enabling fact for the read path: a Pilot can **back out each module's
  raw score** via `raw = score_components[m] / SIGNAL_WEIGHTS[m]` and re-blend under any
  custom weight vector — pure arithmetic on already-persisted data, no engine imports.

The exact `SIGNAL_WEIGHTS` keys/weights used to build the `score_components` values
(so downstream back-out arithmetic matches):

| module (key)                | weight |
|-----------------------------|--------|
| `macro_regime`              | 45.0   |
| `graham_value`              | 15.0   |
| `dividend_quality`          | 25.0   |
| `macd_momentum`             | 15.0   |
| `aroon_trend`               | 15.0   |
| `forecast_alignment`        | 10.0   |
| `relative_strength`         | 10.0   |
| `rsi_extremes`              | 20.0   |
| `sortino_drawdown`          | 10.0   |
| `edge_garch`                | 35.0   |
| `timeseries_momentum`       | 15.0   |
| `cross_sectional_momentum`  | 15.0   |
| `multifactor`               | 15.0   |
| `news_catalyst`             | 10.0   |
| `lgbm_ranker`               | 0.10   |
| `regime_multiplier`         | 0.0    |

Notes for downstream consumers:
- `regime_multiplier` weight is **0.0** by design (it carries the HMM second opinion as
  a sizing multiplier, not directional alpha). Its `score_components` value is always
  `0.0` — **guard against divide-by-zero** when backing out its raw score (skip weight 0).
- Not every module appears identically across symbols in production; here the full set is
  included per symbol for consistency, with a **mix of positive and negative** components
  and **varied sectors** so top-N ranking and sector grouping are non-trivial.
- Eight symbols are included: AAPL, MSFT, NVDA (Information Technology), JPM (Financials),
  XOM (Energy), JNJ (Health Care), PG (Consumer Staples), T (Communication Services).
  Actions span BUY / HOLD / SELL; five are held (nonzero `shares`), three are not.

### `timeseries_momentum_validation_summary.json`
Mirrors **`validation/harness.py::ValidationReport.to_summary_dict()`**, i.e. the schema
of `reports/<strategy_id>_validation_summary.json` consumed by `pilots/performance.py`.

Keys: `strategy_id`, `deployable`, `family_deployable`, `family_bh_significant`, `pbo`,
`dsr`, `sharpe`, `max_drawdown`, `is_options_selling`, `stress_gate_passed`,
`start_date`, `end_date`, `report_date`, `n_trials`, `family_multiple_testing`.

This is a **deployable=true** example: `pbo` (0.18) < 0.5, `dsr` (0.972) > 0.95, `sharpe`
(1.14) > 0.5, and `max_drawdown` (0.176) < 0.30, all clearing the honest deployability
gates in `validation/thresholds`. `family_multiple_testing` is `null` (the field is
populated only after the cross-strategy family sweep runs). The join key `strategy_id`
(`"timeseries_momentum"`) is what a `Pilot.validation_strategy_id` points at.

**Honesty (CONSTRAINT #4):** these fixtures never fabricate a passing gate — a fixture
that needs to exercise the non-deployable path should set the real metric out of range
(e.g. `pbo > 0.5`), never loosen a threshold.
