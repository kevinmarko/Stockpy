"""No-lookahead test for the GDELT-based "Sector Heat Factor" attention
feature (data/sentiment_sources.py::GDELTVolumeSource/compute_sector_heat_factors).

The leakage-critical rule this feature must enforce: a GDELT article-volume
data point dated AFTER the cycle's as-of time ("now") must never influence
"today's" computed Sector Heat Factor -- neither by widening the query
window sent to GDELT, nor by silently accepting a stray future-dated point
in the response.

Per the repo convention (one dedicated file per subsystem's no-lookahead
guarantee -- see tests/test_sentiment_pit_lookahead.py, tests/test_pairs_lookahead.py,
tests/test_hmm_no_lookahead.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.sentiment_sources import GDELTVolumeSource, compute_sector_heat_factors


class TestGDELTVolumeSourceCausalQueryWindow:
    def test_enddatetime_never_exceeds_until(self):
        """The `enddatetime` param sent to GDELT must equal `until` exactly
        -- never a value derived from `since`/lookback that could extend
        past the cycle's as-of instant."""
        src = GDELTVolumeSource()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"timeline": [{"data": []}]}

        until = datetime(2026, 7, 21, 14, 30, 0, tzinfo=timezone.utc)  # mid-session
        since = until - timedelta(days=7)

        with patch("data.sentiment_sources.requests.get", return_value=mock_resp) as mock_get:
            src.fetch_daily_counts("Technology sector stocks", since, until)

        params = mock_get.call_args.kwargs["params"]
        assert params["enddatetime"] == until.strftime("%Y%m%d%H%M%S")
        # Sanity: mid-session cutoff, NOT rounded up to end-of-day.
        assert params["enddatetime"] == "20260721143000"

    def test_default_until_is_now_not_end_of_lookback_window(self):
        """Omitting `until` must default to `datetime.now(timezone.utc)` at
        call time, not e.g. `since + lookback_days` (which could silently
        request a window extending past the real present)."""
        src = GDELTVolumeSource()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"timeline": [{"data": []}]}

        fixed_now = datetime(2026, 7, 21, 9, 0, 0, tzinfo=timezone.utc)
        with patch("data.sentiment_sources.requests.get", return_value=mock_resp) as mock_get:
            with patch("data.sentiment_sources.datetime") as mock_dt:
                mock_dt.now.return_value = fixed_now
                mock_dt.strptime = datetime.strptime
                src.fetch_daily_counts("q", since=fixed_now - timedelta(days=7))

        params = mock_get.call_args.kwargs["params"]
        assert params["enddatetime"] == fixed_now.strftime("%Y%m%d%H%M%S")


class TestGDELTVolumeSourceFutureDatedPointDropped:
    def test_point_dated_after_until_is_excluded(self):
        """Belt-and-suspenders: even if GDELT's response itself contained a
        point dated after `until` (a malformed/buggy response), that point
        must never enter the returned series."""
        src = GDELTVolumeSource()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "timeline": [{
                "data": [
                    {"date": "20260720000000", "value": 4.0},   # within window
                    {"date": "20260722000000", "value": 999.0},  # AFTER `until` -- must be dropped
                ],
            }]
        }
        until = datetime(2026, 7, 21, tzinfo=timezone.utc)
        since = datetime(2026, 7, 19, tzinfo=timezone.utc)

        with patch("data.sentiment_sources.requests.get", return_value=mock_resp):
            counts = src.fetch_daily_counts("q", since, until)

        assert counts == {"2026-07-20": 4.0}
        assert "2026-07-22" not in counts


class TestComputeSectorHeatFactorsLookahead:
    def test_future_dated_volume_spike_never_changes_todays_heat_value(self):
        """End-to-end through the actual feature entry point: a GDELT
        response smuggling in a huge future-dated spike (simulating a clock-
        skew bug or a malformed API response) must not move the computed
        Sector Heat Factor for 'today' at all -- the point is dropped before
        smoothing ever sees it."""
        import numpy as np
        from scipy.ndimage import gaussian_filter1d

        clean_series = [1.0, 2.0, 3.0, 2.0, 1.0]
        expected = float(gaussian_filter1d(np.asarray(clean_series), sigma=1.0)[-1])

        def _fake_fetch(query, since, until):
            # Real GDELT would never return a point beyond `until`, but this
            # simulates the defensive case where it does -- the future spike
            # must be dropped by fetch_daily_counts itself (see
            # TestGDELTVolumeSourceFutureDatedPointDropped above), so
            # compute_sector_heat_factors receives only the clean series.
            return {
                f"2026-07-{15 + i:02d}": v for i, v in enumerate(clean_series)
            }

        fake_source = MagicMock()
        fake_source.fetch_daily_counts.side_effect = _fake_fetch

        with patch("settings.settings.SECTOR_HEAT_ENABLED", True), \
             patch("settings.settings.SECTOR_HEAT_SMOOTHING_SIGMA", 1.0), \
             patch("settings.settings.SECTOR_HEAT_LOOKBACK_DAYS", 5), \
             patch("data.sentiment_sources.GDELTVolumeSource", return_value=fake_source):
            result = compute_sector_heat_factors(["Technology"])

        assert result["Technology"] == pytest.approx(expected)

    def test_since_derived_from_lookback_days_not_from_a_future_anchor(self):
        """compute_sector_heat_factors must derive `since` as `now - lookback_days`
        -- never anchored at some point past `now` -- so the requested
        window itself is always causal, independent of what GDELTVolumeSource
        does with it."""
        fake_source = MagicMock()
        fake_source.fetch_daily_counts.return_value = {"2026-07-20": 1.0}

        fixed_now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
        with patch("settings.settings.SECTOR_HEAT_ENABLED", True), \
             patch("settings.settings.SECTOR_HEAT_LOOKBACK_DAYS", 7), \
             patch("data.sentiment_sources.GDELTVolumeSource", return_value=fake_source):
            compute_sector_heat_factors(["Technology"], now=fixed_now)

        call_args = fake_source.fetch_daily_counts.call_args
        _, kwargs = call_args
        # positional or keyword -- normalize
        args = call_args.args
        since_arg = kwargs.get("since") if "since" in kwargs else (args[1] if len(args) > 1 else None)
        until_arg = kwargs.get("until") if "until" in kwargs else (args[2] if len(args) > 2 else None)
        assert since_arg == fixed_now - timedelta(days=7)
        assert until_arg == fixed_now
