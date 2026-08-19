# Walkthrough: vol_mispricing Live Paper-Execution Endpoint

## What changed and why

`vol_mispricing` is the one options-desk pilot whose deployability gate is a **measured
failure** rather than an unmeasurable data gap: walk-forward validation put its net Sharpe at
-0.499, DSR at 0.027, and it blows up (max drawdown 203.8%) in the Oct-2008 stress window. Its
three siblings (`earnings_crush`, `dispersion_trading`, `zero_dte_engine`) are `UNGATEABLE_DATA_GAP`
— they can't be measured at all (no historical single-name IV / intraday data exists), so their
gate is surfaced for transparency but never blocks. `vol_mispricing` deserved different treatment:
a real execute path exists now, but the measured failure means it must be blocked by default.

## Files changed

### `execution/options_paper_executor.py`
- `execute_earnings_crush_trade` gained `strategy_name: Optional[str] = None`. `None` preserves
  the exact historical `"Earnings Crush"` label for every existing caller; an explicit value
  overrides both the returned `res["strategy"]` and the parent order's blotter label
  (`f"{strategy_name} {symbol}"`, uppercased by the store).
- Fixed a `CONSTRAINT #4` fabrication bug: a leg with no resolvable `fill_price`/`raw_price` no
  longer gets a fake `$1.50`/`$150.00` price. It's now skipped and tracked; if any leg ends up
  unpriced, the whole trade is refused (`{"success": False, "reason": "..."}"`) before
  `apply_multi_leg_fill` is ever called — matching the function's existing
  `if not parsed_legs: return {"success": False, ...}` pattern.

### `pilots/vol_mispricing.py`
- New `execute_vol_mispricing_trade(symbol, *, candidate, contracts=1, dry_run=False,
  is_live=False)`. Requires the caller to pass an already-selected candidate (one element of
  `build_candidate_strategy_trades()`'s output) — this function never picks "the best" one
  itself. Translates `_create_strategy_leg`'s `{"action", "unit_price"}` shape into the
  executor's `{"side", "fill_price"}` shape: **`fill_price = unit_price * 100.0`** (options
  premia are quoted per-share; one contract = 100 shares). Delegates to
  `OptionsPaperExecutor.execute_earnings_crush_trade(..., strategy_name="Vol Mispricing")`.
  No deployability check inside this function — that's the API layer's job.
- Worked example verifying the $/share→$/contract math (also in the test suite): a $190 short
  PUT at $2.50/share and a $185 long PUT at $1.00/share, 2 contracts, commission
  $0.65×2×2=$2.60 → `net_cash_impact = (250.00×2 − 100.00×2) − 2.60 = $297.40`, confirmed against
  the real `PaperAccountStore` cash delta.

### `api/pilots_api.py`
- New `VolMispricingExecuteRequest` (symbol, candidate, contracts, dry_run, is_live,
  `override_deployability_gate: bool = False`).
- New `POST /pilots/options/mispricing/execute`, same auth tier as its three siblings
  (`require_command_token` + `require_paper_broker_writes_enabled`). Before calling
  `execute_vol_mispricing_trade`, checks `OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]
  ["gate_status"] == "MEASURED_FAIL"`; if so and `override_deployability_gate` is not `True`, it
  returns `{"ok": False, "blocked": True, "gate_status": {...}}` and never executes. Every
  response — blocked or not — echoes `gate_status` and (when not blocked) `override_applied`.
- Corrected the stale comment above `OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]` (it
  previously said "no live consumer today" / "IF a live execute endpoint is ever added").

### Tests
- `tests/test_pilots_api.py`: replaced `test_vol_mispricing_has_no_paper_execute_endpoint` with
  `test_vol_mispricing_has_a_paper_execute_endpoint` plus
  `TestVolMispricingExecuteDeployabilityGate` (blocked-without-override — asserting
  `execute_vol_mispricing_trade` is never called; override+dry_run proceeds; `gate_status`
  always present with the real Sharpe/DSR numbers; fails closed on writes-disabled/wrong-token).
- `tests/test_options_paper_executor.py`: `strategy_name` default-preserved / override tests,
  no-fabrication-refusal test.
- `tests/test_vol_mispricing.py`: `execute_vol_mispricing_trade` coverage — symbol validation,
  `is_live` refusal, dry-run preview, missing/empty-candidate refusal, the leg-translation
  worked example (exact `net_cash_impact` and account-cash-delta assertions), and the
  no-fabrication refusal path.

### Docs
- `docs/signals/vol_mispricing.md`: "Live Paper-Execution Status" section rewritten to describe
  the new endpoint, the leg-translation math, the two executor bugs fixed, and the enforced
  per-request override design.
- `docs/VALIDATION_STRATEGY_FIX_LOG.md`: new dated entry ("2026-08-18 (cont.): vol_mispricing
  Live Paper-Execution Endpoint — Enforced Override Gate").
- `CLAUDE.md` (auto-mirrored to `AGENTS.md`): corrected the options-desk summary bullet that
  previously stated vol_mispricing "has no live execute path at all."

## Key design decisions

1. **Override gate is per-request, never a settings flag** — `override_deployability_gate` lives
   on the request body only. No `.env`/`settings.*` flag can disable the check globally, and the
   response always states whether an override was applied.
2. **$/share → $/contract leg translation**: `fill_price = unit_price * 100.0`. Confirmed by
   reading `_create_strategy_leg`'s callers (bid/ask/mid_price values like `2.50`, `1.00` — real
   per-share option quotes) and cross-checked with a hand-computed worked example against the
   real `PaperAccountStore`.
3. **`strategy_name` backward compatibility**: `None` default preserves the exact historical
   "Earnings Crush" behavior for every pre-existing caller (verified against
   `tests/test_options_lifecycle.py`'s unmodified assertions); only an explicit value changes
   the label, avoiding any behavior change to `earnings_crush`'s own execute path.

## Verification results

```
uv run pytest tests/test_options_paper_executor.py tests/test_pilots_api.py tests/test_vol_mispricing.py -q -m "not network" -k "vol_mispricing or earnings_crush or mispricing"
32 passed

uv run pytest tests/test_pilots_api.py -q -m "not network"
398 passed

uv run pytest tests/test_options_paper_executor.py -q -m "not network"
11 passed

uv run python -m ruff check . --select=F821,F822,F823,E9
All checks passed!
```

`scripts/measure_settings_census.py --write` and `scripts/settings_liveness.py --write` were run;
the census diff (route count 78→79, plus downstream line-number drift from the new request
model) was committed. Liveness output was unchanged.
