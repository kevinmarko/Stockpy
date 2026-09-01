---
name: jules-delegation
description: >-
  Jules (Google's coding agent) can only audit/review an existing PR or
  codebase -- it cannot write new code or open a PR from a prompt alone.
  This skill's original write/PR-delegation capability has been permanently
  disabled (data/jules_client.py::dispatch_session() now always raises
  JulesCapabilityNotAvailable) and no working audit-dispatch path exists in
  this repo yet. The only thing this skill can still do is call
  list_jules_sources to enumerate GitHub repos connected to the operator's
  Jules account -- a read-only listing, nothing else. Use only if the
  operator wants that listing, or explain the situation if they ask to
  delegate a coding task to Jules.
---

# Jules Delegation

**Current status: mostly non-functional.** Jules's real, confirmed capability is auditing/reviewing
an existing PR or codebase — it cannot write new code or open a new pull request from a prompt
alone, and it cannot "build something from nothing." This repo's original integration was designed
around the opposite (incorrect) assumption — that Jules would write code and open a real,
unsupervised PR given just a prompt and a connected repo/branch. That assumption was wrong, and the
write/dispatch path it built (`dispatch_jules_task` / `scripts/jules_dispatch.py create-session`)
has been permanently disabled: `data/jules_client.py::dispatch_session()` now unconditionally
raises `JulesCapabilityNotAvailable` as the first thing it does, before any network call. Nothing
in this repo can dispatch a Jules session that writes code or opens a PR anymore, regardless of
`confirm=True`/`--confirm`.

**No replacement exists yet either.** There is currently no implemented path anywhere in this
codebase for Jules's actual capability — having it audit or review an existing PR or codebase.
Building one would be new work (new client code, a new MCP tool or CLI subcommand, new tests); this
skill does not attempt it and should not imply otherwise.

**The one thing that still genuinely works:** `list_jules_sources` (MCP tool) /
`scripts/jules_dispatch.py list-sources` (CLI) — a plain read that enumerates which GitHub repos are
connected to the operator's Jules account. It makes no code-writing or review claim.

## If the operator asks to delegate a coding task to Jules

Tell them plainly: Jules cannot write new code or open a PR from a prompt in this integration —
that capability was based on an incorrect assumption about what Jules can do and has been disabled.
Point them at Claude Code subagents (the `Agent` tool) instead for delegating implementation work
within this session — see "Jules vs. Claude Code subagents," below, for how the two actually
compare now that only one of them can do the job.

## If the operator asks Jules to audit or review something

Say so honestly: no working dispatch path for that exists in this repo yet. It would need new code
to build, which is out of scope for this skill as it stands today.

## Jules vs. Claude Code subagents

These are no longer two ways to delegate the same kind of task — only one of them can write code
and land it in this repo.

- **Claude Code subagents** (the `Agent` tool) are the only in-repo mechanism for delegating
  implementation work. Progress, intermediate output, and test results are all visible in-session,
  and a subagent's work can be verified, iterated on, and fixed before anything ever lands. Use this
  for anything that needs code written — which, per this repo's own CLAUDE.md, should get close
  supervision for anything touching engines, signals, execution, sizing, validation, or other
  runtime/trading logic.
- **Jules**, in this repo, can currently do nothing beyond listing the operator's connected GitHub
  sources. It is not a fire-and-forget code-delegation option here, despite how it may have been
  described previously.

## What still works: listing connected sources

1. Call `list_jules_sources`. If the response has a "not configured" shape (i.e.
   `settings.JULES_ENABLED` is not `True`, or `settings.JULES_API_KEY` is unset), tell the operator
   what to set (`JULES_ENABLED=true` and a real `JULES_API_KEY` in `.env`) if they still want the
   listing.
2. Show the operator the connected repos returned. That is the full extent of what this skill can
   do — there is no next step that writes code, opens a PR, or triggers a review of anything.

## Hard stop

- **Never call `dispatch_jules_task` / `create-session`, with or without `confirm=True`/`--confirm`.**
  It will raise `JulesCapabilityNotAvailable` and accomplishes nothing — do not present calling it
  as a real option to the operator, and do not attempt it "to see what happens."

## See also

`docs/JULES_INTEGRATION.md` for the full corrected setup/capability writeup.
