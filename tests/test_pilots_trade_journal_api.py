"""Tests for the 3 Retrospective Learning Loop / Trade Journal endpoints on
``api/pilots_api.py`` — ``GET /trade-journal/entries``, ``GET
/trade-journal/insights``, ``GET /trade-journal/bridge-status``.

These endpoints are thin glue over four already-real, already-tested
(READ-ONLY, never-raises) modules:

* ``pilots.retrospective_composer.compose_trade_retrospectives``
* ``pilots.retrospective_narrative.build_trade_narrative``
* ``pilots.retrospective_insights.batch_insights``
* ``pilots.bridge_completeness.bridge_completeness_summary``

so this file focuses on WIRING (the endpoints call the right functions with
the right arguments, the response shape matches, auth is the fail-open read
tier, and a cold/missing DB never 500s) rather than re-testing those
modules' own internals — see ``tests/test_retrospective_composer.py``,
``tests/test_retrospective_narrative.py``,
``tests/test_retrospective_cohort_insights.py``, and
``tests/test_bridge_completeness_metric.py`` for that.

``conftest.py``'s session-wide autouse ``_isolate_paper_and_transactions_db_in_tests``
and ``_isolate_trade_decision_snapshot_db_in_tests`` fixtures already point
``PaperAccountStore``'s / ``TransactionsStore``'s / ``TradeDecisionSnapshotStore``'s
default DB resolvers at a private per-test temp file / ``:memory:`` db for
every test in this file, with NO explicit ``db_url`` needed here — both the
write-mode store this file constructs directly and the read-only store the
endpoint constructs internally resolve to the SAME isolated database within
one test, since the monkeypatch is scoped to that test's own fixture
instances.

``data.historical_store.HistoricalStore`` is NOT covered by any autouse
isolation fixture (confirmed by reading ``conftest.py`` directly — its
``_isolate_forecast_tracker_db_in_tests`` docstring explicitly says it
deliberately does NOT repoint ``db_config.resolve_database_url`` broadly for
this exact reason). The composer's ``compose_trade_retrospectives`` batch
constructs a bare, write-mode ``HistoricalStore()`` with no explicit
``db_path`` to fetch OHLC bars for the MFE/MAE/Edge-Ratio evaluation section
-- left unpatched, that would resolve to the operator's real, shared
``~/.stockpy_local/quant_platform.db`` and could attempt a live market-data
fetch. Every test in this file that reaches the composer therefore patches
``data.historical_store.HistoricalStore`` at its SOURCE module to a fake that
always returns an empty DataFrame -- mirroring
``tests/test_retrospective_composer.py``'s own documented convention exactly
-- which also conveniently exercises this endpoint's honest
``evaluation.available=False`` path end-to-end.
"""

from __future__ import annotations

from unittest import mock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api.pilots_api as pilots_api
from data.paper_account_store import PaperAccountStore
from settings import settings

# Loopback host -- see tests/test_pilots_api.py's own docstring for why this
# matters: Starlette's TestClient otherwise defaults request.client.host to
# the literal string "testclient", which trips require_read_token's
# fail-closed-when-non-loopback-and-no-token branch.
client = TestClient(pilots_api.app, client=("127.0.0.1", 54124))


class _FakeEmptyHistoricalStore:
    """Stand-in for ``data.historical_store.HistoricalStore`` that never
    touches disk or network -- constructed with zero args (matching the
    composer's own ``HistoricalStore()`` call), ``get_bars`` always returns
    an empty DataFrame so the composer's evaluation section degrades to its
    honest ``available=False`` / "no price history" branch."""

    def __init__(self, *args, **kwargs):
        pass

    def get_bars(self, symbol, lookback_days=756):
        return pd.DataFrame()


def _patch_historical_store(monkeypatch):
    monkeypatch.setattr(
        "data.historical_store.HistoricalStore", _FakeEmptyHistoricalStore
    )


