# Walkthrough: PR #788 Options-Desk Hardening — Rebase, Bug Fixes & Doc Corrections

## What this PR actually is

The original 13 commits on this branch described themselves as "completing the build-out" of
Phases 1 through 30 — the multi-broker gateway, FIX 4.4 engine, DRL market maker, transformer
volatility forecaster, and diffusion stress engine. All five of those subsystems already existed
on `main`, built in separate commits days before this branch was cut. This branch's real
contribution was always a small set of hardening patches (Pydantic validation bounds, a causal
attention-masking fix, a WebSocket backoff fix, a beta-weighting attempt, a new training
endpoint) plus 27 `.claude/phase_*.md` files that retroactively narrated pre-existing work as new.

This pass (1) rebases the branch onto current `main` so it can actually merge, (2) fixes real
defects an audit surfaced in the branch's own changes, and (3) replaces the misleading planning
docs with this accurately-scoped set.

## Rebase & conflict resolution

`phases-26-to-30` was 36 commits behind `main` (`mergeable: CONFLICTING`), missing #790 (F1-F16
audit remediation), #791 (HMM regime refinement), #793 (CPCV/PBO/DSR fix), #798
(vol_mispricing live-execute endpoint), and more. `git rebase origin/main` surfaced 14 conflicting
files; each was resolved by inspecting both sides rather than blindly taking one:

- **`pilots/options_risk.py`**: the most consequential conflict. `main` had *independently*
  fixed the exact same beta-weighting gap this branch touched, but with a real implementation —
  `pilots/rolling_beta.py::rolling_beta_view` (primary) falling back to
  `data/fmp_fundamentals.py::compute_beta`. This branch's own version called
  `HistoricalStore().get_symbol_beta(...)`, a method that doesn't exist anywhere in the
  codebase; confirmed by direct execution that the bare `except Exception: pass` swallows the
  resulting `AttributeError` and silently returns `1.0` for every symbol, every time — making the
  whole point of the fix (real per-symbol beta feeding `beta_weighted_delta_spy`) a no-op
  indistinguishable from the pre-fix behavior. Kept `main`'s working version. While rewriting
  this section, also found and fixed an unrelated, pre-existing bug on `main` itself: two
  definitions of `calculate_portfolio_greeks` in the same file, the first orphaned and shadowed
  by the second (dead code, not a runtime bug, but confusing and worth cleaning up since the
  section was already being rewritten).
- **`desktop/daemon_runtime.py`**: `main` had already wired `manage_0dte_exits()` into
  `_timer_loop` (fires on every interval wake, doesn't depend on cycle success). This branch
  independently added a second call inside `_run_one_cycle` (fires once per completed cycle).
  Merged naively, every daemon interval tick would have called `manage_0dte_exits()` twice.
  Kept `main`'s `_timer_loop` version (more frequent, more reliable trigger for a
  time-sensitive 15:45 ET liquidation check) and removed the redundant `_run_one_cycle` call,
  leaving a comment explaining why.
- **`docs/VALIDATION_STRATEGY_FIX_LOG.md`**: both sides appended distinct, non-contradictory
  changelog entries; kept both in order. Also corrected an inaccuracy in this branch's own new
  entry — it stated `(γ,κ) ∈ [0.01,10.0]×[0.1,20.0]`, but `train_market_maker_policy`'s actual
  default bounds are `[0.01,1.0]×[0.5,5.0]`.
- **Six docstring-only files** (`broker_live_execution_mcp.py`,
  `execution/almgren_chriss_router.py`, `ml/transformer_vol_forecaster.py`,
  `numba_backtest_loop.py`, `sizing/hrp_cvar_optimizer.py`,
  `validation/synthetic_diffusion_engine.py`): both sides had independently added a module
  docstring header to the same files. Kept `main`'s, dropped the duplicate.
- `api/pilots_api.py` and `tests/test_dispersion_trading.py` auto-merged cleanly; verified by
  reading the merged result (the new `POST /pilots/options/market-maker/train` endpoint and its
  Pydantic bound-hardening survived intact).

