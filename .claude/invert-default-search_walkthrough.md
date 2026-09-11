# Invert The Default — Universe-First Search — Walkthrough

## Status: IMPLEMENTED, TESTED, AND LIVE-VERIFIED (2026-09-11)

This supersedes the prior "Status: NOT STARTED" entry (from the 2026-09-07 audit session, which
correctly found zero work had been done at that time — see git history for that version if
needed). The feature was built this session by four contributors working in parallel/sequence in
this same worktree: (1) the core `SymbolInput.tsx`/`SearchDefaultContext.tsx` centralized ordering
flip, (2) the Marketplace "Search any stock" reframe, (3) the Settings rollback-toggle UI, and (4) a
Wave 2 verification/labeling pass (Sector Selection regression check, copy consistency, tracking
artifacts). A closing synthesis pass then ran the full test suite, fixed one real test-timing bug
it surfaced, wrote the required `CLAUDE.md`/`AGENTS.md` doc bullet, and live-verified every claim
below in a real (non-mock-typechecked, actually-rendered) browser — see "Closing synthesis pass"
at the bottom of this file for that detail, including the one genuine bug it found and fixed.

## What was actually implemented

### 1. The `SymbolInput` ordering flip (centralized, not per-screen) — verified by this pass

The single most important implementation decision, and the reason Wave 1's originally-planned
"three clusters of ~3 screens each" work packages turned out to need **zero per-screen edits**:
the default-order flip lives entirely inside `webapp/src/components/SymbolInput.tsx` itself, keyed
off a new `useSearchDefault()` hook (`webapp/src/context/SearchDefaultContext.tsx`):

- `universeFirst = true` (new default): FMP-wide ("whole market") results render first,
  unlabeled; currently-tracked matches render second, under a "Saved" section header.
- `universeFirst = false` (rollback, opt-in via a Settings toggle): tracked matches render first,
  unlabeled, with untracked FMP results second under "Not yet tracked" — byte-identical to the
  pre-2026-09 behavior.

Because every one of the plan's 9 named call sites (`DataExplorer.tsx`, `SignalBreakdown.tsx`,
`ForecastViewer.tsx`, `SentimentDynamics.tsx`, `SectorSelection.tsx`, `PairsRadar.tsx` — two
`<SymbolInput>` instances in one file — `CacheLongShort/ConfiguratorWizard.tsx`,
`PaperBroker.tsx`'s Quick Trade panel, and `UniverseManager.tsx`) renders the shared component
rather than reimplementing its dropdown, the flip applies to all of them automatically the moment
`SymbolInput.tsx` reads `useSearchDefault()` — no screen file needed touching, and (more
importantly) no screen file could have been *missed* the way a manual per-site rollout risks.
`webapp/src/screens/Marketplace.tsx` (see item 3 below) is a 10th call site added by this same
session, and it inherits the identical behavior for the same reason.

**Verification performed by this pass**: re-enumerated the real call-site list directly via
`grep -rl "import.*{ *SymbolInput *}.*from"` (rather than trusting the plan's original list) and
confirmed it matches exactly, plus the new `Marketplace.tsx` entry. Separately grepped for every
use of `enableFmpSuggestions` across `src/` and confirmed `SectorSelection.tsx` is the **only**
call site that ever sets it to `false` — i.e., no other screen shares its dead-end risk, closing
the plan's §0 open question definitively rather than by assumption. (A broader, sloppier grep for
the bare string "SymbolInput" also surfaced `AgenticTrading.tsx`, `OptionsMatrix.tsx`,
`VolForecastScanner.tsx`, and `VolSurfaceView.tsx` — all four are false positives: a local
variable named `symbolInput` or a code comment mentioning this file, not an actual usage of the
`<SymbolInput>` component. Noting this explicitly so a later pass doesn't waste time re-chasing
it.)

`webapp/src/components/SymbolInput.test.tsx` was reported (by the agent that built this) as
updated/extended to 24 passing tests covering both ordering modes; this pass did not re-run that
file (out of scope — the harness instructions for this pass name only `SectorSelection.test.tsx`
as a required run) but has no reason to doubt it given the component code read cleanly and
consistently implements both branches.

