"""
tests/test_llm_commentary.py
=============================
Unit tests for ``llm.commentary`` + ``llm.cache``.

All provider calls are monkeypatched.  No real API requests are made.

Coverage
--------
TestCacheKey                — sha256 key is deterministic across same UTC day;
                              changes with provider / symbol / action / score bucket.
TestCacheStore              — cache_put then cache_get round-trip; missing key → None;
                              clear empties the file; corrupt JSON returns empty dict.
TestGenerateRationaleEnabled — provider returns valid schema → AnalystRationale instance;
                              second call hits cache (provider call_count == 1).
TestGenerateRationaleDisabled — master switch off → no provider instantiated, returns None.
TestGenerateRationaleSoftFail — provider returns None → returns None (template fallback).
TestGenerateAlertEnabled     — analogous Gemini path.
TestGenerateAlertDisabled    — analogous master-switch-off path.
TestCacheCorruptEntry        — cached payload that fails schema → re-fetched on next call.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from llm import cache as cache_mod
from llm import commentary as commentary_mod
from llm.cache import cache_clear, cache_get, cache_put, make_cache_key
from llm.commentary import generate_alert_commentary, generate_analyst_rationale
from llm.schemas import AlertCommentary, AnalystRationale


# ---------------------------------------------------------------------------
# Fixture — pin cache path to a temp dir per test so we never touch real disk.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    # Point the cache to a per-test temp file so tests can run in parallel
    # without trampling each other or polluting the operator's `output/`.
    p = tmp_path / "llm_commentary_cache.json"
    monkeypatch.setattr(
        cache_mod.settings, "LLM_COMMENTARY_CACHE_PATH", str(p), raising=False
    )
    yield


# ---------------------------------------------------------------------------
# TestCacheKey
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_same_inputs_same_key(self):
        k1 = make_cache_key(
            provider="claude", schema_name="X", symbol="AAPL", score=72.3, action="BUY"
        )
        k2 = make_cache_key(
            provider="claude", schema_name="X", symbol="AAPL", score=72.3, action="BUY"
        )
        assert k1 == k2

    def test_score_bucket_tolerant_to_small_jitter(self):
        # 72.1 and 73.9 both fall in bucket 14 (floor(72/5)=14, floor(73/5)=14).
        k_low = make_cache_key(
            provider="claude", schema_name="X", symbol="AAPL", score=72.1, action="BUY"
        )
        k_hi = make_cache_key(
            provider="claude", schema_name="X", symbol="AAPL", score=73.9, action="BUY"
        )
        assert k_low == k_hi

    def test_score_bucket_changes_on_meaningful_move(self):
        # 47 (bucket 9) vs 52 (bucket 10) cross a bucket boundary.
        k_a = make_cache_key(
            provider="claude", schema_name="X", symbol="AAPL", score=47.0, action="BUY"
        )
        k_b = make_cache_key(
            provider="claude", schema_name="X", symbol="AAPL", score=52.0, action="BUY"
        )
        assert k_a != k_b

    def test_action_change_invalidates(self):
        k_buy = make_cache_key(
            provider="claude", schema_name="X", symbol="AAPL", score=50.0, action="BUY"
        )
        k_sell = make_cache_key(
            provider="claude", schema_name="X", symbol="AAPL", score=50.0, action="SELL"
        )
        assert k_buy != k_sell

    def test_provider_change_invalidates(self):
        k_c = make_cache_key(
            provider="claude", schema_name="X", symbol="A", score=10.0, action="HOLD"
        )
        k_g = make_cache_key(
            provider="gemini", schema_name="X", symbol="A", score=10.0, action="HOLD"
        )
        assert k_c != k_g

    def test_symbol_normalised_to_upper(self):
        k_lo = make_cache_key(
            provider="claude", schema_name="X", symbol="aapl", score=10.0, action="HOLD"
        )
        k_up = make_cache_key(
            provider="claude", schema_name="X", symbol="AAPL", score=10.0, action="HOLD"
        )
        assert k_lo == k_up

    def test_date_change_invalidates(self):
        k_t = make_cache_key(
            provider="claude", schema_name="X", symbol="A", score=10.0, action="HOLD",
            date_iso="2026-06-30",
        )
        k_y = make_cache_key(
            provider="claude", schema_name="X", symbol="A", score=10.0, action="HOLD",
            date_iso="2026-06-29",
        )
        assert k_t != k_y


# ---------------------------------------------------------------------------
# TestCacheStore
# ---------------------------------------------------------------------------


class TestCacheStore:
    def test_put_then_get_roundtrip(self):
        cache_put("k1", {"headline": "hi"})
        assert cache_get("k1") == {"headline": "hi"}

    def test_missing_key_returns_none(self):
        assert cache_get("does-not-exist") is None

    def test_clear_empties_cache(self):
        cache_put("k1", {"headline": "hi"})
        cache_clear()
        assert cache_get("k1") is None

    def test_corrupt_json_treated_as_empty(self, tmp_path):
        # Write garbage to the cache path; reads must degrade silently.
        p = Path(cache_mod.settings.LLM_COMMENTARY_CACHE_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("this is not json", encoding="utf-8")
        assert cache_get("k1") is None
        # And we must still be able to put + get without crashing.
        cache_put("k2", {"headline": "ok"})
        assert cache_get("k2") == {"headline": "ok"}

    def test_non_dict_root_treated_as_empty(self):
        p = Path(cache_mod.settings.LLM_COMMENTARY_CACHE_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert cache_get("anything") is None


# ---------------------------------------------------------------------------
# Helpers — fake providers used across commentary tests.
# ---------------------------------------------------------------------------


def _good_rationale() -> AnalystRationale:
    return AnalystRationale(
        headline="Healthy uptrend with measured pullback.",
        why_now="Aroon and Coppock both positive; rsi(2) flagged oversold; "
                "macro regime supportive of risk-on names.",
        key_risks=["VIX spike could void the trend filter."],
        invalidation="A close below the 200-day SMA invalidates the setup.",
    )


def _good_alert() -> AlertCommentary:
    return AlertCommentary(body="AAPL conviction crossed 0.85.", urgency_hint="high")


class _FakeProvider:
    """Pretend LLMProvider that always returns the same model object."""

    name = "fake"

    def __init__(self, value):
        self._value = value
        self.call_count = 0

    def call_structured(self, system, user, schema_model):
        self.call_count += 1
        return self._value


# ---------------------------------------------------------------------------
# TestGenerateRationale
# ---------------------------------------------------------------------------


class TestGenerateRationaleEnabled:
    def test_returns_validated_model_on_success(self, monkeypatch):
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        prov = _FakeProvider(_good_rationale())
        monkeypatch.setattr(commentary_mod, "get_rationale_provider", lambda: prov)
        out = generate_analyst_rationale(
            rec_skeleton={"symbol": "AAPL", "action": "BUY", "key_indicators": {"score": 75.0}}
        )
        assert isinstance(out, AnalystRationale)
        assert out.headline == "Healthy uptrend with measured pullback."

    def test_cache_hit_skips_provider(self, monkeypatch):
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        prov = _FakeProvider(_good_rationale())
        monkeypatch.setattr(commentary_mod, "get_rationale_provider", lambda: prov)
        skeleton = {"symbol": "AAPL", "action": "BUY", "key_indicators": {"score": 75.0}}
        # First call — provider is hit, cache is populated.
        out1 = generate_analyst_rationale(skeleton)
        assert prov.call_count == 1
        # Second call — same skeleton → same key → cache hit, provider NOT hit again.
        out2 = generate_analyst_rationale(skeleton)
        assert prov.call_count == 1
        assert out1.headline == out2.headline


class TestGenerateRationaleDisabled:
    def test_master_switch_off_returns_none(self, monkeypatch):
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", False, raising=False)
        # Provider must NEVER be constructed when master switch is off.
        called = {"n": 0}

        def _boom():
            called["n"] += 1
            return _FakeProvider(_good_rationale())

        monkeypatch.setattr(commentary_mod, "get_rationale_provider", _boom)
        out = generate_analyst_rationale(
            rec_skeleton={"symbol": "AAPL", "action": "BUY", "key_indicators": {"score": 75.0}}
        )
        assert out is None
        assert called["n"] == 0


class TestGenerateRationaleSoftFail:
    def test_provider_returns_none_propagates_none(self, monkeypatch):
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        prov = _FakeProvider(None)
        monkeypatch.setattr(commentary_mod, "get_rationale_provider", lambda: prov)
        out = generate_analyst_rationale(
            rec_skeleton={"symbol": "AAPL", "action": "BUY", "key_indicators": {"score": 75.0}}
        )
        assert out is None
        assert prov.call_count == 1

    def test_no_provider_configured_returns_none(self, monkeypatch):
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod, "get_rationale_provider", lambda: None)
        out = generate_analyst_rationale(
            rec_skeleton={"symbol": "AAPL", "action": "BUY", "key_indicators": {"score": 75.0}}
        )
        assert out is None

    def test_provider_raises_returns_none(self, monkeypatch):
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)

        class _Raises:
            name = "boom"

            def call_structured(self, *a, **kw):
                raise RuntimeError("oops")

        monkeypatch.setattr(commentary_mod, "get_rationale_provider", lambda: _Raises())
        # commentary.generate_analyst_rationale must catch — never raise.
        out = generate_analyst_rationale(
            rec_skeleton={"symbol": "AAPL", "action": "BUY", "key_indicators": {"score": 75.0}}
        )
        assert out is None


class TestCacheCorruptEntry:
    def test_invalid_cached_payload_is_refetched(self, monkeypatch):
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        # Pre-populate the cache with a payload that will fail schema validation.
        skeleton = {"symbol": "AAPL", "action": "BUY", "key_indicators": {"score": 75.0}}
        key = make_cache_key(
            provider="claude",
            schema_name=AnalystRationale.__name__,
            symbol="AAPL",
            score=75.0,
            action="BUY",
        )
        cache_put(key, {"wrong_field": True})  # bad shape

        prov = _FakeProvider(_good_rationale())
        monkeypatch.setattr(commentary_mod, "get_rationale_provider", lambda: prov)
        out = generate_analyst_rationale(skeleton)
        assert isinstance(out, AnalystRationale)
        assert prov.call_count == 1  # provider WAS hit despite the cached entry


# ---------------------------------------------------------------------------
# TestGenerateAlert
# ---------------------------------------------------------------------------


class TestGenerateAlertEnabled:
    def test_returns_validated_model_on_success(self, monkeypatch):
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        prov = _FakeProvider(_good_alert())
        monkeypatch.setattr(commentary_mod, "get_alert_provider", lambda: prov)
        out = generate_alert_commentary(
            alert_skeleton={"symbol": "AAPL", "kind": "momentum_building",
                            "trigger_detail": "p=0.86"}
        )
        assert isinstance(out, AlertCommentary)
        assert "0.85" in out.body

    def test_priority_field_never_overridden(self, monkeypatch):
        # The schema's urgency_hint must NEVER feed back into the deterministic
        # WatchAlert.priority / TradeAlert.priority.  Verified at the call site;
        # here we just confirm the field is exposed for logging.
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        prov = _FakeProvider(_good_alert())
        monkeypatch.setattr(commentary_mod, "get_alert_provider", lambda: prov)
        out = generate_alert_commentary({"symbol": "AAPL", "kind": "X"})
        assert out.urgency_hint in ("low", "normal", "high")


class TestGenerateAlertDisabled:
    def test_master_switch_off_returns_none(self, monkeypatch):
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", False, raising=False)
        called = {"n": 0}

        def _boom():
            called["n"] += 1
            return _FakeProvider(_good_alert())

        monkeypatch.setattr(commentary_mod, "get_alert_provider", _boom)
        out = generate_alert_commentary({"symbol": "AAPL", "kind": "x"})
        assert out is None
        assert called["n"] == 0
