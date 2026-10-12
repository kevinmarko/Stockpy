"""
legacy/tests/test_progress_emission.py
======================================
The ``main.run_once()`` / ``pipeline/steps.py::AdvisoryEvalStep`` halves of
``tests/test_progress_emission.py``, split out when ``main.py`` and
``pipeline/steps.py`` were archived in shrink step 5.5. The daemon's advisory
loop (``AdvisoryOverlayStep``), ``PipelineRunner`` and ``main_orchestrator``
progress checks stay in the active file.

Keeps its original imports: restore ``main.py`` and ``pipeline/steps.py`` to run it.
"""
from __future__ import annotations

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

    @pytest.fixture(autouse=True)
    def _isolate_watchlist_file(self, monkeypatch, tmp_path):
        """Point WATCHLIST_FILE at a nonexistent tmp path so this
        class's universe-size assertions (``_N_TICKERS``) are deterministic
        regardless of whether a real watchlist.txt happens to exist in the
        repo root -- mirrors tests/test_pipeline_smoke.py's identical
        ``_isolate_watchlist_file`` fixture. These tests intend to exercise
        only the mocked snapshot positions / WATCHLIST env var, never
        whatever real watchlist.txt file a given checkout has on disk.
        Patched on pipeline.advisory_inputs (where load_watchlist() reads it
        since step 5.0), not on main's re-export, which would not reach it.
        """
        import pipeline.advisory_inputs as ai
        monkeypatch.setattr(ai, "WATCHLIST_FILE", str(tmp_path / "nonexistent_watchlist.txt"))

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
        monkeypatch.setattr(_settings, "WATCHLIST", _WATCHLIST)
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
        monkeypatch.setattr(_settings, "WATCHLIST", _WATCHLIST)
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
        monkeypatch.setattr(_settings, "WATCHLIST", _WATCHLIST)
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
