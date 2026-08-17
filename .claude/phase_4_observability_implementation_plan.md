# Implementation Plan: Phase 4 — Observability, Metrics & Calibration

## Goal
Verify and guarantee mission-control telemetry, Brier score calibration, forecast tracking, and Prometheus metrics export across `pilots/observability.py` and `calibration.py`.

## Key Verification & Systems
1. **Mission-Control Composite Read (`GET /observability/summary`)**:
   - Portfolio risk metrics (Sharpe, Calmar, MaxDD, CAGR).
   - Portfolio heat against `MAX_PORTFOLIO_HEAT`.
   - Running peak-to-trough drawdown and macro-regime overlay.
   - Portfolio-wide and per-symbol forecast skill metrics with inverse-RMSE weighting.
   - Risk gate block logs and circuit breaker dashboard aggregation.
   - Host/process system telemetry and quote fetch latency tracking.
2. **Forecast Reliability Curves & Probability Calibration**:
   - Bin-level empirical win rates and Brier score calibration.
   - RLHF calibration store proposal and review aggregation.
3. **AST Safety & Zero Heavy Engine Leaks**:
   - Confirm zero imports of forbidden heavy compute engines (`processing_engine`, `technical_options_engine`, `strategy_engine`).

## Verification Plan
- `pytest tests/test_pilots_observability.py tests/test_observability_telemetry.py tests/test_calibration.py tests/test_pilots_calibration.py tests/test_rlhf_calibration_store.py tests/test_forecast_tracker.py -v`.
- Bandit SAST scan.
