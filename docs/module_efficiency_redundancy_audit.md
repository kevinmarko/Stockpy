# Module Efficiency & Redundancy Audit

**Date:** 2026-08-28
**Scope:** hot production paths, `pilots/` options desk, the data/store layer, webapp mock/live parity.
**Explicitly report-only:** `signals/`, `sizing/`, `execution/`, `validation/`. A "harmless" dedup in
sizing or signal math can silently move live position sizing on a real capital account — findings
in those areas are documented here but **no remediation PR is scheduled for them** without separate,
explicit sign-off.

## Why this audit exists

This codebase's incident history is dominated by one bug class: the same logic implemented N times,
then drifting. Universe resolution existed in three divergent places (now consolidated — see the
"Checked and clean" section). `MAX_STRESS_DRAWDOWN` was defined twice and only coincidentally agreed.
The mock/live parity sweep of 2026-08-19 found 21 bugs across 13 modules — after an earlier pass had
declared the same surface "verified, every one matches." This document exists so the next incident
doesn't have to rediscover where the duplication and hot-path cost actually are.

Every finding below was independently re-verified against the current `HEAD` of this repo
(`f23501dc`) after the original pass, specifically because several PRs landed between the original
audit and this write-up (`#912`/`#923` iterrows→vectorized hardening, `#921` unbounded-blocking-call
sweep) that touch adjacent code. Three findings had a factual error in the *original* draft, corrected
below rather than silently fixed: F4's clamp-vs-early-return attribution was backwards, F13's claim
about which files import `gui.env_io` was wrong, and F1's claim that `LGBMRankerSignal` is the
per-row cost driver doesn't hold up under inspection. Two findings (parts of F9) turned out to already
be fixed since the original pass. All are called out inline.

Findings are ranked by real impact, not LOC.

---

## F1 — The vectorization guard has a blind spot; 7 registered signal modules run row-wise every cycle

`tests/test_no_iterrows_in_core_engines.py:43`'s `_BANNED_METHODS = {"iterrows", "itertuples"}` does
**not** ban `.apply(axis=1)`, and the guard's scope is only `processing_engine.py`, `strategy_engine.py`,
and `signals/*.py` (`GUARDED_MODULES`, lines 37-40). So the default fallback at `signals/base.py:220`:

```python
results = df.apply(lambda row: self.compute(row, context), axis=1)
```

is invisible to it, and so is any module that quietly omits the override.

The live registry (`signals/__init__.py::_register_all()`) registers **20** `SignalModule` subclasses.
Of those, **7 never override `compute_vectorized()`**: `MultifactorSignal`,
`CrossSectionalMomentumSignal`, `MacroRegimeSignal`, `LGBMRankerSignal`, `NewsCatalystSignal`,
`RegimeMultiplierSignal`, `SectorNeutralQualitySignal`. They run row-wise via
`signals/registry.py:147` → `signals/aggregator.py:497` (`compute_all_vectorized`) →
`pipeline/production_steps.py:2312` (`aggregate_vectorized`, called unconditionally every cycle),
once per cycle over the full universe (~500 names).

**Correction to the original finding — severity is lower than first framed.** The original draft
named `LGBMRankerSignal` as "the real cost… per-row model inference instead of one batched
`predict()`." That does not hold up: `signals/lgbm_ranker.py`'s expensive step
(`ranker.predict_score(feat_df)`, a single batched inference over the whole cross-section) runs once
per cycle inside `pre_compute()`. The row-wise `compute(row, context)` that actually executes under
`.apply(axis=1)` is a cheap dict lookup (`context.lgbm_scores.get(ticker, 0.5)`) plus arithmetic — not
per-row inference. The same two-phase pattern (heavy work batched once in `pre_compute`, cheap
per-row lookup in `compute`) holds for `MultifactorSignal`, `CrossSectionalMomentumSignal`,
`NewsCatalystSignal`, and `SectorNeutralQualitySignal`. `MacroRegimeSignal` and `RegimeMultiplierSignal`
have no `pre_compute` hook at all, but their `compute()` bodies are trivial conditionals/attribute
reads with no I/O or heavy math. **None of the 7 modules do genuinely expensive per-row work** — the
real cost is the Python-loop overhead of `.apply(axis=1)` itself over ~500 rows, which is real but
modest, not the "per-row model inference" the original draft claimed.

`CLAUDE.md:353` states *"the entire SignalAggregator and all SignalModule implementations are
natively vectorized in pandas/numpy."* That is still factually wrong as of this writing.

The original draft also cited `scripts/refresh_validations.py:2087-2095` as already documenting this
gap. On closer read that citation is a false lead: that comment (at `2078-2094`) describes an
unrelated backtest-replay adapter that excludes 4 of an 18-module blend for a different reason,
leaving "14 survivors" — a different code path than the live 20-module registry, not a documentation
of this specific vectorization gap.

**Severity: low-to-moderate.** Real, measurable, but not the "invisible model-inference tax" the
original framing suggested — treat as a debt-visibility problem (the guard should catch it) more than
an active performance emergency.

Repro: `grep -n "_BANNED_METHODS" tests/test_no_iterrows_in_core_engines.py`;
`grep -n "apply(lambda row" signals/base.py`;
`grep -rn "def compute_vectorized" signals/*.py` cross-referenced against
`grep -n "class.*Signal.*SignalModule" signals/*.py`;
`grep -n "aggregate_vectorized" pipeline/production_steps.py signals/aggregator.py`.

## F2 — `_safe_float` reimplemented 7 times with genuinely different NaN semantics

Seven copies, no shared implementation, and they disagree on what "bad input" means:

