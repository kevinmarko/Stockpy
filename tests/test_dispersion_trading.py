"""Tests for pilots/dispersion_trading.py."""

import ast
from pathlib import Path
import pytest
import numpy as np
import pandas as pd

from data.paper_account_store import PaperAccountStore
from execution.options_paper_executor import OptionsPaperExecutor
import pilots.dispersion_trading as dispersion_trading
from pilots.dispersion_trading import (
    DEFAULT_DISPERSION_CONSTITUENTS,
    DEFAULT_DISPERSION_INDEX,
    DEFAULT_WEIGHTS,
    INDEX_CONSTITUENTS_MAP,
    DispersionBasket,
    build_dispersion_basket,
    calculate_default_expiration,
    calculate_option_price,
    calculate_straddle_vega,
    compute_implied_correlation,
    compute_realized_correlation_matrix,
    evaluate_dispersion_opportunity,
    execute_dispersion_trade,
)


# ---------------------------------------------------------------------------
# 1. Math & Pricing Helpers
# ---------------------------------------------------------------------------

def test_calculate_default_expiration():
    exp = calculate_default_expiration(30)
    assert len(exp) == 10
    assert exp.count("-") == 2
    # Verify year-month-day structure
    parts = exp.split("-")
    assert len(parts[0]) == 4
    assert len(parts[1]) == 2
    assert len(parts[2]) == 2


def test_calculate_straddle_vega():
    # SPY 500 spot, 500 strike, 30 DTE, 18% IV
    vega = calculate_straddle_vega(spot=500.0, strike=500.0, iv=0.18, dte=30)
    assert vega > 0.0
    # Higher spot should yield higher dollar vega
    vega_high = calculate_straddle_vega(spot=1000.0, strike=1000.0, iv=0.18, dte=30)
    assert vega_high > vega

    # Degenerate / zero guards
    assert calculate_straddle_vega(spot=0.0, strike=500.0, iv=0.18, dte=30) == 0.0
    assert calculate_straddle_vega(spot=500.0, strike=0.0, iv=0.18, dte=30) == 0.0
    assert calculate_straddle_vega(spot=500.0, strike=500.0, iv=0.0, dte=30) == 0.0
    assert calculate_straddle_vega(spot=500.0, strike=500.0, iv=0.18, dte=0) == 0.0


def test_calculate_straddle_vega_matches_canonical_pricer_and_a_prior_hand_computation():
    """F4 dedup (docs/module_efficiency_redundancy_audit.md):
    calculate_straddle_vega now delegates to
    pilots.options_risk.calculate_black_scholes_greeks instead of its own
    inline d1/vega formula -- the one inconsistent copy in this file
    (calculate_option_price a few lines below already delegated correctly).
    Two checks: (1) result equals 2x the canonical function's own raw
    per-share vega x100 contract multiplier, put-call vega parity making the
    option_type choice immaterial; (2) at the exact degenerate input shape
    that produces options_gex.py's documented ~3.6e9 spurious gamma
    (spot=100, strike=100, t_years=1e-11, sigma=1e-7), vega stays sane
    (a small positive number, not a blowup) -- confirming vega's formula
    does not share gamma's vol_sqrt_t-division failure mode, so this
    migration was safe where a gamma migration would not have been."""
    from pilots.options_risk import calculate_black_scholes_greeks

    for spot, strike, iv, dte in [
        (500.0, 500.0, 0.18, 30),
        (150.0, 95.0, 0.35, 60),
        (50.0, 60.0, 0.50, 7),
    ]:
        t_years = max(1, dte) / 365.0
        canonical = calculate_black_scholes_greeks(spot=spot, strike=strike, t_years=t_years, sigma=iv, option_type="call")
        expected = 2.0 * canonical["vega_raw"] * 100.0
        assert calculate_straddle_vega(spot=spot, strike=strike, iv=iv, dte=dte) == pytest.approx(expected, abs=1e-9)

    # dte is capped at t_years=1/365 by max(1, dte) before the canonical call,
    # so iv=1e-7 with dte=1 (not options_gex.py's raw t_years=1e-11) is the
    # closest reachable analogue of that degenerate shape through this
    # function's own public signature -- still confirms boundedness.
    vega_near_boundary = calculate_straddle_vega(spot=100.0, strike=100.0, iv=1e-7, dte=1)
    assert 0.0 <= vega_near_boundary < 1.0, "vega must stay bounded near the degenerate vol_sqrt_t boundary, not blow up like gamma"


