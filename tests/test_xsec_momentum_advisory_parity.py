"""
tests/test_xsec_momentum_advisory_parity.py
============================================
Parity test closing the last gap in the 3-way hand-duplicated 12-1m
cross-sectional momentum formula (Jegadeesh-Titman 1993).

WHY THIS TEST EXISTS: the SKIP_DAYS=22 / LOOKBACK_DAYS=252 momentum
formula (price[t-skip] / price[t-lookback] - 1, then cross-sectional
percentile rank) is independently hand-duplicated across THREE files with
no shared implementation:

  1. main_orchestrator.py::compute_xsec_momentum_ranks
  2. pipeline/production_steps.py::_compute_xsec_momentum (the live
     orchestrator-path implementation)
  3. main.py::_build_context_extras' inline copy (the advisory path)

tests/test_xsec_momentum.py already parity-tests (1) against (2). Nothing
previously compared (3) against the other two, so a future change to
SKIP_DAYS/LOOKBACK_DAYS in only one of the three spots would silently
diverge the advisory path's XSec_12_1M/rank output from the orchestrator
path's without any test noticing. This file is the CI tripwire for that
remaining gap.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd
import pytest

import main as m
from dto_models import MacroEconomicDTO
from main_orchestrator import compute_xsec_momentum_ranks
from pipeline.production_steps import _compute_xsec_momentum

# Repo-documented numeric-drift tolerance for indicator parity (see
# CLAUDE.md: "numeric drift on existing indicators must stay below 1e-5").
ABS_TOL = 1e-5


def _make_bars_df(n: int = 320, start_price: float = 100.0, seed: int = 0) -> pd.DataFrame:
    """Deterministic synthetic OHLCV history, long enough (>= 275 rows) to
    clear main.py's REQUIRED = LOOKBACK_DAYS + SKIP_DAYS + 1 floor. Mirrors
    tests/test_main_multifactor_precompute.py's fixture of the same name;
    a distinct ``seed`` per ticker gives each one a genuinely different
    price path (and therefore a genuinely different 12-1m return), so the
    rank-ordering comparison below isn't trivially satisfied by ties."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    returns = rng.normal(0.0004, 0.01, size=n)
    close = start_price * np.cumprod(1.0 + returns)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.full(n, 1_000_000),
        },
        index=dates,
    )


class _FakeMarket:
    """Minimal MarketDataProvider stand-in. This test exercises only the
    xsec-momentum step of _build_context_extras, so no real fundamentals
    are needed -- an empty response is a legitimate "no data" degrade
    (CONSTRAINT #4), not a test gap, since _fetch_fundamentals_for_universe
    dead-letters a symbol with an empty fundamentals dict."""

    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        return {}


def _neutral_macro_dto() -> MacroEconomicDTO:
    return MacroEconomicDTO(
        yield_curve_10y_2y=0.5,
        high_yield_oas=3.5,
        inflation_rate=3.0,
        nominal_10y=4.5,
        vix_value=18.0,
        sahm_rule_indicator=0.0,
    )


@pytest.fixture(autouse=True)
def _auto_disable_historical_store(disable_historical_store):
    """Route _build_context_extras' fundamentals pre-fetch straight through
    the fake market provider instead of touching the real on-disk
    HistoricalStore (see tests/conftest.py::disable_historical_store)."""


def _build_bars_dict() -> Dict[str, pd.DataFrame]:
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    return {s: _make_bars_df(n=320, seed=i) for i, s in enumerate(symbols)}


class TestAdvisoryXsecMomentumParity:
    """main.py::_build_context_extras' inline 12-1m formula (the advisory
    path, previously untested against its two siblings) vs. the two
    already-cross-checked production copies (main_orchestrator.py,
    pipeline/production_steps.py)."""

    def test_raw_returns_match_production_steps_helper(self) -> None:
        """extras['xsec_12_1m'] (main.py's raw 12-1m returns) must agree,
        ticker-for-ticker within ABS_TOL, with
        pipeline.production_steps._compute_xsec_momentum's raw-return dict
        -- the actual live orchestrator-path implementation."""
        bars_dict = _build_bars_dict()
        symbols = list(bars_dict.keys())
        market = _FakeMarket()

        extras = m._build_context_extras(symbols, bars_dict, _neutral_macro_dto(), market)
        advisory_returns = extras["xsec_12_1m"]

        production_returns, _ = _compute_xsec_momentum(bars_dict)

        assert set(advisory_returns.keys()) == set(production_returns.keys()) == set(symbols)
        for sym in symbols:
            assert abs(advisory_returns[sym] - production_returns[sym]) < ABS_TOL, (
                f"{sym}: main.py raw 12-1m return {advisory_returns[sym]!r} vs "
                f"pipeline/production_steps.py raw 12-1m return "
                f"{production_returns[sym]!r} -- exceeds the {ABS_TOL} tolerance"
            )

    def test_rank_ordering_matches_orchestrator_helper(self) -> None:
        """The percentile-rank ordering derived from main.py's own raw
        returns must agree with main_orchestrator.compute_xsec_momentum_ranks
        -- the third leg of the 3-way parity check."""
        bars_dict = _build_bars_dict()
        symbols = list(bars_dict.keys())
        market = _FakeMarket()

        extras = m._build_context_extras(symbols, bars_dict, _neutral_macro_dto(), market)
        advisory_ranks = pd.Series(extras["xsec_12_1m"]).rank(pct=True, ascending=True)

        orchestrator_ranks = compute_xsec_momentum_ranks(bars_dict)

        assert set(advisory_ranks.index) == set(orchestrator_ranks.index) == set(symbols)

        advisory_ranks = advisory_ranks.sort_index()
        orchestrator_ranks = orchestrator_ranks.sort_index()
        pd.testing.assert_series_equal(
            advisory_ranks,
            orchestrator_ranks,
            check_exact=False,
            atol=ABS_TOL,
            check_names=False,
        )
