"""No-lookahead test for the semantic Related Sector Selection feature's
spec-faithful Sector Heat Factor (data/sector_selection_heat.py).

The leakage-critical rule: a sentiment_ingestion_audit document whose
resolved trading_day falls AFTER the cycle's as-of trading day must never
influence "today's" computed Sector Heat Factor -- neither by widening the
trailing-window query, nor by a stray future-dated row slipping into the
sum.

Per the repo convention (one dedicated file per subsystem's no-lookahead
guarantee -- see tests/test_sector_heat_lookahead.py, tests/test_sentiment_pit_lookahead.py).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from data.historical_store import HistoricalStore
from data.sector_selection_heat import compute_spec_sector_heat


def _doc(**overrides):
    base = dict(
        as_of=datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc),  # 10:00 ET
        symbol="AAPL",
        source_name="gdelt",
        text_content="test",
        raw_sentiment_score=0.5,
    )
    base.update(overrides)
    return base


class TestNoLookahead:
    def test_future_dated_document_never_moves_todays_shf(self, tmp_path):
        """A document dated well after the as-of instant (simulating a
        clock-skew bug or a backfill run writing ahead) must not change the
        SHF computed 'as of' an earlier date."""
        db = str(tmp_path / "shf.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([
            _doc(as_of=datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc), final_weighted_score=0.3),
        ])
        as_of = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)  # before close

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            baseline = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={"AAPL": "Technology"},
                as_of=as_of, historical_store=store,
            )

        # Now inject a huge future-dated spike (e.g. a bogus/misdated row).
        store.save_sentiment_documents([
            _doc(as_of=datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc),
                 source_name="reddit", final_weighted_score=-0.9),
        ] * 50)

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            after_spike = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={"AAPL": "Technology"},
                as_of=as_of, historical_store=store,
            )

        assert after_spike["Technology"]["news_volume"] == pytest.approx(
            baseline["Technology"]["news_volume"]
        )
        assert after_spike["Technology"]["shf"] == pytest.approx(baseline["Technology"]["shf"])

    def test_window_end_day_derived_from_as_of_not_wall_clock(self, tmp_path):
        """The trailing 22-trading-day window must end at the trading day
        resolved from the PASSED-IN as_of, never from datetime.now() --
        otherwise a backtest replay computing 'as of' a past date would
        leak in documents from between that date and today."""
        db = str(tmp_path / "shf.db")
        store = HistoricalStore(db_path=db)
        # A document dated shortly AFTER the as_of used below.
        store.save_sentiment_documents([
            _doc(as_of=datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc), final_weighted_score=0.5),
        ])
        as_of = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={"AAPL": "Technology"},
                as_of=as_of, historical_store=store,
            )
        # The only document is dated the day AFTER as_of's trading day --
        # must be entirely invisible, yielding "never observed", not a
        # fabricated zero.
        assert math.isnan(result["Technology"]["news_volume"])
        assert result["Technology"]["degraded_reason"] == "no_volume_observed"

    def test_post_close_as_of_correctly_rolls_window_forward(self, tmp_path):
        """A cycle running post-market-close scores the NEXT trading
        session (via resolve_trading_day's own roll) -- a document dated
        ON that same calendar day, but before ITS OWN close, is still
        correctly within the (now-rolled-forward) window."""
        db = str(tmp_path / "shf.db")
        store = HistoricalStore(db_path=db)
        store.save_sentiment_documents([
            _doc(as_of=datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc), final_weighted_score=0.3),
        ])
        post_close_as_of = datetime(2026, 7, 21, 20, 30, tzinfo=timezone.utc)  # 16:30 ET

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True):
            result = compute_spec_sector_heat(
                ["Technology"], ticker_sector_map={"AAPL": "Technology"},
                as_of=post_close_as_of, historical_store=store,
            )
        assert result["Technology"]["news_volume"] == pytest.approx(1.0)
