# Known issue (2026-08-22): `OPTIONS_0DTE_ENABLED` missing from the automated options-lifecycle outer gate; automated options lifecycle entirely unwired on the daemon path

**Status: partially fixed.** Branch `fix-options-0dte-gate-missing`. Bug 1
(the `main.py` outer-gate omission) is fixed with a regression test. Bug 2
(the daemon-path gap) is **documented, not fixed** — see "What's still open"
below for why and what a real fix would need.

## Bug 1 (fixed): `main.py`'s outer gate omitted `OPTIONS_0DTE_ENABLED`

`main.py`'s `_run_cycle()` — the closure `main()` calls every cycle, i.e. the
code path both `main.py --interval N` and `main.py --agent` actually execute,
the DEFAULT production backend since `settings.ORCHESTRATOR_DAEMON_ENABLED`
defaults `False` — gated its entire "Automated Strategy Options Paper
Execution & Lifecycle" block on:

```python
if (
    getattr(settings, "PAPER_OPTIONS_AUTO_EXECUTE_ENABLED", False)
    or getattr(settings, "OPTIONS_AUTO_EXIT_ENABLED", False)
    or getattr(settings, "OPTIONS_DELTA_HEDGE_ENABLED", False)
):
```

`settings.OPTIONS_0DTE_ENABLED` was checked correctly on step 1b's *inner*
condition (`if getattr(settings, "OPTIONS_0DTE_ENABLED", False) or
getattr(settings, "OPTIONS_AUTO_EXIT_ENABLED", False):`) but was absent from
this *outer* one. `OPTIONS_0DTE_ENABLED`'s own `settings.py` docstring
describes it as a self-contained feature ("Enable automated 0DTE options
momentum breakout trading and lifecycle management") an operator would
reasonably enable on its own, without also wanting
`PAPER_OPTIONS_AUTO_EXECUTE_ENABLED`/`OPTIONS_AUTO_EXIT_ENABLED`/
`OPTIONS_DELTA_HEDGE_ENABLED`. In that entirely plausible configuration
(0DTE on, the other three off), the outer `if` evaluated `False`, the whole
block was skipped, and `manage_0dte_exits()` never ran — open 0DTE positions
got no automatic +75% profit target, -30% stop loss, or 15:45 ET hard exit.
They simply sat open with no automatic risk management, silently.

This is distinct from (and narrower than) `pilots/zero_dte_engine.py`'s own
already-honest docstring disclosure that no true time-of-day scheduler exists
anywhere in this codebase (a flat-interval poll substitutes for one — an
accepted, disclosed tradeoff). This bug meant the poll didn't even run at all
in the `OPTIONS_0DTE_ENABLED`-only configuration, which was not disclosed
anywhere.

No test exercised this exact flag combination before the fix — confirmed by
searching the full `tests/` tree for every settings flag this gate reads; the
three files that reference these flags (`tests/test_options_hedging.py`,
`tests/test_investyo_mcp_options_analytics.py`, `tests/test_zero_dte_engine.py`)
never touch `main.py`'s wiring of them at all.

### The fix

`main.py`'s inline block (formerly ~60 lines inside the `_run_cycle()`
closure) was extracted into a module-level `_run_automated_options_lifecycle
(macro_dto=None)`, mirroring the existing `_run_automated_delta_hedge_cycle`
extraction pattern (`main.py`, done for the SPY-spot-fabrication fix — see
`docs/known_issues/options_risk_fabricated_spy_spot.md`) for the identical
reason: independent testability without mocking the whole CLI/pipeline. The
outer gate now also OR's in `OPTIONS_0DTE_ENABLED`. Every inner step's own
gate/logic/logging is unchanged.

### Tests

`tests/test_main.py` gained a dedicated test class:
`test_options_lifecycle_runs_0dte_exits_when_only_0dte_flag_enabled` is the
key regression test (only `OPTIONS_0DTE_ENABLED=True` -> `manage_0dte_exits`
is genuinely invoked); a sibling test pins the "all four flags `False` ->
nothing runs" side of the gate; three more pin that each of the other three
flags still independently triggers its own step (so a future edit can't
silently drop one of them while fixing another); a final test confirms the
whole function still swallows an internal exception into a WARNING log
(unchanged non-fatal contract). Manually confirmed the key test fails against
the pre-fix gate condition (reintroducing the missing `OR
getattr(settings, "OPTIONS_0DTE_ENABLED", False)` clause makes it fail) before
committing the fix.

## Bug 2 (documented, not fixed): the automated options lifecycle has no daemon-path equivalent

