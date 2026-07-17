"""
tests/test_options_matrix.py
============================
Matrix Integrity tests for the premium-selling matrix exposed by the
Command Center's Technical Options Matrix tab and audited by Gravity
STEP 38.

Asserts (per `technical_options_engine`):
  * High-IVR + Bullish trend → ``Put Credit Spread`` and the resolved short/
    long deltas land within ``delta_tolerance`` of the conventional targets
    (-0.30 / -0.15).
  * Every recommended strike is on the $0.50 grid.
  * Low-IVR + Bullish → ``Call Debit Spread`` (the engine flips from selling
    to buying cheap volatility, never recommends Cash/Wait without cause).
  * VIX > 30 OR ``CREDIT EVENT`` regime → degrades High-IVR opportunities to
    ``Cash / Wait`` (premium-selling gate fires-closed).
  * The :func:`validate_directive_integrity` helper returns
    ``{"ok": True, ...}`` for engine-generated directives and ``False`` when
    an off-grid strike is injected.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from technical_options_engine import (
    EXPECTED_DELTA_TARGETS,
    OptionsPricingRecommender,
    STRIKE_GRID_USD,
    build_premium_directive,
    validate_directive_integrity,
)


class _MacroProxy:
    """Minimal duck-typed stand-in for MacroEconomicDTO."""

    def __init__(self, vix: float = 15.0, regime: str = "RISK ON") -> None:
        self.vix = vix
        self.market_regime = regime


def _synthetic_bars(n: int = 252, seed: int = 0) -> pd.DataFrame:
    """Deterministic geometric Brownian motion OHLCV bars."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0005, 0.012, size=n)
    close = 100 * np.exp(np.cumsum(returns))
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    df = pd.DataFrame(
        {
            "Open": close * (1 - 0.001),
            "High": close * (1 + 0.005),
            "Low": close * (1 - 0.005),
            "Close": close,
            "Volume": rng.integers(1_000_000, 5_000_000, size=n),
        },
        index=idx,
    )
    return df


# --------------------------------------------------------------------------- #
# Happy path: High IVR + Bullish trend → Put Credit Spread + clean integrity   #
# --------------------------------------------------------------------------- #
def test_high_ivr_bullish_yields_put_credit_spread_with_clean_integrity():
    rec = OptionsPricingRecommender(stock_price=100.0)
    directive = rec.generate_strategy_pricing_matrix(
        true_ivr=75.0, current_iv=0.30, trend_bias="Bullish", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(),
    )
    assert directive["Strategy"] == "Put Credit Spread"
    assert directive["Action"] == "Sell to Open"
    legs = directive["Legs"]
    assert len(legs) == 2

    # Strike grid: every leg on the $0.50 grid.
    for leg in legs:
        strike = float(leg["Strike"])
        assert abs(strike / STRIKE_GRID_USD - round(strike / STRIKE_GRID_USD)) < 1e-6

    # Delta target tolerance (-0.30 short put, -0.15 long put).
    short = next(l for l in legs if l["Side"] == "Short")
    long_ = next(l for l in legs if l["Side"] == "Long")
    assert abs(short["Delta"] - EXPECTED_DELTA_TARGETS[("Put Credit Spread", "Short", "Put")]) <= 0.05
    assert abs(long_["Delta"] - EXPECTED_DELTA_TARGETS[("Put Credit Spread", "Long", "Put")]) <= 0.05

    integrity = validate_directive_integrity(directive)
    assert integrity["ok"], integrity["issues"]


