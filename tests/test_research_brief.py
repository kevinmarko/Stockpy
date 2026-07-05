"""
tests/test_research_brief.py
==============================
Unit tests for ``llm.schemas.ResearchBrief`` and ``llm.research`` (Tier 9
Scope 4 — Opal grounded research brief).

All network/SDK access is avoided via the ``provider`` and ``grounding_fn``
test seams that ``generate_research_brief`` exposes — no real Finnhub or
OpenAI calls are ever made.

Coverage
--------
TestResearchBriefSchema     — bounds on catalysts/risk_factors/recent_developments,
                              data_confidence literal, frozen + extra=forbid,
                              no numeric field (CONSTRAINT #4).
TestGenerateResearchBrief   — happy path via injected provider + grounding_fn;
                              cache hit skips provider; opt-in default-off;
                              empty symbol → None; provider None → None;
                              provider raises → None; corrupt cache entry
                              falls through to re-fetch.
TestGrounding               — _gather_grounding degrades to empty packet on
                              Finnhub failure; _format_grounding_user_prompt
                              renders headlines/earnings/macro.
"""

from __future__ import annotations

from unittest import mock

import pytest
from pydantic import ValidationError

from llm import cache as cache_mod
from llm.schemas import ResearchBrief


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    p = tmp_path / "llm_commentary_cache.json"
    monkeypatch.setattr(cache_mod.settings, "LLM_COMMENTARY_CACHE_PATH", str(p), raising=False)
    yield


def _good_brief(**overrides) -> ResearchBrief:
    payload = dict(
        thesis_context="Momentum is building into the print.",
        catalysts=["Q3 earnings call scheduled Nov 4"],
        risk_factors=["Guidance miss risk"],
        recent_developments=["Announced buyback"],
        data_confidence="medium",
        sources_note="Based on 3 Finnhub headlines from the past 7 days.",
    )
    payload.update(overrides)
    return ResearchBrief(**payload)


# ---------------------------------------------------------------------------
# TestResearchBriefSchema
# ---------------------------------------------------------------------------


