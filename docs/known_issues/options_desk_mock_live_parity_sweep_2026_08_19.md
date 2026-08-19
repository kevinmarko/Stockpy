# Known issue (2026-08-19): Options desk mock/live API parity gap was systemic, not isolated — 21 more crash/fabrication bugs found and fixed across nearly every Paper Broker sub-panel

**Status: fixed.** Branch `claude/paper-broker-screens-errors-3650c6`.

## What happened

An operator reported the Pilots PWA's Paper Broker screen still throwing
`Cannot read properties of null (reading 'toFixed')` after
[`scenario_matrix_field_mismatch.md`](scenario_matrix_field_mismatch.md) (PR #808)
had already fixed the identical bug class in `ScenarioHeatmap.tsx`. That earlier
write-up explicitly flagged this as a *systemic* gap — the same "backend's raw
internal shape never matched what the frontend/`mock.ts` expect" pattern CLAUDE.md's
"Options desk mock/live API parity" bullet already named for 8 other screens — and
said plainly that every sub-panel *besides* `ScenarioHeatmap` "remain[ed] unfixed as
of this write-up."

This turned out to be an undercount. A systematic sweep of the entire Paper Broker
screen dependency tree (every sub-component `PaperBroker.tsx` mounts, toggled or
not, plus the separate-but-adjacent Options Chain screen) found **21 more real
bugs** across **13 backend modules and 10 frontend files** — not a handful of
strays, but the dominant failure mode of this entire subsystem. Every one of the
16 modules shipped in the "Options desk Phases 19-30/31-36" build-out (see
CLAUDE.md) that returns its own bespoke internal dict shape had at least one
field-name, nesting, or nullability mismatch against what the frontend/`mock.ts`
actually declare.

## Root cause (same as PR #808, confirmed to generalize)

Every one of these Python modules (`pilots/dispersion_trading.py`,
`pilots/zero_dte_engine.py`, `pilots/options_vpin.py`, `pilots/options_sor.py`,
`pilots/options_gex.py`, `pilots/lob_simulator.py`, `pilots/volatility_surface.py`,
`pilots/earnings_crush.py`, `pilots/unusual_options_flow.py`,
`pilots/har_volatility.py`, `pilots/vol_mispricing.py`, `pilots/gamma_scalper.py`,
`validation/autonomous_backtest_runner.py`, `execution/multi_broker_gateway.py`,
`execution/sec_rule_606_reporter.py`, `ml/drl_market_maker.py`) was built and
tested (`tests/test_<module>.py`) against its own natural internal return shape —
nested dicts, snake_case-but-differently-named keys, `Optional[float]` fields that
are genuinely `None` on real degenerate/no-data inputs. Nobody reshaped that
internal contract at the `api/pilots_api.py` boundary to match what
`webapp/src/api/types.ts` declares and what the corresponding React component
actually reads. `webapp/src/api/mock.ts`'s fixtures were written against the
*frontend's* contract (the correct one), so **mock mode (the local-dev default)
never exercised the mismatch** — every one of these bugs was invisible until
someone pointed the PWA at a live backend, exactly like the ScenarioHeatmap
incident.

Two failure shapes recur throughout:

1. **Crash**: a component calls `.toFixed()`/`.length`/`.map()` on a field the
   real backend never populates under that name, or that the backend can
   legitimately return `null` for (missing quote, degenerate math guard, NaN from
   an AST-compile failure, "no legs" fallback, etc.) while `types.ts` claimed
   non-null.
2. **Silent fabrication** (CONSTRAINT #4 violation, no crash): a falsy-not-nullish
   check (`field || fallback`) or an implicit `null * 100 → 0` coercion displays a
   plausible-looking wrong number instead of an honest "—" — arguably worse than a
   crash, since nothing signals the value is fake. Examples found: a hardcoded
   "20%" flow-share shown for every FIX venue, "ALPACA ACTIVE" shown when *no*
   broker was actually routable, a 0.0% IV shown for a contract with no resolvable
   implied vol, and a `MarketMakerAgentView` fill-rate that was silently 100x too
   small (backend emits a 0–1 fraction, the UI treated it as a 0–100 percentage).

## The fix pattern (matches PR #808's `to_scenario_matrix_response()` precedent)

For every backend mismatch, a dedicated `to_<x>_response()` / `<x>_for_frontend()`
adapter function was added in the owning `pilots/*.py` module, applied **only** at
the point `api/pilots_api.py`'s endpoint handler calls it — the underlying pure
function and its existing `tests/test_<module>.py` suite are untouched. This keeps
the module's own natural, testable internal contract intact while giving the one
FastAPI route the exact shape the frontend needs. Where a value is genuinely
sometimes unavailable, `types.ts` was widened to `| null` and the component
updated to render `"—"` (this codebase's established honest-null convention) —
never a fabricated zero.

## Full list of bugs found and fixed

| # | File(s) | Bug | Fix |
|---|---|---|---|
| 1 | `PaperBroker.tsx` Greeks panel + `pilots/options_risk.py` | `position_delta`/`_theta_daily`/`_vega_1pct` are `None` when a position's quote can't be resolved; unconditionally-mounted panel — **this is the exact bug the operator's screenshot showed** | Null-guarded all three render sites; widened `PositionGreekBreakdown` fields to `number \| null` |
| 2 | `PaperBroker.tsx` + `pilots/options_hedging.py` | `DeltaHedgePreview` type invented fields (`spy_spot_price`, `net_delta_shares`, `required_hedge_shares`, an `action: "NONE"` the backend never sends) that don't exist in the real `GET /pilots/paper-broker/delta-hedge/preview` response at all | Rewrote the type to the real shape (`spy_spot`, `shares`, `required_action`, `action: "HOLD"\|"BUY"\|"SELL"`); `estimated_cost` now honestly derived client-side from two real fields |
| 3 | `PaperBroker.tsx` + `pilots/options_hedging.py` | `DeltaHedgeResult` (execute-hedge response) type invented a top-level `price`/`side` the backend never returns | Rewrote to match `execute_delta_hedge()`'s real, partly-optional shape |
| 4 | `PaperBroker.tsx` positions/orders table | `current_price`/`market_value`/`unrealized_pl`/`filled_avg_price` rendered with `field ? … : "—"` — a legitimate `$0.00` (worthless expired option, breakeven fill) silently displayed as `"—"` | Changed to `field != null ? … : "—"` |
| 5 | `VolSurfaceView.tsx` + `pilots/volatility_surface.py` | Backend's `smiles`(dict)/`term_structure`(object)/`skew_summary` never matched the frontend's flat `smile_points`/`skew` — crash; `expiration` query param was never wired through | Added `to_vol_surface_response()`; wired the param |
| 6 | `EarningsCrushScanner.tsx` + `pilots/earnings_crush.py` | Backend used `spot`/`earnings_date`/`expected_move_usd`/`strategy`/nested `strikes.*`; frontend reads `spot_price`/`report_date`/`expected_move_dollar`/`suggested_strategy`/flat wing-strike fields — crash | Added `to_earnings_crush_candidate_response()` |
| 7 | `UnusualFlowFeed.tsx` + `pilots/unusual_options_flow.py` | Backend key `call_put_ratio` vs. frontend's `put_call_ratio` — **reciprocals, not a rename**; `symbols=` vs. real client's `symbol=` made the ticker filter a silent no-op; `top_active_strikes[]` missing `option_type`/`notional` | Added `to_flow_sentiment_response()` deriving the ratio honestly from real buy/sell volume (not inverting the lossy sentinel); added the `symbol` param; computed the missing per-strike fields from real aggregates |
| 8 | `VolForecastScanner.tsx` + `pilots/har_volatility.py`, `pilots/vol_mispricing.py` | HAR-RV: backend's `model_fit`/daily-variance forecast never matched `forecast.coefficients.*` — crash. Mispricing: backend's `valuation_tag`/`spread` vs. frontend's `classification`/`iv_spread` silently broke the Rich/Cheap filter buttons (zero results forever, no crash); frontend's `classification` union even declared a fictional `"FAIR"` value the backend never emits | Added `to_har_rv_forecast_response()` (also annualizes variance→vol, fixing a ~100x display error) and `to_vol_mispricing_response()`; corrected the union to the real `RICH\|CHEAP\|NEUTRAL\|UNKNOWN` |
| 9 | `GammaScalperView.tsx` + `pilots/gamma_scalper.py` | Pydantic request model only accepted `position`/`price_path` — every operator-configured sim setting (`symbol`, `strike`, `iv`, `contracts`, …) was silently dropped; response had no `pnl_path` key at all, and this panel **auto-runs a simulation on mount** — crashed on every open | Extended the request model; added `to_gamma_scalp_response()` |
| 10 | `DispersionScanner.tsx` + `pilots/dispersion_trading.py` | Backend's nested `basket`, snake_case `regime`, no top-level `index_spot`/`index_iv`/`constituents` — crash | Added `_opportunity_to_frontend_card()` |
| 11 | `ZeroDteDesk.tsx` + `pilots/zero_dte_engine.py` | (a) frontend called the endpoint with no `symbol` against a backend that requires one → guaranteed 422, desk permanently empty; (b) backend's single flat dict never matched the frontend's `{signals: [...]}` card shape → crash; a fabricated `"0.080"` gamma fallback was also removed | Passed the selected symbol; added `get_0dte_signals_for_frontend()` |
| 12 | `VpinGauge.tsx` + `pilots/options_vpin.py` | Backend's `toxicity_regime`/`recommended_spread_concession`/per-bucket `volume`/`order_imbalance` (no `price_start`/`price_end` at all) vs. frontend's `regime`/`defensive_spread_concession`/`total_volume`/`imbalance`/`price_start`/`price_end` — crashed on hovering any volume bucket; a fabricated `?? 50` percentile fallback was also removed | Added real `price_start`/`price_end` fields to the internal `VPINBucket` dataclass (values were already computed, just discarded) + `get_options_vpin_metrics_for_frontend()` |
| 13 | `SmartOrderRouterView.tsx` + `pilots/options_sor.py` | Backend's nested `cob_pricing`/`synthetic_legging`, lowercase leg `action`/`option_type` vs. frontend's flat `cob_net_price`/uppercase legs — **crashed on the panel's first successful render**, not just on interaction; `sim.latency_curve` didn't exist on the backend at all; a real symbol-derivation bug (`"LEG_1..."` placeholder) was also fixed | Added `analyze_routing_options_for_frontend()` and `simulate_legging_execution_for_frontend()` (the latter builds a real, not fabricated, `pnl_distribution` histogram and `latency_curve` sweep) |
| 14 | `GexProfileView.tsx` + `pilots/options_gex.py` | `zero_gamma_flip`/`call_wall_strike`/`put_wall_strike` are legitimately `Optional[float]` (no root found for the flip point, no calls/puts with positive GEX) but `types.ts` declared them non-null — **another exact match for the reported crash**, reproducible whenever a live quote fails to resolve | Widened the three fields to nullable; null-guarded all four render sites |
| 15 | `LobDepthView.tsx` + `pilots/lob_simulator.py` | `expected_wait_time_sec` is `None` whenever zero simulated paths fill within the horizon (realistic, easily reachable: `theta_market=0`, `mu_cancel=0`) but was declared non-null — **another exact match for the reported crash** | Widened the type; added the guard |
| 16 | `MarketMakerAgentView.tsx` + `ml/drl_market_maker.py` | `fill_rate` is a 0–1 fraction from the backend; `mock.ts` and the component both treated it as a 0–100 percentage — mock mode looked right (both sides agreed on the wrong convention), live mode would silently show a real 30% fill rate as "0.3%" | Component now multiplies by 100 at render; `mock.ts` emits the real fraction; documented the unit convention on the field |
| 17 | `ResearchCopilotView.tsx` + `validation/autonomous_backtest_runner.py` | 11 metrics (`sharpe_ratio`, `sortino_ratio`, `pbo`, `dsr`, …) go `null` on a real AST-compile-failure path (explicit) and on ordinary degenerate-math paths (zero downside deviation, zero max drawdown — not rare) but were declared non-null — crashed on some (`.toFixed()`), silently fabricated "0.0%" on others | Widened all 11 fields to nullable; guarded every render site |
| 18 | `MultiBrokerGatewayView.tsx` + `execution/multi_broker_gateway.py` | `active_broker_id` is genuinely `None` when `NoHealthyBrokerError` is raised (no broker routable at all) — the component did `status?.active_broker_id \|\| "alpaca"`, **fabricating "ALPACA ACTIVE" in the worst-case state this screen exists to catch** | Widened the type to nullable; shows "NONE ACTIVE" in red instead |
| 19 | `SecRule606ReportView.tsx` + `execution/sec_rule_606_reporter.py` | `venues_overall` (the **default** tab) uses `total_orders`/`total_shares`; `by_category` uses `order_count`/`executed_shares` and omits `pct_of_total_shares` entirely — **crashed on the screen's default view**, and crashed differently on every category tab | Widened the row type to include both raw variants as optional; added a `normalizeVenueRow()` step computing the missing percentage from the period total (a real value, not "—") |
| 20 | `FixGatewayStatusRadar.tsx` + `api/pilots_api.py` | `fill_rate_pct`/`share_of_flow_pct` are always `null` (no per-venue execution history exists) but the frontend declared `fill_rate_pct` required, rendered a bare `"%"`, and fell back to a **hardcoded "20%"** flow share for every venue | Made the field optional; shows "—" for both instead of a blank "%" or a fabricated fixed number |
| 21 | `OptionsChain.tsx`/`OptionsOrderTicket.tsx` + `api/data_api.py` | `impliedVolatility` can legitimately be `null` (yfinance's IV solver fails to converge on thin/illiquid contracts) — `c.impliedVolatility * 100` silently fabricated "0.0%"; separately, `spot_price` is absent entirely from the "list expirations" response shape (only present once a specific expiration + live quote resolve) — `(chainData?.spot_price \|\| 0).toFixed(2)` fabricated a "$0.00" share price during the real transient window before the first expiration auto-selects | Widened both fields to optional/nullable; render "—" instead of a fabricated `0.0%`/`$0.00` |

Also audited with **no bugs found** (already null-safe, or every backend field is
structurally guaranteed non-null by construction): `SettingsPaperBroker.tsx`,
`OptionsMatrix.tsx`, `Portfolio.tsx`, `Dashboard.tsx`, `OptionsMetricSelector.tsx`,
`OptionsPayoffChart.tsx`, `OptionsStrategyBuilder.tsx`, `CopulaSpreadView.tsx`,
`HrpPortfolioOptimizerView.tsx`, `AlmgrenChrissRouterView.tsx`, plus a repo-wide
read-only sweep of every other screen (`Attribution`, `Calibration`, `Pilots`,
`Compare`, `StrategyHealth`, `SentimentDynamics`, `Console`, `Observability`, and
~20 more) turned up nothing further. `RealTimeRiskRadar.tsx` was confirmed
unreachable dead code (imported only by its own test file) — not deleted, out of
scope for this fix.

## A second-order bug this fix itself introduced and caught before merge

The concurrent multi-agent sweep that produced fixes #10–#13 above rewrote 5 of
`tests/test_pilots_paper_broker.py`'s endpoint tests to match the corrected
Dispersion/ZeroDte/Vpin/SOR contracts, but **7 test functions across those same 4
endpoint classes were missed** (still asserting the old, pre-fix raw-backend
shape — `"basket" in opp`, `body["toxicity_regime"]`, `body["valid"]`, etc.) and
started failing the moment the corresponding adapter shipped. Caught by re-running
the full targeted test file after every parallel fix landed, rather than trusting
each fix's own self-reported "tests pass" (which had gone stale relative to a
sibling agent's concurrent edit to the same shared file) — a live lesson in why
CLAUDE.md's verification rule is "actually run and shown to pass," not "should
pass." All 7 were corrected against the real, ground-truth response bodies
(captured by driving the actual endpoints through `TestClient`, not
re-derived from reading the adapter source).

## Verification

- `npm run --prefix webapp -s typecheck`: clean, zero errors, across the whole
  webapp (not just the touched files).
- Backend: `pytest tests/test_dispersion_trading.py tests/test_zero_dte_engine.py
  tests/test_options_vpin.py tests/test_options_sor.py tests/test_options_gex.py
  tests/test_lob_simulator.py tests/test_volatility_surface.py
  tests/test_har_volatility.py tests/test_vol_mispricing.py
  tests/test_earnings_crush.py tests/test_unusual_options_flow.py
  tests/test_gamma_scalper.py tests/test_pilots_paper_broker.py
  tests/test_pilots_api.py` — **835 passed, 0 failed**.
- Frontend: the 21 corresponding `*.test.tsx` suites — **113 passed, 0 failed**.
- **Live browser verification against a real (non-mock) backend** — the class of
  check that actually would have caught the original bug, since mock mode never
  exercises it. Ran `api.pilots_api:app` for real (no `FMP_API_KEY` configured, so
  quotes and Greeks that depend on it genuinely resolve to `None` — a realistic
  reproduction of the exact condition that trips bug #1) against the built webapp
  with `VITE_USE_MOCK=false`. Loaded the Paper Broker screen fresh (previously:
  guaranteed "Something went wrong" on first load) and it rendered completely —
  Positions, Orders, Greeks panel, Delta Hedge, Scenario Matrix, ML Meta-Labeler,
  Backtest Harness — then individually opened GEX Profile, LOB Depth, VPIN
  Toxicity, Smart Order Router, Volatility Surface, and Dispersion (six of the
  highest-risk panels above, including both exact matches for the reported crash
  message). Every one rendered its real data, with genuinely-unavailable fields
  (Zero-Gamma Flip Level, 25Δ skew's Realized Vol, VPIN percentile, Dispersion's
  30D Realized) showing an honest "—" instead of crashing or fabricating a number.
  Zero console errors, zero "Something went wrong" boundary trips.
