# Implementation Plan: `copula_stat_arb` options-selling stress-gate exclusion

## Background

An independent audit found that `_resolve_options_selling_stress_fn(name)` in
`scripts/refresh_validations.py` — the function that maps a `STRATEGY_REGISTRY` name to a
`stress_returns_fn` for `validation/stress_scenarios.py`'s tail-scenario deployability gate —
does not include `copula_stat_arb`. CLAUDE.md documents: "Options-selling strategies carry an
additional tail-scenario stress gate... they are deployable only if max drawdown is < 50% AND
the account survives... in EVERY dated shock window." The question: is this a real gap that
needs wiring, or is the gate simply inapplicable to this strategy?

## Investigation

Read `pilots/copula_stat_arb.py` in full, focusing on `execute_copula_spread_trade` (the actual
order-construction code, not the module's docstring or its name). Finding: this is a pure equity
pairs/stat-arb strategy. It buys/sells **shares** of the pair's two legs (`symbol_y`/`symbol_x`)
via `PaperAccountStore.apply_multi_leg_fill`. No options contract is ever constructed, priced, or
written anywhere in the module. The function's own inline comment states this explicitly: "`qty`
here is SHARES, not options contracts." It is also correctly absent from
`PAPER_BROKER_OPTIONS_STRATEGIES` (the list of strategies that actually sell option premium in
the live Paper Broker).

Conclusion: this strategy does NOT sell options, so CLAUDE.md's stress-gate rule does not apply
to it by its own terms. The correct fix is documentation, not a code wire-up forcing a
`stress_returns_fn` onto an equity strategy just to satisfy the rule mechanically.

## Changes

1. `scripts/refresh_validations.py`: add an explanatory comment at the end of
   `_resolve_options_selling_stress_fn`, immediately before the final `return None`, explaining
   why `copula_stat_arb` is deliberately excluded — citing the trade-construction evidence above,
   contrasting with `zero_dte_engine`/`gamma_scalper` (excluded for a *different* reason: no
   reachable data, not "doesn't apply").
2. `docs/signals/copula_stat_arb.md`: add an "Options-selling tail-scenario stress gate: not
   applicable" note to the "Backtest Validation & Deployability Status" section, mirroring the
   style of `docs/signals/zero_dte_engine.md`'s "NOT GATEABLE" section but for the different
   underlying reason.
3. `docs/VALIDATION_STRATEGY_FIX_LOG.md`: append an addendum to the existing 2026-08-19
   `copula_stat_arb` entry recording the investigation, finding, and the explicit decision not
   to wire in a stress function.

No changes to `is_options_selling`/`stress_returns_fn` behavior for `copula_stat_arb` —
it stays `False`/`None`, exactly as before. No `STRATEGY_REGISTRY` entry changes. No new tests
needed (this is a documentation-only fix); existing tests (`tests/test_refresh_validations.py`,
`tests/test_copula_stat_arb.py`, `tests/test_command_manifest_freshness.py`,
`tests/test_stress_gate.py`) verified to still pass unchanged.

## Documentation-update step (per CLAUDE.md's Implementation Plan requirement)

- `docs/signals/copula_stat_arb.md` — updated (see above).
- `docs/VALIDATION_STRATEGY_FIX_LOG.md` — updated (see above).
- No other `docs/` files reference this gate for this strategy.
