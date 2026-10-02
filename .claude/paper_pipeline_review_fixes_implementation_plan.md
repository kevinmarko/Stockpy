# Fix review findings on pipeline paper trading, freeze status and FMP 402

## Context
The operator ran a code review of the last merged commits (#1089, #1092, #1094, #1093, #1095) and pasted 9 findings (2026-10-01). #1093 and #1095 came back clean.

Two findings are already resolved by the open PR #1096 (Alpaca removal), which deletes the code they point at:
- **#3:** RISK REDUCE on the Alpaca path. The Alpaca path is gone, and the paper path always filters on `strategy_id == main_pipeline`.
- **#6:** the duplicated `_uses_local_paper_ledger` guard. It was deleted; `resolve_broker_backend()` is the only rule.

The other 7 are real. **Base: start after #1096 merges**, because the same `_execute_broker_orders` lines are involved. That gives one PR, `fix-paper-pipeline-review-findings`.

Facts checked live:
- FMP `exchange-market-hours?exchange=NASDAQ` returns `isMarketOpen` on this plan, and `holidays-by-exchange` returns closures plus `adjCloseTime` for early closes.
- A comma-separated `/quote?symbol=A,B` returns `[]`, so this plan has no bulk quote.
- The `holidays` package is installed and has an NYSE calendar. `pandas_market_calendars` and `exchange_calendars` are not installed.

## Fixes

### 1. Open pipeline positions always stay in the universe (finding #1, high)
- In `pipeline/advisory_inputs.py::build_universe_detailed`, union the symbols of open `strategy_id='main_pipeline'` paper positions into `held` **before** `compute_tracked_universe(...)`. Held symbols are already spared from rating auto-drop, so a position whose symbol left the scan, watchlist or rating set is still scored every cycle and can still get its exit signal.
- Read them with a new network-free helper, `data/paper_account_store.py::open_position_symbols(strategy_id)`. It is a plain SELECT on `paper_positions` (qty != 0), never `get_open_positions()`, which marks prices over the network.
- Fail soft: on any error, log a warning and use an empty set (CONSTRAINT #6).
- The daemon and `main.py` both build the universe through `build_universe_detailed`, so one change covers both.

### 2. The probe applies only to a genuine cold start, respects risk zeros, and is gross-capped (finding #2, high)
In `main_orchestrator._execute_broker_orders`, a zero-Kelly BUY gets the probe weight only when **all** of these hold:
- **(a) Cold start:** closed `main_pipeline` paper trades < `strategy_engine.MIN_TRADES_REQUIRED` (30). Count them once per call with a cheap SQL count helper on `paper_closed_trades`. After 30 closed trades the probe switches off by itself and measured Kelly decides, so a measured edge of zero stays zero.
- **(b) Dual Momentum did not zero the row:** skip when `DualMomentum_Signal == settings.DUAL_MOMENTUM_SAFE_ASSET` and the symbol is in `DUAL_MOMENTUM_RISKY_ASSETS`.
- **(c) Regime and meta-label allow it:** `Regime_Multiplier > 0` and `Meta_Label_Composite > 0` when those columns are present. A NaN or missing value counts as "not allowed", so it fails closed.
- **(d) Regime scaling and the gross cap:** probe weight = `PAPER_PIPELINE_PROBE_WEIGHT × Regime_Multiplier`. Track the probe gross added this cycle plus the existing `main_pipeline` exposure (sum of position value / equity), and stop issuing probes once `MAX_PORTFOLIO_GROSS` would be exceeded. Log each skip reason once per cycle.

A positive Kelly Target still always wins, unchanged.

### 3. Holiday- and early-close-aware market-hours gate, plus stale-quote rejection (finding #4)
- **New helper** `engine/advisory_agent.py::is_us_market_open_now(now)`:
  - **Primary source:** FMP `exchange-market-hours` (`isMarketOpen`, NASDAQ). Add a thin wrapper in `data/fmp_client.py` that goes through the shared throttle, and cache the answer for 60 s.
  - **Fallback:** existing `is_us_market_open(now)` AND today is not in `holidays.NYSE()`. The fallback has no early-close data, and that limit is disclosed in the docstring. FMP is the primary source and does cover early closes.
- **Wiring:** `_execute_broker_orders` Rule 1 calls the new helper. The existing `is_us_market_open` stays as it is for its other callers.
- **Stale-quote rejection:** in `execution/fmp_paper_broker.py`, reject a fill (status REJECTED, honest reason) when the FMP quote `timestamp` is older than the new setting `PAPER_FILL_MAX_QUOTE_AGE_SECONDS`.
  - Default 900. This is a paper-only safety check. The only callers are the pipeline and the MCP; manual Quick Trade uses `pilots/paper_equity_order.py` and is untouched.
  - A missing timestamp is rejected too (fail closed).

### 4. A 402 latches only on a real plan restriction (finding #5)
- In `data/fmp_client._fmp_get`, a 402 is latched only when the body says it is a plan restriction: the text contains "Restricted Endpoint" or any existing `_ACCESS_DENIED_MARKERS` phrase ("not available under your current subscription", "upgrade your plan").
- Any other 402 (billing lapse, quota) counts as a transient host failure: it advances the breaker or cooldown, exactly like a 429/5xx, with no permanent latch. The body is read with `resp.text`, guarded.
- `/batch-quote`'s 402 body contains "Restricted Endpoint", so #1094's live behavior is unchanged.

### 5. Typed confirmation for the probe weight (finding #7)
Add `PAPER_PIPELINE_PROBE_WEIGHT` to `settings_keysets.DANGEROUS_KEYS`, with a reason text. Update the keyset count tests and the webapp mock if it lists dangerous keys.

### 6. Bound the per-symbol batch fallback (finding #8)
`FMPProvider.get_quotes_batch`'s out-of-plan fallback stops issuing `/quote` calls once a time budget is spent. The budget is the existing `FMP_MAX_SECONDS_PER_CYCLE`, or a 20 s default if that is unset or larger. Unresolved symbols are simply missing from the result, and `CompositeProvider.get_quotes_batch` already sends missing symbols down the yfinance chain. When the budget truncates, it logs a warning naming the number of symbols skipped. Nothing is fabricated.

### 7. Freeze status reads the database only (finding #9)
`scripts/feature_freeze_status.py` uses the new `open_position_symbols("main_pipeline")` helper from fix 1 instead of `get_open_positions()`, so no network calls are made.

## Tests
- `test_advisory_inputs*.py`:
  - An open `main_pipeline` symbol that is absent from the watchlist and scan, and rating-excluded, is still in the universe.
  - The helper failing leaves the universe unchanged.
- `test_execute_broker_orders.py`:
  - Probe skipped at ≥30 closed trades.
  - Probe skipped for a Dual-Momentum-zeroed risky asset.
  - Probe skipped when `Regime_Multiplier` is 0 or NaN.
  - Probe scaled by the regime multiplier.
  - The gross cap stops probes partway through a cycle.
  - The Rule 1 gate uses the new helper (holiday means no orders).
- `test_advisory_agent*.py`:
  - FMP `isMarketOpen` false on a weekday counts as closed.
  - When FMP is unavailable, the fallback is closed on 2026-11-26 and on 2026-12-25.
  - The answer is cached for 60 s.
- `test_fmp_paper_broker.py`: a stale timestamp is rejected; a missing timestamp is rejected; a fresh quote fills.
- `test_fmp_client.py`:
  - A 402 with "Restricted Endpoint" is latched.
  - A 402 with any other body is not latched, advances the breaker, and the next call goes through.
- `test_market_data.py`: the per-symbol fallback stops at the budget and leaves the rest missing.
- `test_settings_keysets.py`: the probe weight is in DANGEROUS_KEYS.
- `test_feature_freeze_status.py`: the script makes no call to `get_open_positions`.

## Docs
- `docs/architecture/execution.md`: the probe gating rules, the holiday-aware gate, the stale-quote rejection.
- `docs/architecture/data-layer.md`: 402 semantics, the batch fallback budget, and the `open_position_symbols` helper.
- `CLAUDE.md`/`AGENTS.md`: the runtime `BrokerExecutionStep` bullet gets "holiday-aware; probe only at cold start".
- `.claude/paper_pipeline_review_fixes_walkthrough.md`.
- Regenerate the settings census and liveness files for the new setting.

## Verification
- Targeted tests above, then the full offline suite and ruff F821/F822/F823/E9.
- Live, read-only checks:
  - `is_us_market_open_now()` returns FMP's answer.
  - `open_position_symbols('main_pipeline')` returns the 8 positions opened today.
  - `feature_freeze_status.py` runs with no network.
- After merge: restart the daemon. The next market-hours cycle should log the probe decisions; no new probes are expected for already-held names. The first closed `main_pipeline` trade should then start counting.
