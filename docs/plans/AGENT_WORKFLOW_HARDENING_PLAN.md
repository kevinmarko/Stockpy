# Agent Workflow Hardening: Verification, Decomposition, Hooks

**Status: shipped.** This document was written during implementation (not
before it), so it reflects what was actually built rather than the original
sketch. See this PR's diff for the full change.

## What this is

The operator pasted a 5-point external checklist — "Enforce Mandatory
Verification Loops", "Decompose Work with Artifacts and Dynamic Subagents",
"Direct Artifact Feedback", "JSON Hooks and Knowledge Bases", "Model Routing
and Slash Commands" — and asked for it built out for **both** agents working
this repo (`CLAUDE.md`'s Branch Workflow section: "no fixed multi-agent
domain split... the operator assigns work to each agent per-task"). This PR
is that build: real hook enforcement + a subagent + slash commands on the
Claude Code side, best-effort ports of the same guardrails on the Antigravity
side, and one new cross-cutting doc section (`## Agent Workflow: Verification
& Planning`) codifying the policy in `CLAUDE.md`/`AGENTS.md`.

No engine/signal/execution/runtime code is touched — this is pure
`.claude/`/`.agents/`/docs configuration.

## Where the pieces are

| Point | File | Purpose |
|---|---|---|
| 1. Verification loops | `.claude/hooks/verify_targeted_tests.sh` | `PostToolUse` (`Edit\|Write`), non-blocking: after an edit to a tracked `.py` file, runs its mapped `tests/test_<module>.py` if one exists and surfaces a failure inline via `additionalContext`. |
| 1. Verification loops | `.claude/hooks/verify_before_stop.sh` | `Stop` event, the real enforcement gate: blocks turn-end (`decision:"block"`) while uncommitted `.py` changes have a mapped failing test; releases itself after 2 consecutive blocks per session (marker file keyed on `session_id`) rather than risk a deadlock. |
| 1. Verification loops | `.claude/commands/verify.md` | Slash command: `ruff --select=F821,F822,F823,E9` then `make ci`, mirroring `.github/workflows/ci.yml`'s `test` job. |
| 1. Verification loops | `.claude/commands/verify-webapp.md` | Slash command: launches `npm run dev --prefix webapp`, drives the browser tool against the affected screen, checks console errors — the thing a clean `tsc --noEmit` alone can't prove. |
| 2. Decomposition | `.claude/agents/test-writer.md` | Subagent: given a module name, writes/extends `tests/test_<module>.py` per this repo's fixture/DTO/no-lookahead conventions. `model:` left unset (inherits the calling session's model — this is real authoring work, not a cheap mechanical check). |
| 2. Decomposition | `.agents/agents/test-writer.md` | Same job, ported to Antigravity's agent frontmatter (`subagent: true`, `model: pro` set explicitly, per the checklist's own routing guidance). |
| 4. JSON hooks | `.claude/settings.json` (modified) | Registers the two new `PostToolUse` hooks above plus `sync_agent_docs.sh` (below), and the new `Stop` hook — alongside the two pre-existing guardrail hooks (`block_env_write.sh`, `webapp_typecheck.sh`). |
| 4. JSON hooks | `.agents/hooks.json` | Antigravity's hook config (new file — this side had none before): registers `block_env_write.sh`, `webapp_typecheck.sh`, `sync_agent_docs.sh` against Antigravity's `edit_file\|create_file` matcher. |
| 4. JSON hooks | `.agents/hooks/block_env_write.sh` | Antigravity port of the existing Claude Code `.claude/hooks/block_env_write.sh` guardrail (blocks any edit targeting the literal `.env` file). |
| 4. JSON hooks | `.agents/hooks/webapp_typecheck.sh` | Antigravity port of `.claude/hooks/webapp_typecheck.sh` (runs `npm run --prefix webapp typecheck` after an edit under `webapp/src/**`). |
| Doc auto-sync (see below) | `.claude/hooks/sync_agent_docs.sh` / `.agents/hooks/sync_agent_docs.sh` | `PostToolUse`: copies `CLAUDE.md`↔`AGENTS.md` onto each other whenever either is edited, on both runtimes. |
| Doc auto-sync | `.gitignore` (modified) | Added `!.agents/hooks.json` immediately after the existing `!docs/settings_liveness.json` exception, in the same block of `*.json`-blanket-rule carve-outs — see below. |
| Cross-cutting policy | `CLAUDE.md` / `AGENTS.md` (modified) | New `## Agent Workflow: Verification & Planning` section, inserted right after `## Branch Workflow`, codifying all 5 checklist points as repo policy for whichever agent is working. |
| Retrospective | `docs/plans/AGENT_WORKFLOW_HARDENING_PLAN.md` | This file. |

