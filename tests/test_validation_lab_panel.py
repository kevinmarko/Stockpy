"""
tests/test_validation_lab_panel.py
==================================
Offline unit tests for Agent B's 🔬 **Validation Lab** tab:

* ``render_validation_lab`` is importable and exported on the ``gui.panels``
  namespace (so ``gui/app.py``'s ``safe_panel(panels.render_validation_lab)``
  binding resolves).
* ``orchestrator_runner.launch_validation_run`` builds the exact expected argv
  (``python -m scripts.refresh_validations --strategies … --start … --end …``)
  and spawns NO real process (``subprocess.Popen`` is monkeypatched to a fake).
* ``launch_validation_run([])`` raises ``ValueError`` (defensive empty guard).
* The ``"🔬 Validation Lab"`` label is present in ``gui/app.py``'s ``tab_labels``.
* The tab's help ``guide_anchor`` resolves to a real heading in
  ``docs/HOW_TO_GUIDE.md``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Set

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Fake Popen — records the argv and never spawns anything.
# ---------------------------------------------------------------------------
class _FakePopen:
    """Stand-in for subprocess.Popen: captures the command, spawns nothing."""

    last_cmd: list | None = None

    def __init__(self, cmd, *args, **kwargs):  # noqa: D401 - test double
        type(self).last_cmd = list(cmd)
        self.pid = 4242
        self._args = args
        self._kwargs = kwargs

    def poll(self):
        return None  # "still running"


# ---------------------------------------------------------------------------
# launch_validation_run argv contract
# ---------------------------------------------------------------------------
class TestLaunchValidationRun:
    def test_builds_expected_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from gui import orchestrator_runner

        _FakePopen.last_cmd = None
        monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _FakePopen)

        handle = orchestrator_runner.launch_validation_run(
            ["rsi2_mean_reversion"], "2010-01-01", "2023-12-31"
        )

        cmd = _FakePopen.last_cmd
        assert cmd is not None, "Popen was never called"
        assert cmd == [
            sys.executable,
            "-m",
            "scripts.refresh_validations",
            "--strategies",
            "rsi2_mean_reversion",
            "--start",
            "2010-01-01",
            "--end",
            "2023-12-31",
        ]
        assert handle.mode == "validation"
        assert handle.log_path == orchestrator_runner.VALIDATION_LOG_PATH
        assert handle.pid == 4242

    def test_multiple_strategies_comma_joined(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gui import orchestrator_runner

        _FakePopen.last_cmd = None
        monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _FakePopen)

        orchestrator_runner.launch_validation_run(
            ["a_strategy", "b_strategy"], "2015-01-01", "2020-01-01"
        )
        cmd = _FakePopen.last_cmd
        assert "--strategies" in cmd
        assert cmd[cmd.index("--strategies") + 1] == "a_strategy,b_strategy"

    def test_empty_strategies_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from gui import orchestrator_runner

        _FakePopen.last_cmd = None
        monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _FakePopen)

        with pytest.raises(ValueError):
            orchestrator_runner.launch_validation_run([], "2010-01-01", "2020-01-01")
        # No process was spawned for an invalid call.
        assert _FakePopen.last_cmd is None


# ---------------------------------------------------------------------------
# Panel export + app wiring
# ---------------------------------------------------------------------------
class TestPanelWiring:
    def test_render_validation_lab_importable(self) -> None:
        from gui.panels.validation_lab import render_validation_lab

        assert callable(render_validation_lab)

    def test_render_validation_lab_on_panels_namespace(self) -> None:
        from gui import panels

        assert hasattr(panels, "render_validation_lab")
        assert callable(panels.render_validation_lab)

    def test_tab_label_present_in_app_source(self) -> None:
        # Read the source (not import — gui/app.py runs Streamlit at import time).
        src = (_REPO_ROOT / "gui" / "app.py").read_text(encoding="utf-8")
        assert '"🔬 Validation Lab"' in src
        # And that it is bound as tabs[17] with the right panel.
        assert "safe_panel(panels.render_validation_lab)" in src


# ---------------------------------------------------------------------------
# Help anchor validity (mirrors tests/test_help_content.py::TestAnchorValidity)
# ---------------------------------------------------------------------------
def _heading_slug(heading_text: str) -> str:
    text = heading_text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = text.replace(" ", "-")
    return "#" + text


def _valid_anchors_from_guide() -> Set[str]:
    guide_path = _REPO_ROOT / "docs" / "HOW_TO_GUIDE.md"
    slugs: Set[str] = set()
    with guide_path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("## ") or line.startswith("### "):
                heading_text = line.strip().lstrip("#").strip()
                slugs.add(_heading_slug(heading_text))
    return slugs


class TestValidationLabHelp:
    def test_tab_help_entry_exists(self) -> None:
        from gui.help_content import TAB_HELP

        assert "validation_lab" in TAB_HELP
        tab = TAB_HELP["validation_lab"]
        assert tab.guide_anchor == "#18-validation-lab"

    def test_guide_anchor_resolves(self) -> None:
        from gui.help_content import TAB_HELP

        anchor = TAB_HELP["validation_lab"].guide_anchor
        assert anchor in _valid_anchors_from_guide()

    def test_key_concepts_all_in_glossary(self) -> None:
        from gui.help_content import GLOSSARY, TAB_HELP

        for term in TAB_HELP["validation_lab"].key_concepts:
            assert term in GLOSSARY, f"key_concept {term!r} missing from GLOSSARY"
