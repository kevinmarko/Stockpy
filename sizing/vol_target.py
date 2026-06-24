"""
InvestYo Quant Platform - Volatility Targeting
=================================================
Sizes a position (or portfolio) so that its expected realized volatility
matches a target level, instead of an arbitrary score-derived allocation.
"""

import logging
import math
from typing import Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def volatility_target_weight(
    realized_vol: float,
    target_vol: float = 0.10,
    max_leverage: float = 2.0,
) -> float:
    """Single-asset volatility-target position weight.

    weight = target_vol / realized_vol, capped at ``max_leverage`` and
    floored at 0.0. A higher-volatility asset gets a smaller weight; a
    lower-volatility asset gets a larger weight (up to the leverage cap).

    Parameters
    ----------
    realized_vol : float
        Annualized realized (or forecasted, e.g. GARCH) volatility of the
        asset. Must be computed from data strictly prior to the current bar
        by the caller -- this function performs no lookahead checks itself.
    target_vol : float
        Desired annualized portfolio/position volatility (default 10%).
    max_leverage : float
        Upper bound on the resulting weight (default 2.0x).

    Returns
    -------
    float
        NaN if ``realized_vol`` is NaN/None (undefined -- caller must decide
        a fallback). ``max_leverage`` if ``realized_vol`` is non-positive
        (degenerate zero-vol case: there is no observed risk to size against,
        so we saturate at the leverage cap rather than divide by zero).
    """
    if realized_vol is None or (isinstance(realized_vol, float) and math.isnan(realized_vol)):
        return float("nan")

    if realized_vol <= 0:
        logger.warning(
            "volatility_target_weight: non-positive realized_vol=%.6f; "
            "saturating at max_leverage=%.2f.", realized_vol, max_leverage
        )
        return float(max_leverage)

    weight = target_vol / realized_vol
    return float(max(0.0, min(max_leverage, weight)))


def portfolio_vol_target(
    positions: Dict[str, float],
    cov_matrix: pd.DataFrame,
    target_vol: float = 0.10,
    max_leverage: float = 2.0,
) -> Dict[str, float]:
    """Scales a raw position vector so total portfolio volatility hits target_vol.

    portfolio_vol = sqrt(w^T * Sigma * w); scalar = target_vol / portfolio_vol
    (capped at max_leverage); every position is multiplied by that one scalar
    so relative weights between symbols are preserved.

    Parameters
    ----------
    positions : dict[str, float]
        Raw (pre-scaling) signal weights keyed by symbol.
    cov_matrix : pd.DataFrame
        Covariance matrix of asset returns, square, indexed and columned by
        symbol. Must be computed strictly from data prior to the current bar.
    target_vol : float
        Desired annualized portfolio volatility.
    max_leverage : float
        Upper bound on the scaling factor applied to every position.

    Returns
    -------
    dict[str, float]
        Scaled weights for every symbol in ``positions``. Symbols missing
        from ``cov_matrix`` are excluded from the volatility calculation and
        explicitly set to 0.0 (not fabricated -- logged as a warning) since
        their risk cannot be estimated.
    """
    symbols = [s for s in positions if s in cov_matrix.index and s in cov_matrix.columns]
    missing = sorted(set(positions) - set(symbols))
    if missing:
        logger.warning(
            "portfolio_vol_target: symbols missing from cov_matrix, excluded "
            "with weight 0.0: %s", missing
        )

    if not symbols:
        return {s: 0.0 for s in positions}

    w = np.array([positions[s] for s in symbols], dtype=float)
    sub_cov = cov_matrix.loc[symbols, symbols].to_numpy(dtype=float)
    portfolio_variance = float(w @ sub_cov @ w)
    portfolio_vol = math.sqrt(max(portfolio_variance, 0.0))

    if portfolio_vol <= 0 or math.isnan(portfolio_vol):
        logger.warning(
            "portfolio_vol_target: non-positive/undefined portfolio_vol=%.6f; "
            "saturating scalar at max_leverage=%.2f.", portfolio_vol, max_leverage
        )
        scalar = float(max_leverage)
    else:
        scalar = float(max(0.0, min(max_leverage, target_vol / portfolio_vol)))

    scaled = {s: positions[s] * scalar for s in symbols}
    for s in missing:
        scaled[s] = 0.0
    return scaled
