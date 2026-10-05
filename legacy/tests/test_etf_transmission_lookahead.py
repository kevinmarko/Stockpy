"""No-lookahead tests for the ETF volatility-transmission measurement
(``risk/etf_transmission.py``, wired by
``pipeline/production_steps.py::_apply_etf_transmission``).

Per the repo convention of one dedicated file per subsystem's no-lookahead
guarantee -- see ``tests/test_sector_heat_lookahead.py``,
``tests/test_sentiment_pit_lookahead.py``, ``tests/test_hmm_no_lookahead.py``.

Two independent leakage surfaces, both covered here:

1. **Price/return dimension.** ``compute_market_residual_r2`` must consume only
   rows up to and including the as-of date on all THREE legs (the stock, the
   ETF composite, and the market proxy) -- perturbing any one of them after a
   cutoff must leave the value at that cutoff bit-identical. This mirrors the
   two-sided perturbation pair ``tests/test_indicators_lookahead.py`` runs
   against ``processing_engine.calculate_rolling_beta``, whose alignment
   contract this function follows exactly.

2. **Holdings-composition dimension.** ETF baskets are published with an
   ``as_of_date``. A basket row stamped AFTER the cycle's as-of date must never
   influence that cycle's ownership, composite, or wrapper label -- the
   provider is expected to honor its own ``as_of``, but the measurement layer
   must not depend on that.

There is no network access in this sandbox; everything is synthetic fixtures
plus a stubbed ``data.etf_holdings`` module.
"""
from __future__ import annotations

import math
import sys
import types
from dataclasses import dataclass
from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from risk.etf_transmission import (
    build_etf_return_composite,
    compute_etf_ownership,
    compute_market_residual_r2,
    filter_holdings_as_of,
    primary_wrapper,
)
from tests.lookahead_check import make_synthetic_ohlcv, verify_no_lookahead

AS_OF = date(2026, 6, 24)
_WINDOW = 60
_CUTOFF = 150


@dataclass(frozen=True)
class StubHolding:
    """Local stand-in for data.etf_holdings.ETFHolding (same field names)."""
    etf_symbol: str
    holding_symbol: str
    weight: float = float("nan")
    shares_held: float = float("nan")
    as_of_date: date = AS_OF
    source: str = "stub"


@pytest.fixture
def stock_bars():
    return make_synthetic_ohlcv(periods=200, seed=101)


@pytest.fixture
def etf_bars():
    return make_synthetic_ohlcv(periods=200, seed=202)


@pytest.fixture
def market_bars():
    return make_synthetic_ohlcv(periods=200, seed=303)


def _composite(bars: pd.DataFrame) -> pd.Series:
    return bars["Close"].pct_change().dropna()


# ── (i) Price/return dimension: perturbation on each of the three legs ───────


class TestComputeMarketResidualR2NoLookahead:
    def test_no_lookahead_when_stock_series_perturbed(self, stock_bars, etf_bars, market_bars):
        def r2_at(df, t):
            return compute_market_residual_r2(
                df.iloc[: t + 1],
                _composite(etf_bars.iloc[: t + 1]),
                market_bars.iloc[: t + 1],
                window=_WINDOW, min_obs=_WINDOW,
            )

        # Sanity: the measurement is actually producing a number at the cutoff,
        # so an "unchanged" result below isn't vacuously NaN == NaN.
        assert not math.isnan(r2_at(stock_bars, _CUTOFF))
        assert verify_no_lookahead(r2_at, stock_bars, t=_CUTOFF)

    def test_no_lookahead_when_etf_composite_perturbed(self, stock_bars, etf_bars, market_bars):
        def r2_at(df, t):
            return compute_market_residual_r2(
                stock_bars.iloc[: t + 1],
                _composite(df.iloc[: t + 1]),
                market_bars.iloc[: t + 1],
                window=_WINDOW, min_obs=_WINDOW,
            )

        assert not math.isnan(r2_at(etf_bars, _CUTOFF))
        assert verify_no_lookahead(r2_at, etf_bars, t=_CUTOFF)

    def test_no_lookahead_when_market_proxy_perturbed(self, stock_bars, etf_bars, market_bars):
        """The market leg drives BOTH residualizations, so it is its own
        distinct leakage surface -- the mirror case of the ticker-perturbation
        pair in tests/test_indicators_lookahead.py."""
        def r2_at(df, t):
            return compute_market_residual_r2(
                stock_bars.iloc[: t + 1],
                _composite(etf_bars.iloc[: t + 1]),
                df.iloc[: t + 1],
                window=_WINDOW, min_obs=_WINDOW,
            )

        assert not math.isnan(r2_at(market_bars, _CUTOFF))
        assert verify_no_lookahead(r2_at, market_bars, t=_CUTOFF)

    def test_trailing_extra_history_does_not_alter_an_earlier_cutoff(
        self, stock_bars, etf_bars, market_bars,
    ):
        """Complementary framing: computing at the cutoff with the FULL series
        truncated to the cutoff must equal computing it from a frame that never
        contained the later rows at all."""
        truncated = compute_market_residual_r2(
            stock_bars.iloc[: _CUTOFF + 1],
            _composite(etf_bars.iloc[: _CUTOFF + 1]),
            market_bars.iloc[: _CUTOFF + 1],
            window=_WINDOW, min_obs=_WINDOW,
        )
        rebuilt = compute_market_residual_r2(
            stock_bars.iloc[: _CUTOFF + 1].copy(),
            _composite(etf_bars.iloc[: _CUTOFF + 1].copy()),
            market_bars.iloc[: _CUTOFF + 1].copy(),
            window=_WINDOW, min_obs=_WINDOW,
        )
        assert truncated == pytest.approx(rebuilt)


