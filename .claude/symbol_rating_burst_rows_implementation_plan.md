# symbol_rating_burst_rows: test-suite leakage into live `symbol_rating_events`

Status: PHASE 1 (investigation + plan). No code edited, live DB opened read-only (`mode=ro`) only.
Date of investigation: 2026-10-05. Python: `/Users/kevinlee/Stockpy-live/.venv/bin/python`.

## 1. Verdict

The sub-second burst rows are **pytest runs writing to the live DB**, not a GUI/API-triggered run.
`rating/symbol_rating_store.py::SymbolRatingStore()` resolves the shared
`~/.stockpy_local/quant_platform.db` by default, `settings.SYMBOL_RATING_ENABLED` defaults to **True**,
and the root `conftest.py` has **no** `_isolate_*` autouse fixture for it. Any test that drives
`pipeline/steps.py` (the "SYMBOL-RATING AUDIT LOG" block, ~L257-275) or
`pipeline/production_steps.py::_record_symbol_ratings` through a bare `SymbolRatingStore()` writes
fixture scores into the operator's real table. The 10:08:55 UTC 2026-10-03 burst is a pytest run;
the 10:08:59 webapp settings save was coincidental (a full suite run also touches settings tests).

It is **ongoing**: during this investigation (14:10-14:21 UTC on 2026-10-05) the live table grew from
27,376 to 27,544 rows while all of this investigation's own pytest runs were redirected to a scratch
`LOCAL_DATA_ROOT` (verified, see 3.3). Those rows came from other concurrent sessions/worktrees.

## 2. Evidence

### 2.1 Schema and who writes
```
symbol_rating_events(id, timestamp, cycle_id VARCHAR(64), symbol, score FLOAT, action_signal, tier, is_held)
```
Production writers of `SymbolRatingStore().record_ratings`:
- `pipeline/steps.py:~275` (`main.py` advisory path; `cycle_id = datetime.now(utc).isoformat()`,
  format `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`, 32 chars, score from `rec.key_indicators["score"]`).
- `pipeline/production_steps.py:743` (`_record_symbol_ratings`, daemon `StrategyEvalStep`; the
  recent commit "keep daemon rating writes off" means the daemon is not writing live rows).
- Readers: `data/portfolio_sync.py:925,1053` (`get_excluded_symbols`), `api/data_api.py`,
  `reporting/state_snapshot.py`; `api/pilots_api.py:1350` (`reinclude`, manual write).
Real cycle_id format and test cycle_id format are identical, so format cannot discriminate.

### 2.2 The burst (2026-10-03)
Query: `select cycle_id,min(timestamp),count(*),group_concat(symbol),min(score),max(score),group_concat(distinct action_signal),sum(is_held),group_concat(distinct tier) from symbol_rating_events where cycle_id like '2026-10-03T10:08%' group by cycle_id order by 1`
Result: 16 cycles between 10:08:46.94 and 10:08:55.31, 1-8 rows each, symbols only the pytest fixture
set (AAPL, MSFT, GOOG, SPY, AGNC, JNJ, NVDA, TSLA), `is_held` 0 or 2, actions HOLD/BUY.
- 3 cycles at 10:08:54.73-.76: AAPL,MSFT, **score 1.0**, BAD (matches `tests/test_progress_emission.py`
  `TestAdvisoryEvalStepProgressWiring`).
- 13 cycles 10:08:46-55.31: score **55.0**, GOOD, symbol-set sequence 2,3,1,1,1,2,8,8,7,7,8 identical to
  `tests/test_run_once.py` (`_make_recommendation` hardcodes `key_indicators["score"] = 55.0`).
  The 8/8/7/7/8-symbol cycles are `TestAdvisoryConcurrency` (AAPL,AGNC,GOOG,JNJ,MSFT,NVDA,SPY,TSLA).

### 2.3 Reproduction (not the live DB)
`LOCAL_DATA_ROOT=<scratch> pytest ...` redirects `db_config.resolve_database_url()` to a scratch DB
(verified by printing it first). Full offline suite (`-m "not network and not slow"`, 11,615 passed)
with a spy plugin wrapping `SymbolRatingStore.record_ratings` and logging `request.node.nodeid`:

| leaking test file | what it writes |
|---|---|
| `tests/test_run_once.py` (TestRunOnce, TestAdvisoryConcurrency; 11 cycles) | 55.0, GOOD, same symbol sequence as the live burst |
| `tests/test_pipeline_smoke.py::TestRunOncePipeline` (2 tests) | AAPL 55.0 |
| `tests/test_progress_emission.py::TestAdvisoryEvalStepProgressWiring` (3 tests) | AAPL,MSFT **1.0** BAD |

