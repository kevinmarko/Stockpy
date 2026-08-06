"""tests/test_cache_long_short_api.py

Auth-tier tests for api/pilots_api.py's Cache Long/Short endpoints, per the
pilots-endpoint skill's checklist: read endpoints are fail-open
(require_read_token alone); the two write endpoints are fail-closed behind
BOTH the command token (FOLLOW_API_TOKEN) and the dedicated
CACHE_LONG_SHORT_WRITES_ENABLED master flag.
"""
from unittest import mock

from fastapi.testclient import TestClient

from settings import settings
import api.pilots_api as pilots_api

client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))

_CMD_TOKEN = "cls-cmd-tok"


# ---------------------------------------------------------------------------
# GET /pilots/cache-long-short/concentrated-positions
# ---------------------------------------------------------------------------


class TestConcentratedPositions:
    def test_read_token_gates_endpoint(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            no_auth = client.get("/pilots/cache-long-short/concentrated-positions")
            wrong = client.get(
                "/pilots/cache-long-short/concentrated-positions",
                headers={"Authorization": "Bearer WRONG"},
            )
        assert no_auth.status_code == 401
        assert wrong.status_code == 401

    def test_flags_position_over_20pct_equity(self):
        snap = mock.MagicMock()
        snap.total_equity = 10000.0
        pos = mock.MagicMock(symbol="AAPL", market_value=3000.0)
        snap.positions = [pos]
        with mock.patch("data.robinhood_portfolio.fetch_account_snapshot", return_value=snap):
            resp = client.get("/pilots/cache-long-short/concentrated-positions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["positions"] == [{"ticker": "AAPL", "market_value": 3000.0, "pct_equity": 0.3}]

    def test_excludes_position_under_threshold(self):
        snap = mock.MagicMock()
        snap.total_equity = 10000.0
        pos = mock.MagicMock(symbol="AAPL", market_value=500.0)
        snap.positions = [pos]
        with mock.patch("data.robinhood_portfolio.fetch_account_snapshot", return_value=snap):
            resp = client.get("/pilots/cache-long-short/concentrated-positions")
        assert resp.json()["positions"] == []

    def test_snapshot_failure_degrades_to_empty_never_500(self):
        with mock.patch("data.robinhood_portfolio.fetch_account_snapshot", side_effect=RuntimeError("no cache")):
            resp = client.get("/pilots/cache-long-short/concentrated-positions")
        assert resp.status_code == 200
        assert resp.json() == {"positions": []}


# ---------------------------------------------------------------------------
# GET /pilots/cache-long-short/dashboard, pending-approvals
# ---------------------------------------------------------------------------


class TestDashboardAndPendingApprovals:
    def test_read_token_gates_dashboard(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get("/pilots/cache-long-short/dashboard")
        assert resp.status_code == 401

    def test_dashboard_disabled_shape(self):
        with mock.patch.object(settings, "CACHE_LONG_SHORT_ENABLED", False):
            resp = client.get("/pilots/cache-long-short/dashboard")
        assert resp.status_code == 200
        assert resp.json() == {"status": "disabled"}

    def test_dashboard_enabled_shape(self):
        with mock.patch("pilots.cache_long_short.get_dashboard", return_value={"status": "enabled", "tax_bank": 42.0}):
            resp = client.get("/pilots/cache-long-short/dashboard")
        assert resp.status_code == 200
        assert resp.json()["tax_bank"] == 42.0

    def test_pending_approvals_read_token_gates(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get("/pilots/cache-long-short/pending-approvals")
        assert resp.status_code == 401

    def test_pending_approvals_empty_when_disabled(self):
        with mock.patch.object(settings, "CACHE_LONG_SHORT_ENABLED", False):
            resp = client.get("/pilots/cache-long-short/pending-approvals")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_pending_approvals_returns_flagged_lots(self):
        fixture = [{"lot_id": 1, "position_id": 1, "cost_basis": 150.0, "unrealized_loss_pct": -0.1}]
        with mock.patch("pilots.cache_long_short.get_pending_approvals", return_value=fixture):
            resp = client.get("/pilots/cache-long-short/pending-approvals")
        assert resp.status_code == 200
        assert resp.json() == fixture


# ---------------------------------------------------------------------------
# POST /pilots/cache-long-short/start, approve-bulk -- fail-closed
# ---------------------------------------------------------------------------


_START_BODY = {"ticker": "AAPL", "proxy_ticker": "XLK", "allocation": 10000.0, "correlation_coefficient": 0.85}


class TestStartStrategy:
    def test_fails_closed_when_writes_disabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "CACHE_LONG_SHORT_WRITES_ENABLED", False):
                resp = client.post(
                    "/pilots/cache-long-short/start",
                    json=_START_BODY,
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 403
        assert "CACHE_LONG_SHORT_WRITES_ENABLED" in resp.json()["detail"]

    def test_fails_closed_when_command_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            with mock.patch.object(settings, "CACHE_LONG_SHORT_WRITES_ENABLED", True):
                resp = client.post(
                    "/pilots/cache-long-short/start",
                    json=_START_BODY,
                    headers={"Authorization": "Bearer anything"},
                )
        assert resp.status_code == 403

    def test_fails_closed_with_wrong_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "CACHE_LONG_SHORT_WRITES_ENABLED", True):
                resp = client.post(
                    "/pilots/cache-long-short/start",
                    json=_START_BODY,
                    headers={"Authorization": "Bearer WRONG"},
                )
        assert resp.status_code == 401

    def test_happy_path_persists_position_and_proxy(self):
        mock_store = mock.MagicMock()
        mock_store.record_position.return_value = 7
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "CACHE_LONG_SHORT_WRITES_ENABLED", True):
                with mock.patch("data.cache_long_short_store.CacheLongShortStore", return_value=mock_store):
                    resp = client.post(
                        "/pilots/cache-long-short/start",
                        json=_START_BODY,
                        headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                    )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"status": "started", "position_id": 7, "ticker": "AAPL"}
        mock_store.record_position.assert_called_once_with("AAPL", "long")
        mock_store.upsert_security_proxy.assert_called_once_with("AAPL", "XLK", 0.85)

    def test_invalid_body_422(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "CACHE_LONG_SHORT_WRITES_ENABLED", True):
                resp = client.post(
                    "/pilots/cache-long-short/start",
                    json={"ticker": "AAPL"},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 422

    def test_write_never_logs_token(self, caplog):
        mock_store = mock.MagicMock()
        mock_store.record_position.return_value = 1
        with caplog.at_level("DEBUG"):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(settings, "CACHE_LONG_SHORT_WRITES_ENABLED", True):
                    with mock.patch("data.cache_long_short_store.CacheLongShortStore", return_value=mock_store):
                        client.post(
                            "/pilots/cache-long-short/start",
                            json=_START_BODY,
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert _CMD_TOKEN not in caplog.text


class TestApproveBulk:
    def test_fails_closed_when_writes_disabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "CACHE_LONG_SHORT_WRITES_ENABLED", False):
                resp = client.post(
                    "/pilots/cache-long-short/approve-bulk",
                    json={"lot_ids": [1, 2]},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 403

    def test_happy_path_approves_lots(self):
        mock_store = mock.MagicMock()
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "CACHE_LONG_SHORT_WRITES_ENABLED", True):
                with mock.patch("data.cache_long_short_store.CacheLongShortStore", return_value=mock_store):
                    resp = client.post(
                        "/pilots/cache-long-short/approve-bulk",
                        json={"lot_ids": [1, 2, 3]},
                        headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                    )
        assert resp.status_code == 200
        assert resp.json() == {"status": "approved", "count": 3}
        mock_store.approve_tax_lots.assert_called_once_with([1, 2, 3])


# ---------------------------------------------------------------------------
# CACHE_LONG_SHORT_WRITES_ENABLED must never be GUI-writable
# ---------------------------------------------------------------------------


class TestWritesEnabledInvariants:
    def test_cache_long_short_writes_enabled_is_not_gui_writable(self):
        """Mirrors test_strategy_writes_enabled_is_not_gui_writable: a GUI
        bug must never flip this on. Neither allowlisted nor secret --
        hand-set only."""
        assert "CACHE_LONG_SHORT_WRITES_ENABLED" not in pilots_api.env_io.ALLOWED_KEYS
        assert "CACHE_LONG_SHORT_WRITES_ENABLED" not in pilots_api.env_io.SECRET_KEYS

    def test_cache_long_short_enabled_stays_allowlisted(self):
        """The master feature switch (distinct from the writes-enabled
        guard above) is a routine GUI-writable feature flag, like
        ORCHESTRATOR_DAEMON_ENABLED/ETF_TRANSMISSION_ENABLED."""
        assert "CACHE_LONG_SHORT_ENABLED" in pilots_api.env_io.ALLOWED_KEYS
