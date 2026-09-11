# Decouple Explore From Execute — Wave 0 Dependency-Check Checklist

> Scaffold artifact for `.claude/decouple-explore-execute_implementation_plan.md`
> §3. Produced by direct, live tracing against this branch's checkout
> (`decouple-explore-execute`, cut from `main` @ `68fc893a`) on 2026-09-11 —
> every claim below is cited to a `file:line`, not assumed from the plan's own
> prose (which itself is doc-level, per that plan's own AGENT HANDOFF NOTES).
> Downstream work packages (A/B/C/D/E) must treat this as ground truth to
> build from, but MUST spot-check any claim they rely on against the cited
> line before asserting it more strongly in their own output.

## 1. The daemon's autonomous per-cycle universe — traced end to end

Two independent orchestrator entry points exist, and both now resolve
through the **same** shared function, confirmed live:

- **`main.py::_build_universe()`** ([main.py:343-421](../../main.py#L343)):
  `held` (Robinhood snapshot positions) ∪ `watchlist`
  (`_load_watchlist()` → `data.portfolio_sync.load_env_watchlist()`,
  [main.py:284-286](../../main.py#L284)) ∪ `discovered` (scan candidates,
  see §2 below) ∪ `settings.DEFAULT_TICKERS` (fallback-if-empty) ∪
  Sheet2 (last-resort fallback, `main.py`-only) ∪ recently-closed retention
  (unioned last). All of held/watchlist/discovered/default_tickers/rating-
  exclusion live in **`data.portfolio_sync.compute_tracked_universe()`**
  ([data/portfolio_sync.py:682-741](../../data/portfolio_sync.py#L682)).
- **`pipeline/production_steps.py::AsyncDataFetchStep.run()`** (the
  persistent daemon's per-cycle universe builder,
  [pipeline/production_steps.py:79-125](../../pipeline/production_steps.py#L79)):
  calls the **identical** `compute_tracked_universe()` with
  `watchlist=load_env_watchlist(ctx.watchlist_file)` +
  `discovered=<scan candidates>`, then separately unions in held positions.

**Finding, confirmed live:** both entry points genuinely share one function
(`compute_tracked_universe`) for the union/rating-exclusion/fallback logic —
this is not a doc-level claim, it is the same Python object imported at both
call sites. `compute_tracked_universe()`'s own inputs are exactly 4 named
parameters (`held`, `watchlist`, `discovered`, `default_tickers`) — no
reference to the Symbol Screener, FMP search, or any request-scoped
Quick-Trade state exists anywhere in its body
([data/portfolio_sync.py:682-741](../../data/portfolio_sync.py#L682)).

## 2. Where "discovered" scan candidates actually come from

`discovered` in both entry points is sourced from **`pilots.discovery.discovery()`**
([pilots/discovery.py:100+](../../pilots/discovery.py#L100)), which reads
`output/scan_candidates.json` — an artifact **this repo's own pipeline never
writes**. Per that module's own docstring
([pilots/discovery.py:1-33](../../pilots/discovery.py#L1)), the file is
populated exclusively by the `.claude/skills/agentic-discovery/SKILL.md`
skill, which runs the **operator's own configured Robinhood broker scans**
(`pilots.scan_config_store.ScanConfigStore`) through the Robinhood MCP,
cross-references hits against `engine.advisory.evaluate()`, and writes the
file. Confirmed: this is a human-operator-initiated, out-of-band action
(a Claude Code skill run), structurally unreachable from the FMP-backed
Symbol Screener (`data/fmp_screener.py`, `GET /data/screener`) or from
Quick Trade — those two features call entirely different endpoints
(`GET /data/symbol-search`, `GET /data/screener`, `GET /data/quotes`) that
never write `scan_candidates.json` or any other file `compute_tracked_universe()`
reads.

**Conclusion for §0 item 1:** confirmed live — a symbol reached only by
browsing the Symbol Screener or opening Quick Trade cannot enter either
orchestrator's autonomous universe without a human taking one of three
explicit actions: (a) adding it to `WATCHLIST`/`watchlist.txt`, (b) it being
already held, or (c) it surfacing from an operator-configured Robinhood
broker scan via the agentic-discovery skill.

## 3. The options auto-scan's operator-supplied symbol-list override — traced, with a correction to the plan's own framing

`execution/options_paper_executor.py::OptionsPaperExecutor.get_actionable_directives()`
([execution/options_paper_executor.py:182-234](../../execution/options_paper_executor.py#L182))
does accept an optional `symbols` override, used when the operator supplies
one. **But the plan's assumption that the default (no-override) path also
reaches the full tracked universe is WRONG — corrected here:**

- **Manual, on-demand path** (`POST /pilots/paper-broker/strategy-options/execute`,
  [api/pilots_api.py:6161-6172](../../api/pilots_api.py#L6161)): `symbols`
  comes **only** from the request body
  (`StrategyOptionsExecutionRequest.symbols`,
  [api/pilots_api.py:5945-5946](../../api/pilots_api.py#L5945)) — genuinely
  per-request, never persisted, exactly as the plan states. Gated by
  `Depends(require_command_token)` + `Depends(require_paper_broker_writes_enabled)`
  → **`settings.PAPER_BROKER_WRITES_ENABLED`**
  ([api/pilots_api.py:546-551](../../api/pilots_api.py#L546)) — **not**
  `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED` as the plan's §0 item 2 assumed. That
  flag gates a different path (below).
- **Fully-automated daemon-cycle path**
  (`execution/options_lifecycle.py::run_automated_options_lifecycle()`,
  called from both `main.py`'s cycle and
  `desktop/daemon_runtime.py::OrchestratorDaemon.trigger_run()`
  [desktop/daemon_runtime.py:570-571](../../desktop/daemon_runtime.py#L570)):
  gated by **`settings.PAPER_OPTIONS_AUTO_EXECUTE_ENABLED`**
  ([execution/options_lifecycle.py:161](../../execution/options_lifecycle.py#L161)).
  This path calls `executor.execute_strategy_directives(macro_dto=macro_dto)`
  with **no `symbols`/`directives` override at all**
  ([execution/options_lifecycle.py:165](../../execution/options_lifecycle.py#L165)).
  Tracing `execute_strategy_directives()`
  ([execution/options_paper_executor.py:288-327](../../execution/options_paper_executor.py#L288)):
  when `directives is None` it calls `self.get_actionable_directives(macro_dto=macro_dto, vrp=vrp)`
  — **no `symbols` and no `run_result` kwarg** — so inside
  `get_actionable_directives()` ([execution/options_paper_executor.py:192-201](../../execution/options_paper_executor.py#L192)):
  `symbols` starts `None`, `run_result` is `None` → `symbols = []` →
  falls through to the **raw-parse fallback**:
  `raw = getattr(settings, "WATCHLIST", "") or ""` then a bare
  `raw.split(",")`.

**New, previously-undocumented finding (confirmed live, not in any existing
doc grepped this session):** the automated daemon-cycle options scan's
*default* universe is **narrower than, and structurally different from,**
`compute_tracked_universe()`'s. It reads only the raw `WATCHLIST` env var —
it does **not** read `watchlist.txt` (unlike `load_env_watchlist()`,
[data/portfolio_sync.py:618-643](../../data/portfolio_sync.py#L618), which
reads both), does **not** include held positions, does **not** include
`DEFAULT_TICKERS`, does **not** include discovered scan candidates, and
applies no plausible-ticker validation. If `WATCHLIST` is empty but
`watchlist.txt` or held positions are populated, the automated options
scan silently finds **zero** symbols that cycle — a real, live-confirmed
gap, distinct from (and narrower than) the "operator-supplied override"
exception the plan focuses on. It is still bounded to an
operator-configured setting (`WATCHLIST`), so it does **not** violate the
explore/execute boundary this plan cares about (nothing a human merely
browsed can reach it) — but WP-B/D's boundary doc must state this
precisely, not repeat the plan's original (incorrect) assumption that the
default path shares `compute_tracked_universe()`'s full breadth.
`_resolve_symbols(run_result)` ([execution/options_queue_builder.py:394-407](../../execution/options_queue_builder.py#L394))
is dead code in production — grepped every non-test call site of
`execute_strategy_directives`/`get_actionable_directives` repo-wide; none
ever pass `run_result`.

## 4. Other automated-execution surfaces — grep confirms none beyond the two above (plus the 0DTE exit sibling)

`desktop/daemon_runtime.py::_timer_loop()` ([desktop/daemon_runtime.py:1341+](../../desktop/daemon_runtime.py#L1341))
is the daemon's one scheduled entry point. Per-wake it calls, in addition to
`trigger_run()` (which runs the full pipeline incl. §1/§3 above):
`manage_0dte_exits()` directly ([desktop/daemon_runtime.py:1413-1414](../../desktop/daemon_runtime.py#L1413))
— an **exit-management** action on already-open positions (a symbol already
in the book), not a new-symbol-discovery surface, so it's out of this
plan's scope by construction (nothing to decouple — it can't introduce a
new symbol). `maybe_update_circuit_breaker`, `maybe_refresh_settings`,
`maybe_refresh_google_trends`, `maybe_alert_on_pipeline_stall`,
`maybe_dispatch_weekly_digest` (new, 2026-09) were also read — none touch a
symbol universe or submit orders. A repo-wide grep for every non-test
importer of `execution/*` (68 files) was cross-checked against this list;
every production (non-test) importer resolves to one of: the two
orchestrators (§1), the options lifecycle (§3), `api/control_api.py`/
`api/pilots_api.py` (request-scoped, human-initiated HTTP handlers — not
scheduled), `broker_live_execution_mcp.py` (a Claude-session-only stdio MCP
tool, not a running-process scheduler), `data/paper_account_store.py`,
`pairs/simulation.py` and `validation/*` (backtest/validation code paths,
not live scheduling), and `pipeline/steps.py` (the legacy synchronous
pipeline runner, same universe-resolution contract as §1). No third
automated-execution surface was found.

## 5. Existing test coverage of this boundary

`tests/test_production_steps_universe.py` (`TestDaemonUniverseReadsWatchlist`,
`TestDaemonUniverseWatchlistSurvivesAlongsideDiscovery`,
`TestDaemonUniverseUnionsAllSources`) proves **inclusion**: that watchlist/
discovery/default-tickers sources are correctly unioned in. **No existing
test proves the inverse** — that a symbol present ONLY in an FMP-screener
result, a Quick-Trade session, or any other non-listed source is excluded.
WP-A's new structural test must prove the exclusion side, which is
genuinely new coverage, not a duplicate of the above.

## 6. Universe Transparency / Explain This Ticker — confirmed landed, live

Both have landed on `main` and are present in this checkout:
`webapp/src/screens/UniverseTransparency.tsx`,
`webapp/src/components/ExplainTickerDrawer.tsx`,
`webapp/src/components/ExplainTickerButton.tsx` (confirmed via `ls`, not
doc prose). `ExplainTickerDrawer.tsx` already renders an untracked-symbol
message — `"Not currently tracked in portfolio or watchlists."`
([webapp/src/components/ExplainTickerDrawer.tsx:609](../../webapp/src/components/ExplainTickerDrawer.tsx#L609)) —
the natural anchor for WP-C's legibility sentence, per the plan's own
instruction to reuse rather than build new. `PaperBroker.tsx`'s "Quick
Trade — Any Symbol" header is at
[webapp/src/screens/PaperBroker.tsx:743](../../webapp/src/screens/PaperBroker.tsx#L743)
and its "⚡ Automated Strategy Options Execution" header at
[webapp/src/screens/PaperBroker.tsx:1241](../../webapp/src/screens/PaperBroker.tsx#L1241).
`SymbolScreener.tsx`'s hand-off copy ("straight to Paper Broker's Quick
Trade, or a whole selection to its Strategy Scan") sits at
[webapp/src/screens/SymbolScreener.tsx:174](../../webapp/src/screens/SymbolScreener.tsx#L174),
adjacent to the `navigate(...quickTradeSymbol=...)` /
`navigate(...scanSymbols=...)` calls at lines 160/165.

## 7. Explicit non-findings (do not overclaim these)

- This trace did **not** re-verify `ADVISORY_ONLY`/live-broker behavior —
  out of scope per the plan's own Constraint #1 statement, and this repo's
  live-order path was not touched or read for this checklist.
- This trace did **not** exhaustively re-audit `pipeline/steps.py` (the
  legacy synchronous runner) line-by-line — it was confirmed to import
  `data.portfolio_sync`/`execution.*` the same way, but WP-A's test should
  target the two now-canonical entry points (`main.py`, `AsyncDataFetchStep`)
  rather than assume `pipeline/steps.py` is still live-reachable in
  production without a direct check.
