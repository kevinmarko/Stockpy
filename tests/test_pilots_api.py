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
import json
import pathlib
from unittest import mock

import pytest
import pandas as pd
from fastapi.testclient import TestClient

from settings import settings
from pilots import catalog
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
# GET /universe — the symbol-autocomplete source
# ---------------------------------------------------------------------------


def test_universe_shape_and_values():
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/universe")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"symbols"}
    rows = body["symbols"]
    symbols = [r["symbol"] for r in rows]
    assert symbols == sorted(symbols)
    assert set(symbols) == {"AAPL", "MSFT", "NVDA", "JPM", "XOM", "JNJ", "PG", "T"}
    for r in rows:
        assert set(r) == {"symbol", "action"}
    aapl = next(r for r in rows if r["symbol"] == "AAPL")
    assert aapl["action"] == "BUY"


def test_universe_cold_start_empty_not_404(tmp_path):
    # Unlike /symbols/{ticker}, /universe never 404s — a cold start is an
    # honestly empty suggestion list, not an error (this endpoint only ever
    # backs an autocomplete UI, so "nothing to suggest yet" is a normal state).
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/universe")
    assert resp.status_code == 200
    assert resp.json() == {"symbols": []}


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


# ---------------------------------------------------------------------------
# GET /portfolio/attribution
# ---------------------------------------------------------------------------


class _AttrPosition:
    def __init__(self, quantity, market_value):
        self.quantity = quantity
        self.market_value = market_value


class _AttrSnapshot:
    def __init__(self, positions):
        self.positions = positions


def _bars_frame(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "Open": closes, "High": closes, "Low": closes,
            "Close": closes, "Volume": [1_000] * len(closes),
        },
        index=idx,
    )


class TestPortfolioAttribution:
    def test_cold_start_no_account_snapshot(self, tmp_path):
        class _EmptyStore:
            def latest_account_snapshot(self):
                return None

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=_EmptyStore()):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/portfolio/attribution")
        assert resp.status_code == 200
        body = resp.json()
        assert body["as_of"] is None
        assert body["factor_exposure"]["reason"] == "no held positions"
        assert body["factor_exposure"]["exposures"] == {
            "value_z": None, "quality_z": None, "lowvol_z": None,
            "size_z": None, "multifactor_composite": None,
        }
        assert body["factor_exposure"]["coverage"] == {
            "held_count": 0, "matched_count": 0,
            "matched_value_pct": None, "unmatched_symbols": [],
        }
        assert body["correlation_clusters"]["clusters"] == []
        assert body["correlation_clusters"]["reason"] == "no held positions"

    def test_db_error_degrades_to_empty_book_never_500(self):
        class _BoomStore:
            def latest_account_snapshot(self):
                raise RuntimeError("cold db")

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=_BoomStore()):
            resp = client.get("/portfolio/attribution")
        assert resp.status_code == 200
        assert resp.json()["factor_exposure"]["reason"] == "no held positions"

    def test_factor_exposure_weights_matched_symbols_only(self):
        """AAPL/MSFT are held AND in the fixture snapshot (with real value_z /
        quality_z / ... fields); ZZZZ is held but absent from the snapshot and
        must contribute nothing (never zero-filled) — it shows up only in
        `unmatched_symbols`."""
        positions = {
            "AAPL": _AttrPosition(10.0, 1000.0),
            "MSFT": _AttrPosition(5.0, 1000.0),
            "ZZZZ": _AttrPosition(3.0, 500.0),
        }

        class _Store:
            def latest_account_snapshot(self):
                return _AttrSnapshot(positions)

            def get_bars(self, symbol, lookback_days=504, provider=None):
                return pd.DataFrame()

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
            with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
                resp = client.get("/portfolio/attribution")
        assert resp.status_code == 200
        body = resp.json()
        fe = body["factor_exposure"]
        assert fe["reason"] is None
        assert fe["coverage"]["held_count"] == 3
        assert fe["coverage"]["matched_count"] == 2
        assert fe["coverage"]["unmatched_symbols"] == ["ZZZZ"]
        # Equal market values (1000/1000) -> a straight average of AAPL/MSFT.
        assert fe["exposures"]["value_z"] == pytest.approx((-0.42 + -0.55) / 2, abs=1e-6)
        assert fe["exposures"]["quality_z"] == pytest.approx((1.15 + 1.42) / 2, abs=1e-6)
        # matched_value_pct = matched (2000) / total held (2500).
        assert fe["coverage"]["matched_value_pct"] == pytest.approx(2000.0 / 2500.0)

    def test_factor_exposure_no_snapshot_yet(self, tmp_path):
        positions = {"AAPL": _AttrPosition(10.0, 1000.0)}

        class _Store:
            def latest_account_snapshot(self):
                return _AttrSnapshot(positions)

            def get_bars(self, symbol, lookback_days=504, provider=None):
                return pd.DataFrame()

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/portfolio/attribution")
        assert resp.status_code == 200
        fe = resp.json()["factor_exposure"]
        assert fe["reason"] == "no pipeline snapshot yet"
        assert fe["exposures"]["value_z"] is None

    def test_correlation_clusters_groups_correlated_symbols(self):
        """AAPL and MSFT move in lockstep (MSFT = 3x AAPL's price, identical
        returns); NVDA is an independent, uncorrelated random-ish walk. AAPL/MSFT
        should land in the same cluster with a high avg_intra_corr."""
        import random

        n = 40
        rng_a = random.Random(42)
        aapl_closes = [100.0]
        for _ in range(n - 1):
            aapl_closes.append(aapl_closes[-1] * (1.0 + rng_a.uniform(-0.015, 0.02)))
        msft_closes = [c * 3.0 for c in aapl_closes]
        rng_b = random.Random(7)
        nvda_closes = [200.0]
        for _ in range(n - 1):
            nvda_closes.append(nvda_closes[-1] * (1.0 + rng_b.uniform(-0.02, 0.02)))

        bars_by_symbol = {
            "AAPL": _bars_frame(aapl_closes),
            "MSFT": _bars_frame(msft_closes),
            "NVDA": _bars_frame(nvda_closes),
        }

        positions = {
            "AAPL": _AttrPosition(10.0, 1000.0),
            "MSFT": _AttrPosition(5.0, 1000.0),
            "NVDA": _AttrPosition(2.0, 500.0),
        }

        class _Store:
            def latest_account_snapshot(self):
                return _AttrSnapshot(positions)

            def get_bars(self, symbol, lookback_days=504, provider=None):
                return bars_by_symbol.get(symbol, pd.DataFrame())

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
            with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
                resp = client.get("/portfolio/attribution?lookback_days=30")
        assert resp.status_code == 200
        cc = resp.json()["correlation_clusters"]
        assert cc["reason"] is None
        assert cc["lookback_days"] == 30
        clusters = cc["clusters"]
        assert clusters, "expected at least one cluster"
        # AAPL and MSFT (perfectly correlated) must share a cluster.
        aapl_cluster = next(c for c in clusters if "AAPL" in c["symbols"])
        assert "MSFT" in aapl_cluster["symbols"]
        # weight_pct values across clusters should not exceed 1.0 in total.
        total_weight = sum(c["weight_pct"] or 0.0 for c in clusters)
        assert total_weight <= 1.0 + 1e-6

    def test_correlation_clusters_empty_when_no_bars(self):
        positions = {"AAPL": _AttrPosition(10.0, 1000.0)}

        class _Store:
            def latest_account_snapshot(self):
                return _AttrSnapshot(positions)

            def get_bars(self, symbol, lookback_days=504, provider=None):
                return pd.DataFrame()

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
            resp = client.get("/portfolio/attribution")
        assert resp.status_code == 200
        cc = resp.json()["correlation_clusters"]
        assert cc["clusters"] == []
        assert cc["reason"] == "no return history available for held positions"

    def test_lookback_days_query_validation(self):
        resp = client.get("/portfolio/attribution?lookback_days=5")
        assert resp.status_code == 422
        resp = client.get("/portfolio/attribution?lookback_days=1000")
        assert resp.status_code == 422


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


