"""
tests/test_env_loading.py
=========================
Regression tests for the os.environ <-> .env loading contract.

Why this exists
---------------
pydantic-settings (Settings in settings.py) reads .env into its own model but
does NOT propagate values to os.environ.  Several runtime modules (notably
data/robinhood_portfolio.py for RH_USERNAME / RH_PASSWORD / RH_MFA_SECRET) read
credentials via os.environ.get() directly.  If load_dotenv() is removed from
the orchestrator entry points, those modules will silently see empty strings
even when .env is fully populated — producing the production failure mode:

    "Required environment variable 'RH_USERNAME' (or 'ROBINHOOD_USERNAME')
     is missing or empty."

These tests pin the contract so that regression is caught at CI time.

Also covers ``settings.ENV_PATH`` — the single repo-root-anchored ``.env``
locator every other locator (pydantic-settings' own ``env_file=``,
``gui/env_io.py``, ``data/brokerage_credentials.py``, ``scripts/_bootstrap.py``)
must import instead of re-deriving — and
``data/robinhood_portfolio.py::_require_setting`` (renamed from
``_require_env``), the ``settings.settings.X``-based credential-presence
guard that replaced a direct ``os.environ`` read.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent

# Entry-point modules that MUST call load_dotenv() before any project imports
# read os.environ at import time.
_ENTRY_POINTS = ("main.py", "main_orchestrator.py")


def _module_calls_load_dotenv_anywhere(path: Path) -> bool:
    """Return True if the module's source contains ANY call to load_dotenv
    (under any alias), at module top OR inside any function body.

    The call MUST live somewhere — module top causes test pollution by
    populating os.environ on import, so the production convention is to
    invoke it inside the entry-point function(s) instead.  Either placement
    is acceptable here; what's not acceptable is removing it entirely, which
    would silently break direct os.environ.get() readers like
    data/robinhood_portfolio.py.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    # Map alias name -> "load_dotenv" (handles `from dotenv import load_dotenv [as X]`).
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "dotenv":
            for alias in node.names:
                if alias.name == "load_dotenv":
                    aliases[alias.asname or alias.name] = "load_dotenv"

    if not aliases:
        return False

    # Walk every Call node in the module and check whether its target is a
    # bare-name call (function-call style) to one of our load_dotenv aliases.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in aliases:
                return True
    return False


@pytest.mark.parametrize("entry", _ENTRY_POINTS)
def test_entrypoint_calls_load_dotenv(entry: str) -> None:
    """Every orchestrator entry point must invoke load_dotenv() somewhere.

    The canonical placement (since it caused test pollution at module top)
    is inside the entry-point function (e.g. main(), run_once()).  Without
    *any* such call, os.environ.get(...) in downstream modules returns ""
    even though .env is populated — breaking Robinhood auth and any other
    direct-environ readers.  See data/robinhood_portfolio.py:203.
    """
    path = REPO_ROOT / entry
    assert path.exists(), f"Entry point {entry!r} not found"
    assert _module_calls_load_dotenv_anywhere(path), (
        f"{entry} must call load_dotenv() somewhere so RH_USERNAME, "
        f"FRED_API_KEY etc. reach os.environ at runtime.  Without it, "
        f"pydantic-settings reads .env into Settings but direct "
        f"os.environ.get() readers see empty strings and fail at runtime."
    )


