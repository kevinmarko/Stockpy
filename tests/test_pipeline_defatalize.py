"""
tests/test_pipeline_defatalize.py
=================================
PR1 (persistent-daemon groundwork): the pipeline must raise PipelineFatalError
instead of calling sys.exit(1) on a fatal per-run failure, so a long-lived
caller (the future orchestrator daemon) can catch it and keep serving. The
standalone CLI path preserves the original non-zero-exit contract by converting
PipelineFatalError -> sys.exit(1) at the __main__ boundary.

These tests exercise _main_body's two fatal branches (data-fetch crash and
pipeline crash) directly with everything network-facing monkeypatched, and
assert PipelineFatalError propagates as an ordinary catchable Exception rather
than a SystemExit.
"""
from __future__ import annotations

import asyncio

import pytest

import main_orchestrator as mo


class TestPipelineFatalErrorType:
    def test_is_runtimeerror_not_systemexit(self) -> None:
        # A daemon catches `except Exception`; SystemExit would escape that.
        assert issubclass(mo.PipelineFatalError, RuntimeError)
        assert not issubclass(mo.PipelineFatalError, SystemExit)


class TestMainBodyFatalPaths:
    def test_data_fetch_crash_raises_pipeline_fatal(self, monkeypatch) -> None:
        # Force the credentials-ABSENT branch (MockDataEngine path, tickers=
        # ["AAPL"]) so we avoid touching FRED / the real DataEngine, then make
        # the concurrent fetch blow up -> the data-fetch except clause fires.
        monkeypatch.setattr(mo.os.path, "exists", lambda p: False)

        class _NoLoginClient:
            def login(self):
                return False

        monkeypatch.setattr(mo, "RobinhoodClient", lambda *a, **k: _NoLoginClient())

        async def _boom(*_a, **_k):
            raise RuntimeError("simulated network collapse")

        monkeypatch.setattr(mo, "fetch_all_data_async", _boom)

        with pytest.raises(mo.PipelineFatalError):
            asyncio.run(mo._main_body(effective_dry_run=True, strict=False))

    def test_pipeline_crash_raises_pipeline_fatal(self, monkeypatch) -> None:
        monkeypatch.setattr(mo.os.path, "exists", lambda p: False)

        class _NoLoginClient:
            def login(self):
                return False

        monkeypatch.setattr(mo, "RobinhoodClient", lambda *a, **k: _NoLoginClient())

        # Fetch succeeds with non-empty data so we skip the empty-fallback and
        # reach run_pipeline...
        import pandas as pd

        async def _ok_fetch(*_a, **_k):
            df = pd.DataFrame({"Close": [1.0, 2.0]})
            return {}, {}, {"AAPL": df}

        monkeypatch.setattr(mo, "fetch_all_data_async", _ok_fetch)
        # Kill switch inactive so we don't early-return before the pipeline.
        monkeypatch.setattr(
            mo, "GlobalKillSwitch", lambda *a, **k: type("K", (), {"is_active": lambda self: False})()
        )

        def _boom_pipeline(*_a, **_k):
            raise ValueError("simulated engine failure")

        monkeypatch.setattr(mo, "run_pipeline", _boom_pipeline)

        with pytest.raises(mo.PipelineFatalError):
            asyncio.run(mo._main_body(effective_dry_run=True, strict=False))
