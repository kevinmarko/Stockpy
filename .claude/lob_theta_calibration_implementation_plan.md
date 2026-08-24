# Implementation Plan: LOB simulator — wire real theta_market calibration

Branch: `fully-fix-lob-theta-calibration` (off `main` post-#904/#908,
including the already-merged `sor_lob_audit_fixes_5_6` fix).

## Context

Follow-up to `docs/known_issues/lob_simulator_uncalibrated_live_arrival_rates.md`
(status: disclosed-not-fixed). Discovered `alpaca-py`'s `Bar` model carries a
real, exchange-reported `trade_count` field that `AlpacaProvider.get_intraday_bars()`
currently discards — a genuine, non-fabricated lever for calibrating
`theta_market` (market-order Poisson arrival rate). Neither FMP nor yfinance
carry an equivalent field (checked both). `lambda_limit`/`mu_cancel` remain
structurally uncalibratable with any data source in this codebase — no
change to that conclusion.

## Scope

1. `data/market_data.py` — `AlpacaProvider.get_intraday_trade_counts()` +
   `CompositeProvider.get_intraday_trade_counts()` (honest tuple-return,
   never raises) + 3 new settings.
2. `pilots/lob_simulator.py` + `api/pilots_api.py` — `estimate_calibrated_theta_market()`,
   `LobSimulateQueueRequest.theta_market` default `5.0 -> None`,
   `simulate_queue_fill()` wiring + response honesty fields.
3. `webapp/src/components/options/LobDepthView.tsx` + `types.ts` — honest
   calibration-status disclosure, mirroring `VpinGauge.tsx`.
4. Docs — module docstring, `docs/architecture/execution.md`, rewrite
   `docs/known_issues/lob_simulator_uncalibrated_live_arrival_rates.md` +
   README row, `settings.py` field descriptions.
5. Full verification — `pytest tests/test_market_data.py tests/test_lob_simulator.py
   tests/test_pilots_paper_broker.py -q`, `npm run --prefix webapp typecheck`.
6. Adversarial CONSTRAINT #4/#6 audit of the complete diff.

Executed as a 6-stage sequential `Workflow` run (user's explicit request).

## Verification

```bash
python3 -m pytest tests/test_market_data.py tests/test_lob_simulator.py tests/test_pilots_paper_broker.py -q
npm run --prefix webapp typecheck
```
