"""No-lookahead test for sector_selection_engine.py's orchestration layer.

The Sector Heat Factor term's own leakage-critical window logic is already
covered end-to-end by tests/test_sector_selection_heat_lookahead.py. This
file covers a DIFFERENT leakage risk specific to the orchestrator: that
``run_sector_selection``'s ``as_of`` parameter (the whole point of which is
letting a backtest replay compute "as of" a past date) is actually
threaded through to every as-of-sensitive call, rather than any call site
silently substituting `datetime.now()` / wall-clock time.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np

from sector_selection_engine import run_sector_selection


def _fake_store(resolved_day="2026-07-21"):
    store = MagicMock()
    store.resolve_trading_day.return_value = resolved_day
    return store


class TestAsOfThreading:
    def test_as_of_forwarded_to_compute_spec_sector_heat(self):
        as_of = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
        heat_mock = MagicMock(return_value={"Technology": {
            "shf": 0.5, "news_volume": 1.0, "review_volume": 0.0, "degraded_reason": None,
        }})

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("settings.settings.SECTOR_SELECTION_TOP_N", 3), \
             patch("settings.settings.SECTOR_SIMILARITY_EMBEDDER", "none"), \
             patch("data.sector_selection_heat.compute_spec_sector_heat", heat_mock), \
             patch("data.sector_embeddings.load_sector_descriptions", return_value={"Technology": "desc"}), \
             patch("engine.portfolio_exposure._load_sector_map", return_value={"AAPL": "Technology"}), \
             patch("data.sector_embeddings.resolve_target_description", return_value="desc"):
            run_sector_selection(
                ["NIO"], as_of=as_of, historical_store=_fake_store(), correlation_store=MagicMock(),
            )

        _, kwargs = heat_mock.call_args
        assert kwargs["as_of"] == as_of

    def test_persisted_as_of_derived_from_passed_as_of_not_wall_clock(self):
        """The trading-day label used to PERSIST a ranking must come from
        resolve_trading_day(as_of) -- if it silently used datetime.now()
        instead, a backtest replaying a past date would write its result
        under TODAY's date, corrupting the historical record with a
        future-relative timestamp."""
        as_of = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
        store = _fake_store(resolved_day="2026-03-16")  # what resolve_trading_day(as_of) would return
        correlation_store = MagicMock()

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("settings.settings.SECTOR_SELECTION_TOP_N", 3), \
             patch("settings.settings.SECTOR_SIMILARITY_EMBEDDER", "none"), \
             patch("data.sector_selection_heat.compute_spec_sector_heat", return_value={
                 "Technology": {"shf": 0.5, "news_volume": 1.0, "review_volume": 0.0, "degraded_reason": None},
             }), \
             patch("data.sector_embeddings.load_sector_descriptions", return_value={"Technology": "desc"}), \
             patch("engine.portfolio_exposure._load_sector_map", return_value={"AAPL": "Technology"}), \
             patch("data.sector_embeddings.resolve_target_description", return_value="desc"):
            run_sector_selection(
                ["NIO"], as_of=as_of, historical_store=store, correlation_store=correlation_store,
            )

        # resolve_trading_day must have been called with the PASSED as_of,
        # not datetime.now() -- and the persisted as_of must match its result.
        store.resolve_trading_day.assert_called_once_with(as_of)
        _, kwargs = correlation_store.record_correlations.call_args
        assert kwargs["as_of"] == "2026-03-16"

    def test_omitted_as_of_resolved_once_and_shared_by_both_calls(self):
        """When as_of is genuinely omitted (a live daily run, not a
        backtest replay), run_sector_selection must resolve 'now' exactly
        ONCE and pass the SAME instant to both resolve_trading_day
        (persistence) and compute_spec_sector_heat (the window) -- two
        independent datetime.now() calls could drift apart across a
        wall-clock tick between them, silently misaligning the persisted
        as_of label from the actual window that was scored."""
        heat_mock = MagicMock(return_value={"Technology": {
            "shf": 0.5, "news_volume": 1.0, "review_volume": 0.0, "degraded_reason": None,
        }})
        store = _fake_store()

        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("settings.settings.SECTOR_SELECTION_TOP_N", 3), \
             patch("settings.settings.SECTOR_SIMILARITY_EMBEDDER", "none"), \
             patch("data.sector_selection_heat.compute_spec_sector_heat", heat_mock), \
             patch("data.sector_embeddings.load_sector_descriptions", return_value={"Technology": "desc"}), \
             patch("engine.portfolio_exposure._load_sector_map", return_value={"AAPL": "Technology"}), \
             patch("data.sector_embeddings.resolve_target_description", return_value="desc"):
            run_sector_selection(["NIO"], historical_store=store, correlation_store=MagicMock())

        assert store.resolve_trading_day.call_count == 1
        resolved_now = store.resolve_trading_day.call_args.args[0]
        _, heat_kwargs = heat_mock.call_args
        assert heat_kwargs["as_of"] is resolved_now  # the exact same object, not a re-derived one
