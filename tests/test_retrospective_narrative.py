"""tests/test_retrospective_narrative.py — Unit Tests for Templated Narrative Generator
=====================================================================================

Authoritative Requirements:
- .agents/ORIGINAL_REQUEST.md (§ R5, WP-F)
- .agents/PROJECT.md (§ 4 Retrospective Core Engine, Milestone 3)
- .agents/worker_m3/DISPATCH.md
- .agents/explorer_survey_2/retrospective_learning_loop_survey_report.md (§ 5)

Coverage:
1. Safe formatting primitives (_fmt_curr, _fmt_pct, _fmt_float).
2. All 15 template permutations across signal-driven, manual, and unknown branches.
3. Strict WP-F anti-fabrication assertions: zero 'None', 'NaN', 'nan', or 'null' leakage.
4. Calling conventions: full dictionary input vs kwargs vs positional args.
5. Boundary conditions: degenerate prices, zero durations, failed bridges, adversarial strings.
"""

from __future__ import annotations

import re

from pilots.retrospective_narrative import (
    _fmt_curr,
    _fmt_float,
    _fmt_pct,
    _is_valid_num,
    build_trade_narrative,
)

FORBIDDEN_REGEX = re.compile(r"\b(None|NaN|nan|null)\b", re.IGNORECASE)


def assert_zero_leakage(text: str) -> None:
    """Strict WP-F check: assert no forbidden placeholder tokens appear in text."""
    match = FORBIDDEN_REGEX.search(text)
    assert match is None, f"WP-F Violation: Leaked '{match.group()}' in narrative: '{text}'"


# =============================================================================
# 1. Formatting Safety Primitives
# =============================================================================

class TestFormattingPrimitives:
    """Test numeric safe formatters guaranteeing zero None/NaN leakage."""

    def test_is_valid_num(self):
        assert _is_valid_num(100) is True
        assert _is_valid_num(100.5) is True
        assert _is_valid_num("100.5") is True
        assert _is_valid_num(None) is False
        assert _is_valid_num(float("nan")) is False
        assert _is_valid_num(float("inf")) is False
        assert _is_valid_num(float("-inf")) is False
        assert _is_valid_num("invalid") is False

    def test_fmt_curr(self):
        assert _fmt_curr(1234.56) == "$1,234.56"
        assert _fmt_curr(0.0) == "$0.00"
        assert _fmt_curr(-50.25) == "-$50.25"
        assert _fmt_curr(None) == "unrecorded"
        assert _fmt_curr(float("nan")) == "unrecorded"
        assert _fmt_curr(float("inf")) == "unrecorded"
        assert _fmt_curr(None, fallback="missing") == "missing"

    def test_fmt_pct(self):
        assert _fmt_pct(0.125) == "12.5%"
        assert _fmt_pct(0.125, signed=True) == "+12.5%"
        assert _fmt_pct(-0.05, signed=True) == "-5.0%"
        assert _fmt_pct(0.0, signed=True) == "0.0%"
        assert _fmt_pct(-0.0, signed=True) == "0.0%"
        assert _fmt_pct(None) == "unrecorded"
        assert _fmt_pct(float("nan")) == "unrecorded"
        assert _fmt_pct(float("inf")) == "unrecorded"

    def test_fmt_float(self):
        assert _fmt_float(3.14159, decimals=2) == "3.14"
        assert _fmt_float(3.14159, decimals=4) == "3.1416"
        assert _fmt_float(0.0) == "0.00"
        assert _fmt_float(None) == "unrecorded"
        assert _fmt_float(float("nan")) == "unrecorded"
        assert _fmt_float(float("inf")) == "unrecorded"


# =============================================================================
# 2. All 15 Template Permutations
# =============================================================================

