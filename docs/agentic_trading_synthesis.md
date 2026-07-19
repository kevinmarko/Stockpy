# Agentic Trading — Capability Synthesis

**Method:** codebase capability audit (three parallel Explore passes: webapp/PWA structure,
Robinhood execution backend, and platform-wide agentic-trading building blocks) | **Date:** 2026-07-18
| **Companion build:** the Agentic Trading tab (`webapp/src/screens/AgenticTrading.tsx`, route
`/agentic`) and its backing endpoints (`GET /agentic/status`, `GET /agentic/discovery`,
`PUT /agentic/scan-config` in `api/pilots_api.py`)

## Executive summary

Before this effort, Stockpy already had a **complete, gated advisory→queue→human-execution
pipeline** and a **Pilots "copy-a-strategy" layer with a follow/mirror write path** — genuinely
agentic building blocks, just scattered across three different webapp screens (a Robinhood queue
view buried inside Commands, execution-mode/kill-switch controls in Settings, brokerage connect
also in Settings) with no single place to see "what is the agent doing." The one capability that
was **structurally absent** — not just unsurfaced — was *opportunity discovery*: the Robinhood
Trading MCP exposes broker-scan tools (`create_scan`, `run_scan`, `update_scan_filters`,
`get_scanner_filter_specs`) that had **zero usage anywhere in the repo**. The platform only ever
analyzed a fixed, operator-curated universe (held positions ∪ `WATCHLIST` ∪ `watchlist.txt`); it
never looked for new candidates on its own.

This synthesis maps every capability relevant to "agentic trading," rates each as **production-ready**,
**partial**, or **net-new**, and records what this PR built on top of that map: a consolidated
command-center tab, plus the first net-new capability (scan-based discovery), delivered through a
companion Claude Code skill rather than a backend change — because the webapp/API cannot call the
Robinhood MCP at all, only a live agent session can (the same constraint that already shapes
`robinhood-execution`).

**The one thing this deliberately does NOT do:** close the loop to unsupervised order placement. That
gap is structural, not an oversight — an AST-guarded test (`tests/test_pipeline_smoke.py::TestNoOrderFunctions`)
forbids order-submission symbols outside `execution/`, and the only actor ever permitted to call
`place_equity_order` is a live Claude Code session running the `robinhood-execution` skill with
per-trade human confirmation. Every control this synthesis recommends builds *toward* that gate, never
around it.

---

## Capability map

| Capability | Status | Key files |
|---|---|---|
| Copy-a-strategy Pilots + follow/mirror rebalance | **Production-ready** | `pilots/catalog.py`, `pilots/mirror.py`, `pilots/follows_store.py` |
| Holding-aware recommendations (rationale + sizing) | **Production-ready** | `engine/advisory.py` |
| Advisory-only adaptive-cadence agent loop policy | **Partial** (advisory only, by design) | `engine/advisory_agent.py` |
| Gated dry-run order queue → human execution | **Production-ready, gated** | `execution/queue_builder.py`, `.claude/skills/robinhood-execution/` |
| Decision journal | **Production-ready** | `gui/decision_log.py`, `GET/POST /decisions` |
| Paper trading book | **Production-ready** | `transactions_store.py`, MCP `execute_paper_trade` |
| Backtest / simulation engine | **Partial** (optional `vectorbt`/`backtrader` deps) | `simulation_engine.py` |
| Interval daemon + cron scheduling | **Production-ready, gated** | `desktop/daemon_runtime.py`, `deploy/crontab.txt` |
| Brokerage read/quotes/watchlists/orders/options | **External MCP, gated** | `robinhood-trading` MCP (agent-only) |
| **Opportunity discovery via broker scans** | **Was net-new; now built** | `.claude/skills/agentic-discovery/`, `pilots/discovery.py`, `pilots/scan_config_store.py` |
| Consolidated agent command-center UI | **Was net-new; now built** | `webapp/src/screens/AgenticTrading.tsx` |
| Autonomous placement w/o per-trade human gate | **Intentionally absent** | blocked by AST guard + skill design — out of scope, not a gap |

### Production-ready: the copy-trading spine

