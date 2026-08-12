# Goal Description

Expand the InvestYo / Stockpy MCP ecosystem with real risk analytics, pre-trade
compliance checks, interactive web widgets, a properly-isolated live-execution
boundary, operational agent skills, and automated VM deployment.

> [!NOTE]
> **Revision note (supersedes the prior version of this plan).** A review of the
> first pass found that `task.md` and `walkthrough.md` had marked every
> component complete despite: (a) two `User Review Required` items never
> actually approved, (b) three Open Questions never answered, and (c) the code
> that got written not matching what was claimed — every new analytics/
> compliance tool was a hardcoded stub returning fabricated numbers regardless
> of input (`validate_order_compliance` always returned `"PASSED"`), the
> execution server did nothing (no broker call, no confirmation, no rate
> limiting despite the docstring claiming both), the widget templates had no
> charting library wired in despite `build_bundle.mjs` being marked updated,
> and none of the 9 new tools/prompts had any test coverage despite the
> walkthrough claiming verification. This revision fixes the plan itself —
> scoping each component to what can be honestly built and verified in
> isolation, and re-flagging the items that need your sign-off *before* code
> is written, not after.
>
> This plan is deliberately staged. **Do not implement Phase 2 or Phase 3
> until Phase 1 is reviewed and you've told me to continue** — that's the
> "incrementally, based on your priorities" ask from the original Open
> Question 1, made structural this time instead of a question that gets
> silently skipped.

## User Review Required

> [!IMPORTANT]
> **Security & Execution Boundary — ANSWERED.** The operator confirmed
> routing through `OrderManager`/`PreTradeRiskGate` (never optional under
> this repo's conventions anyway) and explicitly asked for a genuine
> human-approval gate: the calling MCP agent must not be able to confirm
> its own proposed trade. Phase 4 below now specifies the concrete design —
> a durable `LiveTradeProposal` row, a real alert
> (`observability.alerts.send_alert`) when one is created, and an
> approve/reject action that exists **only** as `api/pilots_api.py`
> endpoints + a Pilots PWA screen, never as an MCP tool the agent could call
> itself. `confirm_live_trade` refuses to execute anything not already
> marked `approved` by that separate human surface. This also fixes two
> confirmed real bugs found by reading the code that landed with #675:
> `get_live_positions()` crashes on any account with real holdings (wrong
> attribute names, iterates a dict as a list), and `confirm_live_trade()`
> currently submits every order with the pre-trade risk gate silently
> disabled (no `risk_context` is ever built or passed). See Phase 4 below
> for the full design, ready for a fresh agent to implement.

> [!CAUTION]
> **Infrastructure Deployment — ANSWERED.** The operator confirmed
> `workflow_dispatch` (manual trigger) over fully automatic `on: push`. Note
> for whoever builds Phase 5: `.github/workflows/deploy_mcp_vm.yml` landed
> on `main` via #675 still wired to `on: push: branches: [main]` — the
> confirmed `workflow_dispatch` change was never applied, so this is a real
> outstanding fix, not just an open question anymore. Also note
> `secrets.GCP_CREDENTIALS` and the IAM binding for VM SSH need to exist in
> this repo before the workflow can run at all; that setup isn't part of
> this PR and needs to happen in the GCP console separately.

## Open Questions

These block the components they gate — I'm not silently picking an answer
this time.

1. **Prioritization** — this plan proposes the phase order below (analytics
   tools → widgets → skills → execution boundary → deploy automation, roughly
   safest-and-most-self-contained first). Confirm or reorder.
2. **Widget framework** — proposal below is to **stay on vanilla JS/Chart.js**,
   consistent with the existing `mcp_widgets/build/build_bundle.mjs` esbuild
   pipeline and the other widgets already in `mcp_widget_resources.py`
   (`follow-result.html`, `pilot-compare.html`, etc., all vanilla). Introducing
   React would mean a second build toolchain for one widget family — only do
   that if there's a concrete reason vanilla Chart.js won't work for the
   equity-curve/risk-matrix use cases. Confirm vanilla, or tell me what's
   pushing toward React.
3. **Real-time transport** — proposal below is **SSE via FastMCP**
   (`investyo_mcp_server.py` already supports `--transport sse`; this repo has
   no existing WebSocket server anywhere, so SSE reuses infrastructure instead
   of adding a new transport). Confirm SSE, or tell me what needs bidirectional
   WebSocket that SSE can't do (SSE is one-way server→client, which covers
   "subscribe to price ticks / alerts" fine — it does not cover a client
   pushing anything back over the same connection).

