# Fix 4 audit findings: options Greeks dict-shape, cost-model market_cap threading, AC router guard, stress-threshold duplication

## Context

Two independent audits this session (options-Greeks, execution-cost-model) surfaced four small,
independently-confirmed gaps. None are live-reachable bugs today (all four are latent — no current
caller trips them), but each is a real correctness/consistency risk worth closing before a future
caller does trip them. All four were verified against the current code in this session:

1. `pilots/options_risk.py::calculate_black_scholes_greeks`'s 0DTE branch returns a dict missing
   `rho`/`rho_1pct`/`rho_raw`, while the degenerate-sigma branch and the main computation branch both
   include them — confirmed at lines 95-109 (0DTE) vs 112-129 (degenerate sigma) vs 159-174 (main).
2. `simulation_engine.py` hardcodes `market_cap=None` at both its `TieredCostModel` call sites
   (`get_vbt_costs(market_cap=None)` inside `optimize_strategy_vectorbt`, line 127; and
   `TieredCostCommissionInfo(..., market_cap=None, ...)` inside `run_backtrader_simulation`, line 216)
   — confirmed `TieredCostModel.get_liquidity_tier(None)` always resolves `"large_cap"`
   (`execution/cost_model.py:51-52`), so the liquidity-tier cost differentiation is defined but never
   exercised through this path.
3. `execution/almgren_chriss_router.py::compute_trading_trajectory` has no guard for
   `η̃ = η - ½γτ > 0` (the AC(2001) well-posedness condition) — confirmed no such check exists among
   the five existing `ValueError` guards (lines 39-48). Confirmed via repo-wide grep that no current
   caller (tests, `api/pilots_api.py`) passes a parameterization that would trip it.
4. `validation/stress_scenarios.py` defines its own local `MAX_STRESS_DRAWDOWN: float = 0.50`
   (line 65) instead of importing `STRESS_MAX_DRAWDOWN` from `validation/thresholds.py`, even though
   `thresholds.py`'s own docstring already claims ownership of this value
   ("Applied by `passes_stress_gate()` in `validation/stress_scenarios.py`"). Both are currently
   `0.50` — no live bug — but they are two independent literals, which is exactly the drift risk
   `thresholds.py` exists to prevent.

This touches `pilots/`, `execution/`, and `validation/` — the "everything else" tier per CLAUDE.md
(runtime/trading logic) — so it goes through a feature branch + PR with reviewed plan artifacts,
per the repo's Branch Workflow and Agent Workflow sections.

## Fix 1 — `pilots/options_risk.py` rho key-shape parity

In `calculate_black_scholes_greeks`, the 0DTE branch (`t_years <= _DEGENERATE_THRESHOLD`, current
lines ~95-109) is missing the three rho keys the other two return branches have. Add them with the
textbook-correct value for an expiring option (`rho = 0.0`):

```python
if t_years <= _DEGENERATE_THRESHOLD:
    delta = ...
    return {
        "delta": float(delta),
        "gamma": 0.0,
        "theta_daily": 0.0,
        "theta_annual": 0.0,
        "theta": 0.0,
        "vega_1pct": 0.0,
        "vega": 0.0,
        "vega_raw": 0.0,
        "rho": 0.0,
        "rho_1pct": 0.0,
        "rho_raw": 0.0,
        "price": float(intrinsic),
        "intrinsic": float(intrinsic),
        "extrinsic": 0.0,
    }
```

This makes the 0DTE branch's key set byte-identical to the degenerate-sigma branch's (lines
~112-129), which is already correct.

**Test** (`tests/test_options_risk.py`): extend
`test_calculate_black_scholes_greeks_0dte_fallback` (or add a new test right after it) asserting
`set(g_call_itm.keys())` includes `"rho"`, `"rho_1pct"`, `"rho_raw"` and that each is `0.0` for
all three 0DTE sub-cases already exercised there (ITM call, OTM call, ITM put). Add a dedicated
`test_black_scholes_greeks_return_shape_consistent_across_branches` that calls
`calculate_black_scholes_greeks` once for each of the three branches (0DTE via `t_years=0.0`,
degenerate sigma via `sigma=1e-15`, normal via the existing call/put params from
`test_calculate_black_scholes_greeks_call_and_put`) and asserts all three returned dicts have
identical `set(...keys())` — this is the actual regression guard against the dict-shape drifting
apart again in the future.

## Fix 2 — `simulation_engine.py` real `market_cap` threading

Thread an optional `market_cap: Optional[float] = None` parameter through both entry points so a
caller who has ticker context can supply it, while every existing call site (which has none)
continues to default to `None` (today's exact `"large_cap"` behavior — no behavior change for
anyone not yet passing a value):

