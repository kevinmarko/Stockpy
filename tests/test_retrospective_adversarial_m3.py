"""tests/test_retrospective_adversarial_m3.py — Milestone 3 Adversarial Challenge Suite
=====================================================================================

Authoritative Challenge Mandate (Milestone 3 Challenger):
1. Adversarially stress test build_trade_narrative with extreme/pathological inputs
   (NaN, Inf, -Inf, negative prices, degenerate entry prices, empty records, non-numeric types).
   Assert zero unhandled exceptions and zero forbidden tokens (None, NaN, nan, null).
2. Adversarially challenge cohort separation in generate_batch_retrospective_insights
   with heavily biased/extreme trades (e.g. 100 manual trades with 100% loss rate and -$1,000,000 PnL
   alongside 5 automated trades with 100% win rate and +$10,000 PnL).
   Confirm automated metrics are 100% immune to manual trade variations, and zero blended
   aggregate keys exist at the root level.
"""

from __future__ import annotations

import math
import random
import re
from typing import Any

import pytest

from pilots.retrospective_insights import (
    generate_batch_retrospective_insights,
)
from pilots.retrospective_narrative import (
    build_trade_narrative,
)

FORBIDDEN_TOKEN_REGEX = re.compile(r"\b(None|NaN|nan|null)\b")

FORBIDDEN_ROOT_KEYS = {
    "win_rate",
    "total_win_rate",
    "blended_win_rate",
    "aggregate_win_rate",
    "overall_win_rate",
    "total_pnl",
    "blended_pnl",
    "aggregate_pnl",
    "realized_pnl",
    "total_realized_pnl",
    "net_pnl",
    "profit_factor",
    "blended_profit_factor",
    "aggregate_profit_factor",
    "sharpe",
    "blended_sharpe",
    "aggregate_sharpe",
    "edge_ratio",
    "mean_edge_ratio",
    "holding_period_days",
    "mean_holding_period_days",
    "trades",
    "total_trades",
    "trade_count",
}


def assert_zero_forbidden_tokens(text: str) -> None:
    """Strict assertion: no forbidden token (None, NaN, nan, null) appears in output text."""
    match = FORBIDDEN_TOKEN_REGEX.search(text)
    assert match is None, f"Forbidden token '{match.group()}' leaked in text: '{text}'"


# =============================================================================
# SUITE 1: Adversarial Narrative Generator Stress Harness
# =============================================================================

