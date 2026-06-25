"""
InvestYo Quant Platform - Fractional Kelly Sizing Tests
==========================================================
Unit tests for sizing/kelly.py: fractional_kelly known scenarios and
estimate_win_rate_and_payoff's trade-history estimation.
"""

import math
import pandas as pd
import pytest

from sizing.kelly import fractional_kelly, estimate_win_rate_and_payoff, MIN_TRADES_REQUIRED


# =============================================================================
# fractional_kelly: known scenarios
# =============================================================================
def test_fractional_kelly_p55_b2_does_not_cap():
    """p=0.55, b=2 -> full Kelly = 0.325, half = 0.1625, cap (0.20) doesn't bind."""
    f_star = (0.55 * 2 - (1 - 0.55)) / 2
    assert math.isclose(f_star, 0.325, rel_tol=1e-9)

    half = fractional_kelly(p=0.55, b=2.0, fraction=0.5, cap=0.20)
    assert math.isclose(half, 0.1625, rel_tol=1e-9)

    full = fractional_kelly(p=0.55, b=2.0, fraction=1.0, cap=1.0)
    assert math.isclose(full, 0.325, rel_tol=1e-9)


def test_fractional_kelly_p70_b3_is_capped():
    """p=0.7, b=3 -> full Kelly = 0.6, half = 0.3, capped to 0.20."""
    f_star = (0.7 * 3 - (1 - 0.7)) / 3
    assert math.isclose(f_star, 0.6, rel_tol=1e-9)

    half = fractional_kelly(p=0.7, b=3.0, fraction=0.5, cap=0.20)
    assert math.isclose(half, 0.20, rel_tol=1e-9)


def test_fractional_kelly_returns_nan_on_undefined_inputs():
    assert math.isnan(fractional_kelly(p=float("nan"), b=2.0))
    assert math.isnan(fractional_kelly(p=0.55, b=float("nan")))
    assert math.isnan(fractional_kelly(p=None, b=2.0))


def test_fractional_kelly_non_positive_payoff_returns_zero():
    assert fractional_kelly(p=0.6, b=0.0) == 0.0
    assert fractional_kelly(p=0.6, b=-1.0) == 0.0


def test_fractional_kelly_never_negative():
    """A losing edge (p*b < 1-p) must floor at 0.0, not go negative."""
    result = fractional_kelly(p=0.2, b=1.0, fraction=1.0, cap=1.0)
    assert result == 0.0


# =============================================================================
# estimate_win_rate_and_payoff: trade-history estimation
# =============================================================================
def _make_closed_trades(n_wins: int, n_losses: int, win_ret: float = 0.10, loss_ret: float = -0.05) -> pd.DataFrame:
    rows = []
    ts = pd.Timestamp("2024-01-01")
    for i in range(n_wins):
        entry = 100.0
        exit_p = entry * (1 + win_ret)
        rows.append({"entry_price": entry, "exit_price": exit_p, "side": "long",
                     "exit_ts": ts + pd.Timedelta(days=i)})
    for i in range(n_losses):
        entry = 100.0
        exit_p = entry * (1 + loss_ret)
        rows.append({"entry_price": entry, "exit_price": exit_p, "side": "long",
                     "exit_ts": ts + pd.Timedelta(days=n_wins + i)})
    return pd.DataFrame(rows)


def test_estimate_win_rate_and_payoff_happy_path():
    """20 wins @ +10%, 20 losses @ -5% -> p=0.5, b=2.0 (10/5), n=40."""
    df = _make_closed_trades(n_wins=20, n_losses=20, win_ret=0.10, loss_ret=-0.05)
    p, b, n = estimate_win_rate_and_payoff(df, lookback_trades=100)
    assert n == 40
    assert math.isclose(p, 0.5, rel_tol=1e-9)
    assert math.isclose(b, 2.0, rel_tol=1e-9)


def test_estimate_win_rate_and_payoff_respects_lookback_window():
    """60 trades available, lookback=30 -> only the most recent 30 are used."""
    df = _make_closed_trades(n_wins=50, n_losses=10, win_ret=0.10, loss_ret=-0.05)
    p, b, n = estimate_win_rate_and_payoff(df, lookback_trades=30)
    assert n == 30
    # The most recent 30 rows (by exit_ts) are the last 10 wins + all 10 losses... actually
    # the tail(30) of a 60-row frame (50 wins then 10 losses by construction) is the last
    # 20 wins + 10 losses.
    assert math.isclose(p, 20.0 / 30.0, rel_tol=1e-9)


def test_estimate_win_rate_and_payoff_insufficient_history_returns_nan():
    """Fewer than MIN_TRADES_REQUIRED (30) closed trades -> (NaN, NaN, n)."""
    df = _make_closed_trades(n_wins=10, n_losses=10)  # n=20 < 30
    p, b, n = estimate_win_rate_and_payoff(df)
    assert n == 20
    assert math.isnan(p)
    assert math.isnan(b)


def test_estimate_win_rate_and_payoff_no_losses_returns_nan_b():
    """All-winning sample (>=30 trades): p is well-defined but b is undefined."""
    df = _make_closed_trades(n_wins=30, n_losses=0, win_ret=0.10)
    p, b, n = estimate_win_rate_and_payoff(df)
    assert n == 30
    assert math.isclose(p, 1.0, rel_tol=1e-9)
    assert math.isnan(b)


def test_estimate_win_rate_and_payoff_empty_returns_nan():
    p, b, n = estimate_win_rate_and_payoff(pd.DataFrame())
    assert n == 0
    assert math.isnan(p)
    assert math.isnan(b)


def test_estimate_win_rate_and_payoff_missing_columns_raises():
    with pytest.raises(ValueError, match="missing required columns"):
        estimate_win_rate_and_payoff(pd.DataFrame({"foo": [1, 2, 3]}))


def test_min_trades_required_constant_is_30():
    assert MIN_TRADES_REQUIRED == 30