Not leakers (already explicit `db_url=` tmp/`:memory:` or class monkeypatched): `test_symbol_rating.py`,
`test_production_steps_symbol_rating.py`, `test_symbol_rating_wiring.py`. Individual runs of
`test_state_snapshot_parity/_advisory`, `test_portfolio_sync`, `test_pilots_api`, `test_data_api`,
`test_production_steps_universe` wrote nothing. This exactly reproduces the live burst, including the
1.0 BAD rows.

### 2.4 Isolation gap
`grep -n -i rating conftest.py` returns nothing. The existing `_isolate_*_db_in_tests` fixtures cover
validation_runs, broker_fills, trends, forecast_tracker, symbol_view, paper/transactions, historical store
only. `tests/test_store_isolation_contract.py` only inspects DIRECT `ClassName(` constructions in test
files and rests on a 2026-08-29 hand audit that assumed gated stores default their flag to False
(e.g. `SIZING_CAP_AUDIT_ENABLED`). `SYMBOL_RATING_ENABLED` defaults **True** (`settings.py:~1817`) so
that assumption fails for this store, and the writes are reached indirectly via pipeline steps.

### 2.5 How widespread (all history)
Discriminator: genuine cycles are the daily `main.py` advisory run (~12:47-12:51 UTC = 08:47 ET,
27-32 symbols, many distinct scores). Every test-fixture cycle has exactly **one distinct score**.
```
select count(distinct cycle_id), count(*) from symbol_rating_events;          -- 5709 cycles, 27488 rows (snapshot)
-- cycles by number of distinct scores:
select n_sc,count(*),sum(n) from (select cycle_id,count(*) n,count(distinct score) n_sc
  from symbol_rating_events group by cycle_id) group by n_sc;
```
- `n_sc = 1`: **5,677 cycles / 26,555 rows (96.6%)**, first 2026-08-13 08:07:42, still being written today.
  Scores only 55.0 (4,710 cycles) and 1.0 (983 cycles); tiers 24,643 GOOD / 1,968 BAD (figures at a later
  snapshot; 1,395 rows flagged `is_held=1`). 1,668 of those cycles are single-row.
- `n_sc >= 4`: **32 cycles / 933 rows** across 31 distinct days (31 at hour 12 UTC, 1 manual run at 14:26 on
  2026-09-04). These are the only genuine rows.
- Fixture symbols dominate (AAPL 5,986, MSFT 3,918, SPY 2,819, ...) but contamination also covers real
  tickers: three large 2026-08-13 08:07:52 test bursts of ~512-517-symbol cycles (5,556 rows), and
  10-30 cycles/day on 08-14..08-21 using the operator's real watchlist symbols (AAL, ABR, AM, AQN, CBRS,
  IBN, ... ) at 55.0. Any rule keyed on "fixture symbols only" under-counts; key on "one distinct score".
- Per-day cycle counts show the pattern (e.g. 2026-08-22: 903 cycles / 3,344 rows, genuine: 1 cycle).
- Zero `manual_reinclude` rows exist.

## 3. Impact on auto-drop (live: `SYMBOL_RATING_AUTO_DROP_ENABLED=true` via runtime_flags since 2026-08-07, threshold 5)

`get_consecutive_bad_cycles/get_excluded_symbols` read per-symbol rows **ordered by `id desc`** with no
cycle or time grouping, so interleaved fake rows directly alter the streak. Held symbols are never
excluded (`should_exclude`).

Replay (read-only, script `analyze2.py` logic): rebuild each symbol's history in id order twice, once with all
rows and once with only genuine rows (n_sc>1); compare `streak>=5 and not last.is_held` after every row.

1. **Fake GOOD rows reset/masked real BAD streaks (real harm to the universe decision).**
   - AQN: real BAD streak reached the threshold on 2026-08-19 12:48. Fake 55.0 rows
     (`08-19 13:14`-`13:24`, 9+ rows, 88 fake rows total) reset it to 0. Real streak = 10 today; with fake rows
     it was 5 (reached only on 2026-09-07 12:48). 21 row-states where real history says "excluded" and the
     live table said "not excluded"; about 19 days of delayed exclusion. Currently excluded either way.
   - CBRS: 1 masked state (fake 55.0s on 2026-08-21 18:19; streak delayed one cycle, 6 real vs 5 observed).
   - AAL: real streak 32 vs 27 observed, but AAL is held so no effect.
