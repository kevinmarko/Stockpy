"""
tests/test_data_api_ai_disagreements.py
========================================
Tests for ``GET /data/ai/disagreements`` (api/data_api.py) — the durable,
webapp-only equivalent of the legacy Streamlit AI Insights tab's "Aggregate
Claude vs Gemini disagreement" table (G15 of the GUI->webapp parity effort).

Unlike the legacy tab (which reads two ``st.session_state`` mirrors that only
exist within one browser session), this endpoint reconstructs the same
{symbol: verdict} maps from the DURABLE on-disk LLM commentary cache
(``llm/cache.py``) via ``shared.ai_insights_panel.latest_verdict_maps_from_cache``.

``load_snapshot`` and ``llm.cache.read_all_entries`` are both monkeypatched at
the ``api.data_api`` import site (or their source module for the lazy
``llm.cache`` import) — no real ``output/state_snapshot.json`` or
``output/llm_commentary_cache.json`` read ever happens here, matching
``tests/test_data_api_ai.py``'s existing fully-offline convention.
"""
from __future__ import annotations

from unittest import mock

from fastapi.testclient import TestClient

import api.data_api as data_api

# Starlette's TestClient defaults request.client.host to the literal string
# "testclient" -- NOT loopback -- which would trip api.auth.require_read_token's
# fail-closed-when-non-loopback branch (mirrors tests/test_data_api_ai.py).
client = TestClient(data_api.app, client=("127.0.0.1", 54124))


def _snapshot(symbols=("AAPL", "MSFT")):
    return {"signals": [{"symbol": s, "action": "BUY"} for s in symbols]}


def _cache_entry(*, symbol, payload, stored_at="2026-07-30T00:00:00+00:00", provider="claude"):
    return {"payload": payload, "meta": {"provider": provider, "symbol": symbol}, "stored_at": stored_at}


def test_no_snapshot_yet_returns_honest_empty_shape():
    with mock.patch.object(data_api, "load_snapshot", return_value=None):
        resp = client.get("/data/ai/disagreements")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] == []
    assert body["summary"] == {
        "total_symbols": 0, "both_present": 0, "agreements": 0, "disagreements": 0,
    }
    assert body["reason"]


def test_empty_signals_list_returns_honest_empty_shape():
    with mock.patch.object(data_api, "load_snapshot", return_value={"signals": []}):
        resp = client.get("/data/ai/disagreements")
    assert resp.status_code == 200
    assert resp.json()["rows"] == []


def test_no_cached_verdicts_yields_all_none_never_a_fabricated_verdict():
    with mock.patch.object(data_api, "load_snapshot", return_value=_snapshot(("AAPL",))):
        with mock.patch("llm.cache.read_all_entries", return_value=[]):
            resp = client.get("/data/ai/disagreements")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] is None
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["symbol"] == "AAPL"
    assert row["claude_verdict"] is None
    assert row["gemini_verdict"] is None
    assert row["disagreement"] is False
    assert body["summary"]["total_symbols"] == 1
    assert body["summary"]["both_present"] == 0


