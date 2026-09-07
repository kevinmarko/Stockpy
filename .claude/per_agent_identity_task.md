# Per-Agent Identity & Least Privilege — Task Tracker

> Companion to `per_agent_identity_implementation_plan.md`. **Do not start Phase 1 until
> the operator has explicitly picked Path A or Path B (plan §1/§4) — this is the primary
> open decision, not a formality.**

## §0 — Decision required before any work
- [ ] Operator confirms: Path A (document as not-solvable-here, close via the
      cross-agent-audit-trail plan's attribution work instead) — recommended, OR
- [ ] Operator confirms: Path B (extend OAuth `subject` for the remote/streamable-http
      transport only) — only if a remote/LAN MCP surface is actually in active use

## If Path A chosen
- [ ] Write the "not solvable within this architecture for a filesystem-privileged agent"
      finding into `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`'s open-gap entry
- [ ] Close this backlog item; no code changes
- [ ] Confirm `cross_agent_audit_trail_implementation_plan.md` covers the attribution need

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
