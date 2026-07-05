# Remote-Updatable Prompt Registry — Implementation Plan

**Status: SHIPPED.** This document is retained as the historical work order / design
record. The `prompt_registry/` package (`models.py`, `store.py`, `registry.py`,
`__main__.py`, `guardrails.py`, `signing.py`, `cache.py`, `baseline/`) exists in full,
matching the structure proposed below, and the GUI Prompts tab
(`gui/panels/prompt_registry.py`) is live. `LocalJSONStore.publish()`
(`prompt_registry/store.py:180`) is now a real read-modify-write implementation — not
read-only as originally scoped below; `HTTPStore` and `FirestoreStore` remain read-only
(`publish` raises `ReadOnlyStoreError`) as planned. Audited by Gravity
`step_73_prompt_registry_audit` (the placeholder `step_69` references below were
renumbered during implementation as other Gravity steps were added in between). See the
"Prompt Registry" section of `docs/FEATURE_TIER_HISTORY.md` for the live description.
**Authored:** 2026-06-30
**Goal.** Move every AI-facing instruction (the **master pre-prompt**, the per-stage
development prompts, and the runtime LLM prompts such as the Gravity auditor's
`SYSTEM_PROMPT` + `STEP_1..7`) out of source code and into a **versioned, remotely-updatable
Prompt Registry**. You change a prompt by publishing a new version to a protected endpoint;
the platform fetches it on next launch (or on an explicit "Sync" click), verifies its
signature, and uses it — with instant rollback by changing one pin.

**The dominating design fact.** Remotely-updatable text that an AI obeys is a
supply-chain / prompt-injection surface. The registry is therefore designed **fail-closed,
signed, audited, and bounded**: a fetched prompt can never alter a code-level safety gate
(advisory-only quarantine, risk gates, kill switch, order code). Those stay in Python.
The registry serves *instructional/narrative* text only.

**Non-goals.** No change to signal/strategy/ML math, the orchestrator pipeline, or the
advisory/broker boundary. No always-on daemon (honors CONSTRAINT #5 — fetch-on-launch +
cache, not a poller). The registry cannot lift `ADVISORY_ONLY`, cannot enable orders, and
cannot change Gravity's pass/fail *thresholds* (only the auditor's prose).

---

## 0. What is a "prompt" here — two consumer classes, one registry

| Class | Examples in this repo | Who consumes it |
|---|---|---|
| **A. Runtime LLM prompts** | `ai_verification_prompts.SYSTEM_PROMPT`, `STEP_1..7_PROMPT` (Gravity AI auditor) | Python code at runtime |
| **B. Development workflow prompts** | the 12-constraint **master pre-prompt**, per-stage task prompts ("Stage 1 — Content store", etc.) | *you*, pasted into a coding agent — retrieved via CLI / GUI, never auto-executed |

Both are stored in the same registry with the same versioning, signing, and rollback. The
difference is only the consumer: class A is fetched by code; class B is fetched by a human
(or an automation you control) via `python -m prompt_registry get master_preprompt`.

**Hard boundary (must never regress).** The registry is allowed to change *what an AI is
told*. It is **never** allowed to change *what the platform is permitted to do*. Order
submission, the advisory quarantine, the risk gate, and the kill switch are enforced in
Python and are out of the registry's reach. A fetched prompt that contains an instruction
to disable a safety control is rejected by the guardrail validator (§5.3) and the last
known-good version is used instead.

---

## 1. Architecture (`prompt_registry/` package)

Flat module package mirroring the codebase's existing `IDataProvider` / `MarketDataProvider`
ABC pattern (a swappable backend hidden behind one interface).

```
prompt_registry/
  __init__.py            # re-exports PromptRegistry, get_registry, PromptRecord
  models.py              # PromptRecord, PromptVersion, RegistryManifest (frozen dataclasses)
  store.py               # PromptStore ABC + LocalJSONStore, HTTPStore, (optional) FirestoreStore
  registry.py            # PromptRegistry — resolve/fetch/cache/verify/rollback orchestration
  signing.py             # HMAC-SHA256 sign + verify (stdlib hmac/hashlib — no new dep)
  guardrails.py          # validate_prompt() — size/required-marker/deny-list integrity gate
  cache.py               # disk cache (output/prompt_cache/) + last-known-good resolution
  baseline/              # in-repo fail-closed defaults (the current prompts, committed)
    master_preprompt.md
    gravity_system.md
    gravity_step_01.md … gravity_step_07.md
  __main__.py            # CLI (list/get/sync/pin/rollback/diff/publish/verify)
```

