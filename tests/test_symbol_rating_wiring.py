"""
tests/test_symbol_rating_wiring.py
===================================
Wiring tests for PART 2 of the symbol-rating subsystem: the two per-cycle
write sites (``pipeline/production_steps.py::_record_symbol_ratings`` for the
orchestrator path, ``pipeline/steps.py::AdvisoryEvalStep.run`` for the
advisory path) that persist ``rating.symbol_rating.classify_tier``'s
GOOD/BAD verdict to ``rating.symbol_rating_store.SymbolRatingStore``.

Deliberately targets ``_record_symbol_ratings`` directly (same pattern as
``tests/test_production_steps_etf_transmission.py``) rather than the whole of
``StrategyEvalStep.run()``, which imports ``main_orchestrator`` and its full
heavy engine chain and has no existing dedicated wiring test suite of its own
(neither does the analogous CAP-EVENT AUDIT LOG block it mirrors) -- keeping
this suite runnable without yfinance/fredapi/statsmodels/tensorflow.

An AST-based structural check confirms the StrategyEvalStep.run() call site
still wraps ``_record_symbol_ratings(...)`` in a broad ``try/except`` --
CONSTRAINT #6 dead-letter resilience for the one call site too heavy to
invoke end-to-end here.

The AdvisoryEvalStep.run() path IS invoked end-to-end (it is lightweight --
no main_orchestrator import, see tests/test_pipeline_package.py's own
TestAdvisoryEvalStep for the precedent).
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest

import pipeline.production_steps as ps_mod
import rating.symbol_rating_store as rating_store_mod
from pipeline.context import RunContext
from pipeline.production_steps import _record_symbol_ratings
from pipeline.steps import AdvisoryEvalStep
from settings import settings


# ---------------------------------------------------------------------------
# Shared test doubles
# ---------------------------------------------------------------------------


class _FakeRatingStore:
    """Minimal stand-in for SymbolRatingStore -- records every
    record_ratings() call's arguments for inspection, optionally raising."""

    calls: List[Dict[str, Any]] = []

    def __init__(self, *, boom: bool = False, readonly: bool = False):
        self._boom = boom
        self.readonly = readonly

    def record_ratings(self, events, *, cycle_id=None):
        type(self).calls.append({"events": list(events), "cycle_id": cycle_id})
        if self._boom:
            raise RuntimeError("simulated DB write failure")


@pytest.fixture(autouse=True)
def _reset_fake_store_calls():
    _FakeRatingStore.calls = []
    yield
    _FakeRatingStore.calls = []


@dataclass
class _FakeSnapshot:
    positions: Dict[str, Any] = field(default_factory=dict)


def _make_ctx(**overrides: Any) -> RunContext:
    defaults: Dict[str, Any] = dict(
        force_account=False,
        started_at=datetime.now(timezone.utc),
        watchlist_file="watchlist.txt",
        fetch_account_snapshot_fn=lambda *a, **k: None,
        build_universe_fn=lambda snapshot: [],
        build_macro_dto_fn=lambda *a, **k: None,
        get_provider_fn=lambda *a, **k: None,
        fetch_bars_fn=lambda symbols, market: {},
        build_context_extras_fn=lambda symbols, bars, macro_dto: {},
        advisory_evaluate_fn=lambda *a, **k: None,
    )
    defaults.update(overrides)
    return RunContext(**defaults)


# ---------------------------------------------------------------------------
# Orchestrator path -- pipeline/production_steps.py::_record_symbol_ratings
# ---------------------------------------------------------------------------


