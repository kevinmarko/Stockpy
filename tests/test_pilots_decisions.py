"""
tests/test_pilots_decisions.py
===============================
Tests for the Decision Journal endpoints on ``api/pilots_api.py``
(``GET/POST /decisions``) — ports
``gui/panels/report_viewer.py::_render_decision_journal_section`` /
``gui/decision_log.py`` to the Pilots PWA.

Isolation: every test monkeypatches ``settings.OUTPUT_DIR`` to a ``tmp_path``
(mirrors ``tests/test_pilots_api.py``'s ``TestFollowAuthorized`` pattern) so
no test ever touches the real ``output/decision_log.jsonl``. Where the
``POST`` handler's best-effort ``TransactionsStore()`` construction matters,
it is monkeypatched to an in-memory store (or a raising stub, for the
construction-failure path) rather than touching the real on-disk
``quant_platform.db``.
"""

from __future__ import annotations

from unittest import mock

import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.pilots_api as pilots_api

client = TestClient(pilots_api.app)

_CMD_TOKEN = "cmd-tok"


def _auth():
    return {"Authorization": f"Bearer {_CMD_TOKEN}"}


@pytest.fixture(autouse=True)
def _in_memory_transactions_store():
    """Every POST in this file exercises the real ``log_decision`` ->
    ``join_to_store`` path, which calls ``transactions_store.get_trade_history``.
    Point the module's ``TransactionsStore`` at a throwaway in-memory DB so no
    test ever touches the real ``quant_platform.db`` on disk."""
    from transactions_store import TransactionsStore as _RealStore

    def _factory(*args, **kwargs):
        return _RealStore(db_url="sqlite:///:memory:")

    with mock.patch.object(pilots_api, "TransactionsStore", side_effect=_factory):
        yield


def _post(tmp_path, body, *, token=_CMD_TOKEN, headers=None):
    with mock.patch.object(settings, "FOLLOW_API_TOKEN", token):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            return client.post(
                "/decisions",
                json=body,
                headers=headers if headers is not None else _auth(),
            )


def _get(tmp_path, params=None, *, token=None, headers=None):
    with mock.patch.object(settings, "STATE_API_TOKEN", token):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            return client.get("/decisions", params=params or {}, headers=headers)


# ---------------------------------------------------------------------------
# GET /decisions — collection view, never 404s
# ---------------------------------------------------------------------------


def test_get_decisions_no_log_file_returns_empty_list(tmp_path):
    """Cold start: no decision_log.jsonl has ever been written. This is a
    collection view, not a single-resource lookup — degrades to `[]`, never
    a 404 (CONSTRAINT #6)."""
    resp = _get(tmp_path, token="")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_decisions_no_auth_required_by_default(tmp_path):
    """require_read_token is fail-open when STATE_API_TOKEN is unset — mirrors
    every other GET on this API."""
    resp = _get(tmp_path, token="")
    assert resp.status_code == 200


def test_get_decisions_read_token_gates_when_set(tmp_path):
    with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            wrong = client.get("/decisions", headers={"Authorization": "Bearer WRONG"})
            ok = client.get("/decisions", headers={"Authorization": "Bearer read-tok"})
    assert wrong.status_code == 401
    assert ok.status_code == 200


# ---------------------------------------------------------------------------
# POST /decisions — fail-closed command token, no dedicated master flag
# ---------------------------------------------------------------------------


def test_post_decision_403_when_follow_api_token_unset(tmp_path):
    """No dedicated master flag for this endpoint (mirrors
    POST /pilots/{id}/follow) — but it is still gated by the fail-closed
    command token alone, which is 403 when unset."""
    resp = _post(
        tmp_path,
        {"symbol": "AAPL", "action_taken": "acted", "signal_action": "BUY"},
        token=None,
    )
    assert resp.status_code == 403


def test_post_decision_401_wrong_token(tmp_path):
    resp = _post(
        tmp_path,
        {"symbol": "AAPL", "action_taken": "acted", "signal_action": "BUY"},
        headers={"Authorization": "Bearer WRONG"},
    )
    assert resp.status_code == 401


