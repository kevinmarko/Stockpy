"""
tests/test_stockpy_codebase_auditor.py
=======================================
Regression test for ``scripts/auditor/stockpy_codebase_auditor.py``'s
``orphaned_module`` check.

The auditor's import-graph builders (``StockpyAuditor._walk_ast`` and
``StockpyAuditor._collect_top_level_imports``) used to skip every
``ast.ImportFrom`` node with ``node.level > 0`` -- i.e. every relative import
(``from .foo import bar``) was invisible to the import graph. A module
imported ONLY via a relative import (the normal way a package's
``__init__.py`` re-exports its own submodules) was therefore wrongly flagged
``orphaned_module``, even though it plainly is imported.

This test builds a minimal throwaway package on disk with exactly that shape
-- an ``__init__.py`` that does ``from .sibling import something`` -- and
asserts the auditor no longer reports ``sibling.py`` as orphaned.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest


def _load_auditor_module():
    """Import ``scripts/auditor/stockpy_codebase_auditor.py`` by file path.

    Neither ``scripts/`` nor ``scripts/auditor/`` is a regular package with
    an ``__init__.py`` for ``auditor``, so a plain ``import`` statement isn't
    reliable across environments -- load by path instead, mirroring how the
    module is invoked as a script.
    """
    repo_root = Path(__file__).resolve().parent.parent
    module_path = repo_root / "scripts" / "auditor" / "stockpy_codebase_auditor.py"
    spec = importlib.util.spec_from_file_location("stockpy_codebase_auditor", module_path)
    module = importlib.util.module_from_spec(spec)
    # dataclasses' internals need the module registered in sys.modules
    # before exec_module runs (it looks itself up via cls.__module__).
    sys.modules["stockpy_codebase_auditor"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


auditor_mod = _load_auditor_module()


@pytest.fixture()
def relative_import_package(tmp_path: Path) -> Path:
    """A minimal on-disk package: ``pkg/__init__.py`` imports ``pkg/sibling.py``
    exclusively via a relative import (``from .sibling import something``)."""
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text(
        "from .sibling import something\n",
        encoding="utf-8",
    )
    (pkg_dir / "sibling.py").write_text(
        "def something() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    return tmp_path


def _orphaned_findings(root: Path):
    auditor = auditor_mod.StockpyAuditor(root=root, include_tests=True)
    auditor.parse()
    auditor.check_architecture()
    return [f for f in auditor.findings if f.check == "orphaned_module"]


class TestRelativeImportOrphanFalsePositive:
    def test_sibling_reached_only_via_relative_import_is_not_orphaned(
        self, relative_import_package: Path
    ):
        findings = _orphaned_findings(relative_import_package)
        flagged_modules = {f.module for f in findings}
        assert "pkg/sibling.py" not in flagged_modules, (
            "sibling.py is imported by pkg/__init__.py via `from .sibling import "
            "something` -- it must not be reported as orphaned_module. "
            f"Findings: {findings}"
        )

    def test_real_cli_introspect_modules_are_not_orphaned(self):
        """End-to-end pin against the module this bug was originally found on:
        cli_introspect/introspect.py and cli_introspect/schema.py are imported
        by cli_introspect/__init__.py only via relative imports."""
        repo_root = Path(__file__).resolve().parent.parent
        findings = _orphaned_findings(repo_root)
        flagged_modules = {f.module for f in findings}
        assert "cli_introspect/introspect.py" not in flagged_modules
        assert "cli_introspect/schema.py" not in flagged_modules
