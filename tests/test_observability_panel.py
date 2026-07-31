"""
tests/test_observability_panel.py
=================================
Unit tests for the pure (Streamlit-free) data-shaping helpers extracted from
``gui/panels/observability.py`` into ``gui/observability_panel_helpers.py``.

Coverage focus:
  * Recession-indicator threshold badges (Sahm / HY-OAS / yield-curve / VIX) at
    and around their boundary values — the badge ``level`` maps 1:1 to the
    Streamlit callable (``st.error`` / ``st.warning`` / ``st.success``) so the
    rendered output is byte-identical to the previous inline branches.
  * ``regime_emoji`` traffic-light glyph selection.
  * ``compute_portfolio_heat`` — the P&L-denominator bugfix: real equity when
    available, NaN (never a fabricated denominator) when equity is
    missing/non-positive/unparseable.
  * ``heartbeat_status`` freshness labels including the NaN "no heartbeat" case.
  * Forecast-skill cell formatters (RMSE / skill weight) NaN/None handling.
"""

from __future__ import annotations

import math

import pytest

from gui.observability_panel_helpers import (
    IndicatorBadge,
    compute_portfolio_heat,
    etf_transmission_rows,
    format_rmse,
    format_skill_weight,
    heartbeat_status,
    hy_oas_badge,
    portfolio_heat_badge,
    regime_emoji,
    sahm_badge,
    vix_badge,
    yield_curve_badge,
)


class TestSahmBadge:
    @pytest.mark.parametrize(
        "value, level",
        [
            (0.5, "error"),      # boundary: >= 0.50 fires kill switch
            (0.75, "error"),
            (0.49999, "warning"),
            (0.3, "warning"),    # boundary: >= 0.30 fast-trigger zone
            (0.29999, "success"),
            (0.0, "success"),
        ],
    )
    def test_levels(self, value, level):
        badge = sahm_badge(value)
        assert isinstance(badge, IndicatorBadge)
        assert badge.level == level

    def test_messages_stable(self):
        assert sahm_badge(0.5).message == "🔴 ≥ 0.50 — kill-switch threshold breached"
        assert sahm_badge(0.3).message == "🟡 ≥ 0.30 — fast-trigger zone (HMM agreement needed)"
        assert sahm_badge(0.1).message == "🟢 < 0.30 — below fast-trigger zone"


class TestHyOasBadge:
    @pytest.mark.parametrize(
        "value, level",
        [
            (6.0, "error"),      # boundary
            (7.5, "error"),
            (5.99, "warning"),
            (4.5, "warning"),    # boundary
            (4.49, "success"),
            (2.0, "success"),
        ],
    )
    def test_levels(self, value, level):
        assert hy_oas_badge(value).level == level


class TestYieldCurveBadge:
    @pytest.mark.parametrize(
        "value, level",
        [
            (-0.26, "warning"),  # inverted below -0.25
            (-1.0, "warning"),
            (-0.25, "success"),  # boundary: exactly -0.25 is NOT inverted
            (0.0, "success"),
            (2.5, "success"),
        ],
    )
    def test_levels(self, value, level):
        assert yield_curve_badge(value).level == level


class TestVixBadge:
    @pytest.mark.parametrize(
        "value, level",
        [
            (30.01, "error"),
            (45.0, "error"),
            (30.0, "warning"),   # boundary: exactly 30 is NOT > 30
            (25.01, "warning"),
            (25.0, "success"),   # boundary: exactly 25 is NOT > 25
            (12.0, "success"),
        ],
    )
    def test_levels(self, value, level):
        assert vix_badge(value).level == level


class TestRegimeEmoji:
    @pytest.mark.parametrize(
        "regime, glyph",
        [
            ("RISK ON", "🟢"),
            ("RISK ON — trending", "🟢"),
            ("RECESSION", "🔴"),
            ("CREDIT EVENT", "🟡"),
            ("NEUTRAL", "🟡"),
            ("UNKNOWN", "🟡"),
            ("—", "🟡"),
            (None, "🟡"),
        ],
    )
    def test_glyph(self, regime, glyph):
        assert regime_emoji(regime) == glyph


class TestComputePortfolioHeat:
    def test_real_equity_divides(self):
        # $5k adverse / $100k equity = 5%
        assert compute_portfolio_heat(5_000.0, 100_000.0) == pytest.approx(0.05)

    def test_none_equity_is_nan(self):
        assert math.isnan(compute_portfolio_heat(5_000.0, None))

    def test_zero_equity_is_nan_not_divide_by_zero(self):
        assert math.isnan(compute_portfolio_heat(5_000.0, 0.0))

    def test_negative_equity_is_nan(self):
        assert math.isnan(compute_portfolio_heat(5_000.0, -1_000.0))

    def test_non_finite_equity_is_nan(self):
        assert math.isnan(compute_portfolio_heat(5_000.0, float("inf")))
        assert math.isnan(compute_portfolio_heat(5_000.0, float("nan")))

    def test_unparseable_equity_is_nan(self):
        assert math.isnan(compute_portfolio_heat(5_000.0, "not-a-number"))

    def test_string_numeric_equity_parses(self):
        assert compute_portfolio_heat(1_000.0, "50000") == pytest.approx(0.02)

    def test_zero_adverse_is_zero_heat(self):
        assert compute_portfolio_heat(0.0, 100_000.0) == 0.0