def _open_and_close(
    store: PaperAccountStore,
    symbol: str,
    strategy_id: str = "trade-journal-test",
) -> None:
    """Open then fully close a 10-share long on ``symbol`` via two real
    ``apply_fill`` calls -- the exact production call shape that drives
    ``_record_closed_trade`` (mirrors
    ``tests/test_bridge_completeness_metric.py``'s own helper)."""
    assert store.apply_fill(
        f"{symbol}_buy", symbol, "buy", 10.0, 100.0, strategy_id=strategy_id
    ) is True
    assert store.apply_fill(
        f"{symbol}_sell_{id(object())}", symbol, "sell", 10.0, 110.0, strategy_id=strategy_id
    ) is True


@pytest.fixture(autouse=True)
def _fail_open_read_tier(monkeypatch):
    """Every test in this file exercises the fail-open read tier by default
    -- STATE_API_TOKEN unset + a loopback TestClient host means no
    Authorization header is required."""
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)


# ---------------------------------------------------------------------------
# GET /trade-journal/entries
# ---------------------------------------------------------------------------


def test_entries_happy_path_with_real_composed_data(monkeypatch):
    _patch_historical_store(monkeypatch)
    store = PaperAccountStore()
    _open_and_close(store, "AAPL")

    resp = client.get("/trade-journal/entries")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert len(body["entries"]) == 1

    entry = body["entries"][0]
    assert entry["symbol"] == "AAPL"
    assert entry["side"] in ("BUY", "SELL")  # PaperClosedTrade's recorded side
    assert entry["realized_pnl"] == pytest.approx(100.0)  # 10 * (110 - 100)
    assert entry["realized_pnl_pct"] is not None

    # decision.state is NEVER inferred from strategy_id (composer's own
    # anti-shortcut rule) -- no snapshot was ever recorded for this trade,
    # so it must read "unknown", never "manual"/"signal_driven".
    assert entry["decision"]["state"] == "unknown"

    # No real bars were available (patched HistoricalStore always returns
    # empty) -- evaluation must degrade honestly, never fabricate MFE/MAE.
    assert entry["evaluation"]["available"] is False
    assert entry["evaluation"]["mfe"] is None
    assert entry["evaluation"]["mae"] is None
    assert isinstance(entry["evaluation"]["reason"], str) and entry["evaluation"]["reason"]

    # The narrative is a real, non-empty, plain-text sentence built from the
    # SAME composed record -- not a placeholder.
    assert isinstance(entry["narrative"], str) and entry["narrative"]
    assert "AAPL" in entry["narrative"]
    assert "Entry context wasn't captured for this trade." in entry["narrative"]


def test_entries_filters_by_symbol(monkeypatch):
    _patch_historical_store(monkeypatch)
    store = PaperAccountStore()
    _open_and_close(store, "AAPL")
    _open_and_close(store, "MSFT")

    resp = client.get("/trade-journal/entries", params={"symbol": "MSFT"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["entries"][0]["symbol"] == "MSFT"


def test_entries_empty_state_no_closed_trades(monkeypatch):
    _patch_historical_store(monkeypatch)
    # Construct a write-mode store so the schema exists, but never close a
    # trade -- the genuinely-empty (not cold/missing) case.
    PaperAccountStore()

    resp = client.get("/trade-journal/entries")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"entries": [], "count": 0}


def test_entries_never_500_on_cold_missing_db(monkeypatch):
    _patch_historical_store(monkeypatch)
    # Deliberately never construct any store here -- the isolated tmp_path
    # db file for this test does not exist yet, so PaperAccountStore(readonly=True)
    # must degrade to an honest empty result rather than raising.
    resp = client.get("/trade-journal/entries")
    assert resp.status_code == 200
    assert resp.json() == {"entries": [], "count": 0}


def test_entries_limit_is_bounded():
    resp = client.get("/trade-journal/entries", params={"limit": 0})
    assert resp.status_code == 422
    resp = client.get("/trade-journal/entries", params={"limit": 500})
    assert resp.status_code == 422


def test_entries_read_token_gates_the_endpoint(monkeypatch):
    _patch_historical_store(monkeypatch)
    with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
        assert client.get("/trade-journal/entries").status_code == 401
        resp = client.get(
            "/trade-journal/entries", headers={"Authorization": "Bearer read-tok"}
        )
        assert resp.status_code == 200
        wrong = client.get(
            "/trade-journal/entries", headers={"Authorization": "Bearer WRONG"}
        )
        assert wrong.status_code == 401


# ---------------------------------------------------------------------------
# GET /trade-journal/insights
# ---------------------------------------------------------------------------


def test_insights_shape_never_has_a_combined_overall_figure():
    resp = client.get("/trade-journal/insights")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"calibration", "cohorts"}
    assert isinstance(body["calibration"], dict)
    assert set(body["cohorts"].keys()) == {"signal_driven", "manual", "unknown"}
    assert "overall" not in body["cohorts"]
    assert "overall" not in body


