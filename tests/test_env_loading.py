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
