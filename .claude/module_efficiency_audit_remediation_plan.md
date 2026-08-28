# Module Efficiency Audit — Remediation Plan

Companion to [`docs/module_efficiency_redundancy_audit.md`](../docs/module_efficiency_redundancy_audit.md).
That document has the full file:line evidence, drift/correction notes, and severity reasoning for
every finding (F1-F14) referenced below — read it before starting any PR here.

Ordered by (safety value ÷ risk). Every PR here is runtime logic and takes its own branch + PR;
nothing in this list goes directly to `main`.

**Out of scope for remediation, report-only per the agreed risk posture:** `signals/`, `sizing/`,
`execution/`, `validation/`. A "harmless" dedup in sizing or signal math can silently move live
position sizing on a real capital account. This rules out F10 (statistics consolidation) entirely,
and rules out batching `LGBMRankerSignal` specifically (tempting after F1, but it's trading logic).

---

**PR 1 — Close the vectorization guard's blind spot (F1).** Add `.apply(axis=1)` to
`_BANNED_METHODS` in `tests/test_no_iterrows_in_core_engines.py`, with the 7 current offenders
(`MultifactorSignal`, `CrossSectionalMomentumSignal`, `MacroRegimeSignal`, `LGBMRankerSignal`,
`NewsCatalystSignal`, `RegimeMultiplierSignal`, `SectorNeutralQualitySignal`) added to
`ALLOWED_EXCEPTIONS` so CI stays green. Correct the `CLAUDE.md:353` /`AGENTS.md` vectorization claim
in the same PR (the `sync_agent_docs.sh` hook mirrors the two files automatically) — and correct it
accurately: per the audit, none of the 7 modules do genuinely expensive per-row work (the two-phase
`pre_compute`/`compute` pattern already makes 5 of them cheap dict lookups; the other 2 are trivial
conditionals), so the corrected doc language should describe this as a debt-visibility gap, not an
active performance emergency. No runtime behavior changes.

**PR 2 — Unify `_safe_float` (F2).** One shared helper with explicit NaN *and* inf filtering.
Migrate the 6 `Optional[float]`-returning copies first (`reporting/state_snapshot.py`'s
`_safe_float_or_none`, `pilots/vol_mispricing.py`, `api/pilots_api.py`, `data/fmp_feeds_company.py`,
`validation/validation_history_store.py`, plus updating call sites). `data/fmp_feeds_market.py`'s
NaN-returning copy and `engine/advisory.py:1882`'s no-filter copy each change behavior, so each gets
its own commit with an explicit note on what downstream consumers now see. `engine/advisory.py` is
advisory-path code — treat as report-only unless the user approves, since it currently leaks NaN and
fixing that is a real behavior change.

**PR 3 — Fix the N+1 in the per-cycle pipeline (F5).** Replace `_apply_symbol_rating_columns`'s
per-ticker `.map()` (`pipeline/production_steps.py:627`) with the existing batched
`get_excluded_symbols()` (`rating/symbol_rating_store.py:209`), and vectorize line 636's
`dashboard_df.apply(_excluded, axis=1)`. Self-contained, diagnostic-column-only, no trading logic.
Confirmed untouched by any recent PR, so no merge-conflict risk with in-flight work. Highest measured
win per unit of risk.

**PR 4 — Consolidate the 3 Black-Scholes holdouts and the regex (F3, F4).** Fix the
`options_gex.py` regex drift first as its own commit — it is a correctness bug, not a refactor. Then
resolve the `vol_sqrt_t` clamp-vs-early-return divergence (`options_gex.py` early-returns `0.0` where
the canonical `options_risk.py` clamps and continues) as a deliberate, disclosed decision — not
silently — before touching anything else in that file, since it changes a live Gamma value for
tiny-but-nonzero `σ√t`. Then migrate `multi_leg_pricing.py`, `realtime_risk_streamer.py`,
`options_gex.py` one file per commit, each asserting numeric equality against the prior
implementation on a seeded grid before deletion.

**PR 5 — Add `get_quotes_batch` to the provider ABC (F6).** The loops exist because
`MarketDataProvider`'s ABC has no batch method — `api/data_api.py`'s own docstring concedes this.
Add it, default-implement it as the current loop so no provider breaks, override it for FMP via the
existing `fmp_client.batch_quote()`, then migrate call sites (`api/data_api.py:645`,
`pilots/options_risk.py:421`, `pilots/scenario_matrix.py:400`, `data/paper_account_store.py`'s
`settle_expired_options`, `pilots/dispersion_trading.py:779-808`, `evaluation_engine.py:1114` via the
existing `data/historical_store.py:1039` `get_bars_bulk()`). Fixes the cause, not the instances.
Confirmed none of these sites were touched by the recent unbounded-blocking-call sweep, so this PR
starts from a clean, unaffected baseline.

**PR 6 — Guard `api/metrics_api.py` (F7).** Extend the existing heavy-import AST guard
(`tests/test_pilots_api.py`'s `test_pilots_api_never_imports_heavy_engines`) to cover
`api/metrics_api.py`, then move the four imports (`processing_engine`, `forecasting_engine`,
`technical_options_engine`, `sentiment_risk_engine`) into function bodies — the lazy-import pattern
this repo already uses widely (see F4's reuse call sites). Since all four engines are genuinely used
at runtime here (unlike a typical "accidental heavy import" case), this PR is pure mechanical
lazy-loading, not a behavior change — verify `tests/test_metrics_api.py` still passes with its
existing mocks after the move.

**PR 7 — Rate-limiter parity (F8).** Add `data/cross_process_throttle.wait_turn` to the GDELT
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

**PR 9 — Shared atomic-write helper (F11).** One `atomic_write_json()` with pid/tid-scoped temp
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

## Verification

Per-PR verification is specified inline above. The recurring requirement, unchanged from the original
plan: **PRs 2 and 4 must prove numeric/behavioral equality on a seeded fixture before deleting any
implementation**, matching this repo's precedent for the ETF-transmission flag-off parity proof.

Before starting each PR, re-run that finding's repro command from the audit doc — the codebase moves
fast enough (3 unrelated PRs landed between the original audit draft and this write-up alone) that a
line number or count cited here may have already drifted again by the time work starts.

## Open item

F15 (dead-code sweep) has not landed — see the audit doc's "Open item" section. Add a PR-12 removal
pass here once it does, sized to what it finds.