def test_insights_cohorts_reflect_real_closed_trades(monkeypatch):
    _patch_historical_store(monkeypatch)
    store = PaperAccountStore()
    _open_and_close(store, "AAPL")

    resp = client.get("/trade-journal/insights")
    assert resp.status_code == 200
    body = resp.json()
    # No decision snapshot was ever recorded -- the trade lands in "unknown",
    # not "manual"/"signal_driven" (same anti-inference rule as /entries).
    assert body["cohorts"]["unknown"]["n_trades"] == 1
    assert body["cohorts"]["signal_driven"]["n_trades"] == 0
    assert body["cohorts"]["manual"]["n_trades"] == 0


def test_insights_never_500_on_cold_missing_db():
    resp = client.get("/trade-journal/insights")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cohorts"]["signal_driven"]["win_rate"] is None
    assert body["cohorts"]["manual"]["n_trades"] == 0


def test_insights_read_token_gates_the_endpoint():
    with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
        assert client.get("/trade-journal/insights").status_code == 401
        resp = client.get(
            "/trade-journal/insights", headers={"Authorization": "Bearer read-tok"}
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /trade-journal/bridge-status
# ---------------------------------------------------------------------------


def test_bridge_status_no_closed_trades():
    resp = client.get("/trade-journal/bridge-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["n_trades_checked"] == 0
    assert body["n_bridged"] == 0
    assert body["completeness_pct"] is None
    assert isinstance(body["reason"], str) and body["reason"]


def test_bridge_status_with_real_trades_bridge_disabled(monkeypatch):
    _patch_historical_store(monkeypatch)
    assert settings.PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED is False
    store = PaperAccountStore()
    _open_and_close(store, "AAPL")
    _open_and_close(store, "MSFT")

    resp = client.get("/trade-journal/bridge-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["n_trades_checked"] == 2
    assert body["n_bridged"] == 0
    # A REAL, measured 0.0 (the bridge never ran) -- not a fabricated None
    # and not a fabricated 100 -- see bridge_completeness.py's own docstring.
    assert body["completeness_pct"] == pytest.approx(0.0)


def test_bridge_status_window_param_is_bounded():
    resp = client.get("/trade-journal/bridge-status", params={"window": 0})
    assert resp.status_code == 422
    resp = client.get("/trade-journal/bridge-status", params={"window": 5000})
    assert resp.status_code == 422


def test_bridge_status_never_500_on_cold_missing_db():
    resp = client.get("/trade-journal/bridge-status")
    assert resp.status_code == 200
    assert resp.json()["n_trades_checked"] == 0


def test_bridge_status_read_token_gates_the_endpoint():
    with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
        assert client.get("/trade-journal/bridge-status").status_code == 401
        resp = client.get(
            "/trade-journal/bridge-status", headers={"Authorization": "Bearer read-tok"}
        )
        assert resp.status_code == 200
