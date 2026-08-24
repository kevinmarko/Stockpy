# Walkthrough: Prompt Registry unsigned remote-store adoption fix

## The gap

`prompt_registry/registry.py::PromptRegistry._safe_adopt()`'s Gate 1
(HMAC-SHA256 signature verification) is skipped whenever
`self._signing_key is None`:

```python
if self._signing_key is not None:
    if not verify(record.body, record.signature, self._signing_key):
        ...
```

`PROMPT_REGISTRY_SIGNING_KEY` defaults to `None` (`settings.py`). The
module's own docstring called this "appropriate for `LocalJSONStore`
offline dev use," but the code never actually scoped the skip to that one
backend — an `HTTPStore` (remote HTTPS fetch) or `FirestoreStore` backend
ran exactly as unsigned the moment no key was configured, regardless of
`PROMPT_REGISTRY_BACKEND`.

`PROMPT_REGISTRY_ENABLED` defaults `False`, so a fresh install is safe. But
`PROMPT_REGISTRY_BACKEND` defaults to `"http"` — the moment an operator
enables the feature to try it, the remote path is what's live unless they
separately remember to set a key, and nothing (`_build_registry_from_settings()`,
`scripts/preflight_check.py`) warned or blocked that misconfiguration.

A body adopted through this path reaches real production LLM call sites
unmodified: `llm/chart_insight.py`, `llm/commentary.py`, `llm/research.py`,
`engine/portfolio_context.py`, `engine/gravity_ai_runner.py`.

## The fix, three layers

**1. Construction-time guard (`PromptRegistry.__init__`)** — the actual
fix. Refuses to construct at all:

```python
if signing_key is None and isinstance(store, (HTTPStore, FirestoreStore)):
    raise ValueError(...)
```

This closes the gap structurally: it is now impossible to hold a live
`PromptRegistry` object backed by a remote store with no signing key,
regardless of how it was constructed — today's one factory
(`_build_registry_from_settings`) or any future call site.

**2. Factory refusal (`_build_registry_from_settings`)** — since the
factory is `get_registry()`'s only production caller and letting the new
`ValueError` propagate there would crash every consumer of the singleton
(CLI, GUI, MCP server, orchestrators), the factory checks the same
condition *before* calling `HTTPStore(...)`/`FirestoreStore(...)`, logs
CRITICAL, fires an `observability.alerts.send_alert("CRITICAL", ...)` alert,
and falls back to a cache/baseline-only registry (`store=None`) — mirroring
the function's pre-existing "`PROMPT_REGISTRY_URL` not set" WARNING-level
degrade, just louder given the severity.

**3. Preflight gate (`scripts/preflight_check.py`)** — a new, blocking
`check_prompt_registry_signing_key_configured` check so the misconfiguration
is caught before go-live, not only discovered via a CRITICAL log line
after the fact. Deliberately **not** `ADVISORY_ONLY`-auto-skipped, since the
LLM call sites this protects run in every deployment shape — unlike the
broker-dependent checks that skip in advisory mode.

## Why this scoping (not a full "always require a key" rule)

`LocalJSONStore` reads a local file — no network fetch, nothing in transit
for an attacker to tamper with. The plan doc
(`docs/plans/PROMPT_REGISTRY_PLAN.md` §4.2) already frames signature
verification as "mandatory" without carving out an exception, but the
task's own guidance was explicit about preserving the local-dev
convenience if genuinely defensible — and it is, for exactly this
network-boundary reason. The fix therefore targets `isinstance(store,
(HTTPStore, FirestoreStore))` specifically, not "any store."

## What proves the fix actually closes the gap

`test_tampered_content_scenario_closed_end_to_end`
(`tests/test_prompt_registry_resolution.py`) re-runs the original reported
scenario: `PROMPT_REGISTRY_ENABLED=True`, `PROMPT_REGISTRY_BACKEND="http"`,
a URL pointing at an attacker-controlled endpoint, no signing key. Before
the fix, this configuration would fetch and (subject only to the guardrails
deny-list) adopt whatever that endpoint served. After the fix:

```python
reg = get_registry()
assert reg._store is None       # nothing to fetch from
assert reg.sync() is False      # no store to sync from
result = reg.get(_KNOWN_ID)
assert result == read_baseline(_KNOWN_ID)  # always the committed baseline
```

The remote store is never constructed, so no HTTP request is ever made —
not "the tampered content would be rejected on arrival," but "there is no
arrival."

## Verification performed

- 441 tests across the full `prompt_registry`/`preflight` test surface,
  plus 31 `investyo_mcp_server.py` prompt-registry tests — all pass.
- `python -m ruff check . --select=F821,F822,F823,E9` (this repo's
  genuine-bug lint gate) — clean.
- `docs/settings_field_census.md`/`.json` and `docs/settings_liveness.json`
  regenerated — the new code paths read `settings.PROMPT_REGISTRY_ENABLED`
  / `_BACKEND` / `_SIGNING_KEY` a few more times each (in the new preflight
  check and its test mocks), which is exactly the kind of drift those
  committed artifacts are designed to catch; regenerating them is the
  documented, expected fix.
- `Gravity AI Review Suite.py`'s own self-check
  (`PromptRegistry(store=None, cache=None, enabled=False)`, CONSTRAINT #6
  parity against the baseline) re-run manually — still passes, confirming
  the guard doesn't affect the `store=None` disabled-registry path it
  relies on.

## Incident, disclosed

Mid-session, a `git stash`/`git stash pop` pair (used to diff against a
clean baseline) collided with another concurrent session sharing this
worktree — the pop applied a different session's stash entry, and this
session's five edited files reverted to `origin/main` on disk. All edits
were redone from the diffs already present in this session's context; nothing
was lost from git history, and the other session's own in-progress,
unrelated changes (Robinhood snapshot fix files) were left untouched
throughout. `git stash` was avoided for the remainder of the session.
