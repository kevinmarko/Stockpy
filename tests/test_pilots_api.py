"""
tests/test_pilots_api.py
=========================
Tests for the standalone ``api/pilots_api.py`` FastAPI service (port 8602) —
the read/follow API backing the Autopilot "Pilots" marketplace PWA.

All read tests point the snapshot loader at the checked-in fixture snapshot
(``tests/fixtures/state_snapshot.json``) by monkeypatching
``settings.OUTPUT_DIR`` (mirroring ``tests/test_state_api.py``), and the
performance loader at ``tests/fixtures`` by monkeypatching
``pilots_api._reports_dir``. Follow-write tests use a ``tmp_path`` OUTPUT_DIR so
``FollowsStore`` never writes into the repo, and patch ``HistoricalStore`` /
``GlobalKillSwitch`` on the module for account-snapshot / kill-switch state.
"""

from __future__ import annotations

import ast
import pathlib
from unittest import mock

import pytest
import pandas as pd
from fastapi.testclient import TestClient

from settings import settings
import api.pilots_api as pilots_api

client = TestClient(pilots_api.app)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
_SNAPSHOT_FIXTURE = (FIXTURES / "state_snapshot.json").read_text(encoding="utf-8")

_CMD_TOKEN = "cmd-tok"


def _point_reports_at_fixtures(monkeypatch):
    monkeypatch.setattr(pilots_api, "_reports_dir", lambda: str(FIXTURES))


# ---------------------------------------------------------------------------
# /health — always open
# ---------------------------------------------------------------------------


