# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Multi-Agent Branch Workflow

Two agents work on this repo: **Claude Code** and **Antigravity IDE**.

### Branch naming
```
agent/claude-code/<short-description>    # Claude Code's branches
agent/antigravity/<short-description>    # Antigravity's branches
```
- Never commit directly to `main` — always open a PR from a feature branch.
- Branch names are lowercase-kebab: `agent/claude-code/fix-hmm-lookahead`.

### Domain split (avoid editing the other agent's files in the same PR)
| Domain | Owner |
|---|---|
| `signals/`, `strategy_engine.py`, `sizing/`, `ml/`, `regime/`, `macro_engine.py`, `validation/`, `execution/`, `tests/` | Claude Code |
| `gui/`, `observability/`, `reporting_engine.py`, `diagnostics_and_visuals.py`, `scripts/` | Antigravity |
| `config.py`, `dto_models.py`, `data/`, `data_engine.py`, `main.py`, `main_orchestrator.py`, `requirements.txt` | **Shared** — flag in PR |

### Claude Code start-of-session checklist
1. `git fetch origin && git rebase origin/main` — sync from main before starting.
2. `git checkout -b agent/claude-code/<description>` — never work on an existing branch another agent pushed.
3. Open a PR when the feature is complete; do not squash or amend published commits.

### Merge sequencing for shared files
When both agents edit a shared file in the same sprint, merge the smaller/less-risky PR first, then rebase the other on the updated `main` before merging. Document the sequence in the PR "Conflicts" section.

## Project

InvestYo Quant Platform ("Stock Dashboard Py") — an automated quantitative analysis pipeline: fetches market/macro data, computes technical & fundamental indicators, runs multi-horizon forecasts, backtests strategies, persists signals to SQLite, and publishes results to Google Sheets / an HTML report.

### Key documentation files
| File | Purpose |
|------|---------|
| `docs/architecture.md` | Mermaid data-flow diagram (Engines → DTOs → Signals → Strategy → Advisory → Broker [quarantined]) |
| `docs/signals/README.md` | Index of all 17 registered `SignalModule` implementations with academic references, logic, and failure modes |
| `docs/signals/<name>.md` | Per-strategy README for each signal module |
| `docs/incident_log.md` | Template + log for production incidents (referenced by RUNBOOK.md §6) |
| `docs/HOW_TO_GUIDE.md` | End-user guide for every platform feature |
| `docs/RUNBOOK.md` | Operational runbook — pre-market checklist, incident playbooks, advisory pause procedure |
| `docs/GO_LIVE_CHECKLIST.md` | Pre-live checklist (all automatable items covered by `preflight_check.py`) |

## Commands

```bash
# ── macOS double-click launcher ────────────────────────────────────────────────
# One-time setup (already done — recorded here for reference):
#   chmod +x launch.command
# Double-click launch.command from Finder or the Dock to start the platform.
# REFRESH_INTERVAL_SECONDS at the top of the file controls single-run (=0)
# vs interval-loop (>0, default 60 s) mode.
# The script verifies .venv exists and that Python is exactly 3.12.x before
# launching, then pauses ("Press any key") on exit so errors are always visible.

./setup.sh                       # creates .venv (Python 3.12), installs requirements.txt
source .venv/bin/activate
python3 main.py                  # clean advisory orchestrator — runs one full cycle (or loops with --interval N); use --refresh-account to force Robinhood re-auth
python3 main.py --interval 60   # refresh market data every 60 s; Robinhood account fetched at most once/day
python3 main.py --refresh-account  # force fresh Robinhood login on this launch, then resume normal daily-cache behavior
python3 main_orchestrator.py     # newer async master orchestrator (data acquisition, schema validation, HTML report compilation)
pytest                           # run test suite
pytest tests/test_quantitative_models.py            # run a single test file
pytest tests/test_quantitative_models.py::test_graham_number_imaginary_bounds  # run a single test
python3 database_setup.py        # (re)build the SQLite schema in quant_platform.db from config.COLUMN_SCHEMA
python3 -m validation.harness --strategy <name> --start YYYY-MM-DD --end YYYY-MM-DD # run strategy validation harness
python scripts/preflight_check.py            # pre-live readiness gate (exit 0 = all pass)
python scripts/preflight_check.py --json     # machine-readable JSON output
streamlit run observability/dashboard.py     # paper-trading observability dashboard (auto-refresh 30s)
streamlit run gui/app.py                     # InvestYo Command Center — full 10-tab operational GUI
./launch_gui.command                         # same as above, macOS double-click launcher
python -m execution.kill_switch --status     # check / activate / deactivate the global kill switch
make verify                                  # env-var check + pytest + one live run_once() + print summary
./verify.command                             # same as make verify, macOS double-click
```

`main.py` and `main_orchestrator.py` both auto-re-exec themselves under `.venv`'s interpreter if not already running inside it — no need to manually activate the venv before running them.

`main.py` symbol universe = `AccountSnapshot.positions` ∪ `WATCHLIST` env var (comma-separated) ∪ `watchlist.txt` (one ticker per line, `#` = comment). Held symbols are always included. **Last-resort fallback:** when held positions AND `WATCHLIST`/`watchlist.txt` are ALL empty, `_load_tickers_from_sheet2()` reads column A of the "Sheet2" tab in the "Stock Dashboard Py" Google Sheet (via `credentials.json`) and uses those tickers instead — it never merges with the other sources, is consulted only when they yield nothing, and degrades silently to `[]` (logged warning, never a crash) when `credentials.json` / the tab / the API is unavailable. New env var: `WATCHLIST`.

## Architecture

Flat, modular "Engine" architecture using dependency injection — no package directories, every engine is a top-level module imported directly by the orchestrators.

