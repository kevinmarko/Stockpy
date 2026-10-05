# Step 5.1 walkthrough: one universe builder for `main.py` and the daemon

Plan: `.claude/shrink_step5_retire_main_py_implementation_plan.md` (section 4, "5.1: One universe builder"; operator decision 7: the universe change is accepted as long as every moved symbol is listed).
Branch: `step5-one-universe-builder`, cut from `origin/main` @ `0e88e265` (includes PR 5.0, #1078).

## What the daemon did before

`pipeline/production_steps.py::AsyncDataFetchStep.run()`:
1. called `pilots.discovery.discovery()` itself;
2. called `compute_tracked_universe(watchlist=..., discovered=..., default_tickers=...)`, **without `held`**;
3. set up the data engine;
4. fetched the account snapshot and **appended** held symbols to `ctx.symbols`.

Two differences from `main.py`'s `_build_universe`:
- **Held-in-fallback rule.** `DEFAULT_TICKERS` fired whenever the watchlist and discovery were both empty, even with positions held. `main.py` passes `held` into the union, so its fallback fires only when held, watchlist and discovered are all empty.
- **No closed-position retention.** `CLOSED_POSITION_RETENTION_DAYS` symbols never reached the daemon.

## What it does now

`pipeline/advisory_inputs.py`:
- New `build_universe_detailed(snapshot, *, watchlist_file=None) -> UniverseBuild`. It holds the logic `build_universe` had (held ∪ watchlist ∪ discovered, rating auto-drop, `DEFAULT_TICKERS` only when that union is empty, retention unioned last), unchanged. `UniverseBuild` also carries the per-source sets (`held`, `watchlist`, `discovered`, `recently_closed`) so the daemon's funnel needs no second read.
- `build_universe(snapshot)` is now a one-line wrapper returning `.symbols`. `main._build_universe` still re-exports it, so `main.py` is unchanged.
- `load_watchlist(watchlist_file=None)` takes an optional path. `main.py`'s path still calls it with no argument (zero-arg test stubs keep working).
- A `None` snapshot, or one without positions, counts as no holdings.

`AsyncDataFetchStep.run()`:
1. fetches the account snapshot first: the same `await asyncio.to_thread(main_orchestrator.fetch_account_snapshot)` call as before, only moved up. No new login path; the 20 h cache and the existing `ROBINHOOD_AUTO_REFRESH_ENABLED` gate are unchanged. On failure the universe is built as if nothing were held, as before.
2. calls `build_universe_detailed(snapshot, watchlist_file=ctx.watchlist_file)`;
3. sets `ctx.symbols` from it (live path, or `ctx.market` preset). MockDataEngine mode keeps `["AAPL"]` plus held, as before.

`universe_funnel` keeps every key, plus `recently_closed_added`:
- `default_tickers_is_fallback` now also counts held (`not (held or watchlist or discovered)`).
- `tracked_universe_before_held` now counts the universe symbols that are neither held nor retained. Held symbols are no longer a separate appended stage, so the old count can't be reproduced without a second builder call.
- `held_positions_added` is still `len(robinhood_positions)`.

`ctx.symbols` is now sorted; held symbols used to be appended at the end. Downstream code keys by symbol.

Also fixed two stale comments that pointed at `main.py::_build_context_extras` / `main.py::_build_universe` (now in `pipeline/advisory_inputs.py`).

Not changed:
- `main.py` (no line shifts; the `tests/test_no_missing_call_timeouts.py` allowlist is untouched).
- `RunContext.build_universe_fn` is still an unused stub on the daemon side.
- Discovery, the rating-store read and the retention read run synchronously inside the async step, like discovery and the rating read did before. Retention adds one read-only `broker_fills` query.

## Equivalence and diff gate

### 1. Tests

`tests/test_production_steps_universe.py`:
- existing classes: the discovery patch moved to `pipeline.advisory_inputs.discovery`, where the call now happens;
- `TestDaemonHeldInFallbackRule`: held suppresses the fallback; a failed snapshot still falls back; held still reaches `robinhood_positions`;
- `TestDaemonClosedPositionRetention`: retained symbol added; retention doesn't suppress the fallback; retention survives the rating auto-drop; MockDataEngine mode is still AAPL plus held;
- `TestDaemonMatchesMainUniverse`: 8 input scenarios (all sources together, held-only with defaults, empty account fallback, fallback plus retention, discovery only, auto-drop, auto-drop emptying the union, nothing at all). Each asserts `AsyncDataFetchStep` gives exactly `main._build_universe()`'s list.

`tests/test_main_body_engine_injection.py`: its discovery isolation patch moved to `pipeline.advisory_inputs.discovery` (the old `pilots.discovery.discovery` patch no longer reaches the call).

Break-then-revert check: with `origin/main`'s `production_steps.py` swapped back in, 13 of the 21 tests fail, including 5 of the 8 parity scenarios (the other 3 have no held symbols and no retention, so old and new agree). Restored afterwards.

`tests/test_run_once_advisory_golden.py` passes unchanged.

### 2. Live diff (read-only)

Harness (scratch only, not committed):
- a scratch `LOCAL_DATA_ROOT` holding a copy of `~/.stockpy_local/quant_platform.db` (sqlite backup API from a `mode=ro` source), plus copies of `scan_candidates.json`, `scan_configs.json`, `runtime_flags.json` and `robinhood_cache/account_snapshot.json`;
- the real non-secret `.env` values passed as env vars: `WATCHLIST` (the inline-comment artifact), `DEFAULT_TICKERS` (27), `SYMBOL_RATING_AUTO_DROP_ENABLED=true`, `SYMBOL_RATING_DROP_THRESHOLD_CYCLES=5`, `CLOSED_POSITION_RETENTION_DAYS=180`, `CLOSED_POSITION_RETENTION_MAX_SYMBOLS=25`;
- the real `/Users/kevinlee/Stockpy-live/watchlist.txt`, read-only;
- `AsyncDataFetchStep.run()` from an export of `origin/main` (OLD) and from this branch (NEW), with the snapshot served cache-only (`allow_live_fetch=False`), the data fetch, kill switch and freshness marker stubbed, and every outbound socket connect blocked.

Inputs seen: snapshot fetched 2026-09-22 12:45 UTC with 25 positions; watchlist.txt has 6 tickers (AAL, ABR, AGNC, AM, AQN, T; AQN is dropped by the rating auto-drop in both); 3 discovered (IBN, SKHY, T); retention returns CMCL and PBF.

| | OLD daemon | NEW daemon | `main.py` |
|---|---|---|---|
| symbols | 28 | 30 | 30 |

| Symbol | Change | Reason |
|---|---|---|
| CMCL | added | closed-position retention |
| PBF | added | closed-position retention |

No symbol removed. The held-in-fallback rule changed nothing today, because the watchlist and discovery aren't empty. NEW equals `main.py`'s universe.

Funnel, OLD → NEW: `tracked_universe_before_held` 7 → 3 (definition change above), `tracked_universe_total` 28 → 30, `recently_closed_added` new (2); the other keys are unchanged.

Side effect to disclose: opening the live DB read-only for the copy made SQLite create or refresh its `quant_platform.db-shm` and a 0-byte `-wal` next to the live file. The DB file itself was not modified (mtime unchanged at 15:12). Nothing else under `~/.stockpy_local` changed.

### 3. Cross-sectional effect

12-1m momentum percentile rank recomputed from the scratch DB copy's `price_bars` (last 504 closes, skip 22, lookback 252, `rank(pct=True)` over the universe plus SPY, as the daemon's `tech_raw` includes SPY). SKHY has no stored bars, so 28 names are eligible OLD and 30 NEW.

