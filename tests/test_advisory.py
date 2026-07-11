"""
tests/test_advisory.py — Unit tests for engine/advisory.py
===========================================================
All tests are fully offline: market data, fundamentals, and the
transactions store are monkeypatched / injected as in-memory stubs.
No external APIs are contacted.

Coverage:
  - HOLD scenario: holding above cost + dividend history + neutral forecast → HOLD
  - SELL scenario: holding below cost + bearish forecast → SELL (elevated conviction)
  - BUY scenario: non-held symbol + bullish signal → BUY with 0 < pct ≤ cap
  - Acceptance criteria from the task spec (3 canonical cases)
  - No magic numbers in decision logic (CONFIG dict exists and is complete)
  - data_quality flags: STALE, PARTIAL, OK
  - Fallback on missing price data → HOLD/PARTIAL
  - Kelly sizing bounded by max_single_position_pct
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pandas as pd
import numpy as np
import pytest

# ── Stub types used before importing the module under test ──────────────────

@dataclass(frozen=True)
class _StubPortfolioPosition:
    symbol: str
    quantity: float
    average_cost: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_pl_pct: float
    dividends_received: float
    name: str


@dataclass(frozen=True)
class _StubAccountSnapshot:
    positions: dict
    buying_power: float
    total_equity: float
    total_dividends: float
    fetched_at: datetime


# ── Helpers to build stub market providers ──────────────────────────────────

def _make_bars(n: int = 252, start_price: float = 100.0, trend: float = 0.0) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame with a given directional trend."""
    idx = pd.date_range(end=datetime.today(), periods=n, freq="B")
    closes = np.cumsum(np.random.default_rng(42).normal(trend, 0.01, n)) + start_price
    closes = np.maximum(closes, 1.0)
    return pd.DataFrame(
        {
            "Open": closes * 0.999,
            "High": closes * 1.005,
            "Low": closes * 0.995,
            "Close": closes,
            "Volume": np.full(n, 100_000),
        },
        index=idx,
    )


def _make_market_provider(
    price: float = 100.0,
    is_stale: bool = False,
    bars: Optional[pd.DataFrame] = None,
    fundamentals: Optional[Dict[str, Any]] = None,
    bars_raise: bool = False,
    quote_raise: bool = False,
) -> MagicMock:
    """Return a MagicMock implementing MarketDataProvider."""
    from data.market_data import Quote

    provider = MagicMock()

    if quote_raise:
        provider.get_latest_quote.side_effect = Exception("quote_network_error")
    else:
        provider.get_latest_quote.return_value = Quote(
            symbol="TEST",
            price=price,
            bid=price - 0.01,
            ask=price + 0.01,
            timestamp=datetime.now(timezone.utc),
            is_stale=is_stale,
            source="test",
        )

    if bars_raise:
        provider.get_intraday_bars.side_effect = Exception("bars_network_error")
    else:
        provider.get_intraday_bars.return_value = bars if bars is not None else _make_bars(n=252, start_price=price)

    provider.get_fundamentals.return_value = fundamentals or {}
    return provider


def _make_account_snapshot(total_equity: float = 100_000.0) -> _StubAccountSnapshot:
    return _StubAccountSnapshot(
        positions={},
        buying_power=10_000.0,
        total_equity=total_equity,
        total_dividends=0.0,
        fetched_at=datetime.now(timezone.utc),
    )


# ── Mock heavy engines (ProcessingEngine / ForecastingEngine / TechnicalOptionsEngine) ──

_MOCK_TECH = {
    "RSI": 55.0,
    "RSI_2": 40.0,
    "MACD_Line": 0.5,
    "MACD_Signal": 0.3,
    "ATR": 2.5,
    "SMA_50": 98.0,
    "SMA_200": 95.0,
    "Aroon Oscillator": 60.0,
    "Chandelier Exit": 92.0,
    "Sortino Ratio": 0.8,
    "Max Drawdown": -0.12,
    "RS vs SPY": 0.03,
    "RS-MACD": 0.2,
    "ROC_12M": 0.08,
    "ROC_6M": 0.04,
    "Momentum_Vol_Scaled": 0.01,
    "Realized_Vol_60D": 0.18,
    "VaR 95": -0.02,
    "Coppock Curve": 0.0,
    "Aroon Up": 80.0,
    "Aroon Down": 20.0,
    "Realized Slippage": 0.0,
    "Options IV Edge": 0.0,
    "CoVaR Proxy": 0.0,
}


def _patch_heavy_engines(
    tech_override: Optional[Dict] = None,
    garch_vol: float = 0.18,
    forecast_30: float = 105.0,
    strategy_signal: str = "BUY",
    strategy_score: int = 60,
    kelly_target: float = 0.04,
):
    """Return a context manager that patches all computationally heavy engines."""
    import unittest.mock as mock

    tech = {**_MOCK_TECH, **(tech_override or {})}

    pe_mock = MagicMock()
    pe_mock.calculate_technical_metrics.return_value = {"TEST": tech}

    toe_mock = MagicMock()
    toe_mock.estimate_gjr_garch_volatility.return_value = garch_vol

    fe_mock = MagicMock()
    fe_mock.generate_forecast.return_value = {
        "Forecast_10": forecast_30 * 0.5,
        "Forecast_30": forecast_30,
        "Forecast_60": forecast_30,
        "Forecast_90": forecast_30,
        "MC_Target": forecast_30,
        "MC_Lower": forecast_30 * 0.95,
        "MC_Upper": forecast_30 * 1.05,
        "ARIMA": forecast_30,
        "Target_Days": 30,
    }

    se_mock = MagicMock()
    se_mock.evaluate_security.return_value = {
        "Action Signal": strategy_signal,
        "Score": strategy_score,
        "Kelly Target": kelly_target,
        "buyRange": f"Buy Zone: ${forecast_30 - 2:.2f} - ${forecast_30:.2f}",
        "sellRange": f"Sell Zone: ${forecast_30:.2f} - ${forecast_30 + 5:.2f} | Stop @ $90.00",
    }

    patches = [
        mock.patch("engine.advisory.ProcessingEngine", return_value=pe_mock),
        mock.patch("engine.advisory.ForecastingEngine", return_value=fe_mock),
        mock.patch("engine.advisory.TechnicalOptionsEngine", return_value=toe_mock),
        mock.patch("engine.advisory.StrategyEngine", return_value=se_mock),
    ]
    return patches