def test_calculate_option_price():
    # ATM Call vs Put prices with positive interest rate
    call_price = calculate_option_price(spot=100.0, strike=100.0, dte=30, iv=0.20, opt_type="call")
    put_price = calculate_option_price(spot=100.0, strike=100.0, dte=30, iv=0.20, opt_type="put")
    assert call_price > 0.0
    assert put_price > 0.0

    # 0DTE intrinsic test
    call_0dte_itm = calculate_option_price(spot=105.0, strike=100.0, dte=0, iv=0.20, opt_type="call")
    assert pytest.approx(call_0dte_itm, 0.01) == 500.0  # (105 - 100) * 100

    put_0dte_itm = calculate_option_price(spot=95.0, strike=100.0, dte=0, iv=0.20, opt_type="put")
    assert pytest.approx(put_0dte_itm, 0.01) == 500.0  # (100 - 95) * 100


# ---------------------------------------------------------------------------
# 2. Implied & Realized Correlation Math
# ---------------------------------------------------------------------------

def test_compute_implied_correlation():
    # If index IV equals constituent IVs exactly and equal weights, implied correlation is 1.0
    weights = {"AAPL": 0.5, "MSFT": 0.5}
    const_ivs = {"AAPL": 0.20, "MSFT": 0.20}
    rho = compute_implied_correlation(index_iv=0.20, constituent_ivs=const_ivs, weights=weights)
    assert pytest.approx(rho, 0.01) == 1.0

    # Non-trivial analytical reference checks for Driessen-Maenhout-Vilkov formula
    # Let w1=0.5, w2=0.5, sigma1=0.30, sigma2=0.40
    # weighted_var_sum = 0.5^2*0.3^2 + 0.5^2*0.4^2 = 0.0625
    # weighted_vol_sum_sq = (0.5*0.3 + 0.5*0.4)^2 = 0.35^2 = 0.1225
    # denominator = 0.1225 - 0.0625 = 0.0600
    ivs_mixed = {"AAPL": 0.30, "MSFT": 0.40}
    # For target rho = 0.50 -> index_iv^2 = 0.0625 + 0.06*0.50 = 0.0925 -> index_iv = sqrt(0.0925) ~ 0.304138
    target_iv_50 = np.sqrt(0.0925)
    rho_50 = compute_implied_correlation(index_iv=target_iv_50, constituent_ivs=ivs_mixed, weights=weights)
    assert rho_50 is not None
    assert pytest.approx(rho_50, abs=1e-4) == 0.50

    # For target rho = 0.75 -> index_iv^2 = 0.0625 + 0.06*0.75 = 0.1075 -> index_iv = sqrt(0.1075) ~ 0.327872
    target_iv_75 = np.sqrt(0.1075)
    rho_75 = compute_implied_correlation(index_iv=target_iv_75, constituent_ivs=ivs_mixed, weights=weights)
    assert rho_75 is not None
    assert pytest.approx(rho_75, abs=1e-4) == 0.75

    # If index IV is significantly lower than individual IVs, implied correlation is lower
    rho_low = compute_implied_correlation(index_iv=0.14, constituent_ivs=const_ivs, weights=weights)
    assert 0.0 <= rho_low < 1.0

    # Degenerate guards -- CONSTRAINT #4: a non-computable correlation must come back None
    # (never a fabricated "typical" 0.50 guess) so a caller can tell "no real data" apart from
    # "computed a genuine 0.50 correlation".
    assert compute_implied_correlation(index_iv=0.0, constituent_ivs=const_ivs, weights=weights) is None
    assert compute_implied_correlation(index_iv=0.20, constituent_ivs={}, weights=weights) is None