Point 3 ("Direct Artifact Feedback") and half of point 5 ("model routing")
needed no new files — they're native UX in both tools (Plan-mode/diff
comments in Claude Code, artifact comments in Antigravity) plus one doc
sentence each in the new `CLAUDE.md`/`AGENTS.md` section: steer a plan or
diff via inline comments rather than a fresh re-prompt, and don't default a
subagent doing genuinely complex work to a cheap model tier.

## The `CLAUDE.md`/`AGENTS.md` auto-sync mechanism

The two files are meant to be exact mirrors — `AGENTS.md`'s own first line
is literally `# CLAUDE.md`, a copy-paste artifact that only makes sense if
the intent was byte-identical content. Nothing enforced that. Confirmed live
via `diff CLAUDE.md AGENTS.md` before this PR: the two had already drifted
by one real bullet — the `runtime_flags_writer.py` WRITE-path bullet
(`runtime_flags_writer.py`, the `write_override`/`delete_override` entry
points, the five refusal gates) was present in `CLAUDE.md` and missing
entirely from `AGENTS.md`. That's direct, in-repo proof that "operator/agent
remembers to hand-copy both files" fails in practice — exactly the class of
drift the checklist's knowledge-base point is about.

Fix: `sync_agent_docs.sh` on both sides (`PostToolUse`, matcher
`Edit|Write` / `edit_file|create_file`) — whichever of the two files was
just edited is copied onto the other with a plain filesystem `cp` (not the
Edit/Write tool, which would re-trigger the same hook). This PR's own
`CLAUDE.md` edit proved the mechanism live: writing the new section into
`CLAUDE.md` alone caused the hook to copy it onto `AGENTS.md` automatically,
which also silently absorbed the pre-existing `runtime_flags_writer.py`
drift as a side effect of whole-file mirroring — `diff CLAUDE.md AGENTS.md`
now returns nothing.

This is deliberately whole-file mirroring, not a section-level diff/merge —
simpler, and matches what the two files actually are (one shared knowledge
base with two names). It does not fix the stray `# CLAUDE.md` header line
sitting inside `AGENTS.md`'s first line; that's pre-existing, not introduced
by this PR, and is left as-is rather than quietly changed out of scope.

## Open gap: Antigravity hooks are policy-only

**All 3 `.agents/hooks/*.sh` scripts have been updated to use the exact Antigravity `toolCall.args.TargetFile` schema.** The tool names for file editing in Antigravity are `write_to_file`, `replace_file_content`, and `multi_replace_file_content`.

However, in a live Antigravity IDE runtime, **these PreToolUse/PostToolUse shell hooks do not natively intercept these tools.** The hooks mechanism does not fire for the IDE's built-in file editor tools. 

Separately: **Antigravity has no `Stop`-equivalent blocking gate.** Its `Stop` hook event does not expose semantics to force an agent to continue. 

Because the hooks do not intercept file changes and `Stop` does not support hard-blocking in this environment, the mandatory-verification requirement is **policy-only** on the Antigravity side (via the `CLAUDE.md`/`AGENTS.md` rules), enforced by prompt adherence rather than system-level blocking. Framing this as equivalent to the Claude Code gate would be dishonest; it isn't.

## The `.gitignore` fix

`.agents/hooks.json` would otherwise have been silently excluded by this
repo's existing blanket `*.json` ignore rule — the same problem
`!docs/settings_liveness.json` was already carved out for. `!.agents/hooks.json`
was added immediately after that line, in the same block of exceptions, so
the new Antigravity hook config actually gets committed instead of quietly
never existing in anyone else's checkout.

## What's explicitly NOT done

- **No directory-tree mirroring beyond the one `CLAUDE.md`/`AGENTS.md`
  pair.** Each new `.claude/` capability (a new hook, a new agent, a new
  slash command) still needs a deliberate, hand-ported `.agents/` equivalent
  — the two runtimes' hook/agent JSON schemas genuinely differ (stdin/stdout
  shape, tool-call arg names, frontmatter fields), so a blind directory copy
  would ship broken config rather than a working port.
- **No attempt to auto-verify the Antigravity hooks live.** The scripts were
  written against the best available documentation and a defensive scanning
  strategy chosen specifically because the schema is unconfirmed — but that
  is a design choice made to tolerate an unverifiable target, not a
  substitute for actually testing against a real Antigravity session.
