# Strategy Report Card + `strategy_id` Vocabulary Standardization

This plan implements the Strategy Report Card and standardizes `strategy_id` across the platform to use `pilots/catalog.py`'s `Pilot.id` scheme, addressing the known gap where paper trades use inconsistent free-text labels.

## §0 Dependency Check
- `pilots/catalog.py` has a `Pilot` dataclass.
- `scripts.refresh_validations.STRATEGY_REGISTRY` is the validation join source.
- `paper_broker_options_order.py` and execution engines will need updates.
- No changes to `main_pipeline` (ensemble decision) or `advisory` (composed bucket).

## User Review Required
No breaking changes to live ordering (as per CONSTRAINT #1). This strictly affects attribution and reporting (read-only layers). We map old string labels to new Pilot IDs dynamically via `LEGACY_STRATEGY_ID_ALIASES` for historical queries.

## Open Questions
None. The plan is prescriptive and well-defined in the `master_preprompt`.

## Proposed Changes

---

### Part A — `strategy_id` vocabulary standardization

#### [MODIFY] pilots/catalog.py
- Add `followable: bool = True` to the `Pilot` dataclass.
- Add `"Options"` to the category docstring allowed list.
- Append 10 new `Pilot` entries (`earnings-crush`, `dispersion-trading`, `zero-dte-momentum-breakout`, `copula-stat-arb`, `put-credit-spread`, `call-credit-spread`, `call-debit-spread`, `put-debit-spread`, `covered-call`, `iron-condor`) with `category="Options"`, `weights={}`, `followable=False`.
- Add `OPTIONS_DIRECTIVE_STRATEGY_TO_PILOT_ID` mapping dict.

#### [MODIFY] tests/test_pilots_catalog.py
- Update `test_weights_keys_are_real_signal_modules` to skip `followable=False` pilots before asserting `p.weights`.
- Update `test_categories_are_known` to include `"Options"`.
- Add test asserting every `followable=False` Pilot has `weights == {}` and non-empty description.
- Add test asserting `OPTIONS_DIRECTIVE_STRATEGY_TO_PILOT_ID` maps 6 known directives to real Pilot IDs.

#### [MODIFY] execution/options_paper_executor.py
- Resolve `strategy_id` through `OPTIONS_DIRECTIVE_STRATEGY_TO_PILOT_ID`.
- Update post-earnings auto-close reconciliation query to `in_(["Earnings Crush", "earnings-crush"])`.

#### [MODIFY] pilots/dispersion_trading.py
- Set `strategy_id = "dispersion-trading"` and pass to `apply_multi_leg_fill`.

#### [MODIFY] pilots/copula_stat_arb.py
- Set `strategy_id = "copula-stat-arb"` and pass to `apply_multi_leg_fill`.
- Update tests to check `strategy_id`.

#### [MODIFY] pilots/zero_dte_engine.py
- Change literal `"0DTE Momentum Breakout"` to `"zero-dte-momentum-breakout"`.
- Update tests to check `strategy_id`.

#### [MODIFY] pilots/paper_broker_options_order.py
- Add `strategy_id="Manual Trade"` to the multi-leg manual-order path's `apply_multi_leg_fill(...)` call.
- Add a regression test.

#### [MODIFY] api/pilots_api.py & webapp/src/api/types.ts & webapp/src/screens/PilotDetail.tsx & webapp/src/screens/Comparison.tsx
- Add `"followable": pilot.followable` to serialization and frontend types.
- Gate Follow CTA on `pilot.followable`.
- Update `mock.ts`.

---

### Part B — Strategy Report Card

#### [NEW] pilots/strategy_report_card.py
- Add `LEGACY_STRATEGY_ID_ALIASES`.
- Implement `_normalize_strategy_id(raw)`, `_predicted_side(pilot, db_latest)`, `_actual_side(trades)` (with `MIN_TRADES_FOR_VERDICT = 10`), and `strategy_report_card_rows()`. Follow the degrade-to-`{}`-with-`reason` pattern.

#### [NEW] tests/test_pilots_strategy_report_card.py
- Cover real Pilot with both sides, null predicted, zero-paper-trade, non-Pilot bucket, legacy string, `Follow:x` row, and the n=9 vs n=10 honesty floor boundary.

#### [MODIFY] tests/test_pilots_strategy_matrix.py
- Add `strategy_report_card` to the allowlist.

#### [MODIFY] api/pilots_api.py & tests/test_pilots_api.py
- Add `GET /strategy/report-card` endpoint.
- Add `TestStrategyReportCard`.

#### [MODIFY] webapp/src/api/types.ts & client.ts & mock.ts
- Define `StrategyReportCardPredicted`, `StrategyReportCardActual`, `StrategyReportCardRow`, `StrategyReportCardSnapshot`.
- Export API client method.
- Generate honest fixtures for UI.

#### [NEW] webapp/src/screens/StrategyReportCard.tsx & webapp/src/screens/StrategyReportCard.test.tsx
- Create the screen, test rendering.

#### [MODIFY] webapp/src/App.tsx & webapp/src/navigation.tsx
- Add route and navigation elements (including Explore tile).

---

### Part C — Documentation

#### [MODIFY] docs/known_issues/paper_trade_strategy_id_vocabulary.md
#### [MODIFY] docs/architecture/execution.md
#### [MODIFY] docs/architecture/webapp-and-gui.md
#### [MODIFY] CLAUDE.md & AGENTS.md & GEMINI.md

## Verification Plan

### Automated Tests
```bash
pytest tests/test_pilots_catalog.py tests/test_dispersion_trading.py tests/test_copula_stat_arb.py tests/test_zero_dte_engine.py tests/test_options_paper_executor.py tests/test_paper_broker_options_order.py tests/test_pilots_mirror.py -v
pytest tests/test_pilots_strategy_report_card.py tests/test_pilots_strategy_matrix.py -v
pytest tests/test_pilots_api.py -k "StrategyReportCard or heavy_engines" -v
cd webapp && npm run typecheck && npm test && npm run build
```

### Manual Verification
- Manual mock UI testing for the StrategyReportCard screen to confirm n=10 floor and proper rendering of `followable=False` pilots.
- Verify `GET /strategy/report-card` schema parity.

## AGENT HANDOFF NOTES
Will update `docs/known_issues/paper_trade_strategy_id_vocabulary.md`, `docs/architecture/execution.md`, `docs/architecture/webapp-and-gui.md`, `CLAUDE.md`, and `AGENTS.md`. No updates to `HOW_TO_GUIDE.md` or `RUNBOOK.md` are necessary, except maybe to mention the new screen.
