# Jules Coding-Agent Integration

**Source:** `data/jules_client.py`
**Consumers:** `investyo_mcp_server.py`'s `list_jules_sources`/`dispatch_jules_task` MCP tools, `scripts/jules_dispatch.py`'s `list-sources`/`create-session` CLI subcommands.
**Precedent this document scales down from:** `docs/FMP_INTEGRATION.md` — Jules is one capability with 3 settings, not FMP's 8+ feeds with 28+ settings, so this document is proportionally shorter, but follows the same shape and the same discipline about stating what has and hasn't actually been verified.

Every setting in this document defaults to today's exact pre-Jules behavior. Nothing here is active until an operator explicitly sets `JULES_ENABLED=true` in `.env` — this document exists so that flip is an informed one.

---

## 1. What Jules is used for

[Jules](https://jules.google.com) is Google's third-party autonomous coding agent. Given a prompt and a GitHub repo/branch already connected to the operator's Jules account, Jules writes code changes and — in the mode this integration hardcodes, `AUTO_CREATE_PR` — opens a real pull request against that repo once it finishes. There is no lower-stakes mode: Jules's `automationMode` enum has exactly two values (`AUTOMATION_MODE_UNSPECIFIED`, meaning no automation at all, and `AUTO_CREATE_PR`), and `data/jules_client.py` hardcodes the latter rather than exposing it as a parameter, precisely so the meaning of this integration's `confirm=True` gate (§4) can never drift out of sync with whether a call actually results in a PR.

This repo wires Jules in as a **second mechanism for delegating fix-out/improvement work**, alongside the existing pattern of dispatching multiple parallel Claude Code subagents within one session. The two are not equivalent: a Claude Code subagent works inside the current session under the operator's live supervision; a dispatched Jules session runs unsupervised, outside this session entirely, on Google's infrastructure, against the operator's real GitHub repo. There is **no human review gate before the PR itself is created** — review happens at merge time, exactly like any other PR, but nothing gates Jules from opening that PR in the first place once a session is dispatched.

---

## 2. Account / auth consequences

- **Auth**: every request carries an `X-Goog-Api-Key` header built from `settings.JULES_API_KEY`. Get a key from https://jules.google.com.
- **Pricing/quota — an honest disclosed unknown.** Jules's pricing and quota model has **not been independently verified** as part of this integration. Check Google's current Jules terms/pricing before enabling `JULES_ENABLED=true` for real, ongoing use. This is stated plainly rather than guessed at, matching this repo's convention elsewhere (see `docs/FMP_INTEGRATION.md`'s §6 "Cannot be verified in this sandbox") of flagging an unverified externality instead of presenting it as confirmed.
- **Source connection is out of scope for this integration.** `list_sources()` (`GET /sources`) only ever returns repos the operator has already connected to their Jules account through Jules's own UI/setup flow. Stockpy's client calls the API once a source is already connected — it does not, and cannot, connect a new source itself.

---

## 3. Settings reference

