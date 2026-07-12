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
    assert set(tf.keys()) == {
        "id", "name", "category", "description",
        "headline", "holdings_count", "aum_proxy", "followers_proxy",
    }
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
# GET /pilots/{id}/performance
# ---------------------------------------------------------------------------


def test_performance_good_range(monkeypatch):
    _point_reports_at_fixtures(monkeypatch)
    resp = client.get("/pilots/trend-following/performance?range=1M")
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"] == "1M"
    assert body["metrics"]["sharpe"] == 1.14
    # Never fabricate a curve.
    assert body["curve"] is None


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


def test_equity_curve_empty_list_when_none():
    class _Store:
        def account_snapshot_history(self, since=None):
            return pd.DataFrame()

    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
        resp = client.get("/portfolio/equity-curve")
    assert resp.status_code == 200
    assert resp.json() == []


def test_equity_curve_rows():
    class _Store:
        def account_snapshot_history(self, since=None):
            return pd.DataFrame(
                [["2026-07-10T00:00:00+00:00", 500.0, 1400.0, 10.0]],
                columns=["fetched_at", "buying_power", "total_equity", "total_dividends"],
            )

    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
        resp = client.get("/portfolio/equity-curve?range=1M")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["total_equity"] == 1400.0


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
