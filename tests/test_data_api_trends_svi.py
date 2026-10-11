"""HTTP tests for ``GET /data/trends/terms`` and ``GET /data/trends/svi``.

A readonly in-memory ``TrendsStore`` is a SEPARATE EMPTY database, so the
fixture builds one shared store first and patches the lazily-imported
``data.trends_store.TrendsStore`` to hand that same store back.
"""
import math
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api import data_api
from data.google_trends_client import plan_windows
from data.trends_stitcher import GoogleTrendsStitcher
from data.trends_store import BATCH_COMPLETE, BATCH_PARTIAL, TrendsStore, batch_window_id
from settings import settings

client = TestClient(data_api.app, client=("127.0.0.1", 54123))
FINISHED = datetime(2026, 10, 10, 12, 0, 0)


@pytest.fixture
def store(monkeypatch, tmp_path):
    # File-backed: sync endpoints run in a worker thread, and an in-memory
    # SQLite database is private to the connection that created it.
    shared = TrendsStore(f"sqlite:///{tmp_path / 'trends.db'}")
    monkeypatch.setattr("data.trends_store.TrendsStore", lambda *a, **k: shared)
    monkeypatch.setattr(data_api, "get_universe", lambda: {"effective_symbols": ["AAL", "ARCC"]})
    monkeypatch.setattr(settings, "STATE_API_TOKEN", None)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", True)
    return shared


def _series_windows():
    end = datetime(2026, 10, 1)
    start = end - timedelta(days=365)
    out = []
    for i, (a, b) in enumerate(plan_windows(start, end, 90, 30)):
        idx = pd.date_range(a, b, freq="D")
        raw = 20.0 + 10.0 * np.sin(np.arange(len(idx)) / 9.0 + i)
        out.append(pd.Series(100.0 * raw / raw.max(), index=idx))
    return out


def _fill(store, term, *, batch_id="b1", status=BATCH_COMPLETE, stitch=True):
    series_list = _series_windows()
    windows = [
        (
            batch_window_id(batch_id, s.index[0].date(), s.index[-1].date()),
            [{"date": d.date(), "value": float(v)} for d, v in s.items()],
        )
        for s in series_list
    ]
    store.record_batch(
        batch_id=batch_id, query_term=term,
        started_at=FINISHED - timedelta(minutes=1), finished_at=FINISHED,
        windows_expected=len(windows), windows=windows, status=status,
        reason=None if status == BATCH_COMPLETE else "test",
    )
    if stitch:
        stitched = GoogleTrendsStitcher.stitch_multiple_intervals(series_list)
        store.save_stitched_series(
            term,
            [{"date": d.date(), "value": float(v)} for d, v in stitched.dropna().items()],
            FINISHED, replace=True,
        )


def _only_stitched(store, term):
    store.save_stitched_series(
        term,
        [{"date": (datetime(2026, 1, 1) + timedelta(days=i)).date(), "value": 10.0 + (i % 7)}
         for i in range(120)],
        FINISHED, replace=True,
    )


def _boom(*a, **k):
    raise RuntimeError("cannot open")


def test_fixture_is_wired_endpoint_sees_recorded_batch(store):
    _fill(store, "AAL")
    body = client.get("/data/trends/svi", params={"term": "AAL"}).json()
    assert body["status"] == "ok"
    assert body["term"] == "AAL"
    terms = client.get("/data/trends/terms").json()
    assert [r["term"] for r in terms["terms"]] == ["AAL"]


def test_status_ok(store):
    _fill(store, "AAL")
    body = client.get("/data/trends/svi?term=AAL").json()
    assert body["status"] == "ok"
    assert len(body["raw_windows"]) > 1 and body["stitched"]["days"] > 0
    assert body["asvi"]["latest"] is not None


def test_status_stitched_only(store):
    _only_stitched(store, "AAL")
    assert client.get("/data/trends/svi?term=AAL").json()["status"] == "stitched_only"


