# Implementation Plan — `iv_history` provenance tracking (bootstrap-IV fabrication-risk fix)

Branch: `fix-bootstrap-iv-history-provenance`

## Problem

`volatility/bootstrap_iv_history.py` writes a realized-vol-derived PROXY
(`rolling_vol(20d) + 0.038`, NOT genuine implied volatility) into `iv_history`
via `IVHistoryStore.record_iv()` — the SAME table and write method the live
production pipeline (`pipeline/production_steps.py::OptionsAnalysisStep`,
`technical_options_engine.py`'s opt-in real-IVR path) uses for genuine
options-chain-derived IV. `calculate_true_ivr()` ranks a live `current_iv`
reading against this table's history to compute `True_IVR`, which gates real
premium-selling trade decisions (`True_IVR > 50`, `VRP > 0.02`). No
provenance marker exists to distinguish a synthetic row from a real one —
CONSTRAINT #4/#6 violation.

## Design decisions

1. **Add `iv_history.source` column** — additive, idempotent
   `ALTER TABLE ... ADD COLUMN` migration, probed via `sqlalchemy.inspect()`
   (not raw `PRAGMA table_info` — `IVHistoryStore` is a dual-backend
   SQLite/Postgres store via `db_config.create_db_engine()`, and `PRAGMA` is
   SQLite-only; mirrors `data/paper_account_store.py`'s dialect-safety
   reasoning rather than `data/historical_store.py`'s SQLite-only pattern
   the task description initially pointed at).
2. **Three tags**: `IV_SOURCE_CHAIN` (default, real writes), `IV_SOURCE_
   SYNTHETIC_BOOTSTRAP` (explicit-only, the bootstrap script),
   `IV_SOURCE_LEGACY_UNKNOWN` (migration backfill for pre-existing rows —
   never asserted real, CONSTRAINT #4).
3. **Minimal-footprint call-site strategy**: `record_iv()`'s `source`
   parameter defaults to `IV_SOURCE_CHAIN`. The two REAL production call
   sites (`pipeline/production_steps.py:397`,
   `technical_options_engine.py:1069`) are left UNTOUCHED — they get the
   correct tag purely from the default, avoiding any change to
   `main_orchestrator.py`'s re-export list or those two files' imports.
   Only `volatility/bootstrap_iv_history.py` is touched to pass `source=
   IV_SOURCE_SYNTHETIC_BOOTSTRAP` explicitly (never silently defaulted).
4. **Filtering lives in `get_historical_ivs()`**, not `calculate_true_ivr()`
   — a new keyword-only `exclude_sources` param, default `(IV_SOURCE_
   SYNTHETIC_BOOTSTRAP,)`. `calculate_true_ivr()` (`pilots/volatility_
   surface.py`, the canonical implementation) calls this positionally with
   3 args and inherits the new default with ZERO code change — only its
   docstring was updated to document the contract. This is option (a) from
   the task spec ("skip synthetic rows entirely — the safer default"),
   chosen over a return-value flag (option b) because it's the smaller,
   safer change that structurally cannot be forgotten by a future caller
   (the exclusion is data-layer, not caller-opt-in).
   `IV_SOURCE_LEGACY_UNKNOWN` rows are NOT excluded by default — excluding
   them would silently blank real historical ranking on every pre-existing
   installation (every pre-migration `record_iv()` caller in shipped code
   was a genuine chain-derived write) for no compensating safety benefit.
5. **Docstring/help-text corrections** in `bootstrap_iv_history.py` — no
   longer claims to produce "historical ATM implied volatilities."

## Files touched

- `volatility/iv_engine.py` — `IV_SOURCE_*` constants, `source` ORM column,
  `_migrate_add_source_column()`, `record_iv(..., source=IV_SOURCE_CHAIN)`,
  `get_historical_ivs(..., exclude_sources=...)`, updated docstrings.
- `volatility/bootstrap_iv_history.py` — corrected module/argparse
  docstrings, explicit `source=IV_SOURCE_SYNTHETIC_BOOTSTRAP` at the one
  `record_iv()` call site.
- `pilots/volatility_surface.py` — `calculate_true_ivr()` docstring update
  only (no logic change).
- `tests/test_iv_history_provenance.py` — new file (13 tests): provenance
  tagging, synthetic-ground-truth before/after ranking proof, wholly-
  synthetic fail-closed-to-NaN, legacy-unknown not excluded, migration
  idempotency against a real pre-existing legacy-schema DB file, bootstrap
  `main()` end-to-end tagging.
- Docs: `docs/known_issues/bootstrap_iv_history_provenance_fabrication_risk.md`
  (new), `docs/known_issues/README.md` (index entry),
  `docs/architecture/signal-engines.md` (True_IVR paragraph addendum),
  `docs/architecture/validation-and-signals.md` (bootstrap script one-liner
  corrected), `docs/test_coverage_analysis.md` (two stale-0%-coverage rows
  marked closed).

## Verification

- New test file: 13/13 pass.
- All pre-existing tests touching `IVHistoryStore`/`calculate_true_ivr`
  (`test_iv_history_no_lookahead.py`, `test_iv_engine.py`,
  `test_options_matrix.py`, `test_engine_context.py`,
  `test_orchestrator_e2e.py`, `test_main_orchestrator.py`,
  `test_volatility_surface.py`) pass unchanged.
- Full offline suite (`pytest -m "not network"`, via the shared
  `/Users/kevinlee/Stockpy-live/.venv` since this worktree has no `.venv`
  of its own): 13,038 passed, 10 pre-existing failures — all confirmed
  unrelated (sandbox socket-bind permission errors, sandbox readonly-DB
  write errors, local `.env`-specific auth divergence, and a pre-existing
  stale committed `docs/settings_liveness.json` artifact — none touch any
  file this change modifies).
- `ruff check . --select=F821,F822,F823,E9` (this repo's actual CI-gate
  ruleset per `/verify`): clean.

## Cross-reference resolution (updated after a rebase)

`docs/known_issues/vrp_premium_selling_no_historical_iv.md` (which first
flagged this hazard) lived on PR #1017 / branch
`extend-backfill-meta-labeling`, not yet merged into `main` when this
branch was created — initially handled by cross-referencing it by PR
number/path only. #1017 merged to `main` while this PR was still open;
rebased onto the new `main` (one real conflict in
`docs/known_issues/README.md`, resolved by keeping both PRs' appended
rows) and updated that doc's own "Related, separate hazard" section to
say Fixed and point at this write-up.
