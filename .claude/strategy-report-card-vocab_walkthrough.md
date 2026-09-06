# Strategy Report Card & `strategy_id` Standardization Walkthrough

## 1. What Changed

This PR standardizes the `strategy_id` vocabulary across the quantitative options desk and builds a full-stack **Strategy Report Card** to cross-examine predicted (validation harness) performance against actual (paper broker) realized performance.

### A. Vocabulary Standardization
- Appended 10 new option-centric strategies to the Pilot Catalog (`pilots/catalog.py`). To prevent unintentional allocation without proper ML integration, all 10 are strictly labeled `followable: False` with `weights={}`.
- Refactored `execution/options_paper_executor.py`, `pilots/dispersion_trading.py`, `pilots/copula_stat_arb.py`, `pilots/zero_dte_engine.py`, and `pilots/paper_broker_options_order.py` to route all new order creations through proper pilot/registry IDs (e.g. `zero-dte-momentum-breakout`, `dispersion-trading`). 

### B. Strategy Report Card Read Helper
- Created `pilots/strategy_report_card.py` wrapping the `ValidationHistoryStore` (Predicted) and `PaperAccountStore` (Actual).
- Deployed a legacy alias mapper (`LEGACY_STRATEGY_ID_ALIASES`) so older untagged records still properly bucket under the new naming convention on the read side.
- Implemented a rigorous honesty limit via `MIN_TRADES_FOR_VERDICT = 10`. Below 10 samples, all evaluative metrics are nullified to prevent fabricating performance stats on tiny samples.

### C. Live Backend and Test Parity
- Surfaced `GET /strategy/report-card` via the fail-open `api/pilots_api.py` endpoint.
- Corrected serialization drift on the `followable` attribute.
- Audited test patches to guarantee `sqlite:///:memory:` operations avoid hanging or crossing boundaries in concurrent execution.

### D. Frontend Strategy Report Card
- Extended TypeScript types to include the raw array response for `StrategyReportCardRow`.
- Rebuilt `mock.ts` with honest fallback datasets covering the missing/null edge cases explicitly.
- Added a standalone `/strategy-report-card` screen displaying visually distinct predicted vs actual panels per row to avoid confusing performance blends.
- Gated the allocation system via `pilot.followable`, adding clear "Restricted" info tooltips over disabled follow buttons.

### E. Documentation
- Expanded `CLAUDE.md`, `AGENTS.md`, `docs/architecture/execution.md`, and `docs/known_issues/paper_trade_strategy_id_vocabulary.md` to record exactly what was completed and what explicitly remains out of scope (e.g., `main_pipeline` attribution).

## 2. What Was Tested

All validations strictly adhered to the audit-first philosophy:

- **Backend tests:** `pytest tests/test_pilots_catalog.py tests/test_dispersion_trading.py tests/test_copula_stat_arb.py tests/test_zero_dte_engine.py tests/test_options_paper_executor.py tests/test_paper_broker_options_order.py tests/test_pilots_strategy_report_card.py tests/test_pilots_api.py -v` (442 passed).
- **Mock/Live Parity Testing:** Purposely broke a frontend mock payload attribute during the audit phase to verify that React tests are actually catching failures. (Tests accurately flagged the issue and were then restored).
- **TypeScript and VITE checks:** `npm run typecheck && npm run build` successfully passed with 0 structural schema errors and 0 missing imports.

## 3. Validation Results

- The PWA accurately disables following on pilot accounts with `followable: False`.
- Evaluative metrics for pilots with under 10 trades correctly display as missing/reason-blocked in the UI rather than 0.0 or other fabricated integers.
- All legacy trades safely traverse the `LEGACY_STRATEGY_ID_ALIASES` mapping and populate seamlessly.
- Documentation accurately reflects the `strategy_id` structural differences to minimize future operator confusion.