def test_driessen_maenhout_vilkov_implied_correlation_exact_multi_asset():
    """Validates Driessen-Maenhout-Vilkov (2009) implied correlation on an asymmetric 3-asset basket.

    w = [0.5, 0.3, 0.2], sigma = [0.30, 0.25, 0.20]
    Target rho_imp = 0.50 -> sigma_index = sqrt(0.049975) ≈ 0.223550889
    """
    weights = {"A": 0.5, "B": 0.3, "C": 0.2}
    const_ivs = {"A": 0.30, "B": 0.25, "C": 0.20}
    index_iv = np.sqrt(0.049975)

    rho = compute_implied_correlation(index_iv=index_iv, constituent_ivs=const_ivs, weights=weights)
    assert rho is not None
    assert pytest.approx(rho, abs=1e-5) == 0.50



def test_compute_realized_correlation_matrix():
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=100)
    # Generate correlated returns
    r1 = np.random.normal(0, 0.01, 100)
    r2 = r1 * 0.8 + np.random.normal(0, 0.005, 100)
    df = pd.DataFrame({"AAPL": r1, "MSFT": r2}, index=dates)

    matrix, avg_corr = compute_realized_correlation_matrix(df)
    assert matrix.shape == (2, 2)
    assert avg_corr > 0.50

    # Weighted realized correlation
    weights = {"AAPL": 0.6, "MSFT": 0.4}
    _, weighted_avg = compute_realized_correlation_matrix(df, weights=weights)
    assert -1.0 <= weighted_avg <= 1.0


# ---------------------------------------------------------------------------
# 1b. _source_real_dispersion_inputs -- batched quote resolution (F6)
# ---------------------------------------------------------------------------

class _FakeQuote:
    def __init__(self, price):
        self.price = price


def test_source_real_dispersion_inputs_uses_one_batched_quote_call(monkeypatch):
    """F6 regression (docs/module_efficiency_redundancy_audit.md):
    _source_real_dispersion_inputs used to call get_current_price() once
    per symbol (index + every constituent), each its own get_latest_quote()
    network round-trip. It must now resolve spot_map via exactly ONE
    get_quotes_batch() call covering the whole symbol set.
    """
    calls = []

    class FakeProvider:
        def get_quotes_batch(self, symbols):
            calls.append(list(symbols))
            return {s.upper(): _FakeQuote(100.0 + i) for i, s in enumerate(symbols)}

    monkeypatch.setattr("data.market_data.get_provider", lambda: FakeProvider())
    # Options provider / historical store are independently best-effort and
    # not the subject of this test -- force them unavailable so only the
    # spot_map path is exercised.
    monkeypatch.setattr("data.market_data.get_options_provider", lambda: (_ for _ in ()).throw(RuntimeError("n/a")))
    monkeypatch.setattr(
        "data.historical_store.HistoricalStore",
        lambda: (_ for _ in ()).throw(RuntimeError("n/a")),
    )

    constituents = ["AAPL", "MSFT"]
    spot_map, iv_map, realized_corr = dispersion_trading._source_real_dispersion_inputs(
        "SPY", constituents, {"AAPL": 0.5, "MSFT": 0.5},
    )

    # Exactly one batched call, covering the index + every constituent.
    assert len(calls) == 1
    assert set(calls[0]) == {"SPY", "AAPL", "MSFT"}

    assert spot_map["SPY"] == 100.0
    assert spot_map["AAPL"] == 101.0
    assert spot_map["MSFT"] == 102.0