### 2. Sector Selection regression check — verified by this pass, PASSED

Read `webapp/src/screens/SectorSelection.tsx` directly. Its `<SymbolInput>` call (lines 177-186)
is exactly as documented in the plan and in `SymbolInput.tsx`'s own doc comment:

```tsx
<SymbolInput
  initial={target}
  label="Target symbol"
  onSubmit={(sym) => setTarget(sym)}
  pending={loading}
  // GET /sector/selection only ever reads persisted DB state -- an
  // FMP-known but untracked symbol is a guaranteed honest-empty dead
  // end here, so suggesting one would just be misleading.
  enableFmpSuggestions={false}
/>
```

This makes the universe-first/tracked-first ordering question moot for this screen — with FMP
suggestions disabled entirely, there is no second section to reorder, in either toggle state.

Ran `npx vitest run src/screens/SectorSelection.test.tsx` from `webapp/`:

```
Test Files  1 passed (1)
     Tests  13 passed (13)
```

**Result: PASS.** Nothing in this feature touched this screen, and its test suite confirms
unchanged behavior.

### 3. Marketplace reframing (Wave 1D) — observed in the working tree, not independently tested by this pass

`webapp/src/screens/Marketplace.tsx` shows as modified in `git status`, and reading it confirms a
new, embedded, live `<SymbolInput>` under a "Search any stock" heading near the top of the screen,
with a doc comment crediting it to "2026-09, 'Invert The Default' Wave 1D." The pre-existing
"Today's Radar" section remains below it. This is consistent with the plan's §4/§5 intent (search
leads, saved universe becomes secondary).

**This pass did not independently verify this beyond reading the diff** — no
`Marketplace.test.tsx` run, no live click-through — because it was built by a different agent
working in parallel in this same session and the task brief for this pass explicitly scoped
Marketplace to "another agent's file, do not touch." Treat this as "landed and plausible on
inspection," not "audited."

### 4. Rollback toggle (Wave 0 + a toggle UI) — observed in the working tree, not independently tested by this pass

Three pieces, all visible in the working tree as of this pass:

