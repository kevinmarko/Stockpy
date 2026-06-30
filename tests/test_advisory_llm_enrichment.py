"""
tests/test_advisory_llm_enrichment.py
======================================
Unit tests for ``engine.advisory.enrich_with_llm_rationale`` and the
``Recommendation.llm_rationale`` field.

Coverage
--------
TestRecommendationField   — llm_rationale default is None; field accepts dict.
TestEnrichDisabled        — master switch off → rec returned unchanged.
TestEnrichSuccess         — provider returns valid model → llm_rationale dict populated;
                            deterministic rationale is PRESERVED.
TestEnrichSoftFail        — provider returns None → rec returned unchanged;
                            provider raises → rec returned unchanged (CONSTRAINT #6).
TestNoTopLevelLLMImport   — engine/advisory.py source has no top-level
                            ``import anthropic`` / ``import google`` (lazy only).
TestNoFabricatedMetrics   — verify the enricher cannot change numeric fields
                            (score, conviction, suggested_position_pct, forecast,
                            key_indicators).
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from unittest import mock

import pytest

# Heavy engines aren't needed here — the dataclass + enricher are the surface
# we exercise.  Import only the lightweight things.
from engine.advisory import Recommendation, enrich_with_llm_rationale
from llm import cache as cache_mod


# Isolate the JSON cache per-test so a populated entry from one case never
# leaks into the next.  Mirrors the fixture in tests/test_llm_commentary.py.
@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    p = tmp_path / "llm_commentary_cache.json"
    monkeypatch.setattr(
        cache_mod.settings, "LLM_COMMENTARY_CACHE_PATH", str(p), raising=False
    )
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


# ---------------------------------------------------------------------------
# TestRecommendationField
# ---------------------------------------------------------------------------


class TestRecommendationField:
    def test_default_llm_rationale_is_none(self):
        r = _rec()
        assert r.llm_rationale is None

    def test_llm_rationale_accepts_dict(self):
        payload = {
            "headline": "x",
            "why_now": "y",
            "key_risks": ["a"],
            "invalidation": "z",
        }
        r = _rec()
        r2 = dataclasses.replace(r, llm_rationale=payload)
        assert r2.llm_rationale == payload

    def test_recommendation_remains_frozen(self):
        r = _rec()
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.llm_rationale = {"x": 1}  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestEnrichDisabled
# ---------------------------------------------------------------------------


class TestEnrichDisabled:
    def test_master_switch_off_returns_rec_unchanged(self, monkeypatch):
        from engine import advisory as advisory_mod

        monkeypatch.setattr(advisory_mod.settings, "LLM_COMMENTARY_ENABLED", False, raising=False)
        r = _rec()
        out = enrich_with_llm_rationale(r)
        # Identity is fine — soft-fail should NOT allocate a new instance.
        assert out is r
        assert out.llm_rationale is None


# ---------------------------------------------------------------------------
# TestEnrichSuccess
# ---------------------------------------------------------------------------


class TestEnrichSuccess:
    def test_success_populates_llm_rationale_dict_and_preserves_template(self, monkeypatch):
        from engine import advisory as advisory_mod
        from llm import commentary as commentary_mod
        from llm.schemas import AnalystRationale

        monkeypatch.setattr(advisory_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)

        payload = AnalystRationale(
            headline="Strong setup",
            why_now="Trend confirmed by Aroon; mean-reversion entry on RSI(2).",
            key_risks=["Macro tail risk if VIX gaps."],
            invalidation="Close below 200-day SMA voids the trend filter.",
        )

        class _Prov:
            name = "claude"

            def call_structured(self, *a, **kw):
                return payload

        monkeypatch.setattr(commentary_mod, "get_rationale_provider", lambda: _Prov())

        r = _rec()
        out = enrich_with_llm_rationale(r)

        # Deterministic rationale string is PRESERVED.
        assert out.rationale == r.rationale
        # New field is populated with the schema dump.
        assert isinstance(out.llm_rationale, dict)
        assert out.llm_rationale["headline"] == "Strong setup"
        assert "Aroon" in out.llm_rationale["why_now"]
        assert out.llm_rationale["key_risks"] == ["Macro tail risk if VIX gaps."]
        assert "200-day" in out.llm_rationale["invalidation"]
        # The returned rec is a new immutable instance (dataclasses.replace).
        assert out is not r


# ---------------------------------------------------------------------------
# TestEnrichSoftFail
# ---------------------------------------------------------------------------


class TestEnrichSoftFail:
    def test_provider_returns_none_returns_rec_unchanged(self, monkeypatch):
        from engine import advisory as advisory_mod
        from llm import commentary as commentary_mod

        monkeypatch.setattr(advisory_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod, "get_rationale_provider", lambda: None)

        r = _rec()
        out = enrich_with_llm_rationale(r)
        assert out is r
        assert out.llm_rationale is None

    def test_commentary_raises_returns_rec_unchanged(self, monkeypatch):
        from engine import advisory as advisory_mod

        monkeypatch.setattr(advisory_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        # Monkeypatch the LAZY import inside enrich_with_llm_rationale so that
        # generate_analyst_rationale raises — the wrapper must catch.
        from llm import commentary as commentary_mod

        def _raise(*a, **kw):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(commentary_mod, "generate_analyst_rationale", _raise)

        r = _rec()
        out = enrich_with_llm_rationale(r)
        # The function must NOT propagate the exception; it returns rec.
        assert out is r
        assert out.llm_rationale is None


# ---------------------------------------------------------------------------
# TestNoTopLevelLLMImport
# ---------------------------------------------------------------------------


class TestNoTopLevelLLMImport:
    """Gravity step_74 enforces this too — keep a quick local guard."""

    def test_advisory_has_no_top_level_anthropic_or_google_import(self):
        path = Path(__file__).resolve().parents[1] / "engine" / "advisory.py"
        src = path.read_text(encoding="utf-8")
        # Walk only the top of the file (module-level statements) — strip out
        # function bodies' indented imports.
        top_level_lines = [ln for ln in src.splitlines() if not ln.startswith(" ") and not ln.startswith("\t")]
        joined = "\n".join(top_level_lines)
        assert "import anthropic" not in joined
        assert "from anthropic" not in joined
        assert "import google" not in joined
        assert "from google" not in joined


# ---------------------------------------------------------------------------
# TestNoFabricatedMetrics — invariant that LLM cannot change numeric fields.
# ---------------------------------------------------------------------------


class TestNoFabricatedMetrics:
    def test_numeric_fields_invariant_across_enrichment(self, monkeypatch):
        from engine import advisory as advisory_mod
        from llm import commentary as commentary_mod
        from llm.schemas import AnalystRationale

        monkeypatch.setattr(advisory_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(commentary_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        payload = AnalystRationale(
            headline="x", why_now="y", key_risks=["z"], invalidation="w"
        )

        class _Prov:
            name = "claude"

            def call_structured(self, *a, **kw):
                return payload

        monkeypatch.setattr(commentary_mod, "get_rationale_provider", lambda: _Prov())

        r = _rec()
        out = enrich_with_llm_rationale(r)

        # Every numeric field must be byte-identical.
        assert out.symbol == r.symbol
        assert out.action == r.action
        assert out.conviction == r.conviction
        assert out.suggested_position_pct == r.suggested_position_pct
        assert out.forecast == r.forecast
        assert out.key_indicators == r.key_indicators
        assert out.data_quality == r.data_quality
        assert out.rationale == r.rationale
        # Only the new field changed.
        assert out.llm_rationale is not None