- `optimize_strategy_vectorbt(price_series: pd.Series, market_cap: Optional[float] = None)` — pass
  `market_cap=market_cap` into its internal `get_vbt_costs(market_cap=market_cap)` call (replacing
  the hardcoded `market_cap=None` at line 127).
- `run_backtrader_simulation(dataframe: pd.DataFrame, market_cap: Optional[float] = None)` — pass
  `market_cap=market_cap` into `TieredCostCommissionInfo(tiered_model=model, market_cap=market_cap,
  order_type='market')` (replacing the hardcoded `market_cap=None` at line 216).

Wire the one real production caller, `investyo_mcp_server.py::run_backtest(symbol, period)`
(currently calls `run_backtrader_simulation(df)` with no market cap at all, line ~1229): resolve a
real market cap from `data.market_data.get_provider().get_fundamentals(symbol)` before the call,
following the existing `dto_models.py:217` convention (`info.get("marketCap", 0.0)`) but treating a
missing/non-finite/non-positive value as `None` rather than `0.0` (a `0.0` would incorrectly steer
`get_liquidity_tier` into its "illiquid" bucket instead of the "unknown → assume large_cap" default
it's supposed to get). Wrap in the same `try/except MarketDataError` pattern already used two lines
above for `get_intraday_bars` in that same function, degrading to `market_cap=None` (today's exact
behavior) on any fetch failure — never raising, matching CONSTRAINT #6.

```python
try:
    fundamentals = provider.get_fundamentals(symbol)
    raw_cap = fundamentals.get("marketCap")
    market_cap = float(raw_cap) if raw_cap is not None and np.isfinite(raw_cap) and raw_cap > 0 else None
except MarketDataError:
    market_cap = None
...
run_backtrader_simulation(df, market_cap=market_cap)
```

`pairs/simulation.py`'s own separate, structurally distinct `TieredCostCommissionInfo(...,
market_cap=None, ...)` call (a two-symbol pairs backtest with no single "the" market cap) is
explicitly **out of scope** — the audit finding named only `simulation_engine.py`'s two call sites,
and pairs trading has no single ticker to resolve a cap for without a larger design decision (e.g.
min of the two legs) that wasn't asked for here.

**Test** (`tests/test_simulation_engine.py`): extend the `get_vbt_costs`/`optimize_strategy_vectorbt`
coverage with a case asserting `optimize_strategy_vectorbt(price, market_cap=1e8)` (illiquid) uses a
higher-fee cost pair than `optimize_strategy_vectorbt(price, market_cap=50e9)` (large-cap) — reusing
the module's existing seeded synthetic-price helper and `importorskip("vectorbt")` guard. For
`run_backtrader_simulation`, add a case passing `market_cap=1e8` and asserting no crash (the existing
happy-path test already proves the commission wiring runs; this proves the parameter accepts and
threads a real value without needing to unpick Backtrader internals for the exact commission dollar
amount). Add a focused unit test in a new or existing `investyo_mcp_server` test module (check
`tests/test_investyo_mcp_server.py` for the right home, matching its existing
`run_backtrader_simulation` monkeypatch tests around line ~3400) asserting `run_backtest(symbol=...)`
resolves fundamentals and passes a non-`None` `market_cap` through to
`run_backtrader_simulation` when `get_fundamentals` returns a valid `marketCap`, and passes `None`
(not a crash, not a fabricated `0.0`) when fundamentals raise/return nothing.

## Fix 3 — `execution/almgren_chriss_router.py` degenerate-parameterization guard

In `compute_trading_trajectory`, after the existing five validation checks and the `tau = total_time
/ n_intervals` computation (both already guarantee `n_intervals`/`total_time`/`temp_impact`/
`perm_impact`/`volatility` are individually valid), add the AC(2001) well-posedness check:

```python
tau = total_time / n_intervals

effective_temp_impact = temp_impact - 0.5 * perm_impact * tau
if effective_temp_impact <= 0:
    raise ValueError(
        "temp_impact must exceed 0.5 * perm_impact * (total_time / n_intervals) "
        "for a well-posed Almgren-Chriss cost function "
        "(eta_tilde = eta - 0.5*gamma*tau must be > 0)"
    )
