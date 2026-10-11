"""Tests for ``data/trends_view.py``, the Google Trends viewer's view builders.

Real in-memory ``TrendsStore`` (the root conftest redirects the default DB),
filled the way the daemon fills it: ``record_batch`` with ids from
``batch_window_id``, then ``save_stitched_series(..., replace=True)`` from a
real ``stitch_multiple_intervals``.
"""
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from data import trends_view
from data.google_trends_client import plan_windows
from data.trends_stitcher import ASVICalculator, GoogleTrendsStitcher
from data.trends_store import (
    BATCH_COMPLETE,
    BATCH_FAILED,
    BATCH_PARTIAL,
    TrendsStore,
    batch_window_id,
)
from data.trends_view import (
    ASVI_LOOKBACK_DAYS,
    STALE_INTERVALS,
    TERM_RE,
    build_svi_view,
    build_terms_view,
    config_echo,
    empty_svi,
)
from settings import settings

NOW = datetime(2026, 10, 11, 12, 0, 0)
FINISHED = datetime(2026, 10, 10, 12, 0, 0)  # 24h before NOW


@pytest.fixture(autouse=True)
def trends_settings(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", True)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS", 24.0)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_WINDOW_DAYS", 90)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_OVERLAP_DAYS", 30)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_MAX_TERMS_PER_PASS", 10)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_RAW_RETENTION_BATCHES", 14)


def _series_windows(phase=0.0):
    """Windows over 365 days (90/30), each normalized so its own max is 100."""
    end = datetime(2026, 10, 1)
    start = end - timedelta(days=365)
    out = []
    for i, (a, b) in enumerate(plan_windows(start, end, 90, 30)):
        idx = pd.date_range(a, b, freq="D")
        raw = 20.0 + 10.0 * np.sin(np.arange(len(idx)) / 9.0 + i + phase)
        out.append(pd.Series(100.0 * raw / raw.max(), index=idx))
    return out


def _fill(store, term, series_list=None, *, batch_id="b1", finished=FINISHED,
          status=BATCH_COMPLETE, stitch=True, started=None):
    """Store a batch (and, for a complete one, its stitched series)."""
    series_list = series_list if series_list is not None else _series_windows()
    windows = [
        (
            batch_window_id(batch_id, s.index[0].date(), s.index[-1].date()),
            [{"date": d.date(), "value": float(v)} for d, v in s.items()],
        )
        for s in series_list
    ]
    store.record_batch(
        batch_id=batch_id, query_term=term,
        started_at=started or finished - timedelta(minutes=1), finished_at=finished,
        windows_expected=len(windows), windows=windows, status=status,
        reason=None if status == BATCH_COMPLETE else "test",
    )
    if stitch:
        stitched = GoogleTrendsStitcher.stitch_multiple_intervals(series_list)
        store.save_stitched_series(
            term,
            [{"date": d.date(), "value": float(v)} for d, v in stitched.dropna().items()],
            finished, replace=True,
        )
    return series_list


def _only_stitched(store, term, values=None, at=FINISHED, start=datetime(2026, 1, 1)):
    values = values if values is not None else [10.0 + (i % 7) for i in range(120)]
    store.save_stitched_series(
        term,
        [{"date": (start + timedelta(days=i)).date(), "value": float(v)} for i, v in enumerate(values)],
        at, replace=True,
    )


# ── module constants / config ────────────────────────────────────────────────


def test_constants():
    assert TERM_RE.match("BRK.B") and TERM_RE.match("A-1")
    assert not TERM_RE.match("aal") and not TERM_RE.match("") and not TERM_RE.match("A" * 13)
    assert STALE_INTERVALS == 2.0
    assert ASVI_LOOKBACK_DAYS == 84


def test_config_echo_reads_settings(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS", 6.0)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", False)
    cfg = config_echo()
    assert cfg == {
        "enabled": False, "window_days": 90, "overlap_days": 30,
        "refresh_interval_hours": 6.0, "max_terms_per_pass": 10,
        "retention_batches": 14, "stale_after_hours": 12.0, "asvi_lookback_days": 84,
    }


