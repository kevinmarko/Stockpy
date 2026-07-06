"""
tests/test_reports_library.py
=============================
Offline, deterministic tests for the 📁 Report Library tab (Agent 3).

Covers:
  (a) ``gui.panels._shared.list_report_files`` — newest-first ordering, glob
      filtering, and the nonexistent-directory dead-letter path.
  (b) A light smoke check that ``gui.panels.reports_library.render_reports_library``
      imports cleanly and is callable (the module import pulls in streamlit,
      which is available in this repo's test environment).
  (c) ``streamlit.testing.v1.AppTest``-driven interaction tests for
      ``_html_file_block``'s inline-view toggle — proving the "Hide report"
      button actually closes the embedded report. ``gui/app.py`` uses
      ``layout="wide"``, which leaves little/no page margin outside the
      embedded ``components.html`` iframe; once a tall report is open,
      mouse-wheel scroll while hovering it scrolls the iframe, not the page,
      so the operator can't wheel back up to the original checkbox to close
      it. The "Hide report" button (rendered directly below the iframe) is
      the fix, and this suite locks in that it actually clears the checkbox.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from gui.panels._shared import list_report_files


def _touch(path: Path, mtime: float) -> None:
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


class TestListReportFiles:
    def test_newest_first_ordering(self, tmp_path: Path) -> None:
        base = time.time()
        old = tmp_path / "briefing_old.md"
        mid = tmp_path / "briefing_mid.md"
        new = tmp_path / "briefing_new.md"
        _touch(old, base - 100)
        _touch(mid, base - 50)
        _touch(new, base)

        result = list_report_files(tmp_path, "briefing_*.md")
        assert result == [new, mid, old]

    def test_oldest_first_when_flag_false(self, tmp_path: Path) -> None:
        base = time.time()
        a = tmp_path / "briefing_a.md"
        b = tmp_path / "briefing_b.md"
        _touch(a, base - 10)
        _touch(b, base)

        result = list_report_files(tmp_path, "briefing_*.md", newest_first=False)
        assert result == [a, b]

    def test_glob_filtering(self, tmp_path: Path) -> None:
        base = time.time()
        keep = tmp_path / "briefing_x.md"
        drop_ext = tmp_path / "briefing_x.txt"
        drop_prefix = tmp_path / "report_y.md"
        _touch(keep, base)
        _touch(drop_ext, base)
        _touch(drop_prefix, base)

        result = list_report_files(tmp_path, "briefing_*.md")
        assert result == [keep]

    def test_nonexistent_directory_returns_empty(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        assert list_report_files(missing, "*.md") == []

    def test_file_path_as_directory_returns_empty(self, tmp_path: Path) -> None:
        a_file = tmp_path / "not_a_dir.md"
        a_file.write_text("x", encoding="utf-8")
        # Passing a file (not a directory) must degrade to [] rather than raise.
        assert list_report_files(a_file, "*.md") == []

    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        assert list_report_files(tmp_path, "*_validation_summary.json") == []

    def test_directories_excluded(self, tmp_path: Path) -> None:
        (tmp_path / "briefing_dir.md").mkdir()
        f = tmp_path / "briefing_file.md"
        _touch(f, time.time())
        result = list_report_files(tmp_path, "briefing_*.md")
        assert result == [f]


class TestRenderImportable:
    def test_render_reports_library_exists_and_callable(self) -> None:
        from gui.panels.reports_library import render_reports_library

        assert callable(render_reports_library)

    def test_reexported_from_panels(self) -> None:
        from gui import panels

        assert hasattr(panels, "render_reports_library")
        assert callable(panels.render_reports_library)


def _html_block_script() -> str:
    """A minimal Streamlit script exercising ``_html_file_block`` in
    isolation, for ``AppTest`` interaction simulation."""
    return (
        "import streamlit as st\n"
        "from settings import settings\n"
        "from gui.panels.reports_library import _html_file_block\n"
        "\n"
        "settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)\n"
        "report_path = settings.OUTPUT_DIR / 'daily_report.html'\n"
        "report_path.write_text('<html><body>hi</body></html>', encoding='utf-8')\n"
        "_html_file_block(report_path, download_label='dl')\n"
    )


class TestInlineViewToggle:
    """AppTest-driven proof that the inline report viewer can be closed.

    Regression coverage for the "can't exit out of the report" bug: checking
    the "View inline" checkbox must reveal a "Hide report" button, and
    clicking that button must clear the checkbox (closing the report) —
    without requiring the operator to interact with the original checkbox
    again, since it's not reliably reachable once a tall report traps
    mouse-wheel scroll inside its iframe under the app's wide layout.
    """

    def test_hide_button_absent_until_report_opened(self, tmp_path, monkeypatch) -> None:
        from streamlit.testing.v1 import AppTest
        from settings import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        at = AppTest.from_string(_html_block_script())
        at.run()

        assert at.checkbox[0].value is False
        assert not any("Hide" in b.label for b in at.button)

    def test_hide_button_closes_the_report(self, tmp_path, monkeypatch) -> None:
        from streamlit.testing.v1 import AppTest
        from settings import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        at = AppTest.from_string(_html_block_script())
        at.run()

        at.checkbox[0].check().run()
        assert at.checkbox[0].value is True
        hide_buttons = [b for b in at.button if "Hide" in b.label]
        assert len(hide_buttons) == 1

        hide_buttons[0].click().run()
        assert at.checkbox[0].value is False
        assert not any("Hide" in b.label for b in at.button)
