"""
tests/test_opal_pipeline_integration.py
=========================================
Integration tests for Opal's threading into the rest of Tier 9 (Tier 9
Scope 4). Verifies:

* ``engine.advisory.enrich_with_llm_rationale`` calls
  ``llm.research.generate_research_brief`` when ``OPAL_RESEARCH_ENABLED`` is
  True, threads the resulting brief into ``context["research_brief"]``, AND
  populates ``Recommendation.research_brief``.
* ``llm.commentary._format_rationale_user_prompt`` cites the research brief
  in the user prompt sent to Claude/Gemini when present.
* Numeric ``Recommendation`` fields are byte-identical after enrichment
  (CONSTRAINT #4 — Opal cannot fabricate/alter a pipeline scalar).
* Opal disabled (default) → ``llm.research`` never calls its provider, and
  ``Recommendation.research_brief`` stays ``None``.
* Both Opal AND Claude/Gemini enabled together → both fields populate and
  the research brief is visible to the rationale call.

All provider calls are injected fakes — no network, no real SDKs.
"""

from __future__ import annotations

import dataclasses

import pytest

from engine.advisory import Recommendation, enrich_with_llm_rationale
from llm import cache as cache_mod
from llm.schemas import AnalystRationale, ResearchBrief


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    p = tmp_path / "llm_commentary_cache.json"
    monkeypatch.setattr(cache_mod.settings, "LLM_COMMENTARY_CACHE_PATH", str(p), raising=False)
    yield


def _rec(**overrides):
    defaults = dict(
        symbol="AAPL",
        action="BUY",
        strategy="multi-signal composite",
        conviction=0.72,
        rationale="Template paragraph naming top 2-3 drivers.",
        suggested_position_pct=0.04,
        forecast=205.50,
        key_indicators={"score": 75.0, "atr": 4.2},
        data_quality="OK",
    )
    defaults.update(overrides)
    return Recommendation(**defaults)


def _good_brief(**overrides) -> ResearchBrief:
    payload = dict(
        thesis_context="Momentum is building into the print.",
        catalysts=["Q3 earnings call scheduled Nov 4"],
        risk_factors=["Guidance miss risk"],
        recent_developments=["Announced buyback"],
        data_confidence="high",
        sources_note="Based on 3 Finnhub headlines from the past 7 days.",
    )
    payload.update(overrides)
    return ResearchBrief(**payload)


class _FakeResearchProvider:
    def __init__(self, *, value=None):
        self._value = value
        self.call_count = 0

    def call_structured(self, *, system, user, schema_model):
        self.call_count += 1
        return self._value


class _FakeRationaleProvider:
    name = "claude"

    def __init__(self, *, value=None):
        self._value = value
        self.captured_user_prompts = []

    def call_structured(self, *a, **kw):
        self.captured_user_prompts.append(kw.get("user"))
        return self._value


# ---------------------------------------------------------------------------
# TestRecommendationField
# ---------------------------------------------------------------------------


class TestRecommendationField:
    def test_default_research_brief_is_none(self):
        r = _rec()
        assert r.research_brief is None

    def test_research_brief_accepts_dict(self):
        r = _rec()
        r2 = dataclasses.replace(r, research_brief={"thesis_context": "x"})
        assert r2.research_brief == {"thesis_context": "x"}


# ---------------------------------------------------------------------------
# TestOpalDisabled
# ---------------------------------------------------------------------------


class TestOpalDisabled:
    def test_opal_disabled_by_default_no_openai_call(self, monkeypatch):
        from engine import advisory as advisory_mod

        monkeypatch.setattr(advisory_mod.settings, "OPAL_RESEARCH_ENABLED", False, raising=False)
        monkeypatch.setattr(advisory_mod.settings, "LLM_COMMENTARY_ENABLED", False, raising=False)

        r = _rec()
        out = enrich_with_llm_rationale(r)
        assert out is r
        assert out.research_brief is None

    def test_opal_disabled_but_commentary_enabled_still_works_without_research(self, monkeypatch):
        from engine import advisory as advisory_mod
        from llm import commentary as commentary_mod

        monkeypatch.setattr(advisory_mod.settings, "OPAL_RESEARCH_ENABLED", False, raising=False)
        monkeypatch.setattr(advisory_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)

        payload = AnalystRationale(
            headline="h", why_now="w", key_risks=["r"], invalidation="i"
        )
        fake_rationale_prov = _FakeRationaleProvider(value=payload)
        monkeypatch.setattr(commentary_mod, "get_rationale_provider", lambda: fake_rationale_prov)

        r = _rec()
        out = enrich_with_llm_rationale(r)
        assert out.research_brief is None
        assert out.llm_rationale is not None
        # No research-context block since no brief was generated.
        assert "Research context" not in fake_rationale_prov.captured_user_prompts[0]