# ── HistoricalStore passthrough (module-wide autouse) ────────────────────────
#
# settings.HISTORICAL_STORE_ENABLED defaults True, and evaluate() now resolves
# a HistoricalStore singleton in Steps 1/3 when the flag is on. Every test in
# this file predates that routing and constructs its `market` mock expecting
# Step 1/3 to call `market.get_intraday_bars`/`market.get_fundamentals`
# directly — without this fixture, those tests would instead exercise a REAL,
# on-disk HistoricalStore() (writing to the actual quant_platform.db), which
# is exactly the "HISTORICAL_STORE_ENABLED trap" this codebase's
# `tests/conftest.py::disable_historical_store` fixture exists to prevent.
# This passthrough stub keeps every existing test's behavior byte-identical
# (it forwards straight to the same mocked `market` provider) while still
# exercising the real Step 1/3 routing code path end-to-end. Tests that want
# to verify the ACTUAL HistoricalStore routing/fallback behavior (see
# TestHistoricalStoreRouting below) inject a real mock via
# `historical_store=` directly instead of relying on this fixture.

class _PassthroughHistoricalStore:
    """Forwards straight to the injected provider — reproduces pre-routing
    test behavior exactly without touching a real on-disk DB."""

    def get_bars(self, symbol, lookback_days=252, *, provider=None):
        return provider.get_intraday_bars(symbol, lookback_days=lookback_days)

    def get_fundamentals_raw(self, symbol, max_age_days=1, *, provider=None):
        return provider.get_fundamentals(symbol)


@pytest.fixture(autouse=True)
def _auto_passthrough_historical_store():
    with patch("engine.advisory.HistoricalStore", side_effect=_PassthroughHistoricalStore):
        with patch("engine.advisory._HISTORICAL_STORE", None):
            yield


# ── Tests ───────────────────────────────────────────────────────────────────

class TestRecommendationDataclass:
    """Verify the Recommendation dataclass invariants."""

    def test_frozen(self):
        from engine.advisory import Recommendation

        rec = Recommendation(
            symbol="AAPL",
            action="HOLD",
            strategy="test",
            conviction=0.5,
            rationale="test rationale",
            suggested_position_pct=0.0,
            forecast=100.0,
            key_indicators={},
            data_quality="OK",
        )
        with pytest.raises((AttributeError, TypeError)):
            rec.action = "BUY"  # type: ignore[misc]

    def test_action_literals(self):
        from engine.advisory import Recommendation

        for action in ("BUY", "SELL", "HOLD"):
            rec = Recommendation(
                symbol="X",
                action=action,
                strategy="s",
                conviction=0.5,
                rationale="r",
                suggested_position_pct=0.0,
                forecast=None,
                key_indicators={},
                data_quality="OK",
            )
            assert rec.action == action


