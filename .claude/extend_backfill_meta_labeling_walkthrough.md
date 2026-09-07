# Walkthrough — extend Forecast Backfill meta-labeling to all 6 eligible signals

## What was asked

The Forecast Backfill screen trains meta-labelers for signals declaring
`meta_label_features`. Six signals declare them; the screen's own explainer
text claimed the feature-compatibility gate "refuses every one of the 6
eligible signals" — stale as of a prior PR (#1015). The operator asked to
extend real training coverage to all 6, and explicitly chose to build the
missing data pipelines rather than just disclose the gap where feasible.

## What was found (measured, not assumed)

Running the real `step_2`/`step_3` code path against a synthetic panel at the
start of this work:

| Signal | Status before this change |
|---|---|
| `timeseries_momentum` | trains |
| `cross_sectional_momentum` | trains |
| `rsi2_mean_reversion` | trains, one-sided by design |
| `options_flow_sentiment` | **newly began training on a price-momentum proxy** (an accidental side effect of an unrelated recent fix) |
| `sector_quality_rank` | blocked — no accrual/gross-profitability/sector data anywhere in this pipeline |
| `vrp_premium_selling` | blocked — no historical `True_IVR`/`VRP` |

## What was built

**WP1 — corrected the stale disclosure.** The compatibility gate has actually
passed for all 6 signals since PR #1015; what blocks live promotion is the
separate DSR/PBO deployability gate. Fixed the webapp explainer, a test
asserting the old text, `ml/meta_bootstrap.py`'s comment, two dangling/stale
comments in `ml/forecast_backfill.py`, and replaced a hardcoded 3-signal
`is_active` list with a derived `BACKFILL_ELIGIBLE_SIGNAL_IDS` membership
check.

**WP2 — honest per-signal eligibility reporting.** A new `self.eligibility`
dict + `_mark_eligibility()` helper records the *real, measured* reason a
declared-eligible signal didn't train (`insufficient_samples:N_for_Hd`,
`missing_required_features:...`, `compute_error:...`, etc.) — never a
fabricated metrics row. Surfaced through the summary JSON, the API, new
TypeScript types, the mock, and a new "Eligible Signals Not Trained" webapp
section. The strategy multi-select now also seeds from eligibility, so an
untrained signal can still be selected to re-run — closing a
chicken-and-egg gap (an untrained signal had no metrics row and therefore
couldn't appear as a selectable option either).

**WP3 — unblocked `sector_quality_rank` for real.** Extracted
`scripts/refresh_validations.py`'s already live-EDGAR-verified
`_fetch_sneqr_quality_facts` into a shared `data/sneqr_quality_facts.py`
module (that script now delegates to it, byte-identical behavior). Wired
real point-in-time `accrual_ratio`/`gross_profitability`/`sector` into
`ml/forecast_backfill.py`'s step 2 and the per-date cross-sectional universe
slice, gated behind `settings.FORECAST_BACKFILL_SNEQR_QUALITY_FACTS_ENABLED`
(default `False`, byte-identical when off — verified by a network-marked
test proving zero network calls with the flag off).

**Verified live, not assumed**: a real 6-name Technology-sector universe
(`AAPL`/`MSFT`/`AMD`/`ADBE`/`ADI`/`AVGO`) genuinely trains
`sector_quality_rank` across all 4 horizons against live SEC EDGAR data.

**WP4 — `vrp_premium_selling` cannot be genuinely unblocked.** Real
per-ticker historical implied volatility is structurally unavailable from
this repo's permitted FMP/Yahoo data sources — verified directly (no
`as_of` param on `yfinance.option_chain`, zero options/IV endpoints in
`data/fmp_client.py`, FRED's only real IV series is index-level `VIXCLS`).
Documented in a new `docs/known_issues/vrp_premium_selling_no_historical_iv.md`.
Instead: a quarantined, realized-vol-derived proxy
(`IVR_Proxy`/`VRP_Proxy`, OHLCV-only, no network) trains a *separately
registered* model_type, `vrp_premium_selling_proxy`
(`ml/vrp_premium_selling_proxy_signal.py`), gated behind
`settings.FORECAST_BACKFILL_VRP_PROXY_ENABLED` (default `False`). Never
touches `signals/vrp_premium_selling.py` (verified zero diff) and is
structurally excluded from `BACKFILL_ELIGIBLE_SIGNAL_IDS` — the bridge can
never promote it into live inference, checked mechanically by a test.

Required a new lookup mechanism: `signals.registry.global_registry.get()`
raises `KeyError` for an unregistered name rather than returning `None` —
every existing `if not module:` defensive check in `ml/forecast_backfill.py`
was previously dead code (every name reaching it was guaranteed to be
registered). A new `self._get_module()`/`self._proxy_modules` pair restores
the graceful-`None` contract for both real and backfill-only proxy modules,
with all 7 call sites converted.

**WP5 — suppressed `options_flow_sentiment`'s momentum-proxy fallback on the
backfill path only.** This signal's legitimate live-production fallback (a
price-momentum proxy fired only when no real UOA flow data exists) was
firing unconditionally against the backfill's dummy `SignalContext`, which
never carries real flow data — training a meta-labeler on pure price
momentum mislabeled as options flow sentiment. The original design brief for
this fix assumed the signal dispatches through `compute_vectorized()`; the
implementing agent verified this was wrong (the signal overrides
`pre_compute`, so it's actually routed through `_run_cross_sectional_module`'s
scalar `compute()` path) and implemented the fix at the correct location
instead of pasting non-functional code — dropping `ROC_5`/`ROC_20` from the
per-date row source fed to this one module's `compute()` calls, never
touching `self.data` itself or `signals/options_flow_sentiment.py` (verified
zero diff, and the live fallback proven to still fire unchanged via a direct
test against the real module).

