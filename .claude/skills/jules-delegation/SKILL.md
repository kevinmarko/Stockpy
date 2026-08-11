---
name: jules-delegation
description: >-
  Delegate a coding task to Google's Jules autonomous agent, which will
  write code against a connected GitHub repo and open a real PR when
  finished. Use when the operator explicitly asks to delegate work to
  Jules, or asks for a "fire and forget" task that doesn't need close
  in-session supervision. Requires JULES_ENABLED=true and a real
  JULES_API_KEY to be configured -- degrades to a clear "not configured"
  message otherwise. NEVER dispatch (confirm=True) without the operator's
  explicit go-ahead for that exact prompt in the current conversation.
---

# Jules Delegation (fire-and-forget, human-gated dispatch)

This skill hands a coding task off to Google's Jules — a third-party
autonomous agent that, given a prompt and a connected GitHub repo/branch,
writes code and opens a real, unsupervised PR when it finishes. In this
integration's hardcoded `AUTO_CREATE_PR` mode there is no human review gate
before the PR is opened; review happens at merge time, like any other PR.

**Jules vs. dispatching Claude Code subagents in this session.** This exact
repo just landed a real PR (#681) by dispatching multiple parallel Claude
Code subagents (the `Agent` tool) within one session — that is the other way
to delegate a coding task, and it is not the same tool for the same job.
Contrast them honestly with the operator when the choice isn't obvious:

- **Jules** is fire-and-forget. Once dispatched, the session runs entirely
  outside this conversation; there is no supervised iteration, no chance to
  redirect it mid-task, and no visibility into its result until a PR shows
  up on GitHub. Good when the operator explicitly wants that flavor — the
  task should happen on Jules's own time, independent of this session's
  lifetime, and doesn't need anyone watching it work.
- **Claude Code subagents** (the `Agent` tool) keep the primary agent in the
  loop for the whole task. Progress, intermediate output, and test results
  are all visible in-session, and a subagent's work can be verified, iterated
  on, and fixed before anything ever lands. Prefer this whenever close
  supervision matters — which, for anything touching engines, signals,
  execution, sizing, validation, or other runtime/trading logic per this
  repo's own CLAUDE.md, is most of the time.

Default to recommending Claude Code subagents unless the operator has a
specific reason to want the fire-and-forget behavior Jules provides.

## Prerequisites (verify before doing anything else)

1. Call `list_jules_sources`. If the response has a "not configured"
   shape (i.e. `settings.JULES_ENABLED` is not `True`, or
   `settings.JULES_API_KEY` is unset), **stop** and tell the operator exactly
   what to set (`JULES_ENABLED=true` and a real `JULES_API_KEY` in `.env`) —
   do not proceed any further into this skill.
2. From that same `list_jules_sources` response, confirm the repo the
   operator wants actually appears in the connected-sources list. Never call
   `dispatch_jules_task` against a `source` you have not just seen returned
   by `list_jules_sources` in this conversation.

## Hard stops (refuse and explain — do not proceed)

- **Never call `dispatch_jules_task` / `create-session` with `confirm=True`
  (or `--confirm`) unless the operator has explicitly approved THIS EXACT
  prompt, title, branch, and target repo in the CURRENT conversation.** A
  prior, more general instruction — "set up Jules," "go ahead and use Jules
  for X eventually" — is **not** authorization to dispatch a specific
  session later without asking again. Every dispatch is its own
  explicit-permission event, the same principle as this agent's own standing
  rule that publishing or modifying public content needs per-action
  confirmation, never a standing blanket approval.
- **Never dispatch against a `source` the operator didn't specify or
  confirm.** Always show the operator what `list_jules_sources` returned and
  let them pick or confirm it — don't guess which connected repo they mean,
  even if only one is connected.
- **`confirm=True` is a code-level boolean, not a substitute for actually
  getting the operator's yes.** The tool itself doesn't force a
  back-and-forth the way the Robinhood execution skill's per-order preview
  loop does — treat asking as mandatory anyway, and say so plainly if the
  operator seems to be relying on the flag alone as the safety gate. State
  this gap honestly rather than treating the tool's own gate as sufficient.
- **Jules opens PRs, never merges them.** Never treat a Jules-created PR as
  pre-approved for merge — it still needs the same review any other PR in
  this repo gets, including the branch-workflow rules in this repo's own
  CLAUDE.md.

## Workflow

1. **Confirm prerequisites** — run the Prerequisites checks above. Stop if
   Jules isn't configured or the target source isn't connected.
2. **Call `list_jules_sources`** and show the operator the connected repos.
   Get their explicit confirmation of which one is the dispatch target.
3. **Draft the exact prompt, title, and branch** with the operator (or per
   their explicit instruction), then show the drafted content back to them
   verbatim before dispatching. Do not paraphrase or summarize what will
   actually be sent — the operator needs to approve the literal text.
4. **Only once the operator has said yes to that exact content**, call
   `dispatch_jules_task(prompt, title, source, branch, confirm=True)` (or
   the CLI equivalent, `scripts/jules_dispatch.py create-session --prompt
   ... --title ... --source ... --branch ... --confirm`).
5. **Report the dispatch and stop there.** Tell the operator the session was
   dispatched and that they'll need to check GitHub for the resulting PR —
   this skill does not poll or wait for it, and no follow-up happens
   automatically.

## See also

`docs/JULES_INTEGRATION.md` for the full setup/safety writeup (the file may
not exist yet in every worktree depending on build timing, but it is the
authoritative reference once merged).