- **config.py** — Single Source of Truth (SSOT). `COLUMN_SCHEMA` defines every column's Google Sheets header, internal dict key, and display format, plus Pandera schemas for validation. Any new field must be added here first before use elsewhere.
- **dto_models.py** — `MarketBarDTO`, `FundamentalDataDTO`, `MacroEconomicDTO`, `RobinhoodPositionDTO`. All market/fundamental/macro data flowing through calculation layers must be coerced into these DTOs — raw dict lookups or positional indexing in calc code is not allowed. DTO parsers also handle messy upstream strings (currency symbols, `%`, `"N/A"`, padding). `MacroEconomicDTO` now accepts an optional `hmm_risk_on_probability: Optional[float] = None` (the `regime/hmm_regime.py` second opinion). `None` (the default) reproduces the exact pre-HMM behavior. The rules-based `_rules_based_regime` stays primary; `market_regime` downgrades RISK ON → NEUTRAL (logged) when `hmm_risk_on_probability < HMM_RISK_ON_DOWNGRADE_THRESHOLD` (0.3) — it can never independently declare a worse regime or upgrade a better one. `killSwitch` ORs in a lowered-threshold check (`vix > KILLSWITCH_VIX_THRESHOLD_AGREED`=25 or `sahm_rule_indicator >= KILLSWITCH_SAHM_THRESHOLD_AGREED`=0.3, vs. the base 30/0.5) only when the rules-based regime is RECESSION AND the HMM agrees (`1 - hmm_risk_on_probability > HMM_RISK_OFF_AGREEMENT_THRESHOLD`=0.7) — strictly OR'd with the base condition, never less sensitive.
- **data_engine.py** — `DataEngine` (live Yahoo Finance/FRED via `fredapi`) and `MockDataEngine` (test fixture), both implementing an `IDataProvider` interface. Fetching is strictly decoupled from calculation. `IDataProvider` now also declares `fetch_macro_history() -> pd.DataFrame` (full historical VIX/yield-curve series for `regime/hmm_regime.py`'s expanding-window fit, distinct from `fetch_macro_raw()`'s single current-snapshot dict). `DataEngine.fetch_macro_history()` pulls full unbounded series via `self.fred.get_series('VIXCLS'/'T10Y2Y')`; returns an empty DataFrame (never fabricated rows) if FRED is unavailable. `MockDataEngine.fetch_macro_history()` returns a deterministic 500-row synthetic series (seeded) for offline tests.
- **data/market_data.py** — Swappable market-data layer with a provider abstraction (`MarketDataProvider` ABC) that hides the concrete backend from all signal, indicator, and forecasting code. **Provider auto-selection** (evaluated at `CompositeProvider` construction): (1) `MARKET_DATA_PROVIDER=alpaca` or Alpaca keys present → `AlpacaProvider` (real-time IEX via alpaca-py); (2) otherwise → `YFinanceProvider` (zero config, ~15-min delayed, `is_stale=True` always). **Components**: `Quote` (frozen dataclass: symbol, price, bid, ask, UTC timestamp, is_stale, source); `MarketDataError` (typed exception — orchestrator catches per-symbol for dead-letter resilience); `AlpacaProvider` (`StockHistoricalDataClient` + `StockLatestQuoteRequest` + `StockBarsRequest`, IEX feed, stale if quote age > `stale_threshold_seconds`=60); `YFinanceProvider` (`Ticker.fast_info` for quotes, `Ticker.history()` for bars — always `is_stale=True`, wraps yfinance errors as `MarketDataError`); `FinnhubProvider` (fundamentals-only, `company_basic_financials`, maps Finnhub metric names to yfinance `.info` key names so `FundamentalDataDTO.from_raw_dict()` is unchanged, degrades to empty dict when `FINNHUB_API_KEY` absent); `CompositeProvider` (routes quotes/bars to the selected backend, fundamentals to Finnhub → yfinance fallback, in-process TTL quote cache, logs startup banner once). **Bar shape contract**: `get_intraday_bars()` returns a DataFrame with columns `Open, High, Low, Close, Volume` and a timezone-naive `DatetimeIndex` sorted ascending — identical to `DataEngine.fetch_technical_raw()`'s shape so all processing/forecasting/strategy code runs unchanged. **Quote cache**: in-process dict + monotonic TTL (default `MARKET_DATA_QUOTE_TTL_SECONDS`=30 s), never written to disk. **Fundamentals cache + rate limiter (2026-06 Finnhub 429 mitigation)**: both `FinnhubProvider` AND `CompositeProvider` carry an in-process `_FundamentalsCache` (default `FUNDAMENTALS_CACHE_TTL_SECONDS`=21600 s / 6 h) that caches positive AND empty (negative) responses — so 429-rate-limited or coverage-miss symbols never re-hit the network within the TTL window. `FinnhubProvider` additionally wraps every outbound call in a `_SlidingWindowRateLimiter` (default `FINNHUB_RATE_LIMIT_PER_MIN`=50, under the 60/min free-tier ceiling) and applies one-shot exponential backoff (2 s) + retry on a detected 429 (duck-typed via `getattr(exc, "status_code", None) == 429`). Persistent 429 is downgraded to an INFO log and a cached empty dict — never propagates as a crash, never floods WARN logs across a large watchlist sync. `clear_fundamentals_cache()` purges both layers (composite + Finnhub) for forced refresh. Module-level singleton via `get_provider()` / `reset_provider()`. **New env vars**: `MARKET_DATA_PROVIDER`, `FINNHUB_API_KEY`, `MARKET_DATA_QUOTE_TTL_SECONDS`, `FUNDAMENTALS_CACHE_TTL_SECONDS`, `FINNHUB_RATE_LIMIT_PER_MIN`.
- **data/robinhood_client.py** — `RobinhoodClient` API wrapper (`robin_stocks`). Logs in (falling back to interactive terminal input if MFA is required), fetches positions, unzips average costs and shares, and merges them into `main.py` and `main_orchestrator.py` universes. **Task 1.4 discovery helpers:** module-level `discover_watchlists(client) -> {watchlist_name: [tickers]}` reads every Robinhood "Lists" entry via `r.get_all_watchlists()` + `r.get_watchlist_by_name()` (per-list failures logged & skipped — never raises). `discover_universe(client, extra_files=None) -> list[str]` returns one sorted/deduped universe of holdings ∪ every RH watchlist ∪ plain-text files supplied via the `extra_files` argument or the colon-separated `SYNC_WATCHLIST_FILES` env var. Both helpers short-circuit cleanly when the client is not authenticated. **2026-06 noise suppression**: Robinhood's `midlands/lists/items/?list_id=<UUID>` endpoint returns 400 for certain system-curated watchlists; `robin_stocks` prints the HTTPError via `print(message, file=helper.get_output())` (not `logging`), which floods stdout on every account. Both discovery calls (`get_all_watchlists`, `get_watchlist_by_name`) are now wrapped in the `_suppress_rs_output()` context manager that swaps `robin_stocks.robinhood.helper`'s output sink to an in-memory `StringIO` for the duration of the call and forwards any captured text to the module logger at DEBUG, then restores the prior sink. Functional behavior is unchanged — the 400-empty result still degrades silently via the existing `or []` and try/except, only the stdout spam is gone.
- **data/portfolio_sync.py** — **Task 1.4 Portfolio & Watchlist Synchronization Engine.** Public API: `CoverageStatus` enum (`FULL` / `QUOTES_ONLY` / `EQUITY_ONLY` / `UNCOVERED` / `UNKNOWN`), frozen dataclasses `SymbolStatus` and `SyncReport`, `build_sync_report(snapshot, *, client=None, watchlist_files=None, forecast_symbols=None, probe_market=True) -> SyncReport`, async wrapper `async_sync_now(...)` (used by the GUI Live Inventory "Sync Now" button), and `write_cache`/`read_cache` for the JSON snapshot at `cache/sync_report.json` (atomic write-then-rename). `build_sync_report` (a) takes the union of holdings (Robinhood `AccountSnapshot.positions`) + every Robinhood watchlist + file-backed lists; (b) probes each symbol against `data.market_data.get_provider()` for quote+bar+fundamentals coverage (per-symbol try/except, never aborts); (c) classifies each symbol — held symbols whose market-data probe fails are **upgraded** from `UNCOVERED` to `EQUITY_ONLY` so they remain visible in the equity view while being excluded from pricing-dependent metrics; (d) emits one structured INFO log per coverage gap (non-blocking diagnostic per the Task 1.4 spec). **No fabricated metrics** (CONSTRAINT #4): a held position with no live quote and a non-positive Robinhood `market_value` reports `current_price=NaN`, `market_value=NaN`; `SyncReport.held_total_equity()` then falls back to `qty * avg_cost` so the cost-basis-anchored equity view stays accurate without inventing a current-price proxy. `async_sync_now(persist_default_tickers=True)` runs `build_sync_report` off-thread and writes the resulting sorted universe to `DEFAULT_TICKERS` in `.env` via `gui.env_io.write_setting` (the allowlist-bounded path); a `SecretWriteError`/`DisallowedKeyError` from env_io is caught and logged rather than propagated so the GUI's refresh handler never crashes. **New env var:** `SYNC_WATCHLIST_FILES` (colon-separated paths to additional plain-text watchlist files; missing files tolerated silently).
- **data/historical_store.py** — **Tier 2.3 Phase 1 + Phase 2 + Phase 3.** Persistent OHLCV bar cache, Robinhood account snapshot store, fundamentals history, and FRED macro series backed by `quant_platform.db` (raw `sqlite3` + WAL). Public API: `HistoricalStore(db_path="quant_platform.db")` — creates all five tables lazily. **Phase 1 — price_bars**: `get_bars(symbol, lookback_days=504, *, provider=None) -> pd.DataFrame` returns tz-naive `DatetimeIndex` with columns `[Open, High, Low, Close, Volume]` — identical to `DataEngine.fetch_technical_raw()`; `latest_bar_date(symbol) -> Optional[pd.Timestamp]`. Incremental rule: `SELECT MAX(date)` → fetch `(max_date, today]` only; first call = `BARS_BACKFILL_DAYS` (504) full backfill. **Phase 2 — account_snapshots / account_positions**: `save_account_snapshot(snapshot) -> int` (returns `snapshot_id` or `-1` on error, single ROLLBACK-safe transaction); `latest_account_snapshot() -> Optional[AccountSnapshot]` (reconstructs full dataclass + positions dict from DB); `account_snapshot_history(since=None) -> pd.DataFrame` (equity-curve data). **Phase 3 — fundamentals_history + macro_history**: `get_fundamentals(symbol, max_age_days=1, *, provider=None) -> Dict[str, float]` returns typed columns `{pe_ratio, pb_ratio, roe, dividend_yield, market_cap, eps, operating_margin, debt_to_equity}` — missing fields are `NaN`, NEVER `0.0` (CONSTRAINT #4); caches the full `raw_json` provider dict for future PIT-fundamentals replay; daily TTL controlled by `FUNDAMENTALS_REFRESH_DAYS`. `get_fundamentals_history(symbol, since=None) -> pd.DataFrame` returns all accumulated snapshots for PIT replay (see note below). `get_macro(series_id, *, lookback_days=None, data_engine=None) -> pd.Series` returns a tz-naive DatetimeIndex Series from `macro_history`, topping up from FRED via `data_engine.fetch_macro_history()` when the cached rows are older than `MACRO_REFRESH_HOURS` (default 12 h). All methods dead-letter resilient (try/except → empty sentinel, never raise — CONSTRAINT #6). No fabricated data (CONSTRAINT #4). `AccountSnapshot` is the in-memory source of truth; DB tables are derived from it. **Settings**: `HISTORICAL_STORE_ENABLED` (default `True`), `BARS_BACKFILL_DAYS` (default 504), `FUNDAMENTALS_REFRESH_DAYS` (default 1), `MACRO_REFRESH_HOURS` (default 12). **PIT-fundamentals note**: `fundamentals_history.raw_json` accumulates real point-in-time snapshots from the day Phase 3 ships. After ≥ 90 days of history, `get_fundamentals_history()` can be used to extend the multifactor validation harness to the Value/Quality factors — see `tests/test_validation_multifactor.py` docstring for the extension path (out-of-scope for Phase 3).
- **data/robinhood_portfolio.py** — **ADVISORY ONLY. NO ORDER CODE.** Read-only Robinhood account snapshot via TOTP MFA (`pyotp`). Public API: `fetch_account_snapshot(max_age_hours=20.0, force=False) -> AccountSnapshot` and `logout()`. Frozen dataclasses: `PortfolioPosition` (symbol, qty, avg cost, current price, market value, unrealized P/L, dividends received, name) and `AccountSnapshot` (positions dict, buying\_power, total\_equity, total\_dividends, fetched\_at UTC-aware datetime). `AccountSnapshot.age_hours()` / `is_stale(max_age_hours)` surface staleness. **Three-tier read order (Tier 2.3 Phase 2):** (1) `HistoricalStore.latest_account_snapshot()` — DB, fastest, no network; (2) `cache/account_snapshot.json` — daily JSON cache, secondary fallback; (3) live Robinhood fetch. After a successful live fetch, the snapshot is written to BOTH the JSON cache AND the DB. Stale cache is returned (not errored) on live-fetch failure; raises only when live fails and no cache exists. Username/Password variables are read from `os.environ` (`RH_USERNAME`/`RH_PASSWORD`). If `RH_MFA_SECRET` is unset or empty, login falls back to prompting for MFA interactively. No secrets are ever written to any cache payload.
- **data/robinhood_orders.py** — **ADVISORY ONLY. NO ORDER CODE.** Read-only realized-P&L engine (Tier 7). Fetches FILLED equity orders (`get_all_stock_orders`) and reconstructs closed round-trip trades via PURE FIFO lot-matching (`reconstruct_closed_trades`), then summarises realized P&L / win rate / profit factor / holding stats (`realized_pnl_summary`). `parse_orders(raw, symbol_resolver)` normalises raw order dicts (filled-only, instrument-URL → ticker); `fetch_filled_orders(...)` adds a daily `cache/robinhood_orders.json` cache + dead-letter resilience (injectable `orders_fetcher`/`symbol_resolver` for tests); `realized_performance(...)` is the fetch→reconstruct→summarise convenience. Excess/short sells drop the unmatched qty (no fabricated entry); empty summaries are NaN-shaped (CONSTRAINT #4). Analytics-only — does NOT auto-write the `trades` table. See the Tier 7 section below.
- **processing_engine.py** — vectorized fundamental valuations (Graham Number, Gordon Fair Value) and technical indicators (RSI, MACD, Aroon, ATR, SMA). Iterative loops/`.iterrows()` over price series are disallowed; everything must be expressed as pandas/numpy vector operations. **Tier 2.3 Phase 3 wiring**: `calculate_fundamental_metrics()` initialises a `HistoricalStore` instance when `settings.HISTORICAL_STORE_ENABLED=True` and calls `get_fundamentals(ticker, max_age_days=settings.FUNDAMENTALS_REFRESH_DAYS)` to serve cached fundamentals or write fresh ones; the typed return dict is overlaid onto the raw `info` dict before existing downstream calculations (no key-mapping changes in downstream code). `HistoricalStore` is imported lazily (inside the method body) to avoid circular imports.
- **macro_engine.py** — macroeconomic regime detection/gating. `MacroEngine.__init__` now constructs a persistent `regime.hmm_regime.HMMRegimeDetector(n_states=3, retrain_freq_days=7)` instance (`self._hmm_detector`). `compute_hmm_risk_on_probability(spy_price_df)` builds the 4-feature matrix via `regime.hmm_regime.build_feature_matrix()` and returns the HMM's `risk_on_probability` second opinion — or `None` (never fabricated) if SPY history, macro history, or the aligned feature matrix (`< HMM_MIN_FIT_ROWS=100` rows) is insufficient, or if fit/predict raises (logged, never propagated — a statistical second opinion failing must never crash the primary rules-based pipeline). **Tier 2.3 Phase 3 wiring**: when `settings.HISTORICAL_STORE_ENABLED=True`, `compute_hmm_risk_on_probability` routes the VIXCLS and T10Y2Y series through `HistoricalStore.get_macro('VIXCLS')` / `get_macro('T10Y2Y')` (with `data_engine=self.data_engine` as the top-up source) instead of calling `self.data_engine.fetch_macro_history()` directly. On any HistoricalStore failure the code falls back to the direct `DataEngine.fetch_macro_history()` call (pre-Phase-3 behavior). The single-snapshot `_build_macro_dto` path (current-state reads for the kill-switch) is NOT routed through HistoricalStore — only the HMM's historical series benefit from DB caching.
- **regime/hmm_regime.py** — Gaussian HMM regime detector (Hamilton 1989), a statistical second opinion to the rules-based regime in `macro_engine.py`. `build_feature_matrix(spy_price_df, vix_series, yield_curve_series)` builds the 4-feature matrix (`spy_return`, `realized_vol_20d`, `vix_level`, `yield_curve_spread`), normalizing all three inputs' indices to timezone-naive, time-stripped dates before aligning (yfinance is often tz-aware/intraday-stamped; FRED is naive/midnight — without this, misaligned sources silently produce an all-NaN join). `HMMRegimeDetector(n_states=3, retrain_freq_days=7)`: `fit(features_df)` refits only if `retrain_freq_days` have elapsed since the last real fit (expanding window is the caller's responsibility — pass progressively more history each call); within that window, repeated `fit()` calls are no-ops, which is exactly what `tests/test_hmm_no_lookahead.py` exercises. `identify_states_by_vol()` sorts states by fitted diagonal-covariance variance ascending and labels `["bull", "sideways", "bear"]`. `predict_proba(features_df)` returns FORWARD (filtered) probabilities at the LAST ROW only of whatever's passed in — never Viterbi/smoothing — via the mathematical identity that a smoothed posterior's last row in any sequence equals pure forward filtering (the backward pass is seeded with `beta_T=1` at the sequence's own terminal step, so there is no "future" to smooth with). Callers MUST slice their feature frame to end exactly at the date they want a probability for; the API structurally cannot return an interior date's probability.
- **technical_options_engine.py** — options-specific technical metrics (IV rank, GARCH vol, options IV edge). **Premium directive helper**: top-level `build_premium_directive(symbol, bars, *, spot_price, is_stale=False, target_dte=30, macro_dto=None, vrp=None, risk_free_rate=settings.RISK_FREE_RATE) -> dict` fuses GJR-GARCH σ, realized-vol IVR proxy, Aroon+Coppock trend bias (via the deterministic `_determine_trend_bias` rule), full ATM Black-Scholes Greeks, and the deterministic strategy directive from `OptionsPricingRecommender.generate_strategy_pricing_matrix` into a single hydrated row (Symbol, Price, Stale, Sigma_GARCH, IVR_Proxy, Aroon_Oscillator, Coppock_Curve, Trend_Bias, Strategy, Action, Net_Premium, Realizable_Daily_Theta, ATM_{Delta,Gamma,Vega,Theta_Daily}, Short_/Long_Strike, Short_/Long_Delta, Legs, Integrity_OK, Integrity_Issues). NaN — never fabricated zeros — when a primitive can't be computed (CONSTRAINT #4). Centralised here (not duplicated in `gui/panels.py`) so the Gravity AI Review Suite exercises the same code path the GUI does. **Integrity validator**: `validate_directive_integrity(directive, *, delta_tolerance=0.05, strike_grid=STRIKE_GRID_USD=0.50) -> {"ok": bool, "issues": [str], "checks": [dict]}` asserts every leg strike lies on the `$0.50` exchange grid and (when a `Delta` is present on the leg dict) the resolved BS delta is within `delta_tolerance` of the conventional target in `EXPECTED_DELTA_TARGETS` (Put Credit Spread ±0.30/±0.15, Iron Condor ±0.16/±0.05, etc.). Cash/Wait directives pass trivially; Iron Condor legs omit `Delta` by engine convention and the validator skips (never fabricates) that check.
- **forecasting_engine.py** — ARIMA, Monte Carlo, Holt-Winters, and CNN-LSTM forecasts. Deterministic trend params are enforced (`trend='t'` for ARIMA, `trend='add'` for Holt-Winters) and Monte Carlo uses structural drift (μ − 0.5σ²) to prevent naive flatline projections. The CNN-LSTM model implements direct multi-horizon forecasting, trains once per ticker, and uses strict train-only scaler fitting and sequence creation to eliminate lookahead bias.
- **strategy_engine.py** — signal generation with calibrated momentum/trend weights and ATR-based volatility corridors (buy zone / hold range / trim-stop). Uses an Aroon Oscillator chop-filter to suppress false-positive MACD signals. Position sizing (`_calculate_kelly_sizing`) is volatility-targeting + estimated-p fractional Kelly (see `sizing/`) — no longer a score-derived formula. **Dedicated sell-side range (`apply_sell_side_range`):** alongside the legacy single-corridor `apply_tactical_ranges` (`buyRange`), `evaluate_security()` now ALWAYS emits a first-class `sellRange` string for every Action Signal. Active long signals (STRONG BUY/BUY/HOLD) yield `"Sell Zone: $LO - $HI | Stop @ $STOP"` where LO=`price + 1.5*ATR`, HI=`max(price + 3*ATR, forecast_price)` (forecast wins only when >ATR ceiling — never fabricated when forecast_price=0), STOP=`chandelier_long` (fallback `price - 2.5*ATR`, clamped ≥ $0.01). RISK REDUCE / unknown signals fail closed to `"Sell Now @ market | Stop @ $STOP"` (chandelier_long, fallback `price - 1.0*ATR`). Lookahead-free: consumes only the already-causal ATR, Chandelier Exit, and `Forecast_30` already flowing into `evaluate_security`.
- **sizing/** — Position sizing package: the single source of truth for "Kelly Target" sizing, replacing two divergent arbitrary score-derived win-probability formulas that previously lived in `strategy_engine._calculate_kelly_sizing` and a `main_orchestrator.py` call site into `evaluation_engine.calculate_kelly_target`.
  - **POSITION SIZING SINGLE SOURCE OF TRUTH (sizing/, strategy_engine.py):**
"Kelly Target" MUST be computed via `StrategyEngine._calculate_kelly_sizing(realized_vol, strategy_id=None)`, which returns a `(weight: float, path_tag: str)` tuple.

**Stage 1.7 — Per-Strategy Bootstrap-Conservative Kelly (current):**
- When `strategy_id` is provided, delegates to `sizing.kelly.kelly_sizing_for_strategy(transactions_store, strategy_id, realized_vol)` which:
  1. Calls `estimate_win_rate_and_payoff_per_strategy()` to filter closed trades to `strategy_id`.
  2. Cold start (< 30 per-strategy trades): falls back to `volatility_target_weight(realized_vol)`, tagged `"vol_target_fallback"`. ALWAYS explicitly logged.
  3. Warm path: runs `bootstrap_kelly_confidence(returns, n_bootstraps=1_000)` and returns the **5th-percentile** Kelly fraction — the "epistemic humility" sizing (conservative until the edge estimate is stable at > ~200 trades). Tagged `"bootstrap_kelly_5th_pct(n=...,k5=...,k50=...,k95=...)"`.
- When `strategy_id` is None (backward-compatible): uses the global-pool aggregate `estimate_win_rate_and_payoff()` point estimate. Tagged `"aggregate_kelly"` or `"vol_target_fallback"`.
- Either path is clamped to `settings.MAX_POSITION_WEIGHT=1.0` in `StrategyEngine._calculate_kelly_sizing()` itself before being multiplied by the regime and meta-label composites in `evaluate_security()`.
- `evaluate_security()` accepts `strategy_id: Optional[str] = None` and threads it down to `_calculate_kelly_sizing()`.
- Do NOT reintroduce a score/sortino/edge_ratio-derived win-probability formula at any call site.

**Meta-Label Probability — Stage 4 Scaffold:**
- `SignalOutput.meta_label_proba: float = 1.0` — Lopez de Prado meta-label probability, always 1.0 until Stage 4.
- `SignalAggregator.aggregate()` returns a **6-tuple**: `(final_score, score_log, warnings, details, outputs, meta_label_composite)`. The 6th element is the geometric mean of active modules' `meta_label_proba` values. Always 1.0 currently (no-op).
- `StrategyEngine.evaluate_security()` multiplies the Kelly Target by `meta_label_composite` (and regime multiplier) before the MAX_POSITION_WEIGHT clamp.
- Any code that unpacks `aggregate()` must now unpack 6 elements, not 5.
  - **sizing/kelly.py** — `estimate_win_rate_and_payoff(closed_trades_df, lookback_trades=100) -> (p, b, n_trades)`: estimates win probability and payoff ratio from the most recent closed trades (by `exit_ts`) in `transactions_store.TransactionsStore.closed_trades_df()`. Returns `(NaN, NaN, n)` if `n_trades < 30` (`MIN_TRADES_REQUIRED`) or if there are no losing trades in the sample (b undefined) — logs an error below 30, a warning below 50 (`MIN_TRADES_FOR_CONFIDENCE`). `fractional_kelly(p, b, fraction=0.5, cap=0.20) -> float`: `f* = (p*b - (1-p))/b`, scaled by `fraction` and capped at `cap`; returns NaN if `p`/`b` is NaN so callers can detect "insufficient history" and fall back.
  - **sizing/vol_target.py** — `volatility_target_weight(realized_vol, target_vol=0.10, max_leverage=2.0) -> float`: `target_vol/realized_vol`, capped at `max_leverage`, saturating at the cap (not dividing by zero) when `realized_vol <= 0`. `portfolio_vol_target(positions, cov_matrix, target_vol, max_leverage) -> dict[symbol, weight]`: scales a raw position vector by one scalar so `sqrt(w^T Σ w) == target_vol` (capped); symbols missing from `cov_matrix` are excluded and explicitly set to `0.0` (logged), never fabricated.
  - `StrategyEngine.__init__` now accepts an optional `transactions_store` (DI for tests, e.g. `TransactionsStore(db_url="sqlite:///:memory:")`); defaults to lazily constructing a real `TransactionsStore()`. `_calculate_kelly_sizing(realized_vol)` is the single source-of-truth sizing call: if `estimate_win_rate_and_payoff` returns real `(p, b)`, sizing = `fractional_kelly(p, b, fraction=settings.KELLY_FRACTION, cap=settings.KELLY_CAP)`; otherwise (insufficient trade history) it logs a warning and falls back to `volatility_target_weight(realized_vol, target_vol=settings.VOL_TARGET, max_leverage=settings.MAX_LEVERAGE)` — **no Kelly multiplier** in the fallback. Either path is then clamped to `settings.MAX_POSITION_WEIGHT` (1.0) via `_raw_kelly_or_vol_target_sizing()` — a hard single-name ceiling independent of sizing methodology, since the vol-target fallback alone can otherwise reach `MAX_LEVERAGE` (2.0x). `main_orchestrator.py` now reads `strategy_output['Kelly Target']` directly (the duplicate `ee.calculate_kelly_target(...)` call + score-bracket override at that call site is gone). `realized_vol` is sourced from `garch_vol` (the per-ticker GJR-GARCH volatility already flowing into `evaluate_security`).
  - **Live data note:** the committed `quant_platform.db` **ships with an empty `trades` table** (0 rows). The closed-trade population that feeds `_calculate_kelly_sizing()` is **reconstructed on demand** (PURE FIFO lot-matching of a Robinhood account's filled equity order history) by `data/robinhood_orders.py` (Tier 7), and accumulates live as advisory runs call `record_trade()`. Until at least `MIN_TRADES_REQUIRED` (30) closed trades exist, `_calculate_kelly_sizing()` takes the **vol-target fallback path** (not the real Kelly path). Tests that assert on `Kelly Target` should still inject `TransactionsStore(db_url="sqlite:///:memory:")` explicitly for determinism (see `tests/test_quantitative_models.py::test_garch_and_edge_scoring` for the pattern) so they never depend on whatever rows happen to be in the on-disk DB.
- **simulation_engine.py** — `vectorbt` parameter-sweep optimization plus `backtrader` event-driven backtests (0.1% commission / 0.05% slippage standard). Integrates survivorship bias warning prints using `universe_engine.py`. New strategies should be proven here before being wired into `strategy_engine.py`.
- **universe_engine.py** — S&P 500 point-in-time universe loader. Scrapes Wikipedia current and changes tables to reconstruct index constituents, loads local delistings from `data/delisted_tickers.csv`, and reports estimated survivorship bias.
- **research_engine.py** — realized slippage, CoVaR tail-dependency proxy, Brinson-Fachler attribution, MFE/MAE, portfolio heat.
- **evaluation_engine.py** — strategy performance evaluation.
- **transactions_store.py** — SQLite-backed transactions database using SQLAlchemy for tracking active/closed trades.
- **database_setup.py** — builds the SQLite schema (`DailySignals`, `ExecutionLogs`) dynamically from `config.COLUMN_SCHEMA`; never hardcode SQL column lists.
- **reporting_engine.py** / **diagnostics_and_visuals.py** / **daily_report_template.html** — HTML report generation/rendering. `diagnostics_and_visuals.generate_html_report(portfolio_data, regime, output_path, *, yield_curve, credit_spread, sahm_rule, real_yield, audit_log=None, account_summary=None)` is the **active** report path (called by BOTH `main.py` and `main_orchestrator.py`; `reporting_engine.py`'s `daily_report_template.html` path is NOT wired into either entry point). **Report redesign (2026-06):** the embedded `HTML_REPORT_TEMPLATE` leads with **Holdings & P&L** (shares, avg cost, current price, market value, signed unrealized P&L $/%) and **Action & Rationale** (action signal w/ colour class + conviction meter, suggested position %, click-to-expand plain-English rationale). New optional keyword `account_summary: dict` renders a top **portfolio summary band** (total equity, buying power, total unrealized P&L, dividends received, position count, BUY/HOLD/SELL tally); when `None` (the `main_orchestrator.py` path) the band is hidden and rows degrade to "—" placeholders. Backward-compatible: field normalization accepts BOTH spaced pipeline keys (`"Action Signal"`, `"Robinhood Unrealized PL"`) and underscored advisory keys (`"Action_Signal"`, `"UnrealizedPL"`); market value / unrealized P&L are **derived** (`shares×price`, `(price−avgCost)×shares`) only when the Robinhood snapshot fields are absent (never fabricated for non-held symbols — those render "—"). Dependency-free client-side **search box + sortable columns** (vanilla JS, no DataTables/jQuery). `main.py._write_html_report()` sources holdings/P&L from the Robinhood `AccountSnapshot` positions (source of truth for account state, CONSTRAINT #4) and builds `account_summary` defensively (a degraded/empty snapshot omits the band, never aborts the report). Covered by `tests/test_html_report.py`.
- **execution/cost_model.py** — Tiered execution cost model and Backtrader CommissionInfo (SEC §31, FINRA TAF, tiered spreads, market order slippage).
- **execution/broker_base.py** — `BrokerBase` ABC: `OrderSide`, `OrderType`, `OrderStatus` enums; `OrderIntent`, `OrderResult`, `AccountSnapshot`, `PositionSnapshot`, `TradeUpdateEvent` dataclasses; six abstract async methods (`submit_order`, `cancel_order`, `get_open_positions`, `get_account`, `get_orders`, `stream_trade_updates`). All broker-facing strategy/orchestrator code must type-annotate against this interface.
- **execution/alpaca_broker.py** — `AlpacaBroker(BrokerBase)` using alpaca-py. Reads `settings.ALPACA_API_KEY/SECRET_KEY/PAPER`. Supports equity market/limit orders and multi-leg options via `OptionLegRequest`. Streams trade updates via `TradingStream`. Dry-run path: logs intent, returns `ACCEPTED` without touching the broker network.
- **execution/kill_switch.py** — File-based global kill switch. `KILL_SWITCH_FILE = settings.OUTPUT_DIR / "KILL_SWITCH"` is the sentinel. `GlobalKillSwitch.is_active()` checks file presence; `activate(reason)` writes atomically (write-then-rename); `deactivate()` removes the file. `KillSwitchActiveError(RuntimeError)` is raised by `OrderManager.submit_order_with_idempotency` BEFORE any pre-trade check when the file exists. CLI: `python -m execution.kill_switch --activate/--deactivate/--status`. `FLATTEN_ON_KILL=true` logs a CRITICAL reminder (automatic flattening is a future extension).
- **execution/risk_gate.py** — Synchronous ten-check pre-trade risk pipeline. `RiskContext` dataclass holds per-call live state (account, open_positions, macro DTO, returns_df, start_of_day_equity, validation_reports, is_premium_sell_strategy, current_prices, timestamp). `PreTradeRiskGate.run_all(intent, context)` short-circuits at the first failure; the tenth check (`max_order_rate_check`) is always last so blocked orders never consume rate-limit budget. Checks in order: (1) max_position_size, (2) portfolio_heat, (3) max_correlation, (4) daily_loss_limit, (5) macro_kill_switch, (6) hmm_regime, (7) stress_scenario, (8) market_hours (NYSE RTH 09:30–16:00 ET via `zoneinfo`), (9) minimum_validation, (10) max_order_rate. Missing context → conservative pass (never blocks on missing data). Thresholds injectable at construction; default to `settings.*` counterparts.
- **execution/order_manager.py** — `OrderManager` sitting between the signal pipeline and the broker adapter. Responsibilities: (1) kill-switch gate (raises `KillSwitchActiveError` BEFORE dedup); (2) deterministic `client_order_id` via SHA-256 of `(strategy_id, symbol, side, qty, 60s-bucket)` for idempotency; (3) `PreTradeRiskGate.run_all()` — returns `ERROR` OrderResult if blocked; (4) single linear-back-off retry on transient broker errors; (5) `reconcile_state(transactions_store)` — compares broker ground truth vs. internal `TransactionsStore`; logs CRITICAL + fires webhook on drift. `make_client_order_id()` is module-level (usable by callers independently). Dry-run: intercepted in `_submit_with_retry` before broker contact.
- **execution/queue_builder.py** — **Tier 8 Robinhood execution bridge.** Emits a GATED, DRY-RUN proposed-order queue (`output/execution_queue.json`) for a Claude Code agent that consumes the Robinhood Trading MCP (the headless pipeline cannot call MCP tools). NEVER contacts a broker. Reuses `OrderIntent`/`PreTradeRiskGate.run_all`/`GlobalKillSwitch`/`make_client_order_id` to gate each intent in dry-run. `emit_execution_queue` returns `None` (writes nothing) when `settings.ROBINHOOD_EXECUTION_MODE=off`; `build_execution_queue` computes `allow_place = mode=="live" AND gate_allowed AND not kill_switch_active AND notional-cap-set` (structurally False otherwise); `gate_intent` fails CLOSED on exception. AST-safe function names (no `place_*`/`submit_order`). See the Tier 8 section below.
- **observability/__init__.py** — Empty package marker.
- **observability/alerts.py** — `send_alert(level, message, channels=None, extra=None) -> None` dispatches to zero or more output channels: `console` (always active via Python logging), `file` (JSONL at `settings.ALERT_FILE_PATH`), `discord` (`settings.DISCORD_WEBHOOK_URL`), `slack` (`settings.SLACK_WEBHOOK_URL`), `email` (SMTP via `settings.ALERT_SMTP_*`). `_active_channels()` auto-detects configured channels. Discord payload: `{"content": "🚨/⚠️/ℹ️ **[LEVEL]** \`timestamp\`\nmessage"}`. Slack payload: `{"text": "..."}`. Email: MIMEText via `smtplib.SMTP` STARTTLS. All channel failures are caught and logged — a broken webhook must never crash the pipeline. Alert rules: CRITICAL for kill switch, reconciliation drift, broker lost, missing validation report; WARNING for heat >5%, correlation concentration, large slippage; INFO for fills, rebalance. `send_daily_summary(pnl_summary, warnings)` composes a multi-line INFO alert with P&L and warnings.
- **observability/dashboard.py** — Streamlit dashboard (`streamlit run observability/dashboard.py`). Refresh interval: `settings.DASHBOARD_REFRESH_SECONDS` (default 30). Reads: `output/state_snapshot.json` (live macro + signals), `output/risk_gate_blocks.jsonl` (last 100 blocks), `output/heartbeat.txt` (staleness check), `reports/*_validation_summary.json` (deployability), `quant_platform.db` (trades + positions), and **`cache/account_snapshot.json`** (Robinhood holdings + P&L, via `_load_account_snapshot()`). Panels: kill switch status banner (red/green), macro regime + VIX + HMM risk-on, **Account Holdings & P&L** (Total Equity / Buying Power / Unrealized P&L / Dividends metrics + per-position table with green/red-coloured unrealized P&L via the `_style_holdings()`/`_color_pnl()` pandas `Styler`), strategy P&L, open positions vs. pipeline signals, portfolio heat/gross/net exposure, validation report status, recent closed trades, risk gate block log. Sidebar has a **🔄 Refresh now** button (`st.cache_data.clear()` + `st.rerun()`) for on-demand refresh without waiting for the TTL. `@st.cache_data(ttl=...)` gates all data loads. Auto-refreshes via `time.sleep(N)` + `st.rerun()`. **Streamlit API note:** all `st.dataframe` calls use `width='stretch'` (the `use_container_width` kwarg is deprecated and removed after 2025-12-31).
- **gui/** — **InvestYo Command Center**: a local-first, on-demand Streamlit operational suite over the full quant lifecycle (`streamlit run gui/app.py` or `./launch_gui.command`). Read-only / file-backed by design — it **never calls async broker code directly**; it launches `main_orchestrator.py` as a **subprocess** and consumes the file-backed state the orchestrator writes (`output/state_snapshot.json`, `output/heartbeat.txt`, `output/KILL_SWITCH`), exactly like `observability/dashboard.py`. Modules:
  - **gui/app.py** — entry point. Repo-root `sys.path` shim + `load_dotenv(override=False)`, `st.set_page_config`, sidebar, and 9 `st.tabs`. Every tab body is wrapped by `safe_panel()` so one panel's exception renders an inline error box instead of crashing the app (dead-letter UI; CONSTRAINT #6).
  - **gui/env_io.py** — safe, allowlist-bounded `.env` read/write for the Settings & Strategy-Matrix tabs. `ALLOWED_KEYS` (NON-secret tunables only) are writable; `SECRET_KEYS` (API keys, passwords, TOTP, webhooks) are **masked** by `read_settings()`/`mask_secret()` and raise `SecretWriteError` if a write is attempted (CONSTRAINT #3). Non-allowlisted keys raise `DisallowedKeyError`. List/dict tunables (`DEFAULT_TICKERS`, `SIGNAL_WEIGHTS`, `DISABLED_SIGNAL_MODULES`) are JSON-encoded so pydantic-settings re-parses them. `MACRO_REGIME_GATE_ENABLED` is in the Risk gate section of `ALLOWED_KEYS`. Uses `dotenv.dotenv_values` + `dotenv.set_key` (preserves comments/other lines). Writes take effect on the **next** launch (no hot-reload).
  - **gui/orchestrator_runner.py** — `launch_orchestrator(dry_run, refresh_account) -> RunHandle` spawns `[sys.executable, "main_orchestrator.py", ...]` via `subprocess.Popen` (non-blocking, stdout→`output/gui_run.log`). `compute_stage_status()` derives coarse Data Acquisition/Processing/Forecasting/Execution stage status from log markers + `heartbeat.txt` freshness + `state_snapshot.json` mtime. `read_log_tail()` / `heartbeat_age_seconds()` feed the Launcher tab.
  - **gui/panels.py** — one `render_*` function per tab: Launcher & Orchestration; Report Viewer (`evaluation_engine.EvaluationEngine` heat/edge/Brinson-Fachler + report export); Settings Manager; Strategy Matrix & Risk Gating (enumerates `signals.registry.global_registry.get_all()`, edits weights, toggles modules → `DISABLED_SIGNAL_MODULES`, controls `execution.kill_switch.GlobalKillSwitch`); Paper-Trading Monitor (`data.robinhood_portfolio.fetch_account_snapshot()` account truth vs. pipeline projection, source-labeled per CONSTRAINT #4); Gravity Audit Logs (runs `Gravity AI Review Suite.py` as a subprocess, parses trailing JSON, shows pass/fail); Technical Options Matrix (**rebuilt**: hydrated premium-selling matrix via `technical_options_engine.build_premium_directive`; auto-iterates the union of held Robinhood positions + watchlist + last pipeline signals via `_active_symbols()` so no premium-selling opportunity is dropped; renders Sigma_GARCH, IVR_Proxy, Aroon+Coppock trend bias, Strategy/Action directive, Short_/Long_Strike + Delta legs, Net_Premium, Realizable_Daily_Theta, ATM Greeks, and a per-symbol Integrity_OK verdict from `validate_directive_integrity`; the snapshot's macro state — VIX + market regime — is forwarded into the engine so `CREDIT EVENT` / `VIX>30` regimes fail-closed to Cash/Wait identically to the live orchestrator path); Market Data (provider source/freshness/cache); **Live Inventory** (Task 1.4 — `data.portfolio_sync.build_sync_report()` view: holdings ∪ RH watchlists ∪ file watchlists with per-symbol `CoverageStatus` + cost-basis delta + forecast-availability flag + watchlist memberships; "🔄 Sync Now" button runs `async_sync_now()` to refresh the universe and persist it as `DEFAULT_TICKERS` in `.env` via `gui/env_io.py` without restarting the orchestrator); **Observability / Mission Control** — 4-section panel: system-health bar (kill switch / regime / VIX / HMM), **Macro Regime Gate toggle** (writes `MACRO_REGIME_GATE_ENABLED` via `gui/env_io`; shows persistent red warning banner when gate is off; changes take effect on next orchestrator launch), recession-indicator telemetry (Sahm Rule / HY OAS / yield curve / VIX with colour-coded threshold badges sourced from `output/state_snapshot.json`), strategy P&L. Shared cached loaders mirror `observability/dashboard.py`.
  - **launch_gui.command** — macOS double-click launcher (mirrors `launch.command`: verify `.venv` + Python 3.12 + streamlit, warn on missing `.env`, then `streamlit run gui/app.py`, pause on exit).
- **scripts/__init__.py** — Package marker for the `scripts/` directory.
- **scripts/preflight_check.py** — Programmatic pre-live readiness gate. Each of 16 checks returns a `CheckResult(name, passed, reason, warning)`. Exits code **0** only when ALL checks pass. CLI: `python scripts/preflight_check.py [--json] [--skip check1 check2 ...]`. Checks in order: `fred_key_configured`, `key_rotation_recent` (warning-only — FRED key age > 90 days), `alpaca_key_rotation_recent` (warning-only — Alpaca key age > 90 days; **auto-skipped when `ADVISORY_ONLY=True`** because Alpaca keys have no blast-radius risk while the broker surface is quarantined), `advisory_only_active`, `alpaca_configured`, **`macro_regime_gate_enabled`** (blocking failure when `MACRO_REGIME_GATE_ENABLED=false` AND `ALPACA_PAPER=false`; warning-only in paper mode), `alpaca_paper_mode` (warning-only, not blocking), `dry_run_disabled`, `env_not_committed`, `kill_switch_inactive`, **`state_snapshot_fresh`** (< 2 h — cross-mode liveness indicator; both `main.py` and `main_orchestrator.py` write `output/state_snapshot.json`; NOT auto-skipped in advisory mode because it IS the advisory liveness check), `heartbeat_fresh` (< 2 h — orchestrator-only; **auto-skipped when `ADVISORY_ONLY=True`**), `db_exists`, `paper_trading_duration` (≥ 90 days — requires `PAPER_TRADING_START_DATE` in `.env`), `validation_reports` (all deployable + < 30 days old; **auto-skipped when `ADVISORY_ONLY=True`** — gates live order submission, not advisory signals), `no_unexpected_risk_blocks` (no `minimum_validation` blocks in last 24 h). All checks catch exceptions rather than propagating them. `_ADVISORY_AUTO_SKIP: dict[str, str]` now has **8 entries** — each with a distinct per-check reason string (not a generic message). `_ADVISORY_AUTO_SKIP` (8 entries): `alpaca_configured`, `alpaca_paper_mode`, `dry_run_disabled`, `paper_trading_duration`, `alpaca_key_rotation_recent` (broker-dependent) + `heartbeat_fresh`, `validation_reports`, `no_unexpected_risk_blocks` (advisory false-positives). **Stage 2 (2026-06):** added `check_state_snapshot_fresh` as check #11, expanded `_ADVISORY_AUTO_SKIP` from 4 to 7 entries, replaced all `datetime.utcnow()` with `datetime.now(timezone.utc)`. **Stage 3 (2026-06):** added `check_alpaca_key_rotation_recent` as check #3, auto-skipped under `ADVISORY_ONLY=True`; expanded `_ADVISORY_AUTO_SKIP` to 8 entries. Gravity step_67 audits the Alpaca rotation check.
- **docs/GO_LIVE_CHECKLIST.md** — Markdown go-live checklist. `python scripts/preflight_check.py` covers all automatable items; manual items are marked `(manual)`. Sections: Security, Strategy Validation, Paper-Trading Track Record, Kill Switch & Risk Gate, Alerts & Observability, Data Integrity, Capital & Sizing, Final Sign-Off.
- **docs/RUNBOOK.md** — Operational runbook (Tier 5.2 rewrite, 2026-06). Advisory-mode default is foregrounded at the top. §0 everyday startup. §1 paper→live switch (marked **⚠ N/A in advisory mode** while `ADVISORY_ONLY=true`; procedure retained for when quarantine is lifted). §1.1 ntfy push alerts. §2 daily pre-market advisory checklist. §3 incident response — **§3.1 stale account snapshot, §3.2 missing recommendation for held symbol, §3.3 calibration score dropping below threshold** (three advisory-relevant playbooks) + §3.4 validation report missing, §3.5 portfolio heat, §3.5b RH_USERNAME env bug, §3.6 HMM risk-off, §3.7 GJR-GARCH warning, §3.8–3.10 broker incidents (retained with **⚠ N/A in advisory mode** markers for completeness). §4 contacts. §5 maintenance schedule. §6 advisory pause-and-restart procedure (replaces the broker emergency-shutdown; uses the kill-switch sentinel as a pause-recommendations gate).
- **validation/purged_cv.py** — Combinatorial Purged Cross Validation (CPCV) logic with purging and embargo support.
- **validation/metrics.py** — Deflated Sharpe Ratio (DSR), Probability of Backtest Overfitting (PBO), and CPCV path evaluation runner.
- **validation/harness.py** — Master Strategy Validation Harness. Evaluates walk-forward stability, CPCV path metrics, and generates Jinja2 HTML validation reports. `StrategyValidationHarness.__init__` accepts `is_options_selling: bool = False` and `stress_returns_fn: Optional[Callable[[str,str], pd.Series]] = None`; when options-selling, `run()` replays the strategy across the tail scenarios (`validation/stress_scenarios.py`), attaches them to the report, prints the stress summary at the top, and renders a stress section in the HTML. `ValidationReport` carries `is_options_selling` + `stress_test_results` and exposes `stress_gate_passed`; its `deployable` property ANDs the existing PBO/DSR/Sharpe/MaxDD gates with the stress gate (the latter only applies to options-selling strategies and fails closed when stress results are missing).
- **validation/stress_scenarios.py** — Tail-scenario stress testing for negatively-skewed options-selling strategies (whose rare-but-violent losses the full-sample MaxDD gate cannot catch). `STRESS_SCENARIOS` registers four dated shock windows — `OCT_2008` (Lehman, VIX>80), `FEB_2018` (Volmageddon/XIV blowup), `MAR_2020` (COVID crash+rebound), `AUG_2024` (yen carry unwind) — each a `StressScenario(start, end, expected_max_dd_for_short_vol, description)`; `.as_tuple()` yields the `(start, end, expected_max_dd_for_short_vol_strategies)` form. A caller supplies `returns_fn(start, end) -> pd.Series` (daily strategy returns for the window); `run_stress_tests()` produces `{name: StressResult}`. `compute_max_drawdown()` returns NaN (never fabricated 0.0) on empty data; `account_survived()` flags a blow-up if any daily return ≤ -100% or compounded equity hits 0. `passes_stress_gate(results)` = True iff every canonical window is present AND each `StressResult.passed` (survived AND `max_drawdown < MAX_STRESS_DRAWDOWN`=0.50) — fails closed on missing/errored windows. `format_stress_summary()` renders the top-of-report block.
- **signals/** — Pluggable quantitative signal modules package.
  - **signals/base.py** — ABC `SignalModule`, `SignalContext`, and `SignalOutput` dataclasses. `SignalContext` carries an optional `xsec_percentile_ranks: dict[str, float]` field populated by cross-sectional modules. `SignalModule` exposes a default no-op `pre_compute(universe_df, context)` hook for cross-sectional pre-ranking.
  - **signals/registry.py** — `SignalRegistry` managing the registration and discovery of modules. Exposes `run_pre_compute(universe_df, context)` which calls each module's `pre_compute` hook once per cycle.
  - **signals/aggregator.py** — `SignalAggregator` aggregating individual signal outputs via a weighted sum.
  - **signals/timeseries_momentum.py** — Moskowitz/Ooi/Pedersen time-series momentum SignalModule.
  - **signals/cross_sectional_momentum.py** — Jegadeesh-Titman (1993) cross-sectional momentum SignalModule. Uses the two-phase `pre_compute` / `compute` hook pattern: `pre_compute` runs once to rank the full universe; `compute` reads the rank from `context.xsec_percentile_ranks`. Score = `2 * (rank - 0.5)` ∈ [-1, +1]. Default weight = 15.0.
  - **signals/rsi2_mean_reversion.py** — Connors-style RSI(2) long-only mean-reversion SignalModule. Unlike every other module, its score is in `[0.0, 1.0]` (long-only, no short analogue), not `[-1.0, 1.0]`. Trend filter: `Close > SMA_200`. Entry: `RSI_2 < 10`, scaled linearly to 1.0 as RSI(2)→0. Already-reverted guard: `Close > SMA_5` forces score to 0 (the bounce already happened). Overrides `is_active_in_regime()` to return `False` (fully suppressed, not just down-weighted) when `market_regime` is `RECESSION`/`CREDIT EVENT` or `vix > 30` — mean reversion is regime-fragile. Default weight in `settings.SIGNAL_WEIGHTS` is `10.0`, matching the established weight scale of the other modules (10–45). See `tests/test_validation_rsi2.py` docstring for an empirical backtest finding: the long-only trend filter alone already fully excludes 2008 exposure for this strategy, before the regime gate even applies — the gate's mitigating effect is load-bearing only for 2020 in that backtest.
  - **signals/multifactor.py** — Fama-French-style multifactor SignalModule (Value, Quality, Low-Vol, Size — Hou-Xue-Zhang (2020) priors; momentum is the separate `cross_sectional_momentum` module). Two-phase hook pattern: `pre_compute(universe_df, context)` reads raw factor inputs (`book_to_market`, `earnings_yield`, `quality_factor_score`, `low_vol_score`, `log_market_cap`, `Market Cap` — written into `dashboard_df` by `processing_engine.calculate_fundamental_metrics()`), excludes tickers with `Market Cap < settings.MULTIFACTOR_MICROCAP_THRESHOLD` (default $300M) from the cross-sectional z-scoring population entirely, z-scores each input and winsorizes at ±3 (`_zscore_winsorize()`), averages `book_to_market_z`/`earnings_yield_z` into `Value_Z`, negates the size z-score into `Size_Z` (smaller = positive), and averages all four into a `Multifactor_Composite` (re-clipped to ±3). Stores per-ticker results in `context.multifactor_scores`. `compute(row, context)` maps `Multifactor_Composite` to `[-1, +1]` via `tanh(z / 2)`; microcap-excluded or data-unavailable tickers get a neutral `0.0` score (never fabricated exposure). Default weight in `settings.SIGNAL_WEIGHTS` is `15.0` — note this deviates from the `0.15` originally specified for this task; it was rescaled to match this codebase's existing points-scale weight convention, where `contribution = score[-1,1] * weight` and other modules' weights range 10–45 (0.15 would be numerically inert at that scale).
  - **signals/regime_multiplier.py** — `RegimeMultiplierSignal` deliberately carries NO directional alpha: `compute()` always returns `score=0.0` regardless of input, so it contributes nothing to `SignalAggregator`'s weighted-sum `final_score` even with a nonzero weight (its `settings.SIGNAL_WEIGHTS["regime_multiplier"] is explicitly `0.0`, structurally enforcing this, not just by convention). It carries `context.macro.hmm_risk_on_probability` (the `regime/hmm_regime.py` second opinion) through its `confidence` field instead — defaulting to `1.0` (neutral) when the HMM didn't run. `StrategyEngine.evaluate_security()` reads `outputs['regime_multiplier'].confidence` from `aggregator.aggregate()`'s returned `outputs` dict (already exposed for introspection) and multiplies the final Kelly Target by it, then re-clamps to `settings.MAX_POSITION_WEIGHT` — this is a position-sizing scalar, not a score input, and is the only signal module wired this way.
  - **volatility/iv_engine.py** — extracts ATM option implied volatilities, handles calendar-30-day linear interpolation, lookahead-free true IVR, and Volatility Risk Premium (VRP) calculation.
  - **volatility/bootstrap_iv_history.py** — CLI script to backfill historical ATM IVs in `iv_history`.
  - **pairs/cointegration.py** — implements Engle-Granger ADF cointegration, AR(1) mean reversion half-life estimation, and rolling ADF p-value checks.
  - **pairs/kalman_hedge.py** — dynamic hedge ratio tracker/filter (batch Kalman Filter and step-by-step Kalman tracking) using `pykalman`.
  - **pairs/simulation.py** — event-driven Backtrader backtest runner for pairs.
  - **signals/pairs_trading.py** — pairs trading signal generator.
- **ml/** — Machine learning pipeline in a three-tier qlib-style architecture (no qlib dependency). Three sub-packages:
  - **ml/data/** — Point-in-time feature store and label construction. `PITFeatureStore` caches daily cross-sectional snapshots as Parquet files under `ml/data/cache/`. `build_meta_features(base_df, primary_score)` extends the base PIT feature matrix with the primary signal's own score for MetaLabeler training.
  - **ml/models/** — Model ABC (`ml/models/base.py`) with abstract `fit(X, y, t1)/predict(X)/save/load` interface. Both `LGBMCrossSectionalRanker` and `MetaLabeler` implement this ABC so the strategy layer can consume them uniformly.
  - **ml/strategies/** — `StrategySpec` data container linking a Model to a `signal_id` and flagging whether it is a meta-labeler.
  - **ml/registry.yaml** — Production model registry: model role, path, `trained_date`, `cpcv_dsr`, `pbo`, `deployable` flag. Updated by the monthly retraining job.
  - **ml/triple_barrier.py** — Lopez de Prado (AFML Ch. 3) triple-barrier labeling. Three functions:
    - `get_volatility(close, span=100) -> pd.Series` — Daily EWMA vol from log-returns (strictly PIT: `adjust=False`, causal).
    - `cusum_filter(close, threshold) -> pd.DatetimeIndex` — Sequential CUSUM event sampler (inherently sequential; scalar loop is correct here). Returns timestamps where cumulative log-return drift first crosses ±threshold. Raises `ValueError` for empty series or threshold ≤ 0.
    - `apply_triple_barrier(events, close, pt_sl_multiples=(2.0,1.0), vertical_barrier_days=5) -> pd.DataFrame` — For each event at t₀: sigma is computed from `get_volatility(close[:t₀])` (PIT), upper/lower barriers are price-based (`entry * (1 ± mult * sigma)`), vertical = t₀ + N business days. Label: +1 (upper), -1 (lower), 0 (vertical). Returns DataFrame indexed by t₀ with columns `[t1, barrier_hit, label, entry, upper_level, lower_level]`. Perturbing `close[t₀+1:]` never changes barriers for event at t₀.
  - **ml/meta_labeling.py** — `MetaLabeler(signal_id, lgbm_params)` binary LightGBM classifier predicting P(primary_signal_correct). Implements Model ABC. Key methods: `fit_from_primary(X, y_primary, y_barrier)` builds meta-label target (1 = direction correct, 0 = wrong/vertical), filters neutral-signal events, trains. `predict_proba_scalar(X_today) -> float` returns the mean P(correct). Returns 1.0 (neutral/no-op) before training. Monthly retraining: `needs_retrain(retrain_freq_days=30) -> bool`. Saved as `ml/models/meta_<signal_id>_<YYYYMMDD>.pkl`. `MetaLabelerRegistry` (global singleton `global_meta_registry`) maps `signal_id -> MetaLabeler`; imported by `signals/aggregator.py`.
  - **`SignalAggregator` Stage 4 wiring (signals/aggregator.py)**: For each active signal module, the aggregator now checks `global_meta_registry.has(name)`. If a MetaLabeler is registered, it calls `get_proba(name, feature_row)` where `feature_row` is the current `row` Series as a DataFrame plus `primary_score = output.score`. If `proba < settings.META_LABEL_MIN_CONFIDENCE` (default 0.4), a `meta_hard_gate` flag is set and `meta_label_composite` is forced to exactly `0.0` (which zeroes the Kelly Target for this cycle). When no MetaLabelers are registered (the default), behavior is identical to pre-Stage-4 (all `meta_label_proba = 1.0`, composite = 1.0).
  - **`settings.META_LABEL_MIN_CONFIDENCE`** — New setting (default 0.4). The P(correct) threshold below which the meta hard gate fires. Controlled via `.env`.
- **reports/cpcv_report.html.j2** — Plotly/Jinja template for CPCV and overfitting validation reports.
- **reports/validation_report_template.html.j2** — Jinja2 template for rendering validation reports.
- **ai_verification_prompts.py** ("Gravity AI Auditor") — 6-step static-analysis + simulation sandbox (via OpenAI/Anthropic) that strategies must pass before deployment.
- **tests/lookahead_check.py** — utility exposing `verify_no_lookahead` to detect and prevent lookahead bias in quantitative indicators.
- **tests/test_indicators_lookahead.py** — lookahead perturbation tests for RSI, MACD, ATR, Aroon, Chandelier, and RS-MACD.
- **tests/test_forecasting_lookahead.py** — tests to ensure `MinMaxScaler` in the forecasting engine is fit strictly on training partitions.
- **tests/test_universe.py** — unit, integration, and point-in-time lookahead checks for `universe_engine.py`.
- **tests/test_dsr.py** / **tests/test_pbo.py** / **tests/test_cpcv_paths.py** — unit tests for overfitting validation metrics and splitters.
- **tests/test_cost_model.py** / **tests/test_cost_integration.py** — unit and integration tests for execution cost modeling.
- **tests/test_risk_gate.py** — unit tests for all 10 `PreTradeRiskGate` checks (happy path, failure path, missing-context conservative pass). Integration test: `run_all()` short-circuits at first failure; rate-limit counter not incremented for blocked orders.
- **tests/test_kill_switch.py** — lifecycle tests for `GlobalKillSwitch` (activate/deactivate/reason/idempotency); `OrderManager` raises `KillSwitchActiveError` before broker contact; deactivating re-enables orders; kill switch checked before idempotency dedup.
- **tests/test_correlation_check.py** — focused tests for `max_correlation_check`: high positive correlation blocked, low correlation passes, high negative correlation blocked (|r| check), configurable threshold, conservative-pass edge cases (no returns, empty frame, no positions, symbol missing, <20 observations), multi-position scenario (any single breach blocks).
- **tests/test_alerts.py** — unit tests for `observability/alerts.py`: console logs at the correct level; file channel writes/appends JSON-lines with correct fields; Discord posts correct JSON payload + `application/json` header, HTTP errors are swallowed; Slack posts `{"text": ...}` payload with emoji prefix; email sends to all recipients with level in subject via mocked SMTP; `send_daily_summary` calls `send_alert`; unconfigured channels silently skipped.
- **tests/test_preflight.py** — unit tests for `scripts/preflight_check.py`: every check independently produces PASS or FAIL with non-empty reason; edge cases for missing files, stale heartbeat, expired reports, invalid dates, active kill switch; `run_checks(skip=[...])` marks skipped checks as passed; `main()` exits 0 on all-pass, 1 on any failure; `--json` output is a valid JSON array with `name`/`passed`/`reason` keys.
- **tests/test_rsi2.py** / **tests/test_rsi2_regime_gate.py** — unit tests for `RSI2MeanReversionSignal`'s trend filter, entry scoring, already-reverted guard, and `is_active_in_regime` regime gate (including end-to-end suppression through `SignalAggregator`).
- **tests/test_kelly.py** / **tests/test_vol_target.py** / **tests/test_kelly_no_history.py** — unit tests for `sizing/kelly.py` and `sizing/vol_target.py`'s known-scenario formulas, trade-history estimation edge cases, and the end-to-end fallback-to-vol-target-only path (via an injected in-memory `TransactionsStore`) when fewer than 30 closed trades exist.
- **tests/test_validation_rsi2.py** — backtests the RSI(2) strategy (gated vs. ungated) over real SPY history (2000–2023) via `validation.harness`. Reconstructs an equivalent RISK-OFF condition from price data alone (5-day return < -6% as a fast VIX-spike proxy, plus >20% drawdown from a trailing 1-year high) since replaying the live FRED-based regime gate over 23 years needs a live `FRED_API_KEY`. Documents an empirical finding: the strategy's own long-only trend filter already fully excludes 2008 exposure, so the regime-gate-mitigation assertion is load-bearing only for 2020.
- **tests/test_multifactor.py** — unit tests for `signals/multifactor.py`: a synthetic 50-stock universe with engineered high-value/high-quality/low-vol exposures recovers those names in the top quintile of `Multifactor_Composite`; winsorization tests confirm an extreme outlier is clipped to ±3 and does not dominate the cross-section; microcap-exclusion tests confirm an excluded ticker neither skews peers' z-scores nor receives a fabricated score; ABC-conformance and registry checks.
- **tests/test_validation_multifactor.py** — runs a Low-Vol + Size multifactor proxy through `validation.harness` over real historical prices (2005–2023) for a 10-ticker representative cross-section. **Documented scope limitation**: Value and Quality (book-to-market, earnings yield, ROE) require point-in-time historical fundamentals, which yfinance's `.info` does not provide (current-snapshot only) and no free vendor supplies — faking 18 years of that history would violate the "no fabricated metrics" constraint, so this harness test is restricted to the two factors honestly derivable from real price/share-count data; Value/Quality correctness is instead covered exactly by the engineered synthetic universe in `tests/test_multifactor.py`.
- **tests/test_triple_barrier_lookahead.py** — two no-lookahead proofs for `ml/triple_barrier.py`: (1) barriers at event t are identical whether sigma is computed from `close[:t]` or the full series at index t; (2) perturbing prices strictly after t does not change any barrier level or the entry price at t.
- **tests/test_triple_barrier_labels.py** — hand-crafted price paths with known outcomes: upper hit (label=+1), lower hit (label=-1), vertical timeout (label=0), first-touch-wins ordering, and output schema conformance.
- **tests/test_cusum_filter.py** — CUSUM filter: monotonic event ordering, threshold control, flat-price no-events, all events within close index, ValueError on invalid inputs.
- **tests/test_meta_labeler_uplift.py** — MetaLabeler: documented precision@50 uplift over a 60% base-rate synthetic signal (≥+0.05), proba in [0,1], neutral (1.0) before training, `fit_from_primary` neutral-event filtering, `build_meta_label_target` correctness, and end-to-end `meta_hard_gate` zeroing of `meta_label_composite` in `SignalAggregator`.
- **tests/test_model_interface.py** — (Prompt 4.3) both `LGBMCrossSectionalRanker` and `MetaLabeler` conform to `ml.models.base.Model` ABC; fit/predict/save/load round-trips; `StrategySpec` wraps models correctly; Model ABC cannot be instantiated directly.
- **tests/test_registry_load.py** — (Prompt 4.3) `ml/registry.yaml` is parseable with required schema and valid metric ranges; `PITFeatureStore` Parquet write/read round-trip; `MetaLabelerRegistry` register/has/get_proba.
- **tests/test_hmm_no_lookahead.py** — verifies two distinct no-lookahead properties of `regime/hmm_regime.py`: (1) the `retrain_freq_days` gate makes a `fit()` call 1 day later a no-op (model unchanged), so a prediction at date D is byte-identical before/after that no-op refit; (2) `predict_proba()`'s last-row probability is unaffected by perturbing data strictly after the prediction cutoff to extreme values, then re-slicing to the same cutoff.
- **tests/test_hmm_state_persistence.py** — on synthetic data with genuine, persistent (sticky) regime structure and a `retrain_freq_days` large enough to avoid mid-window refits, asserts day-over-day `dominant_state` flips occur in <15% of consecutive bars, and that `identify_states_by_vol()`'s labeling doesn't drift across repeated `predict_proba()` calls without a refit.
- **tests/test_hmm_synthetic.py** — generates data from a KNOWN 2-state `GaussianHMM` (via `.sample()`, exact ground truth) and verifies `HMMRegimeDetector` recovers the hidden states with >80% accuracy after resolving label-permutation ambiguity (tries all state-index remappings, keeps the best). Also verifies `identify_states_by_vol()` labels the lower-variance generating state `"bull"`, and that a window drawn entirely from the calm state yields a higher `risk_on_probability` than one drawn entirely from the turbulent state.
- **tests/test_regime_multiplier.py** — unit tests for `signals/regime_multiplier.py`: `compute()`'s score is always `0.0` regardless of `hmm_risk_on_probability`; its `confidence` field carries the multiplier (`1.0` neutral default when HMM unavailable); `SignalAggregator`'s weighted-sum contribution is exactly `0.0` even with an artificially large weight; ABC-conformance, registry, and `settings.SIGNAL_WEIGHTS["regime_multiplier"] == 0.0` checks.
- **tests/test_macro_hmm_integration.py** — unit tests for `MacroEconomicDTO`'s HMM disagreement-downgrade (RISK ON → NEUTRAL below 0.3) and agreement fast-trigger killSwitch (lowered thresholds only when rules=RECESSION AND HMM risk-off > 0.7, never from one condition alone, never less sensitive than the base case), plus `MacroEngine.compute_hmm_risk_on_probability()`'s graceful degradation to `None` on missing/insufficient data.
- **tests/test_stress_runner.py** — sanity tests for `validation/stress_scenarios.py`: each dated window produces real SPY data (yfinance), the runner executes end-to-end on both real and deterministic synthetic returns, errors/exceptions in `returns_fn` are recorded as fail-closed `StressResult`s (never crash), and the `compute_max_drawdown`/`account_survived` primitives are correct.
- **tests/test_stress_gate.py** — verifies the options-selling stress deployability gate: a mocked naked-short-put (catastrophic shock-window loss / >100% blow-up) FAILS and is not deployable despite otherwise-passing PBO/DSR/Sharpe/MaxDD metrics; a mocked iron-condor-with-stops (≈12% capped loss) PASSES and is deployable; a non-options strategy ignores the gate; an options-selling strategy with no stress data (or partial scenario coverage) fails closed.
- **tests/test_market_data.py** — 38 fully offline unit tests for `data/market_data.py`. All network I/O monkeypatched. Classes: `TestQuote` (frozen immutability, field types); `TestQuoteCache` (TTL expiry, invalidate, clear, multi-symbol isolation); `TestAlpacaProvider` (quote source/stale, `MarketDataError` on failure, bar OHLCV shape + tz-naive index); `TestYFinanceProvider` (`is_stale=True` always, `MarketDataError` on empty bars, fundamentals error → empty dict); `TestFinnhubProvider` (key-absent degrade, metric name mapping, network error → empty dict, company profile fields); `TestCompositeProviderSelection` (Alpaca auto-select, yfinance fallback, explicit override, unknown provider error); `TestCompositeProviderCache` (TTL dedup, invalidate/clear force refetch, Finnhub→yfinance fallback); `TestSingleton` (singleton identity, reset forces new instance).
- **tests/test_portfolio_sync.py** — 8 fully-offline tests for Task 1.4 (`data/portfolio_sync.py` + `data/robinhood_client.py` discovery helpers). All Robinhood + market-data network calls monkeypatched via `_FakeRobinhoodClient` / `_FakeProvider` / `_FakeSnapshot`. Coverage: happy-path (holdings ∪ watchlist produces deduped report with mixed `FULL`/`QUOTES_ONLY` classifications); held-uncovered upgrade (a held symbol with no market-data probe yields `EQUITY_ONLY` with `current_price=NaN`/`market_value=NaN`, and `held_total_equity()` falls back to `qty * avg_cost`); dedup + sort (overlapping sources collapse to one alphabetised universe with multi-list memberships preserved); `async_sync_now(persist_default_tickers=False)` dry-run skips the `gui.env_io.write_setting` call; `SecretWriteError` from env_io is swallowed (CONSTRAINT #6); `_file_tickers` honours `#`-comment + dedupe; `discover_universe` integrates env-var `SYNC_WATCHLIST_FILES`; unauthenticated client returns empty containers (never raises). Provider singleton reset via an `autouse=True` fixture so tests don't bleed state.
- **tests/test_robinhood_portfolio.py** — 40 fully offline tests (all network calls monkeypatched) for `data/robinhood_portfolio.py`. Classes: `TestPortfolioPosition` (round-trip serialisation, frozen immutability, string→float coercion); `TestAccountSnapshot` (JSON round-trip, UTC `fetched_at`, `age_hours`, `is_stale`, no secrets in payload); `TestCache` (write-read round-trip, missing→None, corrupt→None, dir auto-created, atomic `.tmp` removed on success); `TestFetchAccountSnapshot` (fresh cache hit → no live call, missing cache → live fetch + write, stale → live refresh, `force=True` bypasses fresh cache, live-fail + cache → stale return, live-fail + no cache → raises); `TestDividendCorrelation` (paid+reinvested counted, pending excluded, scheduled excluded, total sum, UUID extraction from instrument URL, unknown UUID gracefully skipped); `TestUnrealizedPL` (P/L and pct correct, negative P/L, equity-field fallback to qty×price); `TestPositionIsolation` (bad position skipped / good retained, all-None fields → zero-filled position); `TestAccountLevelFields` (equity+buying_power, extended\_hours\_equity fallback, cash fallback); `test_no_order_functions_in_module_source` (static source safety check); **`TestDBIntegration`** (Tier 2.3 Phase 2 — DB read path used when fresh + no live call; falls through to JSON cache when DB raises, confirming dead-letter resilience).
- **tests/test_historical_store.py** — Phase 1 + Phase 2 + Phase 3 tests for `data/historical_store.py`. Phase 1 classes: `TestTableCreation` (all tables + indexes created on init — now includes fundamentals_history and macro_history), `TestLatestBarDate` (empty→None, oldest→latest), `TestGetBars` (OHLCV shape, tz-naive index, sorted ascending), `TestColumnContract` (extra columns tolerated, required columns always present). **Phase 2 class: `TestAccountSnapshotPersistence`** (save + round-trip; save failure → -1 never raises; empty DB → None; multiple snapshots → newest returned; history DataFrame shape; no secrets in DB schema; `since` filter; DB error → empty DF with correct columns; zero-position snapshot). **Phase 3 classes: `TestFundamentalsHistory`** (first fetch writes row + raw_json; fresh cache skips provider; stale row re-fetches; missing fields are NaN not 0.0 — CONSTRAINT #4; total failure → empty dict; history DataFrame schema; debtToEquity /100 conversion) and **`TestMacroHistory`** (round-trip via mock DataEngine; incremental top-up idempotent; fresh cache skips DataEngine; total failure → empty Series — CONSTRAINT #6; lookback_days slices tail; T10Y2Y coexists independently; settings defaults for FUNDAMENTALS_REFRESH_DAYS and MACRO_REFRESH_HOURS).
- **tests/test_alpaca_paper_smoke.py** — live smoke tests for `AlpacaBroker` against Alpaca paper-trading sandbox: account snapshot, submit 1-share SPY market order, verify in `get_orders()`, cancel. Skipped when `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` are absent; never mocked — the point is to verify the real paper endpoint.
- **tests/test_order_manager_idempotency.py** — unit tests for `OrderManager`: same intent submitted twice → one broker call; different symbols → two broker calls; dry_run → zero broker calls; deterministic `client_order_id` for same params; different symbols → different IDs; retry on transient `ERROR`.
- **tests/test_reconciliation.py** — unit tests for `reconcile_state()`: clean state → `report.ok`; broker extra position → drift; internal extra position → drift; qty mismatch → drift; broker error → `report.error` set (never raises); empty both sides → clean.
- **tests/test_run_once.py** — 19 fully offline unit tests for the refactored `main.py`. All network I/O monkeypatched. Classes: `TestLoadWatchlist` (env var, file, precedence, empty, blank-env-string); `TestBuildUniverse` (held-only, union, dedup, empty); `TestRunOnce` (success shape, dead-letter per symbol, all-fail still returns, Robinhood-down uses empty snapshot, empty universe early return, force_account flag threading, held-symbols-always-included); `TestRunResultImmutability` (frozen, duration≥0, error-dict structure).
- **tests/test_advisory.py** — 16 fully offline unit tests for `engine/advisory.py`. All network I/O and heavy engines patched via `mock.patch("engine.advisory.<ClassName>")`. Classes: `TestRecommendationDataclass` (frozen immutability, action literals, field types); `TestConfigCompleteness` (all CONFIG keys present, no bare numeric literals in logic section); `TestAcceptanceCriteria` (AC1: held above cost + dividends + neutral forecast → HOLD with rationale citing both; AC2: held below cost + bearish forecast → SELL with elevated conviction; AC3: non-held + strong bullish forecast + positive Kelly → BUY with `suggested_position_pct` in `(0, max_cap]`); `TestDataQuality` (stale quote → STALE; engine module failure → PARTIAL; no price → PARTIAL/fallback; all fresh → OK); `TestPositionSizing` (SELL/HOLD → 0.0 pct; BUY bounded by `CONFIG["max_single_position_pct"]`; negative Kelly clamped to 0); `TestDividendHoldBiasRule` (high-yield holder on weak signal → HOLD override; strong buy signal on high-yield → BUY preserved). Uses `TransactionsStore(db_url="sqlite:///:memory:")` for isolation.
- **tests/test_env_loading.py** — Regression tests pinning the `.env` → `os.environ` loading contract. `test_entrypoint_calls_load_dotenv` AST-walks `main.py` and `main_orchestrator.py` to assert each invokes `load_dotenv()` *somewhere* — module top OR inside any function body (tracks the `from dotenv import load_dotenv as X` alias so renaming the import doesn't break the check). The "anywhere" semantics are deliberate: module-top placement causes pytest pollution by populating `os.environ` on import, so the canonical placement is inside the entry-point function (`main()` / `run_once()`). `test_load_dotenv_actually_populates_environ` is a functional smoke test that writes a fixture `.env` to `tmp_path`, calls `load_dotenv(dotenv_path=fixture, override=False)`, and verifies the key appears in `os.environ`. Exists because removing the `load_dotenv()` call breaks `data/robinhood_portfolio.py` silently — the prior failure surfaced only as the runtime error `"Required environment variable 'RH_USERNAME' is missing or empty"`.
- **tests/test_pipeline_smoke.py** — End-to-end pipeline smoke tests. All network I/O is monkeypatched. Three classes: `TestRunOncePipeline` (three tests via `main.run_once()` with all call sites patched: correct RunResult shape, dead-letter behavior — one rigged failure → 1 error + N-1 recommendations, all-failures still returns RunResult); `TestAdvisoryTailoringRules` (three tests via `engine.advisory.evaluate()` with mocked heavy engines: Case B held+dividends+weak signal → HOLD, Case A held-below-cost+bearish → SELL, non-held+bullish+GARCH vol → BUY with 0 < pct ≤ cap); `TestNoOrderFunctions` (AST scan guard: no Python module outside `execution/` defines a function whose name matches `submit_order`, `buy_order`, `sell_order`, `place_order`, `place_equity_order`, `place_option_order`, or starts with `place_`). Excluded from AST scan: `execution/`, `tests/`, `.venv/`, `Gravity AI Review Suite.py`, `ai_verification_prompts.py`.
- **engine/__init__.py** — Package marker for the `engine/` advisory and orchestration layer.
- **engine/advisory.py** — Holding-aware per-symbol advisory engine. Public API: `evaluate(symbol, position, market, snapshot, macro_dto=None, transactions_store=None) -> Recommendation`. `Recommendation` is a frozen dataclass: `symbol`, `action: Literal["BUY","SELL","HOLD"]`, `strategy: str`, `conviction: float`, `rationale: str`, `suggested_position_pct: float`, `forecast: float|None`, `key_indicators: dict[str,float]`, `data_quality: Literal["OK","STALE","PARTIAL"]`. All 16 CONFIG thresholds (score gates, P&L thresholds, dividend bias rule, Kelly params, conviction levels, forecast direction threshold) live in a single `CONFIG` dict at module top — no magic numbers in the decision logic. Pipeline (each stage in try/except for dead-letter resilience): (1) live quote + OHLCV bars via `MarketDataProvider`; (2) `MarketBarDTO` construction; (3) fundamentals + `FundamentalDataDTO`; (4) `ProcessingEngine` technical metrics; (5) `TechnicalOptionsEngine` GJR-GARCH vol; (6) `ForecastingEngine` 30-day blended forecast; (7) neutral `MacroEconomicDTO` default when not injected; (8) `StrategyEngine.evaluate_security()` for raw signal + score; (9) **Holding-aware overlay** — three mutually-exclusive cases: *Case A* (below effective cost + bearish forecast → SELL escalation, elevated conviction), *Case B* (**DIVIDEND HOLD BIAS RULE**: `dividend_yield ≥ 4%` OR `dividends_received ≥ $50` AND score < `buy_score_threshold` → HOLD override, dividends cited in rationale), *Case C* (unrealized gain ≥ 10% + non-bearish forecast → HOLD instead of BUY). (10) fractional-Kelly sizing via `sizing.kelly` / `sizing.vol_target` (fallback when < 30 trades), clamped to `CONFIG["max_single_position_pct"]` (default 5%); (11) one-paragraph plain-English rationale citing top 2-3 drivers; (12) `key_indicators` dict with NaN for unavailable values; (13) `data_quality` = PARTIAL (any module failure) > STALE (quote.is_stale) > OK. **Source-of-truth rules** (CONSTRAINT #4): `PortfolioPosition`/`AccountSnapshot` (Robinhood) are the source of truth for cost basis, qty, and dividends; `MarketDataProvider` is the source of truth for prices, bars, and fundamentals — these roles never cross. Heavy engines imported at module level (not lazily) so `mock.patch("engine.advisory.<ClassName>")` resolves correctly in tests.
- **`.env` loading convention (CRITICAL — read this before touching either entry point)** — both entry points **import** `from dotenv import load_dotenv as _load_dotenv` at module top but **call** `_load_dotenv(override=False)` only inside their entry-point function: `main.py` calls it as the first line of `main()`; `main_orchestrator.py` calls it inside `async def main()`. `run_once()` deliberately does NOT call it (its docstring says so) — direct callers that bypass `main()` (`make verify`, `verify.command`, ad-hoc REPL) call `load_dotenv()` themselves in their own wrappers before invoking `run_once()`, and both already do. This is mandatory: `settings.py` uses `pydantic-settings` with `env_file=".env"`, which populates `Settings()` but does NOT propagate values to `os.environ`. Modules that read credentials via `os.environ.get(...)` directly — notably `data/robinhood_portfolio.py` (`RH_USERNAME`/`RH_PASSWORD`/`RH_MFA_SECRET`) — would otherwise see empty strings even with a fully-populated `.env`, producing the misleading runtime error `"Required environment variable 'RH_USERNAME' (or 'ROBINHOOD_USERNAME') is missing or empty."` **Why the call lives inside the function, not at module top?** Module-top invocation pollutes the pytest session: importing `main` would load every `.env` value into `os.environ`, breaking `tests/test_settings.py::test_settings_defaults` (which asserts `ALPACA_API_KEY is None` for a clean-environment Settings()). `override=False` so an explicit shell `export` always wins over `.env`. Regression test: `tests/test_env_loading.py` AST-walks both entry points to assert a `load_dotenv()` call exists *somewhere* in the file (module top OR inside any function body) — any future refactor that removes it entirely fails CI immediately. The empty-universe warning in `main.run_once()` names all four remediation paths (RH_* env vars / WATCHLIST env var / watchlist.txt / Sheet2 column A) so the operator can act without spelunking through the source.
- **alerting.py** — Structured logging setup and push-notification dispatcher. Three public functions: `setup_logging(log_level="INFO")` — idempotent root-logger configuration: `RotatingFileHandler` writing to `logs/investyo.log` (10 MB × 5 backups, UTF-8) + `StreamHandler` to stderr, both using the timestamp/level/module/message formatter; safe to call multiple times. `notify(title, message, priority="default")` — POSTs to `https://ntfy.sh/{NTFY_TOPIC}` using `urllib.request` (stdlib, no new dependency); no-op when `NTFY_TOPIC` is unset; network failures caught and logged as WARNING, never propagated; secrets MUST NOT appear in title/message. `summarize_run(result)` — duck-typed on `RunResult`'s attributes (`recommendations`, `errors`, `started_at`, `duration_seconds`) to avoid circular import; returns a compact multi-line string with symbol counts, BUY/SELL/HOLD tallies, error count, elapsed time, and the top 3 highest-conviction actionable recommendations. Integration in `main.py`: `setup_logging()` is the first call in `main()`; after each `run_once()`, `summarize_run()` is logged at INFO; a `_clean_notified` flag ensures at most one "refresh complete" push per launch in `--interval` mode; any symbol-level error triggers a `priority="high"` ntfy push listing the failing symbols and stages. New env var: `NTFY_TOPIC` (unset = no-op).
- **Makefile** — Developer convenience targets: `make verify` (env-var check → pytest → one live `run_once()` + print summary), `make test` (pytest only), `make smoke` (pipeline smoke tests only). All targets use `.venv/bin/python3`.
- **verify.command** — macOS double-click readiness gate (same steps as `make verify`): (1) env-var presence check (FRED_API_KEY required; Robinhood/ntfy optional); (2) full pytest suite — aborts if any test fails; (3) one live `run_once()` cycle, prints `summarize_run()` output. Equivalent to `make verify` but opens in a Terminal window from Finder or the Dock. Make executable once with `chmod +x verify.command`.
- **main.py** — **Clean advisory orchestrator** (replaces the legacy engine-calling monolith). Implements a two-tier refresh cadence: (1) account tier — Robinhood snapshot via `data.robinhood_portfolio.fetch_account_snapshot()` fetched at most once/day (daily JSON cache); forced by `--refresh-account`. (2) market tier — prices, bars, indicators, forecasts refreshed on every `run_once()` call via `engine.advisory.evaluate()`. Key components: `RunResult` (frozen dataclass: `snapshot, recommendations: list[Recommendation], errors: list[dict], started_at, finished_at, duration_seconds`); `run_once(force_account=False) -> RunResult` — the single public pipeline function; `_build_universe(snapshot) -> list[str]` — held symbols ∪ WATCHLIST, falling back to `_load_tickers_from_sheet2()` (Sheet2 column A) only when both are empty; `_build_macro_dto() -> MacroEconomicDTO` — FRED fetch with neutral defaults fallback; `_fetch_bars_for_universe()` + `_build_context_extras()` — pre-compute xsec ranks + multifactor composites once before the per-symbol loop; `_write_to_sheet(result, market)` — maps `list[Recommendation]` to `config.COLUMN_SCHEMA` columns, preserves full conditional formatting; `_write_html_report(result, macro_dto)` — calls `diagnostics_and_visuals.generate_html_report`; `main()` — argparse with `--interval N` (loop every N seconds, account re-fetched at most once/day) and `--refresh-account`. Dead-letter pattern: per-symbol failures append to `RunResult.errors` and never abort the run. New env var: `WATCHLIST` (comma-separated ticker list; file alternative: `watchlist.txt`). `main_orchestrator.py` continues to exist as the full async pipeline with broker execution and Pandera schema validation — use it for production runs that need all 50+ dashboard columns populated.
- **main_orchestrator.py** — newer async master orchestrator. Pipeline: (1) async data fetch, (2) `run_pipeline()` (macro → options → processing → forecasting → strategy), (3) schema validation, (4) HTML report + Plotly chart, (5) JSON payload print, (6) broker execution via `_execute_broker_orders()` (only when Alpaca credentials configured). New in Prompt 5.2: `main(dry_run=False)` wraps `_main_body()` in a try/finally that cancels a `_heartbeat()` asyncio background task. `_heartbeat(output_dir, interval=60)` logs `"ORCHESTRATOR ALIVE"` and writes `OUTPUT_DIR/heartbeat.txt` (UTC ISO timestamp) every 60 s so an external watchdog can detect crashes. `--dry-run` CLI flag: `python3 main_orchestrator.py --dry-run`. `_execute_broker_orders(final_df, dry_run, macro_dto)` translates BUY/SELL signals → `OrderIntent` → `OrderManager.submit_order_with_idempotency`; runs reconciliation before submission; logs CRITICAL on drift. Skipped entirely when ALPACA_API_KEY/SECRET_KEY are absent.
 
 ## Conventions enforced in this codebase
 
 - **In-app help content (`gui/help_content.py`)**: All operator-facing explainer prose MUST live in `gui/help_content.py` — never hard-code tooltip/expander text directly in `gui/panels.py`. The single-source-of-truth is `GLOSSARY` (terms), `TAB_HELP` (10 tab descriptions), `SECTION_HELP` (section-level tooltips), and `METRIC_HELP` (KPI tooltips keyed by `"<tab>.<metric>"`).
 - **Help-key convention**: Metric tooltip keys follow the form `"<tab>.<metric_name>"` — looked up by `gui.help_content.metric_help(key)`. A missing key returns `""` and renders no tooltip; it **never raises** (CONSTRAINT #6). Never add a default-fallback value in `metric_help` — empty string is the correct sentinel.
 - **Anchor-contract invariant**: Every `GlossaryEntry.guide_anchor` and `TabHelp.guide_anchor` field **must** resolve to a real heading slug in `docs/HOW_TO_GUIDE.md`. Enforced by `tests/test_help_content.py::TestAnchorValidity` (CI) and Gravity step 68 check 3. When renaming a heading in HOW_TO_GUIDE.md, grep `gui/help_content.py` for the old slug and update it.
 - **Thresholds in help text**: Values cited in help strings (VIX thresholds, Kelly caps, score gates, …) MUST be read from `settings`, `validation.thresholds`, or `engine.advisory.CONFIG` at module import time — never re-typed as literals in `help_content.py`. This ensures help text stays in sync with the live config without a separate update step.
 - All data crossing into calculation code goes through the DTOs in `dto_models.py`, not raw dicts/lists.
 - Data fetching always goes through `IDataProvider` implementations in `data_engine.py`.
 - Technical/fundamental math is vectorized — no per-row Python loops.
 - Loops over tickers (in `data_engine.py`, orchestrators) wrap each ticker in try/except so one bad symbol doesn't abort the whole run.
 - New/changed indicators need a corresponding test in `tests/`; numeric drift on existing indicators must stay below 1e-5.
 - Every indicator and forecaster must be verified to have zero lookahead bias using the perturbation tests in `tests/`.
 - Options premium selling (e.g. Put Credit Spreads, Iron Condors) is gated by VRP regime rules: must have `true_ivr > 50`, `VRP > 0.02`, `VIX < 30`, and no `CREDIT EVENT`. If gated, recommender returns `Cash/Wait`.
 - Pairs trading signals are driven by Engle-Granger cointegration test, half-life of mean reversion (between 5 and 60 days), Kalman filter dynamic hedge ratio, rolling spread z-score (entry at |Z| > 2.0, exit at 0 cross or cointegration break rolling ADF p > 0.10, stop loss at |Z| > 4.0).
 - Every backtest execution (e.g. in `simulation_engine.py`) must display the survivorship bias warning and report.
 - Backtests must use the custom `TieredCostModel` for transaction commissions and slippage calculations instead of static assumptions.
 - Gate deployable status of trading strategies on PBO < 0.5 AND DSR > 0.95 in verification audits.

- Deployability gate for strategy harness strictly enforces PBO < 0.5, DSR > 0.95, net net-of-cost Sharpe > 0.5, and Max Drawdown < 30%.
- **Options-selling strategies carry an additional tail-scenario stress gate** (`validation/stress_scenarios.py`): they are deployable only if max drawdown is < 50% AND the account survives (no blow-up) in EVERY dated shock window (OCT_2008, FEB_2018, MAR_2020, AUG_2024). Construct the harness with `is_options_selling=True` and a `stress_returns_fn(start, end) -> daily returns`; the gate fails closed if an options-selling strategy is never stress-tested. The stress summary is printed at the top of every options-selling validation report.
- Strategy validation harness cost modeling scales linearly with average daily turnover.
- Quantitative scoring in `StrategyEngine.evaluate_security` must be decoupled into pluggable signal modules under the `signals/` package, using the weighted-sum aggregator. Weights are defined in settings.SIGNAL_WEIGHTS.
- Cross-sectional signals (e.g. `CrossSectionalMomentumSignal`) use the **two-phase hook pattern**: `pre_compute(universe_df, context)` runs once per cycle on the full dashboard DataFrame before the per-ticker loop (called via `global_registry.run_pre_compute()`); `compute(row, context)` reads pre-computed ranks from `context.xsec_percentile_ranks`. All existing per-ticker signals inherit the no-op `pre_compute` and are unaffected.
- The 12-1m cross-sectional return is computed by `compute_xsec_momentum_ranks()` in `main_orchestrator.py` using vectorized `iloc` indexing with `skip_days=22` (1 month skip) and `lookback_days=252` (12 months) — no iterrows, no current-month leakage.
- `config.COLUMN_SCHEMA` now includes `XSec_12_1M` (percent) and `XSec_Momentum_Rank` (percent). These must be present in any Pandera-validated DataFrame.
- New trading rules must be optimized in `vectorbt` and validated in `backtrader` before being added to `strategy_engine.py`.
- Option overlay trend filter in StrategyEngine uses a lookahead-free strong uptrend filter (ROC_12M > 0 and price > SMA_200) falling back to legacy trend strength when not provided.
- `SignalModule.is_active_in_regime(macro: MacroEconomicDTO) -> bool` (default `True`) lets a module opt out of contributing to the aggregate score entirely for a cycle. `SignalAggregator.aggregate()` checks this before adding a module's weighted contribution/explanation — `compute()` still runs (its raw output remains in the returned `outputs` dict for introspection), but a `False` module adds nothing to `final_score`/`score_log`. Use this for regime-fragile signals (e.g. `rsi2_mean_reversion`) rather than relying on `compute()` to self-zero, so the suppression is enforced centrally and is impossible to forget per-module.
- **Operator module disable (`settings.DISABLED_SIGNAL_MODULES`)**: a `list[str]` of signal-module names (JSON array in `.env`, default `[]`) that `SignalAggregator.aggregate()` skips — checked right alongside the `is_active_in_regime` gate, so a disabled module adds nothing to `final_score`/`score_log` and does not affect `meta_label_composite`, while its raw `compute()` output still appears in the returned `outputs` dict for introspection. Empty list reproduces the legacy behavior exactly; honored by BOTH orchestrators. The GUI Command Center's Strategy Matrix tab writes this (and `SIGNAL_WEIGHTS`) via `gui/env_io.py`. New env var: `DISABLED_SIGNAL_MODULES`.
- **Macro Regime Gate (`settings.MACRO_REGIME_GATE_ENABLED`, default `True`)**: operator-controlled bypass for `PreTradeRiskGate.macro_kill_switch_check`. When `True` (autonomous mode), the check vetoes new BUY orders whenever `MacroEconomicDTO.killSwitch` is `True` (Sahm Rule ≥ 0.5, VIX > 30, or HY OAS > 6%). When `False` (hybrid mode), the check returns pass immediately without reading `context.macro` — technical signals run without macro override, useful when idiosyncratic volatility produces a false-positive systemic alarm. The GUI Observability tab (Mission Control) provides a toggle button that writes `MACRO_REGIME_GATE_ENABLED` via `gui/env_io.py` and shows a persistent red warning banner when the gate is off. `scripts/preflight_check.py`'s `check_macro_regime_gate_enabled` is a **blocking** failure when gate is off AND `ALPACA_PAPER=False`; warning-only in paper mode. The state snapshot (`output/state_snapshot.json`) surfaces `sahm_rule`, `high_yield_oas`, and `macro_regime_gate_enabled` so the GUI can display recession telemetry without a live FRED call. New env var: `MACRO_REGIME_GATE_ENABLED`.
- **GUI Command Center env-write safety (`gui/env_io.py`)**: any GUI-driven `.env` edit MUST go through `gui.env_io.write_setting`/`write_many`, which enforce an `ALLOWED_KEYS` allowlist (NON-secret tunables only) and a `SECRET_KEYS` denylist — secrets are masked (`read_settings`) and raise `SecretWriteError` on write, non-allowlisted keys raise `DisallowedKeyError` (CONSTRAINT #3). Never add a new GUI-writable setting without adding it to `ALLOWED_KEYS`; never add a credential to `ALLOWED_KEYS`.
- **Historical persistence (`data/historical_store.py`, Tier 2.3)**: `HistoricalStore` is the single SQLite write-path for OHLCV bars (Phase 1), account snapshots (Phase 2), fundamentals (Phase 3), and FRED macro series (Phase 3). `fetch_account_snapshot()` MUST follow the three-tier read order — DB first → JSON cache → live — so the DB row survives a network outage. `get_fundamentals()` caches typed columns AND full `raw_json` from the provider; missing fields → `NaN`, NEVER `0.0` (CONSTRAINT #4). `get_macro()` stores both VIXCLS and T10Y2Y from `DataEngine.fetch_macro_history()` and serves them as named pd.Series; fresh rows (age < `MACRO_REFRESH_HOURS`) skip the network call. Import `HistoricalStore` lazily (inside function bodies, not at module top) everywhere — `data/robinhood_portfolio.py`, `processing_engine.py`, and `macro_engine.py` all use lazy imports to avoid circular imports. `HISTORICAL_STORE_ENABLED=False` disables all DB routing without touching the JSON-cache or live-fetch tiers. **Phase 3 settings**: `FUNDAMENTALS_REFRESH_DAYS` (int, default 1 — days before a cached fundamentals row is re-fetched), `MACRO_REFRESH_HOURS` (int, default 12 — hours before FRED macro series are topped up). **Existing settings**: `HISTORICAL_STORE_ENABLED` (bool, default `True`), `BARS_BACKFILL_DAYS` (int, default 504).
- **Portfolio & Watchlist Synchronization (`data/portfolio_sync.py`, Task 1.4)**: any code that needs "the complete universe the operator is tracking" — beyond the held-only or watchlist-only slice — MUST go through `build_sync_report()` / `async_sync_now()`, never re-derive the union ad-hoc. Coverage status drives downstream eligibility: pricing-dependent metrics must consume `CoverageStatus.FULL` symbols only (or `QUOTES_ONLY` if fundamentals aren't required); `EQUITY_ONLY` symbols stay in the equity / holdings view but are excluded from quote-driven calculations (no fabricated price proxy — CONSTRAINT #4). The Sync engine never raises on a coverage gap (dead-letter resilience); it logs an INFO diagnostic per affected symbol and records the failure mode on `SymbolStatus.diagnostic`. **New env var:** `SYNC_WATCHLIST_FILES` (colon-separated paths to additional plain-text watchlist files, one ticker per line, `#` = comment; missing files tolerated silently). The GUI Live Inventory tab's "🔄 Sync Now" persists the discovered universe to `DEFAULT_TICKERS` in `.env` via `gui/env_io.py` (`DEFAULT_TICKERS` is already in `ALLOWED_KEYS` from the Settings Manager tab).
- `config.py`'s `COLUMN_SCHEMA` now includes `RSI_2` and `SMA_5` (both `"format": "number"`/`"currency"`), feeding `processing_engine.calculate_technical_metrics()`'s `ta.rsi(length=2)` / `ta.sma(length=5)` columns (added alongside the existing RSI(14)/SMA(50)/SMA(200) calculations — same causal, vectorized pattern, verified lookahead-free).
- `StrategyEngine.evaluate_security()` and both orchestrator call sites (`main.py`, `main_orchestrator.py`) now also accept/pass `rsi_2: float` and `sma_5: Optional[float]`, propagated into the per-ticker `row` Series as `RSI_2`/`SMA_5`/`Close` for signal modules that need them.
- Position sizing ("Kelly Target") is no longer derived from `score`/`sortino_ratio`/`edge_ratio` brackets. The single source of truth is `StrategyEngine._calculate_kelly_sizing(realized_vol)` → `sizing.kelly`/`sizing.vol_target`, fed by `settings.KELLY_FRACTION` (0.5), `settings.KELLY_CAP` (0.20), `settings.VOL_TARGET` (0.10), `settings.MAX_LEVERAGE` (2.0), `settings.MAX_POSITION_WEIGHT` (1.0). The vol-target fallback (insufficient trade history) can internally compute up to `MAX_LEVERAGE` (2.0x), but `_calculate_kelly_sizing` always applies a final `min(weight, settings.MAX_POSITION_WEIGHT)` clamp before returning — chosen as the middle ground between the old score-bracket system's 25% ceiling and an uncapped 2.0x. Neither `sizing.kelly` nor `sizing.vol_target` enforce this themselves; the clamp lives in `StrategyEngine`, so any other caller of those modules directly must apply its own ceiling.
- `processing_engine.calculate_fundamental_metrics()` now accepts an optional `realized_vol_60d_map: dict[str, float]` parameter (sourced from `calculate_technical_metrics()`'s `Realized_Vol_60D`, itself exposed from `calculate_momentum_metrics()`'s already-lookahead-free internal computation) and computes five multifactor raw inputs per ticker: `book_to_market` (1/P/B), `earnings_yield` (1/P/E), `quality_factor_score` (ROE + operating margin, falling back to `-debt_to_equity` when yfinance omits ROE/margin), `low_vol_score` (negative 60-day realized vol), `log_market_cap`. All five are `NaN` (never fabricated) when the underlying field is unavailable. `calculate_technical_metrics()` also now surfaces `Realized_Vol_60D` itself (`NaN`, not `0.0`, when <60 valid daily returns exist).
- `config.COLUMN_SCHEMA` now includes `Value_Z`, `Quality_Z`, `LowVol_Z`, `Size_Z`, `Multifactor_Composite` (all `"format": "number"`), produced by `signals/multifactor.py`'s `pre_compute()` and written back into `dashboard_df` by `main_orchestrator.py` immediately after `global_registry.run_pre_compute()` (the only signal module whose *outputs*, not just its inputs, need writing back to the dashboard — XSec momentum writes its inputs before `pre_compute` and never needs an outputs write-back since its rank dict isn't surfaced as its own columns the same way).
- `config.COLUMN_SCHEMA` now also includes `sellRange` (`"format": "string"`, header `"Sell Range"`), placed immediately after `buyRange` in the TACTICAL EXECUTION section. Produced by `strategy_engine.apply_sell_side_range` (see strategy_engine.py entry above) and written into `dashboard_df` by both `main.py` and `main_orchestrator.py` from `strategy_output['sellRange']`. Also surfaced in (a) the `main_orchestrator.py` JSON payload printed at end of run, (b) `_write_state_snapshot()`'s per-signal dict as `"buy_range"` / `"sell_range"` for the Streamlit observability dashboard, (c) the `daily_report_template.html` summary table column and per-ticker advice card. The Sheets sink (`main.py`) writes `sellRange` to the "Sell Range" column with no additional plumbing because `config.get_headers()` / `get_internal_keys()` are schema-driven.
- `config.COLUMN_SCHEMA` now also includes `HMM_Risk_On_Probability` (`"format": "percent"`). `run_pipeline()` in `main_orchestrator.py` now accepts an optional `data_engine: IDataProvider` parameter, used to call `MacroEngine.compute_hmm_risk_on_probability(tech_raw.get('SPY'))` and pass the result into `MacroEconomicDTO(hmm_risk_on_probability=...)`. `processing_engine.process_macro_regime()` surfaces it into the dict consumed by `compile_dashboard()`, which writes `HMM_Risk_On_Probability` into every row (`NaN`, never fabricated, when the HMM didn't run). `main()`'s offline-fallback branch (constructing a fresh `MockDataEngine`) now reassigns the outer `de` reference too, so `data_engine=de` passed to `run_pipeline()` is always consistent with whatever engine actually produced `tech_raw`/`macro_raw`/`fund_raw` for that run.
- **Options matrix integrity (`technical_options_engine.py` + Gravity STEP 38)**: every premium-selling directive rendered by the GUI's Technical Options Matrix tab, or returned by `OptionsPricingRecommender.generate_strategy_pricing_matrix`, MUST satisfy two structural invariants — **strike grid** (every leg on the `$0.50` USD grid via `_on_strike_grid`) and **delta-target tolerance** (resolved BS delta within `±0.05` of the conventional target in `EXPECTED_DELTA_TARGETS`). The validator is `validate_directive_integrity(directive)`. Gravity `step_38_options_matrix_integrity_audit` exercises schema hydration, strike-grid compliance, delta tolerance, off-grid rejection, VIX>30 regime gate, CREDIT EVENT regime gate, and low-IVR debit-spread routing. Tests: `tests/test_options_matrix.py`.
- **Market-data layer (`data/market_data.py`)**: All quote/bar/fundamentals fetches outside the existing `DataEngine.fetch_technical_raw()` path MUST go through `CompositeProvider` (the `MarketDataProvider` ABC). `CompositeProvider` auto-selects Alpaca (when `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` present) or yfinance (zero config). `MARKET_DATA_PROVIDER` env-var can force either. Fundamentals via Finnhub (`FINNHUB_API_KEY`) with yfinance `.info` fallback. In-process quote cache (TTL `MARKET_DATA_QUOTE_TTL_SECONDS`=30 s, never persisted to disk). `is_stale=True` on any yfinance quote — treat as delayed by design. `MarketDataError` is the typed exception for dead-letter resilience. `get_provider()` / `reset_provider()` manage the module-level singleton. **New settings**: `settings.MARKET_DATA_PROVIDER`, `settings.FINNHUB_API_KEY`, `settings.MARKET_DATA_QUOTE_TTL_SECONDS`.
- Google Sheets auth uses a service account via `credentials.json`.
- **Broker execution is best-effort**: errors in `_execute_broker_orders` are logged as ERROR and never propagate to crash the analysis pipeline. The pipeline's value (signals, HTML report, JSON payload) must not be held hostage to broker connectivity.
- **Broker abstraction**: all order submission in orchestrator and strategy code MUST go through `OrderManager` (never directly to `AlpacaBroker` or any concrete broker). Type-annotate against `BrokerBase`; swap paper ↔ live by changing one constructor call.
- **Idempotency**: every order intent submitted via `OrderManager.submit_order_with_idempotency` gets a deterministic `client_order_id` from `make_client_order_id(strategy_id, symbol, side, qty, timestamp)`. Do NOT fabricate or reuse IDs manually.
- **Dry-run is enforced at manager level**: `OrderManager._submit_with_retry` checks `intent.dry_run` before calling any broker — this means MockBroker tests in the test suite also correctly see the dry-run guard without implementing it themselves. AlpacaBroker also has its own redundant guard as a belt-and-suspenders, but the manager-level check is the authoritative one.
- **Reconciliation alerts**: set `ALERT_WEBHOOK_URL` in `.env` to a Slack/Discord incoming-webhook URL; `reconcile_state` fires it on any position drift. The webhook call uses `urllib.request` (no extra dependency). Failures in the alert path are logged but never swallowed silently in a bare except.
- **New settings for broker**: `DRY_RUN` (default `false`), `ALERT_WEBHOOK_URL` (default `None`). Both are optional; the orchestrator degrades gracefully when either is absent.

## ML Package Architecture (Stage 4 — Triple Barrier + Meta-Labeling)

**qlib-style three-layer architecture (ml/ package)**:
- `ml/data/` — PIT feature store (PITFeatureStore, Parquet cache), label construction (`build_meta_label_target`, `build_meta_features`). Re-exports `build_pit_feature_matrix` / `build_forward_return_ranks` from `ml/feature_engineering.py`.
- `ml/models/` — `Model` ABC (`fit/predict/save/load`) that ALL ML models must implement. `LGBMCrossSectionalRanker` and `MetaLabeler` both inherit from it.
- `ml/strategies/` — `StrategySpec` links a Model to a SignalModule by `signal_id`. Used by Gravity audits.
- `ml/registry.yaml` — Human-readable model registry with `cpcv_dsr`, `pbo`, `deployable` fields. Parse with PyYAML.

**Triple-barrier no-lookahead invariants**:
- `get_volatility(close, span)` uses `ewm(adjust=False)` (causal) — vol at t uses only returns ≤ t.
- `apply_triple_barrier` pre-computes vol on the FULL series then indexes at event time — this is correct (vol[t] IS the prefix vol because ewm is causal). The perturbation test in `test_triple_barrier_lookahead.py` proves this empirically.
- `cusum_filter` is inherently sequential (scalar loop over dates, not iterrows). This is intentional and correct.

**Meta-labeling hard gate**:
- `MetaLabelerRegistry.global_meta_registry` is a module-level singleton in `ml/meta_labeling.py`.
- `SignalAggregator.aggregate()` imports it lazily via `_get_meta_registry()` to avoid circular imports.
- When a MetaLabeler is registered for a signal AND `predict_proba_scalar` returns P < `settings.META_LABEL_MIN_CONFIDENCE` (0.4), `meta_label_composite` is set to EXACTLY 0.0 (not near-zero via log-space — a hard flag `meta_hard_gate` ensures this).
- Hard gate affects position sizing ONLY (Kelly Target × composite = 0), not the signal score/recommendation (BUY/HOLD/etc.).
- Default state (empty registry) is identical to pre-Stage-4 behavior: composite = 1.0.

**PyYAML** added to `requirements.txt` (needed for `ml/registry.yaml` round-trip tests).

## Lookback & Vectorization Enhancements (Bug Fixes)
- **Lookback pricing history**: Fetch lookback changed from `"1y"` to `"2y"` (~504 trading days) in `data_engine.py` and `data_ingestion.py` to ensure all cross-sectional and momentum engines have sufficient history (requires at least 275 trading days). In `data/market_data.py`, yfinance lookback threshold mapping is adjusted so that `lookback_days <= 500` maps to `"2y"`.
- **DataFrame vectorization**: All mutations in `main_orchestrator.py` and `evaluation_engine.py` that occurred inside `.iterrows()` loops have been refactored to use dictionary collection and vectorized `.map()` operations, satisfying Constraint #3.
- **Gravity AI Review Suite extensions**:
  - **Step 1 dynamic schema validation**: covered dynamic `DashboardSchema` validation in `run_schema_audit()`.
  - **Step 8 multi-indicator perturbation**: added lookahead perturbation check for all technical indicators (`RSI`, `RSI_2`, `MACD`, `ATR`, `Aroon`, `Coppock`, `Chandelier_Exit`) in `run_lookahead_audit()`, verifying the actual `ProcessingEngine` calculations.
  - **Step 35 portfolio heat limit audit**: added `run_risk_gates_portfolio_heat_audit()` verifying that `PreTradeRiskGate` with 6% limit blocks BUY orders and allows SELL orders in mock mode.
  - **Step 37 six-bug regression audit** (`run_six_bug_regression_audit()`): enforces the six production bugs found in the 2026-06 bug-hunt session cannot regress. Checks: (1) `_fallback_sentiment("")` is an NLP scorer not a Sahm proxy; (2) `calculate_sahm_rule()` is called in `run_pipeline`; (3) `sahm_rule_indicator=` keyword wired to `MacroEconomicDTO` in `main_orchestrator.py`; (4) `MacroEconomicDTO.killSwitch` fires at `sahm_rule_indicator >= 0.5`; (5) Gordon Growth Model uses the same capped g in both numerator and denominator; (6) `calculate_momentum_metrics` returns NaN (not 0.0) for `ROC_12M`/`Realized_Vol_60D` when `len(df) < 253`; (7) `evaluate_portfolio`'s `benchmark_df` default is `None`; (8) fallback forecast path in `main_orchestrator` uses `run_monte_carlo`, not `price*(1+mu*N)`.
- **Six-bug session invariants** (2026-06 bug-hunt — must never regress):
  - `main_orchestrator.run_pipeline` MUST call `me.calculate_sahm_rule()` (not `me._fallback_sentiment("")`) and forward the result as `sahm_rule_indicator=sahm_val` to `MacroEconomicDTO`. Violating either half silently disables the Sahm Rule recession kill-switch.
  - `calculate_gordon_fair_value()` MUST cap `g` before computing `D1 = D0 * (1 + g_capped)`. Both numerator and denominator must use the same capped rate.
  - `calculate_momentum_metrics()` MUST return `float('nan')` for all ROC/vol columns in the `len(df) < 253` early-return path. `0.0` is fabricated data (Constraint #4).
  - `evaluate_portfolio()`'s `benchmark_df` parameter MUST default to `None`; create a fresh `pd.DataFrame()` inside the function body.
  - The fallback-forecast exception path in `run_pipeline` MUST call `fe.run_monte_carlo(price, mu, sigma, N)` for every forecast horizon — not `price * (1 + mu * N)`.
- **Zero-position-size crash fix (2026-06-26 — must never regress):** Production CRITICAL crash `"Platform execution pipeline crashed: float division by zero"` was caused by `evaluate_portfolio`'s Brinson-Fachler block computing `df.groupby('sector')['position_size'].sum() / df['position_size'].sum()` when every ticker is a watchlist-only ticker (zero shares → `Shares × Price = 0` → `position_size.sum() == 0`). Three invariants enforced:
  1. `EvaluationEngine.evaluate_portfolio` MUST guard `total_position_size = df['position_size'].sum()` and skip the BF division (default `BF_Allocation`/`BF_Selection` to `0.0`) when `total_position_size <= 0`. Never divide by a zero position total.
  2. `main_orchestrator.run_pipeline` MUST replace zero `position_size` values (after `Shares × Price`) with the `$10 000` notional default via `zero_mask = position_size <= 0.0; dashboard_df.loc[zero_mask, 'position_size'] = 10000.0`.
  3. The pipeline crash handler in `_main_body` MUST use `telemetry.critical(..., exc_info=True)` so the full traceback appears in `logs/investyo.log` for future diagnosis. Covered by `tests/test_evaluate_portfolio_zero_positions.py` (5 tests) and Gravity Step 45.




## Reports tab — Brinson-Fachler Attribution Analysis (2026-06 UI task)
- `gui/panels.py` now exposes a full **Brinson-Fachler Attribution Analysis** section inside `render_report_viewer` (replacing the prior placeholder expander). Five pure helpers (kept module-level so they are testable without Streamlit) form the API boundary between the UI and `EvaluationEngine.calculate_brinson_fachler`:
  - `default_brinson_fachler_frame() -> pd.DataFrame` — seeds the `st.data_editor` with the canonical GICS-11 sector list (`GICS_SECTORS` constant) and the five-column header `(Sector, Portfolio Weight (%), Portfolio Return (%), Benchmark Weight (%), Benchmark Return (%))`.
  - `parse_pasted_sector_matrix(text: str) -> pd.DataFrame` — accepts TSV or CSV pasted from a spreadsheet (delimiter auto-detected from the first line) and supports BOTH header-bearing and header-less 5-column blocks. The header-less branch is detected by sniffing whether columns 2–5 of the first row parse as floats — without this guard, `pd.read_csv` would promote a real data row to the header and silently drop a sector.
  - `build_brinson_fachler_inputs(editor_df) -> (portfolio_df, benchmark_df)` — splits the editor frame into the two-DataFrame shape `EvaluationEngine._calculate_brinson_fachler_compat` consumes. **Unit-consistency invariant (must never regress):** the editor stores percents but the engine multiplies `weight × return` directly, so this helper divides every numeric column by 100 to convert percent → fraction. A regression here would not crash anything — the result dict would just be off by a factor of 100.
  - `validate_brinson_fachler_weights(editor_df, *, tolerance_pct=1.0) -> list[str]` — pre-flight checker called on every render: warns if portfolio or benchmark weights deviate from 100 % by more than 1 % or if any weight is negative (long-only attribution convention).
  - `compute_brinson_fachler(editor_df) -> dict` — orchestrates the above and returns the engine's canonical 8-key result dict (`Portfolio Return`, `Benchmark Return`, `Active Return`, `Allocation Effect`, `Selection Effect`, `Interaction Effect`, `Attribution Sum`, `Sector Details`). The `Sector Details` map of dicts uses the engine's documented per-row schema (`weight_p`, `weight_b`, `return_p`, `return_b`, `allocation_effect`, `selection_effect`, `interaction_effect`, `total_attribution`).
- The rendered UI persists editor + result state under `st.session_state["bf_editor_df"]` and `st.session_state["bf_result"]` so swapping Command Center tabs doesn't lose work. Per-sector breakdown and editor-input CSV downloads are both wired (`bf_download_sector`, `bf_download_input`).
- **Engine path:** `EvaluationEngine.calculate_brinson_fachler` dispatches DataFrame inputs to `_calculate_brinson_fachler_compat` (already present, unchanged). The UI ALWAYS goes through the DataFrame-compat path so the engine's name-mapping branch is exercised deterministically.
- **Test surface:** `tests/test_brinson_fachler_ui.py` (11 tests) pins the default frame shape, all paste-parser branches (TSV/CSV, header/header-less, percent-sign stripping, malformed input), the percent→fraction unit conversion, the validation warnings, and the attribution-sum ≈ active-return identity end-to-end through the engine.
- **Gravity:** `Gravity AI Review Suite.py` step `step_40_brinson_fachler_attribution_audit` verifies the same five invariants in the production code path so a refactor that breaks the wiring fails the audit.

## Launcher tab — dual entry points + telemetry feedback (2026-06 UI task)
- `gui/panels.py::render_launcher` now exposes **two launch paths** as distinct buttons, both spawned via `gui/orchestrator_runner.py`:
  - **▶️ Launch Pipeline** — `orchestrator_runner.launch_orchestrator(dry_run, refresh_account)` → `python main_orchestrator.py` (async, full pipeline, broker, HTML report).
  - **🔄 Refresh Data (Advisory)** — `orchestrator_runner.launch_advisory_main(refresh_account)` → `python main.py` (synchronous advisory loop). This is the project's canonical `.env`-loading entry point (the `load_dotenv()` call lives inside `main.main()`); using it from the GUI gives a fast, broker-free refresh that still hydrates `output/state_snapshot.json` for every observability panel.
- **`RunHandle`** now carries a `mode: str` field (`"orchestrator"` | `"advisory"`) and an explicit `log_path: Path` so callers know which stream to tail. `compute_stage_status()` follows `handle.log_path` rather than the hard-coded `RUN_LOG_PATH`, so the orchestrator stage markers don't false-positive on advisory output.
- **Two log files:** `output/gui_run.log` (orchestrator) and `output/gui_advisory.log` (advisory). Kept distinct so a stage-marker scan on one log never sees the other's text.
- **Pre-launch env readiness check:** `orchestrator_runner.validate_required_env(required=REQUIRED_ENV_VARS) -> dict[str, bool]` is called on every render. Missing variables are surfaced as an inline `st.error` BEFORE the buttons are clicked — eliminating the "Refresh Data does not produce observable results" failure mode where the subprocess silently degraded to neutral defaults. Default `REQUIRED_ENV_VARS = ("FRED_API_KEY",)` — only the minimum needed for non-trivial output; optional integrations (Robinhood, alerts, broker) are not required.
- **Telemetry tail:** the new helper `orchestrator_runner.read_telemetry_tail(max_lines=120)` reads `logs/investyo.log` (written by `alerting.setup_logging()`, rotated at 10 MB × 5 backups) and is rendered as a separate expander under the active run log. This is the entry-point-agnostic structured-logging stream — surfacing it gives the operator a single window into platform-wide diagnostics whether the orchestrator or main.py was launched.
- **Auto-refresh ticker:** opt-in checkbox (`launcher_auto_refresh`); while a run is active it sleeps 5 s then calls `st.rerun()` so the log tail keeps scrolling without manual clicks.
- **Status display:** finished-run banner now distinguishes exit code 0 (✅ green) from any non-zero code (❌ red) and labels the run with the mode (`Orchestrator` / `Advisory`).
- **Test surface:** `tests/test_orchestrator_runner.py` (12 tests) covers the env-validation truth table (missing / present / whitespace), the log-routing contract (`read_log_tail(handle=...)` follows `handle.log_path`), the telemetry-tail idle hint, and that `launch_advisory_main` / `launch_orchestrator` emit handles tagged with the correct `mode` and pointing at distinct log files. Subprocess is monkeypatched so no real child is spawned.
- **Gravity:** `step_41_launcher_telemetry_audit` pins the same wiring in the production code path (validate_required_env truth table, `launch_advisory_main` mode tag, distinct log paths, telemetry idle hint).

## Market Data tab — diagnostics, throttling, connectivity health (2026-06 UI task)
- **gui/market_data_diagnostics.py** — operator-facing helpers for the Market Data Provider tab. Keeps the UI decoupled from provider internals and unit-testable headlessly (no Streamlit imports). Four public surfaces:
  - `classify_market_error(exc) -> ErrorCategory` — five-way classification (`RATE_LIMIT` / `NOT_FOUND` / `NETWORK_TIMEOUT` / `MALFORMED` / `UNKNOWN`). Walks `__cause__`/`__context__` chains and inspects `getattr(exc, "status_code", None)` so wrapped exceptions (e.g. `MarketDataError` over `TimeoutError`; `FinnhubAPIException` with `status_code=429`) still resolve to the specific category instead of falling through to `UNKNOWN`. `category_label(cat)` returns the operator-facing string (`"API Rate Limited"`, `"Symbol Not Found"`, etc.).
  - `validate_quote(quote) -> QuoteValidation` — flags malformed quotes BEFORE they enter the rest of the pipeline (CONSTRAINT #4): NaN/non-positive price, missing timestamp, or inverted bid/ask. **Asymmetric tolerance:** a missing-only-bid OR missing-only-ask is NOT flagged (some providers legitimately omit one side outside RTH); a missing PRICE always flags. `QuoteValidation.label` is the GUI string (`"OK"` or `"⚠ <issues>"`).
  - `FetchHealthTracker` — sliding-window success/failure ledger feeding the connectivity badge. Default `window=20`, `healthy_threshold=0.9`, `degraded_threshold=0.5`. Three-tier `HealthStatus`: HEALTHY / DEGRADED / DOWN. Empty state is HEALTHY (neutral) so the first-paint badge isn't a red scare. Persisted across Streamlit reruns under `st.session_state["md_health_tracker"]`; reset button is exposed in the panel.
  - `BatchQuoteFetcher(fetch_fn, spacing_seconds=0.1, health_tracker=None, sleep_fn=time.sleep)` — generator-based throttled fetcher. `iter_fetch(symbols)` yields one `BatchResult(index, symbol, quote, validation, error, category)` per symbol so the Streamlit panel can stream progress-bar updates. Spacing is enforced via a rolling `_last_call_ts`, so back-to-back batches share one monotonic clock. The `sleep_fn` is pluggable for tests. Default 100 ms spacing (10 calls/s) is safely under yfinance's known rate-limit threshold and trivially within Alpaca's 200 calls/min ceiling. `summarise_categories(results) -> dict[str, int]` rolls the result list up into the `{"ok": …, "rate_limit": …, …}` toast/caption summary.
- **gui/panels.py::render_market_data** — rewritten on top of those helpers. Surface changes:
  - Four KPI columns: **Provider**, **Mode** (real-time vs. delayed), **Quote TTL**, **Connection** (the new health badge).
  - A persistent **yfinance-delayed info banner** when the active provider is not Alpaca, citing the env-var swap needed to upgrade.
  - **Throttle slider** (0–1000 ms, default 100 ms) so the operator can dial spacing per-batch.
  - **Streaming progress bar** (`i/N — SYMBOL`) driven by the `BatchQuoteFetcher` generator.
  - Per-symbol **error category** rendered in a new `Error` column (e.g. `"API Rate Limited: 429 …"`), never a bare `None`.
  - Per-symbol **validation Status** column (`"OK"` or `"⚠ price missing/NaN; bid > ask"`) so malformed quotes are visible at a glance and never silently feed the quant pipeline.
  - Two reset buttons: **♻ Reset provider singleton** (existing) and **🩺 Reset connection health** (new — clears the sliding window).
  - Last-batch results live in `st.session_state["md_last_results"]` so the table survives tab switches.
- **Test surface:** `tests/test_market_data_diagnostics.py` (33 tests). Class coverage: `TestClassifyMarketError` (12 parametrised exception-message cases + `status_code` attribute + chained `__cause__` walk + label round-trip), `TestValidateQuote` (happy path + NaN/zero price + inverted bid-ask + missing-one-side OK), `TestFetchHealthTracker` (empty=HEALTHY-neutral, all-success=HEALTHY, mixed=DEGRADED, all-fail=DOWN, window roll-off, invalid-threshold rejection), `TestBatchQuoteFetcher` (one-result-per-symbol, error classification + health update, success tracking, throttle spacing observed via injected `sleep_fn`, invalid-spacing rejected, malformed quote flagged not-ok, `summarise_categories`).
- **Gravity:** `step_42_market_data_diagnostics_audit` pins the four-surface contract (error classification matrix, validate_quote invariants, sliding-window thresholds, BatchQuoteFetcher throttle + classification) so a refactor that breaks any of them fails the audit.

## Observability tab — System Telemetry + Latency Heatmap + Error Log (2026-06 UI task)
- **gui/observability_telemetry.py** — headless helpers backing three new sections of `render_observability` in `gui/panels.py`. No Streamlit imports so the module is unit-testable cold. Three public surfaces:
  - **System telemetry** — `collect_system_telemetry(disk_path='/') -> SystemTelemetry` samples CPU%, logical-core count, 1-min load avg, memory% + bytes, disk% + bytes for `disk_path`, plus the current Python process's RSS / CPU% / thread count. Frozen `SystemTelemetry` dataclass; psutil unavailability is reported via `psutil_available=False` + NaN floats + `-1` byte counts (CONSTRAINT #4 — never zero-fabricated). Sampling failure is caught and degraded the same way. CPU% is sampled with `interval=None` (delta since last call) so it never blocks the Streamlit reactivity loop — first paint is therefore meaningless and the panel auto-refreshes. `format_bytes(n)` returns the human-readable B/KiB/MiB/GiB/TiB string (`"—"` for `n<0`).
  - **Latency heatmap** — `LatencySampleStore(max_samples=500)` is a bounded ring buffer of `LatencySample(symbol, source, quote_timestamp, ingested_at, latency_seconds, is_stale)`. `record(symbol, source, quote_timestamp, ingested_at=None, is_stale=False)` computes `(ingested - quote_ts).total_seconds()` (both promoted to UTC if naive); negative samples are preserved (forensic value) and clamped to 0 only at render time. **Cross-tab wiring (must not regress):** the store lives in `st.session_state["obs_latency_store"]` and is populated by `render_market_data` on every successful fetch, so one click of "Fetch quotes" on the Market Data tab feeds the heatmap rendered on the Observability tab. `summarise_latency(samples) -> {count, p50, p95, worst_symbol, worst_p95}` powers the KPI strip; empty input returns NaN-shaped output. Heatmap rendering uses pandas `Styler.background_gradient(cmap='RdYlGn_r')` over the `Latency (s)` column with a fall-through to a plain table when the Styler fails.
  - **Error aggregation / log viewer** — `parse_log_lines(lines) -> list[LogEntry]` parses `alerting.setup_logging()`'s formatter `"%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"` (UTF-8, comma- or dot-millisecond). Unparseable lines (multi-line traceback continuations) are RETAINED as `LogEntry(level="", parsed=False, raw=…)` so context survives. `filter_log_entries(entries, *, min_level='INFO', contains=None)` filters ordinally over `VALID_LEVELS = ('DEBUG','INFO','WARNING','ERROR','CRITICAL')` AND a case-insensitive substring; unparsed lines are KEPT regardless of level so traceback continuations are never dropped by a level filter. `tally_levels(entries)` drives the KPI metrics row. `read_log_tail(path, max_lines=500)` is the file IO (missing file → `[]`, never raises). The panel points at `gui.orchestrator_runner.TELEMETRY_LOG_PATH = logs/investyo.log`.
- **gui/panels.py::render_observability** — extended with three new sections after Strategy P&L, each a private helper to keep `render_observability` declarative:
  - `_render_observability_system_telemetry()` — two-column "Host" / "Process" KPI strip with red-saturation warnings at CPU ≥ 90% / memory ≥ 90% and a yellow caution at CPU ≥ 75%.
  - `_render_observability_latency_heatmap()` — KPI strip (Samples, p50, p95, worst symbol) + colour-graded table + "🧹 Clear latency samples" button. Empty-state shows an info hint pointing the operator to the Market Data tab.
  - `_render_observability_error_log()` — KPI strip per level (CRITICAL/ERROR/WARNING/INFO + Total), level dropdown defaulting to INFO, substring filter, and a `st.code(..., language='log')` block capped at 300 most-recent matching lines so a runaway run can't freeze the browser.
- **gui/panels.py::render_market_data** — now also feeds `st.session_state["obs_latency_store"]` (`LatencySampleStore`) on every successful fetch so the Observability heatmap stays in sync without a separate "Sample latency" button.
- **Test surface:** `tests/test_observability_telemetry.py` (26 tests). Classes: `TestSystemTelemetry` (happy shape, forced psutil ImportError → NaN-shaped output, `format_bytes` unit ladder + negative-dash), `TestLatencySampleStore` (record + compute, naive→UTC promotion, ring-buffer roll-off, clear, invalid capacity, empty/non-empty summary), `TestParseLogLines` + `TestFilterLogEntries` + `TestTallyAndIO` (canonical-line round-trip, every level parses, traceback-continuation kept unparsed, blank-line skip, ordinal threshold, case-insensitive substring, unparsed kept under filter, invalid level rejected, tally counts including UNPARSED bucket, missing-file → `[]`, `max_lines` tail).
- **Gravity:** `step_43_observability_telemetry_audit` pins the three-surface contract — telemetry NaN-fallback shape, latency store roll-off + summary, log parser preserves unparseable lines under a level filter.

## Safety / Analytics / Control tabs — circuit breakers, dependency map, version registry, mode toggle, drill-down (2026-06 UI task)
- **gui/circuit_breakers.py** — derivation layer over the file-backed state the platform already writes (`output/KILL_SWITCH`, `output/risk_gate_blocks.jsonl`). Five public surfaces:
  - `read_block_log(path, max_lines=500)` — tolerant JSON-lines reader; corrupt lines are dropped + logged at DEBUG (never raised). Newest first.
  - `derive_kill_switch_trip(path, reason=None)` — emits a CRITICAL `CircuitBreakerTrip` when the sentinel exists; sentinel text and mtime are surfaced as `triggered_at` + detail.
  - `derive_block_log_trips(blocks, *, window=24h, now=…)` — projects risk-gate blocks into typed trips. Keeps the newest per `(check_name, strategy_id)` and drops anything outside the window. Unknown `check_name` values still bubble through (tagged `WARNING`) so a future risk-gate addition surfaces immediately — operator's signal to add a row to the local `_KNOWN_CHECKS` mapping.
  - `collect_circuit_breaker_trips(...)` — top-level helper; kill-switch trip first, then newest-first block-derived trips.
  - `summarise_trips(trips) -> {CRITICAL, WARNING, TOTAL}` — KPI strip rollup.
  - **Architectural rule:** adding a new breaker means adding a check inside `execution/risk_gate.py` and tagging the emitted block; the panel auto-picks it up via `_KNOWN_CHECKS`. NEVER re-implement risk logic in `gui/circuit_breakers.py`.
- **gui/dependency_map.py** — declarative `DataSource` (enum) → `Consumer` (frozen dataclass) graph. The map (`CONSUMERS: Dict[DataSource, tuple[Consumer, ...]]`) is hand-curated by design: inferring it from imports would over-couple to call sites that gate sources on config flags. Public helpers: `impacted_consumers(degraded) -> list[ImpactRecord]` (string inputs that don't match a known `DataSource` resolve to `DataSource.UNKNOWN` with empty impact — never fabricated, CONSTRAINT #5), `all_consumers()`, `render_edges()`. **Extension rule:** add a new consumer of an existing source → append to the right `_*_CONSUMERS` tuple. Add a new source → add a new `DataSource` enum value AND a `_LABELS` entry AND a `CONSUMERS` row. Always carry the data-source change through to Gravity.
- **gui/strategy_registry.py** — strategy file versioning + global execution-mode toggle.
  - `StrategyVersion` dataclass: `(name, file_path, version_hash, last_modified, enabled, weight)`. `version_hash` = sha256(file)[:12] hex; `None` when the file is missing or unreadable (CONSTRAINT #5).
  - `list_strategy_versions(*, module_names=None, weights=None, disabled=None, signals_dir=None)` — joins the live `signals.registry.global_registry` (when available) with `settings.SIGNAL_WEIGHTS` / `settings.DISABLED_SIGNAL_MODULES`. All four kwargs are injectable for tests. Operationally: "version" here means *was the file touched since last orchestrator run*, not semver.
  - `ExecutionMode` enum: `SIMULATION` (DRY_RUN=true), `PAPER` (ALPACA_PAPER=true), `LIVE` (ALPACA_PAPER=false). `read_active_mode() -> ModeState` synthesises mode from the two env vars with `DRY_RUN` winning over `ALPACA_PAPER` (OrderManager intercepts before broker contact regardless of `ALPACA_PAPER`).
  - `set_active_mode(mode)` writes BOTH `DRY_RUN` and `ALPACA_PAPER` together (no half-flips) via the allowlist-bounded `gui/env_io.write_setting`. Effect on **next** launch only — never patches a running `settings.Settings`.
  - **New `ALLOWED_KEYS` entry**: `ALPACA_PAPER`. The Alpaca *secret* keys remain in `SECRET_KEYS` and are still write-protected.
- **gui/panels.py** changes:
  - `render_gravity_audit` is now the **Safety** tab (renamed in the docstring): adds Circuit Breaker Dashboard (`_render_circuit_breaker_dashboard`) + Dependency Map (`_render_dependency_map`) above the existing Gravity audit launcher.
  - `render_strategy_matrix` gains a top **Global Execution Mode** selector (`_render_strategy_mode_toggle`) and a **Strategy Version Registry** table (`_render_strategy_version_registry`) above the existing module enable/weights form. The Live confirm button is labelled `🔴 CONFIRM LIVE PRODUCTION` to force a deliberate click.
  - `render_report_viewer` shows a provenance banner (`_render_report_provenance_banner`): `🔵 Live data` (blue `st.info`) when an orchestrator snapshot exists AND mode ∈ {PAPER, LIVE}; `⚪ Backtested / simulated` (grey Markdown blockquote) otherwise. A new **🔬 Drill down by symbol** expander surfaces the full signal row + recent closed trades for that symbol from `transactions_store.TransactionsStore` — integrating, not reinventing (CONSTRAINT #7).
- **Test surface:** `tests/test_circuit_breakers.py` (12 tests), `tests/test_strategy_registry.py` (12 tests), `tests/test_dependency_map.py` (10 tests). 34 tests total. Coverage: block-log corrupt-line tolerance, kill-switch trip with reason, known-vs-unknown check classification, window filter + per-(name, strategy) dedup; strategy-version happy path + missing-file degradation + hash-changes-on-edit; mode resolution truth table + invalid-mode rejection + two-flag write atomicity; dependency-map registry sanity + UNKNOWN-source no-fabrication + edge-count symmetry.
- **Gravity:** `step_44_safety_analytics_control_audit` pins the contract — kill-switch derivation, block-log dedup, unknown-source no-fabrication, strategy-version hashing, mode resolution truth table.
- **Docs:** `docs/HOW_TO_GUIDE.md` and `docs/RUNBOOK.md` carry a new section on the Safety tab + global mode toggle so a fresh operator can find the kill-switch override and the Live confirm button without spelunking the source.

## Enhanced Observability & Error Handling (2026-06, GUI)

### Dead-Letter Queue (gui/dead_letter.py + main_orchestrator.py)
- **`gui/dead_letter.py`** — read-side consumer of `output/dead_letter.json`. Public API: `DeadLetterEntry` (frozen dataclass: symbol, stage, error, timestamp), `DeadLetterReport` (frozen: run_id, generated_at, entries; `.is_clean`, `.symbols`), `read_dead_letter(path) -> Optional[DeadLetterReport]`. Missing/corrupt file → `None` (CONSTRAINT #4 — no fabricated success). Write side lives in `main_orchestrator.run_pipeline` inline (no `gui.*` import from pipeline layer).
- **`main_orchestrator.run_pipeline`** — per-ticker eval loop now wrapped in try/except with `_stage` tracker (`"dto_construction"` → `"strategy"` → `"edge_ratio"` → `"results"`). Failures append `{symbol, stage, error, timestamp}` to `dead_letter_entries`; after the loop, `output/dead_letter.json` is written atomically (write-then-rename). Empty entries = clean run (file still written so GUI always has a current timestamp). This implements CONSTRAINT #6 for the eval loop — previously, any single-ticker exception would crash the entire pipeline.
- **`gui/orchestrator_runner.py`** — new `RETRY_LOG_PATH = output/gui_retry.log` and `launch_symbol_retry(symbol, refresh_account=False) -> RunHandle`. Spawns `main.py` with `env["WATCHLIST"] = symbol.upper()` so only that ticker (plus held positions) is evaluated. Returns `RunHandle(mode="retry")`. No changes to `main.py` needed — it already reads `WATCHLIST` via `_build_universe()`.
- **`gui/panels.py`** — new `_render_dead_letter_queue()` inserted in `render_launcher()` (after telemetry expander, before auto-refresh). Shows run timestamp, failed symbol + stage + error, and per-symbol **🔄 Retry** buttons that call `launch_symbol_retry()` and display the retry log inline.

### Contextual Error Classification (gui/observability_telemetry.py)
- **`extract_symbol_from_message(message) -> Optional[str]`** — ordered regex patterns (Dead-lettered, "for TICKER", `symbol=`, `ticker=`, `[TICKER]`, prefix colon). Excludes single-letter candidates and common false positives (`AT`, `IN`, `OR`, etc.).
- **`classify_log_entry(entry) -> Literal["systemic", "symbol_specific", "unknown"]`** — symbol-specific is checked FIRST (a dead-lettered ticker message logged by `main_orchestrator` is NOT systemic even though the logger name contains "orchestrat"). Systemic keywords: pipeline, orchestrat, crash, fatal, DataEngine, MacroEngine, fred, sheet, database, schema, etc.
- **`_render_observability_error_log()`** enhanced: a **Contextual Error Summary** expander appears above the raw log when WARNING/ERROR/CRITICAL lines are present, grouping entries into systemic / symbol-specific (deduplicated per ticker) / unclassified buckets. Symbol-specific errors link the operator to the Launcher's Dead-Letter Queue.

### Heartbeat Trend Sparkline (gui/observability_telemetry.py)
- **`HeartbeatSample`** — frozen dataclass: `sampled_at` (UTC datetime), `age_seconds` (float, NaN preserved for gaps).
- **`HeartbeatTrendStore(max_samples=60)`** — bounded ring buffer (deque); `.record(age_seconds) -> HeartbeatSample`; `.to_dataframe()` returns a pandas DataFrame indexed by `sampled_at` with `age_seconds` column for `st.line_chart`. `.clear()` for operator reset.
- **`_render_observability_heartbeat_trend()`** in `gui/panels.py` — wired into `render_observability()` before the system telemetry section. Samples `orchestrator_runner.heartbeat_age_seconds()` on each render; persists store in `st.session_state["obs_heartbeat_trend"]`. KPI strip: Current age / Peak age / Samples / Status (🟢/🟡/🔴). A rising trend over 60 samples ≈ 30 minutes signals a memory leak or hanging thread.

### Test surface
- **`tests/test_dead_letter.py`** (16 tests) — DeadLetterEntry frozen, DeadLetterReport.is_clean/.symbols, read_dead_letter (missing, corrupt, partial entry, empty file, valid payload, run_id preservation).
- **`tests/test_heartbeat_trend.py`** (33 tests) — HeartbeatSample frozen/NaN, HeartbeatTrendStore ring-buffer roll-off/clear/invalid-capacity/to_dataframe, extract_symbol_from_message (7 positive patterns + false-positive exclusions), classify_log_entry (systemic/symbol_specific/unknown, priority ordering, unparsed continuation).
- **Gravity:** `step_46_enhanced_observability_audit` — 10 checks covering all three features: dead-letter read API, contextual classification priority, ring-buffer roll-off, `launch_symbol_retry` callable, `run_pipeline` dead-letter write and `_stage` tracker.

## GUI Operational Efficiency, UX & Architectural Integration (2026-06)

### Pipeline StageStatus enum (gui/orchestrator_runner.py)
- **`StageStatus(str, enum.Enum)`** — five members: `SUCCESS/"success"`, `ACTIVE/"active"`, `ERROR/"error"`, `PENDING/"pending"`, `SKIPPED/"skipped"`. Inherits from `str` so legacy callers doing `if status == "active"` continue to work without modification.
- **`compute_stage_status(handle) -> Dict[str, StageStatus]`** now returns typed `StageStatus` values. New behaviour: `DRY_RUN=true` on an orchestrator run forces the `"Execution"` stage to `SKIPPED`; a non-zero exit code on the last-active stage emits `ERROR`; prior stages on an error run stay `SUCCESS`.
- **`STAGES`** list has exactly 4 pipeline stages: Data Acquisition, Processing, Forecasting, Execution.
- Launcher stage indicator rendering updated: uses `StageStatus`-aware icon map (`✅`/`🟡`/`🔴`/`⚪`/`⏭️`) and displays `.value` for enum instances.
- **Test surface:** `tests/test_pipeline_stage_status.py` (12 tests) — enum str-subclass, all 5 members, string equality, 4-stage count, compute_stage_status variants (None handle, no log, finished-clean, dry-run-skipped, error-path).

### Preflight Runner (gui/preflight_runner.py)
- **`PreflightCheck`** (frozen dataclass: `name, passed, reason, warning`). **`PreflightReport`** (frozen: `all_passed, checks, error, returncode`).
- **`run_preflight(timeout, skip) -> PreflightReport`** — subprocess wrapper around `scripts/preflight_check.py --json`. **CONSTRAINT #4**: timeout/missing-script/corrupt-JSON/empty-stdout → `all_passed=False` — never fabricates success.
- **`gui/panels._render_preflight_panel()`** — on-demand gate button in the Launcher tab; renders per-check pass/fail table; uses `st.session_state["preflight_report"]` for persistence across reruns.
- **Test surface:** `tests/test_preflight_runner.py` (16 tests) — import, frozen fields, good path, non-zero exit, timeout, missing script, corrupt JSON, empty stdout, subprocess exception, wiring checks.

### Launcher Safety Controls (gui/panels._render_launcher_safety_controls)
- **`_render_launcher_safety_controls()`** — kill-switch toggle + DRY_RUN checkbox + Safe Mode composite indicator in the Launcher tab. Safe Mode is **DERIVED** (no new env var): `ks.is_active() AND settings.DRY_RUN`. Writes `DRY_RUN` via the allowlist-bounded `gui.env_io.write_setting`. Wired into `render_launcher()` between stage indicators and the log expanders.
- **Test surface:** `tests/test_launcher_safety_controls.py` (12 tests) — helper exists/callable, SAFE_MODE not in ALLOWED_KEYS/SECRET_KEYS, DRY_RUN in ALLOWED_KEYS, write round-trips, kill-switch activate/deactivate, safe-mode derivation logic.

### Persistent Run-Mode Header (gui/run_mode.py + gui/app.py)
- **`RunModeState`** (frozen dataclass: `mode, process, dry_run, alpaca_paper, icon, color, pid, run_mode_label`). **`read_active_run_mode(session_state={}) -> RunModeState`** — Streamlit-free derivation (testable headlessly). Mode truth table: `(DRY_RUN=T,*) → Simulation`; `(False,PAPER=T) → Paper`; `(False,False) → Live`.
- **`gui/app.py`** renders a persistent colored banner above the tab bar on every Streamlit render: `st.error` (red) for Live, `st.warning` (amber) for Paper, `st.info` (blue) for Simulation.
- **Test surface:** `tests/test_run_mode.py` (15 tests) — import, frozen, idle/running/finished process derivation, mode truth table, icon/color/label non-empty, app.py references run_mode.

### Symbol Search (gui/symbol_search.py)
- **`filter_by_symbol(df, query, *, column="Symbol") -> pd.DataFrame`** — Streamlit-free, case-insensitive contains match on the symbol column. Empty/None/whitespace query returns the full DataFrame unchanged. NaN symbol rows always pass through (never silently dropped for EQUITY_ONLY sentinels). Falls back to first column when `"Symbol"` absent.
- Wired into **`render_report_viewer`** (🔍 Filter by symbol above the signals table) and **`render_live_inventory`** (🔍 Filter by symbol above the inventory table).
- **Test surface:** `tests/test_symbol_search.py` (15 tests) — passthrough, exact/partial/case-insensitive match, no-match empty, NaN pass-through, custom column, fallback column, empty DataFrame, returns-same-object.

### Strategy Health View (gui/strategy_health.py + gui/panels._render_strategy_health)
- **`DeployabilityGate`** (frozen: `metric, value, threshold, direction, passed`). **`StrategyHealth`** (frozen: `strategy_id, deployable, gates, is_options_selling, stress_passed, last_audited_at`).
- **`read_gravity_report(path) -> list[dict]`** — tolerant reader of `output/gravity_verification_report.json`. Missing → `[]`, corrupt JSON → `[]`, wrong schema → `[]` (CONSTRAINT #4 — never fabricate).
- **`evaluate_gate(strategy_dict) -> StrategyHealth`** — evaluates one strategy dict against thresholds from `validation.thresholds` (single source of truth). Missing/NaN metric → `gate.passed=None`. `deployable` mirrors the report field rather than re-deriving.
- **`gui/panels._render_strategy_health()`** — top section of `render_gravity_audit` showing per-strategy gate table with PASS/FAIL/N/A indicators. Wired first (before circuit breakers / dependency map).
- **`Gravity AI Review Suite._write_gravity_verification_report()`** — writes `output/gravity_verification_report.json` atomically (write-then-rename) at the end of every audit run.
- **Test surface:** `tests/test_strategy_health.py` (20 tests) — import, frozen fields, read_gravity_report failure modes (missing/corrupt/non-dict/non-list), valid-file happy path, evaluate_gate all-pass/individual-gate-fail/missing-metric-None/NaN-metric-None/deployable-mirrors-report/options-selling-stress.

### Gravity Audit steps 47-50
- **`step_47_launcher_safety_bundle_audit`** — verifies `_render_launcher_safety_controls` exists, touches DRY_RUN + kill-switch together, SAFE_MODE not in ALLOWED_KEYS.
- **`step_48_preflight_runner_audit`** — verifies `run_preflight` returns typed report; timeout → `all_passed=False`; `_render_preflight_panel` wired into `render_launcher`.
- **`step_49_dual_mode_header_audit`** — verifies `gui.run_mode` importable, `read_active_run_mode({})` returns `process="idle"`, `gui/app.py` references `run_mode`.
- **`step_50_strategy_health_audit`** — verifies `validation.thresholds` exports 5 constants; `validation.harness` imports from it; `read_gravity_report` → `[]` on missing/corrupt file; `tests/test_strategy_health.py` exists.
- **`_extend_launcher_telemetry_audit_stage_status`** — appends StageStatus enum checks to step_41: `StageStatus` is `str`-subclassed, 5 members, string equality, `STAGES` has 4 elements.
- **`_extend_safety_control_audit_launcher`** — appends Launcher-tab safety-control checks to step_44: `_render_launcher_safety_controls` exists and `render_launcher` calls it.

## Tier 1 Decision Support — "Δ Since Last Run" snapshot diff (2026-06)

### scripts/snapshot_diff.py — rotation + diff engine
- **`scripts/snapshot_diff.py`** — single source of truth for snapshot rotation AND the "what changed since yesterday" diff. Public surface: `SnapshotDiff` (frozen dataclass: `prev_ts`, `curr_ts`, `regime_change`, `new_buys`, `action_flips`, `conviction_deltas`, `added_holdings`, `dropped_holdings`, `notes`, `.is_empty`, `.to_dict()`), `load_snapshot(path)`, `list_rotated_snapshots(output_dir)`, `rotate_snapshot(snapshot, output_dir, *, max_age_days, now=None)`, `compute_diff(prev, curr, *, conviction_delta_threshold)`, `compute_diff_from_history(output_dir, *, conviction_delta_threshold)`, `format_markdown(diff)`. CLI: `python -m scripts.snapshot_diff prev.json curr.json [--format markdown|json] [--conviction-threshold 0.2]`; with no positional args, defaults to the two most-recent rotated snapshots under `--output-dir`.
- **Tolerance contract (CONSTRAINT #4 + #6):** every loader/diff path is wrapped — missing file → `None`, corrupt JSON → `None`, non-object JSON → `None`, write/prune failures → logged at WARNING/DEBUG and skipped; the diff engine NEVER raises so the daily HTML report renders even with a degraded history dir. First-run case (`prev is None`): all current BUYs become `new_buys`, all current holdings become `added_holdings`, `regime_change` stays `None` (a first-run regime is not a "change"). Identical snapshots → `SnapshotDiff.is_empty == True`.
- **Classification rules (pinned by `tests/test_snapshot_diff.py` + Gravity step 51):**
  - *New BUY* = (no prior signal OR prior action did not contain `"BUY"`) AND current action contains `"BUY"`. Takes precedence over `action_flips` (a HOLD→BUY is reported once, as a new_buy).
  - *Action flip* = both sides present, both non-empty, different, AND NOT already in `new_buys`. Each entry: `{symbol, before, after}`.
  - *Conviction delta* = `advisory_conviction` (or fallback `conviction`) present on both sides AND `|after − before| ≥ conviction_delta_threshold`. Default threshold = 0.2.
  - *Holdings added/dropped* = set diff over the snapshot's `holdings` list (or, if absent, backfilled from `signals[].shares > 0`).
  - *Regime change* = `prev.market_regime` and `curr.market_regime` both non-empty AND different.
- **Rotation contract:** `rotate_snapshot()` writes `output/history/state_snapshot_<UTC>.json` via atomic write-then-rename. Filename encodes the snapshot's own ISO `timestamp` field when parseable (else wall-clock UTC), formatted `state_snapshot_YYYYMMDDTHHMMSSZ.json` (colon-free, FAT/NTFS-safe). Files older than `max_age_days` are pruned in the same call; `max_age_days=0` disables pruning. Non-matching filenames in `history/` are ignored.

### Wiring
- **`main_orchestrator._write_state_snapshot()`** now writes a `holdings: list[str]` field (sorted symbols where `Shares > 0`) and calls `rotate_snapshot(snapshot, settings.OUTPUT_DIR, max_age_days=settings.SNAPSHOT_HISTORY_DAYS)`. The snapshot is rotated BEFORE the HTML report renders (moved out of the tail of `_main_body()`) so the Δ-band diff sees `curr = this run / prev = previous run`. The HTML-report block then calls `compute_diff_from_history()` and passes `snapshot_diff_payload` to `generate_html_report(snapshot_diff=...)`.
- **`main._write_state_snapshot(result, macro_dto)`** — NEW helper in the advisory entry point. Emits the same JSON schema (timestamp, holdings, signals, market_regime, vix, kill_switch_active, macro_regime_gate_enabled) so the diff engine sees a consistent shape across both entry points. Called from `_write_html_report()` BEFORE the HTML render. `_load_snapshot_diff_for_report()` then returns the `.to_dict()` payload (or `None` on first ever run / any failure).
- **`diagnostics_and_visuals.generate_html_report(..., snapshot_diff=None)`** — new keyword-only kwarg. When non-`None`, the template renders a top-of-report "Δ Since Last Run" band (CSS class `.delta-band`) above the macro/regime cards, with grid cells for `new_buys`, `action_flips`, `conviction_deltas`, `added_holdings`, `dropped_holdings` and a banner for `regime_change`. When `None` (no prior snapshot or any rotation/diff failure) the band is hidden entirely — the report is unchanged.

### New env vars / settings
- **`SNAPSHOT_HISTORY_DAYS: int = 30`** — snapshots in `output/history/` older than this are pruned each run; `0` disables pruning.
- **`SNAPSHOT_CONVICTION_DELTA_THRESHOLD: float = 0.2`** — `|Δ advisory_conviction|` at or above this surfaces in the Δ band; smaller moves are noise-suppressed. Both pinned by Gravity step 51.

### Test surface
- **`tests/test_snapshot_diff.py`** (24 tests). Classes: `TestLoadSnapshot` (missing/empty/corrupt/non-object/valid round-trip), `TestRotation` (rotation-writes-history-file, filename encodes timestamp, prune drops >max_age, prune disabled when max_age=0, ignores unrelated files), `TestComputeDiff` (first-run lists buys/holdings, identical-snapshots-yield-empty, action-flip BUY→HOLD, new-buy precedence over flip, conviction threshold filters 0.19 but surfaces 0.21, regime change detected, no regime change when equal, holdings added/dropped, holdings backfilled from `shares > 0`), `TestHistoryIntegration` (two rotations → real diff, single rotation → first-run shape, no history → empty with note), `TestModuleSurface` (default threshold = 0.2, `SnapshotDiff.to_dict()` is JSON-serialisable).

### Gravity step 51
- **`step_51_snapshot_diff_audit`** — 10 checks: module surface (`SnapshotDiff`, `compute_diff`, `rotate_snapshot`, `compute_diff_from_history`, `load_snapshot`, `list_rotated_snapshots`, `DEFAULT_CONVICTION_DELTA_THRESHOLD`); default threshold = 0.2; `settings.SNAPSHOT_HISTORY_DAYS == 30` AND `settings.SNAPSHOT_CONVICTION_DELTA_THRESHOLD == 0.2`; `generate_html_report` accepts `snapshot_diff` kwarg (signature inspection); `main_orchestrator.py` references `rotate_snapshot` + `"holdings"` + `compute_diff_from_history`; `main.py` defines `_write_state_snapshot` AND references `rotate_snapshot` AND passes `snapshot_diff=`; `rotate_snapshot()` round-trip via tempdir; first-run BUYs land in `new_buys` not `action_flips`; `load_snapshot(corrupt_file)` returns `None` (CONSTRAINT #4 + #6); `tests/test_snapshot_diff.py` exists.

## Tier 1 / 1.2 — Conviction Calibration Tracker (2026-06)

### Overview
"When the system says 0.80, does it actually win 80% of the time?" Bins closed trades with recorded conviction scores into equal-width buckets, computes actual win rate per bucket, and renders a reliability diagram in the GUI Reports tab.

### Schema migration (`transactions_store.py`)
- `Trade.conviction = Column(Float, nullable=True)` — advisory signal conviction [0, 1] at entry time.
- `TransactionsStore._ensure_conviction_column()` runs on every `__init__`: inspects existing columns via SQLAlchemy `inspect`, issues `ALTER TABLE trades ADD COLUMN conviction REAL` only when missing. Safe to call on new or legacy databases.
- `record_trade()` gains `conviction: Optional[float] = None` kwarg (backward-compatible; existing callers unaffected).

### `evaluation_engine.calibration_curve()` (module-level function)
```
calibration_curve(transactions_store, n_bins=10, min_trades_per_bin=5) -> pd.DataFrame
```
- Reads `closed_trades_df()`, drops rows missing `conviction`/`entry_price`/`exit_price` (CONSTRAINT #4).
- Win definition (side-aware): long → `exit_price > entry_price`; short → `exit_price < entry_price`.
- Bins by conviction using `pd.cut` over `np.linspace(0, 1, n_bins+1)`.
- `win_rate=NaN` for bins with fewer than `min_trades_per_bin` trades (never fabricated).
- Returns empty DataFrame with correct 7-column schema (`bin_low`, `bin_high`, `bin_center`, `conviction_mean`, `win_rate`, `count`, `perfect_calibration`) on any failure — dead-letter tolerant (CONSTRAINT #6).

### GUI: Reports tab (`gui/panels.py`)
- `_render_calibration_section()` — inserted after Brinson-Fachler, before report exports.
- KPI strip: Trades w/ Conviction / Overall Win Rate / Calibration Error (MAE) / Bins w/ Data.
- Reliability diagram via matplotlib (`st.pyplot`): bars = actual win rate per bin, dashed diagonal = perfect calibration.
- "No conviction data yet" info box when no conviction-annotated closed trades exist.

### Test surface
- **`tests/test_calibration.py`** (24 tests, 5 classes): `TestSchema` (empty store, no conviction column, all-null, store read failure, count dtype); `TestWinRateLogic` (long win/loss, short win/loss, mixed, exit==entry is not a win); `TestBinning` (n_bins, bin bounds, center=midpoint, perfect_calibration=center, trades in correct bins); `TestMinTradesGate` (below threshold→NaN, at threshold OK, empty bin mean→NaN); `TestRecordTradeConviction` (kwarg accepted, persisted, None→null, column in open trades).

### Gravity step 52
- **`step_52_calibration_audit`** — 10 checks: import, schema constant, empty store, no conviction column, all-null, long win logic, short win logic, min_trades gate, dead-letter read failure, record_trade persistence.

## Tier 1 / 1.3 — Manual Execution Decision Journal (2026-06)

### Overview
Operator logs whether each advisory signal was acted on, passed, or modified. Records accumulate in `output/decision_log.jsonl` (JSON-Lines, append-only). An optional join step links "acted" entries back to the nearest `TransactionsStore` trade record within ±24 h so the calibration tracker (1.2) can filter to decisions that were actually executed — turning the calibration from "all signals" to "signals the operator endorsed."

### New module: `gui/decision_log.py`
Headlessly testable (no streamlit imports). Public API:
- `DecisionEntry` — frozen dataclass: `symbol`, `action_taken` (`"acted"|"passed"|"modified"`), `signal_action`, `conviction`, `notes`, `timestamp`, `signal_ts`, `trade_id`.
- `append_decision(entry, log_path)` — atomic JSONL line append; creates parent dirs.
- `read_decisions(log_path)` — tolerant reader; corrupt/blank lines skipped (CONSTRAINT #6).
- `decisions_df(log_path)` — typed DataFrame (Int64 nullable `trade_id`); empty schema when log absent (CONSTRAINT #4).
- `join_to_store(entry, transactions_store, window_hours=24.0)` — finds closest matching trade by symbol within `±window_hours` of `entry.timestamp`; returns `None` on no-match or any failure.
- `log_decision(...)` — orchestrates: build → join (if acted) → append; injectable `now_fn` for tests.

### GUI: Reports tab (`gui/panels.py`)
- `_render_decision_journal_section(signals)` — inserted between the drill-down expander and Brinson-Fachler.
- Symbol selectbox + signal-context KPI strip (system action, conviction) + notes textarea.
- Three buttons: **✅ Acted** / **⏭ Passed** / **🔁 Modified** (Modified requires non-empty notes).
- Success banner after click shows join result (`linked to trade #N` or `no match within 24h`).
- Past-decisions collapsible log with CSV download.

### Log file
`output/decision_log.jsonl` — append-only, never read by the signal pipeline. Written to `settings.OUTPUT_DIR / "decision_log.jsonl"` from the GUI. Never committed to git (add to `.gitignore` if not already excluded via `output/`).

### Join convention (must never regress)
- `join_to_store` is called ONLY when `action_taken == "acted"`. "passed" and "modified" decisions never set `trade_id`.
- The join window default is 24 h. Never fabricate a `trade_id` — if no match, set `None` (CONSTRAINT #4).
- Symbol matching is case-insensitive (normalized to uppercase).

### Test surface
- **`tests/test_decision_log.py`** (30 tests, 5 classes): `TestDecisionEntry` (frozen, fields, action_taken values); `TestAppendAndRead` (round-trip, multiple entries, missing file, corrupt/blank lines, trade_id/None-conviction round-trips, parent-dir creation); `TestDecisionsDf` (empty schema, Int64 dtype, nullable NaN, row count); `TestJoinToStore` (within window, outside window, symbol not found, closest pick, store failure, case-insensitive); `TestLogDecision` (field wiring, log append, passed/modified skip join, acted joins, acted no match → None, None conviction).

### Gravity step 53
- **`step_53_decision_log_audit`** — 10 checks: import, frozen dataclass + fields, round-trip, empty schema, corrupt line skip, join within window, join returns None outside, passed skips join, acted joins, test file exists.

## Tier 5.1 — ADVISORY_ONLY Mode Quarantine (2026-06)

### Summary
- New flag `settings.ADVISORY_ONLY: bool = True` (project default). When `True`, the entire broker-execution surface is quarantined: `main_orchestrator._execute_broker_orders` returns immediately (no broker imports), the GUI Strategy Matrix mode toggle is suppressed, and `scripts/preflight_check.py` drops the broker-readiness checks (`alpaca_configured` / `alpaca_paper_mode` / `dry_run_disabled` / `paper_trading_duration`) in favour of a new `advisory_only_active` check.
- ADVISORY_ONLY is a HARDER gate than `DRY_RUN`: `DRY_RUN` is enforced inside `OrderManager` (one method, future callers could bypass), while ADVISORY_ONLY is enforced at the `_execute_broker_orders` boundary AND surfaced in every GUI tab as a persistent banner, so an operator cannot click into Live by mistake. Both flags must agree (`ADVISORY_ONLY=false` AND `DRY_RUN=false` AND `ALPACA_PAPER=false`) to reach a live submission.

### Wiring
- **`settings.py`** — adds `ADVISORY_ONLY: bool = Field(default=True, ...)`.
- **`main_orchestrator._execute_broker_orders`** — adds the early-return guard at the very top of the function body (BEFORE the broker-stack imports), emitting an INFO log: `"ADVISORY_ONLY=True — broker execution surface is quarantined; skipping all order submission, reconciliation, and broker imports."`
- **`main_orchestrator._main_body`** (call site of `_execute_broker_orders`) — when `ADVISORY_ONLY=True`, logs `"📋 ADVISORY_ONLY=True — pipeline produced N signals; broker execution is disabled for this run."` and does NOT check `ALPACA_API_KEY`/`SECRET_KEY` — so an operator who happens to have keys in `.env` from an earlier paper-trading phase does NOT trigger any broker import.
- **`gui/app.py`** — the persistent run-mode header now branches on ADVISORY_ONLY first; when `True`, renders a single `st.info` "📋 **ADVISORY MODE** — no orders will be submitted to any broker." banner above the tab bar instead of the Simulation/Paper/Live badge (which would be misleading while the broker is quarantined).
- **`gui/panels._render_strategy_mode_toggle`** — when `ADVISORY_ONLY=True` does NOT render the radio + confirm button; renders an `st.warning` "📋 **Advisory mode — broker execution disabled.**" placeholder + a read-only caption showing the underlying `DRY_RUN` / `ALPACA_PAPER` flags. Set `ADVISORY_ONLY=false` in `.env` to restore the live mode-switcher.
- **`scripts/preflight_check.py`** —
  - New `check_advisory_only_active()` function. PASS (loud) when `ADVISORY_ONLY=True`; PASS with `warning=True` when False so the operator confirms they deliberately lifted the quarantine.
  - Module-level constant `_ADVISORY_AUTO_SKIP` (8 entries): broker-dependent checks (`alpaca_configured`, `alpaca_paper_mode`, `dry_run_disabled`, `paper_trading_duration`, `alpaca_key_rotation_recent`) + advisory false-positive checks (`heartbeat_fresh`, `validation_reports`, `no_unexpected_risk_blocks`). **Stage 2 expanded from 4 → 7 entries** to eliminate false-positive failures on a correctly-running advisory deployment: `heartbeat_fresh` fails because only `main_orchestrator.py` writes `heartbeat.txt` (not `main.py`); `validation_reports` gates live deployment, not advisory operation; `no_unexpected_risk_blocks` requires order submissions which never occur in advisory mode. **Stage 3 expanded from 7 → 8 entries** by adding `alpaca_key_rotation_recent` (Alpaca keys have no blast-radius risk while the broker surface is quarantined). `state_snapshot_fresh` is deliberately NOT in `_ADVISORY_AUTO_SKIP` — it is the advisory liveness indicator (both entry points write `state_snapshot.json`).
  - `run_checks()` reads `settings.ADVISORY_ONLY` once; when True, each check in `_ADVISORY_AUTO_SKIP` is recorded as PASS with reason `"(skipped: ADVISORY_ONLY=True — broker check not applicable)"`. The `--skip` flag still takes precedence (operator-explicit skip wins over auto-skip).
  - `ALL_CHECKS` order (16 total): `check_fred_key_configured` → `check_key_rotation_recent` → `check_alpaca_key_rotation_recent` → `check_advisory_only_active` → broker checks → `check_env_not_committed` → `check_kill_switch_inactive` → **`check_state_snapshot_fresh`** → `check_heartbeat_fresh` → `check_db_exists` → `check_paper_trading_duration` → `check_validation_reports` → `check_no_unexpected_risk_blocks`.

### New env vars / settings (Tier 5.1 + Stage 2)
- **`ADVISORY_ONLY: bool = True`** — the project default; set `ADVISORY_ONLY=false` in `.env` to re-enable broker execution.
- **`FRED_KEY_ROTATED_DATE`** — ISO date (YYYY-MM-DD) in `.env.example` (added Stage 2); `check_key_rotation_recent` uses it to warn when > 90 days since last rotation.

### Test surface
- **`tests/test_advisory_only.py`** (9 tests): orchestrator early-return + INFO log under `ADVISORY_ONLY=True`; no early-return log under `False`; AST/source guards that `gui/panels.py` and `gui/app.py` reference the flag and the banner strings; preflight auto-skip under True; preflight broker checks run under False; `advisory_only_active` row appears in results; warning flag when ADVISORY_ONLY is disabled; default `settings.ADVISORY_ONLY is True`.
- **`tests/test_preflight.py`** — extended in Stage 2 with `TestStateSnapshotFresh` (fresh/stale/missing/mtime-fallback/not-in-auto-skip) and `TestAdvisoryAutoSkip` (all 8 entries present, state_snapshot_fresh excluded, run_checks applies auto-skip; Stage 3 updated count from 7 → 8 for `alpaca_key_rotation_recent`). Stage 3 also extended `TestKeyRotationChecks` with `test_alpaca_rotation_fresh_passes` and `test_alpaca_rotation_invalid_iso_warns` to give symmetric coverage for FRED and Alpaca rotation checks. Total: 55 tests.

### Gravity step 54
- **`step_54_advisory_only_audit`** — 9 checks (updated in Stages 2+3): default ADVISORY_ONLY=True; `main_orchestrator.py` source references ADVISORY_ONLY + the quarantine log message; `gui/panels.py` source has the ADVISORY_ONLY guard + "Advisory mode — broker execution disabled" banner string; `gui/app.py` source renders the "ADVISORY MODE" banner; `preflight_check.check_advisory_only_active` exists; **`_ADVISORY_AUTO_SKIP` is a `dict[str, str]` with all 8 advisory-mode auto-skip entries** (5 broker-dependent including `alpaca_key_rotation_recent`, plus 3 advisory false-positives; check 6 uses superset test not exact equality so future additions don't break it); functional skip path under `ADVISORY_ONLY=True`; `check_advisory_only_active` warns under `ADVISORY_ONLY=False`; `tests/test_advisory_only.py` exists.

### Gravity step 66 (Stage 2)
- **`step_66_advisory_false_positive_audit`** — 10 checks: `check_state_snapshot_fresh` exists + in `ALL_CHECKS`; `_ADVISORY_AUTO_SKIP` contains all 8 expected entries (including `alpaca_key_rotation_recent` from Stage 3); `state_snapshot_fresh` NOT in `_ADVISORY_AUTO_SKIP`; fresh snapshot → PASS + missing snapshot → FAIL (fail-closed); stale snapshot (>2h) fails via timestamp field; `heartbeat_fresh` auto-skipped; `validation_reports` + `no_unexpected_risk_blocks` auto-skipped; `ALL_CHECKS` count == 16; `tests/test_preflight.py` contains both new test classes.

### Gravity step 67 (Stage 3)
- **`step_67_key_rotation_audit`** — 10 checks: `check_alpaca_key_rotation_recent` exists + callable; `settings.ALPACA_KEY_ROTATED_DATE` field exists; unset date → warning PASS; fresh date → clean PASS; stale date → warning PASS (never `passed=False`); invalid ISO → warning PASS; `alpaca_key_rotation_recent` in `_ADVISORY_AUTO_SKIP`; auto-skip fires under `ADVISORY_ONLY=True`; both rotation checks in `ALL_CHECKS` in order (FRED first, Alpaca second); `tests/test_preflight.py` contains `TestKeyRotationChecks`.

### Operator notes
- The kill-switch sentinel (`output/KILL_SWITCH`) and `MACRO_REGIME_GATE_ENABLED` flag are NOT changed by this tier. They remain in place and continue to gate `OrderManager` behaviour when ADVISORY_ONLY is lifted in the future.
- Re-enabling broker execution is a deliberate two-step operation: (1) flip `ADVISORY_ONLY=false` in `.env`; (2) launch the orchestrator; (3) verify the preflight check now runs the broker-readiness gate. The GUI Strategy Matrix mode toggle reappears automatically once ADVISORY_ONLY is False.

## Tier 5.2 — RUNBOOK.md Advisory-Platform Rewrite (2026-06)

Pure docs change. No new code, no new module, no schema change.

### What changed
- `docs/RUNBOOK.md` fully rewritten for advisory-mode operation (see the `docs/RUNBOOK.md` entry in the Architecture section above for the full table of contents).
- `docs/HOW_TO_GUIDE.md` updated: §11 advisory caveat, §13 preflight table updated to show auto-skip under `ADVISORY_ONLY=true`, §15 kill-switch repurposed as pause-recommendations gate, new **Advisory-Only Mode** section added before the Strategy Matrix tab section.

### Incident playbooks added (§3.1–3.3 of RUNBOOK.md)
1. **Stale account snapshot** — `python3 main.py --refresh-account`, root-causes table, held-symbol safety rule.
2. **Missing recommendation for held symbol** — Dead-Letter Queue workflow, stage/cause/fix table, EQUITY_ONLY escalation.
3. **Calibration score dropping below threshold** — `evaluation_engine.calibration_curve()` diagnostic, MAE severity table (< 0.10 monitor / 0.10–0.15 harness re-run / > 0.15 disable module), minimum 30-trade data requirement.

### Advisory pause procedure (§6 of RUNBOOK.md)
The kill-switch sentinel (`output/KILL_SWITCH`) repurposes in advisory mode as a pause-recommendations gate. `main.run_once()` already checks `GlobalKillSwitch.is_active()` and logs "advisory paused by kill-switch sentinel" when the file exists — this is enforced by **Tier 5.1** code, not docs. The runbook documents the operator flow:

```bash
python -m execution.kill_switch --activate --reason "advisory pause — investigating anomaly"
# Expected next run: INFO — advisory paused by kill-switch sentinel; skipping evaluation cycle
python -m execution.kill_switch --deactivate
```

### Gravity step
No Gravity step needed — this is a docs-only change. No new functions, no new schema, no audit criteria were added.

### No new env vars / dependencies
This task introduced no new environment variables and no new Python dependencies.

## Tier 5.3 — Kill Switch as Pause Recommendations Gate (2026-06)

### File-Based Sentinel Protocol
- **`main.run_once()`** — after Stage B (universe build), before Stage C (macro compute): checks `GlobalKillSwitch().is_active()`. When active, logs `"Advisory paused by kill-switch sentinel — skipping evaluation cycle"` (with reason + universe preview) and returns an early `RunResult` with empty `recommendations` and one error entry at `stage="kill_switch_gate"`. The account snapshot is still populated so the observability dashboard continues displaying holdings.
- **`main_orchestrator._main_body()`** — after data fetch, before `run_pipeline()`: same sentinel check. When active, logs the canonical pause message and `return`s immediately. The last written `state_snapshot.json` is untouched so the GUI shows the last known state.
- Both checks import `GlobalKillSwitch` at call time (inside the function, not at module top) so tests that monkeypatch the class resolve correctly.

### Macro-Triggered Advisory Gating (`engine/advisory.py`)
New **Step 8b** block between the StrategyEngine call and the holding-aware overlay:

| Condition | Effect |
|---|---|
| `market_regime in ("RECESSION", "CREDIT EVENT")` | Hard gate: raw STRONG BUY / BUY → HOLD; `raw_signal` and `adjusted_score` both overridden |
| `vix_value > 30.0` OR `sahm_rule_indicator ≥ 0.5` | Soft gate: `adjusted_score = max(0, score - 25)` |
| Finance/Financial Services/Real Estate sector AND (`yield_curve_10y_2y < 0` OR `high_yield_oas > 6`) | Sector veto: BUY → HOLD for structurally-exposed sectors |

`macro_gate_reason` string is assembled and:
- Passed to `_build_rationale()` (new kwarg, default `""`).
- Prepended as "Driver 0" in the rationale when non-empty so it is the first thing the operator reads.
- The holding-aware overlay Case B threshold now uses `adjusted_score` (post-penalty) instead of the raw `score`.

All six CONFIG keys added (see table in architecture section above). No magic numbers in decision logic — every threshold lives in `CONFIG`.

### New CONFIG entries (`engine/advisory.py`)
| Key | Default | Description |
|---|---|---|
| `macro_vix_gate_threshold` | `30.0` | VIX above this → soft gate fires |
| `macro_sahm_gate_threshold` | `0.5` | Sahm Rule at/above this → soft gate fires |
| `macro_score_penalty` | `25` | Points subtracted under soft gate |
| `macro_veto_sectors` | `["Financials","Financial Services","Real Estate"]` | Sectors blocked from fresh buys under adverse conditions |
| `macro_veto_yield_curve_threshold` | `0.0` | Yield curve below this → sector veto applies |
| `macro_veto_oas_threshold` | `6.0` | HY OAS above this → sector veto applies |

### Test surface
- **`tests/test_advisory_pause_gate.py`** (new, 3 test classes, ≈22 tests):
  - `TestKillSwitchPauseGate` — `run_once()` with active/inactive sentinel; pause reason in `errors`; inactive → pipeline runs
  - `TestOrchestratorKillSwitchGate` — `_main_body` skips `run_pipeline`; source-grep check
  - `TestMacroTriggeredGating` — RECESSION, CREDIT EVENT, RISK ON, NEUTRAL, VIX > 30, Sahm ≥ 0.5, sector veto Finance, sector veto Real Estate, non-vetoed Tech, macro_gate_reason in rationale, no gate noise in clean runs
  - `TestMacroGateConfig` — all six CONFIG keys present, correct types, canonical defaults, veto sector membership

### Gravity step 55
`run_advisory_pause_gate_audit()` — 10 checks: CONFIG keys, threshold defaults (30.0 / 0.5 / 25), veto sectors, Step 8b + `macro_gate_reason` in source, `main.py` pause strings, `main_orchestrator.py` pause string, test file exists, `_build_rationale` signature, functional RECESSION→HOLD via minimal mock.

### Operational flow (unchanged CLI)
```bash
python -m execution.kill_switch --activate --reason "advisory pause — investigating anomaly"
# Expected next run: INFO — advisory paused by kill-switch sentinel; skipping evaluation cycle
python -m execution.kill_switch --deactivate
```

### No new env vars / dependencies
This task introduced no new environment variables and no new Python dependencies.

## Tier 1.4 — Symbol Watch with Threshold Alerts (2026-06)

### Overview
Fills the critical visibility gap between manual system runs: `watch_engine.py` evaluates `watch_rules.yaml` rule definitions against advisory pipeline output at the end of every `run_once()` cycle, then dispatches ntfy push notifications for matched rules. Operators who monitor the platform via their phone now receive proactive, intraday alerts without polling the dashboard.

### New module: `watch_engine.py`
Headlessly testable (no Streamlit imports). Public API:
- `WatchRule` — frozen dataclass: `symbol`, `alert_on`, `threshold`, `priority`, `label`.
- `WatchAlert` — frozen dataclass: `symbol`, `rule_type`, `priority`, `title`, `message`, `trigger_detail`.
- `SymbolWatchState` — mutable dataclass: `action`, `conviction`, `alerted_conviction_above`, `alerted_conviction_below`, `timestamp`. Serialises via `.to_dict()` / `.from_dict()`.
- `load_watch_rules(path) -> list[WatchRule]` — parses YAML; returns `[]` on missing/malformed file, never raises. Validates symbol, alert_on, threshold, priority; skips invalid rules with WARNING.
- `load_watch_state(path) -> dict[str, SymbolWatchState]` — reads `output/watch_state.json`; returns `{}` on missing/corrupt, never raises (CONSTRAINT #6).
- `save_watch_state(state, path) -> None` — atomic write-then-rename; swallows failures (CONSTRAINT #6).
- `evaluate_watch_rules(rules, recommendations, prev_state) -> (list[WatchAlert], dict[str, SymbolWatchState])` — pure comparison logic; never fetches market data (no-lookahead invariant).
- `dispatch_watch_alerts(alerts, *, dashboard_url=None) -> None` — calls `alerting.notify()` per alert; per-alert try/except; silent when `NTFY_TOPIC` is unset.

### Alert types
| `alert_on` | Semantics |
|---|---|
| `action_change` | Fires once per action flip (HOLD→BUY, BUY→SELL, etc.). Never fires on first run (no prior action). |
| `conviction_above` | Edge-triggered: fires on the first run where `conviction ≥ threshold`. Silent while condition persists. Resets when conviction drops back below threshold. |
| `conviction_below` | Mirror edge-trigger: fires on the first run where `conviction < threshold`. |

### No-lookahead invariant (Gravity Step 56)
`evaluate_watch_rules` compares `prev_state` (data from the END of the previous run) against `recommendations` (advisory output from the just-completed run). No market-data fetching, forecasting, or model inference occurs inside this function. Verified by Gravity step 56 via monkeypatching `data.market_data.get_provider`.

### State file
`output/watch_state.json` — per-symbol JSON record written atomically. Tracks `action`, `conviction`, `alerted_conviction_above` (dict of threshold → bool), `alerted_conviction_below`, and `timestamp`. Missing file = first run (empty state). Symbols that leave the universe are dropped from state on the next run so stale state cannot produce phantom alerts on re-entry.

### watch_rules.yaml
Default config file at the project root. Two active rules out of the box:
1. Universe-wide conviction siren (`"*"`, `conviction_above`, threshold 0.85, high priority).
2. Universe-wide action-change tracker (`"*"`, `action_change`, default priority).

### Integration in `main.py`
Added inside `run_once()`, immediately after the advisory evaluation loop completes (before Sheet/HTML sinks), wrapped in an outer try/except (CONSTRAINT #6). Always saves state even on quiet runs.

### New settings (`settings.py`)
- `WATCH_RULES_FILE: str = "watch_rules.yaml"` — path to the YAML rule file.

### New env vars
- `WATCH_RULES_FILE` — override the rule-file path (default `"watch_rules.yaml"`).
- `NTFY_DASHBOARD_URL` — optional deep-link URL appended to every watch notification body (e.g. `http://localhost:8501`). Read directly from `os.environ` in `dispatch_watch_alerts`, consistent with the `NTFY_TOPIC` pattern in `alerting.py`.

### Test surface
- **`tests/test_watch_alerts.py`** (60 tests, 7 classes): `TestWatchRule` (frozen, defaults, all fields); `TestWatchAlert` (frozen, fields); `TestSymbolWatchState` (round-trip, from_dict defaults); `TestLoadWatchRules` (missing/malformed/empty/valid/threshold-missing/unknown-alert_on/out-of-range/invalid-priority/uppercase-normalised/multiple/bad-rule-doesnt-block); `TestLoadSaveWatchState` (missing/corrupt/non-object/round-trip/atomic/uppercase-on-load/parent-dir-creation); `TestEvaluateWatchRules` (no-rules/no-recs/action_change-flip/same-action/first-run/conviction-rising-edge/no-spam/reset+refire/first-run-above/first-run-below/conviction-below-falling/no-spam/reset+refire/wildcard-all/wildcard-skip-absent/specific-symbol/bad-rule-resilience/PARTIAL-quality/no-lookahead-structural/multi-rule-independent); `TestDispatchWatchAlerts` (empty-noop/one-per-alert/title/dashboard-url/failure-doesnt-raise/priority); `TestMainPyIntegration` (source guards + settings field + yaml exists).

### Gravity step 56 (`run_watch_alerts_audit`)
14 checks: module importable; frozen dataclasses with required fields; SymbolWatchState round-trip; `load_watch_rules` → `[]` for missing/malformed; valid conviction_above rule parsed; `load_watch_state` → `{}` for missing; action_change fires on HOLD→BUY; conviction_above edge-trigger (no spam); no-lookahead structural verify (market-data monkeypatched); `settings.WATCH_RULES_FILE`; `main.py` source guards; `watch_rules.yaml` exists; `tests/test_watch_alerts.py` exists.

## Tier 1.5 — Plain-English "Why" for Every Recommendation (Expanded) (2026-06)

### Overview
Extends `engine/advisory._build_rationale()` with four institutional-grade narrative sections, gated behind a new `RATIONALE_VERBOSITY` env var. Standard mode (`"standard"`, the default) is a single terse paragraph — unchanged from pre-1.5 behavior. Verbose mode (`"verbose"`) appends four labelled sections immediately after the standard paragraph, separated by a blank line.

### `RATIONALE_VERBOSITY` setting (`settings.py`)
```
RATIONALE_VERBOSITY: str = Field(default="standard", ...)
```
Valid values: `"standard"` (default) | `"verbose"`. Any other value is treated as standard.

### `engine/advisory.py` changes
**`_build_rationale()` extended signature** — all new params are keyword-only with safe defaults so existing call sites are unaffected:
- `hmm_risk_on_probability: Optional[float] = None` — from `macro_dto.hmm_risk_on_probability`
- `vix_value: float = 18.0`, `sahm_rule_indicator: float = 0.0`, `yield_curve: float = 0.50` — macro snapshot for section [A]
- `win_rate_data: Optional[tuple] = None` — `(p, b, n_trades)` pre-computed in `evaluate()` from `TransactionsStore.closed_trades_df()`
- `active_module_docs: Optional[Dict[str, str]] = None` — `{module_name: first_doc_line}` pre-fetched from `signals.registry.global_registry`
- `strategy_explainer_notes: str = ""` — from `strategy_out.get("Strategy Explainer Notes", "")`
- `rsi_2: Optional[float] = None`, `sma_200: Optional[float] = None`, `sector: str = ""` — for section [C] invalidation conditions

**Two new CONFIG entries** (prevent literal magic numbers in logic):
- `"rsi_mean_reversion_exit_level": 35` — RSI(14) flip point for oversold mean-reversion entry
- `"rsi_2_mean_reversion_exit_level": 35` — RSI(2) flip point for ultra-short mean-reversion entry

**`evaluate()` Step 10b** — verbose pre-computation block (inside `if settings.RATIONALE_VERBOSITY == "verbose":`):
1. Calls `estimate_win_rate_and_payoff(closed_trades_df, lookback_trades=100)` on the already-bound `transactions_store`. Sets `_verbose_win_rate = (p, b, n)` when not NaN, else `None`.
2. Lazy-imports `signals.registry.global_registry`, filters by `module.is_active_in_regime(macro_dto)`, extracts first non-boilerplate line of each module's `type(mod).__doc__` into `_verbose_module_docs`. Both steps wrapped in bare `except Exception: pass` per CONSTRAINT #6.

**Four verbose sections (emitted only when `settings.RATIONALE_VERBOSITY == "verbose"`):**

| Label | Content |
|---|---|
| `[A] Regime context` | `{macro_regime} — HMM {confirms/uncertain/risk-off pressure} (p=X.XX). VIX=X, Sahm Rule=X, 10y-2y spread=±X.` |
| `[B] Calibration` | `{p*100:.0f}% win rate over N closed trades (payoff X:1; Kelly edge X — positive/negative).` OR fallback when win_rate_data is None. |
| `[C] Invalidation` | Score flip point, RSI/RSI(2) mean-reversion voids (conditional), VIX/Sahm macro gate tripwires (always), sector-veto conditions (when applicable), SMA-200 trend break (when provided). |
| `[D] Indicator notes` | First-line `__doc__` of ≤4 regime-active signal modules from `global_registry`, title-cased. Omitted entirely when `active_module_docs` is empty. |

**No-lookahead invariant**: `_build_rationale()` contains no I/O. All data (`win_rate_data`, `active_module_docs`, `strategy_explainer_notes`) is gathered by `evaluate()` and passed as arguments.

### New env var
- `RATIONALE_VERBOSITY` — `"standard"` (default) | `"verbose"`. Set in `.env`.

### Test surface
- **`tests/test_rationale_verbosity.py`** (49 tests, 7 classes): `TestSettingsField` (field exists, default "standard"); `TestStandardMode` (no verbose markers, single paragraph, backward-compat for score/regime/macro-gate/dividend text); `TestVerboseModePresence` (standard para still present, [A]/[B]/[C] markers, double-newline separator); `TestRegimeContextSection` (high/mid/low/None HMM → correct prose, VIX and Sahm appear); `TestCalibrationSection` (win% / trade count / payoff ratio / positive/negative edge label / None fallback); `TestInvalidationSection` (BUY score flip, SELL recovery, VIX/Sahm always present, RSI oversold void conditional, RSI-2 void conditional, sector veto for Financials not Technology, SMA-200 void); `TestIndicatorTheorySection` ([D] present with docs, absent with empty/None, title-casing, doc text appears, cap at 4 modules); `TestGracefulDegradation` (all-None does not raise, extreme values don't crash, unknown verbosity string falls back to standard); `TestEndToEndIntegration` (5 end-to-end tests through evaluate() with patched engines: standard no markers, verbose [A/B/C] present, HMM probability in section A, action/conviction unchanged across modes).

### Gravity step 57 (`run_rationale_verbosity_audit`)
10 checks: `settings.RATIONALE_VERBOSITY` exists and defaults to `"standard"`; CONFIG contains both RSI invalidation-level keys; `_build_rationale` signature includes all four verbose-mode kwargs; standard mode produces no [A/B/C/D] markers; verbose mode produces [A/B/C]; HMM ≥ 0.70 → "strongly confirms"; HMM < 0.30 → "risk-off"; `win_rate_data=None` → calibration fallback; sector veto for Financials but not Technology; `tests/test_rationale_verbosity.py` exists.

## Tier 2.1 — Regime-Conditional Signal Weights (2026-06)

### Overview
`SIGNAL_WEIGHTS` is now regime-keyed: per-macro-regime override dicts are merged onto the flat default weights each aggregation cycle. Mean-reversion signals (RSI(2)) can be suppressed in RECESSION/CREDIT EVENT and momentum signals boosted in RISK ON — without any behavioral change when no overrides are configured (fully backward-compatible).

### `resolve_regime_weights()` (`signals/aggregator.py`, module-level)
```python
def resolve_regime_weights(
    market_regime: str,
    regime_weights: Dict[str, Dict[str, float]],
    default_weights: Dict[str, float],
) -> Dict[str, float]:
```
- When `regime_weights` is empty (default): returns `default_weights` unchanged (same object, zero-overhead no-op).
- Exact `market_regime` match → `{**default_weights, **override}` (merge; unlisted keys inherit default).
- Falls back to `regime_weights["_default"]` when no exact match.
- Unknown regime with no `_default` → returns `default_weights` unchanged.
- `SignalAggregator.aggregate()` calls this once per cycle; effective weights replace `self.weights.get(name)` in the module loop.

### `settings.REGIME_SIGNAL_WEIGHTS: dict[str, dict[str, float]]`
Default `{}` (empty — flat weights, identical to pre-Tier-2.1). Configure in `.env` as JSON:
```
REGIME_SIGNAL_WEIGHTS={"RECESSION": {"rsi2_mean_reversion": 0.0, "macro_regime": 60.0}, "RISK ON": {"timeseries_momentum": 40.0}}
```
The `_default` key acts as a catch-all for unmapped regimes.

### Tests
- **`tests/test_regime_weights.py`** (33 tests): empty override returns same object, exact match applies overrides + inherits defaults, `_default` fallback, unknown-regime-no-default returns defaults, merge does not mutate inputs, new keys can be added, RECESSION suppresses rsi2, RISK ON boosts momentum.

### Gravity step 58 (`run_regime_weights_audit`)
8 checks: `resolve_regime_weights` importable; empty dict returns defaults unchanged; RECESSION override applies + inherits uninvolved keys; `_default` fires for unmapped regime; no-match-no-default returns defaults; `settings.REGIME_SIGNAL_WEIGHTS` defaults to `{}`; `SignalAggregator.aggregate` docstring references regime weights; `tests/test_regime_weights.py` exists.

## Tier 2.2 — Forecast Ensemble Weighted by Recent Skill (2026-06)

### Overview
Replaces the static hardcoded blend ratios (`lstm*0.4 + arima*0.2 + mc*0.4`) in `ForecastingEngine.generate_forecast()` with inverse-RMSE weighting from a SQLite-backed skill tracker. Forecast prices are recorded each run and compared to actual prices once the horizon elapses; the model with the lowest recent RMSE gets the highest ensemble weight. Cold-start (< 30 completed observations per model) falls back to equal weights, and the entire tracker is optional — passing no `tracker` to `ForecastingEngine.__init__` reproduces the original static blending exactly.

### New package: `forecasting/`
- **`forecasting/__init__.py`** — package marker; re-exports `ForecastTracker`.
- **`forecasting/forecast_tracker.py`** — `ForecastTracker` class. SQLite table `forecast_errors` (columns: `id`, `symbol`, `model_name`, `horizon_days`, `forecast_ts`, `forecast_price`, `actual_price`, `squared_error`, `recorded_at`). Public API:
  - `record_forecasts(symbol, horizon_days, model_prices: dict[str, float], forecast_ts)` — inserts per-model prices (skips 0/negative).
  - `update_actuals(symbol, horizon_days, actual_price, as_of, tolerance_days=5) -> int` — matches past forecasts with realized prices. The 5-day tolerance absorbs weekends/holidays.
  - `get_skill_weights(symbol, horizon_days, window_days=60, min_obs=30) -> dict[str, float]` — returns normalized inverse-RMSE weights. Empty dict when no history. Cold-start equal weights when any model has < `min_obs` completed rows.
  - `pending_count(symbol, horizon_days) -> int` / `completed_count(symbol, horizon_days, window_days=60) -> int` — monitoring helpers.
  - All methods wrapped in try/except; DB failure → returns `{}` / `0` / `None`, never raises (CONSTRAINT #6).
  - `_MIN_RMSE = 0.01` — floor to prevent infinite weight on a perfect model.
  - WAL journal mode for concurrent read-write safety.

### `ForecastingEngine` changes (`forecasting_engine.py`)
- `__init__(self, tracker=None)` — accepts optional `ForecastTracker`; stores as `self._tracker`. Non-`ForecastTracker` values silently ignored (sets `_tracker=None`).
- `_blend_with_skill(model_forecasts, skill_weights, preferred_model, current_price) -> float` — static method. When `skill_weights` is non-empty, computes weighted average over the intersection of `model_forecasts` and `skill_weights` keys (renormalized). Falls back to original static sector-preference blending when `skill_weights={}`.
- `generate_forecast()` — tracker lifecycle integrated into the `for h in horizons:` loop:
  1. Before the loop: `update_actuals()` for all horizons (fills in past errors).
  2. Per horizon: `get_skill_weights()` → `_blend_with_skill()` → `record_forecasts()`.

### New settings
- **`FORECAST_SKILL_WINDOW_DAYS: int = 60`** — rolling window for RMSE computation.
- **`FORECAST_SKILL_MIN_OBS: int = 30`** — minimum completed rows per model before skill weighting activates (cold-start below this).

### Tests
- **`tests/test_forecast_tracker.py`** (56 tests): table/index creation, DDL column coverage, `record_forecasts` (positive/zero/negative/uppercase), `update_actuals` (past-due, recent, tolerance boundary, idempotency, squared_error value), `get_skill_weights` (empty history, cold-start, warm-path ordering, weights sum to 1, `_MIN_RMSE` guard, old-row window exclusion, DB error), `pending_count`/`completed_count`, `ForecastingEngine` init/tracker kwarg, `_blend_with_skill` (skill path, normalization, static fallback, empty forecasts, single-model restriction, HW preferred).

### Gravity step 59 (`run_forecast_skill_audit`)
10 checks: `ForecastTracker` importable; DDL contains all required columns; cold-start returns equal weights; warm-path inverse-RMSE makes better model higher-weight; `_MIN_RMSE > 0`; `ForecastingEngine.__init__` accepts `tracker` kwarg; `_blend_with_skill` is callable; `settings.FORECAST_SKILL_WINDOW_DAYS` and `FORECAST_SKILL_MIN_OBS` exist and are > 0; `forecasting/__init__.py` re-exports `ForecastTracker`; `tests/test_forecast_tracker.py` exists.

## Tier 2.4 — Sentiment / News Catalyst Signal (2026-06)

### Overview
`signals/news_catalyst.py` adds a `NewsCatalystSignal` that scores headlines via FinBERT (optional) or a built-in keyword lexicon, then gates the signal by earnings proximity. Runs as a standard pluggable `SignalModule` with weight 10.0 and uses the two-phase `pre_compute` / `compute` hook pattern.

### `signals/news_catalyst.py` (new)
- **`NewsCatalystSignal`** — `SignalModule` subclass. `name = "news_catalyst"`. Auto-registers via `global_registry.register(NewsCatalystSignal())` at module end; triggered by `import signals.news_catalyst` added to `signals/__init__.py`.
- **`pre_compute(universe_df, context)`** — batch-fetches Finnhub `company_news` + `earnings_calendar` for every symbol (short-circuits when `FINNHUB_API_KEY` is absent). Populates `self._news_scores`, `self._earnings_dt` (instance cache) AND `context.news_sentiment_scores`, `context.earnings_dates` (new `SignalContext` fields). Courtesy `time.sleep(0.12)` per symbol ≈ 8 calls/s, under the 60/min free-tier ceiling.
- **`compute(row, context)`** — reads `self._news_scores`; returns `SignalOutput(score=0.0)` when no data (dead-letter resilient, CONSTRAINT #6).
- **FinBERT path**: lazy process-level singleton `_FINBERT_PIPELINE` (loaded once via `transformers.pipeline("sentiment-analysis", model="ProsusAI/finbert")`; `_FINBERT_LOAD_ATTEMPTED` flag prevents repeated failures). Maps `positive/negative/neutral` labels to `+confidence/-confidence/0`. Disabled via `settings.FINBERT_ENABLED=False`.
- **Lexicon fallback**: `_POSITIVE_WORDS` and `_NEGATIVE_WORDS` frozensets (~80 words). `_lexicon_sentiment(headline)` = `(pos − neg) / max(1, pos + neg)` ∈ [-1, 1]; tokenises by `.lower().split()` and strips `".,!?;:\"'()[]"`.
- **`_earnings_proximity_multiplier(next_earnings, now, suppress_hours, dampen_days)`**: 0.0 within `suppress_hours` (default 48 h — zero weight near earnings), 0.5 within `dampen_days` (default 7 d — half weight approaching earnings), 0.5 for 24 h post-earnings (fresh noise), 1.0 beyond.

### `signals/base.py` changes
Two new `SignalContext` fields:
- `news_sentiment_scores: Dict[str, float] = field(default_factory=dict)`
- `earnings_dates: Dict[str, str] = field(default_factory=dict)`

### `config.py` changes
Three new `COLUMN_SCHEMA` entries (advisory signals section):
```python
{"header": "News Sentiment", "key": "News_Sentiment",       "format": "number"},
{"header": "Earnings Date",  "key": "Earnings_Date",        "format": "string"},
{"header": "Cluster",        "key": "Correlation_Cluster",  "format": "number"},
```

### `main_orchestrator.py` writeback
After the multifactor writeback block, reads `shared_context.news_sentiment_scores` / `context.earnings_dates` and writes `dashboard_df['News_Sentiment']` / `dashboard_df['Earnings_Date']` via `.map()`. Always initialises `dashboard_df['Correlation_Cluster'] = float('nan')` (on-demand GUI only).

### New settings / env vars
- **`NEWS_LOOKBACK_DAYS: int = 7`** — Finnhub `company_news` fetch window.
- **`FINBERT_ENABLED: bool = True`** — toggles neural vs. lexicon path.
- **`NEWS_EARNINGS_SUPPRESS_HOURS: float = 48.0`** — zero-weight window near earnings.
- **`NEWS_EARNINGS_DAMPEN_DAYS: float = 7.0`** — half-weight window before earnings.
- Env vars `FINNHUB_API_KEY` and `NTFY_DASHBOARD_URL` already existed; no new secrets.
- Optional dep: `transformers>=4.35.0` in `requirements.txt` (PyTorch/TF backend required); `ImportError` → lexicon fallback automatically, never a crash.

### Tests
- **`tests/test_news_catalyst.py`** (46 tests, 8 classes): `TestLexiconSentiment`, `TestEarningsProximity`, `TestScoreHeadline`, `TestSignalCompute`, `TestPreCompute`, `TestRegistration`, `TestFetchHelpers`, `TestSettings`. All Finnhub/transformers calls monkeypatched.

### Gravity step 60 (`run_news_catalyst_audit`)
10 checks: importable; `name == "news_catalyst"`; FinBERT helper returns `None` safely when unavailable; lexicon positive headline > 0 and negative < 0; suppress within 48 h → 0.0; dampen within 7 d → 0.5; `pre_compute` populates `context.news_sentiment_scores` and `context.earnings_dates`; no `FINNHUB_API_KEY` → no crash; `"news_catalyst"` in `settings.SIGNAL_WEIGHTS`; `tests/test_news_catalyst.py` exists.

## Tier 2.5 — Correlation Cluster Awareness (2026-06)

### Overview
`research_engine.compute_correlation_clusters()` uses hierarchical Ward-linkage clustering on the Lopez de Prado distance metric `d = sqrt(0.5 * (1 − ρ))` to label every symbol with a cluster ID. Computed **on-demand** in the GUI Reports tab (not in the main pipeline) because clustering requires simultaneous returns for all symbols at once, which is incompatible with the orchestrator's per-symbol loop.

### `research_engine.py` additions (module-level)
- **`compute_correlation_clusters(returns_df, distance_threshold=0.4, min_obs=20) -> Tuple[Dict[str, int], pd.DataFrame]`**
  - `returns_df`: columns = symbols, index = dates. Symbols with < `min_obs` valid rows get `cluster_id = 0` (excluded; CONSTRAINT #4 — never fabricates a cluster label).
  - Converts correlation matrix to distance via `d = sqrt(0.5 * (1 − ρ))`, then calls `scipy.cluster.hierarchy.linkage(method='ward')` + `fcluster(criterion='distance', t=distance_threshold)`.
  - Returns `(labels: Dict[str, int], cluster_summary: pd.DataFrame)`. `cluster_summary` columns: `cluster_id`, `symbols` (list), `n_symbols`, `avg_intra_corr` (NaN for singletons — never fabricated).
  - Returns `({}, empty DataFrame with correct schema)` on any fatal error (CONSTRAINT #6).
- **`fetch_returns_for_clustering(symbols, lookback_days=60) -> pd.DataFrame`** — fetches yfinance daily closes (lazy `import yfinance as yf` inside body so tests patch `yfinance.download` directly), returns `pct_change()`. Returns empty DataFrame on error or empty symbol list.

### GUI: Reports tab (`gui/panels.py`)
- **`_render_correlation_cluster_section(signals)`** — inserted in `render_report_viewer()` before `_render_decision_journal_section`. UI: lookback slider (30–250 d), threshold slider (0.05–1.5), "Compute Clusters" on-demand button (CONSTRAINT #5). Results stored in `st.session_state["cluster_labels"]` and `st.session_state["cluster_summary"]`; renders a symbol–cluster assignment table and a per-cluster aggregate position % concentration table with `>30%` warning.

### New settings / env vars
- **`CORRELATION_CLUSTER_LOOKBACK_DAYS: int = 60`**
- **`CORRELATION_CLUSTER_THRESHOLD: float = 0.4`**

### Tests
- **`tests/test_correlation_clusters.py`** (27 tests, 7 classes): `TestComputeCorrelationClusters` (7 tests — known correlated A/B share cluster; uncorrelated C separate; all symbols assigned; IDs positive int; all-correlated single cluster; empty/None → empty), `TestDistanceThreshold` (3), `TestSummaryDataFrame` (6), `TestEdgeCases` (4 — single symbol; all-NaN col; insufficient obs; two symbols), `TestFetchReturnsHelper` (3 — patches `yfinance.download` directly), `TestSettings` (7 — defaults, COLUMN_SCHEMA entries). `compute_correlation_clusters` is a pure function; no mocking required.

### Gravity step 61 (`run_correlation_cluster_audit`)
10 checks: `compute_correlation_clusters` importable; `fetch_returns_for_clustering` importable; known-correlated A/B share cluster; uncorrelated C in different cluster; empty DataFrame → empty labels+summary; `Correlation_Cluster` in `COLUMN_SCHEMA`; `CORRELATION_CLUSTER_LOOKBACK_DAYS == 60`; `CORRELATION_CLUSTER_THRESHOLD == 0.4`; insufficient obs symbol gets `cluster_id = 0`; `tests/test_correlation_clusters.py` exists.

## Task 3 — Operator Ergonomics (2026-06)

### 3.1 Daily Briefing Digest (`scripts/daily_briefing.py`)
- **`generate_briefing(output_dir) -> str`** — assembles a full Markdown briefing with five sections: Macro Regime, Top 3 Actions, Δ Since Last Run, Dead-Lettered Symbols, and 30-Day Calibration. Each section wraps in try/except — CONSTRAINT #6. No live network calls.
- **`write_briefing(output_dir) -> Path`** — writes to `output/briefing_YYYY-MM-DD.md` via atomic create + mkdir. Returns the path; never raises.
- **`main(argv)`** — CLI entry point: `python -m scripts.daily_briefing [--print] [--output-dir DIR]`. `--print` echoes the briefing to stdout after writing.
- **Wire-up in `launch.command`**: appended `python -m scripts.daily_briefing --print || true` as the final step so every launch (single-run and interval-mode) ends with a briefing printed to the Terminal window.
- **Section helpers (all `_section_*`)**: `_section_regime` reads `state_snapshot.json` for regime/VIX/HMM; `_section_top_actions` sorts signals by action priority (BUY > HOLD > SELL) then conviction; `_section_delta` calls `scripts.snapshot_diff.compute_diff_from_history`; `_section_dead_letters` reads `output/dead_letter.json` via `gui.dead_letter.read_dead_letter`; `_section_calibration` calls `evaluation_engine.calibration_curve` + `TransactionsStore` (both imported lazily inside the function — CONSTRAINT #7).
- **Dead-letter tolerant**: every section degrades gracefully to a "No data yet" placeholder. First-ever run (no history, no dead_letter.json) still produces a valid briefing.

### 3.2 Mobile-Friendly Daily Report
- Added a `@media (max-width: 600px)` responsive CSS block to `HTML_REPORT_TEMPLATE` in `diagnostics_and_visuals.py`, just before the closing `</style>` tag.
- **No new dependency** — pure CSS addition to the embedded template.
- Behaviour: single-column `exec-grid`; two-column `summary-band`; `overflow-x: auto` on the signals table so it scrolls horizontally rather than overflowing; `min-height: 44px` on `tr.data-row td` and `th` for WCAG 2.5.5 touch-target compliance; reduced font sizes and padding for narrow viewports.

### 3.3 Secrets-Rotation Reminder
- **`settings.FRED_KEY_ROTATED_DATE: Optional[str]`** (default `None`) — ISO date (YYYY-MM-DD) recording when `FRED_API_KEY` was last rotated. Set after generating a new key at https://fred.stlouisfed.org/docs/api/api_key.html.
- **`scripts/preflight_check.check_key_rotation_recent(max_age_days=90) -> CheckResult`** — **warning-only, never blocking**. Three outcomes: (a) `FRED_KEY_ROTATED_DATE` unset → warning to start tracking; (b) key rotated within `max_age_days` → clean PASS; (c) key older → warning citing age + rotation URL. `ALPACA_KEY_ROTATED_DATE` is intentionally **not** checked (paper keys have no blast radius in advisory mode).
- Wired into `ALL_CHECKS` as check #2, immediately after `check_fred_key_configured` and before `check_advisory_only_active`.
- **New env var**: `FRED_KEY_ROTATED_DATE` (ISO date, optional). Add to `.env.example`.

### 3.4 "Quick Add to Watchlist" GUI
- `render_live_inventory()` in `gui/panels.py` now includes a text input + "➕ Add to watchlist" button between the Robinhood snapshot fetch and the Sync Now buttons.
- **File-only**: writes to `watchlist.txt` (repo root), never to `.env` or via `gui.env_io.write_setting`. This avoids GUI-induced env churn and keeps the operator's watchlist editable in a plain text file.
- **Deduplication**: reads existing non-comment lines from `watchlist.txt` before appending — silently skips if the ticker is already present, showing an `st.info` instead.
- **Validation**: ticker is normalized to uppercase; rejects empty input or symbols that don't match `[A-Z0-9.-]` after normalization.
- **Picked up automatically** by `main.py._load_watchlist()` on the next `run_once()` call (no restart needed).
- **No new settings / env vars** (file path is hardcoded to `watchlist.txt` at the repo root, consistent with `main.py`'s `WATCHLIST_FILE` constant).

### Tests
- **`tests/test_operator_ergonomics.py`** (45 tests, 5 classes):
  - `TestDailyBriefingImport` (4) — module importable, callables exist.
  - `TestBriefingSections` (11) — regime/VIX/kill-switch text, top-actions ordering, dead-letter read, calibration MAE rendering.
  - `TestGenerateBriefing` (5) — never raises, required headers, snapshot wired, file creation, date in filename.
  - `TestMobileResponsiveCSS` (5) — `@media` present, 600px breakpoint, 44px tap targets, 1fr grid collapse, `overflow-x:auto`.
  - `TestKeyRotationCheck` (11) — unset/fresh/stale/invalid/boundary, warning-only invariant, check ordering, never-False, Alpaca keys not touched, Settings field, not in SECRET_KEYS.
  - `TestWatchlistQuickAdd` (7) — append, dedup, comment skip, uppercase, file creation, source guards.

### Gravity step 63 (`step_63_operator_ergonomics_audit`)
10 checks: `scripts.daily_briefing` importable + `generate_briefing` callable; `generate_briefing` returns Markdown with regime section; `write_briefing` produces `briefing_YYYY-MM-DD.md`; `HTML_REPORT_TEMPLATE` has `@media (max-width: 600px)` block; mobile CSS has 44px tap target + `overflow-x:auto`; `check_key_rotation_recent` in `ALL_CHECKS`; `check_key_rotation_recent` is always warning-only (never `passed=False`); `Settings.FRED_KEY_ROTATED_DATE` declared; `render_live_inventory` references `watchlist.txt` quick-add; `tests/test_operator_ergonomics.py` exists.

## Tier 4 — Validation & Honesty (2026-06)

### 4.1 Live-vs-Recommendation Tracking

"If you'd taken every BUY at the published conviction-weighted size, the paper-equivalent return over 30 days would be X%; actual decisions returned Y%." Measures whether operator judgement adds or subtracts alpha relative to the raw model signal.

#### `evaluation_engine.recommendation_tracking_report()` (module-level function)
```python
recommendation_tracking_report(
    log_path: Optional[Path] = None,
    transactions_store=None,
    horizon_days: int = 30,
    *,
    historical_store=None,
    _today=None,          # injectable for tests
) -> Dict[str, Any]
```
- Reads the 1.3 decision log (`output/decision_log.jsonl`) via `gui.decision_log.read_decisions()` (lazy import — no circular import).
- Filters to entries where `signal_action` contains `"BUY"` (catches `"STRONG BUY"` too).
- For each BUY entry computes:
  - **Model price**: `HistoricalStore.get_bars(symbol, lookback_days=756)` → `_price_at_or_before(bars, signal_dt)` for entry price and `_price_at_or_before(bars, exit_dt)` for exit price (where `exit_dt = signal_date + timedelta(days=horizon_days)`).
  - **Model return**: `(exit − entry) / entry` when `completed` (i.e. `exit_date <= today`) and both prices are available; `NaN` otherwise — **CONSTRAINT #4, never fabricated**.
  - **Actual return**: for `action_taken=="acted"` entries with a `trade_id`, reads `TransactionsStore.get_trade_history(symbol)` for `entry_price` and `exit_price`. When `exit_ts` is `None` (still open), uses the latest available bar close as a surrogate (still reported as `n_with_exit++`).
- Aggregation:
  - `model_return_30d` = conviction-weighted mean of all completed BUY signals that have a model return.
  - `operator_return_30d` = simple mean of actual returns for `action_taken=="acted"` with a closed or surrogate exit.
  - `delta` = `operator_return_30d − model_return_30d` (positive = operator adds value). `NaN` when either is unavailable.
- Returns a dict with keys: `rows` (per-signal breakdown), `model_return_30d`, `operator_return_30d`, `delta`, `n_signals`, `n_acted`, `n_completed`, `n_with_exit`, `horizon_days`.
- Module-level constants exported for tests: `_TRACKING_EMPTY` (sentinel), `_DEFAULT_DECISION_LOG_PATH`.
- Helper function `_price_at_or_before(bars: pd.DataFrame, target: datetime) -> float` — slices bars to last Close ≤ target date; `NaN` on empty/no-match.
- All I/O in try/except; dead-letter tolerant (CONSTRAINT #6). HistoricalStore is imported lazily (inside function body) to avoid circular imports.

#### GUI: Reports tab (`gui/panels.py`)
- **`_render_recommendation_tracking_section()`** — inserted in `render_report_viewer()` between `_render_decision_journal_section()` and `_render_brinson_fachler_section()`.
- Horizon slider (5–90 days, default 30, session-key `rec_tracking_horizon`).
- `@st.cache_data(ttl=300)` wraps the `recommendation_tracking_report()` call.
- Four KPI columns: BUY Signals Logged / Model {N}d Return / Operator Return / Delta (Δ).
- Plain-English narrative block summarizing whether the operator added or subtracted alpha.
- Expandable per-signal breakdown table.
- Fully wrapped in try/except (CONSTRAINT #6).

#### Tests
- **`tests/test_recommendation_tracking.py`** (≥ 30 tests, 8 classes):
  - `TestEmptyLog` — missing/empty/corrupt log, horizon preserved in result.
  - `TestNoBuySignals` — HOLD/SELL entries not counted; STRONG BUY counted.
  - `TestModelReturn` — correct model return from synthetic bars; NaN when no bars.
  - `TestActualReturn` — closed trade → correct actual_return; open trade → surrogate exit; missing trade_id → NaN.
  - `TestPassedSignal` — "passed" counted in n_signals but not n_acted; still included in model return.
  - `TestHorizonNotElapsed` — recent signal → n_completed=0; completed flag per-row.
  - `TestConvictionWeighting` — high-conviction winner + low-conviction loser → positive weighted result.
  - `TestDelta` — delta = operator − model; NaN when only model available.
  - `TestDeadLetterResilience` — HistoricalStore/TransactionsStore failures degrade gracefully.
  - `TestModuleSurface` — importable; sentinel structure; Path type; `_price_at_or_before` corner cases.

#### Gravity step 64 (`step_64_recommendation_tracking_audit`)
10 checks: `recommendation_tracking_report` importable; `_TRACKING_EMPTY` has all 9 required keys; `_DEFAULT_DECISION_LOG_PATH` is `pathlib.Path`; `_price_at_or_before(empty, now)` returns NaN (CONSTRAINT #4); missing log → n_signals=0 and all returns NaN; passed BUY → n_signals=1, n_acted=0; recent signal (5 days, horizon=30) → n_completed=0; HistoricalStore failure degrades gracefully (CONSTRAINT #6); `gui/panels.py` references `recommendation_tracking_report`; `tests/test_recommendation_tracking.py` exists.

---

### 4.2 Walk-Forward Validation Cadence

Monthly runner that re-validates every registered strategy against recent history to ensure validation reports never go stale.

#### `scripts/refresh_validations.py` (new module, runnable as `python -m scripts.refresh_validations`)
- **Strategy adapters** — pure functions `adapter_fn(spy_close: pd.Series) -> (X, y, precomputed)`:
  - `_build_rsi2_adapter(spy_close)` — mirrors `tests/test_validation_rsi2.py`; RSI(2) + SMA-200 long-only trend filter + crash/recession RISK-OFF gate; returns `X[RSI_2, SMA_200]`, `y=daily_ret`, `precomputed={RSI2_Gated, RSI2_Ungated}`.
  - `_build_tsmom_adapter(spy_close)` — mirrors `tests/test_validation_ts_momentum.py`; four variants (12M/6M × 10%/20% vol target); returns `X[ROC_12M, ROC_6M, Vol]`, `y=daily_ret`, `precomputed` dict with 4 series.
- **`_make_strategy_fn(precomputed, turnover)`** — closure returning a `StrategyValidationHarness`-compatible `strategy_fn(X_train, y_train, X_test, y_test) -> list[dict]` where each dict has `params`, `train_returns`, `test_returns`, `turnover`.
- **`STRATEGY_REGISTRY: Dict[str, Tuple[Callable, float]]`** — maps `strategy_id → (adapter_fn, turnover)`; currently contains `"rsi2_mean_reversion"` and `"timeseries_momentum"`.
- **`_download_spy(start_date, end_date)`** — downloads via `yfinance` (same library as existing test harnesses); raises `RuntimeError` on empty result.
- **`run_validations(strategies, start_date, end_date, output_dir, n_cpcv_splits, n_test_splits)`** — downloads SPY once, loops over strategies, runs `StrategyValidationHarness`, saves JSON summaries. Per-strategy failure → dead-letter entry with `deployable=False` and `error` key (CONSTRAINT #6). Returns `Dict[strategy_id, summary_dict]`.
- **`_print_summary_table(results)`** — ASCII pass/fail table to stdout.
- **`main(argv)`** — argparse CLI; exit code 0 = all pass, 1 = any failure. Flags: `--strategies`, `--start`, `--end`, `--output-dir`, `--n-cpcv-splits`, `--n-test-splits`.

#### `scripts/refresh_validations.sh` (new, executable)
Bash wrapper that verifies `.venv` exists and Python is 3.12.x, then runs `python3 -m scripts.refresh_validations "$@"`. Designed for cron scheduling:
```
0 6 1 * * cd /path/to/stockpy && ./scripts/refresh_validations.sh >> logs/validations.log 2>&1
```

#### Design constraints (CONSTRAINT #4, #6, #7)
- No fabricated synthetic returns passed to the harness — if an adapter cannot build valid X/y (insufficient history), the strategy is dead-lettered with an error.
- Data fetching uses yfinance — no new data providers.
- Each strategy wrapped in try/except so one failure never aborts the run.

#### Tests
- **`tests/test_refresh_validations.py`** (≥ 40 tests, 7 classes):
  - `TestModuleSurface` — importable, public callables exist.
  - `TestRegistryStructure` — STRATEGY_REGISTRY shape, entries are (callable, positive float), turnover in range.
  - `TestBuildRsi2Adapter` — returns 3-tuple; X has RSI_2/SMA_200; X and y share index; precomputed keys; SMA-200 warmup trimmed; RSI bounded [0, 100].
  - `TestBuildTsmomAdapter` — returns 3-tuple; X has ROC_12M/ROC_6M/Vol; 4 precomputed variants; variant names contain "TSMOM\_".
  - `TestMakeStrategyFn` — returns callable; result is list; required keys present; turnover propagated; one result per precomputed series.
  - `TestRunValidations` — returns dict; unknown strategy dead-lettered; SPY download failure marks all as failed; adapter exception dead-lettered; single-strategy filter.
  - `TestMainCLI` — all-pass → exit 0; any-fail → exit 1; error entry → exit 1; `--strategies` forwarded; `--start`/`--end` forwarded; `--n-cpcv-splits` forwarded.

#### Gravity step 65 (`step_65_refresh_validations_audit`)
10 checks: `scripts.refresh_validations` importable; `STRATEGY_REGISTRY` contains both strategies; entries are (callable, positive turnover); RSI(2) adapter returns (X with RSI_2/SMA_200, y, precomputed); TSMOM adapter returns 4 variants; `_make_strategy_fn` closure returns list with required harness keys; unknown strategy dead-lettered (CONSTRAINT #6); main exit-code 0 on all-pass / 1 on any-fail; `scripts/refresh_validations.sh` exists and is executable; `tests/test_refresh_validations.py` exists.

## Tier 6 — Autonomous Advisory Agent (2026-06)

### Overview
"Robinhood agent trader" option 2: a self-pacing loop that wraps `main.run_once()` with adaptive cadence, actionable-backlog reminders, and persistent state. **ADVISORY ONLY** — no order-submission code, no broker imports. Composes on top of the existing `engine/advisory.evaluate()` per-symbol path, `alerting.notify()` ntfy channel, `watch_engine` per-cycle alerts, and `gui/decision_log.py` operator decision tracking.

### New module: `engine/advisory_agent.py`
Headless, dependency-free policy layer (stdlib + `zoneinfo` only). Public API:
- **`AgentState`** — mutable dataclass: `cycle_count`, `last_cycle_iso`, `last_error_count`, `consecutive_error_cycles`, `backlog: dict[str, BacklogEntry]`, `last_summary_iso`. Round-trips via `.to_dict()` / `.from_dict()`.
- **`BacklogEntry`** — frozen dataclass: `symbol`, `action` (BUY/SELL only — HOLD never enters), `conviction`, `first_seen_iso`, `last_pinged_iso`, `reminders_sent`.
- **`BacklogReminder`** — frozen dataclass: one reminder ready for `alerting.notify()`. Carries `tier` (1/2/3), `age_hours`, `priority`, `title`, `message`.
- **`is_us_market_open(now_utc) -> bool`** — NYSE RTH 09:30–16:00 ET Mon–Fri. Holiday calendar NOT applied (would require `pandas_market_calendars`); operator owns the half-day judgement.
- **`is_extended_hours(now_utc) -> bool`** — 04:00–20:00 ET weekday window (RTH is a strict subset).
- **`compute_next_run_delay(now_utc, *, state, vix, market_regime, config=None) -> int`** — adaptive cadence policy. Decision tree, first match wins:
  1. **Error back-off** — `consecutive_error_cycles > 0` → `min(base * N, max)`.
  2. **Open/close 30-min boost** — inside RTH AND within `rth_open_close_window_minutes` of either boundary → `rth_open_close_delay_s` (default 60 s).
  3. **High-vol RTH** — inside RTH AND (`vix ≥ vol_spike_vix_threshold` OR `market_regime in high_vol_regimes`) → `rth_high_vol_delay_s` (default 120 s).
  4. **Normal RTH** — `rth_normal_delay_s` (default 300 s).
  5. **Extended hours** — `extended_hours_delay_s` (default 1 h).
  6. **Off-hours / weekend** — `off_hours_delay_s` (default 4 h).
  Always clamped ≥ `min_delay_s` (default 60 s) to prevent hot-looping the yfinance API.
- **`update_backlog(state, recommendations, decision_log_entries, now_utc) -> AgentState`** — three-stage update (in place): INSERT high-conviction BUY/SELL recommendations; CLEAR entries whose symbol has a matching "acted" `decision_log` record dated after `first_seen_iso`; EXPIRE entries older than `backlog_expiry_hours` (default 72 h). Conviction threshold = `backlog_conviction_threshold` (default 0.85, mirrors `watch_rules.yaml`'s default siren).
- **`compute_backlog_reminders(state, now_utc) -> list[BacklogReminder]`** — walks each backlog entry against `backlog_tier_hours` ladder (default 1 h / 4 h / 24 h); emits AT MOST one reminder per entry per call (the highest tier crossed since the last dispatch). Capped at `backlog_max_reminders` (default 3) per `(symbol, action)`.
- **`apply_reminder_dispatch(state, reminders, now_utc) -> AgentState`** — advances `last_pinged_iso` + `reminders_sent` for every reminder that was dispatched. Call AFTER `dispatch_backlog_reminders`.
- **`process_run_result(state, run_result, now_utc) -> AgentState`** — bumps `cycle_count`, sets `last_cycle_iso`, advances or resets `consecutive_error_cycles` based on `run_result.errors`. Pure with respect to wall-clock.
- **`dispatch_backlog_reminders(reminders, *, dashboard_url=None) -> None`** — mirrors `watch_engine.dispatch_watch_alerts` contract: per-reminder try/except, no-op when `NTFY_TOPIC` unset, optional dashboard URL appended to message body. Imports `alerting.notify` inline.
- **`load_agent_state(path) / save_agent_state(state, path)`** — atomic write-then-rename (same pattern as `watch_engine.save_watch_state`). Missing / corrupt / empty file → fresh `AgentState()` (CONSTRAINT #6 — never raises). Save failures logged at WARNING and swallowed.

### CONFIG (`engine.advisory_agent.CONFIG`)
Single source of truth for every threshold. No magic numbers in the logic functions. Keys:
| Key | Default | Purpose |
|---|---|---|
| `rth_normal_delay_s` | 300 | Midday RTH refresh cadence |
| `rth_high_vol_delay_s` | 120 | RTH under VIX spike / risk-off regime |
| `rth_open_close_delay_s` | 60 | RTH inside open/close 30-min window |
| `rth_open_close_window_minutes` | 30 | Half-width of the boost windows |
| `extended_hours_delay_s` | 3600 | Premarket / aftermarket weekday |
| `off_hours_delay_s` | 14400 | Overnight / weekend heartbeat |
| `error_backoff_base_s` | 60 | Linear back-off step |
| `error_backoff_max_s` | 900 | Back-off ceiling |
| `vol_spike_vix_threshold` | 25.0 | VIX threshold for high-vol cadence |
| `high_vol_regimes` | `("RISK OFF", "RECESSION", "CREDIT EVENT")` | Regimes that also trigger high-vol cadence |
| `min_delay_s` | 60 | Cadence floor — never ping faster |
| `backlog_conviction_threshold` | 0.85 | Min conviction to enter backlog |
| `backlog_tier_hours` | `(1.0, 4.0, 24.0)` | Reminder escalation ladder |
| `backlog_tier_priorities` | `("default", "high", "high")` | Per-tier ntfy priority |
| `backlog_max_reminders` | 3 | Per-`(symbol, action)` reminder cap |
| `backlog_expiry_hours` | 72.0 | Silent drop after this |
| `decision_log_match_window_hours` | 24.0 | Window for matching `decision_log` "acted" entries |

### `main.py --agent` flag
New CLI flag that replaces `--interval N`'s fixed timer with the agent policy. Takes precedence over `--interval`. Loop (per cycle):
1. Run `run_once()` (same code path as `--interval`; preserves all existing watch_engine + ntfy + sheet + HTML report behaviour — the agent layer adds, never replaces).
2. `process_run_result` → update cycle count and error streak.
3. Read fresh `gui/decision_log.read_decisions()` entries.
4. `update_backlog` — insert new high-conviction signals, drop actioned/expired entries.
5. `compute_backlog_reminders` + `dispatch_backlog_reminders` + `apply_reminder_dispatch`.
6. `save_agent_state` (always — even on a failed cycle).
7. `compute_next_run_delay` with `vix` + `market_regime` sourced from the just-written `output/state_snapshot.json` (via `_read_macro_snapshot_hint` — never re-hits FRED).
8. Sleep in 1 s slices so SIGINT/SIGTERM are caught promptly.

`_run_agent_loop` is a module-level helper in `main.py`; the agent module is imported lazily inside it so test imports of `main.py` stay cheap.

### Persistent state
`output/agent_state.json` — atomic write-then-rename. Schema:
```json
{
  "cycle_count": 42,
  "last_cycle_iso": "2026-06-30T18:30:00+00:00",
  "last_error_count": 0,
  "consecutive_error_cycles": 0,
  "backlog": {
    "AAPL:BUY": {
      "symbol": "AAPL", "action": "BUY", "conviction": 0.91,
      "first_seen_iso": "2026-06-30T14:00:00+00:00",
      "last_pinged_iso": "2026-06-30T15:00:00+00:00",
      "reminders_sent": 1
    }
  },
  "last_summary_iso": ""
}
```
Corrupt or missing file → fresh `AgentState()` on next launch (CONSTRAINT #6).

### Composition rules
- The agent layer **never** mutates `engine.advisory.evaluate()`'s output — it observes recommendations + decision log, derives a backlog, and emits ntfy reminders. The advisory pipeline is unchanged.
- The agent layer **never** imports `execution/*` or any broker module — `ADVISORY_ONLY=True` is the project default and the agent stays inside that quarantine (CONSTRAINT enforced by Gravity step 69 check 13, source-grepping for `submit_order` / `place_order` / etc.).
- The watch_engine and the agent fire INDEPENDENTLY each cycle: watch_engine is edge-triggered (action flips, conviction crossings); the agent is time-based (escalating reminders). They are complementary, not redundant.

### Tests
**`tests/test_advisory_agent.py`** (52 tests, 8 classes): `TestMarketHours` (RTH bounds + weekend + extended-hours window + naive→UTC promotion); `TestCadence` (RTH normal / open boost / close boost / high-vol VIX / high-vol regime / extended / off-hours / weekend / error back-off / cap / floor); `TestBacklog` (insert above threshold / skip below threshold / skip HOLD / preserve first_seen on resurface / acted clears / passed does NOT clear / expired drops / BUY+SELL separate keys); `TestReminders` (no-fire-too-young / tier-1@1h / tier-2@4h / cap behavior / dispatch advances counter); `TestStateIO` (round-trip / missing→fresh / corrupt→fresh / empty→fresh / unwritable-dir tolerated / no stray .tmp); `TestProcessRunResult` (cycle bump / error streak / reset / naive-now); `TestDispatch` (empty no-op / one-per-reminder / failure-doesn't-block / dashboard-url appended); `TestModuleSurface` (dataclass round-trips / corrupt backlog dropped / CONFIG keys complete).

### Gravity step 69
**`step_69_advisory_agent_audit`** — 15 checks: importable; CONFIG has all required keys; dataclass field sets; RTH-normal cadence; high-VIX tightens cadence; weekend → off-hours; error back-off short-circuits; `update_backlog` inserts high-conviction BUY; `update_backlog` clears after "acted"; tier-1 reminder fires after 1 h + counter advances; state save/load round-trip; corrupt JSON → fresh state; ADVISORY-ONLY source check (no `submit_order`/`place_order`/etc. keywords in `engine/advisory_agent.py`); `main.py` registers `--agent` flag + `_run_agent_loop`; `tests/test_advisory_agent.py` exists.

### Operational notes
- `--agent` shares the `output/state_snapshot.json` write path with `--interval` and one-shot mode, so the Streamlit dashboard, daily briefing, and snapshot-diff Δ band all light up identically — no separate observability work needed.
- `NTFY_TOPIC` unset → reminder dispatch is silently inert (operator can run the agent purely as a logger).
- `NTFY_DASHBOARD_URL` (unchanged from Tier 1.4) — when set, every reminder message ends with a deep-link to the GUI for one-click context.
- Stopping the agent: SIGINT (Ctrl-C) or SIGTERM — the loop catches the signal, finishes the current cycle + reminder dispatch + state save, then exits cleanly.
- Backlog tuning: drop `backlog_tier_hours` to `(0.5, 2.0, 8.0)` to be more aggressive intraday; raise `backlog_conviction_threshold` to 0.90 to reduce reminder volume.

## Tier 6.1 — Trade-Signal Abilities (Conviction Momentum + Stop/Target Proximity) (2026-06)

### Overview
Two advisory trading abilities layered on top of the Tier 6 autonomous agent, both derived **purely** from the per-cycle `RunResult` the agent already produces (`recommendations` + Robinhood `AccountSnapshot`). **ADVISORY ONLY** — no order code, no broker import; every output is a `TradeAlert` pushed through the existing `alerting.notify()` ntfy channel (no-op when `NTFY_TOPIC` unset). Pinned by Gravity step 70 check 10 (source-grep for `submit_order`/`place_order`/etc.).

### New module: `engine/trade_signals.py`
Headless, dependency-free (stdlib only). Public API:
- **`TradeAlert`** — frozen dataclass: `symbol`, `kind` (`"momentum_building"`|`"momentum_fading"`|`"approaching_stop"`|`"approaching_target"`), `priority` (`"default"`|`"high"`), `title`, `message`, `detail: dict[str,float]` (numeric context; NaN where unavailable, never fabricated).
- **`update_conviction_history(history, recommendations, *, config) -> dict`** — pure; appends each symbol's current conviction, trims to `momentum_lookback_cycles`, prunes symbols absent from the current universe (history can't grow unbounded), returns a NEW dict (input not mutated).
- **`detect_conviction_momentum(history, recommendations, alerted, *, config) -> (alerts, new_alerted)`** — Ability A. Edge-triggered per symbol via the `alerted` debounce map (`symbol -> "building"|"fading"`).
- **`detect_price_triggers(snapshot, recommendations, alerted, *, config) -> (alerts, new_alerted)`** — Ability B. Edge-triggered via `alerted` (`symbol -> "stop"|"target"`).
- **`dispatch_trade_alerts(alerts, *, dashboard_url=None)`** — mirrors `advisory_agent.dispatch_backlog_reminders` (inline `alerting.notify` import, per-alert try/except, dashboard deep-link append).

### Ability A — Conviction Momentum
The autonomous agent uniquely holds cross-cycle state; the static backlog only fires at the 0.85 siren. This watches each symbol's conviction *trajectory*:
- **"building"** (`default` priority) — conviction climbed ≥ `momentum_rising_delta` (0.10) monotonically over the last `momentum_min_cycles` (3), with the latest value in `[momentum_building_floor=0.60, momentum_building_ceiling=0.85)` and action not SELL. An EARLY entry heads-up *below* the backlog siren (so it never double-alerts with the backlog).
- **"fading"** (`high` priority) — conviction fell ≥ `momentum_falling_delta` (0.15) monotonically on a name whose action is no longer BUY. An EARLY exit warning.
- A sustained trend pings ONCE; the debounce flag clears when the trend breaks (choppy window) so a later move re-alerts; a direction flip (building→fading) re-fires immediately.

### Ability B — Stop / Target Proximity
For HELD positions only (`quantity > 0`, `market_value ≥ min_position_value_usd=100`):
- **Stop** (`high`) — volatility-scaled level `average_cost − stop_atr_multiple*ATR` (ATR from `rec.key_indicators["atr"]`), fallback `average_cost*(1 − stop_fallback_pct=0.08)` when ATR missing. Fires when `price ≤ stop*(1 + stop_proximity_pct=0.02)` — within the band above the stop OR already breached (title says "breached" vs "approaching").
- **Target** (`default`) — the 30-day forecast price (`rec.forecast`) when usable, fallback `average_cost + target_atr_multiple*ATR`. Fires when `price ≥ target*(1 − target_proximity_pct=0.02)` — at/near the target, including price already past the forecast.
- Stop is checked before target. No fabricated levels (CONSTRAINT #4): a position with neither a usable ATR nor forecast yields no target alert; dust positions and bad-data rows (price/cost ≤ 0) are skipped.

### `AgentState` additions (`engine/advisory_agent.py`)
Three new serialized fields (tolerant rehydration — corrupt entries dropped, never raise, CONSTRAINT #6):
- `conviction_history: Dict[str, List[float]]` — rolling per-symbol conviction window.
- `momentum_alerted: Dict[str, str]` — Ability A debounce.
- `price_trigger_alerted: Dict[str, str]` — Ability B debounce.

### Wiring in `main._run_agent_loop`
New step (4b), after the backlog-reminder step and before state persistence: lazily imports the four `engine.trade_signals` callables; updates `state.conviction_history`; runs both detectors (Ability B reads `result.snapshot`); concatenates and dispatches the alerts; advances the debounce maps on `state`. Wrapped in its own try/except so a failure degrades the cycle gracefully without affecting cadence or backlog.

### `engine/advisory_agent.py` refinements (same task)
- Removed the dead `seen_now` set in `update_backlog` (computed, never read).
- Fixed a doubled match-window in the "actioned" backlog clear: the upper bound was `first_seen + 2*match_window_h` (48 h) instead of the intended `+ match_window_h` (24 h).
- Replaced three hand-rolled `BacklogEntry` reconstructions with `dataclasses.replace`.

### CONFIG (`engine.trade_signals.CONFIG`)
| Key | Default | Purpose |
|---|---|---|
| `momentum_lookback_cycles` | 5 | Rolling conviction window length |
| `momentum_min_cycles` | 3 | Min points before a trend is judged |
| `momentum_rising_delta` | 0.10 | Rise across window to flag "building" |
| `momentum_building_floor` | 0.60 | Min conviction for a "building" flag |
| `momentum_building_ceiling` | 0.85 | Upper bound (= backlog siren; avoids double-alert) |
| `momentum_falling_delta` | 0.15 | Drop across window to flag "fading" |
| `stop_atr_multiple` | 2.5 | ATR stop distance below cost |
| `stop_fallback_pct` | 0.08 | Stop distance when ATR missing |
| `stop_proximity_pct` | 0.02 | Band above stop that triggers |
| `target_atr_multiple` | 3.0 | ATR target distance above cost (no-forecast fallback) |
| `target_proximity_pct` | 0.02 | Band below target that triggers |
| `min_position_value_usd` | 100.0 | Dust-position floor |

### Test surface
- **`tests/test_trade_signals.py`** (41 tests, 5 classes): `TestConvictionHistory` (append/trim/prune/immutability/NaN-skip), `TestConvictionMomentum` (building once+debounce, ceiling/floor/min-rise suppression, SELL block, not-enough-history, fading HIGH, BUY block, trend-reset clears debounce, direction flip re-fires, immutability), `TestPriceTriggers` (ATR stop, breach, % fallback, forecast target, already-exceeded, ATR target fallback, midrange no-trigger, debounce, dust/zero-qty/bad-data filtering, no-rec % stop, empty/missing positions, immutability), `TestDispatch` (empty no-op, one-per-alert, dashboard URL, broken-notify swallowed, priority forwarded), `TestModuleSurface` (CONFIG keys, frozen dataclass, no order keywords).
- **`tests/test_advisory_agent.py`** — extended with `test_agent_state_roundtrips_trade_signal_fields` and `test_agent_state_from_dict_drops_corrupt_history`.

### Gravity step 70 (`step_70_trade_signals_audit`)
10 checks: module importable; CONFIG keys; history append/trim/prune/immutability; building once+debounce; ceiling suppression; fading HIGH; ATR stop HIGH; forecast target + debounce; dust ignored (CONSTRAINT #4); ADVISORY-ONLY source + main.py wiring + test file exists.

## Tier 7 — Robinhood Realized-P&L Engine (2026-06)

### Overview
`data/robinhood_orders.py` is a **READ-ONLY, ADVISORY-ONLY** engine that fetches the account's *filled* equity orders and reconstructs closed round-trip trades via FIFO lot-matching — producing realized P&L, win rate, profit factor, and holding-period stats. It is the live, repeatable source for the *closed-trade population* that the calibration tracker (Tier 1.2), fractional-Kelly sizing (`sizing/kelly.py`), and the GUI consume (the same FIFO reconstruction that originally seeded the `trades` table, now a first-class tested module). It contains **NO order-submission/modification/cancellation code** (pinned by Gravity step 71 check 10 + the repo-wide `TestNoOrderFunctions` AST guard).

### Public API
- **`OrderFill`** — frozen dataclass: `symbol`, `side` (`buy`/`sell`), `quantity`, `price` (avg execution), `timestamp` (UTC-aware), `order_id`. JSON round-trips via `to_dict`/`from_dict`.
- **`ClosedTrade`** — frozen dataclass: `symbol`, `quantity`, `entry_ts`, `exit_ts`, `entry_price`, `exit_price`, `realized_pnl`, `return_pct`, `holding_days`.
- **`reconstruct_closed_trades(fills) -> list[ClosedTrade]`** — PURE FIFO per symbol: buys push open lots, sells consume oldest-first; partial lots are retained; a sell exceeding open lots matches what exists and DROPS the unmatched excess (CONSTRAINT #4 — never a fabricated zero-cost entry). Output sorted by `exit_ts` ascending (matches `TransactionsStore.closed_trades_df()`).
- **`realized_pnl_summary(trades) -> dict`** — PURE aggregation: `n_trades`, `total_realized_pnl`, `win_rate`, `avg_win`, `avg_loss`, `profit_factor`, `avg_return_pct`, `avg_holding_days`, `best_/worst_trade_pnl`, `gross_profit/loss`. Empty input → NaN-shaped (win rate/averages NaN, never 0.0); `profit_factor` is NaN when there are no losing trades (ratio undefined, not infinite).
- **`parse_orders(raw_orders, symbol_resolver) -> list[OrderFill]`** — normalises raw `get_all_stock_orders()` dicts; keeps only `state == "filled"` with positive qty/price; resolves the instrument URL → ticker via the injected `symbol_resolver`; timestamp fallback chain `last_transaction_at → updated_at → created_at`; malformed records logged at DEBUG and skipped.
- **`fetch_filled_orders(*, force=False, cache_max_age_hours=20.0, orders_fetcher=None, symbol_resolver=None) -> list[OrderFill]`** — network fetch with a daily JSON cache (`cache/robinhood_orders.json`, atomic write-then-rename). Reuses the shared read-only TOTP login from `data.robinhood_portfolio._login`. `orders_fetcher`/`symbol_resolver` are injectable for tests. Dead-letter resilient: a fetch/auth failure degrades to the (stale) cache, else `[]` — never raises (CONSTRAINT #6). The default `symbol_resolver` memoises `get_symbol_by_url` so each instrument URL hits the network at most once per process.
- **`realized_performance(*, force=False, …) -> dict`** — convenience: fetch → reconstruct → summarise, returning `{"summary": {...}, "trades": [...], "n_fills": int}`.

### Design constraints
- **READ ONLY / ADVISORY ONLY** — only `get_all_stock_orders` / `get_symbol_by_url` (reads) are called; no execution surface.
- **No fabricated metrics (CONSTRAINT #4)** — unmatched sells dropped (not invented); empty summaries are NaN-shaped.
- **No auto-persist** — the module is analytics-only; it deliberately does NOT write into the production `trades` table (avoids double-counting the Kelly population). Persistence, if ever wanted, is a separate explicit step via `TransactionsStore.record_trade`/`close_trade`.

### Test surface
- **`tests/test_robinhood_orders.py`** (29 tests, 5 classes): `TestFifoReconstruction` (simple/partial/retained-lot/loss/excess-drop/sell-without-buy/open-position/multi-symbol/sort/zero-qty filtering), `TestSummary` (NaN-empty, win-rate+profit-factor, NaN-PF-no-losses, avg-holding-days), `TestParseOrders` (filled-only, resolver skip, timestamp fallback, zero qty/price, price fallback, malformed skip, empty), `TestFetchAndCache` (injected fetcher, write-then-read cache, failure→empty, failure→stale-cache, end-to-end performance, OrderFill round-trip), `TestModuleSurface` (frozen ClosedTrade, no order-submission keywords). All offline.

### Gravity step 71 (`step_71_robinhood_orders_audit`)
10 checks: importable + full surface; FIFO two-lot split; realized P&L/return%; excess-sell drop (CONSTRAINT #4); exit_ts sort; NaN-empty summary; win-rate+profit-factor (NaN PF no losses); parse_orders filled-only+resolver; fetch dead-letter resilience (CONSTRAINT #6); ADVISORY-ONLY source + test file exists.

### No new env vars / dependencies
Reuses the existing `RH_USERNAME`/`RH_PASSWORD`/`RH_MFA_SECRET` credentials and `robin_stocks` dependency. New cache file: `cache/robinhood_orders.json` (never committed; under the existing `cache/` ignore).

## Tier 8 — Robinhood Execution Bridge (2026-06)

### Overview
Integrates the **Robinhood Trading MCP** (`https://agent.robinhood.com/mcp/trading`) so the platform can act on its advisory output — **paper/dry-run first**. The MCP is an **LLM-agent tool surface** (consumed by Claude Code: `review_equity_order`, `place_equity_order`, `cancel_equity_order`, plus read tools), NOT a Python SDK, so the headless pipeline **cannot** call it. Integration is therefore a **seam**: the Python pipeline emits a gated, dry-run order queue; a Claude Code agent is the only actor that calls the MCP. Robinhood's blast-radius control is a dedicated, separately-funded **Agentic account**; its dry-run primitive is `review_equity_order` (simulate, no execution).

**Relationship to ADVISORY_ONLY:** independent. `ADVISORY_ONLY` (default `True`) stays the master quarantine of the **Alpaca** surface (`main_orchestrator._execute_broker_orders`). Robinhood gets its own `ROBINHOOD_EXECUTION_MODE` so one flag never arms two brokers. Robinhood-live does **not** require lifting `ADVISORY_ONLY`.

### Two-phase ledger invariant
*Python writes intents; the human-driven Claude agent writes outcomes.* No component both decides and executes. Headless Python has no MCP access and defines no `place_*`/`submit_order` function (enforced by `TestNoOrderFunctions` + Gravity).

### `execution/queue_builder.py` (NEW — inside the AST-excluded `execution/` zone)
Reuses the existing decision stack (`OrderIntent`, `PreTradeRiskGate.run_all`, `GlobalKillSwitch`, `make_client_order_id`) to translate actionable advisory `Recommendation`s into a gated, dry-run queue — it **never contacts a broker or the MCP**. Public API (AST-safe names — no `place_*`/`submit_order`/`*_order`):
- `build_execution_queue(run_result, *, mode, config, now) -> dict` — gated payload.
- `gate_intent(intent, context, gate=None) -> (allowed, reasons)` — runs `PreTradeRiskGate`; **fails CLOSED** on exception (never marks allowed).
- `emit_execution_queue(run_result, *, mode=None, output_dir=None) -> Optional[Path]` — atomically writes `output/execution_queue.json`; returns `None` and writes nothing when mode is `off`; never raises (CONSTRAINT #6).
- `CONFIG` (`min_conviction=0.85`, `strategy_id="advisory"`), `VALID_MODES=("off","review","live")`.

**Intent mapping:** BUY → `qty=null` + capped `target_notional` (equity × `suggested_position_pct`, capped by `ROBINHOOD_MAX_NOTIONAL_PER_ORDER`); the agent computes shares from a live MCP quote. SELL → only for HELD symbols, `qty` = held quantity (a SELL of an unheld symbol is dropped — no fabricated position). HOLD / below-`min_conviction` / not-held-SELL are dropped.

**Safety invariant:** `allow_place = (mode=="live") AND gate_allowed AND (not kill_switch_active) AND (notional cap configured)` — structurally `False` in every non-live posture and whenever the cap is unset or the kill switch is active.

### Staged execution mode (`settings.ROBINHOOD_EXECUTION_MODE`, default `off`)
| Mode | Behavior |
|------|----------|
| `off` (default) | `emit_execution_queue` returns `None`; nothing written; zero behavior change. |
| `review` (paper/dry-run) | Queue emitted; the agent calls **only** `review_equity_order`; every intent `allow_place=False`. |
| `live` | `allow_place=True` only when gate passed + kill switch clear + cap set; the agent still requires per-trade human confirmation. |

Rollout is strictly `off → review → live`. A `field_validator` coerces any unknown value → `off` (fail-safe). New setting `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` (default `0.0`; `live` requires it `> 0`).

### Wiring
`main.py::_run_cycle` calls `emit_execution_queue(result)` in a best-effort, non-fatal block (next to the Tier 1.4 watch-engine block). It only writes a file — never contacts a broker. The kill-switch advisory-pause gate already short-circuits `run_once()`, so a paused cycle emits nothing. `output/` is gitignored, so the queue + receipts are never committed.

### Claude Code execution surface (the only MCP caller)
- `.claude/skills/robinhood-execution/SKILL.md` — the runbook: verify MCP connected + queue fresh; honor hard stops (kill switch, `mode: off`, stale queue, no confirmed Agentic account); `get_accounts` → confirm Agentic account; **always `review_equity_order` first**; in `review` stop after preview; in `live` place only `allow_place=true` intents, one at a time, with explicit per-order human confirmation, re-checking the kill switch before each placement; append outcomes to `output/execution_receipts.jsonl`.
- `.claude/commands/rh-execute.md` — `/rh-execute` entry point.

### Setup (operator, local — cannot be done headless)
`claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading`, then `/mcp` → authenticate (OAuth). Fund a dedicated Agentic account. This is interactive and must run in the operator's own Claude Code.

### Guards / audits
- `tests/test_pipeline_smoke.py::TestNoOrderFunctions` — set unchanged (bridge is in `execution/`); added a positive assertion that `queue_builder` defines no order-submission function.
- `scripts/preflight_check.py` — new `check_robinhood_execution_mode` (PASS for off/review; **FAIL** for `live` without a notional cap; warning-only PASS for `live` with a cap). NOT in `_ADVISORY_AUTO_SKIP`. `ALL_CHECKS` is now 17 (Gravity step_66 count updated).
- `gravity/__init__.py` — `step_72_robinhood_execution_bridge_audit` (10 checks): off emits nothing; review never placeable; live+cap+clear-KS allows; kill-switch blocks; cap-unset blocks; drop rules + held-SELL qty; settings default+fail-safe; preflight live-without-cap fails + not auto-skipped; no order defs + main.py wiring + agent skill/command exist.

### Test surface
- `tests/test_queue_builder.py` (19 tests): mode staging (off→no file, review→file+never-placeable), allow_place gating (live+cap allows, no-cap blocks, kill-switch blocks all, gate-failure fails closed), intent construction + drop rules + held-SELL qty + capped notional + deterministic client_order_id, payload schema + atomic write + emit-failure swallowed, `gate_intent` unit.

### Domain note (follow-up)
A GUI banner surfacing the Robinhood execution mode (red on `live`) belongs in `gui/app.py` / `gui/panels.py` (Antigravity-owned per the domain split) and is intentionally left as a follow-up for the GUI owner — it is informational, not a guard.

### New env vars / settings
- `ROBINHOOD_EXECUTION_MODE` — `off` (default) | `review` | `live`.
- `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` — USD per-order ceiling (default `0.0`; required `> 0` for live).
New output artifacts (gitignored): `output/execution_queue.json` (Python-written intents), `output/execution_receipts.jsonl` (agent-written outcomes).

## Prompt Registry (`prompt_registry/`, 2026-06)

Versioned, cryptographically-signed, remotely-updatable store for every AI-facing instruction
(master pre-prompt, Gravity step bodies, etc.).

### Security boundary — MUST NEVER BE VIOLATED

**Fetched prompts are advisory text only.** The registry can change what an AI is *told*; it
cannot change what the platform is *permitted to do*. Order submission, advisory quarantine,
risk gates, and the kill switch are enforced in Python code — never in prompt bodies.
This invariant is audited on every Gravity run (step 69, check 7).

### Package structure

| Module | Role |
|---|---|
| `prompt_registry/models.py` | `PromptRecord`, `PromptVersion`, `RegistryManifest` frozen dataclasses |
| `prompt_registry/signing.py` | HMAC-SHA256 `sign()` / `verify()` + `compute_sha256()` |
| `prompt_registry/guardrails.py` | `validate_prompt()` — rejects `ADVISORY_ONLY=false`, `submit_order`, size overflow |
| `prompt_registry/cache.py` | `CacheManager` — signed-version disk cache + `read_baseline()` / `list_baseline_ids()` |
| `prompt_registry/store.py` | `PromptStore` ABC + `LocalJSONStore` + `HTTPStore` (stdlib `urllib`, no new dep) |
| `prompt_registry/registry.py` | `PromptRegistry` — resolution chain, `sync()`, `rollback()`, `get_registry()` singleton |
| `prompt_registry/__main__.py` | CLI: `list`, `get`, `sync`, `pin`, `rollback`, `diff`, `verify`, `publish` |
| `prompt_registry/baseline/` | Git-committed fallback bodies (always available, zero network) |

### Resolution chain (CONSTRAINT #4 — never `""`)

```
Pin (PROMPT_REGISTRY_PINS) → Remote latest (verified) → Disk cache (verified) → Baseline → sentinel
```

### Secret keys (CONSTRAINT #3)

Four `PROMPT_REGISTRY_*` credentials live in `gui/env_io.SECRET_KEYS` and are **never**
GUI-writable. Set them by hand in `.env` only:

| Key | Role |
|---|---|
| `PROMPT_REGISTRY_URL` | Protected HTTPS URL of the signed manifest |
| `PROMPT_REGISTRY_TOKEN` | Bearer read-token (runtime platform) |
| `PROMPT_REGISTRY_PUBLISH_TOKEN` | Higher-privilege publish credential (author machine only) |
| `PROMPT_REGISTRY_SIGNING_KEY` | HMAC-SHA256 verification key |

Three non-secret tunables (`PROMPT_REGISTRY_ENABLED`, `PROMPT_REGISTRY_BACKEND`,
`PROMPT_REGISTRY_PINS`) are in `gui/env_io.ALLOWED_KEYS` and writable from the GUI Prompts tab.

### Constraints enforced by this codebase

- **CONSTRAINT #3** — 4 secret keys masked + raise `SecretWriteError` on GUI write attempt.
- **CONSTRAINT #4** — `get()` never returns `""`; fails closed to baseline then sentinel.
- **CONSTRAINT #5** — `PROMPT_REGISTRY_REFRESH_SECONDS` defaults to `0`; sync is explicit only
  (CLI or GUI "🔄 Sync" button, never on a timer or at table render).
- **CONSTRAINT #6** — every fetch/verify/parse path in `registry.py` and `__main__.py` degrades
  gracefully; no exception propagates past the GUI boundary.

### Gravity step 69 (`step_69_prompt_registry_audit`)

10 checks:
1. `prompt_registry` importable; `get_registry`, `PromptRegistry`, `PromptRecord` exist.
2. Fail-closed: with no URL/cache, `get("gravity.system")` returns the baseline (non-empty).
3. `verify(tampered_body)` is `False`; `verify(signed_body)` is `True`.
4. Guardrail rejects `ADVISORY_ONLY=false` body and `submit_order` body.
5. Four `PROMPT_REGISTRY_*` secret keys in `SECRET_KEYS` AND not in `ALLOWED_KEYS`.
6. Disabling registry leaves Gravity prompts byte-identical to baseline.
7. No `eval`/`exec`/`import` in `prompt_registry/` source or `ai_verification_prompts.py`.
8. `PROMPT_REGISTRY_REFRESH_SECONDS` default is `0` (CONSTRAINT #5).
9. CLI `verify` exits non-zero on a corrupt cache fixture.
10. `tests/test_prompt_registry_resolution.py` exists.

### Operational notes

- `python -m prompt_registry get master_preprompt` — fetch and print the resolved body.
- `python -m prompt_registry sync` — explicit pull from remote manifest.
- `python -m prompt_registry rollback <id>` — pin to previous cached version.
- Publishing v1.1.0 and moving the "latest" pointer is the "update over the internet" mechanism.
- See `docs/HOW_TO_GUIDE.md §16` for the full operator workflow.
- See `docs/RUNBOOK.md §7` for the publish/rollback incident playbooks.