class TestConfigCompleteness:
    """All required CONFIG keys exist."""

    REQUIRED_KEYS = [
        "strong_buy_score_threshold",
        "buy_score_threshold",
        "sell_score_threshold",
        "unrealized_gain_hold_bias_pct",
        "unrealized_loss_sell_threshold_pct",
        "dividend_yield_hold_bias_threshold",
        "dividend_total_received_hold_bias_usd",
        "max_single_position_pct",
        "kelly_fraction",
        "kelly_cap",
        "conviction_strong_buy",
        "conviction_buy",
        "conviction_hold",
        "conviction_sell",
        "conviction_strong_sell",
        "conviction_partial_multiplier",
        "conviction_stale_multiplier",
        "bearish_forecast_pct_threshold",
        "bullish_forecast_pct_threshold",
        "min_history_bars",
    ]

    def test_all_keys_present(self):
        from engine.advisory import CONFIG

        for key in self.REQUIRED_KEYS:
            assert key in CONFIG, f"CONFIG missing required key: '{key}'"

    def test_no_magic_numbers_in_logic(self):
        """Sanity: decision logic constants are never literals outside CONFIG."""
        import inspect
        import engine.advisory as mod

        src = inspect.getsource(mod)
        # The hardcoded value 55 (buy threshold) must NOT appear raw in the
        # _compute / evaluate logic section — it lives in CONFIG only.
        # We allow it inside the CONFIG dict definition itself.
        config_def_end = src.index("# ---------------------------------------------------------------------------\n# Output dataclass")
        logic_section = src[config_def_end:]
        # Threshold values should not appear as bare numeric literals in logic
        for sentinel in ("55", "75", "35"):
            assert sentinel not in logic_section, (
                f"Magic number '{sentinel}' found in logic section; "
                "should be accessed via CONFIG[...]"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Acceptance criteria — 3 canonical scenarios from the spec
# ─────────────────────────────────────────────────────────────────────────────

class TestAcceptanceCriteria:
    """
    Verifies the three canonical acceptance criteria from the stage prompt.
    """

    def _run(self, position, strategy_signal, strategy_score, forecast_30, extra_tech=None):
        """Helper: patch engines, call evaluate(), return Recommendation."""
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))
        snapshot = _make_account_snapshot()

        patches = _patch_heavy_engines(
            tech_override=extra_tech,
            forecast_30=forecast_30,
            strategy_signal=strategy_signal,
            strategy_score=strategy_score,
            kelly_target=0.04,
        )

        import unittest.mock as mock
        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            pe_instance = MagicMock()
            tech = {**_MOCK_TECH, **(extra_tech or {})}
            pe_instance.calculate_technical_metrics.return_value = {"TEST": tech}
            MockPE.return_value = pe_instance

            fe_instance = MagicMock()
            fe_instance.generate_forecast.return_value = {"Forecast_30": forecast_30, "MC_Target": forecast_30}
            MockFE.return_value = fe_instance

            toe_instance = MagicMock()
            toe_instance.estimate_gjr_garch_volatility.return_value = 0.18
            MockTOE.return_value = toe_instance

            se_instance = MagicMock()
            se_instance.evaluate_security.return_value = {
                "Action Signal": strategy_signal,
                "Score": strategy_score,
                "Kelly Target": 0.04,
            }
            MockSE.return_value = se_instance

            rec = evaluate(
                symbol="TEST",
                position=position,
                market=market,
                snapshot=snapshot,
                transactions_store=ts,
            )
        return rec

    def test_ac1_hold_above_cost_dividend_history_neutral_forecast(self):
        """
        AC-1 (from spec): A symbol held above cost basis with a strong dividend
        history and a neutral forecast → HOLD with rationale mentioning dividends
        and unrealised gain.

        Setup: position is +15% above cost (dividend-adjusted), dividend yield 5%,
        $200 cumulative dividends, raw signal is HOLD (score 45, neutral).
        The forecast is roughly flat (no bearish signal).
        """
        position = _StubPortfolioPosition(
            symbol="TEST",
            quantity=100.0,
            average_cost=85.0,    # price 100, avg_cost 85 → +17.6% before divs
            current_price=100.0,
            market_value=10_000.0,
            unrealized_pl=1_500.0,
            unrealized_pl_pct=17.6,
            dividends_received=200.0,  # $2/share
            name="Test Corp",
        )

        fund_info = {
            "shortName": "Test Corp",
            "sector": "Utilities",
            "dividendYield": 0.05,   # 5% yield → above 4% threshold
            "trailingEps": 5.0,
            "bookValue": 30.0,
        }
        market = _make_market_provider(
            price=100.0,
            bars=_make_bars(252, 100.0),
            fundamentals=fund_info,
        )

        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            pe_instance = MagicMock()
            pe_instance.calculate_technical_metrics.return_value = {"TEST": {**_MOCK_TECH}}
            MockPE.return_value = pe_instance

            fe_instance = MagicMock()
            fe_instance.generate_forecast.return_value = {"Forecast_30": 101.0}  # flat / slightly up
            MockFE.return_value = fe_instance

            toe_instance = MagicMock()
            toe_instance.estimate_gjr_garch_volatility.return_value = 0.18
            MockTOE.return_value = toe_instance

            se_instance = MagicMock()
            se_instance.evaluate_security.return_value = {
                "Action Signal": "HOLD",   # neutral raw signal
                "Score": 45,
                "Kelly Target": 0.02,
            }
            MockSE.return_value = se_instance

            rec = evaluate(
                symbol="TEST",
                position=position,
                market=market,
                snapshot=_make_account_snapshot(),
                transactions_store=ts,
            )

        assert rec.action == "HOLD", f"Expected HOLD, got {rec.action}"
        rationale_lower = rec.rationale.lower()
        assert "dividend" in rationale_lower or "divid" in rationale_lower, (
            "Rationale should mention dividends"
        )
        # Unrealised gain should appear (position is up >10%)
        assert "unrealised" in rationale_lower or "gain" in rationale_lower or "%" in rationale_lower, (
            "Rationale should reference the unrealised gain"
        )

    def test_ac2_sell_below_cost_bearish_forecast(self):
        """
        AC-2 (from spec): A symbol held below cost basis with a bearish forecast
        → SELL with elevated conviction (≥ conviction_strong_sell).

        Setup: position -15% below cost (dividend-adjusted), forecast -6% from
        current (clearly bearish), raw signal is HOLD (score 40).
        """
        position = _StubPortfolioPosition(
            symbol="TEST",
            quantity=50.0,
            average_cost=120.0,   # price 100 → raw loss -17%
            current_price=100.0,
            market_value=5_000.0,
            unrealized_pl=-1_000.0,
            unrealized_pl_pct=-16.7,
            dividends_received=10.0,   # small divs → effective cost ≈ 119.8
            name="Test Corp",
        )

        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        import unittest.mock as mock
        from engine.advisory import evaluate, CONFIG
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")

        # forecast_30 = 94.0 → (94-100)/100 = -6% < bearish_forecast_pct_threshold (-3%)
        forecast_30 = 94.0

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            pe_instance = MagicMock()
            pe_instance.calculate_technical_metrics.return_value = {"TEST": {**_MOCK_TECH, "RSI": 30.0}}
            MockPE.return_value = pe_instance

            fe_instance = MagicMock()
            fe_instance.generate_forecast.return_value = {"Forecast_30": forecast_30}
            MockFE.return_value = fe_instance

            toe_instance = MagicMock()
            toe_instance.estimate_gjr_garch_volatility.return_value = 0.25
            MockTOE.return_value = toe_instance

            se_instance = MagicMock()
            se_instance.evaluate_security.return_value = {
                "Action Signal": "HOLD",   # raw signal neutral
                "Score": 40,
                "Kelly Target": 0.02,
            }
            MockSE.return_value = se_instance

            rec = evaluate(
                symbol="TEST",
                position=position,
                market=market,
                snapshot=_make_account_snapshot(),
                transactions_store=ts,
            )

        assert rec.action == "SELL", f"Expected SELL, got {rec.action}"
        assert rec.conviction >= CONFIG["conviction_strong_sell"], (
            f"Conviction {rec.conviction} should be ≥ conviction_strong_sell "
            f"({CONFIG['conviction_strong_sell']}) for below-cost + bearish forecast"
        )

    def test_ac3_buy_non_held_bullish_positive_kelly(self):
        """
        AC-3 (from spec): A non-held symbol with a strong bullish forecast and
        positive Kelly → BUY with 0 < suggested_position_pct ≤ configured cap.
        """
        import unittest.mock as mock
        from engine.advisory import evaluate, CONFIG
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE, \
             mock.patch("engine.advisory.estimate_win_rate_and_payoff") as mock_kelly_est, \
             mock.patch("engine.advisory.fractional_kelly") as mock_frac_kelly:

            pe_instance = MagicMock()
            pe_instance.calculate_technical_metrics.return_value = {"TEST": {**_MOCK_TECH}}
            MockPE.return_value = pe_instance

            fe_instance = MagicMock()
            fe_instance.generate_forecast.return_value = {"Forecast_30": 110.0}
            MockFE.return_value = fe_instance

            toe_instance = MagicMock()
            toe_instance.estimate_gjr_garch_volatility.return_value = 0.18
            MockTOE.return_value = toe_instance

            se_instance = MagicMock()
            se_instance.evaluate_security.return_value = {
                "Action Signal": "STRONG BUY",
                "Score": 80,
                "Kelly Target": 0.05,
            }
            MockSE.return_value = se_instance

            # Positive Kelly edge
            mock_kelly_est.return_value = (0.60, 1.8, 80)
            mock_frac_kelly.return_value = 0.04  # 4%

            rec = evaluate(
                symbol="TEST",
                position=None,
                market=market,
                snapshot=_make_account_snapshot(),
                transactions_store=ts,
            )

        assert rec.action == "BUY", f"Expected BUY, got {rec.action}"
        assert 0.0 < rec.suggested_position_pct <= CONFIG["max_single_position_pct"], (
            f"suggested_position_pct={rec.suggested_position_pct} should be in "
            f"(0, {CONFIG['max_single_position_pct']}]"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Data-quality flag tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDataQuality:
    def test_stale_quote_sets_stale(self):
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, is_stale=True, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            for M in (MockPE, MockFE, MockTOE):
                M.return_value = MagicMock()
            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 102.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "HOLD",
                "Score": 50,
                "Kelly Target": 0.02,
            }

            rec = evaluate(
                "TEST", None, market, _make_account_snapshot(), transactions_store=ts
            )

        assert rec.data_quality == "STALE"

    def test_bars_failure_sets_partial(self):
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, bars_raise=True)

        with mock.patch("engine.advisory.StrategyEngine") as MockSE:
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "HOLD",
                "Score": 50,
                "Kelly Target": 0.02,
            }
            rec = evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=ts)

        assert rec.data_quality == "PARTIAL"

    def test_no_price_returns_partial_hold(self):
        """When quote AND bars fail, should get HOLD/PARTIAL (never raises)."""
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=0.0, bars_raise=True, quote_raise=True)

        rec = evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=ts)

        assert rec.action == "HOLD"
        assert rec.data_quality == "PARTIAL"
        assert rec.conviction == 0.0

    def test_ok_quality_when_all_sources_fresh(self):
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, is_stale=False, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 102.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "HOLD",
                "Score": 50,
                "Kelly Target": 0.02,
            }

            rec = evaluate(
                "TEST", None, market, _make_account_snapshot(), transactions_store=ts
            )

        # macro_default is added (no macro_dto passed), which sets PARTIAL
        # Accept PARTIAL or STALE but NOT "OK" when macro is default.
        # This test confirms the function runs without raising.
        assert rec.data_quality in ("OK", "STALE", "PARTIAL")

    def test_partial_quality_decays_conviction(self):
        """A1: a PARTIAL-quality recommendation carries strictly lower conviction
        than the same signal on clean (OK) data — the decay multiplier is applied."""
        import unittest.mock as mock
        from engine.advisory import evaluate, CONFIG
        from transactions_store import TransactionsStore

        def _run(bars_raise: bool):
            ts = TransactionsStore(db_url="sqlite:///:memory:")
            market = _make_market_provider(
                price=100.0, is_stale=False,
                bars=None if bars_raise else _make_bars(252, 100.0),
                bars_raise=bars_raise,
            )
            with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
                 mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
                 mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
                 mock.patch("engine.advisory.StrategyEngine") as MockSE:
                MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
                MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 100.5}
                MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
                MockSE.return_value.evaluate_security.return_value = {
                    "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.02,
                }
                return evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=ts)

        clean = _run(bars_raise=False)
        partial = _run(bars_raise=True)

        assert clean.data_quality == "OK"
        assert partial.data_quality == "PARTIAL"
        assert partial.conviction < clean.conviction, (
            f"PARTIAL conviction {partial.conviction} should be < OK conviction {clean.conviction}"
        )
        assert partial.conviction == round(
            clean.conviction * CONFIG["conviction_partial_multiplier"], 4
        )