| Location | NaN | ±inf | Returns |
|---|---|---|---|
| `reporting/state_snapshot.py:31` (named `_safe_float_or_none`) | `None` | not checked | `Optional[float]` |
| `pilots/vol_mispricing.py:364` | `None` | not checked | `Optional[float]` |
| `api/pilots_api.py:2109` | `None` | not checked; no `float()` cast at all | `Optional[float]` |
| `data/fmp_feeds_market.py:73` (pre-migration) | **`float('nan')`** | not checked | `float` (never `None`) |
| `data/fmp_feeds_company.py:75` | `None` | `None` | `Optional[float]` |
| `engine/advisory.py:1882` | **not checked — NaN passes through unchanged** | not checked | `float` |
| `validation/validation_history_store.py:177` | `None` | `None` | `Optional[float]` |

`fmp_feeds_market.py` and `fmp_feeds_company.py` are sibling FMP parsers in the same directory using
**opposite** bad-value sentinels. `engine/advisory.py:1882` is the most dangerous: the only copy that
never filters NaN, in the advisory engine, violating this repo's own CONSTRAINT #4 that five of the
other six docstrings explicitly cite.

Repro: `grep -n "_safe_float" <each file>` then read each function body directly.

**Remediation status (PR 2)**: `numeric_utils.safe_float` is now the canonical implementation for 5 of
the 7 copies. The first pass covered the 4 confirmed behaviorally compatible on their own
(`state_snapshot.py`, `vol_mispricing.py`, `pilots_api.py`, `fmp_feeds_company.py`). `fmp_feeds_market.py`'s
NaN-returning copy — the one genuinely risky migration, deferred out of that first pass — is now also
migrated, in a follow-up commit with two disclosed companion fixes required to make the migration safe:
`fetch_realized_volatility`'s happy path now returns `None` (not NaN) for a missing/unparseable
`hv_10`/`hv_30`/`hv_90` value, matching its own exception-path fallback and no longer silently passing
the `hv_30 is not None` gate `pilots/unusual_options_flow.py` and `pilots/options_alerts.py` depend on;
and `fetch_insider_stats`'s `total_disposed == total_disposed` self-comparison idiom (which depended on
the old NaN-not-None contract) is now an explicit `is not None` check on BOTH operands of the ratio
division, since `total_acquired` can independently be `None` too under the new contract. `engine/advisory.py`
and `validation/validation_history_store.py` stay untouched, report-only per the agreed risk posture — the
2 copies still not migrated. See `.claude/module_efficiency_audit_remediation_plan.md`'s PR 2 entry,
`numeric_utils.py`'s own docstring, and `data/fmp_feeds_market.py`'s own module docstring for the full
reasoning; `tests/test_fmp_feeds_market.py` carries the regression coverage.

## F3 — Option-symbol regex drift: one parser accepts strings the others reject

Byte-verified (line numbers drifted by 1 from the original draft):

- `pilots/options_risk.py:28` — `\$(?P<strike>…)` — **`$` required**
- `pilots/realtime_risk_streamer.py:40` — byte-identical copy of the above, `$` required
- `pilots/options_gex.py:266` — `\$?(?P<strike>…)` — **`$` optional**

A symbol lacking `$` parses in `options_gex.py` and returns `None` everywhere else — a behavioral fork
on the same nominal format. `realtime_risk_streamer.py:40` is additionally a byte-identical duplicate
of `options_risk.py:28`, not an import.

Repro: `grep -n "_OPTION_SYM_RE = re.compile" pilots/options_risk.py pilots/realtime_risk_streamer.py pilots/options_gex.py`.

## F4 — Black-Scholes: canonical pricer exists and is widely reused; 3 real holdouts, one
divergence investigated and RESOLVED (kept as-is — the canonical function has the bug, not the copy)

`pilots/options_risk.py::calculate_black_scholes_greeks` (line 50) is genuinely canonical, reused by
`scenario_matrix.py:30` (a **module-level** import — not lazy, unlike the rest), `zero_dte_engine.py:1237-1238`,
`volatility_surface.py:82/104/125`, `gamma_scalper.py:86,88`, `options_sor.py:191,193`,
`vol_mispricing.py:275,278`, `dispersion_trading.py:177,181` (all six of these via lazy in-function imports).

