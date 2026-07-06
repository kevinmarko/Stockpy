"""
tests/test_help_content.py
==========================
Tests for gui/help_content.py (§6 of the GUI Help Explainers plan).

All tests are offline — no network calls, no Streamlit.

Coverage
--------
* GlossaryEntry and TabHelp are frozen dataclasses with the expected fields.
* GLOSSARY contains every required term and returns correct types.
* TAB_HELP contains all 14 Command Center tab IDs.
* SECTION_HELP and METRIC_HELP are non-empty dicts.
* get_tab_help / get_glossary return correct objects or None for unknown keys.
* metric_help returns a non-empty string for known keys, empty string for unknown.
* search_glossary finds results and handles blanks / edge cases.
* guide_url constructs the correct path or returns empty string.
* Every non-None guide_anchor in GLOSSARY and TAB_HELP matches a real heading
  slug in docs/HOW_TO_GUIDE.md (the anchor-validity test).
* Threshold values referenced in content are NOT hard-coded literals — the
  content module imports them from settings / thresholds / CONFIG.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Set

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _heading_slug(heading_text: str) -> str:
    """Convert a Markdown heading to a GitHub-style anchor slug (with leading #)."""
    text = heading_text.lower()
    # Keep word characters (letters, digits, underscore), whitespace, and hyphens.
    text = re.sub(r"[^\w\s-]", "", text)
    # Replace each space individually (not collapsing multiples) so that
    # removed non-word chars between words produce double-hyphens (--).
    text = text.replace(" ", "-")
    return "#" + text


def _valid_anchors_from_guide() -> Set[str]:
    """Return the set of all valid anchor slugs in docs/HOW_TO_GUIDE.md."""
    guide_path = Path(__file__).parent.parent / "docs" / "HOW_TO_GUIDE.md"
    slugs: Set[str] = set()
    with guide_path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("## ") or line.startswith("### "):
                heading_text = line.strip().lstrip("#").strip()
                slugs.add(_heading_slug(heading_text))
    return slugs


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


class TestImport:
    def test_module_importable(self) -> None:
        import gui.help_content  # noqa: F401

    def test_public_names_exist(self) -> None:
        from gui.help_content import (
            GLOSSARY,
            METRIC_HELP,
            SECTION_HELP,
            TAB_HELP,
            GlossaryEntry,
            TabHelp,
            get_glossary,
            get_tab_help,
            guide_url,
            metric_help,
            search_glossary,
        )
        # All names are importable — no AssertionError needed; import failure is the test.


# ---------------------------------------------------------------------------
# Dataclass structure
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_glossary_entry_frozen(self) -> None:
        from gui.help_content import GlossaryEntry

        entry = GlossaryEntry(term="foo", plain_english="bar")
        with pytest.raises((AttributeError, TypeError)):
            entry.term = "baz"  # type: ignore[misc]

    def test_glossary_entry_fields(self) -> None:
        from gui.help_content import GlossaryEntry

        e = GlossaryEntry(term="Kelly Target", plain_english="explains kelly", guide_anchor="#8-anchor")
        assert e.term == "Kelly Target"
        assert e.plain_english == "explains kelly"
        assert e.guide_anchor == "#8-anchor"

    def test_glossary_entry_default_anchor_none(self) -> None:
        from gui.help_content import GlossaryEntry

        e = GlossaryEntry(term="x", plain_english="y")
        assert e.guide_anchor is None

    def test_tab_help_frozen(self) -> None:
        from gui.help_content import TabHelp

        t = TabHelp(tab_id="launcher", title="L", description="D")
        with pytest.raises((AttributeError, TypeError)):
            t.tab_id = "other"  # type: ignore[misc]

    def test_tab_help_fields(self) -> None:
        from gui.help_content import TabHelp

        t = TabHelp(
            tab_id="reports",
            title="Reports",
            description="Shows reports.",
            key_concepts=("pbo", "dsr"),
            guide_anchor="#10-validating-a-strategy-before-going-live",
        )
        assert t.tab_id == "reports"
        assert t.title == "Reports"
        assert "pbo" in t.key_concepts
        assert t.guide_anchor == "#10-validating-a-strategy-before-going-live"

    def test_tab_help_default_key_concepts_empty(self) -> None:
        from gui.help_content import TabHelp

        t = TabHelp(tab_id="x", title="X", description="X")
        assert t.key_concepts == ()
        assert t.guide_anchor is None


# ---------------------------------------------------------------------------
# GLOSSARY
# ---------------------------------------------------------------------------


class TestGlossary:
    def test_non_empty(self) -> None:
        from gui.help_content import GLOSSARY

        assert len(GLOSSARY) >= 30, "Expected at least 30 glossary entries"

    def test_keys_are_lowercase(self) -> None:
        from gui.help_content import GLOSSARY

        for key in GLOSSARY:
            assert key == key.lower(), f"Glossary key {key!r} is not lower-cased"

    def test_values_are_glossary_entries(self) -> None:
        from gui.help_content import GLOSSARY, GlossaryEntry

        for key, val in GLOSSARY.items():
            assert isinstance(val, GlossaryEntry), f"GLOSSARY[{key!r}] is not a GlossaryEntry"

    def test_required_terms_present(self) -> None:
        from gui.help_content import GLOSSARY

        required = [
            "kelly target",
            "conviction",
            "macro regime",
            "vix",
            "sahm rule",
            "kill switch",
            "advisory mode",
            "pbo",
            "dsr",
            "dead letter",
            "action signal",
        ]
        for term in required:
            assert term in GLOSSARY, f"Required term {term!r} missing from GLOSSARY"

    def test_plain_english_non_empty(self) -> None:
        from gui.help_content import GLOSSARY

        for key, entry in GLOSSARY.items():
            assert entry.plain_english.strip(), f"plain_english is empty for {key!r}"

    def test_advisory_mode_entry_mentions_no_orders(self) -> None:
        from gui.help_content import GLOSSARY

        entry = GLOSSARY.get("advisory mode")
        assert entry is not None
        text = entry.plain_english.lower()
        assert "no order" in text or "not" in text or "never" in text, (
            "advisory mode entry must reinforce that no orders are placed"
        )

    def test_threshold_values_are_not_hardcoded_pbo(self) -> None:
        """PBO threshold in the entry must match the live import, not a literal."""
        from gui.help_content import GLOSSARY
        from validation.thresholds import PBO_MAX

        entry = GLOSSARY.get("pbo")
        assert entry is not None
        assert str(PBO_MAX) in entry.plain_english, (
            f"PBO entry plain_english should contain the live PBO_MAX ({PBO_MAX}); "
            "do not hard-code the value"
        )

    def test_vix_threshold_references_live_config(self) -> None:
        from engine.advisory import CONFIG as _C
        from gui.help_content import GLOSSARY

        entry = GLOSSARY.get("vix")
        assert entry is not None
        thresh = str(int(_C["macro_vix_gate_threshold"]))
        assert thresh in entry.plain_english, (
            f"VIX entry should reference the live threshold ({thresh})"
        )


# ---------------------------------------------------------------------------
# TAB_HELP
# ---------------------------------------------------------------------------


class TestTabHelp:
    _EXPECTED_TAB_IDS = {
        "launcher",
        "reports",
        "settings",
        "strategy_matrix",
        "paper_monitor",
        "gravity",
        "options",
        "market_data",
        "observability",
        "live_inventory",
        "help",
        "prompts",
        "ai_insights",
        "ai_control_center",
        "report_library",
    }

    def test_all_tab_ids_present(self) -> None:
        from gui.help_content import TAB_HELP

        missing = self._EXPECTED_TAB_IDS - set(TAB_HELP.keys())
        assert not missing, f"Missing tab IDs: {missing}"

    def test_exactly_14_tabs(self) -> None:
        from gui.help_content import TAB_HELP

        # All 15 gui/app.py Command Center tabs now have a TAB_HELP entry
        # (14 original + "report_library", the Report Library tab).
        assert len(TAB_HELP) == 15

    def test_values_are_tab_help(self) -> None:
        from gui.help_content import TAB_HELP, TabHelp

        for tab_id, val in TAB_HELP.items():
            assert isinstance(val, TabHelp), f"TAB_HELP[{tab_id!r}] is not a TabHelp"

    def test_tab_id_field_matches_key(self) -> None:
        from gui.help_content import TAB_HELP

        for tab_id, val in TAB_HELP.items():
            assert val.tab_id == tab_id, (
                f"TAB_HELP[{tab_id!r}].tab_id == {val.tab_id!r}, expected {tab_id!r}"
            )

    def test_descriptions_mention_advisory(self) -> None:
        """At least one tab description must reinforce advisory-only nature."""
        from gui.help_content import TAB_HELP

        combined = " ".join(t.description.lower() for t in TAB_HELP.values())
        assert "advisory" in combined or "no order" in combined


# ---------------------------------------------------------------------------
# SECTION_HELP and METRIC_HELP
# ---------------------------------------------------------------------------


class TestSectionAndMetricHelp:
    def test_section_help_non_empty(self) -> None:
        from gui.help_content import SECTION_HELP

        assert len(SECTION_HELP) >= 5

    def test_section_help_values_are_strings(self) -> None:
        from gui.help_content import SECTION_HELP

        for k, v in SECTION_HELP.items():
            assert isinstance(v, str) and v.strip(), f"SECTION_HELP[{k!r}] is empty or not a string"

    def test_metric_help_non_empty(self) -> None:
        from gui.help_content import METRIC_HELP

        assert len(METRIC_HELP) >= 10

    def test_metric_help_known_keys_non_empty(self) -> None:
        from gui.help_content import METRIC_HELP

        for key in ("Kelly Target", "Conviction", "VIX", "RSI", "GARCH Vol"):
            assert key in METRIC_HELP and METRIC_HELP[key].strip(), (
                f"METRIC_HELP[{key!r}] is missing or empty"
            )


# ---------------------------------------------------------------------------
# Lookup functions
# ---------------------------------------------------------------------------


class TestLookupFunctions:
    def test_get_tab_help_known(self) -> None:
        from gui.help_content import get_tab_help

        result = get_tab_help("launcher")
        assert result is not None
        assert result.tab_id == "launcher"

    def test_get_tab_help_unknown_returns_none(self) -> None:
        from gui.help_content import get_tab_help

        assert get_tab_help("nonexistent_tab_xyz") is None

    def test_get_glossary_known(self) -> None:
        from gui.help_content import get_glossary

        result = get_glossary("Kelly Target")
        assert result is not None
        assert result.term == "Kelly Target"

    def test_get_glossary_case_insensitive(self) -> None:
        from gui.help_content import get_glossary

        assert get_glossary("KELLY TARGET") is not None
        assert get_glossary("kelly target") is not None
        assert get_glossary("Kelly Target") is not None

    def test_get_glossary_unknown_returns_none(self) -> None:
        from gui.help_content import get_glossary

        assert get_glossary("zzz_nonexistent_term") is None

    def test_metric_help_known_key(self) -> None:
        from gui.help_content import metric_help

        result = metric_help("Kelly Target")
        assert isinstance(result, str) and result.strip()

    def test_metric_help_unknown_key_returns_empty(self) -> None:
        from gui.help_content import metric_help

        assert metric_help("zzz_column_not_in_dict") == ""

    def test_metric_help_never_raises(self) -> None:
        from gui.help_content import metric_help

        # Should never raise even for weird input
        assert metric_help("") == ""
        assert metric_help(None) == ""  # type: ignore[arg-type]

    def test_search_glossary_finds_partial_match(self) -> None:
        from gui.help_content import search_glossary

        results = search_glossary("Kelly")
        assert len(results) >= 1
        terms = [r.term for r in results]
        assert any("Kelly" in t for t in terms)

    def test_search_glossary_blank_returns_empty(self) -> None:
        from gui.help_content import search_glossary

        assert search_glossary("") == []
        assert search_glossary("   ") == []

    def test_search_glossary_no_match_returns_empty(self) -> None:
        from gui.help_content import search_glossary

        assert search_glossary("zzz_xyzzy_no_match_ever") == []

    def test_search_glossary_returns_list_of_entries(self) -> None:
        from gui.help_content import GlossaryEntry, search_glossary

        results = search_glossary("macro")
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, GlossaryEntry)

    def test_guide_url_known_anchor(self) -> None:
        from gui.help_content import guide_url

        url = guide_url("#7-reading-the-action-signals")
        assert url.startswith("docs/HOW_TO_GUIDE.md")
        assert "#7-reading-the-action-signals" in url

    def test_guide_url_none_returns_empty(self) -> None:
        from gui.help_content import guide_url

        assert guide_url(None) == ""

    def test_guide_url_empty_string_returns_empty(self) -> None:
        from gui.help_content import guide_url

        assert guide_url("") == ""