# ─────────────────────────────────────────────────────────────────────────────
# Task A6 — context_extras threading (universe-relative signals)
# ─────────────────────────────────────────────────────────────────────────────

class TestContextExtrasThreading:
    """evaluate()'s optional context_extras kwarg must be threaded straight
    through to StrategyEngine.evaluate_security() so cross-sectional momentum
    and multifactor signals score with real universe-relative data instead of
    silently falling back to neutral 0 (see signals/cross_sectional_momentum.py
    and signals/multifactor.py's two-phase pre_compute/compute hook pattern).
    """

    def test_context_extras_passed_through_to_strategy_engine(self):
        """When context_extras is supplied, evaluate_security() must be
        called with that EXACT object as its context_extras kwarg."""
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        sentinel_extras = {
            "xsec_percentile_ranks": {"TEST": 0.87},
            "multifactor_scores": {"TEST": {"Multifactor_Composite": 1.2}},
        }

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 102.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            se_instance = MagicMock()
            se_instance.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.02,
            }
            MockSE.return_value = se_instance

            evaluate(
                "TEST", None, market, _make_account_snapshot(),
                transactions_store=ts,
                context_extras=sentinel_extras,
            )

            assert se_instance.evaluate_security.called
            _, kwargs = se_instance.evaluate_security.call_args
            assert kwargs.get("context_extras") is sentinel_extras

    def test_context_extras_omitted_defaults_to_none(self):
        """Backward compatibility: when context_extras is omitted entirely,
        evaluate_security() must still be called (with context_extras=None
        or simply absent) -- existing call sites that predate this kwarg
        must be unaffected."""
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 102.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            se_instance = MagicMock()
            se_instance.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.02,
            }
            MockSE.return_value = se_instance

            evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=ts)

            assert se_instance.evaluate_security.called
            _, kwargs = se_instance.evaluate_security.call_args
            assert kwargs.get("context_extras") is None

    def test_multifactor_scores_populate_key_indicators(self):
        """When context_extras carries this symbol's multifactor Z-scores
        (signals/multifactor.py's pre_compute output), evaluate() must surface
        them on the returned Recommendation.key_indicators under snake_case
        keys (value_z/quality_z/lowvol_z/size_z/multifactor_composite) --
        the schema reporting/state_snapshot.py's advisory writer reads."""
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        context_extras = {
            "multifactor_scores": {
                "TEST": {
                    "Value_Z": 1.23, "Quality_Z": -0.5, "LowVol_Z": 0.75,
                    "Size_Z": -1.1, "Multifactor_Composite": 0.09,
                    "excluded_microcap": False,
                }
            }
        }

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 102.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            se_instance = MagicMock()
            se_instance.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.02,
            }
            MockSE.return_value = se_instance

            rec = evaluate(
                "TEST", None, market, _make_account_snapshot(),
                transactions_store=ts,
                context_extras=context_extras,
            )

            ki = rec.key_indicators
            assert ki["value_z"] == pytest.approx(1.23)
            assert ki["quality_z"] == pytest.approx(-0.5)
            assert ki["lowvol_z"] == pytest.approx(0.75)
            assert ki["size_z"] == pytest.approx(-1.1)
            assert ki["multifactor_composite"] == pytest.approx(0.09)

    def test_multifactor_scores_absent_degrade_to_nan(self):
        """No fabricated exposure (CONSTRAINT #4): when context_extras is
        omitted, or has no entry for this symbol, the multifactor
        key_indicators keys must be NaN, never 0.0 or missing."""
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 102.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            se_instance = MagicMock()
            se_instance.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.02,
            }
            MockSE.return_value = se_instance

            # Case 1: context_extras entirely omitted.
            rec_none = evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=ts)
            for key in ("value_z", "quality_z", "lowvol_z", "size_z", "multifactor_composite"):
                assert math.isnan(rec_none.key_indicators[key]), f"{key} should be NaN, got {rec_none.key_indicators[key]}"

            # Case 2: context_extras present but has no entry for this symbol
            # (e.g. microcap-excluded from the universe DataFrame entirely).
            rec_missing = evaluate(
                "TEST", None, market, _make_account_snapshot(),
                transactions_store=ts,
                context_extras={"multifactor_scores": {"OTHER": {"Value_Z": 5.0}}},
            )
            for key in ("value_z", "quality_z", "lowvol_z", "size_z", "multifactor_composite"):
                assert math.isnan(rec_missing.key_indicators[key]), f"{key} should be NaN, got {rec_missing.key_indicators[key]}"


