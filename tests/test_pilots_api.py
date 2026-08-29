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
import asyncio
import json
import os
import pathlib
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from settings import settings
from pilots import catalog
from rlhf_calibration_store import RlhfCalibrationStore
import api.pilots_api as pilots_api

# Starlette's TestClient defaults request.client.host to the literal
# string "testclient" -- NOT loopback -- which would trip
# api.auth.require_read_token's new fail-closed-when-non-loopback branch
# on every one of this file's existing zero-config-behavior assertions.
# An explicit loopback host here is what these tests have always meant.
client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
_SNAPSHOT_FIXTURE = (FIXTURES / "state_snapshot.json").read_text(encoding="utf-8")

_CMD_TOKEN = "cmd-tok"


def _point_reports_at_fixtures(monkeypatch):
    monkeypatch.setattr(pilots_api, "_reports_dir", lambda: str(FIXTURES))


# GET /strategy/validation-trend reads the durable validation_runs DB table
# (validation/validation_history_store.py) via pilots.validation_trend, with
# no db_url override at the API layer (by design -- production always wants
# the real resolved DB). The root conftest.py's
# `_isolate_validation_runs_db_in_tests` autouse fixture points the default
# resolver at an in-memory db for every test in this file, so none of them
# read the real, shared ~/.stockpy_local/quant_platform.db.


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
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
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
        "headline", "holdings_count", "top_holdings", "aum_proxy", "followers_proxy",
        "long_only",
    }
    assert tf["long_only"] is False
    # Headline comes from tests/fixtures/timeseries_momentum_validation_summary.json.
    assert tf["headline"]["sharpe"] == 1.14
    assert tf["headline"]["deployable"] is True
    # trend-following weights timeseries_momentum; the fixture snapshot has 5
    # names with a positive timeseries_momentum contribution.
    assert tf["holdings_count"] == 5
    assert len(tf["top_holdings"]) == 3
    assert tf["top_holdings"][0]["symbol"] == "NVDA"
    assert tf["aum_proxy"] == 0.0
    assert tf["followers_proxy"] == 0


def test_pilots_list_headline_null_when_no_backtest(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
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
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
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
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
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


def test_pilot_detail_news_coverage_null_for_non_news_pilot(monkeypatch):
    # trend-following carries no `news_catalyst` weight -> news_coverage must
    # be null, never a fabricated/borrowed value (CONSTRAINT #4).
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    _point_reports_at_fixtures(monkeypatch)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/pilots/trend-following")
    assert resp.status_code == 200
    assert resp.json()["news_coverage"] is None


def test_pilot_detail_news_coverage_populated_for_news_catalyst_pilot(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    _point_reports_at_fixtures(monkeypatch)
    fake_coverage = {
        "archived_score_count": 412,
        "headline_volume_7d": 18,
        "universe_score_distribution": {"positive": 9, "neutral": 21, "negative": 4},
    }
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES), mock.patch(
        "pilots.news_catalyst.get_news_catalyst_coverage", return_value=fake_coverage
    ):
        resp = client.get("/pilots/news-catalyst")
    assert resp.status_code == 200
    assert resp.json()["news_coverage"] == fake_coverage


def test_pilot_detail_news_coverage_degrades_to_none_on_failure(monkeypatch):
    # get_news_catalyst_coverage itself never raises (CONSTRAINT #6) — but the
    # detail endpoint must also survive a None return honestly, not 500.
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    _point_reports_at_fixtures(monkeypatch)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES), mock.patch(
        "pilots.news_catalyst.get_news_catalyst_coverage", return_value=None
    ):
        resp = client.get("/pilots/news-catalyst")
    assert resp.status_code == 200
    assert resp.json()["news_coverage"] is None