class TestAllFifteenNarrativePermutations:
    """Verify each of the 15 authoritative permutations from Survey §5.3."""

    def test_permutation_01_signal_driven_full_context(self):
        """P1 (S1.1 + O1.1 + E1.1): Complete context with gain and calibration."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="momentum_alpha",
            entry_price=150.0,
            conviction=0.85,
            macro_regime="expansion",
            exit_price=165.0,
            holding_days=4.5,
            pnl=150.0,
            pnl_pct=0.10,
            mfe=0.12,
            mae=0.02,
            edge_ratio=6.0,
            bin_win_rate=0.75,
            bin_count=12,
            min_sample=5,
        )
        assert "Signal-driven buy trade entered on momentum_alpha recommendation at $150.00" in text
        assert "(conviction: 0.85, regime: expansion)" in text
        assert "Position closed at $165.00 after 4.5 days, realizing a gain of +$150.00 (+10.0%)" in text
        assert "Hold-period excursion reached MFE +12.0% vs MAE -2.0% (Edge Ratio: 6.00)" in text
        assert "entry conviction binned at historical 75.0% win rate (N=12)" in text
        assert_zero_leakage(text)

    def test_permutation_02_signal_driven_missing_regime(self):
        """P2 (S1.2): Conviction present, regime unrecorded."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="trend_follow",
            entry_price=100.0,
            conviction=0.70,
            macro_regime=None,
            exit_price=110.0,
            holding_days=2.0,
            pnl=10.0,
            pnl_pct=0.10,
            mfe=0.12,
            mae=0.02,
            edge_ratio=6.0,
            bin_win_rate=0.65,
            bin_count=8,
        )
        assert "(conviction: 0.70, regime: unrecorded)" in text
        assert_zero_leakage(text)

    def test_permutation_03_signal_driven_missing_conviction(self):
        """P3 (S1.3 + E1.3): Regime present, conviction missing (no calibration bin)."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="mean_revert",
            entry_price=100.0,
            conviction=None,
            macro_regime="stagflation",
            exit_price=110.0,
            holding_days=2.0,
            pnl=10.0,
            pnl_pct=0.10,
            mfe=0.12,
            mae=0.02,
            edge_ratio=6.0,
        )
        assert "(regime: stagflation)" in text
        assert "conviction:" not in text
        assert "Edge Ratio: 6.00)." in text
        assert_zero_leakage(text)

    def test_permutation_04_signal_driven_missing_both_conviction_and_regime(self):
        """P4 (S1.4): Known strategy_id, missing both conviction and regime."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="sell",
            strategy_id="breakout_short",
            entry_price=80.0,
            conviction=None,
            macro_regime=None,
            exit_price=72.0,
            holding_days=1.5,
            pnl=8.0,
            pnl_pct=0.10,
            mfe=0.11,
            mae=0.01,
            edge_ratio=11.0,
        )
        assert "Signal-driven sell trade entered on breakout_short recommendation at $80.00." in text
        assert_zero_leakage(text)

    def test_permutation_05_signal_driven_missing_strategy_id(self):
        """P5 (S1.5): Missing strategy_id entirely."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id=None,
            entry_price=120.0,
            conviction=None,
            macro_regime=None,
            exit_price=125.0,
            holding_days=1.0,
            pnl=5.0,
            pnl_pct=0.042,
            mfe=0.05,
            mae=0.01,
            edge_ratio=5.0,
        )
        assert "Signal-driven buy trade entered via automated strategy at $120.00." in text
        assert_zero_leakage(text)

    def test_permutation_06_outcome_loss_with_duration(self):
        """P6 (O1.2): Position closed with realized loss."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="s1",
            entry_price=100.0,
            exit_price=90.0,
            holding_days=3.2,
            pnl=-10.0,
            pnl_pct=-0.10,
            mfe=0.01,
            mae=0.11,
            edge_ratio=0.09,
        )
        assert "Position closed at $90.00 after 3.2 days, realizing a loss of -$10.00 (-10.0%)." in text
        assert_zero_leakage(text)

    def test_permutation_07_outcome_breakeven(self):
        """P7 (O1.3): Position closed at breakeven ($0.00 PnL)."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="s1",
            entry_price=100.0,
            exit_price=100.0,
            holding_days=1.0,
            pnl=0.0,
            pnl_pct=0.0,
            mfe=0.02,
            mae=0.02,
            edge_ratio=1.0,
        )
        assert "Position closed at $100.00 after 1.0 days at breakeven ($0.00 realized PnL)." in text
        assert_zero_leakage(text)

    def test_permutation_08_outcome_missing_duration(self):
        """P8 (O1.4): Position closed without recorded holding duration."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="s1",
            entry_price=100.0,
            exit_price=105.0,
            holding_days=None,
            pnl=5.0,
            pnl_pct=0.05,
            mfe=0.05,
            mae=0.01,
            edge_ratio=5.0,
        )
        assert "Position closed at $105.00 (holding duration unrecorded), realizing a gain of +$5.00 (+5.0%)." in text
        assert_zero_leakage(text)

    def test_permutation_09_degenerate_entry_price(self):
        """P9 (O1.5): Degenerate entry price <= 0.0 suppresses percentage return."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="s1",
            entry_price=0.0,
            exit_price=10.0,
            holding_days=1.0,
            pnl=10.0,
            pnl_pct=None,
            mfe=0.0,
            mae=0.0,
            edge_ratio=0.0,
        )
        assert "Position closed at $10.00 realizing $10.00 (percentage return unavailable due to degenerate entry price)." in text
        assert_zero_leakage(text)

    def test_permutation_10_bridge_unreached(self):
        """P10 (E1.4): Trade did not reach evaluation bridge."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="s1",
            entry_price=100.0,
            exit_price=105.0,
            holding_days=1.0,
            pnl=5.0,
            pnl_pct=0.05,
            bridge_reached=False,
        )
        assert "Hold-period excursion metrics unavailable (trade did not reach evaluation bridge)." in text
        assert_zero_leakage(text)

    def test_permutation_11_bars_missing(self):
        """P11 (E1.5): Hold-period pricing data missing or insufficient."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="s1",
            entry_price=100.0,
            exit_price=105.0,
            holding_days=1.0,
            pnl=5.0,
            pnl_pct=0.05,
            bars_available=False,
        )
        assert "Hold-period excursion metrics unavailable (pricing data missing for hold period)." in text
        assert_zero_leakage(text)

    def test_permutation_12_manual_standard(self):
        """P12 (S2.1 + E2.1): Standard manual trade with model calibration excluded."""
        text = build_trade_narrative(
            provenance="manual",
            side="buy",
            entry_price=100.0,
            exit_price=105.0,
            holding_days=1.0,
            pnl=5.0,
            pnl_pct=0.05,
            mfe=0.06,
            mae=0.01,
            edge_ratio=6.0,
        )
        assert "Manual discretionary buy trade executed by operator at $100.00." in text
        assert "model calibration not applicable for manual trades." in text
        assert_zero_leakage(text)

    def test_permutation_13_manual_with_operator_notes(self):
        """P13 (S2.2): Manual trade with operator note."""
        text = build_trade_narrative(
            provenance="manual",
            side="buy",
            entry_price=100.0,
            operator_notes="earnings hedge ahead of Q3 call",
            exit_price=105.0,
            holding_days=1.0,
            pnl=5.0,
            pnl_pct=0.05,
            mfe=0.06,
            mae=0.01,
            edge_ratio=6.0,
        )
        assert 'Manual discretionary buy trade executed by operator at $100.00 (note: "earnings hedge ahead of Q3 call").' in text
        assert "model calibration not applicable for manual trades." in text
        assert_zero_leakage(text)

    def test_permutation_14_unknown_standard(self):
        """P14 (S3.1 + E3.1): Unrecorded provenance with entry price captured."""
        text = build_trade_narrative(
            provenance="unknown",
            side="sell",
            entry_price=100.0,
            exit_price=95.0,
            holding_days=1.0,
            pnl=5.0,
            pnl_pct=0.05,
            mfe=0.06,
            mae=0.01,
            edge_ratio=6.0,
        )
        assert "Trade executed at $100.00 with unrecorded provenance (entry-time context not captured)." in text
        assert "conviction calibration unavailable (provenance unrecorded)." in text
        assert_zero_leakage(text)

    def test_permutation_15_unknown_missing_entry_price_unbridged(self):
        """P15 (S3.2 + E3.2): Unrecorded provenance, unverified entry price, unbridged."""
        text = build_trade_narrative(
            provenance="unknown",
            side="sell",
            entry_price=None,
            exit_price=95.0,
            holding_days=None,
            pnl=5.0,
            pnl_pct=None,
            bridge_reached=False,
        )
        assert "Trade executed with unrecorded provenance and unverified entry price." in text
        assert "Hold-period excursion metrics unavailable (trade did not reach evaluation bridge); conviction calibration unavailable." in text
        assert_zero_leakage(text)


# =============================================================================
# 3. Calibration Sample Size & Insufficient Data Handling
# =============================================================================

class TestCalibrationSampleHandling:
    """Test calibration binning with sufficient vs insufficient samples."""

    def test_insufficient_calibration_sample(self):
        """E1.2: Sample < min_sample explicitly states insufficient sample."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="alpha",
            entry_price=100.0,
            conviction=0.9,
            exit_price=105.0,
            pnl=5.0,
            pnl_pct=0.05,
            mfe=0.06,
            mae=0.01,
            edge_ratio=6.0,
            bin_win_rate=0.8,
            bin_count=3,
            min_sample=5,
        )
        assert "historical calibration unavailable for this conviction level (insufficient sample, N=3 < 5)" in text
        assert_zero_leakage(text)

    def test_sufficient_calibration_sample(self):
        """E1.1: Sample >= min_sample displays empirical win rate."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="alpha",
            entry_price=100.0,
            conviction=0.9,
            exit_price=105.0,
            pnl=5.0,
            pnl_pct=0.05,
            mfe=0.06,
            mae=0.01,
            edge_ratio=6.0,
            bin_win_rate=0.8,
            bin_count=5,
            min_sample=5,
        )
        assert "entry conviction binned at historical 80.0% win rate (N=5)" in text
        assert_zero_leakage(text)


# =============================================================================
# 4. Calling Conventions & Dictionary Input
# =============================================================================

class TestCallingConventions:
    """Test calling build_trade_narrative with dict, kwargs, or mixed."""

    def test_composed_record_dictionary_input(self):
        """Composed trade record dictionary from RetrospectiveComposer."""
        composed_record = {
            "trade_id": "t_101",
            "symbol": "NVDA",
            "side": "BUY",
            "entry_price": 450.0,
            "exit_price": 480.0,
            "holding_period_days": 5.0,
            "realized_pnl": 3000.0,
            "realized_pnl_pct": 0.0667,
            "provenance": "signal_driven",
            "strategy_id": "trend_following",
            "entry_snapshot": {
                "captured": True,
                "provenance": "signal_driven",
                "conviction": 0.88,
                "macro_regime": "expansion",
            },
            "bridge_status": "bridged",
            "excursion": {
                "evaluation_status": "available",
                "mae": 0.015,
                "mfe": 0.082,
                "edge_ratio": 5.47,
            },
            "calibration": {
                "bin_win_rate": 0.78,
                "bin_trade_count": 9,
            },
        }
        text = build_trade_narrative(composed_record)
        assert "Signal-driven buy trade entered on trend_following recommendation at $450.00" in text
        assert "(conviction: 0.88, regime: expansion)" in text
        assert "Position closed at $480.00 after 5.0 days, realizing a gain of +$3,000.00 (+6.7%)" in text
        assert "MFE +8.2% vs MAE -1.5% (Edge Ratio: 5.47)" in text
        assert "entry conviction binned at historical 78.0% win rate (N=9)" in text
        assert_zero_leakage(text)

    def test_captured_false_forces_unrecorded_provenance(self):
        """If captured is False, provenance must strictly be unknown even if strategy_id exists."""
        trade_record = {
            "side": "BUY",
            "entry_price": 100.0,
            "exit_price": 110.0,
            "realized_pnl": 10.0,
            "strategy_id": "trend_following",
            "entry_snapshot": {
                "captured": False,
                "provenance": "unknown",
            },
        }
        text = build_trade_narrative(trade_record)
        assert "with unrecorded provenance (entry-time context not captured)" in text
        assert "trend_following" not in text
        assert_zero_leakage(text)

    def test_positional_arguments_compatibility(self):
        """Positional arguments calling convention."""
        text = build_trade_narrative(
            "signal_driven",
            "buy",
            "my_strategy",
            100.0,
            0.8,
            "expansion",
            None,
            110.0,
            2.0,
            10.0,
            0.1,
            0.12,
            0.02,
            6.0,
            0.75,
            10,
        )
        assert "Signal-driven buy trade entered on my_strategy recommendation" in text
        assert_zero_leakage(text)


# =============================================================================
# 5. Adversarial Inputs & Anti-Fabrication Stress Tests
# =============================================================================

class TestAdversarialInputsAndAntiFabrication:
    """Test adversarial edge cases, string injection, and NaN/Inf floats."""

    def test_nan_and_inf_floats_sanitized(self):
        """NaN and Inf floats must never leak into narrative text."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="bad_math",
            entry_price=float("nan"),
            conviction=float("nan"),
            exit_price=float("inf"),
            holding_days=float("-inf"),
            pnl=float("nan"),
            pnl_pct=float("nan"),
            mfe=float("nan"),
            mae=float("nan"),
            edge_ratio=float("nan"),
        )
        assert_zero_leakage(text)
        assert "at unrecorded" in text
        assert "Hold-period excursion metrics unavailable" in text

    def test_adversarial_string_tokens(self):
        """Strings like 'None', 'NaN', 'null' in metadata are safely neutralized."""
        text = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="None",
            macro_regime="null",
            operator_notes="NaN",
            entry_price=100.0,
            exit_price=110.0,
            pnl=10.0,
            pnl_pct=0.1,
        )
        assert_zero_leakage(text)

    def test_unknown_arbitrary_provenance_fallback(self):
        """Arbitrary provenance string (e.g. 'alien_algo') defaults to unrecorded."""
        text = build_trade_narrative(
            provenance="quantum_telepathy",
            side="sell",
            entry_price=50.0,
            exit_price=45.0,
            pnl=5.0,
            pnl_pct=0.1,
        )
        assert "unrecorded provenance" in text
        assert_zero_leakage(text)
