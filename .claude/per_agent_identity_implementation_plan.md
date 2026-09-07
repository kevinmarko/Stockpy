# Per-Agent Identity & Least Privilege — Implementation Plan

## Status: RESOLVED 2026-09-07 — operator confirmed Path A (no build). See `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`'s item 2 and open-gaps backlog for the closure record.

Written from a dedicated research pass against live code (2026-09-07). No code has been
written. This is the artifact to review/edit before any implementation begins.

## 0. Problem statement, as originally framed

Handover-doc framing: "the platform token-gates access using single shared API-surface
tokens (`FOLLOW_API_TOKEN`, `ORCHESTRATOR_DAEMON_TOKEN`). There is no per-caller subject
claim. An action taken by one compromised agent is indistinguishable from any other caller."
Goal as stated: "issue distinct identities for Claude, Antigravity, and Jules with granular,
least-privilege scoping."

**This document exists to surface the central finding before you approve any build**: as
literally framed, the goal is not achievable for the threat model it names, and building it
anyway would produce something that *looks* like a security boundary without being one. Read
§1 before deciding whether/how to proceed.

## 1. The load-bearing finding — read this first

`docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` already names this exact gap almost verbatim —
it appears to be this task's origin document, and is worth reading directly before anything
else.

**How each caller actually reaches this platform today:**
- **Claude Code** (this session) connects to `investyo_mcp_server.py` via a **local stdio
  subprocess** — no HTTP, no bearer token, same OS user/filesystem permissions as whichever
  agent launched it. It can `cat .env` and read every `SECRET_KEYS` token in cleartext, or
  edit `api/auth.py`'s `require_*` functions directly, or call the underlying Python functions
  bypassing FastAPI/MCP entirely.
