"""Tests for data/sector_selection_heat.py -- the semantic Related Sector
Selection feature's spec-faithful Sector Heat Factor (SHF), NOT to be
confused with data/sentiment_sources.py's differently-specified
Sector_Heat_Factor dashboard column (see docs/signals/sector_heat_factor.md's
"Two features, one name" section)."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.historical_store import HistoricalStore
from data.sector_selection_heat import (
    _trailing_trading_days,
    compute_spec_sector_heat,
)


class _FakeStore:
    """Deterministic stand-in for HistoricalStore, giving full control over
    the three methods compute_spec_sector_heat consults."""

    def __init__(self, *, resolved_trading_day="2026-07-21", daily=None, archive_depth=None):
        self.resolved_trading_day = resolved_trading_day
        self.daily = daily or {}
        self.archive_depth = archive_depth or {}

    def resolve_trading_day(self, as_of_utc):
        return self.resolved_trading_day

    def get_sentiment_daily_by_source_class(self, symbols, start_day, end_day):
        return self.daily

    def get_sentiment_archive_depth_by_source(self):
        return self.archive_depth


def _gaussian(x, a=0.8, b=1.0, c=0.6):
    return a * math.exp(-((x - b) ** 2) / (2 * c * c))


class TestTrailingTradingDays:
    def test_returns_n_weekday_labels_ascending(self):
        # 2026-07-21 is a Tuesday.
        days = _trailing_trading_days("2026-07-21", 5)
        assert days == ["2026-07-15", "2026-07-16", "2026-07-17", "2026-07-20", "2026-07-21"]

    def test_includes_end_day(self):
        days = _trailing_trading_days("2026-07-21", 1)
        assert days == ["2026-07-21"]

    def test_skips_weekends(self):
        days = _trailing_trading_days("2026-07-21", 5)
        assert "2026-07-18" not in days  # Saturday
        assert "2026-07-19" not in days  # Sunday

    def test_end_day_itself_a_weekend_is_still_a_valid_output_boundary(self):
        # Not a realistic caller pattern (end_day always derives from
        # resolve_trading_day, which never returns a weekend), but the
        # helper itself should not special-case a weekend end_day input --
        # it simply walks backward and only COLLECTS weekdays.
        days = _trailing_trading_days("2026-07-18", 3)  # Saturday
        assert days[-1] == "2026-07-17"  # Friday, nearest prior weekday


class TestGatingAndEmptyInputs:
    def test_disabled_returns_empty_without_touching_store(self):
        store = MagicMock()
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", False):
            result = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={"AAPL": "Technology"}, historical_store=store
            )
        assert result == {}
        store.resolve_trading_day.assert_not_called()

    def test_empty_sectors_returns_empty(self):
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                [], ticker_sector_map={"AAPL": "Technology"}, historical_store=_FakeStore()
            )
        assert result == {}

    def test_empty_ticker_sector_map_returns_empty(self):
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={}, historical_store=_FakeStore()
            )
        assert result == {}

    def test_no_member_symbols_for_any_candidate_sector_is_unrankable(self):
        store = _FakeStore(daily={}, archive_depth={})
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={"AAPL": "Healthcare"}, historical_store=store
            )
        assert math.isnan(result["Technology"]["shf"])
        assert result["Technology"]["degraded_reason"] == "no_volume_observed"


class TestGaussianFormula:
    def test_single_sector_degenerate_midpoint(self):
        """A single candidate sector has no cross-sector range to normalize
        against -- x defaults to the 0.5 midpoint rather than dividing by
        zero."""
        store = _FakeStore(
            daily={"AAPL": {"2026-07-21": {
                "news_count": 10.0, "news_mean_score": 0.5,
                "comment_count": 5.0, "comment_mean_score": 0.1,
            }}},
            archive_depth={"reddit": {"document_count": 3}},
        )
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={"AAPL": "Technology"}, historical_store=store
            )
        assert result["Technology"]["shf"] == pytest.approx(_gaussian(0.5))
        assert result["Technology"]["degraded_reason"] is None

    def test_max_volume_sector_scores_at_gaussian_center(self):
        """x normalizes to 1.0 (== b) for the highest-volume sector -- SHF
        should equal the Gaussian's peak amplitude 'a'."""
        daily = {
            "LOW": {"2026-07-21": {
                "news_count": 1.0, "news_mean_score": 0.0,
                "comment_count": 0.0, "comment_mean_score": float("nan"),
            }},
            "HIGH": {"2026-07-21": {
                "news_count": 100.0, "news_mean_score": 0.0,
                "comment_count": 0.0, "comment_mean_score": float("nan"),
            }},
        }
        store = _FakeStore(daily=daily, archive_depth={"reddit": {"document_count": 1}})
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["A", "B"],
                ticker_sector_map={"LOW": "A", "HIGH": "B"},
                historical_store=store,
            )
        assert result["B"]["shf"] == pytest.approx(0.8)  # a * exp(0) = a
        assert result["A"]["shf"] == pytest.approx(_gaussian(0.0))

    def test_custom_gaussian_parameters_respected(self):
        store = _FakeStore(
            daily={"AAPL": {"2026-07-21": {
                "news_count": 4.0, "news_mean_score": 0.0,
                "comment_count": 0.0, "comment_mean_score": float("nan"),
            }}},
            archive_depth={"reddit": {"document_count": 1}},
        )
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("settings.settings.SECTOR_SELECTION_HEAT_A", 1.0), \
             patch("settings.settings.SECTOR_SELECTION_HEAT_B", 0.5), \
             patch("settings.settings.SECTOR_SELECTION_HEAT_C", 0.3):
            result = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={"AAPL": "Technology"}, historical_store=store
            )
        assert result["Technology"]["shf"] == pytest.approx(_gaussian(0.5, a=1.0, b=0.5, c=0.3))


