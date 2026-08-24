# Feature: ETF Volatility Transmission

**File:** `risk/etf_transmission.py` (pure math, zero I/O)
**Wiring:** `pipeline/production_steps.py::_apply_etf_transmission` (measurement columns) + `_apply_etf_transmission_multiplier` (per-name sizing derate) + `_build_etf_transmission_cov_matrix` (portfolio-level covariance) — all called from `StrategyEvalStep.run`
**Holdings source:** `data/etf_holdings.py::get_etf_holdings` (consumed by SHAPE only, never by provider behavior)
**Columns:** `ETF_Ownership_Pct` (`percent`), `ETF_Comovement_R2` (`number`), `ETF_Primary_Wrapper` (`string`), `ETF_Transmission_Multiplier` (`number`) — all in `config.COLUMN_SCHEMA`
**Master switches:** `settings.ETF_TRANSMISSION_ENABLED` (measurement), `settings.ETF_TRANSMISSION_SIZING_ENABLED` (per-name derate), `settings.ETF_TRANSMISSION_PORTFOLIO_ENABLED` (portfolio covariance) — each independently `False` by default

**This is NOT a registered `SignalModule`.** It contributes nothing to
`final_score` / `SIGNAL_WEIGHTS` / `meta_label_composite` — it is a risk
overlay on the SIZING path, not a scoring input. Three layers, each behind
its own flag:

1. **Measurement** (`ETF_Ownership_Pct` / `ETF_Comovement_R2` /
   `ETF_Primary_Wrapper`) — diagnostic columns, read by nothing else directly.
2. **Per-name sizing derate** (`ETF_Transmission_Multiplier`,
   `risk.etf_transmission.transmission_multiplier`) — a bounded post-multiplier
   composed into `sizing.position_sizer.size_position()`, derating a heavily
   ETF-wrapped name's own weight.
3. **Portfolio-level covariance** (`risk.etf_transmission.build_transmission_adjusted_cov`,
   documented in its own section below) — the mechanism's actual claim is
   about CO-MOVEMENT between co-held names, which a per-name derate cannot
   see no matter how it's composed; this layer feeds an ETF-co-ownership-
   inflated covariance matrix into `sizing.position_sizer.apply_portfolio_gross_cap`'s
   existing risk-aware `cov_matrix` path.

This file lives under `docs/signals/` because it reads naturally alongside
the other per-feature docs, not because it is one of the 17 scored modules.

---

## Rationale

**Ben-David, Franzoni & Moussawi (2018)**, *"Do ETFs Increase Volatility?"*,
**Journal of Finance 73(6), 2471–2535**.

Authorized participants close the ETF-vs-index price gap by creating and
redeeming whole baskets. That is the point of the arbitrage — but it has a
side effect. A shock that hits ONE constituent propagates into the ETF price,
and the arbitrage trade then pushes that same shock back out into every OTHER
constituent of the basket, including names with no fundamental exposure to the
original event. Empirically the paper finds that ETF ownership raises a stock's
daily volatility and its co-movement with its basket peers, and that the effect
is non-fundamental (it mean-reverts) rather than information-driven.

The portfolio consequence: a heavily ETF-wrapped name carries extra
**non-fundamental, non-diversifiable** variance. Two names that look
independent on fundamentals can be tethered by the same wrapper. A risk model
that treats them as independent understates portfolio variance.

## Why market-residualized R², not naive R²

**This is the crux of the design.**

A naive R² of a stock's returns on its sector-ETF composite is high for
*every* large-cap, regardless of how ETF-wrapped it actually is, because both
legs load on the same market/industry factor. Shipping that number as an
"ETF transmission" measure would be a **market-beta derate wearing an ETF
costume** — it would derate exactly the high-beta names, for a reason that has
nothing to do with ETFs.

Both legs are therefore residualized against the market first:

```
β_i = Cov(r_i, r_mkt) / Var(r_mkt)      u_t = r_i,t − β_i · r_mkt,t
β_E = Cov(r_E, r_mkt) / Var(r_mkt)      e_t = r_E,t − β_E · r_mkt,t

ETF_Comovement_R2 = corr(u, e)²
```

`r_E` is the ownership-weighted composite over **non-market wrappers only**.
`settings.ETF_HOLDINGS_MARKET_PROXY` (default `SPY`) is **excluded** from the
composite and used solely as the market leg.

