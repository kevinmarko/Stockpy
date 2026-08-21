# Signal: `sector_quality_rank`

**File:** `signals/sector_quality_rank.py`
**Default weight:** 15.0
**Score range:** `[-1.0, +1.0]`
**Regime gate:** Always active (no `is_active_in_regime` override — accrual quality and
gross profitability are not regime-conditional the way e.g. `rsi2_mean_reversion`'s
short-term mean reversion edge is)
**Pilot:** — (no Pilot yet; the module is currently **dormant** — see "Data Availability
Gap" below — a Pilot entry is deferred to the follow-up task that wires real
accrual/gross-profitability inputs into the live pipeline, matching `lgbm_ranker`'s
"dormant, no Pilot yet" convention)

---

## Rationale

Sector-Neutral Earnings-Quality Rank (SNEQR) combines two independently-documented
quality anomalies into one sector-neutral composite:

- **Sloan, R. G. (1996).** "Do Stock Prices Fully Reflect Information in Accruals and
  Cash Flows about Future Earnings?" *The Accounting Review*, 71(3), 289–315. — the
  accrual anomaly: firms whose reported earnings are propped up by high non-cash
  accruals (earnings that outrun operating cash flow) subsequently underperform firms
  whose earnings are more cash-backed. The market is shown to under-appreciate the
  lower persistence of the accrual component of earnings relative to the cash-flow
  component.
- **Novy-Marx, R. (2013).** "The Other Side of Value: The Gross Profitability Premium."
  *Journal of Financial Economics*, 108(1), 1–28. — gross profit scaled by total assets
  is a cleaner, less-manipulable profitability measure than net-income-based ratios
  (fewer accounting-discretion line items sit between revenue/COGS and the gross-profit
  figure than between revenue and net income) and independently predicts the
  cross-section of returns, especially among "expensive" (high book-to-market) stocks
  that a naive value screen would otherwise avoid.
- **Asness, C., Frazzini, A., & Pedersen, L. H. (2019).** "Quality Minus Junk." *Review
  of Accounting Studies*, 24(1), 34–112. — the sector/industry-neutral ranking
  mechanism this module borrows. Their QMJ construction ranks quality *within* an
  economically comparable peer group (their paper uses country and industry
  neutralization) rather than pooling raw scores across the whole cross-market
  universe, because "high quality for a bank" and "high quality for a software company"
  are not comparable on the same raw scale — different sectors have structurally
  different accrual and margin norms.

### Distinction from `signals/multifactor.py`'s `Quality_Z`

