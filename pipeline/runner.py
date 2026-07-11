"""Mediator that runs an ordered list of PipelineStep instances.

CRITICAL: this class does NOT wrap steps in a blanket try/except. The
original (pre-refactor) run_once() body had try/except ONLY around the
account-snapshot fetch and the per-symbol advisory loop — every other stage
(_build_universe, _build_macro_dto, get_provider, _fetch_bars_for_universe,
_build_context_extras) ran unguarded, so an exception there propagated out of
run_once() uncaught. Preserving that exactly means PipelineRunner must NOT add
new error handling around steps that never had any — doing so would silently
change a crash into a swallowed error. Any exception handling belongs INSIDE
the individual step's own run() method, exactly where the original code had
it (see pipeline/steps.py).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from pipeline.base import PipelineStep
    from pipeline.context import RunContext
    from reporting.progress import ProgressReporter

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Runs an ordered list of steps against one RunContext."""

    def __init__(self, steps: List["PipelineStep"]) -> None:
        self._steps = steps

    def run(self, ctx: "RunContext", progress: Optional["ProgressReporter"] = None) -> None:
        """Execute each step in order, honoring should_skip() short-circuiting.

        Parameters
        ----------
        progress : reporting.progress.ProgressReporter, optional
            Live 0-100% progress telemetry for the GUI (reporting/progress.py).
            ``None`` (the default) is a complete no-op — existing callers/tests
            that don't pass it see byte-identical behavior. When supplied,
            ``progress.start_stage(step.name, symbols_total=len(ctx.symbols))``
            is called immediately before each NON-skipped step runs, so the
            stage list the reporter was constructed with should be exactly
            ``[step.name for step in steps]`` (see ``main.run_once()``).
            ``symbols_total`` is sized off ``ctx.symbols`` for every step
            (0 before ``UniverseStep`` populates it, which is harmless since
            no step before that point ticks ``advance_symbol()`` anyway) --
            only ``AdvisoryEvalStep`` (pipeline/steps.py) actually calls
            ``advance_symbol()`` per symbol; every other step's stage slice
            just holds at its starting boundary.
        """
        # Stamped onto ctx (not passed as a second run() argument) so steps
        # needing per-symbol ticks -- currently only AdvisoryEvalStep -- can
        # reach it via ctx.progress without changing PipelineStep.run()'s
        # signature for every other step (see pipeline/context.py).
        ctx.progress = progress
        for step in self._steps:
            if step.should_skip(ctx):
                logger.debug(
                    "Pipeline step '%s' skipped (stopped=%s, reason=%s).",
                    step.name, ctx.stopped, ctx.stop_reason,
                )
                continue
            if progress is not None:
                progress.start_stage(step.name, symbols_total=len(ctx.symbols))
            step.run(ctx)


class AsyncPipelineRunner:
    """Runs an ordered list of async/sync steps against one RunContext."""

    def __init__(self, steps: List[Any]) -> None:
        self._steps = steps

    async def run(self, ctx: "RunContext", progress: Optional["ProgressReporter"] = None) -> None:
        import asyncio
        ctx.progress = progress
        for step in self._steps:
            if step.should_skip(ctx):
                logger.debug(
                    "Pipeline step '%s' skipped (stopped=%s, reason=%s).",
                    step.name, ctx.stopped, ctx.stop_reason,
                )
                continue
            if progress is not None:
                progress.start_stage(step.name, symbols_total=len(ctx.symbols))
            
            if asyncio.iscoroutinefunction(step.run):
                await step.run(ctx)
            else:
                await asyncio.to_thread(step.run, ctx)

