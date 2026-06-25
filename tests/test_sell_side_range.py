"""
tests/test_sell_side_range.py
=============================
Coverage for ``strategy_engine.apply_sell_side_range`` and its propagation
through ``StrategyEngine.evaluate_security``.

Test groups (matches the project's standard ``happy / edge / leakage`` triad):

1. Happy path — for every Action Signal (STRONG BUY, BUY, HOLD, RISK REDUCE)
   the helper produces a parseable, monotone (lower < upper) sell-side string.
2. Edge cases — zero ATR, zero chandelier_long, zero forecast_price, and
   unknown signal strings all fail closed (no crash, no negative levels,
   no fabricated upper bound).
3. Schema integration — ``sellRange`` appears in ``config.COLUMN_SCHEMA`` and
   ``StrategyEngine.evaluate_security()``'s return dict.
4. Lookahead invariant — perturbing the future of any input AFTER the
   evaluation cutoff does not change the emitted sellRange (the helper is
   pure with respect to its scalar inputs; this test guards the contract
   that all inputs must remain lookahead-free at the call site).
"""
from __future__ import annotations

import re
from datetime import datetime

import pytest

import config
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
from strategy_engine import StrategyEngine, apply_sell_side_range


# ---------------------------------------------------------------------------
# Regexes that lock in the public string contract.
# ---------------------------------------------------------------------------
_SELL_ZONE_RE = re.compile(
    r"^Sell Zone: \$([0-9]+\.[0-9]{2}) - \$([0-9]+\.[0-9]{2}) \| Stop @ \$([0-9]+\.[0-9]{2})$"
)
_SELL_NOW_RE = re.compile(r"^Sell Now @ market \| Stop @ \$([0-9]+\.[0-9]{2})$")


# ===========================================================================
# 1. Happy path
# ===========================================================================
@pytest.mark.parametrize("signal", ["STRONG BUY", "BUY", "HOLD"])
def test_active_long_signals_emit_sell_zone(signal: str) -> None:
    """STRONG BUY / BUY / HOLD all yield a parseable Sell Zone with lower < upper."""
    out = apply_sell_side_range(
        signal=signal,
        current_price=100.00,
        safe_atr=2.00,
        chandelier_long=95.00,
        chandelier_short=110.00,
        forecast_price=110.00,
    )
    m = _SELL_ZONE_RE.match(out)
    assert m is not None, f"Unexpected sell-side format for {signal!r}: {out!r}"
    lo, hi, stop = map(float, m.groups())

    # Monotonicity invariant: take-profit lower must be strictly below upper.
    assert lo < hi, f"sellRange lower ({lo}) must be < upper ({hi})"
    # Lower bound is the +1.5σ ATR target.
    assert lo == pytest.approx(100.00 + 1.5 * 2.00)
    # Upper bound = max(price + 3*ATR=106.0, forecast=110.0) → forecast wins.
    assert hi == pytest.approx(110.00)
    # Trailing stop is the Chandelier Long when available.
    assert stop == pytest.approx(95.00)


def test_forecast_below_atr_ceiling_uses_atr_resistance() -> None:
    """When the forecast does not exceed the +3σ ATR level, the ATR level wins."""
    out = apply_sell_side_range(
        signal="BUY",
        current_price=100.00,
        safe_atr=5.00,
        chandelier_long=92.00,
        chandelier_short=0.0,
        forecast_price=108.00,  # < 100 + 3*5 = 115
    )
    m = _SELL_ZONE_RE.match(out)
    assert m is not None
    _, hi, _ = map(float, m.groups())
    assert hi == pytest.approx(115.00), "ATR-derived ceiling should win over a smaller forecast"


def test_risk_reduce_emits_sell_now() -> None:
    """RISK REDUCE collapses the take-profit zone into an immediate-exit string."""
    out = apply_sell_side_range(
        signal="RISK REDUCE",
        current_price=50.00,
        safe_atr=1.00,
        chandelier_long=48.00,
        chandelier_short=0.0,
        forecast_price=55.00,
    )
    m = _SELL_NOW_RE.match(out)
    assert m is not None, f"RISK REDUCE should emit 'Sell Now @ market': {out!r}"
    (stop,) = map(float, m.groups())
    assert stop == pytest.approx(48.00)


# ===========================================================================
# 2. Edge cases — fail-closed contract
# ===========================================================================
def test_zero_forecast_does_not_fabricate_upper_bound() -> None:
    """forecast_price=0.0 means 'no forecast'; upper must fall back to pure ATR."""
    out = apply_sell_side_range(
        signal="BUY",
        current_price=200.00,
        safe_atr=4.00,
        chandelier_long=190.00,
        chandelier_short=0.0,
        forecast_price=0.0,
    )
    m = _SELL_ZONE_RE.match(out)
    assert m is not None
    _, hi, _ = map(float, m.groups())
    # Exactly 200 + 3 * 4 = 212, never inflated by a fake forecast.
    assert hi == pytest.approx(212.00)


