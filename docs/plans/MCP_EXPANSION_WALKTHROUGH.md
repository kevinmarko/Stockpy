# MCP Capabilities Expansion — Status

> **Corrected.** The previous version of this file claimed "I have
> successfully completed all phases... fully complete, tested, and ready for
> use." That was false for every phase: every "real"-looking tool in Phases
> 1–2 either raised on first real call (wrong kwarg name, wrong attribute
> access, a method called as a property, a required positional arg omitted)
> or silently degraded to an always-passing/no-op result while looking wired
> up. This version describes what has actually been verified, phase by
> phase, and what has NOT been touched or verified at all.

## Phase 1: Read-only analytics tools — real, verified

All 6 tools in `investyo_mcp_server.py` now compute from real platform data,
each independently re-derived and verified against the actual codebase (not
assumed from the tool's own docstring):

- `get_var_es_metrics`: real historical/parametric 95% VaR/ES from
  `data.historical_store.HistoricalStore.get_bars()` daily returns. Degrades
  to an explicit "insufficient history for ticker X" below 252 days of bars,
  never a fabricated percentage.
- `run_stress_scenario_simulation`: replays `validation.stress_scenarios`'
  real dated shock windows (OCT_2008, FEB_2018, MAR_2020, AUG_2024) against
  the operator's actual cached Robinhood positions
  (`data.robinhood_portfolio.fetch_account_snapshot(allow_live_fetch=False)`
  — never triggers a live broker login). No cached snapshot / no positions →
  an honest error, not a plausible drawdown number.
- `get_factor_attributions`: reads the real, already-computed
  `Value_Z`/`Quality_Z`/`LowVol_Z`/`Size_Z`/`Multifactor_Composite` columns
  from the `DailySignals` table for the ticker's most recent row (same
  `_db_query` pattern `get_signal_breakdown` already uses). No row → "no
  recent factor score for X". **Not** re-derivable per-ticker on demand —
  these are cross-sectional z-scores computed once per cycle across the
  whole universe by `signals/multifactor.py::pre_compute()`.
- `validate_order_compliance`: evaluates the real Kelly-cap
  (`settings.KELLY_CAP`) and options-selling VRP-regime gate conditions
  against the ticker's persisted `DailySignals` row and
  `output/state_snapshot.json` — reports each check's real verdict
  (PASS/FAIL/UNAVAILABLE), never a blanket PASSED regardless of input.
- `get_order_execution_history`: real closed fills from
  `transactions_store.py` (entry/exit price, realized P&L). Deliberately
  does **not** report a slippage figure — the schema has no
  intended/quoted/VWAP price to diff a fill against, and fabricating one
  would violate this codebase's CONSTRAINT #4.
- `get_model_drift_report`: real per-symbol forecast-skill data via
  `pilots.observability.forecast_skill_by_symbol_summary()` against
  `output/state_snapshot.json`. No snapshot → an honest "no forecast-skill
  data yet" message.

3 prompts (`pre_market_briefing`, `portfolio_health_check`,
`strategy_post_mortem`) are structured `@mcp.prompt()` templates — no
computation, nothing to fabricate; unchanged.

**Verified**: `tests/test_investyo_mcp_server.py` rewritten so each tool's
tests exercise the real code path (different inputs produce different real
output; the honest-unavailable path is exercised separately from the
real-data path) instead of mocking around the underlying bugs. Full file:
298 passed alongside the widget/annotation test files (see below).

## Phase 2: Widgets — real, verified