```

Verified against every existing call site (all of `tests/test_almgren_chriss_router.py`'s ~10 cases,
plus `api/pilots_api.py`'s `POST /pilots/execution/optimize/almgren-chriss` with its fixed
`temp_impact=0.1, perm_impact=0.01` params) that none currently trip this guard — the fix is
additive-only, no existing behavior changes.

**Test** (`tests/test_almgren_chriss_router.py`): add
`test_degenerate_cost_parameterization_raises` with `pytest.raises(ValueError)`, using a
`temp_impact` chosen to be below `0.5 * perm_impact * tau` (e.g. `perm_impact=1.0, temp_impact=0.01,
total_time=1.0, n_intervals=1` → `tau=1.0`, threshold `0.5`, `0.01 < 0.5`), matching the file's
existing `test_invalid_parameters` style (one `pytest.raises` block per invalid case). Also add a
boundary-adjacent passing case just above the threshold to confirm the guard doesn't over-trigger.

## Fix 4 — `validation/stress_scenarios.py` import instead of duplicate

Replace the local literal with a real import, preserving the `MAX_STRESS_DRAWDOWN` name (confirmed
imported externally by `Gravity AI Review Suite.py:1825` and `tests/test_stress_gate.py:23,87,116`,
so it must stay a valid public re-export, not just an internal rename):

```python
from validation.thresholds import STRESS_MAX_DRAWDOWN

# Deployability threshold: an options-selling strategy must keep max drawdown
# strictly below this in EVERY stress window. Sourced from validation/thresholds.py
# (the single source of truth for all deployability-gate thresholds) rather than
# duplicated here, so the two can never drift apart. 50% is a deliberately lenient
# survival bar — a short-vol book down 50% in a two-week shock is already in
# serious trouble; anything worse is disqualifying regardless of full-sample
# metrics.
MAX_STRESS_DRAWDOWN: float = STRESS_MAX_DRAWDOWN
```

No circular-import risk: `validation/thresholds.py` has zero project imports (`from __future__
import annotations` only). All four in-file usages (`return self.max_drawdown < MAX_STRESS_DRAWDOWN`
at line 156, the two docstring/log-message references) are untouched — they keep referencing the
module-level `MAX_STRESS_DRAWDOWN` name, which now just resolves to the shared constant instead of
an independent literal.

**Test** (`tests/test_stress_gate.py`): add
`test_max_stress_drawdown_is_sourced_from_thresholds_module` asserting
`validation.stress_scenarios.MAX_STRESS_DRAWDOWN == validation.thresholds.STRESS_MAX_DRAWDOWN`
by identity/value, so a future re-introduction of a second hardcoded literal breaks this test
immediately.

## Documentation update

Add one consolidated bullet to `CLAUDE.md`'s changelog section (the `.claude/hooks/sync_agent_docs.sh`
hook mirrors it onto `AGENTS.md` automatically — no manual edit needed there), placed near the other
recent small-audit-fix bullets (e.g. right after the "Execution audit trail wiring, circuit-breaker
latency trip, and client-order-id collision fix" bullet), covering all four fixes in one entry per
the existing convention for grouped small audit fixes in that file. No `docs/architecture/*.md` or
`docs/signals/*.md` edits are needed: `docs/architecture/execution.md`'s one-line
`almgren_chriss_router.py` entry and `docs/architecture/validation-and-signals.md`'s
`stress_scenarios.py` entry (which already correctly states the 0.50 threshold and gate mechanics)
remain accurate as-is — these are internal robustness/consistency fixes, not behavior or threshold
changes, so nothing in those docs is stale.

## Branch & PR workflow

1. `git checkout -b fix-options-cost-model-audit-findings` from the current synced `main`.
2. Apply the four fixes as four separate small commits (per the user's explicit preference for
   independent commits within one PR), each with its regression test(s):
   - `fix: add missing rho keys to 0DTE Greeks branch in options_risk.py`
   - `fix: thread real market_cap through simulation_engine cost model call sites`
   - `fix: guard against degenerate Almgren-Chriss cost parameterization`
   - `fix: import STRESS_MAX_DRAWDOWN from validation/thresholds.py instead of duplicating`
3. Add the CLAUDE.md documentation bullet as part of (or immediately after) the fourth commit.
4. Copy this plan (and a walkthrough once written) into `.claude/` under a unique, task-scoped name,
   e.g. `.claude/options_cost_model_audit_fixes_implementation_plan.md` and
   `.claude/options_cost_model_audit_fixes_walkthrough.md`, per the repo's PR-artifact-naming rule.
5. Open a PR against `main`.

## Verification

- `pytest tests/test_options_risk.py tests/test_simulation_engine.py tests/test_almgren_chriss_router.py tests/test_stress_gate.py -q`
  — all pass, including the new regression tests.
- `pytest tests/test_investyo_mcp_server.py -k run_backtest -q` (or the correct existing test class
  covering `run_backtest`) to confirm the MCP tool's market-cap threading test passes and nothing
  else in that large file regresses from the signature change.
- Full targeted run: `make verify` (or `pytest -q -p no:randomly -m "not network"`) before opening
  the PR, per CLAUDE.md's mandatory-verification rule — the Stop hook will also block automatically
  if a targeted test tied to these changes is left failing.
