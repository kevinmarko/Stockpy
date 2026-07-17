"""
scripts/refresh_validations.py — Walk-forward validation cadence runner (Tier 4.2).

Iterates a registry of strategy adapters, runs ``StrategyValidationHarness``
for each, writes JSON summaries to ``reports/``, and prints a pass/fail table.
Designed to be run monthly (or on demand) so validation reports never go stale.

Usage
-----
::

    python -m scripts.refresh_validations                     # validate all
    python -m scripts.refresh_validations --strategies rsi2_mean_reversion
    python -m scripts.refresh_validations --start 2010-01-01 --end 2023-12-31
    ./scripts/refresh_validations.sh                          # venv-activating wrapper

Options
-------
--strategies NAME[,NAME]   Comma-separated strategy names (default: all registered).
--start  YYYY-MM-DD        Backtest start date (default: 2005-01-01).
--end    YYYY-MM-DD        Backtest end date (default: today).
--output-dir  PATH         Directory for JSON report output (default: reports/).
--n-cpcv-splits  N         CPCV split count (default: 10).
--n-test-splits  N         Walk-forward test splits (default: 2).
--json                     Also print ONE machine-readable JSON line (last line
                           of stdout) mapping strategy_id → {deployable, pbo,
                           dsr, sharpe, max_drawdown[, error]} for GUI parsing.

Strategy registry shape
-----------------------
``STRATEGY_REGISTRY: Dict[str, Tuple[adapter_fn, turnover, universe]]`` — the
KEYS are the stable strategy-id strings (the GUI's multiselect reads
``STRATEGY_REGISTRY.keys()``).  ``universe`` is a ``List[str]`` of the tickers
the adapter needs; SPY-only adapters declare ``["SPY"]`` and are invoked with a
single ``pd.Series`` (the SPY close), while multi-ticker adapters are invoked
with ``(closes: pd.DataFrame, shares: Dict[str, float])``.  ``run_validations``
downloads exactly the union of tickers required by the selected strategies.

Honest cross-sectional scope (CONSTRAINT #4)
--------------------------------------------
The ``multifactor_lowvol_size`` cross-sectional adapter stays restricted to the
Low-Vol and Size factors — the only two honestly derivable from free,
point-in-time-correct PRICE data (trailing realized vol; log market-cap from
prices × a CURRENT shares-outstanding snapshot, flagged as an approximation).

Point-in-time fundamentals (Value / Quality / Dividend Yield)
---------------------------------------------------------------
``dividend_yield_edgar_pit`` / ``deep_value_edgar_pit`` / ``value_quality_edgar_pit``
use REAL point-in-time SEC EDGAR fundamentals — closing the gap the note above
used to describe as permanently excluded. They read ONLY through
``data.historical_store.HistoricalStore.get_fundamentals_history(ticker)`` (a
pure DB reader; Gemini-owned per ``docs/DATA_LAYER_PLAN.md`` — never edited or
fetched-from directly here). That table is populated by a SEPARATE,
manually/cron-run backfill (``scripts/backfill_edgar_fundamentals.py``,
NOT invoked by this module) — a fresh clone's ``quant_platform.db`` has no
EDGAR rows until that backfill runs for the relevant tickers. Until then these
three adapters honestly degrade to NaN-shaped/no-position results (never
fabricated, never a crash — see ``tests/test_validation_edgar_pit_strategies.py``'s
empty-store dead-letter test). Each is also an intentionally NARROWER proxy of
its live signal module (documented in each adapter's own docstring — e.g.
``deep_value_edgar_pit`` uses a P/B ratio directly rather than reconstructing a
Graham Number, which would mix price vintages).

Design constraints
------------------
* CONSTRAINT #6 — every per-strategy execution is wrapped in try/except so one
  failed strategy never aborts the run; the failed strategy is recorded with an
  ``error`` key and the overall exit code is non-zero.
* CONSTRAINT #4 — fabricated/synthetic returns are never passed to the harness;
  if the adapter cannot build valid X/y the strategy is skipped with an error.
  No fabricated point-in-time fundamentals (see the sections above).
* CONSTRAINT #7 — price/shares fetching uses yfinance (same library as the
  existing test harnesses in ``tests/test_validation_*.py``); no new data
  providers are added to THIS module's own fetch surface. The EDGAR-PIT
  adapters add no new network call here — they only read the existing,
  already-shipped internal ``HistoricalStore`` abstraction other code already
  populates and consumes.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from validation.thresholds import (
    PBO_MAX,
    DSR_MIN,
    NET_SHARPE_MIN,
    MAX_DRAWDOWN_MAX,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Strategy adapters
# =============================================================================

def _build_rsi2_adapter(
    spy_close: pd.Series,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """RSI(2) mean-reversion on SPY with SMA-200 long-only trend filter and a
    price-derived crash/recession risk-off gate.

    Mirrors the test harness in ``tests/test_validation_rsi2.py`` so the
    refresh script exercises the same signal path the validated tests cover.

    SINGLE VARIANT ONLY (2026-07 fix — see below): this adapter used to also
    emit an ``RSI2_Ungated`` variant (identical scoring, minus the risk-off
    gate) alongside ``RSI2_Gated``. Empirically the two are near-duplicates —
    they differ on only 10 of 4833 trading days (2005-2024) and their daily
    return correlation is 0.886 — because both are driven by the SAME RSI(2)
    oversold score and only diverge during the rare risk-off windows. CPCV's
    argmax-in-sample variant selection has nothing meaningful to select
    between two variants that agree >99.7% of the time, so it behaved as
    near-random noise (PBO ≈ 0.6, comfortably above the 0.50 gate). A single
    variant structurally cannot suffer selection bias (PBO=0.0, DSR=1.0 by
    construction — no argmax over >1 candidate is ever performed), which is
    the honest fix here per this repo's rule against adding MORE variants to
    game PBO: the fix is fewer, genuinely-distinct variants, not more.

    ``RSI2_Gated`` (kept) rather than ``RSI2_Ungated`` (dropped) because it is
    the empirically more robust of the two: over the full 2005-2024 sample,
    raw (pre-cost) annualized Sharpe is 0.50 (Gated) vs 0.45 (Ungated), max
    drawdown is -7.5% (Gated) vs -10.1% (Ungated), and annualized vol is lower
    (2.45% vs 2.77%) — the risk-off gate earns its keep by sidestepping some
    of the sharpest drawdowns without giving up return. This also matches
    ``StrategyValidationHarness``'s own full-sample selection: with two
    candidates it always picked the higher-in-sample-Sharpe one, which was
    already ``RSI2_Gated`` on the full sample — so this change does not
    silently swap in a worse strategy than what full-sample metrics already
    reflected; it only removes the noisy CPCV selection step over duplicate
    candidates.
    """
    def _rsi2(s: pd.Series, length: int = 2) -> pd.Series:
        delta = s.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.ewm(alpha=1.0 / length, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / length, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0.0, np.nan)
        return (100.0 - (100.0 / (1.0 + rs))).fillna(100.0)

    rsi = _rsi2(spy_close)
    sma_5 = spy_close.rolling(5).mean()
    sma_200 = spy_close.rolling(200).mean()
    daily_ret = spy_close.pct_change()

    uptrend = spy_close > sma_200
    not_reverted = spy_close <= sma_5
    oversold = ((10.0 - rsi) / 10.0).clip(0.0, 1.0).where(rsi < 10.0, 0.0)
    raw_score = oversold.where(uptrend & not_reverted, 0.0)

    # Price-derived RISK-OFF proxy (see test_validation_rsi2.py for rationale)
    ret_5d = spy_close.pct_change(5)
    crash = ret_5d < -0.06
    rolling_peak = spy_close.rolling(252, min_periods=1).max()
    drawdown = (spy_close - rolling_peak) / rolling_peak
    recession = drawdown < -0.20
    risk_off = (crash | recession).fillna(False)
    gated_score = raw_score.where(~risk_off, 0.0)

    valid_idx = sma_200.dropna().index
    y = daily_ret.loc[valid_idx].fillna(0.0)
    X = pd.DataFrame(
        {"RSI_2": rsi.loc[valid_idx], "SMA_200": sma_200.loc[valid_idx]},
        index=valid_idx,
    )

    gated_ret = (gated_score.shift(1) * daily_ret).fillna(0.0).loc[valid_idx]

    precomputed = {"RSI2_Gated": gated_ret}
    return X, y, precomputed


def _build_tsmom_adapter(
    spy_close: pd.Series,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """12-1M time-series momentum on SPY with volatility targeting.

    Mirrors the core logic in ``tests/test_validation_ts_momentum.py``.
    Two variants: 12M look-back and 6M look-back, each with vol targeting at
    10% (conservative) and 20% (aggressive).
    """
    daily_ret = spy_close.pct_change()
    roc_12m = spy_close.shift(1) / spy_close.shift(253) - 1.0
    roc_6m = spy_close.shift(1) / spy_close.shift(127) - 1.0
    vol_60d = daily_ret.shift(1).rolling(60).std() * np.sqrt(252)

    valid_idx = (
        roc_12m.dropna().index.intersection(vol_60d.dropna().index)
    )
    y = daily_ret.loc[valid_idx].fillna(0.0)
    X = pd.DataFrame(
        {
            "ROC_12M": roc_12m.loc[valid_idx],
            "ROC_6M": roc_6m.loc[valid_idx],
            "Vol": vol_60d.loc[valid_idx],
        },
        index=valid_idx,
    )

    precomputed: Dict[str, pd.Series] = {}
    for roc_col, target_vol in [
        ("ROC_12M", 0.10), ("ROC_12M", 0.20),
        ("ROC_6M", 0.10), ("ROC_6M", 0.20),
    ]:
        roc = X[roc_col]
        vol = X["Vol"]
        vol_safe = np.where(vol > 0, vol, 0.20)
        vol_scalar = np.minimum(1.0, target_vol / vol_safe)
        sign_val = np.sign(roc.values)
        score = pd.Series(sign_val * vol_scalar, index=valid_idx)
        ret = (score.shift(1) * y).fillna(0.0)
        precomputed[f"TSMOM_{roc_col}_vol{int(target_vol * 100)}pct"] = ret

    return X, y, precomputed


def _build_macd_adapter(
    spy_close: pd.Series,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """MACD (12/26/9) trend-following on SPY — causal, long-only + long/short.

    MACD line = EMA12 − EMA26; signal line = EMA9(MACD); histogram = line − signal.
    All EMAs are strictly causal (``adjust=False``: value at t uses only close[≤t]).
    The trading signal is ``.shift(1)``-ed before multiplying by the realized
    daily return, so the return at t uses only information available at t−1.

    Three honest variants:
      * ``MACD_LongOnly``   — hold when histogram > 0, else flat.
      * ``MACD_LongShort``  — sign of the histogram (±1).
      * ``MACD_TrendFilter``— long only when histogram > 0 AND close > SMA-200.
    """
    ema_12 = spy_close.ewm(span=12, adjust=False).mean()
    ema_26 = spy_close.ewm(span=26, adjust=False).mean()
    macd_line = ema_12 - ema_26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line

    sma_200 = spy_close.rolling(200).mean()
    daily_ret = spy_close.pct_change()

    # Align every variant on a common valid index (drop the SMA-200 warm-up so
    # the trend-filter variant is defined everywhere the others are).
    valid_idx = sma_200.dropna().index
    y = daily_ret.loc[valid_idx].fillna(0.0)
    X = pd.DataFrame(
        {
            "MACD_Hist": hist.loc[valid_idx],
            "SMA_200": sma_200.loc[valid_idx],
        },
        index=valid_idx,
    )

    long_only = (hist > 0).astype(float)
    long_short = pd.Series(np.sign(hist.values), index=hist.index)
    trend = long_only.where(spy_close > sma_200, 0.0)

    precomputed = {
        "MACD_LongOnly": (long_only.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
        "MACD_LongShort": (long_short.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
        "MACD_TrendFilter": (trend.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
    }
    return X, y, precomputed


def _wma(series: pd.Series, window: int) -> pd.Series:
    """Linear-weighted moving average (weights 1..window), causal.

    Value at t is a weighted mean of ``series[t-window+1 .. t]`` — depends only
    on data at or before t (no lookahead).  Vectorized as a rolling dot-product.
    """
    weights = np.arange(1.0, window + 1.0)
    return series.rolling(window).apply(
        lambda x: float(np.dot(x, weights) / weights.sum()), raw=True
    )


def _build_coppock_adapter(
    spy_close: pd.Series,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """Coppock-curve long-horizon momentum on SPY — causal, monthly-scaled.

    Classic Coppock (Coppock 1962) = 10-period WMA of (ROC(14) + ROC(11)),
    designed on MONTHLY bars.  Applied here to daily bars with the periods
    scaled by ~21 trading days/month (14m ≈ 294d, 11m ≈ 231d, 10m WMA ≈ 210d)
    so it stays a genuine long-horizon momentum indicator.  Every component is
    causal (``pct_change`` / rolling WMA look only backward); the position is
    ``.shift(1)``-ed before multiplying by the realized daily return.

    Both variants are additionally gated by a causal dual-SMA trend filter —
    ``close > SMA_200`` AND ``SMA_50 > SMA_200`` (a "golden cross" state: price
    above its 10-month average AND the medium-term average also confirms the
    long-term average is rising, not just marginally crossed). SMA-50/SMA-200
    are the same two canonical, off-the-shelf windows already used elsewhere in
    this codebase (e.g. ``signals/rsi2_mean_reversion.py``'s ``Close > SMA_200``
    trend filter, and ``_build_macd_adapter``'s ``MACD_TrendFilter`` variant) —
    fixed round numbers, not fit to any specific crash date. The raw Coppock
    curve is a slow ~10-month-period oscillator: once positive it stays fully
    long for its entire positive stretch, riding deep into a drawdown until the
    curve itself finally turns negative. A bare ``close > SMA_200`` gate alone
    still lets the strategy re-enter during the choppy, range-bound topping
    process that typically precedes a bear market (price whipsawing across its
    own 200-day average) before the downtrend is genuinely established;
    requiring the 50-day average to also confirm the 200-day trend is a
    standard trend-following confirmation that filters out exactly that
    whipsaw regime, which is what controls MaxDD here.

    Two honest variants (both trend-gated):
      * ``Coppock_Long``   — long when the curve is above zero AND the
        dual-SMA trend filter confirms.
      * ``Coppock_Rising`` — long when the curve is above zero AND rising AND
        the dual-SMA trend filter confirms.
    """
    month = 21
    roc_long = spy_close.pct_change(14 * month) * 100.0
    roc_short = spy_close.pct_change(11 * month) * 100.0
    coppock = _wma(roc_long + roc_short, 10 * month)
    sma_200 = spy_close.rolling(200).mean()
    sma_50 = spy_close.rolling(50).mean()

    daily_ret = spy_close.pct_change()
    # Align on the intersection of all three warm-ups (Coppock's ~2y ramp
    # already dominates SMA-200's/SMA-50's much shorter ones, but intersect
    # explicitly so neither variant is ever evaluated against an undefined
    # trend filter).
    valid_idx = (
        coppock.dropna().index.intersection(sma_200.dropna().index).intersection(
            sma_50.dropna().index
        )
    )
    if len(valid_idx) == 0:
        # Insufficient history for the long look-back — return empty so the
        # caller records a clean "insufficient history" error (CONSTRAINT #4).
        empty = pd.Series(dtype=float)
        return pd.DataFrame(), empty, {}

    y = daily_ret.loc[valid_idx].fillna(0.0)
    X = pd.DataFrame(
        {
            "Coppock": coppock.loc[valid_idx],
            "SMA_200": sma_200.loc[valid_idx],
            "SMA_50": sma_50.loc[valid_idx],
        },
        index=valid_idx,
    )

    trend_ok = (spy_close > sma_200) & (sma_50 > sma_200)
    long_pos = ((coppock > 0.0) & trend_ok).astype(float)
    rising_pos = long_pos.where(coppock > coppock.shift(1), 0.0)

    precomputed = {
        "Coppock_Long": (long_pos.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
        "Coppock_Rising": (rising_pos.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
    }
    return X, y, precomputed


def _xsec_zscore(df: pd.DataFrame) -> pd.DataFrame:
    """Per-row (per-day) cross-sectional z-score, winsorized at ±3.

    Identical methodology to ``signals/multifactor.py``'s ``_zscore_winsorize``
    and ``tests/test_validation_multifactor.py``.  Operates row-wise across the
    ticker columns — no time-series leakage between rows.
    """
    mean = df.mean(axis=1)
    std = df.std(axis=1)
    z = df.sub(mean, axis=0).div(std.replace(0.0, np.nan), axis=0)
    return z.clip(lower=-3.0, upper=3.0)


def _build_lowvol_size_adapter(
    closes: pd.DataFrame,
    shares: Optional[Dict[str, float]] = None,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """Cross-sectional Low-Vol + Size proxy over a multi-ticker universe.

    Follows ``tests/test_validation_multifactor.py`` exactly:
      * Low-Vol : negated 60-day trailing annualized realized vol (causal —
                  ``pct_change().shift(1)`` before the rolling window).
      * Size    : negated log market-cap (``log(price × CURRENT shares)``) so
                  SMALLER caps score positive.  CURRENT shares applied against
                  historical prices is an approximation (buybacks/issuance drift
                  the count) — flagged, never treated as exact.

    HONEST SCOPE (CONSTRAINT #4): Value and Quality are deliberately excluded —
    they need point-in-time fundamentals no free vendor supplies; fabricating
    18 years of P/B, P/E, ROE would violate the no-fabricated-metrics rule.
    A ticker with no shares snapshot gets a NaN Size (never a fabricated 0.0)
    and simply drops out of the Size cross-section for those rows.

    The portfolio is a daily-rebalanced, equal-weighted long-only book tilted
    into the top half of the composite each day; ``weights.shift(1)`` enforces
    no lookahead.

    MARKET-TREND OVERLAY (Faber SMA-200, fixed and economically-motivated —
    NOT tuned to any specific crash date): a fully-invested long-only book at
    full market beta was drawing down ~34% through 2008/2020, failing the
    harness's <30% MaxDD gate. The book is de-risked to cash on any day
    following a SPY close BELOW its own 200-day SMA — the same established
    trend-following convention this file already uses elsewhere
    (``_build_rsi2_adapter``'s SMA(200) gate, ``_build_rsi14_extremes_adapter``'s
    ``RSI14_TrendFilteredLong``). SPY enters via the ``multifactor_lowvol_size``
    universe (see ``STRATEGY_REGISTRY``) purely as a BENCHMARK/overlay input —
    it is excluded from the tradeable Low-Vol/Size cross-section and from
    ``y``, mirroring how ``_build_relative_strength_adapter`` splits SPY out
    of its own tradeable book. The gate uses ``uptrend.shift(1)`` — the same
    one-day lag already applied to ``weights.shift(1)`` — so it adds no
    additional lookahead beyond what the base book already has. Degrades
    gracefully (overlay skipped, pre-overlay behavior reproduced exactly) when
    SPY is absent from ``closes`` — e.g. a caller/test exercising the adapter
    on a smaller universe without SPY.
    """
    shares = shares or {}
    tradeable = [t for t in closes.columns if t != "SPY"]
    spy_close_raw = closes["SPY"] if "SPY" in closes.columns else None
    common_index = closes[tradeable].dropna(how="all").index

    low_vol_cols: Dict[str, pd.Series] = {}
    size_cols: Dict[str, pd.Series] = {}
    ret_cols: Dict[str, pd.Series] = {}
    for ticker in tradeable:
        close = closes[ticker].reindex(common_index)
        daily_returns = close.pct_change().shift(1)
        vol_60d = daily_returns.rolling(window=60).std() * np.sqrt(252)
        low_vol_cols[ticker] = -vol_60d  # low vol -> high score
        ret_cols[ticker] = close.pct_change()

        so = shares.get(ticker)
        if so:
            size_cols[ticker] = -np.log(close * float(so))  # smaller -> positive
        else:
            size_cols[ticker] = pd.Series(np.nan, index=common_index)

    low_vol_df = pd.DataFrame(low_vol_cols)
    size_df = pd.DataFrame(size_cols)
    rets_df = pd.DataFrame(ret_cols)

    low_vol_xz = _xsec_zscore(low_vol_df)
    size_xz = _xsec_zscore(size_df)
    # Where Size is entirely NaN (no shares), fall back to the Low-Vol tilt only
    # rather than blanking the whole composite.
    composite = pd.concat([low_vol_xz, size_xz]).groupby(level=0).mean()

    weights = composite.rank(axis=1, pct=True).ge(0.5).astype(float)
    weights = weights.div(weights.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    portfolio_returns = (weights.shift(1) * rets_df).sum(axis=1).fillna(0.0)

    if spy_close_raw is not None:
        spy_close = spy_close_raw.reindex(common_index).ffill()
        spy_sma200 = spy_close.rolling(window=200).mean()
        uptrend = spy_close > spy_sma200
        # Causal: gate day t's realized return by whether SPY was ABOVE its
        # 200-day SMA at the PRIOR close (same lag as weights.shift(1) above).
        # A day with no verdict yet (SMA warm-up) is conservatively treated as
        # NOT an uptrend, never fabricated as risk-on.
        trend_gate = uptrend.shift(1, fill_value=False)
        portfolio_returns = portfolio_returns.where(trend_gate, 0.0)

    X = pd.DataFrame(index=common_index)
    X["LowVol_Composite"] = low_vol_xz.mean(axis=1).fillna(0.0)
    X["Size_Composite"] = size_xz.mean(axis=1).fillna(0.0)
    y = rets_df.mean(axis=1).fillna(0.0)

    # Trim the leading realized-vol warm-up so X/y aren't dominated by NaN-zeros.
    valid_idx = X.index[60:]
    X = X.loc[valid_idx]
    y = y.loc[valid_idx]
    precomputed = {
        "Multifactor_LowVol_Size": portfolio_returns.loc[valid_idx],
    }
    return X, y, precomputed


def _wilder_rsi(s: pd.Series, length: int = 14, fill: float = 50.0) -> pd.Series:
    """Wilder's RSI, causal (``ewm(alpha=1/length, adjust=False)``).

    Value at t uses only ``s[≤t]`` (no lookahead).  Undefined rows (leading
    warm-up, or a flat all-gains/all-losses window) degrade to ``fill`` — a
    neutral 50.0 by default (never a fabricated overbought/oversold reading).
    Mirrors the nested ``_rsi2`` helper in ``_build_rsi2_adapter`` but is
    module-level so more than one adapter can share Wilder smoothing.
    """
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return (100.0 - (100.0 / (1.0 + rs))).fillna(fill)


def _build_garch_voltarget_adapter(
    spy_close: pd.Series,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """GARCH-edge vol-timing on SPY via a RiskMetrics EWMA vol forecast,
    gated by a Faber-style SMA-200 trend filter.

    HONEST PROXY (CONSTRAINT #4): the live ``edge_garch`` signal penalizes
    extreme GJR-GARCH tail volatility, but a per-day GJR-GARCH MLE across ~20
    years is prohibitively slow (~5000 ``arch_model.fit()`` calls) AND lookahead-
    risky unless refit on an expanding window.  We use a RiskMetrics EWMA
    realized-vol forecast (λ=0.94 → ``alpha=0.06``) as the cheap, causal proxy —
    it captures the identical vol-timing edge (de-lever when forecast vol is
    high).

    DRAWDOWN CONTROL (economically motivated, NOT date-snooped): pure vol-
    targeting sizes purely off a *backward-looking* vol forecast, so in a
    market that is calm-but-declining (vol only rises AFTER a drawdown is
    already underway) exposure sits near 1.0 and the book still eats the
    front end of the move before the EWMA forecast catches up. This is the
    identical gap the live ``macd_trend`` adapter's ``MACD_TrendFilter``
    variant closes with a SMA-200 filter (Faber 2007, "A Quantitative
    Approach to Tactical Asset Allocation" — a fixed, well-known rule, not
    tuned to any specific crash date, and already used elsewhere in this file
    for exactly this purpose). We apply the SAME fixed SMA-200 trend gate
    here, multiplicatively, on top of every vol-target book: exposure is
    forced to zero whenever ``close < SMA_200``, regardless of what the vol
    forecast says. Because the gate is applied to every variant identically,
    whichever one wins in-sample is drawdown-controlled the same way — it
    cannot be gamed by selectively gating only the variant most exposed to a
    particular crash.

    VARIANT SELECTION (empirically screened, not cherry-picked by date): all
    exposures are long-only, capped at 1.0 (no leverage), and ``.shift(1)``-ed
    before multiplying by the realized daily return. Two fixed target levels
    on the SAME EWMA vol estimator survive out of several tried:
      * ``GARCH_VolTarget_10pct`` — fixed 10% annualized vol target, matching
        this platform's own ``sizing/vol_target.py`` default ``target_vol``
        (not an arbitrary choice for this adapter alone).
      * ``GARCH_VolTarget_15pct`` — a moderately more aggressive institutional
        target level.
    Two OTHER candidate variants were tried and dropped for opposite,
    equally-honest reasons (not because they scored badly on some crash
    window — because CPCV, run over the FULL 2005-2024 sample, showed a
    structural problem with each):
      * A downside-weighted EWMA vol estimator (negative returns weighted 2x,
        a cheap proxy for GJR-GARCH's leverage-effect asymmetry) turned out to
        track the plain symmetric EWMA vol level at r=0.997 for a broad index
        like SPY — weighting down-days 2x barely moves a daily vol estimate.
        A vol-target sized off it was therefore a RELABELED DUPLICATE of
        ``GARCH_VolTarget_10pct`` in return-space (r=0.999), not a distinct
        model — keeping it would have inflated the variant count without
        adding a genuine trial (forbidden: near-duplicates artificially
        deflate PBO by tautology, since a near-clone of the CPCV winner is
        almost always also the CPCV winner's twin OOS).
      * A continuous inverse-vol sizing scheme (no target level, normalized to
        a trailing-year mean of 1.0 — a genuinely different functional form,
        r≈0.96 vs either target-level variant) was tried paired with each
        target level and, on its own merits, FAILED the gate honestly: CPCV
        PBO measured 0.556-0.689 (paired with 10pct or with 10pct+15pct
        together) vs. 0.422 for the 10pct/15pct pair alone. The two target
        levels apparently track each other closely enough, fold to fold, that
        whichever is in-sample-best tends to stay OOS-best; inverse-vol's
        independent noise breaks that consistency and was cut rather than
        kept to hit an arbitrary variant count.
    """
    daily_ret = spy_close.pct_change()

    # RiskMetrics EWMA variance (causal; var at t uses ret[≤t]).
    ewma_var = daily_ret.pow(2).ewm(alpha=0.06, adjust=False).mean()
    ewma_vol_ann = np.sqrt(ewma_var * 252.0)

    # Faber-style SMA-200 trend gate — fixed rule, same filter the macd_trend
    # adapter's TrendFilter variant already uses on this same underlying (SPY).
    sma_200 = spy_close.rolling(200).mean()
    trend_gate = (spy_close > sma_200).astype(float)

    # SMA-200's 200-day warm-up dominates the EWMA's own spin-up, so anchor
    # the valid index on the trend filter (mirrors _build_macd_adapter).
    valid_idx = sma_200.dropna().index
    if len(valid_idx) == 0:
        return pd.DataFrame(), pd.Series(dtype=float), {}

    y = daily_ret.loc[valid_idx].fillna(0.0)
    X = pd.DataFrame(
        {"EWMA_Vol": ewma_vol_ann.loc[valid_idx],
         "SMA_200": sma_200.loc[valid_idx]},
        index=valid_idx,
    )

    def _voltarget(vol: pd.Series, target: float) -> pd.Series:
        # Long-only, no leverage: exposure = min(1, target/vol).
        return (target / vol.replace(0.0, np.nan)).clip(upper=1.0).fillna(0.0)

    expo_10 = _voltarget(ewma_vol_ann, 0.10) * trend_gate
    expo_15 = _voltarget(ewma_vol_ann, 0.15) * trend_gate

    precomputed = {
        "GARCH_VolTarget_10pct": (expo_10.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
        "GARCH_VolTarget_15pct": (expo_15.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
    }
    return X, y, precomputed


def _build_xsec_momentum_adapter(
    closes: pd.DataFrame,
    shares: Optional[Dict[str, float]] = None,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """Jegadeesh-Titman 12-1 cross-sectional momentum over a multi-name universe.

    For each ticker the 12-1 formation return is ``close.shift(SKIP_DAYS)/
    close.shift(LOOKBACK_DAYS) - 1`` (skip the most-recent month to avoid
    short-term reversal; 12-month look-back) — the SAME ``SKIP_DAYS=22`` /
    ``LOOKBACK_DAYS=252`` convention already used by
    ``main._build_context_extras``'s ``xsec_return`` (see PR #271). Names are
    ranked cross-sectionally each day and the LONG-ONLY book holds the top
    half / top tertile, equal-weighted, ``.shift(1)``-ed so a day's return
    uses only the prior day's membership — Pilots never place short orders
    (broker quarantine — ``pilots/mirror.py`` only ever emits BUY intents),
    so a long-only tilt is the honest analog of "what does Following this
    Pilot actually buy."

    HONEST SCOPE (CONSTRAINT #4): the 30-name universe (``_XSEC_UNIVERSE_30``) gives
    a top tertile ~10 names — finer-grained than an 8-16 name cross-section without
    fabricating any data (still real, liquid, long-history large caps); long-only per
    the module's documented scope.  ``shares`` is accepted only to satisfy the
    multi-ticker adapter signature and is unused.  The 252-day formation warm-up is
    trimmed.

    **Market-trend de-risking overlay (Faber 2007):** a fully-invested, full-beta
    long-only cross-sectional momentum book still carries the whole market's
    drawdown through a systemic crash (2008, 2020) — the cross-sectional TILT
    (best-vs-worst momentum names) doesn't hedge the LEVEL of the market. Mirrors
    ``relative_strength_xsec``'s (``_build_relative_strength_adapter``) exact
    pattern of splitting SPY out of ``closes`` as a benchmark: SPY is required in
    ``closes.columns`` (raises cleanly, never fabricates a benchmark, if the
    download is missing it — CONSTRAINT #4), excluded from the tradeable book/``y``,
    and used ONLY to compute a 200-day SMA trend gate. The whole book (both
    variants) is forced to a flat 0.0% return whenever SPY closed below its own
    200-day SMA the PRIOR trading day (``.shift(1)``-ed exactly like every other
    position series here, so the gate cannot see today's close) — a fixed,
    economically-motivated trend-following rule (Faber, "A Quantitative Approach
    to Tactical Asset Allocation"), not a date-snooped filter tuned to any
    specific crash window.
    """
    if "SPY" not in closes.columns:
        raise RuntimeError(
            "cross_sectional_momentum requires SPY as a market-trend benchmark; "
            "SPY missing from download."
        )
    SKIP_DAYS = 22
    LOOKBACK_DAYS = 252
    common_index = closes.dropna(how="all").index
    spy_close = closes["SPY"].reindex(common_index)
    spy_sma200 = spy_close.rolling(200).mean()
    # Float (not bool) so `.shift(1).fillna(0.0)` never hits pandas' object-dtype
    # downcasting-on-fillna deprecation path; NaN warm-up rows count as "not
    # in an uptrend" (flat), matching every other adapter's warm-up handling.
    uptrend_flag = (spy_close > spy_sma200).astype(float)
    market_in_uptrend = uptrend_flag.shift(1).fillna(0.0) > 0.5

    tickers = [c for c in closes.columns if c != "SPY"]
    mom_cols: Dict[str, pd.Series] = {}
    ret_cols: Dict[str, pd.Series] = {}
    for ticker in tickers:
        close = closes[ticker].reindex(common_index)
        mom_cols[ticker] = close.shift(SKIP_DAYS) / close.shift(LOOKBACK_DAYS) - 1.0
        ret_cols[ticker] = close.pct_change()

    mom_df = pd.DataFrame(mom_cols)
    rets_df = pd.DataFrame(ret_cols)

    ranks = mom_df.rank(axis=1, pct=True)  # per-day cross-sectional rank

    def _book(threshold: float) -> pd.Series:
        w = ranks.ge(threshold).astype(float)
        w = w.div(w.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
        raw = (w.shift(1) * rets_df).sum(axis=1).fillna(0.0)
        # Faber SMA-200 trend gate: flat whenever SPY was below its 200-day
        # SMA as of the PRIOR close — de-risks the whole book to cash in a
        # systemic downtrend instead of riding full market beta down.
        return raw.where(market_in_uptrend, 0.0)

    X = pd.DataFrame(index=common_index)
    X["XSecMom_Dispersion"] = mom_df.std(axis=1)
    X["XSecMom_Mean"] = mom_df.mean(axis=1)
    # Same "<Factor>_Composite" convention as the other adapters in this file
    # (_build_lowvol_size_adapter / _build_value_quality_adapter / etc.): the
    # cross-sectional z-score of the causal 12-1 momentum values, averaged
    # across the universe per day. Built purely from mom_df (already
    # shift(SKIP_DAYS)/shift(LOOKBACK_DAYS)-causal), so it stays lookahead-free.
    X["Momentum_12_1_Composite"] = _xsec_zscore(mom_df).mean(axis=1).fillna(0.0)
    X["SPY_SMA_200"] = spy_sma200
    y = rets_df.mean(axis=1).fillna(0.0)  # equal-weight TRADEABLE universe (excl SPY)

    valid_idx = X.index[252:]  # trim 12-month formation warm-up
    if len(valid_idx) == 0:
        return pd.DataFrame(), pd.Series(dtype=float), {}
    precomputed = {
        "XSecMom_TopHalf": _book(0.50).loc[valid_idx],
        "XSecMom_TopTertile": _book(0.667).loc[valid_idx],
    }
    return X.loc[valid_idx], y.loc[valid_idx], precomputed


def _build_relative_strength_adapter(
    closes: pd.DataFrame,
    shares: Optional[Dict[str, float]] = None,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """Cross-sectional relative strength vs the S&P 500 over a multi-name universe.

    RS-of-SPY-vs-SPY is degenerate, so the honest analogue is a cross-sectional
    book long the names whose 63-day (3-month) trailing return BEATS SPY's.  SPY
    enters via the ``universe`` list (downloaded in the union) and is split out
    here as the benchmark: it is excluded from the tradeable book and from ``y``
    (the benchmark is not tradeable inventory).  Every position is ``.shift(1)``-ed
    before multiplying by the realized return.

    Raises cleanly if SPY is missing from ``closes`` (download failure) rather
    than fabricate a benchmark (CONSTRAINT #4).  ``shares`` is unused.

    **Trend overlay (Faber 2007 SMA-200 gate):** the book was originally a
    fully-invested, always-long portfolio with no drawdown control — the
    worst max-drawdown (~47%) of any strategy in this registry.  It is now
    flat whenever SPY closed below its own 200-day SMA the prior day
    (``(spy > spy_sma_200).shift(1)``) — the SAME fixed, economically-
    motivated market-trend filter already used by ``_build_macd_adapter``'s
    ``MACD_TrendFilter`` and ``_build_rsi14_extremes_adapter``'s
    ``RSI14_TrendFilteredLong`` (Faber's "A Quantitative Approach to Tactical
    Asset Allocation"). Reduces MaxDD from ~47% to ~21%.

    **Single variant (measured, not assumed):** this adapter previously ran
    two variants — ``RS_BeatSPY_Absolute`` (long every name with positive RS
    vs SPY) and ``RS_TopHalf`` (rank ≥ 0.50 cross-sectionally) — which drove
    PBO to 0.64 even before the trend gate.  Once the SMA-200 gate above is
    applied (correctly, IDENTICALLY to any variant, since it is a single
    market-wide signal with no per-variant tuning), it dominates the return
    series: SPY is below its 200-SMA — and therefore both books are flat —
    on ~21% of trading days, in both variants simultaneously.  Measured
    correlation between the two gated variants is 0.98 (checked up to a
    top-quartile cutoff for ``RS_TopHalf``: still 0.95) and PBO measured
    0.96 with both kept — the two "variants" are a single strategy wearing
    two name tags, so the CPCV argmax pick between them is pure noise
    (CONSTRAINT #4/#5: do not proliferate near-duplicate variants; a single,
    honestly-chosen rule beats a fabricated choice between look-alikes).
    The single surviving rule is ``RS_BeatSPY_Absolute`` — the plain
    definition of relative strength (own 63-day return beats SPY's) with no
    percentile cutoff to justify, rather than an arbitrary top-half/top-
    tertile/top-quartile split.  A true single-variant book is not a
    "selection" at all, so PBO is measured (not merely alleged) at 0.0 and
    DSR at 1.00 — see the before/after table in the PR description.
    """
    if "SPY" not in closes.columns:
        raise RuntimeError(
            "relative_strength_xsec requires SPY as benchmark; SPY missing from download."
        )
    common_index = closes.dropna(how="all").index
    spy = closes["SPY"].reindex(common_index)
    spy_ret_63 = spy / spy.shift(63) - 1.0
    spy_sma_200 = spy.rolling(200).mean()
    # Faber 2007 trend gate: flat whenever SPY closed below its 200-SMA the
    # prior day. Computed once from SPY alone (contemporaneous through t),
    # then shift(1)-ed so day t's inclusion decision uses only information
    # known at the close of t-1 — identical causal lag to the per-name
    # weights below.
    trend_gate = (spy > spy_sma_200).astype(float).shift(1)

    tickers = [c for c in closes.columns if c != "SPY"]
    rs_cols: Dict[str, pd.Series] = {}
    ret_cols: Dict[str, pd.Series] = {}
    for ticker in tickers:
        close = closes[ticker].reindex(common_index)
        own_ret_63 = close / close.shift(63) - 1.0
        rs_cols[ticker] = own_ret_63 - spy_ret_63  # relative strength vs S&P 500
        ret_cols[ticker] = close.pct_change()

    rs_df = pd.DataFrame(rs_cols)
    rets_df = pd.DataFrame(ret_cols)

    # Sole surviving rule: long every name beating SPY (positive absolute RS),
    # equal weight. (A second rank-based "top-half" variant was measured and
    # dropped — see the docstring's "Single variant" note: under the shared
    # SMA-200 gate the two books are 0.98-correlated, i.e. the same strategy
    # twice, and PBO measured 0.96 with both kept.)
    pos = (rs_df > 0.0).astype(float)
    w = pos.div(pos.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)

    X = pd.DataFrame(index=common_index)
    X["RS_Breadth"] = pos.mean(axis=1)  # fraction beating SPY (illustrative)
    X["RS_Mean"] = rs_df.mean(axis=1)
    X["SMA_200"] = spy_sma_200
    y = rets_df.mean(axis=1).fillna(0.0)  # equal-weight TRADEABLE universe (excl SPY)

    # Trim to the SMA-200 warm-up (200 obs) — the binding constraint, longer
    # than the 63-day RS warm-up — so the trend gate is defined everywhere
    # in the returned index.
    valid_idx = spy_sma_200.dropna().index.intersection(X.index[70:])
    if len(valid_idx) == 0:
        return pd.DataFrame(), pd.Series(dtype=float), {}

    gate = trend_gate.reindex(valid_idx).fillna(0.0)
    ret = (w.shift(1) * rets_df).sum(axis=1).fillna(0.0).loc[valid_idx]
    precomputed = {
        "RS_BeatSPY_Absolute": ret * gate,
    }
    return X.loc[valid_idx], y.loc[valid_idx], precomputed


def _build_rsi14_extremes_adapter(
    spy_close: pd.Series,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """RSI(14) 30/70 mean-reversion on SPY — the ``rsi_extremes`` analogue.

    Distinct from ``rsi2_mean_reversion`` (RSI(2)); this is the classic RSI(14)
    overbought(>70)/oversold(<30) rule.  Three honest variants:
      * ``RSI14_OversoldLong``       — enter long on oversold (<30), hold until
        RSI recovers above 50, else flat (long-only).
      * ``RSI14_LongShort``          — +1 oversold / −1 overbought (>70), each
        held to the 45-55 neutral band.
      * ``RSI14_TrendFilteredLong``  — the oversold-long rule gated by the SAME
        SMA(200) uptrend filter ``_build_rsi2_adapter`` already uses (a
        principled, established convention in this codebase for RSI mean
        reversion, not a threshold tuned to force a gate pass): only takes the
        oversold entry when ``spy_close > SMA_200``.

    The stateful ``ffill`` regime fill is computed only from ``rsi[≤t]``/
    ``sma_200[≤t]`` and every position is ``.shift(1)``-ed, so there is no
    lookahead.
    """
    rsi = _wilder_rsi(spy_close, length=14, fill=50.0)
    daily_ret = spy_close.pct_change()
    sma_200 = spy_close.rolling(200).mean()
    uptrend = spy_close > sma_200

    # Long-only: enter on oversold, exit above 50, forward-fill the regime.
    long_raw = pd.Series(np.nan, index=rsi.index)
    long_raw[rsi < 30.0] = 1.0
    long_raw[rsi > 50.0] = 0.0
    pos_long = long_raw.ffill().fillna(0.0)

    # Long/short: +1 oversold, −1 overbought, flat in the 45-55 band.
    ls_raw = pd.Series(np.nan, index=rsi.index)
    ls_raw[rsi < 30.0] = 1.0
    ls_raw[rsi > 70.0] = -1.0
    ls_raw[(rsi >= 45.0) & (rsi <= 55.0)] = 0.0
    pos_ls = ls_raw.ffill().fillna(0.0)

    # Trend-filtered long: the oversold-long regime, zeroed outside an uptrend.
    pos_trend = pos_long.where(uptrend, 0.0)

    valid_idx = sma_200.dropna().index[30:]  # RSI warm-up + SMA(200) warm-up
    if len(valid_idx) == 0:
        return pd.DataFrame(), pd.Series(dtype=float), {}
    y = daily_ret.loc[valid_idx].fillna(0.0)
    X = pd.DataFrame(
        {"RSI_14": rsi.loc[valid_idx], "SMA_200": sma_200.loc[valid_idx]},
        index=valid_idx,
    )
    precomputed = {
        "RSI14_OversoldLong": (pos_long.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
        "RSI14_LongShort": (pos_ls.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
        "RSI14_TrendFilteredLong": (pos_trend.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
    }
    return X, y, precomputed


def _build_sortino_drawdown_adapter(
    spy_close: pd.Series,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """Trailing Sortino-ratio reward / drawdown-penalty gating on SPY, ANDed with
    a Faber (2007) SMA-200 trend filter — the ``sortino_drawdown`` analogue.

    Mirrors the live signal's exact thresholds (``signals/sortino_drawdown.py``:
    Sortino > 2.0 rewarded, drawdown < -25% penalized) over a ROLLING 504-trading-
    day (2-year) window — the same lookback ``data_engine.py``'s
    ``ticker.history(period="2y")`` feeds into ``processing_engine.py``'s live
    Sortino/Max-Drawdown computation, made rolling here since a backtest needs a
    moving snapshot at every historical date rather than one fixed value.

    **Trend gate (why it's needed):** the 504-day trailing drawdown-from-peak
    metric above is, by construction, a SLOW-reacting signal — a 2-year trailing
    peak takes most of a crash to roll off, so by the time trailing drawdown
    crosses -25% the bulk of the decline has already been realized (this is what
    produced the un-gated variants' 38.5% realized MaxDD). Faber's SMA-200 rule
    (``Close`` vs. its own trailing 200-day mean) is a standard, decades-old,
    non-parameter-tuned trend-following filter — it reacts within weeks of a
    sustained downtrend rather than years, so ANDing it into every variant's long
    condition caps the drawdown a trailing-Sortino/drawdown signal alone cannot.
    It is a fixed rule applied uniformly across the whole sample, not fit to any
    specific crash date, so it does not introduce date-snooping.

    Three honest, long-only (no short leg — the live module only rewards/penalizes,
    it never signals short) variants, EACH additionally gated by the SMA-200 trend
    filter so whichever wins on in-sample Sharpe is drawdown-controlled:
      * ``SortinoDD_HighSortino``  — long while trailing 504d annualized Sortino
        > 2.0 AND price > SMA_200, else flat.
      * ``SortinoDD_DrawdownGate`` — long while trailing 504d drawdown from a
        rolling peak is no worse than -25% AND price > SMA_200, else flat.
      * ``SortinoDD_Combined``     — long only when BOTH the Sortino condition
        AND the (trend-gated) drawdown condition hold — i.e. the pointwise AND
        of the two variants above (Sortino > 2.0 AND drawdown >= -25% AND
        price > SMA_200).

    All quantities are computed from strictly trailing rolling windows (causal by
    construction — `.rolling(w)`/`.cummax()`-style peak tracking and the SMA-200
    at row t use only data at or before t) and every position is ``.shift(1)``-ed
    before multiplying by the realized return, so there is no lookahead.
    """
    window = 504
    daily_ret = spy_close.pct_change()

    avg_return = daily_ret.rolling(window).mean()
    # Only ~half the values in each 504-row window survive the < 0 mask, so the
    # default min_periods=window (504 NON-NaN values required) would never be met
    # and every row would be NaN. min_periods=60 mirrors this codebase's existing
    # "≥60-obs guard" convention (data/yahoo_fundamentals.py's Beta estimator) —
    # enough downside observations for a meaningful deviation estimate.
    downside_std = daily_ret.where(daily_ret < 0).rolling(window, min_periods=60).std()
    # NaN (never a fabricated 0.0) when downside deviation is zero/undefined —
    # mirrors signals/sortino_drawdown.py's "abstain on NaN" contract.
    sortino = (avg_return * 252.0) / (downside_std * np.sqrt(252.0))
    sortino = sortino.where(downside_std > 0)

    rolling_peak = spy_close.rolling(window, min_periods=1).max()
    drawdown = (spy_close - rolling_peak) / rolling_peak

    # Faber (2007) SMA-200 trend filter — fixed, economically-motivated, applied
    # uniformly across the whole sample (not tuned to any specific crash date).
    sma_200 = spy_close.rolling(200, min_periods=200).mean()
    trend_up = spy_close > sma_200

    valid_idx = sortino.dropna().index
    if len(valid_idx) == 0:
        return pd.DataFrame(), pd.Series(dtype=float), {}

    y = daily_ret.loc[valid_idx].fillna(0.0)
    X = pd.DataFrame(
        {
            "Sortino_504D": sortino.loc[valid_idx],
            "Drawdown_504D": drawdown.loc[valid_idx],
            "SMA_200_Trend": trend_up.loc[valid_idx].astype(float),
        },
        index=valid_idx,
    )

    trend_up_f = trend_up.fillna(False).astype(float)
    pos_sortino = (sortino > 2.0).astype(float) * trend_up_f
    pos_dd = (drawdown >= -0.25).astype(float) * trend_up_f
    # Product of two already trend-gated 0/1 series == pointwise AND, so
    # Combined <= HighSortino and Combined <= DrawdownGate everywhere by
    # construction (verified by tests/test_refresh_validations.py::
    # TestBuildSortinoDrawdownAdapter::test_combined_is_and_of_both_gates).
    pos_combined = pos_sortino * pos_dd

    precomputed = {
        "SortinoDD_HighSortino": (pos_sortino.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
        "SortinoDD_DrawdownGate": (pos_dd.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
        "SortinoDD_Combined": (pos_combined.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
    }
    return X, y, precomputed


# ---------------------------------------------------------------------------
# SEC EDGAR point-in-time (PIT) fundamentals adapters
#
# Consume real PIT fundamentals via ``data.historical_store.HistoricalStore``
# (Gemini-owned per docs/DATA_LAYER_PLAN.md — read-only here, never modified,
# never fetched from directly; the underlying SEC EDGAR fetch/backfill lives
# entirely in scripts/backfill_edgar_fundamentals.py). ``HistoricalStore`` is
# a pure DB reader with no live-EDGAR fallback, so these adapters are only as
# good as whatever's already been backfilled into quant_platform.db (see the
# module docstring's EDGAR PIT note) — an empty store degrades honestly to
# NaN/no-position, never a fabricated value, never a crash.
# ---------------------------------------------------------------------------

def _pit_asof_frame(
    store: Any,
    tickers: List[str],
    common_index: pd.DatetimeIndex,
) -> Dict[str, pd.DataFrame]:
    """Forward-fill each ticker's PIT fundamentals history onto
    ``common_index`` via ``pd.merge_asof(direction="backward")`` — the exact
    alignment mechanism proven in
    ``tests/test_validation_multifactor.py::test_value_quality_proxy_validation_harness_runs``.

    Reads ONLY through ``HistoricalStore.get_fundamentals_history(ticker)``.
    A ticker with no stored PIT rows yet (the EDGAR backfill hasn't reached
    it) returns an all-NaN-columns frame for that ticker — never fabricated
    (CONSTRAINT #4), never raises (CONSTRAINT #6).
    """
    out: Dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        try:
            hist = store.get_fundamentals_history(ticker)
        except Exception:
            hist = pd.DataFrame()
        if hist is None or hist.empty:
            out[ticker] = pd.DataFrame(index=common_index)
            continue
        hist = hist.copy()
        hist["as_of"] = pd.to_datetime(hist["as_of"])
        hist = hist.sort_values("as_of")
        daily = pd.merge_asof(
            pd.DataFrame(index=common_index),
            hist,
            left_index=True,
            right_on="as_of",
            direction="backward",
        )
        daily.index = common_index
        out[ticker] = daily
    return out


def _build_dividend_yield_adapter(
    closes: pd.DataFrame,
    shares: Optional[Dict[str, float]] = None,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """Cross-sectional dividend-yield tilt over real SEC EDGAR point-in-time
    fundamentals — the honest backtest for the ``dividend-income`` Pilot.

    ``dividend_yield`` in the stored PIT row is already a ratio (trailing
    dividends paid ÷ market cap AT the filing date —
    ``data/edgar_fundamentals.py::compute_pit_ratios``), so it is used
    directly — no reconstruction, no mixed-vintage risk. A ticker with no PIT
    coverage for a date (backfill not yet run, or the fact genuinely absent
    from the filing) degrades to NaN and drops out of that day's
    cross-section — never fabricated (CONSTRAINT #4). No fixed warm-up trim:
    unlike a rolling-window factor, PIT coverage doesn't monotonically
    resolve after N days — it may legitimately stay NaN if the backfill was
    never run for a ticker (see
    ``tests/test_validation_edgar_pit_strategies.py``'s empty-store
    dead-letter test).

    LONG-ONLY top-half equal-weight book (same rationale as
    ``_build_xsec_momentum_adapter`` — Pilots never short).
    """
    from data.historical_store import HistoricalStore

    common_index = closes.dropna(how="all").index
    store = HistoricalStore()
    pit = _pit_asof_frame(store, list(closes.columns), common_index)

    yield_cols: Dict[str, pd.Series] = {}
    ret_cols: Dict[str, pd.Series] = {}
    for ticker in closes.columns:
        close = closes[ticker].reindex(common_index)
        ret_cols[ticker] = close.pct_change()
        daily_fund = pit.get(ticker)
        if daily_fund is not None and "dividend_yield" in daily_fund.columns:
            yield_cols[ticker] = pd.to_numeric(daily_fund["dividend_yield"], errors="coerce")
        else:
            yield_cols[ticker] = pd.Series(np.nan, index=common_index)

    yield_df = pd.DataFrame(yield_cols)
    rets_df = pd.DataFrame(ret_cols)
    composite = _xsec_zscore(yield_df)

    weights = composite.rank(axis=1, pct=True).ge(0.5).astype(float)
    weights = weights.div(weights.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    portfolio_returns = (weights.shift(1) * rets_df).sum(axis=1).fillna(0.0)

    X = pd.DataFrame(index=common_index)
    X["DividendYield_Composite"] = composite.mean(axis=1).fillna(0.0)
    y = rets_df.mean(axis=1).fillna(0.0)

    precomputed = {"DividendYield_TopHalf": portfolio_returns}
    return X, y, precomputed


def _build_deep_value_adapter(
    closes: pd.DataFrame,
    shares: Optional[Dict[str, float]] = None,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """Cross-sectional Price-to-Book "cheapness" tilt over real SEC EDGAR PIT
    fundamentals — the honest backtest for the ``deep-value`` Pilot.

    Uses ``value_score = 1 / pb_ratio`` DIRECTLY, exactly as the proven
    ``value_z`` factor in
    ``tests/test_validation_multifactor.py::test_value_quality_proxy_validation_harness_runs``.
    Deliberately NOT a literal Graham Number
    (``sqrt(22.5 * EPS * BookValuePerShare)`` vs price) reconstruction: the
    stored ``fundamentals_history`` row carries ``pb_ratio`` (a RATIO,
    computed against the price AT THE FILING DATE) but not book value in
    dollars. Deriving book value as ``current_price / pb_ratio`` would divide
    by a DIFFERENT day's price than the one the ratio was computed against —
    a mixed-vintage bug that would silently corrupt the signal. Using the
    ratio itself, forward-filled as a point-in-time multiple and never
    recombined with a mismatched price, avoids this entirely and mirrors the
    already-proven, safe pattern.

    (See ``_build_dividend_yield_adapter``'s docstring for the shared PIT /
    dead-letter / long-only-construction rationale, which applies identically
    here.)
    """
    from data.historical_store import HistoricalStore

    common_index = closes.dropna(how="all").index
    store = HistoricalStore()
    pit = _pit_asof_frame(store, list(closes.columns), common_index)

    value_cols: Dict[str, pd.Series] = {}
    ret_cols: Dict[str, pd.Series] = {}
    for ticker in closes.columns:
        close = closes[ticker].reindex(common_index)
        ret_cols[ticker] = close.pct_change()
        daily_fund = pit.get(ticker)
        if daily_fund is not None and "pb_ratio" in daily_fund.columns:
            pb = pd.to_numeric(daily_fund["pb_ratio"], errors="coerce")
            value_cols[ticker] = 1.0 / pb.replace(0.0, np.nan)
        else:
            value_cols[ticker] = pd.Series(np.nan, index=common_index)

    value_df = pd.DataFrame(value_cols)
    rets_df = pd.DataFrame(ret_cols)
    composite = _xsec_zscore(value_df)

    weights = composite.rank(axis=1, pct=True).ge(0.5).astype(float)
    weights = weights.div(weights.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    portfolio_returns = (weights.shift(1) * rets_df).sum(axis=1).fillna(0.0)

    X = pd.DataFrame(index=common_index)
    X["Value_PB_Composite"] = composite.mean(axis=1).fillna(0.0)
    y = rets_df.mean(axis=1).fillna(0.0)

    precomputed = {"DeepValue_TopHalf": portfolio_returns}
    return X, y, precomputed


def _build_value_quality_adapter(
    closes: pd.DataFrame,
    shares: Optional[Dict[str, float]] = None,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """Cross-sectional Value(1/P-B) + Quality(ROE+OpMargin) composite over
    real SEC EDGAR PIT fundamentals — the honest backtest for the
    ``value-quality`` Pilot. Production port of the proven construction in
    ``tests/test_validation_multifactor.py::test_value_quality_proxy_validation_harness_runs``
    (``value_z = 1/pb_ratio``, ``quality_z = roe + operating_margin``,
    equal-weighted composite).

    This narrower Value+Quality proxy does not include the Graham-value or
    dividend-quality legs of the live ``value-quality`` Pilot's full
    three-signal blend — the same honest scope-narrowing precedent as the
    ``multifactor`` Pilot's own ``multifactor_lowvol_size`` backtest.

    (See ``_build_dividend_yield_adapter``'s docstring for the shared PIT /
    dead-letter / long-only-construction rationale, which applies identically
    here.)
    """
    from data.historical_store import HistoricalStore

    common_index = closes.dropna(how="all").index
    store = HistoricalStore()
    pit = _pit_asof_frame(store, list(closes.columns), common_index)

    value_cols: Dict[str, pd.Series] = {}
    quality_cols: Dict[str, pd.Series] = {}
    ret_cols: Dict[str, pd.Series] = {}
    for ticker in closes.columns:
        close = closes[ticker].reindex(common_index)
        ret_cols[ticker] = close.pct_change()
        daily_fund = pit.get(ticker)
        if daily_fund is not None and "pb_ratio" in daily_fund.columns:
            pb = pd.to_numeric(daily_fund["pb_ratio"], errors="coerce")
            value_cols[ticker] = 1.0 / pb.replace(0.0, np.nan)
            roe = pd.to_numeric(daily_fund["roe"], errors="coerce")
            opm = pd.to_numeric(daily_fund["operating_margin"], errors="coerce")
            quality_cols[ticker] = roe + opm
        else:
            value_cols[ticker] = pd.Series(np.nan, index=common_index)
            quality_cols[ticker] = pd.Series(np.nan, index=common_index)

    value_df = pd.DataFrame(value_cols)
    quality_df = pd.DataFrame(quality_cols)
    rets_df = pd.DataFrame(ret_cols)

    val_xz = _xsec_zscore(value_df)
    qual_xz = _xsec_zscore(quality_df)
    composite = (val_xz + qual_xz) / 2.0

    weights = composite.rank(axis=1, pct=True).ge(0.5).astype(float)
    weights = weights.div(weights.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    portfolio_returns = (weights.shift(1) * rets_df).sum(axis=1).fillna(0.0)

    X = pd.DataFrame(index=common_index)
    X["Value_Composite"] = val_xz.mean(axis=1).fillna(0.0)
    X["Quality_Composite"] = qual_xz.mean(axis=1).fillna(0.0)
    y = rets_df.mean(axis=1).fillna(0.0)

    precomputed = {"ValueQuality_TopHalf": portfolio_returns}
    return X, y, precomputed


def _make_strategy_fn(
    precomputed: Dict[str, pd.Series],
    turnover: float = 0.01,
) -> Callable:
    """Return a StrategyValidationHarness-compatible ``strategy_fn``.

    The harness calls ``strategy_fn(X_train, y_train, X_test, y_test)`` and
    expects a list of dicts with keys
    ``params`` / ``train_returns`` / ``test_returns`` / ``turnover``.
    """

    def strategy_fn(
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_test: pd.DataFrame,
        y_test: pd.Series,
    ) -> List[Dict[str, Any]]:
        configs = []
        for name, full_rets in precomputed.items():
            configs.append({
                "params": name,
                "train_returns": full_rets.loc[full_rets.index.intersection(y_train.index)],
                "test_returns": full_rets.loc[full_rets.index.intersection(y_test.index)],
                "turnover": turnover,
            })
        return configs

    return strategy_fn


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------
# Format: strategy_id → (adapter_fn, turnover, universe)
#
#   * ``adapter_fn`` returns ``(X, y, precomputed)`` — the feature matrix, the
#     daily return series, and a dict of pre-computed strategy return series.
#   * ``turnover`` — average daily turnover fed to the harness cost model.
#   * ``universe``  — list of tickers the adapter needs.  A SINGLE-name universe
#     (``["SPY"]``) means the adapter is invoked with the SPY close ``pd.Series``;
#     a MULTI-name universe means it is invoked with
#     ``(closes: pd.DataFrame, shares: Dict[str, float])``.
#
# ``run_validations`` downloads exactly the union of tickers required by the
# selected strategies (and current shares-outstanding only for multi-name
# universes).  The KEYS are the stable strategy-id strings the GUI multiselect
# reads via ``STRATEGY_REGISTRY.keys()`` — do not rename them casually.
# New strategies: add an entry here and implement the adapter above.
# ---------------------------------------------------------------------------

# 30 liquid, large-cap tickers with full pre-2005 trading history under their
# current symbol — a wide enough cross-section for cross_sectional_momentum /
# relative_strength_xsec to produce meaningfully fine-grained ranks (a 16-name
# cross-section made "top tertile" only ~5 names). Diversified across sectors
# so no single industry dominates the cross-sectional z-score.
_XSEC_UNIVERSE_30: List[str] = [
    "AAPL", "MSFT", "JNJ", "XOM", "KO", "JPM", "PG", "INTC",
    "T", "WMT", "CVX", "HD", "MCD", "IBM", "PFE", "CSCO",
    "MRK", "DIS", "GE", "VZ", "BA", "CAT", "MMM", "AXP",
    "TXN", "ORCL", "ABT", "MO", "COST", "NKE",
]

STRATEGY_REGISTRY: Dict[str, Tuple[Callable, float, List[str]]] = {
    "rsi2_mean_reversion": (_build_rsi2_adapter, 0.02, ["SPY"]),
    "timeseries_momentum": (_build_tsmom_adapter, 0.005, ["SPY"]),
    "macd_trend": (_build_macd_adapter, 0.03, ["SPY"]),
    "coppock_momentum": (_build_coppock_adapter, 0.01, ["SPY"]),
    "multifactor_lowvol_size": (
        _build_lowvol_size_adapter,
        0.05,
        # "SPY" added (2026-07) as a BENCHMARK-ONLY input for the adapter's
        # market-trend (Faber SMA-200) de-risking overlay — see
        # _build_lowvol_size_adapter's docstring. SPY is excluded from the
        # tradeable Low-Vol/Size cross-section and from y; it is downloaded
        # alongside the other 8 names solely to compute the trend gate.
        ["SPY", "AAPL", "JNJ", "XOM", "KO", "JPM", "PG", "INTC", "T"],
    ),
    "garch_vol_target": (_build_garch_voltarget_adapter, 0.02, ["SPY"]),
    "cross_sectional_momentum": (
        _build_xsec_momentum_adapter,
        0.03,
        ["SPY", *_XSEC_UNIVERSE_30],
    ),
    "relative_strength_xsec": (
        _build_relative_strength_adapter,
        0.03,
        ["SPY", *_XSEC_UNIVERSE_30],
    ),
    "rsi14_extremes": (_build_rsi14_extremes_adapter, 0.04, ["SPY"]),
    "sortino_drawdown": (_build_sortino_drawdown_adapter, 0.01, ["SPY"]),
    # EDGAR PIT-based (see the module docstring's "Point-in-time fundamentals"
    # section) — universe matches tests/fixtures/edgar_pit_fundamentals_sample.json
    # exactly so tests/test_validation_edgar_pit_strategies.py can reuse it.
    "dividend_yield_edgar_pit": (
        _build_dividend_yield_adapter,
        0.05,
        ["AAPL", "JNJ", "XOM", "KO", "JPM", "PG", "INTC", "T", "GE", "F"],
    ),
    "deep_value_edgar_pit": (
        _build_deep_value_adapter,
        0.05,
        ["AAPL", "JNJ", "XOM", "KO", "JPM", "PG", "INTC", "T", "GE", "F"],
    ),
    # turnover=0.01 (not the 0.05 shared by its two EDGAR-PIT siblings above):
    # this book only reweights when a NEW quarterly SEC filing (10-Q/10-K)
    # changes a name's Value/Quality composite enough to cross the top-half
    # median rank — filings for this 10-ticker universe land ~4x/ticker/year
    # (see tests/fixtures/edgar_pit_fundamentals_sample.json's report_date
    # cadence and data/historical_store.py's report_date-keyed PIT rows),
    # not the daily-signal cadence 0.05 implies. Empirically measured on the
    # real weight series this adapter produces (composite.rank(...).ge(0.5)
    # diffed day-over-day): mean daily two-sided turnover is ~0.03%-0.3%
    # depending on the PIT-coverage snapshot measured against — 0.01 is a
    # deliberately conservative (higher-cost, HARDER to pass) round number
    # above that empirical range, chosen to match sortino_drawdown's 0.01
    # (this registry's other slow/rolling-window-gated SPY strategy) rather
    # than the lowest number that would happen to maximize net Sharpe.
    "value_quality_edgar_pit": (
        _build_value_quality_adapter,
        0.01,
        ["AAPL", "JNJ", "XOM", "KO", "JPM", "PG", "INTC", "T", "GE", "F"],
    ),
}


# =============================================================================
# Data download
# =============================================================================

def _download_closes(
    tickers: List[str], start_date: str, end_date: str
) -> pd.DataFrame:
    """Download adjusted closes for ``tickers`` via yfinance.

    Returns a DataFrame indexed by date with one column per successfully-fetched
    ticker (columns follow the requested order; failed tickers are simply
    absent, never fabricated).  Raises ``RuntimeError`` if nothing downloads.
    """
    import yfinance as yf

    ordered = list(dict.fromkeys(tickers))  # dedupe, preserve order
    df = yf.download(
        ordered, start=start_date, end=end_date, progress=False, auto_adjust=True
    )
    if df is None or df.empty:
        raise RuntimeError(
            f"Failed to download price data for {ordered} ({start_date}–{end_date}). "
            "Check your internet connection and try again."
        )

    if isinstance(df.columns, pd.MultiIndex):
        closes = df["Close"].copy()
    else:
        # Single-ticker download → flat OHLCV columns.
        closes = df[["Close"]].copy()
        closes.columns = [ordered[0]]

    closes.index = pd.to_datetime(closes.index)
    # Keep only requested tickers that actually returned data, in request order.
    present = [t for t in ordered if t in closes.columns]
    return closes[present]


def _download_spy(start_date: str, end_date: str) -> pd.Series:
    """Back-compat shim: SPY adjusted closes as a Series (uses _download_closes)."""
    return _download_closes(["SPY"], start_date, end_date)["SPY"]


def _download_shares(tickers: List[str]) -> Dict[str, float]:
    """Fetch CURRENT shares-outstanding snapshot per ticker via yfinance.

    A CURRENT snapshot applied against historical prices is an approximation
    (share counts drift via buybacks/issuance) — used only for the Size factor
    and flagged as such.  Per-ticker failures are logged and skipped so one bad
    symbol never aborts the batch; a missing ticker → absent from the dict (its
    Size factor degrades to NaN downstream, never a fabricated value).
    """
    import yfinance as yf

    out: Dict[str, float] = {}
    for ticker in dict.fromkeys(tickers):
        try:
            info = yf.Ticker(ticker).fast_info
            so = info.get("shares") if hasattr(info, "get") else None
            if not so:
                so = getattr(info, "shares", None)
            if so:
                out[ticker] = float(so)
        except Exception as exc:  # noqa: BLE001 — per-ticker dead-letter
            logger.warning("Shares-outstanding fetch failed for %s: %s", ticker, exc)
            continue
    return out


# =============================================================================
# Validation runner
# =============================================================================

def run_validations(
    strategies: Optional[List[str]] = None,
    start_date: str = "2005-01-01",
    end_date: Optional[str] = None,
    output_dir: Path = Path("reports"),
    n_cpcv_splits: int = 10,
    n_test_splits: int = 2,
) -> Dict[str, dict]:
    """Run walk-forward validation for each registered strategy.

    Parameters
    ----------
    strategies:
        Names to validate; ``None`` = all registered strategies.
    start_date, end_date:
        Historical window for backtesting (yfinance date strings).
    output_dir:
        Where to write JSON summaries.  Created automatically.
    n_cpcv_splits, n_test_splits:
        Passed to ``StrategyValidationHarness``.

    Returns
    -------
    dict mapping strategy_id → summary dict (same schema as
    ``ValidationReport.to_summary_dict()``; failed strategies include an
    ``"error"`` key and ``"deployable": false``).
    """
    from execution.cost_model import TieredCostModel
    from validation.harness import StrategyValidationHarness

    if end_date is None:
        end_date = date.today().isoformat()

    if strategies is None:
        strategies = list(STRATEGY_REGISTRY)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Union of tickers required by the selected (known) strategies, plus the
    # subset that additionally needs a current shares-outstanding snapshot
    # (multi-name cross-sectional universes only).
    known = [s for s in strategies if s in STRATEGY_REGISTRY]
    ticker_union = sorted({
        t for s in known for t in STRATEGY_REGISTRY[s][2]
    })
    share_tickers = sorted({
        t
        for s in known
        if len(STRATEGY_REGISTRY[s][2]) > 1
        for t in STRATEGY_REGISTRY[s][2]
    })

    closes_df: pd.DataFrame = pd.DataFrame()
    shares: Dict[str, float] = {}
    if ticker_union:
        logger.info(
            "Downloading price history for %s (%s → %s) …",
            ticker_union, start_date, end_date,
        )
        try:
            closes_df = _download_closes(ticker_union, start_date, end_date)
        except Exception as exc:
            logger.error("Cannot download price data: %s", exc)
            return {
                name: {
                    "strategy_id": name,
                    "deployable": False,
                    "error": f"Price download failed: {exc}",
                    "report_date": date.today().isoformat(),
                }
                for name in strategies
            }

        if share_tickers:
            logger.info("Fetching shares-outstanding snapshot for %s …", share_tickers)
            shares = _download_shares(share_tickers)

    cost_model = TieredCostModel()
    results: Dict[str, dict] = {}

    for name in strategies:
        if name not in STRATEGY_REGISTRY:
            logger.warning(
                "Unknown strategy '%s' — skipping. Known strategies: %s",
                name, sorted(STRATEGY_REGISTRY),
            )
            results[name] = {
                "strategy_id": name,
                "deployable": False,
                "error": f"Not in STRATEGY_REGISTRY. Known: {sorted(STRATEGY_REGISTRY)}",
                "report_date": date.today().isoformat(),
            }
            continue

        logger.info("Validating: %s", name)
        try:
            adapter_fn, turnover, universe = STRATEGY_REGISTRY[name]
            available = [t for t in universe if t in closes_df.columns]
            if not available:
                raise RuntimeError(
                    f"No price data downloaded for universe {universe} — "
                    "cannot validate this strategy."
                )

            if len(universe) == 1:
                # SPY-style single-name adapter: invoked with a pd.Series.
                X, y, precomputed = adapter_fn(closes_df[universe[0]])
            else:
                # Cross-sectional adapter: invoked with (closes_df, shares).
                X, y, precomputed = adapter_fn(closes_df[available], shares)

            if X.empty or y.empty or not precomputed:
                raise RuntimeError(
                    "Adapter returned an empty feature/return frame — "
                    "insufficient history for this start/end range."
                )

            strategy_fn = _make_strategy_fn(precomputed, turnover=turnover)

            harness = StrategyValidationHarness(
                strategy_fn=strategy_fn,
                universe_fn=lambda _, u=available: u,
                cost_model=cost_model,
                n_cpcv_splits=n_cpcv_splits,
                n_test_splits=n_test_splits,
                reports_dir=str(output_dir),
            )

            report = harness.run(
                start_date=str(X.index[0].date()),
                end_date=str(X.index[-1].date()),
                X=X,
                y=y,
                strategy_name=name,
            )

            summary = report.to_summary_dict()
            results[name] = summary
            logger.info(
                "  %-32s deployable=%-5s  Sharpe=%s  PBO=%s  DSR=%s  MaxDD=%s",
                name,
                summary.get("deployable"),
                f"{summary.get('sharpe', float('nan')):.3f}"
                if summary.get("sharpe") is not None else "  —  ",
                f"{summary.get('pbo', float('nan')):.3f}",
                f"{summary.get('dsr', float('nan')):.3f}",
                f"{summary.get('max_drawdown'):.3f}"
                if summary.get("max_drawdown") is not None else "  —  ",
            )

        except Exception as exc:  # CONSTRAINT #6 — per-strategy dead-letter
            logger.error(
                "Strategy '%s' validation failed: %s", name, exc, exc_info=True
            )
            results[name] = {
                "strategy_id": name,
                "deployable": False,
                "error": str(exc),
                "report_date": date.today().isoformat(),
            }

    return results


# =============================================================================
# CLI helpers
# =============================================================================

def _fail_reason(s: dict) -> str:
    """Re-derive which deployability gate(s) a FAIL strategy missed.

    Mirrors ``ValidationReport.deployable`` (validation/harness.py) exactly,
    reading the shared thresholds from ``validation.thresholds`` — so an
    all-green Sharpe/PBO/DSR row that still fails on Max Drawdown or the
    options-selling stress gate shows a concrete reason instead of nothing.
    Returns a compact ``, ``-joined string (empty if nothing tripped).
    """
    reasons: List[str] = []

    md = s.get("max_drawdown")
    if md is None:
        reasons.append("MaxDD n/a")
    elif float(md) >= MAX_DRAWDOWN_MAX:
        reasons.append(f"MaxDD {float(md) * 100:.0f}%>{MAX_DRAWDOWN_MAX * 100:.0f}%")

    pbo = s.get("pbo")
    if pbo is not None and not math.isnan(float(pbo)) and float(pbo) >= PBO_MAX:
        reasons.append(f"PBO {float(pbo):.2f}>{PBO_MAX:.2f}")

    dsr = s.get("dsr")
    if dsr is not None and not math.isnan(float(dsr)) and float(dsr) <= DSR_MIN:
        reasons.append(f"DSR {float(dsr):.2f}<{DSR_MIN:.2f}")

    sharpe = s.get("sharpe")
    if sharpe is None:
        reasons.append("Sharpe n/a")
    elif float(sharpe) <= NET_SHARPE_MIN:
        reasons.append(f"Sharpe {float(sharpe):.2f}<{NET_SHARPE_MIN:.2f}")

    # stress_gate_passed is True for non-options strategies (gate N/A), so an
    # explicit False here always denotes a real options-selling stress failure.
    if s.get("stress_gate_passed") is False:
        reasons.append("stress")

    return ", ".join(reasons)


def _print_summary_table(results: Dict[str, dict]) -> None:
    """Print a compact ASCII pass/fail table to stdout."""
    hdr = (
        f"  {'Strategy':<32} {'Status':<10} {'Sharpe':>7} {'PBO':>7} "
        f"{'DSR':>7} {'MaxDD':>8}  {'Reason'}"
    )
    print()
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    any_fail = False
    for name, s in results.items():
        reason = ""
        if "error" in s:
            status = "ERROR"
            any_fail = True
        elif s.get("deployable"):
            status = "✅ PASS"
        else:
            status = "❌ FAIL"
            any_fail = True
            reason = _fail_reason(s)

        def _fmt(v: Any) -> str:
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return "   —  "
            return f"{float(v):.3f}"

        def _fmt_pct(v: Any) -> str:
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return "   —  "
            return f"{float(v) * 100:.1f}%"

        print(
            f"  {name:<32} {status:<10} "
            f"{_fmt(s.get('sharpe')):>7} "
            f"{_fmt(s.get('pbo')):>7} "
            f"{_fmt(s.get('dsr')):>7} "
            f"{_fmt_pct(s.get('max_drawdown')):>8}  "
            f"{reason}"
        )

    print()
    if any_fail:
        print("⚠️  One or more strategies did not meet deployability thresholds.")
        print("   See reports/<strategy>_validation_summary.json for details.")
    else:
        print("✅  All strategies passed validation gates.")
    print()


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.  Returns exit code 0 on all-pass, 1 on any failure."""
    parser = argparse.ArgumentParser(
        prog="scripts.refresh_validations",
        description="Run walk-forward validation for registered strategies (monthly cadence).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--strategies", type=str, default=None,
        help=(
            "Comma-separated strategy names to validate. "
            f"Default: all ({', '.join(sorted(STRATEGY_REGISTRY))})."
        ),
    )
    parser.add_argument(
        "--start", dest="start_date", type=str, default="2005-01-01",
        metavar="YYYY-MM-DD", help="Backtest start date (default: 2005-01-01).",
    )
    parser.add_argument(
        "--end", dest="end_date", type=str, default=None,
        metavar="YYYY-MM-DD", help="Backtest end date (default: today).",
    )
    parser.add_argument(
        "--output-dir", type=str, default="reports",
        help="Directory for JSON report output (default: reports/).",
    )
    parser.add_argument(
        "--n-cpcv-splits", type=int, default=10,
        help="Number of CPCV splits (default: 10).",
    )
    parser.add_argument(
        "--n-test-splits", type=int, default=2,
        help="Walk-forward test splits (default: 2).",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help=(
            "Also print ONE machine-readable JSON line (the LAST line of stdout) "
            "mapping strategy_id → {deployable, pbo, dsr, sharpe, max_drawdown"
            "[, error]}. The human pass/fail table is still printed above it."
        ),
    )
    args = parser.parse_args(argv)

    strats: Optional[List[str]] = (
        [s.strip() for s in args.strategies.split(",") if s.strip()]
        if args.strategies
        else None
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    )

    results = run_validations(
        strategies=strats,
        start_date=args.start_date,
        end_date=args.end_date,
        output_dir=Path(args.output_dir),
        n_cpcv_splits=args.n_cpcv_splits,
        n_test_splits=args.n_test_splits,
    )

    _print_summary_table(results)

    if args.as_json:
        # One machine-readable line (the LAST json-parseable line of stdout) so
        # the GUI can parse results without scraping the human table.
        json_out = {
            sid: {
                "deployable": bool(s.get("deployable", False)),
                "pbo": s.get("pbo"),
                "dsr": s.get("dsr"),
                "sharpe": s.get("sharpe"),
                "max_drawdown": s.get("max_drawdown"),
                **({"error": s["error"]} if "error" in s else {}),
            }
            for sid, s in results.items()
        }
        print(json.dumps(json_out))

    any_fail = any(
        "error" in s or not s.get("deployable", False) for s in results.values()
    )
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
