# Feature: ETF Volatility Transmission

**File:** `risk/etf_transmission.py` (pure math, zero I/O)
**Wiring:** `pipeline/production_steps.py::_apply_etf_transmission` (called from `StrategyEvalStep.run`, immediately after `_apply_sector_heat_factor`)
**Holdings source:** `data/etf_holdings.py::get_etf_holdings` (separate PR — consumed by SHAPE only, never by provider behavior)
**Columns:** `ETF_Ownership_Pct` (`percent`), `ETF_Comovement_R2` (`number`), `ETF_Primary_Wrapper` (`string`) — all in `config.COLUMN_SCHEMA`
**Master switch:** `settings.ETF_TRANSMISSION_ENABLED` (default `False`)

**This is NOT a registered `SignalModule`, and it is DIAGNOSTIC ONLY.** As of
this commit these three columns are measured and surfaced, and read by
*nothing* — not scoring, not sizing, not execution. A sibling PR wires them
into position sizing. This file lives under `docs/signals/` because it reads
naturally alongside the other per-feature docs, not because it is one of the
17 scored modules.

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

Per constituent, exactly **one** basis is used — never mixed:

1. one contributing wrapper → weight is trivially 1.0;
2. every contributing wrapper reports a finite positive `shares_held` →
   weight by `shares_held` (true relative ownership);
3. else, every contributing wrapper reports a finite positive `weight` →
   weight by NAV `weight`. This is a **disclosed proxy**: it mixes by how
   important the name is to each basket rather than by how much of the name
   each basket owns. It is only ever a relative mixing weight between wrappers
   — it is never reported as, or converted into, an ownership quantity (that
   is `compute_etf_ownership`'s job, which has no such fallback);
4. else → no composite, so the constituent reads `NaN`.

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

## Where it's surfaced

- `config.COLUMN_SCHEMA` — the three columns (Google Sheets + Pandera-validated
  `dashboard_df`).
- `main_orchestrator.py::_write_state_snapshot()` — per-signal
  `etf_ownership_pct` / `etf_comovement_r2` / `etf_primary_wrapper` keys in
  `output/state_snapshot.json`. The two floats use the existing
  `_safe_float_or_none` helper (NaN → JSON `null`, never a fabricated `0.0`);
  the string uses an explicit `pd.isna` → `None` so a missing wrapper never
  serializes as the literal text `"nan"`.
- `tests/test_state_snapshot_parity.py::ORCHESTRATOR_ONLY_FIELDS` — all three
  are documented orchestrator-only. The advisory path (`main.py`) has no
  ETF-holdings source at all: `_build_context_extras` builds a minimal
  `universe_df` with no holdings input.

## Not wired into

- `signals/` package — **not** a `SignalModule`, no `pre_compute`/`compute`.
- `settings.SIGNAL_WEIGHTS` — **no entry**. It contributes nothing to
  `final_score` / `score_log` / `meta_label_composite`.
- `StrategyEngine.evaluate_security()` — no scoring effect whatsoever.
- `sizing/position_sizer.py` — a **sibling PR** consumes these columns for
  position sizing. This PR is measurement only.
- `validation/harness.py` / `STRATEGY_REGISTRY` — no entry applies. This is a
  risk overlay, not a strategy: it produces no trade signal, so PBO/DSR/
  Sharpe/MaxDD have nothing to gate.
- `gui/` panels, `gui/env_io.py` `ALLOWED_KEYS`, and the Pilots PWA — all
  explicitly out of scope for this first cut.