| Symbol | 12-1m return | Rank OLD | Rank NEW | Δ |
|---|---|---|---|---|
| AAL | 0.168 | 0.714 | 0.700 | -0.014 |
| ABR | -0.509 | 0.071 | 0.067 | -0.005 |
| AGNC | 0.252 | 0.857 | 0.833 | -0.024 |
| AM | 0.177 | 0.750 | 0.733 | -0.017 |
| ARCC | 0.052 | 0.464 | 0.467 | +0.002 |
| ARR | 0.355 | 0.929 | 0.900 | -0.029 |
| CGBD | -0.002 | 0.429 | 0.433 | +0.005 |
| CMCL | -0.232 | — | 0.133 | new |
| DEI | -0.178 | 0.143 | 0.167 | +0.024 |
| DIV | 0.192 | 0.786 | 0.767 | -0.019 |
| DX | 0.244 | 0.821 | 0.800 | -0.021 |
| ET | 0.299 | 0.893 | 0.867 | -0.026 |
| IBN | -0.010 | 0.393 | 0.400 | +0.007 |
| KRO | 0.544 | 1.000 | 0.967 | -0.033 |
| MFA | 0.066 | 0.500 | 0.500 | 0.000 |
| MPT | -0.109 | 0.250 | 0.267 | +0.017 |
| NTDOY | -0.310 | 0.107 | 0.100 | -0.007 |
| PBF | 1.120 | — | 1.000 | new |
| PK | 0.526 | 0.964 | 0.933 | -0.031 |
| PSEC | 0.069 | 0.536 | 0.533 | -0.002 |
| REFI | -0.049 | 0.321 | 0.333 | +0.012 |
| RITM | -0.039 | 0.357 | 0.367 | +0.010 |
| RWT | -0.109 | 0.214 | 0.233 | +0.019 |
| SDIV | 0.135 | 0.643 | 0.633 | -0.010 |
| SPY | 0.159 | 0.679 | 0.667 | -0.012 |
| SRET | 0.117 | 0.607 | 0.600 | -0.007 |
| SYF | 0.100 | 0.571 | 0.567 | -0.005 |
| T | -0.090 | 0.286 | 0.300 | +0.014 |
| UPBD | -0.174 | 0.179 | 0.200 | +0.021 |
| UWMC | -0.743 | 0.036 | 0.033 | -0.002 |

