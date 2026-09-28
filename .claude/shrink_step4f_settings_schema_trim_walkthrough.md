# Step 4f walkthrough: settings and schema trim

Branch `settings-schema-trim`, off `origin/main` after #1072 (4c, Follow-a-Pilot) merged.
Last PR of the step-4 archive (`.claude/shrink_step4_archive_implementation_plan.md` §4f).

## How the retire list was built

I recomputed the list instead of reusing older lists. A scanner (scratchpad
`s4f/scan.py`) found every `Settings` field and every reference to it outside
`legacy/`, `tests/`, `docs/`, `.claude/`. References in the registries don't
count as readers: `settings.py`, `settings_keysets.py`, `shared/env_io.py`,
`pilots/feature_flags.py`, `pilots/settings_domains.py`, `shared/help_content.py`,
`webapp/src/api/mock.ts`, `webapp/src/help/helpContent.ts`, `.env.example`,
and the census/liveness scripts. The rest of `api/pilots_api.py` was checked by
hand. A reference that only sits in a `_TUNABLE_GROUPS`/`_FMP_GROUPS` editor row
is registry-only, not a reader. I cross-checked the result against a fresh
`scripts/settings_liveness.py` run (its `no_op` bucket).

## Retired fields (63)

- **Options-desk automation** (the whole "Options Desk Automation" tunables group):
  `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED`, `MAX_OPTION_NOTIONAL_PER_TRADE`,
  `MAX_CONCURRENT_OPTION_POSITIONS`, `OPTIONS_AUTO_EXIT_ENABLED`,
  `OPTIONS_PROFIT_TARGET_PCT`, `OPTIONS_STOP_LOSS_MULTIPLE`,
  `OPTIONS_MANAGE_DTE_THRESHOLD`, `OPTIONS_DELTA_HEDGE_ENABLED`,
  `OPTIONS_DELTA_HEDGE_BAND_SPY_SHARES`, `OPTIONS_0DTE_ENABLED`,
  `OPTIONS_0DTE_PROFIT_TARGET_PCT`, `OPTIONS_0DTE_STOP_LOSS_PCT`,
  `OPTIONS_0DTE_HARD_EXIT_TIME`.
- **Other options settings:** `OPTIONS_VRP_THRESHOLD`, `OPTIONS_MATRIX_ENABLED`,
  `OPTIONS_TRUE_IVR_ENABLED`, `OPTIONS_META_LABELER_ENABLED`,
  `OPTIONS_EARNINGS_CRUSH_ENABLED`, `OPTIONS_EARNINGS_MIN_EDGE`,
  `OPTIONS_EARNINGS_WING_MULTIPLIER`, `OPTIONS_ALERT_WEBHOOK_URL`,
  `OPTIONS_DRL_RISK_AVERSION_GAMMA`, `OPTIONS_VPIN_TOXICITY_THRESHOLD`,
  `OPTIONS_SOR_LEGGING_LATENCY_SECONDS`, `OPTIONS_LOB_DEFAULT_MARKET_ORDER_RATE`,
  `OPTIONS_GEX_SEARCH_RANGE_PCT`, `OPTIONS_COPULA_ZSCORE_ENTRY_THRESHOLD`,
  `FORECAST_BACKFILL_VRP_PROXY_ENABLED`, `FMP_OPTIONS_HEALTH_ENABLED`,
  `FMP_OPTIONS_CONTEXT_ENABLED`, `MULTI_BROKER_GATEWAY_ENABLED`.
- **FIX, circuit breaker and streaming:** `FIX_MOCK_VENUES_ENABLED`,
  `FIX_VENUES_CONFIG_PATH`, `FIX_GATEWAY_ENABLED`, `FIX_HEARTBEAT_INTERVAL_SECONDS`,
  `CIRCUIT_BREAKER_{VOLATILITY_Z,VPIN,OFI}_THRESHOLD`,
  `CIRCUIT_BREAKER_LOSS_VELOCITY_WINDOW_MINS`, `CIRCUIT_BREAKER_ENABLED`,
  `CIRCUIT_BREAKER_REFERENCE_SYMBOL`, `OFI_SHIELD_ENABLED` (was a DANGEROUS_KEY),
  `WS_RISK_STREAM_INTERVAL_SECONDS`.
- **ETF (all 19):** `ETF_TRANSMISSION_{ENABLED, SIZING_ENABLED, MAX_DERATE,
  OWNERSHIP_REFERENCE, MIN_MULTIPLIER, WRAPPERS, EXCLUDED_SYMBOLS, WINDOW_DAYS,
  MIN_OBS, PORTFOLIO_ENABLED, COV_INFLATION, COV_WINDOW_DAYS}` and
  `ETF_HOLDINGS_{ENABLED, MARKET_PROXY, TICKERS, REFRESH_DAYS, ISSUER_CSV_ENABLED,
  MAX_SECONDS_PER_CYCLE, CIRCUIT_BREAKER_THRESHOLD}`.
