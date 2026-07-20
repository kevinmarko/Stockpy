"""
tests/test_data_api_ai.py
==========================
Fully-offline tests for the three on-demand AI generation endpoints added to
``api/data_api.py`` (``POST /data/ai/commentary/{symbol}``,
``POST /data/ai/chart/{symbol}``, ``POST /data/ai/research/{symbol}``).

Every generator (``generate_for_symbol_row`` / ``generate_chart_pattern_read`` /
``render_price_chart_png`` / ``generate_research_brief``) and the snapshot
loader (``load_snapshot``) are monkeypatched on the ``api.data_api`` module
namespace — no real network/API call, no real heavy-engine construction, no
real ``output/state_snapshot.json`` read ever happens here.

Proves the honest soft-fail contract (CONSTRAINT #6): capability-off,
missing-key, generator-returned-``None``, and generator-raised-an-exception
all degrade to a 200 with a self-describing ``reason``, never a 500 — and the
honesty rule (CONSTRAINT #4): an unknown symbol is a 404, never a fabricated
row.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pandas as pd
import numpy as np
import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.data_api as data_api

client = TestClient(data_api.app)


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


def _snapshot(symbols=("AAPL",)):
    return {
        "signals": [
            {"symbol": s, "action": "BUY", "score": 62.0, "advisory_conviction": 0.7}
            for s in symbols
        ]
    }


def _bars(n: int = 5) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "Open": np.linspace(100, 104, n),
            "High": np.linspace(101, 105, n),
            "Low": np.linspace(99, 103, n),
            "Close": np.linspace(100.5, 104.5, n),
            "Volume": [1_000_000] * n,
        },
        index=idx,
    )


class _FakeProvider:
    def __init__(self, bars=None, raises=False):
        self._bars = bars
        self._raises = raises

    def get_intraday_bars(self, symbol, lookback_days=252):
        if self._raises:
            raise data_api.MarketDataError(f"no bars for {symbol}")
        return self._bars


def _model_dump_result(payload):
    """A stand-in for a pydantic schema instance — only needs ``model_dump``."""
    return SimpleNamespace(model_dump=lambda: payload)


def _enable_llm_commentary(anthropic=True, gemini=True):
    return [
        mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", True),
        mock.patch.object(settings, "ANTHROPIC_API_KEY", "sk-ant-test" if anthropic else None),
        mock.patch.object(settings, "GEMINI_API_KEY", "gk-test" if gemini else None),
    ]


def _apply(patches):
    for p in patches:
        p.start()
    return patches


def _stop(patches):
    for p in patches:
        p.stop()


@pytest.fixture(autouse=True)
def _ai_generation_api_enabled(monkeypatch):
    """Every test below exercises a specific endpoint's OWN capability-flag
    behavior (LLM_COMMENTARY_ENABLED, OPAL_RESEARCH_ENABLED, ...), not the
    master ``AI_GENERATION_API_ENABLED`` gate added afterward — default it to
    ``True`` here so those tests are unaffected. The dedicated
    ``TestAiGenerationApiEnabledGate`` class below explicitly overrides it
    back to ``False`` to test the 403 branch itself."""
    monkeypatch.setattr(settings, "AI_GENERATION_API_ENABLED", True)


# ---------------------------------------------------------------------------
# Auth (require_token) — reuse the existing fail-open/fail-closed posture
# ---------------------------------------------------------------------------


def test_commentary_401_with_wrong_token():
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
        resp = client.post(
            "/data/ai/commentary/AAPL", headers={"Authorization": "Bearer nope"}
        )
    assert resp.status_code == 401


def test_commentary_401_missing_token():
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
        resp = client.post("/data/ai/commentary/AAPL")
    assert resp.status_code == 401


def test_chart_401_with_wrong_token():
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
        resp = client.post("/data/ai/chart/AAPL", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_research_401_with_wrong_token():
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
        resp = client.post("/data/ai/research/AAPL", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# require_ai_capability_enabled — direct unit coverage of the (currently
# unwired-into-any-endpoint) reusable dependency factory.
# ---------------------------------------------------------------------------


def test_require_ai_capability_enabled_raises_403_when_flag_off():
    dep = data_api.require_ai_capability_enabled("LLM_COMMENTARY_ENABLED", "Commentary")
    with mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", False):
        with pytest.raises(Exception) as exc_info:
            dep()
    assert getattr(exc_info.value, "status_code", None) == 403


def test_require_ai_capability_enabled_passes_when_flag_on():
    dep = data_api.require_ai_capability_enabled("LLM_COMMENTARY_ENABLED", "Commentary")
    with mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", True):
        assert dep() is None  # no exception


# ---------------------------------------------------------------------------
# AI_GENERATION_API_ENABLED — the master gate wired onto all three endpoints.
# api/data_api.py is fail-open when STATE_API_TOKEN is unset (the documented
# zero-config default), so this flag is the ONLY thing standing between "an
# operator enabled LLM_COMMENTARY_ENABLED for their own Streamlit desktop use"
# and "anyone who can reach the data API can trigger a paid Claude call."
# Default False (see settings.py); the autouse fixture above flips it True for
# every OTHER test in this file, so these tests explicitly override it back.
# ---------------------------------------------------------------------------


def test_commentary_403_when_ai_generation_api_disabled(monkeypatch):
    monkeypatch.setattr(settings, "AI_GENERATION_API_ENABLED", False)
    monkeypatch.setattr(data_api, "load_snapshot", lambda: _snapshot(["AAPL"]))
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", True), \
         mock.patch.object(settings, "ANTHROPIC_API_KEY", "sk-ant-test"):
        resp = client.post("/data/ai/commentary/AAPL")
    # 403, not 200 -- even though the underlying capability is fully enabled
    # and configured, the master HTTP-exposure gate takes precedence.
    assert resp.status_code == 403
    assert "AI_GENERATION_API_ENABLED" in resp.json()["detail"]


def test_chart_403_when_ai_generation_api_disabled(monkeypatch):
    monkeypatch.setattr(settings, "AI_GENERATION_API_ENABLED", False)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.post("/data/ai/chart/AAPL")
    assert resp.status_code == 403


def test_research_403_when_ai_generation_api_disabled(monkeypatch):
    monkeypatch.setattr(settings, "AI_GENERATION_API_ENABLED", False)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.post("/data/ai/research/AAPL")
    assert resp.status_code == 403


def test_commentary_reaches_capability_logic_when_ai_generation_api_enabled(monkeypatch):
    # Sanity check for the fixture itself: with the master gate on (the
    # autouse default) but the underlying capability off, the endpoint still
    # reaches its own soft-fail branch (200, not 403) -- proving the two gates
    # are independent, not that the master gate silently swallows everything.
    monkeypatch.setattr(data_api, "load_snapshot", lambda: _snapshot(["AAPL"]))
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", False):
        resp = client.post("/data/ai/commentary/AAPL")
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "reason": "disabled", "payload": None}


def test_ai_generation_api_enabled_is_not_gui_writable():
    from gui import env_io

    assert "AI_GENERATION_API_ENABLED" not in env_io.ALLOWED_KEYS
    assert "AI_GENERATION_API_ENABLED" not in env_io.SECRET_KEYS


# ---------------------------------------------------------------------------
# POST /data/ai/commentary/{symbol}
# ---------------------------------------------------------------------------


def test_commentary_symbol_not_found_is_404(monkeypatch):
    monkeypatch.setattr(data_api, "load_snapshot", lambda: _snapshot(["MSFT"]))
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.post("/data/ai/commentary/AAPL")
    assert resp.status_code == 404


def test_commentary_no_snapshot_is_404(monkeypatch):
    monkeypatch.setattr(data_api, "load_snapshot", lambda: None)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.post("/data/ai/commentary/AAPL")
    assert resp.status_code == 404


def test_commentary_disabled(monkeypatch):
    monkeypatch.setattr(data_api, "load_snapshot", lambda: _snapshot(["AAPL"]))
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", False):
        resp = client.post("/data/ai/commentary/AAPL")
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "reason": "disabled", "payload": None}


def test_commentary_missing_key(monkeypatch):
    monkeypatch.setattr(data_api, "load_snapshot", lambda: _snapshot(["AAPL"]))
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", True), \
         mock.patch.object(settings, "ANTHROPIC_API_KEY", None):
        resp = client.post("/data/ai/commentary/AAPL")
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "reason": "missing_key", "payload": None}


def test_commentary_success(monkeypatch):
    monkeypatch.setattr(data_api, "load_snapshot", lambda: _snapshot(["AAPL"]))
    captured = {}

    def _fake_generate(row):
        captured["row"] = row
        return {
            "headline": "Mean-reversion entry",
            "why_now": "Oversold bounce off support.",
            "key_risks": ["Macro shock"],
            "invalidation": "Close below the 50-day SMA.",
        }

    monkeypatch.setattr(data_api, "generate_for_symbol_row", _fake_generate)
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", True), \
         mock.patch.object(settings, "ANTHROPIC_API_KEY", "sk-ant-test"):
        resp = client.post("/data/ai/commentary/aapl")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["reason"] is None
    assert body["payload"]["headline"] == "Mean-reversion entry"
    assert captured["row"]["symbol"] == "AAPL"


def test_commentary_generator_returns_none_is_soft_fail(monkeypatch):
    monkeypatch.setattr(data_api, "load_snapshot", lambda: _snapshot(["AAPL"]))
    monkeypatch.setattr(data_api, "generate_for_symbol_row", lambda row: None)
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", True), \
         mock.patch.object(settings, "ANTHROPIC_API_KEY", "sk-ant-test"):
        resp = client.post("/data/ai/commentary/AAPL")
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "reason": "generation_failed", "payload": None}


def test_commentary_generator_raises_is_soft_fail_not_500(monkeypatch):
    monkeypatch.setattr(data_api, "load_snapshot", lambda: _snapshot(["AAPL"]))

    def _boom(row):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(data_api, "generate_for_symbol_row", _boom)
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", True), \
         mock.patch.object(settings, "ANTHROPIC_API_KEY", "sk-ant-test"):
        resp = client.post("/data/ai/commentary/AAPL")
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "reason": "generation_failed", "payload": None}


# ---------------------------------------------------------------------------
# POST /data/ai/chart/{symbol}
# ---------------------------------------------------------------------------


def test_chart_no_bars(monkeypatch):
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(bars=None))
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.post("/data/ai/chart/AAPL")
    assert resp.status_code == 200
    assert resp.json() == {
        "available": False,
        "reason": "no_bars",
        "payload": None,
        "chart_png_base64": None,
    }


def test_chart_empty_bars_is_no_bars(monkeypatch):
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(bars=pd.DataFrame()))
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.post("/data/ai/chart/AAPL")
    assert resp.status_code == 200
    assert resp.json()["reason"] == "no_bars"


def test_chart_bars_fetch_raises_is_no_bars(monkeypatch):
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(raises=True))
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.post("/data/ai/chart/AAPL")
    assert resp.status_code == 200
    assert resp.json()["reason"] == "no_bars"


def test_chart_render_failed(monkeypatch):
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(bars=_bars()))
    monkeypatch.setattr(data_api, "render_price_chart_png", lambda symbol, bars: None)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.post("/data/ai/chart/AAPL")
    assert resp.status_code == 200
    assert resp.json() == {
        "available": False,
        "reason": "chart_render_failed",
        "payload": None,
        "chart_png_base64": None,
    }


def test_chart_disabled_still_returns_rendered_chart(monkeypatch):
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(bars=_bars()))
    monkeypatch.setattr(data_api, "render_price_chart_png", lambda symbol, bars: b"PNGDATA")
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", False):
        resp = client.post("/data/ai/chart/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "disabled"
    assert body["payload"] is None
    assert body["chart_png_base64"]  # chart still returned even though AI read is off


def test_chart_missing_key(monkeypatch):
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(bars=_bars()))
    monkeypatch.setattr(data_api, "render_price_chart_png", lambda symbol, bars: b"PNGDATA")
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", True), \
         mock.patch.object(settings, "GEMINI_API_KEY", None):
        resp = client.post("/data/ai/chart/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] == "missing_key"
    assert body["chart_png_base64"]


def test_chart_success(monkeypatch):
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(bars=_bars()))
    monkeypatch.setattr(data_api, "render_price_chart_png", lambda symbol, bars: b"PNGDATA")
    monkeypatch.setattr(
        data_api,
        "generate_chart_pattern_read",
        lambda symbol, bars: _model_dump_result(
            {
                "pattern_name": "ascending triangle",
                "trend_direction": "bullish",
                "support_levels": [],
                "resistance_levels": [],
                "narrative": "Chart looks constructive.",
                "confidence": "medium",
            }
        ),
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", True), \
         mock.patch.object(settings, "GEMINI_API_KEY", "gk-test"):
        resp = client.post("/data/ai/chart/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["reason"] is None
    assert body["payload"]["pattern_name"] == "ascending triangle"
    assert body["chart_png_base64"]
    import base64

    assert base64.b64decode(body["chart_png_base64"]) == b"PNGDATA"


def test_chart_generator_returns_none_still_returns_chart(monkeypatch):
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(bars=_bars()))
    monkeypatch.setattr(data_api, "render_price_chart_png", lambda symbol, bars: b"PNGDATA")
    monkeypatch.setattr(data_api, "generate_chart_pattern_read", lambda symbol, bars: None)
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", True), \
         mock.patch.object(settings, "GEMINI_API_KEY", "gk-test"):
        resp = client.post("/data/ai/chart/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "generation_failed"
    assert body["payload"] is None
    assert body["chart_png_base64"]  # chart still returned per contract


def test_chart_generator_raises_is_soft_fail_not_500(monkeypatch):
    monkeypatch.setattr(data_api, "get_provider", lambda: _FakeProvider(bars=_bars()))
    monkeypatch.setattr(data_api, "render_price_chart_png", lambda symbol, bars: b"PNGDATA")

    def _boom(symbol, bars):
        raise RuntimeError("gemini exploded")

    monkeypatch.setattr(data_api, "generate_chart_pattern_read", _boom)
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "LLM_COMMENTARY_ENABLED", True), \
         mock.patch.object(settings, "GEMINI_API_KEY", "gk-test"):
        resp = client.post("/data/ai/chart/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "generation_failed"


# ---------------------------------------------------------------------------
# POST /data/ai/research/{symbol}
# ---------------------------------------------------------------------------


def test_research_disabled():
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "OPAL_RESEARCH_ENABLED", False):
        resp = client.post("/data/ai/research/AAPL")
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "reason": "disabled", "payload": None}


def test_research_success(monkeypatch):
    monkeypatch.setattr(
        data_api,
        "generate_research_brief",
        lambda symbol, context=None: _model_dump_result(
            {
                "thesis_context": "Setup looks constructive given recent headlines.",
                "catalysts": ["Q3 earnings call scheduled"],
                "risk_factors": [],
                "recent_developments": [],
                "data_confidence": "medium",
                "sources_note": "Based on 2 Finnhub headlines.",
            }
        ),
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "OPAL_RESEARCH_ENABLED", True):
        resp = client.post("/data/ai/research/aapl")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["reason"] is None
    assert body["payload"]["sources_note"] == "Based on 2 Finnhub headlines."


def test_research_generator_returns_none_is_soft_fail(monkeypatch):
    monkeypatch.setattr(data_api, "generate_research_brief", lambda symbol, context=None: None)
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "OPAL_RESEARCH_ENABLED", True):
        resp = client.post("/data/ai/research/AAPL")
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "reason": "generation_failed", "payload": None}


def test_research_generator_raises_is_soft_fail_not_500(monkeypatch):
    def _boom(symbol, context=None):
        raise RuntimeError("opal exploded")

    monkeypatch.setattr(data_api, "generate_research_brief", _boom)
    with mock.patch.object(settings, "STATE_API_TOKEN", None), \
         mock.patch.object(settings, "OPAL_RESEARCH_ENABLED", True):
        resp = client.post("/data/ai/research/AAPL")
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "reason": "generation_failed", "payload": None}