27 of 28 existing names moved (MFA didn't); max |Δ| 0.033 (KRO, which loses the top rank to PBF). The `cross_sectional_momentum` score is `2 * (rank - 0.5)`, so it moves by at most 0.067 before its `SIGNAL_WEIGHTS` weight. UPBD crosses from the bottom quintile label to Q2 (explanation text only; the score is linear).

Not computed: the multifactor z-scores (`Value_Z`, `Quality_Z`, `LowVol_Z`, `Size_Z`, `Multifactor_Composite`). They also shift for every symbol, because the cross-sectional mean and standard deviation now include two more names, but reproducing them needs the full processing-engine fundamentals path. Final action tiers were not recomputed either.

## Verification

- `tests/test_production_steps_universe.py`: 21 passed.
- Full offline suite (`-m "not network and not slow" -n auto --dist loadgroup -p no:randomly`, scratch `LOCAL_DATA_ROOT`): 11573 passed, 18 skipped, 0 failed. The first run's only failures were the settings-census freshness tests, fixed by regenerating `docs/settings_field_census.{json,md}` and `docs/settings_liveness.json` (one `settings.DEFAULT_TICKERS` read left `production_steps.py`, and line numbers moved).
- `ruff check . --select=F821,F822,F823,E9`: clean.

## Docs updated

- `docs/known_issues/daemon_universe_watchlist_divergence.md`: new "Resolution: one universe builder" section with the live diff.
- `CLAUDE.md` / `AGENTS.md`: the universe paragraph and the "Daemon universe-divergence fix" bullet.
- `docs/architecture/data-layer.md` (`compute_tracked_universe` entry) and `docs/architecture/orchestration-entrypoints.md` (retention entry, which said the daemon needed no change).