# --------------------------------------------------------------------------- #
# Low IVR regime: debit (premium-buying), not credit                          #
# --------------------------------------------------------------------------- #
def test_low_ivr_bullish_yields_call_debit_spread():
    rec = OptionsPricingRecommender(stock_price=100.0)
    directive = rec.generate_strategy_pricing_matrix(
        true_ivr=20.0, current_iv=0.18, trend_bias="Bullish", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(),
    )
    assert directive["Strategy"] == "Call Debit Spread"
    assert directive["Action"] == "Buy to Open"
    assert len(directive["Legs"]) == 2
    integrity = validate_directive_integrity(directive)
    assert integrity["ok"], integrity["issues"]

    # Realizable_Daily_Theta is only ever computed for the CREDIT branches
    # (Put/Call Credit Spread, Iron Condor). A debit spread never touches
    # that key, so it must stay NaN — not a fabricated 0.0 (CONSTRAINT #4).
    assert directive["Realizable_Daily_Theta"] != directive["Realizable_Daily_Theta"]


def test_call_debit_spread_directive_carries_nan_not_zero_theta_in_full_row():
    """End-to-end: build_premium_directive's hydrated row must not silently
    coerce the engine's honest NaN theta into a fabricated 0.0 for any
    non-credit strategy (debit spreads, Covered Call, Cash/Wait)."""
    bars = _synthetic_bars(252, seed=11)
    row = build_premium_directive(
        "TEST",
        bars,
        spot_price=float(bars["Close"].iloc[-1]),
        is_stale=False,
        target_dte=30,
        macro_dto=_MacroProxy(),
        vrp=None,
        # Force the LOW-IVR (debit) regime deterministically regardless of the
        # synthetic bars' randomly-generated realized-vol IVR proxy: the engine
        # checks ivr_sell_threshold FIRST (`if true_ivr > ivr_sell_threshold`),
        # so overriding only ivr_buy_threshold left this test flaky whenever
        # the seed happened to produce an IVR proxy above the default 50.
        ivr_sell_threshold=100.0,
        ivr_buy_threshold=100.0,
    )
    assert row["Strategy"] not in {"Put Credit Spread", "Call Credit Spread", "Iron Condor"}
    assert np.isnan(row["Realizable_Daily_Theta"])


# --------------------------------------------------------------------------- #
# Regime gate: VIX > 30 vetoes premium selling even with high IVR             #
# --------------------------------------------------------------------------- #
def test_high_vix_gates_premium_selling_to_cash_wait():
    rec = OptionsPricingRecommender(stock_price=100.0)
    directive = rec.generate_strategy_pricing_matrix(
        true_ivr=80.0, current_iv=0.45, trend_bias="Bullish", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(vix=35.0),
    )
    assert directive["Strategy"] == "Cash"
    assert directive["Action"] == "Wait"


def test_credit_event_regime_gates_premium_selling():
    rec = OptionsPricingRecommender(stock_price=100.0)
    directive = rec.generate_strategy_pricing_matrix(
        true_ivr=80.0, current_iv=0.45, trend_bias="Neutral", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(regime="CREDIT EVENT"),
    )
    assert directive["Strategy"] == "Cash"


# --------------------------------------------------------------------------- #
# Integrity helper rejects an off-grid strike (edge case / leakage proof)     #
# --------------------------------------------------------------------------- #
def test_integrity_validator_catches_off_grid_strike():
    bad_directive = {
        "Strategy": "Put Credit Spread",
        "Action": "Sell to Open",
        "Legs": [
            {"Side": "Short", "Type": "Put", "Strike": 95.37, "Price": 1.50, "Delta": -0.30},
            {"Side": "Long", "Type": "Put", "Strike": 90.00, "Price": 0.50, "Delta": -0.15},
        ],
        "Net_Premium": 1.00,
        "Realizable_Daily_Theta": 0.02,
    }
    integrity = validate_directive_integrity(bad_directive)
    assert not integrity["ok"]
    assert any("off the $0.50 grid" in s for s in integrity["issues"])