class TestHonestDegradation:
    def test_never_observed_sector_gets_nan_not_zero(self):
        """CONSTRAINT #4: a sector with zero rows anywhere in the window
        must be NaN, never fabricated as a zero-volume sector."""
        store = _FakeStore(daily={}, archive_depth={})
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={"AAPL": "Technology"}, historical_store=store
            )
        assert math.isnan(result["Technology"]["news_volume"])
        assert math.isnan(result["Technology"]["review_volume"])
        assert math.isnan(result["Technology"]["shf"])
        assert result["Technology"]["degraded_reason"] == "no_volume_observed"

    def test_review_channel_never_observed_degrades_to_news_only(self):
        """The comment channel has never produced a document anywhere
        (archive_depth has no comment-classified source, or none with
        document_count > 0) -- review_volume must be NaN and SHF computed
        from news volume alone, even though get_sentiment_daily_by_source_
        class itself reports a 'real' zero comment_count for the day."""
        store = _FakeStore(
            daily={"AAPL": {"2026-07-21": {
                "news_count": 5.0, "news_mean_score": 0.4,
                "comment_count": 0.0, "comment_mean_score": float("nan"),
            }}},
            archive_depth={"gdelt": {"document_count": 40}},  # no reddit/comment source at all
        )
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={"AAPL": "Technology"}, historical_store=store
            )
        assert math.isnan(result["Technology"]["review_volume"])
        assert result["Technology"]["degraded_reason"] == "review_unavailable"
        # Still computes a real SHF from news volume alone (single sector -> midpoint).
        assert result["Technology"]["shf"] == pytest.approx(_gaussian(0.5))

    def test_review_channel_previously_observed_but_quiet_this_window_is_a_real_zero(self):
        """Once the comment channel has EVER produced a document, a
        genuine zero this window is trusted as a real zero, not degraded."""
        store = _FakeStore(
            daily={"AAPL": {"2026-07-21": {
                "news_count": 5.0, "news_mean_score": 0.4,
                "comment_count": 0.0, "comment_mean_score": float("nan"),
            }}},
            archive_depth={"reddit": {"document_count": 12}},
        )
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={"AAPL": "Technology"}, historical_store=store
            )
        assert result["Technology"]["review_volume"] == pytest.approx(0.0)
        assert result["Technology"]["degraded_reason"] is None

    def test_archive_depth_source_with_zero_documents_not_counted_as_observed(self):
        """A source_name present in archive_depth with document_count == 0
        (e.g. a stale row from a schema migration) must not count as
        genuine historical review evidence."""
        store = _FakeStore(
            daily={"AAPL": {"2026-07-21": {
                "news_count": 5.0, "news_mean_score": 0.4,
                "comment_count": 0.0, "comment_mean_score": float("nan"),
            }}},
            archive_depth={"reddit": {"document_count": 0}},
        )
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={"AAPL": "Technology"}, historical_store=store
            )
        assert result["Technology"]["degraded_reason"] == "review_unavailable"

    def test_mixed_observed_and_unobserved_sectors(self):
        """One sector has real volume, another has none at all -- the
        unobserved one is excluded from cross-sector normalization
        entirely (unrankable), the observed one still computes normally."""
        daily = {"AAPL": {"2026-07-21": {
            "news_count": 8.0, "news_mean_score": 0.2,
            "comment_count": 2.0, "comment_mean_score": 0.1,
        }}}
        store = _FakeStore(daily=daily, archive_depth={"reddit": {"document_count": 5}})
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology", "Healthcare"],
                ticker_sector_map={"AAPL": "Technology", "JNJ": "Healthcare"},
                historical_store=store,
            )
        assert result["Technology"]["degraded_reason"] is None
        assert not math.isnan(result["Technology"]["shf"])
        assert result["Healthcare"]["degraded_reason"] == "no_volume_observed"
        assert math.isnan(result["Healthcare"]["shf"])


class TestFailureResilience:
    def test_store_exception_degrades_to_unrankable_never_raises(self):
        """CONSTRAINT #6: a store failure must never raise out of this
        function."""
        store = MagicMock()
        store.resolve_trading_day.side_effect = RuntimeError("simulated failure")
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={"AAPL": "Technology"}, historical_store=store
            )
        assert result["Technology"]["degraded_reason"] == "no_volume_observed"
        assert math.isnan(result["Technology"]["shf"])


class TestEndToEndAgainstRealHistoricalStore:
    """Integration coverage against a real HistoricalStore + real
    sentiment_ingestion_audit rows, proving the classify_source wiring
    (Sentiment Source Class Phase 0) genuinely works end-to-end."""

    def _doc(self, **overrides):
        base = dict(
            as_of=datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc),  # 10:00 ET
            symbol="AAPL",
            source_name="gdelt",
            text_content="test",
            raw_sentiment_score=0.5,
        )
        base.update(overrides)
        return base

    def test_real_store_aggregation_and_degradation(self, tmp_path):
        db = str(tmp_path / "sector_heat.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([
            self._doc(symbol="AAPL", source_name="gdelt", final_weighted_score=0.3),
            self._doc(symbol="AAPL", source_name="yahoo_rss", final_weighted_score=0.5),
            self._doc(symbol="MSFT", source_name="edgar", final_weighted_score=0.1),
        ])
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"],
                ticker_sector_map={"AAPL": "Technology", "MSFT": "Technology"},
                as_of=datetime(2026, 7, 21, 20, 30, tzinfo=timezone.utc),  # post-close
                historical_store=store,
            )
        assert result["Technology"]["news_volume"] == pytest.approx(3.0)
        assert result["Technology"]["degraded_reason"] == "review_unavailable"
        assert not math.isnan(result["Technology"]["shf"])
