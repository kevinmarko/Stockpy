# Forecasting rebuild F4, part 1: archive what the data already rules out

Branch `forecasting-rebuild-f4-archive` (Antigravity worktree). Brief:
`.claude/forecasting_rebuild_f4_antigravity_brief.md`. Parent plan:
`.claude/forecasting_rebuild_implementation_plan.md` § F4.
Tier: "Everything else" (it touches `forecasting_engine.py`). **No code is written until the operator
has reviewed this plan.**

## 0. What I measured before writing this (read-only)

| Question | Answer | How |
|---|---|---|
| Does the live `.env` set any of the target keys? | **Yes.** `BERT_LLA_ENABLED=true`, `BERT_LLA_BLEND_ENABLED=true`, `BERT_LLA_ABLATION_ENABLED=true`, `BERT_LLA_WINDOW_SIZE=365`, `BERT_LLA_MIN_SENTIMENT_COVERAGE=0.5`, `SECTOR_FORECAST_CONFIG_PATH='forecasting/sector_configs.json'`, `SECTOR_FORECAST_CONFIGS='{}'`. `FORECAST_DRIFT_SHRINKAGE` is **not** set (default 0.0). | `grep` of key names in the main checkout's `.env` (read only, never edited) |
| Does `output/runtime_flags.json` override any of them? | No. Its 8 flags are unrelated. | key list only |
| Has BERT-LLA ever produced a forecast? | **No.** `torch` is not installed in the live `.venv`, so `run_bert_lla_forecast` returns the zero sentinel every cycle, despite the flags. `forecast_errors` has **0 rows** ever for `bert_lla`, `lstm_attention` or `lstm_baseline`. | `import torch` in the main `.venv`; sqlite `mode=ro` |
| Is `FORECAST_DRIFT_SHRINKAGE` live? | No: default 0.0, so `mu * (1 - 0)` is the identity. | settings + `.env` |
| Does `SECTOR_FORECAST_CONFIGS` change anything live? | No: it is `{}`. | `.env` |
| Does the sector config (artifact) change anything live? | **Yes, two things.** The committed `forecasting/sector_configs.json` sets `days=30` for all 11 sectors (so `Target_Days` = 30 for every known sector, and 60 only for `Unknown`/missing sectors, the hard-coded fallback). Its `model` preference (ARIMA: Basic Materials, Communication Services, Consumer Cyclical, Industrials; HW: Energy, Utilities; MC: the rest) picks the static-blend branch. | file contents |
| How often is the static sector-preference blend the published forecast? | **Often.** With `FORECAST_SKILL_WEIGHTING_ENABLED=true` (window 365, min_obs 30), `get_skill_weights` returns `{}` (so `_blend_with_skill` falls to the static branch) for **185 of 316** (symbol, horizon) pairs in the last 2 days' universe (79 symbols): all h=90, most h=60, and **32 pairs at h=30** (new/young symbols). | `ForecastTracker(readonly=True)` census, script in my scratch dir |
| Where does the sector *preference* (not just the fixed weights) bite? | In the 63-symbol snapshot: ARIMA-preferred sectors 12 symbols / 37 pairs (8 at h=30: e.g. AMZN, GOOG, VZ, STRL, DE, PWR); HW-preferred sectors 7 symbols / 7 pairs (h=90 only: XOM, CVX, VLO, MPC, PBF, ET, AM); 16 symbols had no sector in the snapshot. Exact list comes from the frozen-input diff (§4). | snapshot `signals[].sector` × census |
| Stray `forecasting/sector_configs 2.json`? | **Does not exist.** Confirmed; noted in the walkthrough, no substitute hunted. | `ls` |

**Consequence for scope.** BERT-LLA, the LSTM-attention endpoint, `SECTOR_FORECAST_CONFIGS` and
`FORECAST_DRIFT_SHRINKAGE` are byte-identical to remove. **Removing the sector static-preference blend
is not**: it moves published `Forecast_{h}` for live symbols. Per the brief I am stopping here and
reporting that instead of absorbing it. See decision D1.

## 1. Decisions I need from you

