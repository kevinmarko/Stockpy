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
