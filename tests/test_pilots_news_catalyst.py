"""Tests for pilots/news_catalyst.py -- the news-catalyst pilot telemetry
reader powering GET /pilots/{pilot_id}'s ``news_coverage`` field."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from data.historical_store import HistoricalStore
from pilots.news_catalyst import get_news_catalyst_coverage
from signals.news_catalyst import _content_hash


class TestEmptyAndColdStart:
    def test_no_snapshot_no_scores_returns_zeroes_not_none(self, tmp_path, monkeypatch):
        """An empty-but-reachable DB and a missing snapshot file is a valid
        'nothing archived yet' state, not a failure -- must return real
        zero counts, not degrade to None (CONSTRAINT #6: a genuine zero and
        an unreachable-store failure must stay distinguishable)."""
        monkeypatch.chdir(tmp_path)
        db = str(tmp_path / "hist.db")
        HistoricalStore(db_path=db)  # creates tables, no rows

        result = get_news_catalyst_coverage(
            store=HistoricalStore(db_path=db),
            snapshot_path=tmp_path / "output" / "state_snapshot.json",
        )

        assert result == {
            "archived_score_count": 0,
            "headline_volume_7d": 0,
            "universe_score_distribution": {},
        }

    def test_store_construction_failure_degrades_to_none(self, monkeypatch, tmp_path):
        """CONSTRAINT #6: a DB-unreachable failure must never raise -- and
        must degrade to None (distinguishable from a genuine empty-store
        zero above), not a fabricated zero that hides a real outage."""
        import pilots.news_catalyst as mod

        def _boom(*args, **kwargs):
            raise RuntimeError("db unreachable")

        monkeypatch.setattr(mod, "HistoricalStore", _boom)
        result = get_news_catalyst_coverage(snapshot_path=tmp_path / "missing.json")
        assert result is None


class TestRealData:
    def test_counts_reflect_real_rows(self, tmp_path):
        db = str(tmp_path / "hist2.db")
        store = HistoricalStore(db_path=db)

        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)
        recent = now - timedelta(days=1)

        # Two rows total; only one falls inside the 7-day window.
        store.save_finbert_scores(
            {_content_hash("old headline"): {"positive": 0.3, "neutral": 0.4, "negative": 0.3}}
        )
        # save_finbert_scores always stamps "now" -- backdate the "old" row
        # directly so the 7-day-volume query has something to actually filter.
        import sqlite3

        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE finbert_score_cache SET scored_at = ? WHERE content_hash = ?",
                (old.isoformat(), _content_hash("old headline")),
            )
        store.save_finbert_scores(
            {_content_hash("recent headline"): {"positive": 0.6, "neutral": 0.3, "negative": 0.1}}
        )
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE finbert_score_cache SET scored_at = ? WHERE content_hash = ?",
                (recent.isoformat(), _content_hash("recent headline")),
            )

        snapshot_path = tmp_path / "state_snapshot.json"
        snapshot_path.write_text(
            json.dumps(
                {
                    "signal_breakdown": [
                        {"name": "news_catalyst", "distribution": {"positive": 3, "neutral": 5, "negative": 2}},
                        {"name": "other_signal", "distribution": {"ignored": 1}},
                    ]
                }
            )
        )

        result = get_news_catalyst_coverage(
            store=HistoricalStore(db_path=db, readonly=True),
            snapshot_path=snapshot_path,
        )

        assert result["archived_score_count"] == 2
        assert result["headline_volume_7d"] == 1
        assert result["universe_score_distribution"] == {"positive": 3, "neutral": 5, "negative": 2}

    def test_malformed_snapshot_degrades_distribution_to_empty(self, tmp_path):
        db = str(tmp_path / "hist3.db")
        HistoricalStore(db_path=db)

        snapshot_path = tmp_path / "state_snapshot.json"
        snapshot_path.write_text("{not valid json")

        result = get_news_catalyst_coverage(
            store=HistoricalStore(db_path=db, readonly=True),
            snapshot_path=snapshot_path,
        )

        assert result is not None
        assert result["universe_score_distribution"] == {}
        assert result["archived_score_count"] == 0