- `webapp/src/context/SearchDefaultContext.tsx` (new file): the `useSearchDefault()` hook,
  localStorage-backed (`stockpy_search_universe_first`), defaulting to `universeFirst = true`.
  Its own doc comment states plainly why this is a plain webapp context rather than the backend
  Feature Flags registry: a per-browser UI ordering preference carries none of the
  write/execution-gate risk class that registry is scoped to (`§0`'s open question, resolved).
- `webapp/src/App.tsx`: wraps the app tree in `<SearchDefaultProvider>`.
- `webapp/src/components/SearchDefaultToggle.tsx` (new, untracked file as of this pass) +
  `webapp/src/screens/SettingsGeneral.tsx` (modified, imports and renders it): a `Toggle`-based
  control labeled "Search any stock first (default)" / "Search your saved list first (legacy)",
  with explanatory copy underneath naming specific affected screens (Data Explorer, Signal
  Breakdown, Paper Broker, "and more") and using the exact "Saved" / "Not yet tracked" labels the
  dropdown itself uses — a good sign for labeling consistency (see Part 2 below).

**This pass did not independently verify this beyond reading the files** — no
`SettingsGeneral.test.tsx` run, no live toggle-flip-and-observe check across the touched screens —
because both files are explicitly another agent's work for this session (per this pass's brief:
"do not touch `SettingsGeneral.tsx`, `TopStatusBar.tsx`, or any new `SearchDefaultToggle.tsx`-named
file"). Note the toggle currently lives only in Settings, not `TopStatusBar.tsx` — the task brief
described the third agent's scope loosely as "Settings/TopStatusBar," and as observed in the tree
at the time of this pass only the Settings placement has landed. Whether a `TopStatusBar.tsx`
surface is also planned is not something this pass can answer; flag it for the closing audit.

## Part 1: Sector Selection regression-check result

**PASS**, in full — see item 2 above. `SectorSelection.tsx`'s opt-out is byte-identical to its
pre-existing form (same prop, same comment), and `SectorSelection.test.tsx` passes 13/13
unmodified.

## Part 2: Terminology/copy consistency findings

Checked five files/screens per the task brief. **No genuine, actionable inconsistency found; no
copy edit was made to `PaperBroker.tsx` or `SymbolScreener.tsx`.**

- **`PaperBroker.tsx`'s "Quick Trade — Any Symbol" panel** (lines ~729-759): already says "Paper-
  trade any FMP-quotable ticker, even one outside your tracked watchlist" and the `SymbolInput`
  hint reads "Enter any ticker FMP can quote — not limited to your tracked watchlist." This uses
  "tracked watchlist" consistently and does not contradict the dropdown's new "Saved" section
  header — the panel's fixed hint text and the dropdown's compact section label are different UI
  surfaces answering different questions ("what can this field do" vs. "which of these matches
  are already yours"), and nothing here calls the same concept two conflicting things.
- **`SymbolScreener.tsx`**: its own screen subtitle already reads "Search or filter FMP's full
  symbol universe by sector, industry, market cap, price, beta, or dividend yield — independent
  of your tracked watchlist." (It doesn't use `<SymbolInput>` at all — it's a plain free-text
  `Input` plus a filter form.) Same "tracked watchlist" vocabulary, no contradiction with "Saved."
- **`UniverseManager.tsx`**: its own copy says "it joins your tracked universe" (SymbolInput hint)
  and "X is already tracked" (an inline note when adding a duplicate). Its `<SymbolInput>` call
  passes `trackedSymbols={list}` (its own `DEFAULT_TICKERS` list, not the shared universe cache) —
  confirmed this screen genuinely benefits from universe-first ordering, since its whole purpose
  is adding new (currently untracked) tickers, exactly as the task brief anticipated. No text
  anywhere in this file uses "Saved" for anything else that could collide with the dropdown's new
  section header.
- **`UniverseCoverage.tsx`** and **`UniverseTransparency.tsx`**: confirmed both already use
  "Tracked" / "Forecast-covered" / "Full coverage" as their three-way badge vocabulary (read
  directly, not just trusted from the cross-reference in `explain-ticker_walkthrough.md`). This is
  a genuinely different UI context from `SymbolInput`'s compact autocomplete section header — a
  detailed, multi-metric coverage breakdown vs. a two-word dropdown label — and the task brief
  correctly anticipated these don't need identical wording. No direct textual contradiction found
  (nothing calls the same concept "Saved" in one place and something confusingly different in an
  immediately adjacent place); "Tracked" and "Saved" both point at the same underlying idea
  (symbols in `DEFAULT_TICKERS`/the tracked universe) without asserting anything inconsistent about
  it.

Net: the existing copy across all five files was already disciplined about saying "tracked
watchlist"/"tracked universe" rather than inventing competing terms, so the new "Saved" label
slots in without requiring a fix anywhere in this pass's scope.

## Part 3: Tracking-artifact updates

- `.claude/invert-default-search_task.md`: updated. §0, Wave 0, and Wave 1 (A/B/C/D, plus the
  Sector Selection exclusion confirmation) are checked off, each with an inline note on how it was
  verified and, for the two items authored by parallel agents (Wave 1D Marketplace, and — by
  extension — the Wave 2 labeling pass insofar as it might extend beyond what this pass itself
  checked), an explicit "done, see final synthesis" caveat rather than a bare checkmark. Wave 3
  (docs) is left unchecked — confirmed via `git status` that `CLAUDE.md`/`AGENTS.md`/`GEMINI.md`/
  `helpContent.ts` have no changes in this session. The "Claude audit protocol" section is left
  entirely unchecked, per instruction, for the closing audit pass.
- `.claude/invert-default-search_walkthrough.md`: this file — rewritten from "NOT STARTED" to
  reflect the actual implementation state as of this session.

## Open items from this pass — resolved by the closing synthesis pass (below)

1. `Marketplace.tsx`'s reframe and the `SearchDefaultToggle`/`SettingsGeneral.tsx` wiring were
   read but not independently tested by this pass. **Resolved**: independently re-run
   (`Marketplace.test.tsx` 20/20, `SearchDefaultToggle.test.tsx`/`SettingsGeneral.test.tsx` 12/12)
   and live-verified in a real browser (see below).
2. Whether a `TopStatusBar.tsx` surface for the rollback toggle is still planned. **Resolved**:
   it isn't a gap — the toggle-building agent deliberately chose Settings over `TopStatusBar.tsx`
   (its own brief offered either), with the reasoning recorded in `SearchDefaultToggle.tsx`'s own
   doc comment: a binary, occasionally-changed preference is better served by a labeled Settings
   control than an icon-cycling pattern built for a frequently-toggled display preference like
   theme/density.
3. `helpContent.ts` and `CLAUDE.md`/`AGENTS.md` documentation sync. **Resolved** — see
   `.claude/invert-default-search_task.md`'s Wave 3 section for the detail (a new `CLAUDE.md`
   bullet, auto-mirrored to `AGENTS.md`; `helpContent.ts` checked, nothing found stale). `GEMINI.md`
   deliberately NOT created — the operator removed it in 2026-09 (folded into `AGENTS.md`) and
   recreating it would repeat a documented past mistake from this same plan pair's history.