def test_empty_svi_shape():
    out = empty_svi("AAL", "no_data", "why")
    assert out["term"] == "AAL" and out["status"] == "no_data" and out["reason"] == "why"
    assert out["raw_windows"] == [] and out["seams"] == [] and out["rescaled_windows"] == []
    for key in ("batch", "latest_attempt", "stitched", "recompute_check", "asvi"):
        assert out[key] is None
    assert set(config_echo()) <= set(out)


# ── SVI view: status branches ────────────────────────────────────────────────


def test_status_ok_full_payload():
    store = TrendsStore()
    series = _fill(store, "AAL")
    out = build_svi_view(store, "AAL", now=NOW)

    assert out["status"] == "ok" and out["reason"] is None
    assert out["batch"]["batch_id"] == "b1"
    assert out["batch"]["status"] == "complete"
    assert out["batch"]["age_hours"] == 24.0 and out["batch"]["stale"] is False
    assert out["batch"]["finished_at"] == "2026-10-10T12:00:00Z"
    assert out["latest_attempt"]["batch_id"] == "b1"
    assert len(out["raw_windows"]) == len(series)
    assert len(out["seams"]) == len(series) - 1
    assert [w["index"] for w in out["rescaled_windows"]] == list(range(len(series)))
    assert out["stitched"]["from_latest_batch"] is True
    assert out["stitched"]["days"] == len(out["stitched"]["points"])
    assert out["recompute_check"]["matches"] is True
    assert out["asvi"]["lookback_days"] == 84
    w0 = out["raw_windows"][0]
    assert w0["peak_value"] == 100.0
    assert w0["start"] == series[0].index[0].date().isoformat()
    # Points are [epoch_ms at UTC midnight, value].
    ts, val = w0["points"][0]
    assert ts == int(series[0].index[0].timestamp() * 1000) and val == series[0].iloc[0]


def test_status_stitched_only_when_no_complete_batch():
    store = TrendsStore()
    _only_stitched(store, "AAL")
    out = build_svi_view(store, "AAL", now=NOW)
    assert out["status"] == "stitched_only" and out["reason"]
    assert out["batch"] is None and out["latest_attempt"] is None
    assert out["stitched"]["days"] == 120 and out["stitched"]["from_latest_batch"] is False
    assert out["asvi"] is not None
    assert out["raw_windows"] == [] and out["seams"] == [] and out["recompute_check"] is None


def test_status_partial_only():
    store = TrendsStore()
    _fill(store, "ARCC", status=BATCH_PARTIAL, stitch=False)
    out = build_svi_view(store, "ARCC", now=NOW)
    assert out["status"] == "partial_only" and out["reason"]
    assert out["batch"] is None
    assert out["latest_attempt"]["status"] == "partial"
    assert out["stitched"] is None and out["asvi"] is None


def test_failed_batch_with_no_series_is_partial_only():
    store = TrendsStore()
    _fill(store, "DEAD", [], status=BATCH_FAILED, stitch=False)
    assert build_svi_view(store, "DEAD", now=NOW)["status"] == "partial_only"


def test_status_no_data_and_disabled(monkeypatch):
    store = TrendsStore()
    assert build_svi_view(store, "ZZZZ", now=NOW)["status"] == "no_data"
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", False)
    out = build_svi_view(store, "ZZZZ", now=NOW)
    assert out["status"] == "disabled" and out["enabled"] is False


def test_stored_data_still_returned_when_disabled(monkeypatch):
    store = TrendsStore()
    _fill(store, "AAL")
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", False)
    out = build_svi_view(store, "AAL", now=NOW)
    assert out["status"] == "ok" and out["enabled"] is False


