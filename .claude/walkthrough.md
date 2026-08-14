# Walkthrough: Multi-Leg Option Paper Trading & Automated Strategy Paper Execution

We have built out and verified both **Phase 1: Multi-Leg Option Paper Trading Primitives** and **Phase 2: Automated Strategy Paper Execution** across backend engines, the SQLite account store, FastAPI endpoints, PWA interface, and unit/integration test suites.

---

## What Was Built

### 1. Multi-Leg Paper Trading Primitives (Phase 1)
- **Math & Sizing Core (`pilots/order_sizing.py`)**:
  - Implemented `calculate_multi_leg_option_sizing` handling both debit spreads (budget / contract cost) and defined-risk credit spreads (budget / max risk where `max_risk = (width - credit) * 100`).
- **Atomic SQLite Multi-Leg Ledger (`data/paper_account_store.py`)**:
  - Implemented `apply_multi_leg_fill` executing atomic transactions across cash balance and all leg positions simultaneously.
  - Added support for short positions (`qty < 0`), properly tracking short inventory reductions on buy-to-close orders.
  - Added Black-Scholes mark-to-market dynamic position pricing and P&L tracking in `_resolve_position_prices()`.
- **Brokers (`execution/fmp_paper_broker.py`, `pilots/paper_broker_options_order.py`)**:
  - Removed multi-leg order rejection.
  - Enabled full multi-leg order fills (Bull Call Spreads, Bear Put Spreads, Iron Condors, Straddles, Strangles) with net pricing checks, limit order marketability validation, and $0.65/contract/leg fee accounting.
- **PWA UI & Mock Parity (`webapp/src/api/mock.ts`, `webapp/src/screens/PaperBroker.tsx`)**:
  - Added `OPTION` and `SHORT` status badges to open positions.
  - Mirrored multi-leg execution and position handling in `mockApi.postOptionsOrder`.

---

### 2. Automated Strategy Paper Execution (Phase 2)
- **Configuration (`settings.py`)**:
  - `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED: bool = False` (opt-in daemon execution).
  - `MAX_OPTION_NOTIONAL_PER_TRADE: float = 2500.0` (per-strategy risk budget).
  - `MAX_CONCURRENT_OPTION_POSITIONS: int = 10` (portfolio option position cap).
- **Executor Engine (`execution/options_paper_executor.py`)**:
  - Implemented `OptionsPaperExecutor`:
    - Scans quantitative options strategy directives from `technical_options_engine.py`.
    - Filters by `passes_premium_gate` (VRP > 0.02, True IVR > 50%, VIX < 30, No Credit Event) and `Integrity_OK`.
    - Enforces 1-spread-per-ticker deduplication against open paper positions.
    - Resolves target expiration date (e.g. ~30 DTE Friday).
    - Computes contract sizing via `calculate_multi_leg_option_sizing` capped by `MAX_OPTION_NOTIONAL_PER_TRADE`.
    - Executes atomic multi-leg fills directly into `PaperAccountStore`.
- **Orchestrator Wiring (`main.py`)**:
  - Wired `OptionsPaperExecutor().execute_strategy_directives()` into `main.py` cycle when `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED=True`.
- **Pilots API & Webapp (`pilots/paper_broker.py`, `api/pilots_api.py`, `webapp/`)**:
  - `GET /pilots/paper-broker/strategy-options/candidates` (previews actionable directives).
  - `POST /pilots/paper-broker/strategy-options/execute` (triggers automated paper execution).
  - Webapp: Added **⚡ Automated Strategy Options Execution** panel in `PaperBroker.tsx` with candidate preview table, live status notifications, and one-click execution trigger.
  - Mirrored in `client.ts`, `mock.ts`, `types.ts`.

---

## Verification Results

### Automated Tests Passed
1. **Pytest Backend Suite (64/64 Passed)**:
   ```bash
   pytest tests/test_options_paper_executor.py tests/test_pilots_paper_broker.py tests/test_paper_broker_options_order.py tests/test_paper_account_store.py tests/test_fmp_paper_broker.py -v
   # 64 passed in 3.83s
   ```
2. **Frontend Typecheck (0 Errors)**:
   ```bash
   npm run --prefix webapp typecheck
   # tsc --noEmit: clean
   ```
3. **Frontend Vitest Suite (10/10 Passed)**:
   ```bash
   npx vitest run src/screens/PaperBroker.test.tsx src/screens/OptionsChain.test.tsx src/components/options/OptionsOrderTicket.test.tsx
   # 3 test files passed, 10 tests passed
   ```

---

## Next Steps Queued
- **Phase 3: Options Portfolio Risk & Aggregate Greeks**: Compute portfolio-level net Greeks (Delta, Gamma, Theta, Vega) across paper option positions and display in PWA / reports.
- **Phase 4: Options Backtest Harness Integration**: Feed multi-leg paper trading history into `validation/harness.py` and ML Stage 4 meta-labeling.