def test_load_dotenv_actually_populates_environ(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Functional check: load_dotenv() with a fixture .env file populates
    os.environ for keys not already present."""
    from dotenv import load_dotenv

    fixture = tmp_path / ".env"
    fixture.write_text("REGRESSION_TEST_KEY=hello_world\n", encoding="utf-8")

    monkeypatch.delenv("REGRESSION_TEST_KEY", raising=False)
    load_dotenv(dotenv_path=fixture, override=False)
    try:
        assert os.environ.get("REGRESSION_TEST_KEY") == "hello_world"
    finally:
        os.environ.pop("REGRESSION_TEST_KEY", None)


# ===========================================================================
# Regression guard: no bare load_dotenv() call sites anywhere in production
# code.  A bare load_dotenv() (no dotenv_path/first-positional arg) falls
# back to python-dotenv's own find_dotenv(), which walks UP the directory
# tree from the process CWD — in a git worktree with no .env of its own,
# this silently resolves to a PARENT checkout's .env instead, polluting the
# real os.environ with that checkout's values for the rest of the process.
#
# This exact bug class has been fixed at least four times in this codebase
# (main.py, main_orchestrator.py, app_shell.py, desktop/orchestrator_daemon.py,
# the api/*.py services, scripts/_bootstrap.py, gui/app.py — see settings.py's
# ENV_PATH docstring) and was found AGAIN in engine/gravity_ai_runner.py,
# engine/opal_research.py, and engine/llm_commentary.py: their main() CLI
# entry points called bare `load_dotenv(override=False)`, which in a worktree
# checkout silently loaded a sibling checkout's .env and mutated the real
# process os.environ — corrupting every test that ran afterward in the same
# pytest session (tests/test_runtime_flags.py, tests/test_runtime_flags_writer.py,
# tests/test_pilots_api_tunables.py, tests/test_prompt_registry_resolution.py,
# tests/test_settings.py — 18 failures, all traced to this one root cause).
# This scan exists so a fifth recurrence fails CI instead of silently
# reintroducing cross-worktree test pollution.
# ===========================================================================

def _find_bare_load_dotenv_calls(path: Path) -> list[int]:
    """Return line numbers of any `load_dotenv(...)` call (under any import
    alias) in `path` that supplies no path argument at all (neither a first
    positional arg nor a `dotenv_path=` keyword) — i.e. a bare call that
    falls back to find_dotenv()'s CWD-relative, worktree-unsafe search."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "dotenv":
            for alias in node.names:
                if alias.name == "load_dotenv":
                    aliases[alias.asname or alias.name] = "load_dotenv"

    if not aliases:
        return []

    bare_lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in aliases:
                has_positional_path = len(node.args) >= 1
                has_dotenv_path_kwarg = any(kw.arg == "dotenv_path" for kw in node.keywords)
                if not has_positional_path and not has_dotenv_path_kwarg:
                    bare_lines.append(node.lineno)
    return bare_lines


# Directories excluded from the repo-wide scan: virtualenv/vendor/build
# output (not our code), and tests/ (test fixtures legitimately construct
# throwaway .env files and pass dotenv_path= explicitly, or call
# load_dotenv() against a monkeypatched/reverted os.environ — the risk this
# guard targets is a worktree-unsafe call reachable from production code).
_SCAN_EXCLUDED_DIR_PARTS = {".venv", "node_modules", "tests", "webapp", ".git"}


def test_no_bare_load_dotenv_calls_in_production_code() -> None:
    offenders: list[str] = []
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in _SCAN_EXCLUDED_DIR_PARTS for part in path.relative_to(REPO_ROOT).parts):
            continue
        for lineno in _find_bare_load_dotenv_calls(path):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")

    assert not offenders, (
        "Bare load_dotenv() call(s) found — these fall back to find_dotenv(), "
        "which walks UP from the process CWD and, in a git worktree with no "
        ".env of its own, silently loads a PARENT checkout's .env instead, "
        "polluting the real os.environ for the rest of the process. Pass "
        "settings.ENV_PATH explicitly (or a REPO_ROOT-derived path, for the "
        "documented leaf-module exceptions that cannot import settings.py): "
        + ", ".join(offenders)
    )


# ===========================================================================
# settings.ENV_PATH — the single source of truth every other .env locator
# in the codebase must import instead of re-deriving (see settings.py's own
# comment immediately above the ENV_PATH definition for the three previously
# disagreeing mechanisms this constant unifies).
# ===========================================================================

