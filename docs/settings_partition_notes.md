# Settings partition notes

Plain-English triage of the measured snapshot in
[`settings_field_census.md`](settings_field_census.md). That file is the data; this file is
the "so what". Every number quoted here comes from
`scripts/measure_settings_census.py` run against commit `e7e64529`.

Regenerate both with:

```bash
python3 scripts/measure_settings_census.py --write
```

---

## 1. Security findings (read this first)

### Nothing credential-shaped is missing from `SECRET_KEYS`

This was the highest-priority thing to check, and the answer is clean. Sweeping all 318
fields for `TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL|MFA` returns 20 matches: **19 are
already in `SECRET_KEYS`**, and the single unprotected match is `EDGAR_FULLTEXT_CHUNK_TOKENS`
— an `int` chunk size, not a credential, exactly the false positive the name pattern is
expected to produce. A supplementary wider sweep
(`TOTP|PASSPHRASE|PRIVATE_KEY|WEBHOOK|CLIENT_ID|CLIENT_SECRET|_PW|AUTH`) found **zero**
additional string-shaped unprotected fields.

`ALLOWED_KEYS ∩ SECRET_KEYS` is also empty, so no key is simultaneously GUI-writable and
classified secret.

**No action needed.** Recording this explicitly because the brief flagged it as a real,
previously-exploitable gap class — it is not currently open.

### Two phantom `SECRET_KEYS` entries — both benign, but one is genuinely stale

`SECRET_KEYS` has 40 entries; only 38 are real `Settings` field names.

| Phantom | Assessment |
|---|---|
| `NTFY_TOPIC` | **Stale leftover.** The real field is `ALERT_NTFY_TOPIC`, which *is* separately present in `SECRET_KEYS`. The protection is intact under the correct name; this entry simply matches nothing. Harmless, but it is the "renamed field left a stale protection" pattern, and it would be worth deleting in a future cleanup. |
| `PROMPT_REGISTRY_CREDENTIALS` | **Deliberate and load-bearing — do not remove.** This is a Firestore service-account JSON blob that has no `settings.py` field by design (CLAUDE.md documents it as intentionally still reading `os.environ`, read at `prompt_registry/registry.py:515`). Because `env_io.read_settings()` masks based on raw `.env` keys rather than model fields, the `SECRET_KEYS` entry *does* still mask it in the GUI. It only looks like a phantom from the model-field side. |

So: one is dead weight, one is doing real work. Neither is a vulnerability.

### An SMTP password is read only from `os.environ`, never from `settings`

`ALERT_EMAIL_SMTP_PASSWORD` is classified `SECRET`, but the only place it is read is
`alerting_mcp/notifier.py:60` via `os.getenv` — it has **zero** `settings.X` reads. The same
is true of six sibling fields (`ALERT_NTFY_TOPIC`, `ALERT_EMAIL_SMTP_HOST`,
`ALERT_EMAIL_SMTP_PORT`, `ALERT_SLACK_WEBHOOK_URL`, `ALERT_CHANNELS`, plus
`FINNHUB_RATE_LIMIT_PER_MIN` elsewhere).

This is **documented and deliberate** — `settings.py:1092-1093` says the notifier "keeps
reading `os.getenv` directly so it stays importable without a full `Settings()` load" — so
it is not a new bug and I am not calling it one. Two things are still worth flagging:

