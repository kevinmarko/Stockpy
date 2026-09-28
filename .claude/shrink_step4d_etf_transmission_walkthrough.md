# Step 4d walkthrough: archive ETF volatility transmission

Branch `archive-etf-transmission`, cut from `origin/main`. Plan:
`shrink_step4_archive_implementation_plan.md` (section 4d) and the step-4
inventory (section 3, "ETF").

## Live config check

Read through `settings` (so `output/runtime_flags.json` overrides were applied),
printing booleans only:

| Flag | Live value |
|---|---|
| ETF_TRANSMISSION_ENABLED | False |
| ETF_TRANSMISSION_SIZING_ENABLED | False |
| ETF_TRANSMISSION_PORTFOLIO_ENABLED | False |
| ETF_HOLDINGS_ENABLED | False |
| ETF_HOLDINGS_ISSUER_CSV_ENABLED | False |

All off, so no live sizing changes. No symbol's Kelly target was being derated.

## Pipeline (`pipeline/production_steps.py`)

- `_apply_etf_transmission` and `_apply_etf_transmission_multiplier` are replaced
  by `_prefill_etf_transmission_columns`, which writes the four `ETF_*` schema
  columns as NaN. That is what the flag-off path always wrote, and Pandera's
  `DashboardSchema` still requires the columns until step 4f.
- `_build_etf_transmission_cov_matrix` is deleted.
- The `evaluate_security(..., etf_transmission_multiplier=row.get(...))` kwarg is
  dropped. The column was always NaN, which `size_position()` sanitizes to 1.0;
  omitting the kwarg gives the same 1.0 default.
- **Gross-cap trap.** The `MAX_PORTFOLIO_GROSS` cap shared a `try` with the ETF
  covariance build, so any ETF failure (an ImportError after the move, say) would
  have skipped the cap. It is now `_apply_portfolio_gross_cap(dashboard_df)`,
  called unconditionally in its own `try` with `cov_matrix=None`.

## Proof

`tests/test_production_steps_portfolio_gross_cap.py` (11 tests):
- With `risk.etf_transmission` and `data.etf_holdings` blocked via
  `sys.modules[...] = None`, a fixture with gross 3.0 is scaled to exactly
  `MAX_PORTFOLIO_GROSS` (2.0) with relative sizing preserved; a NaN weight
  doesn't loosen the cap; an under-cap book is left untouched.
- AST guards: `StrategyEvalStep.run()` calls the helper from a `try` whose body
  is only that call, not under any `if`, and references none of the archived
  names.
- Pandera: a full-schema frame fails `DashboardSchema` without the prefill and
  passes with it.
- Mutation check: adding `import risk.etf_transmission` to the helper makes 4 of
  these tests fail.

Byte identity (scratch harness, not committed). It loads `origin/main`'s
`production_steps.py` next to the new one, runs the old ETF functions plus the
old gross-cap `try` block against the new prefill plus the new block, all ETF
flags forced off:

| Fixture | Gross before | Gross after | sha256 (before = after) |
|---|---|---|---|
| over cap | 2.0000 | 2.0000 | `505ca6cb3b0f6569` |
| under cap | 0.3500 | 0.3500 | `b58440ada2808471` |
| NaN weight | 2.0000 | 2.0000 | `c53e31cb103d8a97` |

`evaluate_security` with `etf_transmission_multiplier=NaN` against the kwarg
omitted: pickled outputs identical.

## Surfaces removed

- `main_orchestrator.py` snapshot keys `etf_ownership_pct`, `etf_comovement_r2`,
  `etf_primary_wrapper`, `etf_transmission_multiplier`, plus their
  `ORCHESTRATOR_ONLY_FIELDS` entries in `tests/test_state_snapshot_parity.py`.
- `pilots/observability.py` section 9 and the `etf_transmission` summary key;
  `shared/observability_panel_helpers.etf_transmission_rows`; the
  `shared/help_content.py` helpers and `observability.etf_transmission` entry.
- `api/pilots_api.py`: `/settings/etf-transmission` GET/PUT/PATCH,
  `_ETF_TRANSMISSION_GROUPS`/`_INDEX`, and its `editable_at` entry.
- `pilots/feature_flags.py`: `ETF_HOLDINGS_ENABLED` and `ETF_TRANSMISSION_ENABLED`
  (nothing reads either flag now). The settings fields stay until 4f.
- `scripts/export_notebooklm.py`: the ETF multiplier column.
- `data/historical_store.py`: the TYPE_CHECKING import. The `etf_holdings` DDL
  and methods stay.
- Webapp: `EtfTransmissionSettings` screen (moved to `legacy/`), its route, the
  SettingsModules link, the Observability section, the types, the client methods,
  the mock builders and tunables, the feature-flag mocks, and the glossary and
  TAB_HELP entries.

## Moves

See `legacy/README.md`. `risk/` held only the ETF module, so the whole package
moved.

## Regenerated artifacts

`docs/settings_liveness.json` and the census (`scripts/settings_liveness.py --write`,
`scripts/measure_settings_census.py --write`). The ETF_TRANSMISSION_* fields and
`ETF_HOLDINGS_MARKET_PROXY`/`_TICKERS` now classify as `no_op`. Also dropped the
stale `ETF_HOLDINGS_TICKERS` entry from mock.ts `MOCK_CAPTURE_SITES` and the eight
moved files from `.test_durations.json`.

## Left for 4f

The four `ETF_*` `COLUMN_SCHEMA` columns and their NaN prefill, the settings
fields and their `ALLOWED_KEYS`/`settings_domains`/mock entries,
`size_position()`'s `etf_transmission_multiplier`, the tests pinning it
(`test_position_sizer` sections 4-7, `test_strategy_engine`,
`test_sizing_properties`), and `tests/test_gui_env_io_etf_transmission_keys.py`.
