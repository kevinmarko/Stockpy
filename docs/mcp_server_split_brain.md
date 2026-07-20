# MCP Server Split-Brain: `investyo-platform` vs `investyo`

> Documentation only — **no remediation command in this note has been executed.**
> See [`docs/architecture/observability-and-apis.md`](architecture/observability-and-apis.md)
> for `investyo_mcp_server.py`'s full architecture reference.

## The problem

Two MCP server registrations on this machine both point at *the same source file*
(`investyo_mcp_server.py`) but run **different code**, because one reads it off
local disk and the other reads it off a remote VM that nobody has redeployed:

| Registration | Config file | Transport | What it actually runs |
|---|---|---|---|
| `investyo-platform` | `~/.claude.json` (Claude Code project config, under both the `/Users/kevinlee/Desktop/Stockpy` and `/Users/kevinlee` project entries) | stdio, direct | `/Users/kevinlee/Desktop/Stockpy/.venv/bin/python3 /Users/kevinlee/Desktop/Stockpy/investyo_mcp_server.py` — whatever is checked out **locally**, currently `origin/main` tip |
| `investyo` | `~/Library/Application Support/Claude/claude_desktop_config.json` (Claude Desktop) | stdio, via `gcloud compute ssh` | `sudo -u investyo bash -c 'cd /opt/investyo && exec .venv/bin/python investyo_mcp_server.py'` on the **`investyo-vm`** GCP VM (`us-east4-c` / `stock-data-engine`) — whatever was checked out at `/opt/investyo` the last time someone ran `deploy/setup_gcp_vm.sh` or manually pulled |

`deploy/setup_gcp_vm.sh` is a **one-time bootstrap** script (creates the service
user, clones/extracts the repo, builds the venv, opens the firewall). Nothing in
this repo re-runs it or otherwise pulls fresh code onto the VM on a schedule —
the VM only advances when an operator manually SSHes in and does it. The local
checkout, by contrast, advances every time this repo's `main` is synced.

## Confirmed drift (as of 2026-07-20, this PR's branch point)

The local `investyo_mcp_server.py` on this branch defines **41 `@mcp.tool()`
functions + 3 `@mcp.resource()` + 1 `@mcp.prompt()`**. Comparing the tool
surfaces actually advertised by the two live connections in this environment,
the VM-hosted `investyo` server is missing 10 tools that exist locally —
**two entire categories added well after `deploy/setup_gcp_vm.sh` was last run
against the VM:**

- **Pilots marketplace** (added in `40ef6fa8`, "Add Pilots marketplace tools to
  investyo_mcp_server.py"): `list_pilots`, `get_pilot_detail`,
  `get_pilot_performance`, `get_pilot_trades`, `get_follows`, `follow_pilot`
- **Read-only Advisory & Market Intelligence** (added in `ba74b57c`, "MCP: add
  read-only advisory/options/regime/coverage tools"): `get_recommendation`,
  `get_options_directive`, `get_regime_status`, `get_portfolio_coverage`

Both categories are also missing from this doc's own tool-inventory list in
`docs/architecture/observability-and-apis.md`'s `investyo_mcp_server.py` entry
as of this writing (a secondary documentation gap, separate from the VM drift
itself — the Pilots category was never added to that inventory when it shipped).

**This PR widens the gap further** by adding 6 new Prompt Registry tools
(`get_registry_prompt_status`, `get_registry_prompt`, `diff_registry_prompt`,
`pin_registry_prompt`, `rollback_registry_prompt`, `sync_prompt_registry`) and
fixing the `read_platform_logs` log-path bug — none of which will reach the
`investyo` (VM) connection until it is redeployed.

## Remediation — operator action required

**Not run by this PR.** Restarting a service on a production VM is a live
deploy action, not something to execute autonomously from a docs-only change.
The operator should run (adjust the branch/ref if deploying something other
than `main`):

```bash
gcloud compute ssh investyo-vm \
  --zone=us-east4-c --project=stock-data-engine --quiet \
  --command "cd /opt/investyo && \
    sudo -u investyo git pull origin main && \
    sudo -u investyo /opt/investyo/.venv/bin/pip install -r requirements.txt -q && \
    sudo systemctl restart investyo-mcp"
```

This mirrors `deploy/investyo-mcp.service`'s `ExecStart`
(`/opt/investyo/.venv/bin/python investyo_mcp_server.py --transport sse --port 8080`,
run as the `investyo` service user out of `/opt/investyo`) and
`deploy/setup_gcp_vm.sh`'s existing user/venv conventions — it does not
introduce a new deploy path, just runs the update the bootstrap script never
automated.

**Verify afterward:**
```bash
gcloud compute ssh investyo-vm --zone=us-east4-c --project=stock-data-engine \
  --command "systemctl status investyo-mcp --no-pager"
```
and, from a client connected via the `investyo` registration, confirm one of
the previously-missing tools (e.g. `list_pilots`) now responds instead of
"tool not found."

## Secondary finding: fragile client wiring in `claude_desktop_config.json`

`~/Library/Application Support/Claude/claude_desktop_config.json` inlines its
own raw `gcloud compute ssh ... --command "sudo -u investyo bash -c '...'"`
array directly inside `mcpServers.investyo`, rather than invoking the
regression-tested `mcp_remote_adapter.py` stdio proxy this repo already ships
specifically to handle two documented connection traps (see
`docs/architecture/observability-and-apis.md`'s `investyo_mcp_server.py`
entry and `tests/test_mcp_remote_adapter.py`):

1. GUI-launched MCP clients spawn with a minimal `PATH` that excludes
   Homebrew, so a bare `"gcloud"` lookup can fail silently — `mcp_remote_adapter.py`'s
   `_resolve_gcloud()` resolves an absolute path (`GCLOUD_BIN` env override →
   `shutil.which` → known Homebrew install paths → bare `"gcloud"` last resort).
2. The `--command` string must `cd /opt/investyo` **before** `sudo -u investyo`,
   since `sudo` doesn't change the working directory and `gcloud compute ssh`'s
   default remote cwd (the SSH login user's home, commonly mode `750`) isn't
   even traversable by the `investyo` service user — omitting the `cd` crashes
   pydantic-settings on startup trying to `stat()` a `.env` relative to an
   inaccessible directory.

The inlined command in `claude_desktop_config.json` **does** include the `cd
/opt/investyo` fix by hand, so it isn't currently broken by trap #2, and its
`command` field is already the absolute Homebrew path (`/opt/homebrew/bin/gcloud`),
so trap #1 doesn't bite here either. But it duplicates logic that already lives
in — and is tested against regressions in — `mcp_remote_adapter.py`, and will
silently drift if that adapter is ever changed again (e.g. a future trap fixed
there won't propagate to this hand-rolled config unless someone remembers to
port it by hand a second time).

Worth switching this config entry to invoke `mcp_remote_adapter.py` directly
(`python3 /Users/kevinlee/Desktop/Stockpy/mcp_remote_adapter.py`), matching the
pattern `investyo-platform`'s own Claude Code registration already uses for the
local server. **Not fixed here** — editing a live Claude Desktop config is
outside a code PR's blast radius and deserves its own explicit go-ahead from
the operator, not a silent side effect of an unrelated MCP-tools PR.