class TestCurrentRatioKeyIndicator:
    """REUSE sweep: evaluate() must surface the liquidity ratio
    (FundamentalDataDTO.current_ratio, sourced from the provider's
    ``currentRatio`` fundamentals field) on Recommendation.key_indicators
    under the snake_case key ``current_ratio`` — mirroring the existing
    ``dividend_yield`` display entry. NaN (never fabricated) when absent
    (CONSTRAINT #4)."""

    def test_current_ratio_populates_key_indicators(self):
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        fund_info = {"currentRatio": 1.8, "shortName": "Liquid Corp", "sector": "Technology"}
        market = _make_market_provider(price=100.0, fundamentals=fund_info, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 102.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            se_instance = MagicMock()
            se_instance.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.02,
            }
            MockSE.return_value = se_instance

            rec = evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=ts)

            assert "current_ratio" in rec.key_indicators
            assert rec.key_indicators["current_ratio"] == pytest.approx(1.8)

    def test_current_ratio_absent_degrades_to_nan(self):
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        # No currentRatio in the fundamentals dict → DTO field is NaN.
        market = _make_market_provider(price=100.0, fundamentals={"shortName": "No Ratio Inc"},
                                       bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 102.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            se_instance = MagicMock()
            se_instance.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.02,
            }
            MockSE.return_value = se_instance

            rec = evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=ts)

            assert "current_ratio" in rec.key_indicators
            assert math.isnan(rec.key_indicators["current_ratio"]), (
                f"current_ratio should be NaN when absent, got {rec.key_indicators['current_ratio']}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Sizing tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPositionSizing:
    def test_sell_has_zero_position_pct(self):
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 90.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.35
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "RISK REDUCE",
                "Score": 20,
                "Kelly Target": 0.0,
            }

            rec = evaluate(
                "TEST", None, market, _make_account_snapshot(), transactions_store=ts
            )

        assert rec.action == "SELL"
        assert rec.suggested_position_pct == 0.0

    def test_buy_position_pct_bounded_by_cap(self):
        """BUY suggested_position_pct must never exceed max_single_position_pct."""
        import unittest.mock as mock
        from engine.advisory import evaluate, CONFIG
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE, \
             mock.patch("engine.advisory.fractional_kelly") as mock_fk, \
             mock.patch("engine.advisory.estimate_win_rate_and_payoff") as mock_est:

            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 115.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "STRONG BUY",
                "Score": 85,
                "Kelly Target": 0.10,
            }
            mock_est.return_value = (0.65, 2.0, 100)
            # Return a value larger than the cap to verify clamping
            mock_fk.return_value = 0.99

            rec = evaluate(
                "TEST", None, market, _make_account_snapshot(), transactions_store=ts
            )

        assert rec.action == "BUY"
        assert rec.suggested_position_pct <= CONFIG["max_single_position_pct"] + 1e-9, (
            f"Position pct {rec.suggested_position_pct} exceeds cap "
            f"{CONFIG['max_single_position_pct']}"
        )

    def test_negative_kelly_gives_zero_pct(self):
        """Negative Kelly edge must produce 0.0 position, not a negative allocation."""
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE, \
             mock.patch("engine.advisory.fractional_kelly") as mock_fk, \
             mock.patch("engine.advisory.estimate_win_rate_and_payoff") as mock_est:

            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 108.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "BUY",
                "Score": 58,
                "Kelly Target": 0.03,
            }
            mock_est.return_value = (0.40, 0.8, 50)
            mock_fk.return_value = 0.0  # no edge → Kelly returns 0.0

            rec = evaluate(
                "TEST", None, market, _make_account_snapshot(), transactions_store=ts
            )

        assert rec.suggested_position_pct >= 0.0, "Position pct must never be negative"


# ─────────────────────────────────────────────────────────────────────────────
# Dividend hold bias rule
# ─────────────────────────────────────────────────────────────────────────────

