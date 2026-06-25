"""
tests/test_run_once.py
======================
Offline unit tests for the refactored main.py orchestrator.

All network I/O is monkeypatched:
  - fetch_account_snapshot  → mock snapshot
  - advisory_evaluate       → deterministic mock Recommendation
  - get_provider            → mock MarketDataProvider
  - _build_macro_dto        → returns neutral MacroEconomicDTO (no FRED call)
  - _fetch_bars_for_universe→ returns empty dict (skips network)
  - _build_context_extras   → returns {} (tested separately)
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

# --- import module under test ---
import main as m
from main import (
    RunResult,
    _build_universe,
    _load_tickers_from_sheet2,
    _load_watchlist,
    run_once,
)
from engine.advisory import Recommendation


# ---------------------------------------------------------------------------
# Test fixtures / factories
# ---------------------------------------------------------------------------

def _make_snapshot(
    positions: Optional[Dict[str, Any]] = None,
    buying_power: float = 50_000.0,
    total_equity: float = 100_000.0,
) -> MagicMock:
    """Return a MagicMock that behaves like AccountSnapshot."""
    snap = MagicMock()
    snap.positions = positions or {}
    snap.buying_power = buying_power
    snap.total_equity = total_equity
    snap.total_dividends = 0.0
    snap.fetched_at = datetime.now(timezone.utc)
    snap.age_hours.return_value = 0.1
    snap.is_stale.return_value = False
    return snap


def _make_position(symbol: str, qty: float = 10.0, avg_cost: float = 100.0) -> MagicMock:
    pos = MagicMock()
    pos.symbol = symbol
    pos.quantity = qty
    pos.average_cost = avg_cost
    pos.dividends_received = 5.0
    pos.market_value = qty * avg_cost
    pos.unrealized_pl = 0.0
    pos.name = symbol
    return pos


def _make_recommendation(symbol: str, action: str = "HOLD") -> Recommendation:
    return Recommendation(
        symbol=symbol,
        action=action,
        strategy="test_strategy",
        conviction=0.60,
        rationale=f"{symbol}: test rationale.",
        suggested_position_pct=0.02,
        forecast=105.0,
        key_indicators={
            "score": 55.0,
            "rsi": 52.0,
            "rsi_2": 30.0,
            "macd_line": 0.5,
            "atr": 1.2,
            "aroon_osc": 20.0,
            "sortino": 1.1,
            "max_drawdown": -0.08,
            "rs_vs_spy": 0.03,
            "garch_vol": 0.18,
            "forecast_30d_pct": 0.05,
            "unrealized_pl_pct": 5.0,
            "dividend_yield": 0.02,
            "kelly_raw": 0.04,
        },
        data_quality="OK",
    )


# ---------------------------------------------------------------------------
# _load_watchlist tests
# ---------------------------------------------------------------------------

class TestLoadWatchlist:
    """Tests for _load_watchlist()."""

    def test_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WATCHLIST", "AAPL, MSFT, GOOG")
        result = _load_watchlist()
        assert result == ["AAPL", "MSFT", "GOOG"]

    def test_env_var_takes_precedence_over_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setenv("WATCHLIST", "TSLA")
        (tmp_path / "watchlist.txt").write_text("NVDA\nAMD\n")
        monkeypatch.chdir(tmp_path)
        result = _load_watchlist()
        assert result == ["TSLA"]

    def test_from_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.delenv("WATCHLIST", raising=False)
        wl = tmp_path / "watchlist.txt"
        wl.write_text("NVDA\n# comment line\nAMD\n\n  INTC  \n")
        monkeypatch.chdir(tmp_path)
        result = _load_watchlist()
        assert result == ["NVDA", "AMD", "INTC"]

    def test_empty_when_neither_configured(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.delenv("WATCHLIST", raising=False)
        monkeypatch.chdir(tmp_path)  # no watchlist.txt here
        assert _load_watchlist() == []

    def test_env_empty_string_treated_as_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setenv("WATCHLIST", "   ")
        monkeypatch.chdir(tmp_path)
        assert _load_watchlist() == []


# ---------------------------------------------------------------------------
# _build_universe tests
# ---------------------------------------------------------------------------

class TestBuildUniverse:
    """Tests for _build_universe()."""

    def test_held_only_no_watchlist(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.delenv("WATCHLIST", raising=False)
        monkeypatch.chdir(tmp_path)
        snap = _make_snapshot(positions={"AAPL": _make_position("AAPL"), "TSLA": _make_position("TSLA")})
        result = _build_universe(snap)
        assert set(result) == {"AAPL", "TSLA"}
        assert result == sorted(result)  # must be sorted

    def test_union_with_watchlist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WATCHLIST", "NVDA,MSFT")
        snap = _make_snapshot(positions={"AAPL": _make_position("AAPL")})
        result = _build_universe(snap)
        assert set(result) == {"AAPL", "NVDA", "MSFT"}

    def test_deduplication(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WATCHLIST", "AAPL,MSFT")
        snap = _make_snapshot(positions={"AAPL": _make_position("AAPL")})
        result = _build_universe(snap)
        assert result.count("AAPL") == 1

    def test_empty_account_empty_watchlist(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.delenv("WATCHLIST", raising=False)
        monkeypatch.chdir(tmp_path)
        snap = _make_snapshot(positions={})
        with patch("main._load_tickers_from_sheet2", return_value=[]):
            assert _build_universe(snap) == []

    def test_sheet2_fallback_used_when_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """Sheet2 is consulted only when held + watchlist are both empty."""
        monkeypatch.delenv("WATCHLIST", raising=False)
        monkeypatch.chdir(tmp_path)
        snap = _make_snapshot(positions={})
        with patch("main._load_tickers_from_sheet2", return_value=["SPY", "QQQ"]):
            result = _build_universe(snap)
        assert set(result) == {"SPY", "QQQ"}
        assert result == sorted(result)

    def test_sheet2_not_called_when_watchlist_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sheet2 must NOT be consulted when the watchlist already has tickers."""
        monkeypatch.setenv("WATCHLIST", "AAPL")
        snap = _make_snapshot(positions={})
        with patch("main._load_tickers_from_sheet2") as mock_sheet2:
            result = _build_universe(snap)
        mock_sheet2.assert_not_called()
        assert result == ["AAPL"]

    def test_sheet2_not_called_when_held_present(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """Sheet2 must NOT be consulted when Robinhood positions are held."""
        monkeypatch.delenv("WATCHLIST", raising=False)
        monkeypatch.chdir(tmp_path)
        snap = _make_snapshot(positions={"TSLA": _make_position("TSLA")})
        with patch("main._load_tickers_from_sheet2") as mock_sheet2:
            result = _build_universe(snap)
        mock_sheet2.assert_not_called()
        assert "TSLA" in result


# ---------------------------------------------------------------------------
# _load_tickers_from_sheet2 tests
# ---------------------------------------------------------------------------

class TestLoadTickersFromSheet2:
    """Tests for _load_tickers_from_sheet2()."""

    def test_returns_empty_when_no_credentials(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)  # no credentials.json here
        assert _load_tickers_from_sheet2() == []

    def test_returns_tickers_from_sheet2_col_a(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "credentials.json").write_text("{}")  # presence check only
        mock_ws = MagicMock()
        mock_ws.col_values.return_value = ["SPY", "QQQ", "", "# ignore", "AAPL"]
        mock_sh = MagicMock()
        mock_sh.worksheet.return_value = mock_ws
        mock_gc = MagicMock()
        mock_gc.open.return_value = mock_sh
        with patch("gspread.service_account", return_value=mock_gc):
            result = _load_tickers_from_sheet2()
        assert result == ["SPY", "QQQ", "AAPL"]  # empty + comment stripped

    def test_returns_empty_on_sheet_error(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "credentials.json").write_text("{}")
        with patch("gspread.service_account", side_effect=Exception("network error")):
            assert _load_tickers_from_sheet2() == []


# ---------------------------------------------------------------------------
# run_once tests — all network patched
# ---------------------------------------------------------------------------

_PATCH_SNAPSHOT = "main.fetch_account_snapshot"
_PATCH_EVALUATE = "main.advisory_evaluate"
_PATCH_PROVIDER = "main.get_provider"
_PATCH_MACRO = "main._build_macro_dto"
_PATCH_BARS = "main._fetch_bars_for_universe"
_PATCH_CTX = "main._build_context_extras"


class TestRunOnce:
    """Tests for run_once()."""

    @patch(_PATCH_CTX, return_value={})
    @patch(_PATCH_BARS, return_value={})
    @patch(_PATCH_MACRO)
    @patch(_PATCH_EVALUATE)
    @patch(_PATCH_PROVIDER)
    @patch(_PATCH_SNAPSHOT)
    def test_success_returns_run_result(
        self,
        mock_snap: MagicMock,
        mock_provider: MagicMock,
        mock_eval: MagicMock,
        mock_macro: MagicMock,
        _bars: MagicMock,
        _ctx: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("WATCHLIST", "AAPL,MSFT")
        snap = _make_snapshot()
        mock_snap.return_value = snap
        mock_macro.return_value = MagicMock(market_regime="NEUTRAL", vix_value=18.0)
        mock_eval.side_effect = lambda symbol, **kw: _make_recommendation(symbol, "HOLD")

        result = run_once()

        assert isinstance(result, RunResult)
        assert len(result.recommendations) == 2
        assert len(result.errors) == 0
        assert result.duration_seconds >= 0.0
        assert result.started_at <= result.finished_at

    @patch(_PATCH_CTX, return_value={})
    @patch(_PATCH_BARS, return_value={})
    @patch(_PATCH_MACRO)
    @patch(_PATCH_EVALUATE)
    @patch(_PATCH_PROVIDER)
    @patch(_PATCH_SNAPSHOT)
    def test_dead_letter_per_symbol(
        self,
        mock_snap: MagicMock,
        mock_provider: MagicMock,
        mock_eval: MagicMock,
        mock_macro: MagicMock,
        _bars: MagicMock,
        _ctx: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One symbol raising should not abort the run; error goes to RunResult.errors."""
        monkeypatch.setenv("WATCHLIST", "AAPL,FAIL_SYM")
        snap = _make_snapshot()
        mock_snap.return_value = snap
        mock_macro.return_value = MagicMock(market_regime="NEUTRAL", vix_value=18.0)

        def _eval_side(symbol: str, **kw: Any) -> Recommendation:
            if symbol == "FAIL_SYM":
                raise RuntimeError("Simulated network error")
            return _make_recommendation(symbol, "BUY")

        mock_eval.side_effect = _eval_side

        result = run_once()

        assert len(result.recommendations) == 1
        assert result.recommendations[0].symbol == "AAPL"
        assert len(result.errors) == 1
        err = result.errors[0]
        assert err["symbol"] == "FAIL_SYM"
        assert err["stage"] == "advisory_evaluate"
        assert err["error_type"] == "RuntimeError"
        assert "Simulated network error" in err["message"]
        assert "timestamp" in err

    @patch(_PATCH_CTX, return_value={})
    @patch(_PATCH_BARS, return_value={})
    @patch(_PATCH_MACRO)
    @patch(_PATCH_EVALUATE)
    @patch(_PATCH_PROVIDER)
    @patch(_PATCH_SNAPSHOT)
    def test_all_symbols_fail_still_returns_run_result(
        self,
        mock_snap: MagicMock,
        mock_provider: MagicMock,
        mock_eval: MagicMock,
        mock_macro: MagicMock,
        _bars: MagicMock,
        _ctx: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("WATCHLIST", "BAD1,BAD2")
        mock_snap.return_value = _make_snapshot()
        mock_macro.return_value = MagicMock(market_regime="NEUTRAL", vix_value=18.0)
        mock_eval.side_effect = Exception("always fails")

        result = run_once()

        assert isinstance(result, RunResult)
        assert len(result.recommendations) == 0
        assert len(result.errors) == 2

    @patch(_PATCH_CTX, return_value={})
    @patch(_PATCH_BARS, return_value={})
    @patch(_PATCH_MACRO)
    @patch(_PATCH_EVALUATE)
    @patch(_PATCH_PROVIDER)
    @patch(_PATCH_SNAPSHOT)
    def test_robinhood_failure_uses_empty_snapshot(
        self,
        mock_snap: MagicMock,
        mock_provider: MagicMock,
        mock_eval: MagicMock,
        mock_macro: MagicMock,
        _bars: MagicMock,
        _ctx: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When Robinhood is unreachable, the run proceeds on empty account + watchlist."""
        monkeypatch.setenv("WATCHLIST", "SPY")
        mock_snap.side_effect = RuntimeError("Robinhood login failed")
        mock_macro.return_value = MagicMock(market_regime="NEUTRAL", vix_value=18.0)
        mock_eval.return_value = _make_recommendation("SPY", "HOLD")

        result = run_once()

        assert isinstance(result, RunResult)
        # SPY from watchlist still evaluated even though account was empty
        assert len(result.recommendations) == 1
        assert result.recommendations[0].symbol == "SPY"
        # Empty account snapshot was used
        assert result.snapshot.total_equity == 0.0
        assert result.snapshot.positions == {}

    @patch(_PATCH_CTX, return_value={})
    @patch(_PATCH_BARS, return_value={})
    @patch(_PATCH_MACRO)
    @patch(_PATCH_EVALUATE)
    @patch(_PATCH_PROVIDER)
    @patch(_PATCH_SNAPSHOT)
    def test_empty_universe_returns_early(
        self,
        mock_snap: MagicMock,
        mock_provider: MagicMock,
        mock_eval: MagicMock,
        mock_macro: MagicMock,
        _bars: MagicMock,
        _ctx: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """No held symbols and no watchlist → empty RunResult; advisory never called."""
        monkeypatch.delenv("WATCHLIST", raising=False)
        monkeypatch.chdir(tmp_path)
        mock_snap.return_value = _make_snapshot(positions={})
        mock_macro.return_value = MagicMock(market_regime="NEUTRAL", vix_value=18.0)

        result = run_once()

        assert isinstance(result, RunResult)
        assert len(result.recommendations) == 0
        assert len(result.errors) == 0
        mock_eval.assert_not_called()

    @patch(_PATCH_CTX, return_value={})
    @patch(_PATCH_BARS, return_value={})
    @patch(_PATCH_MACRO)
    @patch(_PATCH_EVALUATE)
    @patch(_PATCH_PROVIDER)
    @patch(_PATCH_SNAPSHOT)
    def test_force_account_passed_through(
        self,
        mock_snap: MagicMock,
        mock_provider: MagicMock,
        mock_eval: MagicMock,
        mock_macro: MagicMock,
        _bars: MagicMock,
        _ctx: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_once(force_account=True) passes force=True to fetch_account_snapshot."""
        monkeypatch.setenv("WATCHLIST", "AAPL")
        mock_snap.return_value = _make_snapshot()
        mock_macro.return_value = MagicMock(market_regime="NEUTRAL", vix_value=18.0)
        mock_eval.return_value = _make_recommendation("AAPL", "BUY")

        run_once(force_account=True)

        mock_snap.assert_called_once_with(max_age_hours=20.0, force=True)

    @patch(_PATCH_CTX, return_value={})
    @patch(_PATCH_BARS, return_value={})
    @patch(_PATCH_MACRO)
    @patch(_PATCH_EVALUATE)
    @patch(_PATCH_PROVIDER)
    @patch(_PATCH_SNAPSHOT)
    def test_held_symbols_always_included(
        self,
        mock_snap: MagicMock,
        mock_provider: MagicMock,
        mock_eval: MagicMock,
        mock_macro: MagicMock,
        _bars: MagicMock,
        _ctx: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """Held tickers appear in universe even when no watchlist is configured."""
        monkeypatch.delenv("WATCHLIST", raising=False)
        monkeypatch.chdir(tmp_path)
        snap = _make_snapshot(
            positions={
                "AAPL": _make_position("AAPL"),
                "MSFT": _make_position("MSFT"),
            }
        )
        mock_snap.return_value = snap
        mock_macro.return_value = MagicMock(market_regime="NEUTRAL", vix_value=18.0)
        mock_eval.side_effect = lambda symbol, **kw: _make_recommendation(symbol)

        result = run_once()

        evaluated = {r.symbol for r in result.recommendations}
        assert "AAPL" in evaluated
        assert "MSFT" in evaluated


# ---------------------------------------------------------------------------
# RunResult immutability
# ---------------------------------------------------------------------------

class TestRunResultImmutability:
    """RunResult must be a frozen dataclass."""

    def test_frozen(self) -> None:
        snap = _make_snapshot()
        result = RunResult(
            snapshot=snap,
            recommendations=[],
            errors=[],
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            duration_seconds=0.5,
        )
        with pytest.raises((AttributeError, TypeError)):
            result.recommendations = []  # type: ignore[misc]

    def test_duration_non_negative(self) -> None:
        snap = _make_snapshot()
        result = RunResult(
            snapshot=snap,
            recommendations=[_make_recommendation("AAPL")],
            errors=[],
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            duration_seconds=1.23,
        )
        assert result.duration_seconds >= 0.0

    def test_error_dict_structure(self) -> None:
        """Error entries must carry the required keys."""
        snap = _make_snapshot()
        err = {
            "symbol": "BAD",
            "stage": "advisory_evaluate",
            "error_type": "ValueError",
            "message": "test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        result = RunResult(
            snapshot=snap,
            recommendations=[],
            errors=[err],
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            duration_seconds=0.0,
        )
        required_keys = {"symbol", "stage", "error_type", "message", "timestamp"}
        assert required_keys.issubset(result.errors[0].keys())