A deliberate, load-bearing consequence: **if a name's only covered wrapper IS
the market proxy, then `e_t ≡ 0` and the partial R² is `NaN`** — not a
fabricated number, not a zero. The identification limit surfaces as missing
data (CONSTRAINT #4). `tests/test_etf_transmission.py::TestMarketResidualization`
pins both halves of this: a synthetic stock/ETF pair sharing only a market beta
reads naive R² > 0.7 but residualized R² < 0.1, while a pair sharing a genuine
non-market shock still reads > 0.9.

## What is deliberately NOT implemented

The paper's own most direct statement of the mechanism is the **arbitrage-gap
regression**: the mispricing `Δ_t = p_t − ι_t` between the ETF's traded price
and its indicative intraday value, with a constituent's next-day return
regressed on lagged `Δ_t`.

That is **not implemented here, on purpose.** Reconstructing the synthetic
basket requires price history for the FULL constituent set (SPY alone is ~500
names) while this pipeline's `tech_raw` only carries the operator universe
(~20–60 names), and free daily NAV/IIV history is unavailable. A basket proxy
built from 5% coverage would be fabricated data dressed as a measurement.

**Phase-2 follow-up, gated on a real NAV/IIV source** — not on more code.

## API shape

`risk/etf_transmission.py` performs **zero I/O**: the caller
(`_apply_etf_transmission`) owns every network call and every settings read.
That is what keeps it unit-testable without `main_orchestrator`'s heavy import
chain, and what lets the honesty contract below be verified in isolation.

```python
def compute_etf_ownership(
    holdings_by_etf: dict[str, list[ETFHolding]],
    shares_outstanding: dict[str, float], *,
    exclude_symbols: frozenset[str] = frozenset(),
) -> dict[str, float]                   # NaN (never 0.0) when shares_out missing/<=0

def build_etf_return_composite(
    holdings_by_etf: dict[str, list[ETFHolding]],
    etf_bars: dict[str, pd.DataFrame], *,
    market_proxy: str = "SPY",
) -> dict[str, pd.Series]               # ownership-weighted; market proxy EXCLUDED

def compute_market_residual_r2(
    stock_bars: pd.DataFrame, composite_returns: pd.Series, market_bars: pd.DataFrame, *,
    window: int = 60, min_obs: int = 60,
) -> float                              # partial R^2 in [0,1]; NaN if <min_obs or degenerate

# Supporting helpers (not part of the frozen three):
def filter_holdings_as_of(holdings_by_etf, as_of=None) -> dict[str, list[ETFHolding]]
def primary_wrapper(holdings_by_etf) -> dict[str, str]
```

`ETFHolding` is `data/etf_holdings.py`'s frozen dataclass
(`etf_symbol` / `holding_symbol` / `weight` / `shares_held` / `as_of_date` /
`source`). This module consumes it **duck-typed** and never imports it, so the
two modules can land independently.

### Composite weighting basis

**2026-08 quant-integrity fix — majority coverage wins, not all-or-nothing.**
The basis selection used to require EVERY contributing wrapper to report a
usable `shares_held` before that basis could be used at all (else require
EVERY wrapper to report a usable NAV `weight`, else drop the constituent
entirely) — one wrapper with an unreported `shares_held` vetoed the whole
shares-held basis for every OTHER wrapper that DID report one, forcing an
unnecessary fallback to the weaker NAV-weight proxy (or an unnecessary drop
of the constituent altogether). This was the same all-or-nothing bug class as
the forecast-skill-weighting fix documented in
`docs/known_issues/graduated_degrade_all_or_nothing_blends.md` and CLAUDE.md's
"Graduated-degrade convention for N-way blends" bullet.

Per constituent, each basis is now filtered **independently** to its own
usable (finite, positive) survivor entries, and whichever basis has MORE
survivors wins — computed over those survivors only, with an unusable entry
in the losing basis simply dropped rather than granted veto power over the
whole constituent:

1. one contributing wrapper → weight is trivially 1.0 (unchanged);
2. 2+ contributing wrappers → filter `shares_held` to its finite-positive
   survivors and `weight` to its own finite-positive survivors independently;
   whichever list is LONGER is used, weighted by its own survivor values only.
   A **tie breaks to `shares_held`** (true relative ownership) over NAV
   `weight` (a **disclosed proxy**: it mixes by how important the name is to
   each basket rather than by how much of the name each basket owns — only
   ever a relative mixing weight between wrappers, never reported as or
   converted into an ownership quantity, that is `compute_etf_ownership`'s
   job, which has no such fallback);
3. else (NEITHER basis has any usable entry at all) → no composite, so the
   constituent reads `NaN`.

### `ETF_Ownership_Pct` and shares outstanding

`ownership_i = Σ_E shares_held(E, i) / shares_outstanding(i)`, emitted as a
**fraction** (`0.07` = 7%), matching the `dividendYield` fraction convention in
`data/market_data.py`. Unlike the composite, this **includes** the market proxy
— being wrapped by the largest basket in the market is precisely the exposure
the column measures.

The repo does not carry shares outstanding, so the caller derives it as
`shares_out ≈ Market Cap / Price`, **guarded on `Market Cap > 0 and Price > 0`,
else NaN**. The guard is load-bearing, not defensive style:
`FundamentalDataDTO.market_cap` defaults to a fabricated `0.0`
(`dto_models.py:135`), so a naive divide yields `inf` on exactly the names
whose fundamentals failed.

**Documented follow-up (deliberately not built here):**
`dei:EntityCommonStockSharesOutstanding` is already parsed by
`data/edgar_fundamentals.py::extract_shares` and is PIT-dated — a strictly
better source than a market-cap quotient, and a self-contained future PR.

### `ETF_Primary_Wrapper`

Largest-weight covered ETF for the name. Required for operator explainability:
without it, *"why is AAPL derated?"* is unanswerable from the dashboard alone,
and `sizing/position_sizer.py` names exactly that question as a design goal.
Ranks by NAV `weight` when any contributing basket reports one, otherwise by
`shares_held`; ties break on ETF symbol for determinism. The market proxy CAN
be the primary wrapper — and if it is the name's only wrapper, the R² beside it
will read `NaN`, which is the identification limit showing honestly rather than
as a silent zero.

## Settings

| Setting | Default | Effect |
|---|---|---|
| `ETF_TRANSMISSION_ENABLED` | `False` | Master gate. `False` is a **complete no-op** — no holdings fetch, no ETF bars fetch, zero network calls, and all three columns stay `NaN` for every row. |
| `ETF_HOLDINGS_MARKET_PROXY` | `"SPY"` | The MARKET leg of the residualization. Excluded from the return composite (see above). |
| `ETF_TRANSMISSION_WRAPPERS` | 15 wrappers (SPY/QQQ/IWM/DIA + the 11 sector SPDRs) | Candidate baskets fetched each cycle. JSON array in `.env`. Coverage is explicitly partial — a name held only by wrappers outside this list reads `NaN`, never a fabricated low ownership. |
| `ETF_TRANSMISSION_EXCLUDED_SYMBOLS` | `[]` | Extra universe symbols that are THEMSELVES funds (e.g. `VOO`, `VTI`, `ARKK`). Everything in `ETF_TRANSMISSION_WRAPPERS` plus `ETF_HOLDINGS_MARKET_PROXY` is excluded automatically. |
| `ETF_TRANSMISSION_WINDOW_DAYS` | `60` | Rolling window (trading days) for the residualized R². Mirrors `processing_engine.calculate_rolling_beta`'s default. |
| `ETF_TRANSMISSION_MIN_OBS` | `60` | Minimum aligned overlapping return observations before an R² is reported at all. See **Composition drift** below. |

Not added to `gui/env_io.py`'s `ALLOWED_KEYS` — GUI-writability is a separate,
optional PR.

## Causality (no lookahead)

Two independent leakage surfaces, both covered by
`tests/test_etf_transmission_lookahead.py`:

**1. Price/return dimension.** `compute_market_residual_r2` follows
`processing_engine.calculate_rolling_beta` (`processing_engine.py:623-679`)
exactly: contemporaneous `.rolling(window)` statistics over a `join="inner"`
alignment, **never** forward-filled. The value at date *t* consumes only rows in
`[t−window+1, t]`, so it is lookahead-free by construction. Pinned by three
perturbation tests using `verify_no_lookahead` / `make_synthetic_ohlcv` from
`tests/lookahead_check.py` — one per leg (stock, ETF composite, market proxy),
because the market leg drives BOTH residualizations and is its own distinct
surface. Each perturbation test first asserts the value at the cutoff is not
`NaN`, so "unchanged" can never pass vacuously as `NaN == NaN`.

**2. Holdings-composition dimension.** ETF baskets carry an `as_of_date`. A row
stamped after the cycle's as-of date must never influence that cycle. The
provider is passed `as_of`, and `filter_holdings_as_of` re-applies the cutoff
client-side as belt-and-suspenders (it also collapses duplicate/multi-snapshot
rows per `(etf, symbol)` so ownership can never be double-counted). Verified
end-to-end: injecting a future-dated basket row that would otherwise move
ownership `0.15 → 1.05`, R² `0.79 → 0.01`, and the wrapper label `XLK → XLF`
leaves all three outputs bit-identical.

**Composition drift.** A name added to a wrapper last week has no tethered
history, so a 60-day R² computed over a partial window would **understate**
transmission with a confident-looking number. The chosen behavior is
**NaN-until-full-window-coverage**: fewer than `max(window, min_obs)` aligned
observations → `NaN`. Missing beats understated.

**PIT feature store.** `ml/feature_engineering.FEATURE_COLUMNS` is an explicit
allowlist (`build_pit_feature_matrix` does `[FEATURE_COLUMNS]` at line 189), so
these columns cannot leak into the PIT feature store by default. That is pinned
by a regression test — holdings are published quarterly and are stale by
construction relative to a daily feature row, so adding any of the three to the
model feature set would be a contaminating feature, not a free signal.

## Failure modes (dead-letter, never fabricated)

Per CONSTRAINT #4/#6, every layer degrades to `NaN` rather than raising or
fabricating. `_apply_etf_transmission` NaN-fills all three columns **first**,
before any branch, so every early-return path stays honest.

| Condition | Result |
|---|---|
| `ETF_TRANSMISSION_ENABLED=False` | All three columns `NaN` for every row. **Zero network calls** — the gate returns before any import. |
| Ticker is in no covered ETF | All three `NaN` (`.map()` on an absent key). Other tickers unaffected. |
| Holdings fetch raised / provider module absent | Whole columns reset to `NaN`, `logger.warning`, pipeline continues. |
| Holdings fetch returned `{}` / no rows for any universe name | All three `NaN`, one INFO line, no bars fetch attempted. |
| Market proxy absent from `tech_raw` | All three `NaN`, one INFO line — and the holdings fetch is **skipped entirely** rather than paid for and discarded. |
| `Market Cap` is the fabricated `0.0` (or `Price` ≤ 0, or either column missing) | `ETF_Ownership_Pct` `NaN` — never `inf`, never `0.0`. `ETF_Comovement_R2` / `ETF_Primary_Wrapper` are independent of fundamentals and still compute. |
| A covered basket reports no `shares_held` for the name | `ETF_Ownership_Pct` `NaN` — the SUM is *unknowable*, not smaller. Silently dropping the row would systematically **understate** ownership, which is an active false claim rather than a gap. (A reported `0.0` is a measured zero and IS summed.) |
| Fewer than `ETF_TRANSMISSION_MIN_OBS` overlapping bars | `ETF_Comovement_R2` `NaN`. Ownership and wrapper, which need no price history, still compute. |
| Composite is market-proxy-only (`e_t ≡ 0`) | `ETF_Comovement_R2` `NaN`. Ownership and wrapper still compute and are still honest. |
| Zero market variance over the window | `ETF_Comovement_R2` `NaN` — no market leg to residualize against. |
| **Ticker is itself an ETF** | Excluded outright from all three columns. `XLK`'s ownership/R² against its own basket is 1.0/1.0 — maximum derate for a trivially wrong reason. The exclusion set is `ETF_TRANSMISSION_WRAPPERS` ∪ `{ETF_HOLDINGS_MARKET_PROXY}` ∪ `ETF_TRANSMISSION_EXCLUDED_SYMBOLS`. |
| Any other exception anywhere in `_apply_etf_transmission` | Caught, `logger.warning`, whole columns reset to `NaN`. Never partially populated, never propagated. |

**Logging discipline.** The per-cycle fallback tally is logged **once, at INFO,
with counts** — never once per name. Forty warnings a cycle is how a real
signal gets ignored. Pinned by a test asserting exactly one log record and that
it names no individual symbol.

## Data-fetch discipline

ETF price bars go through the existing
`DataEngine.fetch_technical_raw_cached` path (HistoricalStore-backed,
incremental), and only for wrappers not already present in `tech_raw`. A second
batched `yf.download` is deliberately **not** added —
`research_engine.fetch_returns_for_clustering:414` is the only one in the repo,
on purpose.

The call sits **after** `_apply_sector_heat_factor` in `StrategyEvalStep.run`,
not before `global_registry.run_pre_compute()`: nothing in `pre_compute`
consumes these columns, so moving a networked call earlier in the critical path
buys nothing.

## Per-name sizing derate (`risk.etf_transmission.transmission_multiplier`)

Behind `settings.ETF_TRANSMISSION_SIZING_ENABLED` (default `False`), a
bounded, monotone post-multiplier derates a heavily ETF-wrapped name's own
sizing weight:

```
m = 1 − max_derate · clip(ownership_pct / ownership_reference, 0, 1)
                   · clip(comovement_r2, 0, 1)
m = max(m, floor)
```

Two knobs (`ETF_TRANSMISSION_MAX_DERATE` default `0.30`,
`ETF_TRANSMISSION_OWNERSHIP_REFERENCE` default `0.20`), a hard lower bound
(`ETF_TRANSMISSION_MIN_MULTIPLIER` default `0.50`), no fitted parameters.
Composed in `sizing.position_sizer.size_position()` step 3, alongside
`regime_multiplier`, and — following that field's precedent exactly —
**excluded from `SizingDecision.was_capped` / `.binding_constraint`**: a
continuous, signal-driven derate is not a hard-ceiling event, and folding it
in would fire the guardrail on every ETF-heavy name and drown out genuine
ceiling events in `sizing/cap_audit_store.py`'s audit log.

**Why a post-multiplier, not vol-inflation into Kelly.** Inflating the
`realized_vol` input to `_calculate_kelly_sizing` looks like the natural
lever and is a broken one: `sizing.kelly.kelly_sizing_for_strategy` reads
`realized_vol` ONLY in its `< MIN_TRADES_REQUIRED` (30) cold-start branch —
at ≥30 closed trades the weight comes from a 1,000-resample bootstrap of
realized trade returns and the vol input is never read. A risk control that
fires on a cold-start book and silently vanishes once the book matures is
the worst possible failure profile.

**Exactly `1.0` on any missing input — never `NaN`.** This is a risk-limit
invariant, not a style choice: `clamp_with_binding` passes NaN straight
through, so a NaN multiplier would make `final_weight` non-finite, and
`apply_portfolio_gross_cap` **excludes non-finite weights from the gross
sum** — a coverage gap on 30 of 40 names would shrink the gross denominator
and silently *loosen* the portfolio cap for the remaining 10. A data outage
must never relax a risk limit. This does not conflict with CONSTRAINT #4:
the measured columns above stay honestly `NaN`; the applied *multiplier* is
an amount of derating, and "derate nothing" is `1.0`.

Wired by `pipeline/production_steps.py::_apply_etf_transmission_multiplier`,
called immediately after `_apply_etf_transmission` (must run after — it
reads `ETF_Ownership_Pct` / `ETF_Comovement_R2`) and before the per-ticker
`evaluate_security` loop. The advisory path (`engine/advisory.py`) is
untouched — it keeps its own tighter, decoupled `CONFIG["max_single_position_pct"]`
cap and is not routed through `size_position()`.

## Portfolio-level covariance (`risk.etf_transmission.build_transmission_adjusted_cov`)

The mechanism this whole feature is named for raises **covariance between
co-held names** — a portfolio-level effect the per-name derate above cannot
see no matter how it's composed. Behind `settings.ETF_TRANSMISSION_PORTFOLIO_ENABLED`
(default `False`), this layer feeds an ETF-co-ownership-inflated covariance
matrix into `sizing.position_sizer.apply_portfolio_gross_cap`'s **existing**
risk-aware `cov_matrix`/`target_vol` path (`sizing.vol_target.portfolio_vol_target`)
— reusing that path rather than building a second portfolio-cap mechanism.
That path was previously unreachable from production; a sibling PR fixed a
latent uplift bug in it first (see `sizing/position_sizer.py`'s "Reduction-only
guarantee" section), which this feature depends on.

```
cov_adj[i,j] = cov[i,j] · (1 + ETF_TRANSMISSION_COV_INFLATION · overlap[i,j])   for i != j
cov_adj[i,i] = cov[i,i]                                                          (untouched)
```

`overlap[i,j]` is the cosine similarity, in `[0, 1]`, of symbols *i* and
*j*'s ETF-basket weight vectors (`risk.etf_transmission._pairwise_etf_overlap`)
— `1.0` for two names held by exactly the same wrappers in the same
proportions, `0.0` for names sharing no wrapper at all. **Only the
off-diagonal is inflated.** Each name's own variance is a different question
that `transmission_multiplier` above already answers; conflating the two
would double-count the same measurement at two layers for no added
information.

**PSD repair is mandatory, not defensive.** Multiplicatively inflating
off-diagonal entries is not guaranteed to preserve positive
semi-definiteness (Schur's product theorem needs the *inflation matrix*
itself to be PSD, which a matrix of raw cosine-similarity values is not
guaranteed to be). `sizing.vol_target.portfolio_vol_target` has no PSD
check of its own — handed a non-PSD matrix, it would compute a nonsensical
(possibly negative) `w' Σ w`, and its own degenerate-`portfolio_vol` branch
would *saturate the leverage scalar at `max_leverage`*, levering the whole
book up on a broken risk estimate. `risk.etf_transmission._nearest_psd`
eigenvalue-clips every eigenvalue up to a floor and reconstructs — the
standard nearest-PSD-by-clipping repair (Higham (2002)'s
alternating-projections algorithm finds the nearest correlation matrix more
exactly; that precision buys nothing here since the input is already close
to PSD by construction — only the off-diagonal was perturbed).

The floor is **relative to the matrix's own eigenvalue scale**
(`max(epsilon, _RELATIVE_PSD_FLOOR * max_eigenvalue)`, `_RELATIVE_PSD_FLOOR
= 1e-6`), not a fixed absolute value. A fixed absolute floor (the original
`1e-10` default, with no relative term) is disconnected from the matrix's
own scale: measured on a realistic 40-symbol book at high inflation (enough
to flip 30 of 40 eigenvalues negative) on an annualized scale, a fixed
`1e-10` floor left a condition number of ~1e8 — a ~30,000x degradation
versus the unadjusted matrix's ~1e3 — severe enough to trigger spurious
BLAS-level `RuntimeWarning`s in the downstream `w' Σ w` computation, even
though the final numeric result stayed technically finite. The relative
floor keeps conditioning proportionate to the matrix's own magnitude
(daily- vs. annualized-scale, high- vs. low-vol universe) instead of
degrading by orders of magnitude whenever inflation is large — pinned at
~1e6 on that same 40-symbol book after the fix. See
`tests/test_etf_transmission_sensitivity_sweep.py`, which surfaced this.

**Never a partially-covered matrix.** `portfolio_vol_target` explicitly
**zeroes out** any symbol missing from `cov_matrix` — its own documented,
correct behavior for an unknowable-risk name, but a far harsher outcome for
a coverage gap than the sum-of-|weight| fallback this feature is opt-in to
replace. `pipeline/production_steps.py::_build_etf_transmission_cov_matrix`
therefore insists on **full coverage** across the cycle's universe (every
requested symbol has ≥ `ETF_TRANSMISSION_COV_WINDOW_DAYS` aligned return
observations) before returning anything other than `None`; any coverage gap
degrades the whole cycle back to `cov_matrix=None` — today's exact
sum-of-|weight| fallback — rather than a partial matrix that would silently
zero out the gapped names' entire positions.

`target_vol` reuses the existing `settings.VOL_TARGET` (the same setting the
per-name vol-target sizing fallback already uses) rather than introducing a
second, redundant target-vol setting.

**Annualization — a real bug this file's own sensitivity sweep caught.**
`build_transmission_adjusted_cov` computes a covariance matrix on whatever
scale its input returns are (its own docstring: "daily simple-return
DataFrame"), with no annualization claim of its own. `_build_etf_
transmission_cov_matrix` builds those returns from DAILY `Close` bars
(`price_df.pct_change()`), but `VOL_TARGET` (and every other `target_vol`
caller in this codebase, e.g. `sizing.vol_target.volatility_target_weight`'s
own docstring: "Annualized ... volatility") is an ANNUALIZED figure. Feeding
a daily-scale covariance matrix into an annualized-target comparison is a
silent units mismatch: daily portfolio vol is essentially always far below
a ~10% annualized target, so `portfolio_vol_target`'s scalar saturates at
its ceiling regardless of the actual covariance structure —
`ETF_TRANSMISSION_COV_INFLATION` would have been a complete no-op in
production. The very first run of the sensitivity sweep below (before this
fix) demonstrated exactly that: `final_gross` bit-for-bit identical across
every `COV_INFLATION` value tested. Fixed by annualizing the covariance in
`_build_etf_transmission_cov_matrix` (`* 252`, matching
`processing_engine.py`'s own `daily_std * sqrt(252)` convention for
`Realized_Vol_60D` — variance/covariance annualizes by `*252`, not
`*sqrt(252)`).

### Sensitivity sweep (`tests/test_etf_transmission_sensitivity_sweep.py`)

The 2-D deterministic sweep this module's design calls for: a synthetic
40-name book (4 groups of 10, each wrapped by its own dedicated ETF, groups
heterogeneously tethered — ownership 0.25/0.15/0.10/0.05, comovement
0.90/0.70/0.50/0.20) gridded over `ETF_TRANSMISSION_MAX_DERATE ×
ETF_TRANSMISSION_COV_INFLATION` at `{0.0, 0.15, 0.30, 0.50} × {0.0, 0.25,
0.50, 1.00}`, holding every other knob at its shipped default. Baseline
per-name weight is a uniform 0.10 (40 × 0.10 = 4.0 gross), deliberately
above `MAX_PORTFOLIO_GROSS`'s shipped default of 2.0 so the portfolio cap
binds even with both features off.

| max_derate ↓ / cov_inflation → | 0.00 | 0.25 | 0.50 | 1.00 |
|---|---|---|---|---|
| **0.00** | 0.4865 | 0.4698 | 0.4548 | 0.4285 |
| **0.15** | 0.4866 | 0.4700 | 0.4549 | 0.4287 |
| **0.30** | 0.4866 | 0.4699 | 0.4548 | 0.4284 |
| **0.50** | 0.4860 | 0.4690 | 0.4537 | 0.4270 |

(final gross exposure per cell, post-fix; `max_single_name_weight` and
`effective_n` — inverse Herfindahl on gross-normalized weights — are
reported alongside in the test's log output)

**The double-count is real and measured, not hypothetical.** At the grid's
extremes (`max_derate=0.50, cov_inflation=1.00`), the actual joint final
gross (0.4270) is below what a naive INDEPENDENT combination of the two
knobs' solo effects would predict (`baseline × (1 − reduction_derate_alone)
× (1 − reduction_cov_alone)` ≈ 0.4281) — a double-count gap of ≈0.2% of
baseline gross. Small in this book, but directionally confirmed and
reproducible; `tests/test_etf_transmission_sensitivity_sweep.py::
TestJointWorstCaseDoubleCount` pins it as a positive, non-negative gap
rather than an exact value (the exact magnitude is sensitive to the
synthetic book's RNG seed; the direction is the invariant).

**A second, more subtle finding — not a bug.** `COV_INFLATION` is strictly
monotonic (raising it, for a fixed weight vector, can only raise `w' Σ w`,
which can only lower the vol-target scalar). `MAX_DERATE` is **not**
always strictly monotonic in `final_gross`: shrinking a subset of weights
lowers realized portfolio vol, and the REACTIVE vol-target scalar can
respond by allowing slightly more leverage elsewhere, partially offsetting
the direct weight reduction (visible above: `cov_inflation=0.00`, gross
ticks up very slightly from 0.4865 at `max_derate=0.00` to 0.4866 at
`max_derate=0.15/0.30`, a ≈0.03% reversal). This is vol-targeting doing
exactly what it's designed to do, and does **not** violate
`apply_portfolio_gross_cap`'s "Reduction-only guarantee" (that guarantee
bounds a single call's scalar at ≤ 1.0 — still true in every cell here —
not cross-call monotonicity as the input weight vector changes, which was
never a promised invariant of either function). The sweep asserts this
reversal stays small (< 2% relative) rather than asserting it can never
happen.

