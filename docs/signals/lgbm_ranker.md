# Signal: `lgbm_ranker`

**File:** `signals/lgbm_ranker.py`
**Default weight:** 0.10
**Score range:** `[-1.0, +1.0]`
**Regime gate:** Always active
**Status:** **Dormant by default** — contributes a neutral `0.0` until a model is trained and deployed.
**Pilot:** None — no Pilot packages this module until it passes the model's own DSR gate;
surfacing it as a copyable strategy before then would advertise a dormant no-op as a
live signal.

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
