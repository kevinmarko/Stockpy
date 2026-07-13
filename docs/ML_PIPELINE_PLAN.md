# ML Pipeline Plan — Honest MLOps for InvestYo (Claude-facing)

## Context

This is the Claude-owned plan for making the platform's machine-learning models
**honestly deployable** via automated retraining and real Combinatorial Purged
Cross-Validation (CPCV) gates. It is one half of a two-agent effort: this doc
covers the ML side (`ml/`, `scripts/train_*.py`, `scripts/retrain_models.py`,
the GUI ML-registry reader); the data side (a real point-in-time historical
fundamentals feed from SEC EDGAR) is owned by a separate Gemini agent and
specified in [`DATA_LAYER_PLAN.md`](./DATA_LAYER_PLAN.md).

**Non-negotiable honesty principle:** a gate is never loosened to force a green
result. A model that fails `DSR > 0.95` / `PBO < 0.5` stays `deployable: false`
with weight `0` — that is the *correct, honest* outcome, not a bug to be
"fixed." The value of this pipeline is that its deployability verdicts are
trustworthy, so no phase in this plan may weaken a threshold, skip a stress
window, or fabricate a metric to make a model look shippable.

## Two-agent boundary

| Concern | Owner | Touches |
|---|---|---|
| ML models, training, CPCV gates, retraining automation, ML registry, ML GUI monitoring | **Claude (this plan)** | `ml/`, `scripts/train_lgbm.py`, `scripts/train_meta_labelers.py`, `scripts/retrain_models.py`, `scripts/com.investyo.monthly-retrain.plist`, `gui/panels/analytics.py` (ML Registry reader), `scripts/preflight_check.py` (optional freshness check) |
| PIT historical fundamentals feed (SEC EDGAR), `fundamentals_history` PIT seams, backfill, PIT audit/coverage | **Gemini** ([`DATA_LAYER_PLAN.md`](./DATA_LAYER_PLAN.md)) | `data/edgar_fundamentals.py`, `data/historical_store.py` (fundamentals seams only), `scripts/backfill_edgar_fundamentals.py`, `validation/pit_fundamentals.py` |

**Hard rule for Claude:** do NOT edit `data/edgar_fundamentals.py`,
`scripts/backfill_edgar_fundamentals.py`, `validation/pit_fundamentals.py`, or
the `fundamentals_history` DDL / PIT seams in `data/historical_store.py`. Phase
M3 *consumes* the contract Gemini delivers
(`HistoricalStore.get_fundamentals_asof(symbol, as_of_date)`) but does not
implement it. Conversely, Gemini must not edit `ml/`.

---

## Phase M1 — Real CPCV metrics for the meta-labelers

### Problem
`scripts/train_meta_labelers.py` (~line 393) currently writes
`cpcv_dsr=None, pbo=None` into the registry entry for every trained
meta-labeler. Because `registry_io.compute_deployable` (`ml/registry_io.py:65`)
requires real `DSR > 0.95` AND `PBO < 0.5` to return `True`, a `None`/`None`
pair means the meta-labelers are **`deployable: false` forever** — not because
they failed validation, but because they were never validated at all. That is a
silent, dishonest gap: the registry looks gated but the gate never ran.

### Change
Compute genuine CPCV DSR/PBO for each meta-labeler, mirroring the already-working
LGBM path in `scripts/train_lgbm.py::compute_cpcv_metrics` (`:158`), which drives
`validation.metrics.run_cpcv_evaluation` (`:133`). Run the evaluation across
**≥ 2 hyperparameter configurations** so PBO (probability of backtest
overfitting across the config family) is meaningful rather than a degenerate
single-config value. Write the real `cpcv_dsr` / `pbo` into the registry via the
existing `registry_io` write path so `compute_deployable` produces an honest
verdict. If a meta-labeler genuinely fails the gate, it stays
`deployable: false` — that is a correct output of this phase.