## Process

Implemented across three phases: solo work for WP1-WP3 (with a full plan-mode
research pass across 6 exploration agents before writing code), then WP4 and
WP5 delegated to two parallel subagents plus a third read-only audit agent
reviewing the WP1-3 commit, then manual integration of all three agents'
output (one agent used isolated-worktree mode and its diff had to be ported
back via `git apply`) plus a fresh full verification pass and two doc
corrections the audit surfaced.

## Verification (all run this session, not assumed)

- `tests/test_forecast_backfill.py`: **60/60** (53 offline + 7 live-network,
  including two live SEC EDGAR verifications).
- `tests/test_train_meta_labelers.py` + `test_meta_labeling.py` +
  `test_registry_load.py` + `test_strategy_engine.py`: **97/97**.
- `tests/test_measure_settings_census.py`: **4/4** (regenerated for the 2 new
  settings, `FORECAST_BACKFILL_SNEQR_QUALITY_FACTS_ENABLED` and
  `FORECAST_BACKFILL_VRP_PROXY_ENABLED`).
- Webapp: `npm run typecheck` clean; `ForecastBackfillScreen.test.tsx` +
  `forecastBackfillCopy.test.ts`: **28/28**.
- `ruff check --select=F821,F822,F823,E9` clean across every changed Python
  file.
- A separate `tests/test_advisory.py` run showed 61 pre-existing errors,
  confirmed unrelated: a `numba`/`pandas_ta` function-caching `RuntimeError`
  specific to this sandbox's filesystem, triggered on import of
  `processing_engine.py` — reproduced in isolation, traced to
  `numba.core.caching.FunctionCache.__init__`, and confirmed absent when the
  same 4 non-advisory suites above are run without `test_advisory.py` in the
  same invocation (97/97 clean). Not touched by, or related to, any file this
  change modifies.

## Explicitly out of scope, disclosed not silently dropped

- No DB persistence/caching for the WP3 EDGAR fetch (every run re-fetches).
- The live per-cycle trading pipeline is untouched — `sector_quality_rank`
  stays dormant there.
- `volatility/bootstrap_iv_history.py`'s separate, pre-existing hazard
  (writes synthetic IV into the real `iv_history` table with no provenance
  marker) is documented but not fixed here, per the operator's explicit
  choice to spawn it as its own follow-up task.
