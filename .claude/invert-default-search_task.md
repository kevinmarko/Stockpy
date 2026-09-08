# Invert The Default — Task Tracker

> Companion to `invert-default-search_implementation_plan.md`. Save as
> `.claude/invert-default-search_task.md`.

## §0 Dependency Check (blocking — do first)
- [ ] Enumerate all 9 `SymbolInput` call sites' current state
- [ ] Confirm which sites (beyond Sector Selection) share its dead-end risk
- [ ] Confirm current `Marketplace.tsx` structure/tile ordering
- [ ] Confirm whether Universe Transparency plan has landed (shared terminology)
- [ ] Confirm rollback-toggle mechanism (Feature Flags registry vs. plain webapp context)
- [ ] Confirm Quick Trade / Symbol Screener's current copy for consistent labeling

## Wave 0 — Scaffold (1 agent)
- [ ] Rollback toggle mechanism, zero behavioral change yet

## Wave 1 — Parallel (4 agents)
- [ ] **A**: Data Explorer + Signal Breakdown + Forecast Viewer default flip
- [ ] **B**: Sentiment Dynamics + Pairs Radar ×2 default flip
- [ ] **C**: Cache Long/Short + Paper Broker Quick Trade + Universe Manager default flip
- [ ] **D**: Marketplace/nav reframing ("Search any stock" primary)
- [ ] Confirm: **Sector Selection explicitly excluded from A/B/C** — its opt-out is untouched

## Wave 2 — Serialized (1 agent)
- [ ] "Saved" labeling consistency pass across Marketplace / Universe Manager / Universe Transparency panel

## Wave 3 — Docs (1 agent)
- [ ] `CLAUDE.md` / `AGENTS.md` / `GEMINI.md`
- [ ] `helpContent.ts`

## Claude audit protocol (6-8 agents)
- [ ] Auditors 1-3 — per Wave-1 cluster: live backend check for dead-end/empty-result regressions
- [ ] Auditor 4 — confirm Sector Selection's opt-out is genuinely untouched
- [ ] Auditor 5 — live click-through of new Marketplace landing flow
- [ ] Auditor 6 — labeling consistency vs. Universe Transparency panel
- [ ] (If 8) Auditors 7-8 — rollback toggle actually reverts every touched screen, tested by flipping it post-hoc

## Explicitly NOT in this task list
- Any change to `main.py::_build_universe()` or pipeline universe-resolution logic
- A real multi-collection data model (relabeling only, v1)
- Reconsidering Sector Selection's existing, correct opt-out
