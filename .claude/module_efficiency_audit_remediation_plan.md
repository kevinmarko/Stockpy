# Module Efficiency Audit — Remediation Plan

Companion to [`docs/module_efficiency_redundancy_audit.md`](../docs/module_efficiency_redundancy_audit.md).
That document has the full file:line evidence, drift/correction notes, and severity reasoning for
every finding (F1-F15) referenced below — read it before starting any PR here.

Ordered by (safety value ÷ risk). Every PR here is runtime logic and takes its own branch + PR;
nothing in this list goes directly to `main`.

**Out of scope for remediation, report-only per the agreed risk posture:** `signals/`, `sizing/`,
`execution/`, `validation/`. A "harmless" dedup in sizing or signal math can silently move live
position sizing on a real capital account. This rules out F10 (statistics consolidation) entirely,
and rules out batching `LGBMRankerSignal` specifically (tempting after F1, but it's trading logic).

---

**PR 1 — OPEN ([#928](https://github.com/kevinmarko/Stockpy/pull/928)).** Close the vectorization guard's blind spot (F1). Add `.apply(axis=1)` to
`_BANNED_METHODS` in `tests/test_no_iterrows_in_core_engines.py`, with the 7 current offenders
(`MultifactorSignal`, `CrossSectionalMomentumSignal`, `MacroRegimeSignal`, `LGBMRankerSignal`,
`NewsCatalystSignal`, `RegimeMultiplierSignal`, `SectorNeutralQualitySignal`) added to
`ALLOWED_EXCEPTIONS` so CI stays green. Correct the `CLAUDE.md:353` /`AGENTS.md` vectorization claim
in the same PR (the `sync_agent_docs.sh` hook mirrors the two files automatically) — and correct it
accurately: per the audit, none of the 7 modules do genuinely expensive per-row work (the two-phase
`pre_compute`/`compute` pattern already makes 5 of them cheap dict lookups; the other 2 are trivial
conditionals), so the corrected doc language should describe this as a debt-visibility gap, not an
active performance emergency. No runtime behavior changes.

**PR 2 — OPEN ([branch: unify-safe-float-helper](https://github.com/kevinmarko/Stockpy/tree/unify-safe-float-helper)). 5 of 7 copies migrated.**
Unify `_safe_float` (F2). New `numeric_utils.safe_float` (stdlib-only leaf, `None`/NaN/±inf all
filtered, real `float()` cast) is now the single canonical implementation. First commit migrated the 4
copies confirmed behaviorally compatible on their own: `reporting/state_snapshot.py`'s
`_safe_float_or_none`, `pilots/vol_mispricing.py`, `api/pilots_api.py`, `data/fmp_feeds_company.py` —
each migrated to a one-line import alias, verified with the existing test suites plus a new dedicated
`tests/test_numeric_utils.py`.

**Follow-up commit, same PR: `data/fmp_feeds_market.py` migrated too**, with two required companion
fixes landed in the same commit (this copy returned `float('nan')`, never `None`, on a bad value, and
was deferred out of the first pass for exactly this reason — folding it in unguarded would have been
genuinely risky in *both* directions): (a) `fetch_insider_stats`'s `total_disposed == total_disposed
and total_disposed > 0` NaN-self-comparison idiom depended on the old NaN-not-None behavior — now an
explicit `is not None` check, and on BOTH operands of the division (`total_acquired` can independently
be `None` too under the new contract, which a `total_disposed`-only guard would have missed and left a
live `TypeError` risk); (b) `fetch_realized_volatility`'s own exception-path fallback already returned
`{"hv_10": None, "hv_30": None, "hv_90": None}` — inconsistent with its own happy-path NaN-returning
`_safe_float` — and two real downstream consumers, `pilots/unusual_options_flow.py` and
`pilots/options_alerts.py`, both gate on `hv_30 is not None`, a check a NaN value silently slipped
past, letting a bad historical-vol reading leak into the IV-vs-HV comparison undetected; migrating
`_safe_float` closes this directly, both paths now agree on `None`. Verified: full
`tests/test_fmp_feeds_market.py` (55 tests, including 4 new regression tests for both fixes) plus
`tests/test_numeric_utils.py`/`tests/test_fmp_feeds_company.py`/`tests/test_production_steps_fmp_stubs.py`
all pass; `tests/test_options_snapshot.py` has 11 pre-existing failures in this sandbox unrelated to
this change (a `pandas_ta_classic`/numba cache-locator error against the system Python 3.14 framework
install this worktree happened to run under, reproduced identically on the pre-migration baseline).
See `numeric_utils.py`'s own module docstring and `data/fmp_feeds_market.py`'s own module docstring for
the full reasoning.

**Remaining, untouched, report-only per the agreed risk posture**: `engine/advisory.py:1882`'s
no-filter copy (advisory-path trading logic; currently leaks NaN unfiltered, and fixing that is a real
behavior change needing explicit user approval) and `validation/validation_history_store.py` (the
`validation/` package).

**PR 3 — OPEN ([#929](https://github.com/kevinmarko/Stockpy/pull/929)).** Fix the N+1 in the per-cycle pipeline (F5). Replace `_apply_symbol_rating_columns`'s
per-ticker `.map()` (`pipeline/production_steps.py:627`) with the existing batched
`get_excluded_symbols()` (`rating/symbol_rating_store.py:209`), and vectorize line 636's
`dashboard_df.apply(_excluded, axis=1)`. Self-contained, diagnostic-column-only, no trading logic.
Confirmed untouched by any recent PR, so no merge-conflict risk with in-flight work. Highest measured
win per unit of risk.

**PR 4 — DONE.** "Consolidate the Black-Scholes holdouts and the regex (F3, F4)". Two
parts landed: (1) the `options_gex.py` regex drift (F3) — fixed, `$` is now required, matching the
canonical pattern exactly, with 4 new regression tests including a direct parity check against
`options_risk.py`'s own regex object. (2) The `vol_sqrt_t` clamp-vs-early-return divergence (F4) —
investigated by attempting the "obvious" fix and testing it empirically first, which reversed the
original finding's direction: the canonical `options_risk.py` clamp-and-continue path produces a
spurious ~3.6e9 gamma for a genuinely negligible-but-nonzero `vol_sqrt_t` input, while
`options_gex.py`'s `return 0.0` avoids it. Decision: `options_gex.py`'s behavior is KEPT, documented
inline, and pinned by two regression tests (one of which is a permanent witness for the canonical
function's own spurious-value behavior, so a future change there doesn't silently break this
reasoning). `options_risk.py`'s own numerical guard was deliberately left unfixed — real, narrow bug,
but it has 7+ reuse sites and deserves its own dedicated PR.

**Remaining three migrations — DONE (branch: migrate-bs-pricer-holdouts).** All three now delegate to
`pilots.options_risk.calculate_black_scholes_greeks`, each proven numerically equivalent on a seeded
grid before landing, matching the ETF-transmission flag-off parity precedent:

- `pilots/multi_leg_pricing.py::calculate_black_scholes_leg_greeks` — thin wrapper returning the
  canonical function's full dict (a strict superset of the original 5-key contract). One genuine,
  strictly-additive behavior fix along the way: the old copy compared raw `option_type` without case
  normalization (an uppercase `"CALL"` silently fell through to the put branch); the canonical
  function's `str(option_type or "call").lower().strip()` closes that. Dead `math`/`scipy.stats.norm`
  imports and the now-unused `_DEGENERATE_THRESHOLD` constant removed.
- `pilots/realtime_risk_streamer.py::compute_black_scholes_unit_greeks` + `parse_option_symbol` —
  both migrated (F3's regex duplicate + F4's Greeks duplicate in one file). `parse_option_symbol` is
  now a direct re-export from `options_risk.py` (byte-identical regex/logic; the old copy's `if not
  symbol: return None` guard was confirmed dead — both real call sites already normalize to a
  non-empty string before calling it). `compute_black_scholes_unit_greeks` stays a thin wrapper
  returning its original narrower 4-key contract. `api/ws_api.py`'s external `from
  pilots.realtime_risk_streamer import ... parse_option_symbol` import continues to resolve correctly
  through the re-export. Dead `math`/`re`/`scipy.stats.norm`/`settings` imports and
  `_TRADING_DAYS_PER_YEAR`/`_OPTION_SYM_RE` removed (`_DEGENERATE_THRESHOLD` kept — used elsewhere in
  the file).
- `pilots/dispersion_trading.py::calculate_straddle_vega` — migrated to delegate, now consistent with
  `calculate_option_price()` a few lines below it. **Investigated first, not assumed safe**: vega's
  formula (`spot * norm.pdf(d1) * sqrt(t_years)`) does not divide by `vol_sqrt_t` the way gamma's
  denominator does, so it does not exhibit F4's documented spurious-blowup failure mode — empirically
  confirmed at the exact reproduction inputs from `options_gex.py`'s own regression test
  (`spot=100, strike=100, t_years=1e-11, sigma=1e-7`): canonical `gamma` there is ~3.6e9 (the known
  bug) while canonical `vega_raw` is a sane `0.000114`. This migration was therefore safe where a
  hypothetical gamma migration through the same canonical function would not have been. Dead
  `math`/`scipy.stats.norm`/`settings` imports removed (confirmed unused file-wide, not just in this
  function).

All three: `tests/test_pilots_strategy_matrix.py`'s auto-discovered AST allowlist updated per module
(new `pilots` cross-import for `multi_leg_pricing`/`realtime_risk_streamer`, both already-permitted
for `dispersion_trading`); new seeded numeric-equivalence regression tests added to each module's own
test file; `docs/settings_field_census.{json,md}`/`settings_liveness.json` regenerated (import-graph
shape changed). Full combined suite (147 tests across the 3 target files + the AST guard + the
canonical pricer's own tests) passes; `ruff --select=F821,F822,F823,E9` clean.

**PR 5 — Add `get_quotes_batch` to the provider ABC (F6).** The loops exist because
`MarketDataProvider`'s ABC has no batch method — `api/data_api.py`'s own docstring concedes this.
Add it, default-implement it as the current loop so no provider breaks, override it for FMP via the
existing `fmp_client.batch_quote()`, then migrate call sites (`api/data_api.py:645`,
`pilots/options_risk.py:421`, `pilots/scenario_matrix.py:400`, `data/paper_account_store.py`'s
`settle_expired_options`, `pilots/dispersion_trading.py:779-808`, `evaluation_engine.py:1114` via the
existing `data/historical_store.py:1039` `get_bars_bulk()`). Fixes the cause, not the instances.
Confirmed none of these sites were touched by the recent unbounded-blocking-call sweep, so this PR
starts from a clean, unaffected baseline.

**PR 6 — WITHDRAWN.** Originally "Guard `api/metrics_api.py` (F7)". Re-verified while
attempting implementation, not just re-read: `api/metrics_api.py`'s own module docstring explicitly
permits the heavy imports this PR proposed to guard against (unlike `pilots_api.py`, which the
existing guard is deliberately scoped to), and `tests/test_metrics_api.py` monkeypatches these
engines at the module level 56 times — a lazy-import migration would silently bypass every one of
those mocks, not a mechanical no-op as originally scoped. See F7's corrected writeup in
`docs/module_efficiency_redundancy_audit.md` for the full reasoning. No PR is scheduled for this
finding without a much larger, separately-scoped test-mocking redesign.

**PR 7 — OPEN ([#930](https://github.com/kevinmarko/Stockpy/pull/930)).** Rate-limiter parity (F8). Add `data/cross_process_throttle.wait_turn` to the GDELT
throttle in `sentiment_sources.py`; add a cooldown/circuit-breaker state machine to
`edgar_fundamentals.py` matching the `_fmp_cooldown_until`/`_fmp_consecutive_failures` pattern already
in `fmp_client.py`. Both are pure additions of protection that a sibling module already has — no
existing behavior changes, only new failure-mode coverage.

**PR 8 — DONE, scoped down from the original plan (F9).** Structural test guard landed;
the `__init__` dedup was investigated and deliberately NOT done — see below for why.

**The load-bearing half (the structural guard) shipped as planned:**
`tests/test_store_isolation_contract.py`, auto-discovering every `*_store.py` file via glob (mirrors
`tests/test_pilots_strategy_matrix.py`'s `pilots/*.py` auto-discovery — a new store file is picked up
the next time this test runs, no hand-maintained list to forget). Four properties, all AST-based
(not regex-over-source, after a first draft flagged `tests/_db_isolation.py`'s own docstring — which
merely *describes* the isolation pattern in prose — as a false-positive "unguarded construction";
switching from `re.finditer` to real `ast.Call` node matching eliminated the whole class of
docstring/comment false positives): (1) every `*_store.py` file is either auto-detected as
SQLAlchemy/`db_config`-backed or explicitly listed in `NON_SQL_STORES` with a documented reason
(`cache/cache_store.py`'s deliberately-separate cache DB; `execution/receipts_store.py`,
`llm/status_store.py`, `pilots/follows_store.py`, `pilots/scan_config_store.py`'s JSON/JSONL files —
none of these route through `db_config` because none of them are SQL at all); (2) every real store
class's (name ends `Store`, not `_Offline...`) `__init__` is statically checked to never default a
`db_url`/`db_path`/`sqlite_path` parameter to a hardcoded string literal (the exact CWD-relative
`db_path` bug class, already fixed once in `historical_store.py`/`forecast_tracker.py` per this
audit's own "Now fixed" note) and to actually call `resolve_database_url()` somewhere in its body;
(3) every DIRECT, implicit (no explicit url override) construction of a store class anywhere under
`tests/` is required to be protected — either by a `conftest.py` autouse fixture (parsed textually
from the existing `_isolate_*_db_in_tests` pattern: `import X.Y as alias` + `monkeypatch.setattr(alias,
"resolve_database_url", ...)`) or by file-local evidence in the same test file
(`tests/_db_isolation.py`'s `redirect_class_to_memory_db`/`make_memory_db_init`, a
`settings.DATABASE_URL` patch, or a `mock.patch`/`monkeypatch.setattr` targeting the class's own
dotted import path — all three patterns already in active use somewhere in this suite, discovered by
re-auditing every non-`conftest.py`-covered store's actual production call sites by hand before
writing the check); (4) a regression guard that the five currently-known `conftest.py` fixtures
(`validation_history_store`, `execution_audit_store`, `broker_fills_store`, `paper_account_store`,
`transactions_store`) stay registered. Verified genuinely load-bearing, not just decorative, via a
throwaway sanity script exercising the guard's own helper functions directly: it correctly flags a
hardcoded literal `db_url` default, correctly flags a missing `resolve_database_url()` call, correctly
flags a real bare `SomeStore()` construction in a test file, and correctly does NOT flag the same
class name appearing in a docstring or an explicit `db_url="sqlite:///:memory:"` override. Passes
clean against the current, un-refactored codebase (4/4 tests). Honest scope boundary stated in the
module's own docstring: property 3 only catches a *direct, literal* construction call inside a
`tests/*.py` file — it does not perform call-graph/reachability analysis into production code, so it
cannot prove a deeply-nested production function reachable only via a test that never names the store
class by name is safe. Every store NOT flagged as needing isolation was individually hand-audited
(2026-08-29, this PR) by tracing its production call sites and confirming the only currently-reachable
test paths either keep the gating settings flag at its coded-safe default (restored every test by
`conftest.py`'s `_clean_settings_between_tests`) or explicitly monkeypatch the store class — a
point-in-time fact, not a guarantee, which is exactly why the guard exists: to catch the *next* test
that reaches one of these classes carelessly.

**The `__init__` dedup was investigated and deliberately skipped — not "not attempted," but a real
architectural conflict discovered during investigation.** Confirmed (re-verified against current line
numbers, some drift from the original audit): 9 of the 10 originally-cited stores share a
byte-for-byte-identical `__init__` body
(`db_url = db_url or resolve_database_url(); ... Base.metadata.create_all(self.engine) ...`) —
`sizing/cap_audit_store.py`, `data/sector_correlation_store.py`, `desktop/run_history_store.py`,
`validation/validation_history_store.py`, `transactions_store.py`, `data/broker_fills_store.py`,
`data/cache_long_short_store.py`, `mcp_oauth_store.py`, `rlhf_calibration_store.py`,
`rating/symbol_rating_store.py` — plus `data/execution_audit_store.py` (near-identical, one extra
`sqlite_path` convenience kwarg) and `execution/live_trade_proposals_store.py` (confirmed: real
`__init__` is at line 102, not 53 — lines 53/63 are two sibling exception classes'
`__init__`s, correctly excluded by the guard's "class name ends in `Store`" filter). The blocker: every
existing `conftest.py` isolation fixture (and, by design, this PR's own new structural guard) works by
`monkeypatch.setattr(<store's own module>, "resolve_database_url", ...)` — replacing the name in THAT
MODULE's namespace, which only works because each store's `__init__` calls the bare name
`resolve_database_url()`, resolved via Python's normal name lookup against the *function's own*
`__globals__` (i.e. the store's own module, where `from db_config import resolve_database_url` bound
it locally). Moving that call into a shared base class defined anywhere else (`db_config.py` or a new
module) would relocate the lookup to the BASE's `__globals__` — silently defeating
`monkeypatch.setattr(<store_module>, "resolve_database_url", ...)` for every migrated store, for both
current and future tests, with no error (the patched name would simply never be read). Confirmed this
isn't merely theoretical for the tests that exist today: `tests/test_investyo_mcp_server.py` and
`tests/test_paper_account_store.py` both construct `TransactionsStore()`/`PaperAccountStore()` bare
(no `db_url=`), relying entirely on `conftest.py`'s `_isolate_paper_and_transactions_db_in_tests`
fixture's module-level patch — proof the pattern is load-bearing today, not just a theoretical future
risk. A working-but-clever fix exists (dynamically resolving `resolve_database_url` via
`sys.modules[type(self).__module__]` instead of a bare name lookup) but was rejected: it trades an
~9-line, fully mechanical, easily-greppable duplication for metaprogramming that actively conflicts
with `db_config.py`'s own stated design value ("grep this name to enumerate every consumer") and would
make "what does `resolve_database_url` resolve to for store X" require tracing indirection instead of
a single grep. Given the real risk (silently breaking an established, load-bearing test-isolation
mechanism for zero currently-failing test — a regression that would only surface the next time
someone tries to isolate a migrated store and can't figure out why their monkeypatch has no effect)
against the modest reward (~80 lines of duplication removed), this PR leaves all `__init__` bodies
untouched. The PRAGMA-probe migration-wrapper dedup (`transactions_store.py:68`,
`data/historical_store.py:924`/`947`) was correspondingly not attempted either, per the plan's own
"do not let it block landing the structural test guard" instruction — it's lower priority than the
`__init__` dedup it was contingent on, and the guard is the actually load-bearing deliverable.
`data/historical_store.py` (the `_ensure_tables()` outlier, confirmed deliberate) was left untouched
throughout, exactly as planned, and is included in the structural guard's enumeration like every other
store (its `__init__` already correctly calls `resolve_database_url()`, so it passes property 2
without any changes).

**PR 9 — OPEN ([#931](https://github.com/kevinmarko/Stockpy/pull/931)).** Shared atomic-write helper (F11). One `atomic_write_json()` with pid/tid-scoped temp
names, matching `runtime_flags_writer.py:473`'s existing race-safe pattern. Migrate the two
byte-identical `reporting/pairs_snapshot.py:105` / `reporting/options_snapshot.py:67` copies first
(both currently use collision-prone `path.with_suffix(".tmp")`, not pid-scoped).

**PR 10 — Relocate `gui/env_io.py` (F13).** Move to top-level `env_io.py`, leaving a re-export shim
so the frozen GUI keeps working. The confirmed live-code call sites to update are narrower than first
thought: `runtime_flags_writer.py:189` and `conftest.py:140` only (the audit corrected two other
citations — `alerting.py` and `diagnostics_and_visuals.py` import different `gui.*` submodules
entirely, never `env_io`). Mechanical, but touches the credential-write allowlist — own PR, careful
diff read regardless of the narrower blast radius.

**PR 11 (spike) — Generate mock fixtures from the live schema (F12).** The only fix addressing the
root cause. FastAPI already emits an OpenAPI schema for all 173 routes (current count, up from 165 at
audit time — re-confirm the count when this spike starts); generate `types.ts` or at minimum a
parity-checking test from it, so a backend response-model change fails CI instead of surfacing as a
blank screen. Largest and least certain — scope as a spike, do not start before PRs 1–10 land.

---

Explicitly **not** scheduled: splitting `api/pilots_api.py` into `APIRouter`s (`CLAUDE.md` records the
single-file layout as deliberate); `Gravity AI Review Suite.py`; the F10 statistics consolidation
(including the newly-noted fifth Sortino implementation inside `validation/metrics.py` itself) and any
`signals/`/`sizing/`/`execution/`/`validation/` changes — report-only per the agreed risk posture,
including batching `LGBMRankerSignal`, which the audit found isn't actually the cost driver F1
originally claimed, and which sits squarely in trading logic regardless.

**PR 12 — Dead-code removal pass (F15).** Two real deletions once independently re-confirmed dead at
the time this PR starts: `ml/drl_market_maker_ppo.py` (644 lines, only `tests/test_drl_market_maker_ppo.py`
imports it) and `validation/walk_forward.py` (431 lines, only `tests/test_walk_forward.py` imports
it — note its own file also exercises 2 of the 4 dead margin wrappers below, so removing it first
changes what "dead" means for the wrappers). Do not delete `execution/overnight_guardrails.py` —
its own docstring discloses the missing wiring as a deliberate, open decision needing explicit
operator sign-off, not an oversight; leave it and this PR alone. Smaller items in the same pass:
delete `validation/regime_diagnostics.py::select_optimal_model` and the 4 backtest-margin wrapper
functions in `validation/options_selling_backtest.py` (both `validation/` — report-only per the
agreed risk posture, so scope this half of the PR as report-only too unless the user approves code
changes there); remove the 3 gate-nothing settings fields (`OPTIONS_EARNINGS_CRUSH_ENABLED`,
`PROMPT_MAX_CHARS`, `SENTIMENT_PIT_MIN_MONTHS`) from `settings.py` and `gui/env_io.py`'s
`ALLOWED_KEYS` together, in one commit, since removing one without the other reintroduces a drift
gap of the same shape as F2; delete the 7 dead API endpoints and 3 dead `client.ts` methods together
per pair (a route and its wrapper are one unit — deleting one without the other just moves the dead
code); leave `threeDisposal.ts`'s 6 unused functions and the 3 unused `execution/` exception/enum
types alone (CLAUDE.md already calls the former cosmetic; the latter reads as forward-declared API
surface, not an accident). Re-run each finding's grep from the audit doc before deleting anything —
a module confirmed dead when F15 was written may have gained a caller since.

## Verification

Per-PR verification is specified inline above. The recurring requirement, unchanged from the original
plan: **PRs 2 and 4 must prove numeric/behavioral equality on a seeded fixture before deleting any
implementation**, matching this repo's precedent for the ETF-transmission flag-off parity proof.

Before starting each PR, re-run that finding's repro command from the audit doc — the codebase moves
fast enough (3 unrelated PRs landed between the original audit draft and this write-up alone) that a
line number or count cited here may have already drifted again by the time work starts.

## Status

F15 landed; PR 12 above is its removal pass, sized to what F15 found.