### Settings (portfolio covariance)

| Setting | Default | Effect |
|---|---|---|
| `ETF_TRANSMISSION_PORTFOLIO_ENABLED` | `False` | Master gate. `False` is a complete no-op: `cov_matrix=None` every cycle, byte-identical to the pre-feature `apply_portfolio_gross_cap` call. |
| `ETF_TRANSMISSION_COV_INFLATION` | `0.25` | Fractional off-diagonal inflation at maximum (`overlap == 1.0`) co-ownership. |
| `ETF_TRANSMISSION_COV_WINDOW_DAYS` | `60` | Trailing trading-day window for the base covariance estimate. Fewer aligned observations than this across the universe → falls back to `None` rather than a short, noisy estimate. |

### Failure modes (portfolio covariance) — degrades to `cov_matrix=None`, never a partial matrix

| Condition | Result |
|---|---|
| `ETF_TRANSMISSION_PORTFOLIO_ENABLED=False` | `None`. Zero network calls — the gate returns before any import. |
| No configured wrapper ETFs | `None`. |
| Holdings fetch raised / returned no covered basket rows | `None`, one INFO line. |
| Any requested symbol lacks price bars in `tech_raw` | `None` — the whole cycle falls back rather than zeroing just the gapped symbol via a partial matrix. |
| Fewer than `ETF_TRANSMISSION_COV_WINDOW_DAYS` fully-aligned return observations | `None`. |
| Fewer than 2 requested symbols | `None`. |
| Adjusted covariance is indefinite (non-PSD) after inflation | Repaired via eigenvalue clipping, never returned as-is and never `None` solely for this reason. |
| Any other exception anywhere in the build | Caught, `logger.warning`, `None`. Never partially populated, never propagated. |