## Real defects fixed

1. **`USE_MULTI_BROKER_GATEWAY` was a dead flag.** `broker_live_execution_mcp.py::_get_broker()`
   read it via bare `os.environ.get(...)`, and no `settings.py` field existed for it at all —
   reproducing this codebase's own documented bug class (a `.env`-only value never reaches the
   real process `os.environ`; see CLAUDE.md's Finnhub/EDGAR/Reddit/robinhood_portfolio write-ups).
   Added `settings.MULTI_BROKER_GATEWAY_ENABLED` (default `False` — this routes live order
   execution, so it stays opt-in per CLAUDE.md's "flags that change trading behavior default
   False" rule), switched the read site to `settings.settings.X`, and classified it as a
   non-secret `ALLOWED_KEY` in `gui/env_io.py` (required by
   `tests/test_gui_env_io.py::test_every_settings_field_is_classified`).
2. **Stale "never imported" documentation.** `ml/drl_market_maker.py`'s own honest-status
   docstring, plus `CLAUDE.md`, `AGENTS.md`, and `docs/architecture/ml-and-reports.md`, all
   stated `train_market_maker_policy` is "never imported by `api/pilots_api.py` or any webapp
   component" — a claim this PR's own new endpoint (`POST /pilots/options/market-maker/train`)
   contradicts. Corrected all three doc locations: the endpoint now exists and calls it, but it
   remains backend-only (no webapp screen calls it, no dedicated test references it).
3. **`converged` was hardcoded `True`.** `PolicyOptimizationResult.converged` defaulted to and
   was always set to `True` regardless of whether the hill-climb search actually plateaued —
   harmless while the field was dead code, but this PR is precisely what makes it live-reachable
   via the new API endpoint. Replaced with a real check: converged only if the best score hasn't
   improved for the trailing 20% of episodes (minimum 10), and at least that many episodes ran.
   Added `test_train_market_maker_policy_convergence_signal`, which exercises both a run that
   plateaus (`converged is True`) and one too short to demonstrate a plateau
   (`converged is False`) — replacing the old test's blanket `assert opt_result.converged is
   True`, which was only ever trivially true because the field was hardcoded.
4. **`dispatch_risk_limit_alert`'s `force` parameter was documented dishonestly.** Its docstring
   claimed "dispatches regardless of evaluation," matching its sibling `dispatch_*_alert`
   functions' pattern — but unlike those siblings, this function has no qualifying threshold at
   all (a `breach` argument means the caller already determined the limit was exceeded), so
   there was nothing for `force` to override. Corrected the docstring rather than remove the
   parameter (kept for signature symmetry with its siblings and forward compatibility).

## Documentation replaced

Deleted the 27 `.claude/phase_1_execution_safety_*.md` through
`.claude/phases_26_to_30_*.md` files and replaced them with this one accurately-scoped
implementation-plan/task/walkthrough set, per this repo's PR-artifact naming convention.

## Verification

- `pytest -m "not network and not slow" -q -p no:randomly`: 11411 passed, 31 skipped, 92
  deselected, 5 failed — all 5 failures (`test_data_api_chat.py::TestMultiProviderRouting` ×3,
  `test_gemini_live_chat.py::TestLiveChatSession` ×2) independently reproduced against an
  unmodified `origin/main` checkout, confirming they predate and are unrelated to this branch.
- `ruff check . --select=F821,F822,F823,E9` (this repo's actual CI lint gate, not the full
  ~1200-violation default ruleset) — clean on every touched file.
- Targeted suites for every touched module — all green, including two new tests
  (`test_train_market_maker_policy_convergence_signal` and the retained/corrected beta tests in
  `tests/test_options_risk.py`).
- `docs/settings_field_census.json`/`.md` and `docs/settings_liveness.json` regenerated to
  include the new `MULTI_BROKER_GATEWAY_ENABLED` field (`scripts/measure_settings_census.py
  --write`, `scripts/settings_liveness.py --write`).
