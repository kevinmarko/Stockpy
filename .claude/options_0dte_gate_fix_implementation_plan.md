# Implementation Plan: `OPTIONS_0DTE_ENABLED` missing from main.py's automated-options outer gate

## Context

An audit found that `main.py`'s `_run_cycle()` (the closure `main()` calls
every cycle — the DEFAULT production backend, since
`settings.ORCHESTRATOR_DAEMON_ENABLED` defaults `False`) gated its whole
"Automated Strategy Options Paper Execution & Lifecycle" block on:

```python
if (
    getattr(settings, "PAPER_OPTIONS_AUTO_EXECUTE_ENABLED", False)
    or getattr(settings, "OPTIONS_AUTO_EXIT_ENABLED", False)
    or getattr(settings, "OPTIONS_DELTA_HEDGE_ENABLED", False)
):
```

`OPTIONS_0DTE_ENABLED` was checked correctly on the inner 0DTE step but was
missing from this outer gate — an operator enabling ONLY 0DTE lifecycle
management (a documented, self-contained flag) got the whole block silently
skipped, so `manage_0dte_exits()`'s +75%/-30%/15:45 ET hard exit never ran
for open 0DTE positions.

A related, broader gap was also found: the whole automated-options lifecycle
has no equivalent wiring in `main_orchestrator.py`/`desktop/daemon_runtime.py`
— only 0DTE is separately wired into the daemon's own `_timer_loop`.

## Fix 1 (required, implemented): main.py outer-gate bug

- Extracted the inline block into a new module-level
  `_run_automated_options_lifecycle(macro_dto=None)` (mirroring the existing
  `_run_automated_delta_hedge_cycle` extraction pattern), fixing the outer
  gate to also OR in `OPTIONS_0DTE_ENABLED`.
- `_run_cycle()` now calls `_run_automated_options_lifecycle(macro_dto=result.macro_dto)`.
- Every inner step's own gate/logic/logging is unchanged.

## Fix 2 (tests, implemented): tests/test_main.py

Added a dedicated test class proving the full four-flag OR-gate truth table:
- `test_options_lifecycle_runs_0dte_exits_when_only_0dte_flag_enabled` — THE
  regression test (only `OPTIONS_0DTE_ENABLED=True` -> `manage_0dte_exits`
  genuinely invoked).
- `test_options_lifecycle_skips_everything_when_all_flags_disabled`
- `test_options_lifecycle_runs_exit_management_when_only_auto_exit_flag_enabled`
- `test_options_lifecycle_runs_strategy_auto_execute_when_only_that_flag_enabled`
- `test_options_lifecycle_runs_delta_hedge_when_only_that_flag_enabled`
- `test_options_lifecycle_swallows_exceptions_and_logs_warning`

Manually verified the key regression test fails when the pre-fix outer gate
condition is reintroduced (confirms the test actually catches the bug).

## Fix 3 (documentation, implemented): the daemon-cutover gap

Disclosed, not fixed in code — `main.py` has a module-top venv re-exec guard
that makes `import main` from `desktop/daemon_runtime.py` unsafe, so reusing
the extracted functions there isn't a mechanical port; a real fix needs a
shared importable-from-both module plus cadence and `macro_dto`-threading
design decisions (`execute_strategy_directives` needs a real `macro_dto` for
its VIX/CREDIT-EVENT gate; `main_orchestrator._main_body` doesn't currently
expose that back to its daemon caller). Documented in four places:

1. New `docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md` —
   full write-up of both bugs, the fix, and the deferred-gap reasoning.
2. `docs/known_issues/README.md` — index row added.
3. `desktop/daemon_runtime.py` — extended the existing `_run_one_cycle` and
   `_timer_loop` comments to state the gap plainly.
4. `docs/architecture/execution.md` — extended the
   `execution/options_paper_executor.py` bullet.
5. `CLAUDE.md` (auto-mirrored to `AGENTS.md` via the sync hook) — extended
   the "Multi-Leg Option Paper Trading & Automated Strategy Paper Execution"
   bullet with a dated addendum.

## Verification performed

- `pytest tests/test_main.py -q` — 13 passed.
- `pytest tests/test_run_once.py -q` — 49 passed (no regression).
- `pytest tests/test_daemon_runtime.py -q` — 58 passed (comment-only changes,
  confirmed no logic touched).
- `pytest tests/test_orchestrator_daemon.py tests/test_options_paper_executor.py tests/test_zero_dte_engine.py -q` — 94 passed.
- Manually reintroduced the pre-fix gate condition and confirmed the key
  regression test fails, then restored the fix and confirmed it passes again.
- `ruff check main.py tests/test_main.py desktop/daemon_runtime.py` — diffed
  against the pre-change baseline; the only new finding is one `UP045`
  (`Optional[X]` vs `X | None`) on the new function's signature, matching
  the exact pre-existing style of the sibling `_run_automated_delta_hedge_cycle`
  function it mirrors — not a genuine issue, not addressed project-wide.
- `diff CLAUDE.md AGENTS.md` — confirmed the sync hook mirrored the edit.