class TestAdversarialNarrativeGenerator:
    """Adversarially stress test build_trade_narrative across pathological inputs."""

    def test_narrative_extreme_degenerate_floats(self):
        """Feed NaN, Inf, -Inf, huge numbers across all numeric argument slots."""
        extreme_values = [
            float("nan"),
            float("inf"),
            float("-inf"),
            1e308,
            -1e308,
            1e-308,
            0.0,
            -0.0,
        ]

        for val in extreme_values:
            text = build_trade_narrative(
                provenance="signal_driven",
                side="buy",
                strategy_id="momentum_extreme",
                entry_price=val,
                conviction=val,
                exit_price=val,
                holding_days=val,
                pnl=val,
                pnl_pct=val,
                mfe=val,
                mae=val,
                edge_ratio=val,
                bin_win_rate=val,
                bin_count=0,
            )
            assert isinstance(text, str)
            assert len(text) > 0
            assert_zero_forbidden_tokens(text)

    def test_narrative_negative_and_zero_prices(self):
        """Feed negative prices and zero entry prices. Verify degenerate entry price guard."""
        # Zero entry price
        text_zero_entry = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="deep_value",
            entry_price=0.0,
            exit_price=25.0,
            holding_days=3.0,
            pnl=25.0,
            pnl_pct=None,
        )
        assert "percentage return unavailable due to degenerate entry price" in text_zero_entry
        assert_zero_forbidden_tokens(text_zero_entry)

        # Negative entry price (e.g. oil futures anomaly)
        text_neg_entry = build_trade_narrative(
            provenance="signal_driven",
            side="buy",
            strategy_id="futures_arb",
            entry_price=-37.63,
            exit_price=10.0,
            holding_days=1.0,
            pnl=47.63,
            pnl_pct=None,
        )
        assert "-$37.63" in text_neg_entry
        assert "percentage return unavailable due to degenerate entry price" in text_neg_entry
        assert_zero_forbidden_tokens(text_neg_entry)

        # Negative exit price
        text_neg_exit = build_trade_narrative(
            provenance="manual",
            side="sell",
            entry_price=10.0,
            exit_price=-5.0,
            holding_days=0.5,
            pnl=-15.0,
            pnl_pct=-1.5,
        )
        assert "-$5.00" in text_neg_exit
        assert_zero_forbidden_tokens(text_neg_exit)

    def test_narrative_empty_and_all_none_records(self):
        """Feed empty dictionaries, all-None fields, and non-dictionary inputs."""
        # Completely empty dict
        t1 = build_trade_narrative({})
        assert "unrecorded provenance" in t1
        assert_zero_forbidden_tokens(t1)

        # Record with all nested sections set to None
        t2 = build_trade_narrative({
            "entry_snapshot": None,
            "excursion": None,
            "calibration": None,
            "entry_price": None,
            "exit_price": None,
            "realized_pnl": None,
            "holding_period_days": None,
        })
        assert "unrecorded provenance" in t2
        assert_zero_forbidden_tokens(t2)

        # Explicit None arguments
        t3 = build_trade_narrative(
            trade_record=None,
            side_or_provenance=None,
            strategy_id=None,
            entry_price=None,
            conviction=None,
            macro_regime=None,
            operator_notes=None,
            exit_price=None,
            holding_days=None,
            pnl=None,
            pnl_pct=None,
            mfe=None,
            mae=None,
            edge_ratio=None,
            bin_win_rate=None,
            bin_count=None,
        )
        assert "unrecorded provenance" in t3
        assert_zero_forbidden_tokens(t3)

        # Arbitrary non-dictionary inputs
        for non_dict in ["arbitrary_string", 12345, 99.9, True, False, [1, 2, 3]]:
            t_non_dict = build_trade_narrative(non_dict)  # type: ignore[arg-type]
            assert isinstance(t_non_dict, str)
            assert_zero_forbidden_tokens(t_non_dict)

    def test_narrative_adversarial_type_confusion(self):
        """Feed invalid types into numeric and string slots without crashing."""
        pathological_kwargs: dict[str, Any] = {
            "entry_price": "invalid_price_string",
            "exit_price": [100.0, 200.0],
            "pnl": {"pnl": 500.0},
            "pnl_pct": complex(1.0, 2.0),
            "holding_days": object(),
            "conviction": (0.8,),
            "mfe": lambda: 0.1,
            "mae": b"binary_data",
            "edge_ratio": [5.0],
            "bin_win_rate": "seventy_percent",
            "bin_count": "invalid_count",
            "min_sample": "five",
        }
        text = build_trade_narrative(provenance="signal_driven", side="buy", **pathological_kwargs)
        assert isinstance(text, str)
        assert len(text) > 0
        assert_zero_forbidden_tokens(text)

    def test_narrative_adversarial_string_tokens_and_injection(self):
        """Feed reserved token strings (None, NaN, nan, null) into text fields."""
        text = build_trade_narrative(
            provenance="None",
            side="null",
            strategy_id="NaN",
            macro_regime="nan",
            operator_notes="None",
            entry_price=100.0,
            exit_price=110.0,
            pnl=10.0,
            pnl_pct=0.1,
        )
        assert_zero_forbidden_tokens(text)

    def test_narrative_combinatorial_fuzzing_matrix(self):
        """Fuzz 300 randomized parameter permutations with pseudo-random seeds."""
        rng = random.Random(42)
        provenance_choices = ["signal_driven", "manual", "unknown", "arbitrary_prov", None]
        side_choices = ["buy", "sell", "long", "short", "hold", "unknown_side", None]
        price_choices = [None, -100.0, 0.0, 0.001, 150.0, float("nan"), float("inf")]
        pnl_choices = [None, -500.0, -0.0, 0.0, 500.0, float("nan"), float("-inf")]
        duration_choices = [None, 0.0, 0.5, 10.0, float("nan"), float("inf")]
        bridge_choices = [True, False]
        bars_choices = [True, False]

        for _ in range(300):
            prov = rng.choice(provenance_choices)
            side = rng.choice(side_choices)
            ep = rng.choice(price_choices)
            xp = rng.choice(price_choices)
            pnl = rng.choice(pnl_choices)
            dur = rng.choice(duration_choices)
            br = rng.choice(bridge_choices)
            bars = rng.choice(bars_choices)

            text = build_trade_narrative(
                provenance=prov,
                side=side,
                strategy_id="fuzz_strategy",
                entry_price=ep,
                exit_price=xp,
                pnl=pnl,
                holding_days=dur,
                bridge_reached=br,
                bars_available=bars,
            )
            assert isinstance(text, str)
            assert_zero_forbidden_tokens(text)


