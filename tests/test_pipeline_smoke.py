"""
tests/test_pipeline_smoke.py
============================
End-to-end smoke tests for the InvestYo advisory pipeline.

All network I/O is monkeypatched — no live API calls are made in this file.

TestRunOncePipeline
    Orchestrator-level tests via main.run_once():
      • Returns a valid RunResult with the expected shape and attributes.
      • Dead-letter: one rigged symbol failure → 1 error + N-1 recommendations,
        no unhandled exception propagates out of run_once().
      • All-failure universe still returns a RunResult (never raises).

TestAdvisoryTailoringRules
    Advisory engine holding-aware overlay via engine.advisory.evaluate():
      • Case B: held position + high cumulative dividends + weak signal → HOLD
        (dividend-hold bias rule fires; BUY is suppressed).
      • Case A: held position below cost + bearish 30-day forecast → SELL
        (loss-escalation rule fires regardless of raw signal).
      • Non-held + bullish strategy signal + positive GARCH vol → BUY with
        0 < suggested_position_pct ≤ CONFIG["max_single_position_pct"].

TestNoOrderFunctions
    Static source-code guard (AST walk):
      • No Python module outside execution/ defines a function whose name
        matches the order-submission patterns (submit_order, buy_order,
        sell_order, place_order, place_equity_order, place_option_order, or
        any function starting with "place_").
      • Backtrader notify_order callbacks and auditor mock stubs are excluded.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


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


# ---------------------------------------------------------------------------
# TestAdvisoryTailoringRules
# ---------------------------------------------------------------------------

class TestAdvisoryTailoringRules:
    """
    Verify the three holding-aware overlay cases in engine.advisory.evaluate().

    A real (in-memory) TransactionsStore is injected so Kelly sizing uses the
    vol-target fallback path (< 30 trades) rather than reading the production DB.
    All heavy engines (ProcessingEngine, TechnicalOptionsEngine, ForecastingEngine,
    StrategyEngine) are monkeypatched with deterministic stubs.
    """

    def _run(
        self,
        symbol: str,
        position: Any,
        strategy_signal: str,
        strategy_score: int,
        forecast_30: float,
        current_price: float = 100.0,
        fundamentals: Optional[Dict] = None,
        garch_vol: float = 0.20,
    ):
        """Helper: patch engines, call evaluate(), return Recommendation."""
        from engine.advisory import evaluate
        from transactions_store import TransactionsStore

        ts = TransactionsStore(db_url="sqlite:///:memory:")
        market = _make_market_provider(
            price=current_price,
            bars=_make_bars(252, current_price),
            fundamentals=fundamentals or {},
        )
        snapshot = _make_snapshot()

        pe_mock = MagicMock()
        pe_mock.calculate_technical_metrics.return_value = {symbol: dict(_MOCK_TECH)}

        toe_mock = MagicMock()
        toe_mock.estimate_gjr_garch_volatility.return_value = garch_vol

        fe_mock = MagicMock()
        fe_mock.generate_forecast.return_value = {
            "Forecast_30": forecast_30,
            "MC_Target": forecast_30,
        }

        se_mock = MagicMock()
        se_mock.evaluate_security.return_value = {
            "Action Signal": strategy_signal,
            "Score": strategy_score,
            "Kelly Target": 0.04,
            "buyRange": "Buy Zone: $98 - $100",
            "sellRange": "Sell Zone: $103 - $107 | Stop @ $95.00",
        }

        with patch("engine.advisory.ProcessingEngine", return_value=pe_mock), \
             patch("engine.advisory.ForecastingEngine", return_value=fe_mock), \
             patch("engine.advisory.TechnicalOptionsEngine", return_value=toe_mock), \
             patch("engine.advisory.StrategyEngine", return_value=se_mock):

            return evaluate(
                symbol=symbol,
                position=position,
                market=market,
                snapshot=snapshot,
                transactions_store=ts,
            )

    def test_case_b_held_high_dividends_weak_signal_gives_hold(self) -> None:
        """
        Case B (dividend-hold bias): holding with ≥ $50 cumulative dividends and
        a weak signal (score < buy_score_threshold = 55) → HOLD, even when raw
        StrategyEngine signal is BUY.

        Setup:
          qty=10, avg_cost=100, price=101 → ~1% nominal gain (below 10% Case-C
          threshold on effective cost, so Case C doesn't fire first via the elif chain).
          dividends_received=$75 (≥ $50 hold-bias threshold → _high_yield_holder=True).
          strategy_signal=BUY, score=45 → Case B inner condition fires.
        Expected: action == "HOLD".
        """
        pos = _make_position(
            symbol="TEST",
            quantity=10.0,
            average_cost=100.0,
            dividends_received=75.0,  # ≥ $50 threshold
            current_price=101.0,
        )
        rec = self._run(
            symbol="TEST",
            position=pos,
            strategy_signal="BUY",
            strategy_score=45,    # below CONFIG["buy_score_threshold"] = 55
            forecast_30=103.0,    # mildly bullish → Case A doesn't fire
            current_price=101.0,
        )
        assert rec.action == "HOLD", (
            f"Expected HOLD (Case B dividend-hold bias) but got {rec.action!r}.\n"
            f"rationale: {rec.rationale}"
        )

    def test_case_a_held_below_cost_bearish_forecast_gives_sell(self) -> None:
        """
        Case A (loss + bearish escalation): holding a position with ≥ 10%
        unrealised loss AND a bearish 30-day forecast → SELL, regardless of
        the raw strategy signal.

        Setup:
          qty=10, avg_cost=100, price=85 → -15% unrealised P&L.
          forecast_30=78 → (78−85)/85 ≈ −8.2% < −3% bearish threshold.
          dividends_received=$0 → Case B doesn't fire.
          strategy_signal=HOLD.
        Expected: action == "SELL".
        """
        pos = _make_position(
            symbol="TEST",
            quantity=10.0,
            average_cost=100.0,
            dividends_received=0.0,
            current_price=85.0,
        )
        rec = self._run(
            symbol="TEST",
            position=pos,
            strategy_signal="HOLD",
            strategy_score=45,
            forecast_30=78.0,    # (78−85)/85 ≈ −8.2% — clearly bearish
            current_price=85.0,
        )
        assert rec.action == "SELL", (
            f"Expected SELL (Case A loss + bearish forecast) but got {rec.action!r}.\n"
            f"rationale: {rec.rationale}"
        )

    def test_non_held_bullish_signal_gives_buy_within_cap(self) -> None:
        """
        Non-held symbol: all holding-aware rules are skipped.  A bullish
        strategy signal with a positive GARCH vol → BUY with a position size
        that is strictly positive and bounded by CONFIG["max_single_position_pct"].

        GARCH vol = 0.20 → vol-target fallback = 0.10/0.20 = 0.50,
        clamped to max_single_position_pct = 0.05.
        Expected: action == "BUY" and 0 < suggested_position_pct ≤ 0.05.
        """
        rec = self._run(
            symbol="TEST",
            position=None,       # no holding → all overlay cases skipped
            strategy_signal="BUY",
            strategy_score=75,
            forecast_30=108.0,   # (108−100)/100 = +8% — strongly bullish
            current_price=100.0,
            garch_vol=0.20,
        )
        from engine.advisory import CONFIG

        assert rec.action == "BUY", (
            f"Expected BUY for non-held bullish signal but got {rec.action!r}.\n"
            f"rationale: {rec.rationale}"
        )
        assert rec.suggested_position_pct > 0.0, (
            "BUY recommendation must carry a positive position-size suggestion."
        )
        assert rec.suggested_position_pct <= CONFIG["max_single_position_pct"] + 1e-9, (
            f"suggested_position_pct {rec.suggested_position_pct:.4f} exceeds "
            f"CONFIG['max_single_position_pct'] = {CONFIG['max_single_position_pct']:.4f}."
        )


# ---------------------------------------------------------------------------
# TestNoOrderFunctions
# ---------------------------------------------------------------------------

class TestNoOrderFunctions:
    """
    Static source-code guard: walk the repository for Python modules that are
    NOT part of the designated execution layer (execution/) and assert that none
    define a function whose name matches order-submission patterns.

    This test enforces the advisory-only / read-only contract of the non-execution
    pipeline — accidental inclusion of order-placement code in advisory modules,
    orchestrators, or data loaders must be caught at review time.

    Excluded from the scan:
      execution/                   — legitimate order code lives here
      tests/                       — test code may define mock broker implementations
      .venv/                       — third-party libraries
      Gravity AI Review Suite.py   — auditor file; contains inline mock broker stubs
      ai_verification_prompts.py   — auditor code
    """

    # Function names that signal order-submission capability.
    _ORDER_NAMES = frozenset({
        "submit_order",
        "buy_order",
        "sell_order",
        "place_order",
        "place_equity_order",
        "place_option_order",
    })

    # If any part of the file's path tree contains one of these, skip it.
    _EXCLUDED_PATH_PARTS = frozenset({
        "execution",
        "tests",
        ".venv",
        "__pycache__",
        "gravity",  # gravity/ package contains inline mock broker stubs in audit step methods
    })

    # Specific file stems (name without extension) excluded by filename.
    _EXCLUDED_STEMS = frozenset({
        "Gravity AI Review Suite",
        "ai_verification_prompts",
    })

    @classmethod
    def _is_excluded(cls, path: Path) -> bool:
        if set(path.parts) & cls._EXCLUDED_PATH_PARTS:
            return True
        if path.stem in cls._EXCLUDED_STEMS:
            return True
        return False

    @classmethod
    def _order_function_names_in(cls, path: Path) -> list[str]:
        """Return order-submission function names found by AST walk."""
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            return []
        found = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                if name in cls._ORDER_NAMES or name.startswith("place_"):
                    found.append(name)
        return found

    def test_no_order_functions_outside_execution(self) -> None:
        """
        Every .py file in the repository tree that is NOT under execution/ must
        define zero functions matching order-submission naming patterns.

        Pattern rationale:
          submit_order / buy_order / sell_order / place_* → clear order-placement intent
          place_equity_order / place_option_order → Robinhood / broker API names
          notify_order (Backtrader) does NOT match → correctly excluded
        """
        repo_root = Path(__file__).parent.parent
        violations: list[str] = []

        for py_file in sorted(repo_root.rglob("*.py")):
            if self._is_excluded(py_file):
                continue
            hits = self._order_function_names_in(py_file)
            for fn_name in hits:
                rel = py_file.relative_to(repo_root)
                violations.append(f"{rel}: def {fn_name}()")

        assert not violations, (
            "Order-submission function(s) found outside execution/:\n"
            + "\n".join(f"  {v}" for v in violations)
        )