def test_source_real_dispersion_inputs_skips_symbols_missing_from_batch(monkeypatch):
    """A symbol absent from the get_quotes_batch() result (unresolvable, or
    a total batch failure) must be simply absent from spot_map -- never a
    fabricated price (CONSTRAINT #4) -- matching the old per-symbol
    get_current_price() loop's degrade-to-0.0-then-skip behavior.
    """
    class PartialProvider:
        def get_quotes_batch(self, symbols):
            # MSFT deliberately absent from the response.
            return {"SPY": _FakeQuote(500.0), "AAPL": _FakeQuote(200.0)}

    monkeypatch.setattr("data.market_data.get_provider", lambda: PartialProvider())
    monkeypatch.setattr("data.market_data.get_options_provider", lambda: (_ for _ in ()).throw(RuntimeError("n/a")))
    monkeypatch.setattr(
        "data.historical_store.HistoricalStore",
        lambda: (_ for _ in ()).throw(RuntimeError("n/a")),
    )

    spot_map, _, _ = dispersion_trading._source_real_dispersion_inputs(
        "SPY", ["AAPL", "MSFT"], {"AAPL": 0.5, "MSFT": 0.5},
    )

    assert spot_map == {"SPY": 500.0, "AAPL": 200.0}
    assert "MSFT" not in spot_map


def test_source_real_dispersion_inputs_batch_call_raising_degrades_to_empty_spot_map(monkeypatch):
    """A total get_quotes_batch() failure (network error, provider outage)
    must degrade to an empty spot_map, never raise out of
    _source_real_dispersion_inputs (CONSTRAINT #6)."""
    class RaisingProvider:
        def get_quotes_batch(self, symbols):
            raise RuntimeError("market data outage")

    monkeypatch.setattr("data.market_data.get_provider", lambda: RaisingProvider())
    monkeypatch.setattr("data.market_data.get_options_provider", lambda: (_ for _ in ()).throw(RuntimeError("n/a")))
    monkeypatch.setattr(
        "data.historical_store.HistoricalStore",
        lambda: (_ for _ in ()).throw(RuntimeError("n/a")),
    )

    spot_map, iv_map, realized_corr = dispersion_trading._source_real_dispersion_inputs(
        "SPY", ["AAPL", "MSFT"], {"AAPL": 0.5, "MSFT": 0.5},
    )

    assert spot_map == {}
    assert iv_map == {}
    assert realized_corr is None


def test_evaluate_dispersion_opportunity():
    # When implied correlation >> realized correlation => Long Dispersion
    res_long = evaluate_dispersion_opportunity(
        index_symbol="SPY",
        index_iv=0.25,
        constituent_ivs={"AAPL": 0.26, "MSFT": 0.26},
        weights={"AAPL": 0.5, "MSFT": 0.5},
        realized_correlation=0.30,
        threshold=0.15,
    )
    assert res_long["regime"] == "Long Dispersion"
    assert res_long["is_actionable"] is True
    assert res_long["direction"] == "long_dispersion"

    # When implied correlation << realized correlation => Short Dispersion
    res_short = evaluate_dispersion_opportunity(
        index_symbol="SPY",
        index_iv=0.12,
        constituent_ivs={"AAPL": 0.30, "MSFT": 0.30},
        weights={"AAPL": 0.5, "MSFT": 0.5},
        realized_correlation=0.80,
        threshold=0.15,
    )
    assert res_short["regime"] == "Short Dispersion"
    assert res_short["is_actionable"] is True
    assert res_short["direction"] == "short_dispersion"

    # Fair value spread => Neutral
    res_neutral = evaluate_dispersion_opportunity(
        index_symbol="SPY",
        index_iv=0.22,
        constituent_ivs={"AAPL": 0.25, "MSFT": 0.25},
        weights={"AAPL": 0.5, "MSFT": 0.5},
        realized_correlation=0.50,
        threshold=0.15,
    )
    assert res_neutral["regime"] == "Neutral"
    assert res_neutral["is_actionable"] is False


