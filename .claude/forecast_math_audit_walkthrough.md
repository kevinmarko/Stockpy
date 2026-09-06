# Forecast & LLM math audit — walkthrough

This walkthrough documents the fixes applied to the forecasting subsystem to resolve multiple structural and math correctness issues, specifically focusing on the Monte Carlo drift simulation, lookahead purging, forecast accuracy measurement, and honest degradation paths.

## Key Changes
1. **Monte Carlo Drift**: Removed the double Itô correction on the log-return mean. The MC simulation now correctly applies `mu * dt`. The test suite was rewritten to verify the theoretical mean and median.
2. **Purged Splits**: Corrected the embargo window size across `forecasting_engine.py` and `cnn_lstm_worker.py` to correctly avoid lookahead leakage from `max_h`.
3. **Forecast Horizon Scoring**: Fixed the evaluation of forecast actuals to use trading bars rather than calendar days. Created a historic repair script to correct past records without discarding them.
4. **Honest Degradation**: Removed fabricated fallbacks across Holt-Winters, options meta-labeler, and transformer vol forecaster, properly propagating `None` or `NaN` to reflect missing data instead of synthetic values.
5. **Measurement Layer**: Introduced a `naive` pseudo-model to `ForecastTracker` for baseline skill comparison and implemented coverage tracking. Added `CalibratedClassifierCV` for the options meta-labeler.
6. **Documentation**: Adjusted architecture documentation and `CLAUDE.md` to reflect the fixed behavior and document the F1 numeric corrections.
