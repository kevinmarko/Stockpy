# Walkthrough — `pilots/unusual_options_flow.py` follow-up audit findings #3–#8 (UOA half)

## What changed and why

### Finding #3 — real HV30 in the live IV-burst path

Before this change, `get_unusual_options_activity`'s live-fetch loop called
`scan_unusual_options_activity(chain_data=chain_map, spot_price=spot_price)` with no
`historical_vol_30d` argument. `calculate_iv_burst_score` therefore always saw `hv_30=None`
for live-fetched data, so the IV Surge / burst-detection path could never actually fire
outside of tests that hand-supply `historical_vol_30d`/`historical_prices` directly.

Added `_resolve_live_historical_vol_30d(symbol)` right after `_resolve_live_spot_price`,
using the same lazy, function-scoped import + never-raises pattern:

```python
def _resolve_live_historical_vol_30d(symbol: str) -> Optional[float]:
    try:
        from data.historical_store import HistoricalStore
        store = HistoricalStore()
        bars = store.get_bars(symbol, lookback_days=45)
        if bars is None or "Close" not in getattr(bars, "columns", []):
            return None
        return calculate_historical_volatility(bars["Close"], window=30)
    except Exception as exc:
        logger.debug(...)
        return None
```

Wired into the live-scan loop:

```python
historical_vol_30d = _resolve_live_historical_vol_30d(sym)
scanned = scan_unusual_options_activity(
    chain_data=chain_map, spot_price=spot_price, historical_vol_30d=historical_vol_30d,
)
```

Since this introduces a second `data.*` submodule import, the AST-safety allowlist test
(`TestASTSafety::test_unusual_options_flow_stays_dependency_light_and_ast_safe`) and the
module's own docstring were both widened from `{"data.market_data"}` to
`{"data.market_data", "data.historical_store"}`.

### Finding #4 — mid-block deadband

