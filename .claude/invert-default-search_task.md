# Invert The Default — Task Tracker

> Companion to `invert-default-search_implementation_plan.md`. Save as
> `.claude/invert-default-search_task.md`.
>
> **2026-09-11 Wave 2 verification/labeling pass**: items below checked off
> reflect what this pass directly confirmed by reading the working tree
> (three parallel agents were mid-flight in this same session: the
> `SymbolInput`/context flip, a Marketplace reframe, and a Settings toggle).
> Items marked "done, see final synthesis" are real and visible in the tree
> as of this pass but were authored by a parallel agent this pass did not
> independently test — final confirmation is the session's closing audit's
> job, not this pass's.

## §0 Dependency Check (blocking — do first)
- [x] Enumerate all 9 `SymbolInput` call sites' current state — re-verified
      directly via `grep -rl "import.*{ *SymbolInput *}.*from"`: the real
      call-site list is `DataExplorer.tsx`, `SignalBreakdown.tsx`,
      `ForecastViewer.tsx`, `SentimentDynamics.tsx`, `SectorSelection.tsx`,
      `PairsRadar.tsx` (×2 instances, one file), `CacheLongShort/
      ConfiguratorWizard.tsx`, `PaperBroker.tsx` (Quick Trade), and
      `UniverseManager.tsx` — exactly the plan's original 9, plus a 10th,
      new one added by Wave 1D: `Marketplace.tsx`'s own "Search any stock"
      CTA. (An earlier, broader grep for the bare string "SymbolInput"
      also matched `AgenticTrading.tsx`/`OptionsMatrix.tsx`/
      `VolForecastScanner.tsx`/`VolSurfaceView.tsx` — confirmed these are
      false positives: a local variable literally named `symbolInput` or a
      comment referencing this file, not an actual `<SymbolInput>` usage.
      Worth a note for whoever does final synthesis so it isn't re-flagged.)
- [x] Confirm which sites (beyond Sector Selection) share its dead-end risk
      — none. Confirmed via `grep -rn "enableFmpSuggestions"` across `src/`:
      `SectorSelection.tsx` is the only call site that ever passes
      `enableFmpSuggestions={false}`; every other real call site (see
      above) uses the component's default (`true`), and the centralized
      flip inside `SymbolInput.tsx` itself (not a per-screen edit) means
      this can't silently drift per-site — a screen would have to
      deliberately opt out, exactly as Sector Selection does.
- [x] Confirm current `Marketplace.tsx` structure/tile ordering — confirmed
      landed: `Marketplace.tsx` now leads with an embedded, live
      `<SymbolInput>` under an "Search any stock" heading (see file's own
      doc comment: "2026-09, 'Invert The Default' Wave 1D"). Existing
      "Today's Radar" section still present below it.
- [x] Confirm whether Universe Transparency plan has landed (shared
      terminology) — yes, both `webapp/src/components/UniverseCoverage.tsx`
      and `webapp/src/screens/UniverseTransparency.tsx` exist and use
      "Tracked" / "Forecast-covered" / "Full coverage" as their three-way
      vocabulary (confirmed by reading both files directly, not just
      trusting the cross-reference in `explain-ticker_walkthrough.md`).
- [x] Confirm rollback-toggle mechanism (Feature Flags registry vs. plain
      webapp context) — resolved as a plain, localStorage-backed React
      context (`webapp/src/context/SearchDefaultContext.tsx`), NOT the
      backend Feature Flags registry — that module's own doc comment states
      the reasoning explicitly (a pure per-browser UI ordering preference
      carries none of the write/execution-gate risk class that registry is
      scoped to).
- [x] Confirm Quick Trade / Symbol Screener's current copy for consistent
      labeling — done, see this session's copy-consistency pass below
      (no actionable inconsistency found; no edit made).

## Wave 0 — Scaffold (1 agent)
- [x] Rollback toggle mechanism, zero behavioral change yet — done:
      `SearchDefaultContext.tsx` (`useSearchDefault()` hook,
      localStorage-backed, `universeFirst` default `true`) exists and
      `App.tsx` wraps the app in `SearchDefaultProvider`.

## Wave 1 — Parallel (4 agents)
- [x] **A**: Data Explorer + Signal Breakdown + Forecast Viewer default flip
      — done. No per-screen edits were needed or made: the ordering flip
      lives centrally in `SymbolInput.tsx` (`useSearchDefault()`), and all
      three screens call `<SymbolInput>` with the component's default
      (`enableFmpSuggestions` unset → `true`), so they inherit the new
      universe-first order automatically.
- [x] **B**: Sentiment Dynamics + Pairs Radar ×2 default flip — done, same
      centralized mechanism as A; confirmed neither call site passes
      `enableFmpSuggestions={false}`.
- [x] **C**: Cache Long/Short + Paper Broker Quick Trade + Universe Manager
      default flip — done, same centralized mechanism; confirmed via
      direct read of `PaperBroker.tsx` and `UniverseManager.tsx` (both
      already checked in this pass for Part 2 copy consistency) that
      neither opts out.
- [x] **D**: Marketplace/nav reframing ("Search any stock" primary) — done,
      see final synthesis. Visible in the working tree as of this pass
      (`Marketplace.tsx` modified, "Search any stock" heading present) but
      authored by a parallel agent in this same session; this pass did not
      run `Marketplace.test.tsx` or click through it live, so final
      confirmation is the session's closing audit's job.
- [x] Confirm: **Sector Selection explicitly excluded from A/B/C** — its
      opt-out is untouched. Directly re-verified in this pass (Part 1):
      `SectorSelection.tsx` still passes `enableFmpSuggestions={false}`
      with its original comment, and `SectorSelection.test.tsx` passes
      (13/13) unmodified.

## Wave 2 — Serialized (1 agent)
- [x] "Saved" labeling consistency pass across Marketplace / Universe
      Manager / Universe Transparency panel — done, see final synthesis.
      This pass's own Part 2 copy-consistency check (scoped to
      `PaperBroker.tsx`, `SymbolScreener.tsx`, `UniverseManager.tsx`,
      `UniverseCoverage.tsx`, `UniverseTransparency.tsx` per this pass's
      brief) found no actionable inconsistency — see the walkthrough for
      full detail — but this pass did not separately audit whatever Wave 2
      agent's own labeling-pass diff may include beyond those five files,
      so treat this checkbox as "independently spot-checked, not a full
      review of that agent's own changes."