Genuine remaining copies (not yet migrated — see the remediation plan's Status section):

- `pilots/multi_leg_pricing.py:54-127` — `calculate_black_scholes_leg_greeks`, near-verbatim copy (no
  drift found)
- `pilots/realtime_risk_streamer.py:123-191` — `compute_black_scholes_unit_greeks`, own copy + the
  duplicated regex fixed in F3 above (`realtime_risk_streamer.py`'s own regex copy was NOT touched by
  that fix — it still needs migrating to import the canonical one, tracked here)
- `pilots/dispersion_trading.py:138-165` — own `calculate_straddle_vega`, inconsistent with
  `calculate_option_price()` a few lines below in the same file, which delegates correctly

**`options_gex.py`'s `vol_sqrt_t` divergence — investigated by attempting the fix, not just re-reading
the code, and resolved in the opposite direction from what the original finding assumed.** Two prior
drafts of this section disagreed with each other about which function's behavior was "correct" without
either one actually testing it. The real answer, confirmed empirically:

- Canonical (`pilots/options_risk.py:134-136`): when `vol_sqrt_t = sigma * sqrt(t_years)` falls below
  `_DEGENERATE_THRESHOLD`, it is **clamped** to the threshold and the Greek calculation continues.
- `options_gex.py::calculate_black_scholes_gamma` (lines 247-249): the same check **early-returns
  `0.0`** instead of clamping.

Making `options_gex.py` match the canonical clamp-and-continue behavior was the obvious-looking fix —
and it is wrong. Tested directly: `calculate_black_scholes_greeks(spot=100, strike=100, t_years=1e-11,
sigma=1e-7, option_type="call")` — an ATM contract with a genuinely negligible but nonzero
`vol_sqrt_t` — returns `gamma ≈ 3.6e9`. That is not a meaningful floored value; it's a spurious
multi-billion-dollar-scale number produced by dividing by a clamped denominator on the edge of float
precision. `options_gex.py`'s `return 0.0` avoids this entirely, and is the safer behavior for a
function that feeds a **portfolio-wide dealer-GEX aggregate**, where one spurious value would dominate
and invalidate the whole sum.

**Decision, now implemented**: `options_gex.py` keeps its `return 0.0`, documented inline with the
empirical finding and pinned by two regression tests
(`test_black_scholes_gamma_tiny_vol_sqrt_t_returns_zero_not_a_spurious_blowup` and
`test_black_scholes_gamma_confirms_canonical_functions_own_blowup_for_context` — the latter a
permanent witness so a future change to the canonical function's own guard doesn't silently
invalidate this reasoning). `pilots/options_risk.py`'s own clamp-and-continue behavior was
**deliberately left unchanged** — it has 7+ reuse sites, and fixing its numerical guard is a
real, separate, and non-trivial task that deserves its own dedicated, carefully-tested PR, not a
byproduct of an "align the duplicate" cleanup. Filed as a genuine (if narrow — the input shape needed
to trigger it is extreme) latent bug in the canonical function itself, not closed here.

`_get_risk_free_rate()` / a `0.045` default rate constant is separately redeclared in `options_gex.py`
(`DEFAULT_RISK_FREE_RATE`, lines 99/208), `vol_mispricing.py` (`DEFAULT_RISK_FREE_RATE`, lines
77/254), and `volatility_surface.py` (named `_DEFAULT_RFR` there, lines 56/64 — a naming
inconsistency on top of the repetition). All three agree on the value 0.045 — repetition, not drift.

Repro: `sed -n '130,140p' pilots/options_risk.py; sed -n '240,265p' pilots/options_gex.py` to compare
the `vol_sqrt_t` handling directly; run
`tests/test_options_gex.py::test_black_scholes_gamma_confirms_canonical_functions_own_blowup_for_context`
to reproduce the canonical function's own spurious-value behavior on demand.

## F5 — N+1 database queries in the per-cycle pipeline, with the batched fix already written

`pipeline/production_steps.py:591-636` (`_apply_symbol_rating_columns`, called unconditionally every
cycle at line 2778) maps per-ticker:

```python
dashboard_df['Symbol_Rating_Consecutive_Bad_Cycles'] = dashboard_df['Symbol'].map(_cycles)
```

Each `_cycles(symbol)` (line 624) opens its own session and issues its own `SELECT`
(`rating/symbol_rating_store.py:168-207`, confirmed: opens `self.Session()` at line 188 and closes it
per call). The same class already has `get_excluded_symbols()` (line 209) whose docstring says it
"Issues exactly ONE query… instead of one query per symbol — this both avoids the N+1 round-trip
pattern," using a window function. That batched method is used correctly for the real
universe-drop decision in `data/portfolio_sync.py:723,851`, but the diagnostic-column path at line
636 reintroduces the exact N+1 it was built to remove — ~500 queries + session open/close per cycle,
purely for display columns. Line 636 also adds a trivially vectorizable
`dashboard_df.apply(_excluded, axis=1)`.

Confirmed via `git show --stat 7e02e0ba` (the recent iterrows→vectorized hardening PR) that this file
is untouched by that or any other recent PR — this finding is unaffected by intervening work and
fully current.

Repro: `grep -n "_apply_symbol_rating_columns\|def _cycles\|dashboard_df.apply" pipeline/production_steps.py`;
`sed -n '186,207p' rating/symbol_rating_store.py`.

## F6 — N+1 network calls where a batch endpoint exists — FIXED, all 6 call sites migrated

`MarketDataProvider`'s ABC originally exposed only per-symbol `get_latest_quote()`, so every caller
looped:

- `api/data_api.py:645-678` (`get_quotes()`) — its own docstring conceded *"We loop per symbol… There
  is no batch `get_quotes` on the provider"* — a documented limitation of the abstraction, not an
  oversight of an available method on the same interface (`fmp_client.batch_quote()` was a separate,
  lower-level FMP-specific function not exposed through the `CompositeProvider` abstraction this call
  site uses)
- `pilots/options_risk.py:421-428`, `pilots/scenario_matrix.py:400-406` — per-request loops
- `data/paper_account_store.py:1567-1613` (`settle_expired_options`) — per-position loop at line 1588
  calling `get_latest_quote()` at line 1610, despite the same file's `_resolve_position_prices` (line
  433) correctly calling `fmp_client.batch_quote()` at line 459
- `pilots/dispersion_trading.py:779-808` — 3 separate per-constituent loops: quote, IV resolution, and
  serial `get_bars()`

`evaluation_engine.py:1114-1129` similarly fetched bars serially per symbol (line 1174, memoized
per-symbol but not batched across distinct symbols) though `data/historical_store.py:1039` provides
`get_bars_bulk()` — reachable from the live `GET /calibration/summary`.

