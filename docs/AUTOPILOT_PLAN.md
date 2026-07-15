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
| — | Catalog coverage — 7 new dedicated Pilots for the previously-uncovered signal modules (`macro_regime`, `edge_garch`, `rsi_extremes`, `relative_strength`, `news_catalyst`, `forecast_alignment`, `sortino_drawdown`), 5 new price-only backtest adapters (`garch_vol_target`, `cross_sectional_momentum` — widened to a 30-name universe, `relative_strength_xsec` — same widened universe, `rsi14_extremes` — 3 variants incl. an SMA(200) trend filter, `sortino_drawdown` — rolling 504d Sortino/drawdown gate) so every honestly-backtestable module gets a real curve (real curves 4→9 of 16 Pilots), 4 new categories (Macro/Risk/Sentiment/Forecast) with a dataviz-skill-validated categorical palette for the chip color, PWA `mock.ts` resynced to the real catalog, `docs/signals/*.md` cross-linked to their Pilot (or an honest reason for having none) | `pilots/catalog.py`, `scripts/refresh_validations.py`, `webapp/src/{api/{mock,types}.ts,theme.ts,components/ui.tsx}`, `docs/signals/*.md` | this PR |

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
