# NotebookLM Modular Multi-Source Knowledge Pack (v2 rebuild) — Task Tracker

## Driver / CLI

- [x] `--output-dir <path>` flag: overrides `settings.OUTPUT_DIR` as the base
      directory for both the consolidated file and the `notebooklm/` subdir.
- [x] `--modular-only` flag: skip writing `notebooklm_source.md`.
- [x] `--consolidated-only` flag: skip writing all 5 modular files.
- [x] `--section {macro,portfolio,signals,trades,options}` flag: generate
      exactly one modular file.
- [ ] ~~Argument validation: `--section` + `--consolidated-only` together
      raises a clear CLI error~~ — NOT implemented. In practice `--section`
      wins (writes exactly that one modular file, skips the consolidated
      file) with no error; an unusual flag combination not covered by
      `docs/GOOGLE_NOTEBOOK_INTEGRATION.md`'s documented usage, left as a
      non-crashing edge case rather than added as new, untested scope. See
      the walkthrough's "Corrections to this walkthrough's own first draft"
      section.
- [x] Default (no flags): writes the consolidated file AND all 5 modular
      files.
- [x] Existing `argparse` scaffolding in `__main__` preserved/extended (real
      flags now, not the zero-flag placeholder) — `--help` confirmed
      side-effect-free; `cli_introspect/build_command_manifest.py` correctly
      introspected all 4 real options (was 0 before this rebuild).

## 5 modular generators

- [x] `01_macro_and_regime.md` — VIX, 10Y-2Y spread, HY OAS, HMM regime
      state/risk-on probability, macro kill switch, gate protection status.
      Reuses `_OneShotMacroDataEngine`'s one-shot-fetch pattern.
- [x] `02_portfolio_and_greeks.md` — positions, total equity, buying power
      (real brokerage account via `HistoricalStore`), portfolio net Greeks
      via `pilots.paper_broker.get_portfolio_greeks()` (Bug 1 fix — NOT a
      bare `calculate_portfolio_greeks()` call). Carries an inline note
      clarifying the Greeks reflect the separate paper-trading book, not
      the live account summarized above it (found during live smoke-test).
- [x] `03_strategy_signals_and_picks.md` — daily BUY/SELL/HOLD signals,
      multifactor z-scores (`Value_Z`/`Quality_Z`/`LowVol_Z`/`Size_Z`/
      `Multifactor_Composite`), sizing guardrail telemetry
      (`was_capped`/`binding_constraint`/ETF transmission multiplier),
      active pilot follows (`FollowsStore().list_active()`).
- [x] `04_trade_journal_and_ledger.md` — closed-trade history & KPIs from
      the durable trade ledger (`pilots.trade_history.trade_history_view`),
      with three distinct messages for ingest-never-ran / genuinely-zero /
      real-trades (Bug 6 fix).
- [x] `05_options_directives_and_matrix.md` — options premium-selling
      directives (strategy, strikes, premiums, IV rank via the fixed
      `True_IVR`/`IVR_Proxy` fallback), fundamental health context, recent
      news headlines for the underlying names.
- [x] Each generator is a standalone function callable independently
      (needed for `--section`), verified directly by
      `tests/test_export_notebooklm.py`'s per-generator test classes.

## Bug fixes (from the abandoned prior attempt)

- [x] **Bug 1 (portfolio-Greeks fabrication, CRITICAL)**: fixed — calls
      `pilots.paper_broker.get_portfolio_greeks()`, never
      `pilots.options_risk.calculate_portfolio_greeks()` directly.
      Regression tests: `TestPortfolioGreeksDelegation` (4 tests).
- [x] **Bug 2 (crash isolation, CRITICAL)**: fixed — each of the 5 modular
      generator calls runs inside its own `try/except` in `build_export()`.
      Regression tests: `TestPerGeneratorCrashIsolation` (3 tests),
      live-reproduced via a real `--output-dir` run with a stub raising
      generator before the fix confirmed the pre-fix cascade, and again
      after the fix confirmed isolation.
- [x] **Bug 3 (dead `True_IVR`/`IVR_Proxy` fallback, HIGH)**: fixed via
      `_resolve_ivr()`. Regression tests: `TestTrueIvrFallback` (3 tests).
- [x] **Bug 4 (Markdown pipe escaping, HIGH)**: fixed via
      `_md_escape(value, default="N/A")`. Regression tests: `TestMdEscape`
      (3 tests), `TestPipeEscapingEndToEnd` (1 end-to-end test asserting
      exact rendered column count).
- [x] **Bug 5 (`pandas.NA`/`pandas.NaT` formatting, MEDIUM)**: fixed via
      shared `_is_missing()`. Regression tests:
      `TestNaNFormattingAcrossHelpers` (15 parametrized cases).