# --------------------------------------------------------------------------- #
# End-to-end helper: hydrated row contains all expected fields, no fabrication
# --------------------------------------------------------------------------- #
def test_build_premium_directive_returns_full_row_with_no_fabrication():
    bars = _synthetic_bars(252, seed=7)
    row = build_premium_directive(
        "TEST",
        bars,
        spot_price=float(bars["Close"].iloc[-1]),
        is_stale=False,
        target_dte=30,
        macro_dto=_MacroProxy(),
        vrp=None,
    )

    # The row must always carry every documented field.
    required = {
        "Symbol", "Price", "Stale",
        "Sigma_GARCH", "IVR_Proxy",
        "Aroon_Oscillator", "Coppock_Curve", "Trend_Bias",
        "Strategy", "Action",
        "Net_Premium", "Realizable_Daily_Theta",
        "ATM_Delta", "ATM_Gamma", "ATM_Vega", "ATM_Theta_Daily",
        "Short_Strike", "Long_Strike", "Short_Delta", "Long_Delta",
        "Legs", "Integrity_OK", "Integrity_Issues",
    }
    assert required.issubset(row.keys())

    # No fabricated 0.0 defaults: where a real number exists, it should be finite.
    assert np.isfinite(row["Price"])
    assert np.isfinite(row["Sigma_GARCH"]) or row["Sigma_GARCH"] != row["Sigma_GARCH"]

    # Trend bias must be one of the three deterministic labels.
    assert row["Trend_Bias"] in {"Bullish", "Bearish", "Neutral"}

    # Engine-generated directives always pass integrity by construction.
    assert row["Integrity_OK"], row["Integrity_Issues"]


def test_build_premium_directive_degrades_on_too_few_bars():
    # 10 rows is well below the 22-row floor — the engine should still return
    # a complete row with Cash/Wait directive (never raise, never fabricate).
    short_bars = _synthetic_bars(10, seed=3)
    row = build_premium_directive(
        "SHORT",
        short_bars,
        spot_price=float(short_bars["Close"].iloc[-1]),
        is_stale=True,
        target_dte=30,
        macro_dto=_MacroProxy(),
    )
    assert row["Symbol"] == "SHORT"
    assert row["Stale"] is True
    # Trend defaults to Neutral when indicators cannot be derived.
    assert row["Trend_Bias"] == "Neutral"
    # No directive could be priced safely — Cash / Wait, no fabricated legs.
    assert row["Strategy"] in {"Cash", "Cash / Wait"} or not row["Legs"]


# --------------------------------------------------------------------------- #
# EXPECTED_DELTA_TARGETS sanity: targets cover every engine-produced strategy
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "true_ivr, trend",
    [
        (75.0, "Bullish"),     # Put Credit Spread
        (75.0, "Bearish"),     # Call Credit Spread
        (75.0, "Neutral"),     # Iron Condor
        (20.0, "Bullish"),     # Call Debit Spread
        (20.0, "Bearish"),     # Put Debit Spread
        (50.0, "Bullish"),     # Covered Call
    ],
)
def test_every_engine_strategy_passes_integrity(true_ivr, trend):
    rec = OptionsPricingRecommender(stock_price=100.0)
    directive = rec.generate_strategy_pricing_matrix(
        true_ivr=true_ivr, current_iv=0.25, trend_bias=trend, target_dte=30,
        vrp=None, macro_dto=_MacroProxy(),
    )
    integrity = validate_directive_integrity(directive)
    assert integrity["ok"], (directive["Strategy"], integrity["issues"])


# --------------------------------------------------------------------------- #
# Operator override: IVR sell threshold changes the regime gate               #
# --------------------------------------------------------------------------- #
def test_ivr_sell_threshold_default_matches_constant_and_override_changes_gate():
    """At the default threshold (50) an IVR of 45 sits in the NEUTRAL band
    (Bullish → Covered Call). Lowering the sell threshold to 40 pushes the same
    IVR into the premium-SELLING regime (Bullish → Put Credit Spread)."""
    rec = OptionsPricingRecommender(stock_price=100.0)

    # Default is byte-identical whether or not the kwarg is passed explicitly.
    baseline = rec.generate_strategy_pricing_matrix(
        true_ivr=45.0, current_iv=0.25, trend_bias="Bullish", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(),
    )
    default_explicit = rec.generate_strategy_pricing_matrix(
        true_ivr=45.0, current_iv=0.25, trend_bias="Bullish", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(), ivr_sell_threshold=50.0,
    )
    assert baseline["Strategy"] == default_explicit["Strategy"] == "Covered Call"

    # Override lowers the gate → 45 > 40 → premium-selling regime.
    overridden = rec.generate_strategy_pricing_matrix(
        true_ivr=45.0, current_iv=0.25, trend_bias="Bullish", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(), ivr_sell_threshold=40.0,
    )
    assert overridden["Strategy"] == "Put Credit Spread"