### Files
- `scripts/train_meta_labelers.py` (compute + record CPCV metrics; ~`:393` write site, `train_signal()` ~`:324`)
- Read-only reference: `scripts/train_lgbm.py::compute_cpcv_metrics` (`:158`), `validation/metrics.py::run_cpcv_evaluation` (`:133`), `ml/registry_io.py::compute_deployable` (`:65`)

### Verify
- Re-run meta-labeler training on a small universe; confirm the registry entry now carries numeric `cpcv_dsr`/`pbo` (not `None`).
- Confirm a deliberately weak signal produces `deployable: false` with the real failing metric visible — the gate must be able to say "no."
- `mcp__investyo__run_platform_tests` green for the ML/validation test surface; `mcp__investyo__query_investyo_db` to inspect any persisted metric rows if applicable.

---

## Phase M2 — Automated monthly retraining (honest-gated)

### Problem
Models are trained ad-hoc by hand. There is no scheduled, reproducible
retraining job, so registry entries drift stale and there is no operational
guarantee that a deployed model was recently re-validated. Retraining must NOT
be folded into the per-advisory-cycle hot path — it is a heavy, monthly-cadence
batch job, not something the pipeline pays for every run.

### Change
Add a new `scripts/retrain_models.py` that orchestrates a full retrain:
`train_lgbm.run_training()` (`:331`) + `train_meta_labelers.train_signal()`
(`:324`) for each configured signal, each honest-gated through
`registry_io.update_model_metrics` (`:90`) so the registry reflects real,
freshly-computed CPCV verdicts. Schedule it monthly via a launchd plist
`scripts/com.investyo.monthly-retrain.plist`, mirroring the existing
`scripts/com.investyo.daily-advisory.plist` structure. Also fix the aspirational
registry header text (`ml/registry.yaml` ~`:4` and the header emitted by
`ml/registry_io.py` ~`:40`) so it describes the retraining job that now actually
exists rather than an intended-future one.

**Explicitly NOT run per advisory cycle** — the daily advisory pipeline must be
untouched by this job.

### Files
- New: `scripts/retrain_models.py`
- New: `scripts/com.investyo.monthly-retrain.plist`
- Edit: `ml/registry.yaml` (~`:4` header), `ml/registry_io.py` (~`:40` header)
- Read-only reference: `scripts/train_lgbm.py::run_training` (`:331`), `scripts/train_meta_labelers.py::train_signal` (`:324`), `registry_io.update_model_metrics` (`:90`), `scripts/com.investyo.daily-advisory.plist`

### Verify
- Run `scripts/retrain_models.py` end-to-end on a small universe; confirm both model families retrain and the registry is updated with honest gate verdicts.
- `plutil -lint scripts/com.investyo.monthly-retrain.plist` (or equivalent) parses; the plist cadence is monthly and points at `retrain_models.py`.
- Confirm no advisory-cycle code path imports or triggers `retrain_models.py`.
- `mcp__investyo__run_platform_tests` green.

---

## Phase M3 — Consume PIT fundamentals in the training panel (gated on Gemini)

### Problem
`ml/training_data.build_training_panel` (`:278`) builds each per-date
`universe_df` purely from `_pit_ticker_row(prior_close)` (`:205`), which is
**price-derived only**. As a result every fundamentals / factor-Z column
(`book_to_market`, `earnings_yield`, `quality_factor_score`, `Value_Z`,
`Quality_Z`, …) comes out `NaN` in the training panel. This is confirmed to be
*not* a lookahead bug — it is simply missing data: there is no PIT fundamentals
source to inject. The ML models therefore train blind to the Value/Quality
factors.