def test_status_error_names_exception_type_only():
    class Boom:
        def latest_batch(self, *a, **k):
            raise RuntimeError("secret detail")

    out = build_svi_view(Boom(), "AAL", now=NOW)
    assert out["status"] == "error"
    assert out["reason"] == "RuntimeError"
    assert "secret" not in str(out["reason"])
    assert out["seams"] == [] and out["stitched"] is None


def test_complete_batch_without_raw_rows_is_error():
    store = TrendsStore()
    store.record_batch(
        batch_id="empty", query_term="AAL", started_at=FINISHED, finished_at=FINISHED,
        windows_expected=3, windows=[], status=BATCH_COMPLETE,
    )
    out = build_svi_view(store, "AAL", now=NOW)
    assert out["status"] == "error" and out["reason"]


def test_stale_batch_flag():
    store = TrendsStore()
    _fill(store, "AAL", finished=NOW - timedelta(hours=49))
    out = build_svi_view(store, "AAL", now=NOW)
    assert out["batch"]["age_hours"] == 49.0 and out["batch"]["stale"] is True


def test_from_latest_batch_false_when_stitched_stamp_differs():
    store = TrendsStore()
    series = _fill(store, "AAL", stitch=False)
    stitched = GoogleTrendsStitcher.stitch_multiple_intervals(series)
    store.save_stitched_series(
        "AAL", [{"date": d.date(), "value": float(v)} for d, v in stitched.items()],
        FINISHED - timedelta(days=3), replace=True,
    )
    assert build_svi_view(store, "AAL", now=NOW)["stitched"]["from_latest_batch"] is False


# ── Seams ────────────────────────────────────────────────────────────────────


def test_seam_applied_factors_match_the_stitcher_including_chained_seam():
    store = TrendsStore()
    w = _fill(store, "AAL")
    out = build_svi_view(store, "AAL", now=NOW)
    stitcher = GoogleTrendsStitcher

    # Seam 1: window 0 against window 1.
    s1 = out["seams"][0]
    assert (s1["index"], s1["a_window"], s1["b_window"]) == (1, 0, 1)
    assert s1["applied_factor"] == stitcher.get_scaling_metadata(w[0], w[1])["f"]
    # With only one earlier window, applied and pair factors coincide.
    assert s1["pair_factor"] == pytest.approx(s1["applied_factor"], rel=1e-12)

    # Seam 2: the CHAINED stitch so far against window 2.
    chained = stitcher.stitch_intervals(w[0], w[1])
    s2 = out["seams"][1]
    assert (s2["index"], s2["a_window"], s2["b_window"]) == (2, 1, 2)
    assert s2["applied_factor"] == stitcher.get_scaling_metadata(chained, w[2])["f"]
    # The raw neighbour ratio is a different number than the chained factor.
    raw_pair = float(w[1].loc[w[1].index.intersection(w[2].index)].sum()) / float(
        w[2].loc[w[1].index.intersection(w[2].index)].sum()
    )
    assert s2["pair_factor"] == pytest.approx(raw_pair, rel=1e-12)
    assert s2["applied_factor"] != pytest.approx(s2["pair_factor"], rel=1e-9)

    meta = stitcher.get_scaling_metadata(chained, w[2])
    assert s2["overlap_days"] == len(meta["overlap_dates"]) == 31
    assert s2["overlap_start"] == meta["overlap_dates"][0].date().isoformat()
    assert s2["sum_a"] == pytest.approx(float(chained.loc[meta["overlap_dates"]].sum()))
    assert s2["sum_b"] == pytest.approx(float(w[2].loc[meta["overlap_dates"]].sum()))
    assert s2["degenerate_guard"] is False

    # Rescaled windows: window k is scaled by its applied factor.
    resc = {r["index"]: r["points"] for r in out["rescaled_windows"]}
    assert resc[0][0][1] == w[0].iloc[0]
    assert resc[2][0][1] == pytest.approx(w[2].iloc[0] * s2["applied_factor"])