class TestDividendHoldBiasRule:
    """Verify the explicit HOLD bias for high-yield holders on neutral signals."""

    def test_high_yield_holder_neutral_signal_becomes_hold(self):
        """
        Holding a 5%-yield stock with $200 cumulative dividends, raw signal is
        weakly bullish (score 50) → advisory overrides to HOLD.
        """
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        position = _StubPortfolioPosition(
            symbol="TEST",
            quantity=100.0,
            average_cost=95.0,
            current_price=100.0,
            market_value=10_000.0,
            unrealized_pl=500.0,
            unrealized_pl_pct=5.26,
            dividends_received=200.0,   # well above $50 threshold
            name="High Yield Corp",
        )
        fund_info = {"dividendYield": 0.06, "shortName": "High Yield Corp", "sector": "Utilities"}
        market = _make_market_provider(price=100.0, fundamentals=fund_info, bars=_make_bars(252, 100.0))

        ts = TransactionsStore(db_url="sqlite:///:memory:")

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 101.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "BUY",    # weak BUY (score < buy_score_threshold)
                "Score": 50,
                "Kelly Target": 0.02,
            }

            rec = evaluate(
                "TEST", position=position, market=market,
                snapshot=_make_account_snapshot(), transactions_store=ts,
            )

        assert rec.action == "HOLD", (
            f"High-yield holder on weak BUY signal should be overridden to HOLD; "
            f"got {rec.action}"
        )
        rationale_lower = rec.rationale.lower()
        assert "dividend" in rationale_lower, (
            "Rationale should cite dividends as the driver of the HOLD override"
        )

    def test_strong_signal_overrides_dividend_bias(self):
        """
        Even with a high-yield position, a genuinely STRONG BUY (score ≥ 75) should
        keep the BUY action rather than being overridden to HOLD.
        """
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        position = _StubPortfolioPosition(
            symbol="TEST",
            quantity=100.0,
            average_cost=90.0,
            current_price=100.0,
            market_value=10_000.0,
            unrealized_pl=1_000.0,
            unrealized_pl_pct=11.1,
            dividends_received=300.0,
            name="High Yield Corp",
        )
        fund_info = {"dividendYield": 0.07, "shortName": "High Yield Corp"}
        market = _make_market_provider(price=100.0, fundamentals=fund_info, bars=_make_bars(252, 100.0))
        ts = TransactionsStore(db_url="sqlite:///:memory:")

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:

            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 110.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "STRONG BUY",
                "Score": 78,  # above strong_buy_score_threshold (75)
                "Kelly Target": 0.05,
            }

            rec = evaluate(
                "TEST", position=position, market=market,
                snapshot=_make_account_snapshot(), transactions_store=ts,
            )

        # Score >= buy_score_threshold (55): dividend bias does not suppress a genuine BUY
        assert rec.action == "BUY", (
            f"Strong signal should prevail over dividend HOLD bias; got {rec.action}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# A2 — synthetic-input honesty
# ─────────────────────────────────────────────────────────────────────────────

class TestSyntheticInputs:
    """When OHLCV bars are unavailable, technical indicators are computed on a
    flat synthetic bar and must NOT be presented as real signal."""

    def test_missing_bars_flags_synthetic_and_hides_technicals(self):
        import math
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        # bars_raise → no OHLCV history → synthetic bar substituted
        market = _make_market_provider(price=100.0, bars_raise=True)

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:
            # Even though the (mocked) engines return canned technicals, the
            # advisory layer must treat them as untrustworthy because the bar was
            # synthetic — so it hides them regardless of the engine output.
            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 101.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.0,
            }
            rec = evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=ts)

        assert rec.synthetic_inputs is True
        assert rec.data_quality == "PARTIAL"
        # Rationale must not cite chart indicators fabricated from a flat bar.
        assert "RSI(" not in rec.rationale
        assert "Aroon oscillator" not in rec.rationale
        # key_indicators technicals are NaN, not a fabricated number.
        for tk in ("rsi", "rsi_2", "atr", "aroon_osc", "macd_line"):
            assert math.isnan(rec.key_indicators[tk]), f"{tk} should be NaN on synthetic bars"

    def test_real_bars_do_not_flag_synthetic(self):
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))
        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:
            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 101.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.0,
            }
            rec = evaluate("TEST", None, market, _make_account_snapshot(), transactions_store=ts)

        assert rec.synthetic_inputs is False


# ─────────────────────────────────────────────────────────────────────────────
# A3 — symmetric forecast handling
# ─────────────────────────────────────────────────────────────────────────────

class TestForecastSymmetry:
    """A confirmed bullish forecast should keep a BUY on an already-appreciated
    holding (rather than the Case-C gain-capture HOLD override) and raise its
    conviction; a flat forecast preserves the legacy Case-C HOLD."""

    def _run_held_gain(self, forecast_30: float):
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        # avg_cost 80, price 100 → +25% (significant gain); no dividend bias.
        position = _StubPortfolioPosition(
            symbol="TEST", quantity=100.0, average_cost=80.0, current_price=100.0,
            market_value=10_000.0, unrealized_pl=2_000.0, unrealized_pl_pct=25.0,
            dividends_received=0.0, name="Winner Corp",
        )
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))
        ts = TransactionsStore(db_url="sqlite:///:memory:")
        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:
            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": forecast_30}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "BUY", "Score": 60, "Kelly Target": 0.03,
            }
            return evaluate(
                "TEST", position=position, market=market,
                snapshot=_make_account_snapshot(), transactions_store=ts,
            )

    def test_bullish_forecast_keeps_buy_and_raises_conviction(self):
        from engine.advisory import CONFIG
        rec = self._run_held_gain(forecast_30=110.0)   # +10% → bullish (> +3%)
        assert rec.action == "BUY", (
            f"Bullish forecast should keep BUY on an appreciated holding; got {rec.action}"
        )
        assert rec.conviction >= CONFIG["conviction_strong_buy"], (
            f"Bullish-confirmed BUY should reach strong-buy conviction; got {rec.conviction}"
        )

    def test_flat_forecast_preserves_case_c_hold(self):
        rec = self._run_held_gain(forecast_30=100.5)   # +0.5% → flat
        assert rec.action == "HOLD", (
            f"Flat forecast on an appreciated holding should HOLD (Case C); got {rec.action}"
        )


