# Task tracker: LOB simulator theta_market calibration

Branch: `fully-fix-lob-theta-calibration`

- [x] Stage 1: `AlpacaProvider.get_intraday_trade_counts` + `CompositeProvider.get_intraday_trade_counts` + settings.
- [x] Stage 2: `estimate_calibrated_theta_market` + `simulate_queue_fill` wiring + API request-model default change.
- [x] Stage 3: `LobDepthView.tsx` + `types.ts` honest disclosure.
- [x] Stage 4: docstrings + `docs/architecture/execution.md` + known-issues doc rewrite + README row + settings descriptions.
- [x] Stage 5: full test/typecheck verification pass (379 passed, typecheck clean).
- [x] Stage 6: adversarial CONSTRAINT #4/#6 audit — found and fixed one real disclosed gap (extended-hours bar mixing) plus one stale/self-contradictory doc claim.
- [x] Independently re-verified myself (not just trusting subagent self-reports): re-ran full test matrix (379 passed) and read `simulate_queue_fill`/`estimate_calibrated_theta_market` directly to confirm the wiring.
- [x] Commit, push, open PR.
