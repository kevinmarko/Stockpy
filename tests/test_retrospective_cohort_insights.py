"""tests/test_retrospective_cohort_insights.py — Unit Tests for Batch Cohort Insights
===================================================================================

Authoritative Requirements:
- .agents/ORIGINAL_REQUEST.md (§ R5, WP-G)
- .agents/PROJECT.md (§ 3 Batch Analytics, Milestone 3)
- .agents/worker_m3/DISPATCH.md
- .agents/explorer_survey_2/retrospective_learning_loop_survey_report.md (§ 6)

Coverage:
1. Structural cohort separation: zero combined aggregate performance metrics at root level.
2. Quant-integrity independence invariant: adding manual trades never alters automated metrics.
3. Empty cohort and division-by-zero guards across all metrics.
4. Per-strategy aggregation within automated cohort.
5. Calibration Brier score mathematical accuracy.
6. Contrastive analytical insights synthesis.
7. Bridge health telemetry calculations.
"""

from __future__ import annotations

import math
import re

from pilots.retrospective_insights import (
    generate_batch_retrospective_insights,
)

FORBIDDEN_ROOT_KEYS = {
    "win_rate",
    "total_win_rate",
    "blended_win_rate",
    "aggregate_win_rate",
    "overall_win_rate",
    "total_pnl",
    "blended_pnl",
    "aggregate_pnl",
    "profit_factor",
    "blended_profit_factor",
    "aggregate_profit_factor",
    "blended_sharpe",
    "aggregate_sharpe",
}

FORBIDDEN_LEAKAGE_REGEX = re.compile(r"\b(None|NaN|nan|null)\b", re.IGNORECASE)


# =============================================================================
# 1. Structural Cohort Separation (WP-G)
# =============================================================================

class TestStructuralCohortSeparation:
    """Verify structural cohort separation and prohibition of aggregate metrics."""

    def test_top_level_schema_strictly_partitioned(self):
        """Top-level dictionary must contain cohort keys and NO blended performance metrics."""
        records = [
            {"provenance": "signal_driven", "realized_pnl": 100.0},
            {"provenance": "manual", "realized_pnl": -50.0},
        ]
        result = generate_batch_retrospective_insights(records)

        assert "automated_cohort" in result
        assert "signal_driven_cohort" in result  # Compatibility alias
        assert "manual_cohort" in result
        assert "unrecorded_cohort" in result
        assert "contrastive_insights" in result
        assert "bridge_health" in result

        for k in FORBIDDEN_ROOT_KEYS:
            assert k not in result, f"WP-G Violation: Found blended aggregate key '{k}' at root level!"

    def test_independence_adding_manual_trades_does_not_change_automated_metrics(self):
        """Mathematical invariant: Adding manual trades must NEVER alter automated cohort metrics."""
        automated_trades = [
            {
                "provenance": "signal_driven",
                "strategy_id": "trend",
                "realized_pnl": 500.0,
                "holding_period_days": 5.0,
                "conviction": 0.8,
                "excursion": {"mae": 0.02, "mfe": 0.10, "edge_ratio": 5.0},
            },
            {
                "provenance": "signal_driven",
                "strategy_id": "trend",
                "realized_pnl": -200.0,
                "holding_period_days": 3.0,
                "conviction": 0.6,
                "excursion": {"mae": 0.04, "mfe": 0.02, "edge_ratio": 0.5},
            },
            {
                "provenance": "signal_driven",
                "strategy_id": "mean_revert",
                "realized_pnl": 300.0,
                "holding_period_days": 2.0,
                "conviction": 0.75,
                "excursion": {"mae": 0.01, "mfe": 0.06, "edge_ratio": 6.0},
            },
        ]

        # 1. Run with automated trades only
        insights_auto_only = generate_batch_retrospective_insights(automated_trades)
        auto_metrics_before = insights_auto_only["automated_cohort"]

        # 2. Add manual trades (some large wins, some large losses, different durations)
        manual_trades = [
            {"provenance": "manual", "realized_pnl": -1000.0, "holding_period_days": 10.0},
            {"provenance": "manual", "realized_pnl": 5000.0, "holding_period_days": 0.5},
            {"provenance": "manual", "realized_pnl": -500.0, "holding_period_days": 1.0},
        ]
        mixed_batch = automated_trades + manual_trades
        insights_mixed = generate_batch_retrospective_insights(mixed_batch)
        auto_metrics_after = insights_mixed["automated_cohort"]

        # 3. Assert automated metrics are 100% identical
        assert auto_metrics_after["total_trades"] == auto_metrics_before["total_trades"]
        assert auto_metrics_after["winning_trades"] == auto_metrics_before["winning_trades"]
        assert auto_metrics_after["losing_trades"] == auto_metrics_before["losing_trades"]
        assert math.isclose(auto_metrics_after["win_rate"], auto_metrics_before["win_rate"], abs_tol=1e-6)
        assert math.isclose(auto_metrics_after["total_realized_pnl"], auto_metrics_before["total_realized_pnl"], abs_tol=1e-6)
        assert math.isclose(auto_metrics_after["profit_factor"], auto_metrics_before["profit_factor"], abs_tol=1e-6)
        assert math.isclose(auto_metrics_after["mean_holding_period_days"], auto_metrics_before["mean_holding_period_days"], abs_tol=1e-6)
        assert math.isclose(auto_metrics_after["mean_edge_ratio"], auto_metrics_before["mean_edge_ratio"], abs_tol=1e-6)
        assert math.isclose(auto_metrics_after["calibration_brier_score"], auto_metrics_before["calibration_brier_score"], abs_tol=1e-6)


