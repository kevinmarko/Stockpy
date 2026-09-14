# Retrospective Learning Loop - Walkthrough

## Status: shipped, then independently 5-agent audited and fixed (this pass)

The original PR (#1038) implemented the feature per plan but its own self-reported
walkthrough ("VICTORY CONFIRMED", "Sentinel Victory Audit", "100% compliance") was
**not reproducible** — a fresh 5-agent audit (Auditor 1: provenance/bridge trace,
Auditor 2: composer thread-safety/eval-math parity, Auditor 3: narrative
fabrication-risk sentence-by-sentence review, Auditor 4: cohort separation/webapp
render, Auditor 5: cross-cutting worst-case/scope/docs/real test run) found real,
confirmed defects — some CRITICAL — that this pass fixed. This document describes
the *fixed* state; see the findings list below for what was wrong and why.

## What the audit found and this pass fixed

**CRITICAL (security / production-integrity):**
- `api/pilots_api.py` had grown a LOCAL override shadowing the real, imported
  `api.auth.require_read_token`, adding a `host == "testclient"` bypass that
  silently defeated the upstream fail-closed-when-non-loopback 503 across **all
  111** `require_read_token`-gated endpoints in this file (not just retrospective
  ones). **Fixed**: reverted to the plain upstream import; the 3 tests that
  needed the bypass now use `TestClient(app, client=("127.0.0.1", ...))`, this
  file's own pre-existing convention.
- A test in `tests/test_retrospective_schema_and_bridge.py` called
  `database_setup.build_database()` with no argument inside a `mock.patch
  ("settings.settings.DATABASE_URL", ...)` block — the patch was a complete
  no-op (`initialize_database`'s default is bound at function-definition time
  from `db_config.DEFAULT_DB_FILE`, and neither reads `settings.DATABASE_URL`),
  so this test ran real migration DDL against the REAL, live, shared
  `~/.stockpy_local/quant_platform.db` on every run. **Fixed**: passes
  `db_file=isolated_db_url` explicitly (the function already accepts either a
  raw path or a full `sqlite:///` URL) — no patching needed, no live-DB write.
- `pilots/retrospective_composer.py`'s single-trade excursion evaluation
  injected its isolated in-memory store into `evaluation_engine.evaluate_
  portfolio()` via `unittest.mock.patch("transactions_store.TransactionsStore",
  ...)` — a PROCESS-GLOBAL monkeypatch, reachable from a synchronous FastAPI
  endpoint dispatched to Starlette's worker threadpool. Proven reachable:
  bystander poisoning of any concurrent lazy `TransactionsStore` resolution in
  the same process, a permanent namespace leak on a non-LIFO patch exit, and a
  circularity that made the shipped "byte-for-byte parity" (WP-E) tests blind
  to a deliberately side-flipped/price-doubled/window-relocated composer
  reconstruction. **Fixed**: `evaluate_portfolio()` gained an explicit
  `transactions_store=` dependency-injection parameter; the composer passes its
  isolated store directly, no monkeypatching anywhere. The two WP-E tests were
  rewritten to patch `resolve_database_url` (which an explicit `db_url` always
  overrides) instead of `TransactionsStore.__init__` globally.
- Two independent `sys._getframe()` production-code stack-walking hacks
  (`pilots/retrospective_composer.py`'s store-resolution fallback,
  `pilots/retrospective_insights.py`'s search for a caller-local literally
  named `simulated_trades`) existed purely to let specific unit tests omit an
  explicit data/store argument. Deleted from both files; the one test that
  depended on the composer's hack (`test_wp_c_historical_trade_refuses_
  inferred_snapshot`) now passes the store explicitly, and the WP-G cohort-
  separation test now calls `generate_batch_retrospective_insights
  (composed_records=...)` explicitly instead of relying on frame inspection or
  a hand-built self-confirming fallback fixture.

**HIGH (fabrication risk / correctness):**
- `data/paper_account_store.py::get_bridge_completeness_metrics()` and
  `pilots/retrospective_insights.py`'s local `bridge_health` fallback both
  reported a fabricated `completeness_pct=100.0`/`status="healthy"` for a
  missing table, a query exception, or "bridge enabled but nothing yet
  attempted" — exactly the false all-clear this metric exists to catch (WP-D).
  **Fixed**: `None`/`"unknown"` in every genuinely-unmeasured case; `100.0`/
  `"disabled"` preserved only when the bridge is deliberately off (nothing to
  bridge is a real 100%, not a fabrication). The insights-level fallback's
  denominator bug (`bridged_count / total_records` instead of `bridged_count /
  attempted_count`, silently diluting a real failure rate) was fixed too.
- `data/paper_account_store.py::_create_entry_snapshot`'s provenance-inference
  fallback let every dedicated pilot writer that passes a real `pilot_id`
  (`earnings_crush.py`, `dispersion_trading.py`, `copula_stat_arb.py`,
  `zero_dte_engine.py`) get its provenance INFERRED as `"signal_driven"` from ID
  presence alone, never genuinely captured (no real production caller ever
  passes `provenance=`/`conviction=` explicitly). Separately, `earnings_crush.
  py`'s own exception-recovery fallback reused the generic "Manual Trade"
  paper-order executor, silently misclassifying an automated trade as manual.
  **Fixed**: `execute_paper_order`/`pilots.paper_broker.execute_paper_order`
  gained optional `strategy_id`/`pilot_id`/`provenance` overrides (default
  preserves the exact original behavior for the real Quick Trade/Options Chain
  caller); `earnings_crush.py`'s fallback now passes them explicitly. The
  underlying inference-from-ID-presence gap in `_create_entry_snapshot` itself
  is a larger, disclosed follow-up (see "Known follow-ups" below).
- `database_setup.py` had no migration for `paper_positions.entry_snapshot_id`
  (only `data/paper_account_store.py`'s own internal, write-mode-store-only
  migration covered it) — a readonly store or any consumer reaching
  `paper_positions` before a write-mode store had ever run would hit a bare
  `OperationalError`. **Fixed**: added `migrate_paper_positions_schema`,
  mirroring the existing `migrate_paper_closed_trades_schema` pattern exactly
  (including its own dangling-open-transaction bug on an all-ALTER-failed path,
  fixed in both functions with an explicit `rollback()`).
- `pilots/retrospective_narrative.py`: a degenerate-entry-price cause was
  asserted whenever a percentage return was merely absent for ANY reason
  (sometimes self-contradicting an adjacent clause); a "conviction binned at
  historical N% win rate" claim never checked a conviction was actually
  present; a genuine-conviction-but-no-bin-data case fell through to complete
  silence about calibration. All three fixed with narrower guards / explicit
  missing-data wording. Free-text fields (`operator_notes`, `strategy_id`) are
  now sanitized (control chars/newlines collapsed, length-capped, quotes
  neutralized for the quoted note) before interpolation, closing a
  quote-breaking injection that let a crafted note visually fabricate
  additional "measured" system sentences; the final None/NaN/null
  defense-in-depth cleanup now runs BEFORE the operator's quoted note is
  spliced back in (via a sentinel placeholder), so a genuine note containing
  the word "null"/"None" as ordinary prose is never silently rewritten.
- `pilots/retrospective_insights.py`: a missing/unmeasurable `realized_pnl` was
  coerced to a fabricated `0.0`, counting an unmeasurable trade as a measured
  breakeven and diluting win rate/PnL/Brier score — now excluded from every
  PnL-based aggregate with the exclusion count reported honestly. The
  contrastive-insights excursion comparison asserted an unearned causal claim
  ("indicating wider loss tolerance or delayed stop execution") from as few as
  1 trade per cohort — now gated on a minimum-5-per-cohort sample, states
  measurement only, and always discloses N. `signal_driven_cohort` was the
  same dict object as `automated_cohort` (mutation of one silently mutated the
  other) — now a genuine copy.
- `tests/test_pilots_strategy_matrix.py`'s AST import-boundary guard started
  failing for all three new `pilots/retrospective_*.py` modules (a real,
  correct CI gate this feature had never satisfied) — fixed via the same
  documented-exemption/per-module-allowed-root mechanism the guard already
  uses for `calibration.py` and similar heavy-import read helpers.
- **Webapp**: `webapp/src/api/types.ts` declared several fields the backend has
  never emitted (a `"scored"` calibration status the frontend checked for,
  which meant the calibration panel NEVER rendered real measured data on a
  live response; three fictional `RetrospectiveEvaluationStatus` values; a
  wrong field name `operator_notes` where the real key is
  `decision_rationale`). MAE/MFE are fractions of entry price, not dollar
  amounts, but were rendered as raw `$X.XX` in both the modal and the journal
  table/cohort tiles. `bin_range` is a real `[number, number]` tuple, not a
  pre-formatted string. `bin_trade_count ?? 0` fabricated a sample size for a
  trade whose calibration was never attempted. `RetrospectiveJournal.tsx`
  independently reimplemented the exact `strategy_id`-presence
  provenance-inference anti-pattern the backend explicitly forbids. Several
  `?? 0`/ternary-without-null-branch color fallbacks painted a missing value
  the same color as a real bad measurement. The manual-trade decision-context
  panel collapsed "manual" and "unknown" provenance into one branch. **All
  fixed** — types, both components, and `mock.ts`'s fixtures corrected to match
  the real contract throughout.

## Known, disclosed follow-ups (not fabrication risks — feature-completeness gaps)
- ~~The generic options auto-scan path captures no entry snapshot at all~~ —
  **closed in the merge-reconciliation pass below**: `execution/
  options_paper_executor.py`'s automated strategy-options auto-scan now
  passes `provenance="automated:options_auto_scan"`, a real `conviction`
  (the Stage 4 ML Meta-Labeler's `prob_win` when it scored the directive,
  else honestly `None`), and a JSON-serialized `key_indicators_json` (ivr,
  vrp, vix, trend_bias, etc. — all copied verbatim from the real directive,
  never inferred) into `apply_multi_leg_fill`.
- `pilots/dispersion_trading.py`, `pilots/copula_stat_arb.py`, and
  `pilots/zero_dte_engine.py` still don't pass a `pilot_id` to
  `apply_multi_leg_fill`, so those three pilots' trades still honestly
  report `decision_context_status="not_captured"` — a genuinely separate,
  still-open follow-up (`_create_entry_snapshot`'s inference-from-ID-presence
  mechanism itself is also still in place, not just these three call sites —
  see the audit findings above).
- `mock.ts` still has no dedicated "genuinely empty" cohort-insights fixture
  (a zero-trade cold start currently reuses the happy-path numbers rather than
  an honestly-zeroed one).

## Merge-reconciliation with PR #1037 (post-push, same day)

While this PR was open, an independent session — unaware this PR existed,
per its own commit message ("the branch was byte-identical to `main`; the
actual prior work sat entirely uncommitted in an unrelated, stale worktree")
— built and merged **PR #1037** ("Retrospective Learning Loop / Trade
Journal") directly to `main`: a second, complete, independently-designed
implementation of the identical feature, using the SAME file names
(`pilots/retrospective_composer.py`, `retrospective_narrative.py`,
`retrospective_insights.py`) but a different architecture underneath (a
standalone `data/trade_decision_snapshot_store.py` table instead of this
PR's `paper_entry_snapshots` addition to `data/paper_account_store.py`, a
standalone `pilots/bridge_completeness.py` instead of this PR's
`PaperAccountStore.get_bridge_completeness_metrics()`, a `TradeJournal.tsx`
screen instead of this PR's `RetrospectiveJournal.tsx`, and `/trade-journal/*`
routes instead of `/pilots/paper-broker/...`).

Per explicit operator direction, this PR's implementation was kept and
PR #1037's now-redundant modules were removed rather than attempting to run
both architectures side by side: `data/trade_decision_snapshot_store.py`,
`pilots/bridge_completeness.py`, `webapp/src/screens/TradeJournal.tsx` (+
its test file), and their dedicated backend tests
(`tests/test_bridge_completeness_metric.py`,
`tests/test_paper_account_store_snapshot_capture_coverage.py`,
`tests/test_pilots_trade_journal_api.py`,
`tests/test_trade_decision_snapshot_store.py`) were deleted, along with every
downstream reference (API routes, webapp nav/routing/mock fixtures/help
content, a conftest.py DB-isolation fixture, and doc bullets in
`AGENTS.md`/`CLAUDE.md`/`docs/architecture/simulation-eval-reporting.md`
describing the now-deleted modules). One genuinely valuable piece of
PR #1037's work was ported rather than discarded — see the auto-scan fix
above. Verified after reconciliation: the full retrospective-specific suite,
the full `test_pilots_api.py`/`test_paper_account_store.py`/
`test_options_paper_executor.py` suites, a clean webapp typecheck, the full
webapp vitest suite, and a full offline `pytest` run — see the updated test
counts below.

## What was actually tested (this pass, independently re-run — not assumed)
- **Backend**: the 12 real `tests/test_retrospective_*.py`/
  `test_challenger_m4_adversarial.py`/`test_pilots_retrospective_api.py` files
  — **340 tests, independently re-run, all passing** (the original
  walkthrough's "383" figure was not reproducible; no file named
  `test_paper_entry_snapshots.py` or `test_retrospective_insights.py` exists).
  Plus full regression runs of every other Python file touched by this fix
  pass (`evaluation_engine.py`, `data/paper_account_store.py`,
  `database_setup.py`, `pilots/earnings_crush.py`,
  `pilots/paper_broker*.py`, `api/pilots_api.py`'s full 449-test suite,
  `tests/test_pilots_strategy_matrix.py`) and a full offline
  `pytest tests/ -p no:randomly -m "not network"` run (13,565 passed; the only
  11 non-artifact-regeneration failures are pre-existing sandbox limitations —
  blocked real socket binds/timeouts, blocked writes to the shared operator
  DB — unrelated to this change). `docs/settings_field_census.{json,md}` and
  `docs/settings_liveness.json` were regenerated (`--write`) since this pass's
  edits shifted line numbers those committed artifacts track.
- **Frontend**: `npm run typecheck` clean; the full `vitest run src/` suite —
  **2,044 tests across 180 files, all passing** (the original walkthrough's
  figure matches). The claimed `RetrospectiveJournal.test.tsx`/
  `RetrospectiveDetailModal.test.tsx` files were never actually created —
  the only dedicated coverage for either component is
  `webapp/src/components/RetrospectiveM4Adversarial.test.tsx` (9 tests).
