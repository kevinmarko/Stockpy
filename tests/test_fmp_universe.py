"""
tests/test_fmp_universe.py
===========================
Unit tests for the FMP S&P 500 constituent-changes integration:

- ``data/fmp_client.py::historical_sp500_changes`` -- the raw
  ``/historical-sp-500`` HTTP wrapper (param mapping, raw-JSON pass-through).
  The shared throttle/retry/cooldown/dead-endpoint machinery it routes
  through (``_fmp_get``) is already exhaustively covered by
  ``tests/test_fmp_client.py``; this file only proves
  ``historical_sp500_changes`` routes through it correctly, not the
  machinery itself.
- ``data/fmp_universe.py::fetch_sp500_changes_via_fmp`` -- the two-gate
  (``FMP_UNIVERSE_ENABLED`` + ``FMP_API_KEY``) dispatcher that reshapes
  FMP's raw JSON into ``universe_engine.py``'s internal change-record
  schema. CONSTRAINT #6: never raises, degrades to ``[]`` on any failure so
  ``universe_engine.py`` falls through to the Wikipedia scrape.

``universe_engine.py``'s own FMP-primary/Wikipedia-fallback ordering (does
FMP actually win when it returns rows, does an empty/failed FMP result fall
through) is covered separately in
``tests/test_dead_letter_resilience.py::TestFetchAndCacheUniverseFMPPrimarySource``.

Everything here is offline. ``requests.get`` / ``data.fmp_client.
historical_sp500_changes`` are monkeypatched or mocked; no real network
request occurs.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from data.fmp_client import FMPUnavailable, historical_sp500_changes, reset_fmp_rate_limiter
from data.fmp_universe import fetch_sp500_changes_via_fmp
from settings import settings


def _resp(status: int = 200, *, payload=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = payload if payload is not None else []
    return resp


@pytest.fixture
def api_key(monkeypatch):
    """A key on the SINGLETON -- never via ``patch.dict(os.environ)``, which
    would test a code path ``data/fmp_client.py`` deliberately does not have
    (see ``tests/test_fmp_news.py``'s identical fixture and its docstring)."""
    reset_fmp_rate_limiter()
    monkeypatch.setattr(settings, "FMP_API_KEY", "test-key-abc123")
    yield "test-key-abc123"
    reset_fmp_rate_limiter()


@pytest.fixture
def enabled(api_key, monkeypatch):
    monkeypatch.setattr(settings, "FMP_UNIVERSE_ENABLED", True)
    yield


# ---------------------------------------------------------------------------
# data/fmp_client.py::historical_sp500_changes
# ---------------------------------------------------------------------------

class TestHistoricalSp500Changes:
    """Only the wrapper's own routing is exercised here; the throttle/retry/
    cooldown/dead-endpoint state machine it runs through (``_fmp_get``) is
    not re-tested per-wrapper -- that's ``tests/test_fmp_client.py``'s job."""

    def test_hits_the_historical_sp_500_path(self, api_key):
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            historical_sp500_changes()
        assert get.call_args.args[0].endswith("/historical-sp-500")

    def test_returns_raw_parsed_json_list_unchanged(self, api_key):
        raw = [{"date": "2024-05-13", "symbol": "GDDY", "removedTicker": "SEDG"}]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload=raw)):
            assert historical_sp500_changes() == raw

    def test_no_api_key_raises_without_touching_the_network(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        with patch("data.fmp_client.requests.get") as get:
            with pytest.raises(FMPUnavailable):
                historical_sp500_changes()
        assert get.called is False


# ---------------------------------------------------------------------------
# data/fmp_universe.py::fetch_sp500_changes_via_fmp
# ---------------------------------------------------------------------------

class TestFetchSp500ChangesViaFmp:
    def test_disabled_by_default_returns_empty_no_network(self, api_key):
        """FMP_UNIVERSE_ENABLED defaults False -- a complete no-op reproducing
        today's exact Wikipedia-only behavior."""
        with patch("data.fmp_client.historical_sp500_changes") as mock_fetch:
            result = fetch_sp500_changes_via_fmp()
        assert result == []
        mock_fetch.assert_not_called()

    def test_enabled_but_no_api_key_returns_empty_no_network(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_UNIVERSE_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        with patch("data.fmp_client.historical_sp500_changes") as mock_fetch:
            result = fetch_sp500_changes_via_fmp()
        assert result == []
        mock_fetch.assert_not_called()

    def test_fmpunavailable_degrades_to_empty_not_raised(self, enabled):
        with patch(
            "data.fmp_client.historical_sp500_changes",
            side_effect=FMPUnavailable("cooldown open"),
        ):
            result = fetch_sp500_changes_via_fmp()
        assert result == []

    def test_unexpected_exception_degrades_to_empty_not_raised(self, enabled):
        """CONSTRAINT #6: any failure shape -- not just FMPUnavailable --
        must degrade, never propagate and crash universe_engine.py."""
        with patch(
            "data.fmp_client.historical_sp500_changes",
            side_effect=RuntimeError("unexpected"),
        ):
            result = fetch_sp500_changes_via_fmp()
        assert result == []

    def test_non_list_response_returns_empty(self, enabled):
        with patch("data.fmp_client.historical_sp500_changes", return_value={"error": "bad"}):
            result = fetch_sp500_changes_via_fmp()
        assert result == []

    def test_empty_list_response_returns_empty(self, enabled):
        with patch("data.fmp_client.historical_sp500_changes", return_value=[]):
            result = fetch_sp500_changes_via_fmp()
        assert result == []

    def test_happy_path_reshapes_rows_and_tags_provider(self, enabled):
        raw = [
            {"date": "2024-05-13", "symbol": "GDDY", "removedTicker": "SEDG"},
            {"date": "2024-06-01", "symbol": "NEWCO", "removedTicker": None},
        ]
        with patch("data.fmp_client.historical_sp500_changes", return_value=raw):
            result = fetch_sp500_changes_via_fmp()

        assert len(result) == 2
        first = result[0]
        assert first["type"] == "change"
        assert first["date"] == "2024-05-13"
        assert first["added_ticker"] == "GDDY"
        assert first["removed_ticker"] == "SEDG"
        assert first["_provider"] == "fmp"
        assert result[1]["added_ticker"] == "NEWCO"
        assert result[1]["removed_ticker"] is None

    def test_row_missing_date_is_skipped_not_raised(self, enabled):
        raw = [
            {"symbol": "NODATE", "removedTicker": "OLD"},  # no "date" key
            {"date": "2024-06-01", "symbol": "GOOD", "removedTicker": "BAD"},
        ]
        with patch("data.fmp_client.historical_sp500_changes", return_value=raw):
            result = fetch_sp500_changes_via_fmp()
        assert len(result) == 1
        assert result[0]["added_ticker"] == "GOOD"

    def test_row_with_neither_added_nor_removed_ticker_is_skipped(self, enabled):
        raw = [{"date": "2024-06-01"}]
        with patch("data.fmp_client.historical_sp500_changes", return_value=raw):
            result = fetch_sp500_changes_via_fmp()
        assert result == []

    def test_non_dict_row_is_skipped_not_raised(self, enabled):
        raw = ["not-a-dict", {"date": "2024-06-01", "symbol": "GOOD"}]
        with patch("data.fmp_client.historical_sp500_changes", return_value=raw):
            result = fetch_sp500_changes_via_fmp()
        assert len(result) == 1
        assert result[0]["added_ticker"] == "GOOD"
