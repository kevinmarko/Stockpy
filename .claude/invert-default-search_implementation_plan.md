# Invert The Default — Universe-First Search — Implementation Plan

> Save as `.claude/invert-default-search_implementation_plan.md`. Written
> outside a repo-connected session — see AGENT HANDOFF NOTES. Structured for
> a **Gemini Antigravity build / Claude Code audit** split, mirroring this
> repo's own wave-based multi-agent convention.

## Status: DRAFT — §0 not yet completed against live code. **This is the
riskier of the two plans in this pair — read §2 and §6 before assigning it.**

## 0. Context & problem statement

Today, discovery in this codebase defaults to curated-first: `SymbolInput`
suggests from the tracked universe first, with FMP-wide matches demoted to
a "Not yet tracked" secondary section (`enableFmpSuggestions`, 2026-08). The
Symbol Screener and Quick Trade panel already let you reach anything, but
they're destinations you navigate *to* — the ambient, everywhere-present
`SymbolInput` combobox still leads with "what you already track." This plan
flips that emphasis: search leads with the whole market, and the tracked
universe becomes a labeled, secondary "Saved" filter — without changing
what the *pipeline* evaluates.

## 1. Goal

Remove the "stuck inside a fence" feeling by making the ambient search
experience universe-first by default, while explicitly preserving every
place that default is currently wrong on purpose.

## 2. Explicit scope boundary — read before assigning this plan

**In scope:** default ordering/labeling in `SymbolInput` and its 9 call
sites, Marketplace/nav framing, a rollback toggle.

**Out of scope, deliberately, and this is the important part:**
- **No change to `main.py::_build_universe()`, `compute_tracked_universe()`,
  or any pipeline universe-resolution logic whatsoever.** This plan is
  entirely presentation-layer. If any work package starts touching what the
  pipeline actually evaluates, that is scope creep into fail-closed/
  single-source-of-truth territory this plan was never reviewed against —
  stop and flag it.
- **No new "multiple named collections" data model.** "Saved collections"
  in this plan means *relabeling* the existing `DEFAULT_TICKERS`/watchlist
  mechanism, not building real multi-list support. That's a genuinely
  separate, larger feature — flag it as a v2 idea, don't fold it in here.
- **`SectorSelection.tsx`'s existing opt-out is preserved, not
  reconsidered.** Per `CLAUDE.md`'s widened-symbol-lookup bullet, this
  screen already sets `enableFmpSuggestions=false` deliberately, because
  `GET /sector/selection` only reads persisted DB state and an untracked
  symbol there is "a guaranteed honest-empty dead end." This is a real,
  already-solved CONSTRAINT #4-adjacent problem — inverting the *general*
  default must not silently override this screen's specific, correct
  exception. Any work package touching `SymbolInput` call sites must treat
  this as a template for "does this screen have a reason to be different,"
  not a one-off to bulldoze.

## 3. §0 dependency check — REQUIRED before any code, not yet done

