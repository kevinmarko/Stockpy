# Settings field census

> **Generated file — do not hand-edit.** Every number below is produced by
> `scripts/measure_settings_census.py` and re-derived on each run. Regenerate with:
> `python3 scripts/measure_settings_census.py --write`

- Measured at commit: `a1b84456680f89d02fab535cf25926a142527471`
- Machine-readable companion: [`settings_field_census.json`](settings_field_census.json)
- Prose triage of these findings: [`settings_partition_notes.md`](settings_partition_notes.md)

This is a point-in-time snapshot of `settings.Settings` and every mechanism that can
currently change a setting. It exists so that later work (a static liveness classifier,
a key-partition design) can build on measured numbers instead of re-deriving them.

## 1. Field-type breakdown

`len(Settings.model_fields)` = **426**

| Annotation | Count |
|---|---|
| `bool` | 124 |
| `int` | 102 |
| `float` | 88 |
| `Optional[str]` | 50 |
| `str` | 47 |
| `list[str]` | 8 |
| `Optional[Path]` | 1 |
| `Path` | 1 |
| `dict[str, dict[str, float]]` | 1 |
| `dict[str, dict]` | 1 |
| `dict[str, float]` | 1 |
| `dict[str, str]` | 1 |
| `list[int]` | 1 |

Fields whose name ends in `_ENABLED`: **111**

Distinct `dict[...]` shapes: **4**

| dict shape | Count |
|---|---|
| `dict[str, dict[str, float]]` | 1 |
| `dict[str, dict]` | 1 |
| `dict[str, float]` | 1 |
| `dict[str, str]` | 1 |

### other/unhandled bucket — **1** field(s)

A future kind-derivation switch needs an explicit branch for each of these:

| Field | Annotation |
|---|---|
| `OUTPUT_DIR` | `Optional[Path]` |

## 2. `gui/env_io.py` list sizes

| Name | len() | len(set()) | Note |
|---|---|---|---|
| `ALLOWED_KEYS` | 374 | 374 | 0 duplicate entries (clean) |
| `SECRET_KEYS` | 46 | 45 | 1 duplicate entries |
| `_JSON_KEYS` | 12 | 12 | frozenset |
| `EXCLUDED_FROM_GUI` | 9 | 9 | frozenset; third classification bucket |

`ALLOWED_KEYS ∩ SECRET_KEYS` overlap: **0** (clean — no key is both writable and secret)

## 3. The partition

Every `Settings.model_fields` name classified into exactly one bucket.

| Bucket | Count | Definition |
|---|---|---|
| `SECRET` | 43 | in `env_io.SECRET_KEYS` |
| `IN_ALLOWED_KEYS` | 374 | in `env_io.ALLOWED_KEYS` |
| `UNCLASSIFIED` | 9 | in neither |

Of the 9 `UNCLASSIFIED` fields, **9** are accounted for by the third `EXCLUDED_FROM_GUI` bucket and **0** are accounted for nowhere.

### Every `UNCLASSIFIED` field

| Field | settings.py | In `EXCLUDED_FROM_GUI` | What it is |
|---|---|---|---|
| `ALERT_FILE_PATH` | L1577 | yes | Absolute path for JSON-lines alert log file. None = disabled. |
| `GCLOUD_BIN` | L4983 | yes | Path to the gcloud binary for environment integrations. |
| `GRAVITY_AI_RUNNER_OUTPUT_PATH` | L4217 | yes | Where the runner writes the per-step Claude + Gemini verdicts. Lives under output/ which is gitignored. |
| `LLM_COMMENTARY_CACHE_PATH` | L4042 | yes | JSON cache for LLM commentary results. Day-bucketed; safe to delete manually. Lives under output/ which is gitignored. |
| `LOCAL_DATA_ROOT` | L1830 | yes | Machine-global root for ALL locally-generated model/data artifacts (trained models, SQLite DBs, caches, logs) -- lives OUTSIDE every git worktree/checkout on purpose. This repo runs many worktrees ... |
| `OUTPUT_DIR` | L1847 | yes | Directory for generated reports. Defaults to <LOCAL_DATA_ROOT>/output when unset. |
| `PROMPT_CACHE_DIR` | L4369 | yes | Directory for the signed-version disk cache. Each prompt ID gets a sub-directory; up to PROMPT_CACHE_KEEP_VERSIONS signed .json files are kept per ID for offline rollback. |
| `SYNC_WATCHLIST_FILES` | L1860 | yes | Colon-separated paths (shell PATH convention) to additional plain-text watchlist files (one ticker per line, '#' = comment) consumed by data.robinhood_client.discover_universe(). Missing files are ... |
| `WATCH_RULES_FILE` | L3927 | yes | Path to watch_rules.yaml. Defines per-symbol ntfy push-alert rules (action_change, conviction_above, conviction_below). Missing file = no rules active (silent no-op). |