### Resolution order (every lookup, fail-closed)
1. **Pin** — explicit version from `settings.PROMPT_REGISTRY_PINS` (`.env`, JSON dict).
2. **Remote `latest`** — only if fetched *and* signature-valid *and* guardrail-clean.
3. **Disk cache** — last known-good signed version under `output/prompt_cache/`.
4. **Baseline** — the committed `prompt_registry/baseline/*.md` (always present).

A prompt is **never** empty and **never** fabricated (CONSTRAINT #4): if every remote/cache
path fails, the baseline that ships in the repo is used and the event is logged at WARNING.

### Fetch flow (on-demand — CONSTRAINT #5)
- `get_registry().sync()` is called **once at entry-point launch** (and on the GUI "🔄 Sync
  prompts" button), not on a timer. Optional `PROMPT_REGISTRY_REFRESH_SECONDS` (default `0`
  = launch-only) lets a long-running process refresh on an interval, but the default is
  fully on-demand, consistent with "no scheduler, no daemon".
- HTTP fetch uses a conditional GET (`If-None-Match` / ETag) so an unchanged registry costs
  one cheap 304. `urllib.request` (stdlib) — no new dependency.
- Every fetch, signature check, and version swap is appended to `logs/prompt_registry.log`
  with `{prompt_id, from_version, to_version, sha256, source}`.

---

## 2. Storage backends (`store.py`)

`PromptStore` ABC: `fetch_manifest() -> RegistryManifest`, `publish(record) -> None` (optional;
raises `ReadOnlyStoreError` when write creds absent). Three concrete stores; pick per
`PROMPT_REGISTRY_BACKEND`:

| Backend | When to use | Auth |
|---|---|---|
| **`HTTPStore`** (recommended default) | A protected HTTPS endpoint serving one signed `registry.json` (private GitHub raw + PAT, S3/GCS presigned object, or a tiny protected server) | `Authorization: Bearer <PROMPT_REGISTRY_TOKEN>` |
| **`LocalJSONStore`** | Offline / single-machine — `registry.json` on disk | filesystem perms |
| **`FirestoreStore`** (optional, lazy import) | You already use Firestore and want a console UI | service-account creds |

Recommendation: ship **`HTTPStore` + `LocalJSONStore`** first; `FirestoreStore` behind a
lazy `import` so its absence never breaks the package (CONSTRAINT #6). The ABC means Redis
or git-pull backends can be added later without touching consumers.

### `registry.json` schema (one signed manifest)
```json
{
  "registry_version": "2026-06-30T12:00:00Z",
  "signing_alg": "HMAC-SHA256",
  "prompts": {
    "master_preprompt": {
      "latest": "1.3.0",
      "versions": {
        "1.3.0": {
          "body": "You are working in the InvestYo/Stockpy repo…",
          "sha256": "…", "signature": "…",
          "created_at": "2026-06-30T11:58:00Z", "author": "kevin",
          "notes": "added constraint 13 (prompt-registry boundary)"
        },
        "1.2.0": { "...": "..." }
      }
    },
    "gravity.system":       { "latest": "1.0.0", "versions": { "...": "..." } },
    "gravity.step_01":      { "...": "..." },
    "stage.gui_help.content_store": { "...": "..." }
  }
}
```
`signature` = `HMAC-SHA256(PROMPT_REGISTRY_SIGNING_KEY, sha256(body))`, hex. The body's
`sha256` is also stored so cache integrity can be checked without the signing key.

---

## 3. Versioning & rollback

- **Semantic versioning** per prompt id (`MAJOR.MINOR.PATCH`). `latest` is a pointer, never
  a body — so promoting/rolling back is a one-line pointer move on the publisher side.
- **Instant rollback, two ways:**
  - *Operator-side, no publish needed:* set `PROMPT_REGISTRY_PINS={"master_preprompt":"1.2.0"}`
    in `.env` → next launch uses 1.2.0 regardless of remote `latest`.
  - *CLI:* `python -m prompt_registry rollback gravity.system` → repoints the local pin to the
    previous cached version and logs it.
- **Diff before adopting:** `python -m prompt_registry diff master_preprompt 1.2.0 1.3.0`
  prints a unified diff so you see exactly what an update changed.
- Cache keeps the last *N* (`PROMPT_CACHE_KEEP_VERSIONS`, default 5) signed versions per id so
  rollback works offline.

---

## 4. Security & integrity (the load-bearing section)

### 4.1 Authentication (who can read / publish)
- **Read:** bearer token (`PROMPT_REGISTRY_TOKEN`) on the HTTPS endpoint. The endpoint must
  be private (not a public gist).
- **Publish:** a *separate*, higher-privilege credential (`PROMPT_REGISTRY_PUBLISH_TOKEN`),
  only present on the machine that authors prompts. The platform's runtime never needs it.

### 4.2 Integrity (signature verification, mandatory)
- Every version carries an HMAC-SHA256 signature over its body. On fetch, `signing.verify()`
  recomputes and compares (constant-time `hmac.compare_digest`). **A bad/missing signature →
  the version is discarded and resolution falls through to cache → baseline.** A compromised
  endpoint cannot inject an unsigned instruction.
- `PROMPT_REGISTRY_SIGNING_KEY` is the shared secret (symmetric HMAC, stdlib, zero new deps).
  *Optional upgrade path:* Ed25519 asymmetric signatures (publisher holds the private key,
  the repo bakes in the public key) if you later add `cryptography` — documented, not
  required for v1.

### 4.3 Authorization boundary (guardrail validator — `guardrails.py`)
`validate_prompt(prompt_id, body) -> (ok: bool, issues: list[str])` runs on **every** prompt
before it is allowed into resolution, even a signed one:
- **Size bounds** — reject empty or > `PROMPT_MAX_CHARS` (default 50 000).
- **Required markers** — e.g. `master_preprompt` must still contain the safety acknowledgement
  line; a Gravity step must still contain its `STEP_N` marker. Missing → reject.
- **Deny-list** — reject if the body contains an instruction that attempts to disable a hard
  safety control (case-insensitive): `ADVISORY_ONLY=false`, `submit_order`, `place_order`,
  `disable the kill switch`, `ignore previous safety`, `bypass the risk gate`, etc. This is
  the structural defense that keeps a malicious or careless prompt from talking the AI past
  the code-level guards. Rejected prompts fall through to the last known-good version and log
  a CRITICAL alert via `observability.alerts.send_alert`.

### 4.4 Auditability
- `logs/prompt_registry.log` records every fetch / verify / swap / rejection.
- `python -m prompt_registry verify` re-checks signatures + guardrails of everything in cache
  and exits non-zero on any failure (CI-friendly).

### 4.5 The boundary, restated for the code reviewer
Prompts are **advisory text**. The platform's *capabilities* are fixed in Python. No registry
value is ever `eval`'d, imported, or used to gate order flow. `ai_verification_prompts` keeps
its pass/fail *logic and thresholds* in code; only the natural-language instruction text is
sourced from the registry.

---

## 5. Consumer integration

### 5.1 Gravity AI auditor (`ai_verification_prompts.py`) — first real consumer
Replace the module-level `SYSTEM_PROMPT` and `STEP_1..7_PROMPT.prompt_text` literals with
registry lookups, **falling back to the current literals as the baseline** so behavior is
byte-identical when the registry is unconfigured:
```python
from prompt_registry import get_registry
_reg = get_registry()
SYSTEM_PROMPT = _reg.get("gravity.system", default=_BASELINE_SYSTEM_PROMPT)
# step text: _reg.get(f"gravity.step_{n:02d}", default=_BASELINE_STEPS[n])
```
The committed `_BASELINE_*` constants are *exactly* today's strings, also copied into
`prompt_registry/baseline/` so cold-start with no `.env` is unchanged.

### 5.2 Development workflow prompts (class B) — retrieval only
- `python -m prompt_registry get master_preprompt` prints the current master pre-prompt to
  paste into a coding agent.
- `python -m prompt_registry get stage.gui_help.content_store --raw` prints a stage prompt.
- These are **never auto-executed**; the registry is a retrieval surface for human-driven
  workflow. (This is the safe way to "update your AI's master pre-prompt over the internet":
  you publish, then pull, then paste — a human stays in the loop.)

### 5.3 Optional future consumers
Verbose-rationale templates and news-sentiment prompts can migrate later via the same
`get(id, default=...)` call. Out of scope for v1.

---

## 6. CLI (`python -m prompt_registry`)

| Command | Effect |
|---|---|
| `list` | table of prompt ids, pinned/latest/cached versions |
| `get <id> [--version v] [--raw]` | print a prompt body (resolution-ordered) |
| `sync` | fetch remote manifest → verify → update cache (the on-launch call, exposed) |
| `pin <id> <version>` | write a pin into `.env` via `gui.env_io` (allowlisted key) |
| `rollback <id>` | repoint pin to previous cached version |
| `diff <id> <vA> <vB>` | unified diff between two versions |
| `verify` | re-check signatures + guardrails of cache; non-zero exit on failure |
| `publish <id> <file>` | (publish creds only) push a new signed version to the remote. **Shipped as read-modify-write on `LocalJSONStore`**; `HTTPStore`/`FirestoreStore` still raise `ReadOnlyStoreError` as planned. |

All commands dead-letter tolerant: a network/parse failure prints a clear message and exits
non-zero, never tracebacks.

---

## 7. GUI tab (optional, ships in a later stage)

A new "📝 Prompts" tab (`panels.render_prompt_registry`) — read-only display + version control:
- Current resolved version + source (pin / remote / cache / baseline) per prompt id.
- **🔄 Sync prompts** button (`get_registry().sync()`), a version dropdown, a unified-diff
  viewer, and a **↩ Rollback** button (writes the pin via `gui.env_io`, takes effect next
  launch — never hot-swaps a running process).
- A persistent banner: "Prompts are advisory text; safety gates are enforced in code and are
  not registry-controlled." Honors the advisory-mode tone of the rest of the GUI.

---

## 8. Settings / env vars (all secret-handled correctly)

Add to `settings.py` (secrets are `Optional[str]` Fields; pins/tunables are typed):

| Setting | Default | Secret? | Purpose |
|---|---|:--:|---|
| `PROMPT_REGISTRY_ENABLED` | `False` | no | master switch; off → baseline-only, zero network |
| `PROMPT_REGISTRY_BACKEND` | `"http"` | no | `http` / `local` / `firestore` |
| `PROMPT_REGISTRY_URL` | `None` | **yes** | protected HTTPS manifest endpoint |
| `PROMPT_REGISTRY_TOKEN` | `None` | **yes** | read bearer token |
| `PROMPT_REGISTRY_PUBLISH_TOKEN` | `None` | **yes** | publish credential (authoring machine only) |
| `PROMPT_REGISTRY_SIGNING_KEY` | `None` | **yes** | HMAC verify key |
| `PROMPT_REGISTRY_PINS` | `{}` | no | JSON `{id: version}` version pins (rollback lever) |
| `PROMPT_REGISTRY_REFRESH_SECONDS` | `0` | no | `0` = launch-only (on-demand) |
| `PROMPT_CACHE_DIR` | `output/prompt_cache` | no | signed-version cache |
| `PROMPT_CACHE_KEEP_VERSIONS` | `5` | no | offline rollback depth |
| `PROMPT_MAX_CHARS` | `50000` | no | guardrail size bound |

**Secret handling (CONSTRAINT #3):** the four secret keys above go into
`gui/env_io.SECRET_KEYS` (masked, never GUI-writable). `PROMPT_REGISTRY_PINS`,
`PROMPT_REGISTRY_ENABLED`, and `PROMPT_REGISTRY_BACKEND` go into `ALLOWED_KEYS` so the GUI
Prompts tab can flip/pin them. Mirror all of the above into `.env.example` (secrets as empty
placeholders).

---

## 9. Tests

- **`tests/test_prompt_registry_resolution.py`** — resolution order (pin > remote > cache >
  baseline); baseline always wins when everything else absent; never returns empty.
- **`tests/test_prompt_registry_signing.py`** — `sign`/`verify` round-trip; tampered body
  fails verify; `compare_digest` used (no early-exit timing leak in the obvious path).
- **`tests/test_prompt_registry_guardrails.py`** — empty / oversize / missing-marker /
  deny-list bodies are rejected; a clean body passes; rejection falls through to known-good.
- **`tests/test_prompt_registry_store.py`** — `HTTPStore` conditional GET + bearer header
  (monkeypatched `urllib`); 304 keeps cache; bad JSON → `RegistryFetchError`, never raises
  past the boundary; `LocalJSONStore` round-trip; `FirestoreStore` import-absent degrades.
- **`tests/test_prompt_registry_cli.py`** — `get`/`list`/`diff`/`verify`/`rollback` exit codes
  and output (subprocess or `main(argv)` injection); `publish` without creds → clean non-zero.
- **`tests/test_gravity_prompt_sourcing.py`** — with the registry disabled, Gravity's
  `SYSTEM_PROMPT`/`STEP_*` are byte-identical to the committed baseline (no behavior drift).

All headless, all offline (network monkeypatched). `prompt_registry/` imports zero Streamlit
so it tests cold.

---

## 10. Gravity audit (step 69)

`step_69_prompt_registry_audit` — 10 checks:
1. `prompt_registry` importable; `get_registry`, `PromptRegistry`, `PromptRecord` exist.
2. Resolution is fail-closed: with no URL/cache, `get("gravity.system")` returns the baseline,
   never `""` (CONSTRAINT #4).
3. `verify(tampered_body)` is `False`; `verify(signed_body)` is `True`.
4. Guardrail rejects an `ADVISORY_ONLY=false` body and a `submit_order` body.
5. The four `PROMPT_REGISTRY_*` secret keys are in `gui/env_io.SECRET_KEYS` and **not** in
   `ALLOWED_KEYS` (CONSTRAINT #3).
6. Disabling the registry leaves Gravity's prompts byte-identical to baseline.
7. No registry value is `eval`/`exec`/`import`-ed (source scan of `prompt_registry/` +
   `ai_verification_prompts.py`).
8. `PROMPT_REGISTRY_REFRESH_SECONDS` default is `0` (on-demand, CONSTRAINT #5).
9. CLI `verify` exits non-zero on a corrupt cache fixture.
10. `tests/test_prompt_registry_resolution.py` exists.

Written last, after the code, so it pins final wiring.

---

## 11. Sequencing for the implementing agent (each step independently mergeable)

1. **`prompt_registry/models.py` + `signing.py` + `guardrails.py`** + their unit tests.
   (Pure, headless foundation — no network.)
2. **`prompt_registry/store.py`** (ABC + `LocalJSONStore` + `HTTPStore`) + `cache.py` +
   `baseline/` (copy today's Gravity prompts verbatim) + store/resolution tests.
3. **`prompt_registry/registry.py` + `__init__.py`** (resolution orchestration, sync,
   rollback) + resolution tests.
4. **`prompt_registry/__main__.py`** CLI + CLI tests.
5. **Wire `ai_verification_prompts.py`** to source from the registry with baseline fallback +
   `tests/test_gravity_prompt_sourcing.py` (assert zero behavior drift when disabled).
6. **`settings.py` + `.env.example` + `gui/env_io.py`** secret/allowlist entries.
7. **GUI "📝 Prompts" tab** (`gui/app.py` + `panels.render_prompt_registry`) — optional, last.
8. **Docs sync + Gravity step 69** — `CLAUDE.md`, `GEMINI.md`, `HOW_TO_GUIDE.md` (new "Remote
   Prompt Updates" section), `RUNBOOK.md` (publish/rollback incident playbook), and
   `Gravity AI Review Suite.py` step 69.

---

## 12. New dependencies / env vars / secrets

- **`requirements.txt`** — none for v1 (HMAC + HTTP are stdlib). `cryptography` only if you
  later opt into Ed25519; `firebase-admin` only if you enable `FirestoreStore`. Both lazy.
- **`.env.example`** — the 11 `PROMPT_*` keys in §8 (4 secret placeholders, 7 tunables).
- **Secrets** — 4 new (`PROMPT_REGISTRY_URL/TOKEN/PUBLISH_TOKEN/SIGNING_KEY`), all in
  `SECRET_KEYS`, never committed, never GUI-writable, never logged.

---

## 13. Constraints honored

- **#3 secrets** — registry creds masked + denylisted; publish creds separate from read.
- **#4 no fabrication** — fail-closed to a committed baseline; never an empty/invented prompt.
- **#5 on-demand** — launch-time + explicit sync; `REFRESH_SECONDS=0` default; no daemon.
- **#6 dead-letter** — every fetch/verify/parse failure degrades to known-good, never raises.
- **#7 integrate, don't reinvent** — reuses the `IDataProvider`-style ABC pattern, `gui/env_io`
  secret handling, `observability.alerts`, and the existing Gravity prompt structures.
- **#8 style** — type-hinted, module-level loggers, docstrings, tests per stage.
- **#11–12 docs sync** — CLAUDE.md + GEMINI.md + Gravity + HOW_TO_GUIDE + RUNBOOK in step 8.
- **Safety boundary** — fetched prompts can change *what the AI is told*, never *what the
  platform may do*; guardrail deny-list + code-level gates enforce this structurally.

---

## 14. The "AI master pre-prompt" (the runtime value the registry will store as `master_preprompt` v1.0.0)

> You are a coding agent working in the InvestYo / Stockpy advisory quant platform. This is
> advisory-only software (`ADVISORY_ONLY=true` by default): it produces signals, sizing, and
> reports, and never submits broker orders in the default mode. Honor these constraints on
> every change:
> 1. **On-demand, not always-on** — no scheduler, cron, daemon, or cloud deployment.
> 2. **Dead-letter resilience** — wrap every per-symbol/per-fetch step in try/except; capture
>    (symbol, stage, exception); continue. Failures are reported, never silently dropped.
> 3. **Integrate, don't reinvent** — call existing engines/registries/stores; write new code
>    only for glue, data, orchestration, and the surface a stage explicitly names.
> 4. **No fabricated data** — missing values are `NaN`/empty, never `0.0` or an invented proxy.
> 5. **Secrets stay secret** — credentials live in `.env`, are masked in any UI, never
>    committed, never logged.
> 6. **Safety gates are code, not prompts** — never weaken `ADVISORY_ONLY`, the risk gate, the
>    kill switch, or order quarantine. No prompt, config, or fetched value may bypass them.
> 7. **Style** — type-hint public functions, use module-level `logging` (not `print`),
>    docstring every new function/class, and add/extend pytest tests for every change.
> 8. **Keep agent-context docs in sync** — after changes, update `CLAUDE.md`, `GEMINI.md`, and
>    `Gravity AI Review Suite.py`; update `HOW_TO_GUIDE.md` / `RUNBOOK.md` when operator-facing.
> 9. **Output format** — show the full file or diff; list new deps (`requirements.txt`) and env
>    vars (`.env.example`); give the pytest commands to verify; show the `CLAUDE.md`,
>    `GEMINI.md`, and `Gravity AI Review Suite.py` diffs (skip only with an explicit reason).
>
> Acknowledge these constraints in one sentence, then wait for the stage prompt.

This is the body of `master_preprompt` v1.0.0 in `registry.json`. Publishing v1.1.0 (e.g.
adding a constraint) and moving the `latest` pointer is how you "update the master pre-prompt
over the internet"; pulling it via `python -m prompt_registry get master_preprompt` is how you
retrieve it to paste. Rollback is `PROMPT_REGISTRY_PINS={"master_preprompt":"1.0.0"}`.