Answered: Open Question 1 resolved to Phases 1–3 now, Phases 4–5 deferred.
Open Question 2 resolved to vanilla JS/Chart.js (built). Open Question 3
(SSE vs WebSocket) does not arise in Phases 1–3 as scoped — no new
real-time/streaming component was part of this round; revisit if a future
phase actually needs one.

---

## Phase 1 — Read-only analytics tools, real data only — ✅ DONE, verified

See `docs/MCP_EXPANSION_WALKTHROUGH.md` for the full verified breakdown
(each tool, its real data source, and the test/lint/pytest results). Kept
below for the original design rationale.

Scope deliberately narrowed to what can be computed from data this codebase
already has, with an honest "not available" path (per this repo's
CONSTRAINT #4 — never fabricate; NaN/explicit-unavailable beats a plausible
fake number) for anything it can't.

### [MODIFY] `investyo_mcp_server.py`

*   `@mcp.tool() def get_var_es_metrics(ticker: str, method: Literal["historical","parametric"] = "historical") -> str`
    Computes historical VaR/ES from real daily returns via `HistoricalStore.get_bars()`
    (already the platform's bar source — see `data/historical_store.py`), using
    the same `< 1e-12` degenerate-std guard convention as `validation/metrics.py`.
    Returns an explicit `"insufficient history"` message (not a number) below a
    minimum sample size — never a fabricated percentage.
*   `@mcp.tool() def run_stress_scenario_simulation(portfolio_id: str, scenario: str) -> str`
    Reuses `validation/stress_scenarios.py`'s existing dated shock windows
    (OCT_2008, FEB_2018, MAR_2020, AUG_2024 — the same ones the options-selling
    deployability gate already uses) rather than inventing new ones. Applies
    the shock to the real positions in the requested portfolio via
    `data/robinhood_portfolio.py` / `transactions_store.py`. If `portfolio_id`
    doesn't resolve to a real account, return an error, not a plausible drawdown
    number.
*   `@mcp.tool() def get_factor_attributions(ticker: str) -> str`
    Reuses `processing_engine.calculate_fundamental_metrics()`'s existing
    `Value_Z`/`Quality_Z`/`LowVol_Z`/`Size_Z`/`Multifactor_Composite` outputs
    (already computed and stored per `signals/multifactor.py` — see CLAUDE.md's
    multifactor bullet) instead of a second, disconnected factor model. Any
    ticker outside the last-computed universe returns "no recent factor score,"
    not a fabricated `beta: 1.1`.
*   `@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True)) def get_order_execution_history(limit: int = 50) -> str`
    Queries real fills from `transactions_store.py` (the same store
    `execute_paper_trade`/`get_trade_journal` already read from) — slippage vs.
    the recorded fill price, not a hardcoded "2bps."
*   `@mcp.tool() def get_model_drift_report() -> str`
    Reuses the forecast-skill machinery already built for the Observability
    screen (`pilots/observability.py::forecast_skill_by_symbol_summary`, per-
    model RMSE) instead of a new invented "10% decay" metric. Empty DB → an
    honest "no drift data yet" message, matching that module's existing
    contract.
*   `@mcp.tool() def validate_order_compliance(ticker: str, side: str, size: float) -> str`
    **Must call the real gates, not assert PASSED.** Reuses
    `sizing/position_sizer.py`'s Kelly-cap check and the VRP regime gate
    documented in CLAUDE.md (`true_ivr > 50`, `VRP > 0.02`, `VIX < 30`, no
    `CREDIT EVENT`) as read-only checks — this tool never places or queues an
    order, it only evaluates the same conditions `execution/risk_gate.py`'s
    `PreTradeRiskGate` would apply, and returns which specific check(s) failed
    or passed. A stub that can't reach real data must say so explicitly
    (`"compliance check unavailable: <reason>"`), never a hardcoded verdict.
*   **Prompts (`@mcp.prompt()`)**: `pre_market_briefing`, `portfolio_health_check`,
    `strategy_post_mortem` — unchanged from the original proposal, these are
    just structured prompt templates (no computation, nothing to fabricate).

### Verification (Phase 1)

*   `tests/test_investyo_mcp_server.py` gains one test per new tool, asserting
    real behavior against fixture data (a known return series → a known VaR;
    a symbol with no recent multifactor score → the explicit "unavailable"
    string, not a number). A tool with zero new tests is not "done."
*   Confirm each tool's output changes when its inputs change (a VaR call for
    two different tickers with different fixture return series must return
    different numbers) — this is the concrete regression test against
    reintroducing a hardcoded stub.

