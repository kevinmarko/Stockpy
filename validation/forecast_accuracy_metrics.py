"""Forecast-accuracy metrics: MASE (primary) and RMSE (tiebreak).

Pure numpy, no pandas dependency at call time. Used by
``sector_forecast_backtest.py`` to score each (sector, model, horizon) cell,
and by ``sector_config_io.derive_sector_configs`` to rank cells within a
sector. No sector/model knowledge lives here — this module only knows about
arrays of realized errors.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from validation.sector_forecast_types import ForecastError

# Floor for the MASE naive-baseline denominator — avoids divide-by-zero on a
# perfectly flat training window without materially distorting real scores.
_MIN_SCALE = 1e-9


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Root mean squared error. NaNs are dropped pairwise; empty -> nan."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    if y_true.shape != y_pred.shape:
        raise ValueError(f"shape mismatch: y_true{y_true.shape} vs y_pred{y_pred.shape}")
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    if not np.any(mask):
        return float("nan")
    diff = y_true[mask] - y_pred[mask]
    return float(np.sqrt(np.mean(diff ** 2)))


def naive_one_step_mae(train_prices: np.ndarray) -> float:
    """Mean absolute one-step price change over the TRAINING window only:
    ``mean(|p[t] - p[t-1]|)``. This is the classic Hyndman in-sample,
    drift-free random-walk naive scale used as the MASE denominator.

    Computed strictly from training data (no leakage). Floored at
    ``_MIN_SCALE`` to avoid divide-by-zero on a flat/degenerate window.
    """
    prices = np.asarray(train_prices, dtype=float)
    prices = prices[np.isfinite(prices)]
    if prices.size < 2:
        return _MIN_SCALE
    diffs = np.abs(np.diff(prices))
    if diffs.size == 0:
        return _MIN_SCALE
    scale = float(np.mean(diffs))
    return scale if scale > _MIN_SCALE else _MIN_SCALE


def mase(errors: Sequence[ForecastError]) -> float:
    """Scaled error pooled across observations.

    ``MASE = mean_i( |y_true_i - y_pred_i| / naive_scale_i )``.

    Each observation carries its own anchor-local naive scale (computed from
    that anchor's training window only), so the pooled MASE is unit-free and
    comparable across symbols, sectors, and horizons. A perfect forecast
    yields 0; a forecast no better than the naive random walk yields ~1.
    Empty input -> nan (never fabricated).
    """
    if not errors:
        return float("nan")
    scaled = [
        abs(e.y_true - e.y_pred) / (e.naive_scale if e.naive_scale > _MIN_SCALE else _MIN_SCALE)
        for e in errors
        if np.isfinite(e.y_true) and np.isfinite(e.y_pred)
    ]
    if not scaled:
        return float("nan")
    return float(np.mean(scaled))


def rmse_from_errors(errors: Sequence[ForecastError]) -> float:
    """Convenience: RMSE over the (y_true, y_pred) pairs of a ForecastError list."""
    if not errors:
        return float("nan")
    y_true = np.array([e.y_true for e in errors], dtype=float)
    y_pred = np.array([e.y_pred for e in errors], dtype=float)
    return rmse(y_true, y_pred)
