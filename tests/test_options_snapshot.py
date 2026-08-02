"""
tests/test_options_snapshot.py
===============================
Tests for ``reporting/options_snapshot.py::write_options_matrix`` -- the one
production caller of ``technical_options_engine.build_premium_directive``
that actually feeds the webapp's Options Matrix screen (``GET /options`` on
the Pilots API reads the ``output/options_matrix.json`` artifact this writes;
see the module docstring for why the API itself never imports the engine).

Coverage:
  * The base ``OPTIONS_MATRIX_ENABLED`` gate (pre-existing behavior, sanity
    check only).
  * **Flag-off proof (fmp-updates-data-apps PR)**: with
    ``FMP_OPTIONS_HEALTH_ENABLED`` and ``FMP_EARNINGS_ENABLED`` both at their
    default ``False``, the FMP fetch functions and ``HistoricalStore`` are
    never imported/constructed/called, and every new field on the written
    directive (``Altman_Z_Score``, ``Piotroski_F_Score``, ``Net_Debt_EBITDA``,
    ``FCF_Yield``, ``Days_To_Earnings``, ``Earnings_Risk``,
    ``Realized_Vol_30D``) is ``None``/``False`` -- byte-identical to
    pre-overlay behavior.
  * **Flag-on wiring**: with both flags on and the fetch functions/store
    mocked, the written directive is correctly hydrated from their return
    values.
  * **Per-symbol sub-fetch resilience**: one FMP sub-fetch raising must not
    blank out a sibling sub-fetch's real data for the SAME symbol, nor
    prevent the base directive from being written (CONSTRAINT #6).

Mocking convention matches the rest of the FMP test series:
``monkeypatch.setattr(settings, "X", ...)`` for gates, ``patch(...)`` for
the FMP fetch functions and ``HistoricalStore`` (constructed with no args
inside the writer, so the class itself is patched -- the same technique
``tests/test_fmp_feeds_company.py``'s ``real_store`` fixture uses).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from reporting.options_snapshot import write_options_matrix
from settings import settings


def _bars(n: int = 60, seed: int = 0) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
    return pd.DataFrame(
        {
            "Open": close, "High": close * 1.01, "Low": close * 0.99,
            "Close": close, "Volume": np.full(n, 1_000_000.0),
        },
        index=dates,
    )


class _FakeQuote:
    def __init__(self, price: float, is_stale: bool = False):
        self.price = price
        self.is_stale = is_stale


class _FakeProvider:
    """Minimal duck-typed stand-in for CompositeProvider -- only the two
    methods write_options_matrix's per-symbol loop actually calls."""

    def __init__(self, prices: dict[str, float] | None = None):
        self._prices = prices or {}

    def get_latest_quote(self, symbol: str) -> _FakeQuote:
        return _FakeQuote(self._prices.get(symbol, 150.0))

    def get_intraday_bars(self, symbol: str, lookback_days: int = 252) -> pd.DataFrame:
        return _bars(seed=abs(hash(symbol)) % 1000)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "OPTIONS_MATRIX_ENABLED", True)
    monkeypatch.setattr(settings, "FMP_OPTIONS_HEALTH_ENABLED", False)
    monkeypatch.setattr(settings, "FMP_EARNINGS_ENABLED", False)
    return settings


class TestWriteOptionsMatrixGate:
    def test_disabled_by_default_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OPTIONS_MATRIX_ENABLED", False)
        result = write_options_matrix(
            ["AAPL"], provider=_FakeProvider(), output_dir=tmp_path,
        )
        assert result is None
        assert not (tmp_path / "options_matrix.json").exists()

    def test_empty_symbol_list_writes_nothing(self, enabled, tmp_path):
        result = write_options_matrix([], provider=_FakeProvider(), output_dir=tmp_path)
        assert result is None


class TestWriteOptionsMatrixFmpFlagsOff:
    """Both new gates default False -- must be a complete no-op for the new
    fields, with zero extra imports/network/DB calls."""

    def test_new_fields_are_all_none_and_false(self, enabled, tmp_path):
        path = write_options_matrix(
            ["AAPL"], provider=_FakeProvider(), output_dir=tmp_path,
        )
        assert path is not None
        payload = json.loads((tmp_path / "options_matrix.json").read_text())
        directive = payload["directives"][0]
        assert directive["Altman_Z_Score"] is None
        assert directive["Piotroski_F_Score"] is None
        assert directive["Net_Debt_EBITDA"] is None
        assert directive["FCF_Yield"] is None
        assert directive["Days_To_Earnings"] is None
        assert directive["Earnings_Risk"] is False
        assert directive["Realized_Vol_30D"] is None

    def test_fmp_fetch_functions_never_imported_or_called(self, enabled, tmp_path):
        with patch("data.fmp_feeds_company.fetch_financial_scores") as mock_scores, \
             patch("data.fmp_feeds_company.fetch_key_ratios_ttm") as mock_ratios, \
             patch("data.fmp_feeds_market.fetch_realized_volatility") as mock_vol, \
             patch("data.historical_store.HistoricalStore") as mock_store:
            write_options_matrix(["AAPL"], provider=_FakeProvider(), output_dir=tmp_path)

        mock_scores.assert_not_called()
        mock_ratios.assert_not_called()
        mock_vol.assert_not_called()
        mock_store.assert_not_called()