2. **Fake BAD rows (score 1.0, `is_held=0`) can fabricate an exclusion for a tracked fixture-named symbol.**
   Score 1.0 < 35 is BAD, written as 3 consecutive BAD cycles per run (3 cycles x many runs; streak
   accumulates across runs because nothing GOOD intervenes for AAPL/MSFT between them). Replay: MSFT 78
   and AAPL 13 row-states where fake rows alone pushed the symbol to `streak >= 5 and not held`
   (first MSFT 2026-08-13 14:25:27, first AAPL 2026-08-14 20:44:21). If either was in the tracked universe
   and unheld at a read, it would have been auto-dropped; I cannot prove a read landed in those windows
   (windows are millisecond-to-seconds wide; they are quickly reset by the following 55.0 rows).
3. **Current state:** neither effect changes today's exclusion set: both views exclude exactly AQN and CBRS.
   The long streaks you see (UPBD/KRO/RWT 31, UWMC 28, AAL 27-32, ABR 19, ARCC 16, ...) are all `is_held=1`
   on the last row, so are protected. AAPL/MSFT currently have a real streak of 1.
4. The 08-13 08:07 table-wide bursts are single score 55.0 GOOD so they only reset streaks, never create them.

Net: contamination did not cause a wrong live auto-drop that I can prove, but it delayed AQN's (and CBRS')
exclusion and the table gives wrong results to any API/webapp view of streaks, rating history
(`get_recent`), and `docs` metrics. Removing it is low-risk and strictly makes behavior match the intended
semantics. Re-evaluate: after cleanup, no symbol's exclusion state flips today (AQN/CBRS stay excluded).

## 4. Proposed fix (Phase 2; tier = "Everything else": touches test infra only but keep a PR)

Branch `fix-symbol-rating-test-isolation`.

### 4.1 `conftest.py`: root autouse isolation (primary fix)
Add `_isolate_symbol_rating_db_in_tests(monkeypatch, tmp_path)` next to `_isolate_symbol_view_db_in_tests`
(same style: lazy import, `monkeypatch.setattr(_srs, "resolve_database_url", lambda: f"sqlite:///{tmp_path/'isolated_symbol_rating.db'}")`).
- Per-test tmp FILE, not `:memory:`: one cycle does write-mode `SymbolRatingStore()` then readonly readers; they must see each other.
- Only the baseline resolution is redirected; tests passing `db_url=` are unaffected.
- Open question to verify in Phase 2: `SymbolRatingStore(readonly=True)` against a not-yet-created tmp file
  (`create_readonly_db_engine`). Production readers already wrap it fail-open; run the full suite and fix any
  test that depended on the real DB. If needed, create the schema in the fixture so readonly works.
- Docstring cites this incident + `docs/known_issues/symbol_rating_burst_rows_test_leakage.md`.

### 4.2 Structural guard so it cannot recur silently
Extend `tests/test_store_isolation_contract.py` (or a new focused test) with a rule: any `*_store.py` that is
written from a settings-gated production path where the gating flag's **coded default is True** must have a root
`conftest.py` `_isolate_*` fixture patching that module's `resolve_database_url`. Concretely for now:
`test_symbol_rating_store_has_root_isolation_fixture` (AST/text check of `conftest.py`) plus the behavioral
test below. Keep honest scope note: still not call-graph analysis.

### 4.3 Defense in depth (optional, small)
Autouse guard that fails a test if `SymbolRatingStore()`'s engine URL equals the real
`resolve_database_url()` computed from `settings.LOCAL_DATA_ROOT` captured at session start (belt and braces,
catches future store modules too if generalized). Do not run if it risks collection cost; document as optional.

### 4.4 Cleanup script (separate, operator-approved; DO NOT RUN in this task)
`scripts/cleanup_symbol_rating_test_rows.py`, uses `scripts/_bootstrap.py`, resolves DB via
`db_config.resolve_database_url()` (no literal path).
- Default **dry run**: prints counts, per-day breakdown, the exact id list bounds, the before/after
  `get_excluded_symbols()` comparison and any symbol whose exclusion state would flip.
- `--apply` required to delete. Before deleting: `sqlite3.Connection.backup()` to
  `LOCAL_DATA_ROOT/backups/quant_platform_pre_symbol_rating_cleanup_<UTC>.db` (refuse if backup fails/exists),
  then one transaction: `DELETE FROM symbol_rating_events WHERE cycle_id IN (SELECT cycle_id FROM symbol_rating_events
  WHERE cycle_id <> 'manual_reinclude' GROUP BY cycle_id HAVING COUNT(DISTINCT score)=1 AND MIN(score) IN (55.0, 1.0))`
  plus an `id <= --max-id` bound (default: max id at dry-run time) so concurrent legitimate writes are untouched.
  Post-checks: remaining rows should equal the dry-run prediction (933 genuine rows at 2026-10-05 snapshot,
  plus anything newer); VACUUM not run automatically.
