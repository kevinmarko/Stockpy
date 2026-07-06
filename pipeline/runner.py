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
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from pipeline.base import PipelineStep
    from pipeline.context import RunContext

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Runs an ordered list of steps against one RunContext."""

    def __init__(self, steps: List["PipelineStep"]) -> None:
        self._steps = steps

    def run(self, ctx: "RunContext") -> None:
        """Execute each step in order, honoring should_skip() short-circuiting."""
        for step in self._steps:
            if step.should_skip(ctx):
                logger.debug(
                    "Pipeline step '%s' skipped (stopped=%s, reason=%s).",
                    step.name, ctx.stopped, ctx.stop_reason,
                )
                continue
            step.run(ctx)
