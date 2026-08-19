# Implementation Plan: vol_mispricing Live Paper-Execution Endpoint

## Objective

Add `POST /pilots/options/mispricing/execute` for `pilots/vol_mispricing.py`, gated by an
explicit per-request `override_deployability_gate` flag, since `vol_mispricing` is a **measured**
deployability failure (Sharpe -0.499, DSR 0.027, fails the Oct-2008 stress window) — unlike its
three sibling pilots (`earnings_crush`, `dispersion_trading`, `zero_dte_engine`), which are merely
`UNGATEABLE_DATA_GAP`s whose gate is informational and never blocks.

## Scope

1. Fix two latent bugs in the shared executor
   (`execution/options_paper_executor.py::OptionsPaperExecutor.execute_earnings_crush_trade`):
   a `CONSTRAINT #4` fabricated-price fallback (`$1.50`/`$150.00` sentinel) and a hardcoded
   `strategy_name="Earnings Crush"` mislabeling bug, both blocking a safe generic reuse of this
   executor by a non-earnings-crush caller.
2. Add `pilots/vol_mispricing.py::execute_vol_mispricing_trade` — a pure execution primitive
   (no deployability check itself) that translates a caller-selected candidate trade's legs
   ($/share `unit_price` → $/contract `fill_price`, i.e. `× 100.0`) and delegates to the shared
   executor with `strategy_name="Vol Mispricing"`.
3. Add `POST /pilots/options/mispricing/execute` to `api/pilots_api.py` — same auth tier as its
   three siblings (`require_command_token` + `require_paper_broker_writes_enabled`) **plus** an
   enforced `OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]` check that blocks unless the
   request sets `override_deployability_gate: true`.
4. Update `tests/test_pilots_api.py`'s regression guard (previously asserting the endpoint's
   absence) to instead assert the blocked-by-default / override-proceeds / gate-always-echoed
   behavioral contract, per that test's own documented follow-up instructions.
5. Add executor-level tests (`tests/test_options_paper_executor.py`) for the `strategy_name`
   param and the no-fabrication fix, and pilot-level tests (`tests/test_vol_mispricing.py`) for
   `execute_vol_mispricing_trade` including a hand-computed leg-price-translation worked example.
6. Documentation: rewrite `docs/signals/vol_mispricing.md`'s "Live Paper-Execution Status"
   section; append a dated entry to `docs/VALIDATION_STRATEGY_FIX_LOG.md`; correct `CLAUDE.md`
   (auto-mirrored to `AGENTS.md` via `.claude/hooks/sync_agent_docs.sh`).

## Design decisions

- **Override is per-request, never a settings flag.** `override_deployability_gate: bool = False`
  on the request body — no `settings.*_ENABLED` flag disables the check globally. Every response
  (blocked or not) echoes the real `gate_status` and `override_applied` so the caller can never
  be surprised about which mode ran.
- **`execute_vol_mispricing_trade` performs no deployability check itself** — that responsibility
  stays in the API layer (`post_options_mispricing_execute`), matching the existing division of
  responsibility where `execute_earnings_crush_trade`/`execute_dispersion_trade` are likewise
  pure execution primitives with gate-stamping happening at the endpoint.
- **`strategy_name=None` preserves exact historical executor behavior.** Every pre-existing
  caller of `execute_earnings_crush_trade` gets byte-identical behavior; only an explicit
  `strategy_name=` argument (used by the new vol_mispricing caller) changes the label.
- **Leg price translation**: `_create_strategy_leg`'s `unit_price` is a per-share premium; one
  option contract = 100 shares, so `fill_price = unit_price * 100.0`. Verified with a
  hand-computed worked example test asserting the exact `net_cash_impact` and account cash delta.
- **No-fabrication fix applies unconditionally**, not just for the vol_mispricing caller — a leg
  with no resolvable price now refuses the whole trade (`CONSTRAINT #4`) for every caller of
  `execute_earnings_crush_trade`, including the original `earnings_crush` pilot.

## Verification

```
uv run pytest tests/test_options_paper_executor.py tests/test_pilots_api.py tests/test_vol_mispricing.py -q -m "not network" -k "vol_mispricing or earnings_crush or mispricing"
uv run pytest tests/test_pilots_api.py -q -m "not network"
uv run pytest tests/test_options_paper_executor.py -q -m "not network"
uv run python -m ruff check . --select=F821,F822,F823,E9
python3 scripts/measure_settings_census.py --write && python3 scripts/settings_liveness.py --write
```

All green; settings census regenerated (route count 78 → 79, line-number drift only); liveness
unchanged.