`execution/options_paper_executor.py::OptionsPaperExecutor`'s exit management
(`execute_auto_exits` — the 50%/2x/21-DTE logic), `execute_strategy_directives`
(new-position auto-execution), and `main.py`'s delta-hedging cycle
(`_run_automated_delta_hedge_cycle`) are **only ever called from `main.py`'s
`_run_cycle()`**. `main_orchestrator.py`/`desktop/daemon_runtime.py` (the
persistent-daemon backend, `settings.ORCHESTRATOR_DAEMON_ENABLED` — off by
default today, but the documented future cutover direction for this codebase
per CLAUDE.md's "Persistent orchestrator daemon" section) call
`main_orchestrator._main_body(...)` for a pipeline cycle and have **zero**
references to `OptionsPaperExecutor` (confirmed via `grep -rn
"OptionsPaperExecutor" desktop/ main_orchestrator.py` — no hits).

The **one** exception is 0DTE: `desktop/daemon_runtime.py::_timer_loop`
already calls `pilots.zero_dte_engine.manage_0dte_exits()` directly on every
interval wake (gated on `settings.OPTIONS_0DTE_ENABLED`, requiring a
non-default `ORCHESTRATOR_INTERVAL_SECONDS > 0`) — this is deliberate,
already-correct, existing wiring, independent of the `main.py` bug fixed
above (it calls `manage_0dte_exits()` directly, not through
`_run_automated_options_lifecycle`). `_run_one_cycle`'s own comment
(`desktop/daemon_runtime.py`, near the `PipelineFatalError` handler)
already explains *why* 0DTE isn't re-run there: `_timer_loop` already fires
it more frequently than once per full pipeline cycle.

**So today**: if an installation flips `ORCHESTRATOR_DAEMON_ENABLED=True`,
0DTE exit management keeps working via the daemon's own separate wiring, but
**exit management (50%/2x/21-DTE), new-position strategy auto-execution, and
delta hedging would all silently stop running** — no error, no log, just an
operator-configured automated behavior that used to run every cycle under
`main.py` quietly not running at all under the daemon. This was not
disclosed anywhere prior to this doc.

### Why this isn't fixed in the same PR

The obvious-looking fix — have `desktop/daemon_runtime.py` import and call
`main._run_automated_options_lifecycle`/`main._run_automated_delta_hedge_cycle`
— does not work cleanly: `main.py` has a module-top venv re-exec guard
(auto-routes to `.venv`'s interpreter if not already running inside it, the
very first executable code in the file, before any function definitions)
that runs unconditionally on **any** import of the module, including one
made purely to reach a function defined further down. Importing `main` from
`desktop/daemon_runtime.py` risks unpredictable behavior in a long-lived
daemon process, and would also blur the deliberate architectural separation
between `main.py` ("clean advisory orchestrator") and
`main_orchestrator.py`/`desktop/` (the async, broker-abstracted daemon
backend) that `docs/architecture/orchestration-entrypoints.md` documents.

A real fix needs the shared logic (exit management, strategy
auto-execution, delta hedging) relocated to a module both `main.py` and
`desktop/daemon_runtime.py` can import without side effects — e.g. a new
`execution/options_lifecycle.py` housing what is now
`_run_automated_options_lifecycle`/`_run_automated_delta_hedge_cycle`, with
`main.py` re-exporting/calling it for backward compatibility. That also
requires deciding:

- **Cadence**: should this run on every full daemon pipeline cycle (tied to
  `_run_one_cycle`, i.e. `DATA_FRESHNESS_TTL_SECONDS`-gated) or on every
  timer wake like 0DTE (more frequent, gated only on
  `ORCHESTRATOR_INTERVAL_SECONDS`)? The three behaviors have different
  urgency profiles (a delta-hedge deadband check is cheap and arguably wants
  the 0DTE cadence; strategy auto-execution scans directives that are
  themselves only refreshed once per full cycle, so it likely wants the
  slower cadence) — this is a real design decision, not a mechanical port.
- **`macro_dto` availability**: `execute_strategy_directives(macro_dto=...)`
  needs the cycle's real `MacroEconomicDTO` to evaluate the VIX/CREDIT-EVENT
  premium-selling regime gate (see the comment at the call site) — silently
  no-op'ing that gate by calling without a `macro_dto` would be a **new**,
  worse regression than not wiring it at all. `main_orchestrator._main_body`
  does not currently expose the cycle's `RunContext`/macro DTO back to its
  caller (`desktop/daemon_runtime.py::_run_one_cycle`), so threading a real
  value through requires either widening `_main_body`'s return contract or
  independently resolving macro context in the daemon path (duplicating,
  not reusing, `main.py::_build_macro_dto()`'s logic) — neither is a small
  change.
- Delta hedging and exit management do **not** need `macro_dto`, so those
  two are the lower-risk half of a future fix; strategy auto-execution is
  the one that genuinely needs the design decision above.

Given the scope and risk of getting cadence and `macro_dto` threading right
in a heavily-tested, safety-critical piece of code (`desktop/daemon_runtime.py`
has extensive existing coverage in `tests/test_daemon_runtime.py`,
`tests/test_orchestrator_daemon.py`), and that the daemon backend is off by
default today (`ORCHESTRATOR_DAEMON_ENABLED=False`, so this gap has zero
live impact on any installation that hasn't explicitly opted into the
daemon), this was judged better scoped as its own dedicated task/PR with a
proper implementation plan, rather than folded into this bug-fix PR. This
doc is the loud, explicit disclosure CLAUDE.md's own workflow requires for
exactly this situation.

### What's still open

- No code fix for Bug 2 in this PR. See "Why this isn't fixed in the same
  PR" above for the recommended follow-up shape (a shared
  `execution/options_lifecycle.py`-style module, a cadence decision, and a
  `macro_dto`-threading decision for the daemon path specifically).
- `desktop/daemon_runtime.py`'s existing comments near `_run_one_cycle` and
  `_timer_loop`'s 0DTE call were extended to state this gap plainly
  (previously they only explained why 0DTE *specifically* isn't re-run per
  full cycle, which reads as "everything else already has an equivalent" by
  omission).
- `docs/architecture/execution.md`'s `execution/options_paper_executor.py`
  bullet was extended with a pointer to this doc.
- No test added for Bug 2 — there is no code change to regression-test yet;
  the follow-up task should add daemon-path coverage alongside its fix.

## Tests

`tests/test_main.py`: `test_options_lifecycle_runs_0dte_exits_when_only_0dte_flag_enabled`,
`test_options_lifecycle_skips_everything_when_all_flags_disabled`,
`test_options_lifecycle_runs_exit_management_when_only_auto_exit_flag_enabled`,
`test_options_lifecycle_runs_strategy_auto_execute_when_only_that_flag_enabled`,
`test_options_lifecycle_runs_delta_hedge_when_only_that_flag_enabled`,
`test_options_lifecycle_swallows_exceptions_and_logs_warning`.
