"""
InvestYo Quant Platform - LGBM Ranker SignalModule Tests
========================================================
Regression coverage for two latent bugs in ``signals/lgbm_ranker.py`` that
masked each other (the module was never registered, so its broken
``compute()`` was never exercised):

* **Bug A — never registered.** Every other signal module ends with
  ``global_registry.register(...)``; this one did not, so despite being
  imported in ``signals/__init__.py`` it never participated in aggregation and
  its ``settings.SIGNAL_WEIGHTS["lgbm_ranker"]`` weight was a dead key.
* **Bug B — invalid SignalOutput construction.** ``compute()`` passed ``name=``
  and ``weight=`` kwargs that do not exist on the ``SignalOutput`` dataclass
  (fields: ``score, confidence, explanation, meta_label_proba``); it would raise
  ``TypeError`` the instant it was called — which would have happened the moment
  a trained model was deployed and the module activated.

These tests pin: registration, registry/weights consistency, the rank→score
map, neutral behavior when a ticker has no rank (no fabricated exposure), and
that ``compute()`` returns a valid ``SignalOutput`` without raising.
"""

import dataclasses

import pandas as pd
import pytest

from signals.base import SignalContext, SignalModule, SignalOutput
from signals.registry import global_registry
from signals.lgbm_ranker import LGBMRankerSignal
from settings import settings


def _ctx_with_scores(scores: dict) -> SignalContext:
    """A SignalContext carrying only the lgbm_scores the ranker reads."""
    ctx = SignalContext.__new__(SignalContext)
    ctx.lgbm_scores = scores
    return ctx


# ---------------------------------------------------------------------------
# Bug A — registration
# ---------------------------------------------------------------------------
def test_lgbm_ranker_is_registered():
    names = set(global_registry.get_all().keys())
    assert "lgbm_ranker" in names, (
        "LGBMRankerSignal must auto-register (it ends signals/lgbm_ranker.py "
        "with global_registry.register(...))"
    )


def test_registry_and_weights_are_consistent():
    """No dead weight keys and no weightless modules — every registered module
    has a weight and every weight maps to a registered module."""
    reg = set(global_registry.get_all().keys())
    weights = set(settings.SIGNAL_WEIGHTS.keys())
    assert weights - reg == set(), f"dead weight keys with no module: {weights - reg}"
    assert reg - weights == set(), f"registered modules with no weight: {reg - weights}"


def test_lgbm_ranker_has_a_weight():
    assert settings.SIGNAL_WEIGHTS.get("lgbm_ranker") == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Bug B — SignalOutput construction must not raise, must be the right type
# ---------------------------------------------------------------------------
def test_compute_returns_valid_signal_output():
    out = LGBMRankerSignal().compute(pd.Series({"Symbol": "AAPL"}), _ctx_with_scores({"AAPL": 0.7}))
    assert isinstance(out, SignalOutput)
    # The fields the dataclass actually exposes — not the phantom name/weight.
    field_names = {f.name for f in dataclasses.fields(SignalOutput)}
    assert field_names == {"score", "confidence", "explanation", "meta_label_proba"}
    assert out.confidence == 1.0
    assert "rank" in out.explanation


def test_signal_output_rejects_phantom_kwargs():
    """Guard the exact regression: name=/weight= are NOT valid kwargs."""
    with pytest.raises(TypeError):
        SignalOutput(name="x", score=0.1, weight=0.1, explanation="e", confidence=1.0)


# ---------------------------------------------------------------------------
# rank → score mapping  (2*(rank-0.5), clipped to [-1, 1])
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rank,expected", [
    (1.0, 1.0),
    (0.9, 0.8),
    (0.5, 0.0),
    (0.25, -0.5),
    (0.0, -1.0),
])
def test_rank_maps_to_score(rank, expected):
    out = LGBMRankerSignal().compute(pd.Series({"Symbol": "AAPL"}), _ctx_with_scores({"AAPL": rank}))
    assert out.score == pytest.approx(expected)


def test_missing_ticker_is_neutral_not_fabricated():
    """A ticker absent from lgbm_scores defaults to rank 0.5 → score 0.0
    (CONSTRAINT #4 — no fabricated exposure)."""
    out = LGBMRankerSignal().compute(pd.Series({"Symbol": "ZZZ"}), _ctx_with_scores({"AAPL": 0.9}))
    assert out.score == 0.0


def test_nan_rank_is_neutral():
    out = LGBMRankerSignal().compute(pd.Series({"Symbol": "AAPL"}), _ctx_with_scores({"AAPL": float("nan")}))
    assert out.score == 0.0


def test_score_clipped_to_unit_band():
    # Even a degenerate out-of-range rank can't push the score past ±1.
    out_hi = LGBMRankerSignal().compute(pd.Series({"Symbol": "A"}), _ctx_with_scores({"A": 5.0}))
    out_lo = LGBMRankerSignal().compute(pd.Series({"Symbol": "A"}), _ctx_with_scores({"A": -5.0}))
    assert out_hi.score == 1.0
    assert out_lo.score == -1.0


# ---------------------------------------------------------------------------
# ABC conformance
# ---------------------------------------------------------------------------
def test_abc_conformance():
    mod = LGBMRankerSignal()
    assert isinstance(mod, SignalModule)
    assert mod.name == "lgbm_ranker"


# ---------------------------------------------------------------------------
# pre_compute degrades to neutral when no model is available (default state)
# ---------------------------------------------------------------------------
def test_pre_compute_neutral_when_no_model(monkeypatch):
    """With no trained model (the documented default), pre_compute must fill
    neutral 0.5 ranks for the whole universe and never raise."""
    import ml.lgbm_ranker as ml_lgbm

    def _boom(*a, **k):
        raise FileNotFoundError("no model")

    monkeypatch.setattr(ml_lgbm.LGBMCrossSectionalRanker, "load_latest", staticmethod(_boom))

    universe = pd.DataFrame(index=["AAPL", "MSFT"], data={"Close": [1.0, 2.0]})
    ctx = SignalContext.__new__(SignalContext)
    ctx.macro = None
    LGBMRankerSignal().pre_compute(universe, ctx)

    assert ctx.lgbm_scores == {"AAPL": 0.5, "MSFT": 0.5}
    # And the per-ticker compute therefore contributes exactly 0.0.
    out = LGBMRankerSignal().compute(pd.Series({"Symbol": "AAPL"}), ctx)
    assert out.score == 0.0


def test_pre_compute_empty_universe_is_noop():
    ctx = SignalContext.__new__(SignalContext)
    ctx.macro = None
    LGBMRankerSignal().pre_compute(pd.DataFrame(), ctx)
    assert ctx.lgbm_scores == {}
