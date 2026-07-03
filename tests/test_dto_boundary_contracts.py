"""
tests/test_dto_boundary_contracts.py
=====================================
Boundary-condition tests for ``dto_models.py`` DTOs with genuine coverage
gaps, verified against source (not assumed) before writing:

  * ``RobinhoodPositionDTO`` — had ZERO direct unit tests prior to this file
    (only incidentally constructed as a side effect of testing
    ``data/portfolio_sync.py``). Covers ``true_break_even``'s shares<=0
    fallback, the dividends-per-share floor at 0.0, and NaN propagation.
  * ``MarketBarDTO`` — only exercised incidentally inside full-pipeline
    tests. Covers the boundary-normalization contract directly: high<low
    coercion, open/close clamping to [low, high], and negative-volume
    coercion via ``BaseDTO._to_int``.
  * ``MacroEconomicDTO`` — ``tests/test_macro_hmm_integration.py`` already
    covers the HMM downgrade/agreement paths thoroughly. This file adds the
    one documented-but-unverified gap: when ``hmm_risk_on_probability`` is
    ``None`` (the HMM did not run), ``killSwitch`` must behave EXACTLY as it
    did before the HMM feature existed — the lowered "agreed" thresholds
    must never apply, even in a RECESSION regime.

``BaseDTO._to_float``/``_to_int`` themselves (including the ``bool``
gotcha) are already covered by ``tests/test_no_fabricated_metrics.py`` —
not duplicated here.
"""

from __future__ import annotations

import math
from datetime import datetime

import pytest

from dto_models import MarketBarDTO, MacroEconomicDTO, RobinhoodPositionDTO


# ---------------------------------------------------------------------------
# RobinhoodPositionDTO
# ---------------------------------------------------------------------------

class TestRobinhoodPositionDTOConstruction:
    def test_basic_fields_coerced(self):
        pos = RobinhoodPositionDTO(
            ticker="aapl", shares="12.5", average_cost="$180.25", total_dividends="8.40"
        )
        assert pos.ticker == "AAPL"
        assert pos.shares == 12.5
        assert pos.average_cost == 180.25
        assert pos.total_dividends == 8.40

    def test_defaults_when_dividends_omitted(self):
        pos = RobinhoodPositionDTO(ticker="MSFT", shares=10.0, average_cost=300.0)
        assert pos.total_dividends == 0.0


class TestTrueBreakEven:
    def test_reduces_cost_basis_by_dividends_per_share(self):
        pos = RobinhoodPositionDTO(
            ticker="T", shares=100.0, average_cost=20.0, total_dividends=200.0
        )
        # 200 / 100 = 2.0 divs/share -> break-even = 20 - 2 = 18.0
        assert pos.true_break_even == pytest.approx(18.0)

    def test_floored_at_zero_when_dividends_exceed_cost_basis(self):
        """A high-yield holder whose accumulated dividends exceed the average
        cost never produces a negative break-even (CONSTRAINT #4 spirit —
        floored, not fabricated negative)."""
        pos = RobinhoodPositionDTO(
            ticker="T", shares=10.0, average_cost=5.0, total_dividends=1000.0
        )
        assert pos.true_break_even == 0.0

    @pytest.mark.parametrize("shares", [0.0, -5.0])
    def test_shares_le_zero_falls_back_to_average_cost(self, shares):
        """No division-by-zero risk for a closed/negative position — the
        property short-circuits to average_cost verbatim."""
        pos = RobinhoodPositionDTO(
            ticker="X", shares=shares, average_cost=42.0, total_dividends=999.0
        )
        assert pos.true_break_even == 42.0

    def test_nan_dividends_field_itself_stays_nan(self):
        """NaN total_dividends is honestly NaN on the raw field — not
        silently replaced with 0.0 (BaseDTO._to_float passes NaN through
        unchanged since isinstance(nan, float) is True)."""
        pos = RobinhoodPositionDTO(
            ticker="X", shares=10.0, average_cost=50.0, total_dividends=float("nan")
        )
        assert math.isnan(pos.total_dividends)

    def test_nan_dividends_fabricates_zero_break_even_via_max_nan_quirk(self):
        """CORRECTED BY DIRECT EXECUTION (not source-reading alone): the raw
        ``total_dividends`` field stays honestly NaN, but ``true_break_even``
        does NOT propagate that NaN as one might assume. ``divs_per_share =
        nan / shares`` is NaN, so ``average_cost - divs_per_share`` is NaN,
        but the property's own guard is ``max(0.0, average_cost -
        divs_per_share)`` — and ``max(0.0, nan)`` evaluates to ``0.0`` in
        Python, because ``nan > 0.0`` is always False so the first argument
        (0.0) never loses the comparison to the NaN candidate. Net effect: a
        NaN dividend total silently fabricates a break-even of exactly
        ``0.0`` rather than propagating NaN. This mirrors the NaN-
        comparison-is-always-False fabrication pattern already documented
        for several ``signals/*.py`` modules in
        ``tests/test_signal_module_contracts.py`` — pinned as current
        behavior, not fixed here."""
        pos = RobinhoodPositionDTO(
            ticker="X", shares=10.0, average_cost=50.0, total_dividends=float("nan")
        )
        result = pos.true_break_even
        assert not math.isnan(result), (
            "if this now fails, max(0.0, nan) semantics changed and the "
            "docstring above needs re-verifying against the new behavior"
        )
        assert result == 0.0

    def test_repr_does_not_raise(self):
        pos = RobinhoodPositionDTO(ticker="T", shares=1.0, average_cost=1.0, total_dividends=0.0)
        assert "T" in repr(pos)


# ---------------------------------------------------------------------------
# MarketBarDTO boundary normalization
# ---------------------------------------------------------------------------

