# Settings field census

> **Generated file — do not hand-edit.** Every number below is produced by
> `scripts/measure_settings_census.py` and re-derived on each run. Regenerate with:
> `python3 scripts/measure_settings_census.py --write`

- Measured at commit: `3444d98d637a75c84bda4e338dd3c4f8c8e84a89`
- Machine-readable companion: [`settings_field_census.json`](settings_field_census.json)
- Prose triage of these findings: [`settings_partition_notes.md`](settings_partition_notes.md)

This is a point-in-time snapshot of `settings.Settings` and every mechanism that can
currently change a setting. It exists so that later work (a static liveness classifier,
a key-partition design) can build on measured numbers instead of re-deriving them.

## 1. Field-type breakdown

`len(Settings.model_fields)` = **328**

| Annotation | Count |
|---|---|
| `int` | 93 |
| `bool` | 89 |
| `float` | 57 |
| `Optional[str]` | 42 |
| `str` | 34 |
| `list[str]` | 7 |
| `Path` | 1 |
| `dict[str, dict[str, float]]` | 1 |
| `dict[str, dict]` | 1 |
| `dict[str, float]` | 1 |
| `dict[str, str]` | 1 |
| `list[int]` | 1 |

Fields whose name ends in `_ENABLED`: **79**

Distinct `dict[...]` shapes: **4**

| dict shape | Count |
|---|---|
| `dict[str, dict[str, float]]` | 1 |
| `dict[str, dict]` | 1 |
| `dict[str, float]` | 1 |
| `dict[str, str]` | 1 |

**other/unhandled bucket: 0 fields.** Every field falls into a recognised kind, so a
kind-derivation switch over the categories above is currently total.

## 2. `gui/env_io.py` list sizes

| Name | len() | len(set()) | Note |
|---|---|---|---|
| `ALLOWED_KEYS` | 273 | 273 | 0 duplicate entries (clean) |
| `SECRET_KEYS` | 39 | 39 | 0 duplicate entries |
| `_JSON_KEYS` | 12 | 12 | frozenset |
| `EXCLUDED_FROM_GUI` | 18 | 18 | frozenset; third classification bucket |

`ALLOWED_KEYS ∩ SECRET_KEYS` overlap: **0** (clean — no key is both writable and secret)

## 3. The partition

Every `Settings.model_fields` name classified into exactly one bucket.

| Bucket | Count | Definition |
|---|---|---|
| `SECRET` | 37 | in `env_io.SECRET_KEYS` |
| `IN_ALLOWED_KEYS` | 273 | in `env_io.ALLOWED_KEYS` |
| `UNCLASSIFIED` | 18 | in neither |

Of the 18 `UNCLASSIFIED` fields, **18** are accounted for by the third `EXCLUDED_FROM_GUI` bucket and **0** are accounted for nowhere.

### Every `UNCLASSIFIED` field

