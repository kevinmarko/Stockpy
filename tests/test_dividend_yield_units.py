"""
InvestYo Quant Platform - Dividend-Yield Unit Normalization Tests
================================================================
Regression coverage for a confirmed unit bug: yfinance (the pinned 1.5.1)
returns ``dividendYield`` as a **percent** (e.g. ``2.57`` for a 2.57% yield,
verified against ``dividendRate / price``), but the entire platform treats
``dividend_yield`` as a **fraction** (mock fixtures ``0.02``; the Finnhub
provider divides by 100; ``BaseDTO._to_float("2.57%")`` → ``0.0257``;
``engine.advisory.CONFIG`` thresholds ``0.04`` / ``0.03``).

The yfinance fundamentals ingestion paths previously passed ``.info`` straight
through with no normalization, so a yfinance-sourced holding's yield was ~100×
too large — wrecking the Gordon fair value (``annual_dividend = price * yield``)
and tripping the dividend HOLD-bias / score gates for every dividend payer.

``normalize_yfinance_dividend_yield`` converts the percent to a fraction at the
ingestion boundary (NOT in the DTO — the Finnhub path already produces a
fraction and must not be double-divided).
"""

import math

import pytest

from dto_models import normalize_yfinance_dividend_yield, FundamentalDataDTO


# ---------------------------------------------------------------------------
# The normalizer itself
# ---------------------------------------------------------------------------
class TestNormalizer:
    def test_percent_float_becomes_fraction(self):
        # yfinance KO-style value: 2.57 (percent) → 0.0257 (fraction)
        info = normalize_yfinance_dividend_yield({"dividendYield": 2.57})
        assert info["dividendYield"] == pytest.approx(0.0257)

    def test_sub_one_percent_yield(self):
        # 0.5% yield: yfinance returns 0.5 → 0.005 (NOT 0.5 = 50%)
        info = normalize_yfinance_dividend_yield({"dividendYield": 0.5})
        assert info["dividendYield"] == pytest.approx(0.005)

    def test_int_value_normalized(self):
        info = normalize_yfinance_dividend_yield({"dividendYield": 3})
        assert info["dividendYield"] == pytest.approx(0.03)

    def test_string_percent_left_untouched(self):
        # A "2.57%" string is the _to_float path's job — must NOT be divided here
        # (otherwise it would be double-divided to 0.000257).
        info = normalize_yfinance_dividend_yield({"dividendYield": "2.57%"})
        assert info["dividendYield"] == "2.57%"

    def test_none_left_untouched(self):
        info = normalize_yfinance_dividend_yield({"dividendYield": None})
        assert info["dividendYield"] is None

    def test_zero_left_untouched(self):
        info = normalize_yfinance_dividend_yield({"dividendYield": 0.0})
        assert info["dividendYield"] == 0.0

    def test_bool_not_treated_as_number(self):
        # True is an int subclass — must not be divided into 0.01.
        info = normalize_yfinance_dividend_yield({"dividendYield": True})
        assert info["dividendYield"] is True

    def test_missing_key_is_noop(self):
        info = normalize_yfinance_dividend_yield({"trailingPE": 10})
        assert "dividendYield" not in info

    def test_returns_same_dict_object(self):
        d = {"dividendYield": 2.0}
        assert normalize_yfinance_dividend_yield(d) is d

    def test_other_keys_preserved(self):
        info = normalize_yfinance_dividend_yield({"dividendYield": 4.0, "trailingPE": 12.3})
        assert info["trailingPE"] == 12.3
        assert info["dividendYield"] == pytest.approx(0.04)


# ---------------------------------------------------------------------------
# End-to-end: a normalized yfinance info dict produces a sane DTO yield that
# the advisory thresholds interpret correctly.
# ---------------------------------------------------------------------------
class TestDtoIntegration:
    def _dto_from_info(self, info: dict) -> FundamentalDataDTO:
        return FundamentalDataDTO(
            ticker="KO",
            pe_ratio=info.get("trailingPE"),
            pb_ratio=info.get("priceToBook"),
            dividend_yield=info.get("dividendYield", 0.0),
            book_value=info.get("bookValue", 0.0),
            eps_trailing=info.get("trailingEps", 0.0),
            dividend_growth_rate=0.0,
            payout_ratio=info.get("payoutRatio", 0.0),
            sector=info.get("sector", "N/A"),
            company_name=info.get("longName", "KO"),
        )

    def test_normalized_yield_is_a_fraction_in_dto(self):
        raw = {"dividendYield": 2.57, "payoutRatio": 0.68, "sector": "Consumer Staples"}
        dto = self._dto_from_info(normalize_yfinance_dividend_yield(raw))
        # 2.57% → 0.0257 fraction
        assert dto.dividend_yield == pytest.approx(0.0257)
        # Sanity: a real equity yield is well below 1.0 (100%).
        assert 0.0 < dto.dividend_yield < 0.5

    def test_unnormalized_would_trip_hold_bias_threshold(self):
        """Documents the bug: WITHOUT normalization the raw percent (2.57)
        clears the 0.04 hold-bias threshold trivially; WITH it, the fraction
        (0.0257) is correctly below 4%."""
        from engine.advisory import CONFIG
        thresh = CONFIG["dividend_yield_hold_bias_threshold"]  # 0.04

        raw_percent = 2.57          # what yfinance returns
        normalized = normalize_yfinance_dividend_yield({"dividendYield": raw_percent})["dividendYield"]

        assert raw_percent >= thresh          # bug: every payer trips it
        assert normalized < thresh            # fixed: 2.57% < 4% hold-bias floor
        assert normalized == pytest.approx(0.0257)
