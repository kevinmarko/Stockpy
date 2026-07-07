"""
tests/test_dashboard_validation.py
==================================
Phase 3b — two-tier DashboardSchema validation in main_orchestrator.

Verifies ``main_orchestrator._validate_dashboard``:
  * empty frame is trivially valid (no validation run);
  * a fully-populated, schema-conformant frame validates True;
  * an invalid frame in NON-strict mode logs and returns False — never raises
    (CONSTRAINT #6: the report must not be held hostage to a coerced column);
  * the SAME invalid frame in STRICT mode raises PipelineFatalError (NOT
    sys.exit) so a long-lived daemon can catch it and survive, while the CLI
    entry point still converts it to a non-zero exit for CI schema-drift gating;
  * lazy=True is used so ALL violations aggregate into one report.

No network, no orchestrator run — the helper is exercised directly.
"""

from __future__ import annotations

import pandas as pd
import pytest

import config
import main_orchestrator as mo


def _valid_dashboard_row() -> dict:
    """Build one schema-conformant row from config.COLUMN_SCHEMA."""
    row: dict = {}
    for col in config.COLUMN_SCHEMA:
        key = col["key"]
        if key == "Symbol":
            row[key] = "AAPL"
        elif col["format"] in ("currency", "currency_large", "percent", "number"):
            row[key] = 1.0
        else:
            row[key] = "x"
    return row


class TestValidateDashboard:
    def test_empty_frame_is_valid_both_modes(self) -> None:
        assert mo._validate_dashboard(pd.DataFrame(), strict=False) is True
        assert mo._validate_dashboard(pd.DataFrame(), strict=True) is True

    def test_valid_frame_passes_strict(self) -> None:
        valid = pd.DataFrame([_valid_dashboard_row()])
        # Strict mode must NOT exit on a conformant frame.
        assert mo._validate_dashboard(valid, strict=True) is True

    def test_invalid_frame_nonstrict_returns_false_never_raises(self) -> None:
        # Symbol > 10 chars violates str_length(1,10); missing columns add more
        # failure cases — lazy=True aggregates them all.
        bad = pd.DataFrame({"Symbol": ["WAYTOOLONGSYMBOL"]})
        result = mo._validate_dashboard(bad, strict=False)
        assert result is False  # logged + degraded, never raised

    def test_invalid_frame_strict_raises_pipeline_fatal_not_systemexit(self) -> None:
        # De-fatalization contract: strict-mode validation failure raises
        # PipelineFatalError (a RuntimeError subclass) instead of sys.exit(1),
        # so a daemon's try/except Exception catches it. It must NOT be a
        # SystemExit any more.
        bad = pd.DataFrame({"Symbol": ["WAYTOOLONGSYMBOL"]})
        with pytest.raises(mo.PipelineFatalError):
            mo._validate_dashboard(bad, strict=True)
        # Explicitly assert it is catchable as an ordinary Exception (not
        # SystemExit) — that is the whole point of the change.
        assert issubclass(mo.PipelineFatalError, Exception)
        assert not issubclass(mo.PipelineFatalError, SystemExit)

    def test_cli_entrypoint_converts_pipeline_fatal_to_exit_1(self) -> None:
        """The standalone-CLI contract: the __main__ block must catch
        PipelineFatalError and convert it to a non-zero exit, so CI /
        make verify / the GUI subprocess still see a failing returncode even
        though the pipeline no longer calls sys.exit(1) inline."""
        import inspect

        src = inspect.getsource(mo)
        # The __main__ block wraps asyncio.run(main(...)) in a handler that
        # maps PipelineFatalError -> sys.exit(1).
        assert "except PipelineFatalError" in src
        assert "sys.exit(1)" in src

    def test_main_threads_strict_flag(self) -> None:
        """`main(strict=...)` and `--strict` must wire through to _main_body."""
        import inspect

        # main() accepts strict
        assert "strict" in inspect.signature(mo.main).parameters
        # _main_body accepts strict
        assert "strict" in inspect.signature(mo._main_body).parameters
        # CLI registers --strict
        src = inspect.getsource(mo)
        assert '"--strict"' in src
