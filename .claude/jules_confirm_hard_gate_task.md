# Jules Dispatch `confirm=True` Hard-Gate — Task Tracker

> Companion to `jules_confirm_hard_gate_implementation_plan.md`.

## §0 — Confirm before building
- [ ] Check whether GitHub branch protection + required CODEOWNERS review is actually
      enabled on the target repo for Jules PRs (materially changes residual risk)
- [ ] Confirm whether any scheduled/cron task is actually configured with this MCP server
      wired in (the unattended-dispatch scenario) — real risk or theoretical today?
- [ ] Operator confirms recommended scope (prompt-hash pinning + cooldown) vs. also wanting
      the costlier out-of-band channel (option 1)

## Phase 1 — Prompt-hash pinning
- [ ] `approve_dispatch(prompt_hash) -> approval_token` step in `data/jules_client.py`
- [ ] `dispatch_session` verifies hash match before proceeding
- [ ] Document explicitly: does not stop a single-turn agent bypass, only an
      approved/dispatched mismatch

## Phase 2 — Dispatch cooldown
- [ ] Add rate-limiter/cooldown to `data/jules_client.py`, mirroring
      `broker_live_execution_mcp.py`'s existing `RateLimiter` token-bucket

## Phase 3 — Branch-protection verification
- [ ] Verify (not assume) GitHub branch-protection/CODEOWNERS status on the target repo
- [ ] Document the finding in `docs/JULES_INTEGRATION.md` regardless of outcome

## Phase 4 — deferred, explicit operator request only
- [ ] Design + cost out an out-of-band confirmation channel (option 1) — separate scoping
      pass required, do not fold into this task list

## Testing
- [ ] Unit test: hash mismatch rejected
- [ ] Unit test: cooldown blocks rapid re-dispatch
- [ ] Pinned SKILL.md-invariant test (mirroring
      `tests/test_robinhood_e2e.py::TestSkillMdInvariantsPinned`) asserting the prose still
      honestly describes these as bar-raising, not bypass-proof

## Doc sync
- [ ] `docs/JULES_INTEGRATION.md` §4 update
- [ ] `CLAUDE.md`/`AGENTS.md` bullet, explicit non-goal restated
- [ ] `.claude/skills/jules-delegation/SKILL.md` (+ `.agents/` mirror) prose update
- [ ] Cross-reference from `docs/known_issues/robinhood_confirmation_gate_is_prose_only.md`

## Explicitly NOT in this task list
- An out-of-band confirmation channel (Phase 4) unless explicitly requested separately
- Any Jules-API-side change (no draft/staged mode exists to use)
- A claim that this fully solves the unattended/scheduled-session scenario