- **Antigravity** works on the *same repo checkout*, same git/filesystem access, distinguished
  from Claude Code today only by a hook-*enforcement* gap (no blocking `Stop` hook — "policy-
  only"), never by a different credential or access boundary.
- **Jules** never calls any of this platform's own APIs at all — the relationship runs the
  opposite direction (Stockpy calls Jules's API), and Jules interacts with the repo
  exclusively via opening a GitHub PR, reviewed like any other PR. **Jules is categorically
  not an inbound caller of the token-gate model** — irrelevant to this plan by construction,
  not merely unenforced against it.

**The central, honest conclusion**: a per-agent identity model over the HTTP/MCP token gates
would deliver two things — **(a) attribution/telemetry** (a durable "which agent/session did
this" record, useful for post-hoc diagnosis — this is the `cross_agent_audit_trail` plan's
job, not this one's) and **(b) genuine access control only for caller classes that lack
filesystem/process access to the running server in the first place** — a remote
`streamable-http` MCP client, a LAN/Tailscale caller of `state_api.py`/`control_api.py`, a
browser hitting the Pilots PWA backend, or a named human on the OAuth multi-user login flow.

**It would NOT contain a coding agent (Claude Code, Antigravity, or any future one) running
with today's normal write access to this git checkout**, because that agent can always route
around the check at its source rather than through it. Every existing "hard" gate surveyed
in this repo (kill-switch activation, the `HIGH_STAKES_COMMANDS` `confirm` param, the
`DANGEROUS_KEYS` typed-echo, the live-trade-proposal approve/reject split) reduces to the
identical pattern: a shared secret or a caller-supplied boolean that the same
filesystem-privileged agent can always read or reproduce itself. The one genuine exception
found anywhere in this codebase — Robinhood's device-approval login, where the operator taps
"approve" on their own physical phone — works *because it's provided by a third party this
codebase has no read/write access to at all*, not because of anything this platform built.

**Recommendation: re-scope the goal before building anything.** Do not build a per-agent
HTTP-identity layer under the banner of "containing a compromised coding agent" — it will not
do that, and shipping it under that framing would be a false sense of security, arguably worse
than the disclosed gap that exists today. The two honest paths forward:

- **Path A (recommended, smaller)**: treat this as closed by the
  `cross_agent_audit_trail` plan (attribution) plus explicit documentation that containment
  of a filesystem-privileged coding agent is **not solvable within this architecture** — the
  actual control point for that risk is process/sandbox isolation (what runs the agent, what
  filesystem access it's granted), not an application-layer token, and is out of scope for
  this codebase to build. Recommend closing this backlog item with that documented finding
  rather than building a system.
- **Path B (larger, only if the operator explicitly wants it anyway)**: build genuine
  per-caller identity for the callers where it IS a real boundary — remote/tunneled MCP
  connections and any future LAN-exposed API surface — reusing the already-existing,
  already-tested `mcp_oauth_store.py::OAuthUser`/`subject`-claim infrastructure (built for
  `MCP_OAUTH_MULTI_USER_ENABLED`, currently unconsumed downstream — "nothing downstream reads
  it yet," per three independent docs). This is real, valuable work, but it is a different
  goal than the one named in the handover doc: it hardens the boundary against a genuinely
  untrusted remote caller, not against a coding agent that already has a stdio connection and
  filesystem access.

## 2. Full token-gate inventory (for reference regardless of which path is chosen)

Central chokepoint: `api/auth.py` — `require_read_token`, `require_write_token`,
`require_stream_token`, `make_command_token_guard`. All use `hmac.compare_digest`.

| Token | Checked in | Fail posture | Gates |
|---|---|---|---|
| `STATE_API_TOKEN` | `require_read_token`/`_stream_token`, `_check_ws_token` | fail-open on loopback (read) / fail-closed (write) | read-only state/data, some misc writes |
| `ORCHESTRATOR_DAEMON_TOKEN` | `require_orchestrator_command_token` | fail-closed | pipeline trigger, interval, daemon restart, `"command"` job type (kill-switch, forced RH re-login, `confirm=True`-gated) |
| `FOLLOW_API_TOKEN` | `require_follow_command_token` | fail-closed | follow writes + ~20 other command endpoints (automation, brokerage connect, RAG query, FIX gateway, decisions, backfill) |
| `MCP_HTTP_BEARER_TOKEN` | `investyo_mcp_server.py`'s ASGI middleware | fail-closed (server won't start without it) | entire streamable-http MCP transport, undifferentiated — doesn't apply to default stdio/sse |
| `MCP_OAUTH_PASSWORD`/`MCP_OAUTH_MULTI_USER_ENABLED` | `mcp_oauth_provider.py` `/login` | fail-closed by design | same MCP transport, alternate auth — **the one place a real per-caller `subject` claim already exists** |
| `PROMPT_REGISTRY_*` | `prompt_registry/*.py` | degrade/fail-closed | remote prompt-registry manifest, unrelated capability class |
| — | `execution/fix_gateway.py` | n/a | no dedicated token; rides `FOLLOW_API_TOKEN` + `FIX_GATEWAY_ENABLED` |
| `JULES_API_KEY` | `data/jules_client.py` | n/a | **outbound** credential (Stockpy → Jules), not an inbound gate at all |

`make_command_token_guard`'s docstring already states the design intent: per-surface scoping
(FOLLOW vs ORCHESTRATOR vs STATE), not per-caller — anyone holding `FOLLOW_API_TOKEN` can do
everything all ~20+ endpoints under it allow, regardless of which agent they are.

**Confirmed genuinely single-operator, not multi-tenant** (multiple docs state this
explicitly — `docs/plans/oauth_multi_user_plan.md`, `docs/architecture/data-layer.md`,
`docs/plans/MCP_EXPANSION_PLAN.md`'s explicit warning against fabricating a user-identity
system that doesn't exist elsewhere in this codebase). Least privilege here means
agent-vs-agent isolation, never user-vs-user multi-tenancy — there's no tenant dimension to
hang a "which user" model off of.

## 3. Explicit scope boundary (assuming Path B is chosen — see §1)

**In scope**: extend the existing OAuth `subject`/`client_name` mechanism to actually be
*read* by at least one downstream gate (today it's issued but unconsumed), scoped to the
`streamable-http`/remote transport only.

**Out of scope, deliberately**:
- Anything framed as containing a filesystem-privileged coding agent — see §1's finding.
- Any change to the stdio transport's trust model (Claude Code's/Antigravity's normal
  operating mode) — there is no meaningful boundary to add there.
- A new user/tenant data model — explicitly rejected precedent exists
  (`docs/plans/MCP_EXPANSION_PLAN.md`).
- Rotating/multiplexing the existing shared-secret tokens (`FOLLOW_API_TOKEN` etc.) into
  per-agent variants — this would be Path A's opposite: more complexity for the same
  non-containment property, since a filesystem-privileged agent still reads `.env` directly
  regardless of how many distinct token values exist there.

## 4. Open questions for the operator — resolve before implementation

- **Confirm Path A vs Path B.** This is the primary decision this plan exists to force. Path
  A costs nothing beyond documentation (recommended default). Path B is real, scoped,
  bounded work, but only worth doing if the operator actually plans to expose a remote/LAN
  MCP surface or the OAuth multi-user connector in a way where caller identity matters today
  — if that surface isn't in active use, this is speculative hardening.
- If Path B: which specific gate(s) should start consuming `subject`? (`require_read_token`
  is the obvious first candidate since it already has a loopback-fail-open branch that could
  be tightened per-subject.)

## 5. Phases (Path B only — do not start without operator confirmation per §4)

**Phase 1** — Wire `subject` from an issued OAuth token through to at least one read-tier
endpoint's logging/telemetry (not yet an access-control decision — just prove the plumbing
works and is visible in logs/audit).

**Phase 2** — If Phase 1 proves useful, add a genuine authorization check for one real
scoped-down remote capability (e.g. a read-only `subject` allowlist for
`streamable-http` callers). Small, single-endpoint pilot before any broader rollout.

**Phase 3 (explicitly deferred, do not build without a fresh, separate scoping pass)** — any
extension to the stdio-transport/coding-agent trust model. Per §1, this is very likely a
dead end; re-scope from scratch (process isolation, sandboxing, not tokens) if this is ever
revisited.

## 6. Testing (Path B)

- OAuth `subject` propagation: an issued token's `subject` claim is readable by the consuming
  endpoint end-to-end (integration test against `mcp_oauth_store.py`).
- Explicit negative test: confirm a stdio-transport call path is genuinely unaffected by any
  Path B change (guards against scope creep back into §1's non-goal).

## 7. Documentation sync required

- `CLAUDE.md`/`AGENTS.md` — a bullet stating the Path A/B decision made and, if Path A, the
  explicit "not solvable within this architecture for a filesystem-privileged agent" finding
  so a future session doesn't re-attempt this under the original framing.
- `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` — update the "no per-agent identity" open-gap
  entry with this plan's finding, whichever path is taken.
- If Path B ships: `docs/architecture/observability-and-apis.md` (OAuth/MCP auth section).
