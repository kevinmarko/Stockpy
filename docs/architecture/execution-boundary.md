# Architecture: The Explore/Execute Boundary

> Part of the split-out `CLAUDE.md` System Architecture reference. See [../../CLAUDE.md](../../CLAUDE.md) for the index.

**Produced**: 2026-09-11, branch `decouple-explore-execute` (cut from `main` @
`68fc893a`). This document is the write-up half of that plan's Work Package B
("Boundary documentation"); Work Package D's policy statement (§4 below) is
merged into it per the plan's own instruction that both land in the same
document.

## Confidence tiers used in this document

Every factual claim below is labeled by how it was established. None of the
tiers below involved actually starting the orchestrator daemon, the Robinhood
MCP, or a live API server — "live" in this document means *the current source
checkout*, not a running system:

- **Traced against live code** — the cited `file:line` was read directly
  against this checkout (either in this session, or in
  `.claude/decouple-explore-execute_wave0_checklist.md`, itself produced this
  session by the same method — see that file's own header). This is this
  document's strongest tier: it means the described behavior is what the code
  that actually runs does, not what a docstring or a plan claims it does.
- **Newly verified this session** — a claim not already present in the Wave 0
  checklist, checked directly against this checkout before being stated here
  (e.g. a `settings.py` default value).
- **Not verified / explicitly out of scope** — stated plainly as such, never
  presented as confirmed. See §7.

## 1. Autopilot's autonomous per-cycle universe

**[Traced against live code.]** Two independent orchestrator entry points
exist in this codebase, and both resolve their per-cycle symbol universe
through the same shared function:

- **`main.py::_build_universe()`** (`main.py:343-421`) — the advisory
  orchestrator's (`main.py --interval`/`--agent`) universe builder.
- **`pipeline/production_steps.py::AsyncDataFetchStep.run()`**
  (`pipeline/production_steps.py:79-125`) — the universe builder the
  **persistent orchestrator daemon** (`main_orchestrator.py` /
  `desktop/daemon_runtime.py`, the backend the Pilots PWA talks to) executes
  every cycle.

Both call **`data.portfolio_sync.compute_tracked_universe()`**
(`data/portfolio_sync.py:682-741`) — the literal same Python function object,
imported at both call sites, not two implementations that happen to agree.
Its signature takes exactly four named parameters:

```python
def compute_tracked_universe(
    *,
    held: Iterable[str] = (),
    watchlist: Iterable[str] = (),
    discovered: Iterable[str] = (),
    default_tickers: Iterable[str] = (),
    apply_rating_exclusion: bool = True,
) -> List[str]:
```

It computes `held ∪ watchlist ∪ discovered`, optionally subtracts symbols
excluded by `settings.SYMBOL_RATING_AUTO_DROP_ENABLED` (held symbols are
never dropped; the exclusion lookup fails open — CONSTRAINT #6), and falls
back to `default_tickers` **only when that whole union is empty**. There is
no fifth input, no keyword for a request-scoped symbol list, and no
reference anywhere in its body to the Symbol Screener, FMP search, or any
Quick-Trade session state.

`watchlist` for both entry points comes from **`load_env_watchlist()`**
(`data/portfolio_sync.py:618-643`) — a shared reader of the `WATCHLIST` env
var and a plain-text watchlist file (`main.py` passes `watchlist.txt`; the
daemon path passes `ctx.watchlist_file`, wired to the same filename by
`main_orchestrator.py`'s `RunContext(...)` construction). `main.py` layers
its own Robinhood-snapshot `held` set, its own `discovery()` call (§2 below),
and a Google-Sheet last-resort fallback tier (`main.py`-only) on top of
`compute_tracked_universe()`'s result; the daemon path layers held positions
on afterward the same way. Neither path can add a symbol to this universe
except through `held`, `watchlist`, `discovered`, or `default_tickers` — the
four parameters above, nothing else.

**What this means for the explore/execute boundary**: a symbol that exists
only because an operator opened the Symbol Screener (`data/fmp_screener.py`,
`GET /data/screener`) or the Paper Broker's Quick Trade panel cannot reach
either orchestrator's autonomous universe. Those two features call
`GET /data/symbol-search`, `GET /data/screener`, and `GET /data/quotes` —
none of which write to any store `compute_tracked_universe()`, `held`,
`watchlist`, or `discovered` ever reads.

## 2. Where `discovered` scan candidates actually come from

**[Traced against live code.]** `discovered` in both entry points is sourced
from **`pilots.discovery.discovery()`** (`pilots/discovery.py:100+`), which
reads `output/scan_candidates.json`. Per that module's own docstring
(`pilots/discovery.py:1-33`), this file is **never written by this repo's own
pipeline** — it is populated exclusively by a human running the
`.claude/skills/agentic-discovery/SKILL.md` skill, which executes the
operator's own configured Robinhood broker scans
(`pilots.scan_config_store.ScanConfigStore`) through the Robinhood MCP,
cross-references each hit against `engine.advisory.evaluate()`, and writes
the file. `discovery()` also filters the result to `BUY`/`None` actions only
(a `SELL`/`HOLD` cross-reference is kept in the file for audit but is not a
buy candidate for a symbol the operator doesn't hold).

This is a structurally distinct surface from the FMP-backed Symbol Screener
and Quick Trade: it requires a human to actually run a Claude Code skill
against their own broker account, out of band from anything a browser session
can trigger. A symbol found by browsing the Symbol Screener or opening Quick
Trade has no path into `output/scan_candidates.json`.

**Conclusion**: confirmed against live code — a symbol reached only by
browsing the Symbol Screener or opening Quick Trade cannot enter either
orchestrator's autonomous universe without a human taking one of three
explicit actions: (a) adding it to `WATCHLIST` / `watchlist.txt`, (b) it
already being held, or (c) it surfacing from an operator-run Robinhood broker
scan via the agentic-discovery skill.

## 3. Other automated-execution surfaces

**[Traced against live code.]** `desktop/daemon_runtime.py::_timer_loop()`
(`desktop/daemon_runtime.py:1341+`) is the daemon's one scheduled entry
point. Per wake it calls `trigger_run()` (the full pipeline, covered by §1
above) plus `manage_0dte_exits()` directly — an **exit-management** action on
already-open positions, not a new-symbol-discovery surface, so it cannot
introduce a symbol and is out of this boundary's scope by construction.
`maybe_update_circuit_breaker`, `maybe_refresh_settings`,
`maybe_refresh_google_trends`, `maybe_alert_on_pipeline_stall`, and
`maybe_dispatch_weekly_digest` were also checked — none touch a symbol
universe or submit orders.

A repo-wide grep of every non-test importer of `execution/*` (68 files) was
cross-checked against this list; every production importer resolves to one
of: the two orchestrators (§1), the options auto-scan (§4-§5 below),
`api/control_api.py`/`api/pilots_api.py` (request-scoped, human-initiated
HTTP handlers — not scheduled), `broker_live_execution_mcp.py` (a
Claude-session-only stdio MCP tool, not a running-process scheduler),
`data/paper_account_store.py`, `pairs/simulation.py` and `validation/*`
(backtest/validation code paths, not live scheduling), and
`pipeline/steps.py` (the legacy synchronous pipeline runner, same
universe-resolution contract as §1, not re-audited line-by-line — see §7).
No third automated-execution surface exists.

## 4. The options auto-scan's operator-supplied symbol-list override — policy statement

**[Traced against live code.]** `execution/options_paper_executor.py::OptionsPaperExecutor.get_actionable_directives()`
(`execution/options_paper_executor.py:182-234`) accepts an optional `symbols`
parameter. When an operator supplies one, it is used verbatim in place of the
tracked universe for that one call — this is a real, already-shipped
capability, not a proposal.

**Call path, traced end to end:**

- `POST /pilots/paper-broker/strategy-options/execute`
  (`api/pilots_api.py:6161-6172`) accepts a JSON body,
  `StrategyOptionsExecutionRequest` (`api/pilots_api.py:5945-5946`):
  ```python
  class StrategyOptionsExecutionRequest(BaseModel):
      symbols: Optional[List[str]] = None
      dry_run: bool = False
      max_notional: Optional[float] = None
  ```
  `symbols` comes **only** from this one request body — it is read once, per
  request, and passed straight through to `execute_strategy_options(symbols=symbols, ...)`.
  Nothing persists it anywhere; the next call (with no `symbols` field) gets
  the default path in §5 below, not a remembered override.
- **Gating**: `dependencies=[Depends(require_command_token), Depends(require_paper_broker_writes_enabled)]`
  on the route itself. `require_command_token` reads `settings.FOLLOW_API_TOKEN`
  live per request and fails closed (403) when unset (`api/pilots_api.py`'s
  own module docstring, lines 58-62). `require_paper_broker_writes_enabled`
  (`api/pilots_api.py:546-551`) checks `settings.PAPER_BROKER_WRITES_ENABLED`
  and 403s when it is `False`.
  **Correction to this plan's own §0 assumption**: the gating flag is
  `PAPER_BROKER_WRITES_ENABLED` — **not** `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED`,
  which gates a different, unrelated path entirely (§5 below). This was
  independently re-verified this session by reading `api/pilots_api.py:546-551`
  and its route decorator directly, not merely by trusting the plan's or
  the Wave 0 checklist's prose.
- **[Newly verified this session]** `settings.PAPER_BROKER_WRITES_ENABLED`
  defaults `True` (`settings.py:259-277`); its own field description
  explicitly lists `/pilots/paper-broker/strategy-options/execute` among the
  endpoints it gates. `settings.PAPER_OPTIONS_AUTO_EXECUTE_ENABLED` defaults
  `False` (`settings.py:278-281`) and gates the fully-automated path in §5,
  not this one.
- Every trade this endpoint places lands in `PaperAccountStore` via
  `FMPPaperBroker` — the platform's simulated paper-trading engine. Nothing
  in this call path reaches a live broker; `ADVISORY_ONLY`/live-order
  behavior is untouched by this feature and out of scope for this document
  (see §7).

**Policy statement (Work Package D):** this is a deliberate, bounded,
already-shipped exception to the tracked-universe boundary described in §1,
and it is being documented here as a confirmed design decision — not
downplayed as "not really execution," and not flagged as something that
should be closed. Three properties make it acceptable:

1. **It requires an explicit human action per call.** The `symbols` field is
   read fresh from the request body every time; there is no settings flag,
   config file, or persisted state that lets one human action arm this for
   future automated cycles. A human types or selects the symbol list and
   sends the request; that is the entirety of how a symbol gets in.
2. **It never writes to any universe-affecting store.** Calling this
   endpoint with a `symbols` override does not touch `WATCHLIST`,
   `watchlist.txt`, `DEFAULT_TICKERS`, `output/scan_candidates.json`, or any
   other input `compute_tracked_universe()` reads. The override's effect is
   scoped to the one paper-trading scan it triggers and ends when that HTTP
   response is returned.
3. **It is orthogonal to whether Autopilot's own autonomous cycle ever
   reaches those symbols — and it does not, and structurally cannot.** The
   fully-automated version of this same scan (§5) never receives a `symbols`
   override at all; the two code paths are independent, and a human calling
   this endpoint with an override has no effect on what the unattended daemon
   cycle does on its own.

This mirrors the same reasoning `CLAUDE.md` already applies to Quick Trade's
own "any-symbol" capability: a human-initiated, paper-only, per-request
action reaching outside the tracked universe is a feature, not a boundary
violation, precisely because Autopilot's *unattended* behavior is what the
boundary is meant to constrain — not everything a human can manually ask the
platform to simulate.

## 5. New finding: the fully-automated options auto-scan's default universe is narrower than `compute_tracked_universe()`'s

**[Traced against live code — newly documented this session; not previously
written down anywhere this session's grep of existing docs found.]**

The fully-automated (no-override) path is a *different* call chain from §4,
and does **not** inherit `compute_tracked_universe()`'s breadth. Traced end
to end:

- **`execution/options_lifecycle.py::run_automated_options_lifecycle()`**
  — called from both `main.py`'s cycle and
  `desktop/daemon_runtime.py::OrchestratorDaemon.trigger_run()`
  (`desktop/daemon_runtime.py:570-571`) — gates the whole step on
  `settings.PAPER_OPTIONS_AUTO_EXECUTE_ENABLED` (`execution/options_lifecycle.py:161`,
  default `False`).
- When enabled, it calls `executor.execute_strategy_directives(macro_dto=macro_dto)`
  (`execution/options_lifecycle.py:165`) with **no `symbols` and no
  `directives` argument**.
- `execute_strategy_directives()` (`execution/options_paper_executor.py:288-327`):
  when `directives is None`, it calls
  `self.get_actionable_directives(macro_dto=macro_dto, vrp=vrp)` — again with
  no `symbols` and no `run_result`.
- Inside `get_actionable_directives()` (`execution/options_paper_executor.py:192-201`):
  `symbols` starts `None`; `run_result` is also `None`, so `symbols = []`;
  the function then falls through to a raw-parse fallback:
  ```python
  if not symbols:
      raw = getattr(settings, "WATCHLIST", "") or ""
      symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
  ```

This reads **only the raw `WATCHLIST` environment variable**, split on
commas, with no plausible-ticker validation. It does **not** read
`watchlist.txt` (unlike `load_env_watchlist()`, which reads both sources and
merges them), does **not** include held positions, does **not** include
`settings.DEFAULT_TICKERS`, and does **not** include discovered scan
candidates. If `WATCHLIST` is empty while `watchlist.txt` or held positions
are populated, the fully-automated options auto-scan finds **zero** symbols
that cycle.

**This is a real, disclosed gap — distinguish it clearly from the §4
exception, and from a boundary leak:**

- It is **narrower than expected**, not wider: `compute_tracked_universe()`'s
  own inputs are a superset of what this path reads, so this gap can never
  cause an *untracked* symbol to be autonomously traded — it can only cause
  the automated scan to silently under-cover symbols the operator legitimately
  intended to track via `watchlist.txt`, held positions, `DEFAULT_TICKERS`,
  or discovery.
- It does **not** violate the explore/execute boundary this document
  otherwise describes: `WATCHLIST` is still an operator-configured setting,
  not something a browsed-but-untracked symbol can reach. Nothing a human
  merely looked at in the Symbol Screener or Quick Trade can enter this path
  either.
- `_resolve_symbols(run_result)` (`execution/options_queue_builder.py:394-407`)
  — the one other way `symbols` could be populated in this call chain — is
  dead code in production: every non-test call site of
  `execute_strategy_directives`/`get_actionable_directives` repo-wide was
  grepped, and none passes `run_result`.

See [`docs/known_issues/options_auto_scan_default_universe_gap.md`](../known_issues/options_auto_scan_default_universe_gap.md)
for the dedicated write-up. Per this plan's own explicit scope boundary,
fixing this gap (which would mean changing
`execution/options_paper_executor.py::get_actionable_directives()`'s
default-universe resolution) is **out of scope for this task** — it is
disclosed here, not patched.

## 6. Existing and companion test coverage

`tests/test_production_steps_universe.py` (`TestDaemonUniverseReadsWatchlist`,
`TestDaemonUniverseWatchlistSurvivesAlongsideDiscovery`,
`TestDaemonUniverseUnionsAllSources`) proves **inclusion** — that
watchlist/discovery/default-ticker sources are correctly unioned into the
daemon's universe. No existing test in this checkout proves the **inverse**:
that a symbol present only in an FMP-screener result, a Quick-Trade session,
or any other non-listed source is excluded from either orchestrator's
autonomous universe.

This plan's companion Work Package A closed exactly that gap:
`tests/test_execution_universe_boundary.py` (13 tests) — mirroring this
repo's established convention of turning a "we checked, it's fine" audit
conclusion into a mechanical guard (e.g. `tests/test_broker_fills_store.py`'s
import-boundary AST guard). **Confirmed by this document's own author,
independently, after that test file landed in this checkout**: `python3 -m
pytest tests/test_execution_universe_boundary.py -q` passes 13/13, and the
test file's own trailing module comment records a deliberate,
temporarily-introduced-then-reverted violation in both
`data/portfolio_sync.py` and `execution/options_paper_executor.py`, each
observed to fail this suite before being reverted — the break-then-revert
proof the plan's honesty checklist requires. It covers both the AST
import-boundary guard (§1's conclusion) and a pinned runtime test for §5's
default-scope finding below.

## 7. What this document does NOT claim

- This trace did **not** re-verify `ADVISORY_ONLY` or any live-broker
  behavior. That is out of scope per the plan's own Constraint #1 statement,
  and no live-order path was touched or read while producing this document.
- This trace did **not** exhaustively re-audit `pipeline/steps.py` (the
  legacy synchronous pipeline runner) line-by-line. It was confirmed to
  import `data.portfolio_sync`/`execution.*` the same way the two canonical
  entry points do, but this document's conclusions target `main.py` and
  `AsyncDataFetchStep` specifically, not an assumption that `pipeline/steps.py`
  is (or isn't) still live-reachable in production.
- This document does not itself re-derive the §6 structural test's pass/fail
  status from first principles — it relies on this document's author having
  independently run it after it landed (see §6) rather than re-proving the
  break-then-revert method a second time here.
- This document does not claim any code change was made to close the §5
  finding. That finding is disclosed, not fixed, by design (see §5's own
  scope note and the plan's explicit "Explicitly NOT in this task list").
- This document does not claim to have exercised a live daemon cycle, a live
  Robinhood MCP scan, or a live HTTP request against a running
  `api/pilots_api.py` process. Every claim above was established by reading
  the source that would run in those scenarios, not by running them.

## 8. Verification status

Every `file:line` citation in §1-§5 above was either:

1. Traced live (i.e., read directly against this checkout) in
   `.claude/decouple-explore-execute_wave0_checklist.md`, itself produced
   this session by the same direct-tracing method (see that file's own
   header note); or
2. Independently re-read this session while writing this document, as a
   spot-check on every load-bearing claim before restating it here more
   formally — specifically: `main.py:280-425`, `data/portfolio_sync.py:600-745`,
   `pipeline/production_steps.py:70-130`, `pilots/discovery.py:1-140`,
   `execution/options_paper_executor.py:175-330`,
   `execution/options_queue_builder.py:385-410`,
   `execution/options_lifecycle.py:150-190`, `desktop/daemon_runtime.py:560-580`,
   `api/pilots_api.py:540-555` and `:5930-6185`, and `settings.py:255-294`
   were all read in full this session, not merely quoted from the checklist.

§6's reference to `tests/test_execution_universe_boundary.py` was confirmed
by this document's author running `python3 -m pytest
tests/test_execution_universe_boundary.py -q` after the file landed
(13 passed), and by reading the file's own trailing break-then-revert proof
record — not merely trusted from the work package's own self-report.
