"""
tests/test_mcp_remote_adapter.py
==================================
Unit tests for ``mcp_remote_adapter.py`` — a tiny stdio-proxy shim that lets
a local MCP client (e.g. Claude Desktop) talk to ``investyo_mcp_server.py``
running on a remote host, over either ``gcloud compute ssh`` (the original,
still-default ``INVESTYO_REMOTE_MODE=gcp``) or a plain ``ssh`` to any host
(``INVESTYO_REMOTE_MODE=ssh`` — a home Raspberry Pi, an Oracle Cloud/Hetzner/
DigitalOcean VPS, etc. — see ``deploy/setup_vps.sh``). The things worth
pinning are (1) the exact command each mode builds — a typo here silently
breaks the remote MCP connection with no local symptom — and (2) that it
wires stdin/stdout/stderr through untouched and propagates the child's exit
code, since it is a transparent proxy and must not swallow or mutate
anything traversing it.

Coverage
--------
* ``_resolve_gcloud`` / ``_resolve_ssh`` each pick an absolute binary path
  rather than trusting inherited ``PATH`` — GUI-launched clients (Claude
  Desktop) spawn this script with a minimal ``PATH`` that excludes Homebrew,
  so a bare ``"gcloud"``/``"ssh"`` lookup can fail silently and the server
  just looks "disconnected". Covered: env override, ``shutil.which`` hit,
  fallback to a known install path, and the last-resort bare-name fallback
  when nothing is found.
* ``INVESTYO_REMOTE_MODE`` dispatch: default/``"gcp"`` builds the exact
  ``gcloud compute ssh`` command (instance, zone, project) and the exact
  remote ``--command`` string (``cd /opt/investyo && sudo -u investyo ...``
  — the ``cd`` is load-bearing per the module's own comment: without it,
  pydantic-settings crashes reading ``.env`` on the remote host);
  ``"ssh"`` builds a plain ``ssh`` command from ``INVESTYO_SSH_HOST``/
  ``INVESTYO_SSH_PORT``/``INVESTYO_SSH_KEY``/``INVESTYO_REMOTE_DIR``/
  ``INVESTYO_REMOTE_USER``, exits with a clear error if
  ``INVESTYO_SSH_HOST`` is missing, and an unknown mode exits with a clear
  error rather than silently falling back to either real mode.
* ``Popen`` is invoked with the caller's real ``stdin``/``stdout``/``stderr``
  (no capturing/buffering — this must stay a transparent proxy).
* ``main()`` propagates the child process's exit code via ``sys.exit``,
  for both a clean exit and a non-zero exit.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

import mcp_remote_adapter


class TestResolveGcloud:
    def test_env_override_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("GCLOUD_BIN", "/custom/path/gcloud")
        monkeypatch.setattr(mcp_remote_adapter.shutil, "which", lambda name: "/should/not/be/used")

        assert mcp_remote_adapter._resolve_gcloud() == "/custom/path/gcloud"

    def test_uses_which_when_no_override(self, monkeypatch):
        monkeypatch.delenv("GCLOUD_BIN", raising=False)
        monkeypatch.setattr(mcp_remote_adapter.shutil, "which", lambda name: "/opt/homebrew/bin/gcloud")

        assert mcp_remote_adapter._resolve_gcloud() == "/opt/homebrew/bin/gcloud"

    def test_falls_back_to_known_install_path_when_which_fails(self, monkeypatch):
        monkeypatch.delenv("GCLOUD_BIN", raising=False)
        monkeypatch.setattr(mcp_remote_adapter.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            mcp_remote_adapter.os.path, "isfile",
            lambda path: path == "/usr/local/bin/gcloud",
        )

        assert mcp_remote_adapter._resolve_gcloud() == "/usr/local/bin/gcloud"

    def test_falls_back_to_bare_name_as_last_resort(self, monkeypatch):
        monkeypatch.delenv("GCLOUD_BIN", raising=False)
        monkeypatch.setattr(mcp_remote_adapter.shutil, "which", lambda name: None)
        monkeypatch.setattr(mcp_remote_adapter.os.path, "isfile", lambda path: False)

        assert mcp_remote_adapter._resolve_gcloud() == "gcloud"


class TestResolveSsh:
    def test_env_override_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("SSH_BIN", "/custom/path/ssh")
        monkeypatch.setattr(mcp_remote_adapter.shutil, "which", lambda name: "/should/not/be/used")

        assert mcp_remote_adapter._resolve_ssh() == "/custom/path/ssh"

    def test_uses_which_when_no_override(self, monkeypatch):
        monkeypatch.delenv("SSH_BIN", raising=False)
        monkeypatch.setattr(mcp_remote_adapter.shutil, "which", lambda name: "/usr/bin/ssh")

        assert mcp_remote_adapter._resolve_ssh() == "/usr/bin/ssh"

    def test_falls_back_to_bare_name_as_last_resort(self, monkeypatch):
        monkeypatch.delenv("SSH_BIN", raising=False)
        monkeypatch.setattr(mcp_remote_adapter.shutil, "which", lambda name: None)
        monkeypatch.setattr(mcp_remote_adapter.os.path, "isfile", lambda path: False)

        assert mcp_remote_adapter._resolve_ssh() == "ssh"


class TestMain:
    def _run(self, monkeypatch, returncode: int) -> MagicMock:
        fake_process = MagicMock()
        fake_process.wait.return_value = None
        fake_process.returncode = returncode

        fake_popen = MagicMock(return_value=fake_process)
        monkeypatch.setattr(mcp_remote_adapter.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(mcp_remote_adapter, "_resolve_gcloud", lambda: "/opt/homebrew/bin/gcloud")
        # Default mode is "gcp" only in the absence of an inherited env var —
        # pin that explicitly so this class's tests can't flake based on the
        # ambient shell's environment.
        monkeypatch.delenv("INVESTYO_REMOTE_MODE", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            mcp_remote_adapter.main()

        assert exc_info.value.code == returncode
        return fake_popen

    def test_builds_expected_gcloud_command(self, monkeypatch):
        fake_popen = self._run(monkeypatch, returncode=0)

        args, _kwargs = fake_popen.call_args
        cmd = args[0]

        assert cmd[:4] == ["/opt/homebrew/bin/gcloud", "compute", "ssh", "investyo-vm"]
        assert "--zone=us-east4-c" in cmd
        assert "--project=stock-data-engine" in cmd
        assert "--quiet" in cmd
        assert "--ssh-flag=-q" in cmd
        assert "--command" in cmd
        remote_command = cmd[cmd.index("--command") + 1]
        assert remote_command.startswith("cd /opt/investyo && ")
        assert "investyo_mcp_server.py" in remote_command
        assert "sudo -u investyo" in remote_command

    def test_wires_stdio_through_untouched(self, monkeypatch):
        fake_popen = self._run(monkeypatch, returncode=0)

        _, kwargs = fake_popen.call_args
        assert kwargs["stdin"] is sys.stdin
        assert kwargs["stdout"] is sys.stdout
        assert kwargs["stderr"] is sys.stderr

    def test_propagates_nonzero_exit_code(self, monkeypatch):
        self._run(monkeypatch, returncode=17)


class TestSshMode:
    def _run_ssh(self, monkeypatch, env: dict, returncode: int = 0) -> MagicMock:
        fake_process = MagicMock()
        fake_process.wait.return_value = None
        fake_process.returncode = returncode

        fake_popen = MagicMock(return_value=fake_process)
        monkeypatch.setattr(mcp_remote_adapter.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(mcp_remote_adapter, "_resolve_ssh", lambda: "/usr/bin/ssh")

        monkeypatch.setenv("INVESTYO_REMOTE_MODE", "ssh")
        for key in ("INVESTYO_SSH_HOST", "INVESTYO_SSH_PORT", "INVESTYO_SSH_KEY",
                    "INVESTYO_REMOTE_DIR", "INVESTYO_REMOTE_USER"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        with pytest.raises(SystemExit) as exc_info:
            mcp_remote_adapter.main()

        assert exc_info.value.code == returncode
        return fake_popen

    def test_builds_minimal_ssh_command_with_defaults(self, monkeypatch):
        fake_popen = self._run_ssh(monkeypatch, {"INVESTYO_SSH_HOST": "ubuntu@140.238.12.34"})

        args, _kwargs = fake_popen.call_args
        cmd = args[0]

        assert cmd[0] == "/usr/bin/ssh"
        assert cmd[1] == "-q"
        assert cmd[-2] == "ubuntu@140.238.12.34"
        remote_command = cmd[-1]
        assert remote_command == (
            "cd /opt/investyo && sudo -u investyo "
            "/opt/investyo/.venv/bin/python /opt/investyo/investyo_mcp_server.py"
        )
        # No -i/-p flags injected when the optional env vars are unset.
        assert "-i" not in cmd
        assert "-p" not in cmd

    def test_builds_full_ssh_command_with_all_options(self, monkeypatch):
        fake_popen = self._run_ssh(monkeypatch, {
            "INVESTYO_SSH_HOST": "pi@investyo-vps",
            "INVESTYO_SSH_PORT": "2222",
            "INVESTYO_SSH_KEY": "/home/user/.ssh/investyo_key",
            "INVESTYO_REMOTE_DIR": "/srv/investyo",
            "INVESTYO_REMOTE_USER": "svc",
        })

        args, _kwargs = fake_popen.call_args
        cmd = args[0]

        assert cmd == [
            "/usr/bin/ssh", "-q",
            "-i", "/home/user/.ssh/investyo_key",
            "-p", "2222",
            "pi@investyo-vps",
            "cd /srv/investyo && sudo -u svc /srv/investyo/.venv/bin/python /srv/investyo/investyo_mcp_server.py",
        ]

    def test_wires_stdio_through_untouched(self, monkeypatch):
        fake_popen = self._run_ssh(monkeypatch, {"INVESTYO_SSH_HOST": "ubuntu@140.238.12.34"})

        _, kwargs = fake_popen.call_args
        assert kwargs["stdin"] is sys.stdin
        assert kwargs["stdout"] is sys.stdout
        assert kwargs["stderr"] is sys.stderr

    def test_propagates_nonzero_exit_code(self, monkeypatch):
        self._run_ssh(monkeypatch, {"INVESTYO_SSH_HOST": "ubuntu@140.238.12.34"}, returncode=17)

    def test_missing_ssh_host_exits_with_clear_error(self, monkeypatch, capsys):
        monkeypatch.setenv("INVESTYO_REMOTE_MODE", "ssh")
        monkeypatch.delenv("INVESTYO_SSH_HOST", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            mcp_remote_adapter.main()

        assert exc_info.value.code == 1
        assert "INVESTYO_SSH_HOST" in capsys.readouterr().err


class TestUnknownMode:
    def test_unknown_mode_exits_with_clear_error_rather_than_silent_fallback(self, monkeypatch, capsys):
        monkeypatch.setenv("INVESTYO_REMOTE_MODE", "carrier-pigeon")

        with pytest.raises(SystemExit) as exc_info:
            mcp_remote_adapter.main()

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "carrier-pigeon" in err
        assert "'gcp' or 'ssh'" in err
