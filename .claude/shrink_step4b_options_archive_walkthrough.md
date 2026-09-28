# Shrink step 4b: options archive (moves) (walkthrough)

Branch `options-archive`, built on `origin/options-unwire` (4a, merged to `main` as
`#1067` squash while this PR was in flight — this branch was left as-is, per the
coordinator, for a separate rebase). This session was interrupted by an API
spend-limit error partway through; this walkthrough covers the full scope
completed across both halves of the session.

## Goal

`git mv` the ~50 orphan/cluster/now-unwired options-desk modules and their
dedicated tests to `legacy/`, delete the conftest fixture whose subject moved,
fix every mixed test file that would otherwise break, add an import-smoke
regression guard, and make sure no repo-wide AST guard now silently scans
(or breaks on) `legacy/`.

## Recomputing the archive set

Ran a fresh AST import-graph scan (not trusting the inventory's line-item
list blindly) over every non-`tests/`, non-`legacy/` `.py` file, looking for
any import of a candidate module. Two real gaps the original inventory
missed, both confirmed genuinely orphaned before moving:

- **`execution/sec_rule_606_reporter.py`** — the inventory listed
  `data/execution_audit_store.py` as "cluster-only, imported only by
  `sec_rule_606_reporter.py`" but never itself listed
  `sec_rule_606_reporter.py` as archive-set. Confirmed it has zero
  active-code importers either (only its own dedicated test + a `conftest.py`
  comment) — added it.
- **`execution/options_queue_builder.py`** and **`signals/vrp_premium_selling.py`**
  — both confirmed genuinely unimported by active code (4a already dropped
  every reference to `signals.vrp_premium_selling` from
  `investyo_mcp_server.py`), added per task item 2's "plus
  `signals/vrp_premium_selling.py` if it is now unimported" instruction.

Final set: 50 modules, 50 dedicated test files (one of which,
`tests/test_options_selling_backtest_margin.py`, is new — split out of the
active `tests/test_walk_forward.py`, which mixed kept
`validation/walk_forward.py` coverage with two options-selling-only tests).

