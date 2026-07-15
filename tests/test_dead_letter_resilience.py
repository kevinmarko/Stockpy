"""
tests/test_dead_letter_resilience.py
=====================================
CONSTRAINT #6 sweep: "a dependency/internal-engine failure must degrade
gracefully, never crash the pipeline."

Companion to tests/test_no_fabricated_metrics.py (CONSTRAINT #4) -- this is a
broad, parametrized sweep across genuine gaps identified by a coverage
survey: the three try/except-guarded research_engine.AdvancedResearchEngine
methods, engine/advisory.evaluate()'s per-stage internal-engine failure
handling (every stage already has dead-letter behavior in production code,
but no test exercised the failure path directly until now), and
universe_engine.py's Wikipedia-scrape / local-CSV failure modes (an area
where the existing tests/test_universe.py makes only real, unmocked live
network calls and never exercises any failure path at all).

engine/trade_signals.py is deliberately excluded -- tests/test_trade_signals.py
(396 lines, 30+ tests) already comprehensively covers NaN conviction, missing
attributes, and broken-notify dead-lettering for that module.
"""

from __future__ import annotations

import math
import os
from unittest import mock

import numpy as np
import pandas as pd
import pytest

import universe_engine
from engine.advisory import evaluate
from research_engine import AdvancedResearchEngine
from transactions_store import TransactionsStore

# Reuse the established market-provider / bars / account-snapshot / heavy-engine
# helpers from tests/test_advisory.py rather than re-deriving them, per item #1's
# established convention of not duplicating fixture machinery across test files.
from tests.test_advisory import (
    _MOCK_TECH,
    _make_account_snapshot,
    _make_bars,
    _make_market_provider,
)


# ============================================================================
# research_engine.AdvancedResearchEngine — try/except-guarded methods
# ============================================================================

@pytest.fixture
def research_engine():
    return AdvancedResearchEngine(risk_free_rate=0.0425, real_yield=0.0215)


class TestRealizedSlippageDeadLetter:
    """None/empty/missing-column inputs degrade to 0.0 (a documented neutral,
    distinct from a fabricated nonzero slippage reading); malformed numeric
    content is caught by the function's own except block."""

    def test_none_transactions_df_returns_zero(self, research_engine):
        assert research_engine.calculate_realized_slippage(None) == 0.0

    def test_empty_dataframe_returns_zero(self, research_engine):
        assert research_engine.calculate_realized_slippage(pd.DataFrame()) == 0.0

    def test_missing_trans_code_column_returns_zero(self, research_engine):
        df = pd.DataFrame({"Amount": ["$100.00"], "Commission": ["$1.00"]})
        assert research_engine.calculate_realized_slippage(df) == 0.0

    def test_malformed_amount_values_are_caught_not_raised(self, research_engine):
        """'Amount' values that fail the $/, -strip + float() parse must be
        caught by the outer except and degrade to 0.0, never propagate."""
        df = pd.DataFrame({
            "Trans Code": ["BUY"],
            "Amount": ["not-a-number"],
            "Commission": ["$1.00"],
        })
        result = research_engine.calculate_realized_slippage(df)
        assert result == 0.0

    def test_missing_commission_column_falls_back_to_manual_calc(self, research_engine):
        """No 'Commission' column exercises the fallback Quantity*Price path
        -- must not raise even with garbage Quantity/Price values."""
        df = pd.DataFrame({
            "Trans Code": ["BUY"],
            "Amount": ["$1,000.00"],
            "Quantity": ["garbage"],
            "Price": ["also-garbage"],
        })
        result = research_engine.calculate_realized_slippage(df)
        assert isinstance(result, float)