## 4. `SECRET_KEYS` sanity check

**Phantom entries** (in `SECRET_KEYS` but not a real `model_fields` name): **2**

- `NTFY_TOPIC`
- `PROMPT_REGISTRY_CREDENTIALS`

### Credential-shaped name sweep — pattern `TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL|MFA` (case-insensitive)

- matches already in `SECRET_KEYS`: **22**
- matches NOT in `SECRET_KEYS`: **1**
- of those, genuinely credential-shaped (`str` / `Optional[str]`): **0**

A field typed `int` / `float` / `bool` cannot hold secret material regardless of a name
match, so those are listed as filtered false positives rather than gaps.

| Field | Type | In `ALLOWED_KEYS` | Verdict |
|---|---|---|---|
| `EDGAR_FULLTEXT_CHUNK_TOKENS` | `int` | yes | false positive (non-string type) |

### Supplementary wider sweep — pattern `TOTP|PASSPHRASE|PRIVATE_KEY|WEBHOOK|CLIENT_ID|CLIENT_SECRET|_PW\b|AUTH`

Not requested by the brief, run because the primary pattern misses several credential
shapes by construction. Extra string-shaped, unprotected matches: **1**

| Field | Type | In `ALLOWED_KEYS` |
|---|---|---|
| `MCP_OAUTH_ISSUER_URL` | `Optional[str]` | yes |

## 5. Hand-set-only write-gate flags

Fields whose `settings.py` comment or `Field(description=...)` claims they are
deliberately never GUI-writable, cross-referenced against **actual** current
`ALLOWED_KEYS` membership.

- fields carrying such a marker: **11**
- markers **contradicted** by current `ALLOWED_KEYS` membership: **0**

| Field | Marker site(s) | In `ALLOWED_KEYS` now | In `SECRET_KEYS` | Claim holds |
|---|---|---|---|---|
| `FMP_API_KEY` | `settings.py:809` | no | yes | yes |
| `FOLLOW_API_TOKEN` | `settings.py:447` | no | yes | yes |
| `JULES_API_KEY` | `settings.py:617` | no | yes | yes |
| `MCP_HTTP_BEARER_TOKEN` | `settings.py:461` | no | yes | yes |
| `MCP_OAUTH_PASSWORD` | `settings.py:509` | no | yes | yes |
| `ORCHESTRATOR_DAEMON_TOKEN` | `settings.py:428` | no | yes | yes |
| `PROMPT_REGISTRY_PUBLISH_TOKEN` | `settings.py:4334` | no | yes | yes |
| `PROMPT_REGISTRY_SIGNING_KEY` | `settings.py:4342` | no | yes | yes |
| `PROMPT_REGISTRY_TOKEN` | `settings.py:4326` | no | yes | yes |
| `PROMPT_REGISTRY_URL` | `settings.py:4318` | no | yes | yes |
| `STATE_API_TOKEN` | `settings.py:420` | no | yes | yes |

## 6. Live-write endpoint inventory — `api/pilots_api.py`

- `PUT`/`POST`/`PATCH`/`DELETE` routes total: **83**
- routes that mutate a setting: **22**

Three *distinct* mutation mechanisms exist — a liveness model that only considers
"this process's singleton" would miss two of them:

