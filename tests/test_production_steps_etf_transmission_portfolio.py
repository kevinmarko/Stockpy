"""
tests/test_production_steps_etf_transmission_portfolio.py
============================================================
Wiring tests for ``pipeline/production_steps.py::_build_etf_transmission_cov_matrix``
-- the function that feeds an ETF-co-ownership-inflated covariance matrix
into ``sizing.position_sizer.apply_portfolio_gross_cap``'s EXISTING
``cov_matrix``/``target_vol`` risk-aware path (built for exactly this
purpose, previously unreachable from production -- see
``sizing/position_sizer.py``'s "Reduction-only guarantee" section, added
in the sibling PR that fixed the gross-cap covariance-path uplift bug).

Modeled on ``tests/test_production_steps_etf_transmission.py``: deliberately
targets the module-level function directly rather than going through
``StrategyEvalStep.run()`` (which imports ``main_orchestrator`` and its full
heavy engine chain at call time), so the suite stays runnable without
yfinance/fredapi/statsmodels.

The single most important test in this file is
``TestCoverageGapFallback::test_partial_price_bar_coverage_falls_back_to_none``:
this function must NEVER return a partially-covered covariance matrix.
``portfolio_vol_target`` (see ``sizing/vol_target.py``) explicitly ZEROES
OUT any symbol missing from ``cov_matrix`` -- a far harsher outcome for a
coverage gap than the existing sum-of-|weight| fallback this feature is
opt-in to replace. ``None`` (the fallback signal) is the only honest answer
when coverage is incomplete.

This sandbox has NO live-market network access. ``data.etf_holdings``
(Agent B's module) is stubbed into ``sys.modules``, matching the sibling
measurement-column test file's convention. No real network request is made
by anything in this file.
"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from pipeline.production_steps import _build_etf_transmission_cov_matrix


@dataclass(frozen=True)
class StubHolding:
    etf_symbol: str
    holding_symbol: str
    weight: float = float("nan")
    shares_held: float = float("nan")
    as_of_date: date = date(2020, 1, 1)
    source: str = "stub"


def _bars(returns: np.ndarray, start: str = "2024-01-01") -> pd.DataFrame:
    close = 100.0 * np.cumprod(1.0 + np.concatenate([[0.0], returns]))
    idx = pd.bdate_range(start=start, periods=len(close))
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6},
        index=idx,
    )


@pytest.fixture
def synthetic_universe():
    """tech_raw covering SPY/XLK + a 3-symbol operator universe, all with
    120 trading days of aligned history (comfortably above the default
    60-day window)."""
    rng = np.random.RandomState(2026)
    n = 120
    tech_raw = {
        "SPY": _bars(rng.normal(0.0, 0.012, n)),
        "XLK": _bars(rng.normal(0.0, 0.012, n)),
        "AAA": _bars(rng.normal(0.0, 0.015, n)),
        "BBB": _bars(rng.normal(0.0, 0.015, n)),
        "CCC": _bars(rng.normal(0.0, 0.015, n)),
    }
    return tech_raw


def _holdings_module(get_etf_holdings):
    mod = types.ModuleType("data.etf_holdings")
    mod.get_etf_holdings = get_etf_holdings
    return mod


def _install_holdings(monkeypatch, get_etf_holdings):
    monkeypatch.setitem(sys.modules, "data.etf_holdings", _holdings_module(get_etf_holdings))


def _settings(**overrides):
    base = {
        "ETF_TRANSMISSION_PORTFOLIO_ENABLED": True,
        "ETF_HOLDINGS_MARKET_PROXY": "SPY",
        "ETF_TRANSMISSION_WRAPPERS": ["SPY", "XLK"],
        "ETF_TRANSMISSION_COV_INFLATION": 0.25,
        "ETF_TRANSMISSION_COV_WINDOW_DAYS": 60,
    }
    base.update(overrides)
    return [patch(f"settings.settings.{k}", v) for k, v in base.items()]


class _Patches:
    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


_FULL_HOLDINGS = {
    "XLK": [
        StubHolding("XLK", "AAA", weight=0.05),
        StubHolding("XLK", "BBB", weight=0.05),
    ],
}


class TestGateOff:
    def test_disabled_returns_none_with_zero_network_calls(self, synthetic_universe, monkeypatch):
        holdings_fn = MagicMock()
        _install_holdings(monkeypatch, holdings_fn)

        with patch("settings.settings.ETF_TRANSMISSION_PORTFOLIO_ENABLED", False):
            result = _build_etf_transmission_cov_matrix(
                ["AAA", "BBB", "CCC"], synthetic_universe,
            )

        assert result is None
        holdings_fn.assert_not_called()


class TestHappyPath:
    def test_returns_a_covariance_matrix_when_fully_covered(self, synthetic_universe, monkeypatch):
        _install_holdings(monkeypatch, lambda etfs, **kw: _FULL_HOLDINGS)
        with _Patches(_settings()):
            result = _build_etf_transmission_cov_matrix(
                ["AAA", "BBB", "CCC"], synthetic_universe,
            )
        assert result is not None
        assert sorted(result.index) == ["AAA", "BBB", "CCC"]
        assert sorted(result.columns) == ["AAA", "BBB", "CCC"]

    def test_holdings_requested_for_the_configured_wrapper_universe(self, synthetic_universe, monkeypatch):
        holdings_fn = MagicMock(return_value=_FULL_HOLDINGS)
        _install_holdings(monkeypatch, holdings_fn)
        with _Patches(_settings()):
            _build_etf_transmission_cov_matrix(["AAA", "BBB", "CCC"], synthetic_universe)

        holdings_fn.assert_called_once()
        assert sorted(holdings_fn.call_args.args[0]) == ["SPY", "XLK"]
        assert "as_of" in holdings_fn.call_args.kwargs

    def test_market_proxy_appended_when_omitted_from_wrapper_config(self, synthetic_universe, monkeypatch):
        holdings_fn = MagicMock(return_value=_FULL_HOLDINGS)
        _install_holdings(monkeypatch, holdings_fn)
        with _Patches(_settings(ETF_TRANSMISSION_WRAPPERS=["XLK"])):
            _build_etf_transmission_cov_matrix(["AAA", "BBB", "CCC"], synthetic_universe)
        assert sorted(holdings_fn.call_args.args[0]) == ["SPY", "XLK"]


class TestCoverageGapFallback:
    """None, never a partially-covered matrix -- see module docstring."""

    def test_partial_price_bar_coverage_falls_back_to_none(self, synthetic_universe, monkeypatch):
        _install_holdings(monkeypatch, lambda etfs, **kw: _FULL_HOLDINGS)
        # DDD requested but has no bars anywhere in tech_raw.
        with _Patches(_settings()):
            result = _build_etf_transmission_cov_matrix(
                ["AAA", "BBB", "DDD"], synthetic_universe,
            )
        assert result is None

    def test_no_covered_basket_rows_falls_back_to_none(self, synthetic_universe, monkeypatch):
        _install_holdings(monkeypatch, lambda etfs, **kw: {})
        with _Patches(_settings()):
            result = _build_etf_transmission_cov_matrix(
                ["AAA", "BBB", "CCC"], synthetic_universe,
            )
        assert result is None

    def test_fewer_than_window_overlapping_observations_falls_back_to_none(
        self, synthetic_universe, monkeypatch,
    ):
        _install_holdings(monkeypatch, lambda etfs, **kw: _FULL_HOLDINGS)
        with _Patches(_settings(ETF_TRANSMISSION_COV_WINDOW_DAYS=500)):
            result = _build_etf_transmission_cov_matrix(
                ["AAA", "BBB", "CCC"], synthetic_universe,
            )
        assert result is None

    def test_fewer_than_two_requested_symbols_falls_back_to_none(self, synthetic_universe, monkeypatch):
        _install_holdings(monkeypatch, lambda etfs, **kw: _FULL_HOLDINGS)
        with _Patches(_settings()):
            result = _build_etf_transmission_cov_matrix(["AAA"], synthetic_universe)
        assert result is None

    def test_no_configured_wrappers_falls_back_to_none(self, synthetic_universe, monkeypatch):
        holdings_fn = MagicMock()
        _install_holdings(monkeypatch, holdings_fn)
        with _Patches(_settings(ETF_TRANSMISSION_WRAPPERS=[], ETF_HOLDINGS_MARKET_PROXY="")):
            result = _build_etf_transmission_cov_matrix(["AAA", "BBB"], synthetic_universe)
        assert result is None
        holdings_fn.assert_not_called()

    def test_holdings_fetch_raising_is_never_propagated(self, synthetic_universe, monkeypatch):
        def _raise(*a, **kw):
            raise RuntimeError("simulated EDGAR outage")

        _install_holdings(monkeypatch, _raise)
        with _Patches(_settings()):
            result = _build_etf_transmission_cov_matrix(["AAA", "BBB", "CCC"], synthetic_universe)
        assert result is None  # never raises -- CONSTRAINT #6

    def test_empty_tech_raw_falls_back_to_none(self, monkeypatch):
        _install_holdings(monkeypatch, lambda etfs, **kw: _FULL_HOLDINGS)
        with _Patches(_settings()):
            result = _build_etf_transmission_cov_matrix(["AAA", "BBB", "CCC"], {})
        assert result is None
