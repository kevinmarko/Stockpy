"""
InvestYo Quant Platform - Volatility Targeting Tests
=======================================================
Unit tests for sizing/vol_target.py: single-asset and portfolio-level
volatility-target position weighting.
"""

import math
import numpy as np
import pandas as pd
import pytest

from sizing.vol_target import volatility_target_weight, portfolio_vol_target


# =============================================================================
# volatility_target_weight
# =============================================================================
def test_volatility_target_weight_known_scenario():
    """realized_vol=0.20, target=0.10 -> weight = 0.5."""
    weight = volatility_target_weight(realized_vol=0.20, target_vol=0.10, max_leverage=2.0)
    assert math.isclose(weight, 0.5, rel_tol=1e-9)


def test_volatility_target_weight_caps_at_max_leverage():
    """Very low realized_vol would imply huge leverage -- must cap."""
    weight = volatility_target_weight(realized_vol=0.01, target_vol=0.10, max_leverage=2.0)
    assert weight == 2.0


def test_volatility_target_weight_zero_vol_saturates_at_cap():
    """Degenerate zero-vol case: no risk to size against, saturate at the cap
    rather than divide by zero."""
    weight = volatility_target_weight(realized_vol=0.0, target_vol=0.10, max_leverage=2.0)
    assert weight == 2.0


def test_volatility_target_weight_nan_input_returns_nan():
    weight = volatility_target_weight(realized_vol=float("nan"), target_vol=0.10)
    assert math.isnan(weight)


def test_volatility_target_weight_none_input_returns_nan():
    weight = volatility_target_weight(realized_vol=None, target_vol=0.10)
    assert math.isnan(weight)


def test_volatility_target_weight_never_negative():
    weight = volatility_target_weight(realized_vol=1000.0, target_vol=0.10, max_leverage=2.0)
    assert weight >= 0.0


# =============================================================================
# portfolio_vol_target
# =============================================================================
def test_portfolio_vol_target_scales_to_target():
    """A diagonal (uncorrelated) covariance matrix should scale positions so
    realized portfolio vol exactly hits target_vol (when not leverage-capped)."""
    symbols = ["A", "B"]
    # Two uncorrelated assets, vol=0.20 each.
    cov = pd.DataFrame(
        [[0.04, 0.0], [0.0, 0.04]],  # variance = vol^2 = 0.04
        index=symbols, columns=symbols,
    )
    positions = {"A": 1.0, "B": 1.0}
    scaled = portfolio_vol_target(positions, cov, target_vol=0.10, max_leverage=2.0)

    w = np.array([scaled["A"], scaled["B"]])
    realized_vol = math.sqrt(w @ cov.to_numpy() @ w)
    assert math.isclose(realized_vol, 0.10, rel_tol=1e-6)
    # Relative weights between symbols preserved (equal positions stay equal).
    assert math.isclose(scaled["A"], scaled["B"], rel_tol=1e-9)


def test_portfolio_vol_target_caps_at_max_leverage():
    symbols = ["A", "B"]
    # Very low vol -> would require huge leverage to hit target_vol.
    cov = pd.DataFrame([[0.0001, 0.0], [0.0, 0.0001]], index=symbols, columns=symbols)
    positions = {"A": 1.0, "B": 1.0}
    scaled = portfolio_vol_target(positions, cov, target_vol=0.10, max_leverage=2.0)
    w = np.array([scaled["A"], scaled["B"]])
    realized_vol = math.sqrt(w @ cov.to_numpy() @ w)
    # Capped scalar means realized vol falls short of target.
    assert realized_vol < 0.10
    assert math.isclose(scaled["A"], 2.0, rel_tol=1e-9)


def test_portfolio_vol_target_excludes_missing_symbols():
    """Symbols absent from cov_matrix get weight 0.0, not fabricated leverage."""
    cov = pd.DataFrame([[0.04]], index=["A"], columns=["A"])
    positions = {"A": 1.0, "B": 1.0}  # "B" has no covariance data
    scaled = portfolio_vol_target(positions, cov, target_vol=0.10, max_leverage=2.0)
    assert scaled["B"] == 0.0
    assert scaled["A"] > 0.0


def test_portfolio_vol_target_zero_positions_returns_zero():
    cov = pd.DataFrame([[0.04, 0.0], [0.0, 0.04]], index=["A", "B"], columns=["A", "B"])
    positions = {"A": 0.0, "B": 0.0}
    scaled = portfolio_vol_target(positions, cov, target_vol=0.10, max_leverage=2.0)
    assert scaled["A"] == 0.0
    assert scaled["B"] == 0.0
