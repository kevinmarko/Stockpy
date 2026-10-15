"""
tests/test_pipeline_package.py
===============================
Offline unit tests for the ``pipeline/`` package's shared core:

  - pipeline/context.py  -> RunContext (mutable per-cycle state carrier)
  - pipeline/base.py     -> PipelineStep ABC
  - pipeline/runner.py   -> PipelineRunner (ordered step executor)

The ``pipeline/steps.py`` step classes (main.py's ``run_once()`` steps) and
their tests were archived to ``legacy/`` in shrink step 5.5; the daemon's steps
live in ``pipeline/production_steps.py``.

All tests are fully offline: every injected dependency on RunContext is a
plain lambda/mock, and no real network/Robinhood/market-data call is ever
made.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict

from pipeline.base import PipelineStep
from pipeline.context import RunContext
from pipeline.runner import PipelineRunner


# ---------------------------------------------------------------------------
# Shared fixtures / factories
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


@dataclass
class _FakeRecommendation:
    """Lightweight stand-in for engine.advisory.Recommendation."""

    symbol: str
    action: str = "HOLD"
    conviction: float = 0.5
    data_quality: str = "OK"
    suggested_position_pct: float = 0.0


# ---------------------------------------------------------------------------
# TestRunContext — defaults + mutable-default-dataclass-field regression
# ---------------------------------------------------------------------------

class TestRunContext:
    """Pins RunContext's default values and guards against the classic
    shared-mutable-default dataclass bug (list/dict defaults MUST use
    field(default_factory=...), never a bare mutable literal)."""

    def test_defaults(self) -> None:
        ctx = _make_ctx()
        assert ctx.snapshot is None
        assert ctx.symbols == []
        assert ctx.macro_dto is None
        assert ctx.market is None
        assert ctx.bars_dict == {}
        assert ctx.context_extras == {}
        assert ctx.recommendations == []
        assert ctx.errors == []
        assert ctx.stopped is False
        assert ctx.stop_reason is None

    def test_required_fields_are_stored(self) -> None:
        started = datetime.now(timezone.utc)
        ctx = _make_ctx(force_account=True, started_at=started, watchlist_file="custom.txt")
        assert ctx.force_account is True
        assert ctx.started_at is started
        assert ctx.watchlist_file == "custom.txt"

    def test_mutating_one_instance_list_field_does_not_leak_to_another(self) -> None:
        ctx1 = _make_ctx()
        ctx2 = _make_ctx()

        ctx1.errors.append({"symbol": "AAPL"})
        ctx1.symbols.append("AAPL")
        ctx1.recommendations.append(_FakeRecommendation("AAPL"))

        assert ctx2.errors == []
        assert ctx2.symbols == []
        assert ctx2.recommendations == []
        # And confirm ctx1 actually did get mutated (sanity check on the test itself).
        assert ctx1.errors == [{"symbol": "AAPL"}]
        assert ctx1.symbols == ["AAPL"]
        assert len(ctx1.recommendations) == 1

    def test_mutating_one_instance_dict_field_does_not_leak_to_another(self) -> None:
        ctx1 = _make_ctx()
        ctx2 = _make_ctx()

        ctx1.bars_dict["AAPL"] = object()
        ctx1.context_extras["xsec_percentile_ranks"] = {"AAPL": 0.9}

        assert ctx2.bars_dict == {}
        assert ctx2.context_extras == {}
        assert ctx1.bars_dict != {}
        assert ctx1.context_extras != {}


# ---------------------------------------------------------------------------
# TestPipelineRunner — ordering + stop-short-circuit semantics
# ---------------------------------------------------------------------------

class _RecordingStep(PipelineStep):
    """Appends its own name to ctx._log (a plain list attached ad-hoc to the
    RunContext instance) whenever run() executes."""

    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, ctx: RunContext) -> None:
        ctx._log.append(self.name)  # type: ignore[attr-defined]


class _StoppingStep(PipelineStep):
    """Sets ctx.stopped=True (and a distinguishing stop_reason) when run."""

    name = "stopper"

    def run(self, ctx: RunContext) -> None:
        ctx._log.append(self.name)  # type: ignore[attr-defined]
        ctx.stopped = True
        ctx.stop_reason = "stopped_by_test"


class TestPipelineRunner:
    """PipelineRunner must run steps strictly in list order, and once any step
    sets ctx.stopped=True, every LATER step must be skipped entirely (never
    have .run() invoked) via the default should_skip(ctx) -> ctx.stopped."""

    def _ctx_with_log(self) -> RunContext:
        ctx = _make_ctx()
        ctx._log = []  # type: ignore[attr-defined]
        return ctx

    def test_runs_steps_in_order(self) -> None:
        ctx = self._ctx_with_log()
        steps = [_RecordingStep("a"), _RecordingStep("b"), _RecordingStep("c")]
        PipelineRunner(steps).run(ctx)
        assert ctx._log == ["a", "b", "c"]  # type: ignore[attr-defined]

    def test_steps_after_stop_are_skipped(self) -> None:
        ctx = self._ctx_with_log()
        steps = [
            _RecordingStep("before"),
            _StoppingStep(),
            _RecordingStep("after_1"),
            _RecordingStep("after_2"),
        ]
        PipelineRunner(steps).run(ctx)
        assert ctx._log == ["before", "stopper"]  # type: ignore[attr-defined]
        assert ctx.stopped is True
        assert ctx.stop_reason == "stopped_by_test"

    def test_step_before_stop_runs_normally(self) -> None:
        ctx = self._ctx_with_log()
        steps = [_RecordingStep("first"), _StoppingStep(), _RecordingStep("never")]
        PipelineRunner(steps).run(ctx)
        assert "first" in ctx._log  # type: ignore[attr-defined]
        assert "never" not in ctx._log  # type: ignore[attr-defined]
