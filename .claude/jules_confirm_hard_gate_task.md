# Jules Dispatch `confirm=True` Hard-Gate — Task Tracker

> Companion to `jules_confirm_hard_gate_implementation_plan.md`.

## §0 — Confirm before building
- [x] Check whether GitHub branch protection + required CODEOWNERS review is actually
      enabled on the target repo for Jules PRs (materially changes residual risk) —
      **done as part of Phase 3** (the operator approved starting Phases 1-3 directly;
      this check and Phase 3 are the same verification, see below)
- [ ] Confirm whether any scheduled/cron task is actually configured with this MCP server
      wired in (the unattended-dispatch scenario) — real risk or theoretical today?
      **Not checked in this pass** — out of the operator-approved scope (Phases 1-3 only)
- [x] Operator confirms recommended scope (prompt-hash pinning + cooldown) vs. also wanting
      the costlier out-of-band channel (option 1) — **confirmed**: Phases 1-3 approved,
      Phase 4 explicitly deferred, operator-request-only

## Phase 1 — Prompt-hash pinning
- [x] `request_dispatch_approval(prompt, source, branch, title) -> {"approval_token",
      "prompt_hash", "expires_at"}` step in `data/jules_client.py`
- [x] `dispatch_session` verifies hash match (via `_consume_dispatch_approval`) before
      proceeding, raising `JulesApprovalMismatch` naming exactly which check failed
      (missing / unknown / expired / already used / content mismatch)
- [x] Document explicitly: does not stop a single-turn agent bypass, only an
      approved/dispatched mismatch (module docstring, MCP tool docstrings, SKILL.md,
      `docs/JULES_INTEGRATION.md` Sec 4)

## Phase 2 — Dispatch cooldown
- [x] Add cooldown to `data/jules_client.py` (`_enforce_dispatch_cooldown`,
      `settings.JULES_DISPATCH_COOLDOWN_SECONDS`, default 60s) — reads the last
      recorded timestamp straight out of the existing dispatch ledger rather than
      introducing a separate token-bucket `RateLimiter` state (a design decision made
      during implementation: the ledger already durably records what a token-bucket
      would need to reconstruct anyway, and this integration's low call volume doesn't
      need a burst-capacity model)

## Phase 3 — Branch-protection verification
- [x] Verify (not assume) GitHub branch-protection/CODEOWNERS status on the target repo —
      done via live `gh api repos/kevinmarko/Stockpy/branches/main/protection` (+
      `required_pull_request_reviews` sub-resource) from this environment, 2026-09-07
- [x] Document the finding in `docs/JULES_INTEGRATION.md` regardless of outcome — Sec 4:
      protection enabled, 1 required review, but `require_code_owner_reviews: false` and
      `enforce_admins.enabled: false`, and this sandbox's own `gh` token independently
      confirmed to hold repo `admin: true` — so the review gate is bypassable by an
      admin-privileged merge from within this very environment

## Phase 4 — CLOSED 2026-09-08, not built
- [x] Scoped (three options: PushNotification+relay, external OTP/TOTP service, ntfy
      action-buttons) and costed per the plan's own instruction — see
      `jules_confirm_hard_gate_implementation_plan.md`'s Phase 4 section for the full
      analysis.
- [x] Operator asked "why would we need this?" — closed rather than built. Reasoning: an
      agent with this repo's normal filesystem/shell access already has far larger levers
      than Jules dispatch (it can edit `execution/risk_gate.py`, place a live trade,
      exfiltrate every `.env` credential, or simply delete the OTP check itself before
      calling `dispatch_session`) — hardening this one narrow action doesn't proportionally
      reduce exposure to a genuinely malicious/compromised agent, and the realistic failure
      mode (accidental content drift, accidental looping) is already covered by Phases 1-3.
      Same underlying finding as the Path A closure in
      `per_agent_identity_implementation_plan.md`: the real control point for containing a
      filesystem-privileged agent is process/sandbox isolation, not hardening individual
      MCP tools one at a time.
- [x] The two weaker options (PushNotification+relay, and ntfy action-buttons without an
      external unreachable verifier) were found to add no real security in the scoping
      pass anyway — a session that composes the confirmation material can also validate its
      own dispatch against it, so they wouldn't have been worth building even absent the
      broader "not worth it" finding above.

## Testing
- [x] Unit test: hash mismatch rejected (+ missing/unknown/expired/already-used —
      5 distinct tests, `TestDispatchSessionApprovalGate`)
- [x] Unit test: cooldown blocks rapid re-dispatch (+ allows after window elapses,
      `TestDispatchCooldown`)
- [x] Pinned SKILL.md-invariant test (`tests/test_jules_delegation_skill.py`, mirroring
      `tests/test_robinhood_e2e.py::TestSkillMdInvariantsPinned`) asserting the prose still
      honestly describes these as bar-raising, not bypass-proof (both `.claude/` and
      `.agents/` copies checked, plus a negative check for overclaiming phrases)

## Doc sync
- [x] `docs/JULES_INTEGRATION.md` §4 update (rewritten with all 4 mechanisms + the Phase 3
      finding; §3 settings table, §5 rollout, §6 known-risks also updated)
- [x] `CLAUDE.md`/`AGENTS.md` bullet, explicit non-goal restated (auto-mirrored by
      `.claude/hooks/sync_agent_docs.sh` — verified byte-identical after the edit)
- [x] `.claude/skills/jules-delegation/SKILL.md` (+ `.agents/` mirror) prose update — both
      edited by hand (this pair is NOT in `sync_agent_docs.sh`'s `MIRRORED_SKILL_NAMES`)
      and verified byte-identical apart from the pre-existing Antigravity port-comment
      header, both before and after this change
- [x] Cross-reference from `docs/known_issues/robinhood_confirmation_gate_is_prose_only.md`

## Explicitly NOT in this task list
- An out-of-band confirmation channel (Phase 4) unless explicitly requested separately
- Any Jules-API-side change (no draft/staged mode exists to use)
- A claim that this fully solves the unattended/scheduled-session scenario
