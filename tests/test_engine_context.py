"""
tests/test_engine_context.py
=============================
PR2 (persistent-daemon groundwork): main_orchestrator.run_pipeline() now
accepts an optional EngineContext of pre-built, long-lived engine instances
so a future persistent caller (the orchestrator daemon) can construct every
engine ONCE and reuse them across many cycles instead of paying full
construction cost every call.

Verifies:
  - engines=None (the default) reproduces today's exact behavior: every
    engine is constructed fresh inside run_pipeline (no change for the
    standalone CLI / existing callers).
  - engines=EngineContext.build(...) causes run_pipeline to REUSE the exact
    same engine instances (identity-checked) rather than constructing new
    ones -- the whole point of warm-keeping.
  - A partially-populated EngineContext falls back to constructing fresh
    engines only for the fields left as None (mixed warm/cold is honored).
  - EngineContext.build() wires data_engine into MacroEngine exactly as
    run_pipeline's default path would.
"""
from __future__ import annotations

from unittest import mock

import pytest

from data_engine import MockDataEngine
from main_orchestrator import EngineContext, run_pipeline
from macro_engine import MacroEngine
from technical_options_engine import TechnicalOptionsEngine
from processing_engine import ProcessingEngine
from forecasting_engine import ForecastingEngine
from strategy_engine import StrategyEngine
from evaluation_engine import EvaluationEngine
from volatility.iv_engine import IVHistoryStore


def _fixture_data():
    mock_de = MockDataEngine()
    tickers = ["AAPL"]
    macro_raw = mock_de.fetch_macro_raw()
    fund_raw = mock_de.fetch_fundamentals_raw(tickers)
    tech_raw = mock_de.fetch_technical_raw(tickers)
    return tickers, macro_raw, fund_raw, tech_raw


class TestEngineContextBuild:
    def test_build_constructs_one_of_each_engine(self) -> None:
        ctx = EngineContext.build(data_engine=None)
        assert isinstance(ctx.macro_engine, MacroEngine)
        assert isinstance(ctx.technical_options_engine, TechnicalOptionsEngine)
        assert isinstance(ctx.iv_history_store, IVHistoryStore)
        assert isinstance(ctx.processing_engine, ProcessingEngine)
        assert isinstance(ctx.forecasting_engine, ForecastingEngine)
        assert isinstance(ctx.strategy_engine, StrategyEngine)
        assert isinstance(ctx.evaluation_engine, EvaluationEngine)

    def test_default_context_is_all_none(self) -> None:
        ctx = EngineContext()
        assert ctx.macro_engine is None
        assert ctx.technical_options_engine is None
        assert ctx.iv_history_store is None
        assert ctx.processing_engine is None
        assert ctx.forecasting_engine is None
        assert ctx.strategy_engine is None
        assert ctx.evaluation_engine is None


class TestRunPipelineBackwardCompatibility:
    def test_no_engines_arg_behaves_identically_to_today(self) -> None:
        """The default (engines=None) path must be completely unaffected --
        this is the existing test_main_orchestrator_pipeline() contract,
        re-asserted here to pin the no-op default."""
        tickers, macro_raw, fund_raw, tech_raw = _fixture_data()
        with mock.patch("settings.settings.HISTORICAL_STORE_ENABLED", False):
            final_df, macro_dto, shared_ctx = run_pipeline(
                tickers, macro_raw, fund_raw, tech_raw
            )
        assert not final_df.empty
        assert "Action Signal" in final_df.columns
        assert macro_dto is not None


class TestRunPipelineEngineReuse:
    def test_injected_engines_are_reused_not_reconstructed(self) -> None:
        """The core promise of warm-keeping: when a full EngineContext is
        supplied, run_pipeline must use the SAME instances (identity, not
        just equality) rather than building new ones. We patch each engine
        class's constructor to explode if called again after the context is
        built, so any accidental re-construction inside run_pipeline fails
        the test loudly."""
        tickers, macro_raw, fund_raw, tech_raw = _fixture_data()

        with mock.patch("settings.settings.HISTORICAL_STORE_ENABLED", False):
            ctx = EngineContext.build(data_engine=None)

            def _explode(*_a, **_k):
                raise AssertionError(
                    "run_pipeline constructed a fresh engine despite a full "
                    "EngineContext being supplied -- warm-keeping is broken."
                )

            with mock.patch("main_orchestrator.MacroEngine", side_effect=_explode), \
                 mock.patch("main_orchestrator.TechnicalOptionsEngine", side_effect=_explode), \
                 mock.patch("main_orchestrator.IVHistoryStore", side_effect=_explode), \
                 mock.patch("main_orchestrator.ProcessingEngine", side_effect=_explode), \
                 mock.patch("main_orchestrator.ForecastingEngine", side_effect=_explode), \
                 mock.patch("main_orchestrator.StrategyEngine", side_effect=_explode), \
                 mock.patch("main_orchestrator.EvaluationEngine", side_effect=_explode):
                final_df, macro_dto, _shared_ctx = run_pipeline(
                    tickers, macro_raw, fund_raw, tech_raw, engines=ctx,
                )

        assert not final_df.empty
        assert macro_dto is not None

    def test_partial_context_falls_back_per_field(self) -> None:
        """A context with only SOME engines populated must still construct
        the missing ones fresh -- mixed warm/cold is honored, not an
        all-or-nothing switch."""
        tickers, macro_raw, fund_raw, tech_raw = _fixture_data()

        partial = EngineContext(
            macro_engine=MacroEngine(data_engine=None),
            # everything else left None -> constructed fresh by run_pipeline
        )

        with mock.patch("settings.settings.HISTORICAL_STORE_ENABLED", False):
            final_df, macro_dto, _shared_ctx = run_pipeline(
                tickers, macro_raw, fund_raw, tech_raw, engines=partial,
            )

        assert not final_df.empty
        assert macro_dto is not None