**D1 — the sector static-preference blend (the only decision-changing item).**
- **(a) Recommended: remove it in this PR, but don't merge until you've accepted the frozen-input diff.**
  `_blend_with_skill`'s static fallback keeps its fixed weights (CNN-LSTM/ARIMA/MC + Prophet overlay,
  i.e. today's "MC" branch) and loses the `preferred_model` branches (ARIMA-alone, HW-alone).
  `Target_Days` becomes a constant 30 (what the artifact already gives every known sector). Moves:
  static-fallback pairs in ARIMA/HW sectors (the `Forecast_{h}` value), and for `Unknown`-sector rows
  `Target_Days`/`ARIMA`/`MC_Target`/`MC_Lower`/`MC_Upper` go 60-day → 30-day (display/persisted columns
  only; no signal or advisory reads them). `/metrics/forecast` always passes `sector="Unknown"`, so its
  `Target_Days` also goes 60 → 30. The walkthrough lists every symbol whose `Forecast_{h}`,
  `forecast_alignment` points, action or sellRange moved.
- **(b) Split it.** This PR archives everything byte-identical: `SECTOR_FORECAST_CONFIGS`,
  `SECTOR_FORECAST_CONFIG_PATH`, the backtest script, the `validation/sector_*` pipeline and the
  artifact, with the artifact's current mapping frozen as the in-code constant. The preference branch
  itself goes with the F3 flip (when the gated blend publishes, it supersedes the static fallback).
  Zero published moves now, but the "static-preference blend" item stays open.
- (c) Remove the whole static fallback (cold start → equal weights over survivors). Bigger move, not
  recommended for an archive PR.

**D2 — `cnn_lstm_worker.py::fit_predict_lstm_attention` / `load_predict_lstm_attention`.** After the
endpoint goes they have no caller. The brief says leave `cnn_lstm_worker.py` alone, so **recommended:
leave both functions and `tests/test_lstm_attention_worker.py` in place** and flag them for F4 part 2
(which decides CNN-LSTM). Alternative: move them now too.

**D3 — `AGENTS.md`.** It currently has no forecasting bullets (the slim-down removed them). Recommended:
add one bullet under "Archived subsystems" ("Forecasting extras (F4 part 1)") and nothing else; the
sync hook mirrors it to `CLAUDE.md`. I won't touch the status lines Claude Code's 5.4b-ii is editing,
and I'll rebase if it collides.

## 2. Changes

### 2.1 BERT-LLA → `legacy/`
- `git mv forecasting/bert_lla.py legacy/forecasting/bert_lla.py`;
  `git mv tests/test_bert_lla.py tests/test_bert_lla_lookahead.py legacy/tests/`.
- `forecasting_engine.py`: drop the `TORCH_AVAILABLE` re-export import, `run_bert_lla_forecast`,
  `self.last_bert_lla_attention` (init + per-call reset), the `bert_lla_multi` training block, the
  `"bert_lla"` guard anchor, the blend-eligibility block, and the ablation-recording block. Fix the
  comments that name BERT-LLA.
- `forecasting/forecast_tracker.py`: remove `MODEL_LSTM_BASELINE`, `MODEL_LSTM_ATTENTION`,
  `MODEL_BERT_LLA` from the constants, `ALL_MODEL_NAMES` (10 → 7), `NON_BLEND_MODEL_NAMES`,
  `_SKILL_ARITHMETIC_EXCLUDED` and the docstring table. Safe: 0 ledger rows carry those names.
- `api/metrics_api.py::get_forecast`: drop the `attention` key and its docstring paragraph.
- `settings.py`: remove the 5 `BERT_LLA_*` fields. Readers first, then the field, then:
  `shared/env_io.py` `ALLOWED_KEYS` (5), `api/pilots_api.py` tunables group (5 rows),
  `pilots/settings_domains.py` (`BERT_LLA_` prefix rule), `docs/settings_field_census.{json,md}` and
  `docs/settings_liveness.json` (regenerated with `scripts/measure_settings_census.py --write` and
  `scripts/settings_liveness.py --write`), and the comment in `tests/test_settings_keysets.py`.
  `.env.example` doesn't list them. The live `.env` still sets all 5; `extra="ignore"` means a stale key
  is ignored, not a crash (same as step 4f). **I won't edit `.env`**; the walkthrough tells you which
  lines you can delete.
- `requirements-optional.txt`: keep `torch` (FinBERT still uses it); delete the BERT-LLA paragraph.
- Tests that only set `BERT_LLA_ENABLED=False` (`test_forecast_rebuild_f2.py`, `_f3.py`,
  `test_forecast_ledger_contamination_and_naive_backfill.py`): drop that line. In
  `test_forecasting_engine.py`, remove the 3 `test_bert_lla_*` tests and fix the comments that mention
  it. Update `test_forecast_tracker.py::test_all_model_names_contains_ten_entries` (→ 7),
  `test_metrics_api.py` (the attention tests go; one assertion that `attention` is absent stays),
  `test_pilots_api_tunables.py` (5 keys), and the comment in `test_browser_diagnostics.py`.
