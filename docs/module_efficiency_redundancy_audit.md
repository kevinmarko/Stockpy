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
| `data/fmp_feeds_market.py:73` | **`float('nan')`** | not checked | `float` (never `None`) |
| `data/fmp_feeds_company.py:75` | `None` | `None` | `Optional[float]` |
| `engine/advisory.py:1882` | **not checked — NaN passes through unchanged** | not checked | `float` |
| `validation/validation_history_store.py:177` | `None` | `None` | `Optional[float]` |

`fmp_feeds_market.py` and `fmp_feeds_company.py` are sibling FMP parsers in the same directory using
**opposite** bad-value sentinels. `engine/advisory.py:1882` is the most dangerous: the only copy that
never filters NaN, in the advisory engine, violating this repo's own CONSTRAINT #4 that five of the
other six docstrings explicitly cite.

Repro: `grep -n "_safe_float" <each file>` then read each function body directly.

## F3 — Option-symbol regex drift: one parser accepts strings the others reject

Byte-verified (line numbers drifted by 1 from the original draft):

- `pilots/options_risk.py:28` — `\$(?P<strike>…)` — **`$` required**
- `pilots/realtime_risk_streamer.py:40` — byte-identical copy of the above, `$` required
- `pilots/options_gex.py:266` — `\$?(?P<strike>…)` — **`$` optional**

A symbol lacking `$` parses in `options_gex.py` and returns `None` everywhere else — a behavioral fork
on the same nominal format. `realtime_risk_streamer.py:40` is additionally a byte-identical duplicate
of `options_risk.py:28`, not an import.

Repro: `grep -n "_OPTION_SYM_RE = re.compile" pilots/options_risk.py pilots/realtime_risk_streamer.py pilots/options_gex.py`.

## F4 — Black-Scholes: canonical pricer exists and is widely reused; 3 real holdouts, one numeric divergence

`pilots/options_risk.py::calculate_black_scholes_greeks` (line 50) is genuinely canonical, reused by
`scenario_matrix.py:30` (a **module-level** import — not lazy, unlike the rest), `zero_dte_engine.py:1237-1238`,
`volatility_surface.py:82/104/125`, `gamma_scalper.py:86,88`, `options_sor.py:191,193`,
`vol_mispricing.py:275,278`, `dispersion_trading.py:177,181` (all six of these via lazy in-function imports).

Genuine remaining copies:

- `pilots/multi_leg_pricing.py:54-127` — `calculate_black_scholes_leg_greeks`, near-verbatim copy (no
  drift found)
- `pilots/realtime_risk_streamer.py:123-191` — `compute_black_scholes_unit_greeks`, own copy + the
  duplicated regex above
- `pilots/options_gex.py:208-259` — own `_norm_pdf`/`_get_risk_free_rate`/
  `calculate_black_scholes_gamma`, and **drifted**
- `pilots/dispersion_trading.py:138-165` — own `calculate_straddle_vega`, inconsistent with
  `calculate_option_price()` a few lines below in the same file, which delegates correctly

**Correction to the original finding — the divergence direction was reported backwards.** Reading
both implementations directly:

- Canonical (`pilots/options_risk.py:134-136`): `vol_sqrt_t = sigma * np.sqrt(t_years)`; if that's
  below `_DEGENERATE_THRESHOLD`, it is **clamped** to the threshold and the Greek calculation
  continues with the floored value.
- `options_gex.py`'s own copy (lines 247-249): the same check **early-returns `0.0`** instead of
  clamping.

The original draft said the opposite (claimed the canonical version clamps-and-continues while
`options_gex.py`'s copy early-returns — that part was actually right; what was backwards was an
earlier internal draft of this sentence, corrected here). The net effect stands either way: for
tiny-but-nonzero `sigma·√t`, `options_gex.py` silently returns a `0.0` Gamma where the canonical
pricer would return a (small, floored) nonzero value — a real, live numeric divergence between the
two functions, not a stylistic difference.

`_get_risk_free_rate()` / a `0.045` default rate constant is separately redeclared in `options_gex.py`
(`DEFAULT_RISK_FREE_RATE`, lines 99/208), `vol_mispricing.py` (`DEFAULT_RISK_FREE_RATE`, lines
77/254), and `volatility_surface.py` (named `_DEFAULT_RFR` there, lines 56/64 — a naming
inconsistency on top of the repetition). All three agree on the value 0.045 — repetition, not drift.