Previously ANY trade fractionally above/below the exact midpoint was classified fully
BULLISH/BEARISH. Added `MID_BLOCK_DIRECTIONAL_THRESHOLD = 0.25` and a deadband computed as
`half_spread * MID_BLOCK_DIRECTIONAL_THRESHOLD` — a trade must clear that offset from the
midpoint (toward bid or ask) to earn a directional label; anything inside the deadband
(including the exact midpoint) stays NEUTRAL. The two pre-existing exact-midpoint tests
(`test_mid_block_trade`, `test_multi_trade_aggressor_classification`'s strike-125.0 case)
were re-verified to still pass unmodified — a 0-offset trade is always inside any
positive deadband.

### Finding #5 — honesty flags for silent proxy substitutions

(a) `_extract_contract_fields` now tracks `trade_price_is_estimated` (set `True` only when
`trade_price` had to fall back to `(bid+ask)/2` because the reported price was `<= 0`) and
returns it in its result dict.

(b) `UOARecord` gained `price_is_estimated`/`spot_price_is_estimated` (both default
`False`). `scan_unusual_options_activity` sets `price_is_estimated` per-record from the
extraction result, and computes `spot_was_estimated` once (right where `resolved_spot` is
inferred from `median(strikes)`), applying it to every record produced by that call.
`webapp/src/api/types.ts`'s `UnusualOptionTrade` gained the two matching optional fields —
purely additive, no `mock.ts`/UI wiring in this task's scope.

### Finding #6 — per-contract isolation

The entire function body previously sat inside one function-wide
`try/except Exception: return []`, so a single malformed contract mid-loop discarded every
anomaly already found for that symbol. Restructured so the outer try/except covers setup
only (extraction, HV30 resolution, spot inference — a totally malformed `chain_data` still
degrades to `[]`), and the per-contract processing body inside `for c in raw_contracts:`
has its own `try/except Exception: continue`, matching
`pilots/earnings_crush.py::evaluate_earnings_crush_candidates`'s existing per-item
try/except/continue shape.

### Finding #7 (UOA half only) — `diagnostics` kwarg

`get_unusual_options_activity` gained an optional `diagnostics: Optional[Dict[str, Any]] =
None` kwarg (purely additive — return type/value is unchanged when omitted). When passed,
it's populated with:
- `symbols_requested` — set near the top, always.
- `read_from_cache` — `True`/`False`, set correctly on every return path (cache hit,
  cache miss with no symbols, and the live-scan path).
- `symbols_fetch_failed` — a list, initialized to `[]` only once the live-scan path is
  reached, and appended to whenever `_fetch_live_options_chain_map` returns falsy. Left
  entirely unset (not even `[]`) on a cache-hit response, so a caller can distinguish "we
  tried a live fetch and nothing failed" from "we never got that far" — documented
  explicitly in the function's docstring.

Note: this task deliberately implements only the UOA half of finding #7 —
`pilots/earnings_crush.py` is owned by a sibling branch and was not touched.

### Finding #8 — atomic write for `save_uoa_records`

Switched from a direct `p.write_text(...)` to the temp-file + `Path.replace()` idiom
already established by `desktop/orchestrator_daemon.py::_write_daemon_file`:

```python
tmp = p.with_suffix(".tmp")
tmp.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
tmp.replace(p)
```

A failed/interrupted write to the `.tmp` sibling now leaves the previously-saved file
byte-identical instead of truncated/corrupted.

## Tests added (`tests/test_unusual_options_flow.py`)

New section "8. Follow-up Audit Findings (#3, #4, #5, #6, #7, #8) Tests" (the pre-existing
"OptionsFlowSentimentSignal Tests" section was renumbered 8 → 9 to make room):

- `TestResolveLiveHistoricalVol30d` (4 tests) — real HV30 recovery via a mocked
  `HistoricalStore`, plus construction-raises / `get_bars` returns `None` / missing
  `Close` column degradation paths.
- `TestLiveFetchIVBurstIntegration` (1 test) — end-to-end `get_unusual_options_activity`
  live-scan path with `_fetch_live_options_chain_map`/`_resolve_live_spot_price`/
  `_resolve_live_historical_vol_30d` all mocked, asserting the resulting record's
  `iv_burst_score`/`iv_burst_detected` are genuinely populated.
- `TestMidBlockDeadband` (4 tests) — inside-deadband stays NEUTRAL, outside-deadband goes
  directional both ways, plus re-confirmation of both pre-existing exact-midpoint cases.
- `TestPriceIsEstimatedFlag` (2 tests) — `False` on a real last price, `True` on a
  midpoint fallback.
- `TestSpotPriceIsEstimatedFlag` (2 tests) — `False` with a real supplied spot, `True`
  for `None`/`0.0`/negative spot (median-of-strikes inference).
- `TestPerContractIsolation` (1 test) — a `categorize_trade_aggressiveness` side_effect
  raises for one contract only; asserts the other qualifying contract is still returned.
- `TestDiagnostics` (3 tests) — cache-hit sets `read_from_cache=True` and omits
  `symbols_fetch_failed`; live-scan path with one failing symbol records it; live-scan
  path with all symbols succeeding reports an empty `symbols_fetch_failed` list.
- `TestSaveUoaRecordsAtomicWrite` (2 tests) — a simulated write failure on the `.tmp`
  sibling leaves the original file's content unchanged; a happy-path roundtrip confirms
  the new honesty fields survive persistence and no `.tmp` file is left behind.

## Verification

```
pytest tests/test_unusual_options_flow.py -q
```
51 passed (32 pre-existing + 19 new), zero regressions — including `TestASTSafety` with
the widened allowlist, and both exact-midpoint NEUTRAL assertions under the new deadband.

```
pytest tests/test_unusual_options_flow.py tests/test_pilots_strategy_matrix.py -q
```
133 passed.

`ruff check pilots/unusual_options_flow.py tests/test_unusual_options_flow.py` — baseline
(pre-change) carries 89 pre-existing findings in this file pair; post-change carries 96,
all 7 new ones are `Dict`-vs-`dict` type-annotation style nits matching the file's own
existing convention (the file already imports and uses `Dict` from `typing` throughout) —
no new genuine-bug-class findings.

`webapp/src/api/types.ts` — additive-only (`price_is_estimated?`/
`spot_price_is_estimated?` on `UnusualOptionTrade`); `npm run --prefix webapp typecheck`
could not be executed in this worktree (`node_modules` not installed), but the change is
two new optional interface fields with no other code referencing them yet, so type risk is
minimal.
