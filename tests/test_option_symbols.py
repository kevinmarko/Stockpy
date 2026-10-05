"""Parity tests for ``data/option_symbols.py``.

The option-symbol parser and Black-Scholes price were copied out of the
retired ``pilots/options_risk.py`` so paper option marking survives the
options-desk archive (2026-09, step 4a). These tests pin the copies to the
originals on a grid of inputs -- including 0DTE, zero/NaN volatility and
degenerate spot/strike -- so the copy cannot silently drift.

The tests import ``pilots.options_risk`` only to compare against it. When
that module moves to ``legacy/`` (step 4b) the comparison half is skipped and
the pinned-value tests below keep guarding the math.
"""

from __future__ import annotations

import itertools
import math

import pytest

from data.option_symbols import black_scholes_price, parse_option_symbol

try:  # the original lives in the options desk, archived in step 4b
    from pilots.options_risk import (
        calculate_black_scholes_greeks as _orig_bs,
        parse_option_symbol as _orig_parse,
    )
except Exception:  # noqa: BLE001
    _orig_bs = None
    _orig_parse = None

needs_original = pytest.mark.skipif(
    _orig_bs is None, reason="pilots.options_risk archived; pinned tests still run"
)

_SPOTS = [0.0, -5.0, 50.0, 99.5, 100.0, 100.5, 250.0]
_STRIKES = [0.0, 50.0, 100.0, 150.0]
_TIMES = [0.0, 1e-13, 1e-12, 1.0 / 365.0, 30.0 / 365.0, 1.0, 2.5]
_SIGMAS = [0.0, 1e-13, float("nan"), 0.05, 0.25, 0.8, 2.0]
_TYPES = ["call", "put", "CALL", "Put", ""]
_RATES = [0.0, 0.045, 0.10]


@needs_original
def test_black_scholes_price_matches_original_on_grid():
    n = 0
    for spot, strike, t, sigma, typ, r in itertools.product(
        _SPOTS, _STRIKES, _TIMES, _SIGMAS, _TYPES, _RATES
    ):
        expected = _orig_bs(spot, strike, t, sigma, typ, r)["price"]
        got = black_scholes_price(spot, strike, t, sigma, typ, r)
        assert got == expected, (spot, strike, t, sigma, typ, r, got, expected)
        n += 1
    assert n == len(_SPOTS) * len(_STRIKES) * len(_TIMES) * len(_SIGMAS) * len(_TYPES) * len(_RATES)


@needs_original
def test_black_scholes_price_default_rate_matches_original(monkeypatch):
    from settings import settings

    monkeypatch.setattr(settings, "OPTIONS_RISK_FREE_RATE", 0.037)
    for typ in ("call", "put"):
        assert black_scholes_price(100.0, 95.0, 0.25, 0.3, typ) == _orig_bs(
            100.0, 95.0, 0.25, 0.3, typ
        )["price"]


_SYMBOLS = [
    "AAPL 2026-09-18 $150.00 CALL",
    "aapl 2026-09-18 $150 put",
    "  SPY 2027-01-15 $450.5 PUT  ",
    "BRK 2026-12-18 $0.5 call",
    "AAPL",
    "AAPL 2026-09-18 150.00 CALL",
    "AAPL 2026-9-18 $150.00 CALL",
    "AAPL 2026-09-18 $150.00 STRADDLE",
    "BRK.B 2026-09-18 $300 CALL",
    "",
    "SPY260918C00450000",
]


@needs_original
def test_parse_option_symbol_matches_original():
    for sym in _SYMBOLS:
        assert parse_option_symbol(sym) == _orig_parse(sym), sym


def test_parse_option_symbol_pinned():
    assert parse_option_symbol("aapl 2026-09-18 $150 put") == {
        "ticker": "AAPL",
        "expiration": "2026-09-18",
        "strike": 150.0,
        "option_type": "put",
    }
    assert parse_option_symbol("AAPL") is None
    assert parse_option_symbol("SPY260918C00450000") is None


def test_black_scholes_price_pinned_values():
    # 0DTE and zero/NaN vol return intrinsic value exactly.
    assert black_scholes_price(105.0, 100.0, 0.0, 0.3, "call", 0.045) == 5.0
    assert black_scholes_price(95.0, 100.0, 0.0, 0.3, "put", 0.045) == 5.0
    assert black_scholes_price(95.0, 100.0, 0.0, 0.3, "call", 0.045) == 0.0
    assert black_scholes_price(105.0, 100.0, 0.5, 0.0, "call", 0.045) == 5.0
    assert black_scholes_price(105.0, 100.0, 0.5, float("nan"), "call", 0.045) == 5.0
    # Degenerate spot/strike.
    assert black_scholes_price(0.0, 100.0, 0.5, 0.3, "call", 0.045) == 0.0
    assert black_scholes_price(100.0, 0.0, 0.5, 0.3, "put", 0.045) == 0.0
    # Textbook value: S=K=100, T=1, sigma=0.2, r=0.05 -> call 10.4506, put 5.5735.
    assert math.isclose(black_scholes_price(100.0, 100.0, 1.0, 0.2, "call", 0.05), 10.4506, abs_tol=1e-4)
    assert math.isclose(black_scholes_price(100.0, 100.0, 1.0, 0.2, "put", 0.05), 5.5735, abs_tol=1e-4)


def test_paper_store_and_broker_do_not_import_options_desk():
    """The paper store and paper broker use this module, not the options desk."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for rel in ("data/paper_account_store.py", "execution/fmp_paper_broker.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
            elif isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
        assert "pilots.options_risk" not in mods, rel
