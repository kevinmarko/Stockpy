# Walkthrough: Closing F1 (Mock/Live API Parity) from the Giant Master Plan Audit

Branch: `fix-mock-live-api-parity`. PR #766. Source: `.claude/giant_master_plan_audit.md` (PR #759),
finding F1 — a systemic mock/live API parity failure across Phase 20-24 webapp screens.

## What happened

Dispatched to a subagent with a pre-researched reference plan
(`/Users/kevinlee/.claude/plans/based-on-the-prompt-proud-corbato-agent-a7cffdf2cb06d0cca.md`, from
an earlier blocked agent in the F2-F10 pass) so the investigation didn't need repeating. Unlike the
earlier 6-agent pass in `fix-options-desk-audit-findings`, this agent self-verified that the stale
"Plan Mode" system-reminder didn't apply (its first Edit call succeeded) and executed the full fix
in one pass without needing to be resumed or taken over.

## Items closed

| # | Item | Disposition |
|---|---|---|
| 1 | GEX profile | Fixed — field renames, `net_gex` scale correction |
| 2 | LOB queue simulate | Fixed — full UI redesign (real endpoint is a queue-fill model, not an order book) |
| 3 | Copula pairs | False positive in original audit (wrong dataclass cited) — added one real missing field |
| 4 | Market maker simulate | False positive in original audit (wrong dataclass cited) — no change needed |
| 5 | Transformer forecast | Fixed — wrong URL + shape rewrite |
| 6 | Diffusion stress test | Fixed — wrong URL + missing required field + honest client-side derivation |
| 7 | Multi-broker status/failover | Fixed — wrong URL, a crash-on-load bug, and wrong failover shape |
| 8 | Research copilot synthesize | Fixed — near-total shape rewrite |

## Verification

`npm run --prefix webapp typecheck` → 0 errors. `npx vitest run` → 161 files, 1,722 tests passed
(re-verified independently by the orchestrating session, not just the subagent's self-report).