# ---------------------------------------------------------------------------
# 3. Dispersion Basket Construction & Vega Neutrality
# ---------------------------------------------------------------------------

def test_build_dispersion_basket_vega_neutrality():
    index_symbol = "SPY"
    constituents = ["AAPL", "MSFT", "NVDA"]
    spot_map = {"SPY": 500.0, "AAPL": 220.0, "MSFT": 420.0, "NVDA": 120.0}
    iv_map = {"SPY": 0.18, "AAPL": 0.25, "MSFT": 0.22, "NVDA": 0.40}
    weights = {"AAPL": 0.40, "MSFT": 0.35, "NVDA": 0.25}

    basket = build_dispersion_basket(
        index_symbol=index_symbol,
        constituent_symbols=constituents,
        spot_map=spot_map,
        iv_map=iv_map,
        weights=weights,
        index_contracts=2,
        target_dte=30,
        is_long_dispersion=True,
    )

    assert isinstance(basket, DispersionBasket)
    assert basket.index_symbol == "SPY"
    assert basket.constituent_symbols == constituents
    assert basket.index_contracts == 2
    assert basket.index_vega > 0
    assert basket.basket_vega > 0

    # Vega neutrality balance: ratio should be close to 1.0 (within integer rounding band)
    assert 0.70 <= basket.vega_neutrality_ratio <= 1.30
    assert abs(basket.vega_imbalance_pct) < 35.0

    # Verify Index Legs (Long Dispersion => Short Index Straddle: Sell Call & Sell Put)
    assert len(basket.index_leg_requests) == 2
    assert basket.index_leg_requests[0]["side"] == "sell"
    assert basket.index_leg_requests[0]["type"] == "call"
    assert basket.index_leg_requests[1]["side"] == "sell"
    assert basket.index_leg_requests[1]["type"] == "put"
    assert basket.index_leg_requests[0]["qty"] == 2.0

    # Verify Constituent Legs (Long Dispersion => Long Constituent Straddles: Buy Call & Buy Put)
    assert len(basket.constituent_leg_requests) == 3
    for sym in constituents:
        legs = basket.constituent_leg_requests[sym]
        assert len(legs) == 2
        assert legs[0]["side"] == "buy"
        assert legs[0]["type"] == "call"
        assert legs[1]["side"] == "buy"
        assert legs[1]["type"] == "put"
        assert legs[0]["qty"] >= 1.0

    # Check to_dict serialization
    d = basket.to_dict()
    assert d["index_symbol"] == "SPY"
    assert "summary" in d
    assert d["summary"]["strategy"] == "Dispersion Arbitrage"


def test_build_dispersion_basket_short_dispersion():
    basket = build_dispersion_basket(
        index_symbol="QQQ",
        constituent_symbols=["AAPL", "MSFT"],
        spot_map={"QQQ": 450.0, "AAPL": 220.0, "MSFT": 420.0},
        iv_map={"QQQ": 0.20, "AAPL": 0.26, "MSFT": 0.24},
        weights={"AAPL": 0.5, "MSFT": 0.5},
        index_contracts=1,
        is_long_dispersion=False,
    )

    # Short Dispersion => Long Index Straddle (Buy), Short Constituent Straddles (Sell)
    assert basket.is_long_dispersion is False
    assert basket.index_leg_requests[0]["side"] == "buy"
    assert basket.index_leg_requests[1]["side"] == "buy"
    assert basket.constituent_leg_requests["AAPL"][0]["side"] == "sell"
    assert basket.constituent_leg_requests["AAPL"][1]["side"] == "sell"


# ---------------------------------------------------------------------------
# 4. Paper Account Execution
# ---------------------------------------------------------------------------

