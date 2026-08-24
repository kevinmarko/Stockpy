# Implementation Plan: Prompt Registry unsigned remote-store adoption fix

## Problem

`PromptRegistry._safe_adopt()` (`prompt_registry/registry.py`) skips its
HMAC-SHA256 signature gate whenever `PROMPT_REGISTRY_SIGNING_KEY` is unset —
for every backend (`HTTPStore`/`FirestoreStore` remote fetch, or
`LocalJSONStore`), not narrowly scoped to `LocalJSONStore` offline dev as the
module docstring implied. `PROMPT_REGISTRY_BACKEND` defaults to `"http"` the
moment `PROMPT_REGISTRY_ENABLED=True`, with no warning anywhere if the
operator forgets to also set a signing key. Confirmed exploitable: a
tampered `PromptRecord` (real body + an injected instruction worded to dodge
the guardrails deny-list) with no signing key was silently adopted and
reached every live LLM call site.

## Approach

Three layers, matching this codebase's CONSTRAINT #6 (fail closed) and "own
risk class, dedicated gate" conventions:

1. **`PromptRegistry.__init__`** — raise `ValueError` when `store` is an
   `HTTPStore`/`FirestoreStore` instance and `signing_key is None`. This is
   the hard, code-level restriction closing the gap at its root — no future
   call site can construct this combination. `LocalJSONStore`/`None` are
   exempt (genuinely local, no network fetch).
2. **`_build_registry_from_settings()`** — check for the same misconfiguration
   *before* dispatching to `HTTPStore`/`FirestoreStore` construction (so the
   new constructor `ValueError` never propagates and crashes `get_registry()`
   for every caller). Log CRITICAL, fire an `observability.alerts.send_alert`
   alert, and fall back to a cache/baseline-only registry — mirroring the
   function's existing "URL not set" degrade pattern.
3. **`scripts/preflight_check.py::check_prompt_registry_signing_key_configured`**
   — new blocking (not warning-only) go-live check catching the
   misconfiguration before deployment, registered in `ALL_CHECKS`. Not
   `ADVISORY_ONLY`-auto-skipped — the LLM call sites this protects run
   regardless of trading mode.

Also: correct the module docstring's misleading "appropriate for
LocalJSONStore offline dev use" framing to state the restriction is now
enforced in code; fix the unrelated stale comment in
`investyo_mcp_server.py:590` (claims the GUI panel reads the "OLDEST" cached
entry — it reads the newest, `versions[0]`, and has for a while).

## Files touched

- `prompt_registry/registry.py` — constructor guard, factory refusal,
  docstring corrections.
- `scripts/preflight_check.py` — new check function + `ALL_CHECKS` entry +
  docstring trailer entry.
- `investyo_mcp_server.py` — stale comment fix (cosmetic only).
- `tests/test_prompt_registry_resolution.py` — `TestUnsignedRemoteStoreConstructionRefused`,
  `TestUnsignedRemoteBackendRefusedByFactory` (incl. an end-to-end re-run of
  the original tamper scenario proving the gap is closed).
- `tests/test_preflight.py` — `_settings()` helper gains the three new
  `PROMPT_REGISTRY_*` mock defaults; `TestPromptRegistrySigningKeyConfigured`.
- `docs/known_issues/prompt_registry_unsigned_remote_adoption.md` — full
  incident write-up (new file).
- `docs/known_issues/README.md` — index row.
- `docs/settings_field_census.md` / `.json`, `docs/settings_liveness.json` —
  regenerated (`scripts/measure_settings_census.py --write`,
  `scripts/settings_liveness.py --write`) to reflect the new
  `settings.PROMPT_REGISTRY_*` read sites this change introduces; required
  for `tests/test_measure_settings_census.py`/`test_settings_liveness.py`'s
  freshness gates to stay green.

## Documentation-update step (CLAUDE.md requirement)

- New `docs/known_issues/prompt_registry_unsigned_remote_adoption.md` +
  `docs/known_issues/README.md` index row (done, see above).
- No `docs/architecture/*.md` or `docs/signals/*.md` touch needed — this is
  a security-gate fix inside an already-documented subsystem
  (`docs/plans/PROMPT_REGISTRY_PLAN.md` §4.2 already states signature
  verification is "mandatory"; this fix makes the code actually honor that
  for remote backends). Not amending the plan doc itself — it already states
  the correct intent; the code was the thing out of sync with it.

## Verification plan

- `pytest tests/test_prompt_registry_resolution.py tests/test_prompt_registry_cli.py
  tests/test_prompt_registry_store.py tests/test_prompt_registry_signing.py
  tests/test_prompt_registry_guardrails.py tests/test_preflight.py
  tests/test_preflight_runner.py` — must be 100% green.
- `pytest tests/test_investyo_mcp_server.py -k "prompt or registry"` — must
  be 100% green (cosmetic comment fix only, but confirms nothing broke).
- `python -m ruff check . --select=F821,F822,F823,E9` — genuine-bug gate,
  must be clean.
- `pytest tests/test_measure_settings_census.py::TestCommittedArtifactIsFresh
  tests/test_settings_liveness.py::TestCommittedArtifactIsFresh` — must be
  green after regenerating the two census artifacts.
- Manual: construct a `PromptRegistry(store=HTTPStore(...), signing_key=None)`
  and confirm `ValueError`; construct via `_build_registry_from_settings()`
  with the misconfiguration and confirm no exception + `_store is None` +
  CRITICAL alert fired; confirm the Gravity self-check
  (`Gravity AI Review Suite.py:11017`, `PromptRegistry(store=None, ...)`)
  still passes unaffected.