# ---------------------------------------------------------------------------
# Anchor validity — every non-None guide_anchor must exist in HOW_TO_GUIDE.md
# ---------------------------------------------------------------------------


class TestAnchorValidity:
    """Verify that every guide_anchor set in GLOSSARY and TAB_HELP is a real
    heading slug from docs/HOW_TO_GUIDE.md.

    The slug algorithm mirrors GitHub Flavored Markdown:
    1. Lowercase the heading text.
    2. Remove characters that are not word chars, whitespace, or hyphens.
    3. Replace each space with a hyphen (without collapsing multiples).
    4. Prepend ``#``.
    """

    def _valid_anchors(self) -> Set[str]:
        return _valid_anchors_from_guide()

    def test_glossary_anchors_all_valid(self) -> None:
        from gui.help_content import GLOSSARY

        valid = self._valid_anchors()
        bad = []
        for term, entry in GLOSSARY.items():
            anchor = entry.guide_anchor
            if anchor is not None and anchor not in valid:
                bad.append(f"  GLOSSARY[{term!r}].guide_anchor={anchor!r}")
        assert not bad, "Invalid guide_anchor(s) found:\n" + "\n".join(bad)

    def test_tab_help_anchors_all_valid(self) -> None:
        from gui.help_content import TAB_HELP

        valid = self._valid_anchors()
        bad = []
        for tab_id, tab in TAB_HELP.items():
            anchor = tab.guide_anchor
            if anchor is not None and anchor not in valid:
                bad.append(f"  TAB_HELP[{tab_id!r}].guide_anchor={anchor!r}")
        assert not bad, "Invalid guide_anchor(s) found:\n" + "\n".join(bad)

    def test_anchor_slug_algorithm(self) -> None:
        """Self-check that the local slug algorithm produces the expected output."""
        assert _heading_slug("8. Understanding Position Sizing (Kelly Target)") == (
            "#8-understanding-position-sizing-kelly-target"
        )
        assert _heading_slug("13. Preflight Check — Are You Ready to Go Live?") == (
            "#13-preflight-check--are-you-ready-to-go-live"
        )
        assert _heading_slug("Advisory-Only Mode") == "#advisory-only-mode"
        assert _heading_slug("Symbol Watch Alerts (Tier 1.4)") == (
            "#symbol-watch-alerts-tier-14"
        )