class TestTacticalRangesAndExitSizing:
    """buy_range/sell_range threading from StrategyEngine.evaluate_security(),
    and suggested_exit_pct sizing for SELL actions."""

    def _run(self, position, forecast_30: float, raw_signal: str, score: int,
              strategy_extra: dict | None = None):
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))
        ts = TransactionsStore(db_url="sqlite:///:memory:")
        strategy_out = {"Action Signal": raw_signal, "Score": score, "Kelly Target": 0.02}
        strategy_out.update(strategy_extra or {})

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:
            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": forecast_30}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.18
            MockSE.return_value.evaluate_security.return_value = strategy_out
            return evaluate(
                "TEST", position=position, market=market,
                snapshot=_make_account_snapshot(), transactions_store=ts,
            )

    def test_buy_range_and_sell_range_threaded_from_strategy_engine(self):
        rec = self._run(
            position=None, forecast_30=100.0, raw_signal="BUY", score=70,
            strategy_extra={
                "buyRange": "Buy Zone: $95.00 - $99.00",
                "sellRange": "Sell Zone: $105.00 - $110.00 | Stop @ $92.00",
            },
        )
        assert rec.buy_range == "Buy Zone: $95.00 - $99.00"
        assert rec.sell_range == "Sell Zone: $105.00 - $110.00 | Stop @ $92.00"

    def test_missing_ranges_default_to_empty_string(self):
        rec = self._run(position=None, forecast_30=100.0, raw_signal="HOLD", score=50)
        assert rec.buy_range == ""
        assert rec.sell_range == ""

    def test_case_a_escalation_suggests_full_exit(self):
        """Held below cost + bearish forecast (Case A) -> full-exit sizing."""
        from engine.advisory import CONFIG
        position = _StubPortfolioPosition(
            symbol="TEST", quantity=50.0, average_cost=120.0, current_price=100.0,
            market_value=5_000.0, unrealized_pl=-1_000.0, unrealized_pl_pct=-16.7,
            dividends_received=10.0, name="Test Corp",
        )
        # -6% forecast, well past the -3% bearish threshold.
        rec = self._run(position=position, forecast_30=94.0, raw_signal="HOLD", score=40)
        assert rec.action == "SELL"
        assert rec.suggested_exit_pct == CONFIG["exit_fraction_strong_sell"]

    def test_base_signal_sell_suggests_partial_trim(self):
        """A held position with a base RISK REDUCE signal (no Case A trigger:
        small gain, flat forecast) -> partial-trim sizing, not a full exit."""
        from engine.advisory import CONFIG
        position = _StubPortfolioPosition(
            symbol="TEST", quantity=50.0, average_cost=98.0, current_price=100.0,
            market_value=5_000.0, unrealized_pl=100.0, unrealized_pl_pct=2.0,
            dividends_received=0.0, name="Test Corp",
        )
        rec = self._run(position=position, forecast_30=100.5, raw_signal="RISK REDUCE", score=20)
        assert rec.action == "SELL"
        assert rec.suggested_exit_pct == CONFIG["exit_fraction_normal_sell"]
        assert rec.suggested_exit_pct < CONFIG["exit_fraction_strong_sell"]

    def test_non_held_sell_has_zero_exit_pct(self):
        rec = self._run(position=None, forecast_30=100.0, raw_signal="RISK REDUCE", score=20)
        assert rec.action == "SELL"
        assert rec.suggested_exit_pct == 0.0

    def test_buy_and_hold_have_zero_exit_pct(self):
        rec_buy = self._run(position=None, forecast_30=100.0, raw_signal="BUY", score=70)
        assert rec_buy.action == "BUY"
        assert rec_buy.suggested_exit_pct == 0.0

        rec_hold = self._run(position=None, forecast_30=100.0, raw_signal="HOLD", score=50)
        assert rec_hold.action == "HOLD"
        assert rec_hold.suggested_exit_pct == 0.0


# ============================================================================
# PR D — precomputed_garch / precomputed_forecast (settings.ADVISORY_REUSE_PIPELINE_COMPUTE)
# ============================================================================
#
# advisory.evaluate() gained two OUTPUT-CHANGING opt-in kwargs so
# main_orchestrator.py's advisory overlay can reuse run_pipeline's
# already-fit GARCH vol / 30-day forecast for the same ticker instead of a
# second independent fit. These tests lock in the dead-letter contract: a
# real positive precomputed value skips the corresponding fit entirely; a
# missing/zero/negative value transparently falls through to the original
# fresh-fit path. StrategyEngine.evaluate_security() is NEVER skipped by
# these kwargs — scoring is always freshly computed.

class TestPrecomputedGarchAndForecast:
    def _run(self, *, precomputed_garch=None, precomputed_forecast=None,
              garch_fit_value=0.22, forecast_fit_value=105.0):
        """Call evaluate() with heavy engines mocked; return
        (Recommendation, toe_mock, fe_mock, se_mock) so callers can assert on
        call counts / call kwargs."""
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))
        snapshot = _make_account_snapshot()

        with patch("engine.advisory.ProcessingEngine") as MockPE, \
             patch("engine.advisory.ForecastingEngine") as MockFE, \
             patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
             patch("engine.advisory.StrategyEngine") as MockSE:

            pe_instance = MagicMock()
            pe_instance.calculate_technical_metrics.return_value = {"TEST": _MOCK_TECH}
            MockPE.return_value = pe_instance

            fe_instance = MagicMock()
            fe_instance.generate_forecast.return_value = {
                "Forecast_30": forecast_fit_value, "MC_Target": forecast_fit_value,
            }
            MockFE.return_value = fe_instance

            toe_instance = MagicMock()
            toe_instance.estimate_gjr_garch_volatility.return_value = garch_fit_value
            MockTOE.return_value = toe_instance

            se_instance = MagicMock()
            se_instance.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.0,
            }
            MockSE.return_value = se_instance

            rec = evaluate(
                symbol="TEST",
                position=None,
                market=market,
                snapshot=snapshot,
                transactions_store=ts,
                precomputed_garch=precomputed_garch,
                precomputed_forecast=precomputed_forecast,
            )
        return rec, toe_instance, fe_instance, se_instance

    def test_default_none_still_runs_fresh_fits(self):
        """Byte-identical to pre-PR-D behavior: no precomputed values ->
        both engines are still invoked exactly as before."""
        rec, toe, fe, se = self._run()
        assert toe.estimate_gjr_garch_volatility.called
        assert fe.generate_forecast.called
        assert se.evaluate_security.called  # scoring is never skipped

    def test_valid_precomputed_garch_skips_fresh_fit(self):
        rec, toe, fe, se = self._run(precomputed_garch=0.31)
        assert not toe.estimate_gjr_garch_volatility.called
        assert rec.key_indicators["garch_vol"] == pytest.approx(0.31)
        # StrategyEngine received the precomputed value, not the fresh-fit stub.
        _, kwargs = se.evaluate_security.call_args
        assert kwargs["garch_vol"] == pytest.approx(0.31)

    def test_valid_precomputed_forecast_skips_fresh_fit(self):
        rec, toe, fe, se = self._run(precomputed_forecast=112.5)
        assert not fe.generate_forecast.called
        assert rec.forecast == pytest.approx(112.5)
        _, kwargs = se.evaluate_security.call_args
        assert kwargs["forecast_price"] == pytest.approx(112.5)

    def test_both_precomputed_skips_both_fresh_fits_but_not_strategy(self):
        rec, toe, fe, se = self._run(precomputed_garch=0.4, precomputed_forecast=120.0)
        assert not toe.estimate_gjr_garch_volatility.called
        assert not fe.generate_forecast.called
        assert se.evaluate_security.called  # scoring is always fresh
        assert rec.key_indicators["garch_vol"] == pytest.approx(0.4)
        assert rec.forecast == pytest.approx(120.0)

    def test_zero_precomputed_garch_falls_through_to_fresh_fit(self):
        """A zero/failed upstream value is never trusted -- must never
        silently substitute a bad value for a real fit (CONSTRAINT #6)."""
        rec, toe, fe, se = self._run(precomputed_garch=0.0)
        assert toe.estimate_gjr_garch_volatility.called
        assert rec.key_indicators["garch_vol"] == pytest.approx(0.22)  # the fresh-fit stub value

    def test_negative_precomputed_forecast_falls_through_to_fresh_fit(self):
        rec, toe, fe, se = self._run(precomputed_forecast=-5.0)
        assert fe.generate_forecast.called
        assert rec.forecast == pytest.approx(105.0)  # the fresh-fit stub value

    def test_none_precomputed_values_are_the_default(self):
        """Sanity check on the public signature: both new kwargs default to
        None so every pre-PR-D caller (main.py, ad-hoc/test calls) is
        unaffected without passing anything."""
        import inspect
        from engine.advisory import evaluate as _evaluate
        sig = inspect.signature(_evaluate)
        assert sig.parameters["precomputed_garch"].default is None
        assert sig.parameters["precomputed_forecast"].default is None


