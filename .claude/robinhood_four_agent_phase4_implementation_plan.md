# Robinhood Four-Agent System — Phase 4 Implementation Plan (Orchestration)

Parent plan: `.claude/robinhood_four_agent_trading_implementation_plan.md` §5 and the P4 row.
Phases 1–3 merged (#1134, #1135, #1136). Feature freeze: this phase needs the operator's
explicit OK before any code is written.

## Goal

Run the four agents (Scout → Analyst → Risk Manager → Trader) as one unattended cycle
after each daemon queue refresh, so the operator only sees approval requests arrive on
their phone, with the thesis for each order in the push notification.

## What already exists (reused, not rebuilt)

- The four agent definitions in `.claude/agents/rh-*.md` and their artifacts under
  `OUTPUT_DIR/agentic/<run_id>/` (`research.json`, `theses.json`, `preflight.json`).
- `alerting.py::notify()`: ntfy push, a silent no-op when `ALERT_NTFY_TOPIC` is unset.
- `execution/receipts_store.py`: receipts, the placed ledger, approval events.
- `execution/kill_switch.py`, `shared/market_hours.py::is_us_market_open_now`.
- macOS launchd job pattern (`scripts/com.investyo.*.plist`).

## Design

### 1. `execution/agentic_cycle.py` (new; deterministic helpers + CLI, no order tools)

The coordinator is an LLM command, so everything that can be code is code:

- **`gate(now, queue, mode, kill_switch_active, run_state)` → `GateResult(run: bool, reason)`**.
  Skips (exit 3, no LLM call spent) when: mode `off`; kill switch on (also pushes one
  alert); outside regular US hours; queue missing or older than 30 min; no actionable
  intents (`allow_place` in `live`, `gate_allowed` in `review`); or this queue's
  `generated_at` already has a completed cycle. Anything unreadable → skip with the
  reason (fail closed).
- **Single-flight lock** `OUTPUT_DIR/agentic/cycle.lock` (non-blocking `fcntl`), so two
  timers or a manual run can't overlap.
- **`cycle.json` per run** (atomic write): each stage's start, end, status and reason.
  It is the audit trail and how `gate` knows a queue was already handled.
- **`cycle-failed`**: writes a `cycle_failed` receipt (stage + reason) and sends one push.
- **`summary`**: builds the push text **from the files only**, never from LLM prose:
  `live` → "2 trades awaiting your approval in Robinhood" plus one line per placed
  order (`BUY 7 T, limit $21.29 — <validated thesis>`); `review` → "Review run: N would
  pass, M vetoed (reasons)". A thesis that didn't validate shows "thesis unavailable".
  Trimmed to the ntfy size limit.
- **`launch`**: what the timer runs. Takes the lock, runs `gate`, then runs
  `claude -p "/rh-agentic-cycle"` as a subprocess with a hard timeout (a code constant,
  not a new setting) and an explicit `--allowedTools` list built by parsing the four
  agents' frontmatter (so the list can't drift from the definitions). Timeout or non-zero
  exit → `cycle-failed`.

### 2. `.claude/commands/rh-agentic-cycle.md` (new coordinator)

1. `python -m execution.agentic_cycle gate` → stop on skip.
2. Run `rh-research-scout`, `rh-strategy-analyst`, `rh-risk-manager`, `rh-trader` in
   order, recording each stage. Any stage failing → `cycle-failed` and stop (fail
   closed; a later agent never runs on a missing artifact).
3. `python -m execution.agentic_cycle summary --notify`.

**Unattended rule (new invariant):** with nobody at the keyboard there's no in-chat
confirmation, so in this command the Trader places **only** orders that go to the phone
(`customer_approval_required: true`, empty `order_checks`, approvals on). Any order that
would need in-chat confirmation is skipped with the reason
`in_chat_confirmation_unavailable`. The Trader never declines a stale approval
unattended; it lists it in the summary for the operator instead (it already needs their
OK). Approval outcomes are recorded at the start of the next cycle (the Trader's existing
step 6), not by a long poll.

### 3. Scheduling: `scripts/com.investyo.rh-agentic-cycle.plist` (new)

launchd `StartInterval` 900 s (every 15 min) running
`.venv/bin/python3 -m execution.agentic_cycle launch`. The gate makes off-hours and
no-work wakes free. **Shipped but not installed:** it is not added to
`install_schedule.command`'s list; the operator loads it by hand (RUNBOOK steps), so
nothing starts on `git pull`. The cycle obeys `ROBINHOOD_EXECUTION_MODE`, so loading it
while the mode is `review` is the Phase 5 burn-in.

### 4. Webapp "Pending approvals" card — **only with a separate OK**

The parent plan marks it a new webapp screen element, which the freeze gates separately.
Default: not in this phase.

## Tests (`tests/test_agentic_cycle.py`, new)

- `gate`: every skip reason, plus a run; unreadable inputs → skip.
- Lock: a second `launch` while one holds the lock exits without running.
- `summary`: numbers come only from `preflight.json` and placed records; invalid
  theses are never quoted; review vs live wording; trimming.
- `cycle-failed` writes the receipt and calls `notify` once (patched).
- `launch` passes a timeout and an `--allowedTools` set equal to the union of the agent
  frontmatter; a timeout records `cycle_failed` (subprocess patched; no real `claude`).
- The plist parses with `plistlib`, points at `execution.agentic_cycle launch`, and is
  **not** in `install_schedule.command`'s label list.
- Pinned phrases in the command file (unattended rule, stage order, fail-closed stop).
- The new module joins the order-tool AST guard in `tests/test_pipeline_smoke.py`.

## Documentation

`docs/RUNBOOK.md` (install, uninstall, read `cycle.json`, clear a stuck lock, rollback =
unload the plist), `docs/HOW_TO_GUIDE.md` (what the push means and how to decline),
`docs/architecture/execution.md`, `docs/architecture/testing.md`,
`docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`, the parent plan's P4 row, and one sentence
in the `CLAUDE.md`/`AGENTS.md` Robinhood bullet. Settings census and liveness
regenerated if the new module reads settings.

## Honest limits

- The cycle runs on the operator's Mac, which needs Claude Code logged in and the
  Robinhood MCP connected there; this cloud session can't test the real launchd run.
  I'll verify it with tests and a manual `gate`/`summary` dry run on a hand-made queue.
- `--allowedTools` includes the Trader's `place_equity_order`; the phone tap stays the
  real gate (the Trader refuses when approvals are off).

## Verification

`pytest tests/test_agentic_cycle.py` plus the Phase 1–3 test files, census/liveness and
agent-docs-sync gates, ruff; full suite on the PR's CI.
