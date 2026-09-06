# Intraday Gamma Scalping (`pilots/gamma_scalper.py`)

## Rationale

Simulates discrete delta-hedge rebalancing on an existing long-gamma options position (straddle
or strangle) against a supplied price path, decomposing the resulting P&L into Gamma Rent, Theta
Decay, Transaction Costs, and Net Edge.

## Backtest Validation — EXCLUDED from the deployability gate (not a strategy)

**Registered in `STRATEGY_REGISTRY` (`scripts/refresh_validations.py`) as of PR #952 — but as a
deliberate `UNGATEABLE_NOT_A_STRATEGY` stub, not a numeric PBO/DSR/Sharpe/MaxDD backtest.** The
registry entry's own adapter (`_build_ungateable_adapter`) always raises
`RuntimeError("UNGATEABLE_DATA_GAP: Excluded — not a strategy (no scan/evaluate/execute path, no
PaperAccountStore import, its only threshold is a hedge band).")`, so a `--strategies
gamma_scalper` validation run records an honest dead-letter rather than silently doing nothing;
`api/pilots_api.py`'s `OPTIONS_DESK_DEPLOYABILITY_GATES["gamma_scalper"]` echoes the same reason
as `gate_status="UNGATEABLE_NOT_A_STRATEGY"` on the live `POST /pilots/options/gamma-scalp/simulate`
response. PBO/DSR/Sharpe/MaxDD remain undefined for what this module actually is — the stub
exists to make that status explicit and queryable, not to compute those metrics.

`simulate_gamma_scalping(option_position=None, price_path=None, delta_threshold=0.15, ...)` takes
**both the position AND the market price path as caller-supplied inputs**. It never decides when
to open or close a position, never scans for an entry condition, and never selects a strike or
expiration — it is a hedging-*economics calculator* answering "given this position and this price
path, what would gamma-scalping P&L look like", not a signal generator answering "should I trade,
and what."

Confirming evidence:
- `pilots/gamma_scalper.py.__all__` contains only `simulate_gamma_scalping`, two synthetic
  price-path generators, and three result dataclasses — no `scan_*`/`evaluate_*`/`execute_*`,
  unlike `pilots/zero_dte_engine.py.__all__` and `pilots/dispersion_trading.py.__all__`.
- It never imports `PaperAccountStore` and has no order-submission path anywhere in the module.
- Its one and only endpoint, `POST /pilots/options/gamma-scalp/simulate`, is read-token gated and
  places no order.
- Its one threshold (`|net delta| ≥ 0.15 × Σ|qty|·multiplier`) is a **rebalance/hedge band**, not
  an entry or exit rule; `volatility_spread` is computed *after the fact* for reporting and
  nothing in the module branches on it.

There is no return series to feed a deployability gate: the P&L is a deterministic transform of a
path the *caller* chooses, so there is no independent "did this strategy generate alpha" question
to answer.

**Fabrication hazard found while analysing it (out of scope here — not fixed)**: called with no
arguments, `simulate_gamma_scalping` invents its own `SPY STRADDLE` leg and a seeded
geometric-Brownian-motion price path, and returns plausible-looking P&L numbers for a position and
market that were never real. Any caller of the bare function without real inputs should be
treated as producing a demo/sanity-check result, not a backtest.

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md)'s 2026-08-17
"Options Desk Deployability-Gate Coverage" entry (the original exclusion analysis and its own
fabrication-hazard finding) and its later "considered and reverted" entry confirming
`gamma_scalper`'s registration was kept as one of the genuine, in-scope registry fixes, plus
`.claude/giant_master_plan_audit.md`'s finding F4.
