# Implementation Plan — `pilots/unusual_options_flow.py` follow-up audit findings #3–#8 (UOA half)

## Scope

Implements findings #3, #4, #5, #6, #7 (the UOA half only — `get_unusual_options_activity`),
and #8 from a follow-up audit of `pilots/unusual_options_flow.py`. Explicitly excludes
`pilots/earnings_crush.py`, `api/pilots_api.py`, and `docs/signals/earnings_crush.md` (owned
by a sibling branch/agent to be merged separately).

## Findings and approach

1. **#3 — real HV30 threaded into the live IV-burst path.** Added
   `_resolve_live_historical_vol_30d(symbol)`, a lazy, function-scoped
   `data.historical_store.HistoricalStore().get_bars(symbol, lookback_days=45)` read
   feeding `calculate_historical_volatility(..., window=30)`, mirroring
   `_resolve_live_spot_price`'s exact lazy-import/never-raises pattern. Wired into
   `get_unusual_options_activity`'s live-scan loop so `scan_unusual_options_activity`
   receives a genuine `historical_vol_30d` instead of always seeing `None`. Widened the
   module's AST-safety allowlist (both the module docstring's Design Invariants section
   and `tests/test_unusual_options_flow.py::TestASTSafety`) to permit
   `data.historical_store` alongside `data.market_data`.

2. **#4 — mid-block deadband.** Added `MID_BLOCK_DIRECTIONAL_THRESHOLD = 0.25` and
   reworked `categorize_trade_aggressiveness`'s mid-block branch to require a trade to
   clear `half_spread * MID_BLOCK_DIRECTIONAL_THRESHOLD` away from the midpoint before
   earning a directional (non-NEUTRAL) label. Verified the two pre-existing exact-midpoint
   tests (`test_mid_block_trade`, the strike-125.0 case in
   `test_multi_trade_aggressor_classification`) still pass unmodified.

3. **#5 — honesty flags for two silent proxy substitutions.**
   - (a) `_extract_contract_fields` now tracks whether `trade_price` fell back to
     `(bid+ask)/2` and returns `"trade_price_is_estimated"` in its result dict.
   - (b) `UOARecord` gained `price_is_estimated`/`spot_price_is_estimated` fields (both
     default `False`). `scan_unusual_options_activity` sets `price_is_estimated` per
     contract from the extraction result, and computes `spot_was_estimated` once per call
     (true exactly when the caller's `spot_price` was falsy/`<=0` and a value was
     successfully inferred from `median(strikes)`), applying it to every record from that
     call.
   - `webapp/src/api/types.ts`'s `UnusualOptionTrade` gained the two matching optional
     fields (additive only — no `mock.ts`/UI changes in this task's scope).

4. **#6 — per-contract isolation.** Split `scan_unusual_options_activity`'s single
   function-wide `try/except` into an outer try/except around setup only (extraction,
   HV30 resolution, spot inference — a malformed `chain_data` still degrades to `[]`) and
   a per-contract `try/except ... continue` inside the `for c in raw_contracts:` loop, so
   one malformed contract no longer discards every anomaly already found for that symbol
   — matching `pilots/earnings_crush.py`'s existing per-item try/except/continue shape.

5. **#7 (UOA half only) — `diagnostics` kwarg on `get_unusual_options_activity`.** Added
   an optional `diagnostics: Optional[Dict[str, Any]] = None` parameter, purely additive
   (return type/value unchanged). Populates `symbols_requested` up front,
   `read_from_cache` on every return path, and `symbols_fetch_failed` (a list, only
   initialized/populated on the live-scan path — deliberately left unset on a cache-hit
   response so a caller can distinguish "we tried and nothing failed" from "we never got
   that far").

6. **#8 — atomic write for `save_uoa_records`.** Switched from a direct `p.write_text(...)`
   to the temp-file + `Path.replace()` idiom already used by
   `desktop/orchestrator_daemon.py::_write_daemon_file`, so a failed/interrupted write
   never corrupts or truncates the previously-saved file.

## Documentation-update step

- Updated `pilots/unusual_options_flow.py`'s own module docstring (Design Invariants →
  AST-Safe bullet) to describe the second permitted lazy import.
- No `docs/architecture/*.md` or `docs/signals/*.md` changes are needed — this module has
  no dedicated `docs/signals/<name>.md` entry, and the `docs/architecture/validation-and-signals.md`
  entry for `pilots/` modules does not enumerate per-function detail at this granularity.

## Verification

- `pytest tests/test_unusual_options_flow.py -q` — 51 passed (32 pre-existing + 19 new),
  zero regressions.
- `pytest tests/test_unusual_options_flow.py tests/test_pilots_strategy_matrix.py -q` —
  133 passed.
- `ruff check` on both touched Python files shows only pre-existing-style findings
  (`Dict` vs `dict`, `DTZ005`) already present in the file's baseline convention — no new
  genuine-bug-class findings.
- `webapp/src/api/types.ts` change is a purely additive two-field addition to an existing
  interface; `npm run --prefix webapp typecheck` could not be run in this worktree
  (`node_modules` not installed), but the change carries negligible type risk (two new
  optional fields on an interface with no other code referencing them yet).
