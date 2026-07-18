"""
tests/test_mcp_remote_adapter.py
==================================
Unit tests for ``mcp_remote_adapter.py`` — a tiny stdio-proxy shim that lets
a local MCP client (e.g. Claude Desktop) talk to ``investyo_mcp_server.py``
running on the remote ``investyo-vm`` GCE instance over ``gcloud compute
ssh``. The two things worth pinning are (1) the exact ``gcloud`` command it
builds — a typo here silently breaks the remote MCP connection with no local
symptom — and (2) that it wires stdin/stdout/stderr through untouched and
propagates the child's exit code, since it is a transparent proxy and must
not swallow or mutate anything traversing it.

Coverage
--------
* ``_resolve_gcloud`` picks an absolute ``gcloud`` path rather than trusting
  inherited ``PATH`` — GUI-launched clients (Claude Desktop) spawn this
  script with a minimal ``PATH`` that excludes Homebrew, so a bare
  ``"gcloud"`` lookup fails silently and the server just looks
  "disconnected". Covered: ``GCLOUD_BIN`` override, ``shutil.which`` hit,
  fallback to a known Homebrew install path, and the last-resort bare-name
  fallback when nothing is found.
* The subprocess command includes the exact ``gcloud compute ssh`` target
  (instance, zone, project) and the exact remote ``--command`` string
  (``cd /opt/investyo && sudo -u investyo ...`` — the ``cd`` is
  load-bearing per the module's own comment: without it, pydantic-settings
  crashes reading ``.env`` on the remote host).
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


class TestMain:
    def _run(self, monkeypatch, returncode: int) -> MagicMock:
        fake_process = MagicMock()
        fake_process.wait.return_value = None
        fake_process.returncode = returncode

        fake_popen = MagicMock(return_value=fake_process)
        monkeypatch.setattr(mcp_remote_adapter.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(mcp_remote_adapter, "_resolve_gcloud", lambda: "/opt/homebrew/bin/gcloud")

        with pytest.raises(SystemExit) as exc_info:
            mcp_remote_adapter.main()

        assert exc_info.value.code == returncode
        return fake_popen

    def test_builds_expected_gcloud_command(self, monkeypatch):
        fake_popen = self._run(monkeypatch, returncode=0)

        args, kwargs = fake_popen.call_args
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