def test_degenerate_seam_is_passthrough_and_flagged():
    store = TrendsStore()
    series = _series_windows()
    ov = series[0].index.intersection(series[1].index)
    series[1] = series[1].copy()
    series[1].loc[ov] = 0.0  # the whole shared span is zero in window 1
    _fill(store, "AQN", series)
    out = build_svi_view(store, "AQN", now=NOW)

    s1 = out["seams"][0]
    assert s1["applied_factor"] == 1.0
    assert s1["pair_factor"] is None
    assert s1["degenerate_guard"] is True
    assert s1["sum_b"] == 0.0  # a real measured zero, not a placeholder
    assert out["status"] == "ok"
    assert out["seams"][1]["degenerate_guard"] is False


# ── Recompute check ──────────────────────────────────────────────────────────


def test_recompute_matches_for_faithful_stitch():
    store = TrendsStore()
    _fill(store, "AAL")
    chk = build_svi_view(store, "AAL", now=NOW)["recompute_check"]
    assert chk["matches"] is True and chk["same_dates"] is True
    assert chk["max_abs_diff"] == 0.0
    assert chk["compared_days"] > 300


def test_recompute_detects_tampered_value():
    store = TrendsStore()
    _fill(store, "AAL")
    rows = store.get_stitched_rows("AAL")
    rows[100]["value"] += 2.5
    store.save_stitched_series(
        "AAL", [{"date": r["date"], "value": r["value"]} for r in rows], FINISHED, replace=True
    )
    chk = build_svi_view(store, "AAL", now=NOW)["recompute_check"]
    assert chk["matches"] is False
    assert chk["same_dates"] is True
    assert chk["max_abs_diff"] == pytest.approx(2.5)


def test_recompute_flags_date_mismatch():
    store = TrendsStore()
    _fill(store, "AAL")
    rows = store.get_stitched_rows("AAL")[:-5]
    store.save_stitched_series(
        "AAL", [{"date": r["date"], "value": r["value"]} for r in rows], FINISHED, replace=True
    )
    chk = build_svi_view(store, "AAL", now=NOW)["recompute_check"]
    assert chk["same_dates"] is False and chk["matches"] is False
    assert chk["max_abs_diff"] == 0.0


def test_complete_batch_without_stitched_rows_has_no_recompute():
    store = TrendsStore()
    _fill(store, "AAL", stitch=False)
    out = build_svi_view(store, "AAL", now=NOW)
    assert out["status"] == "ok"
    assert out["stitched"] is None and out["recompute_check"] is None and out["asvi"] is None


# ── ASVI ─────────────────────────────────────────────────────────────────────


def test_asvi_latest_equals_production_step_value_exactly(monkeypatch):
    from pipeline.production_steps import _apply_google_trends_asvi

    store = TrendsStore()
    _fill(store, "AAL")
    monkeypatch.setattr("data.trends_store.TrendsStore", lambda *a, **k: store)

    df = pd.DataFrame({"Symbol": ["AAL"]})
    _apply_google_trends_asvi(df)
    production = float(df["Google_Trends_ASVI"].iloc[0])
    assert not math.isnan(production)

    asvi = build_svi_view(store, "AAL", now=NOW)["asvi"]
    assert asvi["latest"] == production
    assert asvi["latest_date"] == store.get_stitched_rows("AAL")[-1]["date"].isoformat()


def test_asvi_warmup_points_are_none_not_zero():
    store = TrendsStore()
    _fill(store, "AAL")
    asvi = build_svi_view(store, "AAL", now=NOW)["asvi"]
    assert asvi["points"][0][1] is None
    assert all(p[1] is None for p in asvi["points"][:7])
    assert asvi["points"][7][1] is not None
    assert len(asvi["points"]) == build_svi_view(store, "AAL", now=NOW)["stitched"]["days"]


def test_asvi_all_warmup_gives_null_latest():
    store = TrendsStore()
    _only_stitched(store, "AAL", [10.0] * 5)
    asvi = build_svi_view(store, "AAL", now=NOW)["asvi"]
    assert asvi["latest"] is None and asvi["latest_date"] is None
    assert all(p[1] is None for p in asvi["points"])