`pilots/catalog.py` packages 17 of the platform's own signal-module weight blends as copyable
"Pilots," each joined (where an honest backtest exists) to a validated,
PBO/DSR-gated strategy in `scripts/refresh_validations.py`'s `STRATEGY_REGISTRY`. Following one with
`$X` (`pilots/mirror.py::plan_follow`) computes a proportional rebalance-to-target order set —
BUY when underweight, partial SELL when overweight, SELL-to-zero when the Pilot drops a name — and
emits it into the same gated queue `execution/queue_builder.py` builds for direct advisory
recommendations. It contains **no order-submission code**; that's enforced by the same AST guard
that governs the rest of the execution surface. This was already the closest thing to "agentic
trading" in the platform, and the new tab's Controls section deep-links to it (`/marketplace`)
rather than duplicating follow management.

### Partial: the advisory loop already has a scheduler skeleton

`engine/advisory_agent.py` is worth calling out specifically: it's an **advisory-only autonomous
loop policy** that already exists — `compute_next_run_delay()` picks an adaptive cadence from market
hours, macro regime, VIX, and recent error count; `compute_backlog_reminders()` re-pings
high-conviction recommendations the operator hasn't logged a decision on, on escalating 1h/4h/24h
tiers; `AgentState` round-trips to `output/agent_state.json`. Every `compute_*` function is pure and
lookahead-free by construction — it never calls a market-data provider or forecaster itself. This
PR's `pilots/agentic.py::agent_loop_status()` reads that persisted state (cycle count, last-cycle
timestamp, backlog size) for the new tab's header, without importing `engine.advisory_agent` itself
— it ports the minimal read logic instead, matching `pilots/run_status.py`'s established precedent for
staying off the `engine` package's import path entirely.

### The net-new piece: scan-based discovery

The Robinhood Trading MCP's scan tools were unused. Closing this required two things this repo didn't
have a template for:

1. **A companion Claude Code skill**, not a backend feature — `.claude/skills/agentic-discovery/SKILL.md`.
   The webapp and Pilots API cannot reach the Robinhood MCP under any circumstance (same constraint
   `robinhood-execution` operates under); only a live agent session can. The skill runs the
   operator's configured scans, cross-references hits against the platform's advisory engine via the
   investyo MCP's `get_recommendation(symbol)` tool (which surfaces `engine.advisory.evaluate()`'s
   output), and writes `output/scan_candidates.json`. It is explicitly **read-only with respect
   to orders** — it never calls `place_equity_order`/`review_equity_order`/any option-order tool, and
   never fabricates a score for a symbol the advisory cross-reference couldn't reach (`action: null`,
   `conviction: null`, not a guess).