def test_execute_dispersion_trade_dry_run():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    basket = build_dispersion_basket(
        index_symbol="SPY",
        constituent_symbols=["AAPL", "MSFT"],
        spot_map={"SPY": 500.0, "AAPL": 220.0, "MSFT": 420.0},
        iv_map={"SPY": 0.18, "AAPL": 0.25, "MSFT": 0.22},
        weights={"AAPL": 0.5, "MSFT": 0.5},
        index_contracts=1,
    )

    res = execute_dispersion_trade(basket, store=store, dry_run=True)
    assert res["ok"] is True
    assert res["dry_run"] is True
    assert "Dry run" in res["message"]

    # Store remains untouched in dry run
    assert len(store.get_open_positions()) == 0


def test_execute_dispersion_trade_atomic_execution():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    initial_cash = store.get_account().cash
    assert initial_cash > 0

    basket = build_dispersion_basket(
        index_symbol="SPY",
        constituent_symbols=["AAPL", "MSFT"],
        spot_map={"SPY": 500.0, "AAPL": 220.0, "MSFT": 420.0},
        iv_map={"SPY": 0.18, "AAPL": 0.25, "MSFT": 0.22},
        weights={"AAPL": 0.5, "MSFT": 0.5},
        index_contracts=1,
        is_long_dispersion=True,
    )

    res = execute_dispersion_trade(basket, store=store, dry_run=False)
    assert res["ok"] is True
    assert res["strategy"] == "Dispersion Arbitrage"
    assert res["index_symbol"] == "SPY"
    assert "SPY" in res["index_order_id"]
    assert len(res["constituent_order_ids"]) == 2
    assert res["total_legs_filled"] == 6  # 2 index legs + 2*2 constituent legs

    positions = store.get_open_positions()
    assert len(positions) == 6

    # Verify short index straddle positions (qty < 0)
    spy_positions = [p for p in positions if "SPY" in p.symbol]
    assert len(spy_positions) == 2
    for p in spy_positions:
        assert p.qty == -1.0

    # Verify long constituent straddle positions (qty > 0)
    aapl_positions = [p for p in positions if "AAPL" in p.symbol]
    assert len(aapl_positions) == 2
    for p in aapl_positions:
        assert p.qty > 0.0

    msft_positions = [p for p in positions if "MSFT" in p.symbol]
    assert len(msft_positions) == 2
    for p in msft_positions:
        assert p.qty > 0.0


def test_execute_dispersion_trade_executor_delegation():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    basket = build_dispersion_basket(
        index_symbol="SPY",
        constituent_symbols=["AAPL", "NVDA"],
        spot_map={"SPY": 500.0, "AAPL": 220.0, "NVDA": 120.0},
        iv_map={"SPY": 0.18, "AAPL": 0.25, "NVDA": 0.35},
        weights={"AAPL": 0.6, "NVDA": 0.4},
        index_contracts=1,
    )

    res = executor.execute_dispersion_trade(basket, dry_run=False)
    assert res["ok"] is True
    assert len(store.get_open_positions()) == 6


# ---------------------------------------------------------------------------
# 4b. execute_dispersion_trade(basket=None) real-data-sourcing path: direction
#     must be derived from the measured spread's actual sign.
# ---------------------------------------------------------------------------

