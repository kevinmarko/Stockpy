"""
tests/test_pipeline_runner.py
==============================
Dedicated offline suite for ``pipeline/runner.py::AsyncPipelineRunner`` --
the async mediator that dispatches each ``PipelineStep`` via
``asyncio.to_thread`` (sync steps) or a direct ``await`` (async steps).

``pipeline/runner.py::PipelineRunner`` (the synchronous sibling) already has
dedicated ordering/stop-short-circuit coverage in
``tests/test_pipeline_package.py::TestPipelineRunner`` -- not duplicated
here. This file exists because, before the 2026-08 structural-timeout fix
(see docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md's
"Scope boundary" section, which named this exact dispatcher as an unaudited
gap), ``AsyncPipelineRunner`` itself had zero direct test coverage -- every
other test file that touches it (test_orchestrator_daemon.py,
test_execution_alerts.py, test_progress_emission.py) fakes the whole class
away rather than exercising the real one.

Coverage:
  TestAsyncPipelineRunnerOrdering : strictly-ordered dispatch + stop-skip
                                    semantics, for a mix of sync and async
                                    steps (mirrors TestPipelineRunner's sync
                                    coverage in tests/test_pipeline_package.py).
  TestAsyncPipelineRunnerStepTimeout : a synchronous step that blocks past
                                    settings.PIPELINE_STEP_TIMEOUT_SECONDS
                                    raises TimeoutError within bounded
                                    wall-clock time (not the full sleep
                                    duration); a normal (fast) step is
                                    unaffected by the timeout being in place.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict

import pytest

from pipeline.base import PipelineStep
from pipeline.context import RunContext
from pipeline.runner import AsyncPipelineRunner
from settings import settings


# ---------------------------------------------------------------------------
# Shared fixtures / factories (mirrors tests/test_pipeline_package.py::_make_ctx)
# ---------------------------------------------------------------------------

def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def _make_ctx(**overrides: Any) -> RunContext:
    """Build a RunContext with harmless dummy callables, overridable per test."""
    defaults: Dict[str, Any] = dict(
        force_account=False,
        started_at=datetime.now(timezone.utc),
        watchlist_file="watchlist.txt",
        fetch_account_snapshot_fn=_noop,
        build_universe_fn=lambda snapshot: [],
        build_macro_dto_fn=_noop,
        get_provider_fn=_noop,
        fetch_bars_fn=lambda symbols, market: {},
        build_context_extras_fn=lambda symbols, bars, macro_dto: {},
        advisory_evaluate_fn=_noop,
    )
    defaults.update(overrides)
    return RunContext(**defaults)


def _ctx_with_log() -> RunContext:
    ctx = _make_ctx()
    ctx._log = []  # type: ignore[attr-defined]
    return ctx


def _run(ctx: RunContext, steps) -> None:
    """Drive AsyncPipelineRunner.run() to completion via a plain asyncio.run().

    Safe here (unlike the timeout test below) because every step in the
    ordering suite completes promptly -- no orphaned background thread can
    ever be left running for asyncio.run()'s shutdown_default_executor() to
    block on.
    """
    asyncio.run(AsyncPipelineRunner(steps).run(ctx))


# ---------------------------------------------------------------------------
# Fake steps
# ---------------------------------------------------------------------------

class _RecordingStep(PipelineStep):
    """Synchronous step: appends its own name to ctx._log when run()."""

    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, ctx: RunContext) -> None:
        ctx._log.append(self.name)  # type: ignore[attr-defined]


class _RecordingAsyncStep(PipelineStep):
    """Asynchronous step: appends its own name to ctx._log when run()."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, ctx: RunContext) -> None:
        ctx._log.append(self.name)  # type: ignore[attr-defined]


class _StoppingStep(PipelineStep):
    """Sets ctx.stopped=True (and a distinguishing stop_reason) when run."""

    name = "stopper"

    def run(self, ctx: RunContext) -> None:
        ctx._log.append(self.name)  # type: ignore[attr-defined]
        ctx.stopped = True
        ctx.stop_reason = "stopped_by_test"


class _SlowSyncStep(PipelineStep):
    """Synchronous step whose run() blocks for `seconds` via a BOUNDED real
    time.sleep() -- never an unbounded threading.Event().wait(). An
    unbounded blocked worker thread can hang pytest/interpreter shutdown via
    concurrent.futures.thread's atexit join (see
    tests/test_main_orchestrator.py::test_macro_fetch_hang_isolated_dict_fallback_within_bounded_time's
    own docstring for the identical reasoning)."""

    name = "slow"

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    def run(self, ctx: RunContext) -> None:
        time.sleep(self._seconds)
        ctx._log.append(self.name)  # type: ignore[attr-defined]


class _FastSyncStep(PipelineStep):
    """A normal, fast synchronous step -- the happy-path smoke case."""

    name = "fast"

    def run(self, ctx: RunContext) -> None:
        ctx._log.append(self.name)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# TestAsyncPipelineRunnerOrdering — mirrors TestPipelineRunner (sync) exactly,
# plus a mixed sync/async case that only the async runner needs to handle.
# ---------------------------------------------------------------------------