2. **A dedicated, `.env`-independent config store** — `pilots/scan_config_store.py::ScanConfigStore`,
   an atomic JSON store (mirrors `pilots/follows_store.py`'s write-then-rename idiom) rather than an
   `.env` key, because scan configs are structured, multi-row, operator-editable data, not a global
   tunable. `PUT /agentic/scan-config` writes to it behind a new dedicated flag,
   `AGENTIC_DISCOVERY_ENABLED` (default `False`, deliberately absent from `gui/env_io.py`'s
   `ALLOWED_KEYS`/`SECRET_KEYS` — hand-set in `.env` only, per this repo's write-endpoint auth
   taxonomy: a flag that changes *what the agent discovers* earns its own risk class, same reasoning
   as `STRATEGY_WRITES_ENABLED` not riding in on `AUTOMATION_WRITES_ENABLED`).

### What stays intentionally out of reach

Full brokerage capability (read portfolio, quotes, watchlists, place/cancel equity and option orders)
is available through the external Robinhood MCP, but the repo only ever touches it through two narrow,
human-gated doors: `robinhood-execution` (placement, `live` mode, per-trade confirmation) and now
`agentic-discovery` (discovery only, never placement). Nothing this synthesis found — and nothing this
PR built — creates a third door. An agent that discovers candidates and an agent that places orders
remain two different, independently gated skills on purpose; collapsing them would remove the
per-trade human checkpoint that's this platform's core safety invariant.

---

## Insights → opportunities

| Insight | Opportunity | Impact | Effort |
|---|---|---|---|
| The queue view, execution-mode toggle, kill switch, and follow management were scattered across 3 screens with no single "what is the agent doing" view | Consolidated Agentic Trading tab (`/agentic`) — **done this PR** | High (operator clarity) | Low (mostly composition of existing reads) |
| Scan tools existed in the Robinhood MCP with zero repo usage | `agentic-discovery` skill + `scan_candidates.json`/`scan_configs.json` + Discovery section — **done this PR** | High (first real "find new ideas" capability) | Medium (new skill + 2 new dependency-light stores + 3 endpoints) |
| `engine/advisory_agent.py`'s adaptive cadence and backlog reminders had no UI surface at all | Agent status header surfaces cycle count / last-cycle / backlog size — **done this PR** | Medium | Low (pure read of an existing artifact) |
| The execution-mode ladder (advisory/simulation/paper/live) is a deliberate 4-step safety progression Settings already owns | Deep-link from the new tab rather than a second, simplified copy of the same control | Avoids a real risk: two controls for one setting, one of them incomplete | N/A (design decision, not built) |
| `PUT /automation/execution-mode` throws an unhandled Pydantic error in the current environment and has zero test coverage | Fix + add tests (flagged as a separate task, out of scope for this PR) | High (the existing Settings "1-Click Go Live" control may be broken today) | Low–Medium |
| Paper trading (`execute_paper_trade`) and dry-run queue mode together let an agentic loop run end-to-end without ever touching a live broker | A future "paper-mode agent run" surface could exercise discovery → advisory → queue with zero real-money risk | Medium (safe sandbox for iterating on discovery/scan quality) | Medium |
| Backtesting depends on optional `vectorbt`/`backtrader` — degrades with a warning, not a crash, when absent | No action needed; already dead-letter-resilient | — | — |

---

## Recommendations

1. **(Done this PR) Ship the consolidated tab and scan-discovery skill** — the two highest-leverage,
   lowest-risk moves: one is pure UI consolidation of already-gated reads/writes, the other is the
   platform's first opportunity-discovery capability, delivered without touching the order-placement
   boundary at all.
2. **(Follow-up, separate task) Fix `PUT /automation/execution-mode`** — surfaced in passing while
   building the Controls section; untested and currently throws in this environment. Flagged
   separately rather than folded into this PR to keep the change set scoped.
3. **(Future, not started) Extend discovery into a paper-mode closed loop** — run
   `agentic-discovery` → advisory cross-reference → `execute_paper_trade` on a schedule, entirely
   inside the paper book, before ever considering a live-mode discovery→placement path. This is the
   natural next increment if the operator wants to evaluate scan quality over time without live risk.

## Methodology notes

Synthesized from three parallel Explore-agent passes over the live codebase (not interviews/surveys —
this is a capability audit, not user research, so the `/research-synthesis` format is adapted
accordingly: "capabilities" stand in for "themes," "status" stands in for "prevalence"). Every capability
listed was independently verified against source (file paths cited throughout), not inferred from
documentation alone. The "net-new" findings were confirmed by an exhaustive repo-wide grep for
scan-related MCP tool names returning zero hits prior to this PR.

---

## Appendix — UX improvement backlog (`/user-research` pass, post-launch)

**Method:** expert heuristic evaluation of the shipped tab (grounded in source, not inferred) plus a
jobs-to-be-done interview with the sole operator — adapted from `/user-research`'s standard
multi-participant methods, since this is a solo-operator platform (`AGENTS.md`: one user, own
capital, no team) and the operator's own answers are the research data. **Date:** 2026-07-18.

**Interview result (drives the phasing below):** job-to-be-done is an *active operational surface*
(decide/act on candidates, drive the gated queue, discover opportunities) — not a passive
at-a-glance monitor. Operated from both desktop and mobile. Rollout order: close the action loop →
fix the discovery loop → polish.

### Findings

| # | Friction | Where | Theme | Status |
|---|---|---|---|---|
| 1 | Discovery candidates were display-only — no way to watch/track from the tab | `AgenticTrading.tsx` `DiscoverySection` | Action loop | **Fixed** — [#360](https://github.com/kevinmarko/Stockpy/pull/360) added a "Watch" button (`POST /agentic/watch`) |
| 2 | Decision Journal couldn't log a decision from the tab | `AgenticTrading.tsx` `DecisionJournalSection` | Action loop | **Fixed** — #360 added a "Log" button reusing `DecisionModal` |
| 3 | Blocked queue intents, journal rows, and candidates weren't linked to their symbol page | `ExecutionQueueSection.tsx`, `AgenticTrading.tsx` | Action loop | **Fixed** — #360 |
| 4 | No "run scan" affordance — a saved scan config can't be triggered from the tab; config→results is a manual Claude Code hop | `AgenticTrading.tsx` `ScanConfigModal` | Discovery loop | **Fixed** — [#367](https://github.com/kevinmarko/Stockpy/pull/367) added a per-scan-config `CopyCommandBlock` (extracted from `Commands.tsx` in [#364](https://github.com/kevinmarko/Stockpy/pull/364)) with the exact skill-invocation phrasing, verified against `.claude/skills/agentic-discovery/SKILL.md`'s actual "runs every enabled scan" default behavior |
| 5 | Candidate list age (`generated_at`/`discovered_at`) is fetched but never rendered — a stale list looks current | `api/types.ts` `AgenticDiscovery`/`DiscoveryCandidate` | Discovery loop | **Fixed** — [#365](https://github.com/kevinmarko/Stockpy/pull/365) added an "As of {time}" line + per-candidate "discovered {time}"; confirmed `generated_at` is sourced from the scan-candidates file's own write time, never fabricated at read time |
| 6 | Pause control duplicates Settings' kill-switch toggle under a *different* label ("Agent: Running" vs "Signal generation: Running") | `AgenticTrading.tsx` `ControlsSection` vs `Settings.tsx` | Polish | **Fixed** — [#371](https://github.com/kevinmarko/Stockpy/pull/371) extracted a shared `KillSwitchToggle` used by both screens, unified label ("Signal generation") + cross-reference note on the Agentic side |
| 7 | Redundant Refresh button (the 30s poll already covers it); "Blocked" chip uses low-emphasis muted grey | `AgenticTrading.tsx`, `ExecutionQueueSection.tsx` | Polish | **Fixed** — #371 turned Refresh into a real "Refresh all" (status + Discovery + Journal); [#369](https://github.com/kevinmarko/Stockpy/pull/369) changed the Blocked chip to `tone="caution"` |
| 8 | On mobile the tab is 2 taps + a scroll to the bottom of the "More" sheet (last of 15 items) | `App.tsx` `NAV_ITEMS`/`MOBILE_PRIMARY_COUNT` | Cross-cutting | Not started |

Confirmed: **zero usage telemetry** in the webapp — the decision log is the only behavioral trace of
tab usage, and it only records deliberate "Log decision" actions. Noted as a finding, not treated as
a gap to fix (instrumenting a solo local app isn't worth the added surface).

### Backlog

**Phases 1-3 are all shipped** (findings #1-#7 — see the table above for the landing PR of each).
Everything below is genuinely still open.

**Cross-cutting — mobile reachability** (finding #8, not started): promote `/agentic` out of the
bottom of the "More" sheet — reorder `NAV_ITEMS` and/or bump `MOBILE_PRIMARY_COUNT` (currently 3)
or evict a lower-priority primary tab. Needs an explicit operator decision on which tab to evict,
if any.

### Candidate Phase 4 — same bug class, found elsewhere (fixed — [#372](https://github.com/kevinmarko/Stockpy/pull/372))

A backstop audit run alongside the Phase 2 build (verifying #365/#367 before they landed) swept the
rest of the webapp for the SAME two bug classes finding #5 fixes here — a fetched field that's never
rendered — and found five more instances, all outside the Agentic Trading tab and out of scope for
this doc's phasing. All five fixed in #372 (2026-07-19):

- `Portfolio.tsx:62` — renders `fetched_at` but never `is_stale`/`age_hours` (the type's own comment
  calls these out as the dedicated freshness fields).
- `RecommendedStocks.tsx:37,55` (shared by Dashboard/DataExplorer/Comparison) — never renders
  `RecommendationsResponse.as_of`.
- `ExecutionQueueSection.tsx:22,49` (shared by Commands.tsx and this tab) — surfaces the boolean
  `stale` chip but never the actual `generated_at`/`age_seconds`.
- `Observability.tsx:63-100` (`RegimeBadgeRow`) — never renders `regime.as_of`.
- `Settings.tsx:559-591` (`ErrorsSubsection`) and `Commands.tsx` — never render
  `DeadLetterReport.generated_at` / `CommandManifest.generated_at` respectively.

The audit also confirmed `ScanConfigModal`'s "Add scan config" was already honest (states "nothing
runs automatically" — no action-overclaim bug there) and found no other misleading "Run/Execute"
affordance elsewhere in the app. Not scoped into a phase yet — listed here so it isn't lost.