class TestWriteOptionsMatrixFmpHealthFlagOn:
    def test_financial_scores_and_ratios_populate_the_directive(self, enabled, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "FMP_OPTIONS_HEALTH_ENABLED", True)

        with patch(
            "data.fmp_feeds_company.fetch_financial_scores",
            return_value={"altman_z_score": 4.1, "piotroski_f_score": 6, "source": "fmp"},
        ), patch(
            "data.fmp_feeds_company.fetch_key_ratios_ttm",
            return_value={"net_debt_ebitda": 1.5, "fcf_yield": 0.03, "source": "fmp"},
        ), patch(
            "data.fmp_feeds_market.fetch_realized_volatility",
            return_value={"hv_10": 0.3, "hv_30": 0.25, "hv_90": 0.2},
        ):
            write_options_matrix(["AAPL"], provider=_FakeProvider(), output_dir=tmp_path)

        payload = json.loads((tmp_path / "options_matrix.json").read_text())
        directive = payload["directives"][0]
        assert directive["Altman_Z_Score"] == pytest.approx(4.1)
        assert directive["Piotroski_F_Score"] == 6
        assert directive["Net_Debt_EBITDA"] == pytest.approx(1.5)
        assert directive["FCF_Yield"] == pytest.approx(0.03)
        assert directive["Realized_Vol_30D"] == pytest.approx(0.25)

    def test_a_failing_sub_fetch_does_not_blank_a_sibling_sub_fetchs_data(
        self, enabled, tmp_path, monkeypatch,
    ):
        """financial-scores raising must not prevent ratios-ttm's real data
        (or the base directive) from still landing on the SAME symbol's row
        (CONSTRAINT #6 -- one bad sub-fetch is dead-lettered independently)."""
        monkeypatch.setattr(settings, "FMP_OPTIONS_HEALTH_ENABLED", True)

        with patch(
            "data.fmp_feeds_company.fetch_financial_scores",
            side_effect=RuntimeError("financial-scores boom"),
        ), patch(
            "data.fmp_feeds_company.fetch_key_ratios_ttm",
            return_value={"net_debt_ebitda": 2.2, "fcf_yield": 0.07, "source": "fmp"},
        ), patch(
            "data.fmp_feeds_market.fetch_realized_volatility",
            side_effect=RuntimeError("stddev boom"),
        ):
            path = write_options_matrix(["AAPL"], provider=_FakeProvider(), output_dir=tmp_path)

        assert path is not None
        payload = json.loads((tmp_path / "options_matrix.json").read_text())
        directive = payload["directives"][0]
        assert directive["Altman_Z_Score"] is None
        assert directive["Piotroski_F_Score"] is None
        assert directive["Net_Debt_EBITDA"] == pytest.approx(2.2)
        assert directive["FCF_Yield"] == pytest.approx(0.07)
        assert directive["Realized_Vol_30D"] is None
        # The base directive (Strategy/Action/etc.) must still be present --
        # a health-overlay failure never aborts the whole symbol.
        assert directive["Symbol"] == "AAPL"
        assert "Strategy" in directive


class TestWriteOptionsMatrixEarningsFlagOn:
    def test_days_to_earnings_and_earnings_risk_computed_from_the_store(
        self, enabled, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(settings, "FMP_EARNINGS_ENABLED", True)
        near_future = (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()

        mock_store = MagicMock()
        mock_store.get_earnings_events.return_value = [
            {"symbol": "AAPL", "event_date": near_future}
        ]
        with patch("data.historical_store.HistoricalStore", return_value=mock_store):
            write_options_matrix(
                ["AAPL"], provider=_FakeProvider(), output_dir=tmp_path, target_dte=30,
            )

        payload = json.loads((tmp_path / "options_matrix.json").read_text())
        directive = payload["directives"][0]
        assert directive["Days_To_Earnings"] == 7
        assert directive["Earnings_Risk"] is True
        # Folds into Integrity_OK per technical_options_engine's documented
        # dual-meaning contract (structural + timing verdict).
        assert directive["Integrity_OK"] is False

        # The store is constructed exactly once for the whole run, not once
        # per symbol (matches pipeline/production_steps.py's own convention).
        assert mock_store.get_earnings_events.call_count == 1

    def test_no_upcoming_event_leaves_days_to_earnings_none(
        self, enabled, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(settings, "FMP_EARNINGS_ENABLED", True)
        mock_store = MagicMock()
        mock_store.get_earnings_events.return_value = []
        with patch("data.historical_store.HistoricalStore", return_value=mock_store):
            write_options_matrix(["AAPL"], provider=_FakeProvider(), output_dir=tmp_path)

        payload = json.loads((tmp_path / "options_matrix.json").read_text())
        directive = payload["directives"][0]
        assert directive["Days_To_Earnings"] is None
        assert directive["Earnings_Risk"] is False

    def test_store_lookup_failure_for_one_symbol_does_not_abort_the_run(
        self, enabled, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(settings, "FMP_EARNINGS_ENABLED", True)
        mock_store = MagicMock()
        mock_store.get_earnings_events.side_effect = RuntimeError("db boom")
        with patch("data.historical_store.HistoricalStore", return_value=mock_store):
            path = write_options_matrix(["AAPL"], provider=_FakeProvider(), output_dir=tmp_path)

        assert path is not None
        payload = json.loads((tmp_path / "options_matrix.json").read_text())
        directive = payload["directives"][0]
        assert directive["Days_To_Earnings"] is None
        assert directive["Symbol"] == "AAPL"