class TestResearchBriefSchema:
    def test_canonical_payload_accepted(self):
        r = _good_brief()
        assert r.data_confidence == "medium"

    def test_frozen(self):
        r = _good_brief()
        with pytest.raises(Exception):
            r.thesis_context = "changed"  # type: ignore[misc]

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            ResearchBrief(
                thesis_context="x",
                catalysts=["a"],
                risk_factors=["r"],
                sources_note="s",
                bogus_field=1,  # type: ignore[call-arg]
            )

    def test_too_many_catalysts_rejected(self):
        with pytest.raises(ValidationError):
            ResearchBrief(
                thesis_context="x",
                catalysts=["a", "b", "c", "d", "e"],
                risk_factors=["r"],
                sources_note="s",
            )

    def test_zero_catalysts_allowed(self):
        # Fix 6 — empty catalysts/risk_factors are now allowed so the model can
        # honestly return nothing when the grounding packet is sparse (matches
        # recent_developments; CONSTRAINT #4 — never forced to fabricate).
        r = ResearchBrief(
            thesis_context="x", catalysts=[], risk_factors=[], sources_note="s"
        )
        assert r.catalysts == []
        assert r.risk_factors == []

    def test_catalyst_item_too_long_rejected(self):
        # Fix 4 — per-item string length is now enforced (≤160), not just list count.
        with pytest.raises(ValidationError):
            ResearchBrief(
                thesis_context="x",
                catalysts=["y" * 161],
                risk_factors=["r"],
                sources_note="s",
            )

    def test_recent_development_item_too_long_rejected(self):
        with pytest.raises(ValidationError):
            ResearchBrief(
                thesis_context="x",
                catalysts=["a"],
                risk_factors=["r"],
                recent_developments=["z" * 201],
                sources_note="s",
            )

    def test_risk_factor_item_too_long_rejected(self):
        # Fix 4 parallel — per-item risk_factor length is enforced (≤160).
        with pytest.raises(ValidationError):
            ResearchBrief(
                thesis_context="x",
                catalysts=["a"],
                risk_factors=["r" * 161],
                sources_note="s",
            )

    def test_catalyst_item_exactly_160_accepted(self):
        # Boundary — exactly at the ≤160 cap is valid.
        r = ResearchBrief(
            thesis_context="x",
            catalysts=["c" * 160],
            risk_factors=["r"],
            sources_note="s",
        )
        assert len(r.catalysts[0]) == 160

    def test_recent_development_item_exactly_200_accepted(self):
        # Boundary — exactly at the ≤200 cap is valid.
        r = ResearchBrief(
            thesis_context="x",
            catalysts=["a"],
            risk_factors=["r"],
            recent_developments=["d" * 200],
            sources_note="s",
        )
        assert len(r.recent_developments[0]) == 200

    def test_four_catalysts_accepted(self):
        # Boundary — the list-count cap (max_length=4) survives Fix 4/6;
        # exactly 4 items is still valid (5 is rejected elsewhere).
        r = ResearchBrief(
            thesis_context="x",
            catalysts=["a", "b", "c", "d"],
            risk_factors=["r"],
            sources_note="s",
        )
        assert len(r.catalysts) == 4

    def test_all_empty_lists_brief_validates_and_roundtrips(self):
        # Fix 6 — a fully sparse brief (no catalysts, risks, or developments)
        # is honest and valid, and round-trips through model_dump/re-validate.
        r = ResearchBrief(
            thesis_context="Sparse grounding.",
            catalysts=[],
            risk_factors=[],
            recent_developments=[],
            data_confidence="low",
            sources_note="No news or earnings retrieved.",
        )
        assert r.catalysts == []
        assert r.risk_factors == []
        assert r.recent_developments == []
        r2 = ResearchBrief(**r.model_dump())
        assert r2 == r

    def test_too_many_risk_factors_rejected(self):
        with pytest.raises(ValidationError):
            ResearchBrief(
                thesis_context="x",
                catalysts=["a"],
                risk_factors=["r1", "r2", "r3", "r4", "r5"],
                sources_note="s",
            )

    def test_recent_developments_defaults_to_empty_list(self):
        r = ResearchBrief(
            thesis_context="x", catalysts=["a"], risk_factors=["r"], sources_note="s"
        )
        assert r.recent_developments == []

    def test_recent_developments_capped_at_4(self):
        with pytest.raises(ValidationError):
            ResearchBrief(
                thesis_context="x",
                catalysts=["a"],
                risk_factors=["r"],
                recent_developments=["1", "2", "3", "4", "5"],
                sources_note="s",
            )

    def test_bad_data_confidence_rejected(self):
        with pytest.raises(ValidationError):
            ResearchBrief(
                thesis_context="x",
                catalysts=["a"],
                risk_factors=["r"],
                data_confidence="extreme",  # type: ignore[arg-type]
                sources_note="s",
            )

    def test_data_confidence_default_is_medium(self):
        r = ResearchBrief(
            thesis_context="x", catalysts=["a"], risk_factors=["r"], sources_note="s"
        )
        assert r.data_confidence == "medium"

    def test_thesis_context_length_capped(self):
        with pytest.raises(ValidationError):
            ResearchBrief(
                thesis_context="x" * 700,
                catalysts=["a"],
                risk_factors=["r"],
                sources_note="s",
            )

    def test_sources_note_length_capped(self):
        with pytest.raises(ValidationError):
            ResearchBrief(
                thesis_context="x",
                catalysts=["a"],
                risk_factors=["r"],
                sources_note="x" * 300,
            )

    def test_no_numeric_field_present(self):
        # CONSTRAINT #4 — every field must be qualitative (str / list[str] /
        # Literal).  Fix 4 wraps the list item types in
        # ``Annotated[str, StringConstraints(...)]`` for per-item length caps,
        # so the check must unwrap Annotated to reach the base ``str``.
        import typing

        def _base(ann):
            # Unwrap Annotated[X, ...] → X.
            if hasattr(ann, "__metadata__"):
                return typing.get_args(ann)[0]
            return ann

        for name, field in ResearchBrief.model_fields.items():
            ann = _base(field.annotation)
            origin = typing.get_origin(ann)
            if origin is list:
                item = _base(typing.get_args(ann)[0]) if typing.get_args(ann) else None
                is_qualitative = item is str
            else:
                is_qualitative = ann is str or origin is typing.Literal
            assert is_qualitative, f"field {name!r} is not qualitative: {field.annotation!r}"


# ---------------------------------------------------------------------------
# Fake seams
# ---------------------------------------------------------------------------