| Mechanism | Routes | Effect |
|---|---|---|
| `.env` write via `env_io.write_*` | 22 | durable; takes effect on the **next** process launch |
| in-process `setattr(settings, ...)` | 0 | patches THIS process's singleton only |
| push to the daemon via `daemon_client.set_*` | 1 | HTTP call into a **separately running** daemon process |

Routes declaring an `applies` value in their response: **6** of 22.

Resolution is AST-based and follows one level of indirection: a handler that only calls a
module-level helper which itself calls `env_io.write_*` (or builds the response carrying
`applies`) is still attributed correctly. `applies` values are resolved through `Constant`,
`IfExp`, and locally-bound `Name` expressions — a Constant-only check reports
`(none)` for most of this table.

| Route | Method | Handler | Line | `.env` | `setattr` | daemon push | `applies` claims |
|---|---|---|---|---|---|---|---|
| `/observability/macro-gate` | PUT | `put_macro_gate` | 1900 | yes | no | no | `next_daemon_restart` |
| `/llm/setting` | PUT | `set_llm_setting` | 3098 | yes | no | no | `immediately`, `next_daemon_restart` |
| `/automation/schedule/interval` | PUT | `set_automation_interval` | 3731 | yes | no | yes | `immediately`, `next_daemon_restart` |
| `/strategy/modules` | PUT | `set_strategy_modules` | 3814 | yes | no | no | `next_daemon_restart` |
| `/automation/execution-mode` | PUT | `update_execution_mode` | 3901 | yes | no | no | `next_daemon_restart` |
| `/settings/tunables` | PUT | `put_settings_tunables` | 4336 | yes | no | no | _(none)_ |
| `/settings/tunables` | PATCH | `put_settings_tunables` | 4336 | yes | no | no | _(none)_ |
| `/settings/sentiment` | PUT | `put_settings_sentiment` | 4988 | yes | no | no | _(none)_ |
| `/settings/sentiment` | PATCH | `put_settings_sentiment` | 4988 | yes | no | no | _(none)_ |
| `/settings/sector-selection` | PUT | `put_settings_sector_selection` | 5013 | yes | no | no | _(none)_ |
| `/settings/sector-selection` | PATCH | `put_settings_sector_selection` | 5013 | yes | no | no | _(none)_ |
| `/settings/cache-long-short` | PUT | `put_settings_cache_long_short` | 5038 | yes | no | no | _(none)_ |
| `/settings/cache-long-short` | PATCH | `put_settings_cache_long_short` | 5038 | yes | no | no | _(none)_ |
| `/settings/paper-broker` | PUT | `put_settings_paper_broker` | 5060 | yes | no | no | _(none)_ |
| `/settings/paper-broker` | PATCH | `put_settings_paper_broker` | 5060 | yes | no | no | _(none)_ |
| `/settings/feature-flags` | PUT | `put_feature_flags_settings` | 5124 | yes | no | no | _(none)_ |
| `/settings/feature-flags` | PATCH | `put_feature_flags_settings` | 5124 | yes | no | no | _(none)_ |
| `/settings/fmp` | PUT | `put_settings_fmp` | 5153 | yes | no | no | _(none)_ |
| `/settings/fmp` | PATCH | `put_settings_fmp` | 5153 | yes | no | no | _(none)_ |
| `/settings/etf-transmission` | PUT | `put_settings_etf_transmission` | 5178 | yes | no | no | _(none)_ |
| `/settings/etf-transmission` | PATCH | `put_settings_etf_transmission` | 5178 | yes | no | no | _(none)_ |
| `/prompts/pin` | PUT | `put_prompts_pin` | 5449 | yes | no | no | `next_daemon_restart` |

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

Scope: **429** production `.py` files (excludes `tests/`, `test_*.py`, `conftest.py`, `.venv/`, `webapp/`, `node_modules/`).

Files that could not be parsed: **0**

The singleton is bound under **22** distinct local names
across the tree, which is why this is an AST pass and not a grep:

```
_S.settings, _bl_settings, _dsr_settings, _gravity_settings, _live_settings, _mt_settings, _oos_gate_settings, _rh_settings, _s, _s2, _sett, _settings, _settings.settings, _settings93, _settings93_ro, _settings_local, _settings_mod.settings, _settings_singleton, _wf_settings, platform_settings, settings, settings_module.settings
```