def _dashboard_df(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class TestRecordSymbolRatingsOrchestratorPath:
    def test_derives_bad_tier_and_is_held_correctly(self, monkeypatch):
        monkeypatch.setattr(settings, "SYMBOL_RATING_ENABLED", True)
        monkeypatch.setattr(settings, "SYMBOL_RATING_BAD_SCORE_THRESHOLD", 35.0)
        monkeypatch.setattr(rating_store_mod, "SymbolRatingStore", _FakeRatingStore)

        df = _dashboard_df([
            {"Symbol": "AAPL", "Score": 20.0, "Action Signal": "RISK REDUCE", "Robinhood Shares": 5.0},
            {"Symbol": "MSFT", "Score": 60.0, "Action Signal": "BUY", "Robinhood Shares": 0.0},
        ])
        _record_symbol_ratings(df, "cycle-1")

        assert len(_FakeRatingStore.calls) == 1
        events = {e["symbol"]: e for e in _FakeRatingStore.calls[0]["events"]}
        assert events["AAPL"]["tier"] == "BAD"
        assert events["AAPL"]["is_held"] is True
        assert events["AAPL"]["score"] == pytest.approx(20.0)
        assert events["AAPL"]["action_signal"] == "RISK REDUCE"
        assert events["MSFT"]["tier"] == "GOOD"
        assert events["MSFT"]["is_held"] is False
        assert _FakeRatingStore.calls[0]["cycle_id"] == "cycle-1"

    def test_dead_lettered_row_missing_score_is_skipped(self, monkeypatch):
        """A row with no Score (never reached the 'results' stage this cycle)
        must not produce a fabricated rating -- CONSTRAINT #4."""
        monkeypatch.setattr(settings, "SYMBOL_RATING_ENABLED", True)
        monkeypatch.setattr(rating_store_mod, "SymbolRatingStore", _FakeRatingStore)

        df = _dashboard_df([
            {"Symbol": "AAPL", "Score": float("nan"), "Action Signal": None, "Robinhood Shares": 0.0},
            {"Symbol": "MSFT", "Score": 60.0, "Action Signal": "BUY", "Robinhood Shares": 0.0},
        ])
        _record_symbol_ratings(df, "cycle-1")

        symbols = {e["symbol"] for e in _FakeRatingStore.calls[0]["events"]}
        assert symbols == {"MSFT"}

    def test_disabled_flag_makes_zero_write_attempts(self, monkeypatch):
        monkeypatch.setattr(settings, "SYMBOL_RATING_ENABLED", False)

        def _boom(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("store must not be constructed when disabled")

        monkeypatch.setattr(rating_store_mod, "SymbolRatingStore", _boom)

        df = _dashboard_df([{"Symbol": "AAPL", "Score": 20.0, "Action Signal": "RISK REDUCE", "Robinhood Shares": 0.0}])
        _record_symbol_ratings(df, "cycle-1")  # must not raise, must not construct the store

    def test_empty_dashboard_df_is_a_no_op(self, monkeypatch):
        monkeypatch.setattr(settings, "SYMBOL_RATING_ENABLED", True)

        def _boom(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("store must not be constructed on an empty df")

        monkeypatch.setattr(rating_store_mod, "SymbolRatingStore", _boom)
        _record_symbol_ratings(pd.DataFrame(), "cycle-1")

    def test_store_failure_propagates_from_the_helper_itself(self, monkeypatch):
        """SymbolRatingStore.record_ratings' own documented contract is to
        raise on failure (mirrors CapAuditStore.record_cap_events) -- the
        dead-letter swallow lives at the StrategyEvalStep.run() call site,
        not inside this helper. See TestCallSiteDeadLetterSafety below for
        confirmation that the call site wraps this in a broad try/except."""
        monkeypatch.setattr(settings, "SYMBOL_RATING_ENABLED", True)
        monkeypatch.setattr(
            rating_store_mod, "SymbolRatingStore",
            lambda **kw: _FakeRatingStore(boom=True, **kw),
        )
        df = _dashboard_df([{"Symbol": "AAPL", "Score": 20.0, "Action Signal": "RISK REDUCE", "Robinhood Shares": 0.0}])
        with pytest.raises(RuntimeError):
            _record_symbol_ratings(df, "cycle-1")


class TestCallSiteDeadLetterSafety:
    def test_strategy_eval_step_wraps_the_call_in_a_broad_try_except(self):
        """AST guard: pipeline/production_steps.py's StrategyEvalStep.run()
        must call _record_symbol_ratings(...) from inside a try/except that
        catches (at least) `Exception` -- CONSTRAINT #6. Mirrors this
        codebase's existing AST-guard testing convention (e.g.
        tests/test_control_api.py's test_daemon_properties_are_never_called_as_methods,
        tests/test_runtime_flags.py's TestModuleIsADependencyFreeLeaf)."""
        source = textwrap.dedent(inspect.getsource(ps_mod.StrategyEvalStep.run))
        tree = ast.parse(source)

        found_call = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for stmt in ast.walk(node):
                    if (
                        isinstance(stmt, ast.Call)
                        and isinstance(stmt.func, ast.Name)
                        and stmt.func.id == "_record_symbol_ratings"
                    ):
                        found_call = True
                        # At least one handler must catch a broad exception type.
                        handler_names = {
                            h.type.id for h in node.handlers
                            if h.type is not None and isinstance(h.type, ast.Name)
                        }
                        assert "Exception" in handler_names or "BaseException" in handler_names, (
                            "_record_symbol_ratings call site must be wrapped in a "
                            "try/except Exception block (CONSTRAINT #6)."
                        )
        assert found_call, "StrategyEvalStep.run() no longer calls _record_symbol_ratings(...)"


# ---------------------------------------------------------------------------
# Advisory path -- pipeline/steps.py::AdvisoryEvalStep.run
# ---------------------------------------------------------------------------


@dataclass
class _FakeRecommendation:
    symbol: str
    action: str = "HOLD"
    conviction: float = 0.5
    data_quality: str = "OK"
    suggested_position_pct: float = 0.0
    key_indicators: Optional[Dict[str, float]] = None


class TestAdvisoryEvalStepSymbolRatingWrite:
    def _ctx(self, recs_by_symbol: Dict[str, _FakeRecommendation], held: Optional[Dict[str, Any]] = None) -> RunContext:
        def _evaluate(symbol: str, **_kw: Any) -> _FakeRecommendation:
            return recs_by_symbol[symbol]

        ctx = _make_ctx(advisory_evaluate_fn=_evaluate)
        ctx.symbols = list(recs_by_symbol.keys())
        ctx.snapshot = _FakeSnapshot(positions=held or {})
        ctx.market = object()
        ctx.macro_dto = object()
        ctx.context_extras = {}
        return ctx

    def test_derives_bad_tier_and_is_held_correctly(self, monkeypatch):
        monkeypatch.setattr(settings, "SYMBOL_RATING_ENABLED", True)
        monkeypatch.setattr(settings, "SYMBOL_RATING_BAD_SCORE_THRESHOLD", 35.0)
        monkeypatch.setattr(rating_store_mod, "SymbolRatingStore", _FakeRatingStore)

        recs = {
            "AAPL": _FakeRecommendation("AAPL", action="SELL", key_indicators={"score": 20.0}),
            "MSFT": _FakeRecommendation("MSFT", action="BUY", key_indicators={"score": 60.0}),
        }
        ctx = self._ctx(recs, held={"AAPL": object()})

        AdvisoryEvalStep().run(ctx)

        assert len(_FakeRatingStore.calls) == 1
        events = {e["symbol"]: e for e in _FakeRatingStore.calls[0]["events"]}
        assert events["AAPL"]["tier"] == "BAD"
        assert events["AAPL"]["is_held"] is True
        assert events["AAPL"]["action_signal"] == "SELL"
        assert events["MSFT"]["tier"] == "GOOD"
        assert events["MSFT"]["is_held"] is False

    def test_missing_or_nonfinite_score_is_skipped(self, monkeypatch):
        monkeypatch.setattr(settings, "SYMBOL_RATING_ENABLED", True)
        monkeypatch.setattr(rating_store_mod, "SymbolRatingStore", _FakeRatingStore)

        recs = {
            "AAPL": _FakeRecommendation("AAPL", key_indicators={"score": float("nan")}),
            "MSFT": _FakeRecommendation("MSFT", key_indicators=None),
            "GOOG": _FakeRecommendation("GOOG", key_indicators={"score": 60.0}),
        }
        ctx = self._ctx(recs)

        AdvisoryEvalStep().run(ctx)

        symbols = {e["symbol"] for e in _FakeRatingStore.calls[0]["events"]}
        assert symbols == {"GOOG"}

    def test_disabled_flag_makes_zero_write_attempts(self, monkeypatch):
        monkeypatch.setattr(settings, "SYMBOL_RATING_ENABLED", False)

        def _boom(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("store must not be constructed when disabled")

        monkeypatch.setattr(rating_store_mod, "SymbolRatingStore", _boom)

        recs = {"AAPL": _FakeRecommendation("AAPL", key_indicators={"score": 20.0})}
        ctx = self._ctx(recs)

        AdvisoryEvalStep().run(ctx)  # must not raise, must not construct the store
        assert len(ctx.recommendations) == 1  # the recommendation itself is unaffected

    def test_store_failure_never_raises_out_of_run(self, monkeypatch):
        """CONSTRAINT #6: a rating-store DB hiccup on the advisory path must
        never propagate out of AdvisoryEvalStep.run() -- the recommendations
        already assembled must survive intact."""
        monkeypatch.setattr(settings, "SYMBOL_RATING_ENABLED", True)
        monkeypatch.setattr(
            rating_store_mod, "SymbolRatingStore",
            lambda **kw: _FakeRatingStore(boom=True, **kw),
        )

        recs = {"AAPL": _FakeRecommendation("AAPL", key_indicators={"score": 20.0})}
        ctx = self._ctx(recs)

        AdvisoryEvalStep().run(ctx)  # must not raise

        assert len(ctx.recommendations) == 1
        assert ctx.recommendations[0].symbol == "AAPL"
        assert ctx.errors == []  # the write failure is logged, not surfaced as a symbol error
