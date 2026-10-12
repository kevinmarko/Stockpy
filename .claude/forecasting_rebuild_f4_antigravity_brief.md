# Antigravity brief: forecasting rebuild F4, ungated archive (part 1)

Operator: Kevin. Written by Claude Code on 2026-10-12. Claude Code is working on shrink step 5.4b-ii
in parallel, so stay out of the files listed under "Do not touch".

## Read first
- `AGENTS.md` (the whole file; the freeze, branch and verification rules apply to you).
- `.claude/forecasting_rebuild_implementation_plan.md`, section **F4: Streamline**.
- `.claude/forecasting_rebuild_f1_walkthrough.md` and the F2/F3 PRs (#1073, #1076) for how the blend
  is assembled today.

## Scope (this PR only)
Archive to `legacy/` the parts of F4 that do **not** depend on the F3 shadow verdict:
1. BERT-LLA: `forecasting/bert_lla.py`, its settings, its wiring in `forecasting_engine.py`, and its
   tests (`tests/test_bert_lla.py`, `tests/test_bert_lla_lookahead.py` → `legacy/tests/`).
2. The LSTM-attention endpoint (find it in `api/metrics_api.py` / `api/pilots_api.py`, plus
   `pilots/lstm_diagnostic.py` if only that endpoint uses it) and any webapp caller.
3. The sector static-preference blend, `SECTOR_FORECAST_CONFIGS` and `FORECAST_DRIFT_SHRINKAGE`
   (`settings.py`, `forecasting/sector_configs.json`, `scripts/backtest_sector_configs.py` and its
   test, `forecasting_engine.py`).
4. The stray `forecasting/sector_configs 2.json` from the plan **no longer exists**. Confirm that
   and note it in the walkthrough. Don't go looking for a substitute to delete.

Start with `git grep -nE "BERT_LLA|bert_lla|lstm_attention|SECTOR_FORECAST_CONFIGS|FORECAST_DRIFT_SHRINKAGE|sector_configs"`.
It also hits `settings_domains.py`, `shared/env_io.py`, `runtime_flags.py`, `.env.example`, the
settings census files, `requirements-optional.txt`, the CI workflows and `ml/asvi_feature_engineering.py`.
Check every hit.

## Out of scope
- CNN-LSTM and Prophet. They are archived **only if** they fail the F3 shadow gate, and the operator
  hasn't reviewed that yet. Leave `cnn_lstm_worker.py` and the TF subprocess pool alone.
- The "one code path" and quantile-persistence bullets of F4. They come in a later PR.
- Any change to what the published blend or decisions are, beyond removing the archived components.
  If removing one changes blend weights for live symbols, stop and report the diff. Don't absorb it.

## Do not touch (Claude Code's 5.4b-ii is editing these)
`launch.command`, `scripts/install_*`, `scripts/com.investyo.*.plist`, `.claude/skills/robinhood-execution/`,
`.claude/commands/rh-execute*`, `docs/RUNBOOK.md`, `README.md`, the status lines in `CLAUDE.md`/`AGENTS.md`.
If you need a forecasting-bullet edit in `AGENTS.md`, keep it to the forecasting lines and expect to
rebase.

## Rules that bite here
- This is the "Everything else" tier. Use your own branch `forecasting-rebuild-f4-archive` in your own
  worktree, write an implementation plan first, and have the operator review it before writing code.
  Save the plan, task and walkthrough as `.claude/forecasting_rebuild_f4_archive_{implementation_plan,task,walkthrough}.md`.
  Never use a bare `plan.md`.
- Removing a settings field: remove its readers first, then the field, the census entries
  (`docs/settings_field_census.*`, `docs/settings_liveness.json`), `.env.example` and any `ALLOWED_KEYS`
  entry, in the step 4f style (#1074). Note whether the live `.env` or `output/runtime_flags.json` sets
  any of them. **Never** edit `.env`.
- Record every moved file in `legacy/README.md`. Nothing active may import from `legacy/`.
- Never use `git stash`. Make a WIP commit instead.

## Verification (must be shown green, not assumed)
- `pytest tests/test_forecasting_engine.py tests/test_forecast_rebuild_f2.py tests/test_forecast_rebuild_f3.py tests/test_forecast_tracker.py`
- `make ci` with zero failures.
- `npm run --prefix webapp typecheck` if any webapp file changed.
- One frozen-input or dry-run cycle showing which symbols' published forecasts moved (expected: none,
  or only those that had BERT-LLA / sector-preference weight). List them in the walkthrough.

## Docs in the same PR
`docs/architecture/signal-engines.md` (`forecasting_engine.py` entry), the forecasting bullets in
`AGENTS.md` (the hook mirrors them to `CLAUDE.md`), `docs/FEATURE_TIER_HISTORY.md`, and a status note
in `.claude/forecasting_rebuild_implementation_plan.md` under F4.

Open the PR. Don't merge it until the operator has reviewed the plan and the moved-symbols list.
