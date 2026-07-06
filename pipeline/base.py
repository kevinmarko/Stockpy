"""Abstract base class for one stage of the run_once() advisory pipeline.

Each concrete step (see ``pipeline/steps.py``) implements ``run(ctx)`` and is
invoked in a fixed order by ``pipeline.runner.PipelineRunner``. A step signals
"stop the pipeline here" by setting ``ctx.stopped = True`` (and optionally
``ctx.stop_reason``); every step's default ``should_skip`` then causes all
LATER steps to be skipped without needing individual per-step overrides.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline.context import RunContext


class PipelineStep(ABC):
    """One named stage of the advisory run_once() pipeline."""

    name: str = "step"

    def should_skip(self, ctx: "RunContext") -> bool:
        """Return True to skip this step entirely.

        Default: skip once any earlier step has already set ``ctx.stopped``.
        Concrete steps needing an ADDITIONAL skip condition should call
        ``super().should_skip(ctx)`` first and OR it with their own check.
        """
        return ctx.stopped

    @abstractmethod
    def run(self, ctx: "RunContext") -> None:
        """Execute this stage, mutating ``ctx`` in place."""
        raise NotImplementedError
