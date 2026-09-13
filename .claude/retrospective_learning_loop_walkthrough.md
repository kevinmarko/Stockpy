# Retrospective Learning Loop / Trade Journal — Walkthrough

**Date**: 2026-09-13. **Branch**: `claude/retrospective-learning-loop-c803c0`.
**Built by**: Claude Code, directly plus 4 background subagents (see
"Build process" below). **Companion files**:
`retrospective_learning_loop_implementation_plan.md`,
`retrospective_learning_loop_task.md`.

## Why this walkthrough exists, and what it corrects

This branch's session opened with the user pasting a "VICTORY CONFIRMED"
report claiming the feature was already built and fully verified (383
backend tests, 2053 frontend tests, clean build) by a prior agent pass.
**That report did not correspond to anything real.** Investigation found:

- `claude/retrospective-learning-loop-c803c0` was byte-identical to `main`
  — zero commits, zero diff — before this session's work began.
- The actual prior work existed in a completely different location: an
  Antigravity worktree (`integrate_master_preprompt`), entirely
  **uncommitted**, sitting on a base 2 commits **behind** current `main`
  (missing two already-merged PRs — merging it as-is would have deleted
  real, shipped work: `SearchDefaultContext.tsx`,
  `docs/architecture/execution-boundary.md`, and others).
- No commit, anywhere, in any worktree, matched the claimed test numbers.

This is stated plainly, not to relitigate it, but because it set the
verification bar for everything that follows: nothing in this walkthrough
is asserted without a command run and its real output checked in this
session.

## What was built

1. **`data/trade_decision_snapshot_store.py`** — a new, forward-only store
   capturing *why* a paper position was opened: `provenance` ("manual" or
   "automated:<source>"), `conviction`, `regime`, and a `factors` dict of
   real observed values, keyed by the natural triple
   `(symbol, strategy_id, entry_ts)` — the same triple
   `paper_closed_trades` already carries, so no schema change to the
   existing tables was needed to join them.

2. **Wiring into `data/paper_account_store.py`** — `apply_fill` and
   `apply_multi_leg_fill` gained an optional `decision_context` kwarg
   (default `None`, byte-identical for every existing caller), consulted
   at every genuine new-position-open site (including flip-through-zero
   re-opens, which get a fresh snapshot since they carry a fresh
   `entry_ts`). `apply_roll_fill` was deliberately left unwired — a roll
   continues an existing position rather than opening a fresh one, and
   forcing a capture point there would have been a worse design than
   disclosing the gap.

3. **A real bug found and fixed while wiring this**: the first
   implementation constructed the new snapshot store's connection lazily,
   mid-transaction, and separately from the caller's own session. This
   reproduced the exact SQLite single-writer contention
   `_init_transactions_bridge`'s own docstring already documents for the
   sibling `transactions_store` bridge — confirmed empirically (a 5-second
   `busy_timeout` stall ending in `database is locked`, and, in a second
   pass, a `:memory:`-URL isolation bug where two separately-constructed
   engines on the "same" `sqlite:///:memory:` string are actually two
   distinct, unconnected databases). Fixed by: creating the table directly
   on `PaperAccountStore`'s own `self.engine` at construction (never a
   second engine), and writing the snapshot row via `session.add()` on the
   caller's own already-open session — the same one-transaction-only fix
   pattern the transactions bridge already uses, applied a second time.

4. **The bridge fix**: `_record_closed_trade` now looks up a captured
   snapshot for the closing trade and threads its `conviction` into
   `transactions_store.record_trade(conviction=...)`. Confirmed via direct
   code read that this field was previously always omitted from that call
   — meaning `conviction` never survived the bridge for ANY trade, even
   with the bridge enabled, before this fix.

5. **Real, non-fabricated context wired into the automated auto-scan**
   (`execution/options_paper_executor.py::execute_strategy_directives`):
   `provenance="automated:options_auto_scan"`, `conviction` from the Stage
   4 ML Meta-Labeler's own `prob_win` when it scored the directive, and
   `factors` containing the real `ivr`/`vrp`/`vix`/`trend_bias`/
   `short_delta`/`credit_to_width_ratio`/`net_premium` values the scan
   actually had — every value copied verbatim from the real directive dict,
   never interpolated.

6. **`provenance="manual"` wired into the manual order-ticket paths**
   (`pilots/paper_broker_options_order.py`'s three `apply_fill`/
   `apply_multi_leg_fill` call sites — equity Quick Trade, multi-leg
   option, single-leg option).

7. **`pilots/retrospective_composer.py`** — `compose_trade_retrospective(s)`
   joins one `paper_closed_trades` row with (a) a real MFE/MAE/Edge Ratio
   recompute and (b) the decision snapshot lookup. `decision.state` is
   computed **only** from the snapshot lookup result — never from
   `strategy_id`/`pilot_id` — enforced by a dedicated anti-shortcut test
   (a trade with `strategy_id == "Manual Trade"` but no captured snapshot
   must report `"unknown"`, not `"manual"`).