**Fixed in two passes.** `MarketDataProvider.get_quotes_batch()` (ABC method, default
per-symbol-loop implementation so no existing provider subclass breaks, real `FMPProvider` override
via `fmp_client.batch_quote()`) landed first (PR #935, hardened in #942), and `api/data_api.py`,
`pilots/options_risk.py`, and `pilots/scenario_matrix.py` were migrated to it in that same work. This
audit's remediation PR 5 closed the three remaining call sites:

- `data/paper_account_store.py::settle_expired_options` — restructured into two passes: parse every
  open position and collect the distinct set of underlyings actually expired, then ONE
  `get_quotes_batch()` call resolves all of them, then the settlement loop looks up each position's
  spot from that pre-fetched dict. A symbol absent from the batch result (unresolvable, or a total
  batch failure) leaves that position open with the same `WARNING` log the original per-position
  `try/except` produced — never a fabricated intrinsic-value settlement (CONSTRAINT #4).
- `pilots/dispersion_trading.py::_source_real_dispersion_inputs` — the spot-price loop (index +
  every constituent, previously one `pilots.price_provider.get_current_price()` call per symbol) now
  resolves via one `data.market_data.get_provider().get_quotes_batch()` call. The IV-resolution loop
  (a different data source — the options chain) and the realized-correlation bars loop were
  deliberately left untouched, out of scope for this specific migration.
- `evaluation_engine.py::recommendation_tracking_report` — added a prewarm step, before the
  per-signal loop, that resolves every distinct symbol across `buy_entries` via one
  `HistoricalStore.get_bars_bulk()` call; the loop's own per-symbol lazy-fetch (`_get_bars()`) is kept
  intact as the fallback for any symbol missing from the bulk result, so the final outcome is
  identical to the pre-migration behavior either way. A real test gap was found and closed in the
  same pass: the `_FakeHistoricalStore` fixture used across `tests/test_recommendation_tracking.py`
  had no `get_bars_bulk()` method, so the pre-existing 28-test suite was silently exercising the
  `AttributeError`-fallback path rather than real batching — fixed by adding a genuine
  `get_bars_bulk()` to the fixture.

Verified: `tests/test_paper_account_store.py` (44 passed, 2 new regression tests for the
batch-failure and partial-coverage dead-letter paths), `tests/test_dispersion_trading.py` (18 passed,
3 new), `tests/test_recommendation_tracking.py` (31 passed, 3 new), plus
`tests/test_evaluation_engine.py`/`tests/test_pilots_calibration.py`/`tests/test_pilots_paper_broker.py`/
`tests/test_market_data.py` unaffected. `ruff check . --select=F821,F822,F823,E9` clean.

Repro (pre-fix state, still reproducible on `git show fe683ebc:evaluation_engine.py` /
pre-PR-5 `data/paper_account_store.py` / `pilots/dispersion_trading.py`): `sed -n '644,678p'
api/data_api.py`; `grep -n "get_bars_bulk\|def _get_bars" data/historical_store.py
evaluation_engine.py`.

## F7 — CORRECTED, downgraded to non-actionable: `api/metrics_api.py`'s heavy imports are
deliberate, documented design, not an oversight

**This finding was materially wrong in its original framing and its remediation plan (PR 6) has
been withdrawn — see below.** Re-verified while attempting to implement PR 6, not just re-read:
`api/metrics_api.py`'s own module docstring (lines 18-19) states explicitly: *"This module MAY
import the heavy calculation engines (unlike `state_api.py` / `control_api.py`, which are
AST-guarded against exactly that)."* `tests/test_pilots_api.py:2124-2164`
(`test_pilots_api_never_imports_heavy_engines`)'s own docstring confirms the guard's scope is
deliberately `api/pilots_api.py`-only, for a reason specific to that module: `pilots_api.py` is
architected to stay thin and reach the pipeline only through lightweight `pilots.*` helpers or
loopback HTTP, never the heavy engines directly. `api/metrics_api.py` is a genuinely different
service with a genuinely different purpose (per its own docstring: "exposing computed indicators,
forecasts, options directives, and signal breakdowns" — it IS the service that runs these
computations on demand for the webapp). This is a real, intentional architectural split between two
standalone FastAPI processes, not an inconsistency.

**The original PR 6 plan ("extend the AST guard to cover `api/metrics_api.py`, then lazy-import the
four engines... pure mechanical lazy-loading, not a behavior change") is wrong on both halves.**
Extending the guard would assert something false about this module's own documented design.
Lazy-importing is not mechanical either: `tests/test_metrics_api.py` monkeypatches these engines at
the MODULE level 56 times (e.g. `monkeypatch.setattr(metrics_api, "ForecastingEngine", _FakeFE)`,
`monkeypatch.setattr(metrics_api, "build_premium_directive", _fake_directive)`) — a lazy
`from forecasting_engine import ForecastingEngine` inside each endpoint function would silently
bypass every one of those mocks (the lazy import re-resolves the REAL class from
`forecasting_engine` each call, never seeing the module-level patch), turning ~10+ tests that
believe they're testing against a fake into tests that silently exercise the real heavy engine.
Making the migration actually safe would mean rewriting the test suite's mocking strategy to patch
at the *source* module instead — a materially bigger and riskier change than "mechanical", confirmed
only by attempting the implementation, not by re-reading the code a third time.

**What remains genuinely true and low-severity:** the four heavy imports do cost real process-startup
time/memory on the standalone port-8604 service, and on `tests/test_metrics_api.py`'s pytest
collection — confirmed nothing else imports `api.metrics_api` in production, so that is the entire
blast radius. If this cost is ever worth paying down, the correct fix is NOT a guard (that would be
false to this module's design) and NOT a naive lazy-import (that breaks the test suite's mocking) —
it would need a redesigned test-mocking strategy alongside the migration, scoped as its own
dedicated PR, not a one-line "F7" cleanup. Downgraded from "PR 6, mechanical" to "documented,
non-actionable without a larger redesign" — no PR is scheduled for this finding.

Repro: `sed -n '1,20p' api/metrics_api.py` (the module docstring); `sed -n '2100,2124p'
tests/test_pilots_api.py` (the guard's own documented pilots_api.py-only scope);
`grep -c 'monkeypatch.setattr(metrics_api' tests/test_metrics_api.py` (56).

## F8 — Rate limiting: fixed in 2 of 3 places

Three near-identical throttle implementations: `data/fmp_client.py:259`,
`data/edgar_fundamentals.py:48`, `data/sentiment_sources.py:587` (drifted 1 line).

- `fmp_client.py` and `edgar_fundamentals.py` both call `data/cross_process_throttle.wait_turn` for
  multi-worktree protection (confirmed at lines 282 and 72 respectively — `wait_turn` is called from
  exactly these two files repo-wide). **`sentiment_sources.py`'s GDELT throttle does not** — it uses
  only a `threading.Lock` + module globals, no cross-process spacing. (The original finding framed
  `cross_process_throttle.py`'s docstring as documenting this *exact* GDELT bug by name; on inspection
  the docstring cites production incidents for FMP/EDGAR concurrency specifically, not GDELT — the
  underlying mechanism is structurally identical, but the "documented this exact bug" framing was
  slightly generous.)
- The cooldown/circuit-breaker state machine exists in `fmp_client.py`
  (`_fmp_cooldown_until`/`_fmp_consecutive_failures`) and `sentiment_sources.py`
  (`_gdelt_cooldown_until`/`_gdelt_consecutive_failures`) but is **entirely absent from
  `edgar_fundamentals.py`** — confirmed via `grep -n "_consecutive\|_cooldown\|breaker\|backoff\|retry" data/edgar_fundamentals.py` returning no matches.

Repro: `grep -n "wait_turn\|cooldown\|circuit" data/fmp_client.py data/edgar_fundamentals.py data/sentiment_sources.py`.

## F9 — Store boilerplate: foundation is shared, `__init__` wiring is not

`db_config.py` **is** a genuine single source of truth for `resolve_database_url()` (line 59) /
`create_db_engine()` (67) / `create_readonly_db_engine()` (130) / `session_scope()` (234). This is not
the divergent-implementations failure mode.

What *is* duplicated is the same ~9-line `__init__` block, copy-pasted verbatim (including docstring
wording) into stores at: `sizing/cap_audit_store.py:84`, `data/sector_correlation_store.py:63`,
`validation/validation_history_store.py:84`, `desktop/run_history_store.py:61`,
`transactions_store.py:43`, `data/broker_fills_store.py:117`, `data/execution_audit_store.py:239`,
`data/paper_account_store.py:110`, `data/cache_long_short_store.py:95`.

**Correction to the original finding:** the tenth site, `execution/live_trade_proposals_store.py`,
was cited at line 53 — that line is actually inside an unrelated exception class
(`LiveTradeProposalNotFoundError.__init__`, at line 50-53; a second, sibling exception class,
`LiveTradeProposalAlreadyDecidedError`, starts at line 58). The store's real `__init__` boilerplate is
at **line 102**.

`data/historical_store.py:751` is the outlier — it does *not* call `Base.metadata.create_all()` in
`__init__`, deferring to `_ensure_tables()` (line 819). Confirmed deliberate, not a bootstrap gap.

**Correction to the original finding:** the "probe `PRAGMA table_info`, then `ALTER TABLE ADD COLUMN`"
migration wrapper is not reinvented ~5× as a single idiom — it's actually **two distinct idioms**,
2 sites each: the PRAGMA-probe idiom appears at `data/historical_store.py:924` and `:947`; a
*different* try/except-around-`ALTER` idiom (swallowing the duplicate-column exception, no PRAGMA
probe) appears at `data/execution_audit_store.py:265` and `data/paper_account_store.py:190-202`.
`execution_audit_store.py`'s own docstring explicitly says it deliberately avoids "SQLite-only PRAGMA
table_info probing" for Postgres portability — so this isn't uncoordinated reinvention, it's two
teams solving the same problem with a portability tradeoff in opposite directions, each internally
consistent. `transactions_store.py:68` (`_ensure_conviction_column`) uses the PRAGMA-probe idiom,
bringing that count to 3 sites, still a real duplication worth consolidating.

**Now fixed since the original draft — no longer a live finding:** the original draft flagged
`data/historical_store.py` and `forecasting/forecast_tracker.py` as having hardcoded a CWD-relative
`db_path`, bypassing `db_config`. Both now call `db_config.resolve_database_url()` when `db_path is
None` (`historical_store.py:751`, `forecast_tracker.py:168`), each with a comment noting this replaced
the old CWD-relative `"quant_platform.db"` literal. Recorded here for the audit trail, not carried
into the remediation roadmap.

The real remaining cost is downstream: each store needs its own autouse isolation fixture in
`conftest.py`, and each was added reactively — one only after 260 rows of synthetic test data reached
the operator's real `quant_platform.db`. `CLAUDE.md` already calls this "a confirmed recurring bug
class."

Repro: `grep -n "def __init__" <file>` per store; `grep -n "resolve_database_url" data/historical_store.py forecasting/forecast_tracker.py`.

## F10 — Statistics: 4 Sharpe, 3-4 Sortino, 2 Calmar implementations, coordinated by comments

`validation/metrics.py:144` is canonical for the harness path (`sharpe_ratio`). Independent
reimplementations: `simulation_engine.py:65-98` (inline Sharpe — the comment there now explicitly
cross-references `validation/metrics.py::sharpe_ratio`'s degenerate-std convention, i.e. the constant
is harmonized even though the logic is still duplicated), `processing_engine.py:194-210` (inline
Sortino), `evaluation_engine.py:909-976` (Sharpe at 909, max-DD at 927, DD-duration at 932, CAGR at
945, Calmar at 954-976 — all independent), `scripts/refresh_validations.py:1111-1160` (a fourth
rolling Sortino/drawdown, in `_build_sortino_drawdown_adapter`).

**Addition to the original finding:** `validation/metrics.py` — the file this audit treats as
canonical — itself contains a fifth, independent inline per-path Sortino calculation inside
`run_cpcv_evaluation` (line 638). So there are arguably 4 independent Sortino implementations, not 3,
if code inside the canonical module counts.

`validation/harness.py:917-936` and again at `971-975` **documents in its own comments** that its
Calmar uses arithmetic-mean annualization while `evaluation_engine.py` uses CAGR-based, and that the
two "are NOT directly comparable." Known, documented drift kept in sync by comment discipline alone.
**Report-only under the agreed risk posture** — `validation/`, `sizing/`, `signals/`, `execution/` are
out of scope for remediation in this pass.

## F11 — Atomic-write idiom reinvented 10+ times with inconsistent safety

No shared `atomic_write_json()` exists (`grep -rn "def atomic_write" --include="*.py" .` returns
nothing). `runtime_flags_writer.py:473` (drifted 1 line) uses a race-safe `.tmp.{pid}.{tid}` name.
`reporting/pairs_snapshot.py:105` and `reporting/options_snapshot.py:67` are **near-identical
copies** of each other (`options_snapshot.py`'s carries one extra docstring line, 6 lines vs 5; the
core 4-line write logic — mkdir/tmp/write_text/replace — is byte-identical), both using
`path.with_suffix(".tmp")` — not pid-scoped, so two concurrent writers to one path collide.
`llm/status_store.py:239` uses `mkstemp`, and write failures are caught
and only `logger.debug()`'d rather than raised — practically easy to miss, though technically logged
rather than fully silent (softening the original "silently swallows" framing slightly). Plus ~8
inline `os.replace` sites (`reporting/progress.py:356`, `execution/fix_gateway.py:2043`,
`data/robinhood_session.py:75,99`, `validation/harness.py:1161`, others).

## F12 — Webapp mock/live parity is manual and structurally unverifiable

`webapp/src/api/mock.ts` is **15,965 lines** — 11× `client.ts` (1,457) — with only **19** exported
builders, so the bulk is one hand-written fixture literal. `api/pilots_api.py` alone now defines
**173 routes** (90 GET + 59 POST + 16 PUT + 8 PATCH) in one **8,181**-line file — the original
draft's "165 routes" figure has already drifted upward from real route growth in the intervening
commits.

**Correction to the original finding:** `types.ts` is now **5,774** lines and declares **363** pure
`export interface` declarations, not 403 — the "403" figure only holds if you additionally count the
40 separate `export type` aliases in the same file (363 + 40 = 403). Either framing is directionally
the same point (a very large, hand-maintained type surface); this document uses 363 interfaces + 40
type aliases as the precise current figures.

`webapp/src/api/mock.test.ts` (944 lines, 14 top-level `describe` blocks) hand-asserts shape parity
for roughly a dozen endpoints, and only against the mock — its own header docstring states "Mock
layer only — no network, no live API." (That same header does mention `VITE_USE_MOCK=false`, the
live-client toggle, two lines earlier — so the file isn't entirely silent about the live backend's
existence; the assertions inside it, however, never actually run against a live FastAPI response.)
~173 routes, ~14 describe blocks, **no structural guarantee**. This is the mechanism
behind the 21-bug sweep of 2026-08-19. Adding more hand-written assertions does not fix a problem
whose root cause is that parity is asserted by hand at all.

Repro: `wc -l webapp/src/api/{mock.ts,client.ts,types.ts,mock.test.ts} api/pilots_api.py`;
`grep -cE "^export interface " webapp/src/api/types.ts`; `grep -cE "@app\.(get|post|put|delete|patch)\(" api/pilots_api.py`.

## F13 — A safety-critical backend module lives in the decommissioned `gui/` package

`gui/env_io.py` (1,141 lines) imports only stdlib + `dotenv` + `settings.ENV_PATH` — **no
Streamlit**. It is the canonical `SECRET_KEYS`/`ALLOWED_KEYS` registry (`ALLOWED_KEYS` at line 77;
`SECRET_KEYS` a separate tuple further down at line 723) gating credential writes to `.env`.

**Correction to the original finding — two of the four cited live-code importers were wrong.**
Confirmed importers via `grep` and `git log -S "env_io"`: `runtime_flags_writer.py:189`
(`from gui import env_io`) and `conftest.py:140` (`import gui.env_io as _env_io`) are genuine, current
importers. `alerting.py:264` and `diagnostics_and_visuals.py:884` do **not** import `gui.env_io` and
never have across the repo's full commit history — line 264 of `alerting.py` actually imports
`gui.help_content.MODEL_RETRAIN_WINDOW_DAYS`, and line 884 of `diagnostics_and_visuals.py` imports
`gui.strategy_registry`. This part of the original finding conflated different `gui.*` submodule
imports and was factually wrong from the start, not merely stale. The confirmed live-code blast
radius is narrower than originally stated: two call sites (`runtime_flags_writer.py`, `conftest.py`),
not four.

`CLAUDE.md` (lines 114-135) confirmed still declares `gui/` decommissioned ("The desktop app is
decommissioned — do not develop it further… `gui/`… treat them as frozen"). The architectural point
stands even with the narrower importer list: `env_io.py` is technically inside the "frozen" `gui/`
package yet is load-bearing live infrastructure consumed from outside `gui/`, and
`runtime_flags_writer.py`'s docstring already contorts around this.

Repro: `wc -l gui/env_io.py`; `grep -n env_io runtime_flags_writer.py conftest.py alerting.py diagnostics_and_visuals.py`;
`git log -S "env_io" -- alerting.py diagnostics_and_visuals.py`.

## F14 — Miscellaneous

- Three WebSocket reconnect loops: `execution/alpaca_broker.py:439-511` caps backoff at a **named**
  constant `_STREAM_RECONNECT_MAX_SECONDS = 30.0` (line 60); `data/market_data_ws.py:158-200` also
  caps at 30s by default, via a **configurable** `reconnect_max_seconds` parameter /
  `MARKET_DATA_WS_RECONNECT_MAX_SECONDS` setting (not a bare magic number, contrary to how the
  original finding grouped it with the third site); `data/websocket_streamer.py:204` (drifted 3
  lines) caps at **60s** with a genuine bare magic-number literal (`min(backoff * 2, 60.0)`) and no
  named constant.
- `_read_json_object` duplicated with different exception surfaces: `pilots/scoring.py:105` catches
  `(OSError, ValueError)`; `pilots/strategy_matrix.py:111` catches bare `Exception`.
- `Gravity AI Review Suite.py` is **16,142 lines** — the largest file in the repo (next largest:
  `api/pilots_api.py` at 8,181) — reaching into `gui/` internals at **50** import sites across 19
  distinct `gui` submodules (an AST-parsed recount; originally estimated at ~30 import sites).
  Inventory only; no action proposed.

## F15 — Dead code: two orphaned modules, several smaller cases, all independently re-verified

A fourth background agent swept `pilots/`, `ml/`, `validation/`, `execution/`, `sizing/`, `risk/`,
`signals/`, `data/`, and the webapp for code with no production caller, checking dynamic-dispatch
registries (`pilots/catalog.py`, `signals/registry.py`, `STRATEGY_REGISTRY`) before calling anything
dead. Every finding below was re-verified independently with a targeted grep before inclusion; one
finding from the agent's own report (a "gui.env_io" false positive already caught by F13) is omitted
here as duplicate.

**`ml/drl_market_maker_ppo.py` (644 lines) — entire module, zero production callers.** A real
actor-critic PPO agent (hand-derived backprop, gradient-checked in its own test) for the
market-maker pilot. Only `tests/test_drl_market_maker_ppo.py` imports it — confirmed, `grep -rn
drl_market_maker_ppo` returns exactly those two files. The live endpoint
`POST /pilots/options/market-maker/train` still calls the older `ml.drl_market_maker.train_market_maker_policy`
(a 2-parameter hill-climb); the PPO trainer was never wired in. This matches CLAUDE.md's own
2026-08-19 entry, which already discloses it as "not yet wired to any API endpoint, webapp screen,
or STRATEGY_REGISTRY entry."

**`validation/walk_forward.py` (431 lines) — entire module superseded by an inline
reimplementation.** A full walk-forward engine (`WalkForwardWindow`, `run_walk_forward_analysis`,
WFE ratio per Pardo 2008), imported only by `tests/test_walk_forward.py` — confirmed. The actual
deployability gate, `validation/harness.py`, computes its own `walk_forward_60_40`/`70_30`/`80_20`
Sharpe values inline without ever importing this module. A parallel, simpler implementation shipped
instead and this one was left orphaned. Report-only per the agreed risk posture (`validation/`), but
worth flagging: two walk-forward implementations existing side by side, one live and one dead, is
exactly the kind of drift risk this audit exists to surface.

**`execution/overnight_guardrails.py` (55 lines) — `OvernightGuardrails`, deliberately unwired, not
accidentally dead.** Confirmed: `grep -rn OvernightGuardrails` returns exactly one hit, the class
definition itself — no caller, no test. But its own module docstring already discloses this as an
intentional, open decision, not an oversight: *"NOT YET WIRED INTO THE LIVE ORDER PATH... nothing in
execution/risk_gate.py::PreTradeRiskGate or execution/order_manager.py calls it... needs an explicit
operator decision on where in the pipeline it should run... not a silent wire-up as part of an
unrelated bug-fix pass."* Listed here for completeness, not as a removal or wiring candidate —
`execution/` is report-only per the agreed risk posture, and this module's own comment already asks
for exactly the kind of explicit operator decision this audit isn't the venue for.

**`validation/regime_diagnostics.py::select_optimal_model` (~40 lines, line 255).** AIC/BIC
model-selection wrapper. Confirmed zero references anywhere — not production, not tests
(`grep -rn select_optimal_model` returns only its own definition).

**Three settings fields that gate nothing** — confirmed via direct grep, each appears only in
comments/docstrings that say "settings.X" as prose, plus a `gui/env_io.py` `ALLOWED_KEYS` listing
(making each user-editable in a settings UI despite controlling nothing):
`OPTIONS_EARNINGS_CRUSH_ENABLED` (`settings.py:334` — the earnings_crush pilot itself is fully
wired, but this specific flag is never consulted by it), `PROMPT_MAX_CHARS` (`settings.py:4677` —
`prompt_registry/guardrails.py` documents it as the source of `_DEFAULT_MAX_CHARS` three times but
hardcodes the constant instead of reading the setting), `SENTIMENT_PIT_MIN_MONTHS`
(`settings.py:2934` — referenced in six comments across three files, actually read by none of
them).

**Seven API endpoints with no caller anywhere in `webapp/src`, `investyo_mcp_server.py`, or
`scripts/`** — confirmed via grep against `webapp/src/screens` and `webapp/src/components`
specifically (not just "any file"), since a route can have a client.ts wrapper with no screen
caller:
`GET /pilots/options/multi-leg/price` and `GET /pilots/options/multi-leg/validate`
(`api/pilots_api.py:6424,6459` — no `client.ts` wrapper exists at all, so the gap is upstream of the
webapp layer); `POST /pilots/options/market-maker/train` (`:6837` — self-documented in
`docs/architecture/ml-and-reports.md` as backend-only); `GET /pilots/options/vol-surface/3d-mesh`
(`:8057` — has a full `client.ts`/`types.ts`/`mock.ts` wrapper, `getVolSurface3DMesh`, but zero
callers in `screens` or `components`, confirmed); `GET /metrics/technicals/{symbol}` and
`GET /metrics/signals/registry` (`api/metrics_api.py:162,361` — sibling metrics endpoints have
wrappers and callers, these two have neither); `GET /data/account` (`api/data_api.py:727`); and
`GET /api/queue` (`api/pilots_api.py:2235`, a documented alias for `GET /execution-queue` that
`client.ts` never calls — exists only for the test suite).

**Three `webapp/src/api/client.ts` methods with no caller in any screen or component** — confirmed:
`routeFixOrder` (`client.ts:735` — `FixGatewayStatusRadar.tsx`, the only FIX-related screen, calls
only `getFixSessionStatus`/`reconnectFixSession`), `getVolSurface3DMesh` (`:1404` — see above),
`getCronStatus` (`:774`). All three have full `mock.ts` parity implementations, so the mock/live
parity test suite (F12) passes on them without ever being exercised by real UI code — this is a
second, distinct symptom of F12's root cause: a hand-maintained mock can carry fixtures for an
endpoint nothing calls, and there is nothing that would catch that either.

**`webapp/src/components/charts/threeDisposal.ts` — 6 of 7 exported functions dead, one live.**
CLAUDE.md's own 2026-08 note already discloses this: `VolSurface3D.tsx`/`LobDepth3D.tsx` render via
Canvas 2D, not true Three.js/WebGL, so `disposeThreeScene`/`disposeThreeMesh`/`disposeThreeGeometry`/
`disposeThreeMaterial`/`disposeThreeTexture`/`disposeWebGLRenderer` have nothing to dispose and are
exercised only by their own component tests, never by the components in production use. Only
`disposeCanvas` is genuinely called. Listed for completeness; CLAUDE.md already calls this
"cosmetic, not a bug."

**Four backtest-margin wrapper functions in `validation/options_selling_backtest.py`, ~8 lines
each (lines 1465, 1483, 1492, 1501).** `simulate_call_credit_spread_with_margin`,
`simulate_call_debit_spread_with_margin`, `simulate_put_debit_spread_with_margin`,
`simulate_covered_call_with_margin` — confirmed exactly one hit each (their own definitions). Two
sibling wrappers in the same file, `simulate_put_credit_spread_with_margin` and
`simulate_vrp_iron_condor_with_margin`, are genuinely exercised by `tests/test_walk_forward.py`
(itself dead per this section — see above, a second-order dead-code chain worth noting). Reads as 4
of 6 parallel wrappers never finished being wired into a caller. `validation/` — report-only.

**Three unused exception/enum types in `execution/`, confirmed exactly one hit each (their own
definition):** `FixSequenceError` (`execution/fix_gateway.py:172`), `FailoverTriggerReason`
(`execution/multi_broker_gateway.py:104`), `OrderRoutingFailedError` (`:139`). These read as
forward-declared API surface for the FIX/multi-broker gateways rather than accidental orphans, but
meet the same "defined, never referenced" bar as everything else in this section. `execution/` —
report-only.

**Two never-invoked test-reset singletons, ~4 lines each:** `data/attention_sources.py:359
reset_attention_source()` and `data/market_data.py:2593 reset_options_provider()`. Confirmed no
caller anywhere, including tests — their sibling `get_*` singleton getters are live in production.
Standard test-isolation seams that were written defensively and never wired into a fixture's
teardown. Low-severity; flagged for a future test-suite pass, not a removal candidate on its own.

**Categories checked with no findings:** no commented-out code blocks of 15+ lines found across
`pilots/`, `ml/`, `validation/`, `execution/`, `sizing/`, `risk/`, `signals/`, `data/`, `api/`,
`engine/`, `desktop/`, `pipeline/`, `scripts/`; no TODO/FIXME referencing already-shipped work; no
second unimported implementation of an already-shipped webapp screen/component (the historical
example, `execution/fix_recovery.py`, is already deleted per CLAUDE.md's changelog).

## Checked and found clean (worth recording so the next audit skips them)

- `api/auth.py` (178 lines) is genuinely centralized — a single module
  (`require_read_token`/`require_stream_token`/`require_write_token`/`make_command_token_guard`) with
  no duplicated auth gating found elsewhere.
- `validation/multiple_testing.py` calls `validation.metrics.deflated_sharpe_ratio` verbatim at its
  two call sites (lines 303 and 319 — the "230" cited in the original draft is inside the docstring
  discussing the reuse, not a call site) — the template the F10 items should follow.
- Universe resolution: the historical 3-copy bug **is** consolidated; only one `resolve_universe()`
  definition exists repo-wide, at `data/portfolio_sync.py:754`.
- `get_provider()` is a proper module-level singleton (`data/market_data.py:2525`), with a
  `reset_provider()` seam for tests.
- `simulation_engine.py` and `validation/*.py` have zero `.iterrows()`/`.itertuples()` hits — worth
  noting these files aren't even in the F1 guard test's scope, reinforcing F1's point about narrow
  guard coverage.
- No O(n²) membership-in-loop or concat-in-loop instances found — **caveat:** this was checked via a
  lightweight heuristic grep (`pd.concat(` near `for` blocks), not an exhaustive pass. Treat as "no
  contradicting evidence found," not a fully verified clean bill.

## Status

F15 (dead-code sweep) landed and is folded in above. This audit now covers everything scoped at
the outset: hot production paths, the pilots/ options desk, the data/store layer, webapp mock/live
parity, and dead code across the same surface.

**Remediation in progress** (see `.claude/module_efficiency_audit_remediation_plan.md`):
- PR 1 (F1, vectorization guard blind spot + CLAUDE.md correction) — open, see the plan doc.
- PR 3 (F5, N+1 query in the symbol-rating diagnostic columns) — open, see the plan doc.
- PR 6 (F7) — **withdrawn**, not a real bug; see F7's corrected writeup above.
- PR 7 (F8, rate-limiter parity for GDELT/EDGAR) — open, see the plan doc.
- PR 9 (F11, shared atomic-write helper) — open, see the plan doc.
- PR 4 (F3/F4) — **partially done**: the regex fix (F3) and the `vol_sqrt_t` divergence
  investigation (F4, resolved by KEEPING `options_gex.py`'s behavior — see F4's corrected writeup
  above) are open; migrating the two remaining Black-Scholes copies
  (`multi_leg_pricing.py`, `realtime_risk_streamer.py`) to the canonical pricer is not yet done.
- PRs 2, 5, 8, 10 — not yet started.
- PR 11 — spike, not started, per the plan's own "do not start before PRs 1–10 land".
