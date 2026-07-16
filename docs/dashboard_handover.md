# Auto Pilots Dashboard v2 — Handover

**Screens/components added to the `webapp/` React + Vite PWA.** All four features read through the
existing `webapp/src/api/client.ts` (`api` object); the only backend change is one added alert emission
(below). Mock mode (`VITE_USE_MOCK=true`, the default) fully drives every new component offline.

> This document was rewritten during the PR takeover to describe **what actually shipped**. An earlier
> draft claimed a "Victory Audit" of 198 passing tests, an `api.getPilotCurve()` call, a Markdown
> clipboard export, and a configurable poll interval — none of which were true of the code. Those claims
> have been removed.

---

## 1. What shipped

| Feature | Route / mount | File |
|---|---|---|
| R1 — Drag-and-drop Dashboard | `/` | `webapp/src/screens/Dashboard.tsx` |
| R2 — Pilot Comparison | `/compare` | `webapp/src/screens/Comparison.tsx` |
| R3 — Activity Feed | reusable component | `webapp/src/components/ActivityFeed.tsx` |
| R4 — NotebookML Export | Dashboard widget | `webapp/src/components/NotebookMLExport.tsx` |

The Pilots marketplace (previously at `/`) moved to `/marketplace`; the Dashboard is now the landing
screen. Nav (`webapp/src/App.tsx`, shared `NAV_ITEMS`): Dashboard · Pilots · Activity · Portfolio ·
Compare · Models · Pairs. The mobile bottom bar shows the first three (`NAV_ITEMS.slice(0, 3)`).

### R1 — Dashboard
- Widget order + per-widget size (`S`/`M`/`L`) persist in `localStorage` under `dashboard_layout`.
- Native HTML5 drag-and-drop (no external DnD library). Keyboard/mobile fallback via up/down move buttons.
- The persisted layout is validated on load (unknown ids dropped, missing defaults re-appended) so a
  stale or hand-edited `localStorage` value can't crash the screen.

### R2 — Comparison (`/compare`)
- Up to 5 pilots selected via checkboxes; selection persists in `localStorage`
  (`comparison_selected_ids`).
- Overlaid Recharts `LineChart` fetching each pilot's real OOS equity curve via
  `api.getPerformance(id, "3M")` — the `.curve` field, which the harness persists.
- **A pilot whose `curve` is `null`** (no persisted backtest series — 7 of the 18 mock pilots) is **not**
  drawn as a phantom line. It renders an honest "no backtest series yet" note (with the API's own
  `reason` string) and stays in the metrics table. This is the CONSTRAINT #4 fix; see §3.
- Metrics table: Sharpe / PBO / Max Drawdown / DSR / AUM proxy / followers, each rendering `—` for a
  `null` value, never a fabricated `0`.

### R3 — Activity Feed
- `ActivityFeed({ limit, pilotIds, pollIntervalMs })`. Polls `api.getAlerts(limit)` every
  `pollIntervalMs` (default 30s); toggleable + manual refresh.
- Alert level renders honestly: a `null`/unknown level shows `—`, never a defaulted "Info".
- Empty state surfaces the feed's own `reason` (e.g. "Alert file not configured …") rather than a flat
  "No alerts yet."
- `pilotIds` filters **only** on the structured `entry.extra.pilot_id` field (exact match). It does not
  guess a pilot from alert message text. Alerts with no `pilot_id` are shown under a "Platform" bucket —
  never hidden. The `/activity` screen renders `<ActivityFeed limit={50} />`.

### R4 — NotebookML Export
- Copies / downloads a structured JSON snapshot of the portfolio + active follows for pasting into a
  NotebookML source. (JSON only — there is no Markdown mode.)
- **Preserves `null`.** `market_value`, `qty`, `avg_cost`, `total_equity`, etc. serialize as `null` when
  uncomputable, never coerced to `0` — the export is LLM-analysis input, so a fabricated `$0` would be
  actively misleading. Export buttons are disabled until the portfolio has resolved, so an in-flight
  fetch can't emit an all-`null` payload stamped with a real timestamp.

---

## 2. Backend change — genuinely pilot-scoped alerts

The only backend edit is in `pilots/mirror.py::plan_follow`, which now emits one `INFO` alert carrying
`extra={"type": "follow_planned", "pilot_id": pilot.id, ...}` when a follow plan is built. This is the
single place in the codebase with a first-hand `pilot_id` at alert-emission time.

`extra` round-trips for free: `observability/alerts.py` flattens it into the JSONL line;
`pilots/alerts_feed.py::_normalize_entry` re-gathers it under `extra`; it reaches the PWA as
`entries[].extra.pilot_id`. No changes were needed to `alerts.py` or `alerts_feed.py`.

**Honest scope:** only follow-planning alerts are pilot-attributed. Risk-gate, kill-switch,
reconciliation, and validation alerts stay platform-scoped because those subsystems genuinely do not know
a pilot — attributing them would require plumbing pilot context through `OrderIntent`/`RiskContext`, a
separate architectural change. Do not backfill attribution by guessing from symbol or message text.

---

## 3. The honesty invariant (CONSTRAINT #4)

This repo's load-bearing rule is *never fabricate data* — an uncomputable value is `null`/`—`, not a
plausible zero or a placeholder line. The takeover of this PR removed several violations from the
original handoff:

- NotebookML `?? 0` coercions on every money field → preserve `null`.
- ActivityFeed defaulting a `null` level to `"INFO"` → render `—`.
- ActivityFeed dropping the feed's `reason` → surface it.
- ActivityFeed guessing pilot↔alert links from message text (a "facade placeholder" the original agent's
  own notes flagged) → exact `extra.pilot_id` match only.
- Comparison silently dropping `curve: null` pilots into an invisible line → honest "no backtest series"
  note.
- `useApi` retaining stale data on fetch error across all 8 screens → restored `setData(null)`.

The UI pattern to copy for any new nullable data: `value == null ? "—" : fmt(value)`, and for a whole
missing series, `data?.reason ?? "<literal fallback>"` inside `<div className="empty">` (see
`PilotDetail.tsx`).

---

## 4. Run locally

```bash
cd webapp
npm install
VITE_USE_MOCK=true npm run dev   # offline, mock data
npm run typecheck                # tsc --noEmit
npm run test                     # vitest run
npm run build

# Against the live FastAPI Pilots API instead of mock:
#   VITE_USE_MOCK=false VITE_API_BASE_URL=http://localhost:8602 npm run dev
# (start the backend with PILOTS_API_ENABLED=true; see api/pilots_api.py)
```

Backend alert test path: with `PILOTS_API_ENABLED=true` and `FOLLOW_API_TOKEN` set,
`POST /pilots/{id}/follow`, then `GET /alerts` — the new entry carries `extra.pilot_id`.

---

## 5. Architecture notes

- State is local React `useState` + `localStorage`. No Redux/Zustand.
- `useApi<T>(fn, deps)` (`hooks/useApi.ts`) is the uniform loader: `{ data, loading, error, status,
  reload }`, `data` is `T | null`, and a 404 is distinguished from a hard error via `status` so
  `ErrorState` shows "Nothing here yet" (no Retry) for cold-start.
- Mock vs live is a module-level toggle in `client.ts` (`VITE_USE_MOCK`, defaults to mock).
- Tests render against the real mock API (no `vi.mock` of the module); `vi.spyOn(api, …)` + `ApiError`
  only for error paths.
