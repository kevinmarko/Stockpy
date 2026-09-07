# Walkthrough — `iv_history` provenance tracking (bootstrap-IV fabrication-risk fix)

## What was wrong

`volatility/bootstrap_iv_history.py` is a CLI backfill script that computes a
realized-volatility-derived PROXY for 30-day ATM implied volatility
(`rolling_vol(20d) + 3.8%` — a standard free-data stand-in, since historical
options-chain IV isn't free) and writes it into `iv_history` via
`IVHistoryStore.record_iv()`.

That is the exact same table, and the exact same write method, the LIVE
production pipeline uses for genuine options-chain-derived IV
(`pipeline/production_steps.py::OptionsAnalysisStep`,
`technical_options_engine.py`'s opt-in real-IVR path — both via
`get_30d_atm_iv()`). `calculate_true_ivr()` ranks a live `current_iv` reading
against this table's history to compute `True_IVR`, which gates real
premium-selling trade decisions per this platform's `True_IVR > 50` / `VRP >
0.02` convention.

There was no way — for the code or for an operator reading the table — to
tell a synthetic proxy row from a genuine chain-derived row. Worse, the
script's own docstring called its output "historical ATM implied
volatilities," which overstates what it actually computes.

## What changed

**`volatility/iv_engine.py`**
- `iv_history` gained a `source` column (`IVHistory` ORM model), added via
  an additive, idempotent migration (`IVHistoryStore.
  _migrate_add_source_column`) that runs on every store construction.
  Probes via `sqlalchemy.inspect(engine).get_columns(...)` rather than raw
  `PRAGMA table_info` — `IVHistoryStore` is a dual-backend SQLite/Postgres
  store via `db_config.create_db_engine()`, and `PRAGMA` is SQLite-only.
  This mirrors `data/paper_account_store.py::_migrate_paper_positions_
  schema`'s own reasoning for the same dialect-safety concern, rather than
  `data/historical_store.py`'s `PRAGMA`-based convention (that store is
  raw-sqlite3-only and never faces this problem).
- Three provenance tags: `IV_SOURCE_CHAIN` ("chain", the `record_iv()`
  default), `IV_SOURCE_SYNTHETIC_BOOTSTRAP` ("synthetic_bootstrap",
  explicit-only), `IV_SOURCE_LEGACY_UNKNOWN` ("legacy_unknown", backfilled
  onto rows that predate this migration — never asserted real,
  CONSTRAINT #4: this codebase cannot verify a pre-migration row's true
  origin after the fact).
- `record_iv(ticker, date_val, iv_val, *, source=IV_SOURCE_CHAIN)` — the two
  real production call sites (`pipeline/production_steps.py:397`,
  `technical_options_engine.py:1069`) were deliberately left UNCHANGED;
  both call it with the same 3 positional arguments they always have, and
  now get tagged `chain` automatically via the default.
- `get_historical_ivs(..., *, exclude_sources=(IV_SOURCE_SYNTHETIC_
  BOOTSTRAP,))` — excludes synthetic-bootstrap rows from the ranking
  history by default. `IV_SOURCE_LEGACY_UNKNOWN` rows are NOT excluded
  (see "Design decision" below).

**`pilots/volatility_surface.py`**
- `calculate_true_ivr()` needed no logic change — it calls
  `store.get_historical_ivs(ticker, as_of_date, lookback_days)` positionally
  and inherits the new default exclusion automatically. Only its docstring
  was updated to document the contract it now relies on.

**`volatility/bootstrap_iv_history.py`**
- Module and `argparse` docstrings corrected: states plainly it produces a
  realized-vol-derived PROXY, not genuine IV.
- The one `record_iv()` call site now explicitly passes
  `source=IV_SOURCE_SYNTHETIC_BOOTSTRAP` — never silently defaulted to look
  real (CONSTRAINT #4).

## Design decision: why `IV_SOURCE_LEGACY_UNKNOWN` rows stay ranked

Excluding `legacy_unknown` rows by default would have been the more
"conservative-sounding" choice, but it would be wrong here: every
`record_iv()` call site that existed in shipped code BEFORE this migration
was a genuine chain-derived write — `bootstrap_iv_history.py` is the only
caller that could ever have written a synthetic row, and it only starts
tagging explicitly from this fix forward. Treating "unknown provenance" as
"exclude to be safe" would silently blank out real historical ranking on
every pre-existing installation, for a risk that (in the actually-shipped
code) essentially never materialized. This is documented explicitly in
`IV_SOURCE_LEGACY_UNKNOWN`'s own docstring and in the new known-issues
write-up, rather than left as an unstated judgment call.

## Verification (claims checked against actual run output, not assumed)

- `tests/test_iv_history_provenance.py` (new, 13 tests): 13 passed.
- `tests/test_iv_history_no_lookahead.py`, `tests/test_iv_engine.py`,
  `tests/test_options_matrix.py`, `tests/test_engine_context.py`,
  `tests/test_orchestrator_e2e.py`, `tests/test_main_orchestrator.py`,
  `tests/test_volatility_surface.py`: all pass unchanged (verified by
  running each, not inferred from the diff).
- Full offline suite (`pytest -m "not network"`, run via the shared
  `/Users/kevinlee/Stockpy-live/.venv` — this worktree carries no `.venv`
  of its own): **13,038 passed**, 10 failed. All 10 failures were
  individually inspected and confirmed unrelated:
  - `test_alpaca_http.py`, `test_net_util.py::TestFindFreePort` — sandbox
    denies `socket.bind()` (`PermissionError: Operation not permitted`).
  - `test_data_engine_macro_history.py`'s bounded-real-socket tests — same
    sandbox network restriction.
  - `test_command_execution.py::test_non_command_job_has_no_command_name` —
    403 vs expected 200; unrelated auth/flag state.
  - `test_investyo_mcp_widgets.py` — `sqlite3.OperationalError: attempt to
    write a readonly database` (sandbox filesystem restriction).
  - `test_settings_liveness.py::TestCommittedArtifactIsFresh` — the
    committed `docs/settings_liveness.json` is stale relative to
    `settings.py`/`execution/alpaca_broker.py` line numbers this branch
    never touched — pre-existing drift in this checkout, unrelated to
    `iv_history`.
  - `grep`-confirmed: none of the 10 failing test files reference
    `iv_engine`, `bootstrap_iv_history`, or `volatility_surface` anywhere.
- `ruff check . --select=F821,F822,F823,E9` (the actual scoped rule set the
  `/verify` skill and CI's lint gate use, per `.claude/commands/verify.md`
  — NOT the full default ruleset, which this codebase has ~1,200
  pre-existing violations against): clean, zero findings.
- `make ci`/`.venv/bin/python3` could not be run verbatim FROM this
  worktree (it has no local `.venv`; only the main checkout at
  `/Users/kevinlee/Stockpy-live/.venv` does) — the full offline-suite run
  above, executed against that shared `.venv`'s interpreter with this
  worktree's code, is the equivalent coverage.
- Environment quirk, unrelated to this change, disclosed rather than
  hidden: this sandbox's ambient Python 3.14 (used when `.venv` isn't
  explicitly invoked) hits a numba cache-locator `RuntimeError` importing
  `pandas_ta`/`pandas_ta_classic` at all; `NUMBA_DISABLE_JIT=1` works
  around it for verification purposes and does not touch production code
  or behavior.

## Scope boundary, disclosed

`docs/known_issues/vrp_premium_selling_no_historical_iv.md` — the doc that
originally flagged this hazard in its own "Related, separate hazard"
section — lives on PR #1017 (`extend-backfill-meta-labeling`), which was
still open/unmerged when this branch was created from `main` (per this
task's own repo instructions). That file does not exist on this branch, so
it could not be edited to stop describing the hazard as open. The new
`docs/known_issues/bootstrap_iv_history_provenance_fabrication_risk.md`
cross-references PR #1017 by number/branch instead. Whoever merges #1017
(or a small follow-up after both land on `main`) should update that doc's
text to point at this fix.
