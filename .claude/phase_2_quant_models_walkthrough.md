# Walkthrough: Phase 2 — Quantitative Models, Optimization & Anti-Fabrication

## Overview & Accomplishments

Phase 2 has been built out in the new worktree (`/Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/phase-2-quant-models`) on branch `phase-2-quant-models`.

## Code-review fixes (2026-08-17)

An independent code review of the new `POST /pilots/options/market-maker/train` endpoint (task 2
below) found four real issues, all fixed:

1. **No cross-validation that `gamma_min <= gamma_max` / `kappa_min <= kappa_max`.** Each bound
   was independently validated with `Field(gt=0.0)`, but nothing checked their relative order, and
   `np.clip(value, a_min, a_max)` does not raise when `a_min > a_max` — it silently returns
   `a_max`, collapsing the search to a degenerate fixed value with a `200` response and no
   indication the request was malformed. **Fixed**: added a pydantic `@model_validator(mode="after")`
   on `MarketMakerTrainRequest` that raises (→ HTTP 422) on an inverted pair.
2. **`seed=body.seed or 42` silently discarded an explicit `seed=0`** — the classic Python
   falsy-zero bug (`0 or 42` evaluates to `42`), so a caller requesting a specific, legitimate,
   reproducible seed of `0` silently got `42` instead. **Fixed**: `seed=body.seed if body.seed is
   not None else 42`.
3. **The endpoint trains only on synthetic data with no disclosure.** `MarketMakerTrainRequest` has
   no field for real price history, so `train_market_maker_policy(env=None, ...)` always falls
   through to `MarketMakingEnv`'s default synthetic random-walk price path — never real market
   microstructure. The response (`best_sharpe`, `best_pnl`, ...) reads like a genuine backtest
   result with nothing to indicate otherwise, unlike the sibling `/simulate` endpoint whose name at
   least signals synthetic output. **Fixed**: documented plainly in the endpoint's docstring, and
   the response now includes `"data_source": "synthetic"` so a caller/consumer can't mistake it for
   a real backtest.
4. **Dead-code manual dict-construction fallback.** `train_market_maker_policy` always returns a
   `PolicyOptimizationResult`, a plain dataclass that unconditionally defines `to_dict()` — so the
   endpoint's `else: {...9-line manual dict...}` branch could never execute, silently duplicating
   `to_dict()`'s field list with no way to catch drift. **Fixed**: simplified to
   `res.to_dict() if hasattr(res, "to_dict") else dict(res)`, matching every sibling endpoint in
   this file (`post_market_maker_simulate`, `post_copula_pairs`, ...).

Also corrected `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s stated $(\gamma,\kappa)$ search-grid bounds,
which matched neither `train_market_maker_policy`'s actual defaults, the endpoint's own Pydantic
defaults, nor `tests/test_drl_market_maker.py`'s range — the number had apparently never been
checked against any of the three.

### Key Changes
1. **CVaR 95% Calculation & HRP Optimization**:
   - Verified that `api/pilots_api.py` computes true empirical conditional value at risk via `calculate_cvar(w_aligned, returns_np, alpha=0.05)` on the optimized portfolio weights rather than returning a static placeholder.
2. **Avellaneda-Stoikov Market Maker Policy Optimization Endpoint**:
   - Exposed `train_market_maker_policy` via `POST /pilots/options/market-maker/train` in `api/pilots_api.py` with `MarketMakerTrainRequest` validation schema (see code-review fixes above for the bound-ordering validator and synthetic-data disclosure added 2026-08-17).
   - Documented the institutional high-frequency market maker validation exemption and microstructure evaluation metrics (spread capture, inventory variance, adverse selection) in `docs/VALIDATION_STRATEGY_FIX_LOG.md` (search-grid numbers corrected 2026-08-17, see above).
   - Added `TestMarketMakerTrainEndpoint` to `tests/test_pilots_paper_broker.py` (alongside the existing sibling `TestMarketMakerSimulateEndpoint`) covering the happy path + synthetic-data disclosure, both inverted-bound rejections, the `seed=0` regression, and the auth fail-open/fail-closed contract.
3. **Exact Mathematical Reference Tests**:
   - Added `test_black_scholes_greeks_exact_analytical_reference` to `tests/test_options_risk.py` verifying Delta, Gamma, Theta, Vega, and Rho against exact hand-computed closed-form reference values.
   - Added `test_driessen_maenhout_vilkov_implied_correlation_exact_multi_asset` to `tests/test_dispersion_trading.py` validating implied correlation calculation on multi-asset asymmetric baskets.

---

## Verification Results

| Suite / Gate | Test Scope | Result |
|---|---|---|
| **Quantitative Models Tests** | `test_options_risk.py`, `test_dispersion_trading.py`, `test_drl_market_maker.py`, `test_hrp_cvar_optimizer.py` | ✅ **63/63 Passed** (originally) |
| **Market Maker Train Endpoint** | `tests/test_pilots_paper_broker.py -k MarketMakerTrain` (new, 2026-08-17) | ✅ **6/6 Passed** |
| **Pilots API Integration** | `test_pilots_api.py` | ✅ **391/391 Passed** |
| **TypeScript Typecheck** | `webapp/` (`tsc --noEmit`) | ✅ **0 Errors** |
| **Bandit SAST Scan** | Full repository security scan (148,836 LOC) | ✅ **0 High / 0 Medium** (as originally reported; not independently re-run as part of the 2026-08-17 fixes) |
