"""
tests/test_production_steps_etf_transmission_multiplier.py
==========================================================
Unit tests for pipeline/production_steps.py::_apply_etf_transmission_multiplier
-- the per-cycle writeback that turns the measured ``ETF_Ownership_Pct`` /
``ETF_Comovement_R2`` columns into the ``ETF_Transmission_Multiplier`` column
that ``StrategyEvalStep``'s per-ticker loop feeds into
``StrategyEngine.evaluate_security`` -> ``sizing.position_sizer.size_position``.

Pins the two asymmetric honesty contracts this feature depends on:

* **Feature DISABLED -> the column is NaN.** The multiplier was never
  computed; NaN is the honest "not computed" (CONSTRAINT #4), and consumers
  degrade THAT to the 1.0 no-op themselves.
* **Feature ENABLED but coverage missing -> the cell is exactly 1.0, never
  NaN.** A NaN would make ``final_weight`` non-finite, and
  ``apply_portfolio_gross_cap`` EXCLUDES non-finite weights from its gross
  sum -- so a coverage gap would shrink the gross denominator and silently
  LOOSEN the portfolio-wide cap for every covered name. A data outage must
  never relax a risk limit.

Deliberately targets the module-level function directly rather than going
through ``StrategyEvalStep.run()`` (which imports main_orchestrator and its
full heavy engine chain at call time) -- same rationale as
tests/test_production_steps_sector_heat.py. No network calls are made:
``risk.etf_transmission.transmission_multiplier`` is a pure function.
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pandas as pd
import pytest

from pipeline.production_steps import _apply_etf_transmission_multiplier


def _df(rows):
    return pd.DataFrame(rows)


def _enabled():
    """Patch context: feature on, with the shipped default knobs."""
    return patch.multiple(
        "settings.settings",
        ETF_TRANSMISSION_SIZING_ENABLED=True,
        ETF_TRANSMISSION_MAX_DERATE=0.30,
        ETF_TRANSMISSION_OWNERSHIP_REFERENCE=0.20,
        ETF_TRANSMISSION_MIN_MULTIPLIER=0.50,
    )


class TestDisabledIsANoOp:
    def test_disabled_leaves_the_column_nan(self):
        df = _df([
            {"Symbol": "AAPL", "ETF_Ownership_Pct": 0.20, "ETF_Comovement_R2": 1.0},
            {"Symbol": "XOM", "ETF_Ownership_Pct": 0.05, "ETF_Comovement_R2": 0.4},
        ])
        with patch("settings.settings.ETF_TRANSMISSION_SIZING_ENABLED", False):
            _apply_etf_transmission_multiplier(df)
        # NaN, NOT a fabricated 1.0 -- "never computed" is a different claim
        # from "measured, and there is no transmission risk here".
        assert df["ETF_Transmission_Multiplier"].isna().all()

    def test_column_always_exists_so_downstream_row_get_is_safe(self):
        df = _df([{"Symbol": "AAPL"}])
        with patch("settings.settings.ETF_TRANSMISSION_SIZING_ENABLED", False):
            _apply_etf_transmission_multiplier(df)
        assert "ETF_Transmission_Multiplier" in df.columns


class TestEnabledComputation:
    def test_maps_the_derate_per_row(self):
        df = _df([
            # at reference ownership, full co-movement -> full 0.30 derate
            {"Symbol": "AAPL", "ETF_Ownership_Pct": 0.20, "ETF_Comovement_R2": 1.0},
            # half reference ownership, full co-movement -> half the derate
            {"Symbol": "MSFT", "ETF_Ownership_Pct": 0.10, "ETF_Comovement_R2": 1.0},
            # no co-movement -> nothing transmitted -> no derate
            {"Symbol": "XOM", "ETF_Ownership_Pct": 0.50, "ETF_Comovement_R2": 0.0},
        ])
        with _enabled():
            _apply_etf_transmission_multiplier(df)
        got = dict(zip(df["Symbol"], df["ETF_Transmission_Multiplier"]))
        assert got["AAPL"] == pytest.approx(0.70)
        assert got["MSFT"] == pytest.approx(0.85)
        assert got["XOM"] == pytest.approx(1.0)


class TestEnabledCoverageGapsAreOneNeverNaN:
    def test_missing_cells_become_exactly_one(self):
        df = _df([
            {"Symbol": "AAPL", "ETF_Ownership_Pct": float("nan"), "ETF_Comovement_R2": 1.0},
            {"Symbol": "MSFT", "ETF_Ownership_Pct": 0.20, "ETF_Comovement_R2": float("nan")},
            {"Symbol": "XOM", "ETF_Ownership_Pct": float("nan"), "ETF_Comovement_R2": float("nan")},
        ])
        with _enabled():
            _apply_etf_transmission_multiplier(df)
        assert not df["ETF_Transmission_Multiplier"].isna().any()
        assert (df["ETF_Transmission_Multiplier"] == 1.0).all()

    def test_measurement_columns_entirely_absent_still_yields_all_ones(self):
        """Agent C's ETF ownership/co-movement ingestion may simply not be
        configured -- an absent column must behave exactly like an all-NaN
        one, not raise and not produce NaN."""
        df = _df([{"Symbol": "AAPL"}, {"Symbol": "MSFT"}])
        with _enabled():
            _apply_etf_transmission_multiplier(df)
        assert (df["ETF_Transmission_Multiplier"] == 1.0).all()

    def test_coverage_gap_is_logged_once_per_cycle_with_a_count(self, caplog):
        """Never once per name -- a 500-name universe with the feature newly
        enabled would otherwise emit 500 identical lines every refresh."""
        df = _df([
            {"Symbol": s, "ETF_Ownership_Pct": float("nan"), "ETF_Comovement_R2": float("nan")}
            for s in ["A", "B", "C", "D", "E"]
        ])
        with _enabled():
            with caplog.at_level(logging.INFO, logger="ProductionPipeline"):
                _apply_etf_transmission_multiplier(df)
        gap_lines = [r for r in caplog.records if "ETF transmission derate" in r.getMessage()]
        assert len(gap_lines) == 1
        assert "5 of 5" in gap_lines[0].getMessage()

    def test_full_coverage_logs_nothing(self, caplog):
        df = _df([{"Symbol": "AAPL", "ETF_Ownership_Pct": 0.1, "ETF_Comovement_R2": 0.5}])
        with _enabled():
            with caplog.at_level(logging.INFO, logger="ProductionPipeline"):
                _apply_etf_transmission_multiplier(df)
        assert not [r for r in caplog.records if "ETF transmission derate" in r.getMessage()]


class TestNeverRaises:
    def test_a_computation_failure_degrades_the_column_to_nan(self):
        """CONSTRAINT #6: a bug in the overlay must not abort the cycle. The
        column degrades to NaN, which consumers read as the 1.0 no-op."""
        df = _df([{"Symbol": "AAPL", "ETF_Ownership_Pct": 0.2, "ETF_Comovement_R2": 1.0}])
        with _enabled():
            with patch(
                "risk.etf_transmission.transmission_multiplier",
                side_effect=RuntimeError("boom"),
            ):
                _apply_etf_transmission_multiplier(df)
        assert df["ETF_Transmission_Multiplier"].isna().all()

    def test_empty_universe(self):
        df = pd.DataFrame({"Symbol": []})
        with _enabled():
            _apply_etf_transmission_multiplier(df)
        assert "ETF_Transmission_Multiplier" in df.columns
        assert len(df) == 0