`multifactor.py`'s `Quality_Z` is the mean of `{returnOnEquity, operatingMargins,
grossMargins}` (or a `-debt_to_equity` fallback when none of those three are present),
z-scored **across the whole market** — no sector grouping (see `multifactor.py`'s
`pre_compute()`). It contains **no accrual measure at all**.

SNEQR is deliberately different on both axes:

1. It adds the Sloan (1996) accrual-quality dimension that `multifactor.py`'s
   `Quality_Z` omits entirely.
2. It standardizes **within sector** rather than market-wide — the
   Asness-Frazzini-Pedersen (2019) "Quality Minus Junk" construction choice — rather
   than `multifactor.py`'s cross-market z-score.

The two modules are complementary, not redundant, and both may be simultaneously
active in the aggregate score.

---

## Signal Logic

Two-phase hook pattern (`pre_compute` / `compute`), mirroring
`signals/cross_sectional_momentum.py` and `signals/multifactor.py`:

1. **`pre_compute(universe_df, context)`** — once per cycle, on the full universe:
   - Guards on `Symbol` and a sector column (`sector`, falling back to `Sector`) being
     present; missing → log a `WARNING`, leave `context.sector_quality_ranks` empty,
     never raise.
   - Guards on the two raw input columns, `accrual_ratio` and `gross_profitability`,
     being present in `universe_df` (see "Data Availability Gap" below — as of this
     module's introduction, neither is populated anywhere upstream).
   - **Thin-sector exclusion**: any sector with fewer than `MIN_SECTOR_SIZE` (5) names
     this cycle is excluded from ranking entirely — its tickers never receive a rank.
   - For the remaining eligible tickers, both raw inputs are z-scored **within sector**
     via `groupby(sector).transform(...)`, reusing `signals.multifactor._zscore_winsorize`
     (cross-sectional z-score, winsorized, NaN/degenerate-std-safe).
   - The two within-sector z-scores are averaged (`skipna=True`) into a composite, then
     converted to a **within-sector percentile rank** (`groupby(sector).rank(pct=True)`)
     — this final within-sector grouping (not a global rank across the whole eligible
     universe) is the literal sector-neutral mechanism: the best name in a structurally
     weaker sector still scores as well as the best name in a structurally stronger one.
   - Result stored as `{ticker: percentile}` in `context.sector_quality_ranks`.

2. **`compute(row, context)`** — once per ticker, cheap lookup only:
   - Miss (ticker not in `context.sector_quality_ranks`, or the dict is empty because
     `pre_compute` degraded) → `score=0.0`, `confidence=0.0`, a `WARNING:`-prefixed
     explanation. Never a `KeyError`.
   - Hit → `score = 2 * (percentile - 0.5)`, the same linear `[0, 1] -> [-1, +1]` remap
     `cross_sectional_momentum.py` uses. `confidence = |score|`.

| Percentile (within sector) | Score | Interpretation |
|---|---|---|
| 1.0 (top of sector) | +1.0 | Best accrual quality + gross profitability in its sector |
| 0.5 (sector median) | 0.0 | Neutral |
| 0.0 (bottom of sector) | −1.0 | Worst accrual quality + gross profitability in its sector |
| No rank (missed thin-sector cut, or inputs missing) | 0.0 | Neutral, `confidence=0.0`, `WARNING:` explanation |

---

## Failure Modes

| Failure | Behaviour |
|---|---|
| `Symbol` column missing from `universe_df` | `pre_compute` logs a `WARNING`, leaves `context.sector_quality_ranks` empty, returns. Never raises. |
| No sector column (`sector` or `Sector`) present | Same — `WARNING`, empty ranks, no raise. |
| `accrual_ratio` / `gross_profitability` columns missing from `universe_df` | Same — `WARNING`, empty ranks. **This is the current, verified state of the live pipeline** — see "Data Availability Gap" below. |
| Thin sector (< `MIN_SECTOR_SIZE` = 5 names this cycle) | That sector's tickers are excluded from ranking entirely — never force-ranked against too small a peer group. Documented, tested failure mode (`tests/test_sector_quality_rank.py::test_thin_sector_excluded_from_ranking`). |
| A ticker's both raw inputs are `NaN` (present column, missing value) | Its composite z-score is `NaN` (skipna mean over an all-NaN row is `NaN`, not a fabricated 0); excluded from the rank by `rank(pct=True)`'s own NaN handling. |
| Ticker not present in `context.sector_quality_ranks` at `compute()` time | Neutral score (0.0), zero confidence, `WARNING:`-prefixed explanation — never a `KeyError`. |
| Fundamentals staleness | `accrual_ratio`/`gross_profitability` would, once wired, be as fresh as `settings.FUNDAMENTALS_REFRESH_DAYS` (default 1 day) allows via `HistoricalStore.get_fundamentals()`'s caching — this module does not itself refresh data, it only consumes whatever is already in `universe_df` for the cycle. |
| Sector misclassification | The sector field is whatever `FundamentalDataDTO.sector` (sourced from the fundamentals provider, e.g. yfinance's `info["sector"]`) reports; a misclassified ticker is ranked against the wrong peer group. No independent sector-taxonomy validation is performed here — this is an inherited data-quality risk shared with every other sector-aware module in this codebase (e.g. `sector_heat_factor`, `sector_selection`). |

---

## Data Availability Gap (read before enabling in production)

As of this module's introduction, **neither raw input (`accrual_ratio`,
`gross_profitability`) is populated anywhere in this codebase's live per-cycle data
path.** This was verified directly by tracing every fundamentals source reachable from
`universe_df`:

- **`data/yahoo_fundamentals.py::compute_fundamentals()`** (the default
  `FUNDAMENTALS_SOURCE="yahoo"` primary) emits exactly 17 ratio keys
  (`FUNDAMENTAL_KEYS`) plus a set of derived ratios. It does carry internal alias
  tables for `NET_INCOME` and `GROSS_PROFIT` (used to compute other exposed ratios),
  but has **no alias table for total assets or operating cash flow**, so it cannot
  itself produce either `(NetIncome - OperatingCashFlow) / TotalAssets` (accrual ratio)
  or `GrossProfit / TotalAssets` (gross profitability) — the `TotalAssets` denominator
  is simply never extracted.
- **`data/market_data.py::YFinanceProvider.get_fundamentals()`** (the
  `FUNDAMENTALS_SOURCE="yfinance_info"` fallback) returns the raw yfinance `.info`
  dict, which does not reliably carry `totalAssets` (that lives only in the separate
  balance-sheet statement, which this fallback path does not pull).
- **`data/fmp_fundamentals.py`** (opt-in) is the same story: ratio-only.
- **`dto_models.py::FundamentalDataDTO`** has no `total_assets`, `operating_cash_flow`,
  `net_income`, or `gross_profit` dollar field at all — only ratios (P/E, P/B, dividend
  yield, ROE-derived quality, debt-to-equity, etc.) and `sector: str`.
- **`processing_engine.py::calculate_fundamental_metrics()`** — the function that
  actually populates `universe_df` for every existing multifactor raw input
  (`book_to_market`, `earnings_yield`, `quality_factor_score`, `low_vol_score`,
  `log_market_cap`) — never computes or writes an accrual ratio or a
  gross-profitability ratio today.

Per **CONSTRAINT #4** ("never fabricate a metric or number"), this module does **not**
invent a substitute formula out of unrelated ratios to make the composite "work" today.
Instead, `pre_compute()` declares the real contract it needs (`accrual_ratio`,
`gross_profitability` — see `RAW_INPUT_COLS` in `signals/sector_quality_rank.py`) and
degrades honestly — log a `WARNING`, leave `context.sector_quality_ranks` empty —
exactly like `multifactor.py`'s own `missing_inputs` guard, whenever those columns are
absent from `universe_df` (true in production today). The module is therefore
currently **dormant**: registered, weighted at 15.0 in `settings.SIGNAL_WEIGHTS`, but
contributing `0.0` to every cycle's `final_score` until a follow-up data-plumbing task
wires the two raw inputs into `processing_engine.calculate_fundamental_metrics()`.

`NetIncomeLoss`, `NetCashProvidedByUsedInOperatingActivities`, `Assets`, and
`GrossProfit` are all standard `us-gaap` XBRL tags already used point-in-time by
`data/edgar_fundamentals.py` for the historical PIT backfill path — the raw facts exist
and are already parsed elsewhere in this codebase, just not wired into the **live
per-cycle** `universe_df` construction yet. Wiring them in is left to a dedicated
follow-up task rather than folded into this module's introduction, so that task can be
independently validated (numeric-drift-tested against hand-computed values, per
CLAUDE.md's 1e-5 tolerance convention) without conflating it with this module's own
correctness.

---

## Interaction with Other Modules

- **`multifactor`**: see "Distinction from `signals/multifactor.py`'s `Quality_Z`"
  above — complementary, not redundant. Both may be active simultaneously; SNEQR adds
  the accrual dimension and sector-neutral ranking `multifactor.py` doesn't provide.
- **`dividend_quality`**: also earnings-quality-adjacent (payout-ratio sustainability),
  but from a completely different angle (cash-return-to-shareholders sustainability vs.
  accrual/profitability quality of the underlying earnings). No expected systematic
  conflict.
- **`graham_value`**: a pure valuation signal (intrinsic value vs. price) with no
  quality dimension — SNEQR can validate or contradict a Graham "cheap" signal by
  showing whether the cheapness is backed by genuine earnings quality or is a value
  trap (low accrual quality / weak gross profitability).
- Until the Data Availability Gap above is closed, this module contributes `0.0` to
  every cycle, so no live interaction with any other module actually occurs today —
  this section describes the intended interaction once real data is wired in.

---

## Empirical Notes

None yet — the module is dormant (see "Data Availability Gap"). No live cycle has ever
produced a non-empty `context.sector_quality_ranks`, so there is no empirical
parameter-sensitivity or regime-quirk finding to report. `MIN_SECTOR_SIZE = 5` was
chosen as a conservative floor (consistent with `_zscore_winsorize`'s own `< 2` hard
floor for a meaningful within-sector z-score) rather than derived from a sensitivity
sweep — that sweep is deferred until real data makes it possible to run one honestly.

---

## Backtest Validation

**2026-08-08 (native MultiIndex CPCV validation, `STRATEGY_REGISTRY["sector_quality_rank"]`)**

The live pipeline (`processing_engine.calculate_fundamental_metrics()`) still does not
compute `accrual_ratio`/`gross_profitability` — the module remains dormant in
production exactly as the "Data Availability Gap" section above describes. This entry
validates the SIGNAL ITSELF (not the live wiring) by sourcing both raw inputs directly
from real SEC EDGAR XBRL company facts — `data/edgar_fundamentals.py`'s
`get_cik`/`fetch_companyfacts`/`extract_latest_fact`, called directly from a new
adapter (`scripts/refresh_validations.py::_build_sector_quality_rank_adapter`) rather
than through `HistoricalStore` (verified: neither ratio exists in
`fundamentals_history`'s typed columns or `raw_json` — see that adapter's own
"CONSTRAINT #7 EXCEPTION" docstring for the full reasoning). This is also the first
`STRATEGY_REGISTRY` adapter to build a genuine `(Date, Ticker)` `pd.MultiIndex` panel
and an explicit `t1` Series, exercising `CombinatorialPurgedCV`'s native MultiIndex
support (PR #648) end-to-end through `StrategyValidationHarness.run(..., t1=...)`
(that harness parameter is new — added by this change, threaded through to
`run_cpcv_evaluation`).

**Universe (deliberately narrow, not the 10-ticker EDGAR-PIT universe the sibling
dividend/deep-value/value-quality adapters share):** SNEQR's entire mechanism is
WITHIN-SECTOR ranking, gated by `MIN_SECTOR_SIZE = 5`. The 10-ticker EDGAR-PIT
universe has at most 2 names per sector — every ticker would be thin-sector-excluded,
producing a vacuous, always-flat backtest. 12 tickers were hand-picked from names this
file already vets, chosen because they contain the two sectors that clear
`MIN_SECTOR_SIZE` (per `forecasting/data/ticker_sectors.csv`): **Technology** (7:
AAPL/CSCO/IBM/INTC/MSFT/ORCL/TXN) and **Consumer Defensive** (5: COST/KO/MO/PG/WMT).

**Book construction (documented choice, not a literal top-decile):** long-only,
equal-weighted, **top-half within sector** (percentile ≥ 0.5) rather than a literal
top-decile — a strict decile within a 5–7-name sector degenerates to picking exactly
the single best-ranked name per sector (percentile ranks for n=5 are
0.2/0.4/0.6/0.8/1.0 — only 1.0 clears 0.9), concentrating the whole book into 2 names
and testing idiosyncratic single-stock risk rather than the factor tilt. Top-half
matches the SAME choice every EDGAR-PIT sibling adapter already makes
(`dividend_yield_edgar_pit`/`deep_value_edgar_pit`/`value_quality_edgar_pit`), applied
here within each sector's own sub-cross-section rather than market-wide. `.shift(1)`
enforces no lookahead. Rebalance/embargo horizon (`t1`) is 63 calendar days
(~1 fiscal quarter — accrual/gross-profitability only refresh on a new 10-Q/10-K
filing, so a shorter horizon would purge/embargo more aggressively than the signal's
real information-refresh rate warrants).

**Real, measured numbers** (`python -m scripts.refresh_validations --strategies
sector_quality_rank --start 2010-01-01 --json`, live SEC EDGAR + yfinance, backtest
window 2010-01-01 → 2026-08-08, `n_cpcv_splits=10`/`n_test_splits=2`, single book
variant):

| Metric | Value | Gate | Result |
|---|---|---|---|
| Sharpe (net of cost) | 1.100 | > 0.5 | ✅ |
| PBO | 0.000 | < 0.5 | ✅ |
| DSR | 1.000 | > 0.95 | ✅ |
| Max Drawdown | 28.4% | < 30% | ✅ (narrow margin) |
| **Deployable** | **True** | | |

**PBO = 0.0 / DSR = 1.0 is expected, not a red flag** — this adapter has exactly ONE
book variant (`SNEQR_TopHalfWithinSector`), and PBO/DSR are measuring *selection bias
across candidates*; with only one candidate there is nothing to overfit a selection
to. This is the same "structurally cannot suffer selection bias" situation this file's
own `rsi2_mean_reversion` entry documents for the identical reason (a single-variant
adapter, deliberately, after that entry's own history of removing near-duplicate
variants specifically because CPCV's argmax selection over near-identical candidates
behaved as noise). It is not evidence the signal itself is robust in the AFML "PBO
across many candidate strategies" sense — only that *this* backtest doesn't introduce
selection-bias risk on top of whatever the signal's real edge (or lack of one) is.

**Max Drawdown passes by a narrow margin (28.4% vs. the 30% gate)** — worth flagging
honestly rather than glossing over: a 12-name, 2-sector universe concentrated further
into a ~6-name top-half book is meaningfully less diversified than this file's 30-name
cross-sectional adapters. **A wider universe was tried (2026-08-21) — MaxDD got WORSE,
not better.** This drawdown was NOT reduced by broader diversification as originally
expected; moving to a real 100-name, 8-sector universe instead pushed MaxDD to 34.2% and
flipped the strategy to `deployable=False`. See the addendum immediately below for the
real numbers and the honest, unresolved "why" question.

**What this DOES and DOES NOT validate:** this confirms the SNEQR mechanism (real
Sloan accrual quality + Novy-Marx gross profitability, ranked within-sector) produces
a real, net-of-cost, out-of-sample edge over this 12-ticker/2-sector universe and
2010–2026 window. It does NOT validate the live, in-production signal module as
currently shipped — that module is still dormant (contributes `0.0` every cycle) until
a separate data-plumbing task wires `accrual_ratio`/`gross_profitability` into
`processing_engine.calculate_fundamental_metrics()`. See the "Data Availability Gap"
section above for that remaining gap.

Tests: `tests/test_refresh_validations.py::TestBuildSectorQualityRankAdapter` (9
hermetic tests — MultiIndex shape, thin-sector exclusion, no-lookahead, per-fold
slicing, missing-EDGAR-data dead-letter), `tests/test_validation_sector_quality_rank.py`
(network-marked, the real end-to-end run above), `tests/test_harness_multiindex_t1.py`
(the new `StrategyValidationHarness.run(t1=...)` plumbing, hermetic).


### 2026-08-18 Full Validation Run (`sector_quality_rank`, rebased onto `main`)

| Metric | Result |
|---|---|
| **Sharpe Ratio (net)** | 0.9785 |
| **PBO** | 0.0000 |
| **DSR** | 1.0000 |
| **Max Drawdown** | 19.57% |
| **Deployable** | ✅ True |

### 2026-08-21 addendum: tiered universe widening — a real, measured drawdown regression

`SNEQR_UNIVERSE` changed from the 12-name, 2-sector hand-picked list documented above
(Technology 7, Consumer Defensive 5) to `_XSEC_UNIVERSE_CAPPED` — a deterministic,
alphabetically-sorted 100-name slice of the real, current S&P 500 constituent roster
sourced live from `universe_engine.get_sp500_constituents()`, shared with
`signal_replay_balanced_blend`/`lgbm_ranker` (the other two adapters whose cost scales
with ticker count). `forecasting/data/ticker_sectors.csv` was independently regenerated
to full 503/503 coverage as part of the same change, so this is a real, well-covered
measurement, not an artifact of a partially-populated sector lookup. Within this 100-name
universe, **8 sectors** now clear `MIN_SECTOR_SIZE=5` (vs. the old universe's 2):
Financial Services (24), Technology (16), Healthcare (12), Consumer Cyclical (12),
Industrials (10), Consumer Defensive (6), Utilities (6), Real Estate (6).

| Metric | Before (12-name, 2-sector universe) | After (100-name, 8-sector universe) | Gate |
|---|---|---|---|
| Sharpe | 0.979 | 0.919 | > 0.50 ✅ |
| PBO | 0.000 | 0.000 | < 0.50 ✅ (unchanged) |
| DSR | 1.000 | 1.000 | > 0.95 ✅ (unchanged) |
| MaxDD | 19.6% | **34.2%** | < 30% ❌ **FAIL** |
| `deployable` | True | **False** | |

**Real, measured, counterintuitive — not asserted as understood.** Sharpe and DSR barely
moved and PBO stayed 0.000, but MaxDD rose from 19.6% to 34.2%, crossing the 30% gate
and flipping this strategy from `deployable=True` to `deployable=False`. This is the
opposite of the "broader diversification reduces drawdown" expectation this section
previously stated before the wider universe was actually measured. No root cause is
asserted here beyond the observation itself — a within-sector top-half book spread
across more sectors and roughly 8x more names (from a ~6-name book to a real ~50-name
book) did not produce the naively-expected drawdown reduction in this measurement. Left
as an open question for a future investigation rather than papered over with a
plausible-sounding but unverified explanation (CONSTRAINT #4). `sector_quality_rank`'s
live status is unaffected either way — the module remains dormant in production (see
"Data Availability Gap" above) regardless of this backtest's `deployable` value. See
`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-21 entry for the full cross-strategy
writeup and the honest survivorship-bias scope caveat (this widening is BREADTH only,
not point-in-time constituent-membership correction).


*Note: The 2026-08-17 run verifies stability following a systemic parser fix. The `Deployable: False` outcome and its underlying causal reasoning remain exactly as previously documented.*
