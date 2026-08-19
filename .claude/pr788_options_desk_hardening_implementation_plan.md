# Implementation Plan: PR #788 Options-Desk Hardening — Rebase, Bug Fixes & Doc Corrections

## Background

This branch (`phases-26-to-30`, PR #788) originally shipped 13 commits carrying 27
`.claude/phase_*.md` planning/task/walkthrough files that described "building out" the
multi-broker gateway, FIX 4.4 engine, DRL market maker, transformer forecaster, and diffusion
stress engine. All five of those modules already existed on `main` days before this branch was
cut (see git history: `execution/fix_gateway.py`, `ml/drl_market_maker.py`,
`execution/multi_broker_gateway.py`, `ml/transformer_vol_forecaster.py`,
`validation/synthetic_diffusion_engine.py`, `broker_live_execution_mcp.py`). The branch's real
diff against its own merge-base was 45 files / 1225 insertions, of which ~527 lines were those
now-inaccurate planning docs. This plan supersedes them with one accurately-scoped set of
artifacts and closes the real defects an audit surfaced.

The branch was also 36 commits behind `main` (`mergeable: CONFLICTING`), missing load-bearing
work from #790, #791, #793, #798, and others.

## Scope of this pass

1. **Rebase `phases-26-to-30` onto current `main`** and resolve the 14 conflicting files.
   - `pilots/options_risk.py` / `tests/test_options_risk.py`: `main` had *independently* fixed
     the same beta-weighting gap this PR touched, with a real implementation
     (`pilots/rolling_beta.py` → `data/fmp_fundamentals.py::compute_beta` fallback). This PR's
     own version called a nonexistent `HistoricalStore.get_symbol_beta()` inside a bare
     `except Exception: pass`, silently defaulting every symbol's beta to `1.0` forever —
     verified by direct execution. Resolution: keep `main`'s working implementation; also fixed
     a pre-existing, unrelated bug on `main` itself in the same function — two definitions of
     `calculate_portfolio_greeks` (the first orphaned/dead, shadowed by the second) — while
     already rewriting this section.
   - `desktop/daemon_runtime.py`: both branches independently wired `manage_0dte_exits()` into
     the daemon (main into `_timer_loop`, this PR into `_run_one_cycle`), which would have
     double-fired the check every interval tick post-merge. Resolution: keep `main`'s
     `_timer_loop` wiring (fires more frequently, doesn't depend on cycle success); drop the
     redundant `_run_one_cycle` call.
   - `docs/VALIDATION_STRATEGY_FIX_LOG.md`: both sides independently appended distinct new
     changelog entries; kept both, in order, and corrected a bounds inaccuracy in this PR's
     entry (stated (γ,κ) ∈ [0.01,10.0]×[0.1,20.0]; actual `train_market_maker_policy` defaults
     are [0.01,1.0]×[0.5,5.0]).
   - Six files (`broker_live_execution_mcp.py`, `execution/almgren_chriss_router.py`,
     `ml/transformer_vol_forecaster.py`, `numba_backtest_loop.py`,
     `sizing/hrp_cvar_optimizer.py`, `validation/synthetic_diffusion_engine.py`): both sides
     independently added a module docstring header; kept `main`'s (already-tested, in the
     established repo convention), dropped this PR's redundant duplicate.
   - `api/pilots_api.py`, `tests/test_dispersion_trading.py`: auto-merged cleanly, verified.

2. **`USE_MULTI_BROKER_GATEWAY` env-var bug**: `broker_live_execution_mcp.py::_get_broker()` read
   this flag via bare `os.environ.get(...)`, and it was never declared as a `settings.py` field —
   reproducing this codebase's own documented bug class (pydantic-settings' `.env` loading does
   not populate the real process `os.environ`; see CLAUDE.md's Finnhub/EDGAR/Reddit/
   robinhood_portfolio incidents). An operator setting this in `.env` would silently never
   activate `MultiBrokerGateway` routing. Fixed: added `settings.MULTI_BROKER_GATEWAY_ENABLED`
   (bool, default `False` — this changes broker execution routing, so it stays opt-in per
   CLAUDE.md's "flags that change trading behavior default False" carve-out), read via
   `settings.settings.X`; classified as a non-secret `ALLOWED_KEY` in `gui/env_io.py`.

3. **Stale "never imported" claims**: `train_market_maker_policy`'s "never imported by
   `api/pilots_api.py` or any webapp component" note (in `ml/drl_market_maker.py`'s own honest
   docstring per the earlier audit, and mirrored in `CLAUDE.md`/`AGENTS.md`/
   `docs/architecture/ml-and-reports.md`) is contradicted by this PR's own new
   `POST /pilots/options/market-maker/train` endpoint. Corrected all three doc locations to
   state the endpoint now exists but remains backend-only (no webapp caller, no dedicated test).

4. **`train_market_maker_policy`'s `converged` field was a hardcoded `True`**, invisible until
   this PR's endpoint made it live-reachable. Replaced with a real plateau-based signal (best
   score hasn't improved for the trailing 20% of episodes, minimum 10) and added
   `tests/test_drl_market_maker.py::test_train_market_maker_policy_convergence_signal` covering
   both a converging and a too-short (non-converging) run.

5. **`dispatch_risk_limit_alert`'s `force` parameter** was declared/documented ("dispatches
   regardless of evaluation") but never referenced in the function body — unlike its sibling
   `dispatch_*_alert` functions, which have a real qualifying-threshold gate for `force` to
   bypass. Corrected the docstring to state honestly that `force` is unused here (a risk-limit
   breach always dispatches once `breach` is non-`None`; there is no threshold to override).

6. **Replaced the 27 misleading `.claude/phase_*.md` files** (this document's own predecessors)
   with this single, accurately-scoped implementation plan / task tracker / walkthrough set.

## Documentation-update step

- `CLAUDE.md` / `AGENTS.md`: corrected the stale "never imported" claim for
  `train_market_maker_policy` (auto-synced by `.claude/hooks/sync_agent_docs.sh` on the next
  commit that touches either file — both were hand-edited identically here to be safe).
- `docs/architecture/ml-and-reports.md`: same correction, plus notes the endpoint is
  backend-only/untested end-to-end.
- `docs/VALIDATION_STRATEGY_FIX_LOG.md`: bounds correction (see item 1 above).
- `docs/settings_field_census.json` / `.md`, `docs/settings_liveness.json`: regenerated via
  `scripts/measure_settings_census.py --write` and `scripts/settings_liveness.py --write` after
  adding `MULTI_BROKER_GATEWAY_ENABLED`.

## Verification plan

1. `pytest -m "not network and not slow" -q -p no:randomly` (full offline suite).
2. `ruff check . --select=F821,F822,F823,E9` (this repo's CI lint gate).
3. Targeted: `pytest tests/test_options_risk.py tests/test_dispersion_trading.py
   tests/test_options_alerts.py tests/test_transformer_vol_forecaster.py
   tests/test_drl_market_maker.py tests/test_daemon_runtime.py tests/test_run_once.py
   tests/test_zero_dte_engine.py tests/test_broker_live_execution_mcp.py
   tests/test_gui_env_io.py tests/test_measure_settings_census.py
   tests/test_settings_liveness.py -q`