def test_health_open_no_auth():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_open_even_when_tokens_set():
    with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", "cmd-tok"):
            resp = client.get("/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /pilots — marketplace list
# ---------------------------------------------------------------------------


def test_pilots_list_shape(monkeypatch):
    _point_reports_at_fixtures(monkeypatch)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/pilots")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and data

    tf = next(p for p in data if p["id"] == "trend-following")
    # long_only is part of the PilotSummary contract (webapp types.ts) — the live
    # cutover needs it on every list item, so it's an exact key of the response.
    assert set(tf.keys()) == {
        "id", "name", "category", "description",
        "headline", "holdings_count", "aum_proxy", "followers_proxy",
        "long_only",
    }
    assert tf["long_only"] is False
    # Headline comes from tests/fixtures/timeseries_momentum_validation_summary.json.
    assert tf["headline"]["sharpe"] == 1.14
    assert tf["headline"]["deployable"] is True
    # trend-following weights timeseries_momentum; the fixture snapshot has 5
    # names with a positive timeseries_momentum contribution.
    assert tf["holdings_count"] == 5
    assert tf["aum_proxy"] == 0.0
    assert tf["followers_proxy"] == 0


def test_pilots_list_headline_null_when_no_backtest(monkeypatch):
    _point_reports_at_fixtures(monkeypatch)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/pilots")
    data = resp.json()
    # cross-sectional-momentum has validation_strategy_id=None -> honest nulls.
    csm = next(p for p in data if p["id"] == "cross-sectional-momentum")
    assert csm["headline"] == {
        "sharpe": None, "dsr": None, "pbo": None,
        "max_drawdown": None, "deployable": None,
    }


def test_pilots_list_holdings_count_zero_without_snapshot(tmp_path, monkeypatch):
    _point_reports_at_fixtures(monkeypatch)
    # tmp_path has no state_snapshot.json -> list still returns (never 404).
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/pilots")
    assert resp.status_code == 200
    for p in resp.json():
        assert p["holdings_count"] == 0


# ---------------------------------------------------------------------------
# GET /pilots/{id} — detail
# ---------------------------------------------------------------------------


def test_pilot_detail_shape(monkeypatch):
    _point_reports_at_fixtures(monkeypatch)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/pilots/trend-following")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "trend-following"
    assert body["validation_strategy_id"] == "timeseries_momentum"
    assert isinstance(body["weights"], dict)
    # PilotDetail extends PilotSummary — detail must carry the summary proxies +
    # long_only so the live frontend type is satisfied (Mismatch 3).
    assert body["long_only"] is False
    assert body["holdings_count"] == 5
    assert body["aum_proxy"] == 0.0
    assert body["followers_proxy"] == 0
    assert len(body["holdings"]) == 5
    assert body["holdings"][0]["symbol"]  # each holding carries a symbol
    assert isinstance(body["sector_allocation"], list) and body["sector_allocation"]
    assert body["headline"]["sharpe"] == 1.14
    assert body["as_of"] == "2026-07-11T21:05:00+00:00"
    # No rotated history in fixtures -> no fabricated trades.
    assert body["recent_trades"] == []
    assert body["reason"] is None


def test_pilot_detail_unknown_404():
    resp = client.get("/pilots/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No such pilot."


def test_pilot_detail_cold_start_empty_but_not_404(tmp_path, monkeypatch):
    _point_reports_at_fixtures(monkeypatch)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/pilots/trend-following")
    assert resp.status_code == 200
    body = resp.json()
    assert body["holdings"] == []
    assert body["sector_allocation"] == []
    assert body["recent_trades"] == []
    assert body["as_of"] is None
    assert body["reason"] == "No state snapshot yet — run the pipeline first."


# ---------------------------------------------------------------------------
# GET /symbols/{ticker} — symbol detail
# ---------------------------------------------------------------------------


def test_symbol_detail_shape_and_values():
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["as_of"] == "2026-07-11T21:05:00+00:00"
    assert body["reason"] is None
    assert set(body) == {
        "symbol", "as_of", "reason",
        "identity", "advisory", "factors", "ranges", "risk", "held_by_pilots",
    }
    assert body["identity"] == {
        "sector": "Information Technology", "price": 224.15, "action": "BUY", "shares": 40.0,
    }
    assert body["advisory"]["conviction"] == 0.72
    assert body["advisory"]["score"] == 96.8
    assert body["ranges"]["buy_range"] == "Buy Zone: $210.00 - $222.00"
    # Honesty: fields absent from the advisory fixture serialize to null, never 0.0.
    for k in ("mfe", "mae", "edge_ratio", "macro_status"):
        assert body["risk"][k] is None
    for k in ("xsec_12_1m", "xsec_momentum_rank"):
        assert body["factors"][k] is None
    # Reverse cross-link: AAPL is held by trend-following; deep-value excluded.
    held_ids = {p["pilot_id"] for p in body["held_by_pilots"]}
    assert "trend-following" in held_ids
    assert "deep-value" not in held_ids
    assert body["held_by_pilots"]  # non-empty
    for p in body["held_by_pilots"]:
        assert set(p) == {"pilot_id", "name", "weight"}


def test_symbol_detail_case_insensitive():
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/aapl")
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "AAPL"


def test_symbol_detail_unknown_404():
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/ZZZ")
    assert resp.status_code == 404
    assert resp.json()["detail"] == pilots_api._UNKNOWN_SYMBOL_DETAIL


def test_symbol_detail_cold_start_404(tmp_path):
    # tmp_path has no state_snapshot.json → honest cold-start 404 (distinct detail).
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/symbols/AAPL")
    assert resp.status_code == 404
    assert resp.json()["detail"] == pilots_api._MISSING_SNAPSHOT_DETAIL


# ---------------------------------------------------------------------------
# GET /pilots/{id}/performance
# ---------------------------------------------------------------------------


def test_performance_good_range(monkeypatch):
    _point_reports_at_fixtures(monkeypatch)
    resp = client.get("/pilots/trend-following/performance?range=2Y")
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"] == "2Y"
    assert body["metrics"]["sharpe"] == 1.14
    # The fixture summary carries a persisted equity_curve -> a real curve serves,
    # tail-sliced to the range, {date, value} shaped (never fabricated).
    curve = body["curve"]
    assert isinstance(curve, list) and len(curve) >= 2
    assert all(set(p) == {"date", "value"} for p in curve)
    # The fixture also carries a persisted macro_benchmark_curve (SPY) -> a real,
    # separately-labeled market overlay is serialized alongside curve/benchmark.
    macro = body["macro_benchmark"]
    assert isinstance(macro, list) and len(macro) >= 2
    assert all(set(p) == {"date", "value"} for p in macro)
    assert body["reason"] is None


def test_performance_curve_null_for_pilot_without_backtest(monkeypatch):
    """A Pilot whose validation_strategy_id is None honestly reports curve=null."""
    _point_reports_at_fixtures(monkeypatch)
    resp = client.get("/pilots/balanced-blend/performance?range=1M")
    assert resp.status_code == 200
    body = resp.json()
    assert body["curve"] is None
    assert body["metrics"] is None
    assert body["reason"]  # honest explanation present


def test_performance_bad_range_422():
    resp = client.get("/pilots/trend-following/performance?range=5Y")
    assert resp.status_code == 422
    assert "Invalid range" in resp.json()["detail"]


def test_performance_unknown_pilot_404():
    resp = client.get("/pilots/nope/performance?range=1M")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /pilots/{id}/holdings & /trades
# ---------------------------------------------------------------------------


def test_holdings_endpoint(monkeypatch):
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/pilots/trend-following/holdings")
    assert resp.status_code == 200
    assert len(resp.json()) == 5


def test_holdings_unknown_404():
    resp = client.get("/pilots/nope/holdings")
    assert resp.status_code == 404


def test_holdings_empty_without_snapshot(tmp_path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/pilots/trend-following/holdings")
    assert resp.status_code == 200
    assert resp.json() == []


def test_trades_endpoint_empty_without_history(tmp_path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/pilots/trend-following/trades?limit=5")
    assert resp.status_code == 200
    assert resp.json() == []


def test_trades_unknown_404():
    resp = client.get("/pilots/nope/trades")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /portfolio & /portfolio/equity-curve
# ---------------------------------------------------------------------------


def test_portfolio_honest_404_on_empty_db():
    class _EmptyStore:
        def latest_account_snapshot(self):
            return None

    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_EmptyStore()):
        resp = client.get("/portfolio")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No account snapshot yet — run the pipeline first."


def test_portfolio_404_on_db_error():
    class _BoomStore:
        def latest_account_snapshot(self):
            raise RuntimeError("cold db")

    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_BoomStore()):
        resp = client.get("/portfolio")
    assert resp.status_code == 404


def test_portfolio_serializes_snapshot():
    class _FakeSnap:
        def to_dict(self):
            return {"positions": {}, "buying_power": 500.0, "total_equity": 1500.0,
                    "total_dividends": 12.0, "fetched_at": "2026-07-12T00:00:00+00:00"}

        def is_stale(self):
            return False

        def age_hours(self):
            return 1.5

    class _Store:
        def latest_account_snapshot(self):
            return _FakeSnap()

    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
        resp = client.get("/portfolio")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_equity"] == 1500.0
    assert body["is_stale"] is False
    assert body["age_hours"] == 1.5


def test_portfolio_matches_frontend_contract():
    """The /portfolio response must satisfy the webapp Portfolio /
    PortfolioPositionView type (Mismatch 4): positions is a LIST with
    qty/avg_cost field names, plus derived position_count/total_unrealized_pl
    and an honest source tag."""

    class _FakeSnap:
        def to_dict(self):
            return {
                "positions": {
                    "AAPL": {
                        "symbol": "AAPL", "quantity": 10.0, "average_cost": 100.0,
                        "current_price": 120.0, "market_value": 1200.0,
                        "unrealized_pl": 200.0, "unrealized_pl_pct": 20.0,
                        "dividends_received": 5.0, "name": "Apple",
                    },
                    "MSFT": {
                        "symbol": "MSFT", "quantity": 4.0, "average_cost": 300.0,
                        "current_price": 280.0, "market_value": 1120.0,
                        "unrealized_pl": -80.0, "unrealized_pl_pct": -6.67,
                        "dividends_received": 2.0, "name": "Microsoft",
                    },
                },
                "buying_power": 500.0,
                "total_equity": 2820.0,
                "total_dividends": 7.0,
                "fetched_at": "2026-07-12T00:00:00+00:00",
            }

        def is_stale(self):
            return True

        def age_hours(self):
            return 25.0

    class _Store:
        def latest_account_snapshot(self):
            return _FakeSnap()

    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
        resp = client.get("/portfolio")
    assert resp.status_code == 200
    body = resp.json()
    # Frontend Portfolio contract fields.
    for key in ("total_equity", "buying_power", "total_unrealized_pl",
                "total_dividends", "position_count", "positions", "fetched_at",
                "source", "is_stale", "age_hours"):
        assert key in body, f"missing Portfolio field: {key}"
    assert body["source"] == "db"
    assert body["position_count"] == 2
    assert body["total_unrealized_pl"] == pytest.approx(120.0)  # 200 + (-80)
    assert isinstance(body["positions"], list) and len(body["positions"]) == 2
    aapl = next(p for p in body["positions"] if p["symbol"] == "AAPL")
    # PortfolioPositionView uses qty/avg_cost, not quantity/average_cost.
    assert aapl["qty"] == 10.0
    assert aapl["avg_cost"] == 100.0
    assert set(aapl.keys()) == {
        "symbol", "qty", "avg_cost", "current_price",
        "market_value", "unrealized_pl", "unrealized_pl_pct", "name",
    }


def test_equity_curve_envelope_empty_when_none():
    class _Store:
        def account_snapshot_history(self, since=None):
            return pd.DataFrame()

    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
        resp = client.get("/portfolio/equity-curve")
    assert resp.status_code == 200
    # {range, curve:[]} envelope — never a bare list, never null (Mismatch 1).
    assert resp.json() == {"range": "1Y", "curve": []}


def test_equity_curve_envelope_rows():
    class _Store:
        def account_snapshot_history(self, since=None):
            return pd.DataFrame(
                [
                    ["2026-07-09T00:00:00+00:00", 500.0, 1380.0, 8.0],
                    ["2026-07-10T00:00:00+00:00", 500.0, 1400.0, 10.0],
                ],
                columns=["fetched_at", "buying_power", "total_equity", "total_dividends"],
            )

    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
        resp = client.get("/portfolio/equity-curve?range=1M")
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"] == "1M"
    curve = body["curve"]
    assert isinstance(curve, list) and len(curve) == 2
    # Each point is a CurvePoint {date, value}, fetched_at mapped to an ISO date.
    assert all(set(p) == {"date", "value"} for p in curve)
    assert curve[0] == {"date": "2026-07-09", "value": 1380.0}
    assert curve[1] == {"date": "2026-07-10", "value": 1400.0}


# ---------------------------------------------------------------------------
# Follow endpoints — FAIL-CLOSED command token
# ---------------------------------------------------------------------------


class TestFollowFailClosed:
    """When FOLLOW_API_TOKEN is unset, every follow endpoint is 403 (disabled)."""

    def test_get_follows_403_when_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            resp = client.get("/follows")
        assert resp.status_code == 403

    def test_put_follows_403_when_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            resp = client.put("/follows", json={"pilot_id": "trend-following", "amount": 100})
        assert resp.status_code == 403

    def test_post_follow_403_when_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            resp = client.post("/pilots/trend-following/follow", json={"amount": 100})
        assert resp.status_code == 403


class TestFollowAuthorized:
    """With FOLLOW_API_TOKEN set, follow endpoints require the matching token."""

    def _auth(self):
        return {"Authorization": f"Bearer {_CMD_TOKEN}"}

    def test_get_follows_401_wrong_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.get("/follows", headers={"Authorization": "Bearer WRONG"})
        assert resp.status_code == 401

    def test_get_follows_ok(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/follows", headers=self._auth())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_put_follows_unknown_pilot_404(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.put(
                    "/follows",
                    json={"pilot_id": "nope", "amount": 100},
                    headers=self._auth(),
                )
        assert resp.status_code == 404

    def test_put_follows_upsert(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.put(
                    "/follows",
                    json={"pilot_id": "trend-following", "amount": 250.0},
                    headers=self._auth(),
                )
        assert resp.status_code == 200
        follow = resp.json()["follow"]
        assert follow["pilot_id"] == "trend-following"
        assert follow["amount"] == 250.0
        assert follow["status"] == "active"

    def test_post_follow_success_preview(self, tmp_path):
        (tmp_path / "state_snapshot.json").write_text(_SNAPSHOT_FIXTURE, encoding="utf-8")

        class _FakeSnap:
            total_equity = 100000.0

        class _Store:
            def latest_account_snapshot(self):
                return _FakeSnap()

        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
                    resp = client.post(
                        "/pilots/trend-following/follow",
                        json={"amount": 1000.0},
                        headers=self._auth(),
                    )
        assert resp.status_code == 200
        body = resp.json()
        assert body["follow"]["pilot_id"] == "trend-following"
        assert body["follow"]["amount"] == 1000.0
        assert body["mode"] in ("off", "review", "live")
        # 5 positive-blend holdings -> 5 proportional preview intents.
        assert len(body["planned_intents"]) == 5
        total = sum(i["target_notional"] for i in body["planned_intents"])
        assert abs(total - 1000.0) < 1.0  # proportional split of the amount

    def test_post_follow_response_matches_followresult_contract(self, tmp_path):
        """Lock the live POST /follow response to the webapp FollowResult type
        (webapp/src/api/types.ts) so the live and mock shapes can't silently
        diverge again — the bug that left the live Follow modal blank."""
        (tmp_path / "state_snapshot.json").write_text(_SNAPSHOT_FIXTURE, encoding="utf-8")

        class _FakeSnap:
            total_equity = 100000.0

        class _Store:
            def latest_account_snapshot(self):
                return _FakeSnap()

        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                with mock.patch.object(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 2500.0):
                    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
                        resp = client.post(
                            "/pilots/trend-following/follow",
                            json={"amount": 1000.0},
                            headers=self._auth(),
                        )
        assert resp.status_code == 200
        body = resp.json()
        required = {
            "follow", "planned_intents", "mode", "queue_written",
            "notional_cap", "min_amount", "notice",
        }
        assert required.issubset(body.keys()), f"missing keys: {required - set(body)}"
        assert body["notional_cap"] == pytest.approx(2500.0)
        assert body["min_amount"] == pytest.approx(settings.FOLLOW_MIN_AMOUNT)
        assert isinstance(body["notice"], str) and body["notice"]

    def test_post_follow_kill_switch_423(self, tmp_path):
        class _ActiveKS:
            def is_active(self):
                return True

            def reason(self):
                return "test halt"

        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_ActiveKS()):
                    resp = client.post(
                        "/pilots/trend-following/follow",
                        json={"amount": 1000.0},
                        headers=self._auth(),
                    )
        assert resp.status_code == 423

    def test_post_follow_no_account_snapshot_preview_note(self, tmp_path):
        (tmp_path / "state_snapshot.json").write_text(_SNAPSHOT_FIXTURE, encoding="utf-8")

        class _EmptyStore:
            def latest_account_snapshot(self):
                return None

        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                with mock.patch.object(pilots_api, "HistoricalStore", return_value=_EmptyStore()):
                    resp = client.post(
                        "/pilots/trend-following/follow",
                        json={"amount": 1000.0},
                        headers=self._auth(),
                    )
        assert resp.status_code == 200
        body = resp.json()
        # Follow still persisted; no equity fabricated -> empty preview + honest note.
        assert body["follow"]["amount"] == 1000.0
        assert body["planned_intents"] == []
        assert "note" in body

    def test_post_follow_unknown_pilot_404(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.post(
                    "/pilots/nope/follow",
                    json={"amount": 1000.0},
                    headers=self._auth(),
                )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Backend analytics surfaces (zero-PWA-gap): realized P&L, alerts, forecast
# skill, ML registry, options matrix, pairs radar
# ---------------------------------------------------------------------------


class TestRealizedPerformance:
    def test_shape_and_cold_start_honesty(self, tmp_path, monkeypatch):
        # Force a cache-miss so the cache-only reader returns the honest empty
        # view (available=False) — no network, no fabricated win rate.
        import data.robinhood_orders as rho

        monkeypatch.setattr(rho, "_CACHE_PATH", tmp_path / "no_such_cache.json")
        resp = client.get("/portfolio/realized")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"summary", "trades", "n_fills", "available"}
        assert body["available"] is False
        assert body["trades"] == []
        s = body["summary"]
        assert s["n_trades"] == 0
        # NaN summary fields serialize as null, never a fabricated 0.0.
        assert s["win_rate"] is None
        assert s["profit_factor"] is None


class TestAlertsFeed:
    def test_unconfigured_is_honest_empty(self, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_FILE_PATH", None, raising=False)
        resp = client.get("/alerts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["entries"] == []
        assert body["reason"] and "not configured" in body["reason"]

    def test_tails_jsonl_newest_first(self, tmp_path, monkeypatch):
        import json as _json

        path = tmp_path / "alerts.jsonl"
        path.write_text(
            "\n".join(
                _json.dumps({"timestamp": f"2026-07-1{i}T00:00:00+00:00",
                             "level": "INFO", "message": f"m{i}", "x": i})
                for i in range(1, 4)
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "ALERT_FILE_PATH", str(path), raising=False)
        resp = client.get("/alerts?limit=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["reason"] is None
        # Newest-first.
        assert [e["message"] for e in body["entries"]] == ["m3", "m2", "m1"]
        # Extra keys fold into `extra`, first-class fields stay separate.
        assert body["entries"][0]["extra"] == {"x": 3}


class TestForecastSkill:
    def test_shape_stable(self):
        resp = client.get("/symbols/AAPL/forecast?horizon=30")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "symbol", "horizon_days", "reliability_curve",
            "skill_weights", "pending", "completed", "reason",
        }
        assert body["symbol"] == "AAPL"
        assert body["horizon_days"] == 30
        assert isinstance(body["reliability_curve"], list)
        assert isinstance(body["skill_weights"], dict)


class TestModelsRegistry:
    def test_reads_registry_rows(self):
        resp = client.get("/models")
        assert resp.status_code == 200
        rows = resp.json()
        assert isinstance(rows, list) and rows  # ml/registry.yaml is checked in
        row = rows[0]
        assert set(row) >= {
            "name", "role", "trained_date", "cpcv_dsr", "pbo",
            "n_train", "deployable", "notes",
        }
        # Un-validated models keep null metrics, never a fabricated 0.
        assert any(r["cpcv_dsr"] is None for r in rows) or all(
            r["cpcv_dsr"] is not None for r in rows
        )


class TestOptionsMatrix:
    def test_disabled_is_honest_empty(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/options")
        assert resp.status_code == 200
        body = resp.json()
        assert body["directives"] == []
        assert body["reason"] and "not generated" in body["reason"]

    def test_reads_persisted_matrix(self, tmp_path):
        import json as _json

        (tmp_path / "options_matrix.json").write_text(
            _json.dumps(
                {
                    "timestamp": "2026-07-15T00:00:00+00:00",
                    "target_dte": 30,
                    "directives": [
                        {"Symbol": "AAPL", "Strategy": "Put Credit Spread",
                         "Net_Premium": 1.2, "Integrity_OK": True}
                    ],
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/options")
            sym = client.get("/symbols/AAPL/options")
            miss = client.get("/symbols/ZZZ/options")
        assert resp.json()["directives"][0]["Symbol"] == "AAPL"
        assert resp.json()["as_of"] == "2026-07-15T00:00:00+00:00"
        assert sym.json()["directive"]["Strategy"] == "Put Credit Spread"
        # Honest: a symbol not in the matrix returns directive=null + reason (200).
        assert miss.status_code == 200
        assert miss.json()["directive"] is None
        assert miss.json()["reason"]


class TestPairsRadar:
    def test_disabled_is_honest_empty(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/pairs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pairs"] == []
        assert body["reason"] and "not generated" in body["reason"]

    def test_reads_persisted_radar(self, tmp_path):
        import json as _json

        (tmp_path / "pairs.json").write_text(
            _json.dumps(
                {
                    "timestamp": "2026-07-15T00:00:00+00:00",
                    "universe": ["XOM", "CVX"],
                    "pairs": [
                        {"ticker1": "XOM", "ticker2": "CVX", "p_value": 0.01,
                         "half_life": 12.0, "z_score": 2.4, "beta": 0.9,
                         "rolling_p": 0.02, "position": -1.0,
                         "signal": "ENTER SHORT spread"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/pairs")
        body = resp.json()
        assert body["pairs"][0]["ticker1"] == "XOM"
        assert body["pairs"][0]["signal"] == "ENTER SHORT spread"
        assert body["universe"] == ["XOM", "CVX"]


# ---------------------------------------------------------------------------
# Architectural guard: no heavy-engine imports in api/pilots_api.py
# ---------------------------------------------------------------------------


def test_pilots_api_never_imports_heavy_engines():
    """Static guard (mirrors tests/test_state_api.py): api/pilots_api.py may
    import pilots.*, execution.kill_switch, data.historical_store, and
    data.robinhood_portfolio — but must NEVER directly import a heavy
    calculation engine or the orchestrator (those are reached, if at all, only
    through pilots.mirror -> execution.queue_builder).

    ``desktop`` is forbidden too, even though it's not itself a calculation
    engine: ``desktop.daemon_runtime`` imports ``main_orchestrator`` at its own
    module top, so importing anything under ``desktop.*`` here would pull the
    orchestrator in TRANSITIVELY and defeat this guard's intent (the guard's
    walk is first-segment-only and non-transitive, so ``desktop.daemon_runtime``
    would otherwise pass while smuggling `main_orchestrator` in behind it).
    The Data & Automation feature (api/pilots_api.py's GET /automation/status)
    reaches the orchestrator daemon ONLY over loopback HTTP via
    gui.daemon_client — never by importing the daemon object directly via
    api.control_api.get_daemon(), which only works in the single co-hosted
    deployment shape (PILOTS_API_ENABLED=True) and not the documented
    standalone one. See gui/daemon_client.py's module docstring."""
    src = pathlib.Path(pilots_api.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".")[0])

    forbidden_modules = {
        "processing_engine",
        "strategy_engine",
        "forecasting_engine",
        "macro_engine",
        "technical_options_engine",
        "main_orchestrator",
        "desktop",
    }
    overlap = imported_modules & forbidden_modules
    assert not overlap, f"api/pilots_api.py must not import {overlap}"


def test_gui_package_init_stays_import_inert():
    """api/pilots_api.py imports gui.daemon_client (GET /automation/status'
    only path to the orchestrator daemon — see the guard test above), which
    executes gui/__init__.py as a side effect of the import. That file is
    docstring + `__all__` (a list of strings) only today, so the import is
    inert. If anyone ever adds a real import to gui/__init__.py, the Pilots
    API would silently inherit it — this test pins that gui/__init__.py stays
    free of any actual import statement, so such a change fails loudly here
    instead of surfacing as an unexplained pilots_api import-time side effect."""
    import gui

    tree = ast.parse(pathlib.Path(gui.__file__).read_text(encoding="utf-8"))
    real_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and getattr(node, "module", None) != "__future__"
    ]
    assert not real_imports, (
        f"gui/__init__.py must stay import-inert (found: {real_imports}) — "
        "api/pilots_api.py imports gui.daemon_client and would silently "
        "inherit any real import added here."
    )


# ---------------------------------------------------------------------------
# GET /automation/status — the "did the pipeline run?" composite endpoint.
# gui.daemon_client and execution.kill_switch.GlobalKillSwitch are both
# module-top imports on pilots_api, so both are mock.patch.object-able here.
# ---------------------------------------------------------------------------


class _ActiveKS:
    def is_active(self):
        return True

    def reason(self):
        return "test halt"


class _InactiveKS:
    def is_active(self):
        return False

    def reason(self):
        return ""


def _fake_daemon_status(**overrides):
    base = {
        "daemon_alive": True,
        "is_running": False,
        "current_run_id": None,
        "interval_seconds": 300,
        "engines_warm": True,
        "started_at": "2026-07-16T15:34:45.942581+00:00",
    }
    base.update(overrides)
    return base


def _fake_run_record(**overrides):
    base = {
        "run_id": "orch-123",
        "state": "succeeded",
        "started_at": "2026-07-16T19:00:00+00:00",
        "finished_at": "2026-07-16T19:05:00+00:00",
        "duration_seconds": 300.0,
        "error": None,
        "reason": "manual",
        "progress": None,
    }
    base.update(overrides)
    return base


class TestAutomationStatus:
    def test_daemon_reachable_via_control_api(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(
                pilots_api.daemon_client, "get_status",
                return_value=_fake_daemon_status(),
            ):
                with mock.patch.object(
                    pilots_api.daemon_client, "get_latest_run",
                    return_value=_fake_run_record(),
                ):
                    with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_InactiveKS()):
                        resp = client.get("/automation/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["daemon"]["alive"] is True
        assert body["daemon"]["source"] == "control_api"
        assert body["daemon"]["interval_seconds"] == 300
        assert body["last_run"]["run_id"] == "orch-123"
        assert body["last_run_source"] == "daemon_memory"
        assert body["kill_switch"] == {"active": False, "reason": None}

    def test_daemon_unreachable_falls_back_to_daemon_json(self, tmp_path):
        """The restart-honesty core: when the Control API can't be reached,
        output/daemon.json (written once at startup) still supplies pid/
        interval/started_at, and `alive` honestly reads False."""
        daemon_json = {
            "pid": 77880,
            "state": "started",
            "interval_seconds": 300,
            "started_at": "2026-07-16T15:34:45.942581+00:00",
            "port": 8601,
            "pilots_api_port": None,
        }
        (tmp_path / "daemon.json").write_text(__import__("json").dumps(daemon_json), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                    with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_InactiveKS()):
                        resp = client.get("/automation/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["daemon"]["alive"] is False
        assert body["daemon"]["source"] == "daemon_json"
        assert body["daemon"]["pid"] == 77880
        assert body["daemon"]["interval_seconds"] == 300
        assert body["last_run"] is None
        assert body["last_run_source"] == "state_snapshot"

    def test_daemon_unreachable_and_no_daemon_json(self, tmp_path):
        """Neither the Control API nor a daemon.json file exist (never
        launched, or a very early state) — everything degrades to null,
        never a 500, never a fabricated value."""
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                    resp = client.get("/automation/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["daemon"] == {
            "alive": False, "source": "none", "pid": None, "port": None,
            "started_at": None, "interval_seconds": None, "is_running": None,
            "current_run_id": None, "engines_warm": None,
        }
        assert body["last_run"] is None
        assert body["last_run_source"] == "state_snapshot"

    def test_cold_start_is_200_with_honest_nulls_never_404(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                    resp = client.get("/automation/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pipeline"]["snapshot_age_seconds"] is None
        assert body["pipeline"]["snapshot_age_source"] == "missing"
        assert body["pipeline"]["heartbeat_age_seconds"] is None
        assert body["progress"] is None
        assert body["errors"] == {"generated_at": None, "entry_count": 0, "entries": []}

    def test_snapshot_timestamp_source(self, tmp_path):
        import json
        from datetime import datetime, timezone

        snap = {"timestamp": datetime.now(timezone.utc).isoformat(), "tickers": []}
        (tmp_path / "state_snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                    resp = client.get("/automation/status")
        body = resp.json()
        assert body["pipeline"]["snapshot_age_source"] == "timestamp"
        assert body["pipeline"]["snapshot_age_seconds"] < 5.0

    def test_snapshot_missing_timestamp_field_falls_back_to_mtime(self, tmp_path):
        import json

        (tmp_path / "state_snapshot.json").write_text(json.dumps({"tickers": []}), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                    resp = client.get("/automation/status")
        body = resp.json()
        assert body["pipeline"]["snapshot_age_source"] == "mtime"
        assert body["pipeline"]["snapshot_age_seconds"] < 5.0

    def test_progress_running_and_stale_flag(self, tmp_path):
        import json
        from datetime import datetime, timedelta, timezone

        old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        progress = {
            "run_id": "orch-999", "state": "running", "stage": "forecasting",
            "stage_index": 2, "stage_total": 4, "symbols_done": 5,
            "symbols_total": 10, "percent": 62.5, "message": "Forecasting AAPL",
            "started_at": old, "updated_at": old,
        }
        (tmp_path / "progress.json").write_text(json.dumps(progress), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                    resp = client.get("/automation/status")
        body = resp.json()
        assert body["progress"]["state"] == "running"
        assert body["progress"]["stale"] is True  # 20 min > the 900s/15min threshold

    def test_progress_running_but_fresh_is_not_stale(self, tmp_path):
        import json
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        progress = {
            "run_id": "orch-999", "state": "running", "stage": "forecasting",
            "stage_index": 2, "stage_total": 4, "symbols_done": 5,
            "symbols_total": 10, "percent": 62.5, "message": "Forecasting AAPL",
            "started_at": now, "updated_at": now,
        }
        (tmp_path / "progress.json").write_text(json.dumps(progress), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                    resp = client.get("/automation/status")
        assert resp.json()["progress"]["stale"] is False

    def test_dead_letter_entries_surfaced_and_capped(self, tmp_path):
        import json

        entries = [{"symbol": f"SYM{i}", "stage": "forecasting", "error": "boom"} for i in range(60)]
        payload = {"run_id": "x", "generated_at": "2026-07-16T19:00:00+00:00", "entries": entries}
        (tmp_path / "dead_letter.json").write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                    resp = client.get("/automation/status")
        body = resp.json()
        assert body["errors"]["entry_count"] == 60
        assert len(body["errors"]["entries"]) == 50  # capped, true count still 60

    def test_dead_letter_malformed_degrades_to_empty(self, tmp_path):
        (tmp_path / "dead_letter.json").write_text("{not valid json", encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                    resp = client.get("/automation/status")
        assert resp.status_code == 200
        assert resp.json()["errors"] == {"generated_at": None, "entry_count": 0, "entries": []}

    def test_kill_switch_active_surfaced(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                    with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_ActiveKS()):
                        resp = client.get("/automation/status")
        body = resp.json()
        assert body["kill_switch"] == {"active": True, "reason": "test halt"}

    def test_daemon_client_raising_is_not_silently_swallowed(self, tmp_path):
        """daemon_client's own contract is non-raising (its docstring's
        CONSTRAINT #6) — this endpoint deliberately does NOT wrap the call in
        its own try/except, so if that contract were ever violated the
        failure surfaces loudly (TestClient re-raises server exceptions by
        default) rather than this endpoint silently faking a healthy status."""
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(
                pilots_api.daemon_client, "get_status",
                side_effect=RuntimeError("unexpected"),
            ):
                with pytest.raises(RuntimeError, match="unexpected"):
                    client.get("/automation/status")

    def test_read_token_gates_the_endpoint(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/automation/status")
        assert resp.status_code == 401

        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get(
                    "/automation/status", headers={"Authorization": "Bearer read-tok"}
                )
        assert resp.status_code == 200

    def test_read_token_unset_is_open(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", ""):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/automation/status")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /automation/schedule
# ---------------------------------------------------------------------------


class TestAutomationSchedule:
    def test_no_drift_when_running_matches_configured(self):
        with mock.patch.object(settings, "ORCHESTRATOR_INTERVAL_SECONDS", 300):
            with mock.patch.object(
                pilots_api.daemon_client, "get_status",
                return_value=_fake_daemon_status(interval_seconds=300),
            ):
                resp = client.get("/automation/schedule")
        body = resp.json()
        assert body["interval"]["running_value"] == 300
        assert body["interval"]["configured_value"] == 300
        assert body["interval"]["drift"] is False

    def test_drift_flagged_when_running_differs_from_configured(self):
        """A .env edit doesn't reach a live daemon until it restarts -- this
        is the whole point of the endpoint: never let the operator assume an
        edit already took effect."""
        with mock.patch.object(settings, "ORCHESTRATOR_INTERVAL_SECONDS", 0):
            with mock.patch.object(
                pilots_api.daemon_client, "get_status",
                return_value=_fake_daemon_status(interval_seconds=300),
            ):
                resp = client.get("/automation/schedule")
        body = resp.json()
        assert body["interval"]["running_value"] == 300
        assert body["interval"]["configured_value"] == 0
        assert body["interval"]["drift"] is True

    def test_running_value_falls_back_to_daemon_json_when_control_api_down(self, tmp_path):
        import json

        (tmp_path / "daemon.json").write_text(
            json.dumps({"interval_seconds": 120, "pid": 1, "started_at": "x", "port": 8601, "pilots_api_port": None}),
            encoding="utf-8",
        )
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                resp = client.get("/automation/schedule")
        assert resp.json()["interval"]["running_value"] == 120

    def test_running_value_null_when_no_daemon_signal_at_all(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                resp = client.get("/automation/schedule")
        body = resp.json()
        assert body["interval"]["running_value"] is None
        assert body["interval"]["drift"] is False  # null running_value never claims drift

    def test_interval_is_read_only_in_this_build(self):
        with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
            resp = client.get("/automation/schedule")
        assert resp.json()["interval"]["writable"] is False

    def test_cron_never_shells_out_and_installed_is_honestly_null(self):
        """Regression guard for the RCE-adjacent surface this design
        deliberately avoids: no subprocess call, ever."""
        with mock.patch("subprocess.run", side_effect=AssertionError("must not shell out")):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                resp = client.get("/automation/schedule")
        assert resp.status_code == 200
        assert resp.json()["cron"]["installed"] is None

    def test_cron_entries_parsed_from_repo_crontab(self):
        with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
            resp = client.get("/automation/schedule")
        entries = resp.json()["cron"]["entries"]
        assert len(entries) >= 1
        assert all({"schedule", "command", "comment"} <= e.keys() for e in entries)
        # The real deploy/crontab.txt's daily-briefing line, so this test
        # would catch that file being emptied or moved without noticing.
        assert any("daily_briefing.py" in e["command"] for e in entries)

    def test_cron_missing_file_degrades_to_empty_list(self):
        """A missing/unreadable crontab.txt (pilots.run_status.parse_crontab's
        own OSError catch — see test_run_status.py for that unit-level proof)
        must surface as an empty list here, never a 500."""
        with mock.patch.object(pilots_api.run_status, "parse_crontab", return_value=[]):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                resp = client.get("/automation/schedule")
        assert resp.status_code == 200
        assert resp.json()["cron"]["entries"] == []

    def test_read_token_gates_the_endpoint(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get("/automation/schedule")
        assert resp.status_code == 401
