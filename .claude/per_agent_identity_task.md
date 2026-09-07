# Per-Agent Identity & Least Privilege — Task Tracker

> Companion to `per_agent_identity_implementation_plan.md`. **Resolved 2026-09-07: operator
> confirmed Path A.**

## §0 — Decision (resolved)
- [x] Operator confirms: Path A (document as not-solvable-here, close via the
      cross-agent-audit-trail plan's attribution work instead) — **confirmed 2026-09-07**

## Path A — done
- [x] Write the "not solvable within this architecture for a filesystem-privileged agent"
      finding into `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`'s open-gap entry (item 2 +
      open-gaps backlog, both updated 2026-09-07)
- [x] Close this backlog item; no code changes
- [x] Confirm `cross_agent_audit_trail_implementation_plan.md` covers the attribution need
      (it does — that plan is unaffected by this closure and remains the path forward for
      the attribution half of the original ask)

## If Path B chosen
- [ ] Phase 1: wire `subject` from an issued OAuth token through to read-tier logging/telemetry
- [ ] Phase 2: real per-subject authorization on one pilot remote-only capability
- [ ] Phase 3: explicitly deferred — do not build without a fresh scoping pass
- [ ] Test: OAuth `subject` end-to-end propagation
- [ ] Test: explicit negative test proving stdio transport is unaffected

## Doc sync
- [ ] `CLAUDE.md`/`AGENTS.md` bullet stating which path was taken and why
- [ ] `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` open-gap entry updated
- [ ] `docs/architecture/observability-and-apis.md` if Path B ships

## Explicitly NOT in this task list
- Anything framed as containing a filesystem-privileged coding agent (Claude Code,
  Antigravity) — not achievable via a token/identity layer, see plan §1
- A new user/tenant data model
- Rotating/multiplexing the existing shared-secret tokens into per-agent variants
- Any change to the stdio MCP transport's trust model