class TestEnvPathAnchor:
    def test_env_path_is_a_path_anchored_at_settings_module_location(self) -> None:
        """ENV_PATH must exist, be a pathlib.Path, and be computed relative
        to settings.py's OWN file location (not the process CWD) — this is
        what makes it safe to import from any worktree/CWD without silently
        resolving to a sibling checkout's .env."""
        import settings as settings_module

        assert hasattr(settings_module, "ENV_PATH")
        assert isinstance(settings_module.ENV_PATH, Path)
        assert settings_module.ENV_PATH == (
            Path(settings_module.__file__).resolve().parent / ".env"
        )

    def test_settings_model_config_env_file_is_env_path(self) -> None:
        """pydantic-settings' own env_file= must point at the SAME ENV_PATH
        constant, not a separately-derived '.env' string — otherwise
        Settings() and every other ENV_PATH-based locator could silently
        drift apart and read two different files.

        Chosen over an actual chdir-then-reconstruct-Settings() functional
        test: SettingsConfigDict is a TypedDict/dict-like, so inspecting
        Settings.model_config directly is a precise, side-effect-free check
        of the exact configured value pydantic-settings will use — no risk
        of the reconstructed Settings() instance picking up a stray .env
        from an unexpected directory or mutating any process-global state
        (os.environ, the settings singleton) that other tests depend on.
        """
        from settings import ENV_PATH, Settings

        assert Settings.model_config["env_file"] == ENV_PATH

    def test_env_path_unaffected_by_process_cwd(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Functional companion to the model_config check above: ENV_PATH is
        a module-level constant computed ONCE at import time from
        settings.py's own __file__, never from the process's current
        working directory — so chdir'ing to a directory with no .env at all
        must never change it.

        Safe to actually chdir here (unlike reconstructing Settings()
        itself, which risks side effects) because this test only reads the
        already-computed constant — it never triggers a fresh .env load or
        mutates the settings singleton.
        """
        import settings as settings_module

        before = settings_module.ENV_PATH
        monkeypatch.chdir(tmp_path)
        assert settings_module.ENV_PATH == before
        assert settings_module.ENV_PATH.name == ".env"
        assert settings_module.ENV_PATH.is_absolute()

    def test_runtime_flags_derives_the_same_anchor(self) -> None:
        """``runtime_flags.py`` is a new ``.env`` locator, and it CANNOT import
        ``ENV_PATH`` — it is a stdlib-only leaf imported BY ``settings.py``, so
        importing settings back would be a circular import that breaks
        ``import settings`` for the whole application. It therefore re-derives
        the repo root from its own ``__file__``: the one case where
        re-derivation is forced rather than sloppy.

        This test is the guard that keeps that forced duplicate in sync. It
        lives here (as well as in ``tests/test_runtime_flags.py``) so anyone
        changing ENV_PATH sees the dependency in the file they are editing.

        Why it matters: ``runtime_flags.real_environment_keys()`` decides which
        fields are pinned by a real shell export by diffing ``os.environ``
        against the parsed ``.env``. If it parsed a DIFFERENT ``.env`` than the
        one pydantic-settings loaded, every field would be misclassified.
        """
        import settings as settings_module
        import runtime_flags

        derived = Path(runtime_flags.__file__).resolve().parent / ".env"
        assert derived == settings_module.ENV_PATH
        # ...and the store itself hangs off that same repo-root anchor.
        assert (
            runtime_flags.DEFAULT_STORE_PATH.parent.parent
            == settings_module.ENV_PATH.parent
        )


# ===========================================================================
# data/robinhood_portfolio.py::_require_setting (renamed from _require_env) —
# reads a named settings.settings.X attribute (never os.environ directly) and
# raises RuntimeError when it's missing/empty, or returns the stripped value
# when set. Tested here directly against a real Settings field (RH_USERNAME)
# rather than a fake attribute name, since _require_setting takes an
# arbitrary attribute NAME string and does getattr(_settings, name, None).
# ===========================================================================

class TestRequireSetting:
    def test_raises_runtime_error_when_setting_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import settings as settings_module
        from data.robinhood_portfolio import _require_setting

        monkeypatch.setattr(settings_module.settings, "RH_USERNAME", None, raising=False)
        with pytest.raises(RuntimeError, match="RH_USERNAME"):
            _require_setting("RH_USERNAME")

    def test_raises_runtime_error_when_setting_is_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import settings as settings_module
        from data.robinhood_portfolio import _require_setting

        monkeypatch.setattr(settings_module.settings, "RH_USERNAME", "", raising=False)
        with pytest.raises(RuntimeError, match="RH_USERNAME"):
            _require_setting("RH_USERNAME")

    def test_returns_stripped_value_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import settings as settings_module
        from data.robinhood_portfolio import _require_setting

        monkeypatch.setattr(
            settings_module.settings, "RH_USERNAME", "  someone@example.com  ", raising=False
        )
        result = _require_setting("RH_USERNAME")
        assert result == "someone@example.com"