# =============================================================================
# 2. Boundary & Corner Cases (Zero Division Guards)
# =============================================================================

class TestBoundaryAndCornerCases:
    """Test empty cohorts, breakeven trades, single-trade cohorts."""

    def test_empty_batch_zero_division_guard(self):
        """Empty input list returns clean zero-count dictionaries without division by zero."""
        result = generate_batch_retrospective_insights([])

        for cohort_key in ("automated_cohort", "manual_cohort", "unrecorded_cohort"):
            cohort = result[cohort_key]
            assert cohort["total_trades"] == 0
            assert cohort["trade_count"] == 0
            assert cohort["winning_trades"] == 0
            assert cohort["losing_trades"] == 0
            assert cohort["win_rate"] is None
            assert cohort["total_realized_pnl"] == 0.0
            assert cohort["profit_factor"] is None
            assert cohort["mean_holding_period_days"] is None
            assert cohort["mean_edge_ratio"] is None

        assert len(result["contrastive_insights"]) > 0
        assert "No closed trades available" in result["contrastive_insights"][0]
        assert result["bridge_health"]["total_closed_trades"] == 0
        assert result["bridge_health"]["completeness_pct"] == 100.0

    def test_cohort_all_breakeven_trades(self):
        """Cohort where all trades have 0.0 PnL returns win_rate=0.0."""
        trades = [
            {"provenance": "signal_driven", "realized_pnl": 0.0},
            {"provenance": "signal_driven", "realized_pnl": 0.0},
            {"provenance": "signal_driven", "realized_pnl": 0.0},
        ]
        result = generate_batch_retrospective_insights(trades)
        cohort = result["automated_cohort"]
        assert cohort["total_trades"] == 3
        assert cohort["winning_trades"] == 0
        assert cohort["breakeven_trades"] == 3
        assert cohort["win_rate"] == 0.0
        assert cohort["profit_factor"] is None

    def test_single_trade_cohort_no_crash(self):
        """Single trade cohort calculates clean metrics without volatility crash."""
        trades = [
            {
                "provenance": "manual",
                "realized_pnl": 150.0,
                "holding_period_days": 2.5,
                "symbol": "AAPL",
                "excursion": {"mae": 0.01, "mfe": 0.05, "edge_ratio": 5.0},
            }
        ]
        result = generate_batch_retrospective_insights(trades)
        cohort = result["manual_cohort"]
        assert cohort["total_trades"] == 1
        assert cohort["winning_trades"] == 1
        assert cohort["win_rate"] == 1.0
        assert cohort["total_realized_pnl"] == 150.0
        assert cohort["mean_holding_period_days"] == 2.5
        assert cohort["calibration_status"] == "not_applicable"


# =============================================================================
# 3. Strategy Aggregation & Calibration Accuracy
# =============================================================================