Repro: `sed -n '130,140p' pilots/options_risk.py; sed -n '245,250p' pilots/options_gex.py` to compare
the `vol_sqrt_t` handling directly.

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

## F6 — N+1 network calls where a batch endpoint exists

`MarketDataProvider`'s ABC exposes only per-symbol `get_latest_quote()`, so every caller loops:

- `api/data_api.py:645-678` (`get_quotes()`) — its own docstring concedes *"We loop per symbol… There
  is no batch `get_quotes` on the provider"* — a documented limitation of the abstraction, not an
  oversight of an available method on the same interface (`fmp_client.batch_quote()` is a separate,
  lower-level FMP-specific function not exposed through the `CompositeProvider` abstraction this call
  site uses)
- `pilots/options_risk.py:421-428`, `pilots/scenario_matrix.py:400-406` — per-request loops
- `data/paper_account_store.py:1567-1613` (`settle_expired_options`) — per-position loop at line 1588
  calling `get_latest_quote()` at line 1610, despite the same file's `_resolve_position_prices` (line
  433) correctly calling `fmp_client.batch_quote()` at line 459
- `pilots/dispersion_trading.py:779-808` — 3 separate per-constituent loops: quote, IV resolution, and
  serial `get_bars()`

`evaluation_engine.py:1114-1129` similarly fetches bars serially per symbol (line 1174, memoized
per-symbol but not batched across distinct symbols) though `data/historical_store.py:1039` provides
`get_bars_bulk()` — reachable from the live `GET /calibration/summary`.

Confirmed via `git show --stat f96a3908` (the recent unbounded-blocking-call sweep) that it touches
`api/data_api.py` only in the unrelated `chat_endpoint` LLM-client construction path, and none of the
other files in this finding at all — every site above is still exactly as N+1 as originally found,
and the recent sweep neither fixed the pattern nor added per-call bounding to it.

Repro: `sed -n '644,678p' api/data_api.py`; `grep -n "get_bars_bulk\|def _get_bars" data/historical_store.py evaluation_engine.py`.

## F7 — `api/metrics_api.py` is the one API module outside the heavy-import guard

`tests/test_pilots_api.py:2124-2164` (`test_pilots_api_never_imports_heavy_engines`) AST-parses
`api/pilots_api.py` and asserts it never imports `{processing_engine, strategy_engine,
forecasting_engine, macro_engine, technical_options_engine, main_orchestrator, desktop}`.
`api/metrics_api.py` has no analogous guard and imports at module scope, lines 52-55:
`processing_engine`, `forecasting_engine`, `technical_options_engine`, and `sentiment_risk_engine`
(the fourth import wasn't in the original finding) — transitively pulling TensorFlow/Keras (guarded
by `try/except ImportError` but still eagerly attempted at import time, `forecasting_engine.py:36-38`),
statsmodels, sklearn (`:45-47`), and scipy (`technical_options_engine.py:17-18`) at import time.
`api/data_api.py` and `api/ws_api.py` were checked and remain clean. Cost is process startup and
pytest collection, not per-request.

**Context the original finding didn't include:** all four heavy engines imported by `metrics_api.py`
are genuinely used at runtime (`ProcessingEngine()` at lines 175/425, `ForecastingEngine()` at 211,
`build_premium_directive()` at 250, `SentimentRiskEngine()` at 311) — `tests/test_metrics_api.py`'s
docstring explicitly frames these as real dependencies, mocked only for test determinism. This is
less "an accidental duplicate import" and more "the one API module whose functionality genuinely
needs the heavy engines, and which lacks the guard that would at least document that tradeoff and
catch any *further* accidental heavy imports." The cold-start/memory cost on that standalone
port-8604 process is real regardless of intent.

Repro: `sed -n '2124,2164p' tests/test_pilots_api.py`; `grep -n "^from\|^import" api/metrics_api.py | sed -n '1,20p'`.

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

## Open item

A third background agent tasked with a dead-code sweep (test-only modules, uncalled endpoints, unread
settings flags) had not reported by the time this audit was finalized, and no completed output for it
was found anywhere in this repo's worktrees at time of writing. Findings F1–F14 above are
independently verified and stand alone. When the dead-code results land, fold them into this report
as an **F15** section, and add a PR-12 removal pass to the remediation roadmap if the volume justifies
one.