| Form | Total reads | Distinct fields reached |
|---|---|---|
| (a) `settings.KEY` | 765 | 245 |
| (b) `getattr(settings, "KEY", default)` | 368 | 206 |
| (c) `getattr(settings, <var>)` (dynamic) | 17 sites | n/a — key not statically known |
| (d) `os.environ` / `os.getenv("KEY")` | 25 | 18 |

Fields reached by at least one form: **417** of 426.

### Fields with NO statically-attributable read — **9**

**These are not necessarily dead.** A field whose name is passed as a *string literal* to a
factory that then does a dynamic `getattr` is read at runtime while being invisible to every
form above. The name-literal column is the evidence: a non-empty value means the key is
referenced by name somewhere and is probably read dynamically.

| Field | Name-literal sites | Verdict |
|---|---|---|
| `EDGAR_FULLTEXT_CHUNK_TOKENS` | `api/pilots_api.py:4755` | likely read dynamically |
| `EDGAR_FULLTEXT_FORMS` | `api/pilots_api.py:4754` | likely read dynamically |
| `ETF_HOLDINGS_TICKERS` | `api/pilots_api.py:4924`, `gui/panels/settings_manager.py:126` | likely read dynamically |
| `FMP_ECON_INDICATORS` | `api/pilots_api.py:4896`, `gui/panels/settings_manager.py:162` | likely read dynamically |
| `OPTIONS_EARNINGS_CRUSH_ENABLED` | _none_ | no read and no name reference found |
| `PROMPT_MAX_CHARS` | _none_ | no read and no name reference found |
| `PROMPT_REGISTRY_REFRESH_SECONDS` | `Gravity AI Review Suite.py:11070` | likely read dynamically |
| `SENTIMENT_PIT_MIN_MONTHS` | _none_ | no read and no name reference found |
| `UNIVERSE_SYNC_ENABLED` | `api/data_api.py:1427`, `pilots/feature_flags.py:49` | likely read dynamically |

### Fields reachable ONLY via form (b) or (d), never via (a) — **172**

These are exactly the keys an attribute-only static analysis would miss entirely.

