---
description: Run Agent 5 (Reviewer) for the agentic Robinhood burn-in and send the daily summary push
---

Run the **`rh-reviewer`** agent once (Phase 5 of
`.claude/robinhood_four_agent_trading_implementation_plan.md`; plan
`.claude/robinhood_four_agent_phase5_implementation_plan.md`). Arguments: `$ARGUMENTS`.

1. Run `rh-reviewer` with the Agent tool. Tell it to follow its procedure and to
   finish with `python3 -m execution.agentic_review run --notify`, so the daily
   push is built from `review.json` by code, not written by an agent.
2. Print its report. Exit code 0 means READY, 1 NOT READY and 2 inputs
   unusable; NOT READY is the normal answer during the burn-in, not a failure.
3. Never change `ROBINHOOD_EXECUTION_MODE`, the per-order cap, the queue or an
   approval, even when the review says READY. Going live is the operator's
   decision, made in their own settings.
