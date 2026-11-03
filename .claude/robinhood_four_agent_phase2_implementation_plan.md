# Phase 2 — Portfolio & Risk Manager preflight (Agent 3)

## Context

Phase 1 (merged, #1134) made the Trader approval-aware: each order goes to the operator's
phone. But the Trader still checks each intent mostly *alone* and against the daemon's
snapshot, not the **live** Agentic account. Gaps that remain:

- No check that the **sum** of a cycle's buys fits buying power (each passes alone; together
  they don't).
- No post-trade concentration / gross check against the **real** Robinhood positions.
- Quantity resolution, the notional cap and the "sell ≤ held" rule live in skill prose
  (re-implemented by the LLM every run) rather than tested code.
- No check against a still-pending approval for the same symbol from an earlier run.

Phase 2 moves these into one **pure, tested Python function** that the Trader honors. An
LLM's "looks fine" is never a gate; this function is. It can only *remove* orders (veto),
never add or enlarge them.

Live facts that shape the design (read-only, 2026-11-03): the Agentic account ("MCP",
••4848) is a **cash** account with **$250** cash and **no positions**;
`get_equity_positions` reports sellable shares as `shares_available_for_sells`;
`get_portfolio` gives `total_value` and `buying_power.buying_power`. With whole-share limit
orders, any stock priced above the per-order cap simply cannot be bought — the preflight
reports that honestly (`qty_below_one_share`) instead of rounding up.

## Design

### New module `execution/agentic_preflight.py` (pure logic + one atomic writer)

Inputs are small frozen dataclasses defined in the module (built from MCP JSON by helper
parsers, so the agent passes raw tool output and never hand-computes):

- `AccountState(account_number, total_value, buying_power, positions: {SYM: Holding(qty, sellable)})`
  — `from_mcp(portfolio_json, positions_json)` parser; any missing/non-finite field → `None`
  (CONSTRAINT #4), which makes the dependent check **veto** (CONSTRAINT #6, fail closed).
- `quotes: {SYM: float}` — `quotes_from_mcp(get_equity_quotes_json)`; uses `ask_price` for
  BUY / `bid_price` for SELL when present, else `last_trade_price`; non-finite/≤0 → absent.
- `pending_approvals: [{symbol, side}]` — from a fresh `get_trade_approvals`.
- `placed_dedup_keys` — from `receipts_store.read_placed_ledger()` (reused, not re-read by hand).

`preflight(queue, account, quotes, *, now, pending_approvals, placed_dedup_keys,
kill_switch_active, max_notional=None, max_position_weight=None, max_portfolio_gross=None,
stale_after_minutes=30, earnings_dates=None, earnings_blackout_days=None) -> PreflightReport`

Caps default to `settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER` / `MAX_POSITION_WEIGHT` /
`MAX_PORTFOLIO_GROSS` (read lazily, overridable in tests). `PreflightReport` carries
`queue_generated_at`, `mode`, `run_blockers` (queue-level vetoes) and one
`IntentVerdict` per intent: `client_order_id, symbol, side, passed, reasons[],
qty, order_type, limit_price, ref_price, est_notional`.

**Queue-level blockers (veto every intent):** kill switch (file or queue flag); queue older
than 30 min; `mode == off`; account state unusable (no `total_value`/`buying_power`).

**Per-intent checks, in order (each failure appends a stable reason code):**
1. Gate verdict: `allow_place` in `live` (in `review`, use `gate_allowed` so the burn-in
   shows what *would* pass; `passed` never makes a review-mode order placeable).
2. Dedup: today's `dedup_key` already in the ledger, or a pending approval for the same
   symbol+side → `already_submitted_today` / `approval_already_pending`.
3. Live quote present → else `quote_unavailable`.
4. Quantity resolution, mirroring the skill exactly (moved, not changed):
   BUY `qty = floor(target_notional / price)` where price = limit price
   (`quote*(1+bps/1e4)`, 2-dp) for limit intents else quote; SELL full-exit uses the
   queue `qty`; SELL trim `floor(target_notional/price)` capped at held. `qty < 1` →
   `qty_below_one_share`.
5. Notional cap (BUY): cap unset (≤0) → `notional_cap_unset`; `qty*price > cap` →
   `over_notional_cap`.
6. SELL ≤ live `shares_available_for_sells` → else `sell_exceeds_sellable` (veto, no
   silent shrink of a full exit — the operator sees it).
7. Earnings blackout (BUY), only when the caller passes `earnings_blackout_days`
   (default `None` = off = today's behaviour; no new setting in Phase 2):
   `earnings_within_blackout`.
8. Post-trade concentration (BUY): `(held_mv + est_notional) / total_value >
   max_position_weight` → `over_position_weight`.

**Cycle-wide pass (after per-intent):** surviving BUYs sorted by `conviction` desc, then
`client_order_id` for determinism; accept while cumulative notional ≤ `buying_power` and
post-trade gross ≤ `max_portfolio_gross * total_value`; the rest get
`buying_power_exhausted` / `over_portfolio_gross`. SELLs never consume buying power.

**Invariants (property-tested with hypothesis):** never upsizes (`est_notional` ≤ the
intent's `target_notional` for BUYs; `qty` ≤ queue/held qty for SELLs); never adds,
drops or flips an intent (one verdict per intent, same side); deterministic for equal inputs.

`write_preflight(report, run_id=None, output_dir=None) -> Path` writes
`OUTPUT_DIR/agentic/<run_id>/preflight.json` atomically (temp + `os.replace`), `run_id`
defaulting to `generated_at`-derived id. A small CLI `python -m execution.agentic_preflight
--portfolio p.json --positions pos.json --quotes q.json --approvals a.json [--earnings e.json]`
reads the live queue + ledger itself, prints a table and the JSON path (so the agent pipes
raw MCP output to files and never hand-builds inputs).

Naming: no function/class matching `place_*`/`*_order`/`submit_order` (the repo AST guard);
the file is added to `tests/test_pipeline_smoke.py::_EXECUTION_ZONE_GUARDED_FILES`.

### Agent 3 definition `.claude/agents/rh-risk-manager.md`

Read-only Robinhood tools only (`get_accounts`, `get_portfolio`, `get_equity_positions`,
`get_equity_quotes`, `get_trade_approvals`, `get_earnings_calendar`, both server-name
spellings) + Read/Grep/Glob/Bash. Procedure: gather live state → save raw JSON under
`$OUTPUT_DIR/agentic/<run_id>/inputs/` → run the CLI → narrate passes/vetoes. Never edits
the queue, never sizes up. The existing allowlist test already forbids it any order-power
tool; add a positive test that it lists exactly these read tools.

### Trader integration (veto-only) — `robinhood-execution` skill (both copies)

New step between preview and placement: run the preflight (or read a
`preflight.json` whose `queue_generated_at` equals the queue's `generated_at`); **place
only intents with `passed: true`, using the preflight's resolved `qty` and
`limit_price`**. A preflight that is missing, stale (different `generated_at`) or
unreadable → the in-chat path for every order with the reason stated (fail closed to the
stricter path, not to "no check"). Pinned phrases added to `TestSkillMdInvariantsPinned`.
The e2e driver (`tests/test_robinhood_e2e.py`) calls the real `preflight()` so the skill
port and the code can't drift.

## Files

- New: `execution/agentic_preflight.py`, `tests/test_agentic_preflight.py`,
  `.claude/agents/rh-risk-manager.md`, `.claude/robinhood_four_agent_phase2_walkthrough.md`.
- Edit: `.claude/skills/robinhood-execution/SKILL.md` + `.agents/` copy,
  `.claude/commands/rh-execute.md`, `tests/test_robinhood_e2e.py`,
  `tests/test_rh_agent_tool_allowlists.py`, `tests/test_pipeline_smoke.py` (guard list).
- Reuse: `execution/receipts_store.py` (`make_dedup_key`, `read_placed_ledger`,
  `make_ref_id`), `execution/kill_switch.py::GlobalKillSwitch`, the queue schema from
  `execution/queue_builder.py::_intent_dict` (`target_notional`, `qty`, `order_type`,
  `limit_offset_bps`, `conviction`, `allow_place`, `gate_allowed`).

## Documentation step

`docs/architecture/execution.md` (new module bullet), `docs/architecture/testing.md`
(new test file), `docs/RUNBOOK.md` (Robinhood Live Execution: what a preflight veto means
and the $-cap vs share-price interaction), the plan's Phase 2 row + Agent 3 section
(marking check 10 deferred to Phase 3, earnings blackout as a parameter not a setting),
`docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` (cumulative buying-power and concentration now
code-enforced). `CLAUDE.md` unchanged unless a rule changes.

## Deferred (stated, not silently dropped)

- Check 10 (Analyst `data_gaps` veto) → Phase 3, when the Analyst exists.
- Earnings blackout as an operator setting → only if you want a non-zero default later.
- Shrinking an order to fit buying power → v1 is veto-only (plan Q5).

## Verification

1. `pytest tests/test_agentic_preflight.py tests/test_robinhood_e2e.py
   tests/test_receipts_store.py tests/test_rh_agent_tool_allowlists.py
   tests/test_pipeline_smoke.py -q` — all green (each check pass+fail, every
   blocker, missing-data → veto, buying-power ordering, review mode, never-upsize property,
   CLI round trip, atomic write leaves no `.tmp`).
2. `ruff --select=F821,F822,F823,E9` clean; full suite via CI on the PR.
3. Live read-only dry run: feed the real `get_portfolio`/`get_equity_positions`/
   `get_trade_approvals` output for ••4848 plus a hand-made two-intent queue to the CLI
   and show the verdicts (no order tool called).