## Where it's surfaced

- `config.COLUMN_SCHEMA` — all four columns (Google Sheets + Pandera-validated
  `dashboard_df`).
- `main_orchestrator.py::_write_state_snapshot()` — per-signal
  `etf_ownership_pct` / `etf_comovement_r2` / `etf_primary_wrapper` /
  `etf_transmission_multiplier` keys in `output/state_snapshot.json`, via the
  existing `_safe_float_or_none` helper (NaN → JSON `null`, never a
  fabricated `0.0`); the wrapper string uses an explicit `pd.isna` → `None`
  so a missing wrapper never serializes as the literal text `"nan"`. The
  portfolio-covariance layer surfaces nothing of its own here — it produces
  no new column, only an input to an existing sizing computation.
- `tests/test_state_snapshot_parity.py::ORCHESTRATOR_ONLY_FIELDS` — all four
  columns are documented orchestrator-only. The advisory path (`main.py`) has
  no ETF-holdings source at all: `_build_context_extras` builds a minimal
  `universe_df` with no holdings input, and `engine/advisory.py` is not
  routed through `size_position()` or `apply_portfolio_gross_cap()`.
- `gui/panels/settings_manager.py` — all nine settings (`ETF_HOLDINGS_*`,
  `ETF_TRANSMISSION_*`) are GUI-writable via the standard `_SETTINGS_LAYOUT`
  bool/int/number/text/tickers widgets, allowlisted in
  `gui/env_io.py::ALLOWED_KEYS`.
