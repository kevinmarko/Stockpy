"""
tests/test_help_content_no_import_snapshot.py
===============================================
Regression guard for the "gui/help_content.py snapshots live settings values
once, at import time" staleness bug.

``gui/help_content.py`` is imported by LIVE backend code — not only the
frozen Streamlit GUI — via ``api/pilots_api.py`` and ``pilots/models.py``
(``MODEL_RETRAIN_WINDOW_DAYS``).  Before the fix in this file, module-level
constants such as::

    _KELLY_CAP_PCT = int(settings.KELLY_CAP * 100)

were computed exactly once, the moment the module was first imported, and
then baked verbatim into help/glossary/metric strings for the remaining
lifetime of the process — even if ``settings.KELLY_CAP`` changed afterward
(the whole point of an in-progress settings hot-reload effort elsewhere in
this codebase). This directly violates the repo convention (see CLAUDE.md's
"Thresholds in help text" bullet) that help-text numeric values must stay in
sync with live config, not be a one-time snapshot.

Two layers of guard, in order of importance:

1. **Behavioral** (the one that actually matters) — monkeypatch a live
   ``settings.X`` value *after* ``gui.help_content`` has already been
   imported (no ``importlib.reload``), and assert the text returned by the
   module's public accessors NOW reflects the new value. This is direct
   proof of the fix: against the pre-fix code, every assertion below would
   have failed, because the pre-fix module-level constants were computed
   once, before the monkeypatch ever ran, and were never re-read afterward.

2. **Static** (belt-and-suspenders) — AST-walk ``gui/help_content.py`` and
   assert there is no module-scope ``Assign``/``AnnAssign`` whose RHS reads
   a ``settings.<ATTR>`` attribute *eagerly* (i.e. outside of a nested
   ``def``/``lambda`` body, whose evaluation is deferred until called).
   Catches a regression of the same bug shape even for a setting that isn't
   covered by a specific behavioral case below.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_HELP_CONTENT_PATH = Path(__file__).parent.parent / "gui" / "help_content.py"


# ---------------------------------------------------------------------------
# 1. Behavioral — change settings.X in-process, assert help text picks it up
# ---------------------------------------------------------------------------


class TestSettingsChangesReflectedWithoutReimport:
    """Each case monkeypatches the *live* ``settings.settings`` singleton
    object that ``gui.help_content`` already holds a reference to (via its
    own ``from settings import settings`` at module scope) — deliberately
    with NO ``importlib.reload``. If ``gui.help_content`` baked the old value
    into a module-level constant at import time, the constant would never
    see the monkeypatched value and every assertion below would fail.

    Expected substrings are computed with the exact same transformation the
    source applies (e.g. ``int(x * 100)`` for a "cap as a percent" field)
    rather than hand-computed, so the test is immune to float-formatting
    surprises and only verifies "did the live value get picked up", not
    "does the formatting match my expectation".
    """

    def test_kelly_cap_pct_in_glossary_and_metric_help(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import gui.help_content as hc

        new_value = 0.37
        monkeypatch.setattr("settings.settings.KELLY_CAP", new_value)
        expected_pct = int(new_value * 100)

        entry = hc.get_glossary("kelly target")
        assert entry is not None
        assert f"{expected_pct}%" in entry.resolved_plain_english()
        assert f"{expected_pct}%" in hc.metric_help("Kelly Target")

    def test_kelly_fraction_in_glossary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import gui.help_content as hc

        new_value = 0.777
        monkeypatch.setattr("settings.settings.KELLY_FRACTION", new_value)

        entry = hc.get_glossary("kelly fraction")
        assert entry is not None
        assert f"{new_value}" in entry.resolved_plain_english()

    def test_conviction_delta_in_glossary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import gui.help_content as hc

        new_value = 0.4242
        monkeypatch.setattr(
            "settings.settings.SNAPSHOT_CONVICTION_DELTA_THRESHOLD", new_value
        )

        entry = hc.get_glossary("conviction delta")
        assert entry is not None
        assert f"{new_value}" in entry.resolved_plain_english()

    def test_robinhood_max_notional_in_metric_help(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import gui.help_content as hc

        new_value = 987.65
        monkeypatch.setattr(
            "settings.settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER", new_value
        )

        text = hc.metric_help("robinhood_execution.placed_count")
        assert f"${new_value:,.2f}" in text

    def test_progress_poll_seconds_in_section_help(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import gui.help_content as hc

        new_value = 42
        monkeypatch.setattr("settings.settings.PROGRESS_POLL_SECONDS", new_value)

        text = hc.section_help("pipeline_progress")
        assert f"every {new_value} seconds" in text

    def test_sizing_cap_settings_in_section_help(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import gui.help_content as hc

        alert_pct = 0.61
        threshold_cycles = 13
        escalation_factor = 0.35
        monkeypatch.setattr(
            "settings.settings.SIZING_CAP_ALERT_THRESHOLD_PCT", alert_pct
        )
        monkeypatch.setattr(
            "settings.settings.SIZING_CAP_ESCALATION_THRESHOLD_CYCLES",
            threshold_cycles,
        )
        monkeypatch.setattr(
            "settings.settings.SIZING_CAP_ESCALATION_FACTOR", escalation_factor
        )

        text = hc.section_help("observability.sizing_cap_audit")
        assert f"{int(alert_pct * 100)}%" in text
        assert f"capped {threshold_cycles} consecutive cycles" in text
        assert f"down-weighted {escalation_factor:.2f}x" in text

    def test_etf_transmission_settings_in_section_help(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import gui.help_content as hc

        max_derate = 0.61
        ownership_ref = 0.44
        min_multiplier = 0.789
        monkeypatch.setattr(
            "settings.settings.ETF_TRANSMISSION_MAX_DERATE", max_derate
        )
        monkeypatch.setattr(
            "settings.settings.ETF_TRANSMISSION_OWNERSHIP_REFERENCE", ownership_ref
        )
        monkeypatch.setattr(
            "settings.settings.ETF_TRANSMISSION_MIN_MULTIPLIER", min_multiplier
        )

        text = hc.section_help("observability.etf_transmission")
        assert f"up to {int(max_derate * 100)}%" in text
        assert f"{int(ownership_ref * 100)}%+ ETF ownership" in text
        assert f"floored at {min_multiplier:.2f}x" in text

    def test_value_tracks_live_in_both_directions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not just 'can pick up one new value' — must keep tracking, so a
        second change (not only the first) is also reflected, proving there
        is no caching/memoization anywhere in the read path either."""
        import gui.help_content as hc

        monkeypatch.setattr("settings.settings.KELLY_CAP", 0.11)
        assert "11%" in hc.metric_help("Kelly Target")

        monkeypatch.setattr("settings.settings.KELLY_CAP", 0.22)
        text = hc.metric_help("Kelly Target")
        assert "22%" in text
        assert "11%" not in text