- **Follow:** `FOLLOW_MIN_AMOUNT`. I did this after rebasing onto #1072.

Every one of these was also removed from each registry that named it:
- `ALLOWED_KEYS`, the `_JSON_KEYS` list entries, and the `SECRET_KEYS` exception (see below)
- `SAFETY_CRITICAL_KEY_REASONS` (`OFI_SHIELD_ENABLED`)
- `feature_flags` (`MULTI_BROKER_GATEWAY_ENABLED`)
- `settings_domains`: overrides, the "Options Desk" override, and the now-empty "ETF Transmission" domain (14 → 13 domains)
- `_TUNABLE_GROUPS`:
  - "Options Desk Automation" is removed
  - `OPTIONS_VRP_THRESHOLD` is gone from "Regime Model"
  - "Options & Pairs Snapshots" is renamed "Pairs Snapshot"
- `_FMP_GROUPS`, `FmpSettings.tsx` labels, `mock.ts`, `help_content`, `.env.example`

**The one exception is `OPTIONS_ALERT_WEBHOOK_URL`, which stays in
`env_io.SECRET_KEYS` (both entries).** The operator's real `.env` still sets
it. `env_io.read_settings()` returns every `.env` key and masks only
`SECRET_KEYS`, so removing it would have shown the webhook URL in cleartext.
`tests/test_settings.py::TestStep4fRetiredKeysAreHarmless::test_retired_webhook_url_is_still_masked`
pins this.

## Kept (live readers)

| Field | Reader |
|---|---|
| `OPTIONS_RISK_FREE_RATE` | `data/paper_account_store.py:143,170`, `data/option_symbols.py:64,72` |
| `PAPER_OPTION_MARK_CACHE_SECONDS` | `data/paper_account_store.py:68,76` |
| `DAILY_LOSS_LIMIT_PCT` | `execution/risk_gate.py` |
| `PILOTS_TOP_N` | `pilots/scoring.py` |
| `FOLLOW_API_TOKEN` | `api/auth.py`, `api/pilots_api.py`, webapp |
| `PAPER_BROKER_WRITES_ENABLED` | Still gates `/pilots/paper-broker/{reset,order}`. Its description was updated to stop listing the archived options routes. |

**Noticed but left alone (out of scope):**
- `GOOGLE_TRENDS_WINDOW_DAYS` and `GOOGLE_TRENDS_OVERLAP_DAYS` have no reader, but Google Trends is not archived.
- `PROMPT_MAX_CHARS` and `SENTIMENT_PIT_MIN_MONTHS` are the other `no_op` fields.
- `DASHBOARD_REFRESH_SECONDS` is read only by the frozen legacy Streamlit app.

## `.env` / runtime store still holding retired keys

The operator's real `.env` sets 60 of the 63 retired keys, and
`~/.stockpy_local/output/runtime_flags.json` holds `OFI_SHIELD_ENABLED`. Neither
file was edited. `TestStep4fRetiredKeysAreHarmless` proves both are harmless:
- Retired keys in an env file or in the real environment are ignored (`extra="ignore"`).
- `runtime_flags.apply_overrides` reports them as `skipped_unknown` while still applying real keys.

The live import already logs
`runtime_flags: ignoring unknown key(s) ['OFI_SHIELD_ENABLED']`.

## COLUMN_SCHEMA trim (116 → 108)

Removed `True_IVR`, `VRP`, `Realized_Vol_Rank`, `Option Strategy`,
`ETF_Ownership_Pct`, `ETF_Comovement_R2`, `ETF_Primary_Wrapper` and
`ETF_Transmission_Multiplier`. Everything that wrote or read them was removed in
the same PR, so Pandera still validates and no step indexes a missing column:
- `_prefill_etf_transmission_columns`, `_ETF_TRANSMISSION_COLUMNS` and their call
- the three dead entries in `_TREND_VOL_COLUMN_MAP`
- `"Option Strategy"` in the strategy loop's default, assign and map-back lists
- `"Option Strategy"`/`"True_IVR"` in `StateSnapshotStep`'s stdout payload columns (the ordering trap)
- `StrategyEngine`'s `"Option Strategy"` and `ETF_Transmission_Multiplier_Applied` output keys

No webapp type or state-snapshot key read any of these. 4d had already removed
the Observability ETF section, and `test_state_snapshot_parity.py` had no pins.
The MCP `validate_order_compliance` test fixture no longer inserts
`VRP`/`True_IVR` into `DailySignals`.

