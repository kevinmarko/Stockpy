"""
tests/test_fmp_feeds_market.py
===============================
Unit tests for ``data/fmp_feeds_market.py`` (``fetch_insider_stats``,
``fetch_sector_snapshot``) and the two ``pipeline/production_steps.py``
writers this agent owns the BODIES of: ``_apply_fmp_insider`` and
``_apply_fmp_sector``. The four writer stubs and the eight-column
"flag-off is byte-identical" contract are already pinned by
``tests/test_production_steps_fmp_stubs.py`` (not owned by this agent, not
modified here) -- this file covers what those stubs cannot: the real
fetch/cadence/minimum-lag-filter logic that runs once the two gates this
agent owns (``FMP_INSIDER_ENABLED``, ``FMP_SECTOR_SNAPSHOT_ENABLED``) are on.

Mocking conventions match the rest of this series:
``patch("data.fmp_client.requests.get", ...)`` for the raw HTTP boundary,
``patch("settings.settings.X", ...)`` (via ``monkeypatch.setattr(settings, ...)``,
equivalent) for gates/knobs -- never ``patch.dict(os.environ)``, no
``responses``/VCR. Fully offline, no network marks.

**The critical test in this file** is
``TestApplyFmpInsiderMinimumLagFilter`` -- it proves the leakage trap the
brief called out explicitly: this is NOT a date filter on the row itself,
it is a minimum-lag filter on how long ago the QUARTER ENDED, because a
quarter's aggregate keeps changing as late Form 4s land for weeks after the
quarter closes. A quarter ending 10 days before "today" must be excluded; a
quarter ending 100 days before "today" must be included -- and because real
quarter-end dates are fixed calendar dates (Mar 31 / Jun 30 / Sep 30 /
Dec 31), hitting those two lags EXACTLY only happens on specific real
dates. ``_FrozenDate`` below freezes "today" so the test is deterministic
regardless of when the suite actually runs.
"""
from __future__ import annotations

import datetime as _datetime_module
import itertools
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.fmp_feeds_market import (
    _pct_to_fraction,
    fetch_economics_calendar,
    fetch_insider_stats,
    fetch_peer_group,
    fetch_realized_volatility,
    fetch_sector_snapshot,
)
from pipeline.production_steps import (
    _apply_fmp_econ_calendar,
    _apply_fmp_insider,
    _apply_fmp_sector,
)
from settings import settings


