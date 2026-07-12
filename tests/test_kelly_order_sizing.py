"""Unit tests for main_orchestrator._kelly_target_qty.

The Alpaca BUY path previously submitted a hardcoded qty=1.0 for every new long,
ignoring the Kelly Target weight and neutering the position-size risk check. The
sizing is now `shares = kelly_weight * equity / price`, extracted into this pure
helper so it can be verified without a live broker.
"""
import math

import pytest

from main_orchestrator import _kelly_target_qty


class TestKellyTargetQty:
    def test_basic_sizing(self):
        # 5% of a $100k account at $50/share = $5,000 / $50 = 100 shares.
        assert _kelly_target_qty(0.05, 100_000.0, 50.0) == 100.0

    def test_fractional_shares_preserved(self):
        # 2% of $10k at $500/share = $200 / $500 = 0.4 shares (NOT floored to 0).
        assert _kelly_target_qty(0.02, 10_000.0, 500.0) == pytest.approx(0.4)

    def test_rounding_to_six_dp(self):
        q = _kelly_target_qty(0.01, 1_000.0, 3.0)  # 10/3 = 3.333333...
        assert q == pytest.approx(3.333333, abs=1e-9)

    @pytest.mark.parametrize("kelly,equity,price", [
        (0.0, 100_000.0, 50.0),     # zero weight
        (-0.1, 100_000.0, 50.0),    # negative weight
        (0.05, 0.0, 50.0),          # no equity (e.g. account fetch failed -> 0.0)
        (0.05, -100.0, 50.0),       # negative equity
        (0.05, 100_000.0, 0.0),     # missing price
        (0.05, 100_000.0, -1.0),    # negative price
    ])
    def test_unsizable_returns_zero_not_one(self, kelly, equity, price):
        # CONSTRAINT #4: an unsizable order returns 0.0 (a SKIP signal), never a
        # fabricated 1-share default.
        assert _kelly_target_qty(kelly, equity, price) == 0.0

    def test_max_weight_full_equity(self):
        # A 100% weight (MAX_POSITION_WEIGHT) buys the whole account's worth.
        assert _kelly_target_qty(1.0, 20_000.0, 100.0) == 200.0

    def test_never_negative(self):
        # No combination of valid-sign inputs yields a negative quantity.
        for kelly in (0.01, 0.5, 1.0):
            for equity in (1_000.0, 250_000.0):
                for price in (1.0, 37.5, 999.99):
                    q = _kelly_target_qty(kelly, equity, price)
                    assert q >= 0.0 and math.isfinite(q)
