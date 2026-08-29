"""
tests/test_backfill_scripts_invocation.py
==========================================
Consolidated direct-path invocation smoke test, and empty-universe guard
test, shared byte-for-byte across three of this repo's ``scripts/backfill_*.py``
CLI entry points. Each was previously duplicated near-identically inside its
own script's test file (each docstring literally said "mirrors
test_backfill_X.py's identical regression test") -- folded into one
parametrized module here per this repo's own redundancy audit. Nothing lost:
every script keeps its own individually-reported test case, this only cuts
the duplicated boilerplate source.

``scripts/backfill_edgar_fundamentals.py`` is deliberately NOT included here
-- its own ``TestInvocationForms`` (still in tests/test_backfill_edgar_fundamentals.py)
tests two invocation forms (direct-path AND ``python -m scripts.X``) with
different, weaker assertions (no ``--months`` flag on this script, no
ModuleNotFoundError-in-stderr check), and its empty-universe guard uses a
different ``resolve_universe(spec, **kwargs)`` signature plus a distinct
``_FakeStore`` fixture -- genuinely not the same test as the three below.

``scripts/backfill_sentiment_history.py``'s empty-universe test only asserts
"must not raise" (no log-message check), unlike the two below's caplog-based
assertion -- so ONLY the invocation-smoke-test half is shared for that
script; its own weaker empty-universe test stays in its own file rather than
being silently strengthened (or the two below silently weakened) here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from scripts import backfill_news_history
from scripts import backfill_news_history_from_audit

_REPO_ROOT = Path(__file__).resolve().parent.parent

# (script relative path, argv[0] used by the script's own main()) for the
# direct-path `--help` invocation smoke test.
_INVOCATION_SCRIPTS = [
    pytest.param("scripts/backfill_news_history.py", id="news_history"),
    pytest.param("scripts/backfill_news_history_from_audit.py", id="news_history_from_audit"),
    pytest.param("scripts/backfill_sentiment_history.py", id="sentiment_history"),
]

# (backfill module, argv[0]) for the shared empty-universe guard test --
# only the two scripts whose test bodies were byte-identical.
_EMPTY_UNIVERSE_MODULES = [
    pytest.param(backfill_news_history, "backfill_news_history.py", id="news_history"),
    pytest.param(
        backfill_news_history_from_audit, "backfill_news_history_from_audit.py",
        id="news_history_from_audit",
    ),
]


@pytest.mark.parametrize("script_path", _INVOCATION_SCRIPTS)
def test_direct_path_invocation_imports_cleanly(script_path):
    """Direct-path invocation (`python scripts/backfill_X.py`) must not die
    with ModuleNotFoundError -- the repo-root sys.path shim regression test."""
    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / script_path), "--help"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert "--months" in result.stdout


@pytest.mark.parametrize("backfill,argv0", _EMPTY_UNIVERSE_MODULES)
def test_empty_universe_logs_error_and_returns(backfill, argv0, caplog):
    with mock.patch.object(backfill, "resolve_universe", return_value=[]):
        with mock.patch.object(sys, "argv", [argv0]):
            with caplog.at_level("ERROR"):
                backfill.main()  # must not raise
    assert any("empty universe" in r.message for r in caplog.records)
