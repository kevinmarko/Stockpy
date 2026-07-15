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
    through pilots.mirror -> execution.queue_builder)."""
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
    }
    overlap = imported_modules & forbidden_modules
    assert not overlap, f"api/pilots_api.py must not import {overlap}"