| Field | Reached via | (b) count | (d) count |
|---|---|---|---|
| `ADVISORY_MAX_CONCURRENCY` | b | 2 | 0 |
| `ADVISORY_REUSE_PIPELINE_COMPUTE` | b | 1 | 0 |
| `AI_CHAT_DEFAULT_MODEL` | b | 1 | 0 |
| `AI_CHAT_DEFAULT_PROVIDER` | b | 2 | 0 |
| `AI_GENERATION_API_ENABLED` | b | 1 | 0 |
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
| `BROKER_BACKEND` | b | 2 | 0 |
| `CIRCUIT_BREAKER_LOSS_VELOCITY_WINDOW_MINS` | b | 1 | 0 |
| `CIRCUIT_BREAKER_OFI_THRESHOLD` | b | 1 | 0 |
| `CIRCUIT_BREAKER_VOLATILITY_Z_THRESHOLD` | b | 1 | 0 |
| `CIRCUIT_BREAKER_VPIN_THRESHOLD` | b | 1 | 0 |
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
| `FMP_ECON_CALENDAR_ENABLED` | b | 1 | 0 |
| `FMP_FALLBACK_ENABLED` | b | 2 | 0 |
| `FMP_FUNDAMENTALS_ENABLED` | b | 1 | 0 |
| `FMP_INSIDER_ENABLED` | b | 1 | 0 |
| `FMP_INSIDER_MIN_LAG_DAYS` | b | 1 | 0 |
| `FMP_INSIDER_REFRESH_DAYS` | b | 1 | 0 |
| `FMP_MACRO_ENABLED` | b | 1 | 0 |
| `FMP_MAX_RETRIES` | b | 1 | 0 |
| `FMP_MAX_SECONDS_PER_CYCLE` | b | 4 | 0 |
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
| `FMP_SCREENER_ENABLED` | b | 4 | 0 |
| `FMP_SECTOR_SNAPSHOT_ENABLED` | b | 1 | 0 |
| `FMP_TIMEOUT_SECONDS` | b | 1 | 0 |
| `FMP_UNIVERSE_ENABLED` | b | 1 | 0 |
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
| `GCLOUD_BIN` | d | 0 | 1 |
| `GDELT_COOLDOWN_SECONDS` | b | 1 | 0 |
| `GDELT_COOLDOWN_THRESHOLD` | b | 1 | 0 |
| `GDELT_MAX_RETRIES` | b | 1 | 0 |
| `GDELT_MIN_REQUEST_INTERVAL_SECONDS` | b | 1 | 0 |
| `GDELT_RETRY_BACKOFF_SECONDS` | b | 1 | 0 |
| `GEMINI_CHAT_MODEL` | b | 2 | 0 |
| `GEMINI_LIVE_CHAT_ENABLED` | b | 1 | 0 |
| `GEMINI_LIVE_CHAT_MODEL` | b | 1 | 0 |
| `GEMINI_LIVE_VOICE_NAME` | b | 1 | 0 |
| `GRAVITY_AI_RUNNER_ENABLED` | b | 4 | 0 |
| `HMM_INFLATION_FEATURE_ENABLED` | b | 2 | 0 |
| `HMM_RISK_OFF_AGREEMENT_THRESHOLD` | b | 1 | 0 |
| `HMM_RISK_ON_DOWNGRADE_THRESHOLD` | b | 1 | 0 |
| `KILLSWITCH_SAHM_THRESHOLD_AGREED` | b | 1 | 0 |
| `KILLSWITCH_VIX_THRESHOLD_AGREED` | b | 1 | 0 |
| `LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED` | b | 2 | 0 |
| `LLM_COMMENTARY_CACHE_PATH` | b | 1 | 0 |
| `LLM_STATUS_MAX_AGE_HOURS` | b | 1 | 0 |
| `LOCAL_LLM_API_KEY` | b | 1 | 0 |
| `LOCAL_LLM_BASE_URL` | b | 4 | 0 |
| `LOCAL_LLM_MODEL` | b | 2 | 0 |
| `MARKET_DATA_WS_ENABLED` | b | 2 | 0 |
| `MARKET_DATA_WS_RECONNECT_BASE_SECONDS` | b | 1 | 0 |
| `MARKET_DATA_WS_RECONNECT_MAX_SECONDS` | b | 1 | 0 |
| `MARKET_DATA_WS_STALE_SECONDS` | b | 1 | 0 |
| `MARKET_DATA_WS_SYMBOLS` | b | 1 | 0 |
| `MAX_CONCURRENT_OPTION_POSITIONS` | b | 1 | 0 |
| `MAX_OPTION_NOTIONAL_PER_TRADE` | b | 1 | 0 |
| `META_LABELING_ENABLED` | b | 1 | 0 |
| `MULTI_BROKER_GATEWAY_ENABLED` | b | 1 | 0 |
| `NO_VENV_REEXEC` | d | 0 | 1 |
| `OPAL_RESEARCH_MODEL` | b | 2 | 0 |
| `OPAL_RESEARCH_PROVIDER` | b | 2 | 0 |
| `OPAL_RESEARCH_TIMEOUT_SECONDS` | b | 1 | 0 |
| `OPTIONS_0DTE_ENABLED` | b | 5 | 0 |
| `OPTIONS_0DTE_HARD_EXIT_TIME` | b | 4 | 0 |
| `OPTIONS_0DTE_PROFIT_TARGET_PCT` | b | 3 | 0 |
| `OPTIONS_0DTE_STOP_LOSS_PCT` | b | 3 | 0 |
| `OPTIONS_ALERT_WEBHOOK_URL` | b | 5 | 0 |
| `OPTIONS_AUTO_EXIT_ENABLED` | b | 4 | 0 |
| `OPTIONS_DELTA_HEDGE_BAND_SPY_SHARES` | b | 3 | 0 |
| `OPTIONS_DELTA_HEDGE_ENABLED` | b | 2 | 0 |
| `OPTIONS_EARNINGS_MIN_EDGE` | b | 1 | 0 |
| `OPTIONS_EARNINGS_WING_MULTIPLIER` | b | 1 | 0 |
| `OPTIONS_GEX_SEARCH_RANGE_PCT` | b | 1 | 0 |
| `OPTIONS_MANAGE_DTE_THRESHOLD` | b | 1 | 0 |
| `OPTIONS_MATRIX_ENABLED` | b | 1 | 0 |
| `OPTIONS_META_LABELER_ENABLED` | b | 3 | 0 |
| `OPTIONS_PROFIT_TARGET_PCT` | b | 1 | 0 |
| `OPTIONS_RISK_FREE_RATE` | b | 10 | 0 |
| `OPTIONS_SOR_LEGGING_LATENCY_SECONDS` | b | 1 | 0 |
| `OPTIONS_STOP_LOSS_MULTIPLE` | b | 1 | 0 |
| `OPTIONS_VPIN_TOXICITY_THRESHOLD` | b | 3 | 0 |
| `PAIRS_SNAPSHOT_ENABLED` | b | 1 | 0 |
| `PAIRS_SNAPSHOT_MAX_PAIRS` | b | 1 | 0 |
| `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED` | b | 2 | 0 |
| `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED` | b | 1 | 0 |
| `QDRANT_COLLECTION` | d | 0 | 1 |
| `QDRANT_URL` | d | 0 | 1 |
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
| `VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED` | b | 2 | 0 |
| `VALIDATION_HARNESS_OOS_GATE_ENABLED` | b | 1 | 0 |
| `WATCHLIST` | b+d | 1 | 5 |