- CI: remove `tests/test_bert_lla_lookahead.py` from `.github/workflows/ml_leakage_audit.yml`; remove
  the entries from `.test_durations.json`.
- **Webapp:** remove the `ForecastAttention*` types and the `attention` field in `types.ts`
  (`[key: string]` widening reverts to `number | null`), `mockBertLlaAttention` and its use in
  `mock.ts`, the 5 `BERT_LLA_*` mock settings fixtures (×2 blocks) and the ablation rows in the mock
  error-by-model fixture, `AttentionHeatmapStrip` in `charts.tsx`, its use and caption in
  `ForecastViewer.tsx`, the ablation caption in `SymbolDetail.tsx`, the "attention weight" glossary
  entry in `helpContent.ts`, and the matching vitest cases (`ForecastViewer.test.tsx`,
  `SymbolDetail.test.tsx`).

### 2.2 LSTM-attention endpoint → `legacy/`
- `api/pilots_api.py`: remove `POST /pilots/ml/lstm-attention-forecast`
  (`run_lstm_attention_forecast_endpoint`). It has no webapp caller.
- `git mv pilots/lstm_diagnostic.py legacy/pilots/` (its only caller is the endpoint).
- `forecasting_engine.py`: remove `run_lstm_attention_forecast` (only caller is `lstm_diagnostic`).
- `git mv ml/asvi_feature_engineering.py legacy/ml/`: its only users are the endpoint
  (`resolve_sector_proxy`) and `run_lstm_attention_forecast` (`build_lstm_attention_tensors`).
- Tests → `legacy/tests/`: `test_pilots_lstm_attention_forecast_sector_proxy.py`,
  `test_asvi_feature_engineering.py`, `test_lstm_attention_lookahead.py`. Update the
  `lstm_diagnostic` entry in `tests/test_pilots_strategy_matrix.py`'s allowlist. Also update the
  endpoint row in `docs/settings_field_census.json` (regenerated).
- Per D2, `cnn_lstm_worker.py` and `tests/test_lstm_attention_worker.py` stay untouched.

