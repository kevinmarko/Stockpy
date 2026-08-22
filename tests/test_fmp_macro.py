"""
tests/test_fmp_macro.py
========================
Covers three things:

1. ``data/fmp_macro.py``'s pure translation functions (``fetch_treasury_curve``,
   ``fetch_unemployment_rate``) -- shaping FMP's ``/treasury-rates`` and
   ``/economic-indicators`` payloads into FRED-compatible
   ``(series_id, date, value)`` rows, degrading to ``[]`` on any failure.

2. A regression test for ``HistoricalStore._resolve_data_engine``'s confirmed
   live bug: ``DataEngine()`` was called with no arguments against a
   constructor requiring ``fred_api_key``, so the ``TypeError`` was silently
   swallowed by the enclosing ``except Exception`` and ``HistoricalStore.
   get_macro()`` never once topped up from FRED via this path in production.

3. ``DataEngine.fetch_macro_raw``'s FMP fallback wiring (``data_engine.py``):
   flag-off byte-identical behaviour (zero FMP network activity), flag-on
   T10Y2Y/UNRATE-only supplementation, and the fully-fabricated last resort
   when both FRED and FMP fail.

All offline. Mocking follows this repo's convention:
``patch("data.fmp_client.requests.get", ...)`` and
``patch("settings.settings.X", ...)`` -- never ``patch.dict(os.environ)``, no
``responses``, no VCR. Unmarked (no network).
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from data.fmp_client import FMPUnavailable
from data.fmp_macro import fetch_treasury_curve, fetch_unemployment_rate
from data.historical_store import HistoricalStore
from data_engine import DataEngine
from settings import settings


def _resp(status: int = 200, payload=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = payload if payload is not None else []
    return resp


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setattr(settings, "FMP_API_KEY", "test-key-abc123")
    return "test-key-abc123"


# ---------------------------------------------------------------------------
# fetch_treasury_curve
# ---------------------------------------------------------------------------
class TestFetchTreasuryCurve:
    def test_happy_path_computes_t10y2y_from_year10_minus_year2(self, api_key):
        payload = [
            {"date": "2026-07-01", "month1": 5.3, "year2": 4.1, "year10": 4.35},
            {"date": "2026-07-02", "month1": 5.3, "year2": 4.0, "year10": 4.30},
        ]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload)) as get:
            rows = fetch_treasury_curve("2026-06-01", "2026-07-02")

        assert get.call_count == 1
        assert rows == [
            {"series_id": "T10Y2Y", "date": "2026-07-01", "value": pytest.approx(0.25), "source": "fmp"},
            {"series_id": "T10Y2Y", "date": "2026-07-02", "value": pytest.approx(0.30), "source": "fmp"},
        ]

    def test_rows_are_sorted_ascending_by_date_regardless_of_payload_order(self, api_key):
        payload = [
            {"date": "2026-07-02", "year2": 4.0, "year10": 4.30},
            {"date": "2026-07-01", "year2": 4.1, "year10": 4.35},
        ]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload)):
            rows = fetch_treasury_curve("2026-06-01", "2026-07-02")

        assert [r["date"] for r in rows] == ["2026-07-01", "2026-07-02"]

    def test_row_missing_year10_is_skipped(self, api_key):
        payload = [
            {"date": "2026-07-01", "year2": 4.1},  # no year10
            {"date": "2026-07-02", "year2": 4.0, "year10": 4.30},
        ]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload)):
            rows = fetch_treasury_curve("2026-06-01", "2026-07-02")

        assert len(rows) == 1
        assert rows[0]["date"] == "2026-07-02"

    def test_row_missing_year2_is_skipped(self, api_key):
        payload = [{"date": "2026-07-01", "year10": 4.35}]  # no year2
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload)):
            rows = fetch_treasury_curve("2026-06-01", "2026-07-02")
        assert rows == []

    def test_empty_payload_returns_empty_list(self, api_key):
        with patch("data.fmp_client.requests.get", return_value=_resp(200, [])):
            assert fetch_treasury_curve("2026-06-01", "2026-07-02") == []

    def test_non_list_payload_returns_empty_list(self, api_key):
        with patch("data.fmp_client.requests.get", return_value=_resp(200, {"Error Message": "bad request"})):
            # a dict payload isn't recognised as an access-denied body (too
            # generic), but fetch_treasury_curve must still degrade cleanly
            # rather than raise on the unexpected shape.
            assert fetch_treasury_curve("2026-06-01", "2026-07-02") == []

    def test_missing_api_key_degrades_to_empty_list_never_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        with patch("data.fmp_client.requests.get") as get:
            rows = fetch_treasury_curve("2026-06-01", "2026-07-02")
        assert rows == []
        get.assert_not_called()

    def test_network_failure_returns_empty_list_never_raises(self, api_key):
        with patch("data.fmp_client.requests.get", side_effect=ConnectionError("boom")):
            rows = fetch_treasury_curve("2026-06-01", "2026-07-02")
        assert rows == []

    def test_fmp_unavailable_from_entitlement_refusal_returns_empty_list(self, api_key):
        with patch(
            "data.fmp_client.requests.get",
            return_value=_resp(403, {"Error Message": "Exclusive Endpoint"}),
        ):
            rows = fetch_treasury_curve("2026-06-01", "2026-07-02")
        assert rows == []


# ---------------------------------------------------------------------------
# fetch_unemployment_rate
# ---------------------------------------------------------------------------
class TestFetchUnemploymentRate:
    def test_happy_path(self, api_key):
        payload = [
            {"name": "unemploymentRate", "date": "2026-06-01", "value": 4.1},
            {"name": "unemploymentRate", "date": "2026-05-01", "value": 4.0},
        ]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload)) as get:
            rows = fetch_unemployment_rate("2026-01-01", "2026-06-30")

        assert get.call_count == 1
        # sorted ascending by date -- most recent is last
        assert [r["date"] for r in rows] == ["2026-05-01", "2026-06-01"]
        assert rows[-1] == {
            "series_id": "UNRATE", "date": "2026-06-01", "value": pytest.approx(4.1), "source": "fmp",
        }

    def test_empty_payload_returns_empty_list(self, api_key):
        with patch("data.fmp_client.requests.get", return_value=_resp(200, [])):
            assert fetch_unemployment_rate("2026-01-01", "2026-06-30") == []

    def test_malformed_row_missing_value_is_skipped(self, api_key):
        payload = [{"name": "unemploymentRate", "date": "2026-06-01"}]  # no value
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload)):
            assert fetch_unemployment_rate("2026-01-01", "2026-06-30") == []

    def test_malformed_row_non_numeric_value_is_skipped(self, api_key):
        payload = [{"name": "unemploymentRate", "date": "2026-06-01", "value": "N/A"}]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload)):
            assert fetch_unemployment_rate("2026-01-01", "2026-06-30") == []

    def test_non_list_payload_returns_empty_list(self, api_key):
        with patch("data.fmp_client.requests.get", return_value=_resp(200, {})):
            assert fetch_unemployment_rate("2026-01-01", "2026-06-30") == []

    def test_network_failure_returns_empty_list_never_raises(self, api_key):
        with patch("data.fmp_client.requests.get", side_effect=TimeoutError("timed out")):
            rows = fetch_unemployment_rate("2026-01-01", "2026-06-30")
        assert rows == []

    def test_missing_api_key_degrades_to_empty_list_never_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        with patch("data.fmp_client.requests.get") as get:
            rows = fetch_unemployment_rate("2026-01-01", "2026-06-30")
        assert rows == []
        get.assert_not_called()


# ---------------------------------------------------------------------------
# HistoricalStore._resolve_data_engine regression test (Deliverable 1)
# ---------------------------------------------------------------------------
class TestResolveDataEngineRegression:
    """DataEngine.__init__(self, fred_api_key) requires the argument. The
    original ``return DataEngine()`` call therefore always raised TypeError,
    silently swallowed by the enclosing ``except Exception`` -- meaning
    HistoricalStore.get_macro() never once topped up from FRED via this path
    in production. Fixed to ``DataEngine(fred_api_key=_s.FRED_API_KEY)``.
    """

    def test_returns_a_real_data_engine_when_fred_api_key_is_set(self, monkeypatch):
        monkeypatch.setattr(settings, "FRED_API_KEY", "a-real-fred-key")
        result = HistoricalStore._resolve_data_engine(None)
        assert result is not None
        assert isinstance(result, DataEngine)
        assert result.fred_key == "a-real-fred-key"

    def test_returns_none_gracefully_when_fred_api_key_is_empty(self, monkeypatch):
        monkeypatch.setattr(settings, "FRED_API_KEY", "")
        result = HistoricalStore._resolve_data_engine(None)
        assert result is None

    def test_an_injected_data_engine_is_returned_as_is(self, monkeypatch):
        sentinel = object()
        # Should short-circuit before ever touching settings.FRED_API_KEY.
        result = HistoricalStore._resolve_data_engine(sentinel)
        assert result is sentinel


# ---------------------------------------------------------------------------
# DataEngine.fetch_macro_raw FMP wiring (Deliverable 3)
# ---------------------------------------------------------------------------
_HARDCODED_FALLBACK = {"T10Y2Y": 0.5, "BAMLH0A0HYM2": 3.5, "UNRATE": 3.8, "VIXCLS": 15.0}


class TestFetchMacroRawFmpWiring:
    def _engine(self):
        # fred_api_key=None -> self.fred is None -> FRED "fails" immediately,
        # exercising the fallback path without needing to mock fredapi.
        return DataEngine(fred_api_key=None)

    def test_flag_off_reproduces_the_exact_hardcoded_fallback_zero_fmp_calls(
        self, monkeypatch, api_key,
    ):
        monkeypatch.setattr(settings, "FMP_MACRO_ENABLED", False)
        with patch("data.fmp_client.requests.get") as get:
            result = self._engine().fetch_macro_raw()

        assert result == _HARDCODED_FALLBACK
        get.assert_not_called()

    def test_flag_on_fmp_serves_t10y2y_and_unrate_only(self, monkeypatch, api_key):
        monkeypatch.setattr(settings, "FMP_MACRO_ENABLED", True)

        treasury_payload = [{"date": "2026-07-15", "year2": 4.0, "year10": 4.55}]
        unrate_payload = [{"name": "unemploymentRate", "date": "2026-06-01", "value": 4.2}]

        with patch(
            "data.fmp_client.requests.get",
            side_effect=[_resp(200, treasury_payload), _resp(200, unrate_payload)],
        ) as get:
            result = self._engine().fetch_macro_raw()

        assert get.call_count == 2
        assert result["T10Y2Y"] == pytest.approx(0.55)
        assert result["UNRATE"] == pytest.approx(4.2)
        # VIXCLS and BAMLH0A0HYM2 have no FMP equivalent -- must stay on the
        # hardcoded constant (FRED itself is unavailable in this test), never
        # sourced from FMP.
        assert result["VIXCLS"] == _HARDCODED_FALLBACK["VIXCLS"]
        assert result["BAMLH0A0HYM2"] == _HARDCODED_FALLBACK["BAMLH0A0HYM2"]

    def test_flag_on_fmp_also_failing_falls_back_to_hardcoded_dict_with_warning(
        self, monkeypatch, api_key, caplog,
    ):
        monkeypatch.setattr(settings, "FMP_MACRO_ENABLED", True)

        with patch("data.fmp_client.requests.get", side_effect=ConnectionError("down")):
            with caplog.at_level(logging.WARNING, logger="Data_Engine"):
                result = self._engine().fetch_macro_raw()

        assert result == _HARDCODED_FALLBACK
        warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("fabricated" in msg.lower() or "hardcoded" in msg.lower() for msg in warnings), warnings

    def test_flag_on_no_api_key_falls_back_to_hardcoded_dict_zero_network(
        self, monkeypatch,
    ):
        # FMP_MACRO_ENABLED=True but no FMP_API_KEY configured: fmp_macro's
        # functions degrade to [] with zero network calls (CONSTRAINT #6),
        # and fetch_macro_raw must still land on the same hardcoded dict.
        monkeypatch.setattr(settings, "FMP_MACRO_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        with patch("data.fmp_client.requests.get") as get:
            result = self._engine().fetch_macro_raw()

        assert result == _HARDCODED_FALLBACK
        get.assert_not_called()

    def test_fred_success_short_circuits_before_any_fmp_import_or_call(
        self, monkeypatch, api_key,
    ):
        monkeypatch.setattr(settings, "FMP_MACRO_ENABLED", True)
        engine = DataEngine(fred_api_key=None)

        fake_fred = MagicMock()
        fake_series = MagicMock()
        fake_series.iloc = [4.5]
        # get_series(...).iloc[-1] must resolve to a float-able value for all
        # four calls (T10Y2Y, BAMLH0A0HYM2, UNRATE, then VIXCLS via .dropna()).
        result_series = MagicMock()
        result_series.iloc.__getitem__.return_value = 4.5
        dropna_series = MagicMock()
        dropna_series.iloc.__getitem__.return_value = 15.5
        result_series.dropna.return_value = dropna_series
        fake_fred.get_series.return_value = result_series
        engine.fred = fake_fred

        with patch("data.fmp_client.requests.get") as get:
            result = engine.fetch_macro_raw()

        get.assert_not_called()
        assert result["T10Y2Y"] == pytest.approx(4.5)
        assert result["VIXCLS"] == pytest.approx(15.5)


# ---------------------------------------------------------------------------
# fetch_macro_raw_detailed() / last_macro_raw_fabricated_keys -- the
# populated-but-fabricated blind spot in macro_killswitch_data_unavailable()
# that PR #854's key-presence-only check couldn't see (every key is present
# in _MACRO_HARDCODED_FALLBACK, just with fabricated values).
# ---------------------------------------------------------------------------
class TestFetchMacroRawFabricatedKeys:
    def _engine(self):
        return DataEngine(fred_api_key=None)

    def test_full_outage_reports_all_four_keys_fabricated(self, monkeypatch, api_key):
        monkeypatch.setattr(settings, "FMP_MACRO_ENABLED", False)
        engine = self._engine()

        result, fabricated = engine.fetch_macro_raw_detailed()

        assert result == _HARDCODED_FALLBACK
        assert fabricated == frozenset({"T10Y2Y", "BAMLH0A0HYM2", "UNRATE", "VIXCLS"})
        # Side effect: last_macro_raw_fabricated_keys mirrors the return value.
        assert engine.last_macro_raw_fabricated_keys == fabricated

    def test_fetch_macro_raw_stays_byte_identical_to_detailed_first_element(
        self, monkeypatch, api_key,
    ):
        monkeypatch.setattr(settings, "FMP_MACRO_ENABLED", False)
        engine = self._engine()
        assert engine.fetch_macro_raw() == engine.fetch_macro_raw_detailed()[0]

    def test_fmp_partial_success_leaves_vix_and_oas_fabricated_only(
        self, monkeypatch, api_key,
    ):
        # T10Y2Y/UNRATE served by FMP (real); VIXCLS/BAMLH0A0HYM2 have no FMP
        # equivalent and stay on the hardcoded constant -- both must still be
        # reported fabricated even though FMP "succeeded" overall.
        monkeypatch.setattr(settings, "FMP_MACRO_ENABLED", True)
        treasury_payload = [{"date": "2026-07-15", "year2": 4.0, "year10": 4.55}]
        unrate_payload = [{"name": "unemploymentRate", "date": "2026-06-01", "value": 4.2}]
        engine = self._engine()

        with patch(
            "data.fmp_client.requests.get",
            side_effect=[_resp(200, treasury_payload), _resp(200, unrate_payload)],
        ):
            result, fabricated = engine.fetch_macro_raw_detailed()

        assert result["T10Y2Y"] == pytest.approx(0.55)
        assert fabricated == frozenset({"VIXCLS", "BAMLH0A0HYM2"})

    def test_healthy_fred_fetch_reports_nothing_fabricated(self, monkeypatch, api_key):
        engine = self._engine()
        fake_fred = MagicMock()
        result_series = MagicMock()
        result_series.iloc.__getitem__.return_value = 4.5
        dropna_series = MagicMock()
        dropna_series.iloc.__getitem__.return_value = 15.5
        result_series.dropna.return_value = dropna_series
        fake_fred.get_series.return_value = result_series
        engine.fred = fake_fred

        result, fabricated = engine.fetch_macro_raw_detailed()

        assert result["VIXCLS"] == pytest.approx(15.5)
        assert fabricated == frozenset()

    def test_vix_only_silent_sub_fallback_is_reported_fabricated(self, monkeypatch, api_key):
        """T10Y2Y/BAMLH0A0HYM2/UNRATE succeed, but the VIXCLS-specific
        get_series(...).dropna() call raises -- fred_result is still built
        and returned as a "successful" fetch (the outer try/except never
        fires), so only fabricated={'VIXCLS'} distinguishes this from a
        fully-healthy read. This is the narrow gap the investigation found:
        VIX -- the single most load-bearing field for killSwitch (vix > 30.0
        fires it directly) -- could be silently fabricated even when every
        other series is real, invisible to a plain fetch_macro_raw() caller.
        """
        engine = self._engine()
        fake_fred = MagicMock()

        good_series = MagicMock()
        good_series.iloc.__getitem__.return_value = 4.5

        def _get_series(series_id, limit=None):
            if series_id == "VIXCLS":
                raise ConnectionError("VIXCLS endpoint down")
            return good_series

        fake_fred.get_series.side_effect = _get_series
        engine.fred = fake_fred

        result, fabricated = engine.fetch_macro_raw_detailed()

        assert result["T10Y2Y"] == pytest.approx(4.5)
        assert result["BAMLH0A0HYM2"] == pytest.approx(4.5)
        assert result["UNRATE"] == pytest.approx(4.5)
        assert result["VIXCLS"] == 15.0  # the silent placeholder
        assert fabricated == frozenset({"VIXCLS"})
        assert engine.last_macro_raw_fabricated_keys == frozenset({"VIXCLS"})

    def test_mock_data_engine_always_reports_nothing_fabricated(self):
        from data_engine import MockDataEngine

        engine = MockDataEngine()
        assert engine.last_macro_raw_fabricated_keys == frozenset()
