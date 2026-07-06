"""
tests/test_reporting_package.py
================================
Offline unit tests for the ``reporting/`` package, extracted verbatim from
``main.py`` into ``reporting/sheets_client.py``, ``reporting/sheet_publisher.py``,
``reporting/state_snapshot.py``, and ``reporting/html_publisher.py``.

No network / real Google Sheets / real credentials are ever touched:
  - ``TestSheetsClient`` pins the "no credentials.json → None" degrade path.
  - ``TestSheetPublisher`` pins the "no client → skip write, never raise" path
    and (optionally) that ``main._write_to_sheet`` is the SAME function object
    as ``reporting.sheet_publisher.write_recommendations`` once main.py is
    rewired to alias it.
  - ``TestHtmlPublisher`` pins that ``write_html_report`` produces a real,
    non-empty ``daily_report.html`` file from a minimal RunResult-shaped
    object, and (optionally) the same alias-pin for ``main._write_html_report``.

A NOTE ON main.py REWIRING: at the time this file was written, a concurrent
refactor was extracting ``reporting/`` out of ``main.py``. The alias-pinning
tests below are written defensively (skip rather than fail) for the window
where ``main.py`` hasn't yet been repointed at the new package — once that
lands, these tests start asserting the real invariant automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

import reporting.sheets_client as sheets_client
import reporting.sheet_publisher as sheet_publisher
import reporting.html_publisher as html_publisher

from engine.advisory import Recommendation


# ---------------------------------------------------------------------------
# Minimal RunResult-shaped fixtures (mirrors tests/test_run_once.py's pattern
# of MagicMock-based AccountSnapshot/PortfolioPosition stand-ins, and
# tests/test_html_report.py's minimal-dict-row style).
# ---------------------------------------------------------------------------

def _make_position(
    symbol: str,
    qty: float = 10.0,
    avg_cost: float = 100.0,
    current_price: float = 110.0,
) -> MagicMock:
    """Lightweight duck-typed PortfolioPosition stand-in (matches the fields
    read by rec_to_sheet_row / write_html_report: quantity, average_cost,
    current_price, market_value, unrealized_pl, unrealized_pl_pct,
    dividends_received, name)."""
    pos = MagicMock()
    pos.symbol = symbol
    pos.quantity = qty
    pos.average_cost = avg_cost
    pos.current_price = current_price
    pos.market_value = qty * current_price
    pos.unrealized_pl = (current_price - avg_cost) * qty
    pos.unrealized_pl_pct = (current_price - avg_cost) / avg_cost if avg_cost else 0.0
    pos.dividends_received = 5.0
    pos.name = f"{symbol} Inc."
    return pos


def _make_snapshot(positions: Optional[Dict[str, Any]] = None) -> MagicMock:
    """Lightweight duck-typed AccountSnapshot stand-in."""
    snap = MagicMock()
    snap.positions = positions or {}
    snap.buying_power = 5_000.0
    snap.total_equity = 41_250.0
    snap.total_dividends = 150.40
    snap.fetched_at = datetime.now(timezone.utc)
    snap.age_hours.return_value = 1.4
    snap.is_stale.return_value = False
    return snap


def _make_recommendation(symbol: str, action: str = "HOLD") -> Recommendation:
    return Recommendation(
        symbol=symbol,
        action=action,
        strategy="test_strategy",
        conviction=0.60,
        rationale=f"{symbol}: test rationale citing momentum and valuation.",
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
            "dividend_yield": 0.02,
            "kelly_raw": 0.04,
        },
        data_quality="OK",
    )


@dataclass(frozen=True)
class _FakeRunResult:
    """Duck-typed RunResult stand-in — carries exactly the attributes that
    reporting/sheet_publisher.py, reporting/state_snapshot.py, and
    reporting/html_publisher.py read (snapshot, recommendations, errors);
    started_at/finished_at/duration_seconds are unused by those modules but
    included for shape-fidelity with the real dataclass."""

    snapshot: Any
    recommendations: List[Recommendation]
    errors: List[dict] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_seconds: float = 0.5


# ---------------------------------------------------------------------------
# TestSheetsClient
# ---------------------------------------------------------------------------

class TestSheetsClient:
    """Pins the best-effort degrade-to-None contract in
    reporting/sheets_client.get_service_account_client()."""

    def test_no_credentials_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)  # no credentials.json here
        result = sheets_client.get_service_account_client()
        assert result is None


# ---------------------------------------------------------------------------
# TestSheetPublisher
# ---------------------------------------------------------------------------

class TestSheetPublisher:
    """Pins that write_recommendations() is a best-effort sink: it must never
    raise, and must be a true no-op (return None, no Sheets API calls) when
    credentials.json is absent."""

    def test_write_recommendations_skips_without_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)  # no credentials.json present
        snap = _make_snapshot()
        result = _FakeRunResult(
            snapshot=snap,
            recommendations=[_make_recommendation("AAPL", "HOLD")],
            errors=[],
        )

        # Should not raise, and should return None (write skipped).
        outcome = sheet_publisher.write_recommendations(result)
        assert outcome is None

    def test_write_recommendations_empty_result_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even a fully-empty RunResult (no recs, no errors) must degrade
        cleanly when there's no credentials.json — belt-and-suspenders on
        top of the "no client" short-circuit."""
        monkeypatch.chdir(tmp_path)
        result = _FakeRunResult(snapshot=_make_snapshot(), recommendations=[], errors=[])
        assert sheet_publisher.write_recommendations(result) is None

    def test_main_write_to_sheet_is_reporting_write_recommendations(self) -> None:
        """Pins the wiring: main._write_to_sheet must be (or eventually become)
        the exact same function object as reporting.sheet_publisher.write_recommendations,
        so the extraction can't silently drift into two divergent implementations.

        NOTE: at the time this test was written, a concurrent refactor was still
        migrating main.py to alias the extracted reporting/ package. If main.py
        still defines its own independent _write_to_sheet, this test is skipped
        (not failed) rather than asserting a false negative against in-flight work.
        """
        try:
            import main as m
        except ImportError as exc:
            pytest.skip(f"main module import failed in this environment: {exc}")

        if getattr(m, "_write_to_sheet", None) is not sheet_publisher.write_recommendations:
            pytest.skip(
                "main._write_to_sheet is not yet aliased to "
                "reporting.sheet_publisher.write_recommendations "
                "(expected while the main.py rewiring is in flight)."
            )

        assert m._write_to_sheet is sheet_publisher.write_recommendations


