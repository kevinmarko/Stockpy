"""
InvestYo Quant Platform - Point-in-Time Feature Engineering for LightGBM Ranker
================================================================================
Builds the cross-sectional feature matrix used to train / score the
LGBMCrossSectionalRanker.  Every feature is strictly point-in-time: it is derived
from columns that are already .shift()-safe in the dashboard DataFrame (they
represent information known *before* the current bar closes).

Reference: Lopez de Prado AFML Ch. 13 (ML for Asset Management).
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("ML.FeatureEngineering")

# ──────────────────────────────────────────────────────────────────────────────
# Feature column specs
# All raw column names are the internal keys written by processing_engine /
# strategy_engine into the dashboard_df.
# ──────────────────────────────────────────────────────────────────────────────
_MOMENTUM_COLS = ["ROC_12M", "ROC_6M"]
_VOL_COLS = ["GARCH_Vol"]
_MEAN_REVERSION_COLS = ["RSI", "RSI_2"]
_FUNDAMENTAL_COLS = ["book_to_market", "earnings_yield", "quality_factor_score", "low_vol_score"]
_MACRO_COLS = ["vix_level"]     # scalar context, tiled across cross-section
_FACTOR_COLS = ["Value_Z", "Quality_Z", "LowVol_Z", "Size_Z"]

# Full ordered list used as the canonical feature order for model training.
FEATURE_COLUMNS = (
    _MOMENTUM_COLS
    + _VOL_COLS
    + _MEAN_REVERSION_COLS
    + _FUNDAMENTAL_COLS
    + _MACRO_COLS
    + _FACTOR_COLS
    + [
        # Cross-sectional percentile ranks (added by build_pit_feature_matrix)
        "ROC_12M_rank",
        "ROC_6M_rank",
        "GARCH_Vol_rank",
        "RSI_rank",
        "RSI_2_rank",
        "book_to_market_rank",
        "earnings_yield_rank",
        "quality_factor_score_rank",
        "low_vol_score_rank",
    ]
)


def _percentile_rank(series: pd.Series) -> pd.Series:
    """Cross-sectional percentile rank in [0, 1], NaN-safe."""
    valid = series.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=series.index)
    ranked = valid.rank(pct=True)
    return ranked.reindex(series.index)


def compute_multifactor_zscores(universe_df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional Value/Quality/LowVol/Size Z-scores for a single as-of date.

    Mirrors ``signals/multifactor.py::MultifactorSignal.pre_compute``'s EXACT
    formula (same ``_zscore_winsorize`` primitive, same microcap exclusion,
    same value/size/composite combination) so a model trained on this offline
    panel sees the identical Z-score distribution the live pipeline scores
    tickers against at inference time -- a formula drift here would be a
    silent train/serve skew bug. Keep this in sync with
    ``MultifactorSignal.pre_compute`` if that formula ever changes.

    Parameters
    ----------
    universe_df:
        Indexed by ticker (one row per ticker), for a SINGLE as-of date.
        Expected columns: ``book_to_market``, ``earnings_yield``,
        ``quality_factor_score``, ``low_vol_score``, ``log_market_cap``,
        ``market_cap`` (raw dollar figure, used only for the microcap
        threshold check). Any missing column is treated as all-NaN --
        never fabricated.

    Returns
    -------
    pd.DataFrame indexed by ticker, columns
    ``["Value_Z", "Quality_Z", "LowVol_Z", "Size_Z", "Multifactor_Composite"]``.
    A ticker below ``settings.MULTIFACTOR_MICROCAP_THRESHOLD`` (or with no
    market cap at all) gets all-NaN, matching the live pipeline's
    microcap-exclusion behavior exactly.
    """
    # Lazy imports: keeps this module's import-time cost light for callers
    # (e.g. signals/lgbm_ranker.py's live pre_compute hook) that never need
    # the multifactor path, and avoids a hard settings/-signals import at
    # module load for the many offline tests that only exercise price-based
    # features.
    from settings import settings
    from signals.multifactor import WINSOR_LIMIT, _zscore_winsorize

    cols = ["Value_Z", "Quality_Z", "LowVol_Z", "Size_Z", "Multifactor_Composite"]
    if universe_df.empty:
        return pd.DataFrame(columns=cols)

    required = ["book_to_market", "earnings_yield", "quality_factor_score", "low_vol_score", "log_market_cap"]
    df = universe_df.copy()
    for col in required + ["market_cap"]:
        if col not in df.columns:
            df[col] = np.nan

    market_cap = df["market_cap"]
    is_microcap = market_cap.fillna(0.0) < settings.MULTIFACTOR_MICROCAP_THRESHOLD
    eligible = df.loc[~is_microcap]

    b2m_z = _zscore_winsorize(eligible["book_to_market"])
    ey_z = _zscore_winsorize(eligible["earnings_yield"])
    quality_z = _zscore_winsorize(eligible["quality_factor_score"])
    lowvol_z = _zscore_winsorize(eligible["low_vol_score"])
    size_z_raw = _zscore_winsorize(eligible["log_market_cap"])

    value_z = pd.concat([b2m_z, ey_z], axis=1).mean(axis=1, skipna=True)
    size_z = -size_z_raw
    composite = pd.concat(
        [value_z, quality_z, lowvol_z, size_z], axis=1
    ).mean(axis=1, skipna=True).clip(lower=-WINSOR_LIMIT, upper=WINSOR_LIMIT)

    out = pd.DataFrame(np.nan, index=df.index, columns=cols)
    out.loc[eligible.index, "Value_Z"] = value_z
    out.loc[eligible.index, "Quality_Z"] = quality_z
    out.loc[eligible.index, "LowVol_Z"] = lowvol_z
    out.loc[eligible.index, "Size_Z"] = size_z
    out.loc[eligible.index, "Multifactor_Composite"] = composite
    return out