class TestPortfolioCovarDependencyDeadLetter:
    """Single-column / malformed inputs degrade to 0.0; an all-NaN
    correlation result is explicitly converted from NaN to 0.0 (a deliberate
    design choice, not an accidental fabrication)."""

    def test_none_returns_zero(self, research_engine):
        assert research_engine.calculate_portfolio_covar_dependency(None) == 0.0

    def test_single_column_returns_zero(self, research_engine):
        df = pd.DataFrame({"AAPL": np.random.RandomState(1).normal(0, 0.01, 50)})
        assert research_engine.calculate_portfolio_covar_dependency(df) == 0.0

    def test_all_nan_column_correlation_converts_to_zero_not_nan(self, research_engine):
        """corr() on an all-NaN column produces an all-NaN correlation
        matrix; the function's own np.isnan check converts that to 0.0
        rather than surfacing NaN -- pin this deliberate choice."""
        df = pd.DataFrame({
            "AAPL": np.random.RandomState(2).normal(0, 0.01, 50),
            "ALLNAN": [np.nan] * 50,
        })
        result = research_engine.calculate_portfolio_covar_dependency(df)
        assert result == 0.0

    def test_malformed_dataframe_caught_not_raised(self, research_engine):
        """Object-dtype garbage that breaks .corr() internally must be caught
        by the except block and degrade to 0.0."""
        df = pd.DataFrame({
            "A": ["x", "y", "z", "w"],
            "B": [1.0, 2.0, 3.0, 4.0],
        })
        result = research_engine.calculate_portfolio_covar_dependency(df)
        assert result == 0.0


class TestRelativeStrengthMomentumSlopeDeadLetter:
    """Pairs with tests/test_no_fabricated_metrics.py's coverage of the
    None/short-series 0.0 paths: a degenerate (numerically singular) input
    that would otherwise raise inside np.polyfit must also be caught."""

    def test_degenerate_identical_value_series_does_not_raise(self, research_engine):
        flat = pd.Series(np.full(60, 100.0))
        result = research_engine.calculate_relative_strength_momentum_slope(flat, flat)
        assert isinstance(result, float)


# ============================================================================
# engine/advisory.evaluate() — per-stage internal-engine failure handling
# ============================================================================

def _ts() -> TransactionsStore:
    return TransactionsStore(db_url="sqlite:///:memory:")


class TestAdvisoryEvaluatePerStageDeadLetter:
    """Every stage of evaluate() already has documented try/except dead-letter
    handling in production code (engine/advisory.py lines 280-470); no
    existing test exercised the actual failure path for stages 3/4/5/6/8
    before this file. data_quality is computed as 'PARTIAL' whenever
    partial_flags is non-empty (line 778), independent of which stage(s)
    failed."""

    def test_fundamentals_fetch_failure_falls_back_to_default_dto(self, monkeypatch):
        from settings import settings
        monkeypatch.setattr(settings, "HISTORICAL_STORE_ENABLED", False)
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))
        market.get_fundamentals.side_effect = Exception("fundamentals_network_error")

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:
            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 102.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.02,
            }
            rec = evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=_ts())

        assert rec.data_quality == "PARTIAL"
        assert rec.action in ("BUY", "SELL", "HOLD")

    def test_technical_metrics_failure_leaves_tech_empty_but_still_returns(self):
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:
            MockPE.return_value.calculate_technical_metrics.side_effect = Exception("tech_calc_error")
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 102.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.02,
            }
            rec = evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=_ts())

        assert rec.data_quality == "PARTIAL"

    def test_garch_vol_failure_surfaces_as_nan_not_fabricated_default(self):
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:
            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 102.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.side_effect = Exception("garch_fit_error")
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.02,
            }
            rec = evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=_ts())

        assert rec.data_quality == "PARTIAL"
        assert math.isnan(rec.key_indicators.get("garch_vol", float("nan")))

    def test_forecast_failure_leaves_forecast_none(self):
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:
            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.side_effect = Exception("forecast_model_error")
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.02,
            }
            rec = evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=_ts())

        assert rec.data_quality == "PARTIAL"
        assert rec.forecast is None

    def test_strategy_engine_failure_still_returns_valid_recommendation(self):
        """The central scoring call failing must still produce a complete,
        well-formed Recommendation via the pre-initialized HOLD/50/0.0
        defaults (engine/advisory.py lines 427-470) -- never raises."""
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:
            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 102.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.side_effect = Exception("strategy_engine_error")
            rec = evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=_ts())

        assert rec.data_quality == "PARTIAL"
        assert rec.action in ("BUY", "SELL", "HOLD")
        assert isinstance(rec.conviction, float)

    def test_multiple_simultaneous_failures_are_independent_and_still_return(self):
        """Two engines failing in the same cycle must not interact badly --
        partial_flags just accumulates both reasons, data_quality stays
        PARTIAL (not some worse/crashed state), and a valid Recommendation
        is still produced."""
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:
            MockPE.return_value.calculate_technical_metrics.side_effect = Exception("tech_calc_error")
            MockFE.return_value.generate_forecast.side_effect = Exception("forecast_model_error")
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.02,
            }
            rec = evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=_ts())

        assert rec.data_quality == "PARTIAL"
        assert rec.forecast is None
        assert rec.action in ("BUY", "SELL", "HOLD")


