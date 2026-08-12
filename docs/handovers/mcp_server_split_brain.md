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
| `investyo-platform` | `~/.claude.json` (Claude Code project config, under both the `/Users/kevinlee/Stockpy-live` and `/Users/kevinlee` project entries) | stdio, direct | `/Users/kevinlee/Stockpy-live/.venv/bin/python3 /Users/kevinlee/Stockpy-live/investyo_mcp_server.py` — whatever is checked out **locally**, currently `origin/main` tip |
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
(`python3 /Users/kevinlee/Stockpy-live/mcp_remote_adapter.py`), matching the
pattern `investyo-platform`'s own Claude Code registration already uses for the
local server. **Not fixed here** — editing a live Claude Desktop config is
outside a code PR's blast radius and deserves its own explicit go-ahead from
the operator, not a silent side effect of an unrelated MCP-tools PR.

## Addendum: `streamable-http` is a third, separate deployment path (2026-08)

`investyo_mcp_server.py` gained a `--transport streamable-http` option (see
`docs/architecture/observability-and-apis.md`'s widget-resources entry) to give
the new MCP Apps SDK Pilot-picker widgets a host that can actually render
them — bearer-token gated via `settings.MCP_HTTP_BEARER_TOKEN`, typically run
ad hoc from a developer's own machine behind a tunnel (e.g. for testing against
claude.ai as a custom connector), not a long-lived service. This is
**intentionally not a third registration competing with the two above**, and
redeploying it is **not** a fix for the `stdio`/`sse` drift this file
documents: it doesn't touch `~/.claude.json`, `claude_desktop_config.json`,
`investyo-vm`, or `deploy/investyo-mcp.service`, and neither existing
registration is expected to ever switch to it. Treat any `streamable-http`
instance as ephemeral, developer-machine-local tooling, separate from the two
production-ish stdio connections this document tracks.

## Addendum: `--auth-mode oauth` is an orthogonal choice within `streamable-http`, not a fourth path (2026-08)

`--transport streamable-http` gained a further `--auth-mode {bearer,oauth}`
flag (default `bearer`, preserving the behavior described above exactly).
This is **not** a fourth deployment path competing with the three already
documented in this file — it's a second axis *within* the same
`streamable-http` transport, choosing how that one transport authenticates:

- `--auth-mode bearer` (default): the existing `MCP_HTTP_BEARER_TOKEN`
  perimeter described above, unchanged.
- `--auth-mode oauth` (`settings.MCP_OAUTH_ENABLED=True`): a full OAuth 2.1
  authorization server (`mcp_oauth_provider.py`) instead of a static token.
  This exists because claude.ai's custom-connector UI has no field for a
  static bearer token — it only speaks OAuth's dynamic-client-registration +
  authorization-code flow — so a bearer-token instance cannot be added there
  as a connector at all, only an oauth-mode one can.

The same tunnel-stability reasoning already established for `streamable-http`
above applies with one extra constraint for oauth mode specifically: OAuth has
an issuer-identity concept (`MCP_OAUTH_ISSUER_URL`) that plain bearer-token
auth doesn't, so oauth mode requires a **named/stable-hostname tunnel** —
unlike bearer mode, which tolerates an ephemeral quick-tunnel URL, an
oauth-mode server's issuer URL must stay constant across restarts for
already-registered OAuth clients (and their issued tokens) to keep working.
Both sub-modes remain ephemeral, developer-machine-local tooling in the same
sense as the rest of this addendum — neither is wired into `~/.claude.json`,
`claude_desktop_config.json`, `investyo-vm`, or `deploy/investyo-mcp.service`.

## Addendum: `--auth-mode oauth` gained an opt-in multi-user login (2026-08)

`--auth-mode oauth`'s `/login` password form gained a second, opt-in mode:
`settings.MCP_OAUTH_MULTI_USER_ENABLED` (default `False`, preserving the
single-passphrase `MCP_OAUTH_PASSWORD` behavior described above exactly)
switches the form to per-user named credentials (`mcp_oauth_store.OAuthUser`,
Scrypt-hashed via `mcp_oauth_password.py`), provisioned with the new
`scripts/manage_oauth_users.py` CLI (`add`/`deactivate`/`reactivate`/
`list`/`reset-password`, password always via `getpass`, never a CLI arg).

