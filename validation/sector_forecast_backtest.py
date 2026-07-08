"""Empirical walk-forward backtest for the per-sector forecast-model heuristic.

Replaces the hand-picked ``ForecastingEngine.sector_configs`` heuristic
(``forecasting_engine.py`` lines ~72-85) with a data-driven measurement of
which (model, horizon) combination actually performs best per sector,
via a bespoke per-symbol *expanding-window* walk-forward backtest.

Design notes (see CLAUDE.md / task brief for the full rationale):

* CNN-LSTM is deliberately excluded from the grid — it is not a selectable
  ``model`` value in the existing heuristic, and pulling in TensorFlow would
  break offline determinism for this backtest. ``SECTOR_MODELS`` (imported
  from ``validation.sector_forecast_types``) is the full, frozen model grid:
  ``("MC", "ARIMA", "HW")``.
* No CPCV / purged cross-validation here — this is deliberately a simpler,
  bespoke expanding-window walk-forward over point forecasts, which is
  trivially provably lookahead-free (every model fit only ever sees
  ``Close[:t]``, strictly excluding the anchor index ``t`` itself).
  ``validation/purged_cv.py`` is not used.
* Dead-letter resilience throughout: one bad symbol/model/horizon
  combination, or one bad anchor within a symbol's walk-forward, must never
  abort the whole backtest (mirrors this codebase's established
  per-ticker try/except convention — see CLAUDE.md).
* A model's own sentinel failure value (``0.0`` from ``run_arima`` /
  ``run_holt_winters_grid_search`` on insufficient history or a fit
  exception) must never be recorded as a spurious ForecastError — it is
  filtered out before it can pollute MASE/RMSE.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

import numpy as np
import pandas as pd

from validation.forecast_accuracy_metrics import (
    mase,
    naive_one_step_mae,
    rmse_from_errors,
)
from validation.sector_forecast_types import BacktestConfig, CellResult, ForecastError

logger = logging.getLogger("SectorForecastBacktest")


def _forecast_one(
    engine,
    model: str,
    train_close: pd.Series,
    start_price: float,
    horizon: int,
) -> float:
    """Dispatch a single point forecast to ``engine``'s public methods.

    Mirrors ``ForecastingEngine.generate_forecast``'s own model dispatch.
    Dead-letter: any exception (or an unrecognized ``model`` value) degrades
    to ``0.0`` — the same sentinel-failure convention ``run_arima`` /
    ``run_holt_winters_grid_search`` already use, so callers filter it out
    identically regardless of which model produced it.
    """
    try:
        if model == "MC":
            log_returns = np.log(train_close / train_close.shift(1)).dropna()
            if log_returns.empty:
                return 0.0
            mu = float(log_returns.mean())
            sigma = float(log_returns.std())
            mean_price, _p5, _p95 = engine.run_monte_carlo(
                start_price, mu, sigma, horizon
            )
            return float(mean_price)
        elif model == "ARIMA":
            return float(engine.run_arima(train_close.values, days_forward=horizon))
        elif model == "HW":
            return float(engine.run_holt_winters_grid_search(train_close, horizon))
        else:
            logger.debug("Unrecognized model %r in _forecast_one; returning 0.0", model)
            return 0.0
    except Exception as exc:  # dead-letter: one bad anchor must never abort the backtest
        logger.debug("_forecast_one(model=%s, horizon=%s) failed: %s", model, horizon, exc)
        return 0.0


def _walk_forward_symbol(
    prices: pd.DataFrame,
    engine,
    model: str,
    horizon: int,
    config: BacktestConfig,
) -> list[ForecastError]:
    """Expanding-window walk-forward over one symbol's ``Close`` series.

    For each anchor index ``t`` (stepped by ``config.step_days``), the
    training window is ``Close[max(0, t - lookback_days) : t]`` — strictly
    excluding index ``t`` itself, so the model never sees the anchor price
    or anything at/after it. The realized outcome is ``Close[t + horizon]``,
    which must actually exist in the series or the anchor is skipped.

    Anchors whose model produces a non-finite or non-positive forecast
    (the models' own sentinel failure value, ``0.0``, or any other
    degenerate output) are skipped rather than recorded as a fabricated
    error observation.
    """
    errors: list[ForecastError] = []

    if "Close" not in prices.columns:
        return errors

    close = prices["Close"].astype(float)
    n = len(close)
    if n < config.min_train_bars:
        return errors

    # Anchors must have >= min_train_bars of history strictly before them,
    # and an actual close price at t + horizon.
    first_anchor = config.min_train_bars
    last_anchor = n - horizon - 1
    if last_anchor < first_anchor:
        return errors

    for t in range(first_anchor, last_anchor + 1, max(1, config.step_days)):
        try:
            train_start = max(0, t - config.lookback_days)
            train = close.iloc[train_start:t]  # strictly excludes index t
            if len(train) < config.min_train_bars:
                continue

            start_price = float(close.iloc[t])
            y_true = float(close.iloc[t + horizon])
            if not np.isfinite(start_price) or not np.isfinite(y_true):
                continue

            y_pred = _forecast_one(engine, model, train, start_price, horizon)
            if not np.isfinite(y_pred) or y_pred <= 0:
                # Model sentinel failure (0.0) or degenerate output — never
                # fabricate an error observation from it.
                continue

            naive_scale = naive_one_step_mae(train.values)
            errors.append(
                ForecastError(y_true=y_true, y_pred=y_pred, naive_scale=naive_scale)
            )
        except Exception as exc:  # dead-letter: one bad anchor never aborts the symbol
            logger.debug(
                "Walk-forward anchor t=%s (model=%s, horizon=%s) failed: %s",
                t, model, horizon, exc,
            )
            continue

    return errors


def _group_symbols_by_sector(
    ticker_sectors: Mapping[str, str],
    price_data: Mapping[str, pd.DataFrame],
) -> dict[str, list[str]]:
    """Union of symbols per sector, restricted to symbols that have price data."""
    by_sector: dict[str, list[str]] = {}
    for symbol, sector in ticker_sectors.items():
        if symbol not in price_data:
            continue
        by_sector.setdefault(sector, []).append(symbol)
    return by_sector


def run_sector_backtest(
    price_data: Mapping[str, pd.DataFrame],
    ticker_sectors: Mapping[str, str],
    engine,
    config: BacktestConfig = BacktestConfig(),
) -> list[CellResult]:
    """Expanding-window walk-forward over every (sector, model, horizon) cell.

    Groups symbols by sector (only sectors present in ``ticker_sectors``'s
    values that also have an entry in ``price_data``), accumulates
    ``ForecastError`` observations across all symbols/anchors in the cell,
    then reduces to one ``CellResult`` per cell via ``mase()`` /
    ``rmse_from_errors()``.

    Dead-letter resilient: a symbol/model/horizon combination that raises is
    logged and skipped, never aborting the rest of the grid. A cell with
    zero accumulated observations still produces a ``CellResult`` with
    ``mase=nan, rmse=nan, n_forecasts=0, n_symbols=0`` — never fabricated,
    never a crash.
    """
    sector_symbols = _group_symbols_by_sector(ticker_sectors, price_data)
    results: list[CellResult] = []

    for sector, symbols in sector_symbols.items():
        for model in config.models:
            for horizon in config.horizons:
                cell_errors: list[ForecastError] = []
                contributing_symbols = 0
                for symbol in symbols:
                    try:
                        prices = price_data[symbol]
                        symbol_errors = _walk_forward_symbol(
                            prices, engine, model, horizon, config
                        )
                    except Exception as exc:  # dead-letter per symbol/model/horizon
                        logger.warning(
                            "Sector backtest cell (sector=%s, model=%s, horizon=%s) "
                            "failed for symbol=%s: %s",
                            sector, model, horizon, symbol, exc,
                        )
                        continue
                    if symbol_errors:
                        cell_errors.extend(symbol_errors)
                        contributing_symbols += 1

                results.append(
                    CellResult(
                        sector=sector,
                        model=model,
                        horizon=horizon,
                        mase=mase(cell_errors),
                        rmse=rmse_from_errors(cell_errors),
                        n_forecasts=len(cell_errors),
                        n_symbols=contributing_symbols,
                    )
                )

    return results
