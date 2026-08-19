"""
tests/test_scripts_bootstrap.py
================================
Static/AST-based regression guard for the ``scripts/`` venv-reexec +
``.env``-loading bootstrap (``scripts/_bootstrap.py``, see that module's own
docstring for the full design rationale and the real operator failure it
fixes: a bare ``python3 scripts/foo.py`` run under a non-``.venv``
interpreter silently missing dependencies / ``.env``-only credentials).

Mirrors ``tests/test_cnn_lstm_import_order.py``'s style for this repo's
existing "every file in a specific set must satisfy property X" static
guard: parse each file's source with ``ast``, walk the tree, and check for a
specific import/call pattern — no need to actually import or execute any
``scripts/*.py`` module (several have heavy, network-adjacent, or
argparse-driven top-level code that isn't safe to import in a test process).

Coverage
--------
TestEveryScriptCallsBootstrap  — every scripts/*.py file (except __init__.py
                                  and _bootstrap.py itself) imports AND calls
                                  bootstrap() somewhere in its source.
TestBootstrapModuleItself       — scripts/_bootstrap.py is stdlib-only at
                                  module scope (the dotenv import is
                                  deliberately deferred inside bootstrap()'s
                                  own body) and exports a callable bootstrap.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Every scripts/*.py file that must call bootstrap() somewhere in its source,
# except the two carve-outs: __init__.py (an empty package marker, not an
# entry point) and _bootstrap.py itself (the module being called, which
# obviously does not call itself).
_EXCLUDED = {"__init__.py", "_bootstrap.py"}


def _tracked_script_files() -> list[Path]:
    """Return every git-tracked scripts/*.py file (excluding _EXCLUDED).

    Deliberately scoped via `git ls-files` (tracked/staged files only, NOT
    `--others`) rather than a raw `SCRIPTS_DIR.glob("*.py")` directory
    listing. A raw glob also parametrizes over any untracked file an
    operator happens to have sitting in their own scripts/ working tree --
    e.g. a scratch or in-progress script of their own that was never part
    of the change under test and has no relation to this repo's committed
    bootstrap convention. That makes local runs of this test fail based on
    whatever else happens to be on the machine's filesystem, not on the
    actual state of the codebase -- the same class of machine-state
    dependent flakiness already fixed elsewhere by scoping filesystem walks
    to git's own view of the tree (see tests/test_env_loading.py's
    `_tracked_python_files`). Restricting to tracked/staged (not `--others`)
    is intentional here, unlike that sibling fix: this guard's failure mode
    is specifically an operator's unstaged, unrelated WIP file, and a script
    only needs to satisfy this convention once it's actually part of the
    tree (staged or committed), not while it's still being drafted.
    Falls back to a plain glob if `git` is unavailable (e.g. no repository),
    so this test still has meaningful coverage outside a git checkout.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", "scripts/*.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return sorted(p for p in SCRIPTS_DIR.glob("*.py") if p.name not in _EXCLUDED)

    # Unlike a shell glob, git's pathspec `scripts/*.py` also matches nested
    # paths (e.g. scripts/auditor/foo.py) -- filter to direct children of
    # scripts/ only, preserving this test's original non-recursive scope
    # (SCRIPTS_DIR.glob("*.py") never descended into subdirectories either).
    files = [REPO_ROOT / rel for rel in result.stdout.split("\0") if rel]
    return sorted(p for p in files if p.parent == SCRIPTS_DIR and p.name not in _EXCLUDED)


_SCRIPT_FILES = _tracked_script_files()


def _source_imports_bootstrap_name(tree: ast.Module) -> bool:
    """True if the module contains ``from <...>_bootstrap import bootstrap``
    (any module path ending in ``_bootstrap``, covering both the intra-package
    ``from scripts._bootstrap import bootstrap`` form and any relative-import
    equivalent), with ``bootstrap`` among the imported names (an aliased
    import, e.g. ``as _bootstrap_fn``, still counts — the imported NAME is
    what constitutes "imports bootstrap", regardless of what local name it is
    bound to; the call-site check below independently verifies a bare
    ``bootstrap(...)`` call exists, which only lines up when no alias was
    used — see the module-level call check for why this is deliberately not
    over-engineered to resolve aliases).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("_bootstrap"):
            for alias in node.names:
                if alias.name == "bootstrap":
                    return True
    return False


def _source_calls_bootstrap(tree: ast.Module) -> bool:
    """True if the module contains an ``ast.Call`` node whose ``func`` is a
    bare ``ast.Name(id="bootstrap")`` ANYWHERE in the tree — module top level
    (the common case for a script never imported as a library elsewhere) OR
    nested inside a function/``if __name__ == "__main__":`` block (the
    convention for the handful of scripts also imported as a library by
    production code, e.g. ``daily_briefing.py``, ``preflight_check.py``,
    ``refresh_validations.py``, ``snapshot_diff.py``, ``train_lgbm.py``,
    ``train_meta_labelers.py``). Both placements are valid per
    ``scripts/_bootstrap.py``'s own docstring, so this deliberately does not
    restrict where in the tree the call may live.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "bootstrap":
                return True
    return False


@pytest.mark.parametrize(
    "script_path", _SCRIPT_FILES, ids=[p.name for p in _SCRIPT_FILES]
)
def test_script_calls_bootstrap(script_path: Path) -> None:
    """Every scripts/*.py entry point (except __init__.py/_bootstrap.py)
    must both import ``bootstrap`` from a ``*_bootstrap`` module AND call it
    somewhere in its source -- without this, a bare ``python3 scripts/foo.py``
    run under a non-.venv interpreter can silently miss dependencies, and
    any downstream os.environ.get() reader silently sees an unloaded .env.
    """
    assert script_path.exists(), f"scripts file disappeared mid-test: {script_path}"
    tree = ast.parse(script_path.read_text(encoding="utf-8"), filename=str(script_path))

    assert _source_imports_bootstrap_name(tree), (
        f"{script_path.relative_to(REPO_ROOT)} does not import `bootstrap` "
        f"from a `*_bootstrap` module (expected `from scripts._bootstrap "
        f"import bootstrap`)."
    )
    assert _source_calls_bootstrap(tree), (
        f"{script_path.relative_to(REPO_ROOT)} imports `bootstrap` but never "
        f"calls it -- add a bare `bootstrap()` call at module top (for a "
        f"script never imported as a library elsewhere) or inside "
        f"`if __name__ == \"__main__\":` (for a script also imported as a "
        f"library by production code)."
    )


def test_at_least_one_script_file_was_found() -> None:
    """Sanity guard against the parametrized test above silently collecting
    zero cases (e.g. scripts/ moved, glob pattern typo'd) and reporting a
    false 'all tests passed'."""
    assert len(_SCRIPT_FILES) > 0


# ===========================================================================
# scripts/_bootstrap.py itself: stdlib-only at module scope
# ===========================================================================

class TestBootstrapModuleItself:
    _BOOTSTRAP_PATH = SCRIPTS_DIR / "_bootstrap.py"

    def _module_top_level_body(self) -> list[ast.stmt]:
        tree = ast.parse(
            self._BOOTSTRAP_PATH.read_text(encoding="utf-8"),
            filename=str(self._BOOTSTRAP_PATH),
        )
        return tree.body

    def test_no_third_party_or_project_import_at_module_top_level(self) -> None:
        """scripts/_bootstrap.py must be importable under ANY interpreter,
        including a bare system Python with no project dependencies
        installed at all -- so it may only import stdlib modules
        (os/sys/subprocess/pathlib) at module scope. The `dotenv` import is
        deliberately deferred INSIDE bootstrap()'s own body (see that
        function's docstring for why: it must run strictly after the
        venv-reexec guard, never before it) -- walking only the module's
        TOP-LEVEL body (not ast.walk, which would also descend into
        function bodies where the deferred import correctly lives) confirms
        that deferral actually holds.
        """
        _STDLIB_ALLOWED = {"os", "sys", "subprocess", "pathlib", "__future__"}

        offending: list[str] = []
        for stmt in self._module_top_level_body():
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    top_level = alias.name.split(".")[0]
                    if top_level not in _STDLIB_ALLOWED:
                        offending.append(f"import {alias.name} (line {stmt.lineno})")
            elif isinstance(stmt, ast.ImportFrom):
                # A relative import (module is None, level > 0) has no
                # top-level package name to check against the allowlist;
                # scripts/_bootstrap.py uses none, but guard defensively.
                if stmt.module is None:
                    continue
                top_level = stmt.module.split(".")[0]
                if top_level not in _STDLIB_ALLOWED:
                    offending.append(f"from {stmt.module} import ... (line {stmt.lineno})")

        assert not offending, (
            "scripts/_bootstrap.py must be stdlib-only at module scope; "
            f"found non-stdlib top-level import(s): {offending}. Any "
            "third-party import (e.g. dotenv) must be deferred inside "
            "bootstrap()'s own function body, strictly after the "
            "venv-reexec guard."
        )

    def test_dotenv_import_is_deferred_inside_bootstrap_not_at_module_top(self) -> None:
        """Explicit companion to the allowlist check above: `dotenv` must
        NOT appear anywhere in the module's top-level body -- it is only
        ever valid inside a function (bootstrap()'s own body)."""
        for stmt in self._module_top_level_body():
            if isinstance(stmt, ast.ImportFrom):
                assert stmt.module != "dotenv", (
                    f"scripts/_bootstrap.py imports `dotenv` at module top "
                    f"level (line {stmt.lineno}) -- this import must be "
                    f"deferred inside bootstrap()'s own body, strictly "
                    f"after the venv-reexec guard (see that function's "
                    f"docstring)."
                )
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    assert alias.name != "dotenv", (
                        f"scripts/_bootstrap.py imports `dotenv` at module "
                        f"top level (line {stmt.lineno}) -- must be "
                        f"deferred inside bootstrap()'s own body."
                    )

    def test_bootstrap_is_exported_and_callable(self) -> None:
        """scripts/_bootstrap.py must export a callable named `bootstrap`."""
        from scripts._bootstrap import bootstrap

        assert callable(bootstrap)
