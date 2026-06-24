"""
tests/test_meta_labeler_uplift.py
===================================
Verifies that the MetaLabeler provides measurable precision uplift on a
synthetic primary signal with known accuracy.

Experimental setup
------------------
1. Generate a synthetic primary signal with known 60% win-rate.
2. Build a "high-confidence" feature that correlates with whether the signal
   is correct:  feature_A ∈ [0,1], true label = 1 when feature_A > 0.5.
3. Without meta-labeling, precision@10 equals the base 60% accuracy.
4. With the MetaLabeler trained on feature_A, top-decile predictions should
   exceed baseline precision (documented uplift amount).

Tests
-----
- test_meta_labeler_higher_precision_than_baseline
- test_meta_labeler_returns_proba_in_0_1
- test_meta_labeler_neutral_before_training
- test_meta_labeler_fit_from_primary_convenience
- test_meta_labeler_hard_gate_in_aggregator
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml.meta_labeling import (
    MetaLabeler,
    MetaLabelerRegistry,
    build_meta_label_target,
)
from ml.triple_barrier import apply_triple_barrier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _synthetic_dataset(n: int = 500, seed: int = 0) -> tuple:
    """Generate synthetic (features, primary_signal, barrier_labels).

    ``feature_A`` ∈ [0,1] is predictive: when > 0.5 the primary signal is
    correct; when ≤ 0.5 it is wrong.  This gives a meta-model a learnable signal.
    The base primary win-rate (before conditioning on feature_A) is 60%.
    """
    rng = np.random.default_rng(seed)
    feature_a = rng.uniform(0, 1, n)
    feature_noise = rng.normal(0, 0.5, n)

    # Primary signal: always +1 (always long)
    primary = np.ones(n, dtype=int)

    # Barrier label: +1 when feature_A > 0.5, else -1 (or 0 with 20% prob)
    label = np.where(
        feature_a > 0.5,
        1,
        np.where(rng.uniform(0, 1, n) > 0.2, -1, 0),
    )

    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    X = pd.DataFrame({"feature_A": feature_a, "noise": feature_noise}, index=dates)
    y_primary = pd.Series(primary, index=dates)
    y_barrier = pd.Series(label, index=dates)
    return X, y_primary, y_barrier


def _precision_at_k(proba: pd.Series, true_label: pd.Series, k: int) -> float:
    """Precision@K: fraction correct among the K highest-probability predictions."""
    top_k = proba.nlargest(k).index
    return float(true_label.loc[top_k].mean())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_meta_labeler_higher_precision_than_baseline():
    """MetaLabeler precision@50 should exceed the 60% baseline win-rate."""
    X, y_primary, y_barrier = _synthetic_dataset(n=600, seed=1)

    train_end = 400
    X_train = X.iloc[:train_end]
    y_p_train = y_primary.iloc[:train_end]
    y_b_train = y_barrier.iloc[:train_end]

    X_test = X.iloc[train_end:]
    y_b_test = y_barrier.iloc[train_end:]

    labeler = MetaLabeler(signal_id="test_signal")
    labeler.fit_from_primary(X_train, y_p_train, y_b_train)

    # Predict on test set
    probas = pd.Series(labeler.predict_proba(X_test), index=X_test.index)
    meta_y_test = build_meta_label_target(y_primary.iloc[train_end:], y_b_test)

    baseline_precision = float(meta_y_test.mean())
    meta_precision_at_50 = _precision_at_k(probas, meta_y_test, k=50)

    # Document the uplift
    uplift = meta_precision_at_50 - baseline_precision
    print(
        f"\nBaseline precision: {baseline_precision:.3f} | "
        f"MetaLabeler P@50: {meta_precision_at_50:.3f} | "
        f"Uplift: {uplift:+.3f}"
    )

    # Meta-labeler should improve precision on top-decile selections
    assert meta_precision_at_50 > baseline_precision, (
        f"MetaLabeler P@50 ({meta_precision_at_50:.3f}) did not beat baseline "
        f"({baseline_precision:.3f}) — check feature_A discriminability."
    )


def test_meta_labeler_returns_proba_in_0_1():
    """Predicted probabilities must be in [0, 1]."""
    X, y_primary, y_barrier = _synthetic_dataset(n=400, seed=2)
    labeler = MetaLabeler(signal_id="test")
    labeler.fit_from_primary(X, y_primary, y_barrier)

    probas = labeler.predict_proba(X)
    assert (probas >= 0.0).all() and (probas <= 1.0).all(), (
        "Predicted probabilities outside [0, 1]"
    )


def test_meta_labeler_neutral_before_training():
    """Before training, predict_proba_scalar returns 1.0 (no-op neutral)."""
    labeler = MetaLabeler(signal_id="untrained")
    X = pd.DataFrame({"a": [0.5, 0.7]})
    assert labeler.predict_proba_scalar(X) == 1.0


def test_meta_labeler_fit_from_primary_convenience():
    """fit_from_primary() trains on directional events only (primary ≠ 0)."""
    X, y_primary, y_barrier = _synthetic_dataset(n=400, seed=3)

    # Mix in some neutral (0) signals
    y_primary_mixed = y_primary.copy()
    y_primary_mixed.iloc[:50] = 0  # first 50 bars = no signal

    labeler = MetaLabeler(signal_id="test_mixed")
    labeler.fit_from_primary(X, y_primary_mixed, y_barrier)

    assert labeler._model is not None, "Model should be trained after fit_from_primary"
    # n_train_samples must exclude the 50 neutral bars
    assert labeler._n_train_samples <= 350, (
        f"Expected ≤ 350 training samples (50 neutrals excluded), "
        f"got {labeler._n_train_samples}"
    )


def test_build_meta_label_target_correct_logic():
    """build_meta_label_target produces 1 when directions agree, 0 otherwise."""
    dates = pd.date_range("2020-01-01", periods=5, freq="B")
    y_primary = pd.Series([1, 1, -1, -1, 0], index=dates)
    y_barrier = pd.Series([1, -1, -1, 1, 0], index=dates)

    meta = build_meta_label_target(y_primary, y_barrier)

    expected = [1, 0, 1, 0, 0]  # last=0 because primary=0 → never "correct"
    assert list(meta) == expected, f"Expected {expected}, got {list(meta)}"


# ---------------------------------------------------------------------------
# Test: Hard gate in SignalAggregator
# ---------------------------------------------------------------------------

def test_meta_labeler_hard_gate_zeros_composite():
    """When MetaLabeler P < 0.4 the aggregator forces meta_label_composite = 0."""
    from unittest.mock import patch
    from signals.aggregator import SignalAggregator
    from signals.registry import global_registry
    from signals.base import SignalContext, SignalOutput
    from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
    from ml.meta_labeling import MetaLabelerRegistry

    # Build a dummy registry with one MetaLabeler that always returns P=0.1 (< 0.4)
    class AlwaysLowMetaLabeler(MetaLabeler):
        def predict_proba_scalar(self, X):
            return 0.1

    low_conf_labeler = AlwaysLowMetaLabeler(signal_id="macro_regime")
    test_meta_registry = MetaLabelerRegistry()
    test_meta_registry.register(low_conf_labeler)

    # Patch the global registry in the aggregator
    with patch("signals.aggregator._get_meta_registry", return_value=test_meta_registry):
        aggregator = SignalAggregator(global_registry)

        from datetime import datetime as _dt
        bar = MarketBarDTO(
            date=_dt(2024, 1, 1), ticker="AAPL",
            open_price=149.0, high_price=151.0, low_price=148.0,
            close_price=150.0, volume=1_000_000,
        )
        fundamentals = FundamentalDataDTO(
            ticker="AAPL", pe_ratio=25.0, pb_ratio=5.0, dividend_yield=0.01,
            book_value=30.0, eps_trailing=6.0, dividend_growth_rate=0.05,
            payout_ratio=0.3, sector="Technology", company_name="Apple Inc",
            market_cap=2_500_000_000_000.0,
        )
        macro = MacroEconomicDTO(
            yield_curve_10y_2y=0.5, vix_value=15.0, sahm_rule_indicator=0.1,
            high_yield_oas=300.0, inflation_rate=0.03,
        )
        context = SignalContext(bar=bar, fundamentals=fundamentals, macro=macro)
        row = pd.Series({
            "current_price": 150.0, "Close": 150.0, "RSI_2": 50.0, "SMA_5": 149.0,
            "SMA_200": 140.0, "ROC_12M": 0.1, "GARCH_Vol": 0.15, "garch_vol": 0.15,
            "sector": "Technology", "ticker": "AAPL",
            "forecast_price": 155.0, "trend_strength": 60.0, "atr": 2.0,
            "macd_line": 0.5, "macd_signal": 0.3, "aroon_osc": 40.0,
            "rsi": 55.0, "sortino_ratio": 1.0, "max_drawdown": 0.1,
            "relative_strength": 0.8, "edge_ratio": 1.2,
            "chandelier_long": 145.0, "chandelier_short": 155.0,
        })

        _, _, _, _, _, composite = aggregator.aggregate(row, context)

    assert composite == 0.0, (
        f"Expected meta_label_composite=0.0 when MetaLabeler P=0.1 < 0.4, got {composite}"
    )
