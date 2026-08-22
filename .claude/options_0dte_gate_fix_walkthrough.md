# Walkthrough: `OPTIONS_0DTE_ENABLED` outer-gate fix

## What was broken

`main.py`'s `_run_cycle()` — the closure `main()` calls on every
`main.py --interval N` / `main.py --agent` cycle, the DEFAULT production
backend since `ORCHESTRATOR_DAEMON_ENABLED` defaults `False` — gated its
entire "Automated Strategy Options Paper Execution & Lifecycle" block
(exit management, 0DTE fast exits, new-position strategy auto-execution,
delta hedging) on:

```python
if (
    getattr(settings, "PAPER_OPTIONS_AUTO_EXECUTE_ENABLED", False)
    or getattr(settings, "OPTIONS_AUTO_EXIT_ENABLED", False)
    or getattr(settings, "OPTIONS_DELTA_HEDGE_ENABLED", False)
):
```

`OPTIONS_0DTE_ENABLED` was checked correctly on step 1b's *inner* condition
but was absent from this *outer* one. An operator enabling ONLY
`OPTIONS_0DTE_ENABLED` — a documented, self-contained feature per its own
`settings.py` docstring — got the outer `if` evaluating `False`, so the
entire function body (including `manage_0dte_exits()`) never ran. Open 0DTE
positions got no automatic +75% profit target, -30% stop loss, or 15:45 ET
hard exit.

## What changed

**`main.py`**: extracted the inline block into a new module-level
`_run_automated_options_lifecycle(macro_dto=None)`, mirroring the existing
`_run_automated_delta_hedge_cycle` extraction (done previously for the SPY
fabricated-spot-price fix — same motivation: independent testability). The
outer gate now also OR's in `OPTIONS_0DTE_ENABLED`. Every inner step's own
logic, gating, and logging is byte-for-byte unchanged; only the extraction
boundary and the outer condition changed. `_run_cycle()` now calls
`_run_automated_options_lifecycle(macro_dto=result.macro_dto)`.

**`tests/test_main.py`**: added a test class with the full four-flag
OR-gate truth table, `import main as m` added for `monkeypatch.setattr(m.settings, ...)`
(this file didn't previously import `main` under an alias, only specific
names). Verified the key regression test
(`test_options_lifecycle_runs_0dte_exits_when_only_0dte_flag_enabled`)
fails when the pre-fix condition is reintroduced, by temporarily removing
the `OPTIONS_0DTE_ENABLED` clause from a copy of `main.py` and re-running —
confirms the test is a genuine regression guard, not a tautology.

## What was investigated but not fixed: the daemon-path gap

A second, broader gap was found while investigating: `execution/options_paper_executor.py::OptionsPaperExecutor`'s
exit management and strategy auto-execution, plus `main.py`'s delta-hedging
cycle, are called ONLY from `main.py`. `main_orchestrator.py`/
`desktop/daemon_runtime.py` (the persistent-daemon backend,
`ORCHESTRATOR_DAEMON_ENABLED` — off by default, but the documented future
cutover direction) have zero references to `OptionsPaperExecutor` (confirmed
via `grep`). Only 0DTE has a separate, already-correct, direct wiring into
`desktop/daemon_runtime.py::_timer_loop`.

**Why this wasn't fixed in the same PR**: the obvious fix — have
`desktop/daemon_runtime.py` import and call `main._run_automated_options_lifecycle`
— doesn't work cleanly. `main.py` has a module-top venv re-exec guard (the
first executable code in the file) that runs on ANY import of the module,
including one made purely to reach a function defined further down;
importing `main` from a long-lived daemon process risks unpredictable
behavior and blurs the deliberate architectural separation between `main.py`
and `main_orchestrator.py`/`desktop/` that `docs/architecture/orchestration-entrypoints.md`
documents. A real fix needs the shared logic relocated to a module both
entry points can import safely, plus two real design decisions: (1) what
cadence should exit management/strategy auto-execution/delta hedging run on
in the daemon (tied to the full pipeline cycle, or the more-frequent 0DTE
timer-wake cadence?), and (2) how does `execute_strategy_directives`'s
required `macro_dto` (needed for its VIX/CREDIT-EVENT regime gate) get
threaded through, since `main_orchestrator._main_body` doesn't currently
expose its `RunContext`/macro DTO back to the daemon's caller. Given the
daemon backend is off by default today (zero live impact on any installation
that hasn't opted in) and the risk of getting cadence/macro_dto threading
wrong in a heavily-tested piece of code, this was scoped as documentation
now, dedicated follow-up task later — matching CLAUDE.md's own instruction
to "clearly disclose" rather than force a fix into this PR's scope.

Four places document this: a new `docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md`
(the full write-up, added to `docs/known_issues/README.md`'s index),
extended comments in `desktop/daemon_runtime.py` at both the spot the old
0DTE-only comment lived, `docs/architecture/execution.md`'s
`OptionsPaperExecutor` bullet, and `CLAUDE.md`'s "Multi-Leg Option Paper
Trading" bullet (auto-mirrored to `AGENTS.md`).

## Verification

- `pytest tests/test_main.py -q` → 13 passed (7 pre-existing + 6 new).
- `pytest tests/test_run_once.py -q` → 49 passed, no regression.
- `pytest tests/test_daemon_runtime.py -q` → 58 passed (comment-only edits
  there, confirmed no logic changed).
- `pytest tests/test_orchestrator_daemon.py tests/test_options_paper_executor.py tests/test_zero_dte_engine.py -q` → 94 passed.
- Manually reintroduced the pre-fix gate condition in a scratch copy of
  `main.py` and confirmed the key regression test fails; restored the fix
  and confirmed all tests pass again.
- `ruff check` diffed against the pre-change baseline: only one new finding,
  `UP045` on the new function's `Optional[MacroEconomicDTO]` signature —
  matches the exact pre-existing style of the sibling
  `_run_automated_delta_hedge_cycle` function it mirrors, not a genuine
  issue, and not addressed project-wide (this repo has 22-29 pre-existing
  instances of the same style pattern already).
- `diff CLAUDE.md AGENTS.md` → in sync (the `sync_agent_docs.sh` hook fired
  on the `CLAUDE.md` edit).
