# Autopilot "Pilots" Makeover — Plan & Phasing

A consumer-style marketplace UX ("Pilots") layered over Stockpy's OWN quant strategies.
Until this doc, the phasing lived only in code comments and `CLAUDE.md` — this is the
first committed tracking doc for the makeover. Full dated backstory (PRs, test surface)
is in [`docs/FEATURE_TIER_HISTORY.md`](FEATURE_TIER_HISTORY.md).

## What it is

Repackage the platform's 17 signal modules and their honest backtests as copyable
**Pilots** for a mobile-first PWA: browse a marketplace, inspect a PBO/DSR-gated backtest,
and "Follow" a Pilot with a dollar amount. It is **advisory and paper-first** — "Follow"
only ever builds a gated, human-confirmed order queue; it never places an order
automatically.

## Load-bearing invariants (never regress)

- **Honesty (CONSTRAINT #4).** Never fabricate a curve, metric, or equity figure. A missing
  value stays `null`/`NaN` with an honest `reason`. The mock catalog ships two honest
  examples: `momentum-burst` (non-deployable) and `value-quality` (`curve: null`).
- **Broker quarantine.** No new order code. No `place_*`/`submit_order`/`*_order` symbol
  names (`tests/test_pipeline_smoke.py::TestNoOrderFunctions`). All real safety
  (`PreTradeRiskGate`, `GlobalKillSwitch`, `mode == "live"` gating, notional cap) is reused
  verbatim via `execution/queue_builder.py`.
- **Read-path purity.** The Pilots layer reads only already-persisted state — no
  heavy-engine imports on the read path. `api/pilots_api.py` is AST-guarded against
  importing the calculation engines.

## Phases (all shipped 2026-07-12/13)

| Phase | Scope | Modules | PRs |
|-------|-------|---------|-----|
| 1 | Catalog / scoring / performance / follows | `pilots/catalog.py`, `pilots/scoring.py`, `pilots/performance.py`, `pilots/follows_store.py` | #226 / #227 |
| 1 | Mobile-first PWA | `webapp/` (Vite + React + TS + Recharts + vite-plugin-pwa) | #226 / #227 |
| 2 | FastAPI service | `api/pilots_api.py` (port 8602) | #250 |
| 3 | Gated follow-mirror | `pilots/mirror.py` | #250 |
| — | Daemon hosting of the Pilots API (opt-in) | `desktop/orchestrator_daemon.py`, `settings.PILOTS_API_ENABLED` | #252 |
| — | Reconcile PWA ↔ `pilots_api` response shapes for live cutover | `webapp/src/api/types.ts`, `api/pilots_api.py` | #254 |
| — | Persist real benchmark comparison series | `validation/harness.py`, `pilots/performance.py` | #256 |
| — | Mirror force-exit of dropped names via per-follow attribution | `pilots/mirror.py`, `pilots/follows_store.py` | #257 |
| — | Symbol detail pages (`/symbol/:ticker`) — per-symbol snapshot view + the reverse "which Pilots hold this" cross-link; tappable holding/position rows | `pilots/symbols.py`, `api/pilots_api.py` (`GET /symbols/{ticker}`), `webapp/src/screens/SymbolDetail.tsx` | #270 |
| — | Finish SymbolDetail data — advisory-path snapshot parity (xsec_12_1m, xsec_momentum_rank, macro_status, news_sentiment, CoVaR proxy, MFE/MAE/edge ratio/realized slippage now threaded onto `Recommendation.key_indicators` so the advisory writer matches the rich orchestrator writer) | `main.py` (`_build_context_extras`), `engine/advisory.py`, `reporting/state_snapshot.py` | #271 |
| — | Onboarding "Connect Robinhood" — local, single-operator credential intake (verify-before-persist, three independent gates: `BROKERAGE_CONNECT_ENABLED`, `FOLLOW_API_TOKEN`, loopback-only) | `data/brokerage_credentials.py`, `data/robinhood_portfolio.py` (`verify_credentials`), `api/pilots_api.py` (`/brokerage/status`, `/brokerage/connect`, `/brokerage/disconnect`), `webapp/src/screens/Onboarding.tsx` | #272 |
| — | Expand the catalog — new `edge-garch` Pilot (the highest-weighted genuinely per-symbol orphan signal not already covered by a Pilot) + honest backtests for 4 previously curve-less Pilots (`cross-sectional-momentum` price-only; `dividend-income`/`deep-value`/`value-quality` via a first-time production wiring of the existing SEC EDGAR point-in-time fundamentals mechanism). 8/10 Pilots now backed by a real validation report, up from 4/9. | `pilots/catalog.py`, `scripts/refresh_validations.py` (4 new adapters + `_pit_asof_frame` helper) | this PR |

> **Full parity achieved.** Every SymbolDetail field the rich orchestrator writer
> emits now has a real source on the advisory path too — including `risk.realized_slippage`,
> which turned out to have TWO producers under the same name in this codebase:
> `research_engine.calculate_realized_slippage(transactions_df)` (a portfolio-wide bps
> scalar over a `Trans Code`/`Amount`/`Commission` sheet neither path actually threads
> into the dashboard) and `evaluation_engine.EvaluationEngine.calculate_realized_slippage
> (entry_price, arrival_price)` (the REAL, per-symbol implementation-shortfall figure
> `evaluate_portfolio()` uses to populate `dashboard_df`'s `'Realized Slippage'` column on
> the rich path). The second one needs only a closed trade's entry price + the current
> close — both already fetched for the MFE/MAE/Edge Ratio excursion pre-compute — so it's
> wired from the SAME per-symbol closed-trade lookup, null until that symbol has a closed
> trade (honest by construction). MFE/MAE/Edge Ratio/Realized Slippage light up as trade
> history accrues; news_sentiment needs `FINNHUB_API_KEY`.

> **Catalog expansion — why `macro_regime` didn't get a Pilot.** Of the platform's two
> highest-weighted "orphan" signals (no dedicated Pilot, only riding inside `balanced-blend`),
> only `edge_garch` (weight 35) got one. `macro_regime` (weight 45, `signals/macro_regime.py`)
> was investigated and rejected: its only per-row input is `sector` — every stock sharing a
> sector gets the IDENTICAL score in a given macro regime, so a standalone Pilot built from it
> would recommend a whole sector's names with the exact same "reasoning," misrepresenting
> "why this stock" (against the spirit of honesty — CONSTRAINT #4). `edge_garch` IS genuinely
> per-symbol (real `edge_ratio` + `garch_vol` per ticker).
>
> **EDGAR PIT production wiring.** `dividend-income`/`deep-value`/`value-quality`'s backtests
> read real SEC EDGAR point-in-time fundamentals via `data.historical_store.HistoricalStore
> .get_fundamentals_history()` — read-only, never touching `data/edgar_fundamentals.py`,
> `data/historical_store.py`, or `scripts/backfill_edgar_fundamentals.py` (Gemini-owned per
> `docs/DATA_LAYER_PLAN.md`). That store is a pure DB reader with no live-EDGAR fallback: a
> fresh clone's `quant_platform.db` has zero EDGAR rows until an operator runs
> `python scripts/backfill_edgar_fundamentals.py --tickers AAPL,JNJ,XOM,KO,JPM,PG,INTC,T,GE,F`
> (a real, rate-limited SEC network operation, not run automatically by any pipeline). Until
> then these three backtests honestly degrade to NaN-shaped/no-position reports — never
> fabricated, never crash (tested explicitly against a genuinely empty store).
>
> **What stays curve-less, on purpose.** `balanced-blend` (17 signals, several needing
> FRED/Finnhub/a trained-ML walk-forward — out of scope per CONSTRAINT #7) and the new
> `edge-garch` (its live `edge_ratio` input depends on real closed-trade history — circular
> for a pure-price backtest, no honest proxy designed yet).

## Hardening (post-Phase-3) — the core ships; this is the "declared done too early" layer

The three phases + follow-ups above are merged and live. What remained after the
functional build is the hardening layer — the work that's easy to skip once the product
"works". Tracked here honestly so it doesn't silently rot.

| Item | Status | Where |
|------|--------|-------|
| Gravity audit for the follow-mirror (broker quarantine, D3 floor, off/review gating, honesty) | ✅ done | `Gravity AI Review Suite.py::step_92_pilots_mirror_quarantine_audit` |
| CI gate for `webapp/` (typecheck + build + vitest) | ✅ done | `.github/workflows/ci.yml`'s `webapp` job |
| PWA test surface beyond the single mock-contract test (screen + live-client tests) | ✅ done | `webapp/src/screens/{Marketplace,PilotDetail,FollowModal,Portfolio,Onboarding}.test.tsx`, `webapp/src/api/client.test.ts`, `webapp/src/format.test.ts`, `webapp/src/onboarding.test.ts` — every screen now has a test file; 100 tests total |
| Verified live cutover (run `pilots_api` + PWA against it, confirm shapes) | ✅ done | see below |

**Why step_92 matters most.** `pilots/mirror.py` is the only Pilot module that emits order
*intents*, yet it had zero Gravity coverage while every other order-adjacent subsystem
(steps 79/80/81) is audited. step_92 pins the broker-quarantine invariant (no
`place_*`/`submit_order`/`*_order` defs, execution reached only via
`execution/queue_builder.py`), Decision D3's `FOLLOW_MIN_CONVICTION == 0.0` floor, the
off/review `allow_place=False` gating, and the honesty/dead-letter contract
(`build_follow_intents` → `[]` on non-positive amount/equity, never raises).

### Live cutover verification (2026-07-14)

Ran `uvicorn api.pilots_api:app` for real (fresh SQLite/no `output/state_snapshot.json` — the
honest cold-start case) and the PWA with `VITE_USE_MOCK=false` against it, driving the full
Marketplace → Pilot Detail → Follow → Portfolio flow in a real browser. `CORS_ALLOWED_ORIGINS`'s
default already covers Vite's `5173` port (`settings.py`) — that part of #254's reconciliation
held up. Three real mock↔live type-contract mismatches surfaced (invisible to `mock.test.ts`
because the mock never exercised the values that differ) and were fixed in
`webapp/src/api/types.ts` + call sites:

- **`Headline.deployable` can be `null` live, not just `boolean`.** `pilots/performance.py`'s
  `pilot_headline()` returns `deployable: None` for a Pilot with no backtest AT ALL (cold start —
  same honesty class as `sharpe`/`dsr`/`pbo`), distinct from `false` for a backtest that ran and
  failed a gate. The mock only ever supplied a concrete `true`/`false`. Runtime behavior was
  already correct (`null` is falsy, so `DeployableBadge`'s ternary and the marketplace's
  `.filter(deployable)` both did the right thing) — only the TS type was lying. Widened to
  `boolean | null` in `types.ts`, `DeployableBadge`'s prop type, and `PerformanceResponse.metrics`
  (same `null`-on-cold-start shape, currently unread by any screen but now type-honest for future
  code).
- **`Follow.status` is `"active"` live, never `"queued"`.** `pilots/follows_store.py`'s real
  vocabulary is `STATUS_ACTIVE="active"` / `STATUS_CANCELLED="cancelled"` — the mock invented
  `"queued"`/`"cancelled"` instead. `Portfolio.tsx`'s active-follows badge only special-cased
  `"queued"` (`badge-warn` + "gated queue" label); a live follow would have silently rendered as a
  plain neutral `"active"` label instead of the intended warning treatment. Fixed the mock to emit
  `"active"` and `Portfolio.tsx` to check for it.
- **Auth is two separate tokens, one shared origin.** `STATE_API_TOKEN` (read, fail-open when
  unset) and `FOLLOW_API_TOKEN` (write, fail-closed) gate different endpoint families, but
  `client.ts` sends exactly one `VITE_API_TOKEN` on every request. Verified working when both are
  configured to the same secret (the common real deployment shape); noting as a known constraint
  rather than a bug to fix — a deployment that deliberately wants DIFFERENT read/write secrets
  can't do that with the current single-token client without a design change.

Post-fix, the full flow (marketplace list → pilot detail → Follow modal submit → Portfolio) ran
against the live server with zero console errors and rendered every cold-start state honestly
(`▲ Not deployable`, "No backtest series yet" with the real persisted reason, "Nothing here yet"
for the account-less Portfolio) — never a fabricated line. `npm run typecheck`/`test`/`build`
stayed green throughout.

## Decisions

- **D1 — namespaces genuinely differ.** A signal `name` (a `SIGNAL_WEIGHTS` key) is not a
  `STRATEGY_REGISTRY` key. `pilots/catalog.py` carries an explicit `validation_strategy_id`
  join so a Pilot never borrows an unrelated strategy's Sharpe; Pilots with no honest
  backtest carry `validation_strategy_id=None`.
- **D2 — honest, harness-persisted equity curve.** `validation/harness.py` persists a real
  downsampled base-100 OOS `equity_curve`; `pilots/performance.py` tail-slices it (an honest
  zoom, never a re-simulation). `curve: null` + `reason` when a Pilot has no backtest.
- **D3 — deliberate Follow keeps every chosen name.** Intent conviction = the Pilot's
  normalized target weight (honest proxy, never inflated); the queue is emitted with
  `min_conviction=0.0` so a deliberate Follow keeps every holding the Pilot selected.
- **D4 — single `VITE_API_TOKEN` for both read and write, accepted (2026-07-14).** The backend
  supports two independently-scoped tokens (`STATE_API_TOKEN` read/fail-open,
  `FOLLOW_API_TOKEN` write/fail-closed — see the live-cutover note above), but `client.ts` sends
  one shared token for both. Splitting the client to carry two tokens would reduce blast radius
  (a leaked read-capable token couldn't also write) and allow independent rotation, but for a
  single-operator deployment hitting their own local backend the risk is low. Decision: leave as
  one token; revisit only if the frontend is ever exposed more broadly or a second consumer needs
  scoped read-only access.
- **D5 — brokerage-connect credential intake is local, single-operator, verify-before-persist
  (2026-07-15).** Onboarding's "Connect brokerage" step was a client-side-only stub (no intake,
  no endpoint) while the read/serialize half (`data/robinhood_portfolio.py`,
  `GET /portfolio`) already existed — this was the actual gap AGENTS.md's safety posture warns
  about ("never log or persist secrets", single-operator/local-first). Scoped narrowly rather
  than building a multi-user encrypted vault: `POST /brokerage/connect` reuses `FOLLOW_API_TOKEN`
  (not a new token — same single-token-client tradeoff as D4) and adds two MORE independent
  gates on top — `settings.BROKERAGE_CONNECT_ENABLED` (new, default `False`, deliberately NOT in
  `gui/env_io.py`'s `ALLOWED_KEYS` so a GUI bug can't enable it) and a loopback-only
  (`127.0.0.1`/`::1`) request check. Credentials are verified with a real read-only Robinhood
  login (`data.robinhood_portfolio.verify_credentials`, never falls back to interactive MFA
  prompting — a headless HTTP request must not block on stdin) BEFORE being written, via a
  dedicated hard-scoped writer (`data/brokerage_credentials.py`, NOT `gui/env_io.py`, which
  exists specifically to refuse secret writes) to the ONE local `.env` file. Never a vault, never
  multi-tenant, never echoed back in any response.

## Running it

```bash
# Backend (reads: set STATE_API_TOKEN; follows: set FOLLOW_API_TOKEN to enable)
uvicorn api.pilots_api:app --port 8602

# Frontend PWA (offline mock by default; flip VITE_USE_MOCK=false to go live)
cd webapp
npm install
npm run dev        # http://localhost:5173
npm run test       # Vitest — mock contract, screen tests (Testing Library), live-client (mocked fetch)
npm run typecheck
npm run build      # type-check + production build (+ PWA service worker)
```
