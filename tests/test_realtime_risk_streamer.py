"""
tests/test_realtime_risk_streamer.py
====================================
Unit tests for pilots/realtime_risk_streamer.py:
- Black-Scholes Greeks with degenerate & 0DTE boundaries
- Position risk scaling (equities & multi-leg options)
- Non-fabrication Constraint #4 (missing quotes exclusion)
- Beta-weighted SPY delta portfolio aggregation
"""
from datetime import datetime, timezone
import pytest

from pilots.realtime_risk_streamer import (
    compute_black_scholes_unit_greeks,
    compute_position_risk_greeks,
    compute_portfolio_risk_stream,
    parse_option_symbol,
    PortfolioRiskGreeks,
    PositionRiskGreeks,
)


class TestOptionSymbolParsing:
    def test_parse_valid_call_and_put(self):
        call_res = parse_option_symbol("AAPL 2026-09-18 $150.00 CALL")
        assert call_res is not None
        assert call_res["ticker"] == "AAPL"
        assert call_res["expiration"] == "2026-09-18"
        assert call_res["strike"] == 150.0
        assert call_res["option_type"] == "call"

        put_res = parse_option_symbol("SPY 2026-10-16 $495 PUT")
        assert put_res is not None
        assert put_res["ticker"] == "SPY"
        assert put_res["strike"] == 495.0
        assert put_res["option_type"] == "put"

    def test_parse_equity_or_invalid_returns_none(self):
        assert parse_option_symbol("AAPL") is None
        assert parse_option_symbol("MSFT") is None
        assert parse_option_symbol("") is None
        assert parse_option_symbol("INVALID SYMBOL STRING") is None


class TestBlackScholesUnitGreeks:
    def test_standard_atm_call_greeks(self):
        greeks = compute_black_scholes_unit_greeks(
            spot=100.0,
            strike=100.0,
            t_years=0.25,
            sigma=0.20,
            option_type="call",
            r=0.05,
        )
        assert 0.50 < greeks["delta"] < 0.60
        assert greeks["gamma"] > 0.0
        assert greeks["theta_daily"] < 0.0
        assert greeks["vega_1pct"] > 0.0

    def test_standard_atm_put_greeks(self):
        greeks = compute_black_scholes_unit_greeks(
            spot=100.0,
            strike=100.0,
            t_years=0.25,
            sigma=0.20,
            option_type="put",
            r=0.05,
        )
        assert -0.50 < greeks["delta"] < -0.40
        assert greeks["gamma"] > 0.0
        assert greeks["theta_daily"] < 0.0
        assert greeks["vega_1pct"] > 0.0

    def test_0dte_expiration_boundary_itm_and_otm(self):
        # 0DTE ITM Call -> delta = 1.0, gamma = 0, theta = 0, vega = 0
        call_itm = compute_black_scholes_unit_greeks(
            spot=105.0, strike=100.0, t_years=0.0, sigma=0.20, option_type="call"
        )
        assert call_itm["delta"] == 1.0
        assert call_itm["gamma"] == 0.0
        assert call_itm["theta_daily"] == 0.0
        assert call_itm["vega_1pct"] == 0.0

        # 0DTE OTM Call -> delta = 0.0
        call_otm = compute_black_scholes_unit_greeks(
            spot=95.0, strike=100.0, t_years=0.0, sigma=0.20, option_type="call"
        )
        assert call_otm["delta"] == 0.0

        # 0DTE ITM Put -> delta = -1.0
        put_itm = compute_black_scholes_unit_greeks(
            spot=95.0, strike=100.0, t_years=0.0, sigma=0.20, option_type="put"
        )
        assert put_itm["delta"] == -1.0

        # 0DTE OTM Put -> delta = 0.0
        put_otm = compute_black_scholes_unit_greeks(
            spot=105.0, strike=100.0, t_years=0.0, sigma=0.20, option_type="put"
        )
        assert put_otm["delta"] == 0.0

    def test_zero_volatility_boundary(self):
        res = compute_black_scholes_unit_greeks(
            spot=105.0, strike=100.0, t_years=0.5, sigma=0.0, option_type="call"
        )
        assert res["delta"] == 1.0
        assert res["gamma"] == 0.0
        assert res["vega_1pct"] == 0.0

    def test_degenerate_inputs_fail_safe(self):
        zero_spot = compute_black_scholes_unit_greeks(spot=0.0, strike=100.0, t_years=0.5, sigma=0.2)
        assert zero_spot["delta"] == 0.0
        assert zero_spot["gamma"] == 0.0