| Field | settings.py | In `EXCLUDED_FROM_GUI` | What it is |
|---|---|---|---|
| `AI_GENERATION_API_ENABLED` | L3088 | yes | Enables POST /data/ai/{commentary,chart,research}/{symbol} on the Data API. Off by default -- exposing paid Claude/Gemini/Opal calls over a fail-open HTTP API is its own risk/cost class, separate f... |
| `ALERT_FILE_PATH` | L1132 | yes | Absolute path for JSON-lines alert log file. None = disabled. |
| `AUTOMATION_WRITES_ENABLED` | L2921 | yes | Enables PUT /automation/schedule/interval and POST /automation/resume on the Pilots API. Off by default; also requires FOLLOW_API_TOKEN. Never GUI-writable. POST /automation/run and /automation/pau... |
| `BROKERAGE_REFRESH_ENABLED` | L3169 | yes | Enables POST /brokerage/refresh on the Pilots API (forces a live Robinhood re-login + account-snapshot fetch, bypassing the daily cache). Off by default; also requires FOLLOW_API_TOKEN and a loopba... |
| `COMMAND_EXECUTION_ENABLED` | L246 | yes | Enable the 'command' job type on the orchestrator Control API's POST /jobs (api/_jobs.py) — lets the webapp's Commands screen actually run a manifest-listed CLI target (not just compose/copy it), g... |
| `DEAD_LETTER_RETRY_ENABLED` | L3911 | yes | Enables POST /dead-letter/retry on the Pilots API (re-runs main.py for one dead-lettered symbol, advisory-only -- no orders). Off by default; also requires FOLLOW_API_TOKEN. Never GUI-writable -- h... |
| `GENERAL_SETTINGS_WRITES_ENABLED` | L3010 | yes | Enables PUT /settings/tunables on the Pilots API (general runtime tunables -- Kelly sizing, risk gate, forecasting, market data, runtime/ops -> .env). Off by default; also requires FOLLOW_API_TOKEN... |
| `GRAVITY_AI_RUNNER_OUTPUT_PATH` | L3527 | yes | Where the runner writes the per-step Claude + Gemini verdicts. Lives under output/ which is gitignored. |
| `LLM_COMMENTARY_CACHE_PATH` | L3412 | yes | JSON cache for LLM commentary results. Day-bucketed; safe to delete manually. Lives under output/ which is gitignored. |
| `LLM_WRITES_ENABLED` | L2963 | yes | Enables PUT /llm/setting on the Pilots API (LLM capability toggles + provider selection -> .env). Off by default; also requires FOLLOW_API_TOKEN. Never GUI-writable — hand-set in .env only, so AI-c... |
| `MACRO_GATE_WRITES_ENABLED` | L3140 | yes | Enables PUT /observability/macro-gate on the Pilots API (flips MACRO_REGIME_GATE_ENABLED -> .env). Off by default; also requires FOLLOW_API_TOKEN. Never GUI-writable — hand-set in .env only, so thi... |
| `OUTPUT_DIR` | L1355 | yes | Directory for generated reports. |
| `PROMPT_CACHE_DIR` | L3679 | yes | Directory for the signed-version disk cache. Each prompt ID gets a sub-directory; up to PROMPT_CACHE_KEEP_VERSIONS signed .json files are kept per ID for offline rollback. |
| `PROMPT_REGISTRY_WRITES_ENABLED` | L3933 | yes | FAIL-CLOSED master switch for api/pilots_api.py's `PUT /prompts/pin` (pins/clears a prompt ID's PROMPT_REGISTRY_PINS entry -- changes WHICH PROMPT TEXT THE PLATFORM ACTUALLY RUNS, a real behavioral... |
| `RAG_QUERY_API_ENABLED` | L3110 | yes | Enables POST /rag/query on the Pilots API (agents/rag_orchestrator.py's run_rag_query, calling a paid LLM provider). Off by default -- see AI_GENERATION_API_ENABLED for the same risk-class reasonin... |
| `STRATEGY_WRITES_ENABLED` | L2941 | yes | Enables PUT /strategy/modules on the Pilots API (signal weights + disabled-module set -> .env). Off by default; also requires FOLLOW_API_TOKEN. Never GUI-writable — hand-set in .env only, so signal... |
| `SYNC_WATCHLIST_FILES` | L1357 | yes | Colon-separated paths (shell PATH convention) to additional plain-text watchlist files (one ticker per line, '#' = comment) consumed by data.robinhood_client.discover_universe(). Missing files are ... |
| `WATCH_RULES_FILE` | L3297 | yes | Path to watch_rules.yaml. Defines per-symbol ntfy push-alert rules (action_change, conviction_above, conviction_below). Missing file = no rules active (silent no-op). |

## 4. `SECRET_KEYS` sanity check

**Phantom entries** (in `SECRET_KEYS` but not a real `model_fields` name): **2**

- `NTFY_TOPIC`
- `PROMPT_REGISTRY_CREDENTIALS`

### Credential-shaped name sweep — pattern `TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL|MFA` (case-insensitive)

- matches already in `SECRET_KEYS`: **18**
- matches NOT in `SECRET_KEYS`: **1**
- of those, genuinely credential-shaped (`str` / `Optional[str]`): **0**

A field typed `int` / `float` / `bool` cannot hold secret material regardless of a name
match, so those are listed as filtered false positives rather than gaps.

| Field | Type | In `ALLOWED_KEYS` | Verdict |
|---|---|---|---|
| `EDGAR_FULLTEXT_CHUNK_TOKENS` | `int` | yes | false positive (non-string type) |

### Supplementary wider sweep — pattern `TOTP|PASSPHRASE|PRIVATE_KEY|WEBHOOK|CLIENT_ID|CLIENT_SECRET|_PW\b|AUTH`

Not requested by the brief, run because the primary pattern misses several credential
shapes by construction. Extra string-shaped, unprotected matches: **0**

## 5. Hand-set-only write-gate flags

Fields whose `settings.py` comment or `Field(description=...)` claims they are
deliberately never GUI-writable, cross-referenced against **actual** current
`ALLOWED_KEYS` membership.

- fields carrying such a marker: **19**
- markers **contradicted** by current `ALLOWED_KEYS` membership: **0**

| Field | Marker site(s) | In `ALLOWED_KEYS` now | In `SECRET_KEYS` | Claim holds |
|---|---|---|---|---|
| `AI_GENERATION_API_ENABLED` | `settings.py:3069`, `settings.py:3088` | no | no | yes |
| `AUTOMATION_WRITES_ENABLED` | `settings.py:2909`, `settings.py:2921` | no | no | yes |
| `BROKERAGE_REFRESH_ENABLED` | `settings.py:3150`, `settings.py:3169` | no | no | yes |
| `COMMAND_EXECUTION_ENABLED` | `settings.py:246` | no | no | yes |
| `DEAD_LETTER_RETRY_ENABLED` | `settings.py:3897`, `settings.py:3911` | no | no | yes |
| `FMP_API_KEY` | `settings.py:435` | no | yes | yes |
| `FOLLOW_API_TOKEN` | `settings.py:200` | no | yes | yes |
| `GENERAL_SETTINGS_WRITES_ENABLED` | `settings.py:2992`, `settings.py:3010` | no | no | yes |
| `LLM_WRITES_ENABLED` | `settings.py:2950`, `settings.py:2963` | no | no | yes |
| `MACRO_GATE_WRITES_ENABLED` | `settings.py:3119`, `settings.py:3140` | no | no | yes |
| `ORCHESTRATOR_DAEMON_TOKEN` | `settings.py:181` | no | yes | yes |
| `PROMPT_REGISTRY_PUBLISH_TOKEN` | `settings.py:3644` | no | yes | yes |
| `PROMPT_REGISTRY_SIGNING_KEY` | `settings.py:3652` | no | yes | yes |
| `PROMPT_REGISTRY_TOKEN` | `settings.py:3636` | no | yes | yes |
| `PROMPT_REGISTRY_URL` | `settings.py:3628` | no | yes | yes |
| `PROMPT_REGISTRY_WRITES_ENABLED` | `settings.py:3922` | no | no | yes |
| `RAG_QUERY_API_ENABLED` | `settings.py:3098`, `settings.py:3110` | no | no | yes |
| `STATE_API_TOKEN` | `settings.py:173` | no | yes | yes |
| `STRATEGY_WRITES_ENABLED` | `settings.py:2931`, `settings.py:2941` | no | no | yes |

## 6. Live-write endpoint inventory — `api/pilots_api.py`

- `PUT`/`POST`/`PATCH`/`DELETE` routes total: **35**
- routes that mutate a setting: **16**

Three *distinct* mutation mechanisms exist — a liveness model that only considers
"this process's singleton" would miss two of them:

| Mechanism | Routes | Effect |
|---|---|---|
| `.env` write via `env_io.write_*` | 16 | durable; takes effect on the **next** process launch |
| in-process `setattr(settings, ...)` | 0 | patches THIS process's singleton only |
| push to the daemon via `daemon_client.set_*` | 1 | HTTP call into a **separately running** daemon process |

Routes declaring an `applies` value in their response: **6** of 16.

Resolution is AST-based and follows one level of indirection: a handler that only calls a
module-level helper which itself calls `env_io.write_*` (or builds the response carrying
`applies`) is still attributed correctly. `applies` values are resolved through `Constant`,
`IfExp`, and locally-bound `Name` expressions — a Constant-only check reports
`(none)` for most of this table.

| Route | Method | Handler | Line | `.env` | `setattr` | daemon push | `applies` claims |
|---|---|---|---|---|---|---|---|
| `/observability/macro-gate` | PUT | `put_macro_gate` | 1684 | yes | no | no | `next_daemon_restart` |
| `/llm/setting` | PUT | `set_llm_setting` | 2880 | yes | no | no | `immediately`, `next_daemon_restart` |
| `/automation/schedule/interval` | PUT | `set_automation_interval` | 3513 | yes | no | yes | `immediately`, `next_daemon_restart` |
| `/strategy/modules` | PUT | `set_strategy_modules` | 3596 | yes | no | no | `next_daemon_restart` |
| `/automation/execution-mode` | PUT | `update_execution_mode` | 3683 | yes | no | no | `next_daemon_restart` |
| `/settings/tunables` | PUT | `put_settings_tunables` | 4044 | yes | no | no | _(none)_ |
| `/settings/tunables` | PATCH | `put_settings_tunables` | 4044 | yes | no | no | _(none)_ |
| `/settings/sentiment` | PUT | `put_settings_sentiment` | 4651 | yes | no | no | _(none)_ |
| `/settings/sentiment` | PATCH | `put_settings_sentiment` | 4651 | yes | no | no | _(none)_ |
| `/settings/sector-selection` | PUT | `put_settings_sector_selection` | 4676 | yes | no | no | _(none)_ |
| `/settings/sector-selection` | PATCH | `put_settings_sector_selection` | 4676 | yes | no | no | _(none)_ |
| `/settings/fmp` | PUT | `put_settings_fmp` | 4701 | yes | no | no | _(none)_ |
| `/settings/fmp` | PATCH | `put_settings_fmp` | 4701 | yes | no | no | _(none)_ |
| `/settings/etf-transmission` | PUT | `put_settings_etf_transmission` | 4726 | yes | no | no | _(none)_ |
| `/settings/etf-transmission` | PATCH | `put_settings_etf_transmission` | 4726 | yes | no | no | _(none)_ |
| `/prompts/pin` | PUT | `put_prompts_pin` | 4997 | yes | no | no | `next_daemon_restart` |

### Existing in-process hot-reload beachhead — `gui/ai_control_center.py::LIVE_PATCHABLE_KEYS`

`PUT /llm/setting` is the only route that patches the live singleton, and it does so only
for the **11** keys on this allowlist (all of which are real `Settings` fields:
`True`). Everything else in the table above is `.env`-only.

```
GRAVITY_AI_RUNNER_ENABLED
LLM_COMMENTARY_ALERT_PROVIDER
LLM_COMMENTARY_ENABLED
LLM_COMMENTARY_RATIONALE_PROVIDER
OPAL_RESEARCH_ENABLED
OPAL_RESEARCH_MODEL
OPAL_RESEARCH_PROVIDER
RAG_PORTFOLIO_CONTEXT_ENABLED
RAG_PORTFOLIO_CONTEXT_PROVIDER
SENTIMENT_LLM_VERIFICATION_ENABLED
SENTIMENT_LLM_VERIFICATION_PROVIDER
```

Module-level helpers in this file that write `.env` directly: `_validate_and_write_payload`

### Other `api/*.py` modules (supplementary — not requested, included for the "how many ways can a setting change" count)

| File | Mutating routes | Writes `.env` | Live `setattr` |
|---|---|---|---|
| `api/data_api.py` | 1 | 1 | 0 |

## 7. Read-form census

Scope: **340** production `.py` files (excludes `tests/`, `test_*.py`, `conftest.py`, `.venv/`, `webapp/`, `node_modules/`).

Files that could not be parsed: **0**

The singleton is bound under **18** distinct local names
across the tree, which is why this is an AST pass and not a grep:

```
_S.settings, _bl_settings, _live_settings, _oos_gate_settings, _rh_settings, _s, _s2, _sett, _settings, _settings.settings, _settings93, _settings93_ro, _settings_mod.settings, _settings_singleton, _wf_settings, platform_settings, settings, settings_module.settings
```

| Form | Total reads | Distinct fields reached |
|---|---|---|
| (a) `settings.KEY` | 631 | 199 |
| (b) `getattr(settings, "KEY", default)` | 244 | 149 |
| (c) `getattr(settings, <var>)` (dynamic) | 17 sites | n/a — key not statically known |
| (d) `os.environ` / `os.getenv("KEY")` | 16 | 13 |

Fields reached by at least one form: **319** of 328.

### Fields with NO statically-attributable read — **9**

**These are not necessarily dead.** A field whose name is passed as a *string literal* to a
factory that then does a dynamic `getattr` is read at runtime while being invisible to every
form above. The name-literal column is the evidence: a non-empty value means the key is
referenced by name somewhere and is probably read dynamically.

| Field | Name-literal sites | Verdict |
|---|---|---|
| `AI_GENERATION_API_ENABLED` | `api/data_api.py:177`, `settings_keysets.py:250` | likely read dynamically |
| `EDGAR_FULLTEXT_CHUNK_TOKENS` | `api/pilots_api.py:4463` | likely read dynamically |
| `EDGAR_FULLTEXT_FORMS` | `api/pilots_api.py:4462` | likely read dynamically |
| `ETF_HOLDINGS_TICKERS` | `api/pilots_api.py:4587`, `gui/panels/settings_manager.py:122` | likely read dynamically |
| `FMP_ECON_INDICATORS` | `api/pilots_api.py:4567`, `gui/panels/settings_manager.py:158` | likely read dynamically |
| `PROMPT_MAX_CHARS` | _none_ | no read and no name reference found |
| `PROMPT_REGISTRY_REFRESH_SECONDS` | `Gravity AI Review Suite.py:10999` | likely read dynamically |
| `SENTIMENT_PIT_MIN_MONTHS` | _none_ | no read and no name reference found |
| `UNIVERSE_SYNC_ENABLED` | `api/data_api.py:1055` | likely read dynamically |

### Fields reachable ONLY via form (b) or (d), never via (a) — **120**

These are exactly the keys an attribute-only static analysis would miss entirely.

| Field | Reached via | (b) count | (d) count |
|---|---|---|---|
| `ADVISORY_MAX_CONCURRENCY` | b | 2 | 0 |
| `ADVISORY_REUSE_PIPELINE_COMPUTE` | b | 1 | 0 |
| `ALERT_CHANNELS` | d | 0 | 1 |
| `ALERT_EMAIL_SMTP_HOST` | d | 0 | 1 |
| `ALERT_EMAIL_SMTP_PASSWORD` | d | 0 | 1 |
| `ALERT_EMAIL_SMTP_PORT` | d | 0 | 1 |
| `ALERT_NTFY_TOPIC` | d | 0 | 1 |
| `ALERT_SLACK_WEBHOOK_URL` | d | 0 | 1 |
| `ALERT_WEBHOOK_URL` | b | 1 | 0 |
| `ALPACA_KEY_ROTATED_DATE` | b | 1 | 0 |
| `ATTENTION_CIRCUIT_BREAKER_THRESHOLD` | b | 1 | 0 |
| `ATTENTION_INGESTION_MAX_SECONDS_PER_CYCLE` | b | 1 | 0 |
| `BERT_LLA_ABLATION_ENABLED` | b | 1 | 0 |
| `BERT_LLA_BLEND_ENABLED` | b | 1 | 0 |
| `BERT_LLA_ENABLED` | b | 2 | 0 |
| `BERT_LLA_MIN_SENTIMENT_COVERAGE` | b | 1 | 0 |
| `BERT_LLA_WINDOW_SIZE` | b | 1 | 0 |
| `CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED` | b | 1 | 0 |
| `DATABASE_URL` | b | 1 | 0 |
| `DATA_FETCH_MAX_CONCURRENCY` | b | 4 | 0 |
| `DB_MAX_OVERFLOW` | b | 2 | 0 |
| `DB_POOL_SIZE` | b | 2 | 0 |
| `EDGAR_MAX_CONCURRENCY` | b | 1 | 0 |
| `ETF_HOLDINGS_CIRCUIT_BREAKER_THRESHOLD` | b | 1 | 0 |
| `ETF_HOLDINGS_ISSUER_CSV_ENABLED` | b | 1 | 0 |
| `ETF_HOLDINGS_MARKET_PROXY` | b | 2 | 0 |
| `ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE` | b | 1 | 0 |
| `ETF_HOLDINGS_REFRESH_DAYS` | b | 1 | 0 |
| `ETF_TRANSMISSION_COV_INFLATION` | b | 1 | 0 |
| `ETF_TRANSMISSION_COV_WINDOW_DAYS` | b | 1 | 0 |
| `ETF_TRANSMISSION_EXCLUDED_SYMBOLS` | b | 1 | 0 |
| `ETF_TRANSMISSION_MIN_OBS` | b | 1 | 0 |
| `ETF_TRANSMISSION_WINDOW_DAYS` | b | 1 | 0 |
| `ETF_TRANSMISSION_WRAPPERS` | b | 2 | 0 |
| `EXCURSION_INTRADAY_ENABLED` | b | 1 | 0 |
| `EXECUTION_PRIORITY_QUEUE_ENABLED` | b | 1 | 0 |
| `EXECUTION_QUEUE_LEAK_RATE_PER_SEC` | b | 1 | 0 |
| `FINBERT_BATCH_SIZE` | b | 1 | 0 |
| `FINBERT_SCORE_CACHE_ENABLED` | b | 1 | 0 |
| `FINNHUB_RATE_LIMIT_PER_MIN` | d | 0 | 2 |
| `FMP_ANALYST_ENABLED` | b | 2 | 0 |
| `FMP_ANALYST_REFRESH_HOURS` | b | 1 | 0 |
| `FMP_BARS_ENABLED` | b | 1 | 0 |
| `FMP_BASE_URL` | b | 1 | 0 |
| `FMP_COOLDOWN_SECONDS` | b | 1 | 0 |
| `FMP_COOLDOWN_THRESHOLD` | b | 1 | 0 |
| `FMP_EARNINGS_ENABLED` | b | 2 | 0 |
| `FMP_EARNINGS_REFRESH_HOURS` | b | 1 | 0 |
| `FMP_FALLBACK_ENABLED` | b | 2 | 0 |
| `FMP_FUNDAMENTALS_ENABLED` | b | 1 | 0 |
| `FMP_INSIDER_ENABLED` | b | 1 | 0 |
| `FMP_INSIDER_MIN_LAG_DAYS` | b | 1 | 0 |
| `FMP_INSIDER_REFRESH_DAYS` | b | 1 | 0 |
| `FMP_MACRO_ENABLED` | b | 1 | 0 |
| `FMP_MAX_RETRIES` | b | 1 | 0 |
| `FMP_MAX_SECONDS_PER_CYCLE` | b | 3 | 0 |
| `FMP_MIN_REQUEST_INTERVAL_SECONDS` | b | 1 | 0 |
| `FMP_NEWS_ENABLED` | b | 7 | 0 |
| `FMP_NEWS_MAX_PAGES` | b | 3 | 0 |
| `FMP_NEWS_PAGE_LIMIT` | b | 3 | 0 |
| `FMP_OPTIONS_CONTEXT_ENABLED` | b | 1 | 0 |
| `FMP_OPTIONS_HEALTH_ENABLED` | b | 1 | 0 |
| `FMP_PEERS_ENABLED` | b | 1 | 0 |
| `FMP_QUOTES_ENABLED` | b | 1 | 0 |
| `FMP_QUOTES_REALTIME` | b | 1 | 0 |
| `FMP_RETRY_BACKOFF_SECONDS` | b | 1 | 0 |
| `FMP_SECTOR_SNAPSHOT_ENABLED` | b | 1 | 0 |
| `FMP_TIMEOUT_SECONDS` | b | 1 | 0 |
| `FORECAST_BACKFILL_CLASSIFIER_TYPE` | b | 1 | 0 |
| `FORECAST_BACKFILL_HORIZONS` | b | 2 | 0 |
| `FORECAST_BACKFILL_LOOKBACK_YEARS` | b | 1 | 0 |
| `FORECAST_BACKFILL_MACD_FAST` | b | 1 | 0 |
| `FORECAST_BACKFILL_MACD_SLOW` | b | 1 | 0 |
| `FORECAST_BACKFILL_MAX_DEPTH` | b | 1 | 0 |
| `FORECAST_BACKFILL_MOMENTUM_WINDOW` | b | 1 | 0 |
| `FORECAST_BACKFILL_N_ESTIMATORS` | b | 1 | 0 |
| `FORECAST_BACKFILL_RANDOM_STATE` | b | 1 | 0 |
| `FORECAST_BACKFILL_RSI_WINDOW` | b | 1 | 0 |
| `FORECAST_BACKFILL_TRAIN_SPLIT` | b | 1 | 0 |
| `FORECAST_BACKFILL_VOL_LONG_WINDOW` | b | 1 | 0 |
| `FORECAST_BACKFILL_VOL_RATIO_WINDOW` | b | 1 | 0 |
| `FORECAST_BACKFILL_VOL_SHORT_WINDOW` | b | 1 | 0 |
| `FORECAST_CNN_LSTM_WALKFORWARD_SCALING` | b | 1 | 0 |
| `FORECAST_MAX_CONCURRENCY` | b | 2 | 0 |
| `FORECAST_MODEL_PERSISTENCE_ENABLED` | b | 2 | 0 |
| `FORECAST_PROPHET_WEIGHT` | b | 2 | 0 |
| `FRED_KEY_ROTATED_DATE` | b | 1 | 0 |
| `GDELT_COOLDOWN_SECONDS` | b | 1 | 0 |
| `GDELT_COOLDOWN_THRESHOLD` | b | 1 | 0 |
| `GDELT_MAX_RETRIES` | b | 1 | 0 |
| `GDELT_MIN_REQUEST_INTERVAL_SECONDS` | b | 1 | 0 |
| `GDELT_RETRY_BACKOFF_SECONDS` | b | 1 | 0 |
| `GRAVITY_AI_RUNNER_ENABLED` | b | 3 | 0 |
| `LLM_COMMENTARY_CACHE_PATH` | b | 1 | 0 |
| `LLM_STATUS_MAX_AGE_HOURS` | b | 1 | 0 |
| `MARKET_DATA_WS_ENABLED` | b | 2 | 0 |
| `MARKET_DATA_WS_RECONNECT_BASE_SECONDS` | b | 1 | 0 |
| `MARKET_DATA_WS_RECONNECT_MAX_SECONDS` | b | 1 | 0 |
| `MARKET_DATA_WS_STALE_SECONDS` | b | 1 | 0 |
| `MARKET_DATA_WS_SYMBOLS` | b | 1 | 0 |
| `META_LABELING_ENABLED` | b | 1 | 0 |
| `OPAL_RESEARCH_MODEL` | b | 2 | 0 |
| `OPAL_RESEARCH_PROVIDER` | b | 2 | 0 |
| `OPAL_RESEARCH_TIMEOUT_SECONDS` | b | 1 | 0 |
| `OPENAI_API_KEY` | b | 4 | 0 |
| `OPTIONS_MATRIX_ENABLED` | b | 1 | 0 |
| `PAIRS_SNAPSHOT_ENABLED` | b | 1 | 0 |
| `PAIRS_SNAPSHOT_MAX_PAIRS` | b | 1 | 0 |
| `RAG_EMBEDDING_PROVIDER` | b | 1 | 0 |
| `RAG_INDEX_LOOKBACK_DAYS` | b | 1 | 0 |
| `RAG_INDEX_MAX_DOCUMENTS` | b | 1 | 0 |
| `RAG_PORTFOLIO_CONTEXT_ENABLED` | b | 3 | 0 |
| `RAG_PORTFOLIO_CONTEXT_PROVIDER` | b | 1 | 0 |
| `RAG_RETRIEVAL_TOP_K` | b | 1 | 0 |
| `ROBINHOOD_LIMIT_BUFFER_BPS` | b | 1 | 0 |
| `SECTOR_FORECAST_CONFIGS` | b | 1 | 0 |
| `SECTOR_FORECAST_CONFIG_PATH` | b | 1 | 0 |
| `SENTIMENT_LLM_VERIFICATION_ENABLED` | b | 2 | 0 |
| `SENTIMENT_LLM_VERIFICATION_PROVIDER` | b | 1 | 0 |
| `VALIDATION_HARNESS_OOS_GATE_ENABLED` | b | 1 | 0 |

### Dynamic `getattr` sites (form c) — **17**

The key is not a literal, so no static analysis can attribute these to a field name.

| Site | Expression |
|---|---|
| `Gravity AI Review Suite.py:2714` | `getattr(_rh_settings, _MISSING_ATTR, None)` |
| `api/_redact.py:38` | `getattr(settings, k, None)` |
| `api/auth.py:140` | `getattr(settings, token_setting_name, None)` |
| `api/data_api.py:164` | `getattr(settings, flag_name, False)` |
| `api/pilots_api.py:2949` | `getattr(settings, body.key)` |
| `api/pilots_api.py:3981` | `getattr(settings, key, None)` |
| `api/pilots_api.py:4086` | `getattr(settings, key, None)` |
| `data/brokerage_credentials.py:125` | `getattr(_settings, k, None)` |
| `data/robinhood_portfolio.py:83` | `getattr(_settings, name, None)` |
| `gui/panels/ai_control_center.py:164` | `getattr(settings, tkey, False)` |
| `gui/panels/ai_control_center.py:189` | `getattr(settings, sel_key, 'none')` |
| `gui/panels/settings_manager.py:186` | `getattr(settings, key, fallback)` |
| `gui/panels/settings_manager.py:203` | `getattr(settings, key, '')` |
| `gui/panels/settings_manager.py:271` | `getattr(settings, key, [])` |
| `llm/status_store.py:212` | `getattr(settings, attr, None)` |
| `runtime_flags_writer.py:774` | `getattr(settings_module.settings, key, None)` |
| `runtime_flags_writer.py:783` | `getattr(settings_module.settings, key, None)` |

### Fields read via `os.environ` (form d) — 13 field(s)

`.env` is loaded into the `Settings` model directly by pydantic-settings; it is only
copied into the real `os.environ` when something calls `load_dotenv()`. A field read
this way therefore reads a *different source* than `settings.KEY` does — see CLAUDE.md's
"Credential reads MUST go through `settings.X`" convention for the class of bug this causes.

| Field | Reads | Also read via (a) |
|---|---|---|
| `FINNHUB_RATE_LIMIT_PER_MIN` | 2 | **no** |
| `FUNDAMENTALS_CACHE_TTL_SECONDS` | 2 | yes |
| `FUNDAMENTALS_NEG_CACHE_TTL_SECONDS` | 2 | yes |
| `ALERT_CHANNELS` | 1 | **no** |
| `ALERT_EMAIL_FROM` | 1 | yes |
| `ALERT_EMAIL_SMTP_HOST` | 1 | **no** |
| `ALERT_EMAIL_SMTP_PASSWORD` | 1 | **no** |
| `ALERT_EMAIL_SMTP_PORT` | 1 | **no** |
| `ALERT_EMAIL_TO` | 1 | yes |
| `ALERT_NTFY_TOPIC` | 1 | **no** |
| `ALERT_SLACK_WEBHOOK_URL` | 1 | **no** |
| `LOG_LEVEL` | 1 | yes |
| `PROMPT_REGISTRY_SIGNING_KEY` | 1 | yes |

---

_Regenerate: `python3 scripts/measure_settings_census.py --write`_