def test_ivr_buy_threshold_override_changes_gate():
    """Raising the buy threshold from 30 to 40 pushes an IVR of 35 out of the
    neutral band into the premium-BUYING (debit) regime."""
    rec = OptionsPricingRecommender(stock_price=100.0)

    default = rec.generate_strategy_pricing_matrix(
        true_ivr=35.0, current_iv=0.20, trend_bias="Bullish", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(),
    )
    assert default["Strategy"] == "Covered Call"  # 30 <= 35 <= 50 → neutral band

    overridden = rec.generate_strategy_pricing_matrix(
        true_ivr=35.0, current_iv=0.20, trend_bias="Bullish", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(), ivr_buy_threshold=40.0,
    )
    assert overridden["Strategy"] == "Call Debit Spread"  # 35 < 40 → buy regime


# --------------------------------------------------------------------------- #
# Operator override: delta_target_scale stays consistent with validation      #
# --------------------------------------------------------------------------- #
def test_delta_target_scale_widens_deltas_and_stays_integrity_consistent():
    rec = OptionsPricingRecommender(stock_price=100.0)

    # Scale 1.0 → engine default short-put delta ≈ -0.30.
    base = rec.generate_strategy_pricing_matrix(
        true_ivr=75.0, current_iv=0.30, trend_bias="Bullish", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(), delta_target_scale=1.0,
    )
    base_short = next(l for l in base["Legs"] if l["Side"] == "Short")
    assert abs(base_short["Delta"] - (-0.30)) <= 0.05
    assert validate_directive_integrity(base, delta_target_scale=1.0)["ok"]

    # Scale 1.5 → short-put delta target ≈ -0.45.
    scaled = rec.generate_strategy_pricing_matrix(
        true_ivr=75.0, current_iv=0.30, trend_bias="Bullish", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(), delta_target_scale=1.5,
    )
    scaled_short = next(l for l in scaled["Legs"] if l["Side"] == "Short")
    assert abs(scaled_short["Delta"] - (-0.45)) <= 0.05

    # Validation with the SAME scale passes; validating a scaled directive
    # against the UNSCALED (default) targets correctly flags the deviation.
    assert validate_directive_integrity(scaled, delta_target_scale=1.5)["ok"]
    mismatched = validate_directive_integrity(scaled, delta_target_scale=1.0)
    assert not mismatched["ok"]


def test_build_premium_directive_defaults_are_byte_identical():
    """Passing the override kwargs at their defaults must not change the row."""
    bars = _synthetic_bars(252, seed=11)
    kwargs = dict(
        spot_price=float(bars["Close"].iloc[-1]), is_stale=False,
        target_dte=30, macro_dto=_MacroProxy(), vrp=None,
    )
    plain = build_premium_directive("TEST", bars, **kwargs)
    explicit = build_premium_directive(
        "TEST", bars, **kwargs,
        ivr_sell_threshold=50.0, ivr_buy_threshold=30.0,
        delta_target_scale=1.0, delta_tolerance=0.05, strike_grid=STRIKE_GRID_USD,
    )
    assert plain["Strategy"] == explicit["Strategy"]
    assert plain["Legs"] == explicit["Legs"]
    assert plain["Integrity_OK"] == explicit["Integrity_OK"]