- Safety rails: refuses if daemon/advisory job is mid-run (check lock/Control API status best-effort), refuses
  if the remaining row count differs from the dry-run's by more than the rows written since, and never touches
  other tables. Idempotent.
- Run only after 4.1 is merged and the operator approves (and ideally after the daemon's current cycle).
- Edge: a genuine single-symbol real universe with one distinct score would be matched by the rule. Not observed
  (all 32 genuine cycles have >=27 rows and >=4 distinct scores); the script also prints any matched cycle with
  a score outside {55.0, 1.0} or a row count > 8 and symbols outside the fixture set for human review
  (the 2026-08-13 large bursts and 08-14..08-21 watchlist cycles are in that category and were individually
  verified as single-score 55.0).

## 5. Tests (Phase 2)
- `tests/test_symbol_rating_test_isolation.py`:
  1. `SymbolRatingStore()` with no args inside a test writes to a path under `tmp_path`, not under
     `settings.LOCAL_DATA_ROOT` (assert `engine.url`).
  2. Drive the real write path (`pipeline.steps` rating block / `_record_symbol_ratings`) with
     `SYMBOL_RATING_ENABLED=True`, then assert the *live-path* DB (a scratch `LOCAL_DATA_ROOT` set in-test) has no
     `symbol_rating_events` rows / file not created.
  3. A write-then-readonly-read in one test sees the data (shared-file semantics).
  4. conftest has the fixture (guard from 4.2).
- Cleanup script tests (`tests/test_cleanup_symbol_rating_test_rows.py`) on a tmp SQLite: dry run deletes nothing;
  `--apply` backs up first, deletes single-score cycles only, keeps multi-score cycles and `manual_reinclude`,
  respects `--max-id`, is idempotent, and reports streak flips (AQN-style masked exclusion scenario).
- Verification gate: `pytest tests/test_symbol_rating.py tests/test_run_once.py tests/test_pipeline_smoke.py
  tests/test_progress_emission.py tests/test_production_steps_symbol_rating.py tests/test_store_isolation_contract.py
  tests/test_symbol_rating_test_isolation.py`, then full `make ci`; re-run the spy plugin against a scratch
  `LOCAL_DATA_ROOT` and assert the scratch DB stays empty (proves nothing reaches the default path).
  Also re-count live `symbol_rating_events` growth over a test run (read-only) after merge: must be 0.

## 6. Docs (Phase 2 deliverable)
- NEW `docs/known_issues/symbol_rating_burst_rows_test_leakage.md`: what happened, evidence (2.2-2.5),
  root cause (default-True flag + no isolation fixture + indirect reach), impact (AQN/CBRS delay, fake BAD risk
  for AAPL/MSFT), fix, cleanup status (operator-gated), and the "flag default True => needs root fixture" rule.
- `docs/known_issues/README.md`: add index entry.
- `docs/architecture/testing.md`: add the new fixture and test files to the index.
- `CLAUDE.md`/`AGENTS.md` (mirrors; edit both): add `_isolate_symbol_rating_db_in_tests` to the list of
  isolation fixture examples in "Test isolation for implicit-default stores" only. No other rule changes.
- `docs/RUNBOOK.md` (small): add a short "contaminated rating history" note pointing at the cleanup script,
  if the operator wants it run.
- Update `docs/architecture/data-layer.md` or the rating doc only if it states the store's isolation story.
- PR artifacts per CLAUDE.md rule 6: `.claude/symbol_rating_burst_rows_task.md`,
  `.claude/symbol_rating_burst_rows_walkthrough.md` (this file is the implementation plan).

## 7. Risks / open items
- Another pytest-writing path may exist that the spy did not hit (e.g. `-n` workers, `-m network` tests).
  The 4.3 guard and post-merge zero-growth check cover it.
- Other settings-gated, default-True stores could have the same gap; Phase 2 should grep for
  `*_ENABLED: bool = Field(default=True` gating a `*_store` write and list any without a root fixture.
- Cleanup is irreversible without the backup; keep the backup until the operator confirms.
- Rows currently still being appended by other sessions: run the cleanup with `--max-id` bounded, and re-run once
  after 4.1 lands everywhere (other worktrees need to rebase onto main first, otherwise their pytest runs keep
  leaking until they sync).