1. It is the same shape as the bug class CLAUDE.md has already had to fix three times
   (`signals/news_catalyst.py::build_finnhub_client`, `prompt_registry/*`,
   `data/market_data.py`'s `CompositeProvider`): pydantic-settings loads `.env` into the
   model, **not** into the real `os.environ`, so these values resolve only when some entry
   point has separately called `load_dotenv()`. `main.py`, `main_orchestrator.py`,
   `app_shell.py`, and `scripts/_bootstrap.py` all do. **`investyo_mcp_server.py` does
   not** — and it is what imports `alerting_mcp.notifier` (lines 2153, 2195). Whether that
   matters depends on how the MCP server receives its environment, which I did not test.
2. For the hot-reload work specifically these seven fields are **unreachable**: mutating
   the `settings` singleton can never affect an `os.getenv` call. Any liveness classifier
   must treat form (d) as "never live-patchable", not as an ordinary read.

### Not security, but a correctness bug: `ALLOWED_KEYS` has 12 duplicate entries

`len(ALLOWED_KEYS) == 274` but `len(set(...)) == 262`. Twelve keys appear twice —
`FINNHUB_RATE_LIMIT_PER_MIN` and eleven `SECTOR_SELECTION_*` / `SECTOR_SIMILARITY_*` keys.
The duplicate bug reported in earlier rounds **is still present**. It is benign at runtime
(membership tests are unaffected) but it means any count derived from `len(ALLOWED_KEYS)`
is wrong by 12. Reported, not fixed, per the brief.

---

## 2. How many fields are genuinely unaccounted for: **zero**

This is the cleanest result in the census, and it differs from what earlier rounds
reported — worth stating plainly so the next pass does not re-litigate it.

Using the brief's three-way split (`SECRET` / `IN_ALLOWED_KEYS` / `UNCLASSIFIED`):

| Bucket | Count |
|---|---|
| `SECRET` | 38 |
| `IN_ALLOWED_KEYS` | 262 |
| `UNCLASSIFIED` | 18 |
| **Total** | **318** |

But `UNCLASSIFIED` is misleading as a name here, because `gui/env_io.py` has a **third**
classification set the brief's partition does not mention: `EXCLUDED_FROM_GUI` (18 entries).
**All 18 `UNCLASSIFIED` fields are in it — the residue is empty.** There is a CI test
(`tests/test_gui_env_io.py::test_every_settings_field_is_classified`) that enforces exactly
this, which is why there is no drift to find.

The 18 split cleanly into two intentional groups:

- **7 filesystem paths** — `OUTPUT_DIR`, `PROMPT_CACHE_DIR`, `WATCH_RULES_FILE`,
  `ALERT_FILE_PATH`, `GRAVITY_AI_RUNNER_OUTPUT_PATH`, `LLM_COMMENTARY_CACHE_PATH`,
  `SYNC_WATCHLIST_FILES`. Not secrets, but letting a browser form rewrite a path the
  process reads and writes is its own risk class.
- **11 fail-closed write/execution gates** — `AI_GENERATION_API_ENABLED`,
  `AUTOMATION_WRITES_ENABLED`, `BROKERAGE_REFRESH_ENABLED`, `COMMAND_EXECUTION_ENABLED`,
  `DEAD_LETTER_RETRY_ENABLED`, `GENERAL_SETTINGS_WRITES_ENABLED`, `LLM_WRITES_ENABLED`,
  `MACRO_GATE_WRITES_ENABLED`, `PROMPT_REGISTRY_WRITES_ENABLED`, `RAG_QUERY_API_ENABLED`,
  `STRATEGY_WRITES_ENABLED`. These are the "a GUI bug must never be able to flip this on"
  flags. Every one carries an explicit `never GUI-writable` / `hand-set in .env only`
  marker in `settings.py`, and **every marker checks out against current reality** — 19
  fields carry such a marker (the 11 flags plus 8 secrets) and **zero are contradicted** by
  actually being in `ALLOWED_KEYS`.

**Triage conclusion for a human: there is nothing to triage here.** The partition is
complete and self-consistent. If a future key-partition design wants a fourth bucket, it
should build on `EXCLUDED_FROM_GUI` rather than discovering these 18 as "unknown".

> Measurement caveat worth knowing: my first pass reported 2 marker contradictions
> (`ETF_HOLDINGS_MARKET_PROXY`, `RAG_PORTFOLIO_CONTEXT_PROVIDER`). Both were false
> positives of a too-loose regex — the first says "Deliberately EXCLUDED from the
> ownership-weighted return composite" (return maths, not `ALLOWED_KEYS`); the second's
> "never GUI-writable" describes `ANTHROPIC_API_KEY`/`GEMINI_API_KEY` inside a
> parenthetical, not itself. The script now requires markers to name the write gate
> explicitly and rejects markers scoped to another field. If someone re-runs this and sees
> contradictions reappear, check the sentence before believing it.

---

## 3. Fields reachable only via dynamic `getattr` — the real hazard for static analysis

The brief asked specifically for these, and this is where the interesting findings are.

### 14 dynamic `getattr(settings, <var>)` sites

Few enough to name in full. Every one of them is a **name-driven dispatch**: the key
arrives as a variable, so no static analysis can attribute it to a field.

| Site | Expression |
|---|---|
| `api/auth.py:140` | `getattr(settings, token_setting_name, None)` |
| `api/data_api.py:160` | `getattr(settings, flag_name, False)` |
| `api/_redact.py:38` | `getattr(settings, k, None)` |
| `api/pilots_api.py:3494` | `getattr(settings, key, None)` |
| `api/pilots_api.py:3579` | `getattr(settings, key, None)` |
| `data/brokerage_credentials.py:119` | `getattr(_settings, k, None)` |
| `data/robinhood_portfolio.py:80` | `getattr(_settings, name, None)` |
| `gui/panels/ai_control_center.py:164` | `getattr(settings, tkey, False)` |
| `gui/panels/ai_control_center.py:189` | `getattr(settings, sel_key, 'none')` |
| `gui/panels/settings_manager.py:186` | `getattr(settings, key, fallback)` |
| `gui/panels/settings_manager.py:203` | `getattr(settings, key, '')` |
| `gui/panels/settings_manager.py:271` | `getattr(settings, key, [])` |
| `llm/status_store.py:212` | `getattr(settings, attr, None)` |
| `Gravity AI Review Suite.py:2714` | `getattr(_rh_settings, _MISSING_ATTR, None)` |

Two of these are **security-critical** and deserve naming individually:

- **`api/auth.py:140`** is inside `make_command_token_guard(token_setting_name, ...)`. The
  actual bearer-token comparison for the command APIs resolves the token *dynamically*, by
  name (`"ORCHESTRATOR_DAEMON_TOKEN"`, `"FOLLOW_API_TOKEN"`). A liveness classifier that
  concluded "these token fields are never read" would be badly wrong.
- **`api/data_api.py:160`** is inside `require_ai_capability_enabled(flag_name, ...)`,
  called with `"AI_GENERATION_API_ENABLED"` (L172) and `"UNIVERSE_SYNC_ENABLED"` (L1012).
  `UNIVERSE_SYNC_ENABLED` gates the `POST /data/sync` write endpoint and has **zero**
  statically-attributable reads anywhere in the tree. It is enforced entirely through this
  one dynamic lookup.

**Implication for the liveness classifier:** a name-literal index is not optional. Of the
9 fields with no statically-attributable read, 7 are demonstrably reached at runtime
because their name appears as a string literal feeding one of the dispatchers above. Only
2 are genuinely unreferenced (below).

### 118 fields are invisible to attribute-only analysis

This is the headline structural number. Reads break down as 611 via `settings.KEY`
(191 distinct fields), 243 via `getattr(settings, "KEY", default)` (148 distinct), and 19
via `os.environ` (15 distinct). **118 fields are reached only via the `getattr`-literal or
`os.environ` forms and never via plain attribute access** — 111 only via `getattr`-literal,
7 only via `os.environ`.

In other words, of the 309 fields reached by any static form, attribute access alone reaches
only 191 — an analysis that looked for `settings.KEY` and nothing else would miss **118 of
309, or 38%**. That is almost certainly how earlier hand-counts went wrong.
A large contiguous block of these is the FMP and forecast-backfill families, which were
written throughout in the `getattr(settings, "X", default)` style.

### 2 fields appear to be genuinely dead

Neither is read, and neither has its name referenced as a literal anywhere:

- **`PROMPT_MAX_CHARS`** — `prompt_registry/guardrails.py` deliberately *mirrors* the value
  with its own `_DEFAULT_MAX_CHARS` constant and never reads the setting, so changing this
  key in `.env` has no effect. It is nonetheless in `ALLOWED_KEYS`, i.e. it is a
  GUI-writable knob wired to nothing.
- **`SENTIMENT_PIT_MIN_MONTHS`** — referenced only in prose (other fields' descriptions and
  a `data/historical_store.py` comment). Also in `ALLOWED_KEYS`.

Both are low-stakes, but they are the two most likely candidates if anyone wants to trim
the allowlist.

---

## 4. Three separate mechanisms can currently change a setting

Relevant because "how many ways can a setting change right now" was an explicit question,
and the answer is more than the `.env` writer.

Across `api/pilots_api.py`'s 26 `PUT`/`POST` routes, **11 mutate a setting**, and all 11
declare an `applies` value (once conditional expressions and shared-helper responses are
resolved — a naive check reports 4). The mechanisms:

1. **`.env` write** via `env_io.write_*` — all 11 routes. Durable; effective next launch.
2. **In-process `setattr(settings, ...)`** — exactly one route, `PUT /llm/setting`, and only
   for the 11 keys in `gui/ai_control_center.py::LIVE_PATCHABLE_KEYS`. This is the *only*
   existing hot-reload beachhead in the codebase and is the obvious thing for the new system
   to generalize.
3. **HTTP push into a separately-running daemon** via `daemon_client.set_*` — one route,
   `PUT /automation/schedule/interval`. Neither of the other two mechanisms describes it:
   the value lands in another process entirely.

There is also **one mutating route outside `pilots_api.py`** — `PUT /data/universe`
(`api/data_api.py:384`), which writes `.env` and declares no `applies` value at all. Any
inventory scoped to `pilots_api.py` alone will miss it.

---

## 5. Things a future pass should not have to re-derive

- 318 fields; **0** fall outside the recognised type kinds (bool/int/float/str/`Optional[str]`/
  `list[str]`/`list[int]`/`Path`/4 distinct `dict[...]` shapes), so a kind-derivation switch
  over those categories is currently **total**. 73 fields end in `_ENABLED`.
- 329 production files scanned, **0 parse failures** — the read-form numbers have no
  silent undercount from unparseable files.
- The singleton is bound under **18 distinct local names**. Any future tooling must resolve
  aliases via AST; grepping `settings\.` is not viable.
- `_JSON_KEYS` has 12 entries; values for those keys must be passed to
  `env_io.write_setting` as plain Python objects (it owns the encoding).