# ---------------------------------------------------------------------------
# 2. Static — AST guard against reintroducing an eager module-scope read
# ---------------------------------------------------------------------------


def _reads_settings_attr_eagerly(node: ast.AST) -> bool:
    """True if *node*'s subtree reads a ``settings.<ATTR>`` attribute
    *eagerly* — i.e. NOT nested inside a ``lambda`` or ``def`` body, whose
    evaluation is deferred until the function/lambda is actually called.
    """

    class _Visitor(ast.NodeVisitor):
        found = False

        def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
            if isinstance(node.value, ast.Name) and node.value.id == "settings":
                self.found = True
            self.generic_visit(node)

        def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
            return  # do not descend — lambda body is deferred until called

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            return  # do not descend — def body is deferred until called

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            return

    visitor = _Visitor()
    visitor.visit(node)
    return visitor.found


class TestNoModuleScopeSettingsSnapshot:
    def test_no_eager_module_scope_settings_read(self) -> None:
        tree = ast.parse(
            _HELP_CONTENT_PATH.read_text(encoding="utf-8"),
            filename=str(_HELP_CONTENT_PATH),
        )

        violations: list[str] = []
        for node in tree.body:  # module scope only — top-level statements
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None:
                continue  # annotation-only declaration, e.g. `x: int`
            if isinstance(value, ast.Lambda):
                continue  # the whole RHS is itself deferred
            if _reads_settings_attr_eagerly(value):
                if isinstance(node, ast.Assign):
                    targets = [t for t in node.targets if isinstance(t, ast.Name)]
                else:
                    targets = [node.target] if isinstance(node.target, ast.Name) else []
                names = ", ".join(t.id for t in targets) or "<complex target>"
                violations.append(f"line {node.lineno}: {names}")

        assert not violations, (
            "Module-scope assignment(s) in gui/help_content.py read "
            "settings.X eagerly — this snapshots the value once at import "
            "time and it will never update again for the life of the "
            "process. Wrap the read in a zero-arg function (or a lambda) "
            "and call it at read time instead:\n" + "\n".join(violations)
        )

    def test_helper_module_actually_defines_settings_backed_functions(self) -> None:
        """Sanity check that the static check above isn't vacuously passing
        because nothing in the file reads settings.X at all anymore — it
        should find the def-wrapped helpers and confirm they DO read
        settings.X (just deferred, inside the def body)."""
        import gui.help_content as hc

        assert callable(hc._kelly_cap_pct)  # noqa: SLF001
        assert isinstance(hc._kelly_cap_pct(), int)
