"""
tests/test_llm_commentary_panel.py
====================================
Unit tests for ``gui.llm_commentary_panel`` — the Streamlit-free helpers
that back the on-demand Claude analyst commentary button on the Reports tab.

All tests run headlessly — no Streamlit, no real LLM provider, no network.
The button-render wiring inside ``gui/panels/__init__.py`` is verified by an
AST/source-grep check (mirroring the pattern used by other GUI section
tests, e.g. ``tests/test_strategy_health.py``).

Coverage
--------
TestCommentaryStatus       — three-state classifier (disabled / missing_key / ready).
TestCommentaryStateKey     — UTC-day + score-bucket determinism; bucket
                             boundary invalidates; symbol case-fold; date
                             pivots invalidate; provider pivots invalidate.
TestFormatRationaleMarkdown — None → unavailable sentinel; full payload →
                              headline + why_now + risks + invalidation rendered;
                              partial payload → only the fields present.
TestSignalRowToRecSkeleton — picks underscore OR space variants; missing
                             fields default to None / 0.0 / "HOLD" (CONSTRAINT #4).
TestGenerateForSymbolRow   — enricher returns None → returns None;
                             enricher returns valid payload → llm_rationale dict;
                             enricher raises → returns None (CONSTRAINT #6);
                             missing symbol → returns None without calling enricher.
TestPanelsWiring           — gui.panels exports _render_llm_commentary_button
                             AND render_report_viewer calls it (source grep).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest import mock

import pytest

from gui.llm_commentary_panel import (
    commentary_state_key,
    commentary_status,
    format_rationale_markdown,
    generate_for_symbol_row,
    signal_row_to_rec_skeleton,
)


# ---------------------------------------------------------------------------
# TestCommentaryStatus
# ---------------------------------------------------------------------------


class TestCommentaryStatus:
    def test_disabled_when_master_switch_off(self):
        s = SimpleNamespace(LLM_COMMENTARY_ENABLED=False, ANTHROPIC_API_KEY="sk-x")
        assert commentary_status(s) == "disabled"

    def test_missing_key_when_enabled_but_no_key(self):
        s = SimpleNamespace(LLM_COMMENTARY_ENABLED=True, ANTHROPIC_API_KEY=None)
        assert commentary_status(s) == "missing_key"

    def test_missing_key_when_enabled_but_empty_key(self):
        s = SimpleNamespace(LLM_COMMENTARY_ENABLED=True, ANTHROPIC_API_KEY="")
        assert commentary_status(s) == "missing_key"

    def test_ready_when_enabled_and_key_set(self):
        s = SimpleNamespace(LLM_COMMENTARY_ENABLED=True, ANTHROPIC_API_KEY="sk-x")
        assert commentary_status(s) == "ready"

    def test_defaults_to_disabled_for_minimal_object(self):
        # Settings stub missing both attributes → still safe → disabled.
        s = SimpleNamespace()
        assert commentary_status(s) == "disabled"


# ---------------------------------------------------------------------------
# TestCommentaryStateKey
# ---------------------------------------------------------------------------


class TestCommentaryStateKey:
    def test_same_inputs_same_key(self):
        k1 = commentary_state_key(symbol="AAPL", score=72.0, action="BUY", date_iso="2026-06-30")
        k2 = commentary_state_key(symbol="AAPL", score=72.0, action="BUY", date_iso="2026-06-30")
        assert k1 == k2

    def test_score_bucket_tolerant_to_small_jitter(self):
        # 72.0 and 73.9 both fall in bucket floor(./5) == 14.
        k_lo = commentary_state_key(symbol="AAPL", score=72.0, action="BUY", date_iso="2026-06-30")
        k_hi = commentary_state_key(symbol="AAPL", score=73.9, action="BUY", date_iso="2026-06-30")
        assert k_lo == k_hi

    def test_bucket_boundary_invalidates(self):
        # 47 (bucket 9) vs 52 (bucket 10).
        k_lo = commentary_state_key(symbol="AAPL", score=47.0, action="BUY", date_iso="2026-06-30")
        k_hi = commentary_state_key(symbol="AAPL", score=52.0, action="BUY", date_iso="2026-06-30")
        assert k_lo != k_hi

    def test_symbol_case_folded(self):
        k_lo = commentary_state_key(symbol="aapl", score=10.0, action="HOLD", date_iso="2026-06-30")
        k_up = commentary_state_key(symbol="AAPL", score=10.0, action="HOLD", date_iso="2026-06-30")
        assert k_lo == k_up

    def test_action_pivots_invalidates(self):
        k_buy = commentary_state_key(symbol="A", score=10.0, action="BUY", date_iso="2026-06-30")
        k_sell = commentary_state_key(symbol="A", score=10.0, action="SELL", date_iso="2026-06-30")
        assert k_buy != k_sell

    def test_date_pivot_invalidates(self):
        k_t = commentary_state_key(symbol="A", score=10.0, action="HOLD", date_iso="2026-06-30")
        k_y = commentary_state_key(symbol="A", score=10.0, action="HOLD", date_iso="2026-06-29")
        assert k_t != k_y

    def test_provider_pivot_invalidates(self):
        k_c = commentary_state_key(symbol="A", score=10.0, action="HOLD", date_iso="2026-06-30",
                                   provider="claude")
        k_g = commentary_state_key(symbol="A", score=10.0, action="HOLD", date_iso="2026-06-30",
                                   provider="gemini")
        assert k_c != k_g


# ---------------------------------------------------------------------------
# TestFormatRationaleMarkdown
# ---------------------------------------------------------------------------


class TestFormatRationaleMarkdown:
    def test_none_yields_unavailable_sentinel(self):
        md = format_rationale_markdown(None)
        assert "unavailable" in md.lower()
        assert "source of truth" in md.lower()

    def test_empty_dict_yields_unavailable_sentinel(self):
        # An empty dict has no fields to render — fall through to the
        # unavailable sentinel rather than emit a blank Markdown block.
        md = format_rationale_markdown({})
        assert "unavailable" in md.lower()

    def test_full_payload_renders_all_sections(self):
        md = format_rationale_markdown({
            "headline": "Healthy uptrend",
            "why_now": "Trend confirmed and pullback measured.",
            "key_risks": ["VIX gap risk", "Earnings within 7 days"],
            "invalidation": "Close below 200-day SMA voids the setup.",
        })
        assert "Healthy uptrend" in md
        assert "Trend confirmed" in md
        assert "VIX gap risk" in md
        assert "Earnings within 7 days" in md
        assert "Close below 200-day SMA" in md
        # Section headers in Markdown form.
        assert "Why now" in md
        assert "Invalidation" in md
        assert "Key risks" in md

    def test_partial_payload_only_renders_present_fields(self):
        md = format_rationale_markdown({"headline": "Only headline"})
        assert "Only headline" in md
        assert "Why now" not in md
        assert "Invalidation" not in md

    def test_non_string_risk_entries_are_coerced(self):
        # The schema only allows strings, but defensive coercion guards
        # the renderer against any upstream bug.
        md = format_rationale_markdown({"key_risks": ["plain str", 42]})
        assert "plain str" in md
        assert "42" in md


# ---------------------------------------------------------------------------
# TestSignalRowToRecSkeleton
# ---------------------------------------------------------------------------


class TestSignalRowToRecSkeleton:
    def test_picks_underscore_variants(self):
        row = {
            "symbol": "aapl",
            "advisory_action": "BUY",
            "advisory_conviction": 0.72,
            "advisory_rationale": "template",
            "score": 75.0,
            "data_quality": "OK",
        }
        sk = signal_row_to_rec_skeleton(row)
        assert sk["symbol"] == "AAPL"
        assert sk["action"] == "BUY"
        assert sk["conviction"] == 0.72
        assert sk["rationale"] == "template"
        assert sk["key_indicators"]["score"] == 75.0
        assert sk["data_quality"] == "OK"

    def test_picks_spaced_variants(self):
        row = {
            "Symbol": "tsla",
            "Action Signal": "SELL",
            "Conviction": 0.6,
            "Rationale": "spaced",
            "Score": 30.0,
            "Data Quality": "STALE",
        }
        sk = signal_row_to_rec_skeleton(row)
        assert sk["symbol"] == "TSLA"
        assert sk["action"] == "SELL"
        assert sk["data_quality"] == "STALE"

    def test_missing_fields_default_safely(self):
        sk = signal_row_to_rec_skeleton({"symbol": "NVDA"})
        # CONSTRAINT #4 — missing fields default to None / 0.0 / sentinel
        # without ever being fabricated to a real-looking number.
        assert sk["symbol"] == "NVDA"
        assert sk["action"] == "HOLD"
        assert sk["forecast"] is None
        assert sk["conviction"] == 0.0


# ---------------------------------------------------------------------------
# TestGenerateForSymbolRow
# ---------------------------------------------------------------------------


def _payload_rec_with_llm(payload: Dict[str, Any]):
    return {"llm_rationale": payload}


class TestGenerateForSymbolRow:
    def test_enricher_returns_dict_payload_extracted(self):
        row = {"symbol": "AAPL", "action": "BUY", "score": 80.0}

        def _enricher(skeleton: Dict[str, Any]) -> Any:
            assert skeleton["symbol"] == "AAPL"
            return _payload_rec_with_llm({"headline": "ok", "why_now": "y",
                                          "key_risks": ["x"], "invalidation": "z"})

        out = generate_for_symbol_row(row, enricher=_enricher)
        assert out is not None
        assert out["headline"] == "ok"

    def test_enricher_returns_object_with_llm_rationale_attribute(self):
        row = {"symbol": "AAPL"}

        class _Rec:
            llm_rationale = {"headline": "via-attr"}

        out = generate_for_symbol_row(row, enricher=lambda _s: _Rec())
        assert out is not None
        assert out["headline"] == "via-attr"

    def test_enricher_returns_none_returns_none(self):
        out = generate_for_symbol_row({"symbol": "AAPL"}, enricher=lambda _s: None)
        assert out is None

    def test_enricher_raises_returns_none(self):
        def _boom(_s):
            raise RuntimeError("synthetic")

        out = generate_for_symbol_row({"symbol": "AAPL"}, enricher=_boom)
        assert out is None

    def test_missing_symbol_returns_none_without_calling_enricher(self):
        called = {"n": 0}

        def _track(_s):
            called["n"] += 1
            return _payload_rec_with_llm({})

        out = generate_for_symbol_row({"symbol": ""}, enricher=_track)
        assert out is None
        assert called["n"] == 0


# ---------------------------------------------------------------------------
# TestPanelsWiring
# ---------------------------------------------------------------------------


class TestPanelsWiring:
    def test_render_button_helper_is_exported(self):
        from gui import panels

        assert hasattr(panels, "_render_llm_commentary_button")
        assert callable(panels._render_llm_commentary_button)

    def test_render_report_viewer_calls_button_helper(self):
        # Source-grep guards against an accidental drop of the wiring in
        # the drill-down expander.
        path = Path(__file__).resolve().parents[1] / "gui" / "panels" / "__init__.py"
        src = path.read_text(encoding="utf-8")
        assert "_render_llm_commentary_button(row, pick)" in src

    def test_helper_imports_from_panel_module(self):
        path = Path(__file__).resolve().parents[1] / "gui" / "panels" / "__init__.py"
        src = path.read_text(encoding="utf-8")
        assert "from gui.llm_commentary_panel import" in src
        for name in (
            "commentary_state_key",
            "commentary_status",
            "format_rationale_markdown",
            "generate_for_symbol_row",
        ):
            assert name in src, f"helper {name} missing from gui/panels/__init__.py"