def test_execute_dispersion_trade_none_basket_derives_short_direction_from_real_data():
    """When implied correlation is well BELOW realized correlation (spread strongly
    negative, past the 0.15 default threshold), execute_dispersion_trade(basket=None) must
    source real data via `_source_real_dispersion_inputs` and build a SHORT dispersion
    basket: index leg becomes 'buy' (long index straddle), constituent legs become 'sell'
    (short constituent straddles) -- per build_dispersion_basket's documented convention."""
    idx_sym = "SPY"
    constituents = INDEX_CONSTITUENTS_MAP[idx_sym]

    spot_map = {idx_sym: 500.0}
    spot_map.update({s: 200.0 for s in constituents})
    # Low index IV relative to high, uniform constituent IV => low implied correlation.
    iv_map = {idx_sym: 0.12}
    iv_map.update({s: 0.30 for s in constituents})
    # High realized correlation => spread = implied - realized is strongly negative.
    realized_correlation = 0.90

    def fake_source_inputs(sym, consts, w):
        assert sym == idx_sym
        return dict(spot_map), dict(iv_map), realized_correlation

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dispersion_trading, "_source_real_dispersion_inputs", fake_source_inputs)

        res = execute_dispersion_trade(basket=None, index_symbol=idx_sym, dry_run=True)

    assert res["ok"] is True
    basket = res["basket"]
    assert basket is not None
    assert basket["is_long_dispersion"] is False
    assert basket["correlation_spread"] < -0.15

    # Short Dispersion => Long Index Straddle (buy), Short Constituent Straddles (sell).
    assert basket["index_leg_requests"][0]["side"] == "buy"
    assert basket["index_leg_requests"][1]["side"] == "buy"
    for sym in constituents:
        legs = basket["constituent_leg_requests"][sym]
        assert legs[0]["side"] == "sell"
        assert legs[1]["side"] == "sell"


def test_execute_dispersion_trade_none_basket_derives_long_direction_from_real_data():
    """When implied correlation is well ABOVE realized correlation (spread strongly
    positive, past the 0.15 default threshold), execute_dispersion_trade(basket=None) must
    source real data via `_source_real_dispersion_inputs` and build a LONG dispersion
    basket: index leg becomes 'sell' (short index straddle), constituent legs become 'buy'
    (long constituent straddles) -- per build_dispersion_basket's documented convention."""
    idx_sym = "SPY"
    constituents = INDEX_CONSTITUENTS_MAP[idx_sym]

    spot_map = {idx_sym: 500.0}
    spot_map.update({s: 200.0 for s in constituents})
    # High index IV relative to low, uniform constituent IV => high implied correlation.
    iv_map = {idx_sym: 0.32}
    iv_map.update({s: 0.20 for s in constituents})
    # Low realized correlation => spread = implied - realized is strongly positive.
    realized_correlation = 0.20

    def fake_source_inputs(sym, consts, w):
        assert sym == idx_sym
        return dict(spot_map), dict(iv_map), realized_correlation

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dispersion_trading, "_source_real_dispersion_inputs", fake_source_inputs)

        res = execute_dispersion_trade(basket=None, index_symbol=idx_sym, dry_run=True)

    assert res["ok"] is True
    basket = res["basket"]
    assert basket is not None
    assert basket["is_long_dispersion"] is True
    assert basket["correlation_spread"] > 0.15

    # Long Dispersion => Short Index Straddle (sell), Long Constituent Straddles (buy).
    assert basket["index_leg_requests"][0]["side"] == "sell"
    assert basket["index_leg_requests"][1]["side"] == "sell"
    for sym in constituents:
        legs = basket["constituent_leg_requests"][sym]
        assert legs[0]["side"] == "buy"
        assert legs[1]["side"] == "buy"


# ---------------------------------------------------------------------------
# 5. AST Import Safety Test
# ---------------------------------------------------------------------------

def test_dispersion_trading_ast_import_safety():
    """Verifies that pilots/dispersion_trading.py never imports heavy forbidden engines."""
    file_path = Path(__file__).resolve().parent.parent / "pilots" / "dispersion_trading.py"
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="dispersion_trading.py")

    forbidden_modules = {
        "processing_engine",
        "technical_options_engine",
        "forecasting_engine",
        "strategy_engine",
        "macro_engine",
        "main",
        "main_orchestrator",
        "desktop",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_modules:
                    assert forbidden not in alias.name, f"Forbidden import found: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod_name = node.module or ""
            for forbidden in forbidden_modules:
                assert forbidden not in mod_name, f"Forbidden from-import found: {mod_name}"