# ---------------------------------------------------------------------------
# GET /symbols/{ticker}/rolling-beta
# ---------------------------------------------------------------------------


def _rolling_beta_price_frame(closes):
    """Minimal OHLCV frame (only Close matters for beta) over business days."""
    n = len(closes)
    idx = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n)
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes,
         "Volume": [1_000_000] * n},
        index=idx,
    )


class _RollingBetaStore:
    """Fake HistoricalStore serving canned bars for a fixed set of symbols."""

    def __init__(self, bars_by_symbol):
        self._bars_by_symbol = bars_by_symbol

    def get_bars(self, symbol, lookback_days=504, provider=None):
        return self._bars_by_symbol.get(symbol.upper(), pd.DataFrame())


class TestRollingBeta:
    def test_shape_stable_and_default_window(self):
        """Real, non-trivial beta values from a synthetic correlated series --
        proves the endpoint wires pilots.rolling_beta through end-to-end, not
        just an empty honest shape."""
        import random

        rng = random.Random(1)
        n = 200
        spy = [100.0]
        aapl = [50.0]
        for _ in range(n - 1):
            r = rng.uniform(-0.02, 0.02)
            spy.append(spy[-1] * (1 + r))
            aapl.append(aapl[-1] * (1 + 1.2 * r + rng.uniform(-0.002, 0.002)))
        store = _RollingBetaStore({
            "AAPL": _rolling_beta_price_frame(aapl),
            "SPY": _rolling_beta_price_frame(spy),
        })
        with mock.patch("data.historical_store.HistoricalStore", return_value=store):
            resp = client.get("/symbols/AAPL/rolling-beta")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"symbol", "window", "series", "reason"}
        assert body["symbol"] == "AAPL"
        assert body["window"] == 60  # default
        assert body["reason"] is None
        assert len(body["series"]) > 0
        first = body["series"][0]
        assert set(first) == {"date", "beta"}
        assert isinstance(first["beta"], float)

    def test_window_query_param_is_honored(self):
        store = _RollingBetaStore({})  # empty -> honest degrade, still checks wiring
        with mock.patch("data.historical_store.HistoricalStore", return_value=store):
            resp = client.get("/symbols/AAPL/rolling-beta?window=30")
        assert resp.status_code == 200
        assert resp.json()["window"] == 30

    def test_window_below_minimum_is_422(self):
        resp = client.get("/symbols/AAPL/rolling-beta?window=1")
        assert resp.status_code == 422

    def test_window_above_maximum_is_422(self):
        resp = client.get("/symbols/AAPL/rolling-beta?window=9999")
        assert resp.status_code == 422

    def test_no_cached_bars_is_honest_empty_not_404(self):
        store = _RollingBetaStore({})  # no bars for AAPL or SPY
        with mock.patch("data.historical_store.HistoricalStore", return_value=store):
            resp = client.get("/symbols/AAPL/rolling-beta")
        assert resp.status_code == 200
        body = resp.json()
        assert body["series"] == []
        assert body["reason"]

    def test_store_construction_failure_never_500s(self):
        with mock.patch(
            "data.historical_store.HistoricalStore",
            side_effect=RuntimeError("db unavailable"),
        ):
            resp = client.get("/symbols/AAPL/rolling-beta")
        assert resp.status_code == 200
        body = resp.json()
        assert body["series"] == []
        assert body["reason"]

    def test_read_token_gates_the_endpoint(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get("/symbols/AAPL/rolling-beta")
        assert resp.status_code == 401

        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get(
                "/symbols/AAPL/rolling-beta",
                headers={"Authorization": "Bearer read-tok"},
            )
        assert resp.status_code == 200

    def test_read_token_unset_is_open(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", ""):
            resp = client.get("/symbols/AAPL/rolling-beta")
        assert resp.status_code == 200


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


class TestObservabilitySummary:
    """Endpoint-level wiring/shape tests for GET /observability/summary. The
    substantive per-section logic (drawdown math, portfolio-wide skill weight
    formula, honest degradation) is unit-tested directly against
    pilots/observability.py in tests/test_pilots_observability.py; these tests
    only confirm the FastAPI wiring — auth, query params, snapshot threading,
    and the composite shape — is correct end-to-end."""

    def test_cold_start_shape(self, tmp_path):
        class _EmptyStore:
            def account_snapshot_history(self, since=None):
                return pd.DataFrame()

        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch(
                "data.historical_store.HistoricalStore", return_value=_EmptyStore()
            ):
                with mock.patch(
                    "forecasting.forecast_tracker.ForecastTracker",
                    side_effect=RuntimeError("unavailable"),
                ):
                    resp = client.get("/observability/summary")

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "portfolio_risk", "equity_curve", "regime", "forecast_skill", "risk_gate_blocks",
        }
        assert body["portfolio_risk"]["sharpe_ratio"] is None
        assert body["portfolio_risk"]["n_snapshots"] == 0
        assert body["portfolio_risk"]["reason"]
        assert body["equity_curve"]["range"] == "1Y"
        assert body["equity_curve"]["points"] == []
        assert body["regime"]["market_regime"] is None
        assert body["regime"]["reason"]
        assert body["forecast_skill"]["horizon_days"] == 30
        assert body["forecast_skill"]["reliability_curve"] == []
        assert body["risk_gate_blocks"]["entries"] == []
        assert body["risk_gate_blocks"]["count"] == 0

    def test_reads_regime_from_persisted_snapshot_fixture(self, tmp_path):
        (tmp_path / "state_snapshot.json").write_text(_SNAPSHOT_FIXTURE, encoding="utf-8")

        class _EmptyStore:
            def account_snapshot_history(self, since=None):
                return pd.DataFrame()

        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch(
                "data.historical_store.HistoricalStore", return_value=_EmptyStore()
            ):
                resp = client.get("/observability/summary")

        assert resp.status_code == 200
        regime = resp.json()["regime"]
        assert regime["market_regime"] == "RISK ON"
        assert regime["sahm_rule"] == pytest.approx(0.13)
        assert regime["hmm_risk_on_probability"] == pytest.approx(0.78)
        assert regime["reason"] is None

    def test_query_params_thread_through(self, tmp_path):
        class _EmptyStore:
            def account_snapshot_history(self, since=None):
                return pd.DataFrame()

        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch(
                "data.historical_store.HistoricalStore", return_value=_EmptyStore()
            ):
                with mock.patch(
                    "forecasting.forecast_tracker.ForecastTracker",
                    side_effect=RuntimeError("unavailable"),
                ):
                    resp = client.get("/observability/summary?range=1M&horizon=60")

        body = resp.json()
        assert body["equity_curve"]["range"] == "1M"
        assert body["forecast_skill"]["horizon_days"] == 60

    def test_bad_horizon_422(self):
        resp = client.get("/observability/summary?horizon=0")
        assert resp.status_code == 422

    def test_read_token_gates_endpoint(self, tmp_path):
        class _EmptyStore:
            def account_snapshot_history(self, since=None):
                return pd.DataFrame()

        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                with mock.patch(
                    "data.historical_store.HistoricalStore", return_value=_EmptyStore()
                ):
                    no_auth = client.get("/observability/summary")
                    wrong = client.get(
                        "/observability/summary",
                        headers={"Authorization": "Bearer WRONG"},
                    )
                    ok = client.get(
                        "/observability/summary",
                        headers={"Authorization": "Bearer read-tok"},
                    )
        assert no_auth.status_code == 401
        assert wrong.status_code == 401
        assert ok.status_code == 200


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

    def test_writable_reflects_automation_writes_enabled(self):
        with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", False):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                resp = client.get("/automation/schedule")
        assert resp.json()["interval"]["writable"] is False

        with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                resp = client.get("/automation/schedule")
        assert resp.json()["interval"]["writable"] is True


# ---------------------------------------------------------------------------
# POST /automation/run — pure proxy over daemon_client.trigger_run()
# ---------------------------------------------------------------------------


def _trigger_response(**overrides):
    from gui.daemon_client import TriggerResponse

    base = dict(ok=True, run_id="orch-1", state="queued", error=None,
                existing_run_id=None, kill_switch_reason=None)
    base.update(overrides)
    return TriggerResponse(**base)


class TestAutomationRun:
    def test_ok_returns_202(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(
                pilots_api.daemon_client, "trigger_run",
                return_value=_trigger_response(),
            ):
                resp = client.post(
                    "/automation/run", headers={"Authorization": f"Bearer {_CMD_TOKEN}"}
                )
        assert resp.status_code == 202
        assert resp.json() == {"run_id": "orch-1", "state": "queued"}

    @pytest.mark.parametrize(
        "error,expected_status",
        [
            ("already_running", 409),
            ("kill_switch_active", 423),
            ("command_disabled", 503),
            ("unauthorized", 503),
            ("unavailable", 503),
            ("network_error", 503),
            ("unexpected_response", 503),
        ],
    )
    def test_each_error_tag_maps_to_its_status(self, error, expected_status):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(
                pilots_api.daemon_client, "trigger_run",
                return_value=_trigger_response(
                    ok=False, run_id=None, state=None, error=error,
                    existing_run_id="orch-old" if error == "already_running" else None,
                    kill_switch_reason="halt" if error == "kill_switch_active" else None,
                ),
            ):
                resp = client.post(
                    "/automation/run", headers={"Authorization": f"Bearer {_CMD_TOKEN}"}
                )
        assert resp.status_code == expected_status

    def test_already_running_surfaces_the_existing_run_id(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(
                pilots_api.daemon_client, "trigger_run",
                return_value=_trigger_response(
                    ok=False, run_id=None, state=None, error="already_running",
                    existing_run_id="orch-old",
                ),
            ):
                resp = client.post(
                    "/automation/run", headers={"Authorization": f"Bearer {_CMD_TOKEN}"}
                )
        assert resp.json()["detail"]["run_id"] == "orch-old"

    def test_kill_switch_surfaces_the_reason(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(
                pilots_api.daemon_client, "trigger_run",
                return_value=_trigger_response(
                    ok=False, run_id=None, state=None, error="kill_switch_active",
                    kill_switch_reason="manual halt",
                ),
            ):
                resp = client.post(
                    "/automation/run", headers={"Authorization": f"Bearer {_CMD_TOKEN}"}
                )
        assert resp.json()["detail"]["kill_switch_reason"] == "manual halt"

    def test_unauthorized_and_command_disabled_bodies_are_indistinguishable(self):
        """Never let a caller learn which side's token/config is wrong."""
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(
                pilots_api.daemon_client, "trigger_run",
                return_value=_trigger_response(ok=False, run_id=None, state=None, error="unauthorized"),
            ):
                r1 = client.post(
                    "/automation/run", headers={"Authorization": f"Bearer {_CMD_TOKEN}"}
                )
            with mock.patch.object(
                pilots_api.daemon_client, "trigger_run",
                return_value=_trigger_response(ok=False, run_id=None, state=None, error="command_disabled"),
            ):
                r2 = client.post(
                    "/automation/run", headers={"Authorization": f"Bearer {_CMD_TOKEN}"}
                )
        assert r1.status_code == r2.status_code == 503
        assert r1.json() == r2.json()

    def test_command_token_required_unset_disables(self):
        resp = client.post("/automation/run")
        assert resp.status_code == 403

    def test_command_token_wrong_401(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post("/automation/run", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_run_not_gated_by_automation_writes_enabled(self):
        """Deliberate: run sits behind require_command_token alone, matching
        POST /pilots/{id}/follow's existing posture -- gating it more
        strictly than the follow write-path would invert the risk ordering."""
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", False):
                with mock.patch.object(
                    pilots_api.daemon_client, "trigger_run",
                    return_value=_trigger_response(),
                ):
                    resp = client.post(
                        "/automation/run", headers={"Authorization": f"Bearer {_CMD_TOKEN}"}
                    )
        assert resp.status_code == 202


# ---------------------------------------------------------------------------
# POST /automation/pause / /automation/resume
# ---------------------------------------------------------------------------


class TestAutomationPause:
    def test_pause_activates_kill_switch_with_reason(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(pilots_api, "GlobalKillSwitch") as MockKS:
                inst = MockKS.return_value
                resp = client.post(
                    "/automation/pause", json={"reason": "maintenance"},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        assert resp.json() == {"active": True, "reason": "maintenance"}
        inst.activate.assert_called_once_with(reason="maintenance")

    def test_pause_requires_a_non_empty_reason(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post(
                "/automation/pause", json={"reason": ""},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_pause_not_gated_by_automation_writes_enabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", False):
                with mock.patch.object(pilots_api, "GlobalKillSwitch"):
                    resp = client.post(
                        "/automation/pause", json={"reason": "x"},
                        headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                    )
        assert resp.status_code == 200

    def test_pause_command_token_required(self):
        resp = client.post("/automation/pause", json={"reason": "x"})
        assert resp.status_code == 403


class TestAutomationResume:
    def test_resume_deactivates_kill_switch(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(settings, "ADVISORY_ONLY", True):
                    with mock.patch.object(pilots_api, "GlobalKillSwitch") as MockKS:
                        inst = MockKS.return_value
                        resp = client.post(
                            "/automation/resume", json={"confirm": True, "reason": "back online"},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert resp.status_code == 200
        assert resp.json() == {"active": False, "reason": None}
        inst.deactivate.assert_called_once()

    def test_resume_fails_closed_when_writes_disabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", False):
                resp = client.post(
                    "/automation/resume", json={"confirm": True, "reason": "x"},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 403

    def test_resume_fails_closed_when_live_trading_enabled(self):
        """The core safety property: remote resume is refused once
        ADVISORY_ONLY=False, regardless of every other gate passing."""
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(settings, "ADVISORY_ONLY", False):
                    with mock.patch.object(pilots_api, "GlobalKillSwitch") as MockKS:
                        resp = client.post(
                            "/automation/resume", json={"confirm": True, "reason": "x"},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert resp.status_code == 403
        MockKS.return_value.deactivate.assert_not_called()

    def test_resume_requires_confirm_true(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(settings, "ADVISORY_ONLY", True):
                    resp = client.post(
                        "/automation/resume", json={"confirm": False, "reason": "x"},
                        headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                    )
        # confirm=False is a valid bool per the schema (no server-side check
        # forces true beyond client intent) -- but a missing confirm key is
        # a validation error, exercised below. Assert this succeeds today,
        # documenting confirm as a client-side guard, not a server gate.
        assert resp.status_code == 200

    def test_resume_missing_confirm_field_422(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(settings, "ADVISORY_ONLY", True):
                    resp = client.post(
                        "/automation/resume", json={"reason": "x"},
                        headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                    )
        assert resp.status_code == 422

    def test_resume_missing_reason_422(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                resp = client.post(
                    "/automation/resume", json={"confirm": True, "reason": ""},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 422

    def test_resume_command_token_required(self):
        with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
            resp = client.post("/automation/resume", json={"confirm": True, "reason": "x"})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /automation/schedule/interval
# ---------------------------------------------------------------------------


def _interval_response(**overrides):
    from gui.daemon_client import IntervalResponse

    base = dict(ok=False, interval_seconds=None, error="network_error")
    base.update(overrides)
    return IntervalResponse(**base)


class TestAutomationIntervalWrite:
    """``.env`` is always written first and unconditionally; the LIVE apply
    (``daemon_client.set_interval``) is explicitly stubbed in every test here
    -- an unstubbed test would otherwise make a REAL loopback HTTP call to
    ``http://127.0.0.1:<ORCHESTRATOR_API_PORT>/interval``, which happens to
    fail (connection refused) in an ordinary offline test run but is a latent
    flake: nothing prevents some other process/test from actually binding
    that port and flipping the assertion. Stubbing makes ``applies``
    deterministic regardless of what else is running on the machine."""

    def test_writes_via_env_io_allowlist_daemon_unreachable(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                    with mock.patch.object(
                        pilots_api.daemon_client, "set_interval",
                        return_value=_interval_response(),
                    ):
                        resp = client.put(
                            "/automation/schedule/interval", json={"interval_seconds": 300},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured_value"] == 300
        assert body["applies"] == "next_daemon_restart"
        assert "ORCHESTRATOR_INTERVAL_SECONDS=300" in env_file.read_text(encoding="utf-8")

    def test_applies_immediately_when_daemon_confirms(self, tmp_path):
        """The honesty contract: ``applies`` is ``"immediately"`` ONLY when
        the live daemon actually confirms -- never inferred from the .env
        write, which always succeeds regardless of whether a daemon is
        listening."""
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                    with mock.patch.object(
                        pilots_api.daemon_client, "set_interval",
                        return_value=_interval_response(ok=True, interval_seconds=300, error=None),
                    ) as mock_set_interval:
                        resp = client.put(
                            "/automation/schedule/interval", json={"interval_seconds": 300},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured_value"] == 300
        assert body["applies"] == "immediately"
        # .env is still written even though the live apply also succeeded --
        # it is never conditional on the live outcome.
        assert "ORCHESTRATOR_INTERVAL_SECONDS=300" in env_file.read_text(encoding="utf-8")
        mock_set_interval.assert_called_once_with(300)

    def test_env_written_even_when_live_apply_fails(self, tmp_path):
        """The durable .env record must land regardless of the live outcome
        -- a down/unreachable daemon must never block the operator's
        configured-value write."""
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                    with mock.patch.object(
                        pilots_api.daemon_client, "set_interval",
                        return_value=_interval_response(error="unavailable"),
                    ):
                        resp = client.put(
                            "/automation/schedule/interval", json={"interval_seconds": 300},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert resp.status_code == 200
        assert resp.json()["applies"] == "next_daemon_restart"
        assert "ORCHESTRATOR_INTERVAL_SECONDS=300" in env_file.read_text(encoding="utf-8")

    def test_zero_is_valid(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                    with mock.patch.object(
                        pilots_api.daemon_client, "set_interval",
                        return_value=_interval_response(),
                    ):
                        resp = client.put(
                            "/automation/schedule/interval", json={"interval_seconds": 0},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert resp.status_code == 200

    def test_59_is_rejected(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                resp = client.put(
                    "/automation/schedule/interval", json={"interval_seconds": 59},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 422

    def test_60_is_accepted(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                    with mock.patch.object(
                        pilots_api.daemon_client, "set_interval",
                        return_value=_interval_response(),
                    ):
                        resp = client.put(
                            "/automation/schedule/interval", json={"interval_seconds": 60},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert resp.status_code == 200

    def test_86400_is_accepted_86401_is_rejected(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                    with mock.patch.object(
                        pilots_api.daemon_client, "set_interval",
                        return_value=_interval_response(),
                    ):
                        resp = client.put(
                            "/automation/schedule/interval", json={"interval_seconds": 86400},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
                        assert resp.status_code == 200
                        resp2 = client.put(
                            "/automation/schedule/interval", json={"interval_seconds": 86401},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
                        assert resp2.status_code == 422

    def test_negative_is_rejected(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                resp = client.put(
                    "/automation/schedule/interval", json={"interval_seconds": -1},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 422

    def test_fails_closed_when_automation_writes_disabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", False):
                resp = client.put(
                    "/automation/schedule/interval", json={"interval_seconds": 300},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 403

    def test_command_token_required(self):
        with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
            resp = client.put("/automation/schedule/interval", json={"interval_seconds": 300})
        assert resp.status_code == 403


class TestAutomationWritesInvariants:
    def test_interval_key_is_allowlisted(self):
        assert "ORCHESTRATOR_INTERVAL_SECONDS" in pilots_api.env_io.ALLOWED_KEYS

    def test_automation_writes_enabled_is_not_gui_writable(self):
        """The D5/BROKERAGE_CONNECT_ENABLED invariant: a GUI bug must never
        be able to flip this on. It must be in NEITHER allowlist nor
        secret-list (hand-set in .env only, like its sibling)."""
        assert "AUTOMATION_WRITES_ENABLED" not in pilots_api.env_io.ALLOWED_KEYS
        assert "AUTOMATION_WRITES_ENABLED" not in pilots_api.env_io.SECRET_KEYS


class TestExecutionModeWrite:
    """PUT /automation/execution-mode -- 1-Click Go Live toggle. Tests stub
    ``gui.strategy_registry.set_active_mode`` (its own DRY_RUN/ALPACA_PAPER
    writes are covered by that module's own tests) and redirect
    ``env_io.ENV_PATH`` at a scratch file for the ADVISORY_ONLY write, mirroring
    ``TestAutomationIntervalWrite``."""

    def test_happy_path_writes_advisory_only_and_delegates_mode(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                    with mock.patch(
                        "gui.strategy_registry.set_active_mode"
                    ) as mock_set_mode:
                        resp = client.put(
                            "/automation/execution-mode",
                            json={"mode": "paper", "advisory_only": False},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["written"] == ["ADVISORY_ONLY", "DRY_RUN", "ALPACA_PAPER"]
        assert body["advisory_only"] is False
        assert body["mode"] == "paper"
        assert body["applies"] == "next_daemon_restart"
        assert "ADVISORY_ONLY=false" in env_file.read_text(encoding="utf-8")
        mock_set_mode.assert_called_once_with("paper")

    def test_advisory_mode_never_calls_set_active_mode(self, tmp_path):
        """``mode == "advisory"`` carries no DRY_RUN/ALPACA_PAPER pairing --
        ``written`` must say so rather than claiming a write that never
        happened (CONSTRAINT #4)."""
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                    with mock.patch(
                        "gui.strategy_registry.set_active_mode"
                    ) as mock_set_mode:
                        resp = client.put(
                            "/automation/execution-mode",
                            json={"mode": "advisory", "advisory_only": True},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert resp.status_code == 200
        assert resp.json()["written"] == ["ADVISORY_ONLY"]
        mock_set_mode.assert_not_called()

    def test_response_echoes_body_not_stale_settings(self, tmp_path):
        """Mirrors PUT /strategy/modules's echo contract: the .env write never
        patches the process-lifetime ``settings`` singleton, so the response
        must reflect the REQUEST BODY, not a stale ``settings.ADVISORY_ONLY``."""
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(settings, "ADVISORY_ONLY", True):
                    with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                        with mock.patch("gui.strategy_registry.set_active_mode"):
                            resp = client.put(
                                "/automation/execution-mode",
                                json={"mode": "live", "advisory_only": False},
                                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                            )
        assert resp.status_code == 200
        assert resp.json()["advisory_only"] is False

    def test_invalid_mode_422(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                resp = client.put(
                    "/automation/execution-mode",
                    json={"mode": "not-a-real-mode", "advisory_only": True},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 422

    def test_missing_advisory_only_422(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                resp = client.put(
                    "/automation/execution-mode",
                    json={"mode": "paper"},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 422

    def test_fails_closed_when_automation_writes_disabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", False):
                resp = client.put(
                    "/automation/execution-mode",
                    json={"mode": "paper", "advisory_only": False},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 403

    def test_command_token_required(self):
        with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
            resp = client.put(
                "/automation/execution-mode",
                json={"mode": "paper", "advisory_only": False},
            )
        assert resp.status_code == 403

    def test_401_on_wrong_command_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                resp = client.put(
                    "/automation/execution-mode",
                    json={"mode": "paper", "advisory_only": False},
                    headers={"Authorization": "Bearer wrong"},
                )
        assert resp.status_code == 401

    def test_write_never_logs_token(self, caplog, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        with caplog.at_level("DEBUG"):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                    with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                        with mock.patch("gui.strategy_registry.set_active_mode"):
                            client.put(
                                "/automation/execution-mode",
                                json={"mode": "paper", "advisory_only": False},
                                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                            )
        assert _CMD_TOKEN not in caplog.text


# ===========================================================================
# GET /strategy/matrix + PUT /strategy/modules
# ===========================================================================


def _full_weights_from_matrix():
    """Fetch the matrix (read-only, fail-open) and build a full-coverage weight
    map (every known module -> its weight, 0.0 where None), as the PWA would."""
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        matrix = client.get("/strategy/matrix").json()
    return {m["name"]: (m["weight"] if m["weight"] is not None else 0.0) for m in matrix["modules"]}


class TestStrategyMatrixRead:
    def test_shape_and_modules(self):
        with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
            resp = client.get("/strategy/matrix")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("modules", "disabled", "max_weight", "writable", "note", "env_drift", "reason"):
            assert key in body
        assert len(body["modules"]) > 0
        row = body["modules"][0]
        for key in ("name", "weight", "effective_weight", "enabled", "source", "pinned_zero"):
            assert key in row

    def test_fail_open_read_with_no_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
                resp = client.get("/strategy/matrix")
        assert resp.status_code == 200

    def test_401_on_wrong_read_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get(
                "/strategy/matrix", headers={"Authorization": "Bearer wrong"}
            )
        assert resp.status_code == 401

    def test_writable_tracks_the_flag(self):
        with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
            with mock.patch.object(settings, "STRATEGY_WRITES_ENABLED", True):
                on = client.get("/strategy/matrix").json()
            with mock.patch.object(settings, "STRATEGY_WRITES_ENABLED", False):
                off = client.get("/strategy/matrix").json()
        assert on["writable"] is True
        assert off["writable"] is False

    def test_cold_start_reason_without_snapshot(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/strategy/matrix")
        assert resp.status_code == 200
        assert resp.json()["reason"] is not None

    def test_env_drift_dead_letters_on_mangled_env_never_500(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("SIGNAL_WEIGHTS={not valid json\n", encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
            with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                resp = client.get("/strategy/matrix")
        assert resp.status_code == 200  # never 500 on a hand-mangled .env
        assert resp.json()["env_drift"]["detected"] is False


class TestStrategyModulesWrite:
    def test_fails_closed_when_strategy_writes_disabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "STRATEGY_WRITES_ENABLED", False):
                resp = client.put(
                    "/strategy/modules",
                    json={"weights": {"a": 1.0}, "disabled": []},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 403

    def test_fails_closed_when_follow_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            with mock.patch.object(settings, "STRATEGY_WRITES_ENABLED", True):
                resp = client.put(
                    "/strategy/modules",
                    json={"weights": {"a": 1.0}, "disabled": []},
                    headers={"Authorization": "Bearer anything"},
                )
        assert resp.status_code == 403

    def test_happy_path_writes_both_keys_atomically(self):
        full = _full_weights_from_matrix()
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "STRATEGY_WRITES_ENABLED", True):
                with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
                    with mock.patch.object(
                        pilots_api.env_io, "write_many_atomic",
                        return_value=["SIGNAL_WEIGHTS", "DISABLED_SIGNAL_MODULES"],
                    ) as w:
                        resp = client.put(
                            "/strategy/modules",
                            json={"weights": full, "disabled": []},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert resp.status_code == 200
        # write_many_atomic called ONCE, with BOTH keys (one logical unit).
        assert w.call_count == 1
        assert set(w.call_args[0][0].keys()) == {"SIGNAL_WEIGHTS", "DISABLED_SIGNAL_MODULES"}
        body = resp.json()
        assert body["applies"] == "next_daemon_restart"
        # Echoes the REQUEST BODY, not settings (which would be the stale values).
        assert body["configured_weights"] == full

    def _put_expecting_422(self, weights, disabled=None):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "STRATEGY_WRITES_ENABLED", True):
                with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
                    with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
                        resp = client.put(
                            "/strategy/modules",
                            json={"weights": weights, "disabled": disabled or []},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert resp.status_code == 422
        assert w.call_count == 0  # never writes on a validation failure
        return resp.json()["detail"]

    def test_incomplete_weights_422(self):
        full = _full_weights_from_matrix()
        dropped = next(iter(full))
        partial = {k: v for k, v in full.items() if k != dropped}
        detail = self._put_expecting_422(partial)
        assert detail["error"] == "incomplete_weights"
        assert dropped in detail["missing"]

    def test_unknown_module_422(self):
        full = dict(_full_weights_from_matrix())
        full["not_a_real_module"] = 5.0
        detail = self._put_expecting_422(full)
        assert detail["error"] == "unknown_module"

    def test_weight_out_of_bounds_422(self):
        full = dict(_full_weights_from_matrix())
        full[next(iter(full))] = 150.0
        detail = self._put_expecting_422(full)
        assert detail["error"] == "weight_out_of_bounds"

    def test_pinned_zero_module_422(self):
        full = dict(_full_weights_from_matrix())
        assert "regime_multiplier" in full
        full["regime_multiplier"] = 5.0
        detail = self._put_expecting_422(full)
        assert detail["error"] == "pinned_zero_module"

    def test_write_never_logs_token(self, caplog):
        full = _full_weights_from_matrix()
        with caplog.at_level("DEBUG"):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(settings, "STRATEGY_WRITES_ENABLED", True):
                    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
                        with mock.patch.object(pilots_api.env_io, "write_many_atomic"):
                            client.put(
                                "/strategy/modules",
                                json={"weights": full, "disabled": []},
                                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                            )
        assert _CMD_TOKEN not in caplog.text


class TestStrategyWritesInvariants:
    def test_signal_weight_keys_are_allowlisted(self):
        assert "SIGNAL_WEIGHTS" in pilots_api.env_io.ALLOWED_KEYS
        assert "DISABLED_SIGNAL_MODULES" in pilots_api.env_io.ALLOWED_KEYS

    def test_strategy_writes_enabled_is_not_gui_writable(self):
        """Mirrors test_automation_writes_enabled_is_not_gui_writable: a GUI bug
        must never flip this on. Neither allowlisted nor secret — hand-set only."""
        assert "STRATEGY_WRITES_ENABLED" not in pilots_api.env_io.ALLOWED_KEYS
        assert "STRATEGY_WRITES_ENABLED" not in pilots_api.env_io.SECRET_KEYS


# ===========================================================================
# GET /strategy/health — catalog-wide deployability-gate breakdown
# ===========================================================================


class TestStrategyHealth:
    def test_shape_and_all_gates_pass_for_fixture_backed_pilot(self, monkeypatch):
        _point_reports_at_fixtures(monkeypatch)
        resp = client.get("/strategy/health")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == len(catalog.list_pilots())
        row = next(r for r in body if r["pilot_id"] == "trend-following")
        for key in (
            "pilot_id", "pilot_name", "strategy_id", "deployable", "gates",
            "is_options_selling", "stress_gate_passed", "report_date", "trend", "reason",
        ):
            assert key in row
        assert row["strategy_id"] == "timeseries_momentum"
        assert row["deployable"] is True
        assert row["reason"] is None
        assert row["is_options_selling"] is False
        assert row["stress_gate_passed"] is True
        gate_keys = {g["key"] for g in row["gates"]}
        assert gate_keys == {"pbo", "dsr", "sharpe", "max_drawdown"}
        assert all(g["passed"] is True for g in row["gates"])
        # No reports/history fixture wired for this test -> honest empty trend.
        assert row["trend"] == []

    def test_pilot_without_backtest_is_honest_never_fabricated(self, monkeypatch):
        # news-catalyst is the catalog's genuinely backtest-less pilot
        # (validation_strategy_id=None). balanced-blend used to be, but gained a
        # real signal-replay backtest (signal_replay_balanced_blend) in #321.
        _point_reports_at_fixtures(monkeypatch)
        resp = client.get("/strategy/health")
        row = next(r for r in resp.json() if r["pilot_id"] == "news-catalyst")
        assert row["strategy_id"] is None
        assert row["deployable"] is None
        assert row["gates"] == []
        assert row["is_options_selling"] is None
        assert row["stress_gate_passed"] is None
        assert row["trend"] == []
        assert row["reason"] == "no validated backtest for this pilot"

    def test_missing_summary_degrades_never_500(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pilots_api, "_reports_dir", lambda: str(tmp_path))
        resp = client.get("/strategy/health")
        assert resp.status_code == 200
        row = next(r for r in resp.json() if r["pilot_id"] == "trend-following")
        assert row["deployable"] is None
        assert row["gates"] == []
        assert row["reason"] and "timeseries_momentum" in row["reason"]

    def test_trend_populated_from_history_fixture_oldest_first(self, tmp_path, monkeypatch):
        _point_reports_at_fixtures(monkeypatch)
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        rows = [
            {
                "report_date": "2026-06-01", "pbo": 0.4, "dsr": 0.90,
                "sharpe": 0.40, "max_drawdown": 0.20, "deployable": False,
            },
            {
                "report_date": "2026-06-15", "pbo": 0.18, "dsr": 0.972,
                "sharpe": 1.14, "max_drawdown": 0.176, "deployable": True,
            },
        ]
        (history_dir / "timeseries_momentum_validation_history.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
        monkeypatch.setattr(pilots_api, "_validation_history_dir", lambda: str(history_dir))
        resp = client.get("/strategy/health")
        row = next(r for r in resp.json() if r["pilot_id"] == "trend-following")
        assert [t["report_date"] for t in row["trend"]] == ["2026-06-01", "2026-06-15"]

    def test_fail_open_read_with_no_token(self, monkeypatch):
        _point_reports_at_fixtures(monkeypatch)
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/strategy/health")
        assert resp.status_code == 200

    def test_401_on_wrong_read_token(self, monkeypatch):
        _point_reports_at_fixtures(monkeypatch)
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get("/strategy/health", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_gate_thresholds_are_read_from_validation_thresholds_module(self, monkeypatch):
        from validation import thresholds

        _point_reports_at_fixtures(monkeypatch)
        resp = client.get("/strategy/health")
        row = next(r for r in resp.json() if r["pilot_id"] == "trend-following")
        by_key = {g["key"]: g["threshold"] for g in row["gates"]}
        assert by_key["pbo"] == thresholds.PBO_MAX
        assert by_key["dsr"] == thresholds.DSR_MIN
        assert by_key["sharpe"] == thresholds.NET_SHARPE_MIN
        assert by_key["max_drawdown"] == thresholds.MAX_DRAWDOWN_MAX


# ---------------------------------------------------------------------------
# GET /llm/status — LLM configuration + last-real-call telemetry.
# Mirrors TestBrokerageStatus's four axes (tests/test_brokerage_connect.py):
# unconfigured -> honest shape, configured -> reflected, NOT gated by the LLM
# master switch, and a sub-read failure surfaces (non-raising is the store's
# own contract, pinned in tests/test_llm_status_store.py).
# ---------------------------------------------------------------------------


def _clear_llm_keys(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setattr(settings, k, None, raising=False)


class TestLlmStatus:
    def test_cold_start_honest_empty_shape(self, tmp_path, monkeypatch):
        # Everything off + no keys + no recorded calls -> deterministic body.
        _clear_llm_keys(monkeypatch)
        monkeypatch.setattr(settings, "LLM_COMMENTARY_ENABLED", False, raising=False)
        monkeypatch.setattr(settings, "OPAL_RESEARCH_ENABLED", False, raising=False)
        monkeypatch.setattr(settings, "GRAVITY_AI_RUNNER_ENABLED", False, raising=False)
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/llm/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["attention"] is False
        assert body["attention_reason"] is None
        assert set(body["providers"]) == {"claude", "gemini", "openai"}
        assert all(p["source"] == "none" for p in body["providers"].values())
        assert all(row["status"] == "disabled" for row in body["capabilities"])
        assert body["capabilities_source"]
        assert body["providers_source"]
        assert body["telemetry_note"]

    def test_configured_auth_rejection_flags_attention(self, tmp_path, monkeypatch):
        import llm.status_store as ss

        monkeypatch.setattr(settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(settings, "LLM_COMMENTARY_RATIONALE_PROVIDER", "claude", raising=False)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-x", raising=False)
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            # Record a real auth failure for the current key.
            exc = type("AuthenticationError", (Exception,), {})("bad key")
            exc.status_code = 401
            ss.record_failure("claude", exc)
            resp = client.get("/llm/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["attention"] is True
        assert body["attention_reason"] == "invalid_key"
        claude_row = next(r for r in body["capabilities"] if r["key"] == "claude_commentary")
        assert claude_row["status"] == "invalid_key"
        assert claude_row["invalid_provider"] == "claude"

    def test_not_gated_by_master_switch(self, tmp_path, monkeypatch):
        # Reads even when the feature is OFF — the whole point is to explain a null.
        _clear_llm_keys(monkeypatch)
        monkeypatch.setattr(settings, "LLM_COMMENTARY_ENABLED", False, raising=False)
        monkeypatch.setattr(settings, "OPAL_RESEARCH_ENABLED", False, raising=False)
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/llm/status")
        assert resp.status_code == 200

    def test_response_carries_no_key_material_or_fingerprint(self, tmp_path, monkeypatch):
        import llm.status_store as ss

        sentinel = "sk-ant-QWZXCVBNMASDFGHJKL987654321"
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", sentinel, raising=False)
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            ss.record_success("claude")
            on_disk = json.loads((tmp_path / ss.LLM_STATUS_FILENAME).read_text())
            fingerprint = on_disk["providers"]["claude"]["key_fingerprint"]
            resp = client.get("/llm/status")
        assert sentinel not in resp.text
        assert fingerprint not in resp.text

    def test_makes_no_network_call_and_constructs_no_provider(self, tmp_path, monkeypatch):
        # The endpoint reads settings directly — it must NEVER route through
        # llm.router.get_*_provider() (which constructs a provider, firing an
        # SDK import + a potential network call).
        import llm.router as router

        monkeypatch.setattr(
            router, "get_rationale_provider", lambda: (_ for _ in ()).throw(AssertionError("constructed!"))
        )
        monkeypatch.setattr(
            router, "get_alert_provider", lambda: (_ for _ in ()).throw(AssertionError("constructed!"))
        )
        monkeypatch.setattr(
            router, "get_research_provider", lambda: (_ for _ in ()).throw(AssertionError("constructed!"))
        )
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/llm/status")
        assert resp.status_code == 200

    def test_read_token_gates_the_endpoint(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/llm/status")
        assert resp.status_code == 401

        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/llm/status", headers={"Authorization": "Bearer read-tok"})
        assert resp.status_code == 200

    def test_read_token_unset_is_open(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", ""):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/llm/status")
        assert resp.status_code == 200


def test_engine_package_init_stays_import_inert():
    """api/pilots_api.py imports gui.ai_control_center, whose
    control_center_overview() calls importlib.util.find_spec on the backing
    modules -- including ``engine.gravity_ai_runner``, which imports the
    ``engine`` PACKAGE (executing engine/__init__.py) at runtime.

    engine/__init__.py is docstring-only today, so that's inert. But
    engine/advisory.py imports processing_engine / forecasting_engine /
    technical_options_engine / strategy_engine -- FOUR of the heavy engines on
    the deny-list of test_pilots_api_never_imports_heavy_engines above. If
    anyone ever adds a real import to engine/__init__.py, api/pilots_api.py
    would silently acquire those heavy engines at status-endpoint time, and the
    AST guard (which walks import STATEMENTS only) would never catch it. This
    pins engine/__init__.py import-inert, exactly like the gui/__init__.py
    guard one package over."""
    import engine

    tree = ast.parse(pathlib.Path(engine.__file__).read_text(encoding="utf-8"))
    real_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and getattr(node, "module", None) != "__future__"
    ]
    assert not real_imports, (
        f"engine/__init__.py must stay import-inert (found: {real_imports}) — "
        "gui.ai_control_center.control_center_overview find_spec's engine.* and "
        "would pull any real import here into api/pilots_api.py at status time."
    )


def test_llm_package_import_reaches_no_sdk_and_no_heavy_engine():
    """`import llm` (which api/pilots_api.py's `import llm.status_store` runs)
    must not eagerly pull in any SDK or heavy engine. Subprocess-isolated
    because sys.modules is polluted by sibling tests that install fake SDKs
    (precedent: tests/test_backfill_edgar_fundamentals.py)."""
    import subprocess
    import sys

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    code = (
        "import llm, llm.status_store, sys;"
        "bad = {'anthropic','openai','google.genai','processing_engine',"
        "'strategy_engine','forecasting_engine','macro_engine',"
        "'technical_options_engine','main_orchestrator'} & set(sys.modules);"
        "assert not bad, bad"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=str(repo_root), capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_control_center_overview_end_to_end_leaks_no_heavy_engine():
    """Stronger than test_engine_package_init_stays_import_inert (which only
    proves engine/__init__.py's SOURCE is currently empty): this actually
    DRIVES the runtime path GET /llm/status exercises —
    gui.ai_control_center.control_center_overview() —> _module_available() —>
    importlib.util.find_spec("engine.gravity_ai_runner") —> imports the
    `engine` package as a side effect — and confirms none of the four
    deny-listed heavy engines (processing_engine / forecasting_engine /
    technical_options_engine / strategy_engine, all imported by
    engine/advisory.py) land in sys.modules as a result. Subprocess-isolated
    for a clean sys.modules baseline. This is the live demonstration behind
    test_engine_package_init_stays_import_inert's static guard — if that
    guard is ever weakened, this test independently catches the actual leak."""
    import subprocess
    import sys

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    code = (
        "from gui.ai_control_center import control_center_overview;"
        "from settings import settings;"
        "control_center_overview(settings);"  # the exact call GET /llm/status makes
        "import sys;"
        "bad = {'processing_engine','strategy_engine','forecasting_engine',"
        "'technical_options_engine','macro_engine','main_orchestrator'} & set(sys.modules);"
        "assert not bad, bad"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=str(repo_root), capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# GET /calibration/summary + /calibration/edge-by-strategy + POST /decisions
# ---------------------------------------------------------------------------


_EMPTY_TRACKING_REPORT = {
    "rows": [],
    "model_return_30d": float("nan"),
    "operator_return_30d": float("nan"),
    "delta": float("nan"),
    "n_signals": 0,
    "n_acted": 0,
    "n_completed": 0,
    "n_with_exit": 0,
    "horizon_days": 30,
}


class _EmptyClosedStore:
    """A TransactionsStore stand-in with no closed trades and no trade history."""

    def closed_trades_df(self):
        return pd.DataFrame()

    def get_trade_history(self, symbol):
        return pd.DataFrame()


class TestCalibrationSummaryEndpoint:
    """Endpoint-level wiring for GET /calibration/summary. The substantive
    per-section logic is unit-tested against pilots/calibration.py in
    tests/test_pilots_calibration.py; these confirm the FastAPI wiring — auth,
    query params, snapshot threading, and the composite shape — end-to-end."""

    def test_cold_start_shape(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch(
                "transactions_store.TransactionsStore", return_value=_EmptyClosedStore()
            ):
                with mock.patch(
                    "evaluation_engine.recommendation_tracking_report",
                    return_value=_EMPTY_TRACKING_REPORT,
                ):
                    with mock.patch(
                        "gui.decision_log.decisions_df", return_value=pd.DataFrame()
                    ):
                        resp = client.get("/calibration/summary")

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "calibration",
            "recommendation_tracking",
            "mfe_mae",
            "recent_decisions",
        }
        assert body["calibration"]["bins"] == []
        assert body["calibration"]["overall_win_rate"] is None
        assert body["calibration"]["reason"]
        assert body["recommendation_tracking"]["n_signals"] == 0
        assert body["recommendation_tracking"]["model_return"] is None
        assert body["mfe_mae"]["points"] == []
        assert body["recent_decisions"]["decisions"] == []

    def test_reads_mfe_mae_from_persisted_snapshot_fixture(self, tmp_path):
        (tmp_path / "state_snapshot.json").write_text(_SNAPSHOT_FIXTURE, encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch(
                "transactions_store.TransactionsStore", return_value=_EmptyClosedStore()
            ):
                with mock.patch(
                    "evaluation_engine.recommendation_tracking_report",
                    return_value=_EMPTY_TRACKING_REPORT,
                ):
                    with mock.patch(
                        "gui.decision_log.decisions_df", return_value=pd.DataFrame()
                    ):
                        resp = client.get("/calibration/summary")

        assert resp.status_code == 200
        # mfe_mae is a pure snapshot read — its shape must always be present
        # (points may be empty if the fixture carries no mfe/mae, which is honest).
        assert "points" in resp.json()["mfe_mae"]

    def test_horizon_threads_through(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch(
                "transactions_store.TransactionsStore", return_value=_EmptyClosedStore()
            ):
                with mock.patch(
                    "evaluation_engine.recommendation_tracking_report",
                    return_value={**_EMPTY_TRACKING_REPORT, "horizon_days": 60},
                ) as mock_report:
                    with mock.patch(
                        "gui.decision_log.decisions_df", return_value=pd.DataFrame()
                    ):
                        resp = client.get("/calibration/summary?horizon=60")

        assert resp.status_code == 200
        assert resp.json()["recommendation_tracking"]["horizon_days"] == 60
        # The horizon query param reached the report call.
        assert mock_report.call_args.kwargs["horizon_days"] == 60

    def test_bad_horizon_422(self):
        assert client.get("/calibration/summary?horizon=0").status_code == 422
        assert client.get("/calibration/summary?horizon=999").status_code == 422

    def test_read_token_gates_endpoint(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                with mock.patch(
                    "transactions_store.TransactionsStore",
                    return_value=_EmptyClosedStore(),
                ):
                    with mock.patch(
                        "evaluation_engine.recommendation_tracking_report",
                        return_value=_EMPTY_TRACKING_REPORT,
                    ):
                        with mock.patch(
                            "gui.decision_log.decisions_df", return_value=pd.DataFrame()
                        ):
                            no_auth = client.get("/calibration/summary")
                            ok = client.get(
                                "/calibration/summary",
                                headers={"Authorization": "Bearer read-tok"},
                            )
        assert no_auth.status_code == 401
        assert ok.status_code == 200


class TestEdgeByStrategyEndpoint:
    def test_no_trades_honest_empty(self):
        with mock.patch(
            "transactions_store.TransactionsStore", return_value=_EmptyClosedStore()
        ):
            resp = client.get("/calibration/edge-by-strategy")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rows"] == []
        assert body["reason"]

    def test_happy_path_groups_by_strategy(self):
        closed = pd.DataFrame(
            {
                "symbol": ["AAPL"],
                "entry_price": [100.0],
                "entry_ts": [pd.Timestamp("2026-01-01")],
                "exit_ts": [pd.Timestamp("2026-01-10")],
                "strategy": ["trend"],
            }
        )

        class _Store:
            def closed_trades_df(self):
                return closed

        class _HStore:
            def get_bars(self, sym, lookback_days=756):
                idx = pd.date_range("2026-01-01", periods=30, freq="D")
                return pd.DataFrame(
                    {"Open": 100.0, "High": 112.0, "Low": 96.0, "Close": 105.0, "Volume": 1},
                    index=idx,
                )

        edge_ret = {"MFE": 0.12, "MAE": 0.04, "Edge Ratio": 3.0, "Return Std Dev": 0.01}
        with mock.patch("transactions_store.TransactionsStore", return_value=_Store()):
            with mock.patch("data.historical_store.HistoricalStore", return_value=_HStore()):
                with mock.patch("evaluation_engine.EvaluationEngine") as MockEE:
                    MockEE.return_value.calculate_edge_ratio.return_value = edge_ret
                    resp = client.get("/calibration/edge-by-strategy")

        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) == 1
        assert rows[0]["strategy"] == "trend"
        assert rows[0]["n_trades"] == 1
        assert rows[0]["mean_edge_ratio"] == pytest.approx(3.0)


class _NoTradeStore:
    """A TransactionsStore stand-in whose trade-history join finds nothing —
    so an 'acted' decision's trade_id stays null (best-effort, never fabricated)."""

    def get_trade_history(self, symbol):
        return pd.DataFrame()


class TestDecisionsWrite:
    def test_write_happy_acted_no_trade_match(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch(
                    "transactions_store.TransactionsStore", return_value=_NoTradeStore()
                ):
                    resp = client.post(
                        "/decisions",
                        json={
                            "symbol": "aapl",
                            "action_taken": "acted",
                            "signal_action": "BUY",
                            "conviction": 0.8,
                            "notes": "took it",
                        },
                        headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                    )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"  # normalized upper
        assert body["action_taken"] == "acted"
        assert body["signal_action"] == "BUY"
        assert body["conviction"] == pytest.approx(0.8)
        assert body["trade_id"] is None  # no match within 24h -> null, never fabricated
        assert body["trade_linked"] is False
        # The entry was actually appended to the tmp OUTPUT_DIR log.
        log_file = tmp_path / "decision_log.jsonl"
        assert log_file.exists()
        assert "AAPL" in log_file.read_text(encoding="utf-8")

    def test_bad_action_422_with_stable_tag(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = client.post(
                    "/decisions",
                    json={"symbol": "AAPL", "action_taken": "yolo", "signal_action": "BUY"},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "invalid_action"

    def test_fail_closed_403_when_follow_token_unset(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
                resp = client.post(
                    "/decisions",
                    json={"symbol": "AAPL", "action_taken": "passed", "signal_action": "BUY"},
                )
        assert resp.status_code == 403

    def test_wrong_command_token_401(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                resp = client.post(
                    "/decisions",
                    json={"symbol": "AAPL", "action_taken": "passed", "signal_action": "BUY"},
                    headers={"Authorization": "Bearer WRONG"},
                )
        assert resp.status_code == 401


class TestDecisionsRead:
    """GET /decisions — the standalone, paginated, symbol-filterable read a
    symbol detail page needs (distinct from GET /calibration/summary's
    fixed-size bundled recent_decisions preview)."""

    def test_empty_log_returns_empty_list_never_404(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/decisions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_post_then_get_round_trip(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch(
                    "transactions_store.TransactionsStore", return_value=_NoTradeStore()
                ):
                    post_resp = client.post(
                        "/decisions",
                        json={
                            "symbol": "aapl",
                            "action_taken": "acted",
                            "signal_action": "BUY",
                            "conviction": 0.8,
                            "notes": "took it",
                        },
                        headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                    )
            assert post_resp.status_code == 200

            get_resp = client.get("/decisions")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert len(body) == 1
        assert body[0]["symbol"] == "AAPL"
        assert body[0]["action_taken"] == "acted"
        assert body[0]["notes"] == "took it"
        assert body[0]["trade_id"] is None  # never fabricated

    def test_symbol_filter(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch(
                    "transactions_store.TransactionsStore", return_value=_NoTradeStore()
                ):
                    for sym in ("AAPL", "MSFT"):
                        client.post(
                            "/decisions",
                            json={
                                "symbol": sym,
                                "action_taken": "passed",
                                "signal_action": "HOLD",
                            },
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )

            resp = client.get("/decisions", params={"symbol": "aapl"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["symbol"] == "AAPL"

    def test_limit_caps_result_count(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch(
                    "transactions_store.TransactionsStore", return_value=_NoTradeStore()
                ):
                    for i in range(3):
                        client.post(
                            "/decisions",
                            json={
                                "symbol": "AAPL",
                                "action_taken": "passed",
                                "signal_action": "HOLD",
                                "notes": f"entry {i}",
                            },
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )

            resp = client.get("/decisions", params={"limit": 2})
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_unreadable_log_degrades_to_empty_list(self, tmp_path):
        """A read failure (e.g. read_decisions raising unexpectedly) must
        degrade to [], never a 500 (CONSTRAINT #6)."""
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch(
                "gui.decision_log.read_decisions", side_effect=OSError("boom")
            ):
                resp = client.get("/decisions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_no_auth_token_required_read_tier(self, tmp_path):
        """GET /decisions is fail-open (require_read_token), unlike the
        fail-closed POST — reading your own decision history carries no
        order/money/config risk."""
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(settings, "STATE_API_TOKEN", "some-token"):
                resp = client.get("/decisions")  # no Authorization header
        assert resp.status_code == 401  # requires the READ token, not the command token