`mcp_widgets/build/build_bundle.mjs` genuinely bundles Chart.js v4.5.1 into
`mcp_widgets/vendor/ext-apps-bundle.js` (confirmed via `grep -c "Chart"` on
the built artifact, not just a successful exit code). All 4 tools that back
the widgets (`plot_equity_curve`, `plot_portfolio_equity`,
`get_var_es_metrics`, `get_factor_attributions`, `get_signal_breakdown`,
`get_execution_queue`) now attach a `meta=` widget wiring plus a trailing
` ```json ` payload block built from values each function already computes
— no re-derived or fabricated numbers:

- `equity-curve.html`: real per-symbol or strategy-vs-SPY equity series.
- `risk-matrix.html`: **honest scope reduction** — no live cross-symbol
  correlation data source exists anywhere in this codebase, so rather than
  fabricate one, this renders VaR/ES or factor Z-scores for one symbol at a
  time instead of a true correlation matrix.
- `signal-tree.html`: real `DailySignals` row columns from
  `get_signal_breakdown`. Deliberately does **not** show a weighted
  per-signal contribution — `SIGNAL_WEIGHTS` (keyed by module name) has no
  reliable mapping onto persisted column names, and fabricating one would
  misrepresent the real aggregation.
- `execution-queue.html`: real gated intents from `get_execution_queue`,
  with a Placeable/Gated badge from the real `allow_place` verdict — not an
  invented fill status.

**Verified**: `npm run build` succeeds and the rebuilt bundle is
byte-identical to the committed one; `tests/test_investyo_mcp_widgets.py`
(rendering, tool↔widget meta wiring, JSON-schema/honesty checks) passes
alongside Phase 1's tests, 298 total.

## Phase 3: Agent skills — rewritten, verified

All 5 `.agents/skills/*/SKILL.md` files rewritten from ~13-line generic
bullet lists to real, grounded procedures (132–198 lines each), matching
this repo's own established skill-writing bar
(`.agents/skills/strategy-validation`, `.agents/skills/pilots-endpoint`).
Each cites real file paths, real function/setting names, and real CLI
invocations verified against the actual code — and in the process corrected
several errors the original stubs had invented (e.g. `backtest-optimization`
no longer cites a `--is-options` CLI flag that doesn't exist;
`alert-rule-authoring` no longer attributes `ALERT_WEBHOOK_URL` to the wrong
module, and now documents this repo's actual 3 separate alert systems
instead of 1 invented one). YAML frontmatter validated to parse cleanly on
all 5.

## Phase 4: Execution boundary (`broker_live_execution_mcp.py`, was `robinhood_execution_mcp.py`)

Renamed from `robinhood_execution_mcp.py` — despite the old name, this file
places orders through `AlpacaBroker`/`FMPPaperBroker`, never Robinhood, so
the old filename was actively misleading. Two confirmed bugs from the
code-reading pass documented below — `get_live_positions()` crashing on any
account with real holdings, and `confirm_live_trade()`'s pre-trade risk gate
being a silent no-op — have since been fixed as a minimal, targeted patch
(see `docs/MCP_EXPANSION_PLAN.md`'s "Phase 4 — REVISED PLAN" section). The
larger propose/notify/approve/execute human-approval redesign that same
section describes has since shipped (confirmed 2026-08-29): `broker_live_execution_mcp.py`,
`execution/live_trade_proposals_store.py`'s `LiveTradeProposalStore`, the
`api/pilots_api.py` human-only approval endpoints
(`GET /pilots/execution/pending`, `POST /pilots/execution/{token}/approve`,
`POST /pilots/execution/{token}/reject`), and the
`LIVE_TRADE_EXECUTION_ENABLED`/`LIVE_TRADE_APPROVAL_ENABLED` settings all
exist on disk, with test coverage in `tests/test_broker_live_execution_mcp.py`
and `tests/test_live_trade_proposals_store.py` — see
`docs/MCP_EXPANSION_PLAN.md`'s Phase 4 section, now marked done, for detail.

Original note from this round, left for context: this file existed
(imports `OrderManager`/`PreTradeRiskGate`, has a dual-key propose/confirm
flow and a token-bucket rate limiter) but was explicitly excluded from that
round's build-out and had not been independently verified. Given the
pattern found in every other tool that round — code that imports the right
real modules but has a hidden bug making it a no-op or a silent
always-pass (an unpopulated `RiskContext()` in `validate_order_compliance`
was exactly this failure mode) — this file needed a dedicated verification
pass, the same kind of pass Phases 1–3 got. This is safety-critical: it can
place real live orders.

## Phase 5: VM deploy automation — NOT touched this round

**Update (PR #731): `.github/workflows/deploy_mcp_vm.yml` has since been
deleted** (placeholder GCP IDs, no `permissions` block, failed on every
push to `main` — see docs/known_issues/2026_08_security_quality_review.md
§5). At the time of this walkthrough it triggered on `push: branches:
[main]` — fully automatic — which reversed a deliberate, previously-documented
decision in `docs/handovers/mcp_server_split_brain.md` not to auto-restart production
from CI. **Not changed in this round** — this file was explicitly out of
scope, and no longer exists to modify. If/when Phase 5 is picked up, the
workflow needs to be created fresh with `workflow_dispatch` (manual) as its
trigger from the start per the plan's recommended default, not `on: push`.

## Cross-phase verification (this round)

- `python -m ruff check . --select=F821,F822,F823,E9` (the actual CI-scoped
  gate, not the full ruleset) → clean.
- Full offline suite, `pytest -m "not network and not slow"` → **9791
  passed, 13 skipped, 0 failed**.
- `docs/settings_liveness.json` / `docs/settings_field_census.{json,md}`
  regenerated (`scripts/settings_liveness.py --write`,
  `scripts/measure_settings_census.py --write`) to stay fresh against this
  round's changes.

Phases 4 and 5 are excluded from all of the above — none of this round's
verification touched either file.
