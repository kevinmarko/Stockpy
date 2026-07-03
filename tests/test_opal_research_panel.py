"""
tests/test_opal_research_panel.py
====================================
Unit tests for the Opal research-brief GUI surface (Tier 9 Scope 4):
``gui.ai_insights_panel.format_research_brief_markdown`` plus the
``_render_opal_research_section`` wiring inside ``gui/panels/__init__.py``.

Coverage
--------
TestFormatResearchBriefMarkdown  — None/empty → unavailable sentinel;
                                   full payload renders every section;
                                   partial payload only renders present
                                   fields (never a fabricated placeholder);
                                   confidence suffix rendering.
TestPanelWiring                  — ``_render_opal_research_section`` exists
                                   in ``gui/panels/__init__.py``, is gated
                                   on its OWN ``OPAL_RESEARCH_ENABLED``
                                   switch (independent of
                                   ``LLM_COMMENTARY_ENABLED``), is called
                                   from ``render_ai_insights`` BEFORE the
                                   Claude section (front-of-pipeline), and
                                   ``gui/ai_insights_panel.py`` exports
                                   ``format_research_brief_markdown``.
"""

from __future__ import annotations

from pathlib import Path

from gui.ai_insights_panel import format_research_brief_markdown


_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# TestFormatResearchBriefMarkdown
# ---------------------------------------------------------------------------


class TestFormatResearchBriefMarkdown:
    def test_none_yields_unavailable_sentinel(self):
        out = format_research_brief_markdown(None)
        assert "unavailable" in out.lower()

    def test_empty_dict_yields_unavailable_sentinel(self):
        out = format_research_brief_markdown({})
        assert "unavailable" in out.lower()

    def test_full_payload_renders_all_sections(self):
        payload = {
            "thesis_context": "Momentum is building into the print.",
            "catalysts": ["Q3 earnings call scheduled Nov 4"],
            "risk_factors": ["Guidance miss risk"],
            "recent_developments": ["Announced buyback"],
            "data_confidence": "high",
            "sources_note": "Based on 3 Finnhub headlines from the past 7 days.",
        }
        out = format_research_brief_markdown(payload)
        assert "Momentum is building into the print." in out
        assert "Q3 earnings call scheduled Nov 4" in out
        assert "Guidance miss risk" in out
        assert "Announced buyback" in out
        assert "high" in out
        assert "Based on 3 Finnhub headlines" in out

    def test_partial_payload_only_renders_present_fields(self):
        payload = {
            "thesis_context": "Thin data available.",
            "catalysts": ["Some catalyst"],
            "risk_factors": ["Some risk"],
            "data_confidence": "low",
            "sources_note": "Sparse.",
            # recent_developments intentionally absent
        }
        out = format_research_brief_markdown(payload)
        assert "Thin data available." in out
        assert "Recent developments" not in out

    def test_missing_thesis_context_renders_other_sections(self):
        payload = {
            "catalysts": ["Some catalyst"],
            "risk_factors": ["Some risk"],
            "sources_note": "Sparse.",
        }
        out = format_research_brief_markdown(payload)
        assert "Some catalyst" in out
        assert "Some risk" in out

    def test_confidence_suffix_appears_next_to_thesis(self):
        payload = {
            "thesis_context": "Setup looks constructive.",
            "catalysts": ["c"],
            "risk_factors": ["r"],
            "data_confidence": "medium",
            "sources_note": "s",
        }
        out = format_research_brief_markdown(payload)
        assert "medium confidence" in out

    def test_all_fields_empty_falls_back_to_sentinel(self):
        payload = {
            "thesis_context": "",
            "catalysts": [],
            "risk_factors": [],
            "recent_developments": [],
            "data_confidence": "",
            "sources_note": "",
        }
        out = format_research_brief_markdown(payload)
        assert "unavailable" in out.lower()


# ---------------------------------------------------------------------------
# TestPanelWiring
# ---------------------------------------------------------------------------


class TestPanelWiring:
    def test_ai_insights_panel_exports_formatter(self):
        from gui import ai_insights_panel

        assert hasattr(ai_insights_panel, "format_research_brief_markdown")
        assert callable(ai_insights_panel.format_research_brief_markdown)

    def test_render_opal_research_section_exists(self):
        from gui import panels

        assert hasattr(panels, "_render_opal_research_section")
        assert callable(panels._render_opal_research_section)

    def test_opal_section_gated_on_own_switch(self):
        path = _REPO_ROOT / "gui" / "panels" / "__init__.py"
        src = path.read_text(encoding="utf-8")
        # The Opal helper must reference its OWN independent switch.
        start = src.index("def _render_opal_research_section")
        end = src.index("\ndef ", start + 1)
        body = src[start:end]
        assert "OPAL_RESEARCH_ENABLED" in body

    def test_opal_section_called_before_claude_section_in_render_ai_insights(self):
        path = _REPO_ROOT / "gui" / "panels" / "__init__.py"
        src = path.read_text(encoding="utf-8")
        start = src.index("def render_ai_insights")
        end = src.index("\ndef ", start + 1)
        body = src[start:end]
        opal_idx = body.index("_render_opal_research_section(")
        assert opal_idx != -1
        # Claude commentary button helper should appear only AFTER Opal's call
        # within this function body (front-of-pipeline ordering).
        claude_idx = body.find("_render_llm_commentary_button(")
        if claude_idx != -1:
            assert opal_idx < claude_idx

    def test_research_module_imported_lazily_inside_section(self):
        path = _REPO_ROOT / "gui" / "panels" / "__init__.py"
        src = path.read_text(encoding="utf-8")
        top_level_lines = [
            ln for ln in src.splitlines() if not ln.startswith(" ") and not ln.startswith("\t")
        ]
        joined = "\n".join(top_level_lines)
        assert "from llm.research import" not in joined
        assert "import llm.research" not in joined