- [x] **Bug 6 (0-trades vs. fetch-failed conflation, MEDIUM)**: fixed.
      Regression tests: `TestTradeJournalAvailability` (4 tests).
- [x] **Bug 7 (privacy disclosure gap, MEDIUM)**: fixed via documentation —
      see Documentation section below.
- [x] **Bug 8 (wrong `output_dir` routing, CRITICAL, integration-only)**:
      found only once all 8 agents' pieces were assembled and
      `build_export()` was run end-to-end against real on-disk fixtures —
      the driver was passing the write-target directory
      (`output/notebooklm/`) as the read-source directory to the 3
      JSON-reading generators. Fixed; all 5 previously-failing integration
      tests now pass.
- [x] **Bug 9 (`_fmt_money` regression during assembly, integration-only)**:
      merging 8 independently-written copies of `_fmt_money` into one
      canonical helper accidentally added a `try/except` not present in any
      individual agent's version, silently breaking 2 of the pre-existing
      23 tests (`TestPartialAppendProtection`). Reverted to the original
      (non-catching) behavior.

## Output/formatting

- [x] `_fmt_money`/`_fmt_num`/`_fmt_pct` single-sourced across all 6
      generators (consolidated + 5 modular) — CONSTRAINT #4 "N/A, never
      fabricated 0.0" behavior stays single-sourced.
- [x] Atomic write (pid+tid-scoped temp file + rename) generalized to all
      6 output files via `_atomic_write_file()`.
- [x] `output/notebooklm/` directory created (`mkdir(parents=True,
      exist_ok=True)`) before any modular file is written.

## Tests (`tests/test_export_notebooklm.py`, extended)

- [x] Happy-path + degraded-path test per modular generator.
- [x] Bug 1/2/3/4/5/6 regression tests (see Bug fixes section above).
- [x] CLI flag tests: `--section` forwarding, `--modular-only` /
      `--consolidated-only` forwarding via `TestMainCliFlags`; end-to-end
      filtering behavior (exactly one file / no consolidated file / no
      modular directory) via `TestBuildExportFiltering`.
- [x] `--help` confirmed side-effect-free (live-verified, not just unit
      tested).
- [x] All 29 pre-existing consolidated-export tests still pass unchanged
      (one test's assertion updated for the new multi-file default, its own
      docstring explains why — this is a test-scope update, not a behavior
      regression).

**Final count: 97 passed, 0 failed** (`python3 -m pytest
tests/test_export_notebooklm.py -v`).

## Documentation (per CLAUDE.md's "every plan scopes its own doc updates")

- [x] `docs/GOOGLE_NOTEBOOK_INTEGRATION.md` — written fresh (new file).
      Prominent early "Privacy & Data Handling" section (Bug 7 fix), usage
      for all new CLI flags, `settings.OUTPUT_DIR` location confirmed
      against `settings.py`, 5-file schema table, example NotebookLM
      prompts, known limitations, CONSTRAINT #4/#6 note.
- [x] `CLAUDE.md` changelog bullet inserted (and `AGENTS.md` auto-synced by
      `.claude/hooks/sync_agent_docs.sh`).
- [x] `docs/README.md` one-line index addition inserted (new row under
      "Integration references", alongside `FMP_INTEGRATION.md` /
      `JULES_INTEGRATION.md`).

## Merge readiness

- [x] All 8 parallel implementation slices merged into one coherent
      `scripts/export_notebooklm.py` (integration performed by hand,
      reconciling 4 independently-written `_md_escape`/`_load_json_file`
      copies down to one canonical version each).
- [x] `pytest tests/test_export_notebooklm.py -q` — 97 passed, 0 failures.
- [x] `pytest tests/test_command_manifest_freshness.py
      tests/test_build_command_manifest.py -q` — 26 passed, 0 failures
      (after `python3 scripts/build_command_manifest.py` regeneration —
      the new script now reports its 4 real CLI options instead of 0).
- [x] `ruff check --select=F821,F822,F823,E9` (the genuine-bug lint gate
      `/verify` actually enforces) — clean.
- [ ] ~~Manual spot-check: upload one modular file to a scratch NotebookLM
      notebook~~ — NOT performed; no live NotebookLM account access from
      this environment. What WAS verified: a real end-to-end script run
      against this machine's actual local `quant_platform.db` produced all
      6 files with real, non-fabricated content (a real 25-position
      account, a real 173-trade FIFO ledger) and honest degrades where no
      local pipeline artifact existed — inspected directly, not merely
      trusted. The actual NotebookLM-side table-rendering claim in this
      walkthrough's first draft was aspirational and has been corrected;
      the pipe-escaping fix is verified by exact column-count assertion in
      `tests/test_export_notebooklm.py`, which is the strongest check
      available without a live upload.
