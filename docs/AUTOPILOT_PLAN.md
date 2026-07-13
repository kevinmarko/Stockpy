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

## Running it

```bash
# Backend (reads: set STATE_API_TOKEN; follows: set FOLLOW_API_TOKEN to enable)
uvicorn api.pilots_api:app --port 8602

# Frontend PWA (offline mock by default; flip VITE_USE_MOCK=false to go live)
cd webapp
npm install
npm run dev        # http://localhost:5173
npm run test       # Vitest — mock contract + honesty fixtures
npm run typecheck
npm run build      # type-check + production build (+ PWA service worker)
```