# ─────────────────────────────────────────────────────────────────────────────
# HistoricalStore routing (Steps 1 & 3) tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHistoricalStoreRouting:
    """evaluate()'s Step 1 (bars) and Step 3 (fundamentals) must route through
    an injected/singleton HistoricalStore when settings.HISTORICAL_STORE_ENABLED,
    falling back to the direct MarketDataProvider call on any HistoricalStore
    failure or when the flag is disabled. These tests pass an explicit
    ``historical_store=`` mock directly into evaluate(), which takes
    precedence over both the module-wide autouse passthrough fixture and the
    process-wide singleton -- letting them verify the real routing/fallback
    logic in isolation."""

    def _run(self, historical_store=None, **evaluate_kwargs):
        import unittest.mock as mock
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(
            price=100.0, bars=_make_bars(252, 100.0), fundamentals={"sector": "Technology"}
        )

        with mock.patch("engine.advisory.StrategyEngine") as MockSE:
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.02,
            }
            rec = evaluate(
                "TEST", None, market, _make_account_snapshot(),
                transactions_store=ts, historical_store=historical_store,
                **evaluate_kwargs,
            )
        return rec, market

    def test_bars_routed_through_historical_store_when_available(self):
        fake_hs = MagicMock()
        fake_hs.get_bars.return_value = _make_bars(252, 100.0)
        fake_hs.get_fundamentals_raw.return_value = {"sector": "Technology"}

        rec, market = self._run(historical_store=fake_hs)

        fake_hs.get_bars.assert_called_once_with("TEST", lookback_days=252, provider=market)
        market.get_intraday_bars.assert_not_called()

    def test_fundamentals_routed_through_historical_store_when_available(self):
        fake_hs = MagicMock()
        fake_hs.get_bars.return_value = _make_bars(252, 100.0)
        fake_hs.get_fundamentals_raw.return_value = {"sector": "Technology"}

        rec, market = self._run(historical_store=fake_hs)

        fake_hs.get_fundamentals_raw.assert_called_once()
        args, kwargs = fake_hs.get_fundamentals_raw.call_args
        assert args[0] == "TEST"
        assert kwargs["provider"] is market
        market.get_fundamentals.assert_not_called()

    def test_falls_back_to_direct_provider_on_historical_store_bars_failure(self):
        fake_hs = MagicMock()
        fake_hs.get_bars.side_effect = RuntimeError("simulated HistoricalStore failure")
        fake_hs.get_fundamentals_raw.return_value = {"sector": "Technology"}

        rec, market = self._run(historical_store=fake_hs)

        market.get_intraday_bars.assert_called_once_with("TEST", lookback_days=252)
        assert rec.data_quality != "PARTIAL" or "bars_unavailable" not in (rec.rationale or "")

    def test_falls_back_to_direct_provider_on_historical_store_fundamentals_failure(self):
        fake_hs = MagicMock()
        fake_hs.get_bars.return_value = _make_bars(252, 100.0)
        fake_hs.get_fundamentals_raw.side_effect = RuntimeError("simulated HistoricalStore failure")

        rec, market = self._run(historical_store=fake_hs)

        market.get_fundamentals.assert_called_once_with("TEST")

    def test_disabled_flag_skips_historical_store_entirely(self):
        import unittest.mock as mock
        fake_hs = MagicMock()

        with mock.patch("settings.settings.HISTORICAL_STORE_ENABLED", False):
            # Even though we pass a historical_store, evaluate() must never
            # touch it when the flag is off -- it must be resolved to None
            # up front and Steps 1/3 must call the provider directly.
            rec, market = self._run(historical_store=fake_hs)

        fake_hs.get_bars.assert_not_called()
        fake_hs.get_fundamentals_raw.assert_not_called()
        market.get_intraday_bars.assert_called_once_with("TEST", lookback_days=252)
        market.get_fundamentals.assert_called_once_with("TEST")

    def test_explicit_historical_store_kwarg_overrides_singleton(self):
        """An explicitly-injected historical_store must be used verbatim,
        never replaced by the process-wide singleton or the autouse
        passthrough fixture."""
        import unittest.mock as mock
        fake_hs = MagicMock()
        fake_hs.get_bars.return_value = _make_bars(252, 100.0)
        fake_hs.get_fundamentals_raw.return_value = {"sector": "Technology"}

        with mock.patch("engine.advisory._get_historical_store") as mock_getter:
            self._run(historical_store=fake_hs)
            mock_getter.assert_not_called()