### Change
Once Gemini has delivered
`HistoricalStore.get_fundamentals_asof(symbol, as_of_date)` (see
[`DATA_LAYER_PLAN.md`](./DATA_LAYER_PLAN.md)), inject its output into the
per-date universe rows in `build_training_panel`'s per-date loop
(`:369–411`), at the seam **~line 388, immediately before the
`build_pit_feature_matrix` call**. The returned dict uses the exact key names
`processing_engine.calculate_fundamental_metrics` already expects
(`book_to_market`, `earnings_yield`, `quality_factor_score`, `log_market_cap`,
`pe_ratio`, `pb_ratio`, `roe`, `market_cap`, `eps`), so no downstream rename is
needed — the factor-Z columns populate from real as-of data. NaN is preserved
(never fabricated) for any symbol/date with no filing on or before the decision
date, keeping the no-lookahead guarantee (`report_date <= as_of_date`).

This is expected to ship as a **later, 5th PR**, after the Gemini data layer is
proven (offline tests green + a coverage report showing ≥ N years of PIT history
for the core tickers). Do not start M3 until that signal arrives.

### Files
- `ml/training_data.py` (injection seam ~`:388`, per-date loop `:369–411`, `_pit_ticker_row` `:205`)
- Contract consumed (Gemini-owned, do not edit): `HistoricalStore.get_fundamentals_asof`

### Verify
- After injection, confirm the training panel's `book_to_market` / `earnings_yield` / `quality_factor_score` / factor-Z columns are populated (non-NaN) for tickers/dates with EDGAR coverage, and still NaN where there is genuinely no prior filing.
- Re-run the no-lookahead perturbation checks: perturbing data strictly after a decision date must not change that date's fundamentals row.
- `mcp__investyo__run_backtest` on a factor strategy over the newly-populated panel to sanity-check the Value/Quality factors now carry signal; `mcp__investyo__run_platform_tests` green.

---

## Phase M4 — GUI model monitoring

### Problem
The GUI's ML Registry reader in `gui/panels/analytics.py` surfaces basic
role/trained-date/deployable info but does not make model *staleness* or the
*honest gate metrics* legible at a glance, so an operator cannot easily see that
a model is overdue for retraining or why it is (not) deployable.

### Change
Extend the ML Registry reader in `gui/panels/analytics.py` to show, per model:
last-trained **age**, a `needs_retrain` flag (via
`ml/meta_labeling.py::needs_retrain`, `:270`), the real `DSR` / `PBO`, and a
`deployable` chip (green/red) driven by the honest registry verdict — never a
recomputed-looser value. Optionally add a
`scripts/preflight_check.py::check_model_freshness` check (warning-only unless
live) so the pre-live gate flags overdue models. Honesty rule holds: the chip
shows exactly what the registry says; a failing model shows red.

### Files
- `gui/panels/analytics.py` (ML Registry reader)
- Optional: `scripts/preflight_check.py` (new `check_model_freshness`)
- Read-only reference: `ml/meta_labeling.py::needs_retrain` (`:270`), `ml/registry.yaml`, `ml/registry_io.py`

### Verify
- Load the Analytics tab; confirm each model row shows age, `needs_retrain`, DSR/PBO, and a correctly-colored deployable chip.
- Force a stale `trained_date` in a scratch registry and confirm `needs_retrain` flips and (if added) `check_model_freshness` warns.
- `mcp__investyo__run_platform_tests` green.

---

## MCP verification

Use the InvestYo MCP tools to verify each phase against a live-ish platform state
rather than only unit tests:

- **`mcp__investyo__query_investyo_db`** — inspect registry-adjacent rows,
  training-panel-derived tables, or any persisted metrics after a training run.
- **`mcp__investyo__run_backtest`** — sanity-check that a strategy/model actually
  carries signal (esp. M3's Value/Quality factors once PIT fundamentals land).
- **`mcp__investyo__run_platform_tests`** — run the platform test suite after
  each phase; must be green before opening a PR.
- **`mcp__investyo__trigger_data_engine`** — refresh underlying data when a
  verification run needs current bars/fundamentals.

For third-party library API/config questions during implementation (e.g.
LightGBM, launchd plist schema), use the `context7` docs tools rather than
answering from memory.
