"""Shared contract for the empirical per-sector forecast config backtest.

Frozen dataclasses and constants used by every module in the sector-forecast
backtest pipeline (``forecast_accuracy_metrics.py``, ``sector_forecast_backtest.py``,
``sector_config_io.py``, ``scripts/backtest_sector_configs.py``) and by the
runtime loader in ``forecasting_engine.py``. This module is the single seam
shared across the pipeline — it defines the data shapes once so the backtest
runner, the derivation/artifact-I/O layer, and the runtime engine all agree on
them without redefining anything locally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

# The three forecast models selectable as a sector's "preferred_model" in the
# heuristic (CNN-LSTM is intentionally excluded — see sector_forecast_backtest.py).
SECTOR_MODELS: tuple[str, ...] = ("MC", "ARIMA", "HW")

# Horizon grid matches the heuristic's "days" values exactly (30/60/90).
DEFAULT_HORIZONS: tuple[int, ...] = (30, 60, 90)


@dataclass(frozen=True)
class ForecastError:
    """One realized point-forecast observation.

    ``naive_scale`` is the in-sample one-step random-walk naive MAE computed
    from the training window used to produce ``y_pred`` — the MASE
    denominator for this observation. Always > 0 (floored upstream).
    """

    y_true: float
    y_pred: float
    naive_scale: float


@dataclass(frozen=True)
class BacktestConfig:
    """Parameters governing the expanding-window walk-forward backtest."""

    horizons: tuple[int, ...] = DEFAULT_HORIZONS
    models: tuple[str, ...] = SECTOR_MODELS
    lookback_days: int = 750
    min_train_bars: int = 120
    step_days: int = 21
    embargo_days: int = 5


@dataclass(frozen=True)
class CellResult:
    """Aggregated accuracy for one (sector, model, horizon) cell."""

    sector: str
    model: str
    horizon: int
    mase: float
    rmse: float
    n_forecasts: int
    n_symbols: int


class SectorConfigEntry(TypedDict):
    """Shape of a single sector's config entry — identical to today's
    hardcoded ``ForecastingEngine.sector_configs`` value shape."""

    days: int
    model: str


class ForecastArtifact(TypedDict):
    """Shape of the committed ``forecasting/sector_configs.json`` artifact."""

    schema_version: int
    generated_at: str
    backtest: dict
    sector_configs: dict[str, SectorConfigEntry]
    grid: list[dict]
