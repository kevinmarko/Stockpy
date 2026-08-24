# Known issue (2026-08-24): Earnings Crush / Unusual Options Flow follow-up audit — findings #2–#9

**Status: mixed — see per-finding status below.** This doc covers a combined
follow-up audit of `pilots/earnings_crush.py` and `pilots/unusual_options_flow.py`
that surfaced eight numbered findings (#2 through #9; #1 was the already-closed
BMO/AMC bar-alignment bug — see
[`earnings_crush_bmo_amc_bar_alignment.md`](earnings_crush_bmo_amc_bar_alignment.md)
and `docs/signals/earnings_crush.md`'s own "Defects found" section). The work was
split across three branches merged together into one combined PR:

- `earnings-crush-uoa-diagnostics-followup` (this doc's own branch) — finding #7,
  implemented directly here, covering **both** `pilots/earnings_crush.py` and
  `api/pilots_api.py`'s two GET endpoints.
- `unusual-options-flow-engine-fixes` (sibling branch) — findings #3, #4, #5, #6,
  #8, and the `pilots/unusual_options_flow.py`-side half of finding #7 (the
  `get_unusual_options_activity` function itself).
- `earnings-crush-followup-historical-moves-net-credit` (a separate branch,
  stacked on PR #889) — findings #2 and #9.

Findings #3–#6, #8, #9, and the `unusual_options_flow.py` half of #7 are
described here at the level of detail specified for this combined write-up;
their implementing branches carry the authoritative code-level detail. Finding
#7's `pilots/earnings_crush.py`/`api/pilots_api.py` half is this branch's own
work and is described in full.

---

## Finding #2 (implemented on `earnings-crush-followup-historical-moves-net-credit`): `historical_moves`/`company_name` wiring

**Status: fixed on a separate branch (not verified from this branch).**

`pilots/earnings_crush.py`'s candidate payload previously omitted a per-quarter
`historical_moves` array and a human-readable `company_name` field that the
webapp's `EarningsCrushCandidate` contract (`webapp/src/api/types.ts`) already
had slots for — `to_earnings_crush_candidate_response()`'s omit-rather-than-
fabricate convention (CONSTRAINT #4) meant these fields were simply always
absent from the response rather than wrong, but the UI had no way to render the
per-quarter move history it was designed to show. The stacked branch wires
`get_historical_earnings_moves()`'s already-computed `moves` list and a company
name lookup through to the response shape.

A deliberate **non-fix** was made in the same branch: `report_timing`
(BMO/AMC) remains unset/`None` in the response, matching
`get_historical_earnings_moves()`'s own honest `timing_data_available: False`
sentinel (see `docs/signals/earnings_crush.md`'s Defect #1 write-up) — FMP's
`/earnings` calendar carries no real reporting-session field, so surfacing a
`report_timing` value in the API response would misrepresent an *inferred*
per-event label (`reaction_session_inferred: "bmo" | "amc"`, itself only
computed retroactively from historical bar data) as a forward-looking,
source-confirmed fact about an *upcoming* earnings print. Declining to expose
it is the CONSTRAINT #4-correct choice, not an oversight.

---

## Finding #3 (implemented on `unusual-options-flow-engine-fixes`): IV-burst HV30 live wiring

**Status: fixed on a sibling branch (not verified from this branch).**

`pilots/unusual_options_flow.py`'s IV-burst score is meant to compare a trade's
implied volatility against the underlying's trailing 30-day realized volatility
(HV30) to flag a genuine vol expansion. The audit found this comparison was not
actually wired to a live HV30 computation — the score was effectively comparing
against a stale or placeholder baseline rather than a real, current measurement,
which would silently understate or overstate `iv_burst_score`/`iv_expansion_flag`
for every record. The sibling branch wires a genuine live HV30 computation into
the burst-score path.

---

## Finding #4 (implemented on `unusual-options-flow-engine-fixes`): mid-block sentiment deadband

**Status: fixed on a sibling branch (not verified from this branch).**

A block trade printed at (or extremely close to) the exact midpoint of the
bid/ask spread carries no real information about which side was aggressive —
it is, by construction, the price point equidistant from both a buyer-initiated
and seller-initiated fill. The pre-fix classifier still forced these prints
into a `BULLISH`/`BEARISH` bucket, overstating directional conviction on
genuinely ambiguous trades and inflating the net flow sentiment computed by
`signals/options_flow_sentiment.py` (see that module's own doc, linked above,
for how this signal consumes UOA records). The sibling branch introduces an
explicit deadband around the midpoint so a genuinely ambiguous mid-block print
is classified neutral instead of arbitrarily bullish or bearish.

---

## Finding #5 (implemented on `unusual-options-flow-engine-fixes`): `price_is_estimated`/`spot_price_is_estimated` honesty flags

**Status: fixed on a sibling branch; webapp type support added on this branch.**

Some UOA records' `price` (the option's trade/fill price) or `spot_price` (the
underlying's price at trade time) have to be estimated rather than sourced from
a real, contemporaneous quote — e.g. when a live chain fetch degrades. Before
this fix, an estimated value was indistinguishable from a genuinely observed
one anywhere in the record, violating this codebase's CONSTRAINT #4 honesty
convention (the same convention `pilots/earnings_crush.py`'s own
`pricing_is_estimated` flag on Iron Condor net-credit estimates already
follows — see that module's docstring). The sibling branch adds two new
boolean fields, `price_is_estimated` and `spot_price_is_estimated`, to each
UOA record. This branch adds the corresponding optional fields to
`webapp/src/api/types.ts`'s `UnusualOptionTrade` interface (`price_is_estimated?:
boolean; spot_price_is_estimated?: boolean;`) so the merged PR ships one clean,
complete `types.ts` diff — no `mock.ts` changes were required since both fields
are optional and every existing mock fixture remains valid.

---

## Finding #6 (implemented on `unusual-options-flow-engine-fixes`): per-contract isolation in `scan_unusual_options_activity`

**Status: fixed on a sibling branch (not verified from this branch).**

`scan_unusual_options_activity`'s per-contract processing loop previously let
one contract's malformed or missing chain data abort processing for the rest
of the scan pass, rather than isolating the failure to that one contract and
continuing — the same "one bad symbol/contract must never crash the whole
loop" convention this codebase enforces elsewhere (e.g.
`data_engine.py`'s per-ticker try/except, and `pilots/earnings_crush.py`'s own
per-symbol try/except this branch extends with the `diagnostics["symbols_errored"]`
list — see Finding #7 below). The sibling branch wraps each contract's
processing in its own isolated try/except so a single bad contract is skipped
rather than terminating the scan.

---

## Finding #7 (implemented on this branch, `earnings-crush-uoa-diagnostics-followup`): `degraded`/diagnostics fields for both scan endpoints

**Status: fixed and verified on this branch.**

### Problem

`GET /pilots/options/flow/unusual` and `GET /pilots/options/earnings-crush/candidates`
both return an empty (or short) list on two structurally different conditions
that read identically to a client: **nothing qualified this cycle** (the scan
ran cleanly and genuinely found nothing) and **the scan itself degraded** (a
data source was unavailable, a live fetch failed, or a per-symbol/per-contract
error occurred). Collapsing these two cases violates this codebase's
CONSTRAINT #4 honesty convention — an operator staring at an empty Earnings
Crush or Unusual Flow screen has no way to tell "quiet market" apart from
"the pipeline is broken."

### Root cause

Neither `pilots.earnings_crush.evaluate_earnings_crush_candidates`/
`get_earnings_crush_candidates` nor `pilots.unusual_options_flow.get_unusual_options_activity`
exposed any signal about internal degradation to their caller — both are
already dead-letter-resilient (CONSTRAINT #6: a resolution failure or
per-symbol exception is caught, logged, and skipped, never raised), which is
correct behavior for *not crashing*, but as a side effect also meant no
information about what was skipped ever reached the HTTP layer.

### Fix

**`pilots/earnings_crush.py`** — both `evaluate_earnings_crush_candidates` and
`get_earnings_crush_candidates` gained an optional `diagnostics: Optional[Dict[str,
Any]] = None` kwarg (the convenience alias `get_earnings_crush_candidates`
accepts and forwards it). When passed a mutable dict, `evaluate_earnings_crush_candidates`
populates:

- `diagnostics["symbols_total"]` — the universe size, set near the top of the
  function once `universe` is available.
- `diagnostics["store_available"]` / `diagnostics["options_provider_available"]`
  — booleans reflecting whether `HistoricalStore`/the options provider
  actually resolved to a usable instance, set **after** the existing
  store/options_provider resolution `try/except` blocks (so a construction
  failure is honestly reflected, not just whether an instance was initially
  passed in).
- `diagnostics["symbols_errored"]` — a list, appended to inside the existing
  per-symbol `except Exception as exc: logger.warning(...); continue` block
  (the one place today that already catches a genuine per-symbol processing
  failure — the fix only adds recording the symbol there, no new catch site).

This is purely additive: `diagnostics=None` (the default) leaves both
functions' return type and behavior completely unchanged — verified by a
dedicated regression test (`test_diagnostics_none_default_is_purely_additive`)
and by every pre-existing test in `tests/test_earnings_crush.py` continuing to
pass unmodified.

**`api/pilots_api.py`** — both handlers now construct a local `diagnostics: Dict[str,
Any] = {}`, pass it through to the underlying scan function, and derive an
honest `degraded: bool` from it:

- `get_options_earnings_crush_candidates`: `degraded = not diagnostics.get("store_available",
  True) or not diagnostics.get("options_provider_available", True)`. The
  response gains `degraded` and `symbols_errored` (`list[str]`) alongside the
  existing `count`/`candidates` keys.
- `get_options_flow_unusual`: `degraded = bool(diagnostics.get("symbols_fetch_failed"))
  and not diagnostics.get("read_from_cache", False)` — a fetch failure only
  counts as degraded when the response wasn't served entirely from the
  persisted cache (a cache-served response with stale-but-real data is not the
  same failure mode as a live fetch genuinely failing with nothing to fall
  back on). The response gains `degraded` and `symbols_fetch_failed`
  (`list[str]`) alongside the existing `count`/`records`/`trades` keys.

This half of finding #7 (the `get_unusual_options_activity` function itself
populating `diagnostics["symbols_fetch_failed"]`/`diagnostics["read_from_cache"]`)
is implemented on the sibling `unusual-options-flow-engine-fixes` branch under
the following contract, trusted without independent verification from this
branch:

> `pilots.unusual_options_flow.get_unusual_options_activity(symbols=None,
> min_vol_oi=None, min_notional=None, limit=50, diagnostics: Optional[Dict[str,
> Any]] = None)` — populates `diagnostics["symbols_fetch_failed"]` (`List[str]`,
> symbols whose live chain fetch failed) and `diagnostics["read_from_cache"]`
> (`bool`, whether the response was served entirely from the persisted cache
> with no live fetch attempted) on the live-scan path. Return type/behavior
> otherwise unchanged (`List[Dict[str, Any]]`).

Since this function does not carry the `diagnostics` kwarg on this branch,
`tests/test_pilots_paper_broker.py`'s new endpoint-level tests mock
`pilots.unusual_options_flow.get_unusual_options_activity` directly with a
`side_effect` function that mutates the `diagnostics` dict it receives,
simulating the real implementation's contract — the same idiom already used
elsewhere in that file (e.g. `@patch("pilots.earnings_crush.get_earnings_crush_candidates")`).

### Webapp

`webapp/src/api/types.ts`:
- `EarningsCrushCandidatesResponse` gained `degraded?: boolean;
  symbols_errored?: string[];`
- `UnusualOptionsFlowResponse` gained `degraded?: boolean;
  symbols_fetch_failed?: string[];`

No `mock.ts` changes were required — both new fields are optional, and every
existing mock fixture remains a valid (if slightly less informative) response.

### Tests

`tests/test_earnings_crush.py::TestEarningsCrushDiagnostics` (5 new tests):
`test_diagnostics_none_default_is_purely_additive`, `test_diagnostics_happy_path`,
`test_diagnostics_store_unavailable_when_construction_fails` (patches
`data.historical_store.HistoricalStore` to raise, confirming `store_available`
is `False` only when construction genuinely fails, not merely when `store=None`
was passed), `test_diagnostics_records_per_symbol_error` (a malformed
spot-price override for one of two symbols lands only that symbol in
`symbols_errored`, the healthy symbol still evaluates normally),
`test_get_earnings_crush_candidates_forwards_diagnostics`.

`tests/test_pilots_paper_broker.py` (4 new tests, added to the existing
`TestEarningsCrushEndpoints`/`TestUnusualFlowEndpoints` classes):
`test_get_earnings_crush_candidates_degraded_true_when_store_unavailable`,
`test_get_earnings_crush_candidates_degraded_false_when_healthy`,
`test_get_unusual_flow_degraded_true_on_fetch_failure`,
`test_get_unusual_flow_degraded_false_when_served_from_cache`.

### Verification

```
pytest tests/test_earnings_crush.py -q          # 30 passed
pytest tests/test_pilots_paper_broker.py -q     # 183 passed (full file, no regressions)
npm run --prefix webapp typecheck               # clean
```

---

## Finding #8 (implemented on `unusual-options-flow-engine-fixes`): atomic write for `save_uoa_records`

**Status: fixed on a sibling branch (not verified from this branch).**

`pilots/unusual_options_flow.py`'s `save_uoa_records` persistence path was not
atomic (a direct write rather than a temp-file-then-rename), so a process kill
or crash mid-write could leave a truncated/corrupt cache file behind — the
same failure mode this codebase has fixed at several other sites (e.g.
`execution/kill_switch.py`, `desktop/orchestrator_daemon.py::_write_daemon_file`,
`reporting/state_snapshot.py`, `runtime_flags_writer.py`; see CLAUDE.md's
"Shutdown-budget fix" bullet for the `state_snapshot.json` instance of this
exact bug class). The sibling branch fixes `save_uoa_records` to use the same
write-then-`os.replace` idiom.

---

## Finding #9 (implemented on `earnings-crush-followup-historical-moves-net-credit`): `net_credit` missing from `execute_earnings_crush_trade`'s success branch

**Status: fixed on a separate branch (not verified from this branch).**

`pilots/earnings_crush.py::execute_earnings_crush_trade`'s success-branch
response dict omitted `net_credit` even though the caller (and the webapp's
`EarningsCrushExecutionResult` contract, which declares `net_credit: number`
as required, not optional) expects it — the field was only ever populated
inside `details`, one layer down, not at the top level the frontend actually
reads. The stacked branch threads the real executed net credit up to the
top-level response.

---

## Cross-cutting notes

- All eight findings are additive/fix-only changes to already-dead-letter-safe
  (CONSTRAINT #6) modules; none change the underlying quantitative logic
  (Crush Edge Ratio, Expected Move, V/OI ratio, notional sizing) that
  `docs/signals/earnings_crush.md` and `docs/signals/options_flow_sentiment.md`
  describe.
- Finding #7 is the only finding whose implementation spans two branches by
  design (the `pilots/earnings_crush.py`/`api/pilots_api.py` half here, the
  `pilots/unusual_options_flow.py` half on the sibling branch) — the two
  halves share an identical `degraded`/`diagnostics` shape by construction
  (this doc's Fix section specifies the exact contract both sides implement
  against) so the merged PR's webapp consumers can treat both endpoints
  uniformly.
- See [`docs/signals/options_flow_sentiment.md`](../signals/options_flow_sentiment.md)'s
  own "Defects found while analysing `pilots/unusual_options_flow.py`" section
  for a second, shorter summary of findings #3–#8 from the UOA-signal-consumer
  side.
