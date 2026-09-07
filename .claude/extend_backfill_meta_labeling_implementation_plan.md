# Extend Forecast Backfill meta-labeling toward all 6 eligible signals

## Context

The Forecast Backfill screen trains meta-labelers for signals that declare
`meta_label_features`. Six signals declare them, but the last real run
(`~/.stockpy_local/output/agentic_forecast_summary.json`, 2026-09-06 22:37 UTC,
29 tickers / 6791 rows) produced only 12 model keys — 3 signals × 4 horizons.
The operator asked to extend coverage to all 6, and chose to build the missing
data rather than just disclose the gap.

### Measured starting state

Running the real `step_2` + `step_3` code path at current `HEAD` against a
synthetic 6-ticker × 1400-day panel (`FORECAST_BACKFILL_RSI_WINDOW` pinned to 14
to isolate from the operator's `.env` value of 400):

| Signal | Non-NaN `_Signal` rows | Status |
|---|---|---|
| `timeseries_momentum` | 6882/8400 (±1) | trains |
| `cross_sectional_momentum` | 5740/8400 (±1) | trains |
| `rsi2_mean_reversion` | 760/8400 (+1 only) | trains, one-sided by design |
| `options_flow_sentiment` | 8317/8400 (±1) | trains **on a price-momentum proxy** |
| `sector_quality_rank` | 0/8400 | blocked — no accrual/gross-profitability/sector data |
| `vrp_premium_selling` | 0/8400 | blocked — no historical `True_IVR`/`VRP` |

### Problems this addresses

1. **Stale, now-false disclosure.** PR #1015 (`2f99bd58`) widened
   `LIVE_ROW_FEATURE_WHITELIST` so all 6 signals pass
   `check_feature_compatibility()`, but touched no webapp files. The screen still
   tells the operator the gate "refuses every one of the 6 eligible signals".

2. **`options_flow_sentiment` silently became trainable on the wrong signal.**
   Its `compute()` falls back to a price-momentum proxy
   (`0.5·clamp(ROC_5/0.02) + 0.5·clamp(ROC_20/0.05)`,
   `signals/options_flow_sentiment.py:618-626`) when no UOA flow data exists.
   `ROC_5`/`ROC_20` did not exist in `step_2` before PR #1015
   (`git show 2f99bd58^:ml/forecast_backfill.py | grep -c ROC_5` → 0), so the
   fallback never fired. It now fires on every row — a model keyed
   `options_flow_sentiment_10d` would meta-label pure price momentum.

3. **Two signals have no training data** — WP3 and WP4 below.

### Two scope facts to state up front

- **Live promotion is a separate gate and is not a goal here.**
  `ml/registry_io.py:74` derives `deployable = cpcv_dsr > 0.95 AND pbo < 0.50`.
  The two existing AFML meta-labelers scored DSR `6.7e-45` and `5.4e-08`. Expect
  this work to register real rows with real DSR/PBO — **not** to make anything
  live-deployable. Do not describe it as "activating" the bridge.
- **`vrp_premium_selling` cannot be genuinely unblocked.** Real per-ticker
  historical IV does not exist from FMP or Yahoo (see WP4). It gets a
  research-only, quarantined proxy under a distinct name; the real signal stays
  honestly untrained.

---

## WP1 — Correct the stale disclosure (do first; it is currently false)

- `webapp/src/screens/ForecastBackfillScreen.tsx:371-374` — rewrite the "refuses
  every one of the 6 eligible signals" paragraph to state what is true: the
  compatibility gate now passes for all 6; what blocks live promotion is the
  DSR/PBO deployability gate.
- `webapp/src/screens/ForecastBackfillScreen.test.tsx:354` — asserts on
  `/refuses every one of the 6 eligible signals/`; update with the copy.
- `ml/meta_bootstrap.py:85-87` — same stale claim in a comment.
- `ml/forecast_backfill.py:1003` — dangling reference to a "KNOWN GAP docstring"
  that commit `8e000117` deleted; `cross_sectional_momentum` demonstrably trains.
- `ml/forecast_backfill.py:537` — comment lists `sector_quality_rank` as having
  no `meta_label_features`; it has had them since `73d9f8fe`.
- `ml/forecast_backfill.py:774` — `is_active` is a hardcoded 3-name list, so any
  newly-training signal is mislabelled "Diagnostic". Derive it instead.
- Docs: `CLAUDE.md` (auto-mirrors to `AGENTS.md` via
  `.claude/hooks/sync_agent_docs.sh`), `docs/plans/FORECAST_BACKFILL_PLAN.md`,
  the 6 `docs/signals/<name>.md` "Backfill-Screen Live Meta-Labeler Bridge"
  sections.

## WP2 — Honest per-signal eligibility reporting

`self.metrics` (`ml/forecast_backfill.py:767`) only gains a key when a combo
trains, and `allStrategies` (`ForecastBackfillScreen.tsx:45-52`) derives the
multi-select purely from `metrics` keys — so an untrained signal is invisible
*and* unselectable, a chicken-and-egg.

- Record a per-signal reason where the code currently only logs:
  `ml/forecast_backfill.py:648` (`Insufficient samples (%d)`), the
  `no_meta_label_features` / no-resolvable-features branches in
  `step_5_backtrain_meta_labelers`, and the all-NaN `_Signal` case from step 3.
- Emit as a **separate** `eligibility` block in `agentic_forecast_summary.json`
  — never as zero-valued `metrics` rows (CONSTRAINT #4).
- Surface through `GET /pilots/forecast_backfill` (`api/pilots_api.py:1048`),
  new optional fields on `ForecastBackfillSummary`
  (`webapp/src/api/types.ts:3961`), the mock (`webapp/src/api/mock.ts:16046`),
  and a new "Eligible signals not trained" section on the screen.
- Seed the multi-select from the eligibility block so all 6 are selectable.

## WP3 — PIT accrual + gross profitability (unblocks `sector_quality_rank`)

**Reuse, do not rewrite.** The PIT computation already exists and has been run
against live SEC EDGAR (~99% coverage on a 100-name S&P slice; one `BBY` timeout
degraded to NaN as designed — `docs/signals/sector_quality_rank.md`, 2026-08-22):

- `scripts/refresh_validations.py::_fetch_sneqr_quality_facts` (line 2504) —
  `accrual_ratio = -((NetIncomeLoss - CFO) / Assets)`,
  `gross_profitability = GrossProfit / Assets`, per SEC `filed` date, with
  documented fallback tags. Uses
  `data/edgar_fundamentals.py::extract_latest_fact(..., max_date=filed)`
  (line 243), which filters `point["filed"] <= max_date` — genuinely
  lookahead-free.
- `..._build_sector_quality_rank_adapter` (2623) — per-date panel assembly via
  `pd.merge_asof(..., direction="backward")`.
- `forecasting/data/ticker_sectors.csv` (502 rows) via `_load_ticker_sectors()`
  (4058) — the sector column.

Steps:
1. Extract `_fetch_sneqr_quality_facts` into a shared module (e.g.
   `data/sneqr_quality_facts.py`) importable by both `refresh_validations.py`
   and the backfiller, leaving the validation adapter byte-identical.
2. Persist both ratios so consumers stop re-fetching ~50-150 MB of companyfacts
   JSON per ticker: additive `ALTER TABLE ... ADD COLUMN` on
   `fundamentals_history` (`data/historical_store.py:195`), following the
   existing `_FUNDAMENTALS_HISTORY_ADD_REPORT_DATE_DDL` / `PRAGMA table_info`
   idempotent-probe convention at line 226.
3. Join both ratios plus `sector` onto the `(Date, Ticker)` panel in
   `step_2_calculate_technical_features` (`ml/forecast_backfill.py:259`), PIT
   forward-filled from `filed` dates.
4. **Required:** widen the per-date universe slice in
   `_run_cross_sectional_module` (`ml/forecast_backfill.py:441-447`), which
   hardcodes only `Symbol` + `XSec_12_1M`. Without this
   `sector_quality_rank.pre_compute` still sees nothing regardless of step 2.
   `compute_batch_xsec` (`signals/base.py:231`) takes only `ranks_wide` and does
   **not** fit this module, so it uses the per-date loop.
5. Live path (`processing_engine.py::calculate_fundamental_metrics`, new keys
   beside `book_to_market` at line 559): source live values from EDGAR PIT too,
   so train and serve share one definition. `'sector'` is already written at
   line 602. (The yahoo path could produce both ratios from the
   `quarterly_balance_sheet`/`quarterly_cashflow` frames already in memory at
   `data/market_data.py:699-706`, but those are current-snapshot, not PIT —
   mixing them would be a train/serve source mismatch.)

**Universe breadth is required, not optional.** `MIN_SECTOR_SIZE = 5`
(`signals/sector_quality_rank.py:184`) drops any sector with fewer than 5 names
that date. The operator's `DEFAULT_TICKERS` is 29 symbols across ~11 sectors
(~2.6/sector), so even with perfect data nearly every sector is excluded and
`sector_quality_rank` trains nothing. Reuse
`scripts/refresh_validations.py::_load_wide_universe(cap)` (line 180) — it
already sources an S&P roster with a 100-name capped tier and a safe fallback —
and expose it as an explicit run option.

Disclose, do not silently accept: there is **no point-in-time sector history** in
this repo (current snapshot applied across all history — an approximation
`macro_regime_pit` already accepts), `universe_engine.get_sp500_constituents`
returns the current roster for every historical date (membership survivorship
bias), and XBRL is unreliable before ~2009-2010
(`SNEQR_BACKTEST_START = "2010-01-01"`, line 2493).

## WP4 — Quarantined research proxy for `vrp_premium_selling`

**Real historical per-ticker IV is unavailable from permitted sources.**
`True_IVR` needs a dated ATM IV plus ≥252 prior dated IVs; `VRP` is literally
`current_iv − garch_vol`. `yfinance.option_chain` returns only the current chain;
`data/fmp_client.py` wraps zero options/IV endpoints; `iv_history`
(`volatility/iv_engine.py:24`) only accumulates forward from live runs; FRED's
only real IV series is index-level `VIXCLS`. Alpaca/Finnhub are excluded by repo
policy. `docs/VALIDATION_STRATEGY_FIX_LOG.md:704-707` already records this.

Approach — a research-only artifact that can never reach live inference:

1. Compute two clearly-named proxy columns in `step_2`, from OHLCV only:
   - `IVR_Proxy` — trailing-252d rolling percentile rank of the existing
     vectorized `GARCH_Vol` (`ml/forecast_backfill.py:313-314`).
   - `VRP_Proxy` — `RV60 − GARCH_Vol`, matching
     `volatility/iv_engine.get_vrp(current_iv=rv60, garch_vol=...)` exactly
     (`get_vrp` is subtraction) and the convention at
     `validation/options_selling_backtest.py:581-588`.
   Disclose in code that this is a *vectorized EWMA-GARCH* rank, not the per-date
   GJR-GARCH fit `calculate_realized_vol_rank` performs — chosen because fitting
   GARCH per date over ~1000 dates × N tickers is prohibitive.
   `IVR_Proxy` is already a first-class repo concept surfaced alongside (never
   replacing) `True_IVR` — `technical_options_engine.py:993,1038`.
2. **Leave `signals/vrp_premium_selling.py` untouched.** Add a backfill-only
   adapter registered under a distinct id (e.g. `vrp_premium_selling_proxy`)
   that reads `IVR_Proxy`/`VRP_Proxy`. This keeps live behaviour byte-identical,
   makes the distinct model key structural rather than conventional, and means
   it is automatically outside `BACKFILL_ELIGIBLE_SIGNAL_IDS`
   (`ml/forecast_backfill_registry_bridge.py:29`) so the bridge can never
   promote it.
3. Record provenance in the registry notes and label it in the UI as
   proxy-derived. `vrp_premium_selling` itself stays honestly untrained with the
   real reason shown by WP2.

State plainly in the docs: a model trained on this is a **realized-vol-regime
model**, not a volatility-risk-premium model. It contains no options-market
information and supports no VRP claim.

## WP5 — Suppress the `options_flow_sentiment` momentum proxy

Per the operator's decision, do not train on the proxy. Suppress the
`ROC_5`/`ROC_20` fallback (`signals/options_flow_sentiment.py:618-626`) along the
backfill path only, so the signal reports "no historical UOA flow data" via WP2's
eligibility block instead of silently training a mislabelled model. Live
per-ticker behaviour must stay unchanged — verify with a byte-identical-output
test on the live path.

## Out of scope — flag separately

`volatility/bootstrap_iv_history.py` writes `estimated_iv = rolling_vol + 0.038`
into the real `iv_history` table with no marker distinguishing synthetic rows
from real chain-derived ones, while its docstring calls the output "historical
ATM implied volatilities." Running it would silently make the live-path
`True_IVR` a realized-vol rank wearing an IV label. Write this up in
`docs/known_issues/` and spawn it as its own task; do not fix it here.

---

## Verification

Nothing below is optional — this repo's convention is that a task is not done
until the check has actually run and been shown to pass.

1. **Targeted Python suites**, all green:
   `tests/test_forecast_backfill.py`, `tests/test_train_meta_labelers.py`,
   `tests/test_meta_labeling.py`, `tests/test_registry_load.py`,
   `tests/test_sector_quality_rank.py`, `tests/test_refresh_validations.py`,
   `tests/test_historical_store.py`, `tests/test_edgar_fundamentals.py`,
   `tests/test_processing_engine.py`, `tests/test_strategy_engine.py`.
2. **New tests:**
   - PIT lookahead-freeness for the extracted accrual/GP fetcher — mutate a
     future filing, assert earlier dates are bit-identical (this repo's
     established perturbation convention).
   - `sector_quality_rank` produces non-zero trainable rows on a wide-universe
     fixture, and honest zero rows on a thin-sector one.
   - `options_flow_sentiment` yields no trainable rows in the backfill after WP5,
     while its live `compute()` output is byte-identical to pre-change.
   - The proxy adapter is absent from `BACKFILL_ELIGIBLE_SIGNAL_IDS`, so
     `bootstrap_meta_registry` can never register it.
   - `agentic_forecast_summary.json`'s `eligibility` block never fabricates a
     `metrics` row for an untrained signal.
3. **Real end-to-end run**, not a fixture: `POST /pilots/forecast_backfill/run`
   on a wide universe, then confirm against
   `~/.stockpy_local/output/agentic_forecast_summary.json` (note: `OUTPUT_DIR`
   resolves under `LOCAL_DATA_ROOT`, **not** the repo — the stale
   `/Users/kevinlee/Stockpy-live/output/` copy is from 2026-08-12 and misleads).
   Record the real per-signal trained/not-trained outcome and the real CPCV
   DSR/PBO. Do not assert any signal became deployable unless the registry
   genuinely says so.
4. **Webapp:** `npm run --prefix webapp typecheck` clean, `ForecastBackfillScreen`
   tests green, plus an actual `npm run dev` browser check of the corrected
   explainer copy and the new eligibility section (console clean + visual
   confirmation) — a typecheck alone does not prove the screen renders.
5. `ruff check . --select=F821,F822,F823,E9` clean (this repo's genuine-bug gate).

## Branch & PR

Runtime/signal/validation logic → feature branch + PR, never direct to `main`.
Suggested branch `extend-backfill-meta-labeling`. Per `CLAUDE.md` §6, commit
scoped artifacts (`.claude/backfill_meta_labeling_{implementation_plan,task,walkthrough}.md`)
— never bare `plan.md`/`walkthrough.md`. Given the size, WP1+WP2 are worth
landing as their own PR first (they correct a currently-false operator-facing
claim), with WP3/WP4/WP5 following.
