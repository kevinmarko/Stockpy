"""Tests for ``OrchestratorDaemon.maybe_refresh_google_trends``.

The daemon runs against a real in-memory ``TrendsStore`` (one instance shared
by every ``TrendsStore()`` call in a test), with the network fetch and the
universe resolver patched. Settings are patched through ``monkeypatch`` so the
real ``settings.settings`` singleton is restored after each test.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from data.google_trends_client import plan_windows
from data.trends_store import (
    BATCH_COMPLETE,
    BATCH_FAILED,
    BATCH_PARTIAL,
    TrendsStore,
)
from desktop.daemon_runtime import OrchestratorDaemon, _google_trends_due_terms
from settings import settings


@pytest.fixture
def trends_settings(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", True)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS", 24.0)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_WINDOW_DAYS", 90)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_OVERLAP_DAYS", 30)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_MAX_TERMS_PER_PASS", 10)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_RAW_RETENTION_BATCHES", 14)


@pytest.fixture
def store(monkeypatch):
    shared = TrendsStore()
    monkeypatch.setattr("data.trends_store.TrendsStore", lambda *a, **k: shared)
    return shared


def _windows(start_date, end_date, window_days=90, overlap_days=30, **_):
    """What a successful fetch returns: one series per planned window, each
    normalized so its own max is 100 (like Google's)."""
    out = []
    planned = plan_windows(
        datetime.strptime(start_date, "%Y-%m-%d"),
        datetime.strptime(end_date, "%Y-%m-%d"),
        window_days, overlap_days,
    )
    for i, (a, b) in enumerate(planned):
        idx = pd.date_range(a, b, freq="D")
        raw = 20.0 + 10.0 * np.sin(np.arange(len(idx)) / 9.0 + i)
        out.append(pd.Series(100.0 * raw / raw.max(), index=idx))
    return out


def _complete_fetch(term, start_date, end_date, **kw):
    return _windows(start_date, end_date, **kw)


def _daemon(universe):
    d = OrchestratorDaemon()
    d._google_trends_universe = lambda: list(universe)
    return d


def test_disabled_never_fetches(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", False)
    with patch("data.google_trends_client.fetch_overlapping_windows") as fetch:
        _daemon(["AAL"]).maybe_refresh_google_trends()
    fetch.assert_not_called()


def test_complete_pass_records_one_batch_per_term_and_stitches(trends_settings, store):
    with patch("data.google_trends_client.fetch_overlapping_windows", side_effect=_complete_fetch) as fetch:
        _daemon(["AAL", "ABR"]).maybe_refresh_google_trends()

    assert fetch.call_count == 2
    # The window settings are passed through (they used to be ignored).
    for call in fetch.call_args_list:
        assert call.kwargs == {"window_days": 90, "overlap_days": 30}

    for term in ("AAL", "ABR"):
        batch = store.latest_batch(term)
        assert batch["status"] == BATCH_COMPLETE
        assert batch["windows_expected"] == batch["windows_fetched"] == 6
        rows = store.load_batch_windows(batch["batch_id"])
        assert len({r.window_id for r in rows}) == 6
        stitched = store.get_stitched_series(term)
        assert stitched
        assert stitched[-1]["date"] <= datetime.now(timezone.utc).date()


def test_schedule_survives_a_daemon_restart(trends_settings, store):
    """The old throttle lived in process memory, so every restart refetched."""
    with patch("data.google_trends_client.fetch_overlapping_windows", side_effect=_complete_fetch):
        _daemon(["AAL"]).maybe_refresh_google_trends()

    with patch("data.google_trends_client.fetch_overlapping_windows") as fetch:
        _daemon(["AAL"]).maybe_refresh_google_trends()  # a brand-new daemon instance
    fetch.assert_not_called()


def test_restitch_replaces_old_orphan_days(trends_settings, store):
    old_day = (datetime.now(timezone.utc) - timedelta(days=500)).date()
    store.save_stitched_series("AAL", [{"date": old_day, "value": 12.0}], datetime(2025, 1, 1))
    with patch("data.google_trends_client.fetch_overlapping_windows", side_effect=_complete_fetch):
        _daemon(["AAL"]).maybe_refresh_google_trends()
    assert old_day not in {r["date"] for r in store.get_stitched_series("AAL")}


def test_per_pass_cap_takes_stalest_terms_first(trends_settings, store, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_MAX_TERMS_PER_PASS", 2)
    universe = ["AAA", "BBB", "CCC", "DDD"]
    with patch("data.google_trends_client.fetch_overlapping_windows", side_effect=_complete_fetch) as fetch:
        _daemon(universe).maybe_refresh_google_trends()
        assert [c.args[0] for c in fetch.call_args_list] == ["AAA", "BBB"]
        fetch.reset_mock()
        _daemon(universe).maybe_refresh_google_trends()
        assert [c.args[0] for c in fetch.call_args_list] == ["CCC", "DDD"]


def test_cooldown_stops_the_pass_and_leaves_the_rest_due(trends_settings, store):
    # Closed before AAL's fetch, opened by it (the 429s happen inside the fetch).
    with patch("data.google_trends_client.fetch_overlapping_windows", return_value=[]) as fetch, \
         patch("data.google_trends_client.is_in_cooldown", side_effect=[False, True]):
        _daemon(["AAL", "ABR"]).maybe_refresh_google_trends()
    assert [c.args[0] for c in fetch.call_args_list] == ["AAL"]
    failed = store.latest_batch("AAL", complete_only=False)
    assert failed["status"] == BATCH_FAILED
    assert failed["reason"] == "rate-limit cooldown"
    assert store.latest_batch("ABR", complete_only=False) is None

    # Next wake: AAL was just attempted, ABR was never reached and is still due.
    with patch("data.google_trends_client.fetch_overlapping_windows", side_effect=_complete_fetch) as fetch:
        _daemon(["AAL", "ABR"]).maybe_refresh_google_trends()
    assert [c.args[0] for c in fetch.call_args_list] == ["ABR"]


def test_partial_batch_is_recorded_but_not_stitched(trends_settings, store):
    seeded = [{"date": datetime(2026, 1, 1).date(), "value": 33.0}]
    store.save_stitched_series("AAL", seeded, datetime(2026, 1, 2))

    def half(term, start_date, end_date, **kw):
        return _windows(start_date, end_date, **kw)[:3]

    with patch("data.google_trends_client.fetch_overlapping_windows", side_effect=half), \
         patch("data.google_trends_client.is_in_cooldown", return_value=False):
        _daemon(["AAL"]).maybe_refresh_google_trends()

    batch = store.latest_batch("AAL", complete_only=False)
    assert batch["status"] == BATCH_PARTIAL
    assert batch["reason"] == "3 of 6 windows returned"
    assert store.latest_batch("AAL") is None
    assert store.get_stitched_series("AAL") == seeded


def test_one_failing_symbol_does_not_abort_the_pass(trends_settings, store):
    def fetch(term, start_date, end_date, **kw):
        if term == "BAD":
            raise RuntimeError("boom for BAD")
        return _windows(start_date, end_date, **kw)

    with patch("data.google_trends_client.fetch_overlapping_windows", side_effect=fetch) as f:
        _daemon(["BAD", "GOOD"]).maybe_refresh_google_trends()
    assert f.call_count == 2
    assert store.latest_batch("GOOD")["status"] == BATCH_COMPLETE
    assert store.get_stitched_series("BAD") == []

    # The error is recorded as a failed attempt, so BAD is not retried on
    # the next wake (it used to be: the old test pinned an in-memory throttle
    # for exactly this reason).
    bad = store.latest_batch("BAD", complete_only=False)
    assert bad["status"] == BATCH_FAILED
    assert "boom for BAD" in bad["reason"]
    with patch("data.google_trends_client.fetch_overlapping_windows") as f2:
        _daemon(["BAD", "GOOD"]).maybe_refresh_google_trends()
    f2.assert_not_called()


def test_retention_prunes_to_k_complete_batches(trends_settings, store, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_RAW_RETENTION_BATCHES", 2)
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS", 0.0)
    with patch("data.google_trends_client.fetch_overlapping_windows", side_effect=_complete_fetch):
        for _ in range(4):
            _daemon(["AAL"]).maybe_refresh_google_trends()
    batch_ids = {r.window_id.split("/")[0] for r in store.load_raw_windows("AAL")}
    assert len(batch_ids) == 2


def test_universe_failure_degrades_to_no_pass(trends_settings, store):
    d = OrchestratorDaemon()
    with patch("data.portfolio_sync.compute_tracked_universe", side_effect=RuntimeError("x")), \
         patch("data.google_trends_client.fetch_overlapping_windows") as fetch:
        d.maybe_refresh_google_trends()
    fetch.assert_not_called()


def test_universe_uses_compute_tracked_universe_with_default_fallback(monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_TICKERS", ["AAPL", "MSFT"])
    d = OrchestratorDaemon()
    with patch("data.portfolio_sync.compute_tracked_universe", return_value=["ZZZ"]) as ctu:
        assert d._google_trends_universe() == ["ZZZ"]
    assert ctu.call_args.kwargs["default_tickers"] == ["AAPL", "MSFT"]


# ── _google_trends_due_terms (pure) ──────────────────────────────────────────

NOW = datetime(2026, 10, 11, 12, 0)


def test_due_terms_never_attempted_first_then_oldest_attempt():
    last = {
        "OLD": {"attempt": NOW - timedelta(days=3), "complete": NOW - timedelta(days=3)},
        "OLDER": {"attempt": NOW - timedelta(days=5), "complete": None},
        "FRESH": {"attempt": NOW - timedelta(hours=2), "complete": NOW - timedelta(hours=2)},
    }
    got = _google_trends_due_terms(["OLD", "FRESH", "NEW", "OLDER"], last, NOW, interval_hours=24, max_terms=10)
    assert got == ["NEW", "OLDER", "OLD"]


def test_due_terms_waits_the_interval_after_a_failed_attempt():
    """A term Google has no data for must not be retried on every wake."""
    last = {"DEAD": {"attempt": NOW - timedelta(hours=23), "complete": None}}
    assert _google_trends_due_terms(["DEAD"], last, NOW, interval_hours=24, max_terms=10) == []
    last = {"DEAD": {"attempt": NOW - timedelta(hours=25), "complete": None}}
    assert _google_trends_due_terms(["DEAD"], last, NOW, interval_hours=24, max_terms=10) == ["DEAD"]


def test_due_terms_cap_is_at_least_one():
    got = _google_trends_due_terms(["A", "B"], {}, NOW, interval_hours=24, max_terms=0)
    assert got == ["A"]


def test_cooldown_open_before_the_pass_records_nothing(trends_settings, store):
    """A cooldown left open by an earlier pass must not burn a term that was
    never actually requested."""
    with patch("data.google_trends_client.fetch_overlapping_windows") as fetch, \
         patch("data.google_trends_client.is_in_cooldown", return_value=True):
        _daemon(["AAL", "ABR"]).maybe_refresh_google_trends()
    fetch.assert_not_called()
    assert store.last_attempts() == {}


def test_stitch_failure_marks_batch_failed_and_keeps_series(trends_settings, store):
    """A batch is only marked complete once its stitch succeeded."""
    seeded = [{"date": datetime(2026, 1, 1).date(), "value": 33.0}]
    store.save_stitched_series("AAL", seeded, datetime(2026, 1, 2))
    with patch("data.google_trends_client.fetch_overlapping_windows", side_effect=_complete_fetch), \
         patch("data.trends_stitcher.GoogleTrendsStitcher.stitch_multiple_intervals",
               side_effect=ValueError("No overlapping dates")):
        _daemon(["AAL"]).maybe_refresh_google_trends()
    batch = store.latest_batch("AAL", complete_only=False)
    assert batch["status"] == BATCH_FAILED
    assert batch["reason"].startswith("stitch failed: ValueError")
    assert store.latest_batch("AAL") is None
    assert store.get_stitched_series("AAL") == seeded


def test_no_planned_windows_gives_an_accurate_reason(trends_settings, store, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_WINDOW_DAYS", 30)
    with patch("data.google_trends_client.fetch_overlapping_windows", return_value=[]), \
         patch("data.google_trends_client.is_in_cooldown", return_value=False):
        _daemon(["AAL"]).maybe_refresh_google_trends()
    assert "no windows planned" in store.latest_batch("AAL", complete_only=False)["reason"]
