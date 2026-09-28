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
  * Opt-in real ``True_IVR`` (``settings.OPTIONS_TRUE_IVR_ENABLED``): flag-off
    is byte-identical to pre-feature behavior; flag-on with a populated
    ``IVHistoryStore`` fixture yields a real ranked value that the strategy
    directive is priced off of (preferred over ``IVR_Proxy``); flag-on with
    empty history or any chain-fetch/store failure degrades to NaN without
    crashing and without disturbing the ``IVR_Proxy``-driven fallback.
"""

from __future__ import annotations

from datetime import timedelta

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
from volatility.iv_engine import IVHistoryStore


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


def test_call_debit_spread_legs_carry_delta_and_integrity_catches_mispricing():
    """Finding 15 regression: Call Debit Spread legs previously omitted
    ``Delta`` entirely, so ``validate_directive_integrity`` silently
    SKIPPED the delta-tolerance check for this strategy (a mispriced leg
    would pass integrity by omission, not by actually being correct)."""
    rec = OptionsPricingRecommender(stock_price=100.0)
    directive = rec.generate_strategy_pricing_matrix(
        true_ivr=20.0, current_iv=0.18, trend_bias="Bullish", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(),
    )
    assert directive["Strategy"] == "Call Debit Spread"
    legs = directive["Legs"]
    long_leg = next(l for l in legs if l["Side"] == "Long")
    short_leg = next(l for l in legs if l["Side"] == "Short")
    assert "Delta" in long_leg
    assert "Delta" in short_leg
    assert abs(long_leg["Delta"] - EXPECTED_DELTA_TARGETS[("Call Debit Spread", "Long", "Call")]) <= 0.05
    assert abs(short_leg["Delta"] - EXPECTED_DELTA_TARGETS[("Call Debit Spread", "Short", "Call")]) <= 0.05

    integrity = validate_directive_integrity(directive)
    assert integrity["ok"], integrity["issues"]

    # A mispriced leg now actually FAILS integrity instead of silently
    # passing by omission of the Delta key.
    mispriced = dict(directive)
    mispriced["Legs"] = [
        {**long_leg, "Delta": 0.05},  # far off the 0.50 target
        short_leg,
    ]
    bad_integrity = validate_directive_integrity(mispriced)
    assert not bad_integrity["ok"]


def test_put_debit_spread_legs_carry_delta_and_integrity_catches_mispricing():
    """Finding 15 regression, Bearish/Low-IVR Put Debit Spread branch."""
    rec = OptionsPricingRecommender(stock_price=100.0)
    directive = rec.generate_strategy_pricing_matrix(
        true_ivr=20.0, current_iv=0.18, trend_bias="Bearish", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(),
    )
    assert directive["Strategy"] == "Put Debit Spread"
    legs = directive["Legs"]
    long_leg = next(l for l in legs if l["Side"] == "Long")
    short_leg = next(l for l in legs if l["Side"] == "Short")
    assert "Delta" in long_leg
    assert "Delta" in short_leg
    assert abs(long_leg["Delta"] - EXPECTED_DELTA_TARGETS[("Put Debit Spread", "Long", "Put")]) <= 0.05
    assert abs(short_leg["Delta"] - EXPECTED_DELTA_TARGETS[("Put Debit Spread", "Short", "Put")]) <= 0.05

    integrity = validate_directive_integrity(directive)
    assert integrity["ok"], integrity["issues"]

    mispriced = dict(directive)
    mispriced["Legs"] = [
        {**long_leg, "Delta": -0.05},  # far off the -0.50 target
        short_leg,
    ]
    bad_integrity = validate_directive_integrity(mispriced)
    assert not bad_integrity["ok"]


def test_neutral_ivr_bearish_put_debit_spread_legs_carry_delta():
    """Finding 15 regression, the THIRD (NEUTRAL IVR REGIME) Put Debit Spread
    branch -- identical fix needed, separate code path."""
    rec = OptionsPricingRecommender(stock_price=100.0)
    directive = rec.generate_strategy_pricing_matrix(
        true_ivr=40.0, current_iv=0.22, trend_bias="Bearish", target_dte=30,
        vrp=None, macro_dto=_MacroProxy(),
    )
    assert directive["Strategy"] == "Put Debit Spread"
    legs = directive["Legs"]
    assert all("Delta" in leg for leg in legs)
    integrity = validate_directive_integrity(directive)
    assert integrity["ok"], integrity["issues"]


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


# --------------------------------------------------------------------------- #
# Opt-in real True_IVR (settings.OPTIONS_TRUE_IVR_ENABLED) -- fixtures        #
# --------------------------------------------------------------------------- #


class _FakeChain:
    """Minimal OptionChain-shaped stub: one ATM strike, a fixed IV."""

    def __init__(self, iv: float):
        self.calls = pd.DataFrame({"strike": [100.0], "impliedVolatility": [iv]})
        self.puts = pd.DataFrame({"strike": [100.0], "impliedVolatility": [iv]})


class _FakeChainDataEngine:
    """Minimal ``IDataProvider``-shaped stub exposing only
    ``fetch_options_chain`` -- all ``volatility.iv_engine.get_30d_atm_iv``
    touches when ``spot_price`` is supplied explicitly, which
    ``build_premium_directive`` always does. Two future expirations
    (20d/50d out from ``as_of``) with equal IV so calendar interpolation
    resolves deterministically to that same IV (no fractional-interpolation
    math to keep straight in the assertions)."""

    def __init__(self, as_of: pd.Timestamp, iv: float = 0.30):
        self._as_of = as_of
        self._iv = iv
        self._near = (as_of + timedelta(days=20)).strftime("%Y-%m-%d")
        self._far = (as_of + timedelta(days=50)).strftime("%Y-%m-%d")

    def fetch_options_chain(self, ticker, expiration=None):
        if expiration is None:
            return [self._near, self._far]
        if expiration in (self._near, self._far):
            return _FakeChain(self._iv)
        return None


class _RaisingChainDataEngine:
    """Simulates a network/chain-fetch failure."""

    def fetch_options_chain(self, ticker, expiration=None):
        raise RuntimeError("simulated chain-fetch network failure")


class _RaisingIVHistoryStore:
    """Simulates a DB write failure on the real-IVR record path."""

    def record_iv(self, ticker, date_val, iv_val):
        raise RuntimeError("simulated DB write failure")

    def get_historical_ivs(self, ticker, as_of_date, lookback_days=252):
        return []


def _true_ivr_kwargs(bars):
    return dict(
        spot_price=float(bars["Close"].iloc[-1]), is_stale=False,
        target_dte=30, macro_dto=_MacroProxy(), vrp=None,
    )


# --------------------------------------------------------------------------- #
# Flag off => byte-identical to pre-feature behavior                          #
# --------------------------------------------------------------------------- #
def test_true_ivr_flag_off_is_byte_identical_to_baseline():
    bars = _synthetic_bars(252, seed=41)
    kwargs = _true_ivr_kwargs(bars)

    baseline = build_premium_directive("TEST", bars, **kwargs)
    explicit_off = build_premium_directive("TEST", bars, **kwargs, true_ivr_enabled=False)

    assert set(baseline.keys()) == set(explicit_off.keys())
    for key in baseline:
        if key == "True_IVR":
            continue
        a, b = baseline[key], explicit_off[key]
        if isinstance(a, float) and isinstance(b, float) and np.isnan(a) and np.isnan(b):
            continue
        assert a == b, f"key {key!r} diverged: {a!r} vs {b!r}"

    assert np.isnan(baseline["True_IVR"])
    assert np.isnan(explicit_off["True_IVR"])


def test_true_ivr_defaults_to_settings_when_not_overridden(monkeypatch):
    """``true_ivr_enabled=None`` (the default) must read the live setting --
    False by default, so an untouched call site never triggers the real-IVR
    path (no import of volatility.iv_engine, no DataEngine construction)."""
    import technical_options_engine as toe_mod
    monkeypatch.setattr(toe_mod.settings, "OPTIONS_TRUE_IVR_ENABLED", False)

    assert toe_mod.settings.OPTIONS_TRUE_IVR_ENABLED is False  # platform default

    bars = _synthetic_bars(252, seed=43)
    row = build_premium_directive("TEST", bars, **_true_ivr_kwargs(bars))
    assert np.isnan(row["True_IVR"])


# --------------------------------------------------------------------------- #
# Flag on + populated history => real ranked value, preferred by the         #
# strategy directive over IVR_Proxy                                          #
# --------------------------------------------------------------------------- #
def test_true_ivr_flag_on_with_history_returns_ranked_value():
    bars = _synthetic_bars(252, seed=47)
    as_of = bars.index[-1]
    ticker = "TEST"

    store = IVHistoryStore(db_url="sqlite:///:memory:")
    for i, iv in enumerate([0.10, 0.15, 0.20, 0.25]):
        d = (as_of - timedelta(days=(4 - i) * 5)).strftime("%Y-%m-%d")
        store.record_iv(ticker, d, iv)

    fake_engine = _FakeChainDataEngine(as_of, iv=0.30)  # above all prior history

    row = build_premium_directive(
        ticker, bars, **_true_ivr_kwargs(bars),
        true_ivr_enabled=True, data_engine=fake_engine, iv_history_store=store,
    )

    assert np.isfinite(row["True_IVR"])
    assert row["True_IVR"] == pytest.approx(100.0)
    # IVR_Proxy stays present and untouched -- both keys coexist honestly.
    assert "IVR_Proxy" in row


def test_true_ivr_preferred_over_proxy_in_strategy_directive_when_finite(monkeypatch):
    """When the flag is on and True_IVR resolves to a finite value, the
    strategy directive must be priced off True_IVR, not IVR_Proxy."""
    import technical_options_engine as toe_mod

    captured = {}
    original = toe_mod.OptionsPricingRecommender.generate_strategy_pricing_matrix

    def _capture(self, true_ivr, *args, **kwargs):
        captured["true_ivr"] = true_ivr
        return original(self, true_ivr, *args, **kwargs)

    monkeypatch.setattr(
        toe_mod.OptionsPricingRecommender, "generate_strategy_pricing_matrix", _capture
    )

    bars = _synthetic_bars(252, seed=53)
    as_of = bars.index[-1]
    ticker = "TEST"
    store = IVHistoryStore(db_url="sqlite:///:memory:")
    store.record_iv(ticker, (as_of - timedelta(days=10)).strftime("%Y-%m-%d"), 0.05)
    fake_engine = _FakeChainDataEngine(as_of, iv=0.40)

    row = build_premium_directive(
        ticker, bars, **_true_ivr_kwargs(bars),
        true_ivr_enabled=True, data_engine=fake_engine, iv_history_store=store,
    )

    assert np.isfinite(row["True_IVR"])
    # The value handed to the strategy directive is exactly True_IVR, not
    # whatever IVR_Proxy happened to compute from the random synthetic bars.
    assert captured["true_ivr"] == pytest.approx(row["True_IVR"])


# --------------------------------------------------------------------------- #
# Flag on + empty/insufficient history => NaN, Cash/Wait fallback unaffected  #
# --------------------------------------------------------------------------- #
def test_true_ivr_flag_on_empty_history_degrades_to_nan_and_fallback_unaffected():
    bars = _synthetic_bars(252, seed=59)
    as_of = bars.index[-1]
    ticker = "TEST"

    empty_store = IVHistoryStore(db_url="sqlite:///:memory:")  # no prior rows at all
    fake_engine = _FakeChainDataEngine(as_of, iv=0.30)

    row = build_premium_directive(
        ticker, bars, **_true_ivr_kwargs(bars),
        true_ivr_enabled=True, data_engine=fake_engine, iv_history_store=empty_store,
    )
    assert np.isnan(row["True_IVR"])

    # The IVR_Proxy-driven directive must be identical to the flag-off case --
    # an empty real-IVR history degrades silently, it never forces Cash/Wait
    # or otherwise perturbs the existing fallback path.
    flag_off_row = build_premium_directive(
        ticker, bars, **_true_ivr_kwargs(bars), true_ivr_enabled=False,
    )
    assert row["Strategy"] == flag_off_row["Strategy"]
    assert row["Action"] == flag_off_row["Action"]
    assert row["Legs"] == flag_off_row["Legs"]
    assert row["Integrity_OK"] == flag_off_row["Integrity_OK"]


# --------------------------------------------------------------------------- #
# Flag on + failures (chain fetch raises / store write raises) => NaN, no    #
# crash, Cash/Wait fallback unaffected (CONSTRAINT #4/#6)                     #
# --------------------------------------------------------------------------- #
def test_true_ivr_flag_on_chain_fetch_exception_degrades_to_nan_without_crash():
    bars = _synthetic_bars(252, seed=61)
    ticker = "TEST"
    store = IVHistoryStore(db_url="sqlite:///:memory:")

    row = build_premium_directive(
        ticker, bars, **_true_ivr_kwargs(bars),
        true_ivr_enabled=True, data_engine=_RaisingChainDataEngine(), iv_history_store=store,
    )
    assert np.isnan(row["True_IVR"])
    assert row["Symbol"] == ticker
    assert isinstance(row["Strategy"], str)  # completed without raising


def test_true_ivr_flag_on_store_write_exception_degrades_to_nan_without_crash():
    bars = _synthetic_bars(252, seed=67)
    as_of = bars.index[-1]
    ticker = "TEST"
    fake_engine = _FakeChainDataEngine(as_of, iv=0.30)

    row = build_premium_directive(
        ticker, bars, **_true_ivr_kwargs(bars),
        true_ivr_enabled=True, data_engine=fake_engine,
        iv_history_store=_RaisingIVHistoryStore(),
    )
    assert np.isnan(row["True_IVR"])
    assert row["Symbol"] == ticker
    assert isinstance(row["Strategy"], str)  # completed without raising


def test_true_ivr_flag_on_too_few_bars_does_not_crash():
    """The pre-existing too-few-bars degradation path (Cash/Wait, no pricing)
    must still hold with the flag on -- True_IVR is attempted independently
    of the GARCH/price gate but must never itself raise. Uses the same fake
    chain / in-memory store fixtures as the tests above so this stays fully
    offline -- leaving data_engine/iv_history_store at their defaults here
    would silently fall through to a real network call and the real on-disk
    quant_platform.db, which is exactly what this suite must never do."""
    short_bars = _synthetic_bars(10, seed=71)
    fake_engine = _FakeChainDataEngine(short_bars.index[-1], iv=0.30)
    store = IVHistoryStore(db_url="sqlite:///:memory:")
    row = build_premium_directive(
        "SHORT", short_bars,
        spot_price=float(short_bars["Close"].iloc[-1]), is_stale=True,
        target_dte=30, macro_dto=_MacroProxy(),
        true_ivr_enabled=True, data_engine=fake_engine, iv_history_store=store,
    )
    assert row["Symbol"] == "SHORT"
    assert row["Strategy"] in {"Cash", "Cash / Wait"} or not row["Legs"]
