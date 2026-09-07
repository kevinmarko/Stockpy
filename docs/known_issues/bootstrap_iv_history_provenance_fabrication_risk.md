# `bootstrap_iv_history.py` writes were indistinguishable from real chain-derived IV history (fixed 2026-09)

**Status: Fixed.** See "The fix" below. This hazard was originally flagged as a
related, separate, deliberately-deferred finding while extending the Forecast
Backfill meta-labeling screen (see
[`docs/known_issues/vrp_premium_selling_no_historical_iv.md`](vrp_premium_selling_no_historical_iv.md)'s
"Related, separate hazard" section, [PR #1017](https://github.com/kevinmarko/Stockpy/pull/1017) /
branch `extend-backfill-meta-labeling`) — that discovery correctly scoped this out
as its own follow-up rather than bundling an unrelated fix into that PR. This
write-up is that follow-up; `vrp_premium_selling_no_historical_iv.md`'s own text
has been updated to point here.

## The hazard

`volatility/bootstrap_iv_history.py` is a CLI backfill script that computes a
**realized-volatility-derived proxy** for historical 30-day ATM implied
volatility (`estimated_iv = rolling_vol(20d) + 0.038` — a flat 3.8% VRP
offset) and writes it into the `iv_history` table via
`volatility.iv_engine.IVHistoryStore.record_iv()`.

That is the SAME table, and the SAME write method, the LIVE production
pipeline uses for genuine options-chain-derived IV
(`pipeline/production_steps.py::OptionsAnalysisStep` and
`technical_options_engine.py`'s opt-in
`settings.OPTIONS_TRUE_IVR_ENABLED` path, both via
`volatility.iv_engine.get_30d_atm_iv()`). `calculate_true_ivr()`
(`pilots/volatility_surface.py`, the canonical implementation;
`volatility/iv_engine.py` re-exports it) ranks a live `current_iv` reading
against this table's history to compute `True_IVR`, which in turn gates real
premium-selling trade decisions per this platform's `True_IVR > 50` /
`VRP > 0.02` convention (see `CLAUDE.md`'s "Conventions enforced" bullet on
options premium selling).

Before this fix, `iv_history` rows carried **no provenance marker** — a
synthetic, realized-vol-derived proxy row written by
`bootstrap_iv_history.py` was byte-for-byte indistinguishable from a genuine
options-chain-derived row. Worse, the script's own docstring called its
output "historical ATM implied volatilities," which is misleading — it is a
proxy, not a real IV reading. If an operator ever ran this script against a
symbol, `calculate_true_ivr()` would silently rank the live pipeline's
genuinely-observed IV against a history that was partly (or entirely)
fabricated realized-vol-derived numbers wearing an IV label, with no way for
a caller or operator to tell which rows were real.

This is a direct CONSTRAINT #4 (never fabricate a metric treated as measured)
and CONSTRAINT #6 (fail closed on unverifiable data) concern: a live
premium-selling trade gate must never rank against a value this table cannot
vouch for as genuine.

## The fix

1. **`iv_history` gained a `source` column** (`volatility/iv_engine.py`'s
   `IVHistory` ORM model), added via an additive, idempotent
   `ALTER TABLE ... ADD COLUMN` migration
   (`IVHistoryStore._migrate_add_source_column`) run on every store
   construction. Uses `sqlalchemy.inspect()` (dialect-agnostic — this store
   is SQLite/Postgres dual-backend via `db_config.create_db_engine()`)
   rather than a raw `PRAGMA table_info` probe, mirroring
   `data/paper_account_store.py::_migrate_paper_positions_schema`'s
   dialect-safety reasoning rather than `data/historical_store.py`'s
   SQLite-only convention (that store never supports Postgres, so `PRAGMA`
   is safe there; `IVHistoryStore` does, so it isn't here).

2. **Three provenance tags** (`volatility/iv_engine.py`):
   - `IV_SOURCE_CHAIN` ("chain") — genuine, options-chain-derived IV. The
     `record_iv()` default; every existing production call site
     (`pipeline/production_steps.py:397`, `technical_options_engine.py:1069`)
     relies on this default and was left UNCHANGED — neither passes
     `source` explicitly, both get tagged `IV_SOURCE_CHAIN` automatically.
   - `IV_SOURCE_SYNTHETIC_BOOTSTRAP` ("synthetic_bootstrap") — the
     realized-vol-derived proxy. `bootstrap_iv_history.py` is the ONLY
     caller of `record_iv()` that must pass this explicitly (never silently
     defaulted to look real — CONSTRAINT #4).
   - `IV_SOURCE_LEGACY_UNKNOWN` ("legacy_unknown") — backfilled onto rows
     that predate this migration. Deliberately NOT asserted as
     `IV_SOURCE_CHAIN`: this codebase cannot verify, after the fact,
     whether a pre-migration row was chain-derived or (rarely) written by a
     manual `bootstrap_iv_history.py` run before this fix existed.

3. **`IVHistoryStore.get_historical_ivs()` excludes
   `IV_SOURCE_SYNTHETIC_BOOTSTRAP` rows from ranking history by default**
   (a new keyword-only `exclude_sources` parameter, default
   `(IV_SOURCE_SYNTHETIC_BOOTSTRAP,)`; pass `None`/an empty iterable to see
   the raw, unfiltered history for diagnostics). `calculate_true_ivr()`
   needed no code change to inherit this — it calls
   `store.get_historical_ivs(ticker, as_of_date, lookback_days)` positionally
   and picks up the new default automatically; only its docstring was
   updated to document the contract it now relies on. A ticker whose
   ENTIRE prior history is synthetic-bootstrap-only correctly degrades to
   `history == []` and `calculate_true_ivr()` returns `NaN` (fail closed,
   CONSTRAINT #6) — exactly the same degradation as the pre-existing
   empty/warm-start-history case, not a new failure mode.

   `IV_SOURCE_LEGACY_UNKNOWN` rows are deliberately **not** excluded by
   default — every `record_iv()` caller that existed before this migration
   was a genuine chain-derived write in the shipped code (the only
   synthetic writer, `bootstrap_iv_history.py`, only starts tagging
   explicitly from this fix forward), so treating "unknown" as
   "excludable-if-in-doubt" would silently blank out real historical
   ranking on every pre-existing installation for no compensating safety
   benefit. This closes the *forward-looking* contamination risk; it does
   not (and structurally cannot) retroactively re-attribute historical
   rows written before provenance tracking existed.

4. **`bootstrap_iv_history.py`'s docstring and `--help` text corrected** —
   it now states plainly that it produces a realized-volatility-derived
   PROXY, not genuine implied volatility, and that its rows are tagged
   `synthetic_bootstrap` and excluded from `calculate_true_ivr()`'s ranking
   by default.

## Scope / what this does NOT do

- It does not retroactively re-classify any row written before this fix
  shipped — those are honestly tagged `legacy_unknown`, not asserted real or
  synthetic.
- It does not change `bootstrap_iv_history.py`'s underlying math (the
  realized-vol + 3.8% VRP proxy is unchanged) — only its provenance tagging
  and documentation.
- It does not touch `pipeline/production_steps.py` or
  `technical_options_engine.py` at all — both call `record_iv()` with the
  exact same 3-positional-argument signature they always have, and get the
  correct `IV_SOURCE_CHAIN` tag purely from the new default.

## Tests

`tests/test_iv_history_provenance.py`:
- `record_iv()` provenance tagging (default-to-chain, explicit-synthetic,
  upsert-overwrites-source).
- A synthetic-ground-truth before/after regression proof: a planted
  synthetic-bootstrap outlier row is shown to change `calculate_true_ivr()`'s
  result by construction (what the OLD unfiltered code would have computed,
  reconstructed by hand from the raw unfiltered history, vs. what the NEW
  code actually returns) — not just "new code runs without crashing."
- Wholly-synthetic history degrades to `NaN` (fail closed).
- `legacy_unknown` rows are NOT excluded (no regression for existing
  installations).
- Migration idempotency against a real, pre-existing (legacy-schema) SQLite
  file, including repeated construction.
- `bootstrap_iv_history.main()` end-to-end (mocked `yfinance`), asserting
  every row it writes is tagged `synthetic_bootstrap` and none is tagged
  `chain`.

All pre-existing tests touching `IVHistoryStore`/`calculate_true_ivr`
(`tests/test_iv_history_no_lookahead.py`, `tests/test_iv_engine.py`,
`tests/test_options_matrix.py`, `tests/test_engine_context.py`,
`tests/test_orchestrator_e2e.py`, `tests/test_main_orchestrator.py`) pass
unchanged.
