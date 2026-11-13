# Forecasting rebuild F6 (part b) — walkthrough: the Forecasts screen

Plan: `.claude/forecasting_rebuild_f5_f6_implementation_plan.md` §5 (screen + webapp bullets). Part a (read contract,
`GET /forecasts`, `GET /symbols/{t}/forecast-summary`) merged as #1144; its walkthrough is
`.claude/forecasting_rebuild_f6_walkthrough.md`. Task tracker: `.claude/forecasting_rebuild_f6b_task.md`.
Operator approved building this screen on 2026-11-13. **No backend change**: the live check found no field mismatch.

## What changed

1. **API layer** (`webapp/src/api/`)
   - `types.ts`: the two F6a response shapes, every field explicit, `| null` wherever `pilots/forecasts.py` can emit
     null (forecast, band, `is_fallback`, `gated_naive`, `z`/`err`/`err_n`, `alignment_points`, both tiers,
     `advisory_effects`, `sell_hi_from_forecast`, every skill statistic, `last_decision`, `evaluation`,
     coverage `empirical`/`nominal`). `ForecastResult` (the refit shape) is removed.
   - `client.ts`: `getForecasts()` and `getForecastSummary(t)` with explicit `http<T>` generics.
     `getForecastResult` (`GET /metrics/forecast/{symbol}`, a per-request `ForecastingEngine()` refit) is removed;
     the backend route is untouched and still refits (F4 part 2b iii).
   - `mock.ts`: `mockForecastsView()` / `mockForecastSummary(t)` reuse the backend's verbatim reason strings
     (`z_reason`, `tier_note`, `evaluation_note`, `no completed forecasts in window`,
     `insufficient_history (n=0 < min_obs=5)`, `ledger predates F3 (no gated_blend rows yet)`, `error`,
     `forecast ledger unavailable`, `No matured forecast history for this symbol yet.`). One branch per symbol:

     | Symbol | `/forecasts` row | `/forecast-summary` |
     |---|---|---|
     | AAPL | all horizons; +6 pts moved BUY vs HOLD; bullish boost; sell-zone top from the forecast | gate admitted arima + holt_winters, rejected blend / cnn_lstm ("too few scored pairs") / monte_carlo; 90d has no matured outcomes |
     | MSFT | 30d/60d `gated_naive` (forecast = price); `advisory_effects: []`; sell top from ATR | gate falls back to naive, all rejected |
     | JNJ | 90d fallback with no band; points not recorded; no advisory recommendation (null); "Sell Now" | pre-F3 ledger (no recorded decision) |
     | AGNC | 60d/90d not recorded (null flags too); Case A | ledger read error everywhere |
     | other | not in the snapshot | cold start (live shape) |
     | ZZZZ | not in the snapshot | `_empty_summary`: `horizons: {}`, "forecast ledger unavailable" |

     The empty `/forecasts` (no snapshot) branch is exercised by a test spy and by the live cold-start backend.

