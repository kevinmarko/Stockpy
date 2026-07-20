"""
tests/test_sentiment_risk_engine.py
====================================
Tests for sentiment_risk_engine.py — the honesty-fix for the Sentiment
Dynamics feature (CONSTRAINT #4: missing/unavailable data must render as an
honest null, never a fabricated plausible-looking number).

Coverage
--------
* compute_asymmetric_volatility: real arch_model GJR-GARCH(1,1,1) fit on a
  seeded synthetic returns series (no mocking, loose numeric bounds — mirrors
  test_quantitative_models.py::test_technical_options_engine_garch_volatility_and_ivr).
* compute_asymmetric_volatility: insufficient data (< 100 obs) -> honest
  (None, None, None), never the old hardcoded (0.0, 0.95, 0.15) defaults.
* compute_asymmetric_volatility: a narrowly-scoped causal-purity test (see its
  docstring for exactly what property it does / does not verify — a
  single-shot full-sample GARCH fit is not a rolling computation, so the
  standard lookahead test used elsewhere in this repo doesn't literally
  apply here).
* get_live_sentiment: agent returns empty/{} -> honest source="unavailable"
  shape (sentiment fields all None); agent returns real-looking data ->
  passthrough with source="antigravity_agent".
* generate_mock_sentiment: still works as an explicit demo/test utility,
  tagged source="mock" so it can never be confused with a real result.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from sentiment_risk_engine import SentimentRiskEngine, SentimentResult


# ---------------------------------------------------------------------------
# compute_asymmetric_volatility
# ---------------------------------------------------------------------------


def _synthetic_returns(n: int = 200, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    returns = rng.normal(0.0004, 0.014, n)
    return pd.Series(returns, index=dates)


class TestComputeAsymmetricVolatility:
    def test_happy_path_real_garch_fit(self):
        """Real arch_model GJR-GARCH(1,1,1) fit — no mocking, loose bounds."""
        engine = SentimentRiskEngine()
        returns = _synthetic_returns(n=200)

        gamma, persistence, vol = engine.compute_asymmetric_volatility(returns)

        assert isinstance(gamma, float)
        assert isinstance(persistence, float)
        assert isinstance(vol, float)
        assert not math.isnan(gamma)
        assert not math.isnan(persistence)
        assert vol > 0.0
        # alpha+beta+gamma/2 is a stability-adjacent quantity; a well-behaved
        # fit on stationary synthetic noise should land in a broad sane range.
        assert -0.5 <= persistence <= 1.5

    def test_insufficient_data_returns_honest_none(self):
        """< 100 observations -> (None, None, None), never a fabricated default."""
        engine = SentimentRiskEngine()
        short_returns = pd.Series(
            np.random.default_rng(1).normal(0.0, 0.01, 50),
            index=pd.date_range("2024-01-01", periods=50, freq="B"),
        )

        result = engine.compute_asymmetric_volatility(short_returns)

        assert result == (None, None, None)

    def test_fit_failure_returns_honest_none(self):
        """A raising fit degrades to (None, None, None), not the old (0.0, 0.95, 0.15)."""
        engine = SentimentRiskEngine()
        returns = _synthetic_returns(n=150)

        with mock.patch("sentiment_risk_engine.arch_model", side_effect=RuntimeError("boom")):
            result = engine.compute_asymmetric_volatility(returns)

        assert result == (None, None, None)

    def test_causal_purity_pure_function_of_input_series(self):
        """Narrowly-scoped causal-safety check.

        WHAT THIS DOES verify: compute_asymmetric_volatility is a pure,
        deterministic function of exactly the pd.Series passed to it — no
        hidden engine-level mutable state, no leakage from data that exists
        outside that Series. Fitting on an identical historical prefix
        returns bit-identical output on the SAME engine instance regardless
        of whether a *separately constructed*, longer series sharing that
        exact prefix also exists in memory (with a distinct, higher-variance
        "future" tail appended after the cutoff).

        WHAT THIS DOES NOT verify: that a rolling/expanding-window GARCH
        computation is lookahead-free in the sense
        test_quantitative_models.py::test_lookahead_bias_prevention checks
        for RSI/MACD. compute_asymmetric_volatility performs a single
        full-sample joint fit, not a rolling one — every parameter of a fit
        legitimately depends on every observation *actually included* in the
        sample it's fit on. That's why this test also asserts the full
        (shocked-tail-included) fit DIFFERS from the prefix-only fit: the
        function is expected to be sensitive to what's actually passed in.
        The honest, narrower property is that it is sensitive ONLY to what's
        passed in, and to nothing else.
        """
        n, cutoff = 220, 150
        rng = np.random.default_rng(7)
        prefix_returns = rng.normal(0.0004, 0.014, cutoff)
        # A higher-volatility "future" regime appended after the cutoff.
        tail_returns = rng.normal(0.0, 0.08, n - cutoff)

        idx_prefix = pd.date_range("2025-01-01", periods=cutoff, freq="B")
        idx_full = pd.date_range("2025-01-01", periods=n, freq="B")

        series_a = pd.Series(prefix_returns, index=idx_prefix)  # standalone prefix
        series_full = pd.Series(np.concatenate([prefix_returns, tail_returns]), index=idx_full)
        series_b_prefix = series_full.iloc[:cutoff]  # sliced from the longer series

        # Sanity: the two prefixes carry numerically identical values.
        pd.testing.assert_series_equal(series_a, series_b_prefix, check_names=False)

        engine = SentimentRiskEngine()

        gamma_a, persistence_a, vol_a = engine.compute_asymmetric_volatility(series_a)
        gamma_b, persistence_b, vol_b = engine.compute_asymmetric_volatility(series_b_prefix)

        assert gamma_a is not None and gamma_b is not None
        assert math.isclose(gamma_a, gamma_b, rel_tol=1e-9, abs_tol=1e-12)
        assert math.isclose(persistence_a, persistence_b, rel_tol=1e-9, abs_tol=1e-12)
        assert math.isclose(vol_a, vol_b, rel_tol=1e-9, abs_tol=1e-12)

        # And the full (shock-included) fit is expected to differ — a joint
        # full-sample fit legitimately depends on every row actually in the
        # sample, unlike a causal rolling computation.
        gamma_full, persistence_full, vol_full = engine.compute_asymmetric_volatility(series_full)
        assert gamma_full is None or not math.isclose(gamma_full, gamma_a, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# get_live_sentiment
# ---------------------------------------------------------------------------


class TestGetLiveSentiment:
    def test_agent_unavailable_returns_honest_shape(self):
        """analyze_sentiment() -> {} (empty/unavailable) -> honest unavailable shape.

        Uses a long-enough returns series that compute_asymmetric_volatility
        succeeds independently of the agent, demonstrating that an agent
        failure does not discard a legitimately-computed, unrelated causal
        metric (volatility_persistence is NOT derived from the agent).
        """
        engine = SentimentRiskEngine()
        returns = _synthetic_returns(n=200)
        date = datetime(2026, 7, 20, tzinfo=timezone.utc)

        with mock.patch(
            "engine.agent_sentiment.analyze_sentiment",
            new_callable=mock.AsyncMock,
            return_value={},
        ):
            result = asyncio.run(engine.get_live_sentiment("AAPL", date, returns))

        assert isinstance(result, SentimentResult)
        assert result.source == "unavailable"
        assert result.sentiment_score is None
        assert result.sentiment_intensity is None
        assert result.credibility_score is None
        # Independently computed from real price history — not forced to
        # None just because an unrelated data source (the agent) failed.
        assert result.volatility_persistence is not None
        assert isinstance(result.volatility_persistence, float)

    def test_agent_success_passes_through_real_values(self):
        """analyze_sentiment() returns real-looking data -> honest passthrough."""
        engine = SentimentRiskEngine()
        returns = _synthetic_returns(n=200)
        date = datetime(2026, 7, 20, tzinfo=timezone.utc)
        agent_payload = {
            "sentiment_score": 0.62,
            "sentiment_intensity": 0.81,
            "credibility_score": 0.9,
        }

        with mock.patch(
            "engine.agent_sentiment.analyze_sentiment",
            new_callable=mock.AsyncMock,
            return_value=agent_payload,
        ):
            result = asyncio.run(engine.get_live_sentiment("AAPL", date, returns))

        assert result.source == "antigravity_agent"
        assert result.sentiment_score == 0.62
        assert result.sentiment_intensity == 0.81
        assert result.credibility_score == 0.9
        assert result.volatility_persistence is not None

    def test_agent_unavailable_with_insufficient_price_history(self):
        """Both the agent AND the GARCH fit are honestly unavailable together."""
        engine = SentimentRiskEngine()
        short_returns = pd.Series(
            np.random.default_rng(2).normal(0.0, 0.01, 30),
            index=pd.date_range("2024-01-01", periods=30, freq="B"),
        )
        date = datetime(2026, 7, 20, tzinfo=timezone.utc)

        with mock.patch(
            "engine.agent_sentiment.analyze_sentiment",
            new_callable=mock.AsyncMock,
            return_value={},
        ):
            result = asyncio.run(engine.get_live_sentiment("ZZZZ", date, short_returns))

        assert result.source == "unavailable"
        assert result.sentiment_score is None
        assert result.sentiment_intensity is None
        assert result.credibility_score is None
        assert result.volatility_persistence is None


# ---------------------------------------------------------------------------
# generate_mock_sentiment — explicit demo/test utility, unwired from the live path
# ---------------------------------------------------------------------------


class TestGenerateMockSentiment:
    def test_returns_source_mock(self):
        engine = SentimentRiskEngine()
        returns = _synthetic_returns(n=200)
        date = datetime(2026, 7, 20, tzinfo=timezone.utc)

        result = engine.generate_mock_sentiment("AAPL", date, returns)

        assert isinstance(result, SentimentResult)
        assert result.source == "mock"
        assert result.sentiment_score is not None
        assert -1.0 <= result.sentiment_score <= 1.0

    def test_never_wired_into_get_live_sentiment(self):
        """get_live_sentiment must not call generate_mock_sentiment as a fallback."""
        engine = SentimentRiskEngine()
        returns = _synthetic_returns(n=200)
        date = datetime(2026, 7, 20, tzinfo=timezone.utc)

        with (
            mock.patch(
                "engine.agent_sentiment.analyze_sentiment",
                new_callable=mock.AsyncMock,
                return_value={},
            ),
            mock.patch.object(
                SentimentRiskEngine, "generate_mock_sentiment"
            ) as mock_generate,
        ):
            result = asyncio.run(engine.get_live_sentiment("AAPL", date, returns))

        mock_generate.assert_not_called()
        assert result.source == "unavailable"