def test_status_partial_only(store):
    _fill(store, "ARCC", status=BATCH_PARTIAL, stitch=False)
    assert client.get("/data/trends/svi?term=ARCC").json()["status"] == "partial_only"


def test_status_no_data_is_200_and_disabled(store, monkeypatch):
    resp = client.get("/data/trends/svi?term=ZZZZ")
    assert resp.status_code == 200 and resp.json()["status"] == "no_data"
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", False)
    resp = client.get("/data/trends/svi?term=ZZZZ")
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled" and resp.json()["enabled"] is False


def test_lowercase_term_normalized(store):
    _fill(store, "AAL")
    body = client.get("/data/trends/svi?term=%20aal%20").json()
    assert body["term"] == "AAL" and body["status"] == "ok"


@pytest.mark.parametrize("bad", ["AAL;DROP", "A B", "A/B"])
def test_invalid_term_422(store, bad):
    resp = client.get("/data/trends/svi", params={"term": bad})
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("invalid_term")


def test_too_long_term_422(store):
    assert client.get("/data/trends/svi?term=" + "A" * 13).status_code == 422


def test_store_open_failure_is_200_error(store, monkeypatch):
    monkeypatch.setattr("data.trends_store.TrendsStore", _boom)
    svi = client.get("/data/trends/svi?term=AAL")
    assert svi.status_code == 200
    assert svi.json()["status"] == "error" and svi.json()["term"] == "AAL"
    terms = client.get("/data/trends/terms")
    assert terms.status_code == 200
    body = terms.json()
    assert body["status"] == "error" and body["terms"] == []
    assert body["tracked_universe_size"] is None and body["tracked_with_data"] is None


@pytest.mark.parametrize("url", ["/data/trends/terms", "/data/trends/svi?term=AAL"])
def test_auth(store, monkeypatch, url):
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "secret")
    assert client.get(url, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer secret"}).status_code == 200


def test_terms_coverage_counts(store):
    _fill(store, "AAL")
    _fill(store, "XYZ", batch_id="b2")
    body = client.get("/data/trends/terms").json()
    assert body["status"] == "ok"
    assert body["tracked_universe_size"] == 2
    assert body["tracked_with_data"] == 1
    rows = {r["term"]: r for r in body["terms"]}
    assert rows["AAL"]["in_tracked_universe"] is True
    assert rows["XYZ"]["in_tracked_universe"] is False


def test_terms_coverage_null_when_universe_raises(store, monkeypatch):
    _fill(store, "AAL")
    monkeypatch.setattr(data_api, "get_universe", _boom)
    body = client.get("/data/trends/terms").json()
    assert body["tracked_universe_size"] is None and body["tracked_with_data"] is None
    assert body["terms"][0]["in_tracked_universe"] is None


def test_terms_empty_status(store, monkeypatch):
    assert client.get("/data/trends/terms").json()["status"] == "no_data"
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", False)
    assert client.get("/data/trends/terms").json()["status"] == "disabled"


def test_warmup_asvi_points_are_json_null(store):
    _fill(store, "AAL")
    pts = client.get("/data/trends/svi?term=AAL").json()["asvi"]["points"]
    assert pts[0][1] is None
    assert any(v is not None for _, v in pts)


def test_asvi_latest_equals_production_value(store):
    from pipeline.production_steps import _apply_google_trends_asvi

    _fill(store, "AAL")
    df = pd.DataFrame({"Symbol": ["AAL"]})
    _apply_google_trends_asvi(df)
    production = float(df["Google_Trends_ASVI"].iloc[0])
    assert not math.isnan(production)
    assert client.get("/data/trends/svi?term=AAL").json()["asvi"]["latest"] == production


def test_pytrends_not_imported(store, monkeypatch):
    monkeypatch.delitem(sys.modules, "pytrends", raising=False)
    monkeypatch.delitem(sys.modules, "pytrends.request", raising=False)
    _fill(store, "AAL")
    client.get("/data/trends/terms")
    client.get("/data/trends/svi?term=AAL")
    assert "pytrends" not in sys.modules and "pytrends.request" not in sys.modules
