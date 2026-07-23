"""
tests/test_attention_sources.py
================================
Unit tests for data/attention_sources.py (Wikipedia-pageviews investor
attention feature, follow-on branch to PR #416/#417).

All HTTP/API calls are monkeypatched; no real network requests are made.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.attention_sources import (
    WikipediaPageviewsSource,
    _abnormal_attention_score,
    _fetch_pytrends_attention_score,
    compute_attention_score,
    compute_attention_scores_for_universe,
    resolve_article_title,
)


# ---------------------------------------------------------------------------
# resolve_article_title
# ---------------------------------------------------------------------------

class TestResolveArticleTitle:
    def test_ticker_only_when_no_company_name(self):
        assert resolve_article_title("AAPL") == ["AAPL"]

    def test_ticker_then_company_name(self):
        assert resolve_article_title("AAPL", "Apple Inc.") == ["AAPL", "Apple Inc."]

    def test_placeholder_company_name_excluded(self):
        assert resolve_article_title("XYZ", "Unknown Asset") == ["XYZ"]

    def test_company_name_identical_to_ticker_not_duplicated(self):
        assert resolve_article_title("AAPL", "AAPL") == ["AAPL"]

    def test_empty_symbol_no_company_name(self):
        assert resolve_article_title("", None) == []


# ---------------------------------------------------------------------------
# _abnormal_attention_score -- deterministic transform math
# ---------------------------------------------------------------------------

def _series(baseline_value: float, recent_value: float, baseline_days: int = 10, recent_days: int = 3):
    """Build a deterministic daily_series dict: `baseline_days` days at
    `baseline_value`, followed immediately by `recent_days` days at
    `recent_value`, dated so the last day is 2026-07-21 (a fixed as_of)."""
    as_of = datetime(2026, 7, 21, tzinfo=timezone.utc)
    total = baseline_days + recent_days
    series = {}
    for i in range(total):
        day_offset = total - 1 - i  # 0 = as_of day itself
        d = (as_of - _td(day_offset)).strftime("%Y-%m-%d")
        value = baseline_value if i < baseline_days else recent_value
        series[d] = value
    return series, as_of


def _td(days):
    from datetime import timedelta
    return timedelta(days=days)


class TestAbnormalAttentionScoreMath:
    def test_known_output_flat_series_is_zero(self):
        series, as_of = _series(baseline_value=1000.0, recent_value=1000.0)
        score = _abnormal_attention_score(series, as_of=as_of)
        assert score == pytest.approx(0.0, abs=1e-9)

    def test_known_output_spike_is_positive_and_exact(self):
        series, as_of = _series(baseline_value=1000.0, recent_value=5000.0)
        score = _abnormal_attention_score(series, as_of=as_of)
        expected = math.log1p(5000.0) - math.log1p(1000.0)
        assert score == pytest.approx(expected, rel=1e-9)
        assert score > 0

    def test_known_output_dropoff_is_negative(self):
        series, as_of = _series(baseline_value=1000.0, recent_value=100.0)
        score = _abnormal_attention_score(series, as_of=as_of)
        assert score < 0

    def test_empty_series_is_nan(self):
        assert math.isnan(_abnormal_attention_score({}))
        assert math.isnan(_abnormal_attention_score(None))

    def test_insufficient_baseline_history_is_nan(self):
        # Only 5 total days -- fewer than _MIN_BASELINE_DAYS (7) once the
        # 3-day recent window is carved off.
        as_of = datetime(2026, 7, 21, tzinfo=timezone.utc)
        series = {
            "2026-07-17": 100.0, "2026-07-18": 100.0, "2026-07-19": 100.0,
            "2026-07-20": 100.0, "2026-07-21": 100.0,
        }
        assert math.isnan(_abnormal_attention_score(series, as_of=as_of))

    def test_malformed_date_keys_are_skipped_not_fatal(self):
        series, as_of = _series(baseline_value=1000.0, recent_value=1000.0)
        series["not-a-date"] = 999999.0
        score = _abnormal_attention_score(series, as_of=as_of)
        assert score == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# WikipediaPageviewsSource -- HTTP fetch + parse
# ---------------------------------------------------------------------------

class TestWikipediaPageviewsSource:
    def test_successful_fetch_parses_items(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "items": [
                {"timestamp": "2026070100", "views": 1234},
                {"timestamp": "2026070200", "views": 5678},
            ]
        }
        with patch("data.attention_sources.requests.get", return_value=mock_resp) as mock_get:
            source = WikipediaPageviewsSource()
            series = source.fetch_daily_series("Apple Inc.", 30)
        assert series == {"2026-07-01": 1234.0, "2026-07-02": 5678.0}
        # Confirm the article title is URL-safe-encoded with underscores.
        called_url = mock_get.call_args.args[0]
        assert "Apple_Inc" in called_url

    def test_empty_symbol_returns_none_no_network_call(self):
        with patch("data.attention_sources.requests.get") as mock_get:
            source = WikipediaPageviewsSource()
            assert source.fetch_daily_series("", 30) is None
            assert source.fetch_daily_series(None, 30) is None
        mock_get.assert_not_called()

    def test_http_error_degrades_to_none(self):
        with patch("data.attention_sources.requests.get", side_effect=RuntimeError("boom")):
            source = WikipediaPageviewsSource()
            assert source.fetch_daily_series("AAPL", 30) is None

    def test_404_raise_for_status_degrades_to_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = RuntimeError("404 Not Found")
        with patch("data.attention_sources.requests.get", return_value=mock_resp):
            source = WikipediaPageviewsSource()
            assert source.fetch_daily_series("NOTAREALARTICLE", 30) is None

    def test_malformed_json_payload_degrades_to_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"items": [{"timestamp": None, "views": None}]}
        with patch("data.attention_sources.requests.get", return_value=mock_resp):
            source = WikipediaPageviewsSource()
            assert source.fetch_daily_series("AAPL", 30) is None


# ---------------------------------------------------------------------------
# compute_attention_score -- ticker-then-company-name fallback, pytrends
# supplementary path
# ---------------------------------------------------------------------------

class TestComputeAttentionScore:
    def test_ticker_title_succeeds_company_name_not_tried(self):
        mock_source = MagicMock()
        series, as_of = _series(1000.0, 1000.0)
        mock_source.fetch_daily_series.return_value = series
        with patch("settings.settings.PYTRENDS_ENABLED", False):
            score = compute_attention_score(
                "AAPL", "Apple Inc.", source=mock_source, lookback_days=30, as_of=as_of,
            )
        assert score == pytest.approx(0.0, abs=1e-9)
        mock_source.fetch_daily_series.assert_called_once_with("AAPL", 30)

    def test_falls_back_to_company_name_when_ticker_title_fails(self):
        mock_source = MagicMock()
        series, as_of = _series(1000.0, 2000.0)

        def side_effect(title, lookback_days):
            return None if title == "AAPL" else series

        mock_source.fetch_daily_series.side_effect = side_effect
        with patch("settings.settings.PYTRENDS_ENABLED", False):
            score = compute_attention_score(
                "AAPL", "Apple Inc.", source=mock_source, lookback_days=30, as_of=as_of,
            )
        assert not math.isnan(score)
        assert mock_source.fetch_daily_series.call_count == 2

    def test_no_data_anywhere_and_pytrends_disabled_is_nan(self):
        mock_source = MagicMock()
        mock_source.fetch_daily_series.return_value = None
        with patch("settings.settings.PYTRENDS_ENABLED", False):
            score = compute_attention_score("ZZZZ", None, source=mock_source, lookback_days=30)
        assert math.isnan(score)

    def test_pytrends_fallback_used_only_when_wikipedia_empty(self):
        mock_source = MagicMock()
        mock_source.fetch_daily_series.return_value = None
        with patch("settings.settings.PYTRENDS_ENABLED", True):
            with patch(
                "data.attention_sources._fetch_pytrends_attention_score",
                return_value=0.42,
            ) as mock_pytrends:
                score = compute_attention_score("ZZZZ", None, source=mock_source, lookback_days=30)
        assert score == pytest.approx(0.42)
        mock_pytrends.assert_called_once()

    def test_pytrends_never_called_when_wikipedia_succeeds(self):
        mock_source = MagicMock()
        series, as_of = _series(1000.0, 1000.0)
        mock_source.fetch_daily_series.return_value = series
        with patch("settings.settings.PYTRENDS_ENABLED", True):
            with patch(
                "data.attention_sources._fetch_pytrends_attention_score",
            ) as mock_pytrends:
                compute_attention_score(
                    "AAPL", None, source=mock_source, lookback_days=30, as_of=as_of,
                )
        mock_pytrends.assert_not_called()


# ---------------------------------------------------------------------------
# Optional pytrends path -- 429-tolerant, never load-bearing, never raises
# ---------------------------------------------------------------------------

class TestPytrendsOptionalPath:
    def test_import_error_degrades_to_none(self):
        # pytrends is an optional dependency (requirements-optional.txt) and
        # may genuinely not be installed -- must not raise.
        with patch.dict("sys.modules", {"pytrends": None, "pytrends.request": None}):
            result = _fetch_pytrends_attention_score("AAPL", 30)
        assert result is None

    def test_simulated_429_degrades_to_none_never_raises(self):
        mock_trendreq_cls = MagicMock(side_effect=Exception("429 Too Many Requests"))
        mock_module = MagicMock()
        mock_module.TrendReq = mock_trendreq_cls
        with patch.dict("sys.modules", {"pytrends": MagicMock(), "pytrends.request": mock_module}):
            result = _fetch_pytrends_attention_score("AAPL", 30)
        assert result is None

    def test_build_payload_429_degrades_to_none(self):
        mock_instance = MagicMock()
        mock_instance.build_payload.side_effect = Exception("429 Too Many Requests")
        mock_module = MagicMock()
        mock_module.TrendReq.return_value = mock_instance
        with patch.dict("sys.modules", {"pytrends": MagicMock(), "pytrends.request": mock_module}):
            result = _fetch_pytrends_attention_score("AAPL", 30)
        assert result is None
        # No retry: exactly one construction attempt.
        mock_module.TrendReq.assert_called_once()

    def test_empty_interest_over_time_degrades_to_none(self):
        import pandas as pd
        mock_instance = MagicMock()
        mock_instance.build_payload = MagicMock()
        mock_instance.interest_over_time.return_value = pd.DataFrame()
        mock_module = MagicMock()
        mock_module.TrendReq.return_value = mock_instance
        with patch.dict("sys.modules", {"pytrends": MagicMock(), "pytrends.request": mock_module}):
            result = _fetch_pytrends_attention_score("AAPL", 30)
        assert result is None


# ---------------------------------------------------------------------------
# compute_attention_scores_for_universe -- master gate + batch behavior
# ---------------------------------------------------------------------------

class TestComputeAttentionScoresForUniverse:
    def test_disabled_makes_zero_network_calls_and_returns_empty(self):
        with patch("settings.settings.WIKIPEDIA_ATTENTION_ENABLED", False):
            with patch("data.attention_sources.requests.get") as mock_get:
                result = compute_attention_scores_for_universe(["AAPL", "MSFT"])
        assert result == {}
        mock_get.assert_not_called()

    def test_disabled_pytrends_also_not_attempted(self):
        # Regression: the master gate must short-circuit BEFORE the optional
        # pytrends overlay is ever considered, even if PYTRENDS_ENABLED=True.
        with patch("settings.settings.WIKIPEDIA_ATTENTION_ENABLED", False):
            with patch("settings.settings.PYTRENDS_ENABLED", True):
                with patch(
                    "data.attention_sources._fetch_pytrends_attention_score",
                ) as mock_pytrends:
                    result = compute_attention_scores_for_universe(["AAPL"])
        assert result == {}
        mock_pytrends.assert_not_called()

    def test_disabled_dashboard_column_stays_nan_via_map_default(self):
        # Mirrors pipeline/production_steps.py's write-back pattern: an
        # empty scores dict means every symbol's .get(sym, NaN) call falls
        # through to NaN, exactly reproducing today's placeholder fill.
        with patch("settings.settings.WIKIPEDIA_ATTENTION_ENABLED", False):
            scores = compute_attention_scores_for_universe(["AAPL", "MSFT"])
        assert math.isnan(scores.get("AAPL", float("nan")))
        assert math.isnan(scores.get("MSFT", float("nan")))

    def test_enabled_successful_fetch_populates_dict(self):
        series, as_of = _series(1000.0, 2000.0)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "items": [
                {"timestamp": d.replace("-", "") + "00", "views": v}
                for d, v in series.items()
            ]
        }
        with patch("settings.settings.WIKIPEDIA_ATTENTION_ENABLED", True):
            with patch("settings.settings.PYTRENDS_ENABLED", False):
                with patch("data.attention_sources.requests.get", return_value=mock_resp):
                    result = compute_attention_scores_for_universe(["AAPL"])
        assert "AAPL" in result
        assert not math.isnan(result["AAPL"])

    def test_one_bad_symbol_does_not_abort_batch(self):
        def raising_fetch(*args, **kwargs):
            raise RuntimeError("boom")

        with patch("settings.settings.WIKIPEDIA_ATTENTION_ENABLED", True):
            with patch("settings.settings.PYTRENDS_ENABLED", False):
                with patch(
                    "data.attention_sources.compute_attention_score",
                    side_effect=[RuntimeError("boom"), 0.5],
                ):
                    result = compute_attention_scores_for_universe(["BAD", "GOOD"])
        assert math.isnan(result["BAD"])
        assert result["GOOD"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Da/Engelberg/Gao (2011) citation sanity — module docstring
# ---------------------------------------------------------------------------

def test_module_cites_da_engelberg_gao():
    import data.attention_sources as mod
    assert "Da, Engelberg" in mod.__doc__
    assert "2011" in mod.__doc__
    assert "pytrends" in mod.__doc__.lower()
    assert "archived" in mod.__doc__.lower()
