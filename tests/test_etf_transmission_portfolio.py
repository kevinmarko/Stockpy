"""
tests/test_etf_transmission_portfolio.py
=========================================
Pure-math unit tests for ``risk/etf_transmission.py``'s portfolio-level
covariance functions -- ``_pairwise_etf_overlap``, ``_nearest_psd``, and
``build_transmission_adjusted_cov`` -- built on top of Agent C's measurement
layer (``tests/test_etf_transmission.py``) to feed
``sizing.position_sizer.apply_portfolio_gross_cap``'s EXISTING risk-aware
``cov_matrix``/``target_vol`` path (``sizing.vol_target.portfolio_vol_target``)
with an ETF-co-ownership-inflated covariance matrix, per Ben-David, Franzoni
& Moussawi (2018): the mechanism raises CO-MOVEMENT between co-held names,
not any single name's own variance.

This sandbox has NO live-market network access, so everything here is
fixture-driven synthetic data. Nothing in this file makes (or asserts
anything about) a real holdings/price fetch.

The single most important test in this file is
``TestBuildTransmissionAdjustedCov::test_off_diagonal_inflated_by_exactly_the_overlap_factor``
-- it proves the inflation formula is exactly
``cov_adj[i,j] = cov[i,j] * (1 + inflation * overlap[i,j])`` and that the
diagonal is untouched, which is the whole design claim (portfolio-level
covariance, not a second per-name variance lever).

``ETFHolding`` is Agent B's frozen contract in ``data/etf_holdings.py``.
``risk/etf_transmission.py`` consumes it duck-typed, and these tests use a
local stub of the same shape (matching ``tests/test_etf_transmission.py``'s
convention) so they stay runnable independently of that module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import pytest

from risk.etf_transmission import (
    _nearest_psd,
    _pairwise_etf_overlap,
    build_transmission_adjusted_cov,
)

AS_OF = date(2026, 7, 27)


@dataclass(frozen=True)
class StubHolding:
    """Local stand-in for data.etf_holdings.ETFHolding (same field names)."""
    etf_symbol: str
    holding_symbol: str
    weight: float = float("nan")
    shares_held: float = float("nan")
    as_of_date: date = AS_OF
    source: str = "stub"


def _returns_df(seed: int = 0, n: int = 300, symbols=("AAA", "BBB", "CCC")) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    data = rng.normal(0.0, 0.01, size=(n, len(symbols)))
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(data, columns=list(symbols), index=idx)


# ── _pairwise_etf_overlap ────────────────────────────────────────────────


class TestPairwiseEtfOverlap:
    def test_identical_wrapper_membership_scores_full_overlap(self):
        holdings = {
            "XLK": [
                StubHolding("XLK", "AAA", weight=0.05),
                StubHolding("XLK", "BBB", weight=0.05),
            ],
        }
        overlap = _pairwise_etf_overlap(["AAA", "BBB"], holdings)
        assert overlap.loc["AAA", "BBB"] == pytest.approx(1.0)
        assert overlap.loc["BBB", "AAA"] == pytest.approx(1.0)

    def test_disjoint_wrapper_membership_scores_zero_overlap(self):
        holdings = {
            "XLK": [StubHolding("XLK", "AAA", weight=0.05)],
            "XLF": [StubHolding("XLF", "BBB", weight=0.05)],
        }
        overlap = _pairwise_etf_overlap(["AAA", "BBB"], holdings)
        assert overlap.loc["AAA", "BBB"] == pytest.approx(0.0)

    def test_partial_overlap_lands_strictly_between_zero_and_one(self):
        holdings = {
            "XLK": [
                StubHolding("XLK", "AAA", weight=0.08),
                StubHolding("XLK", "BBB", weight=0.02),
            ],
            "XLF": [StubHolding("XLF", "AAA", weight=0.03)],
        }
        overlap = _pairwise_etf_overlap(["AAA", "BBB"], holdings)
        assert 0.0 < overlap.loc["AAA", "BBB"] < 1.0

    def test_symbol_absent_from_every_basket_scores_zero_against_everything(self):
        holdings = {"XLK": [StubHolding("XLK", "AAA", weight=0.05), StubHolding("XLK", "BBB", weight=0.05)]}
        overlap = _pairwise_etf_overlap(["AAA", "BBB", "ZZZ"], holdings)
        assert overlap.loc["ZZZ", "AAA"] == pytest.approx(0.0)
        assert overlap.loc["ZZZ", "BBB"] == pytest.approx(0.0)

    def test_diagonal_is_zero(self):
        holdings = {"XLK": [StubHolding("XLK", "AAA", weight=0.05)]}
        overlap = _pairwise_etf_overlap(["AAA", "BBB"], holdings)
        assert overlap.loc["AAA", "AAA"] == 0.0
        assert overlap.loc["BBB", "BBB"] == 0.0

    def test_matrix_is_symmetric(self):
        holdings = {
            "XLK": [StubHolding("XLK", "AAA", weight=0.05), StubHolding("XLK", "BBB", weight=0.03)],
            "XLF": [StubHolding("XLF", "BBB", weight=0.07), StubHolding("XLF", "CCC", weight=0.02)],
        }
        overlap = _pairwise_etf_overlap(["AAA", "BBB", "CCC"], holdings)
        assert np.allclose(overlap.to_numpy(), overlap.to_numpy().T)

    def test_falls_back_to_shares_held_when_weight_is_unusable(self):
        holdings = {
            "XLK": [
                StubHolding("XLK", "AAA", weight=float("nan"), shares_held=1000.0),
                StubHolding("XLK", "BBB", weight=float("nan"), shares_held=1000.0),
            ],
        }
        overlap = _pairwise_etf_overlap(["AAA", "BBB"], holdings)
        assert overlap.loc["AAA", "BBB"] == pytest.approx(1.0)

    def test_empty_holdings_yields_all_zero_matrix_never_raises(self):
        overlap = _pairwise_etf_overlap(["AAA", "BBB"], {})
        assert (overlap.to_numpy() == 0.0).all()

    def test_malformed_rows_are_skipped_not_raised(self):
        class Weird:
            pass

        holdings = {"XLK": [Weird(), StubHolding("XLK", "AAA", weight=0.05)]}
        overlap = _pairwise_etf_overlap(["AAA", "BBB"], holdings)
        assert overlap is not None


# ── _nearest_psd ──────────────────────────────────────────────────────────


class TestNearestPsd:
    def test_already_psd_matrix_is_returned_unchanged_up_to_float_tolerance(self):
        base = np.array([[1.0, 0.3], [0.3, 1.0]])
        repaired = _nearest_psd(base)
        assert np.allclose(repaired, base, atol=1e-9)

    def test_indefinite_matrix_is_repaired_to_psd(self):
        # Three mutually strongly-negative correlations -- not realizable as
        # PSD for 3 variables (a genuine indefinite input).
        base = np.array([
            [1.0, -0.9, -0.9],
            [-0.9, 1.0, -0.9],
            [-0.9, -0.9, 1.0],
        ])
        assert np.linalg.eigvalsh(base).min() < 0.0  # precondition: input IS indefinite

        repaired = _nearest_psd(base, epsilon=1e-10)
        eigvals = np.linalg.eigvalsh(repaired)
        assert eigvals.min() >= -1e-8

    def test_repaired_matrix_is_symmetric(self):
        base = np.array([[1.0, -0.9, -0.9], [-0.9, 1.0, -0.9], [-0.9, -0.9, 1.0]])
        repaired = _nearest_psd(base)
        assert np.allclose(repaired, repaired.T)

    def test_floor_scales_with_matrix_magnitude_not_a_fixed_absolute_value(self):
        """Real bug this pins: a fixed absolute epsilon (the original
        default, 1e-10) is disconnected from the matrix's own eigenvalue
        scale. Measured on a realistic 40-symbol ETF-transmission covariance
        matrix at high inflation (enough to flip 30 of 40 eigenvalues
        negative), on an ANNUALIZED scale (max eigenvalue ~2, matching what
        pipeline/production_steps.py::_build_etf_transmission_cov_matrix
        actually feeds this function): a fixed ``epsilon=1e-10`` floor left
        a condition number of ~1e8 -- a ~30,000x degradation versus the
        unadjusted matrix's ~1e3, severe enough to trigger spurious
        BLAS-level RuntimeWarnings downstream. For a large-scale matrix like
        this, ``1e-10`` is a negligible fraction of the eigenvalue range, so
        the RELATIVE floor (``_RELATIVE_PSD_FLOOR * max_eigenvalue``) must be
        the one actually doing the work -- pinning the condition number near
        ``1 / _RELATIVE_PSD_FLOOR`` (~1e6) rather than the ~1e8-1e10 a
        fixed-epsilon-only design would leave for a matrix at this scale."""
        base = np.array([
            [1.0, -0.9, -0.9],
            [-0.9, 1.0, -0.9],
            [-0.9, -0.9, 1.0],
        ])
        annualized_scale = base * 2.0  # max eigenvalue ~2, matching a real annualized cov

        cond = self._condition_number(_nearest_psd(annualized_scale))

        # Should sit close to 1 / _RELATIVE_PSD_FLOOR (~1e6), not the
        # ~1e8-1e10 a fixed-epsilon-only floor would leave for a matrix
        # whose max eigenvalue (~2) makes 1e-10 a negligible fraction of it.
        assert cond < 1e7, (
            f"condition number {cond:.2e} is far worse than the relative "
            f"floor should allow for a matrix at this scale -- the "
            f"relative floor may not be engaging"
        )
        assert cond > 1e4, (
            f"condition number {cond:.2e} is suspiciously good -- this "
            f"test's matrix should genuinely need repair"
        )

    @staticmethod
    def _condition_number(matrix: np.ndarray) -> float:
        eigvals = np.linalg.eigvalsh(matrix)
        return float(eigvals.max() / max(eigvals.min(), 1e-300))

    def test_epsilon_kwarg_still_honored_as_a_lower_bound(self):
        """Backward compatibility: an explicit ``epsilon`` larger than the
        relative floor still wins, so an existing caller who deliberately
        passes a large absolute epsilon is not silently overridden downward."""
        base = np.array([[1.0, -0.9, -0.9], [-0.9, 1.0, -0.9], [-0.9, -0.9, 1.0]])
        repaired = _nearest_psd(base, epsilon=0.5)
        eigvals = np.linalg.eigvalsh(repaired)
        assert eigvals.min() >= 0.5 - 1e-9


# ── build_transmission_adjusted_cov ──────────────────────────────────────


class TestBuildTransmissionAdjustedCov:
    def test_off_diagonal_inflated_by_exactly_the_overlap_factor(self):
        returns = _returns_df(seed=42)
        holdings = {
            "XLK": [
                StubHolding("XLK", "AAA", weight=0.05),
                StubHolding("XLK", "BBB", weight=0.05),
            ],
        }
        base_cov = returns.tail(60).cov()
        adjusted = build_transmission_adjusted_cov(returns, holdings, inflation=0.25, window=60)

        assert adjusted is not None
        # AAA/BBB share full ETF overlap -> exactly (1 + 0.25*1.0) = 1.25x.
        assert adjusted.loc["AAA", "BBB"] / base_cov.loc["AAA", "BBB"] == pytest.approx(1.25, rel=1e-6)
        # CCC shares no wrapper with AAA -> untouched, exactly 1.0x.
        assert adjusted.loc["AAA", "CCC"] / base_cov.loc["AAA", "CCC"] == pytest.approx(1.0, rel=1e-6)

    def test_diagonal_variance_is_never_touched(self):
        returns = _returns_df(seed=7)
        holdings = {"XLK": [StubHolding("XLK", s, weight=0.05) for s in ("AAA", "BBB", "CCC")]}
        base_cov = returns.tail(60).cov()
        adjusted = build_transmission_adjusted_cov(returns, holdings, inflation=0.9, window=60)

        assert adjusted is not None
        for sym in ("AAA", "BBB", "CCC"):
            assert adjusted.loc[sym, sym] == pytest.approx(base_cov.loc[sym, sym], rel=1e-9)

    def test_result_is_always_positive_semi_definite(self):
        # A large, uniform inflation across a fully-overlapping universe is
        # exactly the adversarial case that can push a valid covariance
        # matrix outside the PSD cone.
        returns = _returns_df(seed=3, symbols=("AAA", "BBB", "CCC", "DDD"))
        holdings = {
            "XLK": [StubHolding("XLK", s, weight=0.05) for s in ("AAA", "BBB", "CCC", "DDD")],
        }
        adjusted = build_transmission_adjusted_cov(returns, holdings, inflation=5.0, window=60)
        assert adjusted is not None
        eigvals = np.linalg.eigvalsh(adjusted.to_numpy())
        assert eigvals.min() >= -1e-8

    def test_result_is_symmetric(self):
        returns = _returns_df(seed=11)
        holdings = {"XLK": [StubHolding("XLK", "AAA", weight=0.05), StubHolding("XLK", "CCC", weight=0.02)]}
        adjusted = build_transmission_adjusted_cov(returns, holdings, inflation=0.4, window=60)
        assert adjusted is not None
        arr = adjusted.to_numpy()
        assert np.allclose(arr, arr.T)

    def test_indexed_and_columned_by_returns_df_columns(self):
        returns = _returns_df(seed=1, symbols=("AAA", "BBB"))
        adjusted = build_transmission_adjusted_cov(returns, {}, inflation=0.25, window=60)
        assert adjusted is not None
        assert list(adjusted.index) == ["AAA", "BBB"]
        assert list(adjusted.columns) == ["AAA", "BBB"]

    def test_empty_holdings_produces_the_unmodified_base_covariance(self):
        returns = _returns_df(seed=5)
        base_cov = returns.tail(60).cov()
        adjusted = build_transmission_adjusted_cov(returns, {}, inflation=0.9, window=60)
        assert adjusted is not None
        assert np.allclose(adjusted.to_numpy(), base_cov.to_numpy(), atol=1e-12)

    # ── Degradation paths: None, never a fabricated or partial matrix ────

    def test_fewer_than_two_symbols_returns_none(self):
        returns = _returns_df(seed=1, symbols=("AAA",))
        assert build_transmission_adjusted_cov(returns, {}, inflation=0.25, window=60) is None

    def test_empty_returns_df_returns_none(self):
        empty = pd.DataFrame(columns=["AAA", "BBB"])
        assert build_transmission_adjusted_cov(empty, {}, inflation=0.25, window=60) is None

    def test_none_returns_df_returns_none(self):
        assert build_transmission_adjusted_cov(None, {}, inflation=0.25, window=60) is None

    def test_fewer_rows_than_window_returns_none(self):
        returns = _returns_df(seed=1, n=30)  # only 30 rows, window=60
        assert build_transmission_adjusted_cov(returns, {}, inflation=0.25, window=60) is None

    def test_never_raises_on_malformed_holdings(self):
        returns = _returns_df(seed=1)

        class Weird:
            pass

        result = build_transmission_adjusted_cov(returns, {"XLK": [Weird()]}, inflation=0.25, window=60)
        # Malformed rows are skipped by _pairwise_etf_overlap -- this should
        # succeed with zero overlap, not raise and not return None.
        assert result is not None

    def test_all_nan_returns_column_returns_none(self):
        returns = _returns_df(seed=1)
        returns["AAA"] = float("nan")
        assert build_transmission_adjusted_cov(returns, {}, inflation=0.25, window=60) is None