# ============================================================================
# universe_engine.py — Wikipedia scrape / local CSV failure modes
#
# tests/test_universe.py makes real, unmocked live Wikipedia network calls in
# every existing test (a separate pre-existing issue, not touched here). All
# new tests below mock requests.get/pd.read_html (never live network) and
# monkeypatch CACHE_PATH/DELISTED_PATH to tmp_path locations so they never
# read/write the real repo-shipped cache/seed files.
# ============================================================================

@pytest.fixture(autouse=True)
def _isolated_universe_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(universe_engine, "CACHE_PATH", str(tmp_path / "universe_cache.parquet"))
    monkeypatch.setattr(universe_engine, "DELISTED_PATH", str(tmp_path / "delisted_tickers.csv"))


def _changes_table() -> pd.DataFrame:
    return pd.DataFrame({
        "Date": ["June 1, 2024"],
        "Added Ticker": ["NEW"],
        "Removed Ticker": ["OLD"],
    })


class TestFetchAndCacheUniverseNetworkFailure:
    def test_network_failure_with_cache_present_falls_back_to_stale_cache(self):
        cached = pd.DataFrame({
            "type": ["current"], "date": ["2024-01-01"],
            "added_ticker": ["AAPL"], "removed_ticker": [None],
        })
        cached.to_parquet(universe_engine.CACHE_PATH, index=False)

        with mock.patch("universe_engine.requests.get", side_effect=ConnectionError("network down")):
            result = universe_engine.fetch_and_cache_universe()

        pd.testing.assert_frame_equal(result.reset_index(drop=True), cached.reset_index(drop=True))

    def test_network_failure_with_no_cache_raises_runtime_error(self):
        assert not os.path.exists(universe_engine.CACHE_PATH)
        with mock.patch("universe_engine.requests.get", side_effect=ConnectionError("network down")):
            with pytest.raises(RuntimeError, match="Failed to scrape Wikipedia and no cache found"):
                universe_engine.fetch_and_cache_universe()


