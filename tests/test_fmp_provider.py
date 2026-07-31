"""
tests/test_fmp_provider.py
============================
Fully-offline unit tests for ``FMPProvider`` (``data/market_data.py``) — the
``MarketDataProvider`` ABC implementation for Financial Modeling Prep.

Every ``data.fmp_client`` call is mocked at the function level
(``patch("data.fmp_client.<fn>", ...)``); no network, no ``requests``. Every
config value is sourced via ``patch("settings.settings.X", ...)``, never
``os.environ`` (matching the repo's ``build_finnhub_client`` precedent — see
CLAUDE.md / ``data/fmp_client.py``'s own module docstring).

Classes
-------
* ``TestConstruction``      — api_key validation; IS_REALTIME snapshot timing.
* ``TestUnimplementedStubs`` — get_latest_quote / get_intraday_bars.
* ``TestGetFundamentals``   — dead-letter resilience, per-call isolation,
                               end-to-end wiring into map_fundamentals, the
                               majority-NaN WARNING, SPY-cache reuse.
* ``TestEodPayloadParsing`` — the module-level ``_fmp_eod_payload_to_daily_returns``
                               helper (field-name + sort-order handling).
"""

import logging
import math

import pandas as pd
import pytest
from unittest.mock import patch

from data.fmp_client import FMPUnavailable


# --------------------------------------------------------------------------- #
# 1. Construction.
# --------------------------------------------------------------------------- #
class TestConstruction:
    def test_empty_api_key_raises_runtime_error(self):
        from data.market_data import FMPProvider
        with pytest.raises(RuntimeError, match="non-empty api_key"):
            FMPProvider(api_key="")

    def test_none_api_key_raises_runtime_error(self):
        from data.market_data import FMPProvider
        with pytest.raises(RuntimeError, match="non-empty api_key"):
            FMPProvider(api_key=None)

    def test_is_realtime_true_reflects_settings_at_construction(self):
        from data.market_data import FMPProvider
        with patch("settings.settings.FMP_QUOTES_REALTIME", True):
            provider = FMPProvider(api_key="abc123")
        assert provider.IS_REALTIME is True

    def test_is_realtime_false_reflects_settings_at_construction(self):
        from data.market_data import FMPProvider
        with patch("settings.settings.FMP_QUOTES_REALTIME", False):
            provider = FMPProvider(api_key="abc123")
        assert provider.IS_REALTIME is False

    def test_is_realtime_snapshotted_at_construction_not_import(self):
        """Regression: IS_REALTIME must be an INSTANCE attribute read fresh
        at __init__ time, not the class-level default frozen at import --
        two instances built under different settings must disagree."""
        from data.market_data import FMPProvider
        with patch("settings.settings.FMP_QUOTES_REALTIME", False):
            provider_off = FMPProvider(api_key="abc123")
        with patch("settings.settings.FMP_QUOTES_REALTIME", True):
            provider_on = FMPProvider(api_key="abc123")
        assert provider_off.IS_REALTIME is False
        assert provider_on.IS_REALTIME is True
        # The class-level default is untouched by either instance.
        assert FMPProvider.IS_REALTIME is False

    def test_source_class_attribute(self):
        from data.market_data import FMPProvider
        assert FMPProvider.SOURCE == "fmp"


# --------------------------------------------------------------------------- #
# 2. Unimplemented stubs (quotes/bars land in wave 2).
# --------------------------------------------------------------------------- #
class TestUnimplementedStubs:
    def _provider(self):
        from data.market_data import FMPProvider
        with patch("settings.settings.FMP_QUOTES_REALTIME", False):
            return FMPProvider(api_key="abc123")

    def test_get_latest_quote_raises_market_data_error(self):
        from data.market_data import MarketDataError
        provider = self._provider()
        with pytest.raises(MarketDataError, match="wave 2"):
            provider.get_latest_quote("AAPL")

    def test_get_intraday_bars_raises_market_data_error(self):
        from data.market_data import MarketDataError
        provider = self._provider()
        with pytest.raises(MarketDataError, match="wave 2"):
            provider.get_intraday_bars("AAPL")

    def test_stubs_never_touch_the_network(self):
        """The stubs must raise before any fmp_client call is attempted."""
        from data.market_data import MarketDataError
        provider = self._provider()
        with patch("data.fmp_client.requests.get") as mock_get:
            with pytest.raises(MarketDataError):
                provider.get_latest_quote("AAPL")
            with pytest.raises(MarketDataError):
                provider.get_intraday_bars("AAPL")
        mock_get.assert_not_called()