All three settings live in `settings.py` under the `# --- Jules coding-agent API (data/jules_client.py) ---` block. Purpose column summarizes each field's real `description=` in `settings.py` — see that file for the verbatim text.

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `JULES_API_KEY` | `Optional[str]` | `None` | Jules API key (from https://jules.google.com). Secret — masked in the GUI, never GUI-writable. Absent → every request short-circuits with zero network cost and both MCP tools degrade to a clear "not configured" message rather than crashing. Setting this alone changes nothing; `JULES_ENABLED` must also be explicitly turned on. |
| `JULES_ENABLED` | `bool` | `False` | Master switch for the whole integration (`data/jules_client.py`, the two MCP tools, the CLI script). Deliberately **not** covered by the 2026-08-07 "new admin/write capabilities default to True" policy that applies to this platform's own internally-token-gated capabilities — Jules is a third-party agent that opens real PRs on the operator's actual GitHub repo, with no internal Stockpy trust boundary standing between "flag on" and "PR created" beyond each individual dispatch call's own `confirm=True` argument. Also a `settings_keysets.DANGEROUS_KEYS` member: flipping it through any settings editor requires typed confirmation, on top of the per-call confirm gate. |
| `JULES_REQUEST_TIMEOUT_SECONDS` | `int` | `30` | HTTP timeout (seconds) applied to every `data/jules_client.py` request (`list_sources`, `dispatch_session`). |

---

## 4. The confirm=True + dispatch-ledger safety model

Two independent mechanisms guard every dispatch:

**Explicit per-call confirmation.** Every `dispatch_jules_task`/`create-session` invocation requires an explicit `confirm=True` (MCP tool) or `--confirm` (CLI) — omitting it dispatches nothing. This mirrors `gui/orchestrator_runner.py`'s `HIGH_STAKES_COMMANDS` gate that this codebase already uses for other irreversible actions (the global kill-switch toggle, a forced Robinhood re-login).

**State this explicitly and honestly, do not paper over it: `confirm=True` here is a materially weaker gate than this repo's other precedent for an irreversible, unsupervised action.** The `robinhood-execution` skill's order-placement loop requires a live, per-order, human-narrated, explicit-affirmative back-and-forth before each individual order — a human is in the loop for every single irreversible action, in real time. `confirm=True` on a Jules dispatch is a bare boolean a single tool call or CLI invocation sets in one shot; nothing in the code enforces that a human actually reviewed *this specific prompt* before it was set. What currently closes that gap is the `.claude/skills/jules-delegation/SKILL.md` skill's prose hard-stop — "never pass `confirm=True` without the operator's explicit per-task go-ahead for THIS exact prompt" — and that is enforced by an agent reading and following the skill, not by any code-level check. A future hardening could add a stronger code-level gate (e.g. a second confirmation token, a human-readable prompt echo the operator must retype); this integration does not attempt one, and that is a known, accepted limitation rather than an oversight.

**The dispatch ledger.** `output/jules_dispatched.jsonl` is an append-only JSONL file, one record per successful dispatch, mirroring `execution/receipts_store.py`'s append-only pattern in spirit (this integration has none of that module's multi-file broker-reconciliation machinery — there's nothing here to reconcile against a broker). It serves two purposes: preventing an accidental retry from firing a duplicate autonomous session against the same target on the same day (the `dedup_key` is `{UTC date}:{hash of source|branch|title|prompt}` — a different day's identical prompt is a legitimate new dispatch, not a duplicate), and giving a durable audit trail of what was actually dispatched and when. `--force`/`force=True` overrides the same-day duplicate check for a genuine intentional re-dispatch.

---

## 5. Rollout sequencing

1. Get a real Jules API key from https://jules.google.com and connect the target GitHub repo through Jules's own UI.
2. Add `JULES_API_KEY=<key>` to your local `.env`. Never commit it — `.env` is gitignored, and this repo's `.claude/hooks/block_env_write.sh` hook hard-denies any tool from writing `.env` directly, by design.
3. Set `JULES_ENABLED=true` in `.env`. This is a `settings_keysets.DANGEROUS_KEYS` member — expect a typed-confirmation prompt if you flip it through a settings UI rather than hand-editing `.env`.
4. Run `python scripts/jules_dispatch.py list-sources` (or call the `list_jules_sources` MCP tool) to confirm your connected repo actually shows up.
5. Dispatch one low-stakes test prompt and review the resulting PR like any other PR. Jules gets PR-creation rights only, never merge rights — nothing in this integration auto-merges anything.

---

## 6. Known risks / verification limitations

This integration was built and verified **entirely offline**, in a sandboxed dev/CI environment with no real Jules API key and no live network access. Every test is mock-verified only (`tests/test_jules_client.py`, `tests/test_investyo_mcp_server.py`, `tests/test_jules_dispatch.py`). No live Jules API call has been made, or is possible, in that environment — this mirrors `docs/FMP_INTEGRATION.md`'s own equivalent disclosed limitation (see that document's §6, "Cannot be verified in this sandbox").

The operator should perform one real end-to-end dispatch (step 5 above) before relying on this integration for real work.