## Wave 3 — Docs (1 agent)
- [x] `CLAUDE.md` / `AGENTS.md` — done, by the closing synthesis pass. A new
      top-level bullet added to `CLAUDE.md`'s changelog (after the "Weekly
      Digest Reporting" sub-bullet); `.claude/hooks/sync_agent_docs.sh`
      mirrored it to `AGENTS.md` automatically (verified: both files contain
      an identical "Invert The Default" bullet). **`GEMINI.md` intentionally
      NOT created** — per `CLAUDE.md`'s own 2026-09-07 audit note, the
      operator deliberately removed `GEMINI.md` (folded into `AGENTS.md`)
      and no sync convention for it exists; recreating it would repeat a
      documented past mistake.
- [x] `helpContent.ts` — checked (not modified). Grepped every "tracked
      symbol"/"tracked universe"/"tracked list" reference in `TAB_HELP`/
      `GLOSSARY`/`METRIC_HELP`: none describe `SymbolInput`'s search-box
      ordering default specifically (they describe the tracked-universe
      *concept* generally, which is unchanged), so nothing there is now
      factually stale. No edit needed.

## Claude audit protocol (6-8 agents)
- [x] Auditors 1-3 — per Wave-1 cluster: live backend check for
      dead-end/empty-result regressions — done by the closing synthesis
      pass via a live (non-mock) dev-server click-through: typed a
      genuinely untracked-only query (`NODIVCO`), a tracked-only exact
      match (`XOM`, which suppressed suggestions entirely by the
      pre-existing exact-match rule — confirmed correct, not a dead end),
      and a mixed query (`O`) showing an untracked result (`JNJ`) leading
      unlabeled, a "Saved" header, then tracked matches (`COST`/`GOOGL`/
      `KO`/`XOM`) — exactly the intended universe-first order, no dead end
      anywhere.
- [x] Auditor 4 — confirm Sector Selection's opt-out is genuinely untouched
      — done (Wave 2 pass, re-confirmed: `enableFmpSuggestions={false}`
      intact, `SectorSelection.test.tsx` 13/13 passing).
- [x] Auditor 5 — live click-through of new Marketplace landing flow —
      done by the closing synthesis pass: live dev server, `/marketplace`
      screenshot confirms "Search any stock" hero renders above "Today's
      Radar"; clicked a live suggestion (`JNJ`) and confirmed it navigated
      to `/symbol/JNJ` (an honest "Nothing here yet" render there is a mock
      per-symbol-detail data-coverage gap, unrelated to this feature).
- [x] Auditor 6 — labeling consistency vs. Universe Transparency panel —
      done (Wave 2 pass; see walkthrough for full detail — no
      inconsistency found).
- [x] Auditors 7-8 (rollback toggle reverses every touched screen) — done
      by the closing synthesis pass, live: flipped the Settings → General
      → "Search behavior" toggle to "Search your saved list first
      (legacy)", navigated back to `/marketplace`, and confirmed via
      `read_page` that the SAME `O` query now renders the exact legacy DOM
      order (`COST`/`GOOGL`/`KO`/`XOM` unlabeled, then a "Not yet tracked"
      presentation row, then `JNJ`) — byte-identical to the pre-2026-09
      behavior, not just unit-tested but observed live in the real DOM.

## Explicitly NOT in this task list
- Any change to `main.py::_build_universe()` or pipeline universe-resolution logic
- A real multi-collection data model (relabeling only, v1)
- Reconsidering Sector Selection's existing, correct opt-out
