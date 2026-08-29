# Docs: Agentic Trading Safety Framework reference

This plan corresponds to the "Gemini drafts an Agentic Trading Safety Framework doc, Claude audits it" task.

## Approach
1. Verify Jules integration via `scripts/jules_dispatch.py list-sources` (Phase 0).
2. Draft the Agentic Trading Safety Framework document incorporating Stockpy capabilities, deterministic limits, and known issues.
3. Add document links to `docs/README.md` and `CLAUDE.md`.
4. Perform an extensive multi-agent verification pass (Phase 3 Audit) against `settings.py`, `execution/risk_gate.py`, and other referenced architecture files to ensure zero fabrication.
5. Create PR.

## Addendum: documentation-update step omitted AGENTS.md

CLAUDE.md's Agent Workflow section requires every Implementation Plan to identify which of
`CLAUDE.md`/`AGENTS.md`/other `docs/` files need touching and scope those edits into the plan
itself. Step 3 above named only `docs/README.md` and `CLAUDE.md`, not `AGENTS.md` — even though
CLAUDE.md and AGENTS.md are meant to stay an exact mirror. A real `AGENTS.md` mirror row was in
fact needed for this PR and had to be added in a separate, later commit
(`bd96dcce docs: mirror new doc-index row to AGENTS.md (sync_agent_docs.sh gap)`) after the PR
was already open. See `.claude/agentic_safety_framework_docs_task.md`'s "Verify plan-conformance" checklist item
for the full detail; left here as a corrective addendum rather than rewriting step 3, since
step 3 is a historical record of what was actually scoped at the time.
