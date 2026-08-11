"""Tests for pilots/news_catalyst.py -- News Catalyst Pilot telemetry helper."""
import json
from datetime import datetime
from unittest import mock

from pilots import news_catalyst


class _FakeStore:
    def __init__(self, *, total=0, since_counts=None, raise_on=None):
        self._total = total
        self._since_counts = since_counts or {}
        self._raise_on = raise_on

    def count_finbert_scores(self, since=None):
        if self._raise_on == "count":
            raise RuntimeError("db unreachable")
        if since is None:
            return self._total
        return self._since_counts.get(since, 0)


class TestGetNewsCatalystCoverage:
    def test_no_snapshot_file_returns_zero_counts_and_empty_distribution(self, tmp_path):
        missing = tmp_path / "state_snapshot.json"
        with mock.patch(
            "data.historical_store.HistoricalStore",
            return_value=_FakeStore(total=5, since_counts={}),
        ):
            result = news_catalyst.get_news_catalyst_coverage(snapshot_path=str(missing))
        assert result == {
            "archived_score_count": 5,
            "headline_volume_7d": 0,
            "universe_score_distribution": {"positive": 0, "neutral": 0, "negative": 0},
        }

    def test_real_snapshot_buckets_positive_neutral_negative(self, tmp_path):
        snapshot_path = tmp_path / "state_snapshot.json"
        snapshot_path.write_text(
            json.dumps(
                {
                    "signals": [
                        {"symbol": "AAPL", "news_sentiment": 0.42},
                        {"symbol": "MSFT", "news_sentiment": -0.31},
                        {"symbol": "TSLA", "news_sentiment": 0.01},
                        {"symbol": "GME", "news_sentiment": None},  # excluded, not neutral
                        {"symbol": "AMD"},  # missing key entirely -- excluded
                    ]
                }
            ),
            encoding="utf-8",
        )
        with mock.patch(
            "data.historical_store.HistoricalStore",
            return_value=_FakeStore(total=100, since_counts={}),
        ):
            result = news_catalyst.get_news_catalyst_coverage(snapshot_path=str(snapshot_path))
        assert result["universe_score_distribution"] == {"positive": 1, "neutral": 1, "negative": 1}
        # Only 3 of the 5 rows had a usable score -- the other two must not be
        # silently counted as neutral (CONSTRAINT #4).
        assert sum(result["universe_score_distribution"].values()) == 3

    def test_corrupt_snapshot_degrades_to_empty_distribution_not_a_crash(self, tmp_path):
        snapshot_path = tmp_path / "state_snapshot.json"
        snapshot_path.write_text("{not valid json", encoding="utf-8")
        with mock.patch(
            "data.historical_store.HistoricalStore",
            return_value=_FakeStore(total=7, since_counts={}),
        ):
            result = news_catalyst.get_news_catalyst_coverage(snapshot_path=str(snapshot_path))
        assert result["archived_score_count"] == 7
        assert result["universe_score_distribution"] == {"positive": 0, "neutral": 0, "negative": 0}

    def test_db_failure_returns_none_not_an_exception(self, tmp_path):
        snapshot_path = tmp_path / "state_snapshot.json"
        snapshot_path.write_text(json.dumps({"signals": []}), encoding="utf-8")
        with mock.patch(
            "data.historical_store.HistoricalStore",
            return_value=_FakeStore(raise_on="count"),
        ):
            result = news_catalyst.get_news_catalyst_coverage(snapshot_path=str(snapshot_path))
        assert result is None

    def test_headline_volume_7d_uses_a_separate_since_bounded_call(self, tmp_path):
        snapshot_path = tmp_path / "state_snapshot.json"
        snapshot_path.write_text(json.dumps({"signals": []}), encoding="utf-8")

        calls = []

        class _RecordingStore(_FakeStore):
            def count_finbert_scores(self, since=None):
                calls.append(since)
                return 3 if since is not None else 50

        with mock.patch("data.historical_store.HistoricalStore", return_value=_RecordingStore()):
            result = news_catalyst.get_news_catalyst_coverage(snapshot_path=str(snapshot_path))
        assert result["archived_score_count"] == 50
        assert result["headline_volume_7d"] == 3
        assert calls[0] is None  # total count -- no since bound
        assert isinstance(calls[1], datetime) and calls[1].tzinfo is not None