class TestPositionRiskGreeks:
    def test_equity_position_risk(self):
        pos = {"symbol": "AAPL", "qty": 50}
        res = compute_position_risk_greeks(
            position=pos,
            spot_price=180.0,
            spy_price=500.0,
            beta=1.2,
        )
        assert res is not None
        assert res.position_type == "equity"
        assert res.qty == 50
        assert res.delta == 50.0
        assert res.dollar_delta == 50 * 180.0
        assert res.gamma == 0.0
        assert res.theta_daily == 0.0
        assert res.vega_1pct == 0.0
        # beta-weighted SPY delta = (50 * 180.0 * 1.2) / 500.0 = 21.6
        assert pytest.approx(res.beta_weighted_delta_spy, rel=1e-4) == 21.6

    def test_option_position_risk(self):
        pos = {
            "symbol": "SPY 2026-12-18 $500.00 CALL",
            "qty": 2,
            "iv": 0.20,
        }
        res = compute_position_risk_greeks(
            position=pos,
            spot_price=500.0,
            spy_price=500.0,
            beta=1.0,
            as_of=datetime(2026, 6, 18, tzinfo=timezone.utc),
        )
        assert res is not None
        assert res.position_type == "option"
        assert res.qty == 2
        # 2 contracts = 200 shares multiplier
        assert 100.0 < res.delta < 120.0
        assert res.dollar_delta == res.delta * 500.0
        assert res.gamma > 0.0
        assert res.theta_daily < 0.0
        assert res.vega_1pct > 0.0

    def test_malformed_expiration_returns_none_not_fabricated_dte(self):
        """Regression test: an option symbol whose expiration date fails to
        parse must NOT fabricate a plausible-but-wrong dte=30.0 and continue
        computing Greeks against it. It must return None (matching the
        empty-symbol / near-zero-qty / non-positive-spot degenerate cases
        this same function already handles this way), so the caller's
        missing_positions path is the one that reports it -- see
        test_malformed_expiration_lands_in_missing_positions below."""
        pos = {
            "symbol": "AAPL 2026-13-45 $180.00 CALL",  # invalid month/day
            "qty": 1,
            "iv": 0.25,
        }
        res = compute_position_risk_greeks(
            position=pos,
            spot_price=180.0,
            spy_price=500.0,
            beta=1.0,
        )
        assert res is None


class TestPortfolioRiskStreamAggregation:
    def test_portfolio_aggregation_multi_asset(self):
        positions = [
            {"symbol": "AAPL", "qty": 100},
            {"symbol": "AAPL 2026-12-18 $180.00 CALL", "qty": 1, "iv": 0.25},
            {"symbol": "AAPL 2026-12-18 $190.00 CALL", "qty": -1, "iv": 0.25},
        ]
        quotes = {"AAPL": 180.0, "SPY": 500.0}
        betas = {"AAPL": 1.2, "SPY": 1.0}

        port_risk = compute_portfolio_risk_stream(
            positions=positions,
            quotes=quotes,
            betas=betas,
            spy_price=500.0,
            as_of=datetime(2026, 6, 18, tzinfo=timezone.utc),
        )

        assert port_risk.total_positions_count == 3
        assert port_risk.resolved_positions_count == 3
        assert port_risk.missing_data_count == 0
        assert port_risk.net_delta > 100.0
        assert port_risk.net_dollar_delta > 18000.0
        assert port_risk.beta_weighted_delta_spy > 0.0

    def test_non_fabrication_constraint_4_missing_quotes(self):
        positions = [
            {"symbol": "AAPL", "qty": 100},
            {"symbol": "UNKNOWN_TICKER", "qty": 50},
        ]
        # Only AAPL has quote; UNKNOWN_TICKER is missing
        quotes = {"AAPL": 180.0, "SPY": 500.0}

        port_risk = compute_portfolio_risk_stream(
            positions=positions,
            quotes=quotes,
            spy_price=500.0,
        )

        assert port_risk.total_positions_count == 2
        assert port_risk.resolved_positions_count == 1
        assert port_risk.missing_data_count == 1
        assert "UNKNOWN_TICKER" in port_risk.missing_positions
        # Aggregate delta is strictly based on the resolved position (100 shares of AAPL)
        assert port_risk.net_delta == 100.0

    def test_malformed_expiration_lands_in_missing_positions(self):
        """A position with an unparseable option expiration date must land
        in missing_positions -- not in resolved_positions with a fabricated
        30-day Greek -- when run through the full portfolio aggregation
        entry point. Zero new branching logic was needed in the caller for
        this: the existing `if pos_greeks is None: missing_positions.append`
        path (exercised above for missing quotes) already handles it."""
        positions = [
            {"symbol": "AAPL", "qty": 100},
            {"symbol": "AAPL 2026-13-45 $180.00 CALL", "qty": 1, "iv": 0.25},
        ]
        quotes = {"AAPL": 180.0, "SPY": 500.0}

        port_risk = compute_portfolio_risk_stream(
            positions=positions,
            quotes=quotes,
            spy_price=500.0,
        )

        assert port_risk.total_positions_count == 2
        assert port_risk.resolved_positions_count == 1
        assert port_risk.missing_data_count == 1
        assert "AAPL 2026-13-45 $180.00 CALL" in port_risk.missing_positions
        resolved_symbols = {p.symbol for p in port_risk.positions}
        assert "AAPL 2026-13-45 $180.00 CALL" not in resolved_symbols