# =============================================================================
# SUITE 2: Adversarial Cohort Separation Stress Harness (WP-G)
# =============================================================================

class TestAdversarialCohortSeparation:
    """Adversarially challenge cohort separation in generate_batch_retrospective_insights."""

    @pytest.fixture
    def benchmark_automated_trades(self) -> list[dict[str, Any]]:
        """5 pure systematic automated trades: 100% win rate, +$10,000.00 total PnL."""
        return [
            {
                "provenance": "signal_driven",
                "strategy_id": "trend_alpha",
                "symbol": "AAPL",
                "realized_pnl": 2000.0,
                "holding_period_days": 4.0,
                "conviction": 0.90,
                "excursion": {"mae": 0.01, "mfe": 0.08, "edge_ratio": 8.0},
                "bridge_status": "bridged",
            },
            {
                "provenance": "signal_driven",
                "strategy_id": "trend_alpha",
                "symbol": "MSFT",
                "realized_pnl": 2500.0,
                "holding_period_days": 5.0,
                "conviction": 0.85,
                "excursion": {"mae": 0.02, "mfe": 0.09, "edge_ratio": 4.5},
                "bridge_status": "bridged",
            },
            {
                "provenance": "signal_driven",
                "strategy_id": "mean_reversion",
                "symbol": "GOOGL",
                "realized_pnl": 1500.0,
                "holding_period_days": 2.0,
                "conviction": 0.80,
                "excursion": {"mae": 0.01, "mfe": 0.05, "edge_ratio": 5.0},
                "bridge_status": "bridged",
            },
            {
                "provenance": "signal_driven",
                "strategy_id": "mean_reversion",
                "symbol": "AMZN",
                "realized_pnl": 3000.0,
                "holding_period_days": 3.0,
                "conviction": 0.95,
                "excursion": {"mae": 0.015, "mfe": 0.10, "edge_ratio": 6.67},
                "bridge_status": "bridged",
            },
            {
                "provenance": "signal_driven",
                "strategy_id": "trend_alpha",
                "symbol": "NVDA",
                "realized_pnl": 1000.0,
                "holding_period_days": 1.0,
                "conviction": 0.75,
                "excursion": {"mae": 0.005, "mfe": 0.04, "edge_ratio": 8.0},
                "bridge_status": "bridged",
            },
        ]

    def test_extreme_biased_manual_batch_isolation(self, benchmark_automated_trades):
        """Mix 100 heavily losing manual trades (-$1,000,000 PnL) with 5 automated trades.
        Assert automated cohort remains 100% win rate and +$10,000 PnL with 0.0 contamination.
        """
        # 100 catastrophic manual trades
        catastrophic_manual_trades = [
            {
                "provenance": "manual",
                "strategy_id": "trend_alpha",  # Attempted namespace collision!
                "symbol": f"LOSER_{i}",
                "realized_pnl": -10000.0,
                "holding_period_days": 45.0,
                "conviction": 0.99,  # Attempted calibration contamination!
                "excursion": {"mae": 0.80, "mfe": 0.0, "edge_ratio": 0.0},
                "bridge_status": "failed",
            }
            for i in range(100)
        ]

        # 50 unrecorded legacy trades
        unrecorded_legacy_trades = [
            {
                "provenance": "unknown",
                "symbol": f"LEGACY_{i}",
                "realized_pnl": -5000.0,
                "holding_period_days": 10.0,
            }
            for i in range(50)
        ]

        # Baseline run
        base_res = generate_batch_retrospective_insights(benchmark_automated_trades)
        base_auto = base_res["automated_cohort"]

        # Mixed run
        mixed_batch = benchmark_automated_trades + catastrophic_manual_trades + unrecorded_legacy_trades
        mixed_res = generate_batch_retrospective_insights(mixed_batch)
        mixed_auto = mixed_res["automated_cohort"]

        # Strict equality across all automated metrics
        assert mixed_auto["total_trades"] == 5
        assert mixed_auto["winning_trades"] == 5
        assert mixed_auto["losing_trades"] == 0
        assert mixed_auto["breakeven_trades"] == 0
        assert mixed_auto["win_rate"] == 1.0
        assert mixed_auto["total_realized_pnl"] == 10000.0
        assert mixed_auto["profit_factor"] is None  # Zero losses -> profit_factor is None
        assert math.isclose(mixed_auto["mean_holding_period_days"], 3.0, abs_tol=1e-6)
        assert math.isclose(mixed_auto["calibration_brier_score"], base_auto["calibration_brier_score"], abs_tol=1e-6)

        # Strategy isolation: trend_alpha in automated cohort must NOT contain manual trades
        trend_alpha = mixed_auto["strategies"]["trend_alpha"]
        assert trend_alpha["trades"] == 3
        assert trend_alpha["winning_trades"] == 3
        assert trend_alpha["win_rate"] == 1.0
        assert trend_alpha["total_pnl"] == 5500.0

        # Verify manual cohort accurately captured the catastrophic loss
        man_cohort = mixed_res["manual_cohort"]
        assert man_cohort["total_trades"] == 100
        assert man_cohort["winning_trades"] == 0
        assert man_cohort["losing_trades"] == 100
        assert man_cohort["win_rate"] == 0.0
        assert man_cohort["total_realized_pnl"] == -1000000.0
        assert man_cohort["calibration_status"] == "not_applicable"

    def test_zero_blended_aggregate_root_keys(self, benchmark_automated_trades):
        """Root response dictionary must contain ZERO blended/aggregate performance keys."""
        manual_trades = [
            {"provenance": "manual", "realized_pnl": 500.0},
            {"provenance": "manual", "realized_pnl": -300.0},
        ]
        result = generate_batch_retrospective_insights(benchmark_automated_trades + manual_trades)

        for forbidden_key in FORBIDDEN_ROOT_KEYS:
            assert forbidden_key not in result, f"WP-G Violation: Found blended aggregate key '{forbidden_key}'"

        # Allowed root keys check
        allowed_keys = {
            "automated_cohort",
            "signal_driven_cohort",
            "manual_cohort",
            "unrecorded_cohort",
            "contrastive_insights",
            "bridge_health",
        }
        assert set(result.keys()) == allowed_keys

    def test_mathematical_invariance_across_arbitrary_manual_batches(self, benchmark_automated_trades):
        """Property: Automated cohort metrics are strictly invariant to any manual trade set."""
        base_res = generate_batch_retrospective_insights(benchmark_automated_trades)
        base_auto = base_res["automated_cohort"]

        rng = random.Random(1337)
        for trial in range(10):
            # Generate arbitrary random manual trades
            num_manual = rng.randint(1, 50)
            random_manual = [
                {
                    "provenance": "manual",
                    "realized_pnl": rng.uniform(-50000.0, 50000.0),
                    "holding_period_days": rng.uniform(0.1, 100.0),
                    "symbol": f"SYM_{rng.randint(1, 20)}",
                    "conviction": rng.uniform(0.0, 1.0),
                    "strategy_id": rng.choice(["trend_alpha", "mean_reversion", "arbitrary_strat"]),
                }
                for _ in range(num_manual)
            ]

            trial_res = generate_batch_retrospective_insights(benchmark_automated_trades + random_manual)
            trial_auto = trial_res["automated_cohort"]

            assert trial_auto["total_trades"] == base_auto["total_trades"]
            assert trial_auto["winning_trades"] == base_auto["winning_trades"]
            assert trial_auto["losing_trades"] == base_auto["losing_trades"]
            assert trial_auto["win_rate"] == base_auto["win_rate"]
            assert math.isclose(trial_auto["total_realized_pnl"], base_auto["total_realized_pnl"], abs_tol=1e-6)
            assert trial_auto["profit_factor"] == base_auto["profit_factor"]
            assert math.isclose(trial_auto["mean_holding_period_days"], base_auto["mean_holding_period_days"], abs_tol=1e-6)
            assert math.isclose(trial_auto["calibration_brier_score"], base_auto["calibration_brier_score"], abs_tol=1e-6)

    def test_pathological_records_division_by_zero_guards(self):
        """Empty cohorts and degenerate records must never trigger division by zero."""
        pathological_records = [
            {},  # Empty record -> unknown provenance
            {"provenance": "signal_driven", "realized_pnl": float("nan")},
            {"provenance": "manual", "holding_period_days": float("inf")},
            {"provenance": "signal_driven", "conviction": float("nan"), "realized_pnl": 0.0},
        ]
        result = generate_batch_retrospective_insights(pathological_records)
        assert result["automated_cohort"]["total_trades"] == 2
        assert result["manual_cohort"]["total_trades"] == 1
        assert result["unrecorded_cohort"]["total_trades"] == 1

        # Check contrastive insights for zero token leakage
        insights_text = " ".join(result["contrastive_insights"])
        assert_zero_forbidden_tokens(insights_text)