# ---------------------------------------------------------------------------
# TestOpalThreadsIntoContext
# ---------------------------------------------------------------------------


class TestOpalThreadsIntoContext:
    def test_brief_populates_recommendation_research_brief_field(self, monkeypatch):
        from engine import advisory as advisory_mod
        import llm.research as research_mod

        monkeypatch.setattr(advisory_mod.settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        monkeypatch.setattr(advisory_mod.settings, "LLM_COMMENTARY_ENABLED", False, raising=False)

        brief = _good_brief()
        fake_research_prov = _FakeResearchProvider(value=brief)
        monkeypatch.setattr(research_mod, "_get_default_provider", lambda: fake_research_prov)
        monkeypatch.setattr(research_mod, "_gather_grounding", lambda sym, ctx=None: {
            "headlines": [], "next_earnings": None, "macro_snippet": None,
        })

        r = _rec()
        out = enrich_with_llm_rationale(r)

        assert out.research_brief is not None
        assert out.research_brief["thesis_context"] == brief.thesis_context
        assert fake_research_prov.call_count == 1

    def test_brief_threads_into_rationale_user_prompt(self, monkeypatch):
        from engine import advisory as advisory_mod
        from llm import commentary as commentary_mod
        import llm.research as research_mod

        monkeypatch.setattr(advisory_mod.settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        monkeypatch.setattr(advisory_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)

        brief = _good_brief(thesis_context="Unique thesis marker ABC123.")
        fake_research_prov = _FakeResearchProvider(value=brief)
        monkeypatch.setattr(research_mod, "_get_default_provider", lambda: fake_research_prov)
        monkeypatch.setattr(research_mod, "_gather_grounding", lambda sym, ctx=None: {
            "headlines": [], "next_earnings": None, "macro_snippet": None,
        })

        rationale_payload = AnalystRationale(
            headline="h", why_now="w", key_risks=["r"], invalidation="i"
        )
        fake_rationale_prov = _FakeRationaleProvider(value=rationale_payload)
        monkeypatch.setattr(commentary_mod, "get_rationale_provider", lambda: fake_rationale_prov)

        r = _rec()
        out = enrich_with_llm_rationale(r)

        assert out.research_brief is not None
        assert out.llm_rationale is not None
        assert len(fake_rationale_prov.captured_user_prompts) == 1
        prompt = fake_rationale_prov.captured_user_prompts[0]
        assert "Research context" in prompt
        assert "Unique thesis marker ABC123." in prompt

    def test_research_provider_raises_does_not_block_rationale(self, monkeypatch):
        from engine import advisory as advisory_mod
        from llm import commentary as commentary_mod
        import llm.research as research_mod

        monkeypatch.setattr(advisory_mod.settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        monkeypatch.setattr(advisory_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)

        def _raise(*a, **kw):
            raise RuntimeError("opal down")

        monkeypatch.setattr(research_mod, "generate_research_brief", _raise)

        rationale_payload = AnalystRationale(
            headline="h", why_now="w", key_risks=["r"], invalidation="i"
        )
        fake_rationale_prov = _FakeRationaleProvider(value=rationale_payload)
        monkeypatch.setattr(commentary_mod, "get_rationale_provider", lambda: fake_rationale_prov)

        r = _rec()
        out = enrich_with_llm_rationale(r)

        assert out.research_brief is None
        assert out.llm_rationale is not None


# ---------------------------------------------------------------------------
# TestNoFabricatedMetrics — CONSTRAINT #4
# ---------------------------------------------------------------------------


class TestNoFabricatedMetrics:
    def test_numeric_fields_byte_identical_after_opal_enrichment(self, monkeypatch):
        from engine import advisory as advisory_mod
        import llm.research as research_mod

        monkeypatch.setattr(advisory_mod.settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        monkeypatch.setattr(advisory_mod.settings, "LLM_COMMENTARY_ENABLED", False, raising=False)

        fake_research_prov = _FakeResearchProvider(value=_good_brief())
        monkeypatch.setattr(research_mod, "_get_default_provider", lambda: fake_research_prov)
        monkeypatch.setattr(research_mod, "_gather_grounding", lambda sym, ctx=None: {
            "headlines": [], "next_earnings": None, "macro_snippet": None,
        })

        r = _rec()
        out = enrich_with_llm_rationale(r)

        assert out.symbol == r.symbol
        assert out.action == r.action
        assert out.conviction == r.conviction
        assert out.suggested_position_pct == r.suggested_position_pct
        assert out.forecast == r.forecast
        assert out.key_indicators == r.key_indicators
        assert out.data_quality == r.data_quality
        assert out.rationale == r.rationale
        # Only the new field changed.
        assert out.research_brief is not None
