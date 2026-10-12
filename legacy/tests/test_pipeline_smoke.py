"""
legacy/tests/test_pipeline_smoke.py
===================================
``TestRunOncePipeline`` (smoke tests of ``main.run_once()``), split out of
``tests/test_pipeline_smoke.py`` when ``main.py`` was archived in shrink step
5.5. The active file smoke-tests the daemon's advisory loop
(``AdvisoryOverlayStep``) instead.

Keeps its original imports: restore ``main.py`` to run it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

if TYPE_CHECKING:
    from engine.advisory import Recommendation


# ---------------------------------------------------------------------------
# Shared helpers / factories
# ---------------------------------------------------------------------------

def _make_bars(n: int = 252, start_price: float = 100.0) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame for monkeypatching the market provider."""
    idx = pd.date_range(end=datetime.today(), periods=n, freq="B")
    closes = np.linspace(start_price * 0.90, start_price, n)
    return pd.DataFrame(
        {
            "Open":   closes * 0.999,
            "High":   closes * 1.005,
            "Low":    closes * 0.995,
            "Close":  closes,
            "Volume": np.full(n, 100_000, dtype=float),
        },
        index=idx,
    )


def _make_quote(price: float = 100.0, is_stale: bool = False):
    """Return a real Quote stub (uses the actual Quote frozen dataclass)."""
    from data.market_data import Quote
    return Quote(
        symbol="TEST",
        price=price,
        bid=price - 0.01,
        ask=price + 0.01,
        timestamp=datetime.now(timezone.utc),
        is_stale=is_stale,
        source="test",
    )


def _make_market_provider(
    price: float = 100.0,
    bars: Optional[pd.DataFrame] = None,
    fundamentals: Optional[Dict[str, Any]] = None,
) -> MagicMock:
    """MagicMock implementing MarketDataProvider with deterministic data."""
    provider = MagicMock()
    provider.get_latest_quote.return_value = _make_quote(price)
    provider.get_intraday_bars.return_value = (
        bars if bars is not None else _make_bars(252, price)
    )
    provider.get_fundamentals.return_value = fundamentals or {}
    return provider


def _make_snapshot(positions: Optional[Dict] = None) -> MagicMock:
    """MagicMock with attributes expected by run_once() → _log_summary()."""
    snap = MagicMock()
    snap.positions = positions or {}
    snap.buying_power = 10_000.0
    snap.total_equity = 100_000.0
    snap.total_dividends = 0.0
    snap.fetched_at = datetime.now(timezone.utc)
    snap.age_hours.return_value = 0.5
    snap.is_stale.return_value = False
    return snap


def _make_position(
    symbol: str,
    quantity: float = 10.0,
    average_cost: float = 100.0,
    dividends_received: float = 0.0,
    current_price: float = 100.0,
) -> MagicMock:
    """MagicMock PortfolioPosition with explicit numeric attributes."""
    pos = MagicMock()
    pos.symbol = symbol
    pos.quantity = quantity
    pos.average_cost = average_cost
    pos.dividends_received = dividends_received
    pos.current_price = current_price
    pos.market_value = quantity * current_price
    pos.unrealized_pl = quantity * (current_price - average_cost)
    pos.name = symbol
    return pos


# Deterministic indicator dict used as the ProcessingEngine return value.
_MOCK_TECH: Dict[str, Any] = {
    "RSI": 55.0,
    "RSI_2": 40.0,
    "MACD_Line": 0.5,
    "MACD_Signal": 0.3,
    "ATR": 2.5,
    "SMA_50": 98.0,
    "SMA_200": 95.0,
    "Aroon Oscillator": 60.0,
    "Chandelier Exit": 92.0,
    "Sortino Ratio": 0.8,
    "Max Drawdown": -0.12,
    "RS vs SPY": 0.03,
    "RS-MACD": 0.2,
    "ROC_12M": 0.08,
    "ROC_6M": 0.04,
    "Momentum_Vol_Scaled": 0.01,
    "Realized_Vol_60D": 0.18,
    "VaR 95": -0.02,
    "Coppock Curve": 0.0,
    "Aroon Up": 80.0,
    "Aroon Down": 20.0,
    "Realized Slippage": 0.0,
    "Options IV Edge": 0.0,
    "CoVaR Proxy": 0.0,
}


# ---------------------------------------------------------------------------
# TestRunOncePipeline
# ---------------------------------------------------------------------------

