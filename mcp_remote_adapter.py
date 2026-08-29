"""Stdio proxy that bridges a local MCP client to the InvestYo MCP server running on a
remote host. Opens an SSH session (cd /opt/investyo so pydantic can read .env) and pipes
stdin/stdout/stderr transparently to the remote server process.

Two remote modes, selected via INVESTYO_REMOTE_MODE (default "gcp" -- preserves this
script's exact original behavior for anyone with an existing claude_desktop_config.json
pointing at it):

  * "gcp" (default): `gcloud compute ssh` into the named GCP VM, exactly as before.
  * "ssh": a plain `ssh` to any host -- a home Raspberry Pi, an Oracle Cloud Always
    Free / Hetzner / DigitalOcean VPS, or anything else reachable over SSH. Configured
    entirely via env vars (set these in claude_desktop_config.json's "env" block for
    this server, not in your shell, since GUI-launched MCP clients don't inherit one):

      INVESTYO_SSH_HOST      required, e.g. "ubuntu@140.238.12.34" or a Tailscale
                             hostname like "ubuntu@investyo-vps"
      INVESTYO_SSH_PORT      optional, default 22
      INVESTYO_SSH_KEY       optional, path to a private key (passed as `ssh -i`)
      INVESTYO_REMOTE_DIR    optional, default "/opt/investyo" (matches
                             deploy/setup_vps.sh's INSTALL_DIR)
      INVESTYO_REMOTE_USER   optional, default "investyo" (matches
                             deploy/setup_vps.sh's SERVICE_USER) -- the remote command
                             still runs as this system user via `sudo -u`, same as the
                             gcp mode does today
"""

import os
import shutil
import subprocess
import sys

# GUI-launched MCP clients (e.g. Claude Desktop) spawn this script with a minimal
# PATH that excludes Homebrew, so a bare "gcloud"/"ssh" lookup can silently fail to
# find the binary and the server just looks "disconnected" with no local symptom.
# Resolve an absolute path up front instead of trusting the inherited PATH.
_GCLOUD_FALLBACK_PATHS = (
    "/opt/homebrew/bin/gcloud",  # Apple Silicon Homebrew
    "/usr/local/bin/gcloud",  # Intel Homebrew / Linux
)
_SSH_FALLBACK_PATHS = (
    "/usr/bin/ssh",  # macOS/Linux system ssh
    "/opt/homebrew/bin/ssh",  # Homebrew (rare, but same PATH gap as gcloud above)
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


def _resolve_ssh() -> str:
    override = os.environ.get("SSH_BIN")
    if override:
        return override
    found = shutil.which("ssh")
    if found:
        return found
    for path in _SSH_FALLBACK_PATHS:
        if os.path.isfile(path):
            return path
    return "ssh"


def _build_gcp_command() -> list[str]:
    # The crucial fix is `cd /opt/investyo` so pydantic doesn't crash reading .env
    return [
        _resolve_gcloud(), "compute", "ssh", "investyo-vm",
        "--zone=us-east4-c", "--project=stock-data-engine",
        "--quiet", "--ssh-flag=-q",
        "--command", "cd /opt/investyo && sudo -u investyo /opt/investyo/.venv/bin/python /opt/investyo/investyo_mcp_server.py"
    ]


def _build_ssh_command() -> list[str]:
    host = os.environ.get("INVESTYO_SSH_HOST", "").strip()
    if not host:
        print(
            "mcp_remote_adapter.py: INVESTYO_REMOTE_MODE=ssh requires INVESTYO_SSH_HOST "
            "to be set (e.g. 'ubuntu@140.238.12.34'). Set it in claude_desktop_config.json's "
            "\"env\" block for this server.",
            file=sys.stderr,
        )
        sys.exit(1)

    remote_dir = os.environ.get("INVESTYO_REMOTE_DIR", "/opt/investyo")
    remote_user = os.environ.get("INVESTYO_REMOTE_USER", "investyo")
    remote_cmd = f"cd {remote_dir} && sudo -u {remote_user} {remote_dir}/.venv/bin/python {remote_dir}/investyo_mcp_server.py"

    cmd = [_resolve_ssh(), "-q"]
    key_path = os.environ.get("INVESTYO_SSH_KEY", "").strip()
    if key_path:
        cmd += ["-i", key_path]
    port = os.environ.get("INVESTYO_SSH_PORT", "").strip()
    if port:
        cmd += ["-p", port]
    cmd += [host, remote_cmd]
    return cmd


def main():
    mode = os.environ.get("INVESTYO_REMOTE_MODE", "gcp").strip().lower()
    if mode == "ssh":
        cmd = _build_ssh_command()
    elif mode == "gcp":
        cmd = _build_gcp_command()
    else:
        print(
            f"mcp_remote_adapter.py: unknown INVESTYO_REMOTE_MODE={mode!r} "
            "(expected 'gcp' or 'ssh')",
            file=sys.stderr,
        )
        sys.exit(1)

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
