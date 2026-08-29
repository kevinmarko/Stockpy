# Task Tracker: Fix Fabricated PBO/DSR for Zero-Sample options_meta_labeler

- [x] Reproduce/confirm the bug: `get_model_registry_status` → `options_meta_labeler`
      `Training Samples: 0`, `CPCV DSR: 0.0`, `PBO: 1.0`, `NOT DEPLOYABLE`
- [x] Root-cause investigation
  - [x] Grep `options_meta_labeler` across `scripts/` and `ml/` — no automated CPCV training
        path exists for this model
  - [x] `git log -p -- ml/registry.yaml` — traced the entry to hand-authored commit `693f3717`
  - [x] Audit `ml/registry_io.py` (`update_model_metrics`, `compute_deployable`) — already honest
  - [x] Audit `validation/metrics.py` (`deflated_sharpe_ratio`, `probability_of_backtest_overfitting`)
        and `scripts/train_meta_labelers.py::compute_cpcv_metrics` — all already return
        `None`/`NaN` on degenerate/empty input, no computation bug found
  - [x] Read `investyo_mcp_server.py::get_model_registry_status` rendering logic
- [x] Fix `ml/registry.yaml`: `options_meta_labeler.cpcv_dsr`/`pbo` → `null`, notes updated
- [x] Fix `investyo_mcp_server.py::get_model_registry_status`: render `NOT EVALUATED` +
      reason string when `cpcv_dsr`/`pbo` is `None`, distinct from a genuine `NOT DEPLOYABLE`
- [x] Add regression tests to `tests/test_investyo_mcp_server.py::TestGetModelRegistryStatus`
  - [x] `test_zero_sample_model_not_evaluated_not_fabricated`
  - [x] `test_evaluated_and_failed_keeps_numeric_metrics` (real numbers unaffected)
- [x] `.venv/bin/python3 -m pytest tests/test_investyo_mcp_server.py -k TestGetModelRegistryStatus -q`
      — 8 passed
- [x] `.venv/bin/python3 -m pytest tests/test_pbo.py tests/test_dsr.py tests/test_registry_load.py -q`
      — 29 passed
- [x] Commit to `fix-zero-sample-pbo-dsr`
- [x] `git fetch origin && git rebase origin/main` — clean, no conflicts, diff unchanged
- [x] Re-run both test commands post-rebase — still green (8 + 29 passed)
- [x] Sync machine-local runtime mirror `~/.stockpy_local/ml_models/registry.yaml`
      (`options_meta_labeler.cpcv_dsr`/`pbo` → `null`, direct file edit, not a git commit)
- [x] Live-verify `get_model_registry_status()` output end-to-end against both the repo file
      and the synced local mirror — confirmed `NOT EVALUATED (not evaluated — 0 training
      samples)`, no fabricated `0.0`/`1.0` anywhere in the output
- [x] `git push -u origin fix-zero-sample-pbo-dsr`
- [ ] Add PR artifacts (this implementation plan, task tracker, walkthrough) to `.claude/`,
      commit, push
- [ ] `gh pr create --base main` (never merge — leave for orchestrating session)
- [ ] Poll CI to green or genuine failure