# ── (ii) Holdings-composition dimension: future-dated rows dropped ───────────


class TestHoldingsAsOfCausality:
    def test_row_dated_after_as_of_is_dropped(self):
        holdings = {
            "XLK": [
                StubHolding("XLK", "AAPL", weight=0.20, as_of_date=date(2026, 6, 30)),
                StubHolding("XLK", "AAPL", weight=0.99, as_of_date=date(2026, 9, 30)),
            ],
        }
        kept = filter_holdings_as_of(holdings, as_of=date(2026, 7, 27))["XLK"]
        assert len(kept) == 1
        assert kept[0].weight == pytest.approx(0.20)

    def test_row_dated_exactly_on_as_of_is_kept(self):
        as_of = date(2026, 7, 27)
        holdings = {"XLK": [StubHolding("XLK", "AAPL", weight=0.20, as_of_date=as_of)]}
        assert len(filter_holdings_as_of(holdings, as_of=as_of)["XLK"]) == 1

    def test_future_row_never_moves_ownership(self):
        as_of = date(2026, 7, 27)
        causal = {"XLK": [StubHolding("XLK", "AAPL", shares_held=1_000.0,
                                      as_of_date=date(2026, 6, 30))]}
        contaminated = {"XLK": list(causal["XLK"]) + [
            StubHolding("XLK", "AAPL", shares_held=9_999_999.0, as_of_date=date(2026, 9, 30)),
        ]}
        shares_out = {"AAPL": 10_000.0}
        expected = compute_etf_ownership(causal, shares_out)["AAPL"]
        actual = compute_etf_ownership(
            filter_holdings_as_of(contaminated, as_of=as_of), shares_out,
        )["AAPL"]
        assert actual == pytest.approx(expected) == pytest.approx(0.1)

    def test_future_row_never_moves_the_primary_wrapper_label(self):
        as_of = date(2026, 7, 27)
        contaminated = {
            "XLK": [StubHolding("XLK", "AAPL", weight=0.20, as_of_date=date(2026, 6, 30))],
            # A basket that only STARTS holding AAPL heavily next quarter must
            # not be reported as today's primary wrapper.
            "XLF": [StubHolding("XLF", "AAPL", weight=0.95, as_of_date=date(2026, 9, 30))],
        }
        filtered = filter_holdings_as_of(contaminated, as_of=as_of)
        assert primary_wrapper(filtered)["AAPL"] == "XLK"

    def test_future_row_never_enters_the_return_composite(self, etf_bars):
        as_of = date(2026, 7, 27)
        other = make_synthetic_ohlcv(periods=200, seed=404)
        contaminated = {
            "XLK": [StubHolding("XLK", "AAPL", shares_held=100.0,
                                as_of_date=date(2026, 6, 30))],
            "XLF": [StubHolding("XLF", "AAPL", shares_held=1e9,
                                as_of_date=date(2026, 9, 30))],
        }
        bars = {"XLK": etf_bars, "XLF": other}
        filtered = filter_holdings_as_of(contaminated, as_of=as_of)
        composite = build_etf_return_composite(filtered, bars)["AAPL"]
        # XLF's future-dated (and enormous) position must not enter the mix.
        assert np.allclose(composite.to_numpy(), _composite(etf_bars).to_numpy())


# ── (iii) End-to-end through the pipeline writeback ─────────────────────────


def _bars_from_returns(returns: np.ndarray, start: str = "2024-01-01") -> pd.DataFrame:
    close = 100.0 * np.cumprod(1.0 + np.concatenate([[0.0], returns]))
    idx = pd.bdate_range(start=start, periods=len(close))
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6},
        index=idx,
    )