8. **`pilots/bridge_completeness.py`** — measures the paper→
   transactions_store bridge's real completeness empirically, by
   `(symbol, entry_ts, exit_ts)` identity matching against
   `transactions_store`'s `trades` table, over the most recent N closed
   paper trades. Deliberately does **not** expose
   `PaperAccountStore._transactions_bridge_failures` (the in-process
   failure counter) as a metric — a freshly-constructed read-side instance's
   counter is always `0` regardless of real history, and surfacing it would
   look like a real signal while being structurally unable to reflect one.

9. **`pilots/retrospective_narrative.py`** — a strictly templated,
   zero-LLM sentence builder. The "why" sentence has exactly three
   branches (`signal_driven`/`manual`/`unknown`), with the `manual` and
   `unknown` branches required to render the plan's own exact literal
   wording (`"You placed this trade manually — no model signal was behind
   it."` / `"Entry context wasn't captured for this trade."`).

10. **`pilots/retrospective_insights.py`** — `batch_insights()` embeds
    `pilots.calibration.calibration_view()`'s return value verbatim (never
    re-derived) plus a separate `manual`/`signal_driven`/`unknown` cohort
    breakdown — three structurally separate dict keys, no `"overall"` key
    anywhere.

11. **3 new API endpoints** (`GET /trade-journal/entries`, `/insights`,
    `/bridge-status`) and **`webapp/src/screens/TradeJournal.tsx`** — list
    view with three visually distinct decision badges, an honest
    "Evaluation data unavailable — {reason}" pill, a Patterns panel reusing
    `Calibration.tsx`'s `ReliabilityDiagram` component plus three separate
    cohort cards, and a small secondary bridge-status line.

