---
name: new-pwa-screen
description: Add a new screen to the Stockpy Pilots PWA (webapp/). Use when asked to add, port, or build a screen/tab/page in the webapp -- covers the fixed order of edits (types -> client + mock -> screen -> route -> test), the mock/live parity gate, mobile nav-reachability traps, and honesty-fixture requirements this codebase has repeatedly gotten wrong on the first pass.
---

# Adding a screen to the Pilots PWA

This encodes the exact sequence used to add the Options Analytics and Strategy
Matrix screens (see `webapp/src/screens/OptionsMatrix.tsx` and
`StrategyMatrix.tsx` for worked examples), including the two traps that bit
those PRs before they were caught.

## Fixed order (don't skip or reorder)

1. **`webapp/src/api/types.ts`** — define/extend the response type. Declare
   every field a screen will render **explicitly**; don't rely on a
   `[key: string]: unknown` index signature to carry a field you actually use
   — it types as `unknown` and won't render/map without a cast. Add `null` to
   any field the backend can legitimately omit (CONSTRAINT #4 — never a
   fabricated default).

2. **`webapp/src/api/client.ts` (`liveApi`)** — add the method. Every
   `http()` call MUST carry an **explicit generic** (`http<Foo>(...)`, never
   bare `http(...)`) — a missing generic silently widens the shared
   `liveApi`/`mockApi` contract (see step 6).

3. **`webapp/src/api/mock.ts` (`mockApi`)** — add the matching method, AND
   build an **honest fixture**. A fixture that only emits clean, fully
   populated happy-path rows cannot exercise a single null-handling or
   empty-state code path — write at least one fixture row/case per honesty
   branch (null field, empty list, error/cold-start `reason`, integrity
   failure, whatever the domain's failure modes are). This is not optional
   polish; it's the only thing that will catch a screen mishandling `null`
   before a user does.

4. **`webapp/src/screens/Foo.tsx`** — build the screen. Reuse before
   building: `Modal` (focus-trapped dialog — never hand-roll one, a prior
   hand-rolled dialog shipped a real a11y bug), `Toggle`, `Input`, `Button`,
   `Loading`, `EmptyState`, `ErrorState` (branches on `status===404` for an
   honest cold-start message), `StaleDataNotice`, `useApi`, `useMutation`.
   Only reach for `usePoll` if the screen's data genuinely changes without a
   user action (most screens don't — don't poll a status that only changes
   once a day).

5. **`webapp/src/App.tsx`** — add the `<Route>`. Then decide nav placement
   (see below) — do not skip this: a route with no way to reach it is a
   second flavor of the "endpoint with no caller" bug.

6. **`webapp/src/screens/Foo.test.tsx`** — co-located test file. Cover the
   honesty branches from step 3's fixture, not just the happy path: null
   field renders "—" not "0"/"NaN"; empty-with-reason renders the reason;
   error/cold-start (404) renders the honest empty state.

## Nav placement — the mobile-reachability trap

`BottomNav` (mobile) renders **only `NAV_ITEMS.slice(0, 3)`** — adding an
entry to `NAV_ITEMS` does NOT make a screen reachable on mobile unless it's
one of the first three, which you should essentially never reorder to force
(it evicts something else). Three real doors exist; pick based on what kind
of screen this is:

- **Read-only analytics/research screen** (no writes): a tile in
  Marketplace's "Explore" row (`webapp/src/screens/Marketplace.tsx`) — the
  door `/models`, `/pairs`, and `/options` all use. Also add a `NAV_ITEMS`
  entry so desktop's sidebar (which renders the full list) reaches it too.
- **Settings/config/write screen**: a link card under `/settings`
  (`webapp/src/screens/Settings.tsx`'s `SectionCard` pattern) — the door
  `/settings/strategy` uses. Every `.env`-write surface in this PWA lives
  under Settings; don't put a write screen in top-level nav, it miscategorizes
  it as research. No `NAV_ITEMS` change needed — Settings' `match: (p) =>
  p.startsWith("/settings")` already highlights for a sub-route.
- **Core top-level section**: a `NAV_ITEMS` entry in the first three slots —
  rare; only for something as fundamental as Dashboard/Pilots/Activity.

## Verification gates (all four, every time)

```bash
cd webapp
npm run typecheck   # THE mock/live parity gate (see api-parity-reviewer agent)
npm test             # co-located tests, including your new honesty-branch cases
npm run build        # production build; catches anything typecheck's --noEmit misses
```

Then, if the change is visually observable, drive it in the browser preview
against the mock (fast, exercises every honesty branch you wrote fixtures
for) and — when feasible — against a live backend (`VITE_USE_MOCK=false`) to
confirm the real endpoint's shape actually matches what you typed in step 1.