2. **Screen** (`webapp/src/screens/ForecastViewer.tsx`, rewritten in place; route `/forecast` kept)
   - Universe table: published forecast, % vs price, band (cell tooltip), `naive` / `fallback` badges,
     forecast pts, tier and (tier without forecast). Inside an `overflowX: auto` wrapper, so the page never scrolls
     sideways at 390px.
   - Gate line from `view.gate` (on vs shadow, min improvement, min pairs, window) and a z line ("unavailable" +
     the first `z_reason`, or "not reported by the backend" when there are no rows). No threshold literal.
   - Symbol panel (`?symbol=`, row click or `SymbolInput`; defaults to the first row): horizon tiles with
     "z unavailable · E — · n —"; `ForecastCandleChart` over `GET /data/bars`; "What the forecast changed this cycle"
     (`ADVISORY_EFFECT_LABELS`; null = no recommendation recorded, [] = none fired; sell-zone true/false/null each
     worded); ledger card per horizon (published model, last recorded decision, evaluation now with each rejected
     model's reason, skill-vs-naive table, MC coverage). A symbol not in the snapshot gets an info notice, never a
     refit. Gate wording does not claim "published" for a fallback, since in shadow mode it was only recorded.
   - Kept: the 1M/3M/6M/1Y lookback toggle and a CSV export (empty cells for nulls). Dropped: the +/-1.5%
     BULLISH/NEUTRAL/BEARISH tile, the "Monte Carlo Range Band ±x%" tile, the ARIMA/MC_Lower/MC_Upper tiles and
     the JSON export (their source was the refit).
3. **Links and labels**: Symbol Detail "Forecast skill" card -> `/forecast?symbol=X`; `ModelHealthPanel` ->
   `/forecast` (no symbol) or `/forecast?symbol=X`. "Forecast Viewer" -> "Forecasts" in `navigation.tsx`,
   `ResearchHub.tsx`, `CommandPaletteModal.tsx`, Marketplace "Explore". No `NAV_ITEMS` slot moved.
4. **Help**: `TAB_HELP.forecast` rewritten; glossary `naive gate` (function of `Thresholds`:
   `forecast_gate_min_improvement`, `forecast_gate_min_obs`; "—" when thresholds are null), `skill vs naive`,
   `forecast z`. `shared/help_content.py` GLOSSARY `naive gate` reads `settings.FORECAST_NAIVE_GATE_*` and
   `FORECAST_SKILL_WINDOW_DAYS` at read time, `guide_anchor="#the-forecasts-screen-web-app"`, a new
   `docs/HOW_TO_GUIDE.md` section under §6.

## Verification
See the PR body for the command output. Summary: typecheck clean; full vitest suite green; production build OK;
`make lint` clean; `tests/test_help_content.py` + `tests/test_pilots_forecasts.py` and the other doc/help tests green.

Browser (Playwright/Chromium, 390x844 and 1280x900; screenshots in the session scratchpad `f6b_agent/shots/`):
- **Mock** (`npm run dev`): `/forecast`, `?symbol=MSFT|JNJ|AGNC|ZZZZ|TSLA`, `/symbol/AAPL`: 0 console errors,
  0 failed requests, no horizontal scroll at either width; every branch above seen rendered.
- **Live, cold start** (`uvicorn api.pilots_api:app --port 8602` + `api.data_api:app --port 8603`,
  `VITE_USE_MOCK=false`): `/forecasts` 200 with `rows: []` + reason -> "No published forecasts"; the
  sandbox ledger has real `gated_blend` rows, so AAPL's ledger showed a recorded fallback decision, empty skill
  ("no completed forecasts in window") and unavailable coverage. Only failed request: `:8601/jobs` (the daemon's
  Control API is not running here; unrelated). `/symbol/AAPL` 404 is the honest no-snapshot state.
- **Live, fixture snapshot**: the sandbox has no pipeline output, so a hand-built TEST snapshot in the
  orchestrator's key shape (scratch `make_fixture_snapshot.py`, served via `OUTPUT_DIR` pointing at scratch) was
  used to get populated rows through the real `pilots/forecasts.py`. The JSON key sets of rows, horizon cells and
  impact match `types.ts` and the mock exactly; the screen rendered all four branches. This proves field shapes
  and degraded states, not real forecast values.
- Links: `/agentic`'s Model Health link -> `/forecast`, Symbol Detail link -> `/forecast?symbol=AAPL` (mock and live).
- The backend's default CORS list allows only :5173; the live dev server ran on another port, so the uvicorn
  processes were started with `CORS_ALLOWED_ORIGINS` set in the shell (no `.env` change).

## Known limits
- Mock bars come from the generic `getDataBars` mock, so a mock symbol's chart price level can differ from its
  `/forecasts` price (most visible for AGNC at ~$9). Mock-only; live bars and the snapshot share a source.
- "Tier without the forecast" is approximate (label and note come from the backend).
- z/E/n stay "unavailable" until F5-1.
