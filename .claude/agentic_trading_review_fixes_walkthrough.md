# Agentic trading review fixes — walkthrough

Plan: `.claude/agentic_trading_review_fixes_implementation_plan.md`. Source: the 2026-11-14
`/code-review high` of the agentic Robinhood subsystem; the operator asked for all 10 findings fixed.
Built by four parallel agents on disjoint files, then integrated and reviewed by the lead.

## What changed, by finding

1. **Ledger split → duplicate orders.** `execution/receipts_store.py` no longer falls back to
   `./output`; it raises when it can't resolve `settings.OUTPUT_DIR`. Appends are locked, written via
   a unique temp file + fsync + `os.replace`, return True/False, and refuse to rewrite an unreadable
   ledger. Placed records carry `est_notional`. Every skill/agent/command Python call is now
   `.venv/bin/python3` (a bare `python3` on the launchd PATH can lack this repo's dependencies), and
   the Trader's ledger one-liners exit non-zero when the write fails. The Antigravity mirror
   (`.agents/skills/robinhood-execution/SKILL.md`) is re-synced.
2. **Approvals fail-open.** `agentic_preflight.read_approvals` blocks the run with
   `approvals_unreadable` when the payload has no approvals list; terminal-state items no longer
   block; `approvals_from_mcp` keeps its 2-tuple shape but reports an unreadable payload as one
   unidentified approval (never "nothing pending").
3. **Stale inputs.** Per-intent `quote_stale` (freshest quote timestamp > 15 min or missing) and run
   blocker `inputs_stale` (any input file > 10 min old). The risk manager writes its inputs to the
   per-run folder `agentic/<run_id>/inputs/`.
4. **SELL qty from the main account.** Queue SELLs carry `exit_kind`; the preflight sizes `full`
   from the Agentic account's sellable shares and `fraction` as `floor(sellable × exit_fraction)`.
   **Behavior change:** a SELL whose advisory `suggested_exit_pct` is 0.5 (advisory's normal-SELL /
   RISK REDUCE exit fraction) now sells half the position instead of all of it; Case A's 1.0 and
   unknown fractions stay full exits.
5. **Earnings blind spot.** The unattended risk stage always fetches the earnings calendar and passes
   `--blackout-days 2` (**behavior change**: BUYs within 2 days of earnings are vetoed in unattended
   cycles; attended `/rh-execute` unchanged). The push shows the thesis's invalidation, the analyst's
   flags and the next earnings date. Research with a past `next_earnings` date is rejected.
6. **Analyst tools.** `get_recommendation` no longer writes forecast-ledger rows
   (`persist_forecast=False`) and says it is a fresh re-evaluation; `get_signal_breakdown` says
   plainly that the live pipeline doesn't write `DailySignals`. The analyst cites the queue's
   `context` first.
7. **Pending approvals' dollars.** Reserved against buying power and the gross cap before this
   cycle's BUYs; unknown amount → every BUY vetoed `pending_notional_unavailable`.
8. **Silent skip.** `launch` re-runs the gate when the coordinator exits without a `cycle.json`; if
   it still says run, the cycle is failed with an alert.
9. **Stage order / staleness.** Stages are `risk` → (`scout` ∥ `analyst`) → `trader`; research covers
   only preflight-passed intents; `passed` and `finish --nothing-passed` CLI subcommands. The gate
   skips when the queue is within 10 min of going stale or within 20 min of the close; the preflight
   blocks `market_closed`; the Trader re-checks the market right before sending.
10. **Decision context.** Every queue intent carries `context` (forecast, data quality, signal
    scores, buy/sell ranges, sector, regime, …; null when unavailable), carried through
    `execution/compose.py`. The preflight records `spread_bps`, `quote_age_s` and
    `drift_vs_signal_pct` on each verdict (measurement only, no veto).

## Golden fixtures

`tests/fixtures/run_once_advisory_golden/{advisory_source,execution_queue}.golden` and
`tests/fixtures/compose_advisory_only_queue.golden` were regenerated. Parsed and compared with the new
keys (`context`, `exit_kind`, `exit_fraction`, `suggested_exit_pct`) removed, each is identical to
the previous version: no existing field changed.

## Known limits

- A pending BUY approval from a previous day with no stated amount, and no ledger record carrying its
  `approval_id`, vetoes every BUY until it resolves (fail closed by design).
- `near_market_close` measures from 16:00 ET; early-close days rely on the FMP open flag.
- Agentic-only positions still get no daemon exit intent
  (`docs/known_issues/rh_queue_gated_on_main_account_2026_11.md`).
- `shared/robinhood_execution_panel.py` doesn't surface `context`/`exit_kind` to the PWA yet (new
  screen content would need the operator's OK under the freeze).