def _resp(status: int = 200, *, payload=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = payload if payload is not None else []
    return resp


@pytest.fixture
def fmp_settings(monkeypatch):
    """A usable ``FMP_API_KEY`` plus zero retries, so a single mocked
    failure response maps to exactly one ``requests.get`` call. The root
    ``conftest.py``'s autouse ``_no_fmp_throttle_in_tests`` fixture already
    zeroes the throttle interval/backoff and resets the breaker before and
    after every test."""
    monkeypatch.setattr(settings, "FMP_API_KEY", "test-key-abc123")
    monkeypatch.setattr(settings, "FMP_MAX_RETRIES", 0)
    return settings


@pytest.fixture
def real_store(tmp_path, monkeypatch):
    """A real, temp-file-backed ``HistoricalStore``, substituted for
    whatever ``HistoricalStore()`` the ``_apply_fmp_*`` writers construct
    internally (they always call it with no args). Proves the actual
    upsert -> read-back -> minimum-lag-filter plumbing works end to end,
    rather than asserting against a hand-built mock's call signature."""
    from data.historical_store import HistoricalStore as _HS

    store = _HS(db_path=str(tmp_path / "fmp_feeds_market_test.db"))
    monkeypatch.setattr("data.historical_store.HistoricalStore", lambda *a, **k: store)
    return store


class _FrozenDate(_datetime_module.date):
    """``datetime.date`` subclass whose ``.today()`` returns a fixed value.

    Patched via ``monkeypatch.setattr(_datetime_module, "date", _FrozenDate)``,
    which only affects code that does a FRESH ``datetime.date`` lookup AFTER
    the patch takes effect -- exactly ``_apply_fmp_insider``'s own
    function-local ``from datetime import date as _date``. Any module that
    already bound ``date`` at import time (e.g. via a module-level
    ``from datetime import date``) keeps the real class, completely
    unaffected; ``data/historical_store.py`` only imports ``datetime``,
    ``timedelta``, ``timezone`` at module level, never ``date``, so this is
    safe to use here.
    """
    _frozen = _datetime_module.date(2026, 4, 10)

    @classmethod
    def today(cls):
        return cls._frozen


# ─────────────────────────────────────────────────────────────────────────
# data/fmp_feeds_market.py::fetch_insider_stats
# ─────────────────────────────────────────────────────────────────────────

class TestFetchInsiderStats:
    def test_happy_path_maps_vendor_fields_to_the_upsert_schema(self, fmp_settings):
        payload = [{
            "symbol": "aapl",
            "year": 2026,
            "quarter": 1,
            "acquiredTransactions": 5,
            "disposedTransactions": 12,
            "acquiredDisposedRatio": 0.4167,
            "totalAcquired": 10000.0,
            "totalDisposed": 24000.0,
            "totalPurchases": 5,
            "totalSales": 12,
        }]
        with patch(
            "data.fmp_client.requests.get", return_value=_resp(200, payload=payload),
        ) as get:
            rows = fetch_insider_stats("aapl")

        assert get.call_count == 1
        assert get.call_args.args[0].endswith("/insider-trading/statistics")
        assert get.call_args.kwargs["params"]["symbol"] == "AAPL"

        assert len(rows) == 1
        row = rows[0]
        assert row["symbol"] == "AAPL"
        assert row["year"] == 2026
        assert row["quarter"] == 1
        assert row["acquired_transactions"] == 5
        assert row["disposed_transactions"] == 12
        assert row["acquired_disposed_ratio"] == pytest.approx(0.4167)
        assert row["total_acquired"] == pytest.approx(10000.0)
        assert row["total_disposed"] == pytest.approx(24000.0)
        assert row["total_purchases"] == 5
        assert row["total_sales"] == 12
        assert row["source"] == "fmp"

    def test_ratio_is_derived_when_the_vendor_field_is_absent(self, fmp_settings):
        payload = [{
            "symbol": "AAPL", "year": 2026, "quarter": 1,
            "totalAcquired": 3000.0, "totalDisposed": 12000.0,
        }]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload=payload)):
            rows = fetch_insider_stats("AAPL")
        assert rows[0]["acquired_disposed_ratio"] == pytest.approx(0.25)

    def test_ratio_is_nan_when_total_disposed_is_zero_and_no_vendor_ratio(self, fmp_settings):
        """Never a fabricated ratio (CONSTRAINT #4) -- a zero denominator
        with no vendor-supplied ratio must degrade to NaN, not a divide."""
        payload = [{
            "symbol": "AAPL", "year": 2026, "quarter": 1,
            "totalAcquired": 3000.0, "totalDisposed": 0.0,
        }]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload=payload)):
            rows = fetch_insider_stats("AAPL")
        ratio = rows[0]["acquired_disposed_ratio"]
        assert ratio != ratio  # NaN

    def test_vendor_ratio_is_preferred_over_the_derived_one(self, fmp_settings):
        payload = [{
            "symbol": "AAPL", "year": 2026, "quarter": 1,
            "acquiredDisposedRatio": 0.9,
            "totalAcquired": 3000.0, "totalDisposed": 12000.0,  # would derive 0.25
        }]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload=payload)):
            rows = fetch_insider_stats("AAPL")
        assert rows[0]["acquired_disposed_ratio"] == pytest.approx(0.9)

    def test_partial_failure_a_malformed_row_is_dropped_valid_rows_kept(self, fmp_settings):
        """Partial failure: one row missing its primary-key fields among
        otherwise-valid rows is skipped, not a full wipe of the response."""
        payload = [
            {"symbol": "AAPL", "year": 2026, "quarter": 1, "acquiredDisposedRatio": 0.5},
            {"symbol": "AAPL", "quarter": 2},  # missing year -- dropped
        ]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload=payload)):
            rows = fetch_insider_stats("AAPL")
        assert len(rows) == 1
        assert rows[0]["quarter"] == 1

    def test_total_failure_transport_error_returns_empty_list_never_raises(self, fmp_settings):
        with patch(
            "data.fmp_client.requests.get", side_effect=ConnectionError("boom"),
        ) as get:
            rows = fetch_insider_stats("AAPL")  # must not raise
        assert rows == []
        assert get.call_count == 1  # a transport error is not retried

    def test_no_api_key_returns_empty_list_with_zero_network_calls(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        with patch("data.fmp_client.requests.get") as get:
            rows = fetch_insider_stats("AAPL")
        assert rows == []
        get.assert_not_called()

    def test_empty_symbol_returns_empty_list_with_zero_network_calls(self, fmp_settings):
        with patch("data.fmp_client.requests.get") as get:
            rows = fetch_insider_stats("")
        assert rows == []
        get.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────
# data/fmp_feeds_market.py::fetch_sector_snapshot
# ─────────────────────────────────────────────────────────────────────────

class TestFetchSectorSnapshot:
    def test_happy_path_merges_both_endpoints_by_sector(self, fmp_settings):
        pe_payload = [
            {"date": "2026-07-30", "sector": "Technology", "pe": 28.5},
            {"date": "2026-07-30", "sector": "Energy", "pe": 12.1},
        ]
        perf_payload = [
            {"date": "2026-07-30", "sector": "Technology", "averageChange": 1.23},
            {"date": "2026-07-30", "sector": "Energy", "averageChange": -0.5},
        ]
        with patch(
            "data.fmp_client.requests.get",
            side_effect=[_resp(200, payload=pe_payload), _resp(200, payload=perf_payload)],
        ) as get:
            rows = fetch_sector_snapshot("2026-07-30")

        assert get.call_count == 2
        by_sector = {r["sector"]: r for r in rows}
        assert by_sector["Technology"]["pe"] == pytest.approx(28.5)
        # averageChange is a vendor PERCENT NUMBER (live-verified); this
        # module converts it to the FRACTION every other "percent"-format
        # column in the schema stores -- 1.23% -> 0.0123, not 1.23.
        assert by_sector["Technology"]["change_pct"] == pytest.approx(0.0123)
        assert by_sector["Technology"]["date"] == "2026-07-30"
        assert by_sector["Technology"]["source"] == "fmp"
        assert by_sector["Energy"]["pe"] == pytest.approx(12.1)
        assert by_sector["Energy"]["change_pct"] == pytest.approx(-0.005)

    def test_always_calls_the_dated_endpoint_form_never_none_or_implicit(self, fmp_settings):
        with patch(
            "data.fmp_client.requests.get",
            side_effect=[_resp(200, payload=[]), _resp(200, payload=[])],
        ) as get:
            fetch_sector_snapshot("2026-07-30", exchange="NASDAQ")

        assert get.call_count == 2
        for call in get.call_args_list:
            assert call.kwargs["params"]["date"] == "2026-07-30"
            assert call.kwargs["params"]["date"] is not None
        assert get.call_args_list[0].args[0].endswith("/sector-pe-snapshot")
        assert get.call_args_list[1].args[0].endswith("/sector-performance-snapshot")

    def test_row_date_matches_the_source_snapshot_date_not_today(self, fmp_settings):
        """upsert_sector_snapshots must receive the SOURCE's own snapshot
        date, never datetime.now() -- a backfilled date that plainly differs
        from "today" must round-trip unchanged into every returned row."""
        backfill_date = "2020-01-15"
        assert backfill_date != date.today().isoformat()
        pe_payload = [{"date": backfill_date, "sector": "Technology", "pe": 20.0}]
        with patch(
            "data.fmp_client.requests.get",
            side_effect=[_resp(200, payload=pe_payload), _resp(200, payload=[])],
        ):
            rows = fetch_sector_snapshot(backfill_date)
        assert rows
        for row in rows:
            assert row["date"] == backfill_date

    def test_partial_failure_one_endpoint_down_still_returns_the_other(self, fmp_settings):
        pe_payload = [{"date": "2026-07-30", "sector": "Technology", "pe": 28.5}]
        with patch(
            "data.fmp_client.requests.get",
            side_effect=[_resp(200, payload=pe_payload), _resp(500)],
        ):
            rows = fetch_sector_snapshot("2026-07-30")
        assert len(rows) == 1
        assert rows[0]["sector"] == "Technology"
        assert rows[0]["pe"] == pytest.approx(28.5)
        assert rows[0]["change_pct"] != rows[0]["change_pct"]  # NaN, not fabricated

    def test_total_failure_both_endpoints_down_returns_empty_list(self, fmp_settings):
        with patch(
            "data.fmp_client.requests.get",
            side_effect=[_resp(500), _resp(500)],
        ):
            rows = fetch_sector_snapshot("2026-07-30")
        assert rows == []

    def test_empty_date_returns_empty_list_with_zero_network_calls(self, fmp_settings):
        with patch("data.fmp_client.requests.get") as get:
            rows = fetch_sector_snapshot("")
        assert rows == []
        get.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────
# data/fmp_feeds_market.py::fetch_realized_volatility
# (options-matrix FMP health overlay -- settings.FMP_OPTIONS_HEALTH_ENABLED,
# wired by reporting/options_snapshot.py::write_options_matrix, surfaced as
# the diagnostic-only Realized_Vol_30D passthrough field on build_premium_
# directive's row -- never used as an IVR-rank fallback, see
# technical_options_engine.py's build_premium_directive step-5 comment)
# ─────────────────────────────────────────────────────────────────────────

class TestFetchRealizedVolatility:
    def test_happy_path_maps_all_three_windows(self, fmp_settings):
        def _get(url, params=None, timeout=None):
            assert "standard-deviation" in url
            return _resp(payload=[{
                "stdDev10d": 0.31, "standardDeviation": 0.22, "stdDev90d": 0.19,
            }])

        with patch("data.fmp_client.requests.get", side_effect=_get):
            out = fetch_realized_volatility("aapl")

        assert out["hv_10"] == pytest.approx(0.31)
        assert out["hv_30"] == pytest.approx(0.22)
        assert out["hv_90"] == pytest.approx(0.19)

    def test_hv_30_falls_back_to_stddev30d_key_when_standarddeviation_absent(self, fmp_settings):
        with patch("data.fmp_client.requests.get", return_value=_resp(payload=[{"stdDev30d": 0.27}])):
            out = fetch_realized_volatility("MSFT")
        assert out["hv_30"] == pytest.approx(0.27)

    def test_empty_payload_degrades_to_all_nan_never_raises(self, fmp_settings):
        """This module's own ``_safe_float`` (unlike fmp_feeds_company.py's)
        returns NaN, not None, for an absent field -- matches the value the
        `Realized_Vol_30D` diagnostic column stores everywhere else."""
        with patch("data.fmp_client.requests.get", return_value=_resp(payload=[])):
            out = fetch_realized_volatility("NEWCO")
        assert out["hv_10"] != out["hv_10"]  # NaN
        assert out["hv_30"] != out["hv_30"]
        assert out["hv_90"] != out["hv_90"]

    def test_total_failure_no_api_key_degrades_to_none_never_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        out = fetch_realized_volatility("AAPL")
        assert out == {"hv_10": None, "hv_30": None, "hv_90": None}

    def test_transport_error_degrades_to_none_never_raises(self, fmp_settings):
        with patch("data.fmp_client.requests.get", side_effect=ConnectionError("boom")):
            out = fetch_realized_volatility("AAPL")
        assert out == {"hv_10": None, "hv_30": None, "hv_90": None}


# ─────────────────────────────────────────────────────────────────────────
# data/fmp_feeds_market.py::_pct_to_fraction
#
# Live-verified 2026-07-31: FMP's sector-performance-snapshot `averageChange`
# field is a PERCENT NUMBER (e.g. -2.79 for a -2.79% average sector move),
# not a fraction. This is the isolated unit-conversion logic that keeps
# Sector_1D_Change consistent with every other "percent"-format column in
# config.COLUMN_SCHEMA (which store fractions, per processing_engine.py's
# ROC_12M convention).
# ─────────────────────────────────────────────────────────────────────────

class TestPctToFraction:
    def test_typical_values_converted_to_fraction(self):
        assert _pct_to_fraction(1.23) == pytest.approx(0.0123)
        assert _pct_to_fraction(-2.79) == pytest.approx(-0.0279)
        assert _pct_to_fraction(0.0) == pytest.approx(0.0)

    def test_none_returns_nan(self):
        assert _pct_to_fraction(None) != _pct_to_fraction(None)  # NaN

    def test_unparseable_returns_nan(self):
        assert _pct_to_fraction("not-a-number") != _pct_to_fraction("not-a-number")

    def test_nan_input_passes_through_as_nan(self):
        result = _pct_to_fraction(float("nan"))
        assert result != result  # NaN

    def test_implausible_magnitude_refuses_and_logs_error(self, caplog):
        import logging
        with caplog.at_level(logging.ERROR, logger="data.fmp_feeds_market"):
            result = _pct_to_fraction(75.0)  # >50% -- a shape-change red flag
        assert result != result  # NaN, never a wrong-but-plausible-looking number
        assert any(
            r.levelno == logging.ERROR and "implausible sector change value" in r.message
            for r in caplog.records
        )

    def test_boundary_value_at_the_guard_threshold_still_converts(self):
        # Exactly at the threshold is still plausible; only strictly beyond
        # it is refused.
        assert _pct_to_fraction(50.0) == pytest.approx(0.5)
        assert _pct_to_fraction(-50.0) == pytest.approx(-0.5)

    def test_just_beyond_threshold_refuses(self):
        result = _pct_to_fraction(50.01)
        assert result != result  # NaN


# ─────────────────────────────────────────────────────────────────────────
# data/fmp_feeds_market.py::fetch_peer_group
#
# Backs the new on-demand GET /data/peers/{symbol} endpoint (api/data_api.py,
# settings.FMP_PEERS_ENABLED) AND the existing FMP_OPTIONS_CONTEXT_ENABLED
# options-matrix batch overlay (reporting/options_snapshot.py) -- this
# function itself had ZERO test coverage anywhere in the repo before this
# file, confirmed by grep. CONSTRAINT #6: `[]` on any failure, never raises.
# ─────────────────────────────────────────────────────────────────────────

class TestFetchPeerGroup:
    def test_happy_path_peers_list_shape(self, fmp_settings):
        payload = [{"symbol": "AAPL", "peersList": ["msft", "  googl  ", "amzn"]}]
        with patch(
            "data.fmp_client.requests.get", return_value=_resp(200, payload=payload),
        ) as get:
            result = fetch_peer_group("aapl")

        assert get.call_count == 1
        assert get.call_args.args[0].endswith("/peers")
        assert get.call_args.kwargs["params"]["symbol"] == "AAPL"
        assert result == ["MSFT", "GOOGL", "AMZN"]

    def test_happy_path_bare_list_of_strings_shape(self, fmp_settings):
        # Defensive fallback shape: a bare list of ticker strings with no
        # wrapping {"peersList": [...]} envelope.
        payload = ["msft", "googl", "  amzn  "]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload=payload)):
            result = fetch_peer_group("AAPL")
        assert result == ["MSFT", "GOOGL", "AMZN"]

    def test_malformed_peers_list_value_not_a_list_returns_empty(self, fmp_settings):
        payload = [{"symbol": "AAPL", "peersList": "not-a-list"}]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload=payload)):
            result = fetch_peer_group("AAPL")
        assert result == []

    def test_malformed_bare_list_with_non_string_entries_filters_them_out(self, fmp_settings):
        payload = ["msft", 123, None, {"nested": True}, "googl"]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload=payload)):
            result = fetch_peer_group("AAPL")
        assert result == ["MSFT", "GOOGL"]

    def test_empty_payload_returns_empty_list(self, fmp_settings):
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload=[])):
            result = fetch_peer_group("AAPL")
        assert result == []

    def test_unexpected_dict_payload_shape_returns_empty_list_never_raises(self, fmp_settings):
        payload = {"symbol": "AAPL", "note": "no peersList key at all"}
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload=payload)):
            result = fetch_peer_group("AAPL")
        assert result == []

    def test_transport_error_degrades_to_empty_list_never_raises(self, fmp_settings):
        with patch(
            "data.fmp_client.requests.get", side_effect=ConnectionError("boom"),
        ) as get:
            result = fetch_peer_group("AAPL")  # must not raise
        assert result == []
        assert get.call_count == 1  # a transport error is not retried

    def test_no_api_key_returns_empty_list_with_zero_network_calls(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        with patch("data.fmp_client.requests.get") as get:
            result = fetch_peer_group("AAPL")
        assert result == []
        get.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────
# pipeline/production_steps.py::_apply_fmp_insider
# ─────────────────────────────────────────────────────────────────────────

class TestApplyFmpInsiderGateOff:
    def test_gate_off_never_constructs_a_historical_store(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_INSIDER_ENABLED", False)
        df = pd.DataFrame({"Symbol": ["AAPL"]})
        with patch("data.historical_store.HistoricalStore") as MockStore:
            _apply_fmp_insider(df)
        MockStore.assert_not_called()
        assert df["Insider_Buy_Sell_Ratio"].isna().all()


class TestApplyFmpInsiderCadenceGate:
    def test_fresh_archive_skips_network_and_still_populates(self, real_store, monkeypatch):
        monkeypatch.setattr(settings, "FMP_INSIDER_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_INSIDER_REFRESH_DAYS", 7)
        # 2020 Q1 is guaranteed to be far more than any reasonable
        # FMP_INSIDER_MIN_LAG_DAYS in the past regardless of the real
        # wall-clock date the suite happens to run on.
        real_store.upsert_insider_stats([
            {"symbol": "AAPL", "year": 2020, "quarter": 1,
             "acquired_disposed_ratio": 0.55, "source": "fmp"},
        ])  # upsert stamps fetched_at = now -> fresh

        df = pd.DataFrame({"Symbol": ["AAPL"]})
        with patch("data.fmp_client.requests.get") as get:
            _apply_fmp_insider(df)

        get.assert_not_called()
        assert df["Insider_Buy_Sell_Ratio"].iloc[0] == pytest.approx(0.55)

    def test_stale_symbol_triggers_a_fetch_and_persists_it(self, real_store, monkeypatch):
        monkeypatch.setattr(settings, "FMP_INSIDER_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_INSIDER_REFRESH_DAYS", 7)
        stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        monkeypatch.setattr(real_store, "latest_insider_fetched_at", lambda sym: stale)

        df = pd.DataFrame({"Symbol": ["AAPL"]})
        with patch(
            "data.fmp_feeds_market.fetch_insider_stats",
            return_value=[{
                "symbol": "AAPL", "year": 2020, "quarter": 1,
                "acquired_disposed_ratio": 0.77, "source": "fmp",
            }],
        ) as mock_fetch:
            _apply_fmp_insider(df)

        mock_fetch.assert_called_once_with("AAPL")
        assert df["Insider_Buy_Sell_Ratio"].iloc[0] == pytest.approx(0.77)
        # And it was actually persisted, not just used in-memory this cycle.
        assert real_store.get_insider_stats("AAPL")


class TestApplyFmpInsiderMinimumLagFilter:
    """The critical test: the leakage trap is a minimum-LAG filter on when
    the quarter ENDED, not a filter on the row's own date. A quarter ending
    10 days ago must be excluded; one ending 100 days ago must be included
    -- using FMP_INSIDER_MIN_LAG_DAYS=45 explicitly (per the brief) rather
    than relying on the settings default, so this stays correct even if the
    default is ever changed."""

    def test_quarter_ending_10_days_ago_excluded_100_days_ago_included(
        self, real_store, monkeypatch,
    ):
        monkeypatch.setattr(settings, "FMP_INSIDER_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_INSIDER_MIN_LAG_DAYS", 45)
        monkeypatch.setattr(_datetime_module, "date", _FrozenDate)

        # _FrozenDate.today() == 2026-04-10.
        # Q1 2026 ends 2026-03-31 -> lag = 10 days -> EXCLUDED (10 < 45).
        # Q4 2025 ends 2025-12-31 -> lag = 100 days -> INCLUDED (100 >= 45).
        real_store.upsert_insider_stats([
            {"symbol": "AAPL", "year": 2026, "quarter": 1,
             "acquired_disposed_ratio": 9.99, "source": "fmp"},
            {"symbol": "AAPL", "year": 2025, "quarter": 4,
             "acquired_disposed_ratio": 0.42, "source": "fmp"},
        ])  # fresh archive (fetched_at stamped "now") -> no network needed

        df = pd.DataFrame({"Symbol": ["AAPL"]})
        with patch("data.fmp_client.requests.get") as get:
            _apply_fmp_insider(df)

        get.assert_not_called()
        assert df["Insider_Buy_Sell_Ratio"].iloc[0] == pytest.approx(0.42)

    def test_no_sufficiently_lagged_quarter_gives_nan_not_the_latest_unlagged_one(
        self, real_store, monkeypatch,
    ):
        monkeypatch.setattr(settings, "FMP_INSIDER_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_INSIDER_MIN_LAG_DAYS", 45)
        monkeypatch.setattr(_datetime_module, "date", _FrozenDate)

        # Only the 10-day-lag quarter exists -- must NOT fall back to it.
        real_store.upsert_insider_stats([
            {"symbol": "AAPL", "year": 2026, "quarter": 1,
             "acquired_disposed_ratio": 9.99, "source": "fmp"},
        ])

        df = pd.DataFrame({"Symbol": ["AAPL"]})
        with patch("data.fmp_client.requests.get") as get:
            _apply_fmp_insider(df)

        get.assert_not_called()
        ratio = df["Insider_Buy_Sell_Ratio"].iloc[0]
        assert ratio != ratio  # NaN, never the unlagged 9.99


class TestApplyFmpInsiderWallClockBudget:
    def test_budget_exhausted_stops_the_loop_leaves_remaining_symbols_nan(
        self, real_store, monkeypatch,
    ):
        monkeypatch.setattr(settings, "FMP_INSIDER_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_MAX_SECONDS_PER_CYCLE", 10.0)

        df = pd.DataFrame({"Symbol": ["AAA", "BBB", "CCC"]})

        # 1 call to establish the deadline, then one check per loop
        # iteration: AAA (in budget), BBB (in budget), CCC (over budget ->
        # break before doing any work for it). itertools.repeat pads the
        # tail so an incidental extra time.monotonic() call elsewhere never
        # raises StopIteration.
        monotonic_values = itertools.chain(
            [1000.0, 1000.0, 1005.0, 1020.0], itertools.repeat(1020.0),
        )
        with patch("time.monotonic", side_effect=monotonic_values), patch(
            "data.fmp_feeds_market.fetch_insider_stats", return_value=[],
        ) as mock_fetch:
            _apply_fmp_insider(df)

        assert mock_fetch.call_count == 2
        called_symbols = {c.args[0] for c in mock_fetch.call_args_list}
        assert called_symbols == {"AAA", "BBB"}
        assert df["Insider_Buy_Sell_Ratio"].isna().all()


# ─────────────────────────────────────────────────────────────────────────
# pipeline/production_steps.py::_apply_fmp_sector
# ─────────────────────────────────────────────────────────────────────────

class TestApplyFmpSectorGateOff:
    def test_gate_off_never_constructs_a_historical_store(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_SECTOR_SNAPSHOT_ENABLED", False)
        df = pd.DataFrame({"Symbol": ["AAPL"], "sector": ["Technology"]})
        with patch("data.historical_store.HistoricalStore") as MockStore:
            _apply_fmp_sector(df)
        MockStore.assert_not_called()
        assert df["Sector_PE"].isna().all()
        assert df["Sector_1D_Change"].isna().all()


class TestApplyFmpSectorCadenceGate:
    def test_fresh_archive_skips_network_and_still_populates(self, real_store, monkeypatch):
        monkeypatch.setattr(settings, "FMP_SECTOR_SNAPSHOT_ENABLED", True)
        real_store.upsert_sector_snapshots([
            {"sector": "Technology", "date": date.today().isoformat(),
             "pe": 28.5, "change_pct": 0.012, "source": "fmp"},
        ])

        df = pd.DataFrame({"Symbol": ["AAPL"], "sector": ["Technology"]})
        with patch("data.fmp_client.requests.get") as get:
            _apply_fmp_sector(df)

        get.assert_not_called()
        assert df["Sector_PE"].iloc[0] == pytest.approx(28.5)
        assert df["Sector_1D_Change"].iloc[0] == pytest.approx(0.012)

    def test_stale_archive_triggers_a_new_fetch_and_persists_it(self, real_store, monkeypatch):
        monkeypatch.setattr(settings, "FMP_SECTOR_SNAPSHOT_ENABLED", True)
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        real_store.upsert_sector_snapshots([
            {"sector": "Technology", "date": yesterday,
             "pe": 10.0, "change_pct": 0.01, "source": "fmp"},
        ])

        today_str = date.today().isoformat()
        df = pd.DataFrame({"Symbol": ["AAPL"], "sector": ["Technology"]})
        with patch(
            "data.fmp_feeds_market.fetch_sector_snapshot",
            return_value=[{
                "sector": "Technology", "date": today_str,
                "pe": 31.0, "change_pct": -0.02, "source": "fmp",
            }],
        ) as mock_fetch:
            _apply_fmp_sector(df)

        mock_fetch.assert_called_once_with(today_str)
        assert df["Sector_PE"].iloc[0] == pytest.approx(31.0)
        assert df["Sector_1D_Change"].iloc[0] == pytest.approx(-0.02)
        # And it was actually persisted, keyed by the source's own date.
        assert real_store.get_sector_snapshots()["Technology"]["date"] == today_str


class TestApplyFmpSectorUnknownSector:
    def test_missing_sector_gets_nan_never_a_fallback(self, real_store, monkeypatch):
        monkeypatch.setattr(settings, "FMP_SECTOR_SNAPSHOT_ENABLED", True)
        real_store.upsert_sector_snapshots([
            {"sector": "Technology", "date": date.today().isoformat(),
             "pe": 28.5, "change_pct": 0.012, "source": "fmp"},
        ])

        df = pd.DataFrame({
            "Symbol": ["AAPL", "ZZZ"],
            "sector": ["Technology", None],
        })
        with patch("data.fmp_client.requests.get") as get:
            _apply_fmp_sector(df)

        get.assert_not_called()
        assert df["Sector_PE"].iloc[0] == pytest.approx(28.5)
        assert pd.isna(df["Sector_PE"].iloc[1])
        assert pd.isna(df["Sector_1D_Change"].iloc[1])

    def test_sector_absent_from_the_snapshot_gets_nan_never_a_universe_average(
        self, real_store, monkeypatch,
    ):
        monkeypatch.setattr(settings, "FMP_SECTOR_SNAPSHOT_ENABLED", True)
        real_store.upsert_sector_snapshots([
            {"sector": "Technology", "date": date.today().isoformat(),
             "pe": 28.5, "change_pct": 0.012, "source": "fmp"},
        ])

        df = pd.DataFrame({"Symbol": ["XOM"], "sector": ["Energy"]})
        with patch("data.fmp_client.requests.get") as get:
            _apply_fmp_sector(df)

        get.assert_not_called()
        assert pd.isna(df["Sector_PE"].iloc[0])
        assert pd.isna(df["Sector_1D_Change"].iloc[0])


class TestFetchEconomicsCalendar:
    def test_fetch_economics_calendar_happy_path(self):
        raw_payload = [
            {
                "event": "CPI MoM",
                "date": "2026-08-20 08:30:00",
                "country": "US",
                "actual": 0.2,
                "estimate": 0.2,
                "impact": "High",
            },
            {
                "event": "Non Farm Payrolls",
                "date": "2026-09-04 08:30:00",
                "country": "US",
                "actual": None,
                "estimate": 180000.0,
                "impact": "High",
            },
        ]
        with patch("data.fmp_client.economics_calendar", return_value=raw_payload):
            events = fetch_economics_calendar(from_date="2026-08-01")

        assert len(events) == 2
        assert events[0]["event"] == "CPI MoM"
        assert events[0]["country"] == "US"
        assert events[0]["actual"] == pytest.approx(0.2)
        assert events[1]["event"] == "Non Farm Payrolls"

    def test_fetch_economics_calendar_empty_and_error(self):
        with patch("data.fmp_client.economics_calendar", return_value=[]):
            assert fetch_economics_calendar() == []

        with patch("data.fmp_client.economics_calendar", side_effect=RuntimeError("timeout")):
            assert fetch_economics_calendar() == []


class TestApplyFmpEconCalendar:
    def test_gate_off_is_noop(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_ECON_CALENDAR_ENABLED", False)
        df = pd.DataFrame({"Symbol": ["AAPL", "MSFT"]})

        with patch("data.fmp_feeds_market.fetch_economics_calendar") as mock_fetch:
            _apply_fmp_econ_calendar(df)

        mock_fetch.assert_not_called()
        assert pd.isna(df["Next_Macro_Event"].iloc[0])
        assert pd.isna(df["Next_Macro_Event_Date"].iloc[0])

    def test_gate_on_broadcasts_upcoming_event(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_ECON_CALENDAR_ENABLED", True)
        df = pd.DataFrame({"Symbol": ["AAPL", "MSFT"]})

        fake_events = [
            {
                "event": "FOMC Rate Decision",
                "date": "2026-09-16 14:00:00",
                "country": "US",
                "impact": "High",
            },
            {
                "event": "Initial Jobless Claims",
                "date": "2026-08-20 08:30:00",
                "country": "US",
                "impact": "High",
            },
        ]
        with patch("data.fmp_feeds_market.fetch_economics_calendar", return_value=fake_events):
            _apply_fmp_econ_calendar(df)

        # Earliest US/High impact event is Initial Jobless Claims on 2026-08-20
        assert df["Next_Macro_Event"].iloc[0] == "Initial Jobless Claims"
        assert df["Next_Macro_Event_Date"].iloc[0] == "2026-08-20"
        assert df["Next_Macro_Event"].iloc[1] == "Initial Jobless Claims"
        assert df["Next_Macro_Event_Date"].iloc[1] == "2026-08-20"

    def test_gate_on_empty_events_leaves_nan(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_ECON_CALENDAR_ENABLED", True)
        df = pd.DataFrame({"Symbol": ["AAPL"]})

        with patch("data.fmp_feeds_market.fetch_economics_calendar", return_value=[]):
            _apply_fmp_econ_calendar(df)

        assert pd.isna(df["Next_Macro_Event"].iloc[0])
        assert pd.isna(df["Next_Macro_Event_Date"].iloc[0])