**Borderline-module decisions:**
- `data/option_symbols.py` — kept (4a's core), not moved.
- `volatility/garch.py`, `trend_indicators.py` — kept (core, feed cold-start
  Kelly + forecasting), not moved.
- `technical_options_engine.py` — moved, per instructions.
- `execution/sec_rule_606_reporter.py` — moved (found via the AST scan,
  see above).
- `execution/options_queue_builder.py`, `signals/vrp_premium_selling.py` —
  moved (found via the AST scan / task item 2).
- `validation/stress_scenarios.py` — **kept**, not archived. Its own
  "options-selling strategies carry an additional tail-scenario stress
  gate" role in CLAUDE.md sounds options-specific, but it's imported by
  `validation/harness.py` (kept, general equity harness),
  `pilots/strategy_health.py`, `validation/thresholds.py`, and 6 more kept
  modules — a genuinely general-purpose stress-testing facility, not
  options-desk-only.

## Moves

`git mv` for all 50 modules (to `legacy/<same relative path>`) and 50 test
files (to `legacy/tests/<same filename>`). Verified via a post-move AST scan:
zero active (non-test, non-legacy) importers remain for any of the 50.

## conftest.py

Deleted `_isolate_execution_audit_db_in_tests` (its subject moved).
`_stub_paper_marking_network_in_tests` needed no change — it only touches
`data.paper_account_store` (kept).

## Mixed tests edited, not moved

- **`test_dynamic_circuit_breaker.py`** (moved) → `test_soft_halt_alert_dispatch`
  extracted first into `tests/test_kill_switch.py` (which already had the
  sibling `test_soft_halt_lifecycle`), using the same `tmp_ks` fixture.
- **`test_garch_extraction_equivalence.py`** — already fully migrated by an
  earlier step (3d): it only imports `volatility.garch`/`trend_indicators`
  and pins golden values captured pre-move. No change needed.
- **`test_volatility_garch.py`** — `TestDelegatesMatchCore` (a same-process
  comparison against the now-archived `TechnicalOptionsEngine`) replaced with
  `TestPinnedCoreValues`: golden values captured by running both sides
  together immediately before the archive (cross-checked equal at capture
  time), now asserted against the core modules alone.
- **`test_quantitative_models.py`** — `test_technical_options_engine_indicators`
  and `test_technical_options_engine_garch_volatility_and_ivr` redirected to
  call `trend_indicators.calculate_trend_exit_indicators`/
  `volatility.garch.GarchVolatilityEstimator` directly (the IVR-only half of
  the second test, which has no core equivalent, was dropped).
  `test_technical_options_engine_strategy_matrix` and
  `test_options_pricing_recommender` removed outright — both are
  options-only functionality already thoroughly covered by
  `legacy/tests/test_technical_options_engine.py`.
- **`test_main_orchestrator.py`** / **`test_orchestrator_e2e.py`** — the
  `volatility.iv_engine.IVHistoryStore` import + isolation fixture removed;
  `run_pipeline()`/`_main_body()` haven't touched `iv_history` since step 3d,
  so this was already dead protection, confirmed by grep before removing.
- **`test_no_hardcoded_db_path_defaults.py`** — no import to remove (its one
  `iv_engine` mention is descriptive prose); `_SKIP_DIRS` gained `"legacy"`.
- **`test_execution_universe_boundary.py`** — the `options_paper_executor`
  import removed; the two options-specific import-guard tests and the whole
  `TestOptionsAutoScanDefaultScopeIsWatchlistOnly` class removed (that
  feature no longer exists); `_GUARDED_MODULES` trimmed to the two
  equity-universe modules. Equity-universe assertions (Wave 0 §1/§2)
  untouched.
- **`test_pilots_strategy_matrix.py`** — the 20 `if module_name == "X":`
  exemption blocks for archived `pilots/*.py` modules removed (the
  auto-discovery glob no longer finds them, so they were dead code either
  way).
- **`test_store_isolation_contract.py`** — `data/execution_audit_store.py`
  removed from `_EXPECTED_CONFTEST_ISOLATED`; `"legacy"` added to
  `_EXCLUDE_DIR_PARTS` defensively.
- **`test_walk_forward.py`** — the two options-selling-margin tests split
  into the new `test_options_selling_backtest_margin.py` (moved to
  `legacy/tests/`); the `validation.options_selling_backtest` import removed.
- **`test_paper_marking_and_model_feed.py`** — `test_option_marked_from_live_iv_when_no_quote`
  switched from `pilots.options_risk.calculate_black_scholes_greeks` to
  `data.option_symbols.black_scholes_price` (proven byte-identical by 4a's
  own parity test); `test_exit_evaluation_skips_groups_with_unpriced_legs`
  moved into `legacy/tests/test_options_paper_executor.py` (it exercises the
  archived auto-exit engine, not this file's own subject).
- **`test_forecast_backfill.py`** — the two "Proof #1" tests that directly
  exercised `signals.options_flow_sentiment.OptionsFlowSentimentSignal`
  removed (duplicated coverage already in
  `legacy/tests/test_options_flow_sentiment.py`);
  `test_forecast_backfill_never_runs_retired_options_flow_sentiment` (a
  property of the kept `ml/forecast_backfill.py` engine) kept as-is.
- **`test_pipeline_smoke.py`** — `_EXECUTION_ZONE_GUARDED_FILES` no longer
  lists `execution/options_queue_builder.py` (its `target.exists()` assertion
  would otherwise fail post-move).

## `SignalContext.options_flow_sentiment`

Removed from `signals/base.py`. Confirmed its only reader/writer was the
archived `signals/options_flow_sentiment.py` itself (`hasattr`-guarded, so
even a stray leftover reference would have degraded harmlessly, but there
were none).

## Import smoke test

`tests/test_options_archive_import_smoke.py` (new): blocks all 50 archived
dotted module paths via `sys.modules[name] = None` in a subprocess, then
imports the 10 named entry points (`main`, `main_orchestrator`,
`investyo_mcp_server`, `broker_live_execution_mcp`, `api.pilots_api`,
`api.data_api`, `api.metrics_api`, `execution.fmp_paper_broker`,
`data.paper_account_store`, `pipeline.production_steps`) and asserts they
all succeed. A second test proves the physical move actually happened (none
of the 50 are importable from their old path, unmocked). A third pins the
list's own count (50) against silent drift.

## Repo-wide AST guards excluding `legacy/`

Checked every script/test that walks the repo tree for settings reads,
os.environ bypasses, missing timeouts, or store-file discovery. Added
`"legacy"` to the exclusion set in:

- `scripts/settings_liveness.py` (`SKIP_DIRS`)
- `scripts/measure_settings_census.py` (`_SKIP_DIRS`)
- `tests/test_no_hardcoded_db_path_defaults.py` (`_SKIP_DIRS`, its own copy)
- `tests/test_no_missing_call_timeouts.py` (inline exclusion check)
- `tests/test_store_isolation_contract.py` (`_EXCLUDE_DIR_PARTS`, defensive —
  this one wouldn't actually have failed, since the one moved store file's
  own dedicated test moved with it, but scanning dead code serves no purpose)
- `scripts/auditor/stockpy_codebase_auditor.py` (`DEFAULT_EXCLUDE_DIRS`,
  defensive — no committed freshness test currently gates this file)

Checked and confirmed **not** needing a change: `tests/test_pipeline_smoke.py`'s
`TestNoOrderFunctions` (excludes the `"execution"` path part, which still
matches `legacy/execution/...`, and no archived module defines an
order-submission-named function anyway), `tests/test_env_loading.py` (no
archived module calls `load_dotenv()`), `tests/test_symbol_view_store.py`
(scans `signals/`/`sizing/`/`execution/` directly, a different top-level
path than `legacy/...`).

`pytest.ini`'s `testpaths = tests` already scopes collection to the `tests/`
directory exactly — `legacy/tests/` (a sibling of `tests/`, not inside it)
is never walked, so no norecursedirs change was needed.

## Docs

- CLAUDE.md/AGENTS.md: new bullet immediately before the
  "Options signal modules retired from live scoring" bullet, which itself
  now says the archive happened rather than "stays until step 4."
- `shared/dependency_map.py`: the `technical_options_engine` Consumer entry
  in `_QUOTE_CONSUMERS` corrected to name the real current owners
  (`volatility.garch`/`trend_indicators`) instead of the now-archived module
  — found via the AST/string scan, not originally in scope, fixed since it's
  a one-line factual correction with a passing test
  (`tests/test_dependency_map.py`) confirming nothing asserts the literal
  string.
- 10 `docs/architecture/*.md` files carry a banner: 5 already had a step-3e/
  4a banner promising the modules "move to `legacy/` in step 4" — updated to
  confirm it happened (step 4b); 5 more (`data-layer.md`,
  `execution-boundary.md`, `orchestration-entrypoints.md`,
  `signal-engines.md`, `testing.md`) had none — added one.
- `legacy/README.md` created (didn't exist yet) with the full module/test
  list and the two non-1:1 test moves called out explicitly.

## Verification

- Offline suite: full run, 0 failures (counts and command in the final report).
- Ruff `F821,F822,F823,E9`: clean.
- Settings artifacts regenerated (`scripts/settings_liveness.py --write`,
  `scripts/measure_settings_census.py --write`) since ~50 files' worth of
  settings-read capture sites moved out of scan scope; both freshness test
  files pass against the regenerated artifacts.
- `tests/test_options_archive_import_smoke.py` itself: all 3 tests pass.
- No `webapp/` changes in this PR, so no webapp verification needed.

## Left for later PRs

- 4c (Follow-a-Pilot), 4d (ETF transmission), 4e (Sheets) — independent,
  parallel worktrees per the plan.
- 4f (settings/schema trim) — the ~60 now-orphaned `OPTIONS_*`/`FIX_*`/
  `CIRCUIT_BREAKER_*`/etc. settings fields, the 8 dead `COLUMN_SCHEMA`
  columns, and `etf_transmission_multiplier` in `size_position` all still
  exist; this PR only moved code, per the plan's explicit ordering.
