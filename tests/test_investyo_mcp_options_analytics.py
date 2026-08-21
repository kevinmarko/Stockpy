import pytest
import math
import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from investyo_mcp_server import analyze_options_chain, scan_0dte_signals

def test_scan_0dte_signals_honest_status():
    result = scan_0dte_signals("SPY", 1)
    
    # Evaluate what the setting actually is right now
    try:
        from settings import settings as _s
        expected_wired = bool(getattr(_s, "OPTIONS_0DTE_ENABLED", False))
    except ImportError:
        expected_wired = False

    assert "live_exit_gate_wired" in result
    assert result["live_exit_gate_wired"] is expected_wired, "Must honestly report the real live exit gate status"
    
    assert "strategy_registry_status" in result
    assert result["strategy_registry_status"] == "unregistered", "Must honestly report it is unregistered"
    
    # Must never call execute_0dte_trade (implicit, we don't mock it because it would fail if called)

def test_analyze_options_chain_missing_data(monkeypatch):
    # Test that missing chain data degrades gracefully without crashing
    def mock_fetch_chain(*args, **kwargs):
        raise ValueError("Network error")
        
    class MockOptionsProvider:
        def fetch_options_chain(self, *args, **kwargs):
            raise ValueError("Network error")
            
    monkeypatch.setattr("data.market_data.get_options_provider", lambda: MockOptionsProvider())
    
    result = analyze_options_chain("XYZ")
    assert "error" in result
    assert "No chain data" in result["error"]


