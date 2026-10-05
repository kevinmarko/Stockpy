# Shrink step 4a: options unwire (walkthrough)

Branch `options-unwire`, based on origin/main @ 96f81c44. Edits only; no module was moved.

## Goal

After this PR, no production `.py` file outside `tests/` and `legacy/` imports any
module in the step-4 options archive set (the inventory's 25 orphans, 11 cluster
modules and 14 still-wired modules). PR 4b can then `git mv` them.

## What changed

| Area | Change |
|---|---|
| `investyo_mcp_server.py` | `get_options_directive`, `analyze_options_chain`, `scan_0dte_signals` are retired stubs. They stay registered, return a fixed retired message (every analytics field `None`) and never raise. `validate_order_compliance` drops its VRP regime half; the Kelly-cap check no longer depends on any options import. `_MacroProxy` (only used by the removed tools) is gone. |
| `data/option_symbols.py` (new) | `parse_option_symbol` and `black_scholes_price`, copied unchanged from `pilots/options_risk.py`. |
| `data/paper_account_store.py`, `execution/fmp_paper_broker.py` | Use `data/option_symbols.py` instead of `pilots.options_risk`. Option-chain fetching and the marking ladder stay in the store, so `conftest.py::_stub_paper_marking_network_in_tests` needed no change. |
| `pilots/paper_broker.py` | Deleted `execute_paper_order`, `get_strategy_options_candidates`, `execute_strategy_options`, `get_portfolio_greeks`, `manage_position_exits`, `execute_roll`. No route or production code called them. |
| `scripts/export_notebooklm.py` | The portfolio document drops its Greeks section. The account summary and positions table and their honest-degrade paths are unchanged. The filename `02_portfolio_and_greeks.md` is kept for stability. |
| `validation/harness.py` | Removed `run_options_validation`, `_run_options_bulk`, `_print_options_bulk_summary`, `_fail_reason` and the `--strategies`/`--ticker`/`--workers`/`--json` CLI. `--strategy` is now required. The equity harness, including the generic options-selling stress gate, is untouched. |
| `scripts/refresh_validations.py` | Removed 13 registry entries and their adapters. Also removed `_build_options_spread_adapter`, `_build_ungateable_adapter`, `PAPER_BROKER_OPTIONS_STRATEGIES`, `_resolve_options_selling_stress_fn`, and the two no-op `_REPLAY_EXCLUDED_MODULES` entries. 20 strategies remain. |
| `scripts/build_command_manifest.py`, `cli_introspect/command_manifest.json`, `pilots/commands.py` | The two options registry fetchers and keys are gone, and the manifest is regenerated. `GET /commands` no longer returns the two keys. |
| `completions/investyo.{bash,zsh}` | Regenerated from the new manifest. |
| `ml/forecast_backfill.py` | Removed the VRP-proxy feature columns and the proxy-signal branch. The `FORECAST_BACKFILL_VRP_PROXY_ENABLED` field stays until 4f; it is now a `no_op`. |
| `llm/__init__.py` | Dropped the three research-copilot lazy names. |
| `broker_live_execution_mcp.py` | Removed the `MultiBrokerGateway` branch. `pilots/feature_flags.py` now marks `MULTI_BROKER_GATEWAY_ENABLED` as retired. |
| `Gravity AI Review Suite.py` | Removed `run_ivr_vrp_audit` (step 19) and `run_options_matrix_integrity_audit` (step 38), plus their call sites. |
| `pilots/catalog.py` | Removed the 10 options Pilots and `OPTIONS_DIRECTIVE_STRATEGY_TO_PILOT_ID`. |
| `execution/options_paper_executor.py` | Keeps a local copy of the directive-to-id map, so it no longer imports the catalog. It is archived in 4b. |
| `pilots/strategy_report_card.py` | `LEGACY_STRATEGY_ID_ALIASES` is unchanged. A new `RETIRED_OPTIONS_PILOT_NAMES` map renders historical trades under a removed id as `category="Retired"` non-Pilot rows. Those rows carry the display name and a fully-null predicted side with reason `"retired options pilot (options desk removed 2026-09)"`. |
| webapp | `mock.ts` drops the two options Pilots, the two manifest registries, harness `--strategies`/`--workers`/`--json`, and the removed strategy names. Its report-card `iron-condor` row becomes a Retired row. `commandParse.ts`'s fallback strategy list is trimmed to the same 20 names. The Commands screen shows the options bulk button only when `validation.harness` still has `--strategies`, so an older manifest still works. `types.ts` documents the two registries as retired but still accepts them. |
| Settings artifacts | Regenerated `docs/settings_liveness.json` and `docs/settings_field_census.{json,md}`. `FORECAST_BACKFILL_VRP_PROXY_ENABLED` moved from live_safe to no_op. `MULTI_BROKER_GATEWAY_ENABLED` moved from live_safe to restart_required_via_name_literal_only; it is only referenced by name in `env_io`/`feature_flags`. |

