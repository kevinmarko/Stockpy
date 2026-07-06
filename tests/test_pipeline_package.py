"""
tests/test_pipeline_package.py
===============================
Offline unit tests for the new ``pipeline/`` package (Part 2 of the
"modularize the orchestrators" refactor) that extracts main.py's
``run_once()`` function body into a command/mediator structure:

  - pipeline/context.py  -> RunContext (mutable per-cycle state carrier)
  - pipeline/base.py     -> PipelineStep ABC
  - pipeline/steps.py    -> AccountStep, UniverseStep, KillSwitchGateStep,
                            MacroStep, PrecomputeStep, AdvisoryEvalStep
  - pipeline/runner.py   -> PipelineRunner (ordered step executor)

All tests are fully offline: every injected dependency on RunContext is a
plain lambda/mock, and no real network/Robinhood/market-data call is ever
made. Mirrors the fixture/mocking style already established in
tests/test_run_once.py (see _make_snapshot / _make_recommendation there).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

from pipeline.base import PipelineStep
from pipeline.context import RunContext
from pipeline.runner import PipelineRunner
from pipeline.steps import (
    AccountStep,
    AdvisoryEvalStep,
    KillSwitchGateStep,
    MacroStep,
    PrecomputeStep,
    UniverseStep,
)
from data.robinhood_portfolio import AccountSnapshot


# ---------------------------------------------------------------------------
# Shared fixtures / factories (mirrors tests/test_run_once.py conventions)
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
    """Lightweight stand-in for engine.advisory.Recommendation, carrying only
    the attributes AdvisoryEvalStep.run()'s log line reads."""

    symbol: str
    action: str = "HOLD"
    conviction: float = 0.5
    data_quality: str = "OK"
    suggested_position_pct: float = 0.0


class _FakeSnapshot:
    """Minimal stand-in exposing only what AdvisoryEvalStep reads (.positions)."""

    def __init__(self, positions: Optional[Dict[str, Any]] = None) -> None:
        self.positions = positions or {}


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


# ---------------------------------------------------------------------------
# TestAccountStep
# ---------------------------------------------------------------------------

class TestAccountStep:
    """AccountStep must degrade to an empty AccountSnapshot (never raise) when
    fetch_account_snapshot_fn fails, and pass through the real snapshot object
    unchanged on the happy path."""

    def test_exception_degrades_to_empty_snapshot(self) -> None:
        def _boom(**_kw: Any) -> Any:
            raise RuntimeError("Robinhood login failed")

        ctx = _make_ctx(fetch_account_snapshot_fn=_boom)

        AccountStep().run(ctx)  # must not raise

        assert isinstance(ctx.snapshot, AccountSnapshot)
        assert ctx.snapshot.positions == {}
        assert ctx.snapshot.total_equity == 0.0
        assert ctx.snapshot.buying_power == 0.0
        assert ctx.snapshot.total_dividends == 0.0

    def test_happy_path_passes_through_exact_snapshot(self) -> None:
        real_snapshot = AccountSnapshot(
            positions={"AAPL": object()},
            buying_power=1000.0,
            total_equity=5000.0,
            total_dividends=12.5,
            fetched_at=datetime.now(timezone.utc),
        )
        ctx = _make_ctx(fetch_account_snapshot_fn=lambda **_kw: real_snapshot)

        AccountStep().run(ctx)

        assert ctx.snapshot is real_snapshot


# ---------------------------------------------------------------------------
# TestUniverseStep
# ---------------------------------------------------------------------------

class TestUniverseStep:
    """UniverseStep must stop the pipeline with stop_reason='empty_universe'
    when the universe comes back empty, without appending to ctx.errors, and
    otherwise just populate ctx.symbols."""

    def test_empty_universe_stops_pipeline(self) -> None:
        ctx = _make_ctx(build_universe_fn=lambda snapshot: [])
        ctx.snapshot = AccountSnapshot(
            positions={}, buying_power=0.0, total_equity=0.0,
            total_dividends=0.0, fetched_at=datetime.now(timezone.utc),
        )

        UniverseStep().run(ctx)

        assert ctx.stopped is True
        assert ctx.stop_reason == "empty_universe"
        assert ctx.errors == []

    def test_nonempty_universe_populates_symbols(self) -> None:
        ctx = _make_ctx(build_universe_fn=lambda snapshot: ["AAPL", "MSFT"])
        ctx.snapshot = AccountSnapshot(
            positions={}, buying_power=0.0, total_equity=0.0,
            total_dividends=0.0, fetched_at=datetime.now(timezone.utc),
        )

        UniverseStep().run(ctx)

        assert ctx.symbols == ["AAPL", "MSFT"]
        assert ctx.stopped is False


# ---------------------------------------------------------------------------
# TestKillSwitchGateStep
# ---------------------------------------------------------------------------

