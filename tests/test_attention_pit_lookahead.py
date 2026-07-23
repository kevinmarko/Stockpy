"""No-lookahead test for the Wikipedia-pageviews attention feature.

The leakage-critical rule this feature must enforce: a pageview count dated
AFTER the cycle's as-of time can never influence that cycle's Attention_Score
-- whether it arrives via a malformed/future-dated Wikimedia API response, or
(in tests) a deliberately future-dated fixture. This exercises the real
chain end-to-end: WikipediaPageviewsSource.fetch_daily_series() (HTTP -> raw
per-day dict, no date filtering of its own) -> compute_attention_score()'s
``as_of``-bounded ``_abnormal_attention_score()`` transform, the same
function pipeline/production_steps.py drives every cycle.

Per the repo convention (one dedicated file per subsystem's no-lookahead
guarantee -- see tests/test_sentiment_pit_lookahead.py,
tests/test_pairs_lookahead.py, tests/test_hmm_no_lookahead.py).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.attention_sources import (
    WikipediaPageviewsSource,
    _abnormal_attention_score,
    compute_attention_score,
)


def _make_series(as_of: datetime, baseline_value: float, recent_value: float,
                  baseline_days: int = 10, recent_days: int = 3) -> dict:
    total = baseline_days + recent_days
    series = {}
    for i in range(total):
        offset = total - 1 - i
        d = (as_of - timedelta(days=offset)).strftime("%Y-%m-%d")
        series[d] = baseline_value if i < baseline_days else recent_value
    return series


class TestAttentionPITLookahead:
    def test_future_dated_row_excluded_from_transform(self):
        """A future-dated spike must never leak into the recent-window mean.

        Baseline and recent windows are both flat at 1000 (expected score ~
        0). We then inject a same-magnitude-as-baseline row dated ONE day
        AFTER as_of with an extreme spike value. Without the as_of cutoff
        filter, that spike would displace the oldest "recent" day and blow
        the score far away from 0; with the filter in place (as implemented
        in _abnormal_attention_score), the score must stay ~0.
        """
        as_of = datetime(2026, 7, 21, tzinfo=timezone.utc)
        series = _make_series(as_of, baseline_value=1000.0, recent_value=1000.0)
        future_date = (as_of + timedelta(days=1)).strftime("%Y-%m-%d")
        series[future_date] = 999_999.0  # would dominate the mean if leaked

        score_with_future_row = _abnormal_attention_score(series, as_of=as_of)
        assert score_with_future_row == pytest.approx(0.0, abs=1e-9)

        # Sanity: WITHOUT the as_of bound (i.e. treating the future row's
        # own date as "as_of"), the same series produces a wildly different,
        # clearly-leaked score -- proving the guard is actually doing work,
        # not just a no-op on this fixture.
        leaked_as_of = as_of + timedelta(days=1)
        score_if_leaked = _abnormal_attention_score(series, as_of=leaked_as_of)
        assert score_if_leaked > 1.0
        assert score_if_leaked != pytest.approx(score_with_future_row, abs=1e-6)

    def test_end_to_end_fetch_then_score_never_leaks_future_row(self):
        """Same guarantee through the real fetch->score chain: a Wikimedia
        response containing a future-dated row (malformed/clock-skewed
        upstream data) must not perturb compute_attention_score()'s result
        for a cycle whose as_of predates it."""
        as_of = datetime(2026, 7, 21, tzinfo=timezone.utc)
        series = _make_series(as_of, baseline_value=1000.0, recent_value=1000.0)
        future_date = (as_of + timedelta(days=2)).strftime("%Y-%m-%d")
        series[future_date] = 5_000_000.0

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "items": [
                {"timestamp": d.replace("-", "") + "00", "views": v}
                for d, v in series.items()
            ]
        }
        with patch("data.attention_sources.requests.get", return_value=mock_resp):
            source = WikipediaPageviewsSource()
            score = compute_attention_score(
                "AAPL", None, source=source, lookback_days=30, as_of=as_of,
            )
        assert not math.isnan(score)
        assert score == pytest.approx(0.0, abs=1e-9)

    def test_same_day_boundary_is_inclusive_not_leakage(self):
        """A row dated exactly on as_of's calendar day is legitimately
        "today's" data (e.g. a same-day partial pageview count) and must be
        included -- only STRICTLY future rows are dropped. This guards
        against an overly-aggressive off-by-one that would silently starve
        the recent window of its most current day."""
        as_of = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
        series = _make_series(as_of, baseline_value=1000.0, recent_value=2000.0)
        # The final entry in _make_series is dated exactly as_of's calendar day.
        score = _abnormal_attention_score(series, as_of=as_of)
        expected = math.log1p(2000.0) - math.log1p(1000.0)
        assert score == pytest.approx(expected, rel=1e-9)
