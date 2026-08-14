"""
tests/test_data_api_peers.py
=============================
Fully-offline tests for ``GET /data/peers/{symbol}`` (``api/data_api.py``) --
the on-demand, per-click FMP peer-group lookup gated by
``settings.FMP_PEERS_ENABLED`` (default ``True`` as of PR #737's FMP rollout;
was ``False`` before that -- every flag-on/flag-off test below explicitly
monkeypatches the value it needs regardless of the shipped default, so only
the one test asserting the real, unpatched default itself needed updating).

Mirrors this series' conventions: ``mock.patch.object(settings,
"STATE_API_TOKEN", None)`` to exercise the fail-open-on-loopback read path
(matching ``tests/test_data_api.py``'s fundamentals tests), and
``unittest.mock.patch("data.fmp_feeds_market.fetch_peer_group", ...)`` to
substitute the lazily-imported vendor call without touching the network.
"""
from __future__ import annotations

from unittest import mock

from fastapi.testclient import TestClient

from settings import settings
import api.data_api as data_api

# Starlette's TestClient defaults request.client.host to the literal string
# "testclient" -- NOT loopback -- which would trip api.auth.require_read_token's
# fail-closed-when-non-loopback branch on every zero-config assertion below.
client = TestClient(data_api.app, client=("127.0.0.1", 54123))


# ---------------------------------------------------------------------------
# Flag-off (default) — complete no-op, zero network calls
# ---------------------------------------------------------------------------


def test_flag_off_returns_empty_list_with_honest_reason_and_never_calls_fetch(monkeypatch):
    monkeypatch.setattr(settings, "FMP_PEERS_ENABLED", False)
    with mock.patch(
        "data.fmp_feeds_market.fetch_peer_group"
    ) as mock_fetch, mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/peers/AAPL")

    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["peers"] == []
    assert body["reason"] == "FMP peer-group lookup is disabled (FMP_PEERS_ENABLED=False)."
    mock_fetch.assert_not_called()


def test_flag_attribute_exists_with_documented_default():
    # getattr(settings, "FMP_PEERS_ENABLED", False) -- confirms the attribute
    # actually exists on the real Settings model with the documented default,
    # not just that our monkeypatched tests exercise a fabricated attribute.
    # Default flipped False -> True in PR #737's FMP rollout (settings.py);
    # every other test in this file monkeypatches the value it needs, so this
    # is the one place that must track the real shipped default.
    assert getattr(settings, "FMP_PEERS_ENABLED", False) is True


# ---------------------------------------------------------------------------
# Flag-on — happy path
# ---------------------------------------------------------------------------


def test_flag_on_returns_the_mocked_peer_list(monkeypatch):
    monkeypatch.setattr(settings, "FMP_PEERS_ENABLED", True)
    with mock.patch(
        "data.fmp_feeds_market.fetch_peer_group", return_value=["MSFT", "GOOGL", "AMZN"],
    ) as mock_fetch, mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/peers/AAPL")

    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["peers"] == ["MSFT", "GOOGL", "AMZN"]
    assert body["reason"] is None
    mock_fetch.assert_called_once_with("AAPL")


def test_symbol_is_uppercased_and_stripped(monkeypatch):
    monkeypatch.setattr(settings, "FMP_PEERS_ENABLED", True)
    with mock.patch(
        "data.fmp_feeds_market.fetch_peer_group", return_value=[],
    ) as mock_fetch, mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/peers/aapl")

    assert resp.status_code == 200
    assert resp.json()["symbol"] == "AAPL"
    mock_fetch.assert_called_once_with("AAPL")


def test_flag_on_empty_result_gets_an_honest_reason(monkeypatch):
    monkeypatch.setattr(settings, "FMP_PEERS_ENABLED", True)
    with mock.patch(
        "data.fmp_feeds_market.fetch_peer_group", return_value=[],
    ), mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/peers/ZZZZ")

    assert resp.status_code == 200
    body = resp.json()
    assert body["peers"] == []
    assert body["reason"] == "No peer data available for this symbol."


# ---------------------------------------------------------------------------
# Defensive degradation — never a 500, even if the wrapped function
# (which its own docstring says never raises) somehow did.
# ---------------------------------------------------------------------------


def test_unexpected_exception_from_fetch_degrades_to_empty_list_never_500(monkeypatch):
    monkeypatch.setattr(settings, "FMP_PEERS_ENABLED", True)
    with mock.patch(
        "data.fmp_feeds_market.fetch_peer_group", side_effect=RuntimeError("boom"),
    ), mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/data/peers/AAPL")

    assert resp.status_code == 200
    body = resp.json()
    assert body["peers"] == []
    assert body["reason"] == "No peer data available for this symbol."


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_401_with_wrong_read_token(monkeypatch):
    monkeypatch.setattr(settings, "FMP_PEERS_ENABLED", False)
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret-token"):
        resp = client.get(
            "/data/peers/AAPL", headers={"Authorization": "Bearer wrong-token"},
        )
    assert resp.status_code == 401
