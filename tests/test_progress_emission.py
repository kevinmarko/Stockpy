"""
tests/test_progress_emission.py
================================
Offline tests for progress-instrumentation wiring: ``ProgressReporter``
(reporting/progress.py — the frozen, pre-existing public API this file does
NOT redefine) is threaded through main.py's ``run_once()`` pipeline
(pipeline/runner.py, pipeline/steps.py) and main_orchestrator.py's
``_main_body()`` / ``run_pipeline()``.

All network I/O is monkeypatched, mirroring the established conventions in
tests/test_run_once.py (the ``main.run_once()`` mocking pattern —
``_PATCH_SNAPSHOT``/``_PATCH_EVALUATE``/etc. below reuse the exact same patch
targets) and tests/test_pipeline_defatalize.py (``main_orchestrator._main_body``
fatal-path mocking, reused here for a fast failure-path progress check that
never constructs a single heavy engine).

Coverage:
  * main.run_once() writes output/progress.json reaching a terminal
    finish("succeeded") on the happy path, and finish("failed") — while still
    RE-RAISING the original exception unchanged — when an unguarded step
    raises (mirrors pipeline/runner.py's documented "unguarded steps
    propagate" contract).
  * advance_symbol() is called exactly once per symbol in the advisory loop,
    both at the main.py level (AdvisoryEvalStep, via a full run_once() cycle)
    and in isolation (AdvisoryEvalStep.run() called directly).
  * progress=None (the default added to every new parameter by this PR) is a
    complete no-op — PipelineRunner.run(ctx) and AdvisoryEvalStep.run(ctx)
    behave exactly as they did before this instrumentation existed.
  * main_orchestrator._main_body()'s wrapper marks a cycle "failed" (not
    "succeeded") when the pipeline crashes during data fetch, still
    re-raising the exact same PipelineFatalError unchanged.
  * main_orchestrator.run_pipeline() / _main_body_impl() both default
    `progress` to None — pinned via signature introspection; the full "None
    reproduces byte-identical behavior" claim for the (expensive) heavy
    pipeline is empirically covered by the pre-existing, UNMODIFIED
    main_orchestrator test suites (tests/test_main_orchestrator.py,
    tests/test_engine_context.py, tests/test_orchestrator_e2e.py, ...), every
    one of which calls run_pipeline()/_main_body() without ever passing
    `progress` and all still pass after this instrumentation landed.

Nothing here touches the network.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from reporting.progress import ProgressReporter, read_progress
from settings import settings as _settings

import main as m
from main import RunResult, run_once
from engine.advisory import Recommendation


# ---------------------------------------------------------------------------
# Shared fixtures / factories (mirrors tests/test_run_once.py conventions)
# ---------------------------------------------------------------------------

def _make_snapshot() -> MagicMock:
    snap = MagicMock()
    snap.positions = {}
    snap.buying_power = 50_000.0
    snap.total_equity = 100_000.0
    snap.total_dividends = 0.0
    snap.fetched_at = datetime.now(timezone.utc)
    snap.age_hours.return_value = 0.1
    snap.is_stale.return_value = False
    return snap


def _make_recommendation(symbol: str, action: str = "HOLD") -> Recommendation:
    return Recommendation(
        symbol=symbol,
        action=action,
        strategy="test_strategy",
        conviction=0.60,
        rationale=f"{symbol}: test rationale.",
        suggested_position_pct=0.02,
        forecast=105.0,
        key_indicators={},
        data_quality="OK",
    )


_PATCH_SNAPSHOT = "main.fetch_account_snapshot"
_PATCH_EVALUATE = "main.advisory_evaluate"
_PATCH_PROVIDER = "main.get_provider"
_PATCH_MACRO = "main._build_macro_dto"
_PATCH_BARS = "main._fetch_bars_for_universe"
_PATCH_CTX = "main._build_context_extras"

_WATCHLIST = "AAPL,MSFT,GOOG"
_N_TICKERS = 3


# ---------------------------------------------------------------------------
# main.run_once() — full-cycle progress emission
# ---------------------------------------------------------------------------

class TestRunOnceProgressEmission:
    """main.run_once() end-to-end progress-instrumentation checks."""

    @patch(_PATCH_CTX, return_value={})
    @patch(_PATCH_BARS, return_value={})
    @patch(_PATCH_MACRO)
    @patch(_PATCH_EVALUATE)
    @patch(_PATCH_PROVIDER)
    @patch(_PATCH_SNAPSHOT)
    def test_writes_progress_json_reaching_terminal_succeeded(
        self,
        mock_snap: MagicMock,
        mock_provider: MagicMock,
        mock_eval: MagicMock,
        mock_macro: MagicMock,
        _bars: MagicMock,
        _ctx: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        monkeypatch.setenv("WATCHLIST", _WATCHLIST)
        monkeypatch.setattr(_settings, "OUTPUT_DIR", tmp_path)
        mock_snap.return_value = _make_snapshot()
        mock_macro.return_value = MagicMock(market_regime="NEUTRAL", vix_value=18.0)
        mock_eval.side_effect = lambda symbol, **kw: _make_recommendation(symbol, "HOLD")

        result = run_once()

        assert isinstance(result, RunResult)
        assert len(result.recommendations) == _N_TICKERS

        state = read_progress(tmp_path)
        assert state is not None, "output/progress.json was not written"
        assert state.is_terminal is True
        assert state.state == "succeeded"
        assert state.percent == pytest.approx(100.0)

    @patch(_PATCH_CTX, return_value={})
    @patch(_PATCH_BARS, return_value={})
    @patch(_PATCH_MACRO)
    @patch(_PATCH_EVALUATE)
    @patch(_PATCH_PROVIDER)
    @patch(_PATCH_SNAPSHOT)
    def test_advance_symbol_called_exactly_once_per_ticker(
        self,
        mock_snap: MagicMock,
        mock_provider: MagicMock,
        mock_eval: MagicMock,
        mock_macro: MagicMock,
        _bars: MagicMock,
        _ctx: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        monkeypatch.setenv("WATCHLIST", _WATCHLIST)
        monkeypatch.setattr(_settings, "OUTPUT_DIR", tmp_path)
        mock_snap.return_value = _make_snapshot()
        mock_macro.return_value = MagicMock(market_regime="NEUTRAL", vix_value=18.0)
        mock_eval.side_effect = lambda symbol, **kw: _make_recommendation(symbol, "HOLD")

        calls: List[str] = []
        _original_advance = ProgressReporter.advance_symbol

        def _counting_advance(self: ProgressReporter, message: str = "") -> None:
            calls.append(message)
            return _original_advance(self, message)

        # Patches the CLASS attribute, so every ProgressReporter instance
        # constructed inside run_once() (there is exactly one per cycle) picks
        # up the wrapper via normal Python method resolution.
        monkeypatch.setattr(ProgressReporter, "advance_symbol", _counting_advance)

        run_once()

        assert len(calls) == _N_TICKERS

    @patch(_PATCH_CTX, return_value={})
    @patch(_PATCH_BARS, return_value={})
    @patch(_PATCH_EVALUATE)
    @patch(_PATCH_PROVIDER)
    @patch(_PATCH_SNAPSHOT)
    def test_unguarded_step_failure_marks_progress_failed_and_reraises(
        self,
        mock_snap: MagicMock,
        mock_provider: MagicMock,
        mock_eval: MagicMock,
        _bars: MagicMock,
        _ctx: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """MacroStep is unguarded (pipeline/runner.py's documented contract):
        a raise there must propagate out of run_once() uncaught. Progress
        instrumentation must mark the reporter "failed" BEFORE that exception
        escapes, never swallow it (CONSTRAINT #6)."""
        monkeypatch.setenv("WATCHLIST", _WATCHLIST)
        monkeypatch.setattr(_settings, "OUTPUT_DIR", tmp_path)
        mock_snap.return_value = _make_snapshot()

        def _boom() -> Any:
            raise RuntimeError("simulated macro engine crash")

        monkeypatch.setattr(m, "_build_macro_dto", _boom)

        with pytest.raises(RuntimeError, match="simulated macro engine crash"):
            run_once()

        state = read_progress(tmp_path)
        assert state is not None
        assert state.is_terminal is True
        assert state.state == "failed"


# ---------------------------------------------------------------------------
# progress=None default path — must be a complete no-op
# ---------------------------------------------------------------------------

class TestProgressNoneDefaultUnchanged:
    """progress=None (the implicit default on every new parameter this PR
    added) must reproduce byte-identical pre-instrumentation behavior at the
    PipelineRunner / PipelineStep level — see pipeline/runner.py and
    pipeline/steps.py docstrings for the "no new error handling" contract
    this instrumentation must not violate."""

    def test_pipeline_runner_run_without_progress_arg(self) -> None:
        from pipeline.base import PipelineStep
        from pipeline.context import RunContext
        from pipeline.runner import PipelineRunner

        class _Recording(PipelineStep):
            name = "rec"

            def run(self, ctx: RunContext) -> None:
                ctx.errors.append({"ran": self.name})

        ctx = RunContext(
            force_account=False,
            started_at=datetime.now(timezone.utc),
            watchlist_file="watchlist.txt",
            fetch_account_snapshot_fn=lambda **_kw: None,
            build_universe_fn=lambda snapshot: [],
            build_macro_dto_fn=lambda: None,
            get_provider_fn=lambda: None,
            fetch_bars_fn=lambda symbols, market: {},
            build_context_extras_fn=lambda symbols, bars, macro_dto: {},
            advisory_evaluate_fn=lambda **_kw: None,
        )

        PipelineRunner([_Recording()]).run(ctx)  # no progress kwarg at all

        assert ctx.progress is None
        assert ctx.errors == [{"ran": "rec"}]

    def test_run_pipeline_progress_defaults_to_none(self) -> None:
        """Pins main_orchestrator.run_pipeline()'s new `progress` parameter's
        default (None) so a future edit that accidentally makes it required
        (breaking every existing caller/test that doesn't pass it) fails CI
        immediately."""
        import inspect

        import main_orchestrator as mo

        sig = inspect.signature(mo.run_pipeline)
        assert "progress" in sig.parameters
        assert sig.parameters["progress"].default is None

    def test_main_body_impl_progress_defaults_to_none(self) -> None:
        import inspect

        import main_orchestrator as mo

        sig = inspect.signature(mo._main_body_impl)
        assert "progress" in sig.parameters
        assert sig.parameters["progress"].default is None

    def test_main_body_wrapper_signature_unchanged(self) -> None:
        """The public `_main_body()` wrapper's pre-instrumentation params
        (effective_dry_run, strict, engines, data_engine) must stay present
        and in order — tests/test_dashboard_validation.py and
        tests/test_main_body_engine_injection.py both introspect/call it
        directly and must be unaffected by the internal
        _main_body -> _main_body_impl split. `mode` (added by PR #330's
        pipeline-mode control endpoints) is keyword-only with a default
        ("full"), so it is a backward-compatible addition — every existing
        positional/keyword caller (including the two tests above, verified
        directly) is genuinely unaffected; this test's own docstring names
        that as the actual invariant, so the assertion allows `mode` rather
        than pinning the exact param list."""
        import inspect

        import main_orchestrator as mo

        params = list(inspect.signature(mo._main_body).parameters)
        assert params[:4] == ["effective_dry_run", "strict", "engines", "data_engine"]
        assert "progress" not in params  # progress lives only on _main_body_impl


# ---------------------------------------------------------------------------
# AdvisoryEvalStep — direct unit tests of advance_symbol() ticks
# ---------------------------------------------------------------------------

class TestAdvisoryEvalStepProgressWiring:
    """Direct unit tests of pipeline/steps.py::AdvisoryEvalStep's
    advance_symbol() ticks, isolated from the rest of run_once()."""

    def _make_ctx(self, progress: Optional[ProgressReporter] = None) -> Any:
        from pipeline.context import RunContext

        def _evaluate(symbol: str, **_kw: Any) -> Any:
            if symbol == "BAD":
                raise RuntimeError("simulated failure")
            rec = MagicMock()
            rec.symbol = symbol
            rec.action = "HOLD"
            rec.conviction = 0.5
            rec.data_quality = "OK"
            rec.suggested_position_pct = 0.0
            return rec

        ctx = RunContext(
            force_account=False,
            started_at=datetime.now(timezone.utc),
            watchlist_file="watchlist.txt",
            fetch_account_snapshot_fn=lambda **_kw: None,
            build_universe_fn=lambda snapshot: [],
            build_macro_dto_fn=lambda: None,
            get_provider_fn=lambda: None,
            fetch_bars_fn=lambda symbols, market: {},
            build_context_extras_fn=lambda symbols, bars, macro_dto: {},
            advisory_evaluate_fn=_evaluate,
        )
        ctx.symbols = ["AAPL", "MSFT", "BAD"]
        ctx.snapshot = MagicMock(positions={})
        ctx.market = object()
        ctx.macro_dto = object()
        ctx.context_extras = {}
        ctx.progress = progress
        return ctx

    def test_advance_symbol_ticks_once_per_symbol_sequential(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        from pipeline.steps import AdvisoryEvalStep

        monkeypatch.setattr("pipeline.steps.settings.ADVISORY_MAX_CONCURRENCY", 1, raising=False)
        reporter = ProgressReporter(["advisory_eval"], output_dir=tmp_path)
        reporter.start_stage("advisory_eval", symbols_total=3)
        ctx = self._make_ctx(progress=reporter)

        AdvisoryEvalStep().run(ctx)

        state = read_progress(tmp_path)
        assert state is not None
        assert state.symbols_done == 3  # 2 ok + 1 dead-lettered, each ticks exactly once
        assert len(ctx.recommendations) == 2
        assert len(ctx.errors) == 1

    def test_advance_symbol_ticks_once_per_symbol_parallel(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        from pipeline.steps import AdvisoryEvalStep

        monkeypatch.setattr("pipeline.steps.settings.ADVISORY_MAX_CONCURRENCY", 8, raising=False)
        reporter = ProgressReporter(["advisory_eval"], output_dir=tmp_path)
        reporter.start_stage("advisory_eval", symbols_total=3)
        ctx = self._make_ctx(progress=reporter)

        AdvisoryEvalStep().run(ctx)

        state = read_progress(tmp_path)
        assert state is not None
        assert state.symbols_done == 3

    def test_progress_none_is_complete_noop(self) -> None:
        from pipeline.steps import AdvisoryEvalStep

        ctx = self._make_ctx(progress=None)

        AdvisoryEvalStep().run(ctx)  # must not raise with ctx.progress is None

        assert len(ctx.recommendations) == 2
        assert len(ctx.errors) == 1


# ---------------------------------------------------------------------------
# main_orchestrator.py — fatal-path progress check
# ---------------------------------------------------------------------------
# Mirrors tests/test_pipeline_defatalize.py's exact fixture pattern (fast, no
# heavy engine construction, since the crash happens before run_pipeline is
# ever reached).

class TestMainOrchestratorProgressFatalPath:
    def test_data_fetch_crash_marks_progress_failed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        import main_orchestrator as mo

        monkeypatch.setattr(mo.os.path, "exists", lambda p: False)
        monkeypatch.setattr(_settings, "OUTPUT_DIR", tmp_path)

        monkeypatch.setattr(mo, "fetch_account_snapshot", lambda: None)

        async def _boom(*_a, **_k):
            raise RuntimeError("simulated network collapse")

        monkeypatch.setattr(mo, "fetch_all_data_async", _boom)

        with pytest.raises(mo.PipelineFatalError):
            asyncio.run(mo._main_body(effective_dry_run=True, strict=False))

        state = read_progress(tmp_path)
        assert state is not None
        assert state.is_terminal is True
        assert state.state == "failed"
