"""
tests/test_launch_webapp_vite_port_ownership.py
================================================
Regression tests for ``launch_webapp.command``'s :5173 port-ownership check.

The incident
------------
``launch_webapp.command`` runs Vite with ``--strictPort`` (a silent port bump
would break CORS against the backends, which are pinned to :5173). Its
``_check_vite_port`` helper therefore has to decide, for whatever process holds
:5173, between two outcomes:

*   **ours** — a leftover dev server we may recycle, so kill it and continue;
*   **foreign** — someone else's process, so ``exit 1`` with an actionable
    message rather than Vite's raw EADDRINUSE stack trace.

The original ownership test was ``lsof -d cwd | grep -q "$SCRIPT_DIR/webapp"``:
true only for *this exact checkout*. But this repo is routinely developed across
many simultaneous ``git worktree`` checkouts (agent sessions create them under
``.claude/worktrees/``), and each one's ``npm run dev`` binds the same fixed
:5173. An abandoned session leaves that server listening indefinitely, and the
cwd prefix test classified it as a total stranger — so the real launcher took
the hard ``exit 1`` path on every subsequent launch and the app simply stopped
opening.

Two things were wrong and both are pinned here:

1.  Ownership must be decided by **git common dir**, which is identical for a
    repository's main checkout and every worktree of it, and different for any
    unrelated project. See :class:`TestRepoCommonDirOwnership`.
2.  The check must run **before** the heavy backends are started. It used to
    run only just before ``npm run dev``, i.e. after data_api, metrics_api and
    the orchestrator daemon had each been started and waited on — so a port
    conflict meant ~20s of booting three backends and then the EXIT trap
    tearing all three back down, which in the daemon log reads as a clean
    "started" line followed one second later by "Received signal 15". See
    :class:`TestPortCheckRunsBeforeBackends`.

These are source-level and behavioural guards; they never touch the real :5173
or any real process.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "launch_webapp.command"


def _launcher_source() -> str:
    assert LAUNCHER.is_file(), f"{LAUNCHER} is missing"
    return LAUNCHER.read_text(encoding="utf-8")


#: A top-level bash function definition: ``name() {`` at column 0 (optionally
#: followed by a trailing comment), through the first line that is exactly
#: ``}`` at column 0 — this file's consistent formatting for top-level
#: functions.
_FUNC_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\(\) \{.*?\n(?:.*?)^\}$", re.S | re.M)


def _function_spans(source: str) -> list[tuple[str, int, int]]:
    """Return ``(name, start_line, end_line)`` (0-indexed, inclusive) per function."""
    spans = []
    for match in _FUNC_RE.finditer(source):
        start = source.count("\n", 0, match.start())
        end = source.count("\n", 0, match.end())
        spans.append((match.group("name"), start, end))
    return spans


def _extract_bash_function(source: str, name: str) -> str:
    """Return the text of a top-level ``name() { ... }`` bash function.

    Raising here (rather than returning "") is deliberate: a renamed or deleted
    helper should fail loudly, not silently test nothing.
    """
    for match in _FUNC_RE.finditer(source):
        if match.group("name") == name:
            return match.group(0) + "\n"
    raise AssertionError(
        f"{name}() not found in launch_webapp.command — if it was renamed, "
        "update this test rather than deleting it: it pins a real incident."
    )


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=60,
    )


def _make_repo(path: Path) -> Path:
    """Create a real git repo with one commit (a worktree needs a commit)."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=path)
    _git("config", "user.email", "test@example.invalid", cwd=path)
    _git("config", "user.name", "Test", cwd=path)
    (path / "README").write_text("x", encoding="utf-8")
    _git("add", "README", cwd=path)
    _git("commit", "-qm", "init", cwd=path)
    return path