class TestMarketBarDTOBoundaryNormalization:
    def test_well_formed_bar_untouched(self):
        bar = MarketBarDTO(
            date=datetime(2026, 1, 2), ticker="aapl",
            open_price=178.0, high_price=180.0, low_price=177.0, close_price=179.0,
            volume=1_000_000,
        )
        assert bar.ticker == "AAPL"
        assert (bar.open, bar.high, bar.low, bar.close) == (178.0, 180.0, 177.0, 179.0)
        assert bar.volume == 1_000_000

    def test_high_below_low_is_coerced_to_low(self):
        """Malformed feed (high < low) — high is silently coerced to low,
        never raised, but the coercion happens (not a fabricated value out
        of thin air, the low bound is real feed data)."""
        bar = MarketBarDTO(
            date=datetime(2026, 1, 2), ticker="X",
            open_price=179.0, high_price=175.0, low_price=178.0, close_price=178.5,
            volume=100,
        )
        assert bar.high == bar.low == 178.0

    def test_open_and_close_clamped_into_high_low_range(self):
        bar = MarketBarDTO(
            date=datetime(2026, 1, 2), ticker="X",
            open_price=500.0,   # above high -> clamp to high
            high_price=200.0, low_price=100.0,
            close_price=50.0,   # below low -> clamp to low
            volume=100,
        )
        assert bar.open == 200.0
        assert bar.close == 100.0

    def test_open_close_within_bounds_are_unchanged(self):
        bar = MarketBarDTO(
            date=datetime(2026, 1, 2), ticker="X",
            open_price=150.0, high_price=200.0, low_price=100.0, close_price=175.0,
            volume=100,
        )
        assert bar.open == 150.0
        assert bar.close == 175.0

    def test_string_prices_and_volume_coerced(self):
        bar = MarketBarDTO(
            date=datetime(2026, 1, 2), ticker="X",
            open_price="$180.50", high_price="182.00", low_price="179.00",
            close_price="181.25", volume="10,250,300",
        )
        assert bar.open == 180.50
        assert bar.volume == 10_250_300

    def test_negative_volume_coerced_via_to_int(self):
        """No explicit non-negative guard on volume — BaseDTO._to_int just
        coerces the type; a negative feed value passes through as a negative
        int rather than being clamped or raising."""
        bar = MarketBarDTO(
            date=datetime(2026, 1, 2), ticker="X",
            open_price=10.0, high_price=11.0, low_price=9.0, close_price=10.5,
            volume=-500,
        )
        assert bar.volume == -500

    def test_repr_includes_ticker_and_close(self):
        bar = MarketBarDTO(
            date=datetime(2026, 1, 2), ticker="AAPL",
            open_price=10.0, high_price=11.0, low_price=9.0, close_price=10.5,
            volume=1,
        )
        assert "AAPL" in repr(bar)
        assert "10.50" in repr(bar)


# ---------------------------------------------------------------------------
# MacroEconomicDTO — hmm_risk_on_probability=None gap
# ---------------------------------------------------------------------------

class TestMacroEconomicDTOHmmNoneGap:
    """tests/test_macro_hmm_integration.py covers the downgrade/agreement
    paths when the HMM DID run. This class pins the documented invariant for
    when it did NOT: killSwitch/market_regime must be byte-identical to the
    pre-HMM-feature behavior — the lowered "agreed" thresholds structurally
    cannot fire without a real HMM probability."""

    def test_recession_with_hmm_none_uses_only_base_kill_thresholds(self):
        # RECESSION regime (sahm >= 0.6), but base_kill conditions (sahm>=0.5
        # or vix>30) both fail here (vix=26 is between the agreed 25 and base
        # 30 thresholds; sahm=0.6 >= 0.5 so base_kill IS true via sahm -- use
        # values that isolate the agreed-lowered VIX threshold specifically).
        dto = MacroEconomicDTO(
            yield_curve_10y_2y=-0.5, high_yield_oas=7.0,  # -> RECESSION via yield_curve+spread
            inflation_rate=2.0, sahm_rule_indicator=0.2,   # sahm below both thresholds
            vix_value=26.0,                                 # between agreed(25) and base(30)
            hmm_risk_on_probability=None,
        )
        assert dto._rules_based_regime == "RECESSION"
        # If the agreed-lowered VIX threshold (25) applied, this would be True.
        # With hmm=None it must fall back to the base-only check (vix>30) -> False.
        assert dto.killSwitch is False

    def test_same_scenario_with_hmm_confirming_recession_fires_lowered_threshold(self):
        """Sanity contrast: identical inputs, but with a real HMM risk-off
        probability that AGREES with RECESSION -- now the lowered VIX
        threshold (25) does apply and killSwitch fires. Confirms the None
        case above is genuinely different behavior, not coincidental."""
        dto = MacroEconomicDTO(
            yield_curve_10y_2y=-0.5, high_yield_oas=7.0,
            inflation_rate=2.0, sahm_rule_indicator=0.2,
            vix_value=26.0,
            hmm_risk_on_probability=0.1,  # risk_off_probability = 0.9 > 0.7 agreement threshold
        )
        assert dto._rules_based_regime == "RECESSION"
        assert dto.killSwitch is True

    def test_non_recession_regime_with_hmm_none_unaffected(self):
        dto = MacroEconomicDTO(
            yield_curve_10y_2y=1.0, high_yield_oas=2.0,
            inflation_rate=2.0, sahm_rule_indicator=0.0,
            vix_value=15.0,
            hmm_risk_on_probability=None,
        )
        assert dto._rules_based_regime == "RISK ON"
        assert dto.killSwitch is False
        assert dto.market_regime == "RISK ON"
