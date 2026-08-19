# Task Tracker: PR #788 Options-Desk Hardening — Rebase, Bug Fixes & Doc Corrections

## Status Overview
- **Implementation Status**: Complete
- **Verification Status**: Full offline suite green except 5 pre-existing failures confirmed
  unrelated to this branch (verified against unmodified `origin/main`:
  `tests/test_data_api_chat.py::TestMultiProviderRouting` ×3,
  `tests/test_gemini_live_chat.py::TestLiveChatSession` ×2 — environment-dependent, not caused
  by this PR).

## Checklist

- [x] Rebase `phases-26-to-30` onto current `main` (36 commits caught up); resolve all 14
      conflicting files.
- [x] Fix `pilots/options_risk.py::_resolve_symbol_beta` — replaced this PR's call to a
      nonexistent `HistoricalStore.get_symbol_beta()` (silently defaulted every beta to `1.0`)
      with `main`'s already-correct `rolling_beta_view`/`compute_beta` implementation; also fixed
      a pre-existing dead-code duplicate `calculate_portfolio_greeks` definition found on `main`
      in the same section.
- [x] Deduplicate 0DTE exit-management wiring in `desktop/daemon_runtime.py` (kept `main`'s
      `_timer_loop` call, dropped this PR's redundant `_run_one_cycle` call).
- [x] Fix `docs/VALIDATION_STRATEGY_FIX_LOG.md` merge (kept both independently-added entries) and
      its (γ,κ) bounds inaccuracy.
- [x] Fix `USE_MULTI_BROKER_GATEWAY` — added `settings.MULTI_BROKER_GATEWAY_ENABLED` (default
      `False`), read via `settings.settings.X`, classified in `gui/env_io.py::ALLOWED_KEYS`.
- [x] Correct the stale "never imported by `api/pilots_api.py`" claim for
      `train_market_maker_policy` in `CLAUDE.md`, `AGENTS.md`, `docs/architecture/ml-and-reports.md`.
- [x] Replace hardcoded `converged=True` in `ml/drl_market_maker.py::train_market_maker_policy`
      with a real plateau-based signal; added
      `test_train_market_maker_policy_convergence_signal` (converging + non-converging cases).
- [x] Correct `dispatch_risk_limit_alert`'s misleading `force` docstring.
- [x] Delete the 27 misleading `.claude/phase_*.md` files and replace with this scoped set.
- [x] Regenerate `docs/settings_field_census.json`/`.md` and `docs/settings_liveness.json`.
- [x] Full offline test suite: `pytest -m "not network and not slow" -q -p no:randomly` — 11411
      passed, 31 skipped, 92 deselected, 5 failed (all 5 pre-existing on `main`, confirmed).
- [x] `ruff check . --select=F821,F822,F823,E9` on all touched files — clean.
- [x] Targeted suites (options_risk, dispersion_trading, options_alerts,
      transformer_vol_forecaster, drl_market_maker, daemon_runtime, run_once, zero_dte_engine,
      broker_live_execution_mcp, gui_env_io, measure_settings_census, settings_liveness) — all
      green.