### Dynamic `getattr` sites (form c) — **17**

The key is not a literal, so no static analysis can attribute these to a field name.

| Site | Expression |
|---|---|
| `Gravity AI Review Suite.py:2733` | `getattr(_rh_settings, _MISSING_ATTR, None)` |
| `api/_redact.py:38` | `getattr(settings, k, None)` |
| `api/auth.py:150` | `getattr(settings, token_setting_name, None)` |
| `api/data_api.py:177` | `getattr(settings, flag_name, False)` |
| `api/pilots_api.py:3167` | `getattr(settings, body.key)` |
| `api/pilots_api.py:4273` | `getattr(settings, key, None)` |
| `api/pilots_api.py:4378` | `getattr(settings, key, None)` |
| `data/brokerage_credentials.py:125` | `getattr(_settings, k, None)` |
| `data/robinhood_portfolio.py:84` | `getattr(_settings, name, None)` |
| `gui/panels/ai_control_center.py:164` | `getattr(settings, tkey, False)` |
| `gui/panels/ai_control_center.py:189` | `getattr(settings, sel_key, 'none')` |
| `gui/panels/settings_manager.py:190` | `getattr(settings, key, fallback)` |
| `gui/panels/settings_manager.py:207` | `getattr(settings, key, '')` |
| `gui/panels/settings_manager.py:275` | `getattr(settings, key, [])` |
| `llm/status_store.py:212` | `getattr(settings, attr, None)` |
| `runtime_flags_writer.py:774` | `getattr(settings_module.settings, key, None)` |
| `runtime_flags_writer.py:783` | `getattr(settings_module.settings, key, None)` |

### Fields read via `os.environ` (form d) — 18 field(s)

`.env` is loaded into the `Settings` model directly by pydantic-settings; it is only
copied into the real `os.environ` when something calls `load_dotenv()`. A field read
this way therefore reads a *different source* than `settings.KEY` does — see CLAUDE.md's
"Credential reads MUST go through `settings.X`" convention for the class of bug this causes.

| Field | Reads | Also read via (a) |
|---|---|---|
| `WATCHLIST` | 5 | **no** |
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
| `GCLOUD_BIN` | 1 | **no** |
| `LOG_LEVEL` | 1 | yes |
| `NO_VENV_REEXEC` | 1 | **no** |
| `PROMPT_REGISTRY_SIGNING_KEY` | 1 | yes |
| `QDRANT_COLLECTION` | 1 | **no** |
| `QDRANT_URL` | 1 | **no** |

---

_Regenerate: `python3 scripts/measure_settings_census.py --write`_
