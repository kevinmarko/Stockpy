"""
tests/test_fmp_feeds_company.py
================================
Tests for ``data/fmp_feeds_company.py`` (the pure fetch+shape layer for FMP's
per-symbol analyst-consensus and earnings-calendar feeds) and the two
``pipeline/production_steps.py`` writers it feeds — ``_apply_fmp_analyst`` /
``_apply_fmp_earnings``.

Mocking convention matches the rest of the FMP test series (see
``tests/test_fmp_client.py``): ``patch("data.fmp_client.requests.get", ...)``
and ``monkeypatch.setattr(settings, "X", ...)``, never
``patch.dict(os.environ)``, no ``responses``/VCR. Offline; no marks. The root
``conftest.py``'s ``_no_fmp_throttle_in_tests`` autouse fixture zeroes the FMP
client's throttle/backoff and resets its module-level breaker state before
and after every test, so these run with zero real sleeping.

The integration tests (``TestApplyFmpAnalystIntegration`` /
``TestApplyFmpEarningsIntegration``) exercise the FULL round trip — fetch (
mocked at the ``data.fmp_feeds_company.fetch_*`` boundary, one layer above
the raw HTTP) → persist via a REAL temp-file ``HistoricalStore`` → read back
→ write onto ``dashboard_df`` — because the lookahead rules this series must
prove (CLAUDE.md's earnings four-rule contract) are enforced by the
INTERACTION between the writer's query shape and the store's SQL filters, not
by either half alone. Injected the same way ``tests/test_etf_holdings.py``
injects a store into ``get_etf_holdings`` for its cacheless-failure case:
patching the ``data.historical_store.HistoricalStore`` name itself, since
``_apply_fmp_analyst``/``_apply_fmp_earnings`` construct their own store
internally (no ``store=`` parameter on these two functions).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.fmp_feeds_company import (
    _derive_grade_score,
    _safe_float,
    fetch_analyst_snapshot,
    fetch_earnings_rows,
)
from data.historical_store import HistoricalStore
from pipeline.production_steps import _apply_fmp_analyst, _apply_fmp_earnings
from settings import settings


def _resp(payload) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = payload
    return resp


def _access_denied_resp() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 403
    resp.headers = {}
    resp.json.return_value = {"Error Message": "Exclusive Endpoint"}
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestSafeFloat:
    def test_none_is_none_never_zero(self):
        assert _safe_float(None) is None

    def test_real_zero_round_trips_as_zero_not_none(self):
        """CONSTRAINT #4's OTHER direction: a genuine 0 in the payload (e.g.
        break-even guided EPS) must not be conflated with "not reported"."""
        assert _safe_float(0) == 0.0
        assert _safe_float("0") == 0.0

    def test_uncoercible_value_is_none(self):
        assert _safe_float("not-a-number") is None
        assert _safe_float(object()) is None

    def test_nan_and_inf_are_none(self):
        assert _safe_float(float("nan")) is None
        assert _safe_float(float("inf")) is None


class TestDeriveGradeScore:
    def test_typical_mix(self):
        record = {"strongBuy": 10, "buy": 15, "hold": 5, "sell": 2, "strongSell": 1}
        # (10 + 15 - 2 - 1) / (10+15+5+2+1) = 22 / 33
        assert _derive_grade_score(record) == pytest.approx(22 / 33)

    def test_all_zero_counts_is_none_not_a_fabricated_neutral_score(self):
        record = {"strongBuy": 0, "buy": 0, "hold": 0, "sell": 0, "strongSell": 0}
        assert _derive_grade_score(record) is None

    def test_missing_fields_entirely_is_none(self):
        assert _derive_grade_score({}) is None
        assert _derive_grade_score({"consensus": "Buy"}) is None


# ─────────────────────────────────────────────────────────────────────────────
# fetch_analyst_snapshot
# ─────────────────────────────────────────────────────────────────────────────


class TestFetchAnalystSnapshot:
    def test_happy_path(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")

        def _get(url, params=None, timeout=None):
            if "price-target-consensus" in url:
                return _resp([{
                    "symbol": "AAPL", "targetConsensus": 220.0,
                    "targetMedian": 215.0, "targetHigh": 260.0, "targetLow": 180.0,
                }])
            if "grades-summary" in url:
                return _resp([{
                    "symbol": "AAPL", "strongBuy": 10, "buy": 15, "hold": 5,
                    "sell": 2, "strongSell": 1,
                }])
            raise AssertionError(f"unexpected FMP path in {url!r}")

        with patch("data.fmp_client.requests.get", side_effect=_get):
            out = fetch_analyst_snapshot("aapl")

        assert out["target_consensus"] == pytest.approx(220.0)
        assert out["target_median"] == pytest.approx(215.0)
        assert out["target_high"] == pytest.approx(260.0)
        assert out["target_low"] == pytest.approx(180.0)
        assert out["grade_score"] == pytest.approx(22 / 33)
        assert out["source"] == "fmp"

    def test_partial_failure_grades_endpoint_down_still_returns_targets(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")

        def _get(url, params=None, timeout=None):
            if "price-target-consensus" in url:
                return _resp([{"targetConsensus": 100.0}])
            if "grades-summary" in url:
                return _access_denied_resp()
            raise AssertionError(url)

        with patch("data.fmp_client.requests.get", side_effect=_get):
            out = fetch_analyst_snapshot("XOM")

        assert out["target_consensus"] == pytest.approx(100.0)
        assert out["grade_score"] is None  # degraded, not fabricated
        assert out["source"] == "fmp"

    def test_partial_failure_targets_endpoint_down_still_returns_grades(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")

        def _get(url, params=None, timeout=None):
            if "price-target-consensus" in url:
                return _access_denied_resp()
            if "grades-summary" in url:
                return _resp([{"strongBuy": 1, "buy": 1, "hold": 0, "sell": 0, "strongSell": 0}])
            raise AssertionError(url)

        with patch("data.fmp_client.requests.get", side_effect=_get):
            out = fetch_analyst_snapshot("KO")

        assert out["target_consensus"] is None
        assert out["grade_score"] == pytest.approx(1.0)

    def test_total_failure_no_api_key_returns_empty_dict_never_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        assert fetch_analyst_snapshot("AAPL") == {}

    def test_total_failure_both_endpoints_empty_returns_empty_dict(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")
        with patch("data.fmp_client.requests.get", return_value=_resp([])):
            assert fetch_analyst_snapshot("AAPL") == {}


# ─────────────────────────────────────────────────────────────────────────────
# fetch_earnings_rows
# ─────────────────────────────────────────────────────────────────────────────


class TestFetchEarningsRows:
    def test_happy_path(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")
        payload = [
            {
                "date": "2026-05-01", "epsActual": 1.23, "epsEstimated": 1.10,
                "revenueActual": 1000.0, "revenueEstimated": 950.0,
                "lastUpdated": "2026-05-02T00:00:00+00:00",
            },
            {
                "date": "2026-08-01", "epsActual": None, "epsEstimated": 1.30,
                "revenueActual": None, "revenueEstimated": 1100.0,
                "lastUpdated": "2026-07-01T00:00:00+00:00",
            },
        ]
        with patch("data.fmp_client.requests.get", return_value=_resp(payload)):
            rows = fetch_earnings_rows("aapl")

        assert len(rows) == 2
        assert rows[0]["symbol"] == "AAPL"
        assert rows[0]["event_date"] == "2026-05-01"
        assert rows[0]["eps_actual"] == pytest.approx(1.23)
        assert rows[0]["source"] == "fmp"
        # A null actual on a future row is normal and expected -- NEVER 0.0.
        assert rows[1]["eps_actual"] is None

    def test_partial_failure_bad_rows_are_skipped_not_fatal(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")
        payload = [
            {"date": "2026-05-01", "epsActual": 1.0, "epsEstimated": 1.0},
            "not-a-dict",
            {"epsActual": 2.0},  # missing date -> no anchor, skipped
        ]
        with patch("data.fmp_client.requests.get", return_value=_resp(payload)):
            rows = fetch_earnings_rows("AAPL")
        assert len(rows) == 1
        assert rows[0]["event_date"] == "2026-05-01"

    def test_total_failure_no_api_key_returns_empty_list_never_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        assert fetch_earnings_rows("AAPL") == []

    def test_total_failure_non_list_payload_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")
        with patch("data.fmp_client.requests.get", return_value=_resp({"Error Message": "bad symbol"})):
            assert fetch_earnings_rows("ZZZZ") == []

    def test_limit_is_forwarded(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")
        captured = {}

        def _get(url, params=None, timeout=None):
            captured["params"] = params
            return _resp([])

        with patch("data.fmp_client.requests.get", side_effect=_get):
            fetch_earnings_rows("AAPL", limit=4)
        assert captured["params"]["limit"] == 4


# ─────────────────────────────────────────────────────────────────────────────
# _apply_fmp_analyst / _apply_fmp_earnings integration (real temp-file store)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def real_store(tmp_path, monkeypatch):
    """A real, temp-file-backed ``HistoricalStore`` wired into the module the
    two writers import lazily. Both functions do
    ``from data.historical_store import HistoricalStore`` INSIDE their body
    and then call ``HistoricalStore()`` with no arguments, so patching the
    module attribute (not a function parameter -- neither writer accepts a
    ``store=`` injection point) redirects that zero-arg construction to this
    fixture's instance, matching ``tests/test_etf_holdings.py``'s
    ``mock.patch("data.historical_store.HistoricalStore", ...)`` idiom.
    """
    store = HistoricalStore(db_path=str(tmp_path / "fmp_feeds_company.db"))
    monkeypatch.setattr("data.historical_store.HistoricalStore", lambda *a, **kw: store)
    return store


class TestApplyFmpAnalystIntegration:
    def _df(self):
        return pd.DataFrame([
            {"Symbol": "AAPL", "Price": 200.0},   # full coverage
            {"Symbol": "XOM", "Price": 0.0},      # Price <= 0 -> upside NaN
            {"Symbol": "KO", "Price": 130.0},     # no analyst coverage at all
        ])

    def test_fetch_persists_and_populates_columns(self, monkeypatch, real_store):
        monkeypatch.setattr(settings, "FMP_ANALYST_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_ANALYST_REFRESH_HOURS", 24)
        monkeypatch.setattr(settings, "FMP_MAX_SECONDS_PER_CYCLE", 120.0)

        def _fake_fetch(sym):
            if sym == "AAPL":
                return {
                    "target_consensus": 220.0, "target_median": 215.0,
                    "target_high": 250.0, "target_low": 180.0,
                    "grade_score": 0.5, "source": "fmp",
                }
            if sym == "XOM":
                return {
                    "target_consensus": 100.0, "target_median": None,
                    "target_high": None, "target_low": None,
                    "grade_score": None, "source": "fmp",
                }
            return {}  # KO: no coverage this cycle

        df = self._df()
        with patch("data.fmp_feeds_company.fetch_analyst_snapshot", side_effect=_fake_fetch):
            _apply_fmp_analyst(df)

        row = df.set_index("Symbol")
        assert row.loc["AAPL", "Analyst_Target_Consensus"] == pytest.approx(220.0)
        assert row.loc["AAPL", "Analyst_Grade_Score"] == pytest.approx(0.5)
        assert row.loc["AAPL", "Analyst_Target_Upside"] == pytest.approx(220.0 / 200.0 - 1.0)

        # Price <= 0 -> upside NaN even though a consensus target exists.
        assert row.loc["XOM", "Analyst_Target_Consensus"] == pytest.approx(100.0)
        assert pd.isna(row.loc["XOM", "Analyst_Target_Upside"])

        # No coverage at all -> every column NaN despite a perfectly valid Price.
        assert pd.isna(row.loc["KO", "Analyst_Target_Consensus"])
        assert pd.isna(row.loc["KO", "Analyst_Target_Upside"])

        # Archived for the cadence gate to find next cycle.
        snap = real_store.get_analyst_snapshot("AAPL")
        assert snap["target_consensus"] == pytest.approx(220.0)
        assert snap["source"] == "fmp"

    def test_cadence_gate_skips_fetch_and_serves_the_archive(self, monkeypatch, real_store):
        monkeypatch.setattr(settings, "FMP_ANALYST_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_ANALYST_REFRESH_HOURS", 24)
        today = datetime.now(timezone.utc).date().isoformat()
        real_store.upsert_analyst_snapshot(
            "AAPL", today, target_consensus=199.0, grade_score=0.2, source="fmp",
        )

        df = pd.DataFrame([{"Symbol": "AAPL", "Price": 200.0}])
        with patch("data.fmp_client.requests.get") as mock_get, \
             patch("data.fmp_feeds_company.fetch_analyst_snapshot") as mock_fetch:
            _apply_fmp_analyst(df)

        mock_get.assert_not_called()
        mock_fetch.assert_not_called()
        assert df.loc[0, "Analyst_Target_Consensus"] == pytest.approx(199.0)
        assert df.loc[0, "Analyst_Grade_Score"] == pytest.approx(0.2)


class TestApplyFmpEarningsIntegration:
    def test_lookahead_future_actual_never_leaks_into_surprise(self, monkeypatch, real_store):
        """The critical test: a vendor bug that populates ``epsActual`` on a
        FUTURE-dated row must never contaminate the trailing surprise, and
        the next-scheduled-date read must resolve to the correctly
        null-actual future row, not the buggy one."""
        monkeypatch.setattr(settings, "FMP_EARNINGS_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_EARNINGS_REFRESH_HOURS", 12)
        monkeypatch.setattr(settings, "FMP_MAX_SECONDS_PER_CYCLE", 120.0)

        today = datetime.now(timezone.utc).date()
        as_of = today.isoformat()
        past_date = (today - timedelta(days=90)).isoformat()
        near_future = (today + timedelta(days=5)).isoformat()   # (i) correct
        far_future = (today + timedelta(days=15)).isoformat()   # (ii) vendor bug

        fake_rows = [
            {
                "symbol": "AAPL", "event_date": past_date,
                "eps_actual": 1.10, "eps_estimated": 1.00,
                "revenue_actual": None, "revenue_estimated": None,
                "last_updated": "2026-01-01T00:00:00+00:00", "source": "fmp",
            },
            {
                # (i) future-dated, null actual -- the correct, expected shape.
                "symbol": "AAPL", "event_date": near_future,
                "eps_actual": None, "eps_estimated": 1.20,
                "revenue_actual": None, "revenue_estimated": None,
                "last_updated": "2026-07-01T00:00:00+00:00", "source": "fmp",
            },
            {
                # (ii) future-dated, but a vendor bug populated an actual.
                "symbol": "AAPL", "event_date": far_future,
                "eps_actual": 99.0, "eps_estimated": 1.30,
                "revenue_actual": None, "revenue_estimated": None,
                "last_updated": "2026-07-01T00:00:00+00:00", "source": "fmp",
            },
        ]

        df = pd.DataFrame([{"Symbol": "AAPL", "Price": 200.0, "Earnings_Date": ""}])
        with patch("data.fmp_feeds_company.fetch_earnings_rows", return_value=fake_rows):
            _apply_fmp_earnings(df)

        # Surprise uses ONLY the past actual: (1.10 - 1.00) / 1.00 = 0.10.
        # Neither future row (i) nor (ii) contributes -- if row (ii)'s bogus
        # actual had leaked, this would instead be (99.0-1.30)/1.30 ~= 75.15.
        assert df.loc[0, "Last_EPS_Surprise_Pct"] == pytest.approx(0.10)

        # Next date resolves to (i), the correctly null-actual future row.
        assert df.loc[0, "Earnings_Date"] == near_future
        assert df.loc[0, "Days_To_Earnings"] == pytest.approx(5.0)

    def test_earnings_date_preserved_when_fmp_has_no_coverage(self, monkeypatch, real_store):
        """A symbol with an existing Finnhub-sourced date and zero FMP
        coverage this cycle keeps its original date unchanged -- a NaN/absent
        FMP row must never blank a date the news-catalyst path resolved."""
        monkeypatch.setattr(settings, "FMP_EARNINGS_ENABLED", True)

        df = pd.DataFrame([{"Symbol": "AAPL", "Price": 200.0, "Earnings_Date": "2026-08-15"}])
        with patch("data.fmp_feeds_company.fetch_earnings_rows", return_value=[]):
            _apply_fmp_earnings(df)

        assert df.loc[0, "Earnings_Date"] == "2026-08-15"
        assert pd.isna(df.loc[0, "Days_To_Earnings"])
        assert pd.isna(df.loc[0, "Last_EPS_Surprise_Pct"])

    def test_cadence_gate_skips_fetch_and_serves_the_archive(self, monkeypatch, real_store):
        monkeypatch.setattr(settings, "FMP_EARNINGS_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_EARNINGS_REFRESH_HOURS", 12)
        near_future = (datetime.now(timezone.utc).date() + timedelta(days=5)).isoformat()
        real_store.upsert_earnings_events([{
            "symbol": "AAPL", "event_date": near_future,
            "eps_actual": None, "eps_estimated": 1.2,
            "revenue_actual": None, "revenue_estimated": None,
            "last_updated": "2026-07-01T00:00:00+00:00", "source": "fmp",
        }])

        df = pd.DataFrame([{"Symbol": "AAPL", "Price": 200.0, "Earnings_Date": ""}])
        with patch("data.fmp_client.requests.get") as mock_get, \
             patch("data.fmp_feeds_company.fetch_earnings_rows") as mock_fetch:
            _apply_fmp_earnings(df)

        mock_get.assert_not_called()
        mock_fetch.assert_not_called()
        assert df.loc[0, "Days_To_Earnings"] == pytest.approx(5.0)
        assert df.loc[0, "Earnings_Date"] == near_future
