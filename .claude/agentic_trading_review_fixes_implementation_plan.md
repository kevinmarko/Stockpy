# Agentic trading review fixes — implementation plan

Branch: `fix-agentic-trading-review-bugs`. Source: the 2026-11-14 `/code-review high` of the agentic
Robinhood subsystem (10 findings). Freeze status: bug fixes, fail-closed fixes, measurement and
prompt fixes, all allowed under the step-7 freeze. The operator asked for all 10 to be fixed
("fix all 10", 2026-11-14); that request is the OK for the two behavior changes marked **(behavior)**.

## Findings → owner

| # | Finding | Owner |
|---|---|---|
| 1 | `receipts_store._resolve_output_dir` silently falls back to `./output`; skill uses bare `python3` (Homebrew, no pydantic_settings) → ledger split → same-day duplicate order | A |
| 2 | `approvals_from_mcp` treats an unreadable payload as "nothing pending" (fail-open); ignores item `state` | B |
| 3 | Quote age and live-input file age never checked; shared `agentic/inputs/` folder can serve yesterday's files | B (checks), A (per-run inputs folder in prompts) |
| 4 | Full-exit SELL qty comes from the MAIN account; `suggested_exit_pct` ignored | C (queue), B (preflight) |
| 5 | Earnings blackout never on in unattended cycle; push drops invalidation/flags/earnings; validator accepts a past earnings date | A (prompts), D (push + validator) |
| 6 | `get_recommendation` MCP tool persists forecast-ledger rows and re-evaluates; `get_signal_breakdown` reads a table with no writer | C (tool), A (analyst prompt) |
| 7 | Pending phone approvals' dollars not reserved against buying power / gross cap | B |
| 8 | `launch` silently returns EXIT_SKIP when the coordinator never wrote `cycle.json` | D |
| 9 | Research runs before risk, eating the 30-min staleness budget; market hours/age only checked at the gate | A (cycle order in prompt), D (stage order, gate headroom), B (market-hours blocker) |
| 10 | Queue drops the Recommendation's context (forecast, data quality, signals, ranges, sector…) | C (queue), A (analyst/risk prompts cite it), B (measurement fields) |

## Cross-agent contracts (every agent codes to these)

**C1 — queue intent keys (C writes, B/D/A read).** All additive; old queues without them keep today's behavior.
- SELL intents gain `exit_kind`: `"full"` | `"fraction"` | `"trim"`.
  - `"full"`: exit the whole position. `qty` stays as today (main-account qty) for back-compat/display only.
  - `"fraction"`: sell `exit_fraction` (float in (0, 1)) of the position — used when the Recommendation's `suggested_exit_pct` (or equivalent field; C verifies the real name in `engine/advisory.py`) says a partial exit. **(behavior)**
  - `"trim"`: today's notional trim (`qty` null, `target_notional` set).
- Every intent gains `context` (dict). Keys, each `null` when unavailable — **never** a default (no 0.0, no 1.0):
  `decision_price`, `data_quality`, `synthetic_inputs`, `forecast_30d_pct`, `forecast_is_fallback`,
  `score_components`, `buy_range`, `sell_range`, `suggested_exit_pct`, `sector`,
  `sizing_binding_constraint`, `regime`, `news_sentiment` (only those the Recommendation actually has; C documents the final list).

**C2 — preflight SELL sizing (B).** `exit_kind == "full"` → qty = the Agentic holding's `sellable` (fractional kept).
`"fraction"` → qty = `floor(sellable * exit_fraction)` (whole shares; < 1 → `qty_below_one_share`).
Missing holding/sellable → `sell_exceeds_sellable` (fail closed). No `exit_kind` → today's logic.

**C3 — preflight new checks (B).** Blockers/reasons (new stable codes):
- run blocker `inputs_stale`: any of the four input files' mtime older than `--max-input-age-minutes` (default 10).
- run blocker `market_closed`: `now` outside regular US hours (holiday-aware, via `shared/market_hours.py`).
- run blocker `approvals_unreadable`: approvals payload has no recognizable list (replaces the silent `([], 0)`).
- per-intent `quote_stale`: quote `as_of` older than `--max-quote-age-minutes` (default 15) or missing.
- per-BUY `pending_notional_unavailable`: a pending BUY approval whose dollar amount can't be found
  (approval item, else the placed ledger's `est_notional` for that symbol/side).
- pending BUY notional is reserved before the cycle-wide buying-power / gross-cap loop.
- measurement only (no veto) on each verdict: `spread_bps`, `quote_age_s`, `drift_vs_signal_pct`
  (vs `context.decision_price`, null when absent).

**C4 — ledger (A).** `receipts_store` never falls back to `./output`: no explicit `output_dir` and no
importable settings → raise `RuntimeError`. `append_placed` / `append_approval_event` return `True`
on a durable write and `False` on failure (still never raise on I/O, so daemon callers are safe);
the skill's one-liners exit non-zero on `False`. Placed records carry `est_notional`.
Every agent/skill/command Python invocation uses `.venv/bin/python3` (repo-root relative).

**C5 — cycle order (D code, A prompt).** `STAGES = ("risk", "scout", "analyst", "trader")`.
`scout` and `analyst` may run in parallel after `risk` (neither requires the other).
Research/theses are required only for preflight-PASSED client_order_ids when a preflight exists for the queue.
Gate skips with headroom: queue age > `stale_after − 10` min, or within 20 min of the regular close.

**C6 — blackout (A prompt).** In the unattended cycle the risk stage always builds `earnings.json` from
`get_earnings_calendar` and passes `--earnings … --blackout-days 2`. **(behavior)** Attended `/rh-execute` unchanged.

**C7 — push (D).** Each order line adds the thesis's invalidation, the analyst's flags and the
research `next_earnings` date when present; preflight-passed orders the Trader skipped are listed.

## Docs (lead, after agents)
`docs/architecture/execution.md` (preflight checks, queue `context`/`exit_kind`), the
`docs/known_issues/rh_queue_gated_on_main_account_2026_11.md` follow-up, walkthrough
`.claude/agentic_trading_review_fixes_walkthrough.md`.

## Verification
`tests/test_agentic_*.py`, `tests/test_receipts_store.py`, `tests/test_queue_builder.py`,
`tests/test_daemon_agentic_queue_primary.py`, `tests/test_investyo_mcp_server.py`, then `make local-ci`.