def build_pit_feature_matrix(
    universe_df: pd.DataFrame,
    as_of_date: Optional[pd.Timestamp] = None,
    macro_vix: Optional[float] = None,
) -> pd.DataFrame:
    """Return a (n_tickers × n_features) DataFrame of point-in-time features.

    Parameters
    ----------
    universe_df:
        Dashboard DataFrame indexed by ticker (one row per ticker), containing
        the columns produced by the processing / signal engines.  All values
        must already represent information available *before* ``as_of_date``.
    as_of_date:
        Optional label attached to the returned DataFrame for traceability;
        not used in any calculation.
    macro_vix:
        Current VIX level (scalar).  Tiled across all tickers so the model
        can condition on macro context.  If None, VIX column is NaN.

    Returns
    -------
    pd.DataFrame indexed by ticker, columns = FEATURE_COLUMNS.
    """
    n = len(universe_df)
    if n == 0:
        return pd.DataFrame(columns=FEATURE_COLUMNS)

    rows: dict[str, pd.Series] = {}

    # ── raw features ──────────────────────────────────────────────────────────
    for col in _MOMENTUM_COLS + _VOL_COLS + _MEAN_REVERSION_COLS + _FUNDAMENTAL_COLS + _FACTOR_COLS:
        if col in universe_df.columns:
            rows[col] = universe_df[col].astype(float)
        else:
            rows[col] = pd.Series(np.nan, index=universe_df.index)

    # macro context: scalar tiled across tickers
    rows["vix_level"] = pd.Series(
        float(macro_vix) if macro_vix is not None else np.nan,
        index=universe_df.index,
    )

    # ── cross-sectional percentile ranks ──────────────────────────────────────
    rankable = [
        "ROC_12M", "ROC_6M", "GARCH_Vol", "RSI", "RSI_2",
        "book_to_market", "earnings_yield", "quality_factor_score", "low_vol_score",
    ]
    for col in rankable:
        rows[f"{col}_rank"] = _percentile_rank(rows[col])

    feat_df = pd.DataFrame(rows, index=universe_df.index)[FEATURE_COLUMNS]

    if as_of_date is not None:
        feat_df.attrs["as_of_date"] = as_of_date

    return feat_df


def build_forward_return_ranks(
    price_history: pd.DataFrame,
    as_of_dates: pd.DatetimeIndex,
    horizon_days: int = 21,
) -> pd.DataFrame:
    """Build cross-sectional forward-return rank percentiles for supervised training.

    Parameters
    ----------
    price_history:
        Wide DataFrame of adjusted close prices — columns = tickers, index = dates.
    as_of_dates:
        The training dates for which we want forward-return ranks.
    horizon_days:
        Number of calendar days ahead to measure the forward return.

    Returns
    -------
    pd.DataFrame indexed by as_of_date, columns = tickers, values = rank ∈ [0,1].
    NaN for a ticker on a date where we cannot compute a forward return
    (e.g. near the end of history).
    """
    result_rows = {}
    for dt in as_of_dates:
        try:
            loc = price_history.index.get_loc(dt)
        except KeyError:
            continue
        future_loc = loc + horizon_days
        if future_loc >= len(price_history):
            continue
        p0 = price_history.iloc[loc]
        p1 = price_history.iloc[future_loc]
        fwd_ret = (p1 - p0) / p0.replace(0, np.nan)
        result_rows[dt] = fwd_ret.rank(pct=True)

    if not result_rows:
        return pd.DataFrame(columns=price_history.columns)

    return pd.DataFrame(result_rows).T