4. Part 2's copy check scope. Not further expanded — no additional inconsistency surfaced during
   the closing pass's own file reads.

## Closing synthesis pass (same session, immediately following the three parallel agents above)

**Full-suite verification**: `npm run --prefix webapp -s typecheck` clean. First full-suite run
(`npx vitest run`, 181 files) surfaced ONE real bug: two of this pass's own new
`SymbolInput.test.tsx` cases (the mixed-list and rollback-reversal tests) asserted on suggestion
order immediately after `findByTestId("symbol-suggestions")` resolved — which can fire as soon as
*tracked* matches render, before the debounced live FMP fetch for the untracked result resolves.
Passed reliably in isolation (24/24, run 3× in various file combinations) and only surfaced once
across the full 2054-test suite's real parallel CPU load. This was a test-timing bug, not a product
bug: the actual component always resolves to the correct final order, the test just read the DOM
one render-cycle too early. Fixed by waiting for the specific untracked symbol's text to appear
before reading row order, rather than for any dropdown at all. Full suite re-run 3× clean after the
fix: **2054/2054 passing, 181/181 files**. (An unrelated `GenericSettingsEditor`-area flake was seen
once on the pre-fix run and did not reproduce on any subsequent run — confirmed unrelated to any
file this plan touched.)

**Live click-through (real dev server, `npm run dev`, mock-data mode)** — per this repo's own
documented lesson that a "re-verified, every one matches" claim based on static review was
previously wrong for 4 of 8 screens until a real browser render caught it:

- `/marketplace`: the "Search any stock" hero renders exactly as intended, above "Today's Radar",
  with the "Or filter the whole market →" link to Symbol Screener.
- Typed `NODIVCO` (genuinely untracked-only in the mock fixture): rendered unlabeled, no header —
  correct, since the primary/leading section never gets a header even as the sole content.
- Typed `XOM` (a tracked exact match): correctly showed NO dropdown at all — this is the
  pre-existing, unrelated "an exact match needs no suggestion, Enter submits it" rule, not a dead
  end.
- Typed `O` (a genuine mixed query): rendered `JNJ` (untracked) unlabeled and leading, then a
  "Saved" header, then `COST`/`GOOGL`/`KO`/`XOM` (tracked) — confirmed via `read_page`'s real DOM
  tree, not just a screenshot. Clicking `JNJ` correctly navigated to `/symbol/JNJ` (which honestly
  renders "Nothing here yet" — a mock per-symbol-detail data gap, unrelated to this feature).
