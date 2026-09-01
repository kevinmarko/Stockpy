"""
tests/test_jules_dispatch.py
==============================
Unit tests for ``scripts/jules_dispatch.py``'s argument parsing and dispatch
logic. ``data.jules_client.list_sources``/``dispatch_session`` are mocked at
the point they were imported into ``scripts.jules_dispatch``'s own
namespace (``from data.jules_client import ...`` there), so these tests
never touch the real Jules API.

``list-sources`` remains genuinely functional (read-only) and is tested as
such. ``create-session`` is NON-FUNCTIONAL as of 2026-08-31: Jules can only
audit/review an existing PR or codebase, not write new code or open a PR
from a prompt alone, so ``dispatch_session`` now unconditionally raises
``JulesCapabilityNotAvailable`` before making any network call — the
``TestCreateSessionWithConfirm`` tests below mock that real (disabled)
shape rather than a successful dispatch.

``main()`` returns an int exit code (see ``scripts/jules_dispatch.py``'s own
module docstring for why), so no ``pytest.raises(SystemExit)`` is needed
anywhere here — only the "missing required arg" cases, where argparse
itself calls ``sys.exit()`` before ``main()``'s own return path is reached.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from data.jules_client import JulesCapabilityNotAvailable, JulesUnavailable
from scripts.jules_dispatch import main


# ===========================================================================
# list-sources
# ===========================================================================


class TestListSources:
    def test_success_prints_sources_and_returns_zero(self, capsys) -> None:
        fake_result = {
            "sources": [
                {"name": "sources/github/kevinmarko/Stockpy-live"},
                {"name": "sources/github/kevinmarko/other-repo"},
            ]
        }
        with patch("scripts.jules_dispatch.list_sources", return_value=fake_result) as mock_fn:
            exit_code = main(["list-sources"])

        mock_fn.assert_called_once_with()
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "sources/github/kevinmarko/Stockpy-live" in out
        assert "sources/github/kevinmarko/other-repo" in out

    def test_empty_sources_returns_zero(self, capsys) -> None:
        with patch("scripts.jules_dispatch.list_sources", return_value={"sources": []}):
            exit_code = main(["list-sources"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "No Jules sources" in out

    def test_failure_prints_error_to_stderr_and_returns_one(self, capsys) -> None:
        with patch(
            "scripts.jules_dispatch.list_sources",
            side_effect=JulesUnavailable("JULES_ENABLED is False"),
        ):
            exit_code = main(["list-sources"])

        assert exit_code == 1
        err = capsys.readouterr().err
        assert "JULES_ENABLED is False" in err


# ===========================================================================
# create-session
# ===========================================================================


class TestCreateSessionWithoutConfirm:
    def test_dispatch_never_called_and_exit_code_one(self, capsys) -> None:
        with patch("scripts.jules_dispatch.dispatch_session") as mock_dispatch:
            exit_code = main(
                [
                    "create-session",
                    "--prompt",
                    "fix the bug",
                    "--title",
                    "Fix bug",
                    "--source",
                    "sources/github/kevinmarko/Stockpy-live",
                ]
            )

        mock_dispatch.assert_not_called()
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "--confirm" in err


class TestCreateSessionWithConfirm:
    def test_capability_not_available_prints_error_and_returns_one(self, capsys) -> None:
        """``dispatch_session`` is permanently disabled — it now raises
        ``JulesCapabilityNotAvailable`` unconditionally, as the very first
        thing it does, before making any network call (Jules can only
        audit/review an existing PR or codebase; it cannot write new code
        or open a PR from a prompt alone). The CLI must surface this as a
        clean stderr message and exit code 1, never a silent success.
        """
        with patch(
            "scripts.jules_dispatch.dispatch_session",
            side_effect=JulesCapabilityNotAvailable(
                "dispatch_session() assumed Jules could write new code and "
                "open a PR from a prompt alone. This capability does not "
                "exist."
            ),
        ) as mock_dispatch:
            exit_code = main(
                [
                    "create-session",
                    "--prompt",
                    "fix the bug",
                    "--title",
                    "Fix bug",
                    "--source",
                    "sources/github/kevinmarko/Stockpy-live",
                    "--confirm",
                ]
            )

        # dispatch_session is mocked at the point jules_dispatch imported it
        # into its own namespace, so this call never reaches the real HTTP
        # layer in data/jules_client.py -- asserting the mock was called
        # (with the real kwargs) alongside a nonzero exit code is the CLI-
        # level proof that no HTTP call was attempted: the only path to an
        # HTTP request is inside the real (here, unexercised) function body.
        mock_dispatch.assert_called_once_with(
            prompt="fix the bug",
            source="sources/github/kevinmarko/Stockpy-live",
            branch="main",
            title="Fix bug",
            force=False,
            confirm=True,
        )
        assert exit_code == 1
        out = capsys.readouterr()
        assert "dispatched successfully" not in out.out.lower()
        err = out.err
        assert "JulesCapabilityNotAvailable" in err or "capability does not exist" in err

    def test_failure_prints_error_to_stderr_and_returns_one(self, capsys) -> None:
        with patch(
            "scripts.jules_dispatch.dispatch_session",
            side_effect=JulesUnavailable("unknown source"),
        ):
            exit_code = main(
                [
                    "create-session",
                    "--prompt",
                    "fix the bug",
                    "--title",
                    "Fix bug",
                    "--source",
                    "sources/github/bad/repo",
                    "--confirm",
                ]
            )

        assert exit_code == 1
        err = capsys.readouterr().err
        assert "unknown source" in err

    def test_force_flag_passed_through(self, capsys) -> None:
        """Argument-threading test: --force must still reach ``dispatch_session``'s
        kwargs even though the (mocked, real-shaped) call now always raises
        ``JulesCapabilityNotAvailable`` rather than succeeding.
        """
        with patch(
            "scripts.jules_dispatch.dispatch_session",
            side_effect=JulesCapabilityNotAvailable("capability does not exist"),
        ) as mock_dispatch:
            exit_code = main(
                [
                    "create-session",
                    "--prompt",
                    "fix the bug",
                    "--title",
                    "Fix bug",
                    "--source",
                    "sources/github/kevinmarko/Stockpy-live",
                    "--confirm",
                    "--force",
                ]
            )

        assert exit_code == 1
        _, kwargs = mock_dispatch.call_args
        assert kwargs["force"] is True

    def test_custom_branch_passed_through(self) -> None:
        """Argument-threading test: --branch must still reach ``dispatch_session``'s
        kwargs even though the (mocked, real-shaped) call now always raises
        ``JulesCapabilityNotAvailable`` rather than succeeding.
        """
        with patch(
            "scripts.jules_dispatch.dispatch_session",
            side_effect=JulesCapabilityNotAvailable("capability does not exist"),
        ) as mock_dispatch:
            exit_code = main(
                [
                    "create-session",
                    "--prompt",
                    "fix the bug",
                    "--title",
                    "Fix bug",
                    "--source",
                    "sources/github/kevinmarko/Stockpy-live",
                    "--branch",
                    "develop",
                    "--confirm",
                ]
            )

        assert exit_code == 1
        _, kwargs = mock_dispatch.call_args
        assert kwargs["branch"] == "develop"


# ===========================================================================
# argparse required-argument enforcement
# ===========================================================================


class TestRequiredArgs:
    @pytest.mark.parametrize(
        "missing_arg",
        ["--prompt", "--title", "--source"],
    )
    def test_missing_required_arg_exits_nonzero(self, missing_arg: str) -> None:
        args = [
            "create-session",
            "--prompt",
            "fix the bug",
            "--title",
            "Fix bug",
            "--source",
            "sources/github/kevinmarko/Stockpy-live",
            "--confirm",
        ]
        # Strip out the flag-and-value pair for the arg under test.
        idx = args.index(missing_arg)
        del args[idx : idx + 2]

        with pytest.raises(SystemExit) as exc_info:
            main(args)

        assert exc_info.value.code != 0
