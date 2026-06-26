"""
forecasting/ — Forecast skill tracking for the InvestYo Quant Platform.

Modules
-------
forecast_tracker
    SQLite-backed per-model RMSE tracker.  Records ARIMA / Monte Carlo /
    Holt-Winters / CNN-LSTM forecast prices, updates them with actuals once
    the horizon elapses, and returns normalized inverse-RMSE weights for
    skill-weighted ensemble blending (Tier 2.2).
"""

from forecasting.forecast_tracker import ForecastTracker  # noqa: F401