---

## Phase 2 — Interactive widgets — ✅ DONE, verified (with one honest scope cut)

### [MODIFY] `mcp_widget_resources.py`, [MODIFY] `mcp_widgets/build/build_bundle.mjs`, [NEW] `mcp_widgets/templates/*.html`

*   Register `ui://widgets/equity-curve.html`, `risk-matrix.html`,
    `signal-tree.html`, `execution-queue.html` (unchanged from original).
*   **`build_bundle.mjs` must actually be edited** to pull in Chart.js (per
    Open Question 2's default) and bundle it — this was silently skipped
    last time; "the widget resource is registered" is not the same as "the
    widget renders a chart."
*   Equity curve reads real equity-curve data the same way
    `plot_equity_curve`/`plot_portfolio_equity` (already-shipped MCP tools)
    do, not placeholder numbers.

### Verification (Phase 2)

*   `cd mcp_widgets/build && npm run build` succeeds AND the built bundle
    actually contains a Chart.js reference (`grep` the output, don't just
    check exit code).
*   Manually open each `ui://` resource in a supported client and confirm a
    real chart renders, not an empty `<div>`.

---

## Phase 3 — Agent skills — ✅ DONE, verified

### [NEW] `.agents/skills/{backtest-optimization,regime-model-tuning,mcp-widget-builder,alert-rule-authoring,incident-triage}/SKILL.md`

Written to match this repo's own established skill bar — compare
`.agents/skills/strategy-validation/SKILL.md` (260 lines: exact commands,
gate thresholds, two-place documentation requirement, honest-FAIL handling)
or `.agents/skills/pilots-endpoint/SKILL.md` (65 lines: concrete auth-tier
decision tree, exact file paths) — not the 12-14 line generic-bullet versions
from the first pass. Each skill needs, at minimum: the exact CLI command(s)
involved (e.g. `python -m validation.harness --strategy <name> --start
YYYY-MM-DD --end YYYY-MM-DD`, not "run validation/harness.py"), the specific
thresholds/gates already documented in `CLAUDE.md` (don't restate them
loosely — copy the exact numbers), and at least one documented failure mode
per skill (what does it look like when this goes wrong, and what's the fix).

### Verification (Phase 3)

*   Skills load without YAML frontmatter errors (as originally planned).
*   Each skill is reviewed against its Claude Code sibling (where one exists)
    for parity of concrete detail, not just topic coverage.

---

## Phase 4 — Execution boundary — REVISED PLAN, ready for a fresh agent to build

> **Why this section was rewritten.** `broker_live_execution_mcp.py` (at the
> time this section was written, still named `robinhood_execution_mcp.py` --
> renamed in a later minimal-patch pass, see the note below) already
> exists on `main` (landed as part of PR #675) but was explicitly excluded
> from that round's build-out and verification. A direct code-reading pass
> (not a test run — no test file covered these paths at all) found it has
> the exact "imports the right real modules, wrong details underneath" bug
> class every other tool in this plan turned out to have:
>
> 1. **`get_live_positions()` — 3 confirmed bugs, crashes on any account with
>    real holdings.** `AccountSnapshot.positions` is a `dict[symbol ->
>    PortfolioPosition]` (confirmed in `data/robinhood_portfolio.py` and
>    independently in `api/pilots_api.py::_serialize_portfolio`'s docstring),
>    but the code does `for p in positions:` — iterating the dict's string
>    *keys*, not its values — then calls `p.symbol`/`p.qty` on that string,
>    which raises immediately. `PortfolioPosition` has no `.qty` field (it's
>    `.quantity`), and `AccountSnapshot` has no `.net_liquidity` field (it's
>    `.total_equity`).
> 2. **`confirm_live_trade()` — the pre-trade risk gate is currently a
>    no-op.** It calls `OrderManager.submit_order_with_idempotency(intent)`
>    without ever building or passing `risk_context`. Read
>    `execution/order_manager.py`'s actual code: when a gate is configured
>    (it is — `PreTradeRiskGate()` is passed at construction) but
>    `risk_context is None`, the manager does **not** block — it logs
>    `"ALL pre-trade risk checks are being silently skipped for this
>    order"` and lets the trade through anyway. Only the separate global
>    kill switch (`execution/kill_switch.py`, checked unconditionally,
>    independent of `risk_context`) still actually functions today.
>
> Beyond fixing those, **the operator explicitly asked for a real
> human-approval gate**: today's "dual-key confirmation" is just two MCP
> tool calls (`execute_live_trade` then `confirm_live_trade`) — nothing
> stops the *same* calling agent/session from making both calls back to
> back with no human ever actually seeing the trade. This plan redesigns
> the flow so a human, not the calling agent, is the only party that can
> ever set a proposal to `approved`.

### Design: propose (agent) → notify (system) → approve (human, separate surface) → execute (agent, gated)

Do not reinvent this shape — this codebase already has the exact template
in `rlhf_calibration_store.py` (`RlhfCalibrationProposal(Base)` +
`RlhfCalibrationStore`, `POST /rlhf/proposals` to create,
`POST /rlhf/proposals/{id}/review` for the human's verdict). Copy that
propose/store/human-reviews-via-a-separate-endpoint shape; do not
build a parallel confirmation mechanism from scratch. The critical
difference from the RLHF queue: that one is paper-only advisory data with
no capital at stake, so the webapp never even needed to call its own
create endpoint. This one gates **real order submission**, so the
approve/reject action must be reachable *only* from a surface the MCP
agent has no path to invoke itself.

#### [NEW] `execution/live_trade_proposals_store.py`

A new store, matching `rlhf_calibration_store.py`'s SQLAlchemy-declarative
pattern (own `Base`, resolved through `db_config.py` like
`transactions_store.py`/`cache_long_short_store.py`). One row per proposed
trade:

```
LiveTradeProposal:
    id / token          (str, primary key — the value returned to the caller)
    symbol, side, qty, order_type, limit_price   (the resolved OrderIntent fields)
    strategy_id          (str, default "mcp-agent" — who/what proposed it)
    proposed_at          (datetime, UTC)
    expires_at           (datetime, UTC — proposed_at + TTL, e.g. 5 min, matching today's constant)
    status                ("pending_approval" | "approved" | "rejected" | "expired" | "executed" | "failed")
    approved_at / approved_by   (nullable — set ONLY by the human-approval endpoint below, never by the MCP tool)
    broker_order_id / error_message   (nullable — set by confirm_live_trade after a real submission attempt)
```

This REPLACES the current `_pending_orders = {}` in-memory module dict —
durable storage also fixes the secondary gap that a process restart
between propose and confirm silently drops the proposal today.

#### [MODIFY] `broker_live_execution_mcp.py::execute_live_trade`

*   Builds the `OrderIntent` exactly as today (same validation of
    `side`/`order_type`), but instead of storing it in `_pending_orders` and
    returning immediately, writes a `LiveTradeProposal` row with
    `status="pending_approval"` via the new store.
*   Sends a real notification via the existing multi-channel alert
    infrastructure, `observability.alerts.send_alert("WARNING", ...)`
    (the same function already used for the Sizing Cap threshold alert —
    do not build a new notification path) with the proposal's id/token and
    order details, so the operator is actually told a trade is waiting
    rather than needing to poll.
*   Returns the token/id to the caller with an explicit, unambiguous
    message: **this order will NOT execute until the operator approves it
    through the Pilots PWA — do not expect `confirm_live_trade` to succeed
    on the next call.** (Today's docstring already implies a wait; make the
    tool's *return value*, not just its docstring, say this plainly, since
    an agent reads the return value, not the source.)
*   Still consumes the rate limiter (keep the existing token-bucket class,
    just point it at proposal creation instead of at both tools equally —
    a human approving is not a rate-limitable event).

#### [NEW] `api/pilots_api.py` — human-only approval surface

*   `GET /pilots/execution/pending` — list proposals with
    `status="pending_approval"` and not yet expired. Read tier
    (`require_read_token`), matching every other GET in this file.
*   `POST /pilots/execution/{id}/approve` and
    `POST /pilots/execution/{id}/reject` — the ONLY way a proposal's status
    can become `approved`. Gated by `require_command_token` **stacked with
    a new dedicated flag**, following the exact `require_rag_query_enabled`
    /`DATA_APP_CHAT_ENABLED` precedent from Phase 1 (own risk class, not a
    shared flag) — call it `LIVE_TRADE_APPROVAL_ENABLED`. Per this
    codebase's "changes what the platform can do with capital, defaults
    False" carve-out (see `CACHE_LONG_SHORT_WRITES_ENABLED`'s reasoning in
    `CLAUDE.md`) this master switch defaults **`False`**, not the
    2026-08-03 "new admin capabilities default True" convention — that
    convention explicitly excludes anything that "changes trading
    behavior." Record `approved_by` from whatever identity context this
    endpoint already has available (there is no per-operator auth in this
    single-operator codebase — a fixed string like `"operator"` is honest
    and sufficient; do not fabricate a user-identity system that doesn't
    exist elsewhere here).
*   These two endpoints are load-bearing precisely *because* they are not
    MCP tools — `investyo_mcp_server.py`/`broker_live_execution_mcp.py` MUST
    NOT expose an equivalent "approve" tool. The whole point of this
    redesign is that the calling agent has no code path to set
    `approved_by` itself.

#### [NEW] Pilots PWA — pending live-trade approvals

A small screen (or a card on an existing operational screen — Commands or
Observability are the closest fits) listing `GET /pilots/execution/pending`
with Approve/Reject buttons calling the two endpoints above. This is the
human's actual interaction surface. Follow this repo's standard webapp
pattern for a new screen (see `.agents/skills/new-pwa-screen/SKILL.md`):
types → client + mock → screen → route → test, mock/live parity gate.

#### [MODIFY] `broker_live_execution_mcp.py::confirm_live_trade`

*   Looks up the proposal by id. If `status != "approved"`, return an
    honest "still pending operator approval" (or "rejected"/"expired") —
    never execute. This is the enforcement point: the calling agent cannot
    forge an approved status, so this check is the actual gate.
*   If approved: build a **real, populated `RiskContext`** — this is the
    fix for the silently-skipped risk gate. Source real data the same way
    the Phase 1 risk-tools agent already established:
    `account`/`open_positions` from
    `data.robinhood_portfolio.fetch_account_snapshot(allow_live_fetch=False)`
    (cache-only — never trigger a live broker login from this path),
    `macro` from whatever the platform's last-computed `MacroEconomicDTO`
    source is (check `output/state_snapshot.json` or the macro engine's own
    cached read path — do not add a new live macro fetch here).
    `validation_reports`/`returns_df`/`start_of_day_equity` may stay `None`
    if genuinely unavailable (every `RiskContext` field is documented
    optional and a `None` field passes conservatively — this is a real,
    intentional degrade path, not a gap to fabricate around).
*   Calls `OrderManager.submit_order_with_idempotency(intent,
    risk_context=risk_context)` — the one-line fix that actually turns the
    risk gate on.
*   Updates the proposal row to `executed`/`failed` with the real
    `broker_order_id`/`error_message`.

#### [MODIFY] `broker_live_execution_mcp.py::get_live_positions`

*   Fix the 3 confirmed bugs: iterate `snapshot.positions.values()`, use
    `.quantity` not `.qty`, use `.total_equity` not `.net_liquidity`.

#### [NEW SETTING] `LIVE_TRADE_EXECUTION_ENABLED`

Master switch for the whole `execute_live_trade`/`confirm_live_trade` pair
(distinct from `LIVE_TRADE_APPROVAL_ENABLED` above, which gates the
*approval* endpoints specifically) — defaults **`False`**, same
capital-affecting-behavior carve-out reasoning as above. Both flags must be
`True`, on top of each surface's own token gate, before a real order can
ever be placed through this path.

### Verification (Phase 4)

*   `execute_live_trade` never calls the broker, ever — assert this
    directly (mock/spy the broker and assert zero calls) rather than only
    checking the returned message text.
*   `confirm_live_trade` on a `pending_approval`/`rejected`/`expired`
    proposal never calls `submit_order_with_idempotency` — same
    zero-broker-calls assertion.
*   `confirm_live_trade` on an `approved` proposal calls
    `submit_order_with_idempotency` with a `risk_context` that is NOT
    `None` and contains real, non-empty `account`/`open_positions` data
    from a test fixture snapshot — this is the concrete regression test
    against reintroducing the exact silently-skipped-gate bug found above.
*   The approval endpoints are reachable only via `require_command_token` +
    `LIVE_TRADE_APPROVAL_ENABLED`; confirm no MCP tool can set
    `status="approved"`.
*   `get_live_positions()` against a real (test) `AccountSnapshot` fixture
    with actual positions does not raise and reports correct
    `quantity`/`total_equity` values — the regression test for the 3
    confirmed bugs.
*   A duplicate `confirm_live_trade` call on an already-`executed` proposal
    is idempotent (doesn't double-submit) — `OrderManager`'s own
    `client_order_id` dedup already guarantees this at the broker layer;
    write the test that proves it holds through this new code path too.
*   Per `CLAUDE.md`'s branch workflow, this component touches execution
    logic and goes through `git checkout -b` + PR off current `main` —
    never a direct commit, regardless of which agent builds it.

---

## Phase 5 — VM deploy automation — ready for a fresh agent to build

### [MODIFY] `.github/workflows/deploy_mcp_vm.yml`

*   Change the trigger from `on: push: branches: [main]` (its current state
    on `main` as of this writing — landed via #675 without this change)
    to `on: workflow_dispatch` — per the operator's earlier answer, this is
    the confirmed default, not still an open question.
*   Body: the SSH/restart command itself is already correct (verified
    against `docs/mcp_server_split_brain.md`'s documented remediation —
    correct `cd /opt/investyo` before `sudo -u investyo`, correct
    zone/project/VM name) — leave it as-is, do not re-derive it.
*   Requires `secrets.GCP_CREDENTIALS` and IAM configured in GCP first
    (operator action, outside this PR — confirm this exists before relying
    on the workflow; if it doesn't, the workflow will fail cleanly at the
    auth step rather than silently doing nothing).

### [MODIFY] `docs/mcp_server_split_brain.md`

*   Update the "Remediation" section to describe the manual-dispatch
    workflow as an operator-invokable option (a button click after
    reviewing the diff) instead of the current state (which, since it
    landed on `main` still wired to `push:main`, has been describing
    automatic-and-unconditional deployment inaccurately since #675 merged
    — fix this promptly, independent of when the rest of Phase 5 lands).

### Verification (Phase 5)

*   Trigger the workflow manually once (via `gh workflow run` or the
    Actions UI) against a non-critical change and confirm
    `systemctl status investyo-mcp` reports healthy afterward, per the
    doc's existing "Verify afterward" section.
*   Confirm the workflow does NOT fire on an ordinary `git push` to `main`
    that touches `investyo_mcp_server.py` — the actual regression test for
    this phase.

---

## Verification Plan (overall)

*   Nothing in `task.md` gets checked off until: the corresponding code is
    real (no hardcoded/plausible-looking placeholder output), it has a test
    that would fail if the implementation were reverted to a stub, and — for
    anything in Phase 4 or 5 — you've explicitly approved that phase.
*   `walkthrough.md` for this plan should only claim a component is complete
    once the above holds; a component with test coverage that doesn't
    exercise the new code is not "verified," regardless of the overall suite's
    pass/fail count.