# --------------------------------------------------------------------------- #
# 3. get_fundamentals.
# --------------------------------------------------------------------------- #
class TestGetFundamentals:
    def _provider(self):
        from data.market_data import FMPProvider
        with patch("settings.settings.FMP_QUOTES_REALTIME", False):
            return FMPProvider(api_key="abc123")

    def test_never_raises_when_every_fmp_client_call_raises_unavailable(self):
        provider = self._provider()

        def _raise(*_a, **_kw):
            raise FMPUnavailable("nope")

        with patch("data.fmp_client.quote", side_effect=_raise), \
             patch("data.fmp_client.profile", side_effect=_raise), \
             patch("data.fmp_client.key_metrics_ttm", side_effect=_raise), \
             patch("data.fmp_client.ratios_ttm", side_effect=_raise), \
             patch("data.fmp_client.income_statement_ttm", side_effect=_raise), \
             patch("data.fmp_client.dividends", side_effect=_raise), \
             patch("data.fmp_client.shares_float", side_effect=_raise), \
             patch("data.fmp_client.historical_eod", side_effect=_raise):
            result = provider.get_fundamentals("AAPL")

        # Never raises; degrades to an all-NaN-but-structurally-present dict
        # (CONSTRAINT #4 -- NaN, not a fabricated 0.0; CONSTRAINT #6 -- the
        # data layer never raises into the pipeline).
        assert isinstance(result, dict)
        assert result["_source"] == "fmp"
        assert math.isnan(result["currentPrice"])
        assert math.isnan(result["beta"])

    def test_never_raises_on_a_totally_unexpected_exception(self):
        """Even a non-FMPUnavailable exception (e.g. a bug in map_fundamentals)
        must be caught by the outer dead-letter and degrade to {}."""
        provider = self._provider()
        with patch("data.fmp_client.quote", side_effect=RuntimeError("boom")), \
             patch("data.fmp_client.profile", side_effect=RuntimeError("boom")), \
             patch("data.fmp_client.key_metrics_ttm", side_effect=RuntimeError("boom")), \
             patch("data.fmp_client.ratios_ttm", side_effect=RuntimeError("boom")), \
             patch("data.fmp_client.income_statement_ttm", side_effect=RuntimeError("boom")), \
             patch("data.fmp_client.dividends", side_effect=RuntimeError("boom")), \
             patch("data.fmp_client.shares_float", side_effect=RuntimeError("boom")), \
             patch("data.fmp_client.historical_eod", side_effect=RuntimeError("boom")):
            result = provider.get_fundamentals("AAPL")
        assert result == {}

    def test_one_missing_endpoint_does_not_blank_the_rest(self):
        """Each fmp_client call is isolated in its own try/except FMPUnavailable
        -- key_metrics_ttm (Ultimate-only, say) failing must not blank fields
        sourced from ratios_ttm / income_statement_ttm / profile."""
        provider = self._provider()

        def _raise(*_a, **_kw):
            raise FMPUnavailable("Ultimate-only endpoint")

        with patch("data.fmp_client.quote", return_value=[{"price": 150.0}]), \
             patch("data.fmp_client.profile", return_value=[{
                 "companyName": "Test Co", "sector": "Technology",
                 "marketCap": 1_500_000.0, "price": 150.0,
             }]), \
             patch("data.fmp_client.key_metrics_ttm", side_effect=_raise), \
             patch("data.fmp_client.ratios_ttm", return_value=[{
                 "priceToEarningsRatioTTM": 15.0, "bookValuePerShareTTM": 10.0,
                 "priceToBookRatioTTM": 15.0, "dividendYieldTTM": 0.02,
                 "dividendPayoutRatioTTM": 0.3, "grossProfitMarginTTM": 0.45,
                 "operatingProfitMarginTTM": 0.25, "debtToEquityRatioTTM": 1.5,
                 "currentRatioTTM": 1.8,
             }]), \
             patch("data.fmp_client.income_statement_ttm", return_value=[{"epsDiluted": 10.0}]), \
             patch("data.fmp_client.dividends", return_value=[]), \
             patch("data.fmp_client.shares_float", return_value=[{"outstandingShares": 100_000.0}]), \
             patch("data.fmp_client.historical_eod", side_effect=_raise):
            result = provider.get_fundamentals("AAPL")

        assert math.isnan(result["returnOnEquity"])  # the failed endpoint
        assert result["currentPrice"] == pytest.approx(150.0)  # unaffected sibling
        assert result["debtToEquity"] == pytest.approx(150.0)  # unaffected sibling
        assert result["sharesOutstanding"] == pytest.approx(100_000.0)
        assert math.isnan(result["beta"])  # historical_eod failed -> beta NaN

    def test_end_to_end_delegates_to_map_fundamentals_correctly(self):
        """Full happy path: every fmp_client call succeeds -> the returned
        dict matches what map_fundamentals would compute directly."""
        from data.fmp_fundamentals import map_fundamentals
        provider = self._provider()

        quote_payload = [{"price": 150.0}]
        profile_payload = [{
            "companyName": "Test Co", "sector": "Technology",
            "marketCap": 1_500_000.0, "price": 150.0,
        }]
        km_payload = [{"returnOnEquityTTM": 0.20}]
        ratios_payload = [{
            "priceToEarningsRatioTTM": 15.0, "bookValuePerShareTTM": 10.0,
            "priceToBookRatioTTM": 15.0, "dividendYieldTTM": 0.02,
            "dividendPayoutRatioTTM": 0.3, "grossProfitMarginTTM": 0.45,
            "operatingProfitMarginTTM": 0.25, "debtToEquityRatioTTM": 1.5,
            "currentRatioTTM": 1.8,
        }]
        income_payload = [{"epsDiluted": 10.0}]
        shares_payload = [{"outstandingShares": 100_000.0}]

        with patch("data.fmp_client.quote", return_value=quote_payload), \
             patch("data.fmp_client.profile", return_value=profile_payload), \
             patch("data.fmp_client.key_metrics_ttm", return_value=km_payload), \
             patch("data.fmp_client.ratios_ttm", return_value=ratios_payload), \
             patch("data.fmp_client.income_statement_ttm", return_value=income_payload), \
             patch("data.fmp_client.dividends", return_value=[]), \
             patch("data.fmp_client.shares_float", return_value=shares_payload), \
             patch.object(provider, "_compute_beta", return_value=1.1):
            result = provider.get_fundamentals("AAPL")

        expected = map_fundamentals(
            "AAPL",
            quote=quote_payload, profile=profile_payload,
            key_metrics_ttm=km_payload, ratios_ttm=ratios_payload,
            income_statement_ttm=income_payload, shares_float=shares_payload,
            dividends=[], beta=1.1,
        )
        # Pop the pandas Series before a dict == comparison (Series equality
        # is ambiguous under bool()); compare it separately.
        result_series = result.pop("_dividends_series")
        expected_series = expected.pop("_dividends_series")
        pd.testing.assert_series_equal(result_series, expected_series)
        assert result == expected
        assert result["debtToEquity"] == pytest.approx(150.0)
        assert result["beta"] == pytest.approx(1.1)

    def test_majority_nan_response_logs_a_warning(self, caplog):
        """A degraded-but-nonempty response (most fields NaN) must be
        surfaced at WARNING -- it won't trigger CompositeProvider's fallback
        chain since the dict itself is non-empty, so this is the only signal
        an operator gets."""
        provider = self._provider()

        def _raise(*_a, **_kw):
            raise FMPUnavailable("down")

        with patch("data.fmp_client.quote", side_effect=_raise), \
             patch("data.fmp_client.profile", side_effect=_raise), \
             patch("data.fmp_client.key_metrics_ttm", side_effect=_raise), \
             patch("data.fmp_client.ratios_ttm", side_effect=_raise), \
             patch("data.fmp_client.income_statement_ttm", side_effect=_raise), \
             patch("data.fmp_client.dividends", side_effect=_raise), \
             patch("data.fmp_client.shares_float", side_effect=_raise), \
             patch("data.fmp_client.historical_eod", side_effect=_raise), \
             caplog.at_level(logging.WARNING, logger="data.market_data"):
            provider.get_fundamentals("AAPL")

        assert any(
            "numeric fields" in r.message and "NaN" in r.message
            for r in caplog.records
        )

    def test_healthy_response_does_not_log_the_majority_nan_warning(self, caplog):
        provider = self._provider()
        with patch("data.fmp_client.quote", return_value=[{"price": 150.0}]), \
             patch("data.fmp_client.profile", return_value=[{
                 "companyName": "Test Co", "sector": "Technology",
                 "marketCap": 1_500_000.0, "price": 150.0,
             }]), \
             patch("data.fmp_client.key_metrics_ttm", return_value=[{"returnOnEquityTTM": 0.2}]), \
             patch("data.fmp_client.ratios_ttm", return_value=[{
                 "priceToEarningsRatioTTM": 15.0, "bookValuePerShareTTM": 10.0,
                 "priceToBookRatioTTM": 15.0, "dividendYieldTTM": 0.02,
                 "dividendPayoutRatioTTM": 0.3, "grossProfitMarginTTM": 0.45,
                 "operatingProfitMarginTTM": 0.25, "debtToEquityRatioTTM": 1.5,
                 "currentRatioTTM": 1.8,
             }]), \
             patch("data.fmp_client.income_statement_ttm", return_value=[{"epsDiluted": 10.0}]), \
             patch("data.fmp_client.dividends", return_value=[]), \
             patch("data.fmp_client.shares_float", return_value=[{"outstandingShares": 100_000.0}]), \
             patch.object(provider, "_compute_beta", return_value=1.1), \
             caplog.at_level(logging.WARNING, logger="data.market_data"):
            provider.get_fundamentals("AAPL")

        assert not any("numeric fields" in r.message for r in caplog.records)

    def test_spy_returns_cached_across_symbols_within_ttl(self):
        """The SPY leg must be fetched at most once per TTL window even
        across multiple symbols in the same cycle -- mirrors
        YahooFundamentalsProvider's _SPY_CACHE_TTL_SECONDS pattern."""
        provider = self._provider()

        idx = pd.date_range("2024-01-01", periods=70, freq="B")
        spy_payload = [
            {"date": d.strftime("%Y-%m-%d"), "adjClose": 400.0 + i}
            for i, d in enumerate(idx)
        ]
        stock_payload = [
            {"date": d.strftime("%Y-%m-%d"), "adjClose": 100.0 + i * 0.5}
            for i, d in enumerate(idx)
        ]

        call_log = []

        def _historical_eod(symbol, **kwargs):
            call_log.append(symbol)
            if symbol == "SPY":
                return spy_payload
            return stock_payload

        with patch("data.fmp_client.historical_eod", side_effect=_historical_eod):
            beta1 = provider._compute_beta("AAPL")
            beta2 = provider._compute_beta("MSFT")

        spy_calls = [s for s in call_log if s == "SPY"]
        assert len(spy_calls) == 1  # fetched once, reused for the second symbol
        assert not math.isnan(beta1)
        assert not math.isnan(beta2)

    def test_spy_fetch_failure_keeps_prior_cached_series(self):
        """A transient SPY fetch failure after a prior success must reuse
        the cached series rather than clobbering it with None."""
        provider = self._provider()
        idx = pd.date_range("2024-01-01", periods=70, freq="B")
        good_payload = [
            {"date": d.strftime("%Y-%m-%d"), "adjClose": 400.0 + i}
            for i, d in enumerate(idx)
        ]

        with patch("data.fmp_client.historical_eod", return_value=good_payload):
            first = provider._spy_returns()
        assert first is not None and not first.empty

        with patch("data.fmp_client.historical_eod", side_effect=FMPUnavailable("down")):
            second = provider._spy_returns()
        # TTL not expired -> served from cache, no need to even hit the
        # (failing) network in this window; either way it must not be None.
        assert second is not None
        pd.testing.assert_series_equal(first, second)


