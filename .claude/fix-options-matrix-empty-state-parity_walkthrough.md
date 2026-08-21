# Options Matrix empty-state parity fix — walkthrough

## Reported symptom
Operator observed dropdown/select UI present in mock mode (`VITE_USE_MOCK=true`)
that appeared missing when running against the live backend
(`VITE_USE_MOCK=false`), specifically mentioning the Options Matrix, Commands,
and Settings screens.

## Investigation
Dispatched three parallel read-only audits (one per screen area), each tracing
every `<select>`/dropdown in the render tree back through `webapp/src/api/client.ts`
to `mockApi` vs. `liveApi`, and to the real backend handler, looking for genuine
mock/live drift (hardcoded mock data vs. an unwired or differently-shaped live
endpoint).

**Result: no code-level mock/live API-parity bug found anywhere.**

- **Commands screen** — already fully fixed by the prior commit (#837, "sync
  Commands screen strategy list + bulk-validate UI"). Verified end-to-end:
  `cli_introspect/command_manifest.json`'s `strategy_registry` has 29 entries,
  matches `STRATEGY_REGISTRY` exactly, `tests/test_command_manifest_freshness.py`
  passes, and mock/live are wired identically. No other dropdown on this screen
  has the reported bug shape.
- **Settings screens** (all 16 routed under `SettingsLayout.tsx`) — every
  enum-backed `<Select>` in `GenericSettingsEditor` has its option list
  hardcoded *identically* on the backend (`api/pilots_api.py`) and in the mock
  fixture (`webapp/src/api/mock.ts`) — 9/9 diffed byte-for-byte, zero drift.
  `PromptRegistry`'s version picker is the one dropdown genuinely sourced from
  variable live data, and it already degrades honestly ("No versions available
  to pin") rather than rendering a broken/hidden control.
- **Options Matrix** — the one screen with a real, confirmed cause, but not a
  wiring bug: `settings.OPTIONS_MATRIX_ENABLED` defaults `False` on a live
  backend, so `GET /options` honestly returns `directives: []` (per this
  repo's CONSTRAINT #4 — never fabricate data) until an operator opts in and
  runs the pipeline at least once. `mockApi.getOptions()` always seeds a
  non-empty fixture, so mock never hits this path.

## Root cause (UX, not wiring)
`OptionsMatrix.tsx` hid the **entire** Search/Sort/Filter row — not just the
results list — whenever `directives.length === 0`. That made the empty state
look identical to "this screen doesn't have this feature" rather than the
true "there's honestly nothing to filter yet," which is exactly what read as
a mock/live parity bug from the operator's side.

## Fix
`webapp/src/screens/OptionsMatrix.tsx`:
- Merged the two mutually-exclusive `directives.length === 0` /
  `directives.length > 0` render blocks into one.
- The context row / IVR honesty banner / summary metrics banner stay gated on
  `directives.length > 0` — showing e.g. "$0.00 Actionable Premium" for a
  disabled feature would misleadingly look like a measured zero, not "no
  data" (same CONSTRAINT #4 concern as the backend's own empty response).
- The Search input, Sort select, and filter segmented control now always
  render, with `disabled={directives.length === 0}` — visible but inert
  instead of vanishing — directly above the existing honest reason message
  (`data.reason`, e.g. "Options matrix not generated yet — enable
  OPTIONS_MATRIX_ENABLED and run the pipeline.").

`webapp/src/index.css`:
- Added `.segmented button:disabled { opacity: 0.5; cursor: not-allowed; }`,
  matching the existing `.btn:disabled` convention — `.input:disabled` /
  `.select:disabled` styling already existed and needed no change.

## Verification
- `npm run --prefix webapp typecheck` — clean.
- Full webapp suite — 1810/1810 passing (`OptionsMatrix.test.tsx`'s existing
  "an empty matrix renders the honest reason, never a fabricated row" test
  still passes unchanged).
- Live browser check (`npm run dev`, mock mode): normal non-empty render path
  confirmed unchanged (screenshot). Then `mockOptionsMatrix()` was temporarily
  edited to return `directives: []` + the real backend's `_DISABLED_REASON`
  string, confirming via screenshot + DOM inspection (`input.disabled === true`,
  `select.disabled === true`, all 5 filter buttons `disabled === true`) that
  the row now renders visibly-disabled with the reason text below, then
  reverted the fixture edit (`git diff` on `mock.ts` clean before commit).

## Out of scope / not touched
- Whether to actually enable `OPTIONS_MATRIX_ENABLED` on the operator's real
  live deployment — that's an operator `.env`/pipeline decision, not made
  here.
- `GreeksRollup` (portfolio-wide Greeks, independent of `directives`) kept its
  existing `directives.length > 0` gate — out of scope for this fix, which
  was specifically about the filter/sort/search chrome the operator named.