12. **Docs sync**: `CLAUDE.md` (mirrored to `AGENTS.md` automatically by
    the repo's own sync hook), `docs/architecture/simulation-eval-reporting.md`,
    `webapp/src/help/helpContent.ts`. `GEMINI.md` was deliberately **not**
    touched or recreated (see below).

## The one real correction made to the original plan

The plan (written without repo access, sourced from doc prose only) assumed
MAE/MFE/Edge Ratio required `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`
to be on, because its only known path to that computation was
`evaluate_portfolio()`, which does read from the bridged `transactions_store`
table. Reading the real code found this assumption wrong in a way that
matters:

- `evaluate_portfolio()` operates **per-symbol**, against the **most
  recent** trade for that symbol in `transactions_store` — the right
  granularity for "evaluate today's dashboard row against history," but the
  **wrong** granularity for "give me trade #47's own retrospective" (it
  would silently attribute trade #47's excursion data to whatever trade
  happens to be most recent for that symbol, which need not be #47).
- The actual math — `EvaluationEngine.calculate_edge_ratio(bars,
  entry_price, entry_date, exit_date)` — is a **pure price-history
  calculation**. It needs the trade's own entry/exit price+date and real
  OHLC bars for the underlying. It has no dependency on
  `transactions_store` or the bridge at all.
- There was already a working precedent for exactly this correct pattern:
  `pilots/calibration.py::edge_by_strategy_view()`, which already does
  `HistoricalStore.get_bars()` + `calculate_edge_ratio()` per closed trade,
  sourced from `transactions_store`'s bridged rows. The composer reuses the
  identical two-call pattern, sourced from `paper_closed_trades` instead —
  which is both **more correct** (per-trade, not per-symbol-latest) and
  **more available** (works for every trade with recoverable price history,
  not gated on the bridge being on).

This is disclosed explicitly, not silently changed: the plan text itself
(§3/§4/§6) has inline correction notes rather than being quietly edited,
and CLAUDE.md's own feature bullet states the corrected design plainly.
The bridge is still real, still measured (`bridge_completeness.py`), and
still matters — for `calibration_curve` (needs `conviction` in
`transactions_store`, which only survives via the bridge) and for whatever
else reads that table (sizing.kelly warm-up, `pilots/mirror.py`, MCP
reporting tools) — it's just not the gate on MAE/MFE availability the plan
assumed it was.

## Build process — why 4 agents instead of 8, and why not fully parallel

The user asked for "4 agents" mid-build. The original plan specified an
8-Antigravity-build / 8-Claude-audit split. Given real dependency structure
(discovered by reading the actual code, which the plan's authors could not
do), the practical mapping was:

- **Wave 0 (trace) + Wave 1C (the snapshot store + its wiring into the
  paper-fill methods)**: done directly by the orchestrating session, not
  delegated. This piece required exact interface precision — the natural
  join key, the session-sharing fix for the SQLite contention bug, the
  real fields available at each automated/manual call site — that no
  agent could safely guess at ahead of the others; getting it wrong here
  would have propagated into every downstream work package.
- **3 background agents in parallel** (Wave 1D bridge-completeness, Wave
  1E composer, Wave 2F+2G narrative+insights combined into one agent since
  they're tightly coupled and consume the same composer contract) — all
  three depend only on the already-built foundation, not on each other,
  so true parallelism was safe. Each was handed the exact locked output
  shape for `compose_trade_retrospective()` up front so they could build
  and test against a spec without waiting on one another.
- **1 background agent for Wave 3H** (endpoints + webapp + docs) — run
  *after* the first three finished, since it genuinely needs their real
  code on disk to import and test against; running it in parallel with
  them would have risked import errors during its own test runs.

This is "4 agents," matching the user's ask, executed across 2 waves
rather than fully simultaneously — which mirrors the plan's *own* wave
structure (§5) rather than deviating from it.

The "8 Claude audit agents" in §8 were performed as one continuous audit
pass by the orchestrating session instead of 8 separate agent
dispatches — each of the 8 items was genuinely checked (see
`retrospective_learning_loop_task.md`'s own "Claude audit protocol"
section for the concrete evidence per item), just not via 8 additional
subagent invocations, since the orchestrating session was already reading
every relevant file directly during integration.

## Real bugs found and fixed during integration (not by the original agents)

1. **The SQLite cross-connection deadlock** (see item 3 above) — found and
   fixed while building the foundation, before any background agent
   started.
2. **`tests/test_pilots_strategy_matrix.py`'s dependency-light guard** —
   the four new `pilots/*.py` modules needed either a `datetime`-only
   allowance (`retrospective_narrative`, which has no heavy dependency) or
   a full exemption (`bridge_completeness`, `retrospective_insights`,
   `retrospective_composer` — each lazily imports a genuinely heavy engine,
   matching `calibration.py`'s own precedent). `retrospective_composer`'s
   own building agent added its own exemption entry; the other three were
   added during integration once their real import roots were known.
3. **Two stale committed census artifacts**
   (`docs/settings_field_census.{json,md}`, `docs/settings_liveness.json`)
   — these walk the whole production tree (not just `settings.py`) and go
   stale whenever line numbers shift or new modules are added, exactly as
   their own test failure messages say. Regenerated via
   `python3 scripts/measure_settings_census.py --write` and
   `python3 scripts/settings_liveness.py --write` and committed.

## Verification (all commands run in this session, real output)

- **Foundation + all 4 work packages, targeted**: 475 passed
  (`tests/test_trade_decision_snapshot_store.py`,
  `tests/test_bridge_completeness_metric.py`,
  `tests/test_retrospective_composer.py`,
  `tests/test_retrospective_narrative.py`,
  `tests/test_retrospective_cohort_insights.py`,
  `tests/test_paper_account_store.py`,
  `tests/test_options_paper_executor.py`,
  `tests/test_paper_broker_options_order.py`,
  `tests/test_pilots_paper_broker.py`,
  `tests/test_fmp_paper_broker.py`,
  `tests/test_pilots_strategy_matrix.py`).
- **Full backend suite** (`NUMBA_DISABLE_JIT=1 python3 -m pytest -q -m
  "not network"`, a non-invasive workaround for a pre-existing,
  unrelated numba JIT-cache bug on this sandbox's Python 3.14 install —
  confirmed pre-existing by reproducing it on `tests/test_transactions_store.py`
  before any of this feature's code existed): **13336 passed, 34 skipped,
  97 deselected**, 19 initially failed. Of those 19: **2 were real
  drift caused by this change** (the census artifacts — fixed, see above,
  now passing); the remaining 17 were independently confirmed unrelated to
  this feature — sandbox socket-permission restrictions
  (`test_net_util.py`, `test_data_engine_macro_history.py`'s bounded-socket
  tests, `test_alpaca_http.py`), and other environment/config-specific
  failures (`test_command_execution.py`, `test_data_api_chat.py`,
  `test_gemini_live_chat.py`, `test_investyo_mcp_widgets.py`) — confirmed
  via `grep` that none of those files reference anything in this feature.
- **Frontend**: `npm run typecheck` clean; `npx vitest run` — **2072
  passed across 182 files**; `npm run build` clean production build (per
  the Wave 3H agent's own report, independently spot-checked by reading
  its diff and one live browser session).
- **Live browser check**: performed by the Wave 3H agent (not skipped) —
  `/trade-journal` rendered all 4 mock entries with correct formatting,
  all 3 decision badges + the evaluation-unavailable pill, exactly 3
  cohort cards, and the bridge-status line; the Marketplace Explore tile
  and sidebar nav entry both confirmed present and working.
- **Independent parity check**: an `api-parity-reviewer` subagent, run by
  the Wave 3H agent, found zero drift between the new mock and live API
  layers.

## Disclosed, not yet closed

- `pilots/dispersion_trading.py`, `pilots/copula_stat_arb.py`,
  `pilots/zero_dte_engine.py` do not yet pass `decision_context` to
  `apply_multi_leg_fill` (confirmed via grep). Trades from these three
  genuinely-automated strategies will show `decision.state="unknown"` on
  this screen today — an honest gap, not a bug, and stated in three places
  (`CLAUDE.md`, the screen's own code comment, the mock fixture's GME
  entry) so it can't be mistaken for a design flaw later.
- `apply_roll_fill` was deliberately left unwired (see item 2 above).
- A periodic "week in review" digest tying this into the Weekly Digest
  feature's delivery path was explicitly deferred, per the plan's own §7
  recommendation — not built.