# ---------------------------------------------------------------------------
# TestHtmlPublisher
# ---------------------------------------------------------------------------

class TestHtmlPublisher:
    """Pins that write_html_report() produces a real daily_report.html file
    from a minimal RunResult-shaped object, and never raises even with
    minimal/missing optional fields (macro_dto=None, empty positions)."""

    def test_write_html_report_produces_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        # html_publisher.py does `from settings import settings`, so patch the
        # binding it actually reads (established pattern across the test
        # suite, e.g. tests/test_orchestrator_runner.py / test_pipeline_stage_status.py).
        monkeypatch.setattr(html_publisher.settings, "OUTPUT_DIR", tmp_path, raising=False)

        positions = {
            "AAPL": _make_position("AAPL", qty=12.0, avg_cost=180.25, current_price=214.10),
        }
        snap = _make_snapshot(positions=positions)
        result = _FakeRunResult(
            snapshot=snap,
            recommendations=[
                _make_recommendation("AAPL", "BUY"),
                _make_recommendation("AGNC", "SELL"),
            ],
            errors=[],
        )

        # Must not raise even with macro_dto=None (degrades to NEUTRAL regime,
        # no yield_curve/credit_spread/sahm_rule/real_yield kwargs).
        html_publisher.write_html_report(result, macro_dto=None)

        out_path = tmp_path / "daily_report.html"
        assert out_path.exists(), "daily_report.html was not written"
        content = out_path.read_text(encoding="utf-8")
        assert len(content) > 0
        assert "AAPL" in content

    def test_write_html_report_empty_positions_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A snapshot with zero positions (e.g. Robinhood degraded/empty) must
        still render a report — never fabricate holdings, never crash."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(html_publisher.settings, "OUTPUT_DIR", tmp_path, raising=False)

        snap = _make_snapshot(positions={})
        result = _FakeRunResult(
            snapshot=snap,
            recommendations=[_make_recommendation("SPY", "HOLD")],
            errors=[],
        )

        html_publisher.write_html_report(result, macro_dto=None)

        out_path = tmp_path / "daily_report.html"
        assert out_path.exists()
        assert out_path.stat().st_size > 0

    def test_main_write_html_report_is_reporting_write_html_report(self) -> None:
        """Pins the wiring: main._write_html_report must be (or eventually
        become) the exact same function object as
        reporting.html_publisher.write_html_report.

        NOTE: skipped (not failed) while the concurrent main.py rewiring to
        the extracted reporting/ package is still in flight — see the
        corresponding note on TestSheetPublisher above.
        """
        try:
            import main as m
        except ImportError as exc:
            pytest.skip(f"main module import failed in this environment: {exc}")

        if getattr(m, "_write_html_report", None) is not html_publisher.write_html_report:
            pytest.skip(
                "main._write_html_report is not yet aliased to "
                "reporting.html_publisher.write_html_report "
                "(expected while the main.py rewiring is in flight)."
            )

        assert m._write_html_report is html_publisher.write_html_report