# ── Terms view ───────────────────────────────────────────────────────────────


def test_terms_view_rows_stale_flag_and_coverage():
    store = TrendsStore()
    _fill(store, "AAL", batch_id="a1")                                       # fresh, complete
    _fill(store, "ABR", batch_id="r1", finished=NOW - timedelta(hours=60))   # stale (> 48h)
    _fill(store, "ARCC", batch_id="p1", status=BATCH_PARTIAL, stitch=False)  # no data
    _only_stitched(store, "AGNC")                                            # stitched only

    out = build_terms_view(store, ["aal", "ABR", "ARCC", "MSFT"], now=NOW)
    rows = {r["term"]: r for r in out["terms"]}

    assert out["status"] == "ok" and out["reason"] is None
    assert [r["term"] for r in out["terms"]] == sorted(rows)
    assert set(rows) == {"AAL", "ABR", "ARCC", "AGNC"}
    assert out["tracked_universe_size"] == 4
    assert out["tracked_with_data"] == 2  # AAL + ABR; ARCC has no stitched days, MSFT unknown

    aal = rows["AAL"]
    assert aal["in_tracked_universe"] is True
    assert aal["has_complete_batch"] is True
    assert aal["age_hours"] == 24.0 and aal["stale"] is False
    assert aal["last_attempt_status"] == "complete"
    assert aal["last_complete_at"] == "2026-10-10T12:00:00Z"
    assert aal["stitched_days"] > 300
    assert aal["latest_asvi"] is not None and aal["latest_asvi_date"] == aal["last_date"]

    assert rows["ABR"]["stale"] is True and rows["ABR"]["age_hours"] == 60.0

    arcc = rows["ARCC"]
    assert arcc["has_complete_batch"] is False and arcc["last_complete_at"] is None
    assert arcc["last_attempt_status"] == "partial" and arcc["last_attempt_reason"] == "test"
    assert arcc["stitched_days"] == 0 and arcc["last_date"] is None
    assert arcc["latest_asvi"] is None and arcc["age_hours"] is None and arcc["stale"] is None

    agnc = rows["AGNC"]
    assert agnc["in_tracked_universe"] is False
    assert agnc["has_complete_batch"] is False
    assert agnc["age_hours"] == 24.0  # falls back to stitched_at
    assert agnc["stitched_days"] == 120


def test_terms_view_coverage_none_when_universe_unknown():
    store = TrendsStore()
    _fill(store, "AAL")
    out = build_terms_view(store, None, now=NOW)
    assert out["tracked_universe_size"] is None and out["tracked_with_data"] is None
    assert out["terms"][0]["in_tracked_universe"] is None
    assert out["status"] == "ok"


def test_terms_view_empty_is_no_data_or_disabled(monkeypatch):
    store = TrendsStore()
    out = build_terms_view(store, ["AAL"], now=NOW)
    assert out["status"] == "no_data" and out["terms"] == []
    assert out["tracked_universe_size"] == 1 and out["tracked_with_data"] == 0

    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", False)
    out = build_terms_view(store, [], now=NOW)
    assert out["status"] == "disabled" and out["enabled"] is False
    assert out["tracked_universe_size"] == 0


def test_terms_view_error_names_type_only():
    class Boom:
        def latest_batches(self):
            raise ValueError("private message")

    out = build_terms_view(Boom(), ["AAL"], now=NOW)
    assert out["status"] == "error" and out["reason"] == "ValueError"
    assert out["terms"] == [] and out["tracked_universe_size"] is None


def test_default_now_is_used_when_omitted():
    store = TrendsStore()
    _fill(store, "AAL", finished=datetime(2020, 1, 1))
    out = build_terms_view(store, None)
    assert out["terms"][0]["stale"] is True


def test_module_does_not_import_api_or_pytrends():
    import ast
    import inspect as _inspect

    tree = ast.parse(_inspect.getsource(trends_view))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert "api" not in mods and "pytrends" not in mods