def _run_repo_common_dir(probe: Path | str) -> str:
    """Run the REAL ``_repo_common_dir`` helper from the launcher on ``probe``."""
    fn = _extract_bash_function(_launcher_source(), "_repo_common_dir")
    script = f'{fn}\n_repo_common_dir "$1"\n'
    result = subprocess.run(
        ["bash", "-c", script, "bash", str(probe)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout.strip()


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="git and bash are required to exercise the launcher's shell helpers",
)


# ===========================================================================
# 1. Ownership is decided by git common dir
# ===========================================================================
class TestRepoCommonDirOwnership:
    def test_launcher_still_defines_the_helpers(self):
        source = _launcher_source()
        for name in ("_repo_common_dir", "_pid_cwd", "_check_vite_port"):
            assert f"{name}() {{" in source, f"{name}() disappeared from the launcher"

    def test_a_worktree_of_the_same_repo_is_classified_as_ours(self, tmp_path):
        """The exact case that broke launching: a sibling worktree's dev server."""
        main = _make_repo(tmp_path / "main")
        worktree = tmp_path / "sibling"
        _git("worktree", "add", "-q", "-b", "wt", str(worktree), cwd=main)

        main_common = _run_repo_common_dir(main)
        worktree_common = _run_repo_common_dir(worktree)

        assert main_common, "the main checkout must resolve to a git common dir"
        assert worktree_common == main_common, (
            "a worktree of the same repo must share the main checkout's git "
            "common dir — this is the whole ownership signal"
        )

    def test_a_subdirectory_of_a_worktree_is_also_ours(self, tmp_path):
        """Vite's cwd is ``<checkout>/webapp``, not the checkout root."""
        main = _make_repo(tmp_path / "main")
        worktree = tmp_path / "sibling"
        _git("worktree", "add", "-q", "-b", "wt", str(worktree), cwd=main)
        webapp = worktree / "webapp"
        webapp.mkdir()

        assert _run_repo_common_dir(webapp) == _run_repo_common_dir(main)

    def test_an_unrelated_repo_is_still_foreign(self, tmp_path):
        ours = _make_repo(tmp_path / "ours")
        theirs = _make_repo(tmp_path / "theirs")

        ours_common = _run_repo_common_dir(ours)
        theirs_common = _run_repo_common_dir(theirs)

        assert ours_common and theirs_common
        assert theirs_common != ours_common, (
            "widening ownership to sibling worktrees must NOT widen it to "
            "unrelated repositories — those still take the exit-1 path"
        )

    def test_a_non_git_directory_is_foreign(self, tmp_path):
        plain = tmp_path / "not_a_repo"
        plain.mkdir()
        assert _run_repo_common_dir(plain) == ""

    def test_a_missing_or_empty_path_is_foreign(self, tmp_path):
        assert _run_repo_common_dir(tmp_path / "does_not_exist") == ""
        assert _run_repo_common_dir("") == ""

    def test_ownership_condition_is_not_a_bare_cwd_prefix_grep(self):
        """Pin the fix itself, not just the helper it relies on."""
        check = _extract_bash_function(_launcher_source(), "_check_vite_port")
        assert 'grep -q "$SCRIPT_DIR/webapp"' not in check, (
            "_check_vite_port regressed to the cwd-prefix ownership test, which "
            "misclassifies a sibling worktree's dev server as foreign and makes "
            "the launcher exit 1 on every launch"
        )
        assert "_repo_common_dir" in check, (
            "_check_vite_port must decide ownership via _repo_common_dir"
        )
        # The foreign branch must still exist — widening ownership must not
        # have turned this into a kill-anything-on-5173 helper.
        assert "exit 1" in check, (
            "_check_vite_port must still fail closed for a genuinely foreign "
            "process holding :5173"
        )


# ===========================================================================
# 2. The check is pre-flighted before anything heavy starts
# ===========================================================================
class TestPortCheckRunsBeforeBackends:
    def test_check_vite_port_is_called_before_the_first_backend_start(self):
        source = _launcher_source()
        lines = source.splitlines()

        # Calls written INSIDE a function body are not execution order — they
        # only run when that function is itself called. `_bring_up_control_and_
        # pilots_api` contains several `_start_api` calls hundreds of lines
        # above its own single call site, so a naive first-match scan reads the
        # ordering backwards. Exclude every function body and compare only the
        # statements that actually execute top-to-bottom.
        in_function = set()
        for _name, start, end in _function_spans(source):
            in_function.update(range(start, end + 1))

        def first_executed_call(pattern: str) -> int:
            for i, line in enumerate(lines):
                if i in in_function:
                    continue
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue  # a comment mentioning the call is not the call
                if re.search(pattern, stripped):
                    return i
            return -1

        # A bare `_check_vite_port` invocation on its own line, i.e. the call.
        check_at = first_executed_call(r"^_check_vite_port\s*$")
        start_at = first_executed_call(r"^_start_api\s")

        assert check_at != -1, "_check_vite_port is never actually called"
        assert start_at != -1, "_start_api is never actually called"
        assert check_at < start_at, (
            "_check_vite_port must run BEFORE the first _start_api call. When it "
            "ran afterwards, a :5173 conflict booted data_api, metrics_api and "
            "the orchestrator daemon (~20s) only for the EXIT trap to tear all "
            f"three straight back down. (check at line {check_at + 1}, "
            f"first _start_api at line {start_at + 1})"
        )

    def test_launcher_is_syntactically_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(LAUNCHER)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"