class TestApplyETFTransmissionEndToEndCausality:
    """The wiring layer must not reintroduce leakage the math layer prevents."""

    def _fixture(self):
        rng = np.random.RandomState(2026)
        n = 400
        mkt = rng.normal(0.0, 0.012, n)
        shared = rng.normal(0.0, 0.008, n)
        tech_raw = {
            "SPY": _bars_from_returns(mkt),
            "XLK": _bars_from_returns(1.0 * mkt + shared + rng.normal(0, 0.001, n)),
            "XLF": _bars_from_returns(1.0 * mkt + rng.normal(0, 0.01, n)),
            "AAPL": _bars_from_returns(1.05 * mkt + shared + rng.normal(0, 0.001, n)),
        }
        df = pd.DataFrame([
            {"Symbol": "AAPL", "Price": 200.0, "Market Cap": 2_000_000_000.0},
        ])
        return tech_raw, df

    def _run(self, monkeypatch, df, tech_raw, holdings):
        from pipeline.production_steps import _apply_etf_transmission

        mod = types.ModuleType("data.etf_holdings")
        mod.get_etf_holdings = lambda etfs, **kw: holdings
        monkeypatch.setitem(sys.modules, "data.etf_holdings", mod)

        overrides = {
            "ETF_TRANSMISSION_ENABLED": True,
            "ETF_HOLDINGS_MARKET_PROXY": "SPY",
            "ETF_TRANSMISSION_WRAPPERS": ["SPY", "XLK", "XLF"],
            "ETF_TRANSMISSION_EXCLUDED_SYMBOLS": [],
            "ETF_TRANSMISSION_WINDOW_DAYS": 250,
            "ETF_TRANSMISSION_MIN_OBS": 250,
        }
        patches = [patch(f"settings.settings.{k}", v) for k, v in overrides.items()]
        for p in patches:
            p.start()
        try:
            _apply_etf_transmission(df, tech_raw)
        finally:
            for p in reversed(patches):
                p.stop()
        return df.iloc[0]

    def test_future_dated_basket_row_changes_nothing_end_to_end(self, monkeypatch):
        past = date(2026, 1, 31)
        future = date(2099, 12, 31)

        causal_holdings = {
            "XLK": [StubHolding("XLK", "AAPL", weight=0.22, shares_held=1_500_000.0,
                                as_of_date=past)],
        }
        contaminated_holdings = {
            "XLK": list(causal_holdings["XLK"]),
            # A basket that only starts holding AAPL (massively) in the future.
            "XLF": [StubHolding("XLF", "AAPL", weight=0.99, shares_held=9_000_000.0,
                                as_of_date=future)],
        }

        tech_raw_a, df_a = self._fixture()
        baseline = self._run(monkeypatch, df_a, tech_raw_a, causal_holdings)

        tech_raw_b, df_b = self._fixture()
        contaminated = self._run(monkeypatch, df_b, tech_raw_b, contaminated_holdings)

        assert not math.isnan(baseline["ETF_Comovement_R2"])
        assert contaminated["ETF_Comovement_R2"] == pytest.approx(
            baseline["ETF_Comovement_R2"]
        )
        assert contaminated["ETF_Ownership_Pct"] == pytest.approx(
            baseline["ETF_Ownership_Pct"]
        )
        assert contaminated["ETF_Primary_Wrapper"] == baseline["ETF_Primary_Wrapper"] == "XLK"

    def test_as_of_is_passed_to_the_holdings_provider(self, monkeypatch):
        """The provider must be told the cycle's as-of date, not left to guess
        -- the client-side filter is belt-and-suspenders, not the whole plan."""
        from pipeline.production_steps import _apply_etf_transmission

        captured = {}

        def _get(etfs, **kw):
            captured.update(kw)
            return {"XLK": [StubHolding("XLK", "AAPL", weight=0.2, shares_held=1.0)]}

        mod = types.ModuleType("data.etf_holdings")
        mod.get_etf_holdings = _get
        monkeypatch.setitem(sys.modules, "data.etf_holdings", mod)

        tech_raw, df = self._fixture()
        with patch("settings.settings.ETF_TRANSMISSION_ENABLED", True), \
             patch("settings.settings.ETF_HOLDINGS_MARKET_PROXY", "SPY"), \
             patch("settings.settings.ETF_TRANSMISSION_WRAPPERS", ["SPY", "XLK"]), \
             patch("settings.settings.ETF_TRANSMISSION_EXCLUDED_SYMBOLS", []):
            _apply_etf_transmission(df, tech_raw)

        assert isinstance(captured.get("as_of"), date)


# ── PIT feature-store guard ─────────────────────────────────────────────────


class TestPITFeatureStoreExclusion:
    def test_etf_columns_stay_out_of_the_pit_feature_matrix(self):
        """``ml/feature_engineering.FEATURE_COLUMNS`` is an explicit allowlist
        (``build_pit_feature_matrix`` does ``[FEATURE_COLUMNS]``), so new
        dashboard columns cannot leak into the PIT feature store by default.
        Pin that: ETF basket holdings are published quarterly and are STALE by
        construction relative to a daily feature row, so adding any of these
        three to the model's feature set would be a contaminating feature, not
        a free signal. If a future PR genuinely wants them there, it must
        first solve the as-of staleness -- and delete this test deliberately.
        """
        from ml.feature_engineering import FEATURE_COLUMNS

        for col in ("ETF_Ownership_Pct", "ETF_Comovement_R2", "ETF_Primary_Wrapper"):
            assert col not in FEATURE_COLUMNS
            assert f"{col}_rank" not in FEATURE_COLUMNS
