# Decouple Explore From Execute — Task Tracker

> Companion to `decouple-explore-execute_implementation_plan.md`. Save as
> `.claude/decouple-explore-execute_task.md`.

## §0 Dependency Check (blocking — do first)
- [ ] Trace every code path feeding the daemon's autonomous per-cycle universe
- [ ] Trace the options auto-scan's operator-supplied symbol-list override path + gating
- [ ] Confirm no other automated-execution surface exists beyond these two
- [ ] Confirm existing test coverage of this boundary (avoid duplicating)
- [ ] Confirm whether Universe Transparency / Explain This Ticker have landed

## Wave 0 — Scaffold (1 agent)
- [ ] Exhaustive traced-code-paths checklist artifact

## Wave 1 — Parallel (3 agents)
- [ ] **A**: structural regression test (AST/runtime guard)
- [ ] **B**: `docs/architecture/execution-boundary.md` writeup
- [ ] **C**: UI legibility copy on untracked-symbol views (Quick Trade, Screener)

## Wave 2 — Serialized (1 agent)
- [ ] **D**: written policy statement for auto-scan's existing override (doc only, no code change)

## Wave 3 — Docs (1 agent)
- [ ] `CLAUDE.md` / `AGENTS.md` / `GEMINI.md`

## Honesty checklist (verify before merge)
- [ ] Boundary doc claims only what was actually traced this session, live
- [ ] Auto-scan exception documented as-is, not downplayed or overstated
- [ ] Structural test proven via deliberate break-then-revert, not just asserted

## Claude audit protocol (6-8 agents — redundancy is the point here)
- [ ] Auditor 1 — independently re-derive Wave 0's checklist from scratch, compare after
- [ ] Auditor 2 — confirm break-then-revert proof for the structural test
- [ ] Auditor 3 — sentence-by-sentence claim check on the boundary doc vs. live code
- [ ] Auditor 4 — live non-mock render of Wave 1C's UI copy
- [ ] Auditor 5 — re-verify auto-scan's actual settings-flag gating against WP-D's claims
- [ ] Auditor 6 — independently re-run the "any other automated-execution surface" grep
- [ ] (If 8) Auditors 7-8 — full independent second trace, blind to Auditor 1's findings

## Explicitly NOT in this task list
- Any change to `main.py::_build_universe()` or universe-resolution logic
- Removing/restricting the auto-scan's existing symbol-list override
- Any live-order/broker-submission path — this plan is paper-only, full stop
- New discovery UI beyond the copy tie-ins (see companion plans instead)
