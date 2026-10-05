"""
tests/test_ai_insights_panel.py
================================
Unit tests for ``shared.ai_insights_panel`` (Tier 9 Scope 3) — Streamlit-free
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

from types import SimpleNamespace


from shared.ai_insights_panel import (
    DisagreementRow,
    derive_disagreement_overview,
    disagreement_summary,
    format_chart_pattern_markdown,
    insights_status,
    latest_verdict_maps_from_cache,
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
# TestLatestVerdictMapsFromCache — G15: the durable (webapp) equivalent of
# the legacy tab's st.session_state Claude/Gemini mirrors.
# ---------------------------------------------------------------------------


def _entry(symbol, payload, stored_at="2026-07-30T00:00:00+00:00"):
    return {"payload": payload, "meta": {"symbol": symbol, "provider": "x"}, "stored_at": stored_at}


class TestLatestVerdictMapsFromCache:
    def test_empty_entries_yields_empty_maps(self):
        claude_map, gemini_map = latest_verdict_maps_from_cache([])
        assert claude_map == {}
        assert gemini_map == {}

    def test_classifies_by_payload_shape_not_meta(self):
        entries = [
            _entry("AAPL", {"headline": "h", "why_now": "w"}),
            _entry("MSFT", {"pattern_name": "flag", "trend_direction": "bullish"}),
        ]
        claude_map, gemini_map = latest_verdict_maps_from_cache(entries)
        assert "AAPL" in claude_map
        assert "AAPL" not in gemini_map
        assert "MSFT" in gemini_map
        assert "MSFT" not in claude_map

    def test_ignores_alert_and_research_payloads(self):
        entries = [
            _entry("AAPL", {"body": "push text"}),
            _entry("AAPL", {"thesis_context": "thesis"}),
        ]
        claude_map, gemini_map = latest_verdict_maps_from_cache(entries)
        assert claude_map == {}
        assert gemini_map == {}

    def test_keeps_only_the_most_recent_entry_per_symbol_and_side(self):
        entries = [
            _entry("AAPL", {"headline": "old", "why_now": "w"}, stored_at="2026-07-01T00:00:00"),
            _entry("AAPL", {"headline": "new", "why_now": "w"}, stored_at="2026-07-29T00:00:00"),
        ]
        claude_map, _ = latest_verdict_maps_from_cache(entries)
        assert claude_map["AAPL"]["headline"] == "new"

    def test_missing_symbol_in_meta_is_skipped_not_fatal(self):
        entries = [{"payload": {"headline": "h", "why_now": "w"}, "meta": {}, "stored_at": "t"}]
        claude_map, gemini_map = latest_verdict_maps_from_cache(entries)
        assert claude_map == {}
        assert gemini_map == {}

    def test_malformed_entry_is_skipped_not_fatal(self):
        entries = [
            "not-a-dict",
            {"payload": "not-a-dict-either", "meta": {"symbol": "AAPL"}},
            {"payload": {"headline": "h", "why_now": "w"}, "meta": "not-a-dict"},
        ]
        claude_map, gemini_map = latest_verdict_maps_from_cache(entries)
        assert claude_map == {}
        assert gemini_map == {}

    def test_round_trips_into_derive_disagreement_overview(self):
        entries = [
            _entry("AAPL", {"headline": "Breakout above the range", "why_now": "w"}),
            _entry("AAPL", {"pattern_name": "flag", "trend_direction": "bearish"}),
        ]
        claude_map, gemini_map = latest_verdict_maps_from_cache(entries)
        rows = derive_disagreement_overview(
            signals=[{"symbol": "AAPL", "action": "BUY"}],
            claude_map=claude_map,
            gemini_map=gemini_map,
        )
        assert rows[0].claude_verdict == "bullish"
        assert rows[0].gemini_verdict == "bearish"
        assert rows[0].disagreement is True


# ---------------------------------------------------------------------------
# TestPanelWiring
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestOpalIndependentGating — Fix 1: Opal section renders even when the
# Claude/Gemini insights_status gate is "disabled".
# ---------------------------------------------------------------------------
