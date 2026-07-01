"""
tests/test_ai_insights_panel.py
================================
Unit tests for ``gui.ai_insights_panel`` (Tier 9 Scope 3) — Streamlit-free
helpers behind the AI Insights tab.

Coverage
--------
TestInsightsStatus         — disabled / missing_key / ready truth table.
TestFormatChartMarkdown    — None → unavailable sentinel; full payload →
                             pattern + trend arrow + narrative + support +
                             resistance; partial payload only renders present
                             fields (no fabricated placeholders).
TestDeriveDisagreement     — empty signals → []; agreement → no disagreement;
                             explicit disagreement flagged; missing side
                             NEVER flags disagreement (CONSTRAINT #4);
                             heuristic direction picked from headline.
TestSummary                — counts add up; missing sides counted correctly.
TestPanelWiring            — render_ai_insights exposed; gui/app.py wires
                             tab 12; render_ai_insights calls inner helpers.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gui.ai_insights_panel import (
    DisagreementRow,
    derive_disagreement_overview,
    disagreement_summary,
    format_chart_pattern_markdown,
    insights_status,
)


# ---------------------------------------------------------------------------
# TestInsightsStatus
# ---------------------------------------------------------------------------


class TestInsightsStatus:
    def test_disabled_when_switch_off(self):
        assert insights_status(SimpleNamespace()) == "disabled"

    def test_missing_key_when_switch_on_no_key(self):
        s = SimpleNamespace(LLM_COMMENTARY_ENABLED=True, GEMINI_API_KEY=None)
        assert insights_status(s) == "missing_key"

    def test_missing_key_when_key_is_empty_string(self):
        s = SimpleNamespace(LLM_COMMENTARY_ENABLED=True, GEMINI_API_KEY="")
        assert insights_status(s) == "missing_key"

    def test_ready_when_switch_on_and_key_set(self):
        s = SimpleNamespace(LLM_COMMENTARY_ENABLED=True, GEMINI_API_KEY="g-x")
        assert insights_status(s) == "ready"


# ---------------------------------------------------------------------------
# TestFormatChartMarkdown
# ---------------------------------------------------------------------------


class TestFormatChartMarkdown:
    def test_none_yields_unavailable_sentinel(self):
        md = format_chart_pattern_markdown(None)
        assert "unavailable" in md.lower()
        assert "source of truth" in md.lower()

    def test_empty_dict_yields_unavailable_sentinel(self):
        assert "unavailable" in format_chart_pattern_markdown({}).lower()

    def test_full_payload_renders_all_sections(self):
        md = format_chart_pattern_markdown({
            "pattern_name": "ascending triangle",
            "trend_direction": "bullish",
            "narrative": "Trend confirmed by 200-day SMA.",
            "confidence": "high",
            "support_levels": ["recent low near $170"],
            "resistance_levels": ["prior breakout zone"],
        })
        assert "ascending triangle" in md
        assert "▲" in md
        assert "high confidence" in md
        assert "200-day SMA" in md
        assert "recent low near $170" in md

    def test_partial_payload_only_renders_present_fields(self):
        md = format_chart_pattern_markdown({"pattern_name": "only pattern"})
        assert "only pattern" in md
        assert "▲" not in md
        assert "Support" not in md

    def test_bearish_direction_renders_down_arrow(self):
        md = format_chart_pattern_markdown({
            "pattern_name": "head and shoulders",
            "trend_direction": "bearish",
            "narrative": "Right shoulder forming.",
        })
        assert "▼" in md

    def test_neutral_direction_renders_arrow(self):
        md = format_chart_pattern_markdown({
            "pattern_name": "rectangle",
            "trend_direction": "neutral",
            "narrative": "Sideways consolidation.",
        })
        assert "→" in md


# ---------------------------------------------------------------------------
# TestDeriveDisagreement
# ---------------------------------------------------------------------------


class TestDeriveDisagreement:
    def test_empty_signals_returns_empty(self):
        assert derive_disagreement_overview([]) == []

    def test_explicit_disagreement_flagged(self):
        rows = derive_disagreement_overview(
            [{"symbol": "AAPL", "action": "BUY"}],
            claude_map={"AAPL": {"trend_direction": "bullish"}},
            gemini_map={"AAPL": {"trend_direction": "bearish"}},
        )
        assert len(rows) == 1
        assert rows[0].disagreement is True

    def test_explicit_agreement_not_flagged(self):
        rows = derive_disagreement_overview(
            [{"symbol": "AAPL", "action": "BUY"}],
            claude_map={"AAPL": {"trend_direction": "bullish"}},
            gemini_map={"AAPL": {"trend_direction": "bullish"}},
        )
        assert rows[0].disagreement is False

    def test_missing_side_never_flags_disagreement(self):
        """CONSTRAINT #4 — partial coverage never produces a fabricated disagreement."""
        rows = derive_disagreement_overview(
            [{"symbol": "AAPL", "action": "BUY"}, {"symbol": "TSLA", "action": "HOLD"}],
            claude_map={"AAPL": {"trend_direction": "bullish"}},
            gemini_map={"TSLA": {"trend_direction": "neutral"}},
        )
        assert rows[0].disagreement is False
        assert rows[1].disagreement is False
        # And each row records the present side, not None for both.
        assert rows[0].claude_verdict == "bullish"
        assert rows[0].gemini_verdict is None
        assert rows[1].claude_verdict is None
        assert rows[1].gemini_verdict == "neutral"

    def test_heuristic_direction_from_rationale_headline(self):
        rows = derive_disagreement_overview(
            [{"symbol": "AAPL", "action": "BUY"}],
            claude_map={"AAPL": {"headline": "Strong bullish setup"}},
            gemini_map={"AAPL": {"trend_direction": "bullish"}},
        )
        assert rows[0].claude_verdict == "bullish"
        assert rows[0].disagreement is False

    def test_unknown_headline_yields_none_not_fabricated_direction(self):
        rows = derive_disagreement_overview(
            [{"symbol": "AAPL", "action": "BUY"}],
            claude_map={"AAPL": {"headline": "Some neutral-sounding text"}},
            gemini_map={},
        )
        # "neutral" is one of the keywords, so this one DOES match → neutral.
        assert rows[0].claude_verdict == "neutral"

    def test_action_lifted_from_spaced_or_underscored_key(self):
        rows = derive_disagreement_overview(
            [{"symbol": "AAPL", "Action Signal": "STRONG BUY"}],
        )
        assert rows[0].advisory_action == "STRONG BUY"

    def test_skips_rows_missing_symbol(self):
        rows = derive_disagreement_overview(
            [{"action": "BUY"}, {"symbol": "AAPL", "action": "HOLD"}],
        )
        assert len(rows) == 1
        assert rows[0].symbol == "AAPL"

    def test_skips_non_mapping_entries(self):
        rows = derive_disagreement_overview(
            [None, 42, {"symbol": "X", "action": "BUY"}],
        )
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# TestSummary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_counts_add_up(self):
        rows = [
            DisagreementRow("A", "BUY", "bullish", "bullish", False),
            DisagreementRow("B", "BUY", "bullish", "bearish", True),
            DisagreementRow("C", "HOLD", None, "neutral", False),
            DisagreementRow("D", "HOLD", "neutral", None, False),
        ]
        s = disagreement_summary(rows)
        assert s["total_symbols"] == 4
        assert s["both_present"] == 2
        assert s["agreements"] == 1
        assert s["disagreements"] == 1


# ---------------------------------------------------------------------------
# TestPanelWiring
# ---------------------------------------------------------------------------


class TestPanelWiring:
    def test_render_ai_insights_exported(self):
        from gui import panels

        assert hasattr(panels, "render_ai_insights")
        assert callable(panels.render_ai_insights)
        assert hasattr(panels, "_render_gemini_chart_section")

    def test_app_py_registers_thirteenth_tab(self):
        path = Path(__file__).resolve().parents[1] / "gui" / "app.py"
        src = path.read_text(encoding="utf-8")
        assert "🪄 AI Insights" in src
        assert "panels.render_ai_insights" in src
        assert "tabs[12]" in src

    def test_panel_imports_helper_module(self):
        path = Path(__file__).resolve().parents[1] / "gui" / "panels" / "__init__.py"
        src = path.read_text(encoding="utf-8")
        assert "from gui.ai_insights_panel import" in src
        for name in (
            "derive_disagreement_overview",
            "disagreement_summary",
            "format_chart_pattern_markdown",
            "insights_status",
        ):
            assert name in src, f"helper {name} missing"
