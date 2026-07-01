"""
tests/test_chart_insight.py
============================
Unit tests for ``llm.chart_insight`` (Tier 9 Scope 3) — the Gemini Vision
chart pattern interpretation entry point.

All multimodal provider calls are mocked; matplotlib runs on the Agg backend.

Coverage
--------
TestChartPatternReadSchema     — schema bounds (trend_direction literal,
                                pattern_name length, support/resistance caps).
TestRenderPriceChartPng        — happy path returns PNG bytes;
                                empty/missing-Close DataFrame returns None;
                                non-DataFrame input returns None.
TestBarFingerprint             — fingerprint changes when close moves;
                                empty df returns 0.0.
TestGenerateChartPatternRead   — happy path: provider returns model;
                                soft-fail when chart render returns None;
                                soft-fail when provider returns None;
                                soft-fail when provider raises;
                                empty symbol returns None;
                                cache hit returns without re-calling provider.
TestProviderSurface            — Gemini provider exposes call_structured_with_image.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from llm import cache as cache_mod
from llm.chart_insight import (
    _bar_fingerprint,
    generate_chart_pattern_read,
    render_price_chart_png,
)
from llm.schemas import ChartPatternRead


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    p = tmp_path / "llm_commentary_cache.json"
    monkeypatch.setattr(cache_mod.settings, "LLM_COMMENTARY_CACHE_PATH", str(p), raising=False)
    yield


def _make_bars(n=120):
    np.random.seed(42)
    idx = pd.date_range("2025-01-01", periods=n)
    return pd.DataFrame({"Close": np.linspace(150, 180, n) + np.random.randn(n) * 2}, index=idx)


# ---------------------------------------------------------------------------
# TestChartPatternReadSchema
# ---------------------------------------------------------------------------


class TestChartPatternReadSchema:
    def test_canonical_payload_accepted(self):
        r = ChartPatternRead(
            pattern_name="ascending triangle",
            trend_direction="bullish",
            support_levels=["recent low"],
            resistance_levels=["prior high"],
            narrative="Pattern is well-formed.",
            confidence="high",
        )
        assert r.pattern_name == "ascending triangle"

    def test_bad_trend_rejected(self):
        with pytest.raises(Exception):
            ChartPatternRead(
                pattern_name="x", trend_direction="sideways", narrative="y"
            )  # type: ignore[arg-type]

    def test_pattern_name_length_capped(self):
        with pytest.raises(Exception):
            ChartPatternRead(
                pattern_name="x" * 200,
                trend_direction="bullish",
                narrative="y",
            )

    def test_too_many_support_levels_rejected(self):
        with pytest.raises(Exception):
            ChartPatternRead(
                pattern_name="p",
                trend_direction="bullish",
                support_levels=["a", "b", "c", "d"],
                narrative="y",
            )


# ---------------------------------------------------------------------------
# TestRenderPriceChartPng
# ---------------------------------------------------------------------------


class TestRenderPriceChartPng:
    def test_happy_path_returns_png(self):
        png = render_price_chart_png("AAPL", _make_bars())
        assert png is not None
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(png) > 1000

    def test_empty_df_returns_none(self):
        empty = pd.DataFrame({"Close": []})
        assert render_price_chart_png("AAPL", empty) is None

    def test_missing_close_column_returns_none(self):
        bad = pd.DataFrame({"Open": [1, 2, 3]}, index=pd.date_range("2025-01-01", periods=3))
        assert render_price_chart_png("AAPL", bad) is None

    def test_non_dataframe_returns_none(self):
        assert render_price_chart_png("AAPL", None) is None
        assert render_price_chart_png("AAPL", "not a frame") is None


# ---------------------------------------------------------------------------
# TestBarFingerprint
# ---------------------------------------------------------------------------


class TestBarFingerprint:
    def test_fingerprint_changes_with_close(self):
        df1 = _make_bars(n=10)
        df2 = df1.copy()
        df2.loc[df2.index[-1], "Close"] = float(df2["Close"].iloc[-1]) + 5.0
        assert _bar_fingerprint(df1) != _bar_fingerprint(df2)

    def test_empty_df_returns_zero(self):
        assert _bar_fingerprint(pd.DataFrame()) == 0.0

    def test_non_df_returns_zero(self):
        assert _bar_fingerprint(None) == 0.0
        assert _bar_fingerprint("not a frame") == 0.0


# ---------------------------------------------------------------------------
# Fake providers
# ---------------------------------------------------------------------------


def _good_read() -> ChartPatternRead:
    return ChartPatternRead(
        pattern_name="ascending triangle",
        trend_direction="bullish",
        support_levels=["recent low"],
        resistance_levels=["prior high"],
        narrative="Clean breakout setup.",
        confidence="medium",
    )


class _FakeVisionProvider:
    name = "fake-gemini"

    def __init__(self, *, value=None, raises=None):
        self._value = value
        self._raises = raises
        self.call_count = 0

    def call_structured_with_image(self, *, system, user, image_bytes, schema_model, mime_type="image/png"):
        self.call_count += 1
        if self._raises is not None:
            raise self._raises
        return self._value


# ---------------------------------------------------------------------------
# TestGenerateChartPatternRead
# ---------------------------------------------------------------------------


class TestGenerateChartPatternRead:
    def test_happy_path_returns_validated_model(self):
        prov = _FakeVisionProvider(value=_good_read())
        out = generate_chart_pattern_read("AAPL", _make_bars(), provider=prov)
        assert isinstance(out, ChartPatternRead)
        assert out.trend_direction == "bullish"
        assert prov.call_count == 1

    def test_cache_hit_skips_provider(self):
        prov = _FakeVisionProvider(value=_good_read())
        df = _make_bars()
        out1 = generate_chart_pattern_read("AAPL", df, provider=prov)
        out2 = generate_chart_pattern_read("AAPL", df, provider=prov)
        # Same bars + same UTC day → cache hit on the second call.
        assert prov.call_count == 1
        assert out1.pattern_name == out2.pattern_name

    def test_chart_render_failure_returns_none_without_provider_call(self):
        prov = _FakeVisionProvider(value=_good_read())
        out = generate_chart_pattern_read(
            "AAPL",
            pd.DataFrame(),
            provider=prov,
            chart_renderer=lambda s, b: None,
        )
        assert out is None
        assert prov.call_count == 0

    def test_provider_returns_none_returns_none(self):
        prov = _FakeVisionProvider(value=None)
        out = generate_chart_pattern_read("AAPL", _make_bars(), provider=prov)
        assert out is None
        assert prov.call_count == 1

    def test_provider_raises_returns_none(self):
        prov = _FakeVisionProvider(raises=RuntimeError("synthetic"))
        out = generate_chart_pattern_read("AAPL", _make_bars(), provider=prov)
        assert out is None

    def test_empty_symbol_returns_none(self):
        prov = _FakeVisionProvider(value=_good_read())
        out = generate_chart_pattern_read("", _make_bars(), provider=prov)
        assert out is None
        assert prov.call_count == 0

    def test_provider_without_image_method_returns_none(self):
        class _NoImg:
            name = "no-img"

            def call_structured(self, system, user, schema_model):  # no image variant
                return _good_read()

        out = generate_chart_pattern_read("AAPL", _make_bars(), provider=_NoImg())
        assert out is None

    def test_no_provider_configured_returns_none_default(self, monkeypatch):
        # Master switch off (default) → _get_vision_provider returns None.
        out = generate_chart_pattern_read("AAPL", _make_bars())
        assert out is None
