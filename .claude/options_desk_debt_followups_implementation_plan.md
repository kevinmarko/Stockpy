# Implementation Plan: Options Desk Technical Debt Follow-Up Fixes

## Context

PR #749 ("fix(options-desk): resolve flagged technical debt and audit findings") merged into
`main` claiming 100% Green across 8 audit domains and four resolved items: alert dispatcher
wiring, the UOA feed frontend contract, copula stat-arb lookahead-bias fixes, and a
Black-Scholes/Greeks consolidation. A post-merge independent re-audit (4 parallel review agents,
one per claimed fix, each reading the real merged diff and running the actual test suite) found
two of the four items shipped with real, non-cosmetic bugs despite all 267 targeted tests
passing. This plan fixes those two items.

## Findings from the re-audit

### 1. Alert dispatcher wiring — 2 of 3 dispatchers broken

- `dispatch_uoa_whale_alert` (`pilots/unusual_options_flow.py`) — correctly wired, no fix needed.
- `dispatch_earnings_crush_alert` (`pilots/earnings_crush.py`) — **bug**: dispatch condition was
  `is_recommended or crush_edge_ratio >= 1.35`. `is_recommended` is deliberately forced `False`
  when the realized-move history is synthetic/fallback data (the code's own adjacent comment
  cites "CONSTRAINT #4: Never recommend a trade on synthetic fallback data"), but the `OR`
  branch lets the alert fire anyway purely off `crush_edge_ratio`, which is computed the same
  way regardless of the fallback flag.
- `dispatch_delta_hedge_alert` (`pilots/options_hedging.py`) — **bug**: the `get_delta_hedge_preview()`
  call site is correct, but `execute_delta_hedge()` — the call site `main.py`'s unattended
  automated pipeline cycle actually reaches (`OPTIONS_DELTA_HEDGE_ENABLED` gate) — passed the raw
  `order` dict (`side`/`qty`/`shares_needed`/`current_beta_weighted_delta` from
  `calculate_delta_hedge_order`) straight to `dispatch_delta_hedge_alert`, which reads
  `action`/`shares`/`required_action` for its qualifying gate. None of those keys exist on
  `order`, so they default (`action="HOLD"`, `required_action=False`) and the gate is always
  `False` — this dispatcher silently never fires from the one place it's meant to run unattended.
- Zero test coverage was added for any of the three wirings in the original PR, which is how both
  bugs went undetected despite the full test suite passing.
- `docs/architecture/execution.md` was updated by the original PR to claim all three are
  "actively wired into live evaluation paths," which overclaimed given the above.

### 2. Copula lookahead fix — leak #1 fixed cleanly, leak #2 only half-fixed

- `evaluate_copula_stat_arb_pair`'s full-sample `latest_beta` scalar leak: genuinely fixed
  (uses the causal time-varying `kalman_res.beta` series), verified by a real, non-tautological
  perturbation test. No further action needed.
- `generate_copula_stat_arb_signals`'s full-sample copula fit: the copula lower-tail-dependence
  refit was correctly made causal per-bar (rolling window over strictly trailing data). But the
  original (pre-#749) gate combined TWO criteria on every bar — copula tail-dependence AND the
  OU half-life mean-reversion check (`5 <= half_life <= 60` days) — and PR #749's rewrite only
  restored the tail-dependence half causally; the half-life check was dropped from the per-bar
  gate entirely (only evaluated, via the full-sample `tail_risk_acceptable` summary, at the very
  last bar). This is a real regression: for the entire backtest history except the final bar,
  positions could open in pairs whose spread isn't actually mean-reverting, and the reported
  backtest stats no longer reflect the same rule as the live/current-bar signal. Not disclosed
  in PR #749's diff or docs.

## Proposed changes

### A. `pilots/earnings_crush.py`
Change the dispatch gate in `evaluate_earnings_crush_candidates()` from
`if cand.get("is_recommended") or float(cand.get("crush_edge_ratio", 0.0)) >= 1.35:` to
`if cand.get("is_recommended"):` — `is_recommended` already encodes both the edge threshold and
the fallback-data exclusion; the `or` branch defeats that exclusion.

### B. `pilots/options_hedging.py`
In `execute_delta_hedge()`, replace the `dispatch_delta_hedge_alert(order or {...})` call with a
properly preview-shaped dict — the same shape `get_delta_hedge_preview()` already constructs
(`symbol`/`net_dollar_delta`/`beta_weighted_delta_spy`/`target_hedge_shares`/
`tolerance_band_shares`/`action`/`shares`/`required_action`/`reason`/`spy_spot`) — built from
values already resolved in the function (`portfolio_greeks`, `order`, `side`, `qty`, `fill_price`,
`tolerance_band_shares`). `required_action=True` is correct at this point since a fill has
already been applied to the store.

### C. `pilots/copula_stat_arb.py`
In `generate_copula_stat_arb_signals()`'s per-bar loop, add a causal rolling OU half-life
estimate (`estimate_ou_half_life` on the same trailing `spread_df["spread"]` window already used
for the copula refit, at the same `copula_cache_interval` cadence) and require BOTH
`step_copula_ok` AND `step_half_life_ok` for `step_tail_ok`, restoring the two-criteria gate on
every bar (not just the last).

### D. Documentation
Update `docs/architecture/execution.md`'s `pilots/options_alerts.py` and
`pilots/copula_stat_arb.py` entries to accurately describe the current wiring/gate behavior and
record the fix history (per this repo's CLAUDE.md convention that every Implementation Plan must
scope its own documentation-update step).

### E. Tests
- `tests/test_earnings_crush.py`: two new tests — dispatch fires for a genuine
  `is_recommended=True` candidate; dispatch does NOT fire for a fallback-data candidate whose
  `crush_edge_ratio` alone clears 1.35x (the exact bug scenario).
- `tests/test_options_hedging.py`: two new tests — `execute_delta_hedge` dispatches a
  preview-shaped dict whose `action`/`shares`/`required_action`/etc. actually clear the
  dispatcher's qualifying gate; no dispatch call at all when the hedge is a no-op.
- `tests/test_copula_stat_arb.py`: one new test that monkeypatches `fit_best_copula` (forced
  passing tail-dependence) and `estimate_ou_half_life` (forced `inf`) independently, proving the
  per-bar gate is a genuine AND — with a counterfactual sanity check (verified manually) that an
  in-bounds half-life under otherwise-identical conditions DOES open a position.

## Verification plan

```bash
pytest tests/test_options_alerts.py tests/test_unusual_options_flow.py tests/test_earnings_crush.py \
       tests/test_options_hedging.py tests/test_copula_stat_arb.py tests/test_options_sor.py \
       tests/test_vol_mispricing.py tests/test_dispersion_trading.py tests/test_gamma_scalper.py \
       tests/test_volatility_surface.py tests/test_options_risk.py tests/test_options_matrix.py \
       tests/test_options_selling_backtest_stress.py -q
```
Confirmed: 272 passed (267 pre-existing + 5 new regression tests), zero regressions.
