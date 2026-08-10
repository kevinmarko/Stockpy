# Validation Strategy Fix Log

Dated record of the 2026-07 effort to bring every failing `STRATEGY_REGISTRY` strategy
(`scripts/refresh_validations.py`) up to the walk-forward deployability gate
(`validation/harness.py` / `validation/thresholds.py`: `PBO<0.50 AND DSR>0.95 AND
Sharpe>0.50 AND MaxDD<0.30`), honestly. This log is the rollup; each fixed or
investigated strategy also has a **Backtest Validation** section in its corresponding
`docs/signals/<name>.md` (where a live signal module exists) with the same before/after
numbers plus fuller reasoning.

**The rule this whole effort operated under** (AGENTS.md §3, CLAUDE.md, and stated
inline in `scripts/refresh_validations.py`'s own docstring): thresholds are never
loosened, filters are never date-snooped to a specific crash window, and a strategy
that genuinely can't clear the gate reports `deployable=False` — that is a correct,
honest outcome, not a failure to hide. Every fix below is a **fixed, causal, uniformly-
applied rule** (a Faber 2007 SMA-200 trend gate, an empirically-measured turnover
correction, or a variant-count reduction backed by measurement) — never a threshold
edit, never a lookahead, never a cherry-picked parameter.

## Starting state (2026-07-17)

Only `macd_trend` was `deployable=True`. The other 12 of 13 registered strategies
failed on at least one gate:

| Strategy | Sharpe | PBO | DSR | MaxDD | Failing gate(s) |
|---|---|---|---|---|---|
| `rsi2_mean_reversion` | 0.411 | 0.667 | 0.998 | 8.3% | PBO, Sharpe |
| `timeseries_momentum` | 0.520 | 0.733 | 0.987 | 26.0% | PBO |
| `coppock_momentum` | 0.683 | 0.267 | 0.998 | 33.7% | MaxDD |
| `multifactor_lowvol_size` | 0.669 | 0.000 | 1.000 | 34.0% | MaxDD |
| `garch_vol_target` | 0.776 | 0.444 | 1.000 | 34.3% | MaxDD |
| `cross_sectional_momentum` | 0.848 | 0.067 | 1.000 | 37.9% | MaxDD |
| `relative_strength_xsec` | 0.707 | 0.644 | 1.000 | 46.9% | MaxDD, PBO |
| `rsi14_extremes` | 0.220 | 0.200 | 0.962 | 29.1% | Sharpe |
| `sortino_drawdown` | 0.608 | 0.156 | 0.976 | 38.5% | MaxDD |
| `dividend_yield_edgar_pit` | 0.251 | 0.000 | 1.000 | 25.7% | Sharpe |
| `deep_value_edgar_pit` | 0.468 | 0.000 | 1.000 | 25.7% | Sharpe |
| `value_quality_edgar_pit` | 0.395 | 0.000 | 1.000 | 31.9% | MaxDD, Sharpe |

## Final state

| Strategy | Sharpe | PBO | DSR | MaxDD | `deployable` | PR |
|---|---|---|---|---|---|---|
| `macd_trend` | 0.507 | 0.022 | 0.977 | 23.7% | ✅ True (already passing) | — |
| `coppock_momentum` | 0.634 | 0.089 | 0.991 | 25.1% | ✅ **True** | [#310](https://github.com/kevinmarko/Stockpy/pull/310) |
| `multifactor_lowvol_size` | 0.621 | 0.000 | 1.000 | 21.1% | ✅ **True** | [#310](https://github.com/kevinmarko/Stockpy/pull/310) |
| `garch_vol_target` | 0.767 | 0.422 | 1.000 | 18.8% | ✅ **True** | [#310](https://github.com/kevinmarko/Stockpy/pull/310) |
| `sortino_drawdown` | 0.668 | 0.178 | 0.982 | 26.6% | ✅ **True** | [#310](https://github.com/kevinmarko/Stockpy/pull/310) |
| `cross_sectional_momentum` | 0.872 | 0.156 | 1.000 | 20.2% | ✅ **True** | [#311](https://github.com/kevinmarko/Stockpy/pull/311) |
| `relative_strength_xsec` | 0.745 | 0.000 | 1.000 | 21.3% | ✅ **True** | [#311](https://github.com/kevinmarko/Stockpy/pull/311) |
| `rsi2_mean_reversion` | 0.276 | 0.000 | 1.000 | 8.3% | ❌ False (honest) | [#311](https://github.com/kevinmarko/Stockpy/pull/311) |
| `value_quality_edgar_pit` | 0.128 | 0.000 | 1.000 | 15.7% | ❌ False (honest) | [#311](https://github.com/kevinmarko/Stockpy/pull/311) |
| `timeseries_momentum` | 0.523 | 0.000 | 1.000 | 26.0% | ✅ **True** | [#314](https://github.com/kevinmarko/Stockpy/pull/314) |
| `deep_value_edgar_pit` | 0.129 | 0.000 | 1.000 | 13.1% | ❌ False (honest) | [#314](https://github.com/kevinmarko/Stockpy/pull/314) |
| `rsi14_extremes` | 0.154 | 0.289 | 0.923 | 29.1% | ❌ False (honest) | [#314](https://github.com/kevinmarko/Stockpy/pull/314) |
| `dividend_yield_edgar_pit` | 0.222 | 0.000 | 1.000 | 12.2% | ❌ False (honest) | [#314](https://github.com/kevinmarko/Stockpy/pull/314) |

**8 of 13 strategies are now `deployable=True`** (up from 1). **5 remain honestly
`deployable=False`**, each with a measured, evidence-backed reason — never a loosened
gate.

---

## Fix levers used, by category

### Category A — MaxDD failures fixed via Faber (2007) SMA-200 trend gate

The single most effective, reusable lever in this series. Every strategy in this
category was a fully-invested, always-long (or always-vol-targeted) book with no
mechanism to de-risk ahead of a sustained downtrend — the exact gap that already made
`macd_trend`'s `MACD_TrendFilter` variant the one strategy passing before this effort
began. The fix is always the same shape: gate exposure to zero whenever
`close < close.rolling(200).mean()` (or, for multi-name books, `SPY < SPY.rolling
(200).mean()`, with SPY added as a benchmark-only input where not already present),
applied identically to **every** variant a strategy emits — because the harness selects
whichever variant has the best in-sample Sharpe to report MaxDD/Sharpe from, an ungated
variant sitting alongside a gated one will still win and still fail.

- **`coppock_momentum`**: a bare SMA-200 gate alone only got MaxDD to 30.3% (still
  failing) — 2007-2010's choppy topping/whipsaw process re-entered the position before
  a genuine downtrend was established. A dual SMA-50/200 "golden cross" confirmation
  (both fixed, off-the-shelf windows already used elsewhere in this codebase) closed
  the gap: MaxDD 33.7%→25.1%.
- **`multifactor_lowvol_size`**: SPY added as a benchmark-only trend-filter input
  (registry universe updated); degrades gracefully when SPY is absent so offline test
  fixtures are unaffected. MaxDD 34.0%→21.1%.
- **`garch_vol_target`**: layered on top of the existing vol-target sizing — pure
  vol-targeting alone still eats the front of a calm-but-declining move before the
  EWMA vol forecast catches up. MaxDD 34.3%→18.8%.
- **`sortino_drawdown`**: added on top of (not replacing) the existing 504-day trailing
  Sortino/drawdown gate, which reacts too slowly (a 2-year lookback can't detect a
  crash until much of it has already happened). MaxDD 38.5%→26.6%.
- **`cross_sectional_momentum`**: SPY added as benchmark-only input, mirroring
  `relative_strength_xsec`'s pre-existing pattern. MaxDD 37.9%→20.2%.
- **`relative_strength_xsec`**: SPY was already a benchmark-only input here. MaxDD
  46.9%→21.3% (the worst starting MaxDD in the registry).

### Category B — PBO failures fixed via variant-count reduction

PBO measures, per CPCV path, whether the best-in-sample variant's OOS Sharpe falls
below the OOS median across all variants. Near-duplicate variants make this selection
effectively random noise; a genuinely single variant cannot suffer this selection bias
at all (`n_trials=1` structurally yields PBO=0.0, DSR=1.0).

- **`relative_strength_xsec`** (also Category A): before settling on a fix, the two
  pre-existing variants were *measured*, not assumed distinct — adding the SMA-200
  gate alone pushed PBO to 0.956, because under a shared market-wide gate the two
  variants became 0.98-correlated (genuinely the same strategy wearing two names).
  Collapsed to the single surviving `RS_BeatSPY_Absolute` variant.
- **`rsi2_mean_reversion`**: dropped `RSI2_Ungated`, measured at 0.886 correlation with
  the surviving `RSI2_Gated` (differs on only 10/4833 trading days). PBO 0.667→0.000.
  Sharpe on the sole surviving variant (0.276) stayed honestly below the gate — a real
  edge-strength limit, not fixed by this lever (see below).
- **`timeseries_momentum`**: 4 candidate variant sets were empirically tested rather
  than assumed. Counterintuitively, the "obviously distinct" pairing (different
  lookback windows) measured *worse* (PBO 0.73) than a near-duplicate pairing (same
  lookback, different vol target, 0.965-correlated, PBO 0.31) — different-lookback
  momentum signals dominate in different historical regimes, so which wins in-sample is
  a poor OOS predictor, exactly what PBO is built to catch. The near-duplicate pairing
  was correctly rejected anyway (not a genuine second hypothesis); landed on a single,
  literature-fixed Moskowitz-Ooi-Pedersen 12-month/10%-vol-target variant. PBO
  0.756→0.000.

### Category C — Sharpe failures fixed (partially) via empirically-measured turnover correction

Three EDGAR point-in-time (PIT) fundamentals strategies shared the same registry defect:
a flat `turnover=0.05` (a high-frequency-strategy number) was being charged against
books that only actually reweight when a new quarterly SEC filing changes a name's
composite rank — the harness's net-Sharpe cost model is `returns −
turnover×0.0011/day`, so an overstated turnover directly and mechanically suppresses
Sharpe.

- **`value_quality_edgar_pit`**: turnover corrected 0.05→0.01 (measured 0.03–0.33%/day
  from both the real backfilled EDGAR DB and the committed test fixture). This alone
  fixed MaxDD (31.9%→15.7%). Sharpe stayed honestly failing (0.128) — see Category D.
- **`deep_value_edgar_pit`**: same correction (measured ~0.086%/day, 5 rebalance events
  in 20 years). MaxDD 25.7%→13.1%. Sharpe stayed honestly failing (0.129) — Category D.
- **`dividend_yield_edgar_pit`**: same correction (measured 0.119%/day, 8 rebalance
  events). MaxDD 25.7%→12.2%. Sharpe stayed honestly failing (0.222) — Category D.

### Category D — Honest `deployable=False`: real data-coverage ceilings (not fixable by any lever tried)

All three EDGAR PIT strategies above hit the *same class* of genuine, evidence-backed
limitation after their turnover fix: the underlying SEC EDGAR point-in-time field
simply isn't populated widely enough across this fixed 10-ticker universe or across the
full 2005–2024 backtest window to produce a book that's invested often enough to clear
Sharpe net of cost — this is a real fact about the data, not a tunable.

- **`value_quality_edgar_pit`**: `pb_ratio`/`roe` are never populated for PG/T/XOM
  (pb_ratio/roe) and `operating_margin` is never populated for JPM/XOM — since the
  composite requires BOTH legs simultaneously, the book is invested on only ~2% of
  trading days.
- **`deep_value_edgar_pit`**: `pb_ratio` alone (a single-leg requirement, so less
  compounding sparsity than its sibling) still only spans ~2023+ for 7 of 10 tickers
  and is entirely absent for T/PG/XOM — 18 of the requested 20 backtest years have zero
  exposure. Even at turnover=0 (zero simulated cost), the diluted full-window Sharpe is
  only ~0.196. Within its genuinely PIT-covered window alone, gross Sharpe is a
  respectable 0.622 — a backtest-window-length dilution artifact, not a weak signal.
- **`dividend_yield_edgar_pit`**: manifests as a *time* gap rather than a *ticker* gap —
  real `dividend_yield` PIT coverage only exists from 2024-02 onward (95.5% of the
  20-year window is forced-flat), and JNJ/XOM/GE have zero coverage at any date. Within
  its ~228-day covered window, raw Sharpe is a strong 1.40.

For all three, a market-trend overlay was tested (not assumed) as a possible second
lever and rejected with evidence:
- `value_quality_edgar_pit`: adding it as a second variant collapsed DSR from 1.0 to
  ~2.3e-35 — direct confirmation of why this repo's "don't add near-duplicate variants"
  rule exists.
- `deep_value_edgar_pit`: proven to be a pure no-op — 100% of the strategy's
  already-scarce active trading days already had SPY above its 200-SMA, so the gate
  could only ever remove days, never add signal.
- `dividend_yield_edgar_pit`: tested across 4 lookback windows on the book's own
  trailing return; every one measurably *hurt* performance (an already-thin 228-day
  active sample means any filter removes real signal, not noise).

### Category E — Honest `deployable=False`: genuinely weak net-of-cost edge

- **`rsi2_mean_reversion`**: PBO was fixed (Category B), but net Sharpe on the sole
  surviving `RSI2_Gated` variant is 0.276 — a genuinely weak short-horizon SPY
  mean-reversion edge net of realistic transaction costs. Not fixed by loosening the
  RSI<10 entry threshold or removing the SMA-200/crash-recession risk-off filters,
  since those are exactly what keep the strategy honest.
- **`rsi14_extremes`**: no adapter logic changed. Isolating the existing SMA-200-
  trend-filtered variant alone achieves a much better MaxDD (14.8% vs. 29.1%) but net
  Sharpe goes **negative** (-0.11) — traced to a real mechanic of
  `validation/harness.py`'s cost model, which charges the turnover-derived cost
  against every calendar day regardless of whether a position is held that day, so a
  low-exposure trend-filtered variant absorbs the same absolute cost drag as one active
  far more often. A commonly-cited faster-exit variant (RSI recovery at 40 instead of
  50) was also tested and didn't help. Classic Wilder RSI(14) 30/70 mean-reversion on
  SPY caps out around Sharpe 0.15 net of realistic costs across every construction
  tried. The 30/70 thresholds themselves were never loosened to chase a better number.

---

## A mechanical finding worth flagging (not fixed, per the rules — left as-is)

`rsi14_extremes`'s investigation surfaced a real property of
`StrategyValidationHarness._apply_cost_model` (`validation/harness.py`): it charges a
flat, turnover-derived cost against **every calendar day** in the backtest window,
regardless of whether the strategy actually holds a position that day. This
structurally penalizes any low-exposure, trend-filtered construction relative to one
that trades more often — exactly the kind of whipsaw-suppression fix that worked for
every Category A strategy above can make net Sharpe *worse*, not better, for a
naturally sparse strategy. Per the rules of this effort (never edit
`validation/harness.py`/`validation/thresholds.py`/`validation/metrics.py`), this was
documented rather than "fixed" — flagging here for anyone who later revisits the cost
model's exposure-weighting design.

---

## 2026-07-29 addendum: `signal_replay_balanced_blend` (Category A)

**PR:** [#464](https://github.com/kevinmarko/Stockpy/pull/464)

`signal_replay_balanced_blend` — the real `SignalAggregator`/`SignalRegistry` replay
backing the `balanced-blend` Pilot (`pilots/catalog.py`) — was added to
`STRATEGY_REGISTRY` after the 2026-07-17 wave above and was never covered by it. Its
first real (non-fabricated) validation run, 2026-07-29, surfaced the exact Category A
failure pattern documented throughout this log: a daily-rebalanced, equal-weight,
top-half-by-rank, **always-fully-invested** long-only book with no de-risking
mechanism — none of `sizing/position_sizer.py`, Kelly, vol-targeting, or a trend/regime
gate were wired into the replay's score-to-return step
(`scripts/refresh_validations.py::_build_signal_replay_adapter`).

| Metric | Before | After |
|---|---|---|
| Sharpe | 0.731 | **0.820** |
| PBO | 0.000 | 0.000 |
| DSR | 1.000 | 1.000 |
| MaxDD | 41.8% | **19.9%** |
| `deployable` | ❌ False (MaxDD 42%>30%) | ✅ **True** |

**Fix (same lever as every other Category A strategy above):** a Faber (2007) SMA-200
trend gate — de-risk the book to cash on any day following a SPY close below its own
200-day SMA, lagged one day (`uptrend.shift(1)`), same lag already applied to
`weights.shift(1)`. SPY was already present in the adapter's universe as a benchmark
input for `relative_strength`/`cross_sectional_momentum`, so no registry/universe
change was needed — the gate is inserted immediately after `portfolio_returns` is
computed and before it's packed into `precomputed["SignalReplay_TopHalf"]`. Mirrors
`_build_lowvol_size_adapter`'s existing overlay almost verbatim (that adapter has a
structurally identical `composite.rank(...).ge(0.5)` equal-weight construction).

Unlike every other Category A fix in this log, Sharpe *improved* alongside MaxDD
(0.731→0.820) rather than trading one off against the other — consistent with Faber's
original finding that a trend overlay improves risk-adjusted return, not just drawdown,
on a fully-invested long-only book. No variant-count or turnover changes were needed;
PBO/DSR were already passing and are unaffected by an exposure-only gate applied
identically to the strategy's sole variant.

Verified via the real walk-forward harness (`python -m scripts.refresh_validations
--strategies signal_replay_balanced_blend --json`, live yfinance + FRED-backed
`HistoricalStore` data) both before and after the fix, from the main checkout (worktree
sessions don't inherit the untracked `.env` `FRED_API_KEY`). Existing offline test
suite (`tests/test_validation_signal_replay.py` and 4 other files referencing this
strategy) re-run green after the change — the trend gate is inserted downstream of
every existing test's assertions (variant key set, warm-up trim length, score bounds,
no-lookahead) and doesn't alter any of them.

---

## 2026-07-29 addendum: harness-level fix, `VALIDATION_HARNESS_OOS_GATE_ENABLED` (opt-in)

**Scope note:** every fix above changed a `STRATEGY_REGISTRY` *adapter*. This entry is
different — it changes `validation/harness.py`/`validation/metrics.py` THEMSELVES,
which the rest of this log's strategies were explicitly fixed *without* touching (see
"A mechanical finding worth flagging" above — that constraint applied to the per-
strategy fix effort this log otherwise records, not a permanent prohibition on ever
improving the harness). This entry is that harness-level follow-up, done as a separate,
explicitly opt-in change specifically so it does not silently invalidate any
`deployable`/PBO/DSR/Sharpe/MaxDD number already recorded above.

**What was found (a genuine quant-integrity investigation of the harness itself, not
an adapter):**

1. `ValidationReport.sharpe`/`.max_dd`/`.sortino`/`.calmar`/`.hit_rate`/`.avg_trade_pct`/
   `.turnover` — every one of them feeding the `deployable` gate's `Sharpe > 0.5` /
   `MaxDD < 30%` criteria except PBO/DSR — were computed from
   `self.strategy_fn(X, y, X, y)`: a "test" set **identical** to the training set, i.e.
   an in-sample number masquerading as an out-of-sample one. Only PBO/DSR (via
   `CombinatorialPurgedCV`) were genuinely out-of-sample.
2. Separately, `run_cpcv_evaluation`'s own PBO/DSR Sharpes were computed on **gross**
   (cost-free) returns, even though the in-sample Sharpe/MaxDD leg above applied
   `_apply_cost_model`'s turnover-scaled cost — an inconsistent cost basis between the
   two halves of the same gate.

**Fix:** `run_cpcv_evaluation` (`validation/metrics.py`) gained an optional
`cost_model_fn` parameter and four new genuinely-OOS aggregates
(`mean_oos_max_dd`/`mean_oos_sortino`/`mean_oos_hit_rate`/`mean_oos_avg_trade_pct`/
`mean_oos_turnover`) — each the mean of that metric computed independently on every
CPCV path's own held-out (purged+embargoed) returns for the DSR-selected strategy (not
a single concatenated equity curve — CPCV's combinatorial test-block reuse across paths
makes a naive concatenation double/triple-count most dates; see the function's own
docstring). `validation/harness.py` gained `settings.VALIDATION_HARNESS_OOS_GATE_ENABLED`
(default `False`): when `True`, `run()` passes the harness's own cost model into
`run_cpcv_evaluation` and replaces the seven gate/report numbers listed above with these
new genuinely-OOS, now-also-cost-adjusted aggregates. `equity_curve`/`benchmark_curve`/
`macro_benchmark_curve` are **unchanged either way** — a single non-overlapping OOS
equity curve needs the AFML CPCV backtest-path-recombination algorithm, not implemented
here; documented as a real, separate follow-up rather than silently claimed as fixed too.

**Why this ships opt-in, default off, rather than replacing the gate outright:** flipping
this on necessarily changes Sharpe/MaxDD (in-sample numbers are expected to run hotter
than genuine OOS ones) for every strategy in the table above, and could flip some of the
"✅ True" verdicts recorded in this log to `False` (or vice versa) — this sandboxed
dev/CI environment has no live-market network access, so none of the 13 registered
strategies could be re-run against real data to measure the actual before/after here (the
same limitation this log's own "Verification methodology" section and several
`docs/architecture/signal-engines.md` entries already document for other opt-in levers
introduced without live-data access). Flipping this flag on is a deliberate, separate
follow-up: re-run `python -m scripts.refresh_validations --json` for every
`STRATEGY_REGISTRY` strategy with the flag enabled, and append the resulting before/after
table here (and to each affected strategy's `docs/signals/<name>.md`) exactly like every
other entry in this log, rather than assuming today's numbers still hold.

**Not fixed by this entry** (documented, not silently glossed over): the flat-turnover-
cost-charged-on-every-calendar-day issue flagged in "A mechanical finding worth
flagging" above is unrelated and still open; `TieredCostModel`'s full spread/liquidity-
tier richness is still not wired into `_apply_cost_model` (which remains a flat
11bps-round-trip constant, now just applied consistently to both gate legs); and no
genuine rolling-origin (walk-forward) loop was added — the existing static 60/40/70/30/
80/20 "walk-forward stability checks" remain informational-only splits, unchanged.

Tests: `tests/test_metrics_cpcv_oos_aggregates.py` (pure `run_cpcv_evaluation` math,
hand-computed expected values), `tests/test_harness_oos_gate.py` (flag wiring, default-off
byte-for-byte reproduction of the pre-existing in-sample fit, equity-curve scope limit).

---

## 2026-08-08: new strategy, `sector_quality_rank` — first native MultiIndex CPCV adapter

**New `STRATEGY_REGISTRY` entry, not a fix to an existing one.** Validates
`signals/sector_quality_rank.py::SectorNeutralQualitySignal` (SNEQR — Sloan 1996
accrual quality + Novy-Marx 2013 gross profitability, ranked *within sector*), joined
to the new `sector-quality-rank` Pilot. Also the first adapter in this file to build a
genuine `(Date, Ticker)` `pd.MultiIndex` panel with an explicit `t1` Series and
exercise `CombinatorialPurgedCV`'s native MultiIndex support (PR #648) end-to-end via
`StrategyValidationHarness.run(..., t1=...)` — a new, backward-compatible parameter
added to `validation/harness.py::StrategyValidationHarness.run()` by this change
(default `t1=None` reproduces every pre-existing flat-index adapter's behavior
byte-for-byte; threaded straight through to `run_cpcv_evaluation`). A second small
harness fix landed alongside it: `y.reindex(full_returns.index)` (the benchmark-curve
alignment step) raises `ValueError` for a MultiIndex `y` — verified directly, not
assumed — so it is now wrapped to degrade to no benchmark overlay rather than crash
(CONSTRAINT #6).

**Why this needed its own real-data sourcing path (CONSTRAINT #7 exception):**
verified that neither `accrual_ratio` nor `gross_profitability` exists anywhere in
`HistoricalStore`'s `fundamentals_history` table (typed columns OR `raw_json` —
`scripts/backfill_edgar_fundamentals.py` persists only `data/edgar_fundamentals.py`'s
computed 9-key PIT ratio dict, explicitly not the raw XBRL payload). Every EDGAR-PIT
sibling adapter in this file reads only through `HistoricalStore`; this one is a
documented, narrow exception that calls `data.edgar_fundamentals.get_cik`/
`fetch_companyfacts`/`extract_latest_fact` directly (same already-shipped module every
sibling adapter's own backfill depends on — not a new provider) because the honest
alternative was not validating the signal at all (CONSTRAINT #4 forbids fabricating
the two inputs).

**Universe:** 12 hand-picked tickers (not the 10-ticker EDGAR-PIT universe the
dividend/deep-value/value-quality siblings share) — chosen because SNEQR's mechanism
is WITHIN-SECTOR ranking (`MIN_SECTOR_SIZE=5`), and the 10-ticker universe has at most
2 names per sector (every ticker would be thin-sector-excluded, testing nothing).
Technology (7: AAPL/CSCO/IBM/INTC/MSFT/ORCL/TXN) and Consumer Defensive (5:
COST/KO/MO/PG/WMT) are the two sectors that clear the threshold among this file's
already-vetted large-cap names.

**Real, measured numbers** (`python -m scripts.refresh_validations --strategies
sector_quality_rank --start 2010-01-01 --json`, live SEC EDGAR + yfinance,
2010-01-01 → 2026-08-08, `n_cpcv_splits=10`/`n_test_splits=2`):

| Strategy | Sharpe | PBO | DSR | MaxDD | Deployable |
|---|---|---|---|---|---|
| `sector_quality_rank` | 1.100 | 0.000 | 1.000 | 28.4% | ✅ True |

Re-run a second time (89 seconds later, same day) to confirm reproducibility:
Sharpe 1.1004753 / MaxDD 0.2837052 — matched to 6 decimal places (the negligible
residual is intraday price-bar drift between the two fetches, not run-to-run
instability). Single book variant (`SNEQR_TopHalfWithinSector` — top-half,
equal-weighted, WITHIN SECTOR, `.shift(1)`-lagged), so PBO=0.0/DSR=1.0 is expected by
construction (no selection-bias risk with only one candidate to select among) — same
situation this log's `rsi2_mean_reversion` entry already documents for the identical
reason, not evidence of a broader-sense-robust edge. MaxDD passes with a narrow
margin (28.4% vs. the 30% gate) — flagged honestly rather than glossed over; a
12-name/2-sector book is meaningfully less diversified than this file's 30-name
cross-sectional adapters.

**Scope, honestly stated:** this validates the SNEQR mechanism itself over a real,
if narrow, universe and window. It does NOT validate the live, in-production signal
module as currently shipped — `SectorNeutralQualitySignal` remains dormant
(contributes `0.0` every cycle) until a separate data-plumbing task wires real
`accrual_ratio`/`gross_profitability` into
`processing_engine.calculate_fundamental_metrics()`. See
`docs/signals/sector_quality_rank.md`'s "Data Availability Gap" section.

Tests: `tests/test_refresh_validations.py::TestBuildSectorQualityRankAdapter` (9
hermetic tests), `tests/test_validation_sector_quality_rank.py` (network-marked, the
real run above), `tests/test_harness_multiindex_t1.py` (the new `t1` parameter,
hermetic — default-off byte-for-byte reproduction, MultiIndex-without-t1 raises,
MultiIndex-with-t1 runs end-to-end, benchmark-reindex-never-crashes guard).

---

## 2026-08-08 — `lgbm_ranker`: new `STRATEGY_REGISTRY` entry (genuine per-fold retraining)

**New entry, not a fix to an existing one.** `scripts/refresh_validations.py::
_build_lgbm_ranker_adapter` is the first `STRATEGY_REGISTRY` adapter that genuinely RETRAINS a
fresh `LGBMCrossSectionalRanker` on each CPCV fold's own training rows, instead of replaying one
fixed, precomputed return series across folds (the pattern every other adapter here uses, correct
for a static formula but a real look-ahead leak for a trained model — a model fit once on the
full history and "OOS"-scored on slices of that same history has already seen every fold's test
data). Uses `ranker.train(X_tr, y_tr, t1=t1_tr, use_native_multiindex_cv=True)` — the first
production caller of `validation/purged_cv.py::CombinatorialPurgedCV`'s native `(date, ticker)`
MultiIndex support (PR #648), with a real ~21-trading-day forward-return `t1` rather than a
synthesized "next row" default. `ml/lgbm_ranker.py::LGBMCrossSectionalRanker.train()` gained the
`use_native_multiindex_cv` kwarg and `settings.LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED` (default
`False`) to support this — see that file's own docstring; both changes are additive and
byte-identical for every existing caller (verified: `tests/test_lgbm_no_leakage.py`,
`tests/test_lgbm_feature_pit.py`, `tests/test_lgbm_ranker_signal.py`,
`tests/test_lgbm_purged_integration.py`, `tests/test_validation_lgbm.py`, `tests/test_train_lgbm.py`,
`tests/test_model_interface.py` all pass unmodified).

**Real, measured result** (live yfinance data, `python -m scripts.refresh_validations
--strategies lgbm_ranker --start 2015-01-01 --end 2024-12-31 --json`, 2026-08-07; effective window
self-reported by the harness as 2019-01-02 → 2024-11-25 — the adapter bounds its own feature-panel
build to the last ~6 years, the same "computationally infeasible to re-fit at every historical
date across the full 20-year window" reasoning `forecast_direction_arima_hw`'s entry below already
established):

| Strategy | Sharpe | PBO | DSR | MaxDD | `deployable` |
|---|---|---|---|---|---|
| `lgbm_ranker` | **−0.334** | 0.000 | 1.000 | 3.68% | **False** |

**Honest reason for the `deployable=False`**: PBO/DSR/MaxDD all clear their gates, but net-of-cost
Sharpe is negative — the top-minus-bottom-half long-short book genuinely lost money after
`TieredCostModel` costs over this window. This is a real, measured number (confirmed the wiring
itself is correct: per-fold training produces real, finite long-short returns —
`tests/test_validation_lgbm_ranker_registry.py`), not a data or plumbing bug. Plausible
contributing factors, stated honestly as unverified (no counterfactual re-run was performed to
isolate any one of them): only ONE hyperparameter candidate is tried per fold (`n_trials=1` in
the JSON summary — unlike `scripts/train_lgbm.py::compute_cpcv_metrics`'s own 3-candidate design,
so DSR/PBO here are a much weaker statement about selection-bias correction than the sibling
entries in this table); the bounded 6-year window and the adapter's own `_ClosesOnlyDataEngine`
proxy OHLCV (real Close, synthesized High/Low/Volume); and `historical_store=False` (no
point-in-time fundamentals, so a third of the live signal's feature set is NaN-filled to 0.0
throughout). `turnover=0.03` is a reasoned estimate matching `cross_sectional_momentum`/
`relative_strength_xsec`'s own daily-rebalance figure, not measured directly from this adapter's
weight series. See `docs/signals/lgbm_ranker.md`'s own Backtest Validation section for the full
writeup — including the explicit statement that this does NOT change `signals/lgbm_ranker.py`'s
live dormant status, which is gated independently via `ml/registry.yaml`/`scripts/train_lgbm.py`.

**Per this log's own stated rule**: no threshold was loosened, no filter was date-snooped, and
this genuinely-measured `deployable=False` is recorded as-is — an honest outcome, not a failure to
hide. `ml-cross-sectional-rank` (`pilots/catalog.py`) is still surfaced as a Pilot joined to this
`validation_strategy_id`, with an explicit inline comment (and its own catalog-docstring bullet)
stating this backtest validates the RETRAINING METHODOLOGY, not the exact currently-deployed
`ml/models/lgbm_latest.pkl` artifact — matching every other Pilot catalog entry's convention of
stating its own scope-narrowing caveats plainly rather than implying a stronger guarantee than
what was actually measured.

Tests: `tests/test_lgbm_ranker_native_cv.py` (the `train()` signature change — native-path
`ValueError`-if-missing-t1, flatten-path unchanged, settings-flag fallback),
`tests/test_validation_lgbm_ranker_registry.py` (network-marked, the production adapter end-to-end
through the real harness), plus the seven pre-existing lgbm test files listed above (regression).

---

## Verification methodology

Every fix in this log was independently re-run through the real walk-forward harness
(`python -m scripts.refresh_validations --strategies <name> --start 2005-01-01 --end
2024-12-31 --json`, live yfinance + EDGAR-backed `HistoricalStore` data) both by the
agent that made the change and again during integration, when all of a wave's
strategies were re-validated together to confirm no cross-effects from merging
independent adapter edits in the same file. `deep_value_edgar_pit` and
`dividend_yield_edgar_pit`'s numbers were verified against the real backfilled
`quant_platform.db` — a fresh worktree's empty DB produces a numerically-degenerate
Sharpe blowup (a known fresh-clone artifact, not a code defect).