def test_pilot_detail_unknown_404(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    resp = client.get("/pilots/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No such pilot."


def test_pilot_detail_cold_start_empty_but_not_404(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    _point_reports_at_fixtures(monkeypatch)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/pilots/trend-following")
    assert resp.status_code == 200
    body = resp.json()
    assert body["holdings"] == []
    assert body["top_holdings"] == []
    assert body["sector_allocation"] == []
    assert body["recent_trades"] == []
    assert body["as_of"] is None
    assert body["reason"] == "No state snapshot yet — run the pipeline first."


# ---------------------------------------------------------------------------
# GET /symbols/{ticker} — symbol detail
# ---------------------------------------------------------------------------


def test_symbol_detail_shape_and_values(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["as_of"] == "2026-07-11T21:05:00+00:00"
    assert body["reason"] is None
    assert set(body) == {
        "symbol", "as_of", "reason",
        "identity", "advisory", "factors", "ranges", "risk", "sizing", "held_by_pilots",
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
    # Position-sizing decomposition — real values from the fixture (the
    # "no MetaLabelers/HMM adjustment active" honest 1.0 state).
    assert body["sizing"]["kelly_target_pre_regime"] == 0.041
    assert body["sizing"]["kelly_target_post_regime"] == 0.041
    assert body["sizing"]["regime_multiplier"] == 1.0
    assert body["sizing"]["meta_label_composite"] == 1.0
    assert body["sizing"]["max_position_weight"] == settings.MAX_POSITION_WEIGHT
    # Reverse cross-link: AAPL is held by trend-following; deep-value excluded.
    held_ids = {p["pilot_id"] for p in body["held_by_pilots"]}
    assert "trend-following" in held_ids
    assert "deep-value" not in held_ids
    assert body["held_by_pilots"]  # non-empty
    for p in body["held_by_pilots"]:
        assert set(p) == {"pilot_id", "name", "weight"}


def test_symbol_detail_case_insensitive(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/aapl")
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "AAPL"


def test_symbol_detail_unknown_404(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/ZZZ")
    assert resp.status_code == 404
    assert resp.json()["detail"] == pilots_api._UNKNOWN_SYMBOL_DETAIL


def test_symbol_detail_cold_start_404(tmp_path, monkeypatch):
    # tmp_path has no state_snapshot.json → honest cold-start 404 (distinct detail).
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/symbols/AAPL")
    assert resp.status_code == 404
    assert resp.json()["detail"] == pilots_api._MISSING_SNAPSHOT_DETAIL


# ---------------------------------------------------------------------------
# GET /symbols/compare — symbol-vs-symbol comparison
# ---------------------------------------------------------------------------


def test_symbols_compare_shape_and_values(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/compare?symbols=AAPL,MSFT")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"as_of", "symbols", "modules"}
    assert body["as_of"] == "2026-07-11T21:05:00+00:00"
    assert [r["symbol"] for r in body["symbols"]] == ["AAPL", "MSFT"]
    aapl = body["symbols"][0]
    assert set(aapl) == {
        "symbol", "found", "reason", "score", "action", "kelly_target",
        "conviction", "garch_vol", "meta_label_composite", "regime_multiplier",
        "score_components", "sector", "sector_pe", "sector_change_pct",
    }
    assert aapl["found"] is True
    assert aapl["score"] == 96.8
    assert aapl["action"] == "BUY"
    assert aapl["garch_vol"] == 0.243
    assert isinstance(aapl["score_components"], dict) and aapl["score_components"]
    assert isinstance(body["modules"], list) and body["modules"] == sorted(body["modules"])


def test_symbols_compare_not_shadowed_by_ticker_route(monkeypatch):
    # Regression guard for the FastAPI route-ordering trap: /symbols/compare
    # must match its own handler, not get captured as ticker="compare" by the
    # earlier-declared-in-file-order... (it's declared BEFORE /symbols/{ticker}
    # precisely to avoid this). A shadowed route would 404 with
    # _UNKNOWN_SYMBOL_DETAIL instead of returning the comparison shape.
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/compare?symbols=AAPL,MSFT")
    assert resp.status_code == 200
    assert "symbols" in resp.json()
    assert "identity" not in resp.json()  # would be present if it hit get_symbol_detail


def test_symbols_compare_case_insensitive_and_deduped(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/compare?symbols=aapl,AAPL,  Aapl ,msft")
    assert resp.status_code == 200
    body = resp.json()
    # 3 case/whitespace variants of AAPL dedup to one row; MSFT is the second.
    assert len(body["symbols"]) == 2
    assert body["symbols"][0]["symbol"] == "AAPL"
    assert body["symbols"][1]["symbol"] == "MSFT"


def test_symbols_compare_unknown_symbol_gets_honest_row_not_404(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/compare?symbols=AAPL,ZZZ")
    assert resp.status_code == 200
    rows = {r["symbol"]: r for r in resp.json()["symbols"]}
    assert rows["AAPL"]["found"] is True
    assert rows["ZZZ"]["found"] is False
    assert rows["ZZZ"]["reason"] == "Not tracked in the latest snapshot."
    assert rows["ZZZ"]["score"] is None


def test_symbols_compare_cold_start_never_404s(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/symbols/compare?symbols=AAPL,MSFT")
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] is None
    for row in body["symbols"]:
        assert row["found"] is False
        assert row["reason"] == pilots_api._MISSING_SNAPSHOT_DETAIL


def test_symbols_compare_too_few_symbols_422(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/compare?symbols=AAPL")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "too_few_symbols"


def test_symbols_compare_too_few_symbols_after_dedup_422(monkeypatch):
    # AAPL,aapl de-duplicates to a single symbol -> still below the floor.
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/compare?symbols=AAPL,aapl")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "too_few_symbols"


def test_symbols_compare_too_many_symbols_422(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/compare?symbols=AAPL,MSFT,NVDA,JPM,XOM,JNJ")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "too_many_symbols"


def test_symbols_compare_missing_query_param_422(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/symbols/compare")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /universe — the symbol-autocomplete source
# ---------------------------------------------------------------------------


def test_universe_shape_and_values(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
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


def test_universe_cold_start_empty_not_404(tmp_path, monkeypatch):
    # Unlike /symbols/{ticker}, /universe never 404s — a cold start is an
    # honestly empty suggestion list, not an error (this endpoint only ever
    # backs an autocomplete UI, so "nothing to suggest yet" is a normal state).
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/universe")
    assert resp.status_code == 200
    assert resp.json() == {"symbols": []}


# ---------------------------------------------------------------------------
# POST /universe/{symbol}/reinclude — manual symbol-rating re-include
# ---------------------------------------------------------------------------


class TestUniverseReinclude:
    def test_happy_path_calls_store_write_and_returns_result(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch(
                "rating.symbol_rating_store.SymbolRatingStore"
            ) as MockStore:
                inst = MockStore.return_value
                resp = client.post(
                    "/universe/xom/reinclude",
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        assert resp.json() == {"symbol": "XOM", "reincluded": True}
        # Constructed in write mode (no readonly=True) and called exactly once
        # with the upper-cased symbol.
        MockStore.assert_called_once_with()
        inst.reinclude.assert_called_once_with("XOM")

    def test_uppercases_and_strips_symbol(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch("rating.symbol_rating_store.SymbolRatingStore") as MockStore:
                inst = MockStore.return_value
                resp = client.post(
                    "/universe/%20nvda%20/reinclude",
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "NVDA"
        inst.reinclude.assert_called_once_with("NVDA")

    def test_command_token_required_unset_disables(self):
        """Fail-closed require_command_token ALONE (matches POST /decisions /
        POST /automation/pause's tier — see the pilots-endpoint auth
        taxonomy): FOLLOW_API_TOKEN unset means the endpoint is fully disabled.

        FOLLOW_API_TOKEN must be EXPLICITLY unset here, not assumed ambient —
        see TestAutomationIntervalWrite.test_command_token_required's comment for why:
        its value otherwise depends on whatever real .env happens to be on
        the machine running pytest."""
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            resp = client.post("/universe/XOM/reinclude")
        assert resp.status_code == 403

    def test_command_token_wrong_401(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post(
                "/universe/XOM/reinclude", headers={"Authorization": "Bearer wrong"}
            )
        assert resp.status_code == 401

    def test_store_write_failure_returns_503_not_500(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch("rating.symbol_rating_store.SymbolRatingStore") as MockStore:
                MockStore.return_value.reinclude.side_effect = RuntimeError("db unavailable")
                resp = client.post(
                    "/universe/XOM/reinclude",
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 503

    def test_not_gated_by_a_dedicated_writes_enabled_flag(self):
        """Deliberate: reinclude sits behind require_command_token alone --
        no SYMBOL_RATING_*_ENABLED gate exists on this endpoint (it only
        breaks a rating streak; it never places an order or bypasses any
        other risk gate)."""
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "SYMBOL_RATING_AUTO_DROP_ENABLED", False):
                with mock.patch("rating.symbol_rating_store.SymbolRatingStore"):
                    resp = client.post(
                        "/universe/XOM/reinclude",
                        headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                    )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /recommendations — the ranked BUY-picks feed
# ---------------------------------------------------------------------------


def test_recommendations_shape_and_ranking(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/recommendations")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"recommendations", "count", "as_of", "reason"}
    # Fixture BUYs ranked by conviction: NVDA(0.88) AAPL(0.72) JPM(0.64) XOM(0.58).
    symbols = [r["symbol"] for r in body["recommendations"]]
    assert symbols == ["NVDA", "AAPL", "JPM", "XOM"]
    assert body["count"] == 4
    assert body["as_of"] == "2026-07-11T21:05:00+00:00"
    assert body["reason"] is None
    for r in body["recommendations"]:
        assert set(r) == {"symbol", "action", "conviction", "score", "buy_range", "sector", "price"}


def test_recommendations_limit_clamped(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/recommendations?limit=2")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["symbol"] for r in body["recommendations"]] == ["NVDA", "AAPL"]
    assert body["count"] == 2
    # FastAPI validates ge=1/le=200 → 422 out of range.
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        assert client.get("/recommendations?limit=0").status_code == 422
        assert client.get("/recommendations?limit=999").status_code == 422


def test_recommendations_cold_start_empty_with_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/recommendations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommendations"] == []
    assert body["count"] == 0
    assert body["as_of"] is None
    assert body["reason"]  # honest "nothing yet" note, never 404


def test_recommendations_read_token_gates_the_endpoint():
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            assert client.get("/recommendations").status_code == 401
            resp = client.get("/recommendations", headers={"Authorization": "Bearer read-tok"})
        assert resp.status_code == 200
        with mock.patch.object(settings, "STATE_API_TOKEN", ""):
            assert client.get("/recommendations").status_code == 200


# ---------------------------------------------------------------------------
# GET /thresholds — live deployability-gate / sizing thresholds for the PWA's
# education panels. Asserted against the SAME imported constants the route
# itself reads, so this test can never silently drift from the live source.
# ---------------------------------------------------------------------------


def test_thresholds_shape_and_live_values(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    from validation.thresholds import (
        DSR_MIN,
        MAX_DRAWDOWN_MAX,
        NET_SHARPE_MIN,
        PBO_MAX,
        STRESS_MAX_DRAWDOWN,
    )
    from gui.help_content import MODEL_RETRAIN_WINDOW_DAYS

    resp = client.get("/thresholds")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "pbo_max", "dsr_min", "net_sharpe_min", "max_drawdown_max",
        "stress_max_drawdown", "kelly_fraction", "kelly_cap",
        "robinhood_max_notional_per_order", "follow_min_amount",
        "agentic_max_candidates", "retrain_window_days",
    }
    assert body["pbo_max"] == PBO_MAX
    assert body["dsr_min"] == DSR_MIN
    assert body["net_sharpe_min"] == NET_SHARPE_MIN
    assert body["max_drawdown_max"] == MAX_DRAWDOWN_MAX
    assert body["stress_max_drawdown"] == STRESS_MAX_DRAWDOWN
    assert body["kelly_fraction"] == settings.KELLY_FRACTION
    assert body["kelly_cap"] == settings.KELLY_CAP
    assert body["robinhood_max_notional_per_order"] == settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER
    assert body["follow_min_amount"] == settings.FOLLOW_MIN_AMOUNT
    assert body["agentic_max_candidates"] == float(settings.AGENTIC_MAX_CANDIDATES)
    assert body["retrain_window_days"] == float(MODEL_RETRAIN_WINDOW_DAYS)


def test_thresholds_never_depends_on_snapshot(tmp_path, monkeypatch):
    # Config constants, not persisted state — a cold-start OUTPUT_DIR (no
    # state_snapshot.json) must not change anything, unlike /universe or
    # /symbols/{ticker}.
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/thresholds")
    assert resp.status_code == 200
    assert resp.json()["pbo_max"] == 0.5


# ---------------------------------------------------------------------------
# GET /pilots/{id}/performance
# ---------------------------------------------------------------------------


def test_performance_good_range(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
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
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    _point_reports_at_fixtures(monkeypatch)
    resp = client.get("/pilots/balanced-blend/performance?range=1M")
    assert resp.status_code == 200
    body = resp.json()
    assert body["curve"] is None
    assert body["metrics"] is None
    assert body["reason"]  # honest explanation present


def test_performance_bad_range_422(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    resp = client.get("/pilots/trend-following/performance?range=5Y")
    assert resp.status_code == 422
    assert "Invalid range" in resp.json()["detail"]


def test_performance_unknown_pilot_404(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    resp = client.get("/pilots/nope/performance?range=1M")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /pilots/{id}/holdings & /trades
# ---------------------------------------------------------------------------


def test_holdings_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
        resp = client.get("/pilots/trend-following/holdings")
    assert resp.status_code == 200
    assert len(resp.json()) == 5


def test_holdings_unknown_404(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    resp = client.get("/pilots/nope/holdings")
    assert resp.status_code == 404


def test_holdings_empty_without_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/pilots/trend-following/holdings")
    assert resp.status_code == 200
    assert resp.json() == []


def test_trades_endpoint_empty_without_history(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/pilots/trend-following/trades?limit=5")
    assert resp.status_code == 200
    assert resp.json() == []


def test_trades_unknown_404(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    resp = client.get("/pilots/nope/trades")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /portfolio & /portfolio/equity-curve
# ---------------------------------------------------------------------------


def test_portfolio_honest_404_on_empty_db(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    class _EmptyStore:
        def latest_account_snapshot(self):
            return None

    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_EmptyStore()):
        resp = client.get("/portfolio")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No account snapshot yet — run the pipeline first."


def test_portfolio_404_on_db_error(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    class _BoomStore:
        def latest_account_snapshot(self):
            raise RuntimeError("cold db")

    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_BoomStore()):
        resp = client.get("/portfolio")
    assert resp.status_code == 404


def test_portfolio_serializes_snapshot(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
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


def test_portfolio_matches_frontend_contract(monkeypatch):
    """The /portfolio response must satisfy the webapp Portfolio /
    PortfolioPositionView type (Mismatch 4): positions is a LIST with
    qty/avg_cost field names, plus derived position_count/total_unrealized_pl
    and an honest source tag."""

    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
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


def test_equity_curve_envelope_empty_when_none(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    class _Store:
        def account_snapshot_history(self, since=None):
            return pd.DataFrame()

    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
        resp = client.get("/portfolio/equity-curve")
    assert resp.status_code == 200
    # {range, curve:[], buying_power_curve:[]} envelope — never a bare list,
    # never null (Mismatch 1).
    assert resp.json() == {"range": "1Y", "curve": [], "buying_power_curve": []}


def test_equity_curve_envelope_rows(monkeypatch):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
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
    # buying_power_curve is a PARALLEL series (G14 buying-power overlay),
    # sourced from the same rows' buying_power column.
    bp_curve = body["buying_power_curve"]
    assert isinstance(bp_curve, list) and len(bp_curve) == 2
    assert bp_curve[0] == {"date": "2026-07-09", "value": 500.0}
    assert bp_curve[1] == {"date": "2026-07-10", "value": 500.0}


def test_equity_curve_buying_power_missing_value_drops_only_that_point(monkeypatch):
    """A row with a missing/non-finite buying_power must not truncate the
    equity curve, and vice-versa -- the two series degrade independently."""
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "", raising=False)
    class _Store:
        def account_snapshot_history(self, since=None):
            return pd.DataFrame(
                [
                    ["2026-07-09T00:00:00+00:00", None, 1380.0, 8.0],
                    ["2026-07-10T00:00:00+00:00", 500.0, 1400.0, 10.0],
                ],
                columns=["fetched_at", "buying_power", "total_equity", "total_dividends"],
            )

    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
        resp = client.get("/portfolio/equity-curve?range=1M")
    body = resp.json()
    assert len(body["curve"]) == 2  # equity series unaffected by the missing buying_power
    assert len(body["buying_power_curve"]) == 1
    assert body["buying_power_curve"][0] == {"date": "2026-07-10", "value": 500.0}


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

        # This test is about proportional-split math, not Kelly sizing -- stub
        # the Kelly ceiling generously. plan_follow first calls
        # estimate_win_rate_and_payoff_per_strategy to decide cold-start vs.
        # warm; a real (unmocked) TransactionsStore for a brand-new
        # "Follow:<pilot_id>" strategy always has zero closed trades, which
        # would report cold-start and route around kelly_sizing_for_strategy
        # entirely -- so both must be stubbed together for this stub to have
        # any effect.
        #
        # ROBINHOOD_MAX_NOTIONAL_PER_ORDER must be EXPLICITLY pinned to the
        # "unset" default (0.0) here, not assumed ambient: execution/compose.py's
        # per-order notional cap clamps every intent's target_notional to this
        # value when it's a positive real number, which is exactly what a real
        # operator .env configures for live trading -- and would otherwise
        # silently truncate this test's $1000 proportional split down to
        # 5 * min-per-leg-cap, breaking the total-notional assertion below on
        # whatever machine happens to be running pytest.
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0):
                with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                    with mock.patch.object(pilots_api, "HistoricalStore", return_value=_Store()):
                        with mock.patch(
                            "sizing.kelly.estimate_win_rate_and_payoff_per_strategy",
                            return_value=(0.6, 1.5, 999),
                        ):
                            with mock.patch(
                                "sizing.kelly.kelly_sizing_for_strategy",
                                return_value=(1.0, "test_stub_no_ceiling"),
                            ):
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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
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


class TestTradeHistory:
    def test_shape_and_cold_start_honesty(self, tmp_path, monkeypatch):
        import data.broker_fills_store as bfs

        db_url = f"sqlite:///{tmp_path}/cold_trade_history.db"
        from data.broker_fills_store import BrokerFillsStore

        BrokerFillsStore(db_url=db_url)  # create schema, no fills
        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)

        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/portfolio/trade-history")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "trades", "summary", "total", "limit", "offset", "symbols",
            "available", "source", "last_ingested_at",
        }
        assert body["available"] is False
        assert body["trades"] == []
        assert body["total"] == 0
        assert body["source"] == "durable_store"
        assert body["summary"]["win_rate"] is None

    def test_populated_and_paginated(self, tmp_path, monkeypatch):
        import data.broker_fills_store as bfs
        from data.broker_fills_store import BrokerFillsStore
        from data.robinhood_orders import OrderFill
        from datetime import datetime, timezone

        db_url = f"sqlite:///{tmp_path}/populated_trade_history.db"
        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)
        store = BrokerFillsStore(db_url=db_url)
        store.record_fills([
            OrderFill("AAPL", "buy", 10, 100, datetime(2026, 1, 1, tzinfo=timezone.utc), "a1"),
            OrderFill("AAPL", "sell", 10, 120, datetime(2026, 1, 5, tzinfo=timezone.utc), "a2"),
        ])

        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/portfolio/trade-history?limit=1&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["total"] == 1
        assert len(body["trades"]) == 1
        assert body["trades"][0]["symbol"] == "AAPL"


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
    @pytest.fixture(autouse=True)
    def _reset_state_api_token(self, monkeypatch):
        """This machine's real .env sets a live STATE_API_TOKEN secret, which
        would otherwise make every fail-open GET /portfolio/attribution call
        below 401 instead of 200/422 (none of these tests exercise the auth
        gate itself, so none patch it locally). Reset to the coded default
        (unset) before each test so this class's outcome doesn't depend on
        this machine's local .env, matching a fresh clone. Should be
        superseded once conftest.py's per-test settings reset is extended to
        cover secret string fields too (see gui.env_io.SECRET_KEYS) -- kept
        local here per this task's scoping rules (conftest.py is out of
        bounds -- other agents own classes in the same file)."""
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)

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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)
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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)
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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/symbols/AAPL/forecast?horizon=30")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "symbol", "horizon_days", "reliability_curve",
            "skill_weights", "error_by_model", "pending", "completed", "reason",
        }
        assert body["symbol"] == "AAPL"
        assert body["horizon_days"] == 30
        assert isinstance(body["reliability_curve"], list)
        assert isinstance(body["skill_weights"], dict)
        assert isinstance(body["error_by_model"], list)


class TestSectorSelection:
    @pytest.fixture(autouse=True)
    def _reset_state_api_token(self, monkeypatch):
        """See TestPortfolioAttribution's identical fixture above for the
        full rationale -- this machine's real .env sets a live
        STATE_API_TOKEN, which would otherwise 401 every call below (none of
        these tests exercise the auth gate itself)."""
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)

    def test_shape_stable(self):
        resp = client.get("/sector/selection?target=NIO&n=3")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "target_symbol", "as_of", "top_n", "rows", "embedder", "pooling", "reason",
        }
        assert body["target_symbol"] == "NIO"
        assert body["top_n"] == 3
        assert isinstance(body["rows"], list)

    def test_no_history_returns_honest_reason_not_404(self):
        resp = client.get("/sector/selection?target=ZZZZ_NOT_TRACKED&n=3")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rows"] == []
        assert body["reason"]

    def test_n_defaults_to_three(self):
        resp = client.get("/sector/selection?target=NIO")
        assert resp.status_code == 200
        assert resp.json()["top_n"] == 3

    def test_n_below_range_rejected(self):
        resp = client.get("/sector/selection?target=NIO&n=0")
        assert resp.status_code == 422

    def test_n_above_range_rejected(self):
        resp = client.get("/sector/selection?target=NIO&n=6")
        assert resp.status_code == 422

    def test_missing_target_rejected(self):
        resp = client.get("/sector/selection?n=3")
        assert resp.status_code == 422


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
    @pytest.fixture(autouse=True)
    def _reset_state_api_token(self, monkeypatch):
        """See TestPortfolioAttribution's identical fixture above for the
        full rationale. The two tests below that DO exercise the auth gate
        (test_read_token_gates_the_endpoint, test_read_token_unset_is_open)
        already patch STATE_API_TOKEN themselves inside their own bodies, so
        this outer reset is simply overridden for the duration of those
        `with` blocks -- harmless."""
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)

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
        with mock.patch.object(settings, "STATE_API_TOKEN", ""):
            resp = client.get("/models")
        assert resp.status_code == 200
        rows = resp.json()
        assert isinstance(rows, list) and rows  # ml/registry.yaml is checked in
        row = rows[0]
        assert set(row) >= {
            "name", "role", "trained_date", "cpcv_dsr", "pbo",
            "n_train", "deployable", "notes", "age_days", "needs_retrain",
        }
        # Un-validated models keep null metrics, never a fabricated 0.
        assert any(r["cpcv_dsr"] is None for r in rows) or all(
            r["cpcv_dsr"] is not None for r in rows
        )

    def test_needs_retrain_age_flag_is_consistent_with_trained_date(self):
        """Rider 13b: age_days/needs_retrain are computed off the SAME
        MODEL_RETRAIN_WINDOW_DAYS constant GET /thresholds' retrain_window_days
        surfaces -- never independently re-derived/hard-coded in either place."""
        from datetime import date, datetime
        from gui.help_content import MODEL_RETRAIN_WINDOW_DAYS

        with mock.patch.object(settings, "STATE_API_TOKEN", ""):
            resp = client.get("/models")
        rows = resp.json()
        checked_any = False
        for row in rows:
            if row["trained_date"] is None:
                assert row["age_days"] is None and row["needs_retrain"] is None
                continue
            trained = datetime.strptime(row["trained_date"], "%Y-%m-%d").date()
            expected_age = (date.today() - trained).days
            assert row["age_days"] == expected_age
            assert row["needs_retrain"] == (expected_age >= MODEL_RETRAIN_WINDOW_DAYS)
            checked_any = True
        assert checked_any  # ml/registry.yaml has at least one dated model


class TestOptionsMatrix:
    def test_disabled_is_honest_empty(self, tmp_path):
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(settings, "STATE_API_TOKEN", None):
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
            with mock.patch.object(settings, "STATE_API_TOKEN", None):
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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(settings, "STATE_API_TOKEN", None):
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
            with mock.patch.object(settings, "STATE_API_TOKEN", None):
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

    @pytest.fixture(autouse=True)
    def _reset_state_api_token(self, monkeypatch):
        """See TestPortfolioAttribution's identical fixture above for the
        full rationale. test_read_token_gates_endpoint already patches
        STATE_API_TOKEN itself inside its own body, so this outer reset is
        simply overridden for the duration of that `with` block -- harmless."""
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)

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
            "portfolio_risk", "portfolio_heat", "equity_curve", "regime",
            "forecast_skill", "forecast_skill_by_symbol", "risk_gate_blocks",
            "circuit_breakers", "system_telemetry", "latency_heatmap",
            "sizing_cap_audit", "etf_transmission", "heartbeat", "strategy_pnl",
        }
        # system_telemetry is a LIVE psutil sample (point-in-time, not read
        # from a cold-start fixture) -- psutil is a hard requirements.txt
        # dependency, so it should always be available in the test env.
        assert body["system_telemetry"]["psutil_available"] is True
        assert isinstance(body["system_telemetry"]["cpu_percent"], (int, float))
        assert body["portfolio_risk"]["sharpe_ratio"] is None
        assert body["portfolio_risk"]["n_snapshots"] == 0
        assert body["portfolio_risk"]["reason"]
        assert body["portfolio_heat"]["heat_pct"] is None
        assert body["portfolio_heat"]["reason"]
        assert body["equity_curve"]["range"] == "1Y"
        assert body["equity_curve"]["points"] == []
        assert body["regime"]["market_regime"] is None
        assert body["regime"]["reason"]
        assert body["forecast_skill"]["horizon_days"] == 30
        assert body["forecast_skill"]["reliability_curve"] == []
        assert body["risk_gate_blocks"]["entries"] == []
        assert body["risk_gate_blocks"]["count"] == 0
        assert body["circuit_breakers"]["trips"] == []
        assert body["circuit_breakers"]["counts"] == {"critical": 0, "warning": 0, "total": 0}
        assert body["circuit_breakers"]["reason"]

    def test_circuit_breakers_surface_kill_switch_through_the_composite(self, tmp_path):
        """End-to-end wiring check: GET /observability/summary's new
        circuit_breakers section reflects a real KILL_SWITCH sentinel file,
        not just pilots/observability.py's own unit tests."""
        (tmp_path / "KILL_SWITCH").write_text("halted", encoding="utf-8")

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

        breakers = resp.json()["circuit_breakers"]
        assert breakers["counts"]["critical"] == 1
        assert breakers["trips"][0]["name"] == "global_kill_switch"
        assert breakers["trips"][0]["severity"] == "CRITICAL"

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

    def test_regime_carries_writable_fields(self, tmp_path):
        class _EmptyStore:
            def account_snapshot_history(self, since=None):
                return pd.DataFrame()

        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(settings, "MACRO_GATE_WRITES_ENABLED", False):
                with mock.patch(
                    "data.historical_store.HistoricalStore", return_value=_EmptyStore()
                ):
                    with mock.patch(
                        "forecasting.forecast_tracker.ForecastTracker",
                        side_effect=RuntimeError("unavailable"),
                    ):
                        resp = client.get("/observability/summary")
        regime = resp.json()["regime"]
        assert regime["macro_gate_writable"] is False
        assert "MACRO_GATE_WRITES_ENABLED=false" in regime["macro_gate_writable_note"]

        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(settings, "MACRO_GATE_WRITES_ENABLED", True):
                with mock.patch(
                    "data.historical_store.HistoricalStore", return_value=_EmptyStore()
                ):
                    with mock.patch(
                        "forecasting.forecast_tracker.ForecastTracker",
                        side_effect=RuntimeError("unavailable"),
                    ):
                        resp2 = client.get("/observability/summary")
        assert resp2.json()["regime"]["macro_gate_writable"] is True


# ===========================================================================
# PUT /observability/macro-gate — MACRO_REGIME_GATE_ENABLED write path
# ===========================================================================


class TestMacroGateWrite:
    def test_fails_closed_when_macro_gate_writes_disabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "MACRO_GATE_WRITES_ENABLED", False):
                resp = client.put(
                    "/observability/macro-gate",
                    json={"enabled": False, "reason": "false-positive VIX spike"},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 403
        assert "MACRO_GATE_WRITES_ENABLED" in resp.json()["detail"]

    def test_fails_closed_when_follow_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            with mock.patch.object(settings, "MACRO_GATE_WRITES_ENABLED", True):
                resp = client.put(
                    "/observability/macro-gate",
                    json={"enabled": False, "reason": "false-positive VIX spike"},
                    headers={"Authorization": "Bearer anything"},
                )
        assert resp.status_code == 403

    def test_fails_closed_with_wrong_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "MACRO_GATE_WRITES_ENABLED", True):
                resp = client.put(
                    "/observability/macro-gate",
                    json={"enabled": False, "reason": "false-positive VIX spike"},
                    headers={"Authorization": "Bearer WRONG"},
                )
        assert resp.status_code == 401

    def test_empty_reason_rejected_422(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "MACRO_GATE_WRITES_ENABLED", True):
                resp = client.put(
                    "/observability/macro-gate",
                    json={"enabled": False, "reason": ""},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 422

    def test_happy_path_disables_gate(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "MACRO_GATE_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "write_setting") as w:
                    resp = client.put(
                        "/observability/macro-gate",
                        json={"enabled": False, "reason": "idiosyncratic vol, not systemic"},
                        headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                    )
        assert resp.status_code == 200
        w.assert_called_once_with("MACRO_REGIME_GATE_ENABLED", False)
        body = resp.json()
        assert body["written"] == ["MACRO_REGIME_GATE_ENABLED"]
        assert body["applies"] == "next_daemon_restart"
        # Echoes the REQUEST BODY, not settings (which would show the stale
        # pre-write value and read as a failed write).
        assert body["enabled"] is False

    def test_happy_path_enables_gate(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "MACRO_GATE_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "write_setting") as w:
                    resp = client.put(
                        "/observability/macro-gate",
                        json={"enabled": True, "reason": "re-enabling before going live"},
                        headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                    )
        assert resp.status_code == 200
        w.assert_called_once_with("MACRO_REGIME_GATE_ENABLED", True)
        assert resp.json()["enabled"] is True

    def test_write_never_logs_token(self, caplog):
        with caplog.at_level("DEBUG"):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(settings, "MACRO_GATE_WRITES_ENABLED", True):
                    with mock.patch.object(pilots_api.env_io, "write_setting"):
                        client.put(
                            "/observability/macro-gate",
                            json={"enabled": False, "reason": "test"},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert _CMD_TOKEN not in caplog.text


# ===========================================================================
# GET /observability/logs — bounded, parsed tail of logs/investyo.log
# ===========================================================================


class TestObservabilityLogs:
    """Endpoint-level wiring/shape tests. Substantive parsing/classification
    logic is unit-tested directly against pilots/observability.py in
    tests/test_pilots_observability.py; these only confirm the FastAPI
    wiring (auth, query params, honest empty shape) end-to-end."""

    def test_missing_log_file_is_honest_empty(self, tmp_path):
        missing = tmp_path / "investyo.log"
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch("gui.orchestrator_runner.TELEMETRY_LOG_PATH", missing):
                resp = client.get("/observability/logs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["entries"] == []
        assert body["total_lines"] == 0
        assert body["returned_count"] == 0
        assert body["reason"]

    def test_warm_path_returns_parsed_entries_and_tally(self, tmp_path):
        log_path = tmp_path / "investyo.log"
        log_path.write_text(
            "2026-07-26 08:40:28,615  INFO      main_orchestrator — Cycle started\n"
            "2026-07-26 08:40:29,500  ERROR     data_engine — FRED unavailable\n",
            encoding="utf-8",
        )
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch("gui.orchestrator_runner.TELEMETRY_LOG_PATH", log_path):
                resp = client.get("/observability/logs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_lines"] == 2
        assert body["tally"]["INFO"] == 1
        assert body["tally"]["ERROR"] == 1
        assert body["systemic_count"] == 1
        assert body["reason"] is None
        assert body["log_path"] == str(log_path)

    def test_limit_param_bounds_returned_entries(self, tmp_path):
        log_path = tmp_path / "investyo.log"
        lines = [
            f"2026-07-26 08:40:{i:02d},000  INFO      main — line {i}" for i in range(10)
        ]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch("gui.orchestrator_runner.TELEMETRY_LOG_PATH", log_path):
                resp = client.get("/observability/logs?limit=2")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_lines"] == 10
        assert body["returned_count"] == 2
        assert len(body["entries"]) == 2

    def test_limit_out_of_bounds_422(self):
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            assert client.get("/observability/logs?limit=0").status_code == 422
            assert client.get("/observability/logs?limit=1001").status_code == 422

    def test_read_token_gates_endpoint(self, tmp_path):
        missing = tmp_path / "investyo.log"
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            with mock.patch("gui.orchestrator_runner.TELEMETRY_LOG_PATH", missing):
                no_auth = client.get("/observability/logs")
                wrong = client.get(
                    "/observability/logs", headers={"Authorization": "Bearer WRONG"}
                )
                ok = client.get(
                    "/observability/logs", headers={"Authorization": "Bearer read-tok"}
                )
        assert no_auth.status_code == 401
        assert wrong.status_code == 401
        assert ok.status_code == 200


class TestMacroGateWritesInvariants:
    def test_macro_regime_gate_enabled_key_stays_allowlisted(self):
        """The TARGET key this endpoint writes has been GUI-writable via the
        Streamlit Observability tab for a long time (gui/panels/observability.py)
        — this new write path must not require (or accidentally break) that."""
        assert "MACRO_REGIME_GATE_ENABLED" in pilots_api.env_io.ALLOWED_KEYS

    def test_macro_gate_writes_enabled_is_gui_writable(self):
        """2026-08-08 (PR #630 audit): reclassified into ALLOWED_KEYS by
        explicit operator decision -- not secret, so GUI-writability no
        longer turns on capability class alone. (Distinct from the assertion
        above: that one is about the TARGET key MACRO_REGIME_GATE_ENABLED,
        which has been allowlisted separately for a long time; this one is
        about the master switch that guards the NEW write path to it.) Still
        a settings_keysets.DANGEROUS_KEYS member (typed confirmation
        required on write); the endpoint remains independently gated by
        FOLLOW_API_TOKEN regardless."""
        assert "MACRO_GATE_WRITES_ENABLED" in pilots_api.env_io.ALLOWED_KEYS
        assert "MACRO_GATE_WRITES_ENABLED" not in pilots_api.env_io.SECRET_KEYS
        assert "MACRO_GATE_WRITES_ENABLED" not in pilots_api.env_io.EXCLUDED_FROM_GUI


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
        output/daemon.json (written at startup, or with state="stopped" at
        a graceful teardown) still supplies pid/interval/started_at, and
        `alive` honestly reads False."""
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
            with mock.patch.object(pilots_api.run_status.os, "kill", side_effect=ProcessLookupError):
                with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                    with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                        with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_InactiveKS()):
                            resp = client.get("/automation/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["daemon"]["alive"] is False
        # pid_alive is a MACHINE-CHECKED probe distinct from the file's own
        # (unverifiable, self-reported) "state" field -- this is the
        # regression pin for the actual bug this fix removes: a dead pid
        # must read pid_alive=False even though the file itself still says
        # "started".
        assert body["daemon"]["pid_alive"] is False
        assert body["daemon"]["source"] == "daemon_json"
        assert body["daemon"]["pid"] == 77880
        assert body["daemon"]["interval_seconds"] == 300
        assert body["last_run"] is None
        assert body["last_run_source"] == "state_snapshot"

    def test_control_api_reachable_but_no_daemon_attached_falls_back(self, tmp_path):
        """Regression pin: a reachable Control API still answers `GET
        /status` with HTTP 200 and {"daemon_alive": False} whenever no
        OrchestratorDaemon is attached (startup window, mid-restart, or the
        API served standalone). That 200 response is not proof of life --
        this must fall through to the daemon_json branch exactly like a
        connection failure does, never hardcode alive=True from the mere
        fact that *a* response came back. This was the root cause of the
        Settings screen showing "Daemon: Alive" while "Run now" reported
        "Orchestrator daemon is not reachable" in the same breath -- POST
        /automation/run correctly checked get_daemon() is None server-side
        while this endpoint didn't look at the identical daemon_alive flag
        the Control API had already told it about."""
        daemon_json = {
            "pid": 55123,
            "state": "started",
            "interval_seconds": 300,
            "started_at": "2026-07-16T15:34:45.942581+00:00",
            "port": 8601,
            "pilots_api_port": None,
        }
        (tmp_path / "daemon.json").write_text(__import__("json").dumps(daemon_json), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.run_status.os, "kill", return_value=None):
                with mock.patch.object(
                    pilots_api.daemon_client, "get_status",
                    return_value={"daemon_alive": False},
                ):
                    with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                        with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_InactiveKS()):
                            resp = client.get("/automation/status")
        assert resp.status_code == 200
        daemon = resp.json()["daemon"]
        assert daemon["alive"] is False
        assert daemon["source"] == "daemon_json"
        assert daemon["pid_alive"] is True
        assert daemon["pid"] == 55123

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
            "alive": False, "source": "none", "pid": None, "pid_alive": None, "port": None,
            "started_at": None, "interval_seconds": None, "is_running": None,
            "current_run_id": None, "engines_warm": None,
        }
        assert body["last_run"] is None
        assert body["last_run_source"] == "state_snapshot"

    def test_dead_daemon_pid_reports_down_without_destroying_the_record(self, tmp_path):
        """THE headline test for this fix: a daemon.json left behind by a
        SIGKILLed process (state still says "running", pid is dead) must
        report pid_alive=False -- and must NOT be discarded in favor of a
        source="none" response. The record's pid/port/interval_seconds/
        started_at are the whole reason this fallback exists; destroying
        them on a dead pid would be strictly less honest, not more."""
        daemon_json = {
            "pid": 99999,
            "state": "running",
            "interval_seconds": 300,
            "started_at": "2026-07-16T15:34:45.942581+00:00",
            "port": 8601,
            "pilots_api_port": None,
        }
        (tmp_path / "daemon.json").write_text(__import__("json").dumps(daemon_json), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.run_status.os, "kill", side_effect=ProcessLookupError):
                with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                    with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                        with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_InactiveKS()):
                            resp = client.get("/automation/status")
        assert resp.status_code == 200
        daemon = resp.json()["daemon"]
        assert daemon["alive"] is False
        assert daemon["source"] == "daemon_json"
        assert daemon["pid_alive"] is False
        assert daemon["pid"] == 99999
        assert daemon["port"] == 8601
        assert daemon["interval_seconds"] == 300
        assert daemon["started_at"] == "2026-07-16T15:34:45.942581+00:00"

    def test_stale_running_state_in_file_is_never_echoed_as_live(self, tmp_path):
        """The file's own "state" string is a self-report a SIGKILLed
        daemon can never correct -- this endpoint must never surface it
        directly (only the machine-checked pid_alive), and must not
        fabricate is_running from it either."""
        daemon_json = {
            "pid": 99999,
            "state": "running",
            "interval_seconds": 300,
            "started_at": "2026-07-16T15:34:45.942581+00:00",
            "port": 8601,
            "pilots_api_port": None,
        }
        (tmp_path / "daemon.json").write_text(__import__("json").dumps(daemon_json), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.run_status.os, "kill", side_effect=ProcessLookupError):
                with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                    with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                        with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_InactiveKS()):
                            resp = client.get("/automation/status")
        daemon = resp.json()["daemon"]
        assert daemon["is_running"] is None
        assert daemon["alive"] is False
        assert "state" not in daemon

    def test_control_api_path_reports_pid_alive_none_not_true(self, tmp_path):
        """Anti-fabrication guard on the live branch: GET /status doesn't
        echo a pid at all, so pid_alive must be None there -- never a
        fabricated True derived from daemon_alive/is_running."""
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(
                pilots_api.daemon_client, "get_status",
                return_value={"daemon_alive": True, "is_running": True, "interval_seconds": 60,
                              "started_at": None, "current_run_id": None, "engines_warm": True},
            ):
                with mock.patch.object(pilots_api.daemon_client, "get_latest_run", return_value=None):
                    with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_InactiveKS()):
                        resp = client.get("/automation/status")
        daemon = resp.json()["daemon"]
        assert daemon["source"] == "control_api"
        assert daemon["alive"] is True
        assert daemon["pid_alive"] is None

    def test_daemon_json_missing_pid_reports_pid_alive_none(self, tmp_path):
        """CONSTRAINT #4 at the API layer: an absent pid must read as
        unknowable, never a fabricated False."""
        daemon_json = {
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
        daemon = resp.json()["daemon"]
        assert daemon["pid_alive"] is None

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

    def test_schedule_running_value_survives_a_dead_pid(self, tmp_path):
        """Regression guard against a future "simplification" that nulls
        running_value/drift when the daemon.json pid turns out to be dead.
        Deliberately NOT suppressed: an operator who just edited .env needs
        to see drift against the daemon's LAST KNOWN interval, not "no
        drift" -- daemon.alive/daemon.pid_alive on GET /automation/status
        already convey deadness; this endpoint's only job is interval
        drift, and a dead daemon's last known interval is still the honest
        answer to "what was it running when it died"."""
        import json

        (tmp_path / "daemon.json").write_text(
            json.dumps({"interval_seconds": 120, "pid": 99999, "started_at": "x",
                        "port": 8601, "pilots_api_port": None}),
            encoding="utf-8",
        )
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), \
             mock.patch.object(settings, "ORCHESTRATOR_INTERVAL_SECONDS", 300), \
             mock.patch.object(pilots_api.run_status.os, "kill", side_effect=ProcessLookupError):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                resp = client.get("/automation/schedule")
        body = resp.json()
        assert body["interval"]["running_value"] == 120
        assert body["interval"]["configured_value"] == 300
        assert body["interval"]["drift"] is True

    def test_running_value_null_when_no_daemon_signal_at_all(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
                resp = client.get("/automation/schedule")
        body = resp.json()
        assert body["interval"]["running_value"] is None
        assert body["interval"]["drift"] is False  # null running_value never claims drift

    def test_interval_is_writable_by_default_in_this_build(self):
        with mock.patch.object(pilots_api.daemon_client, "get_status", return_value=None):
            resp = client.get("/automation/schedule")
        assert resp.json()["interval"]["writable"] is True

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
        # FOLLOW_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment
        # for why.
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
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
        # FOLLOW_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment
        # for why.
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
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
        # FOLLOW_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment
        # for why.
        with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
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
        # FOLLOW_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # there is no conftest fixture or test .env pinning it, so its value
        # otherwise depends on whatever real .env (if any) happens to be on the
        # machine running pytest (see TestExecutionModeWrite.test_command_token_required's
        # comment for the sibling "configured but missing header -> 401" case).
        with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
                resp = client.put("/automation/schedule/interval", json={"interval_seconds": 300})
        assert resp.status_code == 403


class TestAutomationWritesInvariants:
    def test_interval_key_is_allowlisted(self):
        assert "ORCHESTRATOR_INTERVAL_SECONDS" in pilots_api.env_io.ALLOWED_KEYS

    def test_automation_writes_enabled_is_gui_writable(self):
        """2026-08-08 (PR #630 audit): reclassified into ALLOWED_KEYS by
        explicit operator decision -- not secret, so this no longer needs to
        be hand-set-only. Still a settings_keysets.DANGEROUS_KEYS member
        (typed confirmation required on write); the endpoint remains
        independently gated by FOLLOW_API_TOKEN regardless."""
        assert "AUTOMATION_WRITES_ENABLED" in pilots_api.env_io.ALLOWED_KEYS
        assert "AUTOMATION_WRITES_ENABLED" not in pilots_api.env_io.SECRET_KEYS
        assert "AUTOMATION_WRITES_ENABLED" not in pilots_api.env_io.EXCLUDED_FROM_GUI


class TestExecutionModeWrite:
    """PUT /automation/execution-mode -- 1-Click Go Live toggle. Tests stub
    ``gui.strategy_registry.set_active_mode`` (its own DRY_RUN/ALPACA_PAPER
    writes are covered by that module's own tests) and redirect
    ``env_io.ENV_PATH`` at a scratch file for the ADVISORY_ONLY write, mirroring
    ``TestAutomationIntervalWrite``.

    Every request below carries the ``confirm`` dict a real caller now must
    send -- see ``TestExecutionModeConfirmation`` for the gate itself (missing
    confirmation, mismatched confirmation, advisory-only-touches-only-one-key,
    and the "cannot flip ADVISORY_ONLY without confirmation" proof)."""

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
                            json={
                                "mode": "paper",
                                "advisory_only": False,
                                "confirm": {
                                    "ADVISORY_ONLY": "ADVISORY_ONLY",
                                    "DRY_RUN": "DRY_RUN",
                                },
                            },
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
        happened (CONSTRAINT #4). Only ADVISORY_ONLY needs confirming here."""
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
                            json={
                                "mode": "advisory",
                                "advisory_only": True,
                                "confirm": {"ADVISORY_ONLY": "ADVISORY_ONLY"},
                            },
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
                                json={
                                    "mode": "live",
                                    "advisory_only": False,
                                    "confirm": {
                                        "ADVISORY_ONLY": "ADVISORY_ONLY",
                                        "DRY_RUN": "DRY_RUN",
                                    },
                                },
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
                    json={
                        "mode": "paper",
                        "advisory_only": False,
                        "confirm": {
                            "ADVISORY_ONLY": "ADVISORY_ONLY",
                            "DRY_RUN": "DRY_RUN",
                        },
                    },
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 403

    def test_command_token_required(self):
        # FOLLOW_API_TOKEN must be EXPLICITLY configured here, not assumed
        # ambient -- there is no conftest fixture or test .env pinning it, so
        # its value otherwise depends on whatever real .env (if any) happens
        # to be on the machine running pytest. With no token configured (the
        # honest default in a hermetic/CI checkout), a missing Authorization
        # header correctly hits require_command_token's "token wholly
        # unconfigured" branch (403), not "missing credentials" (401) --
        # those are two different failure modes, both real, so the test must
        # pin which one it's exercising rather than leave it up to whatever
        # `.env` happens to be lying around.
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                resp = client.put(
                    "/automation/execution-mode",
                    json={
                        "mode": "paper",
                        "advisory_only": False,
                        "confirm": {
                            "ADVISORY_ONLY": "ADVISORY_ONLY",
                            "DRY_RUN": "DRY_RUN",
                        },
                    },
                )
        assert resp.status_code == 401

    def test_401_on_wrong_command_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                resp = client.put(
                    "/automation/execution-mode",
                    json={
                        "mode": "paper",
                        "advisory_only": False,
                        "confirm": {
                            "ADVISORY_ONLY": "ADVISORY_ONLY",
                            "DRY_RUN": "DRY_RUN",
                        },
                    },
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
                                json={
                                    "mode": "paper",
                                    "advisory_only": False,
                                    "confirm": {
                                        "ADVISORY_ONLY": "ADVISORY_ONLY",
                                        "DRY_RUN": "DRY_RUN",
                                    },
                                },
                                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                            )
        assert _CMD_TOKEN not in caplog.text


class TestExecutionModeConfirmation:
    """The gate this PR adds: ``PUT /automation/execution-mode`` must require
    the SAME typed field-name confirmation ``PUT /settings/tunables`` requires
    for the same ``settings_keysets.DANGEROUS_KEYS`` fields (ADVISORY_ONLY,
    DRY_RUN) -- see ``_require_dangerous_confirmation``. Before this gate
    existed, this endpoint wrote both with zero confirmation of any kind,
    even though the general settings editor already required one for the
    same two fields. ``ALPACA_PAPER`` is also written by this endpoint but is
    NOT a ``DANGEROUS_KEYS`` member (an Alpaca-specific paper/live selector,
    not a broker-agnostic quarantine) and so needs no confirmation here
    either -- see ``test_alpaca_paper_is_written_without_needing_confirmation``."""

    def _put(self, payload, tmp_path=None, set_active_mode_mock=None):
        env_file = (tmp_path or pathlib.Path("/tmp")) / ".env"
        env_file.write_text("", encoding="utf-8")
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AUTOMATION_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                    with mock.patch("gui.strategy_registry.set_active_mode") as mocked:
                        resp = client.put(
                            "/automation/execution-mode",
                            json=payload,
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
                        if set_active_mode_mock is not None:
                            set_active_mode_mock.append(mocked)
        return resp, env_file

    def test_advisory_only_cannot_be_flipped_without_confirmation(self, tmp_path):
        """The headline proof: no ``confirm`` at all -> 422, ADVISORY_ONLY is
        NEVER written to .env."""
        resp, env_file = self._put(
            {"mode": "advisory", "advisory_only": False}, tmp_path=tmp_path
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "confirmation_required"
        assert detail["missing"] == ["ADVISORY_ONLY"]
        assert "ADVISORY_ONLY" not in env_file.read_text(encoding="utf-8")

    def test_advisory_only_can_be_flipped_with_confirmation(self, tmp_path):
        """The other half of the proof: the identical request, WITH the typed
        confirmation, succeeds and writes it."""
        resp, env_file = self._put(
            {
                "mode": "advisory",
                "advisory_only": False,
                "confirm": {"ADVISORY_ONLY": "ADVISORY_ONLY"},
            },
            tmp_path=tmp_path,
        )
        assert resp.status_code == 200
        assert "ADVISORY_ONLY=false" in env_file.read_text(encoding="utf-8")

    def test_missing_confirmation_for_mode_change_is_422_and_writes_nothing(self, tmp_path):
        """mode="live" needs TWO confirmations (ADVISORY_ONLY, DRY_RUN);
        sending zero must reject the whole request and call neither
        env_io.write_setting nor set_active_mode."""
        set_active_mode_calls: list = []
        resp, env_file = self._put(
            {"mode": "live", "advisory_only": False},
            tmp_path=tmp_path,
            set_active_mode_mock=set_active_mode_calls,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "confirmation_required"
        assert sorted(detail["missing"]) == ["ADVISORY_ONLY", "DRY_RUN"]
        assert detail["required"] == ["ADVISORY_ONLY", "DRY_RUN"]
        assert env_file.read_text(encoding="utf-8") == ""
        set_active_mode_calls[0].assert_not_called()

    def test_partial_confirmation_still_rejects_the_whole_request(self, tmp_path):
        """Confirming ADVISORY_ONLY alone for a mode="live" change (which also
        needs DRY_RUN confirmed) must still 422 -- there is no partial-write
        here, unlike the batch settings editors' per-key semantics. Nothing
        is written, including the confirmed key."""
        set_active_mode_calls: list = []
        resp, env_file = self._put(
            {
                "mode": "live",
                "advisory_only": False,
                "confirm": {"ADVISORY_ONLY": "ADVISORY_ONLY"},
            },
            tmp_path=tmp_path,
            set_active_mode_mock=set_active_mode_calls,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["missing"] == ["DRY_RUN"]
        assert env_file.read_text(encoding="utf-8") == ""
        set_active_mode_calls[0].assert_not_called()

    def test_mismatched_confirmation_value_is_rejected(self, tmp_path):
        """Confirming with the wrong string (not the field's own name) is
        treated as unconfirmed, not accepted as a truthy flag."""
        resp, env_file = self._put(
            {
                "mode": "advisory",
                "advisory_only": False,
                "confirm": {"ADVISORY_ONLY": "yes"},
            },
            tmp_path=tmp_path,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "confirmation_mismatch"
        assert detail["mismatched"] == ["ADVISORY_ONLY"]
        assert "ADVISORY_ONLY" not in env_file.read_text(encoding="utf-8")

    def test_confirming_one_dangerous_field_does_not_implicitly_confirm_another(self, tmp_path):
        """Echoing a field's own name (rather than a blanket boolean) means a
        caller who only meant to confirm DRY_RUN cannot accidentally also
        confirm ADVISORY_ONLY -- each key must be named."""
        set_active_mode_calls: list = []
        resp, env_file = self._put(
            {
                "mode": "live",
                "advisory_only": False,
                "confirm": {"DRY_RUN": "DRY_RUN"},
            },
            tmp_path=tmp_path,
            set_active_mode_mock=set_active_mode_calls,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["missing"] == ["ADVISORY_ONLY"]
        assert env_file.read_text(encoding="utf-8") == ""
        set_active_mode_calls[0].assert_not_called()

    def test_alpaca_paper_is_written_without_needing_confirmation(self, tmp_path):
        """ALPACA_PAPER is written by this same call (mode != "advisory") but
        is NOT a settings_keysets.DANGEROUS_KEYS member -- an Alpaca-specific
        paper/live account selector, not a broker-agnostic quarantine like
        ADVISORY_ONLY/DRY_RUN -- so confirming only those two is sufficient
        even though ALPACA_PAPER is among the keys `written`."""
        set_active_mode_calls: list = []
        resp, env_file = self._put(
            {
                "mode": "live",
                "advisory_only": False,
                "confirm": {"ADVISORY_ONLY": "ADVISORY_ONLY", "DRY_RUN": "DRY_RUN"},
            },
            tmp_path=tmp_path,
            set_active_mode_mock=set_active_mode_calls,
        )
        assert resp.status_code == 200
        assert resp.json()["written"] == ["ADVISORY_ONLY", "DRY_RUN", "ALPACA_PAPER"]
        set_active_mode_calls[0].assert_called_once_with("live")


# ===========================================================================
# GET /strategy/matrix + PUT /strategy/modules
# ===========================================================================


def _full_weights_from_matrix():
    """Fetch the matrix (read-only, fail-open) and build a full-coverage weight
    map (every known module -> its weight, 0.0 where None), as the PWA would.

    STATE_API_TOKEN is explicitly reset to the coded default (unset) here --
    this helper relies on GET /strategy/matrix's fail-open behavior, which a
    real operator .env setting a live STATE_API_TOKEN secret would otherwise
    break (every call would 401 instead of returning the matrix), independent
    of anything TestStrategyModulesWrite's own tests patch."""
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
            matrix = client.get("/strategy/matrix").json()
    return {m["name"]: (m["weight"] if m["weight"] is not None else 0.0) for m in matrix["modules"]}


class TestStrategyMatrixRead:
    @pytest.fixture(autouse=True)
    def _reset_state_api_token(self, monkeypatch):
        """See TestPortfolioAttribution's identical fixture above for the
        full rationale. The two tests below that DO exercise the auth gate
        (test_fail_open_read_with_no_token, test_401_on_wrong_read_token)
        already patch STATE_API_TOKEN themselves inside their own bodies, so
        this outer reset is simply overridden for the duration of those
        `with` blocks -- harmless."""
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)

    def test_shape_and_modules(self):
        with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
            resp = client.get("/strategy/matrix")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("modules", "disabled", "max_weight", "writable", "note", "env_drift", "reason", "meta_label"):
            assert key in body
        assert len(body["modules"]) > 0
        row = body["modules"][0]
        for key in ("name", "weight", "effective_weight", "enabled", "source", "pinned_zero"):
            assert key in row
        for key in ("bins", "count", "missing", "n_gated", "all_unity", "min", "max", "min_confidence", "reason"):
            assert key in body["meta_label"]

    def test_meta_label_reflects_the_fixture_snapshot(self):
        # tests/fixtures/state_snapshot.json's 8 signals all carry
        # meta_label_composite == 1.0 (the honest "no MetaLabelers registered"
        # state) -- proves GET /strategy/matrix actually threads
        # pilots.strategy_matrix._meta_label_distribution's real output
        # through, not a stub.
        with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
            resp = client.get("/strategy/matrix")
        ml = resp.json()["meta_label"]
        assert ml["count"] == 8
        assert ml["all_unity"] is True
        assert ml["n_gated"] == 0

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

    def test_env_drift_parses_env_file_once_not_per_key(self, tmp_path):
        """``_env_drift`` checks two keys (SIGNAL_WEIGHTS,
        DISABLED_SIGNAL_MODULES) -- before the ``env_io.read_raw()`` fix, each
        used its own ``env_io.get_value()`` call, so a single GET /strategy/matrix
        re-parsed ``.env`` twice. Pins it at exactly one full-file parse."""
        env_file = tmp_path / ".env"
        env_file.write_text("SIGNAL_WEIGHTS={}\n", encoding="utf-8")
        real_dotenv_values = pilots_api.env_io.dotenv_values
        with mock.patch.object(
            pilots_api.env_io, "dotenv_values", wraps=real_dotenv_values
        ) as spy:
            with mock.patch.object(settings, "OUTPUT_DIR", FIXTURES):
                with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                    resp = client.get("/strategy/matrix")
        assert resp.status_code == 200
        assert spy.call_count == 1


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

    def test_strategy_writes_enabled_is_gui_writable(self):
        """2026-08-08 (PR #630 audit): reclassified into ALLOWED_KEYS by
        explicit operator decision -- not secret, so this no longer needs to
        be hand-set-only. Still a settings_keysets.DANGEROUS_KEYS member
        (typed confirmation required on write); the endpoint remains
        independently gated by FOLLOW_API_TOKEN regardless."""
        assert "STRATEGY_WRITES_ENABLED" in pilots_api.env_io.ALLOWED_KEYS
        assert "STRATEGY_WRITES_ENABLED" not in pilots_api.env_io.SECRET_KEYS
        assert "STRATEGY_WRITES_ENABLED" not in pilots_api.env_io.EXCLUDED_FROM_GUI


# ===========================================================================
# GET /strategy/health — catalog-wide deployability-gate breakdown
# ===========================================================================


class TestStrategyHealth:
    @pytest.fixture(autouse=True)
    def _reset_state_api_token(self, monkeypatch):
        """See TestPortfolioAttribution's identical fixture above for the
        full rationale. test_fail_open_read_with_no_token /
        test_401_on_wrong_read_token already patch STATE_API_TOKEN
        themselves inside their own bodies, so this outer reset is simply
        overridden for the duration of those `with` blocks -- harmless."""
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)

    def test_shape_and_all_gates_pass_for_fixture_backed_pilot(self, monkeypatch, tmp_path):
        _point_reports_at_fixtures(monkeypatch)
        # `_validation_history_dir()` defaults to the real, CWD-relative
        # "reports/history" -- an operator checkout that has actually run
        # the validation pipeline has a real
        # reports/history/timeseries_momentum_validation_history.jsonl on
        # disk there, which would make this test's "no reports/history
        # fixture wired -> honest empty trend" assertion below false for the
        # wrong reason. Point it at an empty tmp dir instead, matching
        # test_trend_populated_from_history_fixture_oldest_first's identical
        # pattern below.
        monkeypatch.setattr(pilots_api, "_validation_history_dir", lambda: str(tmp_path / "no_history_here"))
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
# GET /strategy/validation-trend — cross-strategy validation snapshot +
# run-over-run trend + macro-regime timeline. The CROSS-STRATEGY counterpart
# to TestStrategyHealth above: reads every reports/*_validation_summary.json
# on disk regardless of catalog Pilot mapping, so
# tests/fixtures/multifactor_lowvol_size_validation_summary.json (a strategy
# with NO pilots.catalog Pilot pointing at it) is the key fixture proving the
# "invisible on /strategy/health" gap this endpoint closes.
# ---------------------------------------------------------------------------


class TestStrategyValidationTrend:
    def test_cross_strategy_snapshot_includes_pilot_and_orphan_strategies(self, tmp_path, monkeypatch):
        _point_reports_at_fixtures(monkeypatch)
        # tmp_path (empty) for OUTPUT_DIR only -- keeps the regime-timeline
        # section's list_rotated_snapshots() from touching (or mkdir'ing) the
        # real repo output/history/ dir; this test doesn't assert on regime.
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/strategy/validation-trend")
        assert resp.status_code == 200
        body = resp.json()
        ids = {row["strategy_id"] for row in body["strategies"]}
        # timeseries_momentum is wired to the trend-following Pilot; it also
        # already appears on GET /strategy/health. multifactor_lowvol_size is
        # NOT wired to any pilots.catalog Pilot -- it would be invisible on
        # /strategy/health entirely, but must appear here.
        assert "timeseries_momentum" in ids
        assert "multifactor_lowvol_size" in ids
        assert body["strategies_reason"] is None
        # Deterministic, sorted by strategy_id.
        assert [r["strategy_id"] for r in body["strategies"]] == sorted(ids)

    def test_snapshot_row_schema_matches_legacy_panel_columns(self, tmp_path, monkeypatch):
        _point_reports_at_fixtures(monkeypatch)
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/strategy/validation-trend")
        row = next(
            r for r in resp.json()["strategies"] if r["strategy_id"] == "multifactor_lowvol_size"
        )
        assert row == {
            "strategy_id": "multifactor_lowvol_size",
            "deployable": False,
            "pbo": 0.28,
            "dsr": 0.93,
            "sharpe": 0.61,
            "max_drawdown": 0.22,
            "is_options_selling": False,
            "stress_gate_passed": True,
            "report_date": "2026-07-14",
        }

    def test_cold_start_no_reports_dir_is_honest_not_500(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pilots_api, "_reports_dir", lambda: str(tmp_path / "does_not_exist"))
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/strategy/validation-trend")
        assert resp.status_code == 200
        body = resp.json()
        assert body["strategies"] == []
        assert body["strategies_reason"]
        assert body["trend"] == {}
        assert body["trend_reason"]
        assert body["regime_timeline"] == []
        assert body["n_rotated_snapshots"] == 0
        assert body["regime_reason"]

    def test_corrupt_summary_file_skipped_never_500(self, tmp_path, monkeypatch):
        (tmp_path / "good_validation_summary.json").write_text(
            json.dumps({"strategy_id": "good", "deployable": True, "pbo": 0.1,
                        "dsr": 0.99, "sharpe": 1.5, "max_drawdown": 0.05,
                        "is_options_selling": False, "stress_gate_passed": True,
                        "report_date": "2026-07-01"}),
            encoding="utf-8",
        )
        (tmp_path / "corrupt_validation_summary.json").write_text(
            "{not valid json,,,", encoding="utf-8"
        )
        monkeypatch.setattr(pilots_api, "_reports_dir", lambda: str(tmp_path))
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/strategy/validation-trend")
        assert resp.status_code == 200
        ids = {r["strategy_id"] for r in resp.json()["strategies"]}
        assert ids == {"good"}

    def test_literal_nan_in_summary_file_is_nulled_not_reserialized(self, tmp_path, monkeypatch):
        # json.loads accepts a bare NaN token as a Python extension; a summary
        # written this way must never round-trip back out as an invalid JSON
        # NaN literal (mirrors the bug fixed in pilots/live_inventory.py).
        (tmp_path / "nanstrat_validation_summary.json").write_text(
            '{"strategy_id": "nanstrat", "deployable": false, "pbo": NaN, '
            '"dsr": 0.9, "sharpe": Infinity, "max_drawdown": 0.1, '
            '"is_options_selling": false, "stress_gate_passed": null, '
            '"report_date": "2026-07-01"}',
            encoding="utf-8",
        )
        monkeypatch.setattr(pilots_api, "_reports_dir", lambda: str(tmp_path))
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/strategy/validation-trend")
        assert resp.status_code == 200
        assert "NaN" not in resp.text
        assert "Infinity" not in resp.text
        row = next(r for r in resp.json()["strategies"] if r["strategy_id"] == "nanstrat")
        assert row["pbo"] is None
        assert row["sharpe"] is None
        assert row["stress_gate_passed"] is None

    def test_trend_requires_at_least_two_runs(self, tmp_path, monkeypatch):
        (tmp_path / "onerun_validation_summary.json").write_text(
            json.dumps({"strategy_id": "onerun", "deployable": True}), encoding="utf-8"
        )
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        (history_dir / "onerun_validation_history.jsonl").write_text(
            json.dumps({"report_date": "2026-06-01", "dsr": 0.9}) + "\n", encoding="utf-8"
        )
        monkeypatch.setattr(pilots_api, "_reports_dir", lambda: str(tmp_path))
        monkeypatch.setattr(pilots_api, "_validation_history_dir", lambda: str(history_dir))
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/strategy/validation-trend")
        body = resp.json()
        assert "onerun" not in body["trend"]
        assert body["trend_reason"]

    def test_trend_populated_oldest_first_for_two_plus_runs(self, tmp_path, monkeypatch):
        (tmp_path / "tworun_validation_summary.json").write_text(
            json.dumps({"strategy_id": "tworun", "deployable": True}), encoding="utf-8"
        )
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        rows = [
            {"report_date": "2026-06-01", "pbo": 0.4, "dsr": 0.90, "sharpe": 0.40,
             "max_drawdown": 0.20, "deployable": False},
            {"report_date": "2026-06-15", "pbo": 0.18, "dsr": 0.972, "sharpe": 1.14,
             "max_drawdown": 0.176, "deployable": True},
        ]
        (history_dir / "tworun_validation_history.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
        monkeypatch.setattr(pilots_api, "_reports_dir", lambda: str(tmp_path))
        monkeypatch.setattr(pilots_api, "_validation_history_dir", lambda: str(history_dir))
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/strategy/validation-trend")
        body = resp.json()
        assert [t["report_date"] for t in body["trend"]["tworun"]] == ["2026-06-01", "2026-06-15"]
        assert body["trend_reason"] is None

    def test_regime_timeline_needs_two_rotated_snapshots(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pilots_api, "_reports_dir", lambda: str(tmp_path))
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/strategy/validation-trend")
        body = resp.json()
        assert body["regime_timeline"] == []
        assert body["n_rotated_snapshots"] == 0
        assert body["regime_reason"]

    def test_regime_timeline_only_shows_transitions(self, tmp_path, monkeypatch):
        from scripts.snapshot_diff import rotate_snapshot

        base_ts = datetime(2026, 7, 1, tzinfo=timezone.utc)

        def _snap(offset_hours: int, regime: str) -> dict:
            ts = base_ts + timedelta(hours=offset_hours)
            return {"timestamp": ts.isoformat(), "market_regime": regime}

        # RISK ON -> RISK ON (no change) -> RISK OFF -> RISK OFF (no change).
        # Only the two genuine transitions (offsets 0 and 48) should render.
        rotate_snapshot(_snap(0, "RISK ON"), tmp_path, max_age_days=0)
        rotate_snapshot(_snap(24, "RISK ON"), tmp_path, max_age_days=0)
        rotate_snapshot(_snap(48, "RISK OFF"), tmp_path, max_age_days=0)
        rotate_snapshot(_snap(72, "RISK OFF"), tmp_path, max_age_days=0)

        monkeypatch.setattr(pilots_api, "_reports_dir", lambda: str(tmp_path))
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            resp = client.get("/strategy/validation-trend")
        body = resp.json()
        assert body["n_rotated_snapshots"] == 4
        assert [t["market_regime"] for t in body["regime_timeline"]] == ["RISK ON", "RISK OFF"]
        assert body["regime_reason"] is None

    def test_fail_open_read_with_no_token(self, tmp_path, monkeypatch):
        _point_reports_at_fixtures(monkeypatch)
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), \
                mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/strategy/validation-trend")
        assert resp.status_code == 200

    def test_401_on_wrong_read_token(self, tmp_path, monkeypatch):
        _point_reports_at_fixtures(monkeypatch)
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), \
                mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get("/strategy/validation-trend", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401


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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)
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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)
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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)
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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)
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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
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
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
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

    @pytest.fixture(autouse=True)
    def _reset_state_api_token(self, monkeypatch):
        """See TestPortfolioAttribution's identical fixture above for the
        full rationale. test_no_auth_token_required_read_tier already
        patches STATE_API_TOKEN itself inside its own body, so this outer
        reset is simply overridden for the duration of that `with` block --
        harmless."""
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)

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


# ===========================================================================
# GET /agentic/status, GET /agentic/discovery, PUT /agentic/scan-config —
# the Agentic Trading tab's composite status, scan-discovered candidates, and
# gated scan-config write.
# ===========================================================================


def _fake_queue_snapshot(**overrides):
    """A real ExecutionQueueSnapshot (not a Mock) so the REAL
    is_queue_stale/queue_age_seconds functions can process it — only
    read_execution_queue itself is mocked (it ignores settings.OUTPUT_DIR;
    see gui/robinhood_execution_panel.py's module-top EXECUTION_QUEUE_PATH)."""
    from datetime import datetime, timezone

    from gui.robinhood_execution_panel import ExecutionQueueSnapshot

    base = dict(
        generated_at=datetime.now(timezone.utc).isoformat(),
        mode="review",
        kill_switch_active=False,
        max_notional_per_order=500.0,
        n_intents=2,
        n_placeable=1,
        intents=[],
    )
    base.update(overrides)
    return ExecutionQueueSnapshot(**base)


class TestAgenticStatus:
    @pytest.fixture(autouse=True)
    def _reset_state_api_token(self, monkeypatch):
        """See TestPortfolioAttribution's identical fixture above for the
        full rationale. test_fail_open_read_with_no_token /
        test_401_on_wrong_read_token already patch STATE_API_TOKEN
        themselves inside their own bodies, so this outer reset is simply
        overridden for the duration of those `with` blocks -- harmless."""
        monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)

    def test_shape_and_composition(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(
                pilots_api.execution_panel, "read_execution_queue",
                return_value=_fake_queue_snapshot(),
            ):
                with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_InactiveKS()):
                    resp = client.get("/agentic/status")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("mode", "advisory_only", "kill_switch", "queue", "follows", "agent_loop"):
            assert key in body
        assert body["mode"] == "review"
        assert body["kill_switch"] == {"active": False, "reason": None}
        assert body["queue"]["n_intents"] == 2
        assert body["queue"]["n_placeable"] == 1
        assert body["follows"] == {"n_active": 0, "total_amount": 0.0}
        # No agent_state.json in tmp_path -> honest cold-start, never fabricated.
        assert body["agent_loop"]["cycle_count"] == 0
        assert body["agent_loop"]["reason"] is not None

    def test_cold_start_no_queue_file(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(
                pilots_api.execution_panel, "read_execution_queue", return_value=None
            ):
                with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_InactiveKS()):
                    resp = client.get("/agentic/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "off"
        assert body["queue"]["generated_at"] is None
        assert body["queue"]["n_intents"] == 0

    def test_kill_switch_active_reflected(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(
                pilots_api.execution_panel, "read_execution_queue", return_value=None
            ):
                with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_ActiveKS()):
                    resp = client.get("/agentic/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["kill_switch"] == {"active": True, "reason": "test halt"}

    def test_active_follows_counted_and_summed(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                client.put(
                    "/follows", json={"pilot_id": "trend-following", "amount": 250.0},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
                client.put(
                    "/follows", json={"pilot_id": "dip-buyer", "amount": 100.0},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
                with mock.patch.object(
                    pilots_api.execution_panel, "read_execution_queue", return_value=None
                ):
                    with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_InactiveKS()):
                        resp = client.get("/agentic/status")
        assert resp.status_code == 200
        assert resp.json()["follows"] == {"n_active": 2, "total_amount": 350.0}

    def test_fail_open_read_with_no_token(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                with mock.patch.object(
                    pilots_api.execution_panel, "read_execution_queue", return_value=None
                ):
                    with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_InactiveKS()):
                        resp = client.get("/agentic/status")
        assert resp.status_code == 200

    def test_401_on_wrong_read_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get("/agentic/status", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_never_500_on_corrupt_agent_state(self, tmp_path):
        (tmp_path / "agent_state.json").write_text("{ not valid json", encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(
                pilots_api.execution_panel, "read_execution_queue", return_value=None
            ):
                with mock.patch.object(pilots_api, "GlobalKillSwitch", return_value=_InactiveKS()):
                    resp = client.get("/agentic/status")
        assert resp.status_code == 200
        assert resp.json()["agent_loop"]["reason"] is not None


class TestAgenticDiscoveryRead:
    def test_cold_start_shape(self, tmp_path):
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/agentic/discovery")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("generated_at", "candidates", "scan_configs", "reason", "writable", "note"):
            assert key in body
        assert body["candidates"] == []
        assert body["reason"] is not None

    def test_writable_tracks_the_flag(self, tmp_path):
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                with mock.patch.object(settings, "AGENTIC_DISCOVERY_ENABLED", True):
                    on = client.get("/agentic/discovery").json()
                with mock.patch.object(settings, "AGENTIC_DISCOVERY_ENABLED", False):
                    off = client.get("/agentic/discovery").json()
        assert on["writable"] is True
        assert off["writable"] is False
        assert "AGENTIC_DISCOVERY_ENABLED=false" in off["note"]

    def test_fail_open_read_with_no_token(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/agentic/discovery")
        assert resp.status_code == 200

    def test_401_on_wrong_read_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get("/agentic/discovery", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_never_500_on_corrupt_candidates_file(self, tmp_path):
        (tmp_path / "scan_candidates.json").write_text("{ not valid json", encoding="utf-8")
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/agentic/discovery")
        assert resp.status_code == 200
        assert resp.json()["candidates"] == []

    def test_populated_candidates_and_configs(self, tmp_path):
        (tmp_path / "scan_candidates.json").write_text(
            json.dumps(
                {
                    "generated_at": "2026-07-18T00:00:00+00:00",
                    "candidates": [
                        {"symbol": "NVDA", "action": "BUY", "conviction": 0.7},
                        {"symbol": "PLTR", "action": None, "conviction": None},
                    ],
                }
            ),
            encoding="utf-8",
        )
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/agentic/discovery")
        assert resp.status_code == 200
        body = resp.json()
        assert body["reason"] is None
        symbols = {c["symbol"]: c for c in body["candidates"]}
        assert symbols["NVDA"]["action"] == "BUY"
        assert symbols["PLTR"]["action"] is None  # never fabricated


class TestAgenticScanConfigWrite:
    def _auth(self):
        return {"Authorization": f"Bearer {_CMD_TOKEN}"}

    def test_fails_closed_when_agentic_discovery_disabled(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AGENTIC_DISCOVERY_ENABLED", False):
                resp = client.put(
                    "/agentic/scan-config",
                    json={"name": "breakout", "filters": {"min_price": 5}, "enabled": True},
                    headers=self._auth(),
                )
        assert resp.status_code == 403

    def test_fails_closed_when_follow_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            with mock.patch.object(settings, "AGENTIC_DISCOVERY_ENABLED", True):
                resp = client.put(
                    "/agentic/scan-config",
                    json={"name": "breakout", "filters": {}, "enabled": True},
                    headers={"Authorization": "Bearer anything"},
                )
        assert resp.status_code == 403

    def test_401_on_wrong_command_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AGENTIC_DISCOVERY_ENABLED", True):
                resp = client.put(
                    "/agentic/scan-config",
                    json={"name": "breakout", "filters": {}, "enabled": True},
                    headers={"Authorization": "Bearer WRONG"},
                )
        assert resp.status_code == 401

    def test_happy_path_persists_and_echoes_stored_row(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AGENTIC_DISCOVERY_ENABLED", True):
                with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                    resp = client.put(
                        "/agentic/scan-config",
                        json={
                            "name": "high_momentum_breakout",
                            "filters": {"min_price": 5, "min_volume": 1000000},
                            "enabled": True,
                        },
                        headers=self._auth(),
                    )
        assert resp.status_code == 200
        body = resp.json()
        assert body["applies"] == "next_discovery_run"
        row = body["scan_config"]
        assert row["name"] == "high_momentum_breakout"
        assert row["filters"] == {"min_price": 5, "min_volume": 1000000}
        assert row["enabled"] is True
        assert "created_at" in row and "updated_at" in row
        # Actually persisted to disk (not just echoed).
        on_disk = json.loads((tmp_path / "scan_configs.json").read_text(encoding="utf-8"))
        assert on_disk["scan_configs"][-1]["name"] == "high_momentum_breakout"

    def test_upsert_calls_store_exactly_once_with_full_payload(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AGENTIC_DISCOVERY_ENABLED", True):
                with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                    with mock.patch.object(
                        pilots_api.ScanConfigStore, "upsert",
                        return_value={
                            "name": "x", "filters": {"a": 1}, "enabled": True,
                            "created_at": "t0", "updated_at": "t0",
                        },
                    ) as up:
                        resp = client.put(
                            "/agentic/scan-config",
                            json={"name": "x", "filters": {"a": 1}, "enabled": True},
                            headers=self._auth(),
                        )
        assert resp.status_code == 200
        up.assert_called_once_with("x", {"a": 1}, enabled=True)

    def test_write_never_logs_token(self, tmp_path, caplog):
        with caplog.at_level("DEBUG"):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(settings, "AGENTIC_DISCOVERY_ENABLED", True):
                    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                        client.put(
                            "/agentic/scan-config",
                            json={"name": "x", "filters": {}, "enabled": True},
                            headers=self._auth(),
                        )
        assert _CMD_TOKEN not in caplog.text


class TestAgenticWatchWrite:
    """POST /agentic/watch — appends a discovered candidate to watchlist.txt.

    Same auth tier as the scan-config write (require_command_token +
    AGENTIC_DISCOVERY_ENABLED). Patches watchlist_writer.DEFAULT_WATCHLIST_PATH
    to a tmp file (the endpoint writes the CWD-relative watchlist.txt otherwise)
    and forces WATCHLIST unset unless a test is exercising the precedence guard.
    """

    def _auth(self):
        return {"Authorization": f"Bearer {_CMD_TOKEN}"}

    def _patch_path(self, tmp_path):
        return mock.patch(
            "pilots.watchlist_writer.DEFAULT_WATCHLIST_PATH", tmp_path / "watchlist.txt"
        )

    def _post(self, symbol, tmp_path, env_watchlist=""):
        with mock.patch.dict(os.environ, {"WATCHLIST": env_watchlist}):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(settings, "AGENTIC_DISCOVERY_ENABLED", True):
                    with self._patch_path(tmp_path):
                        return client.post(
                            "/agentic/watch", json={"symbol": symbol}, headers=self._auth()
                        )

    def test_fails_closed_when_agentic_discovery_disabled(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AGENTIC_DISCOVERY_ENABLED", False):
                resp = client.post("/agentic/watch", json={"symbol": "NVDA"}, headers=self._auth())
        assert resp.status_code == 403

    def test_fails_closed_when_follow_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            with mock.patch.object(settings, "AGENTIC_DISCOVERY_ENABLED", True):
                resp = client.post(
                    "/agentic/watch", json={"symbol": "NVDA"},
                    headers={"Authorization": "Bearer anything"},
                )
        assert resp.status_code == 403

    def test_401_on_wrong_command_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "AGENTIC_DISCOVERY_ENABLED", True):
                resp = client.post(
                    "/agentic/watch", json={"symbol": "NVDA"},
                    headers={"Authorization": "Bearer WRONG"},
                )
        assert resp.status_code == 401

    def test_happy_path_appends_and_echoes(self, tmp_path):
        resp = self._post("NVDA", tmp_path)
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "NVDA"
        assert body["added"] == ["NVDA"]
        assert body["already_present"] == []
        assert body["applies"] == "next_pipeline_run"
        # Actually written to disk.
        contents = (tmp_path / "watchlist.txt").read_text(encoding="utf-8")
        assert "NVDA" in contents.splitlines()

    def test_uppercases_symbol_before_writing(self, tmp_path):
        resp = self._post("nvda", tmp_path)
        assert resp.status_code == 200
        assert resp.json()["added"] == ["NVDA"]
        assert "NVDA" in (tmp_path / "watchlist.txt").read_text().splitlines()

    def test_dedup_reports_already_present_and_does_not_double_write(self, tmp_path):
        wl = tmp_path / "watchlist.txt"
        wl.write_text("AAPL\nNVDA\n", encoding="utf-8")
        resp = self._post("NVDA", tmp_path)
        assert resp.status_code == 200
        body = resp.json()
        assert body["added"] == []
        assert body["already_present"] == ["NVDA"]
        # NVDA still appears exactly once.
        assert wl.read_text().split().count("NVDA") == 1

    def test_watchlist_env_precedence_returns_409(self, tmp_path):
        resp = self._post("NVDA", tmp_path, env_watchlist="AAPL,MSFT")
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "watchlist_env_precedence"
        # Nothing was written when the env var takes precedence.
        assert not (tmp_path / "watchlist.txt").exists()

    def test_invalid_symbol_returns_422(self, tmp_path):
        resp = self._post("NOT A TICKER!", tmp_path)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "invalid_symbol"

    def test_write_never_logs_token(self, tmp_path, caplog):
        with caplog.at_level("DEBUG"):
            self._post("NVDA", tmp_path)
        assert _CMD_TOKEN not in caplog.text


class TestAgenticDiscoveryInvariants:
    def test_agentic_discovery_enabled_is_gui_writable(self):
        """AGENTIC_DISCOVERY_ENABLED was previously a hand-set-only invariant
        (like STRATEGY_WRITES_ENABLED) but was made GUI-writable by operator
        decision. It must stay a non-secret allowlisted key (PUT
        /agentic/scan-config remains gated independently by FOLLOW_API_TOKEN via
        require_agentic_discovery_enabled's command-token dependency, so this
        flag's own writability is not the sole safeguard)."""
        assert "AGENTIC_DISCOVERY_ENABLED" in pilots_api.env_io.ALLOWED_KEYS
        assert "AGENTIC_DISCOVERY_ENABLED" not in pilots_api.env_io.SECRET_KEYS


class TestCORSLanTailscale:
    """LAN/Tailscale origins are allowed via api.cors.LAN_TAILSCALE_ORIGIN_REGEX
    (additive to the explicit CORS_ALLOWED_ORIGINS list), scoped to the Pilots
    PWA dev server's port (5173, per webapp/vite.config.ts's
    ``server: { host: true, port: 5173 }``)."""

    def test_lan_origin_is_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://192.168.1.42:5173"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://192.168.1.42:5173"

    def test_tailscale_range_origin_is_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://100.101.102.5:5173"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://100.101.102.5:5173"

    def test_lan_origin_wrong_port_not_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://192.168.1.42:5174"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") != "http://192.168.1.42:5174"

    def test_public_ip_not_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://8.8.8.8:5173"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") != "http://8.8.8.8:5173"


# ===========================================================================
# Prompt Registry (webapp parity gap G4) — GET /prompts, GET /prompts/{id},
# PUT /prompts/pin. Appended at the end of the file per this repo's
# multi-agent collision protocol (other agents append their own new test
# classes elsewhere in this same file concurrently on separate branches).
# ===========================================================================


def _reset_prompt_registry_singleton():
    from prompt_registry.registry import reset_registry
    reset_registry()


class TestPromptsRead:
    """GET /prompts and GET /prompts/{id} — fail-open reads over
    pilots.prompt_registry (which wraps prompt_registry.registry.get_registry()).
    The committed prompt_registry/baseline/*.md files guarantee at least one
    real prompt ID resolves in every test environment, with zero mocking."""

    def setup_method(self):
        _reset_prompt_registry_singleton()

    def teardown_method(self):
        _reset_prompt_registry_singleton()

    def test_list_shape(self):
        resp = client.get("/prompts")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("enabled", "prompts", "reason", "writable", "note"):
            assert key in body
        assert len(body["prompts"]) > 0
        row = body["prompts"][0]
        for key in ("id", "resolved_version", "source", "pinned_version", "cached_version_count"):
            assert key in row

    def test_writable_tracks_the_flag(self):
        with mock.patch.object(settings, "PROMPT_REGISTRY_WRITES_ENABLED", True):
            on = client.get("/prompts").json()
        with mock.patch.object(settings, "PROMPT_REGISTRY_WRITES_ENABLED", False):
            off = client.get("/prompts").json()
        assert on["writable"] is True
        assert off["writable"] is False

    def test_baseline_id_resolves_from_committed_baseline(self):
        resp = client.get("/prompts")
        row = next(r for r in resp.json()["prompts"] if r["id"] == "gravity.step_01")
        assert row["source"] == "baseline"
        assert row["resolved_version"] == "baseline"
        assert row["pinned_version"] is None

    def test_pinned_prompt_reflects_pin_source_and_value(self):
        with mock.patch.object(settings, "PROMPT_REGISTRY_PINS", {"gravity.step_01": "9.9.9"}):
            _reset_prompt_registry_singleton()
            resp = client.get("/prompts")
        row = next(r for r in resp.json()["prompts"] if r["id"] == "gravity.step_01")
        assert row["pinned_version"] == "9.9.9"
        assert row["source"] == "pin"
        assert row["resolved_version"] == "9.9.9"

    def test_fail_open_read_with_no_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/prompts")
        assert resp.status_code == 200

    def test_401_on_wrong_read_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get("/prompts", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_never_500_when_registry_unconstructible(self):
        with mock.patch.object(
            pilots_api.prompt_registry_reader, "_get_registry_or_none", return_value=None
        ):
            resp = client.get("/prompts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["prompts"] == []
        assert body["reason"] is not None

    def test_get_prompt_resolved_body(self):
        resp = client.get("/prompts/gravity.step_01")
        assert resp.status_code == 200
        body = resp.json()
        assert body["found"] is True
        assert body["body"]
        assert body["source"] == "baseline"
        assert body["version"] == "baseline"
        assert body["has_baseline"] is True
        assert body["cached_versions"] == []

    def test_get_prompt_reports_cached_versions_and_has_baseline(self):
        """cached_versions/has_baseline are populated on EVERY call — a
        diff-version picker needs the full set up front, not just whichever
        single version this particular request happened to resolve."""
        resp = client.get("/prompts/gravity.step_01", params={"version": "baseline"})
        body = resp.json()
        assert "cached_versions" in body
        assert "has_baseline" in body
        assert body["has_baseline"] is True

    def test_get_prompt_specific_version_baseline_keyword(self):
        resp = client.get("/prompts/gravity.step_01", params={"version": "baseline"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["found"] is True
        assert body["version"] == "baseline"
        # A specific-version lookup does not re-derive provenance.
        assert body["source"] is None

    def test_get_prompt_unknown_id_is_honest_not_found_never_404(self):
        resp = client.get("/prompts/totally.unknown.id")
        assert resp.status_code == 200
        body = resp.json()
        assert body["found"] is False
        assert body["body"] is None
        assert body["reason"] is not None

    def test_get_prompt_unknown_version_is_honest_not_found(self):
        resp = client.get(
            "/prompts/gravity.step_01", params={"version": "9.9.9-does-not-exist"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["found"] is False
        assert body["reason"] is not None

    def test_get_prompt_fail_open_read_with_no_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/prompts/gravity.step_01")
        assert resp.status_code == 200

    def test_get_prompt_401_on_wrong_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get(
                "/prompts/gravity.step_01", headers={"Authorization": "Bearer wrong"}
            )
        assert resp.status_code == 401


class TestPromptsPinWrite:
    """PUT /prompts/pin — fail-closed command token (FOLLOW_API_TOKEN) STACKED
    with the dedicated PROMPT_REGISTRY_WRITES_ENABLED master flag, mirroring
    PUT /strategy/modules's auth tier exactly."""

    def _auth(self):
        return {"Authorization": f"Bearer {_CMD_TOKEN}"}

    def setup_method(self):
        _reset_prompt_registry_singleton()

    def teardown_method(self):
        _reset_prompt_registry_singleton()

    def test_fails_closed_when_prompt_registry_writes_disabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "PROMPT_REGISTRY_WRITES_ENABLED", False):
                resp = client.put(
                    "/prompts/pin",
                    json={"prompt_id": "gravity.step_01", "version": "baseline"},
                    headers=self._auth(),
                )
        assert resp.status_code == 403

    def test_fails_closed_when_follow_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            with mock.patch.object(settings, "PROMPT_REGISTRY_WRITES_ENABLED", True):
                resp = client.put(
                    "/prompts/pin",
                    json={"prompt_id": "gravity.step_01", "version": "baseline"},
                    headers={"Authorization": "Bearer anything"},
                )
        assert resp.status_code == 403

    def test_401_on_wrong_command_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "PROMPT_REGISTRY_WRITES_ENABLED", True):
                resp = client.put(
                    "/prompts/pin",
                    json={"prompt_id": "gravity.step_01", "version": "baseline"},
                    headers={"Authorization": "Bearer WRONG"},
                )
        assert resp.status_code == 401

    def test_happy_path_pin_writes_and_echoes(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "PROMPT_REGISTRY_WRITES_ENABLED", True):
                with mock.patch.object(settings, "PROMPT_REGISTRY_PINS", {}):
                    with mock.patch.object(pilots_api.env_io, "write_setting") as w:
                        resp = client.put(
                            "/prompts/pin",
                            json={"prompt_id": "gravity.step_01", "version": "baseline"},
                            headers=self._auth(),
                        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["prompt_id"] == "gravity.step_01"
        assert body["version"] == "baseline"
        assert body["pins"] == {"gravity.step_01": "baseline"}
        assert body["applies"] == "next_daemon_restart"
        # Writer called exactly once, with the full expected payload.
        w.assert_called_once_with("PROMPT_REGISTRY_PINS", {"gravity.step_01": "baseline"})

    def test_happy_path_merges_onto_existing_pins(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "PROMPT_REGISTRY_WRITES_ENABLED", True):
                with mock.patch.object(
                    settings, "PROMPT_REGISTRY_PINS", {"other.prompt": "1.0.0"}
                ):
                    with mock.patch.object(pilots_api.env_io, "write_setting") as w:
                        resp = client.put(
                            "/prompts/pin",
                            json={"prompt_id": "gravity.step_01", "version": "baseline"},
                            headers=self._auth(),
                        )
        assert resp.status_code == 200
        expected = {"other.prompt": "1.0.0", "gravity.step_01": "baseline"}
        assert resp.json()["pins"] == expected
        w.assert_called_once_with("PROMPT_REGISTRY_PINS", expected)

    def test_clearing_pin_omits_it_from_response_and_write(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "PROMPT_REGISTRY_WRITES_ENABLED", True):
                with mock.patch.object(
                    settings, "PROMPT_REGISTRY_PINS", {"gravity.step_01": "1.0.0"}
                ):
                    with mock.patch.object(pilots_api.env_io, "write_setting") as w:
                        resp = client.put(
                            "/prompts/pin",
                            json={"prompt_id": "gravity.step_01"},
                            headers=self._auth(),
                        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] is None
        assert body["pins"] == {}
        w.assert_called_once_with("PROMPT_REGISTRY_PINS", {})

    def test_version_not_found_422_stable_tag(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "PROMPT_REGISTRY_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "write_setting") as w:
                    resp = client.put(
                        "/prompts/pin",
                        json={
                            "prompt_id": "gravity.step_01",
                            "version": "9.9.9-nonexistent",
                        },
                        headers=self._auth(),
                    )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "version_not_found"
        w.assert_not_called()  # never writes on a validation failure

    def test_empty_prompt_id_422_stable_tag(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "PROMPT_REGISTRY_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "write_setting") as w:
                    resp = client.put(
                        "/prompts/pin",
                        json={"prompt_id": "   ", "version": "baseline"},
                        headers=self._auth(),
                    )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "invalid_prompt_id"
        w.assert_not_called()

    def test_write_never_logs_token(self, caplog):
        with caplog.at_level("DEBUG"):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(settings, "PROMPT_REGISTRY_WRITES_ENABLED", True):
                    with mock.patch.object(pilots_api.env_io, "write_setting"):
                        client.put(
                            "/prompts/pin",
                            json={"prompt_id": "gravity.step_01", "version": "baseline"},
                            headers=self._auth(),
                        )
        assert _CMD_TOKEN not in caplog.text


class TestPromptRegistryWritesInvariants:
    def test_prompt_registry_writes_enabled_is_gui_writable(self):
        """2026-08-08 (PR #630 audit): reclassified into ALLOWED_KEYS by
        explicit operator decision -- not secret, so this no longer needs to
        be hand-set-only. Still a settings_keysets.DANGEROUS_KEYS member
        (typed confirmation required on write); the endpoint remains
        independently gated by FOLLOW_API_TOKEN regardless."""
        assert "PROMPT_REGISTRY_WRITES_ENABLED" in pilots_api.env_io.ALLOWED_KEYS
        assert "PROMPT_REGISTRY_WRITES_ENABLED" not in pilots_api.env_io.SECRET_KEYS
        assert "PROMPT_REGISTRY_WRITES_ENABLED" not in pilots_api.env_io.EXCLUDED_FROM_GUI

    def test_prompt_registry_pins_key_stays_allowlisted(self):
        """The TARGET key this endpoint writes has been GUI-writable via the
        Streamlit Prompt Registry tab for a long time (gui/panels/prompt_registry.py)
        — this new write path must not require (or accidentally break) that."""
        assert "PROMPT_REGISTRY_PINS" in pilots_api.env_io.ALLOWED_KEYS


# ===========================================================================
# POST /rag/query — agents/rag_orchestrator.py's first production caller
# ===========================================================================


class TestRagQuery:
    def _auth(self):
        return {"Authorization": f"Bearer {_CMD_TOKEN}"}

    def test_fails_closed_when_rag_query_disabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RAG_QUERY_API_ENABLED", False):
                resp = client.post("/rag/query", json={"query": "any risks?"}, headers=self._auth())
        assert resp.status_code == 403
        assert "RAG_QUERY_API_ENABLED" in resp.json()["detail"]

    def test_fails_closed_when_follow_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            with mock.patch.object(settings, "RAG_QUERY_API_ENABLED", True):
                resp = client.post(
                    "/rag/query", json={"query": "any risks?"},
                    headers={"Authorization": "Bearer anything"},
                )
        assert resp.status_code == 403

    def test_fails_closed_with_wrong_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RAG_QUERY_API_ENABLED", True):
                resp = client.post(
                    "/rag/query", json={"query": "any risks?"},
                    headers={"Authorization": "Bearer WRONG"},
                )
        assert resp.status_code == 401

    def test_empty_query_rejected_422(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RAG_QUERY_API_ENABLED", True):
                resp = client.post("/rag/query", json={"query": "   "}, headers=self._auth())
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "empty_query"

    def test_happy_path_returns_analysis(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RAG_QUERY_API_ENABLED", True):
                with mock.patch.object(pilots_api, "run_rag_query", return_value="Real analysis text.") as m:
                    resp = client.post(
                        "/rag/query", json={"query": "What are my portfolio risks?"},
                        headers=self._auth(),
                    )
        assert resp.status_code == 200
        m.assert_called_once_with("What are my portfolio risks?")
        body = resp.json()
        assert body["analysis"] == "Real analysis text."
        assert body["available"] is True
        assert body["query"] == "What are my portfolio risks?"

    def test_degraded_string_passed_through_as_available(self):
        """A "(...)"-style degraded-mode message (e.g. langgraph missing) is
        still honest, human-readable status text -- not a failure."""
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RAG_QUERY_API_ENABLED", True):
                with mock.patch.object(
                    pilots_api, "run_rag_query",
                    return_value="(RAG unavailable — langgraph not installed)",
                ):
                    resp = client.post("/rag/query", json={"query": "risks?"}, headers=self._auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert "langgraph not installed" in body["analysis"]

    def test_empty_string_result_reports_unavailable_not_fabricated(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RAG_QUERY_API_ENABLED", True):
                with mock.patch.object(pilots_api, "run_rag_query", return_value=""):
                    resp = client.post("/rag/query", json={"query": "risks?"}, headers=self._auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["analysis"] is None

    def test_write_never_logs_token(self, caplog):
        with caplog.at_level("DEBUG"):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(settings, "RAG_QUERY_API_ENABLED", True):
                    with mock.patch.object(pilots_api, "run_rag_query", return_value="ok"):
                        client.post("/rag/query", json={"query": "risks?"}, headers=self._auth())
        assert _CMD_TOKEN not in caplog.text


class TestRagQueryInvariants:
    def test_rag_query_api_enabled_is_gui_writable(self):
        """2026-08-08 (PR #630 audit): reclassified into ALLOWED_KEYS by
        explicit operator decision, same treatment as AI_GENERATION_API_ENABLED
        -- not secret, so this no longer needs to be hand-set-only. Still a
        settings_keysets.DANGEROUS_KEYS member (typed confirmation required
        on write); the endpoint remains independently gated by
        require_command_token regardless."""
        assert "RAG_QUERY_API_ENABLED" in pilots_api.env_io.ALLOWED_KEYS
        assert "RAG_QUERY_API_ENABLED" not in pilots_api.env_io.SECRET_KEYS
        assert "RAG_QUERY_API_ENABLED" not in pilots_api.env_io.EXCLUDED_FROM_GUI


# ---------------------------------------------------------------------------
# RLHF Calibration Review Queue (GET /rlhf/summary, POST /rlhf/proposals,
# POST /rlhf/proposals/{id}/review, POST /rlhf/export-sft) — every proposal is
# hypothetical/paper-only (rlhf_calibration_store.py), so the write endpoints
# are gated by require_command_token + require_rlhf_calibration_enabled
# (RLHF_CALIBRATION_ENABLED, default True). Every test isolates the store's
# SQLite backend to a tmp_path file via settings.DATABASE_URL so nothing here
# ever touches the real repo-root quant_platform.db.
# ---------------------------------------------------------------------------


def _rlhf_db_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'rlhf_test.db'}"


class TestRlhfSummaryRead:
    def test_cold_start_shape(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "DATABASE_URL", _rlhf_db_url(tmp_path)):
                resp = client.get("/rlhf/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"proposals", "kpis", "writable", "reason"}
        assert body["proposals"] == []
        assert body["kpis"]["pending_count"] == 0
        assert body["kpis"]["average_human_rating"] is None
        assert body["reason"] is not None  # honest "no pending proposals" / missing table

    def test_fail_open_read_with_no_token(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "DATABASE_URL", _rlhf_db_url(tmp_path)):
                resp = client.get("/rlhf/summary")
        assert resp.status_code == 200

    def test_401_on_wrong_read_token(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            with mock.patch.object(settings, "DATABASE_URL", _rlhf_db_url(tmp_path)):
                resp = client.get("/rlhf/summary", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_writable_tracks_the_flag(self, tmp_path):
        db_url = _rlhf_db_url(tmp_path)
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "DATABASE_URL", db_url):
                with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                    on = client.get("/rlhf/summary").json()
                with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", False):
                    off = client.get("/rlhf/summary").json()
        assert on["writable"] is True
        assert off["writable"] is False

    def test_real_data_shape(self, tmp_path):
        db_url = _rlhf_db_url(tmp_path)
        with mock.patch.object(settings, "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED", False):
            RlhfCalibrationStore(db_url=db_url).create_proposal(
                symbol="AAPL",
                action="BUY",
                rationale="RSI oversold with bullish sentiment shift.",
                confidence=0.65,
            )
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "DATABASE_URL", db_url):
                resp = client.get("/rlhf/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["proposals"]) == 1
        assert body["proposals"][0]["symbol"] == "AAPL"
        assert body["proposals"][0]["status"] == "pending"
        assert body["kpis"]["pending_count"] == 1
        assert body["reason"] is None

    def test_limit_out_of_range_is_422(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "DATABASE_URL", _rlhf_db_url(tmp_path)):
                too_low = client.get("/rlhf/summary", params={"limit": 0})
                too_high = client.get("/rlhf/summary", params={"limit": 201})
        assert too_low.status_code == 422
        assert too_high.status_code == 422

    def test_never_500_on_corrupt_db_file(self, tmp_path):
        db_file = tmp_path / "corrupt.db"
        db_file.write_bytes(b"not a sqlite file at all")
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "DATABASE_URL", f"sqlite:///{db_file}"):
                resp = client.get("/rlhf/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["proposals"] == []
        assert body["kpis"]["pending_count"] == 0


class TestRlhfProposalsCreate:
    """POST /rlhf/proposals — exists mainly for API completeness/testability
    (see require_rlhf_calibration_enabled's docstring): the webapp itself only
    ever calls the review/export endpoints, a sibling MCP tool creates real
    proposals directly against the store. Used here to build fixture data for
    the review/export test classes below through the real API."""

    def _auth(self):
        return {"Authorization": f"Bearer {_CMD_TOKEN}"}

    def _payload(self, **overrides):
        payload = dict(
            symbol="AAPL",
            action="BUY",
            rationale="RSI oversold with bullish sentiment shift.",
            confidence=0.55,
            price=180.5,
        )
        payload.update(overrides)
        return payload

    def test_fails_closed_when_rlhf_calibration_disabled(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", False):
                resp = client.post("/rlhf/proposals", json=self._payload(), headers=self._auth())
        assert resp.status_code == 403

    def test_fails_closed_when_follow_token_unset(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                resp = client.post(
                    "/rlhf/proposals", json=self._payload(),
                    headers={"Authorization": "Bearer anything"},
                )
        assert resp.status_code == 403

    def test_401_on_wrong_command_token(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                resp = client.post(
                    "/rlhf/proposals", json=self._payload(),
                    headers={"Authorization": "Bearer WRONG"},
                )
        assert resp.status_code == 401

    def test_happy_path_with_price_supplied(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", _rlhf_db_url(tmp_path)):
                    resp = client.post(
                        "/rlhf/proposals", json=self._payload(price=123.45), headers=self._auth(),
                    )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["action"] == "BUY"
        assert body["price"] == pytest.approx(123.45)
        assert body["quote_source"] == "caller_supplied"
        assert body["status"] == "pending"
        assert "id" in body

    def test_happy_path_omits_price_live_quote_succeeds(self, tmp_path):
        fake_quote = mock.Mock(price=201.11)
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", _rlhf_db_url(tmp_path)):
                    with mock.patch.object(pilots_api, "get_provider") as gp:
                        gp.return_value.get_latest_quote.return_value = fake_quote
                        resp = client.post(
                            "/rlhf/proposals",
                            json=self._payload(price=None),
                            headers=self._auth(),
                        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["price"] == pytest.approx(201.11)
        assert body["quote_source"] == "live"
        gp.return_value.get_latest_quote.assert_called_once_with("AAPL")

    def test_live_quote_market_data_error_degrades_honestly(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", _rlhf_db_url(tmp_path)):
                    with mock.patch.object(pilots_api, "get_provider") as gp:
                        gp.return_value.get_latest_quote.side_effect = pilots_api.MarketDataError(
                            "no quote available"
                        )
                        resp = client.post(
                            "/rlhf/proposals",
                            json=self._payload(price=None),
                            headers=self._auth(),
                        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["price"] is None
        assert body["quote_source"] == "unavailable"

    def test_hold_action_skips_quote_fetch_even_without_price(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", _rlhf_db_url(tmp_path)):
                    with mock.patch.object(pilots_api, "get_provider") as gp:
                        resp = client.post(
                            "/rlhf/proposals",
                            json=self._payload(action="HOLD", price=None),
                            headers=self._auth(),
                        )
        assert resp.status_code == 200
        assert resp.json()["quote_source"] == "unavailable"
        gp.assert_not_called()

    def test_invalid_action_returns_422(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", _rlhf_db_url(tmp_path)):
                    resp = client.post(
                        "/rlhf/proposals",
                        json=self._payload(action="SHORT", price=100.0),
                        headers=self._auth(),
                    )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "invalid_action"

    def test_invalid_confidence_returns_422(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", _rlhf_db_url(tmp_path)):
                    resp = client.post(
                        "/rlhf/proposals",
                        json=self._payload(confidence=1.5, price=100.0),
                        headers=self._auth(),
                    )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "invalid_confidence"

    def test_write_never_logs_token(self, tmp_path, caplog):
        with caplog.at_level("DEBUG"):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                    with mock.patch.object(settings, "DATABASE_URL", _rlhf_db_url(tmp_path)):
                        client.post("/rlhf/proposals", json=self._payload(), headers=self._auth())
        assert _CMD_TOKEN not in caplog.text


class TestRlhfProposalReview:
    def _auth(self):
        return {"Authorization": f"Bearer {_CMD_TOKEN}"}

    def _create(self, db_url, **overrides):
        payload = dict(
            symbol="MSFT",
            action="BUY",
            rationale="Momentum breakout above 50DMA.",
            confidence=0.6,
            price=310.0,
        )
        payload.update(overrides)
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED", False):
                    with mock.patch.object(settings, "DATABASE_URL", db_url):
                        resp = client.post("/rlhf/proposals", json=payload, headers=self._auth())
        assert resp.status_code == 200
        return resp.json()

    def test_404_not_found(self, tmp_path):
        db_url = _rlhf_db_url(tmp_path)
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", db_url):
                    resp = client.post(
                        "/rlhf/proposals/999999/review",
                        json={"human_rating": 4},
                        headers=self._auth(),
                    )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "not_found"

    def test_409_already_reviewed(self, tmp_path):
        db_url = _rlhf_db_url(tmp_path)
        row = self._create(db_url)
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", db_url):
                    first = client.post(
                        f"/rlhf/proposals/{row['id']}/review",
                        json={"human_rating": 3}, headers=self._auth(),
                    )
                    second = client.post(
                        f"/rlhf/proposals/{row['id']}/review",
                        json={"human_rating": 4}, headers=self._auth(),
                    )
        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["detail"] == "already_reviewed"

    def test_422_invalid_rating(self, tmp_path):
        db_url = _rlhf_db_url(tmp_path)
        row = self._create(db_url)
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", db_url):
                    resp = client.post(
                        f"/rlhf/proposals/{row['id']}/review",
                        json={"human_rating": 9}, headers=self._auth(),
                    )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "invalid_rating"

    def test_happy_path(self, tmp_path):
        db_url = _rlhf_db_url(tmp_path)
        row = self._create(db_url)
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "RLHF_CALIBRATION_AUTO_EXPORT_SFT_ENABLED", False):
                    with mock.patch.object(settings, "DATABASE_URL", db_url):
                        resp = client.post(
                            f"/rlhf/proposals/{row['id']}/review",
                            json={
                                "human_rating": 4,
                                "human_correction": "Good call, would size smaller.",
                            },
                            headers=self._auth(),
                        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "reviewed"
        assert body["human_rating"] == 4
        assert body["human_correction"] == "Good call, would size smaller."
        assert body["sft_exported"] is False

    def test_auto_export_on_five_star_when_enabled(self, tmp_path):
        db_url = _rlhf_db_url(tmp_path)
        row = self._create(db_url)
        out_dir = tmp_path / "output_rlhf"
        out_dir.mkdir()
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "RLHF_CALIBRATION_AUTO_EXPORT_SFT_ENABLED", True):
                    with mock.patch.object(settings, "DATABASE_URL", db_url):
                        with mock.patch.object(settings, "OUTPUT_DIR", out_dir):
                            resp = client.post(
                                f"/rlhf/proposals/{row['id']}/review",
                                json={"human_rating": 5},
                                headers=self._auth(),
                            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["sft_exported"] is True
        sft_file = out_dir / "rlhf_sft_dataset.jsonl"
        assert sft_file.exists()
        lines = sft_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["messages"][-1]["role"] == "assistant"
        assert "MSFT" in record["messages"][1]["content"]

    def test_no_auto_export_when_flag_off(self, tmp_path):
        db_url = _rlhf_db_url(tmp_path)
        row = self._create(db_url)
        out_dir = tmp_path / "output_rlhf_off"
        out_dir.mkdir()
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "RLHF_CALIBRATION_AUTO_EXPORT_SFT_ENABLED", False):
                    with mock.patch.object(settings, "DATABASE_URL", db_url):
                        with mock.patch.object(settings, "OUTPUT_DIR", out_dir):
                            resp = client.post(
                                f"/rlhf/proposals/{row['id']}/review",
                                json={"human_rating": 5},
                                headers=self._auth(),
                            )
        assert resp.status_code == 200
        assert resp.json()["sft_exported"] is False
        assert not (out_dir / "rlhf_sft_dataset.jsonl").exists()

    def test_write_never_logs_token(self, tmp_path, caplog):
        db_url = _rlhf_db_url(tmp_path)
        row = self._create(db_url)
        with caplog.at_level("DEBUG"):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                    with mock.patch.object(settings, "DATABASE_URL", db_url):
                        client.post(
                            f"/rlhf/proposals/{row['id']}/review",
                            json={"human_rating": 3}, headers=self._auth(),
                        )
        assert _CMD_TOKEN not in caplog.text


class TestRlhfExportSft:
    def _auth(self):
        return {"Authorization": f"Bearer {_CMD_TOKEN}"}

    def _create_and_review(self, db_url, rating, **overrides):
        payload = dict(
            symbol="GOOG",
            action="SELL",
            rationale="Overextended vs 50DMA.",
            confidence=0.7,
            price=150.0,
        )
        payload.update(overrides)
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED", False):
                    with mock.patch.object(settings, "RLHF_CALIBRATION_AUTO_EXPORT_SFT_ENABLED", False):
                        with mock.patch.object(settings, "DATABASE_URL", db_url):
                            created = client.post(
                                "/rlhf/proposals", json=payload, headers=self._auth()
                            ).json()
                            reviewed = client.post(
                                f"/rlhf/proposals/{created['id']}/review",
                                json={"human_rating": rating},
                                headers=self._auth(),
                            ).json()
        return reviewed

    def test_happy_path_exports_five_star_unexported_row(self, tmp_path):
        db_url = _rlhf_db_url(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        row = self._create_and_review(db_url, rating=5)
        assert row["sft_exported"] is False

        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", db_url):
                    with mock.patch.object(settings, "OUTPUT_DIR", out_dir):
                        resp = client.post("/rlhf/export-sft", headers=self._auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["exported_count"] == 1
        assert body["proposal_ids"] == [row["id"]]
        assert body["file"] == str(out_dir / "rlhf_sft_dataset.jsonl")
        assert (out_dir / "rlhf_sft_dataset.jsonl").exists()

        # A second call finds nothing left to export -- already marked.
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", db_url):
                    with mock.patch.object(settings, "OUTPUT_DIR", out_dir):
                        again = client.post("/rlhf/export-sft", headers=self._auth())
        assert again.json()["exported_count"] == 0

    def test_no_op_when_none_qualify(self, tmp_path):
        db_url = _rlhf_db_url(tmp_path)
        out_dir = tmp_path / "out2"
        out_dir.mkdir()
        self._create_and_review(db_url, rating=3)  # not 5-star -- doesn't qualify

        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", db_url):
                    with mock.patch.object(settings, "OUTPUT_DIR", out_dir):
                        resp = client.post("/rlhf/export-sft", headers=self._auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["exported_count"] == 0
        assert body["proposal_ids"] == []
        assert not (out_dir / "rlhf_sft_dataset.jsonl").exists()

    def test_fails_closed_when_rlhf_calibration_disabled(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", False):
                resp = client.post("/rlhf/export-sft", headers=self._auth())
        assert resp.status_code == 403

    def test_401_on_wrong_command_token(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "RLHF_CALIBRATION_ENABLED", True):
                resp = client.post("/rlhf/export-sft", headers={"Authorization": "Bearer WRONG"})
        assert resp.status_code == 401


class TestRlhfCalibrationInvariants:
    def test_rlhf_calibration_enabled_is_gui_writable_but_never_secret(self):
        """Mirrors test_reclassified_flags_are_now_gui_writable's assertions
        (tests/test_gui_env_io.py): created directly in ALLOWED_KEYS per
        AGENTS.md's 2026-08-03 convention, not hand-set-only — the endpoint
        stays independently gated by FOLLOW_API_TOKEN regardless."""
        assert "RLHF_CALIBRATION_ENABLED" in pilots_api.env_io.ALLOWED_KEYS
        assert "RLHF_CALIBRATION_ENABLED" not in pilots_api.env_io.SECRET_KEYS


# ---------------------------------------------------------------------------
# Live trade approval gate — GET /pilots/execution/pending,
# POST /pilots/execution/{token}/{approve,reject}. Reads are fail-open
# (require_read_token alone); writes are fail-closed behind
# require_command_token + require_live_trade_approval_enabled
# (LIVE_TRADE_APPROVAL_ENABLED, default False). Every test isolates the
# store's SQLite backend to a tmp_path file via settings.DATABASE_URL so
# nothing here ever touches the real repo-root quant_platform.db.
# ---------------------------------------------------------------------------


def _live_trade_db_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'live_trade_test.db'}"


def _create_live_trade_proposal(db_url, **overrides):
    from execution.live_trade_proposals_store import LiveTradeProposalStore

    payload = dict(symbol="AAPL", side="buy", qty=10, order_type="market")
    payload.update(overrides)
    store = LiveTradeProposalStore(db_url=db_url)
    return store.create_proposal(**payload)


class TestLiveTradeExecutionPending:
    def test_fail_open_read_with_no_token(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "DATABASE_URL", _live_trade_db_url(tmp_path)):
                resp = client.get("/pilots/execution/pending")
        assert resp.status_code == 200
        assert resp.json() == {"proposals": []}

    def test_401_on_wrong_read_token(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            with mock.patch.object(settings, "DATABASE_URL", _live_trade_db_url(tmp_path)):
                resp = client.get(
                    "/pilots/execution/pending", headers={"Authorization": "Bearer wrong"}
                )
        assert resp.status_code == 401

    def test_no_gate_on_read_even_when_approval_disabled(self, tmp_path):
        """The read endpoint has no write-flag gate — it must still work
        while LIVE_TRADE_APPROVAL_ENABLED is False (the default)."""
        db_url = _live_trade_db_url(tmp_path)
        _create_live_trade_proposal(db_url, symbol="MSFT", side="sell", qty=5)
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "LIVE_TRADE_APPROVAL_ENABLED", False):
                with mock.patch.object(settings, "DATABASE_URL", db_url):
                    resp = client.get("/pilots/execution/pending")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["proposals"]) == 1
        assert body["proposals"][0]["symbol"] == "MSFT"
        assert body["proposals"][0]["status"] == "pending_approval"

    def test_never_500_on_corrupt_db_file(self, tmp_path):
        db_file = tmp_path / "corrupt.db"
        db_file.write_bytes(b"not a sqlite file at all")
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "DATABASE_URL", f"sqlite:///{db_file}"):
                resp = client.get("/pilots/execution/pending")
        assert resp.status_code == 200
        assert resp.json() == {"proposals": []}


class TestLiveTradeExecutionApprove:
    def _auth(self):
        return {"Authorization": f"Bearer {_CMD_TOKEN}"}

    def test_401_on_wrong_command_token(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LIVE_TRADE_APPROVAL_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", _live_trade_db_url(tmp_path)):
                    resp = client.post(
                        "/pilots/execution/deadbeef/approve",
                        headers={"Authorization": "Bearer WRONG"},
                    )
        assert resp.status_code == 401

    def test_fails_closed_when_approval_disabled(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LIVE_TRADE_APPROVAL_ENABLED", False):
                with mock.patch.object(settings, "DATABASE_URL", _live_trade_db_url(tmp_path)):
                    resp = client.post(
                        "/pilots/execution/deadbeef/approve", headers=self._auth()
                    )
        assert resp.status_code == 403

    def test_404_not_found(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LIVE_TRADE_APPROVAL_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", _live_trade_db_url(tmp_path)):
                    resp = client.post(
                        "/pilots/execution/does-not-exist/approve", headers=self._auth()
                    )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "not_found"

    def test_happy_path(self, tmp_path):
        db_url = _live_trade_db_url(tmp_path)
        token = _create_live_trade_proposal(db_url, symbol="NVDA", side="buy", qty=3)
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LIVE_TRADE_APPROVAL_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", db_url):
                    resp = client.post(
                        f"/pilots/execution/{token}/approve", headers=self._auth()
                    )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"] == token
        assert body["symbol"] == "NVDA"
        assert body["status"] == "approved"
        assert body["approved_by"] == "operator"
        assert body["approved_at"] is not None

    def test_409_already_decided(self, tmp_path):
        db_url = _live_trade_db_url(tmp_path)
        token = _create_live_trade_proposal(db_url, symbol="TSLA", side="buy", qty=1)
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LIVE_TRADE_APPROVAL_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", db_url):
                    first = client.post(
                        f"/pilots/execution/{token}/approve", headers=self._auth()
                    )
                    second = client.post(
                        f"/pilots/execution/{token}/approve", headers=self._auth()
                    )
        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["detail"] == "already_decided"


class TestLiveTradeExecutionReject:
    def _auth(self):
        return {"Authorization": f"Bearer {_CMD_TOKEN}"}

    def test_401_on_wrong_command_token(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LIVE_TRADE_APPROVAL_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", _live_trade_db_url(tmp_path)):
                    resp = client.post(
                        "/pilots/execution/deadbeef/reject",
                        headers={"Authorization": "Bearer WRONG"},
                    )
        assert resp.status_code == 401

    def test_fails_closed_when_approval_disabled(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LIVE_TRADE_APPROVAL_ENABLED", False):
                with mock.patch.object(settings, "DATABASE_URL", _live_trade_db_url(tmp_path)):
                    resp = client.post(
                        "/pilots/execution/deadbeef/reject", headers=self._auth()
                    )
        assert resp.status_code == 403

    def test_404_not_found(self, tmp_path):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LIVE_TRADE_APPROVAL_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", _live_trade_db_url(tmp_path)):
                    resp = client.post(
                        "/pilots/execution/does-not-exist/reject", headers=self._auth()
                    )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "not_found"

    def test_happy_path(self, tmp_path):
        db_url = _live_trade_db_url(tmp_path)
        token = _create_live_trade_proposal(db_url, symbol="AMD", side="sell", qty=7)
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LIVE_TRADE_APPROVAL_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", db_url):
                    resp = client.post(
                        f"/pilots/execution/{token}/reject", headers=self._auth()
                    )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"] == token
        assert body["symbol"] == "AMD"
        assert body["status"] == "rejected"

    def test_409_already_decided(self, tmp_path):
        db_url = _live_trade_db_url(tmp_path)
        token = _create_live_trade_proposal(db_url, symbol="AMZN", side="buy", qty=2)
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LIVE_TRADE_APPROVAL_ENABLED", True):
                with mock.patch.object(settings, "DATABASE_URL", db_url):
                    first = client.post(
                        f"/pilots/execution/{token}/reject", headers=self._auth()
                    )
                    second = client.post(
                        f"/pilots/execution/{token}/reject", headers=self._auth()
                    )
        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["detail"] == "already_decided"


class TestHrpCvarOptimize:
    """POST /pilots/portfolio/optimize/hrp-cvar previously used
    np.random.randn as its `returns` input and hardcoded the response's
    `cvar_95` to the same 0.05 ceiling it was constrained to (audit finding
    F2). Fixed to fetch real historical bars via HistoricalStore.get_bars
    and compute the real CVaR of the optimized portfolio's actual returns.
    """

    class _Store:
        def __init__(self, series_by_symbol):
            self._series = series_by_symbol

        def get_bars(self, symbol, lookback_days=504):
            closes = self._series.get(symbol)
            if closes is None:
                return pd.DataFrame()
            idx = pd.bdate_range(end="2026-08-01", periods=len(closes))
            return pd.DataFrame({"Close": closes}, index=idx)

    @staticmethod
    def _synthetic_closes(seed, n=120, start=100.0):
        rng = np.random.default_rng(seed)
        rets = rng.normal(loc=0.0003, scale=0.01, size=n)
        return list(start * np.cumprod(1 + rets))

    def test_cvar_varies_across_requests_and_is_positive(self):
        series_a = {
            "AAPL": self._synthetic_closes(1, start=150.0),
            "MSFT": self._synthetic_closes(2, start=300.0),
        }
        series_b = {
            "AAPL": self._synthetic_closes(3, start=150.0),
            "MSFT": self._synthetic_closes(4, start=300.0),
        }

        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=self._Store(series_a)):
                resp_a = client.post(
                    "/pilots/portfolio/optimize/hrp-cvar",
                    json={"symbols": ["AAPL", "MSFT"]},
                )
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=self._Store(series_b)):
                resp_b = client.post(
                    "/pilots/portfolio/optimize/hrp-cvar",
                    json={"symbols": ["AAPL", "MSFT"]},
                )

        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        cvar_a = resp_a.json()["cvar_95"]
        cvar_b = resp_b.json()["cvar_95"]
        assert cvar_a > 0.0
        assert cvar_b > 0.0
        # No longer the hardcoded 0.05 placeholder, and genuinely differs
        # across two different real (here: synthetic-but-varied) return series.
        assert cvar_a != 0.05
        assert cvar_b != 0.05
        assert cvar_a != cvar_b

    def test_insufficient_history_returns_honest_422(self):
        store = self._Store({"AAPL": [], "MSFT": self._synthetic_closes(5, start=300.0)})
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
                resp = client.post(
                    "/pilots/portfolio/optimize/hrp-cvar",
                    json={"symbols": ["AAPL", "MSFT"]},
                )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "insufficient_history"
        assert "AAPL" in resp.json()["detail"]["symbols_missing"]

    def test_too_few_overlapping_days_returns_honest_422(self):
        store = self._Store({
            "AAPL": self._synthetic_closes(6, n=10, start=150.0),
            "MSFT": self._synthetic_closes(7, n=10, start=300.0),
        })
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
                resp = client.post(
                    "/pilots/portfolio/optimize/hrp-cvar",
                    json={"symbols": ["AAPL", "MSFT"]},
                )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "insufficient_history"

    def test_turnover_regularization_and_telemetry_fields(self):
        series = {
            "AAPL": self._synthetic_closes(10, start=150.0),
            "MSFT": self._synthetic_closes(11, start=300.0),
        }
        with mock.patch.object(pilots_api, "HistoricalStore", return_value=self._Store(series)):
            resp = client.post(
                "/pilots/portfolio/optimize/hrp-cvar",
                json={
                    "symbols": ["AAPL", "MSFT"],
                    "current_weights": {"AAPL": 0.8, "MSFT": 0.2},
                    "lambda_turnover": 0.1,
                    "sector_map": {"AAPL": "Tech", "MSFT": "Tech"},
                    "sector_caps": {"Tech": 1.0},
                    "asset_betas": {"AAPL": 1.2, "MSFT": 0.9},
                    "target_beta_range": [0.8, 1.3],
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "turnover" in data
        assert "portfolio_beta" in data
        assert "sector_exposures" in data
        assert "diversification_ratio" in data
        assert "allocations" in data
        assert "expected_return" in data
        assert "cvar_95" in data
        assert "sharpe_ratio" in data
        assert "as_of" in data
        assert data["turnover"] >= 0.0
        assert data["portfolio_beta"] >= 0.0
        assert "Tech" in data["sector_exposures"]
        assert data["diversification_ratio"] >= 1.0
        # Honesty fix (audit finding): status/hrp_fallback must be surfaced on the
        # happy path too, not just on a forced-fallback request.
        assert data["status"] == "optimal"
        assert data["hrp_fallback"] is False

    def test_max_asset_weight_constrains_endpoint_output(self):
        # Phase 35 remediation item 13: max_asset_weight was previously a UI-only
        # slider whose value was never sent to the backend, and the backend's own
        # request model had no such field at all. Construct return series with a
        # heavy vol skew so an unconstrained HRP-CVaR optimum concentrates weight
        # in the calmest asset well above 40%, then confirm max_asset_weight=0.4
        # genuinely caps every allocation end-to-end through the real endpoint.
        rng_a = np.random.default_rng(100)
        calm = list(150.0 * np.cumprod(1 + rng_a.normal(0.0005, 0.001, size=150)))
        rng_b = np.random.default_rng(200)
        volatile_b = list(150.0 * np.cumprod(1 + rng_b.normal(0.0, 0.05, size=150)))
        rng_c = np.random.default_rng(300)
        volatile_c = list(150.0 * np.cumprod(1 + rng_c.normal(0.0, 0.05, size=150)))
        series = {"CALM": calm, "VOLB": volatile_b, "VOLC": volatile_c}

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=self._Store(series)):
            resp_unconstrained = client.post(
                "/pilots/portfolio/optimize/hrp-cvar",
                json={"symbols": ["CALM", "VOLB", "VOLC"], "lambda_turnover": 0.0},
            )
            resp_constrained = client.post(
                "/pilots/portfolio/optimize/hrp-cvar",
                json={
                    "symbols": ["CALM", "VOLB", "VOLC"],
                    "lambda_turnover": 0.0,
                    "max_asset_weight": 0.4,
                },
            )
        assert resp_unconstrained.status_code == 200
        assert resp_constrained.status_code == 200
        unconstrained_weights = {
            a["symbol"]: a["weight"] for a in resp_unconstrained.json()["allocations"]
        }
        constrained_weights = {
            a["symbol"]: a["weight"] for a in resp_constrained.json()["allocations"]
        }
        # Sanity: the unconstrained optimum genuinely concentrates weight above the
        # cap in the calm asset -- otherwise this test wouldn't exercise the cap.
        assert unconstrained_weights["CALM"] > 0.4
        # The cap is genuinely enforced end-to-end through the real endpoint.
        for w in constrained_weights.values():
            assert w <= 0.4 + 1e-6

    def test_infeasible_constraints_surface_fallback_status_honestly(self):
        """
        Math-audit finding: sizing.hrp_cvar_optimizer.optimize_turnover_regularized_hrp_cvar
        already computes `status`/`hrp_fallback`, but the API handler previously dropped
        both from its JSON response -- so a genuinely non-convergent SLSQP solve was
        indistinguishable over the wire from a clean optimum. Force an infeasible
        constraint combination (mirrors test_hrp_cvar_optimizer.py's own
        test_graceful_degradation_infeasible: all symbols in one sector, cap far below
        100%) through the REAL HTTP endpoint and confirm the response honestly reflects
        status != "optimal", not just at the sizing-module layer.
        """
        series = {
            "AAPL": self._synthetic_closes(20, start=150.0),
            "MSFT": self._synthetic_closes(21, start=300.0),
            "GOOGL": self._synthetic_closes(22, start=140.0),
        }
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=self._Store(series)):
                resp = client.post(
                    "/pilots/portfolio/optimize/hrp-cvar",
                    json={
                        "symbols": ["AAPL", "MSFT", "GOOGL"],
                        "sector_map": {"AAPL": "Tech", "MSFT": "Tech", "GOOGL": "Tech"},
                        # Impossible: all three assets are Tech but the cap is 20% while
                        # weights must sum to 100% -- no feasible point exists.
                        "sector_caps": {"Tech": 0.20},
                    },
                )
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] == "fallback"
        assert isinstance(data["hrp_fallback"], bool)
        # Weights must still sum to ~1.0 -- graceful degradation, not a broken response.
        assert np.isclose(sum(a["weight"] for a in data["allocations"]), 1.0, atol=1e-3)


# ---------------------------------------------------------------------------
# POST /pilots/options/meta-model/retrain -- previously fed the ML
# meta-labeler hardcoded literals (ivr=50.0, vrp=0.02, vix=20.0,
# credit_to_width_ratio=0.30, short_delta=0.30) for every simulated trade
# regardless of its real entry conditions (audit finding F3). Fixed to read
# validation.options_harness's real, computed entry-condition fields off
# each OptionsTradeRecord and skip (not silently default) any trade missing
# one of them.
# ---------------------------------------------------------------------------


class TestOptionsMetaModelRetrain:
    @staticmethod
    def _trade(strategy="Put Credit Spread", pnl=10.0, ivr=50.0, vrp=0.02, vix=20.0, ctw=0.30, delta=0.30):
        from validation.options_harness import OptionsTradeRecord

        return OptionsTradeRecord(
            entry_date="2023-01-01",
            exit_date="2023-02-01",
            strategy=strategy,
            underlying_entry_price=100.0,
            underlying_exit_price=101.0,
            entry_net_premium=30.0,
            exit_net_cost=10.0,
            pnl_dollar=pnl,
            pnl_pct=0.1,
            exit_reason="profit_target",
            holding_days=20,
            contracts=1,
            entry_ivr=ivr,
            entry_vrp=vrp,
            entry_short_delta=delta,
            entry_credit_to_width_ratio=ctw,
            entry_vix=vix,
        )

    def test_fails_closed_when_writes_disabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "PAPER_BROKER_WRITES_ENABLED", False):
                resp = client.post(
                    "/pilots/options/meta-model/retrain",
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 403

    def test_fails_closed_with_wrong_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "PAPER_BROKER_WRITES_ENABLED", True):
                resp = client.post(
                    "/pilots/options/meta-model/retrain",
                    headers={"Authorization": "Bearer WRONG"},
                )
        assert resp.status_code == 401

    def test_features_vary_across_trades_not_constant(self):
        from types import SimpleNamespace

        trades = [
            self._trade(ivr=10.0, vrp=0.01, vix=15.0, ctw=0.20, delta=0.20, pnl=5.0),
            self._trade(ivr=90.0, vrp=0.05, vix=30.0, ctw=0.45, delta=0.40, pnl=-5.0),
        ]
        fake_res = SimpleNamespace(trades=trades)

        captured = {}

        def fake_train(samples):
            captured["samples"] = list(samples)
            return {"samples": len(samples), "accuracy": 0.75, "roc_auc": 0.8}

        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "PAPER_BROKER_WRITES_ENABLED", True):
                with mock.patch(
                    "validation.options_harness.OptionsValidationHarness.run_backtest",
                    return_value=fake_res,
                ):
                    with mock.patch(
                        "ml.options_meta_labeler.global_options_meta_labeler.train",
                        side_effect=fake_train,
                    ) as mock_train:
                        resp = client.post(
                            "/pilots/options/meta-model/retrain",
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert mock_train.called
        # run_backtest is mocked identically for all 3 strategies
        # ("Put Credit Spread", "Call Credit Spread", "Iron Condor"), so
        # samples = 2 trades * 3 strategy calls = 6.
        samples = captured["samples"]
        assert len(samples) == 6
        assert body["skipped_trades"] == 0

        # The core assertion this test exists for: feature values genuinely
        # differ across samples instead of every sample carrying the old
        # hardcoded constants (ivr=50.0/vrp=0.02/vix=20.0/ctw=0.30/delta=0.30).
        assert len({s.ivr for s in samples}) > 1
        assert len({s.vrp for s in samples}) > 1
        assert len({s.vix for s in samples}) > 1
        assert len({s.credit_to_width_ratio for s in samples}) > 1
        assert len({s.short_delta for s in samples}) > 1

    def test_skips_trades_missing_a_real_field(self):
        from types import SimpleNamespace

        good_trade = self._trade(ivr=10.0, vrp=0.01, vix=15.0, ctw=0.20, delta=0.20)
        missing_vix_trade = self._trade(ivr=20.0, vrp=0.02, vix=None, ctw=0.25, delta=0.25)
        fake_res = SimpleNamespace(trades=[good_trade, missing_vix_trade])

        captured = {}

        def fake_train(samples):
            captured["samples"] = list(samples)
            return {"samples": len(samples), "accuracy": 0.7, "roc_auc": 0.7}

        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "PAPER_BROKER_WRITES_ENABLED", True):
                with mock.patch(
                    "validation.options_harness.OptionsValidationHarness.run_backtest",
                    return_value=fake_res,
                ):
                    with mock.patch(
                        "ml.options_meta_labeler.global_options_meta_labeler.train",
                        side_effect=fake_train,
                    ):
                        resp = client.post(
                            "/pilots/options/meta-model/retrain",
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )

        assert resp.status_code == 200
        body = resp.json()
        # 1 good trade * 3 strategies = 3 samples trained on;
        # 1 missing-entry_vix trade * 3 strategies = 3 skipped.
        assert len(captured["samples"]) == 3
        assert body["skipped_trades"] == 3


# ---------------------------------------------------------------------------
# GET /pilots/options/ai/transformer-forecast -- previously fed the model
# np.random.randn(...) noise as "market history" and never trained the
# model's output weights at all (audit finding F7). Fixed to fetch real
# historical bars, build a real causal feature/window pipeline, and train
# before predicting.
# ---------------------------------------------------------------------------


class _OhlcvStore:
    """Minimal HistoricalStore.get_bars stand-in returning a real-shaped
    OHLCV DataFrame from a pre-baked Close series (matches
    TestHrpCvarOptimize._Store's convention one section above)."""

    def __init__(self, series_by_symbol):
        self._series = series_by_symbol

    def get_bars(self, symbol, lookback_days=504):
        closes = self._series.get(symbol)
        if closes is None:
            return pd.DataFrame()
        idx = pd.bdate_range(end="2026-08-01", periods=len(closes))
        closes = pd.Series(closes, index=idx)
        return pd.DataFrame(
            {
                "Open": closes.shift(1).fillna(closes.iloc[0]),
                "High": closes * 1.01,
                "Low": closes * 0.99,
                "Close": closes,
                "Volume": pd.Series(1_000_000.0, index=idx),
            },
            index=idx,
        )


def _synthetic_closes_walk(seed, n=400, start=100.0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(loc=0.0003, scale=0.011, size=n)
    return list(start * np.cumprod(1 + rets))


class TestTransformerForecast:
    def test_calls_get_bars_with_symbol_and_returns_trained_forecast(self):
        store = _OhlcvStore({"AAPL": _synthetic_closes_walk(1, n=400, start=150.0)})
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=store) as mock_hs:
                resp = client.get("/pilots/options/ai/transformer-forecast", params={"symbol": "AAPL"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        for h in ["1d", "5d", "21d", "60d"]:
            assert h in body["forecast"]
            assert isinstance(body["forecast"][h], float)
        assert body["trained_samples"] >= 30
        assert "quantile_forecast" in body
        for h in ["1d", "5d", "21d", "60d"]:
            assert h in body["quantile_forecast"]
            q_h = body["quantile_forecast"][h]
            assert "q10" in q_h and "q50" in q_h and "q90" in q_h
            assert q_h["q10"] <= q_h["q50"] <= q_h["q90"]
        assert "macro_conditioned" in body
        # get_bars was actually called -- the real-data path is exercised,
        # not bypassed.
        mock_hs.assert_called()

    def test_two_different_series_produce_different_forecasts(self):
        store_a = _OhlcvStore({"AAPL": _synthetic_closes_walk(11, n=400, start=150.0)})
        store_b = _OhlcvStore({"AAPL": _synthetic_closes_walk(22, n=400, start=150.0)})

        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=store_a):
                resp_a = client.get("/pilots/options/ai/transformer-forecast", params={"symbol": "AAPL"})
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=store_b):
                resp_b = client.get("/pilots/options/ai/transformer-forecast", params={"symbol": "AAPL"})

        assert resp_a.status_code == 200 and resp_b.status_code == 200
        forecast_a = resp_a.json()["forecast"]
        forecast_b = resp_b.json()["forecast"]
        assert forecast_a != forecast_b

    def test_insufficient_history_returns_honest_422(self):
        store = _OhlcvStore({"AAPL": _synthetic_closes_walk(1, n=50, start=150.0)})
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
                resp = client.get("/pilots/options/ai/transformer-forecast", params={"symbol": "AAPL"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "insufficient_history_for_symbol"

    def test_unknown_symbol_returns_honest_422(self):
        store = _OhlcvStore({})
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
                resp = client.get("/pilots/options/ai/transformer-forecast", params={"symbol": "ZZZZ"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "insufficient_history_for_symbol"


# ---------------------------------------------------------------------------
# POST /pilots/options/ai/diffusion-stress-test -- previously fed the model
# np.random.randn(...) * volatility + drift as "historical data" (audit
# finding F7). Fixed to fetch real historical bars and window real log
# returns. train_diffusion_model already fits its own score-network weights
# via an internal Adam loop, so the real-input-data swap alone closes this
# finding (no separate training call needed, unlike the transformer above).
# ---------------------------------------------------------------------------


class TestDiffusionStressTest:
    def _base_request(self, symbol="AAPL", regime="vol_shock", guidance_scale=2.0):
        return {
            "symbol": symbol,
            "spot_price": 150.0,
            "volatility": 0.25,
            "num_paths": 50,
            "horizon": 30,
            "drift": 0.0,
            "regime": regime,
            "guidance_scale": guidance_scale,
        }

    def test_calls_get_bars_with_symbol_and_returns_real_data_driven_result(self):
        store = _OhlcvStore({"AAPL": _synthetic_closes_walk(1, n=400, start=150.0)})
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=store) as mock_hs:
                resp = client.post("/pilots/options/ai/diffusion-stress-test", json=self._base_request())
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["regime"] == "vol_shock"
        assert body["guidance_scale"] == 2.0
        assert len(body["paths"]) == 50
        assert body["VaR_95"] >= 0.0
        assert body["CVaR_95"] >= body["VaR_95"]
        assert body["VaR_99"] >= 0.0
        assert body["CVaR_99"] >= body["VaR_99"]
        assert body["trained_windows"] > 0
        mock_hs.assert_called()

    def test_custom_regime_and_guidance_scale(self):
        store = _OhlcvStore({"AAPL": _synthetic_closes_walk(5, n=400, start=150.0)})
        with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
            resp = client.post(
                "/pilots/options/ai/diffusion-stress-test",
                json=self._base_request(regime="stagflation", guidance_scale=3.5),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["regime"] == "stagflation"
        assert body["guidance_scale"] == 3.5
        assert len(body["paths"]) == 50
        assert "VaR_99" in body
        assert "CVaR_99" in body

    def test_two_different_series_produce_different_var(self):
        store_a = _OhlcvStore({"AAPL": _synthetic_closes_walk(11, n=400, start=150.0)})
        store_b = _OhlcvStore({"AAPL": _synthetic_closes_walk(22, n=400, start=150.0)})

        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=store_a):
                resp_a = client.post("/pilots/options/ai/diffusion-stress-test", json=self._base_request())
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=store_b):
                resp_b = client.post("/pilots/options/ai/diffusion-stress-test", json=self._base_request())

        assert resp_a.status_code == 200 and resp_b.status_code == 200
        assert resp_a.json()["VaR_95"] != resp_b.json()["VaR_95"]

    def test_insufficient_history_returns_honest_422(self):
        store = _OhlcvStore({"AAPL": _synthetic_closes_walk(1, n=5, start=150.0)})
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
                resp = client.post("/pilots/options/ai/diffusion-stress-test", json=self._base_request())
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "insufficient_history_for_symbol"

    def test_horizon_out_of_bounds_returns_honest_422(self):
        # 2026-08: horizon is now bounded (Field(30, ge=5, le=35)) -- the
        # early-stop + Tweedie denoising calibration fix is only verified
        # well-calibrated up to horizon~30-35; see docs/known_issues/
        # synthetic_diffusion_reverse_sde_sign_error.md's "Further
        # mitigated" section for the measured L-dependence table. A
        # request outside the bound must get a clean Pydantic validation
        # 422, never a 500 or a silent clamp.
        req = self._base_request()
        req["horizon"] = 50
        resp = client.post("/pilots/options/ai/diffusion-stress-test", json=req)
        assert resp.status_code == 422

        req2 = self._base_request()
        req2["horizon"] = 1
        resp2 = client.post("/pilots/options/ai/diffusion-stress-test", json=req2)
        assert resp2.status_code == 422

    def test_unknown_symbol_returns_honest_422(self):
        store = _OhlcvStore({})
        # STATE_API_TOKEN must be EXPLICITLY unset here, not assumed ambient --
        # see TestAutomationIntervalWrite.test_command_token_required's comment for why.
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
                resp = client.post("/pilots/options/ai/diffusion-stress-test", json=self._base_request())
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "insufficient_history_for_symbol"

    def test_var_cvar_never_reach_or_exceed_spot_price_end_to_end(self):
        # Phase 34 remediation item 10 (audit Critical #5) regression guard,
        # exercised through the REAL endpoint (not just the pure helper):
        # a dollar VaR/CVaR loss can never imply a negative post-loss price,
        # and every generated price path stays strictly positive, regardless
        # of how extreme the (undertrained, few-epoch) diffusion model's raw
        # output happens to be on this draw.
        store = _OhlcvStore({"AAPL": _synthetic_closes_walk(3, n=400, start=150.0)})
        with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
            resp = client.post("/pilots/options/ai/diffusion-stress-test", json=self._base_request())
        assert resp.status_code == 200
        body = resp.json()
        spot = 150.0
        for key in ("VaR_95", "CVaR_95", "VaR_99", "CVaR_99"):
            assert 0.0 <= body[key] < spot, f"{key}={body[key]} is not in [0, spot={spot})"
        for path in body["paths"]:
            assert all(p > 0 for p in path), "a generated price path went <= 0"

    def test_regime_labels_none_when_macro_unavailable_degrades_gracefully(self):
        # _OhlcvStore has no get_macro() -- confirms _derive_diffusion_regime_labels
        # degrades to None (today's exact unconditional-training behavior)
        # rather than crashing the whole endpoint when macro data is
        # unavailable (CONSTRAINT #6).
        store = _OhlcvStore({"AAPL": _synthetic_closes_walk(1, n=400, start=150.0)})
        with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
            resp = client.post("/pilots/options/ai/diffusion-stress-test", json=self._base_request())
        assert resp.status_code == 200
        assert resp.json()["regime_conditioned"] is False

    def test_var_cvar_computed_from_the_same_paths_returned_to_the_client(self):
        # 2026-08 regression guard, see docs/known_issues/
        # synthetic_diffusion_reverse_sde_sign_error.md's "VaR/CVaR-vs-paths
        # consistency" section. Previously VaR/CVaR were computed from the
        # raw, unclipped generated log-returns while `paths` were built from
        # a clipped/compounded variant of the SAME draw -- two consumers
        # reading different effective data. This recomputes VaR/CVaR
        # entirely independently from ONLY the `paths` array in the
        # response body (each path's total realized simple return,
        # final_price/spot - 1, percentile-ranked) and asserts it matches
        # the endpoint's own reported figures exactly -- proving VaR/CVaR
        # really is derived from the exact data the client also sees, not a
        # separately-drawn or separately-transformed variant of it.
        store = _OhlcvStore({"AAPL": _synthetic_closes_walk(5, n=400, start=150.0)})
        with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
            resp = client.post("/pilots/options/ai/diffusion-stress-test", json=self._base_request())
        assert resp.status_code == 200
        body = resp.json()
        spot = 150.0

        from validation.synthetic_diffusion_engine import compute_diffusion_var

        total_simple_returns = np.array([p[-1] / spot - 1.0 for p in body["paths"]])
        for cl, var_key, cvar_key in ((0.95, "VaR_95", "CVaR_95"), (0.99, "VaR_99", "CVaR_99")):
            expected_var_frac, expected_cvar_frac = compute_diffusion_var(
                total_simple_returns, confidence_level=cl,
            )
            expected_var = max(0.0, spot * expected_var_frac)
            expected_cvar = max(0.0, spot * expected_cvar_frac)
            assert body[var_key] == pytest.approx(expected_var, rel=1e-9, abs=1e-9)
            assert body[cvar_key] == pytest.approx(expected_cvar, rel=1e-9, abs=1e-9)


# ---------------------------------------------------------------------------
# Phase 34 remediation item 10 (audit Critical #5) -- unit tests for the
# extracted pure helpers directly, independent of the diffusion model's own
# (possibly extreme, undertrained-at-15-epochs) output.
# ---------------------------------------------------------------------------


class TestDiffusionPriceBoundAndVarUnitFix:
    def test_clip_and_compound_never_goes_negative_on_adversarial_returns(self):
        # Mirrors the original audit's repro: an adversarial/extreme
        # synthetic return path (as an undertrained diffusion model's
        # reverse SDE could emit -- generate_guided_crisis_paths clips the
        # latent state to +/-50) that would explode/flip negative under the
        # OLD unclipped `price_path[-1] * (1.0 + r)` compounding.
        extreme_returns = [5.0, -3.0, 10.0, -1.5, 2.0, -8.0, 6.0]
        path = pilots_api._clip_and_compound_diffusion_path(extreme_returns, spot_price=150.0)
        assert path[0] == 150.0
        assert all(p > 0 for p in path), "a clipped path went <= 0"
        assert min(path) >= 0.01
        # Every step is bounded to a -50%/+200% move, so the path can never
        # exceed spot * 3^len(extreme_returns).
        assert path[-1] <= 150.0 * (3.0 ** len(extreme_returns))

    def test_clip_and_compound_matches_naive_compounding_for_normal_returns(self):
        # A realistic, small-magnitude return path (well inside the clip
        # bounds) must compound identically to the naive formula -- the fix
        # must not distort ordinary, non-adversarial paths.
        normal_returns = [0.01, -0.02, 0.015, -0.01, 0.02]
        path = pilots_api._clip_and_compound_diffusion_path(normal_returns, spot_price=150.0)
        expected = [150.0]
        for r in normal_returns:
            expected.append(expected[-1] * (1.0 + r))
        assert path == pytest.approx(expected, rel=1e-9)

    def test_logret_loss_to_dollars_well_under_spot_for_realistic_var(self):
        # A realistic horizon log-return VaR (a handful of percent to ~25%)
        # should convert to a dollar loss well under 100% of spot -- not the
        # near-100%-saturated artifact the old linear formula could produce.
        spot = 150.0
        for var_logret in (0.05, 0.10, 0.15, 0.25):
            dollars = pilots_api._diffusion_logret_loss_to_dollars(var_logret, spot)
            assert 0.0 <= dollars < spot
            assert dollars < spot * 0.30, f"VaR ${dollars:.2f} not well under spot ${spot}"

    def test_logret_loss_to_dollars_never_exceeds_spot_for_extreme_var(self):
        # The exponential form is bounded ABOVE by spot_price (never
        # exceeds it, unlike the old linear multiply). For a genuinely
        # extreme var_logret (e.g. 50.0) exp(-var_logret) underflows to a
        # value indistinguishable from 0.0 in float64, so the loss can
        # legitimately round to exactly spot_price -- the invariant that
        # matters is "never exceeds", not "always strictly less than".
        spot = 150.0
        for var_logret in (0.9, 1.5, 5.0, 50.0):
            dollars = pilots_api._diffusion_logret_loss_to_dollars(var_logret, spot)
            assert 0.0 <= dollars <= spot
        # At a realistic-to-moderately-stressed magnitude, strictly below spot.
        for var_logret in (0.9, 1.5, 5.0):
            dollars = pilots_api._diffusion_logret_loss_to_dollars(var_logret, spot)
            assert dollars < spot

    def test_old_linear_conversion_would_have_implied_negative_price_regression_guard(self):
        # Documents the exact bug being fixed: the OLD linear formula
        # (var_logret * spot_price) implies a negative post-loss price for
        # any var_logret > 1.0 -- nonsensical for a VaR/CVaR loss on a long
        # spot position. The new exponential transform never does.
        spot = 150.0
        var_logret = 1.5
        old_linear_loss = var_logret * spot
        assert old_linear_loss > spot  # the bug: implies price < 0
        new_loss = pilots_api._diffusion_logret_loss_to_dollars(var_logret, spot)
        assert new_loss < spot  # fixed: implied price always > 0


class _OhlcvAndMacroStore(_OhlcvStore):
    """Extends _OhlcvStore with a real get_macro() stub for Phase 34
    remediation item 11 tests. UNRATE is a long, flat monthly history (never
    triggers RECESSION via the internally-derived Sahm proxy once past
    rolling-window warmup); T10Y2Y and VIX stay constant/benign; the
    high-yield credit spread (BAMLH0A0HYM2) steps from a calm 2.0% to a
    stressed 8.0% at a known cutover business-day index, so a real,
    non-degenerate CREDIT EVENT regime is reconstructable across the trading
    history."""

    def __init__(self, series_by_symbol, *, n_days, credit_spread_cutover_idx):
        super().__init__(series_by_symbol)
        self._idx = pd.bdate_range(end="2026-08-01", periods=n_days)
        self._cutover_date = self._idx[credit_spread_cutover_idx]

    def get_macro(self, series_id, *, lookback_days=None, data_engine=None):
        if series_id == "VIXCLS":
            return pd.Series(15.0, index=self._idx, name=series_id)
        if series_id == "T10Y2Y":
            return pd.Series(1.0, index=self._idx, name=series_id)
        if series_id == "BAMLH0A0HYM2":
            values = np.where(self._idx < self._cutover_date, 2.0, 8.0)
            return pd.Series(values, index=self._idx, name=series_id)
        if series_id == "UNRATE":
            # 8 years of flat monthly unemployment so the internally-derived
            # Sahm proxy is well past its rolling-window warmup (needs ~15
            # months) and stays 0.0 (never >= 0.6 -- never RECESSION) at
            # every date this test's window-end dates could touch.
            monthly_idx = pd.date_range(end="2026-08-01", periods=96, freq="MS")
            return pd.Series(4.0, index=monthly_idx, name=series_id)
        if series_id == "BAA10Y":
            return pd.Series(2.0, index=self._idx, name=series_id)
        return pd.Series(dtype=float, name=series_id)


class TestDiffusionRegimeConditioning:
    """Phase 34 remediation item 11 (audit Critical #6): the live endpoint
    never passed regime_labels into train_conditional_diffusion_model, so
    classifier-free guidance was training against an entirely unconditional
    dataset regardless of the caller's requested regime."""

    def test_regime_labels_passed_with_multiple_distinct_classes(self):
        n = 750
        closes = _synthetic_closes_walk(9, n=n, start=150.0)
        store = _OhlcvAndMacroStore({"AAPL": closes}, n_days=n, credit_spread_cutover_idx=600)

        # Capture the REAL function BEFORE patching -- re-importing it from
        # inside the spy while the patch is active would just return the
        # mock again (infinite recursion), since mock.patch replaces the
        # module attribute for the duration of the context manager.
        from validation.synthetic_diffusion_engine import (
            train_conditional_diffusion_model as _real_train,
        )

        captured: dict = {}

        def _spy_train(historical_data, regime_labels=None, **kwargs):
            captured["regime_labels"] = regime_labels
            captured["n_rows"] = len(historical_data)
            return _real_train(historical_data, regime_labels=regime_labels, epochs=1, lr=0.01)

        with mock.patch.object(pilots_api, "HistoricalStore", return_value=store), mock.patch(
            "validation.synthetic_diffusion_engine.train_conditional_diffusion_model",
            side_effect=_spy_train,
        ):
            resp = client.post(
                "/pilots/options/ai/diffusion-stress-test",
                json={
                    "symbol": "AAPL",
                    "spot_price": 150.0,
                    "volatility": 0.25,
                    "num_paths": 10,
                    "horizon": 30,
                    "drift": 0.0,
                    "regime": "vol_shock",
                    "guidance_scale": 2.0,
                },
            )

        assert resp.status_code == 200
        assert resp.json()["regime_conditioned"] is True

        regime_labels = captured.get("regime_labels")
        assert regime_labels is not None
        assert len(regime_labels) == captured["n_rows"]
        distinct = set(regime_labels)
        assert len(distinct) > 1, f"expected multiple distinct regime classes, got {distinct}"
        assert "credit_freeze" in distinct
        assert "unconditional" in distinct
        # This store's get_macro() has no "T10YIE" case (falls through to an
        # empty Series), so the real T10YIE-based stagflation override in
        # _derive_diffusion_regime_labels never fires here -- see
        # TestDiffusionStagflationOverride below for the override itself,
        # exercised against a store that DOES mock T10YIE.
        assert "stagflation" not in distinct

    def test_window_end_dates_mirror_build_return_windows_index_math(self):
        from validation.synthetic_diffusion_engine import build_return_windows

        dates = pd.bdate_range(end="2026-08-01", periods=400)
        returns = np.arange(400, dtype=float)  # value == position, for an easy check
        window_len = 29
        max_windows = 200

        windows = build_return_windows(returns, window_len=window_len, max_windows=max_windows)
        end_dates = pilots_api._diffusion_window_end_dates(
            dates, window_len=window_len, max_windows=max_windows,
        )

        assert len(end_dates) == len(windows)
        for row, end_date in zip(windows, end_dates):
            # row[-1] is the raw return value, which we set equal to its
            # original position in `returns` -- so it's also the position in
            # `dates` whose date must equal end_date.
            assert dates[int(row[-1])] == end_date


class _StagflationMacroStore:
    """``HistoricalStore.get_macro()`` stand-in for testing the T10YIE +
    UNRATE stagflation override added to ``_derive_diffusion_regime_labels``.

    VIXCLS/T10Y2Y/BAA10Y stay flat/benign for the whole history (never push
    the base bucket toward RECESSION/CREDIT EVENT on their own). BAMLH0A0HYM2
    (credit spread) is a low, RISK-ON-territory 2.0 everywhere except one
    single spiked date (8.0, real CREDIT EVENT territory per
    dto_models.MacroEconomicDTO._rules_based_regime), used to prove the
    override never overrides an already-more-specific real signal. T10YIE
    steps from a flat 2.0 baseline to an elevated 3.5 plateau starting at
    ``elevated_start_idx`` (well within a 126-business-day rolling window of
    itself by the time any test date is checked). UNRATE is a long, flat 4.0%
    monthly series (so the Sahm Rule proxy is safely warmed up and near-zero)
    that rises gently -- 4.0% -> 4.3% over its final 12 months, well under
    the Sahm Rule's 0.6pp recession trigger -- so "UNRATE trending up" is
    real without also flipping the base bucket to RECESSION.
    """

    def __init__(self, dates, *, elevated_start_idx, credit_event_date):
        self._dates = dates
        self._elevated_start_idx = elevated_start_idx
        self._credit_event_date = credit_event_date

    def get_macro(self, series_id, *, lookback_days=None, data_engine=None):
        idx = self._dates
        if series_id == "VIXCLS":
            return pd.Series(15.0, index=idx, name=series_id)
        if series_id == "T10Y2Y":
            return pd.Series(1.0, index=idx, name=series_id)
        if series_id == "BAA10Y":
            return pd.Series(2.0, index=idx, name=series_id)
        if series_id == "BAMLH0A0HYM2":
            values = pd.Series(2.0, index=idx, name=series_id)
            values.loc[self._credit_event_date] = 8.0
            return values
        if series_id == "T10YIE":
            values = np.where(np.arange(len(idx)) >= self._elevated_start_idx, 3.5, 2.0)
            return pd.Series(values, index=idx, name=series_id)
        if series_id == "UNRATE":
            monthly_idx = pd.date_range(end=idx[-1], periods=120, freq="MS")
            values = np.full(len(monthly_idx), 4.0)
            values[-12:] = np.linspace(4.0, 4.3, 12)
            return pd.Series(values, index=monthly_idx, name=series_id)
        return pd.Series(dtype=float, name=series_id)


class TestDiffusionStagflationOverride:
    """The plan's item 1: ``_derive_diffusion_regime_labels`` now assigns a
    real, FRED-sourced ``stagflation`` label (elevated T10YIE + rising
    UNRATE) rather than never emitting it. Uses window_len=1 so every date
    in ``dates`` is its own window's end date (n_available == len(dates),
    n_windows == len(dates) when max_windows >= len(dates)), letting a single
    store/call exercise three distinct dates deterministically."""

    N_DAYS = 500
    ELEVATED_START_IDX = 470  # T10YIE plateau starts here
    CALM_IDX = 200            # before the T10YIE plateau and the UNRATE rise
    STAGFLATION_IDX = 490     # inside the plateau; credit spread stays calm
    CREDIT_EVENT_IDX = 485    # inside the plateau; credit spread is spiked here

    def _dates_and_store(self):
        dates = pd.bdate_range(end="2026-08-01", periods=self.N_DAYS)
        store = _StagflationMacroStore(
            dates,
            elevated_start_idx=self.ELEVATED_START_IDX,
            credit_event_date=dates[self.CREDIT_EVENT_IDX],
        )
        return dates, store

    def test_assigns_stagflation_to_elevated_inflation_and_rising_unemployment_window(self):
        dates, store = self._dates_and_store()
        with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
            labels = pilots_api._derive_diffusion_regime_labels(
                dates, window_len=1, max_windows=self.N_DAYS,
            )
        assert labels is not None
        assert labels[self.STAGFLATION_IDX] == "stagflation"

    def test_does_not_assign_stagflation_to_a_calm_window(self):
        dates, store = self._dates_and_store()
        with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
            labels = pilots_api._derive_diffusion_regime_labels(
                dates, window_len=1, max_windows=self.N_DAYS,
            )
        assert labels is not None
        assert labels[self.CALM_IDX] != "stagflation"

    def test_does_not_override_an_already_credit_event_window(self):
        # Elevated T10YIE + rising UNRATE both hold at this date too (it's
        # inside the same plateau as STAGFLATION_IDX), but the base bucket
        # is a real, more-specific CREDIT EVENT (credit spread spiked to 8.0
        # on this exact date) -- the override must never replace a more
        # specific, already-correct classification with a less specific one.
        dates, store = self._dates_and_store()
        with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
            labels = pilots_api._derive_diffusion_regime_labels(
                dates, window_len=1, max_windows=self.N_DAYS,
            )
        assert labels is not None
        assert labels[self.CREDIT_EVENT_IDX] == "credit_freeze"

    def test_no_t10yie_series_never_applies_override(self):
        # Same elevated-plateau/rising-UNRATE setup, but get_macro("T10YIE")
        # degrades to an empty Series (mirrors a real HistoricalStore that
        # has never cached T10YIE) -- the override must never fire, and the
        # rest of the label derivation must proceed unaffected (CONSTRAINT #6).
        dates, store = self._dates_and_store()

        real_get_macro = store.get_macro

        def _get_macro_no_t10yie(series_id, **kwargs):
            if series_id == "T10YIE":
                return pd.Series(dtype=float, name=series_id)
            return real_get_macro(series_id, **kwargs)

        store.get_macro = _get_macro_no_t10yie
        with mock.patch.object(pilots_api, "HistoricalStore", return_value=store):
            labels = pilots_api._derive_diffusion_regime_labels(
                dates, window_len=1, max_windows=self.N_DAYS,
            )
        assert labels is not None
        assert "stagflation" not in set(labels)
        assert labels[self.STAGFLATION_IDX] == "unconditional"


# ---------------------------------------------------------------------------
# FIX 4.4 Protocol Gateway Session Management Endpoints
# ---------------------------------------------------------------------------


class TestFixGatewaySessionEndpoints:
    def test_get_fix_session_status_success(self):
        # Phase 36 remediation item 15: the status endpoint no longer fabricates a
        # NYSE/NASDAQ/BATS/IEX/ARCA equity venue list or a synthetic 3-message audit
        # log fallback -- it reports the module's REAL configured venues (CBOE, MIAX,
        # BOX, PHLX, ARCA, EDGX from MultiVenueAggregator) and only ever real
        # session.message_log entries. Send a real Test Request first so message_log
        # is deterministically non-empty regardless of what order tests run in
        # (the global FixSession singleton is process-wide and this test module is
        # not guaranteed to run before/after its siblings under pytest-randomly).
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            client.post(
                "/pilots/execution/fix/session/test-request",
                json={"test_req_id": "TEST-STATUS-SEED"},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )

        with mock.patch.object(settings, "STATE_API_TOKEN", _CMD_TOKEN):
            resp = client.get(
                "/pilots/execution/fix/session/status",
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "session_id" in body
        assert body["sender_comp_id"] == "INVESTYO_PWA"
        assert body["target_comp_id"] == "FIX_GATEWAY"
        assert body["state"] in {
            "ACTIVE", "CONNECTING", "LOGON_SENT", "LOGON_RECEIVED",
            "RESEND_REQUESTED", "GAP_FILL_PROCESSING", "LOGOUT_SENT", "DISCONNECTED", "SUSPENDED"
        }
        assert isinstance(body["in_seq_num"], int)
        assert isinstance(body["out_seq_num"], int)
        assert isinstance(body["gap_queue_depth"], int)
        assert isinstance(body["venues_active"], list)
        # Real MultiVenueAggregator venues, not the old fabricated equity list.
        assert set(body["venues_active"]) == {"CBOE", "MIAX", "BOX", "PHLX", "ARCA", "EDGX"}
        assert "NYSE" not in body["venues_active"]
        assert "NASDAQ" not in body["venues_active"]
        assert "venue_stats" in body
        assert len(body["venue_stats"]) == 6
        for v in body["venue_stats"]:
            # Real VenueConfig-backed fields are always populated numerically.
            assert isinstance(v["base_latency_ms"], (int, float))
            assert isinstance(v["maker_fee"], (int, float))
            assert isinstance(v["taker_fee"], (int, float))
            assert isinstance(v["liquidity_depth"], (int, float))
            # Fields with no real source in this stateless aggregator are honestly
            # None rather than a fabricated plausible-looking number.
            assert v["fill_rate_pct"] is None
            assert v["share_of_flow_pct"] is None
        assert "audit_log" in body
        assert len(body["audit_log"]) > 0
        # No fabricated ORD-99124/CL-3019 fake fill in the log.
        assert not any("ORD-99124" in line for line in body["audit_log"])
        assert "session_uptime_sec" in body
        assert body["session_uptime_sec"] is None or body["session_uptime_sec"] >= 0

    def test_get_fix_session_status_no_fabricated_audit_log_when_empty(self):
        # A brand-new session with zero real messages returns an honest empty
        # audit_log rather than a synthetic fallback (audit finding Critical #9).
        import execution.fix_gateway as fix_gateway_module

        with mock.patch.object(fix_gateway_module, "_global_fix_session", None):
            with mock.patch.object(settings, "STATE_API_TOKEN", _CMD_TOKEN):
                resp = client.get(
                    "/pilots/execution/fix/session/status",
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        assert resp.json()["audit_log"] == []

    def test_get_fix_session_status_fail_open_without_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/pilots/execution/fix/session/status")
        assert resp.status_code == 200
        assert resp.json()["session_id"].startswith("FIX.4.4:")

    def test_post_fix_session_test_request_success(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post(
                "/pilots/execution/fix/session/test-request",
                json={"test_req_id": "TEST-UNIT-01"},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["test_req_id"] == "TEST-UNIT-01"
        assert "Heartbeat" in body["message"]
        assert body["session_state"] == "ACTIVE"
        assert "round_trip_ms" in body
        # Real measurement, not the fixed sentinel this endpoint used to return
        # for every call regardless of how long the round trip actually took.
        assert isinstance(body["round_trip_ms"], (int, float))
        assert body["round_trip_ms"] >= 0.0

    def test_post_fix_session_test_request_round_trip_reflects_real_elapsed_time(self):
        """`round_trip_ms` must be computed from real elapsed wall-clock time
        (CONSTRAINT #4), not the old hardcoded `1.25` constant -- proven by
        injecting a real, measurable `time.sleep()` into the session's own
        `simulate_receive` call (this repo's established pattern for
        timing-sensitive tests, see `tests/test_market_data.py`) and
        asserting the returned value reflects it. This deliberately does NOT
        try to fully control `time.perf_counter()` globally, since
        ASGI/Starlette internals make their own untracked calls to it during
        a request."""
        import time as time_module

        from execution.fix_gateway import get_global_fix_session

        session = get_global_fix_session()
        real_simulate_receive = session.simulate_receive

        def _make_slow_simulate_receive(delay_s):
            def _fn(*args, **kwargs):
                time_module.sleep(delay_s)
                return real_simulate_receive(*args, **kwargs)
            return _fn

        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN), \
                mock.patch.object(session, "simulate_receive", side_effect=_make_slow_simulate_receive(0.05)):
            resp = client.post(
                "/pilots/execution/fix/session/test-request",
                json={"test_req_id": "TEST-TIMING-01"},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        # Must reflect (at least most of) the injected 50ms delay -- a
        # hardcoded 1.25 could never do this.
        assert body["round_trip_ms"] >= 0.05 * 1000 * 0.8
        assert body["round_trip_ms"] != 1.25

        # A LONGER injected delay must produce a LARGER round_trip_ms --
        # proving this isn't a constant in disguise.
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN), \
                mock.patch.object(session, "simulate_receive", side_effect=_make_slow_simulate_receive(0.15)):
            resp2 = client.post(
                "/pilots/execution/fix/session/test-request",
                json={"test_req_id": "TEST-TIMING-02"},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["round_trip_ms"] > body["round_trip_ms"]

    def test_post_fix_session_reset_seq_hard_and_gap_fill(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            # Hard reset
            resp1 = client.post(
                "/pilots/execution/fix/session/reset-seq",
                json={"new_seq_num": 500, "gap_fill": False},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
            assert resp1.status_code == 200
            body1 = resp1.json()
            assert body1["status"] == "ok"
            assert body1["new_seq_num"] == 500
            assert body1["out_seq_num"] == 500

            # Gap fill
            resp2 = client.post(
                "/pilots/execution/fix/session/reset-seq",
                json={"new_seq_num": 600, "gap_fill": True},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
            assert resp2.status_code == 200
            body2 = resp2.json()
            assert body2["status"] == "ok"
            assert body2["new_seq_num"] == 600
            assert body2["out_seq_num"] == 600

    def test_post_fix_session_reconnect_success(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post(
                "/pilots/execution/fix/session/reconnect",
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["session_state"] == "ACTIVE"

    def test_post_fix_session_command_auth_required(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post(
                "/pilots/execution/fix/session/test-request",
                json={},
                headers={"Authorization": "Bearer WRONG_TOKEN"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PR #792 deep-dive audit follow-up (Cluster A): items 4 and 5
# ---------------------------------------------------------------------------


class TestFixGatewayEnabledFlag:
    """Item 5: settings.FIX_GATEWAY_ENABLED is an ADDITIONAL gate on top of
    each endpoint's existing require_command_token/require_read_token check
    -- when False, every route/session-management endpoint must refuse with
    403 rather than proceeding, even with a valid command/read token."""

    def test_route_blocked_when_disabled(self):
        with mock.patch.object(settings, "FIX_GATEWAY_ENABLED", False), \
                mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post(
                "/pilots/execution/fix/route",
                json={"symbol": "AAPL", "side": "BUY", "quantity": 10, "limit_price": 100.0},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403
        assert "FIX_GATEWAY_ENABLED" in resp.json()["detail"]

    def test_session_status_blocked_when_disabled(self):
        with mock.patch.object(settings, "FIX_GATEWAY_ENABLED", False), \
                mock.patch.object(settings, "STATE_API_TOKEN", _CMD_TOKEN):
            resp = client.get(
                "/pilots/execution/fix/session/status",
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403
        assert "FIX_GATEWAY_ENABLED" in resp.json()["detail"]

    def test_test_request_blocked_when_disabled(self):
        with mock.patch.object(settings, "FIX_GATEWAY_ENABLED", False), \
                mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post(
                "/pilots/execution/fix/session/test-request",
                json={},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403

    def test_reset_seq_blocked_when_disabled(self):
        with mock.patch.object(settings, "FIX_GATEWAY_ENABLED", False), \
                mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post(
                "/pilots/execution/fix/session/reset-seq",
                json={"new_seq_num": 5},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403

    def test_reconnect_blocked_when_disabled(self):
        with mock.patch.object(settings, "FIX_GATEWAY_ENABLED", False), \
                mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post(
                "/pilots/execution/fix/session/reconnect",
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403

    def test_venues_endpoint_not_gated_by_flag(self):
        """GET /pilots/execution/fix/venues is explicitly out of scope for
        this gate per the approved plan -- confirm it is unaffected."""
        with mock.patch.object(settings, "FIX_GATEWAY_ENABLED", False), \
                mock.patch.object(settings, "STATE_API_TOKEN", _CMD_TOKEN):
            resp = client.get(
                "/pilots/execution/fix/venues",
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200

    def test_route_allowed_when_enabled(self):
        """Sanity companion: the default (True) must not block anything --
        no regression versus pre-existing behavior."""
        with mock.patch.object(settings, "FIX_GATEWAY_ENABLED", True), \
                mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post(
                "/pilots/execution/fix/route",
                json={"symbol": "MSFT", "side": "BUY", "quantity": 5, "limit_price": 50.0},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200


class TestFixSessionStatusConcurrency:
    """Item 4: GET /pilots/execution/fix/session/status previously read
    FixSession's mutable state (message_log, _incoming_buffer, state,
    sequence numbers, ...) from FastAPI's threadpool with zero locking while
    connect()/disconnect() mutate the same singleton on the main event loop
    under session._lock. The handler is now `async def` and must genuinely
    acquire that same lock for the duration of its reads."""

    @pytest.mark.anyio
    async def test_status_handler_blocks_while_lock_held_externally(self):
        import execution.fix_gateway as fix_gateway_module

        with mock.patch.object(fix_gateway_module, "_global_fix_session", None):
            session = fix_gateway_module.get_global_fix_session()
            await session._lock.acquire()
            try:
                task = asyncio.ensure_future(
                    pilots_api.get_pilots_execution_fix_session_status()
                )
                # Give the handler every chance to run past its lock acquire
                # if it were (incorrectly) not honoring the lock at all.
                await asyncio.sleep(0.05)
                assert not task.done(), (
                    "status handler completed while session._lock was held "
                    "externally -- it is not genuinely acquiring the lock"
                )
            finally:
                session._lock.release()

            result = await asyncio.wait_for(task, timeout=2.0)
            assert result["sender_comp_id"] == "INVESTYO_PWA"
            assert result["target_comp_id"] == "FIX_GATEWAY"

    @pytest.mark.anyio
    async def test_status_handler_concurrent_with_connect_disconnect_no_corruption(self):
        """A real concurrent connect()/disconnect() racing the status read
        must never surface a torn/inconsistent snapshot (e.g. a KeyError, a
        half-updated sequence number, or an exception) -- with the lock in
        place, each of the concurrent operations sees a consistent, fully
        applied state."""
        import execution.fix_gateway as fix_gateway_module

        with mock.patch.object(fix_gateway_module, "_global_fix_session", None):
            session = fix_gateway_module.get_global_fix_session()

            results = await asyncio.gather(
                pilots_api.get_pilots_execution_fix_session_status(),
                session.connect(),
                pilots_api.get_pilots_execution_fix_session_status(),
                return_exceptions=True,
            )

            for r in results:
                assert not isinstance(r, Exception), f"concurrent call raised: {r!r}"

            status_results = [r for r in results if isinstance(r, dict)]
            assert len(status_results) == 2
            for status in status_results:
                assert status["session_id"] == "FIX.4.4:INVESTYO_PWA->FIX_GATEWAY"
                assert isinstance(status["in_seq_num"], int)
                assert isinstance(status["out_seq_num"], int)
                assert status["state"] in {
                    "ACTIVE", "CONNECTING", "LOGON_SENT", "LOGON_RECEIVED",
                    "RESEND_REQUESTED", "GAP_FILL_PROCESSING", "LOGOUT_SENT",
                    "DISCONNECTED", "SUSPENDED",
                }

            await session.disconnect()


class TestFixRouteOrderSymbolValidation:
    """Phase 36 remediation item 19 (audit High): FixRouteOrderRequest.symbol must
    reject FIX tag-injection characters (SOH, '=', '|') rather than silently
    accepting them, since they could inject spurious tag-value pairs into a
    downstream raw FIX message via the Symbol tag (55).
    """

    @pytest.mark.parametrize("bad_symbol", ["AAPL\x0135=D", "AAPL=INJECT", "AAPL|55=XYZ"])
    def test_soh_and_delimiter_symbols_rejected_with_422(self, bad_symbol):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post(
                "/pilots/execution/fix/route",
                json={
                    "symbol": bad_symbol,
                    "side": "BUY",
                    "quantity": 10,
                    "limit_price": 100.0,
                },
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 422

    def test_clean_symbol_accepted(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.post(
                "/pilots/execution/fix/route",
                json={
                    "symbol": "AAPL",
                    "side": "BUY",
                    "quantity": 10,
                    "limit_price": 100.0,
                },
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200

# ---------------------------------------------------------------------------
# vol_mispricing HAS a live paper-execute path as of 2026-08-18 (`POST
# /pilots/options/mispricing/execute`). Like earnings_crush/dispersion_trading/
# zero_dte_engine (each an UNGATEABLE_DATA_GAP), vol_mispricing is a MEASURED
# deployability failure, so ALL FOUR endpoints BLOCK execution by default and
# only proceed when the request explicitly sets override_deployability_gate=True
# (as of 2026-08-29, closing a gap where zero_dte_engine's handler called
# execute_0dte_trade unconditionally with no enforcement check at all -- see
# tests/test_options_desk_deployability_runtime_gap.py for that endpoint's own
# blocked-without-override coverage). See docs/signals/vol_mispricing.md's
# "Live Paper-Execution Status" section and the comment above
# OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"] in api/pilots_api.py.
# ---------------------------------------------------------------------------


def _all_pilots_api_route_paths_and_methods(app) -> set:
    """Recursively collect every (path, method) pair served by *app*,
    unwrapping FastAPI's lazy sub-router mount wrapper -- mirrors
    ``tests/test_control_api.py::_all_route_paths`` / ``tests/test_data_api.py``'s
    equivalent so a mounted sub-router's routes are never silently missed."""
    pairs: set = set()
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path and methods:
            for method in methods:
                pairs.add((path, method))
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            stack.extend(original_router.routes)
    return pairs


def test_vol_mispricing_has_a_paper_execute_endpoint():
    """`POST /pilots/options/mispricing/execute` exists (superseding the prior
    "no execute endpoint" regression guard now that this closes
    docs/VALIDATION_STRATEGY_FIX_LOG.md's follow-up decision to build a
    gated execute path for vol_mispricing rather than leave it
    documentation-only)."""
    pairs = _all_pilots_api_route_paths_and_methods(pilots_api.app)
    assert ("/pilots/options/mispricing/execute", "POST") in pairs


_VOL_MISPRICING_CANDIDATE = {
    "strategy_type": "bull_put_spread",
    "name": "Bull Put Credit Spread ($185.00/$190.00P)",
    "legs": [
        {
            "symbol": "AAPL 2026-09-18 $190.00 PUT",
            "action": "sell",
            "type": "PUT",
            "strike": 190.0,
            "expiration": "2026-09-18",
            "unit_price": 2.50,
        },
        {
            "symbol": "AAPL 2026-09-18 $185.00 PUT",
            "action": "buy",
            "type": "PUT",
            "strike": 185.0,
            "expiration": "2026-09-18",
            "unit_price": 1.00,
        },
    ],
}


class TestVolMispricingExecuteDeployabilityGate:
    """POST /pilots/options/mispricing/execute is blocked-by-default (MEASURED_FAIL
    deployability gate) and only proceeds with an explicit per-request override."""

    def test_blocked_without_override_never_executes_a_trade(self):
        """Without override_deployability_gate, the endpoint refuses -- and never
        even calls execute_vol_mispricing_trade (no PaperAccountStore write)."""
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN), \
             mock.patch.object(settings, "PAPER_BROKER_WRITES_ENABLED", True), \
             mock.patch("pilots.vol_mispricing.execute_vol_mispricing_trade") as mock_exec:
            resp = client.post(
                "/pilots/options/mispricing/execute",
                json={"symbol": "AAPL", "candidate": _VOL_MISPRICING_CANDIDATE},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["blocked"] is True
        mock_exec.assert_not_called()

    def test_override_true_with_dry_run_proceeds_to_dry_run_path(self):
        """override_deployability_gate=True does not block; dry_run=True reaches
        the real execute_vol_mispricing_trade dry-run preview path (no fill)."""
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN), \
             mock.patch.object(settings, "PAPER_BROKER_WRITES_ENABLED", True):
            resp = client.post(
                "/pilots/options/mispricing/execute",
                json={
                    "symbol": "AAPL",
                    "candidate": _VOL_MISPRICING_CANDIDATE,
                    "dry_run": True,
                    "override_deployability_gate": True,
                },
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("blocked") is not True
        assert body["ok"] is True
        assert body["dry_run"] is True
        assert body["override_applied"] is True

    def test_response_always_includes_real_gate_status_blocked(self):
        expected_gate = pilots_api.OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN), \
             mock.patch.object(settings, "PAPER_BROKER_WRITES_ENABLED", True):
            resp = client.post(
                "/pilots/options/mispricing/execute",
                json={"symbol": "AAPL", "candidate": _VOL_MISPRICING_CANDIDATE},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        body = resp.json()
        assert body["gate_status"] == expected_gate
        assert expected_gate["deployable"] is False
        assert expected_gate["gate_status"] == "MEASURED_FAIL"
        assert "-0.499" in expected_gate["reason"]
        assert "0.027" in expected_gate["reason"]

    def test_response_always_includes_real_gate_status_overridden(self):
        expected_gate = pilots_api.OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN), \
             mock.patch.object(settings, "PAPER_BROKER_WRITES_ENABLED", True):
            resp = client.post(
                "/pilots/options/mispricing/execute",
                json={
                    "symbol": "AAPL",
                    "candidate": _VOL_MISPRICING_CANDIDATE,
                    "dry_run": True,
                    "override_deployability_gate": True,
                },
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        body = resp.json()
        assert body["gate_status"] == expected_gate

    def test_fails_closed_when_writes_disabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN), \
             mock.patch.object(settings, "PAPER_BROKER_WRITES_ENABLED", False):
            resp = client.post(
                "/pilots/options/mispricing/execute",
                json={"symbol": "AAPL", "candidate": _VOL_MISPRICING_CANDIDATE},
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 403

    def test_fails_closed_with_wrong_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN), \
             mock.patch.object(settings, "PAPER_BROKER_WRITES_ENABLED", True):
            resp = client.post(
                "/pilots/options/mispricing/execute",
                json={"symbol": "AAPL", "candidate": _VOL_MISPRICING_CANDIDATE},
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401
