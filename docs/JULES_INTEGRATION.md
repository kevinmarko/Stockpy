# Jules Coding-Agent Integration

**Source:** `data/jules_client.py`
**Consumers:** `investyo_mcp_server.py`'s `list_jules_sources`/`dispatch_jules_task` MCP tools, `scripts/jules_dispatch.py`'s `list-sources`/`create-session` CLI subcommands.

**Status as of this rewrite: the write/PR-creation path is permanently disabled, and no working
capability exists in this repo for Jules's real function (auditing/reviewing an existing PR or
codebase).** This document is a corrected reference — it replaces an earlier version built around
a capability Jules does not actually have. Read §1 first.

---

## 1. What Jules actually does — corrected

[Jules](https://jules.google.com) is Google's third-party coding agent. Its real, confirmed
capability is **auditing and reviewing an existing PR or an existing codebase** — Jules can look at
code that already exists and report on it.

**Jules cannot write new code from a prompt alone, and it cannot open a new pull request "from
nothing."** It has no capability to take a bare instruction ("add feature X") and generate a PR
implementing it. Any earlier description of this integration (or of Jules generally) claiming
otherwise — that "given a prompt and a connected GitHub repo/branch, Jules writes code and opens a
real, unsupervised PR" — was incorrect. That was this integration's original design assumption,
not a real Jules capability, and it has been corrected.

**Consequence for this codebase:** `data/jules_client.py::dispatch_session()` — the function that
built and sent the write/PR-creation request — now unconditionally raises a new exception,
`JulesCapabilityNotAvailable`, as the very first thing it does. No network call is ever made. This
is a permanent disablement, not a temporary outage: the capability it targeted (autonomous
code-writing + PR creation from a prompt) does not exist on Jules's side to call in the first
place.

**What is NOT built:** a real dispatch path for Jules's actual capability (having Jules audit or
review an existing PR or codebase) does not exist anywhere in this repo as of this rewrite. Building
one would be new work — new client code, a new MCP tool, new tests — none of which this pass
attempts. Be clear about this gap rather than implying a working audit/review mechanism is already
wired up: it is not.

**What remains genuinely functional today:** `list_sources()` (`GET /sources`) and its
`format_sources()` formatter, exposed via the `list_jules_sources` MCP tool and the
`scripts/jules_dispatch.py list-sources` CLI subcommand. This is a plain read — it enumerates which
GitHub repos the operator has already connected to their Jules account through Jules's own UI. It
makes no code-writing or review claim and is unaffected by the correction above.

---

## 2. Account / auth consequences

- **Auth**: `list_sources()` still carries an `X-Goog-Api-Key` header built from
  `settings.JULES_API_KEY`. Get a key from https://jules.google.com if you want to use
  `list_jules_sources`/`list-sources`.
- **Pricing/quota — an honest disclosed unknown.** Jules's pricing and quota model has **not been
  independently verified** as part of this integration. This is stated plainly rather than guessed
  at, matching this repo's convention elsewhere (see `docs/FMP_INTEGRATION.md`'s §6 "Cannot be
  verified in this sandbox") of flagging an unverified externality instead of presenting it as
  confirmed.
- **Source connection is out of scope for this integration.** `list_sources()` only ever returns
  repos the operator has already connected to their Jules account through Jules's own UI/setup
  flow. Stockpy's client calls the API once a source is already connected — it does not, and
  cannot, connect a new source itself.

---

## 3. Settings reference

All three settings still live in `settings.py` under the
`# --- Jules coding-agent API (data/jules_client.py) ---` block. They now only govern the
read-only `list_sources()` path — `JULES_ENABLED`/`JULES_API_KEY` gate whether that call is even
attempted, and `dispatch_session()` is unreachable regardless of how these are set.

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `JULES_API_KEY` | `Optional[str]` | `None` | Jules API key (from https://jules.google.com). Secret — masked in the GUI, never GUI-writable. Absent → `list_sources()` short-circuits with zero network cost and `list_jules_sources`/`list-sources` degrade to a clear "not configured" message rather than crashing. |
| `JULES_ENABLED` | `bool` | `False` | Master switch gating `list_sources()` and the two `list_jules_sources`/`list-sources` entry points. Still a `settings_keysets.DANGEROUS_KEYS` member (typed confirmation required to flip it through a settings editor) — a conservative holdover from when this flag also gated the write path; there is no live-write risk left to guard against today, but the flag has not been reclassified. |
| `JULES_REQUEST_TIMEOUT_SECONDS` | `int` | `30` | HTTP timeout (seconds) applied to `data/jules_client.py`'s `list_sources()` request. `dispatch_session()` never reaches the point of making an HTTP request, so this setting no longer affects it. |

---

## 4. The disabled write/dispatch path (historical — do not follow)

Everything in this section describes this integration's **original, incorrect design**. It is kept
here only so the history is legible, not as instructions to follow. None of it is functional.

The original design hardcoded Jules's `automationMode` to `AUTO_CREATE_PR` — the assumption was
that, given a prompt and a connected GitHub repo/branch, Jules would write code and open a real
pull request once it finished. A `confirm=True`/`--confirm` gate (mirroring
`gui/orchestrator_runner.py`'s `HIGH_STAKES_COMMANDS` pattern) and an append-only dispatch ledger
(`output/jules_dispatched.jsonl`, same-day dedup by `{UTC date}:{hash of source|branch|title|prompt}`)
were both built to guard that dispatch. Both pieces of scaffolding (the ledger file format, the
`confirm=True` parameter, the CLI's `--confirm` flag) still exist in the code, but
`dispatch_session()` now raises `JulesCapabilityNotAvailable` before any of it is reached — the
ledger is never written to, and no PR is ever created through this path. This is permanent: the
capability the design assumed Jules had does not exist to build a working version of.

If you see any earlier documentation, commit message, or comment claiming
`automationMode: "AUTO_CREATE_PR"` results in Jules opening a real PR today, treat it as describing
this retired design, not current behavior.

---

## 5. What you can actually do with this integration today

1. Get a real Jules API key from https://jules.google.com and connect a target GitHub repo through
   Jules's own UI, if you want to exercise the read path.
2. Add `JULES_API_KEY=<key>` to your local `.env`. Never commit it — `.env` is gitignored, and this
   repo's `.claude/hooks/block_env_write.sh` hook hard-denies any tool from writing `.env` directly,
   by design.
3. Set `JULES_ENABLED=true` in `.env`. This is a `settings_keysets.DANGEROUS_KEYS` member — expect a
   typed-confirmation prompt if you flip it through a settings UI rather than hand-editing `.env`.
4. Run `python scripts/jules_dispatch.py list-sources` (or call the `list_jules_sources` MCP tool)
   to confirm your connected repo shows up. **This is the full extent of what this integration can
   do today.** There is no next step that writes code, opens a PR, or triggers any kind of review —
   calling `dispatch_jules_task`/`create-session` will raise `JulesCapabilityNotAvailable`
   immediately.

---

## 6. Known risks / verification limitations

The surviving read path (`list_sources()`) was built and verified **entirely offline**, in a
sandboxed dev/CI environment with no real Jules API key and no live network access. Its test
coverage (`tests/test_jules_client.py`, `tests/test_investyo_mcp_server.py`,
`tests/test_jules_dispatch.py`) is mock-verified only; no live Jules API call has been made, or is
possible, in that environment.

There is no operator action that "finishes" this integration the way the old §5 rollout used to
describe — the honest next step, if this capability is wanted, is building a genuine audit/review
dispatch path against Jules's real API, which is new work this pass does not attempt.
