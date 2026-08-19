# Known issue (2026-08-19): Paper Broker screen crashed on live backend — `ScenarioHeatmap` and `pilots/scenario_matrix.py` disagreed on almost every field name

**Status: fixed.** [PR #808](https://github.com/kevinmarko/Stockpy/pull/808)
(branch `claude/error-investigation-6b723d`).

## What happened

`ScenarioHeatmap.tsx` renders unconditionally on the Pilots PWA's Paper
Broker screen — it is not gated behind any toggle button, unlike most of the
screen's other options-desk sub-panels — and calls `api.getScenarioMatrix()`
→ `POST /pilots/paper-broker/scenario-matrix` on every visit.

The frontend's declared `ScenarioMatrixResponse` contract
(`webapp/src/api/types.ts`), and `webapp/src/api/mock.ts`'s fake
implementation of it, use key names the real backend never produced:

| Frontend expects (`types.ts` / `mock.ts`) | Real backend produced (`pilots/scenario_matrix.py`) |
|---|---|
| `matrix` (list of cells) | `grid` (list of cells) |
| `time_slices` | `time_shifts_days` |
| `historical_scenarios` (a **list**) | `historical_presets` (a **dict**, keyed by preset id) |
| `current_portfolio_value` | `baseline.portfolio_market_value` (nested) |
| per-cell `spot_shift_pct` | per-cell `spot_shift` |
| per-cell `iv_shift_pct` | per-cell `iv_shift` |
| per-cell `days_forward` | per-cell `time_shift_days` |
| per-cell `portfolio_value` | per-cell `portfolio_market_value` |
| per-cell `pnl_dollar` | per-cell `pnl_shift` |
| per-cell `net_theta` | per-cell `net_theta_daily` |
| per-cell `net_vega` | per-cell `net_vega_1pct` |

Only `spot_shifts`, `iv_shifts`, `pnl_pct`, `net_delta`, and `net_gamma`
happened to already match by coincidence.

On any live (non-mock) load, `matrixData.matrix` was therefore `undefined`,
and `ScenarioHeatmap.tsx`'s `matrix.filter((c) => c.days_forward ===
selectedTimeSlice)` threw `Cannot read properties of undefined (reading
'filter')`. React's error boundary caught this, replacing the **entire**
Paper Broker screen — not just the scenario-matrix panel — with a generic
"Something went wrong" page, since `ScenarioHeatmap` is one of the few
Paper Broker subcomponents mounted unconditionally rather than behind a
toggle.

A second, independent bug lived in the same wrapper function:
`ScenarioMatrixRequest.time_days_forward` defaulted to `0` (not `None`),
and `api/pilots_api.py`'s route always passed a concrete int through to
`evaluate_portfolio_scenario_matrix(time_days_forward=...)`. That collapsed
every live request to a single T+0 time slice instead of the intended
4-slice default grid (`[0, 7, 14, 21]`) — invisible on top of the crash
above, but would have been a second, separate defect once the crash was
fixed.

This is the **same systemic "options desk mock/live API parity" gap**
CLAUDE.md already documents (see the "Options desk mock/live API parity"
bullet) for `GexProfileView.tsx`, `LobDepthView.tsx`, `CopulaSpreadView.tsx`,
`MarketMakerAgentView.tsx`, `TransformerVolForecastView.tsx`,
`GenerativeDiffusionStressView.tsx`, `MultiBrokerGatewayView.tsx`, and
`ResearchCopilotView.tsx`. `ScenarioHeatmap.tsx` was **not** one of the
components that bullet already named, and it is worse than the named ones:
those are individually gated behind their own toggle buttons, so a
field-mismatch crash in one of them breaks only that panel. `ScenarioHeatmap`
is mounted unconditionally, so its crash took down the whole screen. This
fix covers only `ScenarioHeatmap` / `pilots/scenario_matrix.py` — the other
components named in that CLAUDE.md bullet remain unfixed as of this
write-up.

## Real impact

Any operator running the webapp against a real backend (`VITE_USE_MOCK=false`)
hit this on **every single visit** to the Paper Broker screen — not an edge
case, not conditional on portfolio contents or any particular options
position. Mock mode (`VITE_USE_MOCK=true`, the default for local
development) never exhibited the bug, since `mock.ts`'s fake
`getScenarioMatrix()` already returns the shape the frontend expects — which
is exactly why this went unnoticed until someone pointed the PWA at a live
backend.

## How it was discovered

User report with a screenshot of the React error boundary's "Something went
wrong" page on the Paper Broker screen. Root-caused by first reproducing the
mock path cleanly (zero errors across all 20 Paper Broker sub-panels,
confirming the bug was live-backend-specific), then reading the real
endpoint's code (`pilots/scenario_matrix.py::evaluate_scenario_matrix()`,
reached via `evaluate_portfolio_scenario_matrix()`) side-by-side with the
frontend's declared `ScenarioMatrixResponse`/`ScenarioMatrixCell` contract in
`webapp/src/api/types.ts`, and finding the field-name mismatch was nearly
total.

## The fix

`evaluate_scenario_matrix()` itself — the pure math function every existing
test in `tests/test_scenario_matrix.py` asserts on (`grid`,
`time_shifts_days`, `historical_presets` as a dict, nested `baseline`, etc.)
— was left completely untouched, so none of those ~10 existing tests needed
to change. It gained one small additive field, `"spot_map": dict(spot_map)`
(ticker → resolved live spot price used for the evaluation), so the reshape
step below doesn't need to re-resolve quotes a second time.

A new adapter, `to_scenario_matrix_response()`, renames `evaluate_scenario_matrix()`'s
internal result into the `ScenarioMatrixResponse` shape the webapp actually
consumes — `matrix`/`time_slices`/`historical_scenarios` (as a list)/
`current_portfolio_value`, with the per-cell field renames from the table
above. `evaluate_portfolio_scenario_matrix()` — the one function
`POST /pilots/paper-broker/scenario-matrix` actually calls — now calls
`evaluate_scenario_matrix()` and pipes the result through
`to_scenario_matrix_response()` before returning.

Per-cell `spot_price` is handled honestly (CONSTRAINT #4 — never fabricate a
measurement): a scenario matrix can span multiple underlying tickers (equity
+ several option legs on different names) with no single "the portfolio's
spot price." `_resolve_reference_spot()` only derives a real per-cell
`spot_price` (the shocked spot re-derived from the single ticker's live
quote) when the book has exactly one distinct underlying ticker with a known
quote. A multi-ticker book, or an empty book, simply omits `spot_price` from
every cell rather than inventing a blended/average number that corresponds
to no real instrument.

The second bug — `time_days_forward` defaulting to `0` instead of `None` —
was fixed in the same change. `evaluate_portfolio_scenario_matrix()`'s
`time_days_forward` parameter changed from `int = 0` to `Optional[int] =
None`, distinguishing "caller didn't ask for a time dimension at all"
(`None` → falls through to the full `DEFAULT_TIME_SHIFTS_DAYS` grid) from
"caller explicitly wants only day 0" (`0` → collapses to a single T+0
slice). `api/pilots_api.py`'s `ScenarioMatrixRequest.time_days_forward` and
its route handler were updated to match — the route now passes
`body.time_days_forward if body else None` instead of unconditionally
substituting `0`.

New regression tests in `tests/test_scenario_matrix.py` cover: the
default-call-uses-full-grid behavior, the explicit-single-day-still-works
behavior, the full frontend-shape reshape (top-level keys, per-cell keys,
the honest single-ticker `spot_price` re-derivation), and the two
honest-omission cases (`spot_price` absent for a multi-ticker book, and for
an empty book).

## What is still open

The sibling components CLAUDE.md's "Options desk mock/live API parity"
bullet already named — `GexProfileView.tsx`, `LobDepthView.tsx`,
`CopulaSpreadView.tsx`, `MarketMakerAgentView.tsx`,
`TransformerVolForecastView.tsx`, `GenerativeDiffusionStressView.tsx`,
`MultiBrokerGatewayView.tsx`, `ResearchCopilotView.tsx` — are unaffected by
this fix and remain broken against a live backend.

Separately: the **request**-side param naming is also inconsistent between
frontend and backend. The frontend's `api.getScenarioMatrix(params?: {...,
days_forward})` type does not match the backend's
`ScenarioMatrixRequest.time_days_forward`/`.time_shifts` field names. This is
currently dead/inert — nothing in the webapp ever calls `getScenarioMatrix`
with any params today, so no live request is affected — but it is left
unfixed here and flagged as a latent landmine for whoever wires up
scenario-matrix filtering UI (e.g. a time-slice selector that posts a
specific `days_forward`) later. That future work should reconcile the
request-side names at the same time, not rediscover this mismatch the hard
way.

## Related

- CLAUDE.md's "Options desk mock/live API parity" bullet — the same
  systemic gap this issue is one confirmed instance of.
- `webapp/src/api/types.ts`'s `ScenarioMatrixResponse`/`ScenarioMatrixCell`/
  `HistoricalScenarioPreset` interfaces — the contract this fix now
  satisfies.
- `tests/test_scenario_matrix.py` — the pure-math tests (unchanged) plus the
  new wrapper/reshape regression tests this fix added.