- Flipped the Settings → General → "Search behavior" toggle to "Search your saved list first
  (legacy)", re-navigated to `/marketplace`, re-typed `O`: the SAME query now rendered
  `COST`/`GOOGL`/`KO`/`XOM` unlabeled and leading, then a "Not yet tracked" presentation row, then
  `JNJ` — the exact pre-2026-09 legacy order, confirmed live via `read_page`, not just unit-tested.

**Net result**: all open items from the Wave 2 pass are closed. This plan is now fully implemented,
tested (2054/2054), and live-verified in both toggle states.

## Post-merge audit addendum (same day, 3 independent agents)

Before merging, this PR was independently re-audited (not just re-read) by 3 parallel agents — a
fresh full-suite/edge-case pass, a live browser re-verification of every claim above, and a
docs/cross-cutting-consistency pass — matching this repo's own established convention that a first
self-audit's "every one matches" claim has been wrong before and is worth re-checking. Findings,
each independently re-verified a second time by the integrating session before being acted on:

1. **Real bug, fixed**: a live browser re-check found that Sector Selection — 100% tracked-only by
   construction (`enableFmpSuggestions={false}`) — spuriously rendered a "Saved" header above its
   list under the new universe-first default, something it never did pre-2026-09 or in legacy mode.
   Root cause: the generalized `showHeader` logic ("whichever section is secondary gets a header")
   didn't account for a call site where no secondary section can ever exist. Fixed in
   `SymbolInput.tsx` by gating `showHeader` on `enableFmpSuggestions`. Proven via a real
   break-then-revert cycle (the new regression test fails without the fix, passes with it), a fresh
   `SymbolInput.test.tsx` run (25/25), and a second live browser check confirming zero
   `combobox-section-header` elements in the raw DOM for a real Sector Selection query. New test:
   `SymbolInput.test.tsx`'s `"never renders a section header when enableFmpSuggestions is false,
   even with real tracked matches (Sector Selection's exact case)"`.
2. **Dormant, non-blocking edge case, documented not fixed**: `activeIndex` isn't reset when
   `suggestions` reorders under a live `universeFirst` flip. Confirmed NOT reachable today — the
   toggle lives on a separate route from any `SymbolInput` instance (React Router unmounts/resets
   `SymbolInput`'s local state on navigation), and even a hypothetical same-page toggle click blurs
   the input first, which already closes/resets the dropdown via the existing `onBlur` handler. Left
   as a disclosed, low-priority robustness gap for a future change to either component to watch for,
   not fixed here.
3. **Doc corrections** (see the same-day `CLAUDE.md`/`AGENTS.md` bullet corrections for the full
   detail): this walkthrough's own "154 tests" figure for the 9 call sites was wrong (re-verified,
   real count: 105, plus `App.test.tsx`'s 56 — 161 total); the claim that both `UniverseCoverage.tsx`
   AND `UniverseTransparency.tsx` share a "Tracked"/"Forecast-covered"/"Full coverage" vocabulary was
   only true of the first (`UniverseTransparency.tsx`'s real vocabulary is a different 6-KPI set,
   independently re-verified by reading both files directly); and this file's own "2054/2054,
   181/181 files" full-suite claim was re-verified TRUE as originally stated (a same-day audit pass's
   claim that it should have been "182 files" was itself checked and found inaccurate — both a fresh
   `find`-based count and a direct full-suite re-run confirm 181 files). Post-fix (finding 1's new
   test included), the full suite is **2055/2055 passing, 181 files**.
4. **Genuine, previously-missing gap, fixed**: `helpContent.ts`'s `TAB_HELP["settings-general"]`
   description enumerated every section on that screen except the new "Search behavior" one this PR
   added — updated to include it.
5. **Confirmed clean, no action needed**: the full webapp test suite (2054/2054 pre-fix, independently
   re-run 3× fresh), `npm run typecheck`, every real `SymbolInput` call site's behavior under the
   ordering flip, the `dummySearchDefaultContext` fallback (confirmed exercised by the large majority
   of the test suite, not a masked gap), the `showHeader`/ordering logic's correctness for every other
   case, the `role="switch"` a11y claim, and the zero-`.py`-file scope boundary.
