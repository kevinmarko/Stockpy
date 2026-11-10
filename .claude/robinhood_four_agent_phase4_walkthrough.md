# Robinhood Four-Agent System — Phase 4 Walkthrough (Orchestration + Pending-approvals card)

Plan: `.claude/robinhood_four_agent_phase4_implementation_plan.md` (operator: "Build as
planned", plus "Yes, include it" for the webapp card, 2026-11-10). Parent plan:
`.claude/robinhood_four_agent_trading_implementation_plan.md`. Phases 1–3 merged as
#1134, #1135, #1136.

## Why

Phases 1–3 built the four agents, but someone still had to start each one. Phase 4
runs them as one unattended cycle after each fresh queue, so the operator only sees
approval requests on their phone, each with its thesis.

## What changed

| File | Change |
|---|---|
| `execution/agentic_cycle.py` (new) | `gate()` (pure run/skip decision with stable reasons), single-flight `fcntl` lock, `cycle.json` per run with stages in enforced order, `cycles.jsonl` log, `fail_cycle` (one push, never raises), `build_summary`/`summary_for_run`/`record_summary` (push text from files only), `launch()` (lock → gate → `claude -p` with a 30-min timeout and an allowlist parsed from the agent definitions), CLI `gate/begin/stage/fail/finish/summary/launch/allowed-tools`. |
| `.claude/commands/rh-agentic-cycle.md` (new) | The coordinator: begin → four agents with artifact checks → finish and notify. Fails closed at the first bad stage. Carries the unattended rules for the Trader. |
| Skill (both copies), `rh-trader.md` | "Unattended cycle" section: no daemon refresh, approval path only, never ask in chat (`in_chat_confirmation_unavailable`), never decline unattended, record `run_id`. |
| `scripts/com.investyo.rh-agentic-cycle.plist` (new) | launchd every 900 s running `execution.agentic_cycle launch`. Not in `install_schedule.command`; loaded by hand. |
| `shared/agentic_approvals.py` (new), `GET /agentic/approvals` | Read side for the card: pending approvals with validated theses only, recent decisions, last cycle. |
| `webapp/` | `PendingApprovalsSection.tsx` on Agentic Trading; `AgenticApprovals` types; client + mock parity with an honest degraded fixture (missing thesis, unknown state, failed cycle). |
| Tests | `tests/test_agentic_cycle.py` (85), `tests/test_agentic_approvals.py` (5), `TestAgenticApprovals` in `tests/test_pilots_api.py`, guard list in `tests/test_pipeline_smoke.py`. |
| Docs | RUNBOOK "Agentic cycle (unattended)", HOW_TO_GUIDE "Hands-off mode", `docs/architecture/{execution,testing,observability-and-apis,webapp-and-gui}.md`, safety framework, parent plan P4 row, `CLAUDE.md`/`AGENTS.md` Robinhood bullet; settings census and liveness regenerated. |

## Design choices worth knowing

- **Cheap gate first.** The timer wakes 96 times a day; the gate exits with no LLM call
  unless there's a fresh, actionable, unhandled queue in market hours.
- **One cycle per queue, never auto-retried.** Any `cycle.json` for a queue means it was
  handled. A failed cycle would otherwise re-run (and re-alert) every 15 minutes.
- **Stricter mode wins.** A queue written in `live` is only reviewed if settings now say
  `review`.
- **Push text is code.** Numbers come from `preflight.json` and the placed ledger;
  theses only if the validator accepted them. The card uses `valid_thesis_ids` recorded
  in `cycle.json` at finish, so it never quotes a thesis it can't vouch for after the
  queue moves on.
- **Deviation from the plan:** the plan said a failed cycle writes a `cycle_failed`
  receipt. `execution_receipts.jsonl` is read by the reconciliation and the queue panel
  as order outcomes, so failures go to `agentic/cycles.jsonl` and `cycle.json` instead.
- **Kill switch skips are silent.** The plan said the gate pushes one alert when the
  kill switch is on; at a 15-min cadence that would repeat all day, and the kill switch
  already alerts on its own.

## Verification

- `pytest tests/test_agentic_cycle.py tests/test_agentic_approvals.py tests/test_pilots_api.py::TestAgenticApprovals`
  plus the Phase 1–3 files, `tests/test_pipeline_smoke.py`, census/liveness and
  agent-docs-sync gates: see the PR for counts.
- `npm run --prefix webapp typecheck` clean. Browser check (Playwright, Chromium) of
  `/agentic` on mock data at 1280 px and 390 px: card renders, no console errors. Then
  against a real `uvicorn api.pilots_api:app` on hand-made files with
  `VITE_USE_MOCK=false`: the live JSON matched `AgenticApprovals` field for field and
  the card rendered it (the only console errors were the unrelated data API on :8603,
  which wasn't running).

## Honest limits

- Not run end to end here: the real cycle needs the operator's Mac (Claude Code logged
  in, Robinhood MCP connected, launchd). Everything around the LLM is unit-tested; the
  first real runs are Phase 5's burn-in in `review` mode.
- `--allowedTools` includes the Trader's order tools and general `Bash`, and the Scout
  reads untrusted news/filings. The prompts treat tool output as data, but a prompt
  injection is still possible; the phone tap is the gate that holds regardless.

## Next

Phase 5 (burn-in): load the plist with `ROBINHOOD_EXECUTION_MODE=review` for 10 trading
days, then `live` with a small `ROBINHOOD_MAX_NOTIONAL_PER_ORDER`. Needs the operator's OK.
