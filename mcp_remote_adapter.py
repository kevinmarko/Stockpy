"""Stdio proxy that bridges a local MCP client to the InvestYo MCP server running on a remote GCP VM. Opens an SSH session (cd /opt/investyo so pydantic can read .env) and pipes stdin/stdout/stderr transparently to the remote server process."""

import subprocess
import sys
import os

def main():
    # The crucial fix is `cd /opt/investyo` so pydantic doesn't crash reading .env
    cmd = [
        "gcloud", "compute", "ssh", "investyo-vm",
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
