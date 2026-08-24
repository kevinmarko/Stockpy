# Walkthrough: 4 audit-finding fixes (options Greeks, cost model, AC router, stress threshold)

Branch: `fix-options-cost-model-audit-findings`

## What changed

Four independent, small fixes, each in its own commit, addressing findings from two audits
run this session (options-Greeks, execution-cost-model). None were live-reachable bugs today
— no current caller trips any of them — but each closed a real latent risk.

### 1. `pilots/options_risk.py` — 0DTE Greeks dict-shape parity

`calculate_black_scholes_greeks`'s 0DTE branch (`t_years <= 1e-12`) was missing
`rho`/`rho_1pct`/`rho_raw`, while the degenerate-sigma branch and the main computation branch
both included them. Added the three keys with `rho=0.0` (textbook-correct for an expiring
option). New test `test_black_scholes_greeks_return_shape_consistent_across_branches` asserts
all three return branches share an identical key set.

### 2. `simulation_engine.py` + `investyo_mcp_server.py` — real `market_cap` threading

`optimize_strategy_vectorbt()` and `run_backtrader_simulation()` hardcoded `market_cap=None`
at their `TieredCostModel` call sites, so `get_liquidity_tier(None)` always resolved
`"large_cap"` regardless of the actual symbol — the liquidity-tier cost differentiation was
defined but never exercised. Both functions now accept an optional `market_cap` parameter
(default `None` — byte-identical for any caller not yet passing one).

Wired the one real production caller, `investyo_mcp_server.py::run_backtest(symbol, period)`,
to resolve a real market cap via `get_provider().get_fundamentals(symbol)["marketCap"]` before
calling `run_backtrader_simulation`. Any resolution failure (typed `MarketDataError`, a bad
provider response, a non-finite/non-positive value) degrades to `market_cap=None` — never a
fabricated `0.0` (CONSTRAINT #4), never crashes the backtest (CONSTRAINT #6).

`pairs/simulation.py`'s own separate `market_cap=None` call site was left untouched — a
two-symbol pairs backtest has no single "the" market cap without a larger design decision that
wasn't asked for here.

New/updated tests: `tests/test_simulation_engine.py` (spy-based threading tests for both
entry points, using a `bt.CommInfoBase` subclass reading `self.p.market_cap` post-`__init__`
rather than intercepting constructor kwargs directly — backtrader's Params metaclass resolves
kwargs into `self.p` in a way that reading raw `**kwargs` before `super().__init__()` doesn't
reliably reflect), `tests/test_investyo_mcp_server.py::TestRunBacktest` (two new tests: valid
resolution + threading, and graceful degradation to `None` on fetch failure). One pre-existing
test (`test_happy_path_delegates_to_simulation_engine`) needed its `_fake_sim` stub updated to
accept the new `market_cap` kwarg.

### 3. `execution/almgren_chriss_router.py` — Almgren-Chriss well-posedness guard

`compute_trading_trajectory` had no guard for the AC(2001) well-posedness condition
(`eta_tilde = temp_impact - 0.5*perm_impact*tau > 0`). Added an explicit `ValueError` check
right after `tau` is computed, alongside the module's existing five validation checks. Verified
against every existing test case and `api/pilots_api.py`'s fixed params (`temp_impact=0.1,
perm_impact=0.01`) that nothing currently trips it — purely additive.

New tests: `test_degenerate_cost_parameterization_raises` (below-threshold and exactly-at-the-
boundary cases) and `test_well_posed_cost_parameterization_just_above_threshold_does_not_raise`
(guards against over-triggering).

### 4. `validation/stress_scenarios.py` — import instead of duplicate threshold

Replaced the local `MAX_STRESS_DRAWDOWN: float = 0.50` literal with a real import of
`STRESS_MAX_DRAWDOWN` from `validation/thresholds.py` (the file that already documents itself
as owning this value). `MAX_STRESS_DRAWDOWN` stays a valid public re-export — confirmed
imported externally by `Gravity AI Review Suite.py` and `tests/test_stress_gate.py`.

New test `test_max_stress_drawdown_is_sourced_from_thresholds_module` locks in the fix.

## Documentation

Added one consolidated bullet to `CLAUDE.md`'s changelog covering all four fixes;
`.claude/hooks/sync_agent_docs.sh` auto-mirrored it to `AGENTS.md` on save (confirmed via
`git diff AGENTS.md`). No `docs/architecture/*.md`/`docs/signals/*.md` edits were needed —
existing entries for these modules were already accurate and unaffected by these internal
robustness fixes.

## Verification

```bash
python3 -m pytest tests/test_options_risk.py tests/test_simulation_engine.py \
    tests/test_almgren_chriss_router.py tests/test_stress_gate.py \
    tests/test_investyo_mcp_server.py -q -p no:randomly
```

All pass (see individual commit messages for per-file pass counts). Full targeted verification
(`make verify` / `/verify`) to be run before merge per CLAUDE.md's mandatory-verification rule.
