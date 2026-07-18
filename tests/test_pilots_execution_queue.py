"""Tests for GET /execution-queue — the read-only Robinhood execution-queue
surface added to api/pilots_api.py.

This endpoint reuses gui.robinhood_execution_panel.read_execution_queue (the
existing, dead-letter-tolerant reader the Streamlit Launcher tab already uses)
rather than re-parsing output/execution_queue.json. It never contacts the
Robinhood MCP and never places an order — per execution/queue_builder.py's
module contract, only a live Claude Code agent session ever calls
place_equity_order, so this endpoint has nothing to trigger; it can only ever
report what's already on disk.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

from fastapi.testclient import TestClient

from settings import settings
import gui.robinhood_execution_panel as execution_panel
import api.pilots_api as pilots_api

client = TestClient(pilots_api.app)


def _snapshot(**overrides):
    defaults = dict(
        # Computed at call time (not a fixed past literal) so the "fresh" test
        # case stays fresh regardless of when the suite happens to run --
        # is_queue_stale/queue_age_seconds compare against the real wall clock.
        generated_at=datetime.now(timezone.utc).isoformat(),
        mode="review",
        kill_switch_active=False,
        max_notional_per_order=500.0,
        n_intents=2,
        n_placeable=1,
        intents=[
            execution_panel.QueuedIntent(
                symbol="AAPL",
                action="BUY",
                side="buy",
                qty=None,
                target_notional=250.0,
                conviction=0.8,
                gate_allowed=True,
                gate_reasons=[],
                allow_place=True,
                rationale="strong momentum",
                client_order_id="advisory-AAPL-buy-1",
            ),
            execution_panel.QueuedIntent(
                symbol="TSLA",
                action="SELL",
                side="sell",
                qty=3.0,
                target_notional=600.0,
                conviction=0.6,
                gate_allowed=False,
                gate_reasons=["macro_kill_switch"],
                allow_place=False,
                rationale="risk-reduce exit",
                client_order_id="advisory-TSLA-sell-1",
            ),
        ],
    )
    defaults.update(overrides)
    return execution_panel.ExecutionQueueSnapshot(**defaults)


def test_execution_queue_shape_and_intent_fields():
    with mock.patch.object(execution_panel, "read_execution_queue", return_value=_snapshot()):
        resp = client.get("/execution-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] is None
    assert body["mode"] == "review"
    assert body["n_intents"] == 2
    assert body["n_placeable"] == 1
    assert body["stale"] is False

    placeable = next(i for i in body["intents"] if i["symbol"] == "AAPL")
    assert placeable["allow_place"] is True
    assert placeable["gate_reasons"] == []

    blocked = next(i for i in body["intents"] if i["symbol"] == "TSLA")
    assert blocked["allow_place"] is False
    assert blocked["gate_reasons"] == ["macro_kill_switch"]


def test_execution_queue_cold_start_is_honest_not_fabricated():
    with mock.patch.object(execution_panel, "read_execution_queue", return_value=None):
        resp = client.get("/execution-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intents"] == []
    assert body["n_intents"] == 0
    assert body["mode"] == "off"
    assert "ROBINHOOD_EXECUTION_MODE" in body["reason"]


def test_execution_queue_unparsable_timestamp_degrades_to_null_not_nan():
    # generated_at="" makes queue_age_seconds/is_queue_stale fall through to
    # NaN internally; the endpoint must coerce that to JSON null, never emit
    # an invalid `NaN` token or a fabricated number.
    with mock.patch.object(
        execution_panel, "read_execution_queue", return_value=_snapshot(generated_at="")
    ):
        resp = client.get("/execution-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["age_seconds"] is None
    assert body["stale"] is True  # unparsable timestamp fails toward caution


def test_execution_queue_fail_open_no_token():
    with mock.patch.object(settings, "STATE_API_TOKEN", ""):
        with mock.patch.object(execution_panel, "read_execution_queue", return_value=None):
            resp = client.get("/execution-queue")
    assert resp.status_code == 200


def test_execution_queue_401_on_wrong_token():
    with mock.patch.object(settings, "STATE_API_TOKEN", "real-tok"):
        resp = client.get("/execution-queue", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_execution_queue_never_calls_mcp_or_places_orders():
    """Architectural pin: this module must not import anything that could place
    a Robinhood order. Only a live Claude Code agent session may do that (see
    execution/queue_builder.py's module docstring) — this endpoint is read-only
    by construction, not just by convention."""
    import ast
    import pathlib

    src = pathlib.Path(pilots_api.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    # robin_stocks (or any direct broker/MCP client) must never appear here.
    assert "robin_stocks" not in imported