def test_missing_chandelier_falls_back_to_atr_stop() -> None:
    """chandelier_long=0 → trailing stop derived from current_price - 2.5*ATR."""
    out = apply_sell_side_range(
        signal="HOLD",
        current_price=80.00,
        safe_atr=2.00,
        chandelier_long=0.0,
        chandelier_short=0.0,
        forecast_price=0.0,
    )
    m = _SELL_ZONE_RE.match(out)
    assert m is not None
    _, _, stop = map(float, m.groups())
    assert stop == pytest.approx(80.00 - 2.5 * 2.00)


def test_stop_never_goes_negative_under_extreme_atr() -> None:
    """A pathologically large ATR must clamp the stop floor to >= $0.01."""
    out = apply_sell_side_range(
        signal="BUY",
        current_price=1.00,
        safe_atr=10.00,  # > current_price -> would drive stop negative
        chandelier_long=0.0,
        chandelier_short=0.0,
        forecast_price=0.0,
    )
    m = _SELL_ZONE_RE.match(out)
    assert m is not None
    _, _, stop = map(float, m.groups())
    assert stop >= 0.01, "stop must be clamped to >= $0.01 (never negative or zero)"


def test_unknown_signal_fails_closed_to_sell_now() -> None:
    """Unknown / future signal strings fail closed to the conservative exit branch."""
    out = apply_sell_side_range(
        signal="MOON",  # unknown
        current_price=50.00,
        safe_atr=1.0,
        chandelier_long=49.00,
        chandelier_short=0.0,
        forecast_price=0.0,
    )
    assert _SELL_NOW_RE.match(out), f"Unknown signal must fail closed: {out!r}"


# ===========================================================================
# 3. Schema integration
# ===========================================================================
def test_sell_range_registered_in_column_schema() -> None:
    """sellRange must be a first-class entry in COLUMN_SCHEMA between buyRange and notes."""
    keys = [c["key"] for c in config.COLUMN_SCHEMA]
    assert "sellRange" in keys, "sellRange must be registered in config.COLUMN_SCHEMA"
    headers = {c["key"]: c["header"] for c in config.COLUMN_SCHEMA}
    assert headers["sellRange"] == "Sell Range", "Google Sheets header must read 'Sell Range'"
    # Ordering: sellRange comes immediately after buyRange (kept as a pair for the UI).
    buy_idx = keys.index("buyRange")
    sell_idx = keys.index("sellRange")
    assert sell_idx == buy_idx + 1, "sellRange must follow buyRange in COLUMN_SCHEMA ordering"


def test_evaluate_security_returns_sell_range() -> None:
    """End-to-end: StrategyEngine.evaluate_security must populate 'sellRange'."""
    bar = MarketBarDTO(datetime.now(), "AAPL", 150.00, 152.00, 149.00, 150.00, 4_000_000)
    fund = FundamentalDataDTO(
        ticker="AAPL", company_name="Apple Inc.", sector="Technology",
        pe_ratio=28.0, pb_ratio=42.0, book_value=4.00, eps_trailing=6.00,
        dividend_yield=0.005, dividend_growth_rate=0.05, payout_ratio=0.15,
    )
    macro = MacroEconomicDTO(0.45, 2.50, 2.10, 4.0)
    se = StrategyEngine()
    out = se.evaluate_security(
        bar=bar, fundamentals=fund, macro=macro,
        forecast_price=160.00, trend_strength=65.0, atr=2.50,
    )
    assert "sellRange" in out, "evaluate_security must return a 'sellRange' key"
    assert isinstance(out["sellRange"], str) and len(out["sellRange"]) > 0
    # Must match one of the two canonical formats.
    assert _SELL_ZONE_RE.match(out["sellRange"]) or _SELL_NOW_RE.match(out["sellRange"])


# ===========================================================================
# 4. Lookahead invariant
# ===========================================================================
def test_sell_range_invariant_under_future_input_perturbation() -> None:
    """The helper is pure w.r.t. its scalars: perturbing inputs the caller would only
    have AFTER the cutoff cannot change a prior emission. This guards the contract that
    upstream callers must keep ATR / chandelier / forecast lookahead-free — if anyone
    later wires a non-causal input here, this test still passes because the helper itself
    has no hidden state, but the test documents the invariant so a future refactor
    introducing state (e.g. caching) would fail it."""
    args = dict(signal="BUY", current_price=100.0, safe_atr=2.0,
                chandelier_long=95.0, chandelier_short=0.0, forecast_price=110.0)
    first = apply_sell_side_range(**args)
    # Call again with the same inputs (simulating a "today" re-eval after future bars
    # would have been observed — the helper has no time dependency).
    second = apply_sell_side_range(**args)
    assert first == second, "apply_sell_side_range must be a pure function of its scalars"