class TestPortfolioHeatBadge:
    @pytest.mark.parametrize(
        "heat, glyph",
        [
            (0.051, "🔴"),
            (0.06, "🔴"),
            (0.05, "🟡"),   # exactly 5% is NOT > 5%
            (0.031, "🟡"),
            (0.03, "🟢"),   # exactly 3% is NOT > 3%
            (0.0, "🟢"),
        ],
    )
    def test_glyph(self, heat, glyph):
        assert portfolio_heat_badge(heat) == glyph


class TestHeartbeatStatus:
    def test_nan_is_no_heartbeat(self):
        assert heartbeat_status(float("nan")) == "⚪ No heartbeat"

    @pytest.mark.parametrize(
        "age, status",
        [
            (121.0, "🔴 Stale"),
            (120.0, "🟡 Slow"),   # boundary: exactly 120 is NOT > 120
            (61.0, "🟡 Slow"),
            (60.0, "🟢 Fresh"),   # boundary: exactly 60 is NOT > 60
            (5.0, "🟢 Fresh"),
            (0.0, "🟢 Fresh"),
        ],
    )
    def test_thresholds(self, age, status):
        assert heartbeat_status(age) == status


class TestForecastCellFormatters:
    def test_rmse_formats_value(self):
        assert format_rmse(1.23456) == "1.2346"

    def test_rmse_none_is_dash(self):
        assert format_rmse(None) == "—"

    def test_rmse_nan_is_dash(self):
        assert format_rmse(float("nan")) == "—"

    def test_skill_weight_formats_percent(self):
        assert format_skill_weight(0.25) == "25.0%"

    def test_skill_weight_none_is_dash(self):
        assert format_skill_weight(None) == "—"


class TestEtfTransmissionRows:
    """gui.observability_panel_helpers.etf_transmission_rows -- the pure
    row-extraction/sort logic behind the Observability tab's ETF Volatility
    Transmission section (gui/panels/observability.py)."""

    def test_symbol_with_no_etf_fields_is_filtered_out(self):
        signals = [
            {"symbol": "AAPL", "etf_ownership_pct": 0.1},
            {"symbol": "ZZZ", "etf_ownership_pct": None, "etf_comovement_r2": None,
             "etf_primary_wrapper": None, "etf_transmission_multiplier": None},
        ]
        rows = etf_transmission_rows(signals)
        assert [r["symbol"] for r in rows] == ["AAPL"]

    def test_symbol_missing_entirely_is_also_filtered_out(self):
        # A symbol dict that never carries any etf_* key at all (e.g. an
        # older snapshot, or a signal type this feature never touches).
        signals = [{"symbol": "AAPL"}]
        assert etf_transmission_rows(signals) == []

    def test_empty_signals_list_returns_empty(self):
        assert etf_transmission_rows([]) == []

    def test_none_signals_returns_empty_never_raises(self):
        assert etf_transmission_rows(None) == []

    def test_row_missing_symbol_is_skipped(self):
        signals = [{"symbol": None, "etf_ownership_pct": 0.5}]
        assert etf_transmission_rows(signals) == []

    def test_malformed_entry_is_skipped_not_raised(self):
        signals = ["not a dict", {"symbol": "AAPL", "etf_ownership_pct": 0.1}]
        rows = etf_transmission_rows(signals)
        assert [r["symbol"] for r in rows] == ["AAPL"]

    def test_all_four_fields_are_carried_through(self):
        signals = [{
            "symbol": "AAPL",
            "etf_ownership_pct": 0.15,
            "etf_comovement_r2": 0.8,
            "etf_primary_wrapper": "XLK",
            "etf_transmission_multiplier": 0.85,
        }]
        row = etf_transmission_rows(signals)[0]
        assert row == {
            "symbol": "AAPL",
            "etf_ownership_pct": 0.15,
            "etf_comovement_r2": 0.8,
            "etf_primary_wrapper": "XLK",
            "etf_transmission_multiplier": 0.85,
        }

    def test_sorted_ascending_by_multiplier_most_derated_first(self):
        signals = [
            {"symbol": "LOW_DERATE", "etf_transmission_multiplier": 0.95},
            {"symbol": "HIGH_DERATE", "etf_transmission_multiplier": 0.50},
            {"symbol": "MID_DERATE", "etf_transmission_multiplier": 0.75},
        ]
        rows = etf_transmission_rows(signals)
        assert [r["symbol"] for r in rows] == ["HIGH_DERATE", "MID_DERATE", "LOW_DERATE"]

    def test_rows_without_multiplier_sort_after_rows_with_one(self):
        signals = [
            {"symbol": "NO_MULTIPLIER", "etf_ownership_pct": 0.99},
            {"symbol": "HAS_MULTIPLIER", "etf_transmission_multiplier": 0.50},
        ]
        rows = etf_transmission_rows(signals)
        assert [r["symbol"] for r in rows] == ["HAS_MULTIPLIER", "NO_MULTIPLIER"]

    def test_rows_without_multiplier_sort_by_descending_ownership(self):
        signals = [
            {"symbol": "SMALL_OWNERSHIP", "etf_ownership_pct": 0.05},
            {"symbol": "BIG_OWNERSHIP", "etf_ownership_pct": 0.40},
        ]
        rows = etf_transmission_rows(signals)
        assert [r["symbol"] for r in rows] == ["BIG_OWNERSHIP", "SMALL_OWNERSHIP"]