# --------------------------------------------------------------------------- #
# 4. Module-level EOD-payload parsing helper.
# --------------------------------------------------------------------------- #
class TestEodPayloadParsing:
    def test_prefers_adj_close_field(self):
        """Verified live (2026-07-31): the dividend-adjusted variant returns
        'adjClose', not 'close'. Must read the adjusted field."""
        from data.market_data import _fmp_eod_payload_to_daily_returns
        payload = [
            {"date": "2026-01-03", "adjClose": 100.0, "close": 999.0},
            {"date": "2026-01-02", "adjClose": 102.0, "close": 999.0},
            {"date": "2026-01-01", "adjClose": 101.0, "close": 999.0},
        ]
        rets = _fmp_eod_payload_to_daily_returns(payload)
        assert rets is not None
        # ascending: 2026-01-01(101) -> 01-02(102) -> 01-03(100)
        assert rets.index.is_monotonic_increasing
        assert rets.iloc[0] == pytest.approx((102.0 - 101.0) / 101.0)
        assert rets.iloc[1] == pytest.approx((100.0 - 102.0) / 102.0)

    def test_falls_back_to_close_when_adj_close_absent(self):
        from data.market_data import _fmp_eod_payload_to_daily_returns
        payload = [
            {"date": "2026-01-02", "close": 102.0},
            {"date": "2026-01-01", "close": 100.0},
        ]
        rets = _fmp_eod_payload_to_daily_returns(payload)
        assert rets is not None
        assert rets.iloc[0] == pytest.approx(0.02)

    def test_handles_descending_and_ascending_order_identically(self):
        from data.market_data import _fmp_eod_payload_to_daily_returns
        ascending = [
            {"date": "2026-01-01", "adjClose": 100.0},
            {"date": "2026-01-02", "adjClose": 102.0},
            {"date": "2026-01-03", "adjClose": 101.0},
        ]
        descending = list(reversed(ascending))
        rets_asc = _fmp_eod_payload_to_daily_returns(ascending)
        rets_desc = _fmp_eod_payload_to_daily_returns(descending)
        pd.testing.assert_series_equal(rets_asc, rets_desc)

    def test_empty_payload_returns_none(self):
        from data.market_data import _fmp_eod_payload_to_daily_returns
        assert _fmp_eod_payload_to_daily_returns([]) is None
        assert _fmp_eod_payload_to_daily_returns(None) is None
        assert _fmp_eod_payload_to_daily_returns({}) is None

    def test_malformed_rows_skipped_individually(self):
        from data.market_data import _fmp_eod_payload_to_daily_returns
        payload = [
            {"date": "2026-01-01", "adjClose": 100.0},
            "not a dict",
            {"date": None, "adjClose": 105.0},
            {"date": "2026-01-02"},  # missing close
            {"date": "2026-01-02", "adjClose": 102.0},
        ]
        rets = _fmp_eod_payload_to_daily_returns(payload)
        assert rets is not None
        assert len(rets) == 1  # only the two valid, distinct-date rows survive -> 1 return