- [ ] Enumerate the current state of all 9 `SymbolInput` call sites (Data
      Explorer, Signal Breakdown, Forecast Viewer, Sentiment Dynamics,
      Sector Selection, Pairs Radar ×2, Cache Long/Short's
      `ConfiguratorWizard`, Paper Broker's Quick Trade, Universe Manager) —
      confirm which ones, beyond Sector Selection, have a similarly good
      reason to keep tracked-first ordering. Do not assume Sector Selection
      is the only exception; confirm.
- [ ] Confirm current `Marketplace.tsx` structure/tile ordering.
- [ ] Confirm whether the companion **Universe Transparency** plan has
      landed — this plan's "Saved Collections" framing should visually tie
      to that panel's three-count display if it exists, rather than
      introduce a second, inconsistent way of explaining the same tracked/
      forecast/coverage distinction.
- [ ] Confirm whether this repo's existing Feature Flags registry
      (`CLAUDE.md`'s 2026-08-07 update: "added a new Feature Flags screen +
      registry to prevent silent fail-closed behavior") is the right
      mechanism for a purely-frontend UX-ordering rollback toggle, or
      whether that registry is scoped specifically to the
      write/execution-gate flags it already catalogs and a plain webapp
      constant/local-storage-free React context is more appropriate here —
      genuinely open, don't assume either way.
- [ ] Confirm `Quick Trade — Any Symbol`'s and Symbol Screener's current
      framing/copy, since this plan's Marketplace work package should
      reference them consistently rather than introduce new competing
      terminology for the same "search anything" capability.

## 4. Proposed UX

- `SymbolInput`: default ordering flips to FMP-wide results first, with
  currently-tracked matches shown under a "Saved" label (renamed from
  today's implicit "tracked universe" framing) — **per-call-site**, driven
  by an explicit allowlist/denylist decided in §0, never a single blanket
  flip.
- Marketplace: leads with "Search any stock" as the primary action;
  "Your Saved Universe" (or whatever Universe Transparency's panel calls
  it) becomes a secondary tile, not the landing default.
- Quick Trade and Symbol Screener are already universe-first by design
  (confirmed via `CLAUDE.md`) — this plan's job here is consistent
  labeling/discoverability, not new plumbing.
- A rollback toggle (mechanism TBD per §0) lets the operator revert to
  today's tracked-first default without a redeploy while living with the
  change for a while.

## 5. Multi-agent build plan (Antigravity)

| Wave | Agent(s) | Work package | Files (expected, confirm in §0) | Depends on |
|---|---|---|---|---|
| 0 | 1 | **Scaffold** — the rollback toggle mechanism (per §0's open question), threaded as a prop/context default, changing nothing yet | New flag/context, zero behavioral change | — |
| 1 | A, B, C | **Per-screen `SymbolInput` default flip**, split across the 9 call sites in clusters of ~3 each (e.g. A: Data Explorer + Signal Breakdown + Forecast Viewer; B: Sentiment Dynamics + Pairs Radar ×2; C: Cache Long/Short + Paper Broker Quick Trade + Universe Manager). **Sector Selection is explicitly excluded from all three clusters** — its existing opt-out is preserved, not touched, per §2. | `webapp/src/screens/*.tsx` (9, minus Sector Selection) | Wave 0 |
| 1 | D | **Marketplace/nav reframing** — "Search any stock" as primary, saved universe demoted | `Marketplace.tsx` and nav config | Wave 0 |
| 2 | E | **"Saved" labeling pass** — consistent terminology across Marketplace, Universe Manager, and wherever Universe Transparency's panel (if landed) already uses different words for the same three-way distinction | Cross-cutting copy/labels only, no logic | Wave 1 |
| 3 | F | **Docs** | `CLAUDE.md`/`AGENTS.md`/`GEMINI.md`, `helpContent.ts` | Wave 2 |

**Total build agents: 7** (1 scaffold + 3 SymbolInput clusters + 1
Marketplace + 1 labeling pass + 1 docs). Note Wave 1's three
`SymbolInput`-cluster agents are genuinely parallel-safe *only if* each
cluster's screen files are disjoint — confirm no two clusters share a file
before assigning (e.g. if any screen imports/re-exports something another
cluster's screen also touches, that pair needs to move to the same agent or
be serialized).

## 6. Why this plan is riskier than its companion, stated plainly

Unlike Explain This Ticker (additive, new surface), this plan changes an
**existing, working default** across nine screens plus navigation. The
concrete failure mode already has one confirmed precedent in this exact
codebase: Sector Selection's opt-out exists *because* a blanket default
produces "a guaranteed honest-empty dead end" on a screen that only reads
persisted state. §0 explicitly requires checking every other call site for
the same risk before flipping anything — treat that check as the actual
hard part of this plan, not a formality. The rollback toggle in Wave 0 is
not optional scaffolding; it is required precisely because this is a
default change to something people already use daily, and the honest
prediction is that at least one call site's "right default" won't be
obvious until it's live.

## 7. Claude audit protocol (post-build, before merge)

| Auditor | Scope | Method |
|---|---|---|
| 1-3 | Wave 1 clusters A/B/C | For each of the 9 (minus Sector Selection) call sites: does the new default produce a real, populated result on a live backend for a realistic query, or does it silently produce an empty/dead-end state the old default avoided? This is the exact failure class §6 names — check for it explicitly, per screen, not just "does it compile." |
| 4 | Sector Selection (regression check) | Confirm its opt-out is untouched and still behaves exactly as before — this is the one screen every other auditor should assume was correctly left alone, but someone should still verify it wasn't accidentally swept into a cluster. |
| 5 | Marketplace/nav (Wave 1D) | Live click-through of the new landing flow, not a static review |
| 6 | Labeling consistency (Wave 2E) | Confirm "Saved"/tracked/forecast-covered terminology matches Universe Transparency's panel if it exists — inconsistent naming for the same concept across two features shipped close together is a real, avoidable confusion this audit should catch |
| 7-8 (if 8-agent team) | Rollback toggle | Confirm the toggle actually reverts every touched screen's behavior, not just the ones the toggle's author remembered to wire it into — test by flipping it after the fact against the full touched-file list, not just re-reading the diff |

## 8. Testing

- Component test per `SymbolInput` call site: default ordering matches
  intended cluster assignment, Sector Selection's opt-out is unchanged.
- Marketplace navigation test: primary CTA is the search action.
- Rollback toggle test: flipping it reproduces today's exact pre-change
  behavior across all touched screens, not a subset.

## 9. Documentation sync required

`CLAUDE.md`/`AGENTS.md`/`GEMINI.md` mirror (this is exactly the kind of
webapp-default change the file's existing per-feature bullet convention
covers), `helpContent.ts` if any TabGuide copy referenced the old default
framing.

## AGENT HANDOFF NOTES

- Authored with no Desktop Commander / repo filesystem access. The 9-call-
  site enumeration and the Sector Selection exception are both sourced
  directly from `investyo:get_doc("CLAUDE.md")` (commit `e2a8dcb6`,
  2026-09-07) prose — real, not assumed — but which *additional* call sites
  (if any) share Sector Selection's specific failure mode is genuinely
  unverified and is §0's single most important open item.
- This plan's Wave 2 labeling pass assumes the Universe Transparency plan's
  terminology as the shared vocabulary. If that plan hasn't shipped yet,
  Wave 2 should either wait for it or explicitly define its own terms and
  flag the eventual reconciliation as follow-up work — don't let two
  features invent two different names for "tracked vs. forecast-covered vs.
  everything else" independently.