def test_post_then_get_round_trip(tmp_path):
    """POST an entry, then GET it back — the core Decision Journal contract."""
    post_resp = _post(
        tmp_path,
        {
            "symbol": "aapl",
            "action_taken": "acted",
            "signal_action": "BUY",
            "conviction": 0.82,
            "notes": "Sized normally.",
            "signal_ts": "2026-07-17T12:00:00+00:00",
        },
    )
    assert post_resp.status_code == 200
    posted = post_resp.json()
    assert posted["symbol"] == "AAPL"  # log_decision normalises to uppercase
    assert posted["action_taken"] == "acted"
    assert posted["signal_action"] == "BUY"
    assert posted["conviction"] == pytest.approx(0.82)
    assert posted["notes"] == "Sized normally."
    assert posted["timestamp"]  # server-stamped ISO timestamp
    # No matching TransactionsStore trade exists in the fresh in-memory DB —
    # trade_id must be None, never a fabricated match (CONSTRAINT #4).
    assert posted["trade_id"] is None

    get_resp = _get(tmp_path, token="")
    assert get_resp.status_code == 200
    rows = get_resp.json()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["notes"] == "Sized normally."
    assert rows[0]["trade_id"] is None


def test_post_modified_action_with_empty_notes_is_accepted(tmp_path):
    """The Streamlit UI only nudges for notes on 'modified' client-side
    (a st.warning, not a hard block) — the API does not silently replicate
    that as a server-side 422. There is no honesty reason an empty note
    should be rejected: the operator may simply not have anything to add."""
    resp = _post(
        tmp_path,
        {"symbol": "MSFT", "action_taken": "modified", "signal_action": "HOLD"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action_taken"] == "modified"
    assert body["notes"] == ""


def test_post_passed_action_minimal_body(tmp_path):
    resp = _post(
        tmp_path,
        {"symbol": "tsla", "action_taken": "passed", "signal_action": "SELL"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "TSLA"
    assert body["conviction"] is None


def test_post_decision_invalid_action_taken_422(tmp_path):
    resp = _post(
        tmp_path,
        {"symbol": "AAPL", "action_taken": "ignored", "signal_action": "BUY"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /decisions — filtering, ordering, limit
# ---------------------------------------------------------------------------


def test_get_decisions_respects_limit(tmp_path):
    for i in range(3):
        r = _post(
            tmp_path,
            {"symbol": "AAPL", "action_taken": "passed", "signal_action": "HOLD",
             "notes": f"entry-{i}"},
        )
        assert r.status_code == 200

    resp = _get(tmp_path, params={"limit": 1}, token="")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    # Most-recent-first: the last posted entry comes back.
    assert rows[0]["notes"] == "entry-2"


def test_get_decisions_filters_by_symbol(tmp_path):
    assert _post(
        tmp_path, {"symbol": "AAPL", "action_taken": "acted", "signal_action": "BUY"},
    ).status_code == 200
    assert _post(
        tmp_path, {"symbol": "MSFT", "action_taken": "passed", "signal_action": "HOLD"},
    ).status_code == 200

    resp = _get(tmp_path, params={"symbol": "msft"}, token="")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "MSFT"


def test_get_decisions_symbol_filter_no_match_returns_empty(tmp_path):
    assert _post(
        tmp_path, {"symbol": "AAPL", "action_taken": "acted", "signal_action": "BUY"},
    ).status_code == 200

    resp = _get(tmp_path, params={"symbol": "ZZZZ"}, token="")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_decisions_most_recent_first_across_symbols(tmp_path):
    assert _post(
        tmp_path, {"symbol": "AAPL", "action_taken": "acted", "signal_action": "BUY"},
    ).status_code == 200
    assert _post(
        tmp_path, {"symbol": "MSFT", "action_taken": "passed", "signal_action": "HOLD"},
    ).status_code == 200

    resp = _get(tmp_path, token="")
    rows = resp.json()
    assert [r["symbol"] for r in rows] == ["MSFT", "AAPL"]


# ---------------------------------------------------------------------------
# POST /decisions — TransactionsStore construction-failure fallback
# ---------------------------------------------------------------------------


def test_post_decision_survives_transactions_store_construction_failure(tmp_path):
    """A DB-outage-style TransactionsStore() construction failure must degrade
    to a skipped trade-join, never a 500 — mirrors the try/except-to-None
    fallback gui/panels/report_viewer.py::_do_log already uses for the
    Streamlit form this endpoint ports."""
    with mock.patch.object(
        pilots_api, "TransactionsStore", side_effect=RuntimeError("db unreachable")
    ):
        resp = _post(
            tmp_path,
            {"symbol": "AAPL", "action_taken": "acted", "signal_action": "BUY"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["trade_id"] is None


def test_post_decision_never_logs_token(tmp_path, caplog):
    with caplog.at_level("DEBUG"):
        resp = _post(
            tmp_path,
            {"symbol": "AAPL", "action_taken": "acted", "signal_action": "BUY"},
        )
    assert resp.status_code == 200
    assert _CMD_TOKEN not in caplog.text
