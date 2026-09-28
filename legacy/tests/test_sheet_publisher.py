"""
legacy/tests/test_sheet_publisher.py
====================================
ARCHIVED (2026-09, step 4e). Not collected by pytest (testpaths = tests in
pytest.ini) — kept so it can be restored if needed. See legacy/README.md.

Split out of tests/test_reporting_package.py when the Google Sheet publisher
(``reporting/sheets_client.py``, ``reporting/sheet_publisher.py``) was moved
to ``legacy/reporting/``. The HTML-publisher tests that used to share this
file stayed in tests/test_reporting_package.py (still active).

No network / real Google Sheets / real credentials are ever touched:
  - ``TestSheetsClient`` pins the "no credentials.json -> None" degrade path.
  - ``TestSheetPublisher`` pins the "no client -> skip write, never raise"
    path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

import legacy.reporting.sheets_client as sheets_client
import legacy.reporting.sheet_publisher as sheet_publisher

from engine.advisory import Recommendation


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
    legacy/reporting/sheet_publisher.py reads (snapshot, recommendations,
    errors); started_at/finished_at/duration_seconds are unused but included
    for shape-fidelity with the real dataclass."""

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
    legacy/reporting/sheets_client.get_service_account_client()."""

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