class TestKillSwitchGateStep:
    """KillSwitchGateStep must stop the pipeline (stop_reason='kill_switch')
    and record exactly one 'AdvisoryPaused' error when the sentinel is active,
    and be a complete no-op when it is not."""

    class _FakeActiveKillSwitch:
        def is_active(self) -> bool:
            return True

        def reason(self) -> str:
            return "manual pause for maintenance"

    class _FakeInactiveKillSwitch:
        def is_active(self) -> bool:
            return False

        def reason(self) -> str:
            return ""

    def test_active_kill_switch_stops_pipeline_and_records_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "pipeline.steps.GlobalKillSwitch", self._FakeActiveKillSwitch
        )
        ctx = _make_ctx()
        ctx.symbols = ["AAPL", "MSFT"]

        KillSwitchGateStep().run(ctx)

        assert ctx.stopped is True
        assert ctx.stop_reason == "kill_switch"
        assert len(ctx.errors) == 1
        err = ctx.errors[0]
        assert err["stage"] == "kill_switch_gate"
        assert err["error_type"] == "AdvisoryPaused"

    def test_inactive_kill_switch_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "pipeline.steps.GlobalKillSwitch", self._FakeInactiveKillSwitch
        )
        ctx = _make_ctx()
        ctx.symbols = ["AAPL", "MSFT"]

        KillSwitchGateStep().run(ctx)

        assert ctx.stopped is False
        assert ctx.errors == []


# ---------------------------------------------------------------------------
# TestMacroStep
# ---------------------------------------------------------------------------

class TestMacroStep:
    """MacroStep must set ctx.macro_dto to exactly what build_macro_dto_fn
    returns, and must not raise even though it attempts (and dead-letter
    swallows) the meta-labeler bootstrap import/registration."""

    def test_macro_dto_set_to_sentinel(self) -> None:
        sentinel = object()
        ctx = _make_ctx(build_macro_dto_fn=lambda: sentinel)

        MacroStep().run(ctx)  # must not raise regardless of ml.meta_bootstrap availability

        assert ctx.macro_dto is sentinel


# ---------------------------------------------------------------------------
# TestPrecomputeStep
# ---------------------------------------------------------------------------

class TestPrecomputeStep:
    """PrecomputeStep must wire ctx.market/bars_dict/context_extras to exactly
    what the three injected callables return, and call fetch_bars_fn /
    build_context_extras_fn with the documented positional argument shapes."""

    def test_wires_market_bars_and_context_extras(self) -> None:
        market_sentinel = object()
        bars_sentinel = {"AAPL": object()}
        extras_sentinel = {"xsec_percentile_ranks": {"AAPL": 0.5}}

        fetch_bars_calls: List[Any] = []
        build_extras_calls: List[Any] = []

        def _fetch_bars(symbols: List[str], market: Any) -> Dict[str, Any]:
            fetch_bars_calls.append((symbols, market))
            return bars_sentinel

        def _build_extras(symbols: List[str], bars: Dict[str, Any], macro_dto: Any) -> Dict[str, Any]:
            build_extras_calls.append((symbols, bars, macro_dto))
            return extras_sentinel

        macro_sentinel = object()
        ctx = _make_ctx(
            get_provider_fn=lambda: market_sentinel,
            fetch_bars_fn=_fetch_bars,
            build_context_extras_fn=_build_extras,
        )
        ctx.symbols = ["AAPL", "MSFT"]
        ctx.macro_dto = macro_sentinel

        PrecomputeStep().run(ctx)

        assert ctx.market is market_sentinel
        assert ctx.bars_dict is bars_sentinel
        assert ctx.context_extras is extras_sentinel

        assert len(fetch_bars_calls) == 1
        called_symbols, called_market = fetch_bars_calls[0]
        assert called_symbols == ["AAPL", "MSFT"]
        assert called_market is market_sentinel

        assert len(build_extras_calls) == 1
        called_symbols2, called_bars, called_macro = build_extras_calls[0]
        assert called_symbols2 == ["AAPL", "MSFT"]
        assert called_bars is bars_sentinel
        assert called_macro is macro_sentinel


# ---------------------------------------------------------------------------
# TestAdvisoryEvalStep
# ---------------------------------------------------------------------------

class TestAdvisoryEvalStep:
    """AdvisoryEvalStep must dead-letter per-symbol failures (never abort the
    run) and this must hold true regardless of ADVISORY_MAX_CONCURRENCY —
    exercise both the sequential (workers=1) and the parallel (workers>1)
    code paths."""

    def _make_ctx_for_eval(self) -> RunContext:
        def _evaluate(symbol: str, **_kw: Any) -> _FakeRecommendation:
            if symbol == "MSFT":
                raise RuntimeError("Simulated network error")
            return _FakeRecommendation(symbol=symbol, action="BUY")

        ctx = _make_ctx(advisory_evaluate_fn=_evaluate)
        ctx.symbols = ["AAPL", "MSFT"]
        ctx.snapshot = _FakeSnapshot(positions={})
        ctx.market = object()
        ctx.macro_dto = object()
        ctx.context_extras = {}
        return ctx

    def test_sequential_path_dead_letters_failing_symbol(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pipeline.steps.settings.ADVISORY_MAX_CONCURRENCY", 1, raising=False)
        ctx = self._make_ctx_for_eval()

        AdvisoryEvalStep().run(ctx)

        assert len(ctx.recommendations) == 1
        assert ctx.recommendations[0].symbol == "AAPL"
        assert len(ctx.errors) == 1
        assert ctx.errors[0]["symbol"] == "MSFT"
        assert ctx.errors[0]["stage"] == "advisory_evaluate"

    def test_parallel_path_dead_letters_failing_symbol(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pipeline.steps.settings.ADVISORY_MAX_CONCURRENCY", 8, raising=False)
        ctx = self._make_ctx_for_eval()

        AdvisoryEvalStep().run(ctx)

        assert len(ctx.recommendations) == 1
        assert ctx.recommendations[0].symbol == "AAPL"
        assert len(ctx.errors) == 1
        assert ctx.errors[0]["symbol"] == "MSFT"
        assert ctx.errors[0]["stage"] == "advisory_evaluate"