class TestAsyncPipelineRunnerOrdering:
    """AsyncPipelineRunner must run steps strictly in list order, and once
    any step sets ctx.stopped=True, every LATER step must be skipped
    entirely (never have .run() invoked) via the default
    should_skip(ctx) -> ctx.stopped. This must hold regardless of whether a
    step's run() is sync or async."""

    def test_runs_sync_steps_in_order(self) -> None:
        ctx = _ctx_with_log()
        steps = [_RecordingStep("a"), _RecordingStep("b"), _RecordingStep("c")]
        _run(ctx, steps)
        assert ctx._log == ["a", "b", "c"]  # type: ignore[attr-defined]

    def test_runs_mixed_sync_and_async_steps_in_order(self) -> None:
        ctx = _ctx_with_log()
        steps = [
            _RecordingStep("sync_1"),
            _RecordingAsyncStep("async_1"),
            _RecordingStep("sync_2"),
            _RecordingAsyncStep("async_2"),
        ]
        _run(ctx, steps)
        assert ctx._log == ["sync_1", "async_1", "sync_2", "async_2"]  # type: ignore[attr-defined]

    def test_steps_after_stop_are_skipped(self) -> None:
        ctx = _ctx_with_log()
        steps = [
            _RecordingStep("before"),
            _StoppingStep(),
            _RecordingStep("after_1"),
            _RecordingAsyncStep("after_2"),
        ]
        _run(ctx, steps)
        assert ctx._log == ["before", "stopper"]  # type: ignore[attr-defined]
        assert ctx.stopped is True
        assert ctx.stop_reason == "stopped_by_test"

    def test_step_before_stop_runs_normally(self) -> None:
        ctx = _ctx_with_log()
        steps = [_RecordingStep("first"), _StoppingStep(), _RecordingAsyncStep("never")]
        _run(ctx, steps)
        assert "first" in ctx._log  # type: ignore[attr-defined]
        assert "never" not in ctx._log  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# TestAsyncPipelineRunnerStepTimeout — the 2026-08 structural-timeout fix.
# ---------------------------------------------------------------------------

class TestAsyncPipelineRunnerStepTimeout:
    """settings.PIPELINE_STEP_TIMEOUT_SECONDS bounds AsyncPipelineRunner's
    generic `await asyncio.to_thread(step.run, ctx)` dispatch for a
    synchronous step. A step that blocks past the timeout raises
    TimeoutError, which -- per pipeline/runner.py's own module docstring --
    is deliberately allowed to propagate uncaught out of
    AsyncPipelineRunner.run() rather than being swallowed."""

    def test_slow_sync_step_raises_timeout_error_within_bounded_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A step whose sync run() sleeps well past a (monkeypatched, tiny)
        PIPELINE_STEP_TIMEOUT_SECONDS must cause AsyncPipelineRunner.run()
        to raise TimeoutError promptly -- not hang for the full sleep
        duration.

        Deliberately uses a manually-managed event loop (new_event_loop +
        run_until_complete + close in a try/finally), NOT asyncio.run():
        asyncio.wait_for's cancellation on timeout cannot actually interrupt
        a thread already blocked inside a running synchronous call
        (concurrent.futures.Future.cancel() is a no-op once running) -- the
        thread keeps sleeping in the background regardless. asyncio.run()
        additionally calls shutdown_default_executor(), which BLOCKS until
        every such orphaned thread finishes, which would confound this
        test's wall-clock assertion. Plain loop.close() does not wait for
        it. See tests/test_main_orchestrator.py's
        test_macro_fetch_hang_isolated_dict_fallback_within_bounded_time for
        the identical, already-established pattern.
        """
        monkeypatch.setattr(settings, "PIPELINE_STEP_TIMEOUT_SECONDS", 0.05)
        ctx = _ctx_with_log()
        steps = [_RecordingStep("before"), _SlowSyncStep(0.3), _RecordingStep("never")]

        loop = asyncio.new_event_loop()
        try:
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                loop.run_until_complete(AsyncPipelineRunner(steps).run(ctx))
            elapsed = time.monotonic() - started
        finally:
            loop.close()

        # Bounded by the (tiny, monkeypatched) timeout, not the 0.3s sleep.
        assert elapsed < 1.0
        # The step before the slow one ran; the one after never got a chance
        # (the exception propagates out of the for-loop immediately).
        assert ctx._log == ["before"]  # type: ignore[attr-defined]

    def test_normal_fast_step_runs_cleanly_with_timeout_in_place(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Happy-path regression check: a normal (fast) synchronous step
        must still run and return cleanly now that every sync dispatch is
        wrapped in asyncio.wait_for(...) -- no false-positive timeout, no
        behavior change for the overwhelming common case."""
        monkeypatch.setattr(settings, "PIPELINE_STEP_TIMEOUT_SECONDS", 5.0)
        ctx = _ctx_with_log()
        steps = [_FastSyncStep(), _RecordingAsyncStep("async_after"), _RecordingStep("sync_after")]

        _run(ctx, steps)

        assert ctx._log == ["fast", "async_after", "sync_after"]  # type: ignore[attr-defined]

    def test_default_timeout_setting_is_generous(self) -> None:
        """Pin the documented production default (900s) so a future,
        unintentional edit to settings.py can't silently shrink the
        production timeout without a test noticing."""
        assert settings.PIPELINE_STEP_TIMEOUT_SECONDS == 900.0