class TestFetchAndCacheUniverseMalformedTable:
    def _mock_response(self):
        resp = mock.MagicMock()
        resp.raise_for_status.return_value = None
        resp.text = "<html></html>"
        return resp

    def test_too_few_tables_raises_value_error(self):
        with mock.patch("universe_engine.requests.get", return_value=self._mock_response()), \
             mock.patch("universe_engine.pd.read_html", return_value=[pd.DataFrame({"Symbol": ["AAPL"]})]):
            with pytest.raises(ValueError, match="tables not found"):
                universe_engine.fetch_and_cache_universe()

    def test_missing_symbol_column_raises_value_error(self):
        current_no_symbol = pd.DataFrame({"NotASymbolColumn": ["AAPL"]})
        with mock.patch("universe_engine.requests.get", return_value=self._mock_response()), \
             mock.patch("universe_engine.pd.read_html", return_value=[current_no_symbol, self._changes_table()]):
            with pytest.raises(ValueError, match="Symbol/Ticker column"):
                universe_engine.fetch_and_cache_universe()

    def test_missing_changes_columns_raises_value_error(self):
        current_df = pd.DataFrame({"Symbol": ["AAPL"]})
        changes_no_cols = pd.DataFrame({"Something": ["irrelevant"]})
        with mock.patch("universe_engine.requests.get", return_value=self._mock_response()), \
             mock.patch("universe_engine.pd.read_html", return_value=[current_df, changes_no_cols]):
            with pytest.raises(ValueError, match="Date, Added Ticker, or Removed Ticker"):
                universe_engine.fetch_and_cache_universe()

    def _changes_table(self):
        return _changes_table()

    def test_single_malformed_change_row_is_skipped_not_raised(self):
        """A bad DATE on one row (not a missing column) must be logged and
        skipped via the inner try/except -- the function still completes and
        returns the other valid rows, rather than raising for one bad row.

        A plain unparseable string is not good enough here since
        pd.to_datetime's lenient parser may still coerce it in some pandas
        versions; use an object that raises on str() to guarantee the
        row-level except triggers regardless of date-parsing leniency."""
        current_df = pd.DataFrame({"Symbol": ["AAPL"]})

        class _UnparseableDate:
            def __str__(self):
                raise RuntimeError("cannot stringify")

        changes_df_guaranteed = pd.DataFrame({
            "Date": [_UnparseableDate(), "June 1, 2024"],
            "Added Ticker": ["BAD", "NEW"],
            "Removed Ticker": [None, "OLD"],
        })
        with mock.patch("universe_engine.requests.get", return_value=self._mock_response()), \
             mock.patch("universe_engine.pd.read_html", return_value=[current_df, changes_df_guaranteed]):
            result = universe_engine.fetch_and_cache_universe()

        # The valid "NEW"/"OLD" change row must still be present.
        change_rows = result[result["type"] == "change"]
        assert "NEW" in change_rows["added_ticker"].values


class TestLoadUniverseDataFallbackLayer:
    """load_universe_data() is a second dead-letter layer on top of
    fetch_and_cache_universe(): it catches that function's raise and falls
    back to a stale cache if present, else re-raises."""

    def test_fetch_failure_with_cache_falls_back(self):
        cached = pd.DataFrame({
            "type": ["current"], "date": ["2024-01-01"],
            "added_ticker": ["AAPL"], "removed_ticker": [None],
        })
        cached.to_parquet(universe_engine.CACHE_PATH, index=False)
        # Cache exists but is "stale" by virtue of refresh always being
        # attempted when load_universe_data can't see a fresh mtime — force
        # the refresh path directly by mocking fetch_and_cache_universe.
        with mock.patch("universe_engine.fetch_and_cache_universe", side_effect=RuntimeError("scrape failed")):
            result = universe_engine.load_universe_data()

        pd.testing.assert_frame_equal(result.reset_index(drop=True), cached.reset_index(drop=True))

    def test_fetch_failure_with_no_cache_reraises(self):
        assert not os.path.exists(universe_engine.CACHE_PATH)
        with mock.patch("universe_engine.fetch_and_cache_universe", side_effect=RuntimeError("scrape failed")):
            with pytest.raises(RuntimeError, match="scrape failed"):
                universe_engine.load_universe_data()


class TestGetDelistedTickersDeadLetter:
    def test_missing_csv_file_returns_empty_correctly_schemed_dataframe(self):
        assert not os.path.exists(universe_engine.DELISTED_PATH)
        result = universe_engine.get_delisted_tickers()
        assert list(result.columns) == ["ticker", "company", "delisting_date", "reason"]
        assert len(result) == 0

    def test_malformed_csv_missing_required_column_raises(self):
        """Unlike the missing-file case, a malformed CSV (present but wrong
        schema) has NO try/except guard in production code and WILL raise.
        This pins the current (acceptable, since it's a local
        version-controlled seed file, not an untrusted external dependency)
        behavior rather than assuming graceful degradation that doesn't
        actually exist."""
        pd.DataFrame({"ticker": ["XYZ"], "company": ["Xyz Corp"]}).to_csv(
            universe_engine.DELISTED_PATH, index=False
        )
        with pytest.raises(KeyError):
            universe_engine.get_delisted_tickers()
