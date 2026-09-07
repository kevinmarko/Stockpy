# Jules Dispatch `confirm=True` Hard-Gate — Implementation Plan

## Status: SCOPED, NOT YET APPROVED FOR BUILD

Written from a dedicated research pass against live code (2026-09-07). No code has been
written. This is the artifact to review/edit before any implementation begins.

## 0. Problem statement, as originally framed

`docs/JULES_INTEGRATION.md` and CLAUDE.md already disclose this gap honestly: Jules's
`confirm=True` dispatch gate relies on prompt/skill prose (an agent is instructed not to pass
`confirm=True` without the operator's per-task go-ahead for that exact prompt), not a
hard code-level interception point. Goal as stated: investigate whether a physical
wait/confirm intercept can be implemented at the HTTP/SDK layer.

## 1. Exact current code path (confirmed against live code)

Two consumers (`investyo_mcp_server.py::dispatch_jules_task`, `scripts/jules_dispatch.py`)
are thin wrappers around `data/jules_client.py::dispatch_session(...)`:

```python
def dispatch_session(prompt, source, branch, title, *, force=False, confirm=False):
    if confirm is not True:
        raise JulesConfirmationRequired(...)
    ...
    with _dispatch_lock():                     # real cross-process advisory lock
        dedup_key = _compute_dedup_key(...)     # {UTC day}:{sha256(...)[:16]}
        if not force and _check_dispatch_dedup(dedup_key):
            raise JulesUnavailable(...)          # real same-day dedup
        response = requests.post(..., json={..., "automationMode": "AUTO_CREATE_PR", ...})
        _record_dispatch(...)                    # ledger write AFTER the POST succeeds
        return payload
```

**What is genuinely code-level today**: `JULES_ENABLED`/`JULES_API_KEY` presence, `source`
validated against a fresh `list_sources()` call, a real same-day dedup ledger with a
cross-process lock closing a TOCTOU race. **`confirm` gets nothing beyond
`if confirm is not True: raise`** — no check on who/what set it, no binding to the specific
prompt text, no record of a human having seen the literal prompt before dispatch. The dedup
ledger records `prompt_hash` only *after* the POST succeeds — an audit trail of what was
sent, not a pre-dispatch approval artifact.

The prose layer (`.claude/skills/jules-delegation/SKILL.md`, byte-identical `.agents/`
mirror) is the only enforcement: an LLM reading and following markdown. Nothing stops a
non-compliant or compromised invocation from calling `dispatch_jules_task(..., confirm=True)`
directly.

## 2. The load-bearing finding — read this before choosing an approach

**Every "hard" gate surveyed anywhere in this repo turned out to be a shared-secret or
self-supplied-boolean check, not a human-presence proof** — this is the same finding as the
companion `per_agent_identity_implementation_plan.md`, confirmed independently here:

| Mechanism | What it actually checks |
|---|---|
| Jules `confirm=True` | a plain bool the caller sets itself |
| `HIGH_STAKES_COMMANDS` (kill-switch, forced RH re-login) | an HTTP request-body `confirm` field any caller can set |
| `execution/kill_switch.py::activate()` itself | **no confirmation parameter at all** — the gate exists only in one particular caller (`launch_manifest_command`); calling the module directly bypasses it entirely |
| `DANGEROUS_KEYS` typed-confirmation echo | caller echoes a fixed string trivially reproducible by anyone |
| Live-trade `pending_approval` split (propose vs. approve, two different endpoints) | genuinely splits the calls, but `approve` is still gated by the same shared-secret `require_command_token` — an agent with HTTP+filesystem access to read that token can call both sides itself |
| `mcp_oauth_provider.py`'s human `/login` password | keeps out an unauthenticated *remote* client; irrelevant to a co-resident agent with filesystem access to read `settings.MCP_OAUTH_PASSWORD` |
| Robinhood order-placement confirmation | **the identical gap, already found and documented** (`docs/known_issues/robinhood_confirmation_gate_is_prose_only.md`) — its own mitigation (a pinned test asserting the skill prose hasn't drifted) is explicitly *not* claimed as code enforcement |
| **Robinhood device-approval login** | **the one genuine exception** — the operator taps "approve" on their own physical phone, a channel this codebase (and any agent driving it) has no read/write access to at all. Real, but provided by a third party, not built here. |

**Honest conclusion**: there is no existing in-house pattern in this repo that actually
distinguishes "a human did this" from "an agent with the right token/file-access did this."
An agent with Claude Code's normal filesystem/shell access can read or reproduce any locally-
stored secret or flag. This bounds what's achievable — see ranked options below.

**A second, sharper finding**: nothing in this repo's runtime calls Jules dispatch
automatically today (only reachable via the MCP tool or CLI, both requiring an agent
session or a human at a terminal) — but this environment's own tool surface includes generic
scheduled/cron agent-session capability (`mcp__scheduled-tasks__*`, the `schedule`/`loop`
skills). If a scheduled, unattended Claude Code session had this MCP server wired in and
decided to dispatch, **there would be no human in the conversation to consult at all** — the
prose gate's whole model presumes an interactive human is reachable, and silently does not
apply (not fail-closed, just absent) when that premise is false. Any hardening must account
for the unattended case, not just the interactive one.

## 3. Ranked realistic options (by actual strength, not by gate-appearance)

1. **Strongest — an out-of-band channel the agent structurally cannot read or write**,
   modeled on Robinhood's device-approval flow: the operator retrieves a short-lived
   confirmation code from a channel with no corresponding read/write MCP tool (a push
   notification, a TOTP authenticator app, a value shown only in a UI surface this session
   has no tool access to) and types/pastes it back. Genuinely strong because its security
   rests on *what tool access exists*, not on any value stored on disk. Most expensive —
   this repo would have to build the channel itself; Jules's own API offers nothing like
   this (see §4).
2. **Moderate — mandatory prompt-hash pinning.** Bind the operator's approval to a hash of
   the *exact* prompt/title/branch text; `dispatch_session` refuses on a mismatch. Closes
   the "approved X, dispatched Y" swap risk. Does not stop an agent from generating both the
   prompt and its own matching hash in the same turn — raises the bar for accidental drift,
   not deliberate bypass.
3. **Weak-but-real friction — a rate-limit/cooldown breaker.** `data/jules_client.py` today
   has zero throttle (unlike `data/fmp_client.py`'s circuit-breaker or
   `broker_live_execution_mcp.py`'s existing token-bucket `RateLimiter`). Would make an
   *accidental* loop structurally rare; a single deliberate dispatch defeats it trivially.
4. **Cosmetic — extending the `DANGEROUS_KEYS`-style typed-echo pattern.** Already shown to
   be as easily defeated as `confirm=True` itself; not a real step up.

**Recommended path**: combine (2) + (3) as the practical, buildable increment — genuine
improvement over pure prose at low cost, honestly labeled as "raises the bar," never
oversold as (1)'s strength. Treat (1) as a separate, larger, only-if-operator-wants-it
follow-up given its cost, and explicitly do NOT build (4).

## 4. What Jules's own API and GitHub's mechanics offer

- Jules's API has exactly two `automationMode` values
  (`AUTOMATION_MODE_UNSPECIFIED`/`AUTO_CREATE_PR`) — no draft/staged/reviewer-assignment
  mode to fall back on. This integration was built/tested entirely offline (no live Jules
  API call has ever been made per `docs/JULES_INTEGRATION.md` §6) — any additional Jules-side
  capability beyond what's implemented is unverified either way.
- **The one real, load-bearing lever this repo already has pieces for is on the GitHub
  side**: `.github/CODEOWNERS` already names the operator as owner across `signals/`,
  `execution/`, `sizing/`, `validation/`, `data/`. If GitHub branch protection on the target
  repo requires CODEOWNERS review before merge (a server-side setting, not verifiable from
  this repo's files), then even though Jules opens the PR fully unsupervised, **merging it
  still requires the operator's actual GitHub review** — genuinely enforced outside this
  codebase's and any agent's reach, *provided* the agent's own `gh`/GitHub credentials can't
  bypass branch protection (e.g. an admin token capable of `gh pr merge --admin`). This
  closes the "PR gets merged unreviewed" risk but not the "an unsupervised session got
  dispatched and burned quota/opened a PR in the first place" risk `confirm=True` exists for.

## 5. Explicit scope boundary

**In scope**: prompt-hash pinning + a dispatch cooldown, both inside `data/jules_client.py`;
confirming (not assuming) whether GitHub branch protection on the target repo is actually
configured to require review, as a documented finding either way.

**Out of scope, deliberately, unless the operator explicitly asks for it as a follow-up**:
- Building an out-of-band confirmation channel (option 1) — real but costly; a separate
  scoping decision given the expense.
- Any change to Jules's own API integration beyond what's used today (no draft-mode exists
  to opt into).
- Solving the unattended/scheduled-session case fully — flag it explicitly in documentation
  (§2's second finding) rather than attempt a fix that this plan's own research shows has no
  in-house precedent to build on.

## 6. Open questions for the operator

- **Confirm whether GitHub branch protection with required CODEOWNERS review is actually
  enabled** on the target repo for Jules-authored PRs — this is checkable by the operator
  (or an agent with `gh api` access to repo settings) and materially changes how much residual
  risk remains after this plan's Phase 1.
- **Is the scheduled/cron unattended-dispatch scenario (§2) a real, current risk** (is any
  scheduled task actually configured with this MCP server wired in), or purely theoretical
  today? If real, this plan's scope may need to widen — flag before starting Phase 1.
- **Is option 1 (out-of-band channel) worth its cost** given the honest ranking above, or is
  the operator satisfied with the recommended (2)+(3) increment plus the prose gate as-is?

## 7. Phases

**Phase 1** — Add prompt-hash pinning: an `approve_dispatch(prompt_hash) -> approval_token`
step the operator (or an agent, honestly disclosed as not eliminating the underlying gap)
must call before `dispatch_session` accepts a matching request; `dispatch_session` verifies
the hash matches what was approved. Document explicitly this does not stop a single agent
turn from calling both steps itself — it only prevents an approved-prompt/dispatched-prompt
mismatch.

**Phase 2** — Add a dispatch rate-limiter/cooldown to `data/jules_client.py`, mirroring
`broker_live_execution_mcp.py`'s existing token-bucket `RateLimiter` pattern.

**Phase 3** — Verify (not assume) GitHub branch-protection/CODEOWNERS-review status on the
target repo; document the finding in `docs/JULES_INTEGRATION.md` regardless of outcome.

**Phase 4 (deferred, only on explicit operator request)** — design and cost out an
out-of-band confirmation channel (option 1). Do not build without a fresh, dedicated scoping
pass given its real expense.

## 8. Testing

- Unit test: `dispatch_session` rejects a prompt whose hash doesn't match an approved one.
- Unit test: rate-limiter/cooldown genuinely blocks a rapid second dispatch attempt.
- Explicit test asserting the SKILL.md prose still states plainly that these are
  bar-raising, not bypass-proof, mechanisms — mirroring
  `tests/test_robinhood_e2e.py::TestSkillMdInvariantsPinned`'s existing pattern for the
  analogous Robinhood gap.

## 9. Documentation sync required

- `docs/JULES_INTEGRATION.md` §4 — replace "floats, without attempting" language with what
  actually shipped, and the honest branch-protection finding from Phase 3.
- `CLAUDE.md`/`AGENTS.md` — bullet describing the hash-pinning + cooldown addition and
  explicitly restating the non-goal (this does not stop a single-agent-turn bypass).
- `.claude/skills/jules-delegation/SKILL.md` (+ `.agents/` mirror) — update prose to describe
  the new `approve_dispatch` step if Phase 1 ships.
- `docs/known_issues/robinhood_confirmation_gate_is_prose_only.md` — cross-reference this
  plan as the sibling instance of the same gap class, once resolved here.
