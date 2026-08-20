# Paper Broker Desk — Dead Code Cleanup Walkthrough

Branch: `paper-broker-desk-dead-code-cleanup`
Scope: strictly the 4 cleanup items below. No other finding from the broader Paper Broker audit was touched.

## 1. Deleted: orphaned duplicate `HrpCvarOptimizerView.tsx`

**Verification (grep, before touching anything):**

```
grep -rn "HrpCvarOptimizerView" webapp/src
grep -rn "HrpPortfolioOptimizerView" webapp/src
```

Confirmed:
- `webapp/src/screens/PaperBroker.tsx` imports `HrpCvarOptimizerView` from
  `../components/portfolio/HrpPortfolioOptimizerView` (the real component, exported
  under an alias: `export { HrpPortfolioOptimizerView as HrpCvarOptimizerView };` at
  `HrpPortfolioOptimizerView.tsx:687`).
- The standalone `webapp/src/components/portfolio/HrpCvarOptimizerView.tsx` (a
  separate 119-line implementation calling the same `api.optimizeHrpCvar`) was
  referenced ONLY by its own test file (`HrpCvarOptimizerView.test.tsx`) — zero
  imports from any screen or from `HrpPortfolioOptimizerView.tsx`.

**Deleted:**
- `webapp/src/components/portfolio/HrpCvarOptimizerView.tsx`
- `webapp/src/components/portfolio/HrpCvarOptimizerView.test.tsx`

## 2. Deleted: orphaned `RealTimeRiskRadar.tsx`

**Verification (grep):**

```
grep -rn "RealTimeRiskRadar" webapp/src
```

Confirmed: only `RealTimeRiskRadar.tsx` (self-declaration) and
`RealTimeRiskRadar.test.tsx` (its own test) reference the name — no screen or other
component imports it.

**Deleted:**
- `webapp/src/components/options/RealTimeRiskRadar.tsx`
- `webapp/src/components/options/RealTimeRiskRadar.test.tsx`

## 3. Fixed: stale `settings.PAPER_BROKER_WRITES_ENABLED` docstring

**Verification (grep):**

```
grep -n "PAPER_BROKER_WRITES_ENABLED" api/pilots_api.py
grep -n "require_paper_broker_writes_enabled" api/pilots_api.py
grep -c "Depends(require_paper_broker_writes_enabled)" api/pilots_api.py   # -> 13
```

The old docstring said this flag "Gates POST /pilots/paper-broker/reset endpoint" —
true of only 1 of the 13 endpoints it actually gates. Enumerated every real usage of
`Depends(require_paper_broker_writes_enabled)` in `api/pilots_api.py` (13 occurrences,
each preceded upward to its own `@app.post(...)` decorator to get the real route path):

1. `POST /pilots/paper-broker/reset`
2. `POST /brokerage/options/order`
3. `POST /pilots/paper-broker/strategy-options/execute`
4. `POST /pilots/paper-broker/manage-exits`
5. `POST /pilots/paper-broker/roll`
6. `POST /pilots/paper-broker/delta-hedge/execute`
7. `POST /pilots/options/meta-model/retrain`
8. `POST /pilots/paper-broker/settle-expired`
9. `POST /pilots/options/earnings-crush/execute`
10. `POST /pilots/options/mispricing/execute`
11. `POST /pilots/options/dispersion/execute`
12. `POST /pilots/options/zero-dte/execute`
13. `POST /pilots/options/0dte/manage-exits`

Rewrote `settings.py`'s `PAPER_BROKER_WRITES_ENABLED` field description to list all 13
endpoints instead of just the reset endpoint. This is a **docs-only change** — the
flag's default (`True`), behavior, and gating logic (`require_paper_broker_writes_enabled`
in `api/pilots_api.py`) were not touched.

## 4. Documented (not deleted): two unused-but-fully-built API methods

`api.getVolSurface3DMesh` (`webapp/src/api/client.ts`) and `api.routeFixOrder`
(`webapp/src/api/client.ts`) are fully implemented end-to-end — real TypeScript types,
a real `client.ts` call, a real `mock.ts` fixture, and (for `getVolSurface3DMesh`) a
real backend route (`GET /pilots/options/vol-surface/3d-mesh` in
`api/pilots_api.py::get_pilots_options_vol_surface_3d_mesh`) — but have zero callers in
`webapp/src/screens/` or `webapp/src/components/` (verified via grep against both
directories).

Per instructions, this backend/API surface was **not deleted** — removing a working
endpoint is a bigger product decision than this cleanup task's scope. Instead, added a
one-line comment at each declaration site noting the method is currently unused and
available for a future UI wire-up, so a future reader doesn't mistake "no caller yet"
for "safe to delete":

- `webapp/src/api/client.ts` — comment above `getVolSurface3DMesh`
- `webapp/src/api/client.ts` — comment above `routeFixOrder`
- `api/pilots_api.py` — added a note to `get_pilots_options_vol_surface_3d_mesh`'s
  docstring (no backend route exists for `routeFixOrder`'s equivalent purpose to
  annotate beyond the client — `FixRouteOrderRequest`/`FixRouteOrderResponse` are
  handled by the FIX gateway's existing `/pilots/execution/fix/route` endpoint, which
  is itself only reachable via this unused client method; no separate backend
  docstring edit was needed there since the route's own docstring doesn't claim any
  caller).

`routeFixOrder`'s `mock.ts` fixture (`async routeFixOrder(...)`) and the backend route
implementation were left untouched — no code changes beyond the client.ts comment,
matching the instruction to document rather than modify.

## What was left alone, and why

- No other finding from the broader Paper Broker audit was touched (scope explicitly
  limited to items 1–4 above).
- `mock.ts` fixtures for `getVolSurface3DMesh`/`routeFixOrder` were left as-is — they
  are real, working mock implementations; only a documentation comment was requested,
  and it was placed at the `client.ts` declaration sites (plus the backend docstring
  for the one with a real route) per the task's own guidance ("in `client.ts` and/or
  the backend route").
- `HrpPortfolioOptimizerView.tsx` (the real, actually-rendered component) was not
  touched — only its orphaned duplicate was removed.

## Verification run

- `npm run --prefix webapp typecheck` → clean, zero errors (confirms nothing else was
  quietly depending on the two deleted component files).
- `npm run --prefix webapp test -- --run` → **166 test files / 1766 tests passed**,
  zero failures (confirms no other test references the deleted files or components).
- Final grep re-check of `Depends(require_paper_broker_writes_enabled)` count in
  `api/pilots_api.py` → 13, matching the 13 endpoints enumerated in the new
  `settings.py` docstring exactly.
