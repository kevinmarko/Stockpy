"""
legacy/tests/test_symbol_rating_wiring.py
=========================================
The advisory-path half of ``tests/test_symbol_rating_wiring.py``:
``pipeline/steps.py::AdvisoryEvalStep.run``'s per-cycle symbol-rating write,
split out when ``pipeline/steps.py`` was archived with ``main.py`` in shrink
step 5.5. The daemon's only rating writer,
``pipeline/production_steps.py::_record_symbol_ratings``, stays tested in the
active file.

Keeps its original imports: restore ``pipeline/steps.py`` to run it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

import rating.symbol_rating_store as rating_store_mod
from pipeline.context import RunContext
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