## Decisions

- **`data/paper_option_marks.py` did not exist.** The plan named it, but the marking code lives inside `data/paper_account_store.py`. I created the small shared core module `data/option_symbols.py` and pointed both call sites at it, as the task allowed.
- **`options_paper_executor`'s catalog import:** I inlined the map into the executor, so it now imports nothing from the catalog. It still imports other archive-set modules (it moves in 4b), but no production code imports it.
- **Tests removed with their feature:**
  - `tests/test_investyo_mcp_options_analytics.py`, now covered by `TestRetiredOptionsTools`.
  - `tests/test_harness_bulk_cli.py`. Its equity case moved to the new `tests/test_harness_equity_cli.py`.
  - The three `tests/test_validation_*_registry.py` files for copula, vol_mispricing and vrp_premium_selling.

## Verification

- **Parity of the copied math** (`tests/test_option_symbols.py`):
  - `black_scholes_price` equals `calculate_black_scholes_greeks(...)["price"]` exactly (`==`) over a 20,580-point grid. The grid covers 7 spots (including 0 and negative), 4 strikes (including 0), 7 times (including 0, 1e-13 and 1e-12, the 0DTE guard), 7 sigmas (including 0, 1e-13 and NaN), 5 type spellings and 3 rates. The default-rate path also matches.
  - `parse_option_symbol` matches the original on 11 symbols, including malformed ones.
  - Pinned values keep guarding the math once the original is archived: intrinsic value at 0DTE and zero vol, and the textbook 10.4506/5.5735.
- Offline suite: `12986 passed, 1 failed, 15 skipped`. The one failure is `tests/test_lstm_attention_worker.py::TestFitPredictLstmAttention::test_architecture_matches_shape`, the known order-dependent flake. That file (6 passed) and its class (4 passed) pass on their own, and the file is untouched here.
- Ruff `F821,F822,F823,E9`: clean.
- Webapp: `tsc --noEmit` clean, and vitest 154 files / 1806 tests pass.
- AST import scan: 389 production files scanned (excluding tests/, legacy/ and the 50 archive-set files). The one remaining importer is `conftest.py:294` (`_isolate_execution_audit_db_in_tests` → `data.execution_audit_store`). It is test infrastructure, and the plan deletes it in the same PR as the move (4b).
- Blocked-import smoke: with all 50 archive-set modules set to `None` in `sys.modules`, 19 modules import cleanly. They are `main`, `main_orchestrator`, `investyo_mcp_server`, `broker_live_execution_mcp`, `api.pilots_api`, `api.data_api`, the paper store and broker, `pilots.paper_broker`/`catalog`/`strategy_report_card`/`commands`, `refresh_validations`, `build_command_manifest`, `export_notebooklm`, `validation.harness`, `ml.forecast_backfill`, `llm` and `data.option_symbols`. The MCP stubs and paper BS marking also work.

## Left for later PRs

- 4b: move the archive set and its tests, and delete `_isolate_execution_audit_db_in_tests`.
- 4f: retire `FORECAST_BACKFILL_VRP_PROXY_ENABLED`, `MULTI_BROKER_GATEWAY_ENABLED` and the other options settings.
- The webapp still carries the now-dormant `optionsStrategyRegistry` prop plumbing and `REGISTERED_OPTIONS_STRATEGIES` in `commandParse.ts`/`CommandFormBuilder.tsx`/`CommandPaletteModal.tsx`. It is unreachable because the manifest's harness has no `--strategies`, and it can go in a later cleanup.
