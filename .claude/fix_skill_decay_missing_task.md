# Task Tracker: Forecast-skill decay_pct fix

- [x] Confirm the gap against real code (`investyo_mcp_server.py
      ::get_model_drift_report` reads `decay_pct`; `pilots/observability.py
      ::forecast_skill_by_symbol_summary` never sets it).
- [x] Repo-wide case-insensitive grep for "decay" — confirmed no prior
      "skill decay" implementation anywhere, including the legacy
      `gui/panels/observability.py` Streamlit panel.
- [x] Read `forecasting/forecast_tracker.py::compute_skill_weights_from_stats`/
      `get_forecast_reliability_curve` and `pilots/observability.py` lines
      550-925 in full to confirm exactly what's available per symbol.
- [x] Implement `_skill_from_pooled_stats` (pure inverse-RMSE helper,
      reusing `forecasting.forecast_tracker._MIN_RMSE`).
- [x] Implement `_forecast_decay_stats_by_symbol` (bulk SQL aggregate,
      recent/baseline half-window split, per-symbol `decay_pct`/`decay_reason`).
- [x] Wire `decay_pct`/`decay_reason` into every row of
      `forecast_skill_by_symbol_summary`.
- [x] Update `get_model_drift_report` to surface `decay_reason` inline
      instead of a bare dash when `decay_pct` is `None`.
- [x] Decide portfolio-wide decay scope — checked `portfolio_forecast_skill`'s
      only consumer, confirmed nothing downstream needs a portfolio-level
      figure; documented the per-symbol-only decision explicitly.
- [x] Add `tests/test_observability_skill_decay.py` — cases (a) recent
      worse → positive decay, (b) recent better → negative decay,
      (c) insufficient history (either half) → `None` + honest reason,
      plus pooling, empty-input, and dead-letter edge cases.
- [x] Update `tests/test_pilots_observability.py`'s one exact-dict
      assertion broken by the new row keys.
- [x] Update `tests/test_investyo_mcp_widgets.py`'s stale
      "decay is never computed" comment/assertion.
- [x] `docs/architecture/observability-and-apis.md` — dated note added.
- [x] `pytest tests/test_observability_skill_decay.py
      tests/test_pilots_observability.py tests/test_investyo_mcp_widgets.py
      tests/test_investyo_mcp_server.py::TestGetModelDriftReport -q` — 166
      passed, 3 pre-existing/unrelated sandbox failures confirmed via
      isolated reruns.
- [x] Commit to `fix-skill-decay-missing`.
- [x] `git fetch origin && git rebase origin/main` (10 unrelated upstream
      commits) — clean rebase, no conflicts.
- [x] Re-run targeted tests post-rebase — still green (96 passed on the
      full touched-plus-new set).
- [x] Add PR artifacts (this file + implementation plan + walkthrough).
- [x] `git push -u origin fix-skill-decay-missing`.
- [x] `gh pr create` — PR opened, URL reported back.
