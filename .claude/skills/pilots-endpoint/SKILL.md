---
name: pilots-endpoint
description: >-
  Add a new endpoint to api/pilots_api.py (the Pilots PWA's FastAPI backend).
  Use when asked to add, wire up, or expose a new GET/POST/PUT endpoint on the
  Pilots API -- covers picking the right auth tier (fail-open read vs.
  fail-closed command vs. +dedicated-flag write), whether a pilots/*.py read
  helper is required to stay off the AST-guarded heavy-engine import path, and
  the write-endpoint honesty requirements (atomic multi-key writes, echoing
  the request body not the stale settings singleton, and the
  "applies: next_daemon_restart" contract).
---

# Adding an endpoint to `api/pilots_api.py`

Every endpoint on this API re-derives the same three decisions. This skill
encodes the taxonomy already established across `/automation/*`,
`/brokerage/*`, and `/strategy/*` so it doesn't get re-litigated each time.

## 1. Pick the auth tier

| Tier | Guard(s) | Use for |
|---|---|---|
| Fail-open read | `require_read_token` alone | Any `GET` serving already-persisted, non-sensitive state. Unset `STATE_API_TOKEN` = unauthenticated (documented, zero-config local use). |
| Fail-closed command | `require_command_token` alone | A write whose worst case is *safe to trigger* — e.g. pausing something (`POST /automation/pause`), or an order-queue write already covered by other real safety rails (`POST /pilots/{id}/follow` under `FOLLOW_API_TOKEN` alone — the queue is still gated by `PreTradeRiskGate`/`GlobalKillSwitch`/dry-run downstream). |
| Fail-closed command + dedicated master flag | `require_command_token` **+** a new `require_<x>_enabled` dependency reading a new `settings.<X>_ENABLED` (default `False`, deliberately absent from `gui/env_io.py`'s `ALLOWED_KEYS` — hand-set in `.env` only) | A write with real persistence/rollback cost, or that changes *what the platform recommends/does* — an `.env` edit, re-enabling live order submission, editing signal weights. **Give it its own flag; do not reuse an existing one for an unrelated risk class** (`STRATEGY_WRITES_ENABLED` exists specifically because signal tuning must not ride in on `AUTOMATION_WRITES_ENABLED`, which was scoped to the daemon interval and kill-switch resume). |

Read the existing `require_brokerage_connect_enabled` / `require_automation_writes_enabled` / `require_strategy_writes_enabled` docstrings in `api/pilots_api.py` before adding a fourth — the reasoning for each is spelled out there and should be mirrored, not reinvented.

## 2. Decide if you need a `pilots/*.py` read helper

`api/pilots_api.py` is AST-guarded (`tests/test_pilots_api.py::test_pilots_api_never_imports_heavy_engines`) against directly importing `processing_engine`, `strategy_engine`, `forecasting_engine`, `macro_engine`, `technical_options_engine`, `main_orchestrator`, or `desktop`. The guard is a **denylist, first-segment-only, non-transitive** — it does NOT catch `import signals` (which itself imports no forbidden engine today, but eagerly imports all ~17 signal modules and adds ~700 modules to `sys.modules`, defeating the guard's *intent* while passing its letter).

If the data you need requires one of those heavy engines (or `signals`) to compute live: **you cannot compute it in the endpoint.** It must already be persisted by the pipeline to a JSON artifact (`output/state_snapshot.json`, `output/options_matrix.json`, etc.), and you read that artifact through a new, dependency-light `pilots/<name>.py` module — stdlib + `settings` only, following `pilots/options.py` / `pilots/run_status.py` / `pilots/strategy_matrix.py`. Never raises (missing/corrupt artifact → an honest empty shape + `reason`, not an exception — CONSTRAINT #6).

Add your new `pilots/*.py` module to the parametrized allowlist test if one exists (`tests/test_pilots_strategy_matrix.py::test_pilots_read_helpers_stay_dependency_light`) — an allowlist over the helper's own imports is a stronger guarantee than the API file's denylist, since it constrains the helper directly rather than hoping nothing routes an engine import through it later.

## 3. Response honesty (every endpoint)

- Nulls, not fabricated defaults, for anything uncomputed/unavailable (CONSTRAINT #4).
- A `reason` field when the payload can legitimately be empty (cold start, feature flag off).
- Never 500 on a missing/corrupt input file — degrade to the empty shape (CONSTRAINT #6).

## 4. Write-endpoint specifics

- **Multi-key writes that are one logical unit** (e.g. weights + a disabled-set, where a half-applied combination is worse than an all-or-nothing failure) go through `gui.env_io.write_many_atomic`, not `write_setting`/`write_many` called separately — see its docstring for why (`write_many` is intentionally left non-atomic for independent scalars).
- **Echo the request body's values in the response, not `settings`.** `settings` is a process-lifetime singleton; a `.env` write does not reach it. Echoing `settings` after a successful write returns the OLD values and looks like the write silently failed.
- **`applies` must say `"next_daemon_restart"`, never imply an immediate effect** — there is no live setter for `.env`-sourced config in this codebase yet.
- Consider whether the read-side `GET` companion needs an `env_drift` field (compares on-disk `.env` vs. the running `settings` value) so a pending, not-yet-applied write is visible rather than looking like a failure on the next read — see `GET /strategy/matrix`'s `env_drift` for the pattern.

## 5. Tests to add

- Read: 200 shape; fail-open with no token; 401 on wrong token; cold-start `reason`; never 500 on a corrupt artifact.
- Write: 403 with a valid command token but the master flag off; 403 with the flag on but the command token unset; 401 on wrong token; happy path calls the underlying writer exactly once with the full expected payload; response `applies` and body-echo correctness.
- Every validation-failure branch, with a **stable error tag** (not a bare message string — the frontend branches on the tag).
- `test_<flag>_is_not_gui_writable` — mirrors `test_automation_writes_enabled_is_not_gui_writable`: the new flag must be in NEITHER `ALLOWED_KEYS` NOR `SECRET_KEYS`.
- `test_write_endpoint_never_logs_token` (caplog) — CONSTRAINT #3.