def test_warm_path_surfaces_agreement_and_disagreement_from_the_durable_cache():
    # AnalystRationale is a `extra="forbid"` pydantic model with ONLY
    # headline/why_now/key_risks/invalidation fields (llm/schemas.py) -- it
    # never carries `trend_direction` in real data, so the Claude side's
    # direction comes from the heuristic headline scan
    # (shared.ai_insights_panel._heuristic_direction_from_rationale), not an
    # explicit field. Headlines below are chosen to hit that heuristic.
    entries = [
        _cache_entry(
            symbol="AAPL",
            payload={"headline": "Breakout above resistance confirmed", "why_now": "text"},
            provider="claude",
        ),
        _cache_entry(
            symbol="AAPL",
            payload={"pattern_name": "flag", "trend_direction": "bullish", "confidence": "high"},
            provider="gemini",
        ),
        _cache_entry(
            symbol="MSFT",
            payload={"headline": "Bearish breakdown below support", "why_now": "text"},
            provider="claude",
        ),
        _cache_entry(
            symbol="MSFT",
            payload={"pattern_name": "wedge", "trend_direction": "bullish", "confidence": "low"},
            provider="gemini",
        ),
    ]
    with mock.patch.object(data_api, "load_snapshot", return_value=_snapshot(("AAPL", "MSFT"))):
        with mock.patch("llm.cache.read_all_entries", return_value=entries):
            resp = client.get("/data/ai/disagreements")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] is None
    by_symbol = {r["symbol"]: r for r in body["rows"]}
    assert by_symbol["AAPL"]["claude_verdict"] == "bullish"
    assert by_symbol["AAPL"]["gemini_verdict"] == "bullish"
    assert by_symbol["AAPL"]["disagreement"] is False
    assert by_symbol["MSFT"]["claude_verdict"] == "bearish"
    assert by_symbol["MSFT"]["gemini_verdict"] == "bullish"
    assert by_symbol["MSFT"]["disagreement"] is True
    assert body["summary"]["both_present"] == 2
    assert body["summary"]["disagreements"] == 1
    assert body["summary"]["agreements"] == 1


def test_only_the_most_recent_entry_per_symbol_and_side_is_used():
    entries = [
        _cache_entry(
            symbol="AAPL",
            payload={"headline": "Bearish sell-off underway", "why_now": "old text"},
            stored_at="2026-07-01T00:00:00+00:00",
        ),
        _cache_entry(
            symbol="AAPL",
            payload={"headline": "Bullish breakout above the range", "why_now": "new text"},
            stored_at="2026-07-29T00:00:00+00:00",
        ),
    ]
    with mock.patch.object(data_api, "load_snapshot", return_value=_snapshot(("AAPL",))):
        with mock.patch("llm.cache.read_all_entries", return_value=entries):
            resp = client.get("/data/ai/disagreements")
    row = resp.json()["rows"][0]
    assert row["claude_verdict"] == "bullish"


def test_alert_and_research_cache_entries_are_ignored_not_misclassified():
    """AlertCommentary (`body`) and ResearchBrief (`thesis_context`) payloads
    match neither disambiguation branch and must be silently skipped, not
    misread as a Claude or Gemini verdict."""
    entries = [
        _cache_entry(symbol="AAPL", payload={"body": "push notif text"}, provider="gemini"),
        _cache_entry(symbol="AAPL", payload={"thesis_context": "some thesis"}, provider="openai"),
    ]
    with mock.patch.object(data_api, "load_snapshot", return_value=_snapshot(("AAPL",))):
        with mock.patch("llm.cache.read_all_entries", return_value=entries):
            resp = client.get("/data/ai/disagreements")
    row = resp.json()["rows"][0]
    assert row["claude_verdict"] is None
    assert row["gemini_verdict"] is None


def test_cache_read_failure_degrades_to_honest_empty_never_500():
    with mock.patch.object(data_api, "load_snapshot", return_value=_snapshot(("AAPL",))):
        with mock.patch("llm.cache.read_all_entries", side_effect=RuntimeError("disk error")):
            resp = client.get("/data/ai/disagreements")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] == []
    assert body["reason"]


def test_fail_open_when_no_token_configured(monkeypatch):
    from settings import settings

    monkeypatch.setattr(settings, "STATE_API_TOKEN", None, raising=False)
    with mock.patch.object(data_api, "load_snapshot", return_value=None):
        resp = client.get("/data/ai/disagreements")
    assert resp.status_code == 200


def test_401_with_wrong_token_when_configured(monkeypatch):
    from settings import settings

    monkeypatch.setattr(settings, "STATE_API_TOKEN", "secret-token", raising=False)
    with mock.patch.object(data_api, "load_snapshot", return_value=None):
        resp = client.get(
            "/data/ai/disagreements", headers={"Authorization": "Bearer wrong"}
        )
    assert resp.status_code == 401