class TestRunOncePipeline:
    """
    Orchestrator smoke tests for main.run_once().

    Patches all network calls at the main-module boundary using monkeypatch so
    each test runs fully offline without any live API contact.
    """

    # Canonical patch targets (mirrors test_run_once.py convention)
    _P_SNAPSHOT = "main.fetch_account_snapshot"
    _P_EVALUATE = "main.advisory_evaluate"
    _P_PROVIDER = "main.get_provider"
    _P_MACRO    = "main._build_macro_dto"
    _P_BARS     = "main._fetch_bars_for_universe"
    _P_CTX      = "main._build_context_extras"

    @pytest.fixture(autouse=True)
    def _isolate_watchlist_file(self, monkeypatch, tmp_path):
        """Point WATCHLIST_FILE at a nonexistent tmp path so these
        tests' universe-building assertions are deterministic regardless of
        whether a real watchlist.txt happens to exist in the repo root (e.g.
        one written by the Live Inventory "Sync Now" feature) -- these tests
        intend to exercise only the mocked positions / WATCHLIST env var,
        never whatever real watchlist.txt file a given checkout has on disk.
        Patched on pipeline.advisory_inputs (where load_watchlist() reads it
        since step 5.0), not on main's re-export, which would not reach it.
        """
        import pipeline.advisory_inputs as ai
        monkeypatch.setattr(ai, "WATCHLIST_FILE", str(tmp_path / "nonexistent_watchlist.txt"))

    @pytest.fixture(autouse=True)
    def _isolate_scan_discovery(self, monkeypatch):
        """Neutralize discovery() for the same reason as
        _isolate_watchlist_file above: _build_universe() unconditionally
        unions discovery()'s scan candidates into the universe, and
        pilots.discovery.discovery() reads settings.OUTPUT_DIR -- a
        machine-global settings.LOCAL_DATA_ROOT path, not repo/test-scoped,
        so a real ~/.stockpy_local/output/scan_candidates.json (e.g. from a
        real agentic-discovery skill run) would otherwise leak extra
        symbols into these tests' universe-size assertions."""
        import pipeline.advisory_inputs as ai
        monkeypatch.setattr(ai, "discovery", lambda *a, **kw: {"candidates": []})

    def _neutral_macro(self) -> MagicMock:
        m = MagicMock()
        m.market_regime = "RISK ON"
        m.hmm_risk_on_probability = None
        m.vix_value = 15.0
        return m

    def _good_rec(self, symbol: str) -> "Recommendation":
        from engine.advisory import Recommendation
        return Recommendation(
            symbol=symbol,
            action="HOLD",
            strategy="smoke_test",
            conviction=0.60,
            rationale=f"Smoke-test HOLD for {symbol}.",
            suggested_position_pct=0.0,
            forecast=105.0,
            key_indicators={"score": 55.0, "rsi": 52.0},
            data_quality="OK",
        )

    @patch("main._build_context_extras", return_value={})
    @patch("main._fetch_bars_for_universe", return_value={})
    def test_returns_runresult_shape(
        self,
        _bars: MagicMock,
        _ctx: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_once() returns a RunResult with the expected fields."""
        import main as m

        snap = _make_snapshot(positions={"AAPL": _make_position("AAPL")})
        monkeypatch.setenv("WATCHLIST", "")

        with patch(self._P_SNAPSHOT, return_value=snap), \
             patch(self._P_PROVIDER, return_value=_make_market_provider()), \
             patch(self._P_MACRO, return_value=self._neutral_macro()), \
             patch(self._P_EVALUATE, side_effect=lambda symbol, **kw: self._good_rec(symbol)):

            result = m.run_once()

        assert hasattr(result, "recommendations")
        assert hasattr(result, "errors")
        assert hasattr(result, "snapshot")
        assert hasattr(result, "duration_seconds")
        assert result.duration_seconds >= 0.0
        assert len(result.recommendations) == 1
        assert len(result.errors) == 0
        assert result.recommendations[0].symbol == "AAPL"

    @patch("main._build_context_extras", return_value={})
    @patch("main._fetch_bars_for_universe", return_value={})
    def test_dead_letter_on_symbol_failure(
        self,
        _bars: MagicMock,
        _ctx: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        When advisory_evaluate raises for one symbol, that symbol is dead-
        lettered: RunResult.errors contains it; the other symbol's
        recommendation is unaffected.  No exception propagates out.
        """
        import main as m

        snap = _make_snapshot(positions={
            "AAPL": _make_position("AAPL"),
            "FAIL": _make_position("FAIL"),
        })
        monkeypatch.setenv("WATCHLIST", "")

        def _eval(symbol: str, **kw: Any):
            if symbol == "FAIL":
                raise RuntimeError("Simulated network error")
            return self._good_rec(symbol)

        with patch(self._P_SNAPSHOT, return_value=snap), \
             patch(self._P_PROVIDER, return_value=_make_market_provider()), \
             patch(self._P_MACRO, return_value=self._neutral_macro()), \
             patch(self._P_EVALUATE, side_effect=_eval):

            result = m.run_once()

        assert len(result.recommendations) == 1, (
            "Only the healthy symbol should appear in recommendations."
        )
        assert len(result.errors) == 1, (
            "The failed symbol should produce exactly one error entry."
        )
        err = result.errors[0]
        assert err["symbol"] == "FAIL"
        assert "error_type" in err
        assert "stage" in err
        assert "message" in err
        assert "timestamp" in err

    @patch("main._build_context_extras", return_value={})
    @patch("main._fetch_bars_for_universe", return_value={})
    def test_all_failures_still_returns_runresult(
        self,
        _bars: MagicMock,
        _ctx: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """All symbols failing must never raise — run_once() always returns."""
        import main as m

        snap = _make_snapshot(positions={"BOOM": _make_position("BOOM")})
        monkeypatch.setenv("WATCHLIST", "")

        def _always_fail(symbol: str, **kw: Any):
            raise ValueError("catastrophic failure")

        with patch(self._P_SNAPSHOT, return_value=snap), \
             patch(self._P_PROVIDER, return_value=_make_market_provider()), \
             patch(self._P_MACRO, return_value=self._neutral_macro()), \
             patch(self._P_EVALUATE, side_effect=_always_fail):

            result = m.run_once()

        assert result is not None
        assert len(result.recommendations) == 0
        assert len(result.errors) == 1