- `gui/panels/observability.py::_render_observability_etf_transmission` — a
  read-only Mission Control sub-section showing each of the three master
  switches' ON/OFF state plus a per-symbol table (`ETF_Ownership_Pct` /
  `ETF_Comovement_R2` / `ETF_Primary_Wrapper` / `ETF_Transmission_Multiplier`)
  sourced from `state_snapshot.json`, sorted so the most heavily-derated
  names surface first. Row extraction/sorting is a pure, Streamlit-free
  helper (`gui.observability_panel_helpers.etf_transmission_rows`,
  unit-tested in `tests/test_observability_panel.py`) — the panel itself
  never writes anything and degrades to an info message (never a table of
  fabricated nulls) when the measurement gate is off or no symbol has
  coverage yet.

## Not wired into

- `signals/` package — **not** a `SignalModule`, no `pre_compute`/`compute`.
- `settings.SIGNAL_WEIGHTS` — **no entry**. It contributes nothing to
  `final_score` / `score_log` / `meta_label_composite`.
- `StrategyEngine.evaluate_security()` — no scoring effect whatsoever (the
  per-name derate is a sizing input, applied after scoring is complete).
- `validation/harness.py` / `STRATEGY_REGISTRY` — no entry applies (verified
  by grep: no adapter calls `size_position()` or `apply_portfolio_gross_cap()`).
  This is a risk overlay, not a strategy: it produces no trade signal, so
  PBO/DSR/Sharpe/MaxDD have nothing to gate.
- `gui/` panels, `gui/env_io.py` `ALLOWED_KEYS`, and the Pilots PWA — all
  explicitly out of scope for this first cut.