def _contains_nan_string(obj) -> bool:
    """Walk a nested dict/list/scalar structure and return True if the literal
    string "NaN" appears anywhere -- catches both a bare "NaN" value and any
    string that merely contains it as a substring."""
    if isinstance(obj, str):
        return "NaN" in obj
    if isinstance(obj, dict):
        return any(_contains_nan_string(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_contains_nan_string(v) for v in obj)
    return False


class _MockOptionsProvider:
    """Mimics data.market_data.CompositeOptionsProvider.fetch_options_chain's real
    two-shape contract: a bare list of expiration-date strings when `expiration` is
    omitted, and a realistic {"calls": [...], "puts": [...]} chain body (yfinance-style
    field names: strike/bid/ask/lastPrice/impliedVolatility) for a specific expiration --
    the exact shape pilots.volatility_surface.calculate_volatility_surface's
    parse_expiration_slice() consumes."""

    def __init__(self, spot: float):
        self.spot = spot
        today = date.today()
        self.expirations = [
            (today + timedelta(days=30)).isoformat(),
            (today + timedelta(days=60)).isoformat(),
        ]

    def fetch_options_chain(self, symbol, expiration=None):
        if expiration is None:
            return list(self.expirations)
        strikes = [self.spot - 10, self.spot - 5, self.spot, self.spot + 5, self.spot + 10]
        calls, puts = [], []
        for k in strikes:
            base_iv = 0.25 + 0.002 * abs(k - self.spot)
            calls.append({
                "strike": float(k),
                "bid": round(max(0.1, self.spot - k + 3.0), 2),
                "ask": round(max(0.2, self.spot - k + 3.5), 2),
                "lastPrice": round(max(0.15, self.spot - k + 3.2), 2),
                "impliedVolatility": round(base_iv, 4),
            })
            puts.append({
                "strike": float(k),
                "bid": round(max(0.1, k - self.spot + 3.0), 2),
                "ask": round(max(0.2, k - self.spot + 3.5), 2),
                "lastPrice": round(max(0.15, k - self.spot + 3.2), 2),
                "impliedVolatility": round(base_iv + 0.01, 4),
            })
        return {"calls": calls, "puts": puts}


def test_analyze_options_chain_happy_path(monkeypatch):
    """Realistic 2-step chain fetch (bug 1) feeding a real
    pilots.volatility_surface.calculate_volatility_surface() surface with real,
    non-empty smiles/atm_iv (proving bug 2's fair_iv_forecast derivation reads real
    surface data, not a nonexistent top-level key), a plain-dict mispricing result --
    never a raw MispricingAnalysis dataclass (bug 3) -- and confirms no field anywhere
    in the response serializes NaN as the literal string "NaN" (bug 4). Also implicitly
    exercises bug 5's outer try/except by completing without raising.

    Honesty note (see the accompanying report): pilots.vol_mispricing.
    extract_chain_contracts() does not descend into a per-expiration {"calls":...,
    "puts":...} sub-dict when chain_data is a multi-expiration map (only a flat
    list-of-contracts or a single-expiration top-level calls/puts dict is handled) --
    a real, pre-existing gap shared identically by the "correct" reference function
    pilots.vol_mispricing.get_volatility_mispricing_data(), which papers over it with
    a synthetic fallback chain that analyze_options_chain's own docstring explicitly
    promises never to do (CONSTRAINT #4: never fabricate). So `mispricing` here is
    honestly EMPTY_CHAIN (a real, non-raising MispricingAnalysis.to_dict() result, not
    an error dict) rather than populated with strikes. To still prove bug 2's fix
    end-to-end -- that a real, non-None fair_iv_forecast scalar reaches
    evaluate_strike_mispricing rather than the old always-None top-level-key bug --
    this test wraps (not replaces) the real evaluate_strike_mispricing to capture its
    call kwargs.
    """
    import data.market_data as md_mod
    import technical_options_engine as toe_mod
    import pilots.vol_mispricing as vm_mod
    from pilots.vol_mispricing import MispricingAnalysis

    spot = 150.0
    idx = pd.bdate_range(end=pd.Timestamp.today(), periods=90)
    bars = pd.DataFrame(
        {"Open": spot, "High": spot + 1, "Low": spot - 1, "Close": spot, "Volume": 1_000_000},
        index=idx,
    )

    fake_provider = MagicMock()
    fake_provider.get_intraday_bars.return_value = bars
    fake_provider.get_latest_quote.return_value = SimpleNamespace(price=spot, is_stale=False)
    monkeypatch.setattr(md_mod, "get_provider", lambda *a, **k: fake_provider, raising=False)
    monkeypatch.setattr(
        md_mod, "get_options_provider", lambda *a, **k: _MockOptionsProvider(spot), raising=False
    )

    fake_directive = {
        "Symbol": "AAPL",
        "Strategy": "Put Credit Spread",
        "Action": "SELL",
        "Net_Premium": 1.25,
        "Short_Strike": 145.0,
        "Long_Strike": 140.0,
        "Integrity_OK": True,
    }
    monkeypatch.setattr(toe_mod, "build_premium_directive", lambda *a, **k: dict(fake_directive))

    captured_kwargs = {}
    real_evaluate = vm_mod.evaluate_strike_mispricing

    def _capturing_evaluate(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(vm_mod, "evaluate_strike_mispricing", _capturing_evaluate)

    result = analyze_options_chain("AAPL", target_dte=30)

    assert "error" not in result
    assert set(["ticker", "spot_price", "directive", "surface", "mispricing"]) <= set(result.keys())

    # directive: a real dict, not None/error
    assert isinstance(result["directive"], dict)
    assert "error" not in result["directive"]

    # surface: a real dict with real numeric fields
    surface = result["surface"]
    assert isinstance(surface, dict)
    assert "error" not in surface
    smiles = surface.get("smiles")
    assert isinstance(smiles, dict) and len(smiles) > 0
    for entry in smiles.values():
        assert isinstance(entry.get("atm_iv"), (int, float))
        assert entry["atm_iv"] > 0

    # bug 2: a real, non-None fair_iv_forecast (sourced from the surface's own
    # smile nearest target_dte) reached evaluate_strike_mispricing -- not the old
    # surface.get("atm_iv") bug, which always resolved to None.
    assert captured_kwargs.get("fair_iv_forecast") is not None
    assert captured_kwargs["fair_iv_forecast"] > 0

    # mispricing: a plain dict, never a raw MispricingAnalysis dataclass (bug 3)
    mispricing = result["mispricing"]
    assert type(mispricing) is dict
    assert not isinstance(mispricing, MispricingAnalysis)
    assert "error" not in mispricing

    # bug 4: NaN must never serialize as the literal string "NaN" anywhere in the
    # nested response
    serialized = json.dumps(result, default=str)
    assert "NaN" not in serialized
    assert not _contains_nan_string(result)
