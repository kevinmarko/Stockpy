"""Tests for GET /execution-queue — the read-only Robinhood execution-queue
surface added to api/pilots_api.py.

This endpoint reuses shared.robinhood_execution_panel.read_execution_queue (the
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
import shared.robinhood_execution_panel as execution_panel
import api.pilots_api as pilots_api

# Starlette's TestClient defaults request.client.host to the literal
# string "testclient" -- NOT loopback -- which would trip
# api.auth.require_read_token's new fail-closed-when-non-loopback branch
# on every one of this file's existing zero-config-behavior assertions.
# An explicit loopback host here is what these tests have always meant.
client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))


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


# ===========================================================================
# Query-param filters (action/follow_type/status_filter/min_conviction),
# the real (non-guessed) `follow_type` attribution, `available_follow_types`,
# and the `/api/queue` alias.
# ===========================================================================


def _intent(**overrides) -> execution_panel.QueuedIntent:
    defaults = dict(
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
        strategy="",
    )
    defaults.update(overrides)
    return execution_panel.QueuedIntent(**defaults)


def _multi_attribution_snapshot() -> execution_panel.ExecutionQueueSnapshot:
    """Four intents spanning every real attribution bucket
    `get_execution_queue` derives from `QueuedIntent.strategy`: a base
    advisory intent (a derived composite label, no "Follow:"/"Composed:"
    prefix), a single-pilot follow, a multi-pilot composed intent, and a
    legacy intent with no `strategy` at all (pre-dates the field). The
    snapshot's own declared `n_intents`/`n_placeable` are deliberately WRONG
    (mismatched from the real intents list) so a test can prove the endpoint
    recomputes from the actual (possibly filtered) intents rather than
    echoing the snapshot's raw totals.
    """
    return execution_panel.ExecutionQueueSnapshot(
        generated_at=datetime.now(timezone.utc).isoformat(),
        mode="review",
        kill_switch_active=False,
        max_notional_per_order=500.0,
        n_intents=999,
        n_placeable=999,
        intents=[
            _intent(
                symbol="AAPL",
                action="BUY",
                side="buy",
                conviction=0.8,
                allow_place=True,
                # A real advisory-derived label containing the word "macd" --
                # pins that follow_type is read from `strategy`, never
                # keyword-sniffed out of free text.
                rationale="MACD crossover confirms trend strength.",
                strategy="high-conviction multi-signal composite [risk-on]",
                client_order_id="advisory-AAPL-buy-1",
            ),
            _intent(
                symbol="TSLA",
                action="SELL",
                side="sell",
                conviction=0.6,
                gate_allowed=False,
                gate_reasons=["macro_kill_switch"],
                allow_place=False,
                rationale="risk-reduce exit",
                strategy="Follow:trend-following",
                client_order_id="follow-trend-following-TSLA-sell-1",
            ),
            _intent(
                symbol="MSFT",
                action="BUY",
                side="buy",
                conviction=0.3,
                allow_place=True,
                rationale="netted across two follows",
                strategy="Composed: Follow:trend-following, Follow:dip-buyer",
                client_order_id="composed-MSFT-buy-1",
            ),
            _intent(
                symbol="NVDA",
                action="BUY",
                side="buy",
                conviction=0.9,
                allow_place=True,
                rationale="legacy queue file, no strategy field",
                strategy="",
                client_order_id="advisory-NVDA-buy-1",
            ),
        ],
    )


class TestExecutionQueueFilters:
    def test_follow_type_is_real_attribution_not_a_rationale_guess(self):
        """AAPL's rationale contains the word 'macd', but its real `strategy`
        is a plain advisory composite label -- follow_type must reflect the
        real attribution ('advisory'), never a keyword match against
        rationale text (CONSTRAINT #4)."""
        with mock.patch.object(
            execution_panel, "read_execution_queue", return_value=_multi_attribution_snapshot()
        ):
            resp = client.get("/execution-queue")
        assert resp.status_code == 200
        by_symbol = {i["symbol"]: i for i in resp.json()["intents"]}
        assert by_symbol["AAPL"]["follow_type"] == "advisory"
        assert by_symbol["TSLA"]["follow_type"] == "trend-following"
        assert by_symbol["MSFT"]["follow_type"] == "composed"
        assert by_symbol["NVDA"]["follow_type"] == "advisory"  # no strategy -> honest fallback

    def test_available_follow_types_reflects_unfiltered_set(self):
        with mock.patch.object(
            execution_panel, "read_execution_queue", return_value=_multi_attribution_snapshot()
        ):
            resp = client.get("/execution-queue", params={"action": "SELL"})
        assert resp.status_code == 200
        body = resp.json()
        # Filtered down to just TSLA (SELL)...
        assert [i["symbol"] for i in body["intents"]] == ["TSLA"]
        # ...but available_follow_types still lists every attribution present
        # in the UNFILTERED queue, not just the filtered-down set.
        assert body["available_follow_types"] == ["advisory", "composed", "trend-following"]

    def test_follow_type_filter_matches_real_pilot_id(self):
        with mock.patch.object(
            execution_panel, "read_execution_queue", return_value=_multi_attribution_snapshot()
        ):
            resp = client.get("/execution-queue", params={"follow_type": "trend-following"})
        assert resp.status_code == 200
        assert [i["symbol"] for i in resp.json()["intents"]] == ["TSLA"]

    def test_follow_type_filter_composed(self):
        with mock.patch.object(
            execution_panel, "read_execution_queue", return_value=_multi_attribution_snapshot()
        ):
            resp = client.get("/execution-queue", params={"follow_type": "composed"})
        assert resp.status_code == 200
        assert [i["symbol"] for i in resp.json()["intents"]] == ["MSFT"]

    def test_action_filter(self):
        with mock.patch.object(
            execution_panel, "read_execution_queue", return_value=_multi_attribution_snapshot()
        ):
            resp = client.get("/execution-queue", params={"action": "SELL"})
        assert resp.status_code == 200
        assert [i["symbol"] for i in resp.json()["intents"]] == ["TSLA"]

    def test_status_filter_ready_excludes_blocked(self):
        with mock.patch.object(
            execution_panel, "read_execution_queue", return_value=_multi_attribution_snapshot()
        ):
            resp = client.get("/execution-queue", params={"status_filter": "Ready"})
        assert resp.status_code == 200
        symbols = {i["symbol"] for i in resp.json()["intents"]}
        assert symbols == {"AAPL", "MSFT", "NVDA"}

    def test_status_filter_blocked_excludes_ready(self):
        with mock.patch.object(
            execution_panel, "read_execution_queue", return_value=_multi_attribution_snapshot()
        ):
            resp = client.get("/execution-queue", params={"status_filter": "Blocked"})
        assert resp.status_code == 200
        assert [i["symbol"] for i in resp.json()["intents"]] == ["TSLA"]

    def test_min_conviction_filter(self):
        with mock.patch.object(
            execution_panel, "read_execution_queue", return_value=_multi_attribution_snapshot()
        ):
            resp = client.get("/execution-queue", params={"min_conviction": 0.7})
        assert resp.status_code == 200
        symbols = {i["symbol"] for i in resp.json()["intents"]}
        assert symbols == {"AAPL", "NVDA"}  # 0.8 and 0.9; TSLA=0.6, MSFT=0.3 excluded

    def test_counts_reflect_filtered_result_not_raw_snapshot_totals(self):
        """The snapshot's own declared n_intents/n_placeable are 999/999
        (deliberately wrong) -- the endpoint must report the FILTERED
        count, matching what `intents` actually contains, never the raw
        declared totals."""
        with mock.patch.object(
            execution_panel, "read_execution_queue", return_value=_multi_attribution_snapshot()
        ):
            unfiltered = client.get("/execution-queue")
            filtered = client.get("/execution-queue", params={"action": "SELL"})
        assert unfiltered.json()["n_intents"] == 4
        assert unfiltered.json()["n_placeable"] == 3
        assert filtered.json()["n_intents"] == 1
        assert filtered.json()["n_placeable"] == 0

    def test_all_bypasses_every_filter(self):
        with mock.patch.object(
            execution_panel, "read_execution_queue", return_value=_multi_attribution_snapshot()
        ):
            resp = client.get(
                "/execution-queue",
                params={"action": "ALL", "follow_type": "ALL", "status_filter": "ALL"},
            )
        assert resp.status_code == 200
        assert len(resp.json()["intents"]) == 4


class TestApiQueueAlias:
    def test_matches_execution_queue_shape_and_filters(self):
        with mock.patch.object(
            execution_panel, "read_execution_queue", return_value=_multi_attribution_snapshot()
        ):
            direct = client.get("/execution-queue", params={"action": "SELL"}).json()
            alias = client.get("/api/queue", params={"action": "SELL"}).json()
        # age_seconds is computed against wall-clock "now" independently per
        # request, so it can differ by a fraction of a millisecond between
        # the two sequential calls -- everything else must match exactly.
        direct.pop("age_seconds")
        alias.pop("age_seconds")
        assert alias == direct

    def test_cold_start_is_honest_not_fabricated(self):
        with mock.patch.object(execution_panel, "read_execution_queue", return_value=None):
            resp = client.get("/api/queue")
        assert resp.status_code == 200
        body = resp.json()
        assert body["intents"] == []
        assert body["available_follow_types"] == []
        assert "ROBINHOOD_EXECUTION_MODE" in body["reason"]


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
