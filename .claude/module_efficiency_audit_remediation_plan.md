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

**PR 4 — PARTIALLY DONE.** "Consolidate the Black-Scholes holdouts and the regex (F3, F4)". Two
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

**PR 8 — Shared store base + a structural test guard (F9).** Extract the ~9-line `__init__` block
duplicated across 10 stores (correcting the tenth site's citation: `execution/live_trade_proposals_store.py`'s
real `__init__` is at line 102, not 53). Separately extract the PRAGMA-probe migration wrapper
duplicated at `transactions_store.py:68`, `data/historical_store.py:924`/`947` (3 sites, not 5 — the
`execution_audit_store.py`/`paper_account_store.py` sites use a deliberately different
Postgres-portable try/except-`ALTER` idiom per `execution_audit_store.py`'s own docstring; leave that
second idiom as-is unless a separate decision is made to standardize on one). The load-bearing half is
**not** the dedup: add a test enumerating every `*_store.py` that fails if a store's default DB
resolution bypasses `db_config.resolve_database_url()` or has no autouse isolation fixture — this
turns a recurring incident class into a CI failure. Small stores first; `data/historical_store.py`
(the `_ensure_tables()` outlier, confirmed deliberate) last or never. Note: the CWD-relative
`db_path` bug this PR was originally partly motivated by (`historical_store.py`,
`forecast_tracker.py`) is already fixed as of this audit — confirm the new structural test also
passes against that already-fixed state rather than re-fixing it.

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
