"""
tests/test_production_steps_etf_transmission.py
===============================================
Wiring tests for ``pipeline/production_steps.py::_apply_etf_transmission`` --
the writeback that maps ``risk/etf_transmission.py``'s pure-math output onto
``dashboard_df``'s three ETF volatility-transmission columns
(``ETF_Ownership_Pct`` / ``ETF_Comovement_R2`` / ``ETF_Primary_Wrapper``).

Modeled on ``tests/test_production_steps_sector_heat.py``: deliberately
targets the module-level function directly rather than going through
``StrategyEvalStep.run()`` (which imports ``main_orchestrator`` and its full
heavy engine chain at call time), so the suite stays runnable without
yfinance/fredapi/statsmodels.

This sandbox has NO live-market network access. ``data.etf_holdings`` (Agent
B's module) is stubbed into ``sys.modules`` and ``data_engine.DataEngine`` is
never reached because every ETF's bars are supplied through ``tech_raw``. No
real network request is made by anything in this file.
"""
from __future__ import annotations

import math
import sys
import types
from dataclasses import dataclass
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from pipeline.production_steps import _apply_etf_transmission

_COLS = ("ETF_Ownership_Pct", "ETF_Comovement_R2", "ETF_Primary_Wrapper")


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
def synthetic_market():
    """(tech_raw, dashboard_df) with AAPL/MSFT wrapped by SPY + XLK.

    AAPL and XLK share a genuine non-market shock (real transmission); MSFT's
    idiosyncratic component is independent of XLK's (no transmission).
    """
    rng = np.random.RandomState(2026)
    n = 400
    mkt = rng.normal(0.0, 0.012, n)
    shared = rng.normal(0.0, 0.008, n)
    xlk = 1.0 * mkt + shared + rng.normal(0.0, 0.001, n)
    aapl = 1.05 * mkt + shared + rng.normal(0.0, 0.001, n)
    msft = 1.0 * mkt + rng.normal(0.0, 0.008, n)

    tech_raw = {
        "SPY": _bars(mkt),
        "XLK": _bars(xlk),
        "AAPL": _bars(aapl),
        "MSFT": _bars(msft),
    }
    df = pd.DataFrame([
        {"Symbol": "AAPL", "Price": 200.0, "Market Cap": 2_000_000_000.0},
        {"Symbol": "MSFT", "Price": 400.0, "Market Cap": 4_000_000_000.0},
    ])
    return tech_raw, df


def _holdings_module(get_etf_holdings):
    mod = types.ModuleType("data.etf_holdings")
    mod.get_etf_holdings = get_etf_holdings
    return mod


def _install_holdings(monkeypatch, get_etf_holdings):
    monkeypatch.setitem(sys.modules, "data.etf_holdings", _holdings_module(get_etf_holdings))