class TestStrategyAggregationAndCalibration:
    """Test per-strategy breakdown and Brier score accuracy."""

    def test_per_strategy_aggregation_in_automated_cohort(self):
        """Automated cohort partitions metrics by strategy_id."""
        trades = [
            {"provenance": "signal_driven", "strategy_id": "trend", "realized_pnl": 100.0, "excursion": {"edge_ratio": 4.0}},
            {"provenance": "signal_driven", "strategy_id": "trend", "realized_pnl": 200.0, "excursion": {"edge_ratio": 6.0}},
            {"provenance": "signal_driven", "strategy_id": "mean_revert", "realized_pnl": -50.0, "excursion": {"edge_ratio": 1.0}},
            {"provenance": "manual", "strategy_id": "discretionary", "realized_pnl": 500.0},
        ]
        result = generate_batch_retrospective_insights(trades)
        strategies = result["automated_cohort"]["strategies"]

        assert "trend" in strategies
        assert "mean_revert" in strategies
        assert "discretionary" not in strategies, "Manual strategy must not appear in automated breakdown"

        assert strategies["trend"]["trades"] == 2
        assert strategies["trend"]["winning_trades"] == 2
        assert strategies["trend"]["win_rate"] == 1.0
        assert strategies["trend"]["total_pnl"] == 300.0
        assert math.isclose(strategies["trend"]["mean_edge_ratio"], 5.0)

        assert strategies["mean_revert"]["trades"] == 1
        assert strategies["mean_revert"]["winning_trades"] == 0
        assert strategies["mean_revert"]["win_rate"] == 0.0
        assert strategies["mean_revert"]["total_pnl"] == -50.0

    def test_calibration_brier_score_calculation(self):
        """Brier score = mean of (conviction - outcome)^2 where outcome is 1.0 for win, 0.0 for loss."""
        trades = [
            {"provenance": "signal_driven", "conviction": 0.8, "realized_pnl": 10.0},   # (0.8 - 1.0)^2 = 0.04
            {"provenance": "signal_driven", "conviction": 0.6, "realized_pnl": -10.0},  # (0.6 - 0.0)^2 = 0.36
            {"provenance": "signal_driven", "conviction": None, "realized_pnl": 5.0},   # Excluded
        ]
        result = generate_batch_retrospective_insights(trades)
        brier = result["automated_cohort"]["calibration_brier_score"]
        # Expected: (0.04 + 0.36) / 2 = 0.20
        assert brier is not None
        assert math.isclose(brier, 0.20, abs_tol=1e-4)


# =============================================================================
# 4. Contrastive Insights & Bridge Health
# =============================================================================

class TestContrastiveInsightsAndBridgeHealth:
    """Test qualitative contrastive text generation and bridge completeness."""

    def test_contrastive_insights_synthesis_both_cohorts(self):
        """When both automated and manual cohorts exist, generate comparison strings."""
        trades = [
            {"provenance": "signal_driven", "realized_pnl": 100.0, "holding_period_days": 10.0, "excursion": {"mae": 0.02, "edge_ratio": 3.0}, "conviction": 0.8},
            {"provenance": "manual", "realized_pnl": -50.0, "holding_period_days": 2.0, "excursion": {"mae": 0.06, "edge_ratio": 1.2}},
            {"provenance": "unknown", "realized_pnl": 20.0},
        ]
        result = generate_batch_retrospective_insights(trades)
        insights = result["contrastive_insights"]

        assert len(insights) >= 3
        full_text = " ".join(insights)
        assert "Automated strategies achieved" in full_text
        assert "compared to manual discretionary trading" in full_text
        assert "Manual trades experienced higher average adverse excursion" in full_text
        assert "Model conviction calibration is operating" in full_text
        assert "historical trades were executed with unrecorded provenance" in full_text

        # WP-F check on contrastive strings
        match = FORBIDDEN_LEAKAGE_REGEX.search(full_text)
        assert match is None, f"Leaked forbidden token '{match.group()}' in insights: '{full_text}'"

    def test_bridge_health_telemetry_calculation(self):
        """Bridge health correctly tallies status across records."""
        trades = [
            {"provenance": "signal_driven", "bridge_status": "bridged"},
            {"provenance": "signal_driven", "bridge_status": "bridged"},
            {"provenance": "manual", "bridge_status": "failed"},
            {"provenance": "unknown", "bridge_status": "disabled"},
        ]
        result = generate_batch_retrospective_insights(trades)
        health = result["bridge_health"]

        assert health["total_closed_trades"] == 4
        assert health["bridged_count"] == 2
        assert health["failed_count"] == 1
        assert health["disabled_count"] == 1
        assert math.isclose(health["completeness_pct"], 50.0)
        assert health["status"] == "degraded"
