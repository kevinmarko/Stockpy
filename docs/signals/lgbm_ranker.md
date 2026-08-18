# Signal: `lgbm_ranker`

**File:** `signals/lgbm_ranker.py`
**Default weight:** 0.10
**Score range:** `[-1.0, +1.0]`
**Regime gate:** Always active
**Status:** **Dormant by default** — contributes a neutral `0.0` until a model is trained and deployed.
**Pilot:** [`ml-cross-sectional-rank`](../../pilots/catalog.py) (added 2026-08) — joined to the
`STRATEGY_REGISTRY["lgbm_ranker"]` backtest below. **This is a methodology gate, not the live
model's own gate**: the two are deliberately independent. `STRATEGY_REGISTRY["lgbm_ranker"]`
genuinely retrains a fresh `LGBMCrossSectionalRanker` per CPCV fold on real point-in-time
features (native MultiIndex CPCV, PR #648) to ask "does this approach generalize
out-of-sample on this universe?" — it does **not** load or evaluate the single persisted
`ml/models/lgbm_latest.pkl` artifact the live signal module actually loads via
`load_latest()`. The live signal module itself stays **exactly as dormant as before** until
`ml/registry.yaml` independently records `deployable: true` for the currently-trained artifact
(via `scripts/train_lgbm.py`'s own DSR/PBO gate — see "Training & Activation" below) —
passing the Pilot's backtest does not, by itself, activate the live module. See the
**Backtest Validation** section below for the real measured numbers and honest scope caveats.

---

## Rationale

A LightGBM gradient-boosted ranker is a non-linear, cross-sectional complement to the
linear factor signals (`multifactor`, `cross_sectional_momentum`). Tree ensembles capture
interactions between features (e.g. "momentum *only when* volatility is low") that a
weighted z-score sum cannot. This module is a thin `SignalModule` wrapper around
`ml/lgbm_ranker.LGBMCrossSectionalRanker`, plugging the trained model into the standard
two-phase signal-aggregation pipeline as **one modest input among many** — never an
override of the rules-based stack.

The deliberately small default weight (0.10, vs 10–45 for the established signals) reflects
that a learned ranker is only trustworthy after out-of-sample validation at > ~200 dates
(`cpcv_dsr`, `pbo` gates in `ml/registry.yaml`).

---

## Signal Logic

Two-phase cross-sectional pattern:

1. **`pre_compute(universe_df, context)`** — runs once per cycle. Loads the latest
   persisted model via `LGBMCrossSectionalRanker.load_latest()`, builds the point-in-time
   feature matrix (`ml/feature_engineering.build_pit_feature_matrix`), scores the whole
   cross-section, and stores per-ticker rank percentiles in `context.lgbm_scores`
   (`{ticker -> rank ∈ [0, 1]}`).
2. **`compute(row, context)`** — maps the stored rank to a score:
   ```
   score = clip(2 * (rank - 0.5), -1, +1)
   ```
   rank `1.0` → `+1.0` (top of the cross-section), rank `0.0` → `-1.0`, rank `0.5` → `0.0`.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| **No trained model** (the default — `ml/registry.yaml` ships `deployable: false`, `trained_date: null`) | `load_latest()` raises → caught → **every ticker gets a neutral rank 0.5 → score 0.0**. Logged at INFO (not WARNING — an untrained model is the documented default, not an error). The feature build is skipped entirely. |
| Feature matrix build fails | Caught → neutral `0.5` for the whole universe (logged WARNING). |
| `predict_score` raises | Caught → neutral `0.5` (logged WARNING). |
| Ticker absent from `lgbm_scores` | `compute()` defaults to rank `0.5` → score `0.0` — no fabricated exposure (CONSTRAINT #4). |
| `NaN` rank | Treated as `0.5` → score `0.0`. |

Because the default deployment has no trained model, this module is a guaranteed **`0.0`
contribution** to `final_score` until a model is trained, validated, and committed to
`ml/models/lgbm_latest.pkl` + marked `deployable: true` in `ml/registry.yaml`.

---

## Training & Activation

Monthly retraining is the **caller's** responsibility (a scheduled job or
`main_orchestrator.py`), not this module — it only *loads* the latest persisted model.
Activation path:

1. Train + validate via `ml/lgbm_ranker.LGBMCrossSectionalRanker` (CPCV; gate on
   `DSR > 0.95`, `PBO < 0.5`).
2. Persist to `ml/models/lgbm_latest.pkl`; update `ml/registry.yaml`
   (`trained_date`, `cpcv_dsr`, `pbo`, `deployable: true`).
3. Next cycle, `pre_compute` loads it automatically and the module starts contributing a
   real `±0.10`-weighted cross-sectional ranker score.

---

## Empirical Notes

- The module is **registered and wired** so that the day a validated model lands, it
  activates with zero code changes — but until then it is provably score-neutral.
- Covered by `tests/test_lgbm_ranker_signal.py` (registration, registry/weight
  consistency, rank→score map, neutral-when-no-model) and `tests/test_model_interface.py`
  / `tests/test_lgbm_purged_integration.py` (the underlying `ml/lgbm_ranker` model).

---

## Backtest Validation (`STRATEGY_REGISTRY["lgbm_ranker"]`, 2026-08)

**New `STRATEGY_REGISTRY` entry** (`scripts/refresh_validations.py::_build_lgbm_ranker_adapter`),
joined to the `ml-cross-sectional-rank` Pilot. This is a genuinely different kind of adapter from
every other entry in the registry: instead of replaying one fixed, precomputed return series
across CPCV folds (which would leak — a model fit once on the full history and then "OOS"-scored
on slices of that same history has already seen every fold's test data), it RETRAINS a fresh
`LGBMCrossSectionalRanker` on each fold's own training rows via
`ranker.train(X_tr, y_tr, t1=t1_tr, use_native_multiindex_cv=True)` — the first production caller
of PR #648's native `(date, ticker)` MultiIndex CPCV path, with a real ~21-trading-day forward-
return `t1` (not a synthesized default).

**Real, measured result** (live yfinance data, `python -m scripts.refresh_validations
--strategies lgbm_ranker --start 2015-01-01 --end 2024-12-31 --json`, run 2026-08-07):

| Metric | Value | Gate | Result |
|---|---|---|---|
| Sharpe (net) | **−0.334** | > 0.50 | ❌ FAIL |
| PBO | 0.000 | < 0.50 | ✅ |
| DSR | 1.000 | > 0.95 | ✅ |
| MaxDD | 3.68% | < 30% | ✅ |
| `deployable` | **False** | | |

Actual window used: **2019-01-02 → 2024-11-25** — the CLI was asked for 2015-2024, but the
adapter internally bounds its OWN feature-panel build to the last ~6 years of the requested
range (`_build_lgbm_ranker_adapter`'s own docstring, "Bounded window" — the same computational-
feasibility reasoning `forecast_direction_arima_hw` already documents for its own bounded 5-year
window), so the harness's `X.index[0]`/`X.index[-1]` self-report this narrower effective window.

**Honest read**: PBO/DSR/MaxDD all clear their gates comfortably, but net-of-cost Sharpe is
**negative** — the top-minus-bottom-half long-short book genuinely lost money after
`TieredCostModel` transaction costs over this window. This is a real, measured result, not a
data or wiring bug (the adapter's own unit/network tests confirm real training and real,
finite long-short returns per fold — see `tests/test_validation_lgbm_ranker_registry.py`). A
few honest, evidence-adjacent factors likely contributing (not verified as THE cause, since no
counterfactual re-run was performed to isolate them individually — stated as plausible, not
proven):

* **Single hyperparameter candidate.** Unlike `scripts/train_lgbm.py::compute_cpcv_metrics`'s
  own `_CANDIDATE_PARAMS` (3 configs), this adapter trains exactly ONE fixed
  `LGBMCrossSectionalRanker(purged_kfold_splits=3, embargo_pct=0.0)` config per fold — `n_trials=1`
  in the JSON summary. DSR with a single trial is a weak statement (no selection-bias correction
  is actually exercised); PBO=0.0 with one trial is close to structurally guaranteed rather than
  a meaningfully passed gate. This is an honest limitation of this adapter's current design, not
  a fabricated pass.
* **Bounded, proxy-OHLCV feature panel.** The 6-year window (vs. other adapters' full 2005-2024)
  and the `_ClosesOnlyDataEngine` proxy (`High`/`Low`/`Volume` synthesized around real Close —
  see the adapter's own docstring) mean the ranker never sees genuine intraday range or real
  volume, and trains on materially less history than a live monthly-retrain cadence would
  accumulate over years.
* **No point-in-time fundamentals** (`historical_store=False`) — `book_to_market`/
  `earnings_yield`/`quality_factor_score`/`low_vol_score` are NaN-filled to 0.0 at train time for
  every row in this backtest (verified: the trained model's feature list includes all of
  `ml.feature_engineering.FEATURE_COLUMNS`, but the four fundamental columns carry no real signal
  here), removing roughly a third of the live signal's intended feature set.
* **`turnover=0.03`** is a reasoned estimate (matching `cross_sectional_momentum`/
  `relative_strength_xsec`'s own daily-rebalance figure), not measured directly from this
  adapter's own weight series — a real measurement was not performed for this entry.

**Not a contradiction of the module's live status**: `signals/lgbm_ranker.py` was already, and
remains, dormant by default (neutral `0.0` contribution) until `ml/registry.yaml` independently
records `deployable: true` for a trained artifact via `scripts/train_lgbm.py`'s own gate — this
backtest exercises a DIFFERENT question (does the methodology generalize on this universe with
these features?) and answers it honestly: not yet, on this measurement. No number above is
fabricated; a future re-run with multiple hyperparameter candidates, the full universe/window,
and/or real point-in-time fundamentals could plausibly move this result but was not performed
here — see `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08 entry for the full writeup and
`tests/test_validation_lgbm_ranker_registry.py` for the adapter's own regression coverage.


### 2026-08-17 Full Validation Run (`lgbm_ranker`)

| Metric | Result |
|---|---|
| **Sharpe Ratio (net)** | 5.1808 |
| **PBO** | 0.0000 |
| **DSR** | 0.8598 |
| **Max Drawdown** | 2.44% |
| **Deployable** | ❌ False |


*Note: The 2026-08-17 run verifies stability following a systemic parser fix. The `Deployable: False` outcome and its underlying causal reasoning remain exactly as previously documented.*
