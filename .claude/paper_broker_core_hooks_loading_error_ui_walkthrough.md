# Paper Broker: loading/error UI for the 7 core data hooks

## The gap

`webapp/src/screens/PaperBroker.tsx` calls `useApi` seven times for its
always-on (non-toggle) sections:

| Hook | Section |
|---|---|
| `account` | Equity / Cash / Buying Power summary cards |
| `positions` | Positions table |
| `orders` | Orders (Last 100) table |
| `candidates` | Automated Strategy Options Execution (candidates table) |
| `greeks` | Portfolio Risk & Aggregate Greeks card |
| `deltaHedge` | Dynamic Delta Hedging panel (nested inside the Greeks card) |
| `metaStatus` | Stage 4 ML Options Meta-Labeler status card |

Unlike the 18 toggleable options-desk sub-panels on the same screen (e.g.
`ScenarioHeatmap`, `VolSurfaceView`, `EarningsCrushScanner`), which all read
`.loading`/`.error` off their own `useApi` result and render a loading
skeleton or an inline error message, these 7 sections were gated only on
`X.data &&`. If any of the seven fetches failed, the corresponding section
just silently didn't render — no skeleton, no error message, no indication
anything went wrong. The `candidates` section additionally always rendered
its "No strategy options directives currently meet VRP / regime gates."
empty-state message even while the fetch was still in flight (since
`candidates.data` is `null` during loading, which the old ternary read as
"empty", not "loading").

## The fix

Added two small local presentational components at the top of
`PaperBroker.tsx`, `CoreSectionLoading` and `CoreSectionError`, that
reproduce the exact visual pattern already used by `ScenarioHeatmap`'s own
loading/error early-returns (`padding: 24`, centered text, `theme.surface`
background, `theme.border` border; `theme.textSecondary` for loading,
`theme.decline` for error) — reused for consistency rather than inventing a
new style.

For each of the 7 core sections, added:

- `{X.loading && !X.data && <CoreSectionLoading label="..." />}`
- `{X.error && !X.data && <CoreSectionError label="..." error={X.error} />}`

directly ahead of the existing `{X.data && (...)}` block, which is otherwise
untouched — the happy-path JSX is byte-identical to before. The `!X.data`
guard matches `useApi`'s own semantics: on a `reload()` (not just the
initial mount), `loading` flips back to `true` but the previous `data` is
kept until the new response lands, so a manual "Reset"/"Execute"/etc. reload
doesn't flash a loading placeholder over already-rendered content — only a
genuine first-load-with-nothing-yet shows the skeleton.

Section-specific notes:

- **`deltaHedge`**: nested inside the `greeks.data &&` block (as it already
  was), with its own loading/error placeholders inserted right before its
  existing `{deltaHedge.data && (...)}` render, so a Greeks-loaded-but-
  hedge-still-loading (or hedge-failed) state is now visible instead of the
  panel just vanishing.
- **`candidates`**: restructured the existing
  `candidates.data?.candidates?.length > 0 ? <table> : <empty message>`
  ternary to be nested inside a new `candidates.data && (...)` wrapper, so
  the "No directives" message only shows once data has actually loaded —
  this incidentally fixes the pre-existing minor bug where that message
  showed during the initial fetch too.
- **`positions`** / **`orders`**: loading/error placeholders added as
  additional `<tr><td colSpan={N}>...</td></tr>` rows in `<tbody>`, matching
  the table-compatible style already used for the existing "No open
  positions" / "No recent orders" empty-state rows.
- **`metaStatus`**: the stats grid (Training Samples / Model Accuracy /
  ROC-AUC / Last Retrained) is wrapped in a condition that's `true`
  whenever we're not showing a loading-with-no-data or error-with-no-data
  placeholder, since this section's header (with the "Retrain Meta-Model"
  button) is always visible regardless of hook state and only the stats
  block itself needed gating.
- **`account`**: straightforward — loading/error blocks ahead of the
  existing `{account.data && (...)}` summary-card row.

No restructuring beyond what each section needed; the diff is scoped to the
7 sections' loading/error rendering only, per the concurrent-PR
merge-conflict-avoidance note (another agent is independently touching this
same file's desk-panel spot-price prop-passing, a different section).

## Tests added

Extended `webapp/src/screens/PaperBroker.test.tsx` with a new
`describe("core hook loading/error UI", ...)` block (6 new test cases):

- Positions: loading placeholder while pending, inline error message on
  rejection.
- Portfolio Greeks: loading placeholder while pending (and confirms the
  happy-path "Portfolio Risk & Aggregate Greeks" heading is *not* shown
  during loading), inline error message on rejection.
- Account summary: loading placeholder while pending.
- Strategy options candidates: inline error message on rejection.

A `pending<T>()` helper (a `Promise` that never resolves) pins the
corresponding `useApi` hook in its `loading: true` state for the duration
of the test.

The existing `vi.mock("../api/client", () => ({ api: {...} }))` factory
fully replaced the module, which meant `useApi`'s `e instanceof ApiError`
check saw `ApiError === undefined` and threw an unhandled rejection as soon
as a test rejected a mocked call with a plain `Error`. Changed the mock
factory to `async (importOriginal) => ({ ApiError: actual.ApiError, api: {...} })`
so the real `ApiError` class is preserved alongside the mocked `api` object
— this only affects test-file internals, not runtime behavior.

## Verification

- `npm run --prefix webapp typecheck` — clean, no errors.
- `npm run --prefix webapp test -- PaperBroker` — 18/18 passing (12
  pre-existing + 6 new).
- `npm run --prefix webapp test` (full suite) — 168 test files / 1780 tests,
  all passing (no regressions elsewhere).
- Browser check: started `npm run dev` (port 5188, since 5173 was already in
  use by a concurrent session) and loaded `/paper-broker` in the Browser
  pane against the mock API. Bypassed the onboarding gate by seeding
  `localStorage["stockpy.onboarding.v1"]` directly (same shape
  `onboarding.ts::completeOnboarding` writes). Confirmed via `get_page_text`
  that all 7 core sections render their full happy-path content exactly as
  before (equity/cash/buying power cards, Greeks grid, delta-hedge panel
  with "Rebalance Required", the 2-row candidates table, positions/orders
  empty states, and the meta-model stats grid) with no loading/error
  placeholders showing once data resolved. `read_console_messages` showed
  only one pre-existing, unrelated error ("An unknown error occurred when
  fetching the script" — a dev-mode PWA/workbox service-worker script fetch
  noise, present before this change and unrelated to the loading/error UI
  edits) — no new console errors introduced by this change. The mock API
  responds fast enough that the loading state couldn't be caught visually
  on a plain page load; that path is covered directly by the new vitest
  cases instead (which pin the hook in `loading: true` via a
  never-resolving promise).

## Files changed

- `webapp/src/screens/PaperBroker.tsx`
- `webapp/src/screens/PaperBroker.test.tsx`
