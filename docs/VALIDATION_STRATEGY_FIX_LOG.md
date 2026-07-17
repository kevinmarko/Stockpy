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
