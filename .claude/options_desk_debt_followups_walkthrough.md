# Walkthrough: Options Desk Technical Debt Follow-Up Fixes

## Why this branch exists

PR #749 claimed "100% Green across all 8 audit domains" for four options-desk fixes. A
post-merge independent re-audit (4 parallel review agents, each given the real merged diff plus
the actual current source and told to run the real test suite, not just trust the diff/docstrings)
found that two of the four claimed fixes shipped with real bugs. This branch fixes those two.

## What was found and fixed

### 1. Earnings-crush alert could fire on fabricated data

**Before**: `pilots/earnings_crush.py`'s dispatch gate was
`if cand.get("is_recommended") or float(cand.get("crush_edge_ratio", 0.0)) >= 1.35:`.
`is_recommended` is set `False` specifically when the historical realized-move data is
synthetic/fallback (< 3 real quarters of history), per the codebase's CONSTRAINT #4 ("never
recommend a trade on synthetic fallback data"). But `crush_edge_ratio` is computed the same way
regardless of the fallback flag — so a fallback-data candidate could still clear the 1.35x
`crush_edge_ratio` threshold on its own, and the `or` let the alert dispatch anyway, directly
undermining the adjacent CONSTRAINT #4 comment in the same function.

**After**: gate on `is_recommended` alone. Added
`test_dispatch_alert_not_fired_on_fallback_data_despite_high_edge_ratio`, which reproduces the
exact scenario (fallback data, `crush_edge_ratio >= 1.35`) and asserts the dispatcher mock is
never called; plus `test_dispatch_alert_fires_for_recommended_candidate` confirming the happy
path still works.

### 2. Delta hedge alert never actually fired from the automated pipeline

**Before**: `pilots/options_hedging.py::execute_delta_hedge()` — the call site `main.py`'s
unattended pipeline cycle reaches when `OPTIONS_DELTA_HEDGE_ENABLED=True` — dispatched with
`dispatch_delta_hedge_alert(order or {...})`, where `order` is the raw dict from
`calculate_delta_hedge_order` (`symbol`/`side`/`qty`/`order_type`/`target_delta`/
`current_beta_weighted_delta`/`shares_needed`/`spy_spot`). `dispatch_delta_hedge_alert`'s own
qualifying gate reads `action`, `shares`, and `required_action` — none of which exist on `order`
— so they always defaulted (`action="HOLD"`, `required_action=False`) and
`qualifies = force or (required_action and action in ("BUY","SELL") and abs(shares) > 0)` was
always `False`. The dispatcher was genuinely being called (so a naive `mock.assert_called_once()`
test would have passed), but it always silently declined to alert — the one path meant to fire
unattended never did.

**After**: build the same preview-shaped dict `get_delta_hedge_preview()` already constructs, from
values already resolved in `execute_delta_hedge()` at the point the fill has succeeded
(`required_action=True` is correct there — a fill already happened). Added
`test_execute_delta_hedge_dispatches_alert_with_qualifying_shape`, which asserts on the actual
dict passed to the dispatcher mock (`action == "SELL"`, `shares == 100.0`,
`required_action is True`, etc. — the exact fields the real gate reads), plus
`test_execute_delta_hedge_no_alert_within_tolerance` confirming no dispatch call at all for a
no-op hedge.

### 3. Copula per-bar gate silently dropped the half-life criterion

**Before**: PR #749 made `generate_copula_stat_arb_signals`'s per-bar copula tail-dependence
check causal (a real, verified fix, with a solid perturbation test). But the pre-#749 gate had
always checked TWO criteria together — copula lower-tail dependence AND the OU half-life
mean-reversion bound (`5 <= half_life <= 60` days) — and the rewrite only restored the
tail-dependence half per-bar; half-life was evaluated only at the final bar via the full-sample
summary. For the rest of the backtest history, a position could open purely on passing
tail-dependence, even against a spread with no real mean reversion.

**After**: the same per-bar loop now also computes a causal rolling OU half-life estimate
(`estimate_ou_half_life` on the identical trailing window used for the copula refit, same
`copula_cache_interval` cadence) and requires both `step_copula_ok` AND `step_half_life_ok`.
Added `test_generate_copula_stat_arb_signals_requires_causal_half_life_not_only_copula`, which
monkeypatches `fit_best_copula` (forced passing) and `estimate_ou_half_life` (forced `inf`)
independently and asserts no position ever opens despite a large z-score dislocation — with a
manually-verified counterfactual (an in-bounds half-life under otherwise identical conditions
DOES open a position) confirming the test genuinely exercises the half-life half of the gate, not
just a vacuous pass.

## What was NOT touched (confirmed correct by the re-audit, no action needed)

- `dispatch_uoa_whale_alert` wiring in `pilots/unusual_options_flow.py` — correctly gated,
  correctly non-blocking.
- The UOA feed frontend contract fix (`webapp/src/components/options/UnusualFlowFeed.tsx` +
  `api/pilots_api.py`'s dual `records`/`trades` response) — verified against the real backend
  field casing, genuinely fixed. (Two adjacent, out-of-scope gaps were flagged for a possible
  future follow-up: `webapp/src/api/mock.ts`'s UOA fixture still uses idealized casing rather
  than mirroring live, and the Flow Sentiment Gauge's `top_active_strikes` pills — a different
  endpoint, same component file — render `NaN`/`undefined` against live data.)
- The Black-Scholes/Greeks consolidation across `options_sor.py`, `vol_mispricing.py`,
  `dispersion_trading.py`, `gamma_scalper.py`, `volatility_surface.py` into
  `pilots/options_risk.py` — verified numerically faithful (units, signs, 0DTE/degenerate-vol
  guards all preserved) by manually diffing the moved algebra.
- `evaluate_copula_stat_arb_pair`'s `latest_beta` scalar leak — genuinely and cleanly fixed by
  PR #749, verified by a real non-tautological perturbation test.

## Verification

```
pytest tests/test_options_alerts.py tests/test_unusual_options_flow.py tests/test_earnings_crush.py \
       tests/test_options_hedging.py tests/test_copula_stat_arb.py tests/test_options_sor.py \
       tests/test_vol_mispricing.py tests/test_dispersion_trading.py tests/test_gamma_scalper.py \
       tests/test_volatility_surface.py tests/test_options_risk.py tests/test_options_matrix.py \
       tests/test_options_selling_backtest_stress.py -q
```
→ 272 passed (267 pre-existing + 5 new), 0 failed.