Tests that pass:
- `tests/test_config.py`: pinned count 108, a new `test_dead_options_and_etf_columns_are_trimmed`
- `tests/test_production_steps_portfolio_gross_cap.py::TestEtfColumnsTrimmedFromSchema`: a `DashboardSchema` built from the trimmed schema validates
- `tests/test_orchestrator_e2e.py`: the payload is still valid JSON
- `tests/test_main_orchestrator.py`
- `tests/test_production_steps_columns_contract.py`

## `etf_transmission_multiplier` removal: sizing is byte-identical

Before editing `sizing/position_sizer.py` I captured `size_position()` output
for 500 cases (scratchpad `s4f/sizer_golden.py`):
- the 200-case seeded grid from `tests/test_position_sizer.py` (seed 20260727)
- 300 more cases with NaN, inf, -0.0, subnormals, negative weights and escalation histories

I recorded the `repr` of every `SizingDecision` field and compared after the
edit: **500/500 identical, 0 diffs**. That is expected, because `x * 1.0 == x`
exactly in IEEE-754.

In `tests/test_position_sizer.py`, section 7 (the ETF no-op, composition,
guardrail and NaN-safety classes) is replaced by `TestStepThreeCompositionGrid`:
- It runs the same seeded grid against an independent `clamp_with_binding` reconstruction, now with exact `==`.
- It checks that the kwarg is gone: `TypeError`, and no `SizingDecision` attribute.

The ETF wiring tests in `tests/test_strategy_engine.py` became one
kwarg-is-gone test. The Hypothesis property test in
`tests/test_sizing_properties.py` dropped its ETF dimension.

## Leftovers from 4c / 4e

- **`Pilot.followable`** is removed from `pilots/catalog.py`. Every remaining Pilot used the default `True`. `test_non_followable_pilots` became `test_followable_field_removed`.
- **`config.get_headers()` / `get_rename_mapping()`** are removed. `get_internal_keys()` is kept (it has live readers: `processing_engine.py` and tests).
- **Stale Sheets comments** are fixed in `config.py` (module docstring, header doc, ADVISORY METADATA block, sellRange comment) and in `pipeline/production_steps.py` (the advisory-metadata block, which the brief called "~2049", and three other "Sheet" mentions).
- **`etf_holdings` table:** `HistoricalStore` no longer creates it, and its accessors (`save_etf_holdings`, `get_etf_holdings`, `latest_etf_holdings_date`) are deleted. `_nan_to_null` is kept because the FMP writers use it. There is no migration, and nothing drops the table. `tests/test_historical_store.py::TestEtfHoldingsTableRetired` checks two things:
  - A fresh DB has no `etf_holdings` table.
  - A pre-existing table and its rows survive `HistoricalStore()` construction untouched.
- **`legacy/tests/test_gui_env_io_etf_transmission_keys.py`** was moved there with `git mv`, because every key it pinned is retired.

## Artifacts

- `docs/settings_liveness.json`: 325 live_safe / 85 restart_required / 4 no_op of 414.
- `docs/settings_field_census.{json,md}`: regenerated.
- CLI manifest: no target changed, so not regenerated.

## Webapp

- Settings Reference: the mock fixture now uses the real no_op `PROMPT_MAX_CHARS`. No real *boolean* no_op field remains, so the backend tests (`tests/test_settings_reference.py`) inject a fake liveness artifact to exercise the no_op-exclusion path, and the vitest inline fixture uses a synthetic key.
- The domain count reads 13, and the stale "464" field count was dropped from the help copy.
- Checked in the browser against the mock backend:
  - `/settings/reference` renders 13 domains.
  - `/settings/tunables` shows "Pairs Snapshot" and no "Options Desk Automation".
  - `/settings/fmp` has no options toggles.

## Verification

- Full offline suite, final run: **11473 passed, 18 skipped, 0 failed**. An earlier run had 2 intermittent `BrokenPipeError` failures in `tests/test_forecast_backfill_job.py` (the worker subprocess under `-n auto` load). They passed in isolation (20/20) and in the final run. The known LSTM flake did not show up.
- ruff F821/F822/F823/E9: clean.
- Webapp `tsc`: clean.
- Webapp vitest: 1779/1779 on the second run. The first run had 1 failure in `SettingsManager.test.tsx`, which passes alone (15/15) and passed on the full rerun, so it is a timing flake.
- `tests/test_options_archive_import_smoke.py`: passes.

## Docs

- CLAUDE.md/AGENTS.md (kept identical):
  - Added one 4f bullet.
  - Collapsed to one-line "archived; settings retired in 4f" pointers: the options-lifecycle header plus its items 1-6 (paper-store items 7-10 kept), options ML/safety gates, Phases 31-36 (both passes), fabricated SPY spot, and multi-leg automated execution.
  - Updated the past-tense wording in the 3d, 4c, 4d, Settings Reference, sellRange and explore/execute bullets.
- Banners: `docs/architecture/signal-engines.md`, `docs/architecture/data-layer.md`.
- `legacy/README.md`: step-4f section.