### 2.3 Sector static-preference blend, `SECTOR_FORECAST_CONFIGS`, `FORECAST_DRIFT_SHRINKAGE`
(written for D1 (a); under (b) I keep `preferred_model` and freeze the artifact's mapping instead)
- `forecasting_engine.py`: remove `_DEFAULT_SECTOR_CONFIGS`, `_load_sector_configs`,
  `self.sector_configs`, the per-row config lookup (`Target_Days` = module constant
  `_PRIMARY_TARGET_DAYS = 30`), the `preferred_model` parameter and its ARIMA/HW branches in
  `_blend_with_skill` (the remaining static branches are unchanged), and the drift-shrinkage lines in
  `run_monte_carlo`.
- `settings.py`: remove `SECTOR_FORECAST_CONFIG_PATH`, `SECTOR_FORECAST_CONFIGS` (+ its
  `field_validator`), `FORECAST_DRIFT_SHRINKAGE`, then: `shared/env_io.py` `ALLOWED_KEYS` (3) and
  `_JSON_KEYS` (1), `api/pilots_api.py` tunables (2 rows + the comment at ~4265), `.env.example`
  (2 lines), `runtime_flags.py` docstring example (switch to another dict field), census/liveness
  regenerated, `webapp/src/api/mock.ts` (tunable-reader map + 2 fixtures), and the
  `GenericSettingsEditor.tsx` comment. The live `.env` still sets the 2 sector keys; same `extra="ignore"` note.
- → `legacy/`: `forecasting/sector_configs.json`, `scripts/backtest_sector_configs.py`,
  `validation/sector_forecast_backtest.py`, `validation/sector_config_io.py`,
  `validation/sector_forecast_types.py`, `validation/forecast_accuracy_metrics.py` (all four serve only
  this pipeline), with `tests/test_sector_config_io.py`, `test_sector_forecast_backtest.py`,
  `test_forecast_accuracy_metrics.py`, `test_sector_backtest_settings.py`,
  `test_forecasting_engine_config_loader.py`.
- `tests/test_backtest_sector_configs_cli.py` is split: the `build_ticker_sector_map` tests and the
  committed-CSV tests (`forecasting/data/ticker_sectors.csv` stays; `engine/portfolio_exposure.py` and
  `data/sneqr_quality_facts.py` read it) move to a new `tests/test_build_ticker_sector_map.py`, with the
  11 sector names inlined in place of `bsc._DEFAULT_SECTOR_CONFIGS`. The rest goes to `legacy/tests/`.
- `tests/test_sector_descriptions.py`: drop `test_superset_of_sector_configs_json` (the CSV-superset
  test already covers the same names). Fix the comment in `data/sector_descriptions.yaml`.
- `tests/test_forecasting_engine.py`: the 2 `mock.Mock(FORECAST_DRIFT_SHRINKAGE=0.0)` sites and the
  sector-config comment at ~379; tests that pass `preferred_model` get updated.
- `.gitignore`: remove the `!forecasting/sector_configs.json` exception and its comments.
  `.github/workflows/ci.yml`: fix the xdist-group comment. `.test_durations.json`: drop the moved files.

### 2.4 Other files the grep hits (checked, no change)
`.claude/*` history docs, `docs/known_issues/*` and older `FEATURE_TIER_HISTORY` entries are history
and stay as written. `cnn_lstm_worker.py` (D2).

## 3. Documentation (same PR)
- `legacy/README.md`: a new "Forecasting rebuild F4 part 1 (2026-10)" section listing every moved
  file, why, and that BERT-LLA and the endpoint never produced a live forecast.
- `docs/architecture/signal-engines.md` (`forecasting_engine.py` entry): no BERT-LLA, no sector
  config, static fallback = fixed weights, `Target_Days` constant 30.
- `docs/architecture/testing.md` and `docs/architecture/observability-and-apis.md` /
  `webapp-and-gui.md` where they name the moved tests/endpoint/overlay (checked when editing).
- `docs/FEATURE_TIER_HISTORY.md`: a dated entry.
- `.claude/forecasting_rebuild_implementation_plan.md` § F4: a status note (part 1 done, what's left).
- `AGENTS.md` per D3 (`CLAUDE.md` mirrored by the hook; `tests/test_agent_docs_sync.py` checks it).
- `.claude/forecasting_rebuild_f4_archive_{task,walkthrough}.md` and the brief, committed with the PR.

## 4. Verification (run and shown green, not assumed)
1. `pytest tests/test_forecasting_engine.py tests/test_forecast_rebuild_f2.py tests/test_forecast_rebuild_f3.py tests/test_forecast_tracker.py`
   plus every test file touched above.
2. `git grep -nE "BERT_LLA|bert_lla|lstm_attention|SECTOR_FORECAST_CONFIG|FORECAST_DRIFT_SHRINKAGE|sector_configs"`
   outside `legacy/`, `.claude/` and dated history docs → empty (or each remaining hit explained).
   Plus a check that nothing active imports `legacy`.
3. `make ci`, zero failures.
4. `npm run --prefix webapp typecheck` and `npm run --prefix webapp test`, then `npm run dev` with a
   browser check of Forecast Viewer and Symbol Detail (no console errors, overlay gone, layout intact).
5. **Frozen-input diff** (method of the F2 walkthrough, read-only, scratch script): the latest
   `state_snapshot.json` universe, `price_bars` via sqlite `mode=ro` truncated to the snapshot date,
   `ForecastTracker(readonly=True)` with the operator's skill settings, a capturing tracker so nothing is
   written. Run `generate_forecast` per symbol with origin/main's engine and with the branch engine on
   the same inputs. List every symbol whose `Forecast_{10,30,60,90}`, `Target_Days`, `MC_*`,
   `forecast_alignment` points, action or sellRange moved, with the cause. Expected: BERT-LLA /
   endpoint / shrinkage / overrides move nothing; under D1 (a) only static-fallback pairs in ARIMA/HW
   sectors and `Unknown`-sector `Target_Days`/`MC_*` move.

## 5. PR
Open the PR with the moved-symbols table in the body. **Don't merge** until you've reviewed the plan and
that list (brief), even after it goes green.

## Do-not-touch list (Claude Code's 5.4b-ii)
`launch.command`, `scripts/install_*`, `scripts/com.investyo.*.plist`,
`.claude/skills/robinhood-execution/`, `.claude/commands/rh-execute*`, `docs/RUNBOOK.md`, `README.md`,
the status lines in `CLAUDE.md`/`AGENTS.md`. None of the changes above touches them.