**This is Option A, not genuine multi-tenancy** (see
`docs/plans/oauth_multi_user_plan.md`'s §0 scope resolution): every named user still
reaches the exact same single trading account, follows, paper account, and
kill switch as today. The only observable difference per user is which
`subject` (the authenticated username) lands on their issued OAuth token —
a pure identity label that nothing downstream currently reads. Per-user
lockout (`oauth_login_state`, now keyed by `username` instead of a
singleton `id=1` row) is the one genuinely new security property: one
user's mistyped password can no longer lock out every other user, and an
attacker no longer gets one shared budget of `LOGIN_LOCKOUT_THRESHOLD`
guesses across every account. The legacy single-password path is
unaffected either way — it addresses its own reserved sentinel row
(`mcp_oauth_store.LEGACY_SINGLE_PASSWORD_USERNAME`) under the hood, so a
pre-existing deployment's lockout state survives the upgrade via an
additive migration rather than being reset or dropped.

Same ephemeral, developer-machine-local framing as the rest of this
addendum applies — multi-user mode changes who can pass the `/login` gate
on one already-ephemeral instance, not the instance's own deployment
model.

## Addendum: docs are now served over MCP too, and inherit this exact risk — mitigated with a staleness-visibility signal, not autonomous redeploy (2026-08)

`investyo_mcp_server.py` gained `investyo://docs/index` (serves
`docs/README.md`, the master index of this repo's documentation library) and
a `get_doc(path)` tool (reads any single file under `docs/` or
`CLAUDE.md`/`AGENTS.md` by repo-relative path — see
`docs/architecture/observability-and-apis.md`'s `investyo_mcp_server.py`
entry for the full "why a tool, not a resource template" reasoning). The
motivating use case is the same one the rest of this file already
establishes: a client connected via `investyo` (the GCP VM) or a
`streamable-http` instance has **no filesystem access to this repo at all**
— it can query the platform's data/tools but previously had no way to read
CLAUDE.md's conventions or a signal's `docs/signals/<name>.md` writeup.

**This inherits the exact split-brain risk this file already documents, not
a new one.** The VM's `investyo_mcp_server.py` — and therefore its
`investyo://docs/index`/`get_doc` responses — is only as fresh as the last
manual `git pull` + service restart an operator ran there. A client on that
connection could easily read a stale `docs/README.md` or a stale
`CLAUDE.md` section with zero indication anything was wrong, which is worse
for docs than for tools: a missing tool at least fails loudly ("tool not
found"); stale prose fails silently and can actively mislead an agent
working through that connection.

**The fix applied here is visibility, not automation — consistent with this
file's existing stance** ("Restarting a service on a production VM is a
live deploy action, not something to execute autonomously from a docs-only
change," above). Nothing in this change SSHes into `investyo-vm`, pulls
code, or restarts `investyo-mcp.service` — that remains the same manual
operator action documented in "Remediation" above, now additionally
covering docs. Instead, every response from `investyo://docs/index` and
`get_doc` is prefixed with `_repo_commit_info()`: the short git commit SHA
+ ISO commit date of whichever checkout is actually serving that response
(`unknown (not a git checkout, or git unavailable)` if `git` itself isn't
available — never fabricated, per CONSTRAINT #4). A client — or an operator
eyeballing a response — can now directly compare that SHA against
`origin/main`'s tip and know immediately whether the connection it's
reading docs through is stale, instead of trusting silently.

**Practical implication for the "Confirmed drift" table above:** the next
time someone audits tool-surface drift between `investyo-platform` and
`investyo`, `investyo://docs/index`'s commit header is now also a
one-request staleness check for the VM connection generally — a fast
proxy for "has this VM been redeployed recently" without needing to
enumerate every tool by hand.

Tests: `tests/test_investyo_mcp_server.py`'s `TestRepoCommitInfo`,
`TestResolveDocPath`, `TestGetDocsIndex`, `TestGetDoc` classes — including
path-traversal/absolute-path/outside-allowed-root rejection tests, since
`get_doc` accepts a client-supplied path and must never become an
arbitrary-filesystem-read primitive.
