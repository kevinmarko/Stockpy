"""
tests/test_mcp_remote_adapter.py
==================================
Unit tests for ``mcp_remote_adapter.py`` — a tiny stdio-proxy shim that lets
a local MCP client (e.g. Claude Desktop) talk to ``investyo_mcp_server.py``
running on the remote ``investyo-vm`` GCE instance over ``gcloud compute
ssh``. The module has no branching logic of its own; the two things worth
pinning are (1) the exact ``gcloud`` command it builds — a typo here
silently breaks the remote MCP connection with no local symptom — and
(2) that it wires stdin/stdout/stderr through untouched and propagates the
child's exit code, since it is a transparent proxy and must not swallow or
mutate anything traversing it.

Coverage
--------
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


class TestMain:
    def _run(self, monkeypatch, returncode: int) -> MagicMock:
        fake_process = MagicMock()
        fake_process.wait.return_value = None
        fake_process.returncode = returncode

        fake_popen = MagicMock(return_value=fake_process)
        monkeypatch.setattr(mcp_remote_adapter.subprocess, "Popen", fake_popen)

        with pytest.raises(SystemExit) as exc_info:
            mcp_remote_adapter.main()

        assert exc_info.value.code == returncode
        return fake_popen

    def test_builds_expected_gcloud_command(self, monkeypatch):
        fake_popen = self._run(monkeypatch, returncode=0)

        args, kwargs = fake_popen.call_args
        cmd = args[0]

        assert cmd[:4] == ["gcloud", "compute", "ssh", "investyo-vm"]
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
