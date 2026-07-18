"""Stdio proxy that bridges a local MCP client to the InvestYo MCP server running on a remote GCP VM. Opens an SSH session (cd /opt/investyo so pydantic can read .env) and pipes stdin/stdout/stderr transparently to the remote server process."""

import os
import shutil
import subprocess
import sys

# GUI-launched MCP clients (e.g. Claude Desktop) spawn this script with a minimal
# PATH that excludes Homebrew, so a bare "gcloud" lookup silently fails to find
# the binary and the server just looks "disconnected" with no local symptom.
# Resolve an absolute path up front instead of trusting the inherited PATH.
_GCLOUD_FALLBACK_PATHS = (
    "/opt/homebrew/bin/gcloud",  # Apple Silicon Homebrew
    "/usr/local/bin/gcloud",  # Intel Homebrew / Linux
)


def _resolve_gcloud() -> str:
    override = os.environ.get("GCLOUD_BIN")
    if override:
        return override
    found = shutil.which("gcloud")
    if found:
        return found
    for path in _GCLOUD_FALLBACK_PATHS:
        if os.path.isfile(path):
            return path
    return "gcloud"


def main():
    # The crucial fix is `cd /opt/investyo` so pydantic doesn't crash reading .env
    cmd = [
        _resolve_gcloud(), "compute", "ssh", "investyo-vm",
        "--zone=us-east4-c", "--project=stock-data-engine",
        "--quiet", "--ssh-flag=-q",
        "--command", "cd /opt/investyo && sudo -u investyo /opt/investyo/.venv/bin/python /opt/investyo/investyo_mcp_server.py"
    ]
    
    # We pipe stdin, stdout, stderr directly. 
    # This acts as a transparent stdio proxy.
    process = subprocess.Popen(
        cmd,
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr
    )
    process.wait()
    sys.exit(process.returncode)

if __name__ == "__main__":
    main()
