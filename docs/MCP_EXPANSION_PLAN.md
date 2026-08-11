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
> **Security & Execution Boundary.** `robinhood-execution-mcp` will be able to
> place live orders. Per `CLAUDE.md`, *every* order submission in this
> codebase — no exceptions — goes through `execution/order_manager.py`'s
> `OrderManager` (idempotent `client_order_id`, dry-run gate, and
> `execution/risk_gate.py`'s `PreTradeRiskGate`), never directly to a broker
> client. This plan wires the new server through that exact stack rather than
> reimplementing safety checks. Before this is built I need your explicit
> confirmation of two things: (1) that routing through `OrderManager`/
> `PreTradeRiskGate` is the correct approach (it should be — this isn't really
> optional under this repo's conventions, but I'm not fabricating a "your call"
> where the constraint has already been decided), and (2) what "dual-key human
> confirmation" concretely means for an MCP tool call — see the proposed
> two-step propose/confirm design in Component 3 below and tell me if that
> matches what you had in mind, since an MCP tool invocation has no built-in
> confirmation dialog the way a chat turn does.

> [!CAUTION]
> **Infrastructure Deployment.** `docs/mcp_server_split_brain.md` currently
> documents a *deliberate* decision not to auto-restart the production
> `investyo-mcp` service from CI ("Restarting a service on a production VM is
> a live deploy action, not something to execute autonomously from a
> docs-only change"). This plan proposes changing that. Two separate
> approvals are needed, not one: (1) do you want CI to be able to restart
> production at all, and (2) if yes, do you want it gated behind manual
> `workflow_dispatch` (someone clicks "Run workflow" after reviewing the diff)
> or fully automatic `on: push` to `main`? I've defaulted the proposal below
> to `workflow_dispatch`-only as the safer starting point — flip to `on: push`
> only if you explicitly want that. Also note `secrets.GCP_CREDENTIALS` and
> the IAM binding for VM SSH need to exist in this repo before the workflow
> can run at all; that setup isn't part of this PR and needs to happen in the
> GCP console separately.

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

## Phase 4 — Execution boundary — ⏸ NOT STARTED THIS ROUND (still gated on `[!IMPORTANT]` sign-off above)

### [NEW] `robinhood_execution_mcp.py`

*   `execute_live_trade` builds an `OrderIntent` and submits it through
    `execution/order_manager.py`'s `OrderManager.submit_order_with_idempotency`
    — inherits the dry-run gate and `PreTradeRiskGate` checks for free, exactly
    as `CLAUDE.md` requires for every order-submission path in this codebase.
    No parallel safety logic is reimplemented here.
*   **Dual-key confirmation**, concretely: `execute_live_trade` does not place
    an order on first call. It returns a `confirmation_token` describing the
    resolved order (symbol, side, qty, estimated cost, which risk-gate checks
    it will pass through) and requires a second call,
    `confirm_live_trade(confirmation_token)`, within a short TTL, to actually
    submit — mirroring the existing `propose_paper_trade_for_review` /
    review-then-execute pattern already used elsewhere in this MCP server,
    rather than inventing a new confirmation mechanism.
*   `cancel_order` / `get_live_positions` unchanged in shape from the original
    proposal, but `get_live_positions` reads from the real
    `data/robinhood_portfolio.py` snapshot, not a hardcoded `"AAPL, MSFT"`.
*   Rate-limiting: a simple per-process token bucket (e.g. N calls per minute)
    is sufficient for V1 — call this out explicitly as V1 scope so it isn't
    silently claimed as more than it is.

### Verification (Phase 4)

*   A dedicated test file exercises: dry-run intents never reach a broker call;
    a rejected `PreTradeRiskGate` check blocks submission with the gate's own
    reason string surfaced back to the caller; the propose/confirm token
    expires; and a duplicate `confirm_live_trade` call is idempotent (doesn't
    double-submit).
*   Per `CLAUDE.md`'s branch workflow, this component touches execution logic
    and goes through `git checkout -b` + PR — never a direct commit to `main`,
    regardless of which agent builds it.

---

## Phase 5 — VM deploy automation — ⏸ NOT STARTED THIS ROUND (still gated on `[!CAUTION]` sign-off above; `.github/workflows/deploy_mcp_vm.yml` currently triggers on `push: main`, NOT the recommended `workflow_dispatch` default — do not treat it as production-safe until this phase is explicitly picked up)

### [NEW] `.github/workflows/deploy_mcp_vm.yml`

*   Trigger: `workflow_dispatch` only, per the default proposed above — change
    to `on: push` only on your explicit instruction.
*   Body: the same SSH/restart command already verified and documented in
    `docs/mcp_server_split_brain.md`'s existing remediation section (correct
    `cd /opt/investyo` before `sudo -u investyo`, correct zone/project/VM
    name) — reused verbatim, not re-derived.
*   Requires `secrets.GCP_CREDENTIALS` and IAM configured in GCP first (your
    action, outside this PR).

### [MODIFY] `docs/mcp_server_split_brain.md`

*   Update the "Remediation" section to describe the new manual-trigger
    workflow as an *option* operators can run instead of the raw `gcloud`
    command, not as something that now happens automatically and
    unconditionally — the doc's own prior reasoning against silent automation
    stays correct until you say otherwise.

### Verification (Phase 5)

*   Trigger the workflow manually once against a non-critical change and
    confirm `systemctl status investyo-mcp` reports healthy afterward, per the
    doc's existing "Verify afterward" section.

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