class _FakeProvider:
    name = "fake-openai"

    def __init__(self, *, value=None, raises=None):
        self._value = value
        self._raises = raises
        self.call_count = 0

    def call_structured(self, *, system, user, schema_model):
        self.call_count += 1
        if self._raises is not None:
            raise self._raises
        return self._value


def _fake_grounding(symbol, context=None):
    return {
        "headlines": ["Company announces new product line"],
        "next_earnings": "2026-08-15",
        "macro_snippet": (context or {}).get("macro_snippet"),
    }


# ---------------------------------------------------------------------------
# TestGenerateResearchBrief
# ---------------------------------------------------------------------------


class TestGenerateResearchBrief:
    def test_default_disabled_returns_none(self, monkeypatch):
        from settings import settings as _settings
        import llm.research as research_mod

        monkeypatch.setattr(_settings, "OPAL_RESEARCH_ENABLED", False, raising=False)
        prov = _FakeProvider(value=_good_brief())
        out = research_mod.generate_research_brief(
            "AAPL", provider=prov, grounding_fn=_fake_grounding
        )
        assert out is None
        assert prov.call_count == 0

    def test_happy_path_via_injected_provider(self, monkeypatch):
        from settings import settings as _settings
        import llm.research as research_mod

        monkeypatch.setattr(_settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        prov = _FakeProvider(value=_good_brief())
        out = research_mod.generate_research_brief(
            "AAPL", provider=prov, grounding_fn=_fake_grounding
        )
        assert isinstance(out, ResearchBrief)
        assert prov.call_count == 1

    def test_cache_hit_skips_provider(self, monkeypatch):
        from settings import settings as _settings
        import llm.research as research_mod

        monkeypatch.setattr(_settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        prov = _FakeProvider(value=_good_brief())
        out1 = research_mod.generate_research_brief(
            "AAPL", provider=prov, grounding_fn=_fake_grounding
        )
        out2 = research_mod.generate_research_brief(
            "AAPL", provider=prov, grounding_fn=_fake_grounding
        )
        assert prov.call_count == 1
        assert out1.thesis_context == out2.thesis_context

    def test_empty_symbol_returns_none(self, monkeypatch):
        from settings import settings as _settings
        import llm.research as research_mod

        monkeypatch.setattr(_settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        prov = _FakeProvider(value=_good_brief())
        out = research_mod.generate_research_brief(
            "", provider=prov, grounding_fn=_fake_grounding
        )
        assert out is None
        assert prov.call_count == 0

    def test_provider_none_returns_none(self, monkeypatch):
        from settings import settings as _settings
        import llm.research as research_mod

        monkeypatch.setattr(_settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        out = research_mod.generate_research_brief(
            "MSFT", provider=None, grounding_fn=_fake_grounding
        )
        assert out is None

    def test_provider_raises_returns_none(self, monkeypatch):
        from settings import settings as _settings
        import llm.research as research_mod

        monkeypatch.setattr(_settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        prov = _FakeProvider(raises=RuntimeError("synthetic failure"))
        out = research_mod.generate_research_brief(
            "GOOG", provider=prov, grounding_fn=_fake_grounding
        )
        assert out is None

    def test_provider_returns_none_propagates_none(self, monkeypatch):
        from settings import settings as _settings
        import llm.research as research_mod

        monkeypatch.setattr(_settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        prov = _FakeProvider(value=None)
        out = research_mod.generate_research_brief(
            "TSLA", provider=prov, grounding_fn=_fake_grounding
        )
        assert out is None

    def test_corrupt_cache_entry_falls_through_to_refetch(self, monkeypatch):
        from settings import settings as _settings
        import llm.research as research_mod
        from llm.cache import cache_put, make_cache_key

        monkeypatch.setattr(_settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        key = make_cache_key(
            provider="openai", schema_name="ResearchBrief", symbol="NFLX",
            score=0.0, action="RESEARCH",
        )
        # Corrupt payload — missing required fields.
        cache_put(key, {"bogus": True})
        prov = _FakeProvider(value=_good_brief())
        out = research_mod.generate_research_brief(
            "NFLX", provider=prov, grounding_fn=_fake_grounding
        )
        assert isinstance(out, ResearchBrief)
        assert prov.call_count == 1

    def test_cache_key_reflects_live_opal_research_provider_setting(self, monkeypatch):
        # Regression guard: switching OPAL_RESEARCH_PROVIDER (e.g. openai ->
        # gemini) must NOT silently serve a cached brief generated by the
        # other provider — the cache key must be derived from the LIVE
        # setting, not hardcoded to "openai".
        from settings import settings as _settings
        import llm.research as research_mod
        from llm.cache import cache_get, make_cache_key

        monkeypatch.setattr(_settings, "OPAL_RESEARCH_ENABLED", True, raising=False)

        # Seed a cache entry under the "openai" provider slot only.
        monkeypatch.setattr(_settings, "OPAL_RESEARCH_PROVIDER", "openai", raising=False)
        prov_openai = _FakeProvider(value=_good_brief())
        out_openai = research_mod.generate_research_brief(
            "IBM", provider=prov_openai, grounding_fn=_fake_grounding
        )
        assert isinstance(out_openai, ResearchBrief)
        assert prov_openai.call_count == 1

        openai_key = make_cache_key(
            provider="openai", schema_name="ResearchBrief", symbol="IBM",
            score=0.0, action="RESEARCH",
        )
        assert cache_get(openai_key) is not None

        # Switching to "gemini" must miss the openai-seeded cache entry and
        # call the (new) provider again — never reuse the other provider's
        # cached payload.
        monkeypatch.setattr(_settings, "OPAL_RESEARCH_PROVIDER", "gemini", raising=False)
        gemini_key = make_cache_key(
            provider="gemini", schema_name="ResearchBrief", symbol="IBM",
            score=0.0, action="RESEARCH",
        )
        assert gemini_key != openai_key
        assert cache_get(gemini_key) is None

        prov_gemini = _FakeProvider(value=_good_brief())
        out_gemini = research_mod.generate_research_brief(
            "IBM", provider=prov_gemini, grounding_fn=_fake_grounding
        )
        assert isinstance(out_gemini, ResearchBrief)
        assert prov_gemini.call_count == 1  # not served from the openai cache entry

    def test_grounding_fn_never_reaches_real_finnhub(self, monkeypatch):
        # If grounding_fn were bypassed, _gather_grounding would try to
        # import signals.news_catalyst and hit the network — this proves
        # the injected seam is what's actually used.
        from settings import settings as _settings
        import llm.research as research_mod

        monkeypatch.setattr(_settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        calls = []

        def _tracking_grounding(symbol, context=None):
            calls.append(symbol)
            return _fake_grounding(symbol, context)

        prov = _FakeProvider(value=_good_brief())
        research_mod.generate_research_brief(
            "IBM", provider=prov, grounding_fn=_tracking_grounding
        )
        assert calls == ["IBM"]


# ---------------------------------------------------------------------------
# TestGrounding
# ---------------------------------------------------------------------------


class TestGrounding:
    def test_gather_grounding_degrades_on_finnhub_failure(self, monkeypatch):
        from settings import settings as _settings
        import llm.research as research_mod

        monkeypatch.setattr(_settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        # No FINNHUB_API_KEY configured by default in test env — build_finnhub_client
        # degrades to None, so the packet should be the empty shape.
        packet = research_mod._gather_grounding("AAPL", context=None)
        assert packet["headlines"] == []
        assert packet["next_earnings"] is None

    def test_gather_grounding_folds_in_macro_snippet_from_context(self):
        import llm.research as research_mod

        packet = research_mod._gather_grounding(
            "AAPL", context={"macro_snippet": "RISK ON"}
        )
        assert packet["macro_snippet"] == "RISK ON"

    def test_format_grounding_user_prompt_renders_headlines_and_earnings(self):
        import llm.research as research_mod

        packet = {
            "headlines": ["Headline one", "Headline two"],
            "next_earnings": "2026-09-01",
            "macro_snippet": "NEUTRAL",
        }
        prompt = research_mod._format_grounding_user_prompt("AAPL", packet)
        assert "Headline one" in prompt
        assert "2026-09-01" in prompt
        assert "NEUTRAL" in prompt
        assert "AAPL" in prompt

    def test_format_grounding_user_prompt_handles_empty_packet(self):
        import llm.research as research_mod

        packet = {"headlines": [], "next_earnings": None, "macro_snippet": None}
        prompt = research_mod._format_grounding_user_prompt("XYZ", packet)
        assert "none retrieved" in prompt.lower()
        assert "unknown" in prompt.lower()