def _settings(**overrides):
    """Patch context enabling the feature with a small, deterministic config."""
    base = {
        "ETF_TRANSMISSION_ENABLED": True,
        "ETF_HOLDINGS_MARKET_PROXY": "SPY",
        "ETF_TRANSMISSION_WRAPPERS": ["SPY", "XLK"],
        "ETF_TRANSMISSION_EXCLUDED_SYMBOLS": [],
        "ETF_TRANSMISSION_WINDOW_DAYS": 250,
        "ETF_TRANSMISSION_MIN_OBS": 250,
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


class TestGateOff:
    def test_disabled_leaves_all_columns_nan_with_zero_network_calls(self, synthetic_market, monkeypatch):
        tech_raw, df = synthetic_market
        holdings_fn = MagicMock()
        _install_holdings(monkeypatch, holdings_fn)

        with patch("settings.settings.ETF_TRANSMISSION_ENABLED", False):
            with patch("data_engine.DataEngine") as mock_engine:
                _apply_etf_transmission(df, tech_raw)

        holdings_fn.assert_not_called()
        mock_engine.assert_not_called()
        for col in _COLS:
            assert col in df.columns
            assert df[col].isna().all()

    def test_disabled_creates_the_columns_even_on_an_empty_frame(self):
        df = pd.DataFrame([])
        with patch("settings.settings.ETF_TRANSMISSION_ENABLED", False):
            _apply_etf_transmission(df, {})
        for col in _COLS:
            assert col in df.columns


class TestHappyPath:
    def test_populates_all_three_columns(self, synthetic_market, monkeypatch):
        tech_raw, df = synthetic_market
        holdings = {
            "SPY": [
                StubHolding("SPY", "AAPL", weight=0.07, shares_held=1_000_000.0),
                StubHolding("SPY", "MSFT", weight=0.06, shares_held=500_000.0),
            ],
            "XLK": [
                StubHolding("XLK", "AAPL", weight=0.22, shares_held=500_000.0),
                StubHolding("XLK", "MSFT", weight=0.20, shares_held=250_000.0),
            ],
        }
        _install_holdings(monkeypatch, lambda etfs, **kw: holdings)

        with _Patches(_settings()):
            _apply_etf_transmission(df, tech_raw)

        aapl = df[df["Symbol"] == "AAPL"].iloc[0]
        msft = df[df["Symbol"] == "MSFT"].iloc[0]

        # shares_out = 2e9 / 200 = 1e7 -> (1e6 + 5e5) / 1e7 = 0.15
        assert aapl["ETF_Ownership_Pct"] == pytest.approx(0.15)
        # shares_out = 4e9 / 400 = 1e7 -> (5e5 + 2.5e5) / 1e7 = 0.075
        assert msft["ETF_Ownership_Pct"] == pytest.approx(0.075)

        # XLK (0.22/0.20) outweighs SPY (0.07/0.06) for both names.
        assert aapl["ETF_Primary_Wrapper"] == "XLK"
        assert msft["ETF_Primary_Wrapper"] == "XLK"

        # AAPL genuinely shares a non-market shock with XLK; MSFT does not.
        assert aapl["ETF_Comovement_R2"] > 0.5
        assert msft["ETF_Comovement_R2"] < 0.2

    def test_holdings_are_requested_for_the_configured_wrapper_universe(self, synthetic_market, monkeypatch):
        tech_raw, df = synthetic_market
        holdings_fn = MagicMock(return_value={
            "XLK": [StubHolding("XLK", "AAPL", weight=0.22, shares_held=1.0)],
        })
        _install_holdings(monkeypatch, holdings_fn)

        with _Patches(_settings()):
            _apply_etf_transmission(df, tech_raw)

        holdings_fn.assert_called_once()
        assert sorted(holdings_fn.call_args.args[0]) == ["SPY", "XLK"]
        assert "as_of" in holdings_fn.call_args.kwargs

    def test_market_proxy_appended_to_wrappers_when_omitted_from_config(self, synthetic_market, monkeypatch):
        tech_raw, df = synthetic_market
        holdings_fn = MagicMock(return_value={})
        _install_holdings(monkeypatch, holdings_fn)

        with _Patches(_settings(ETF_TRANSMISSION_WRAPPERS=["XLK"])):
            _apply_etf_transmission(df, tech_raw)

        assert sorted(holdings_fn.call_args.args[0]) == ["SPY", "XLK"]

    def test_no_second_batched_download_when_bars_are_already_in_tech_raw(self, synthetic_market, monkeypatch):
        tech_raw, df = synthetic_market
        _install_holdings(monkeypatch, lambda etfs, **kw: {
            "XLK": [StubHolding("XLK", "AAPL", weight=0.22, shares_held=1.0)],
        })
        with _Patches(_settings()):
            with patch("data_engine.DataEngine") as mock_engine:
                _apply_etf_transmission(df, tech_raw)
        mock_engine.assert_not_called()


class TestDegradationPaths:
    """Honesty contract: every one of these is NaN, never 0.0."""

    def test_ticker_in_no_covered_etf_stays_nan(self, synthetic_market, monkeypatch):
        tech_raw, df = synthetic_market
        _install_holdings(monkeypatch, lambda etfs, **kw: {
            "XLK": [StubHolding("XLK", "AAPL", weight=0.22, shares_held=1_500_000.0)],
        })
        with _Patches(_settings()):
            _apply_etf_transmission(df, tech_raw)

        msft = df[df["Symbol"] == "MSFT"].iloc[0]
        assert math.isnan(msft["ETF_Ownership_Pct"])
        assert math.isnan(msft["ETF_Comovement_R2"])
        assert pd.isna(msft["ETF_Primary_Wrapper"])
        # ...while AAPL is unaffected.
        assert not math.isnan(df[df["Symbol"] == "AAPL"].iloc[0]["ETF_Ownership_Pct"])

    def test_holdings_fetch_failure_degrades_whole_columns_to_nan(self, synthetic_market, monkeypatch):
        tech_raw, df = synthetic_market

        def _boom(etfs, **kw):
            raise RuntimeError("holdings provider down")

        _install_holdings(monkeypatch, _boom)
        with _Patches(_settings()):
            _apply_etf_transmission(df, tech_raw)  # must not raise
        for col in _COLS:
            assert df[col].isna().all()

    def test_holdings_module_absent_degrades_to_nan(self, synthetic_market, monkeypatch):
        """Agent B's data/etf_holdings.py not present (or not importable) must
        be a soft failure, never a pipeline crash (CONSTRAINT #6)."""
        tech_raw, df = synthetic_market
        monkeypatch.setitem(sys.modules, "data.etf_holdings", None)
        with _Patches(_settings()):
            _apply_etf_transmission(df, tech_raw)  # must not raise
        for col in _COLS:
            assert df[col].isna().all()

    def test_empty_holdings_result_leaves_columns_nan(self, synthetic_market, monkeypatch):
        tech_raw, df = synthetic_market
        _install_holdings(monkeypatch, lambda etfs, **kw: {})
        with _Patches(_settings()):
            _apply_etf_transmission(df, tech_raw)
        for col in _COLS:
            assert df[col].isna().all()

    def test_market_proxy_absent_from_tech_raw_degrades_to_nan(self, synthetic_market, monkeypatch):
        tech_raw, df = synthetic_market
        tech_raw.pop("SPY")
        holdings_fn = MagicMock(return_value={})
        _install_holdings(monkeypatch, holdings_fn)
        with _Patches(_settings()):
            _apply_etf_transmission(df, tech_raw)
        # Bails BEFORE the holdings fetch -- no point paying for it.
        holdings_fn.assert_not_called()
        for col in _COLS:
            assert df[col].isna().all()

    def test_fabricated_zero_market_cap_yields_nan_ownership_not_inf(self, synthetic_market, monkeypatch):
        """FundamentalDataDTO.market_cap defaults to a fabricated 0.0, so a
        naive Market Cap / Price divide yields inf on exactly the names whose
        fundamentals failed."""
        tech_raw, df = synthetic_market
        df.loc[df["Symbol"] == "MSFT", "Market Cap"] = 0.0
        _install_holdings(monkeypatch, lambda etfs, **kw: {
            "XLK": [
                StubHolding("XLK", "AAPL", weight=0.22, shares_held=1_500_000.0),
                StubHolding("XLK", "MSFT", weight=0.20, shares_held=750_000.0),
            ],
        })
        with _Patches(_settings()):
            _apply_etf_transmission(df, tech_raw)

        msft = df[df["Symbol"] == "MSFT"].iloc[0]
        assert math.isnan(msft["ETF_Ownership_Pct"])
        assert not np.isinf(msft["ETF_Ownership_Pct"])
        # The R² leg is independent of fundamentals and still computes.
        assert not math.isnan(msft["ETF_Comovement_R2"])

    def test_missing_market_cap_column_yields_nan_ownership_only(self, synthetic_market, monkeypatch):
        tech_raw, df = synthetic_market
        df = df.drop(columns=["Market Cap"])
        _install_holdings(monkeypatch, lambda etfs, **kw: {
            "XLK": [StubHolding("XLK", "AAPL", weight=0.22, shares_held=1_500_000.0)],
        })
        with _Patches(_settings()):
            _apply_etf_transmission(df, tech_raw)
        assert df["ETF_Ownership_Pct"].isna().all()
        assert df[df["Symbol"] == "AAPL"].iloc[0]["ETF_Primary_Wrapper"] == "XLK"

    def test_insufficient_overlap_yields_nan_r2(self, synthetic_market, monkeypatch):
        tech_raw, df = synthetic_market
        _install_holdings(monkeypatch, lambda etfs, **kw: {
            "XLK": [StubHolding("XLK", "AAPL", weight=0.22, shares_held=1_500_000.0)],
        })
        # Window longer than the fixture's 400 bars.
        with _Patches(_settings(ETF_TRANSMISSION_WINDOW_DAYS=5000, ETF_TRANSMISSION_MIN_OBS=5000)):
            _apply_etf_transmission(df, tech_raw)
        assert df["ETF_Comovement_R2"].isna().all()
        # ...but ownership, which needs no price history, still computes.
        assert not math.isnan(df[df["Symbol"] == "AAPL"].iloc[0]["ETF_Ownership_Pct"])

    def test_market_proxy_only_wrapper_yields_nan_r2_but_real_ownership(
        self, synthetic_market, monkeypatch,
    ):
        """The identification limit surfacing as missing data: with SPY excluded
        from the composite, a SPY-only name has e_t == 0 and no measurable
        partial R². Ownership and the wrapper label are still honest."""
        tech_raw, df = synthetic_market
        _install_holdings(monkeypatch, lambda etfs, **kw: {
            "SPY": [StubHolding("SPY", "AAPL", weight=0.07, shares_held=1_500_000.0)],
        })
        with _Patches(_settings()):
            _apply_etf_transmission(df, tech_raw)

        aapl = df[df["Symbol"] == "AAPL"].iloc[0]
        assert math.isnan(aapl["ETF_Comovement_R2"])
        assert aapl["ETF_Ownership_Pct"] == pytest.approx(0.15)
        assert aapl["ETF_Primary_Wrapper"] == "SPY"

    def test_ticker_that_is_itself_an_etf_is_excluded_entirely(self, monkeypatch):
        """XLK's ownership/R² against its OWN basket is 1.0/1.0 -- maximum
        derate for a trivially wrong reason."""
        rng = np.random.RandomState(9)
        n = 400
        mkt = rng.normal(0.0, 0.012, n)
        xlk = 1.0 * mkt + rng.normal(0.0, 0.004, n)
        tech_raw = {"SPY": _bars(mkt), "XLK": _bars(xlk)}
        df = pd.DataFrame([
            {"Symbol": "XLK", "Price": 200.0, "Market Cap": 2_000_000_000.0},
            {"Symbol": "SPY", "Price": 500.0, "Market Cap": 5_000_000_000.0},
        ])
        holdings_fn = MagicMock(return_value={
            "XLK": [StubHolding("XLK", "XLK", weight=1.0, shares_held=1e7)],
            "SPY": [StubHolding("SPY", "SPY", weight=1.0, shares_held=1e7)],
        })
        _install_holdings(monkeypatch, holdings_fn)

        with _Patches(_settings()):
            _apply_etf_transmission(df, tech_raw)

        # Nothing measurable is left, so we never even fetch holdings.
        holdings_fn.assert_not_called()
        for col in _COLS:
            assert df[col].isna().all()

    def test_operator_declared_fund_is_excluded(self, synthetic_market, monkeypatch):
        """A fund an operator holds that isn't itself one of the configured
        wrappers (e.g. VOO) is excluded via ETF_TRANSMISSION_EXCLUDED_SYMBOLS."""
        tech_raw, df = synthetic_market
        _install_holdings(monkeypatch, lambda etfs, **kw: {
            "XLK": [
                StubHolding("XLK", "AAPL", weight=0.22, shares_held=1_500_000.0),
                StubHolding("XLK", "MSFT", weight=0.20, shares_held=750_000.0),
            ],
        })
        with _Patches(_settings(ETF_TRANSMISSION_EXCLUDED_SYMBOLS=["MSFT"])):
            _apply_etf_transmission(df, tech_raw)

        msft = df[df["Symbol"] == "MSFT"].iloc[0]
        for col in _COLS:
            assert pd.isna(msft[col])
        assert df[df["Symbol"] == "AAPL"].iloc[0]["ETF_Primary_Wrapper"] == "XLK"

    def test_missing_symbol_column_degrades_to_nan_no_crash(self):
        df = pd.DataFrame([{"Price": 1.0}])
        with patch("settings.settings.ETF_TRANSMISSION_ENABLED", True):
            _apply_etf_transmission(df, {})  # must not raise
        for col in _COLS:
            assert df[col].isna().all()

    def test_empty_wrapper_universe_is_a_no_op(self, synthetic_market, monkeypatch):
        tech_raw, df = synthetic_market
        holdings_fn = MagicMock()
        _install_holdings(monkeypatch, holdings_fn)
        with _Patches(_settings(ETF_TRANSMISSION_WRAPPERS=[], ETF_HOLDINGS_MARKET_PROXY="")):
            _apply_etf_transmission(df, tech_raw)
        holdings_fn.assert_not_called()
        for col in _COLS:
            assert df[col].isna().all()

    def test_stock_bars_missing_from_tech_raw_yields_nan_r2(self, synthetic_market, monkeypatch):
        tech_raw, df = synthetic_market
        tech_raw.pop("MSFT")
        _install_holdings(monkeypatch, lambda etfs, **kw: {
            "XLK": [
                StubHolding("XLK", "AAPL", weight=0.22, shares_held=1_500_000.0),
                StubHolding("XLK", "MSFT", weight=0.20, shares_held=750_000.0),
            ],
        })
        with _Patches(_settings()):
            _apply_etf_transmission(df, tech_raw)

        msft = df[df["Symbol"] == "MSFT"].iloc[0]
        assert math.isnan(msft["ETF_Comovement_R2"])
        assert msft["ETF_Ownership_Pct"] == pytest.approx(0.075)


class TestLoggingDiscipline:
    def test_one_info_line_per_cycle_not_one_per_name(self, synthetic_market, monkeypatch, caplog):
        """40 warnings a cycle is how a real signal gets ignored."""
        tech_raw, df = synthetic_market
        _install_holdings(monkeypatch, lambda etfs, **kw: {
            "XLK": [StubHolding("XLK", "AAPL", weight=0.22, shares_held=1_500_000.0)],
        })
        with caplog.at_level("INFO", logger="ProductionPipeline"):
            with _Patches(_settings()):
                _apply_etf_transmission(df, tech_raw)

        etf_records = [r for r in caplog.records if "ETF transmission" in r.getMessage()]
        assert len(etf_records) == 1
        assert etf_records[0].levelname == "INFO"
        # MSFT is uncovered but never gets its own log line.
        assert "MSFT" not in etf_records[0].getMessage()
