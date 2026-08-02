# FMP Data Integration

**Source:** `data/fmp_client.py`, `data/fmp_fundamentals.py`, `data/fmp_macro.py`, `data/fmp_feeds_company.py`, `data/fmp_feeds_market.py`, `FMPProvider` in `data/market_data.py`
**Verification gate:** `scripts/verify_fmp_bars.py`
**Architecture reference:** `docs/architecture/data-layer.md`'s "FMP data layer" bullet
**Planning source:** this document, `docs/architecture/data-layer.md`, and `CLAUDE.md`'s FMP bullet are all derived from the integration plan (`i-just-signed-up-modular-abelson`); where a claim below could not be independently re-verified against a live response by the agent that wrote it, that is stated explicitly rather than presented as confirmed.

Every setting in this document defaults to today's exact pre-FMP behavior. Nothing here is active until an operator explicitly flips a flag in `.env` — this document exists so that flip is an informed one.

---

## 1. What FMP is used for

The platform's entire fundamentals and price stack ran, until this integration, on unauthenticated Yahoo/yfinance scraping — fragile by construction, and several downstream metrics were already silently broken or fabricated as a result (see `CLAUDE.md`'s FMP bullet for the specific list: `Institutional Velocity` hardcoded to `0.0`, `dividend_growth_rate` falling back to a fabricated 2% constant, `fetch_macro_raw` falling back to hardcoded macro constants, and others). Financial Modeling Prep becomes the **primary, opt-in source** for quotes, bars, and fundamentals, with the existing Alpaca/yfinance/Yahoo providers kept as automatic fallbacks — never removed, never bypassed silently. Alongside that replacement role, four genuinely new feeds land as **diagnostic-only dashboard columns**: analyst consensus/grades, an earnings calendar with surprise history, treasury-rate and economic-indicator macro supplements, and insider-trading + sector-snapshot statistics. None of the four is a scored signal (see §1a below) — they exist to give an operator more to look at, not more for the strategy engine to trade on.

**1a. Why none of the four new feeds is a `SignalModule`.** `signals/` modules are backtested through the purged-CV validation harness before they can earn a `SIGNAL_WEIGHTS` entry — that is the platform's whole quality bar for anything influencing `final_score`. None of the four new feeds has point-in-time history on day one (FMP serves only the *current* analyst consensus, for example — targets get revised, and there is no archived "what did the consensus say on date X" to backtest against). A signal the repo cannot backtest cannot earn a weight. This is also the no-lookahead *guarantee mechanism*, not just a policy: because nothing new ever enters `signals/` or `dto_models.py`, nothing new ever needs to pass through the perturbation/lookahead test harness in the first place.

---

## 2. Account tier and its consequences

The integration was planned and probed against a **Starter** plan.

**Confirmed working on Starter:** `/quote` + `batch-quote`; EOD charts (confirmed back to 2008) + intraday; full statements, `key-metrics-ttm`, `ratios-ttm`, `financial-scores`; `/profile`, `/market-cap`, `/shares-float`, `/peers`; analyst `price-target-consensus`, `analyst-estimates`, `grades-summary`, `ratings-snapshot`; calendars (earnings/dividends/splits, including future-dated rows); `treasury-rates` (full curve, daily), `economic-indicators`, `economics-calendar`; `insider-trading/statistics`; `sector-pe-snapshot` + `sector-performance-snapshot` (date-parameterized); news; technical indicators.

**Confirmed blocked — verified `ACCESS DENIED`, requires Ultimate/Enterprise:** ETF & mutual-fund holdings, and **Form 13F / institutional ownership**.

Two honest, permanent consequences follow, stated plainly so nobody re-discovers them later as if they were bugs:

1. **`data/etf_holdings.py`'s SEC N-PORT path cannot be replaced by FMP.** ETF/mutual-fund holdings are Ultimate/Enterprise-only on this account. `data/etf_holdings.py` stays exactly as it is — SEC N-PORT via EDGAR, 1–5 months stale by the nature of that filing cadence.
2. **`Institutional Velocity` cannot be fixed by this integration, and stays hardcoded `0.0`.** It is computed from `netPercentInsiderShares` / `sharesShort` / `sharesOutstanding`, but Form 13F (institutional ownership) is Ultimate-only and Starter has no short-interest feed at all — there is no FMP endpoint on this plan that could feed either half of that calculation. This is a **known, permanent limitation of the Starter tier**, not a bug to file against this integration or a gap left for a future PR to close. A future plan upgrade (Ultimate/Enterprise) is the only path that changes this.

---

## 3. Settings reference

All 26 settings live in `settings.py` under `# --- 25. Financial Modeling Prep (data/fmp_client.py) ---`, are mirrored in `.env.example`, and (except the credential) are GUI-writable via `gui/panels/settings_manager.py`. **`FMP_API_KEY` alone never elects FMP as the active provider for anything.** All nine feed master gates enforce a genuine two-gate convention (the `STOCKTWITS_ENABLED` precedent) where a source is actually being REPLACED: quotes need `MARKET_DATA_PROVIDER=fmp` **and** `FMP_QUOTES_ENABLED`; bars need `MARKET_DATA_PROVIDER=fmp` **and** `FMP_BARS_ENABLED`, independently of the quotes gate; fundamentals need `FUNDAMENTALS_SOURCE=fmp` **and** `FMP_FUNDAMENTALS_ENABLED`; the six diagnostic feeds (`FMP_ANALYST_ENABLED` through `FMP_OPTIONS_HEALTH_ENABLED`) are each a single, standalone gate — they add new columns rather than replacing an existing source, so there is no second selector to require. When `MARKET_DATA_PROVIDER=fmp`/`FUNDAMENTALS_SOURCE=fmp` is set but the matching capability flag is `False`, that capability falls through **unconditionally** to the pre-existing default (Alpaca-if-keyed-else-yfinance for quotes/bars, Yahoo-derived for fundamentals) — this is deliberately independent of `FMP_FALLBACK_ENABLED`, since FMP is never attempted in the first place and there is nothing to fall back *from*. `CompositeProvider.quote_source`/`.is_realtime`/`.source_name` always report the provider that is genuinely serving, never `"fmp"` while its capability gate is off. `FMP_QUOTES_ENABLED` and `FMP_BARS_ENABLED` are fully independent: an operator can run quotes on FMP while bars stay on yfinance, or vice versa, from the same `MARKET_DATA_PROVIDER=fmp` selection.

### Credential (1) — `SECRET_KEYS` only, never GUI-writable

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `FMP_API_KEY` | `Optional[str]` | `None` | Financial Modeling Prep API key. Absent → every request short-circuits with zero network cost and every FMP-backed feed degrades to its existing source or to `NaN`. |

### Client tuning (7) — throttle / retry / breaker

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `FMP_BASE_URL` | `str` | `"https://financialmodelingprep.com/stable"` | Base URL every request is built from. The `/stable` family is the one the verified endpoint paths belong to. |
| `FMP_TIMEOUT_SECONDS` | `float` | `10.0` | Per-request HTTP timeout. A timeout is a transport error: never retried, but counts toward `FMP_COOLDOWN_THRESHOLD`. |
| `FMP_MIN_REQUEST_INTERVAL_SECONDS` | `float` | `0.25` | Minimum seconds between request issuance, shared process-wide across every FMP consumer (the budget is per-account). `0.25` = 240 req/min by construction. |
| `FMP_MAX_RETRIES` | `int` | `2` | Retries after a 429/5xx before giving up, with exponential backoff. Only 429/5xx are retried. |
| `FMP_RETRY_BACKOFF_SECONDS` | `float` | `2.0` | Base seconds for retry backoff; attempt N waits `FMP_RETRY_BACKOFF_SECONDS * 2**N` unless the server sent `Retry-After`. |
| `FMP_COOLDOWN_THRESHOLD` | `int` | `5` | Consecutive failed requests (429, 5xx, or transport error) after which FMP calls are skipped outright for `FMP_COOLDOWN_SECONDS`. |
| `FMP_COOLDOWN_SECONDS` | `float` | `300.0` | How long the cooldown stays open once the threshold is hit. Self-expiring — a recovered account resumes without operator action. |

`FMP_MIN_REQUEST_INTERVAL_SECONDS=0` with `FMP_MAX_RETRIES=0` and `FMP_COOLDOWN_THRESHOLD=0` reproduces un-throttled behavior exactly (the `GDELT_*` comment convention).

### Master feed gates (9) — all default `False` = complete no-op

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `FMP_QUOTES_ENABLED` | `bool` | `False` | Master switch for FMP-sourced quotes. Also requires `MARKET_DATA_PROVIDER=fmp`. |
| `FMP_BARS_ENABLED` | `bool` | `False` | Master switch for FMP-sourced OHLCV bars. Also requires `MARKET_DATA_PROVIDER=fmp`. Read `FMP_BARS_ADJUSTMENT` and run `scripts/verify_fmp_bars.py` before enabling — see §4. |
| `FMP_FUNDAMENTALS_ENABLED` | `bool` | `False` | Master switch for FMP-sourced fundamentals. Also requires `FUNDAMENTALS_SOURCE=fmp`. |
| `FMP_ANALYST_ENABLED` | `bool` | `False` | Master switch for the analyst feed (price-target consensus + grades summary) as diagnostic columns. Single gate. |
| `FMP_EARNINGS_ENABLED` | `bool` | `False` | Master switch for the earnings calendar/surprise feed. Single gate. |
| `FMP_MACRO_ENABLED` | `bool` | `False` | Master switch for the macro feed (treasury rates + `FMP_ECON_INDICATORS`). Single gate. |
| `FMP_INSIDER_ENABLED` | `bool` | `False` | Master switch for the insider-trading statistics feed (per-symbol cost). Single gate, separate from sector snapshots on purpose. |
| `FMP_SECTOR_SNAPSHOT_ENABLED` | `bool` | `False` | Master switch for the dated sector P/E + sector performance snapshots (2 requests/cycle total). Single gate. |
| `FMP_OPTIONS_HEALTH_ENABLED` | `bool` | `False` | Master switch for the fundamental-health overlay bundled into the options premium-directive matrix (Altman Z / Piotroski F / Net Debt-EBITDA / FCF Yield / 30-day realized vol). Single gate bundling three endpoints. See §3a below. |

### Behavior knobs (9)

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `FMP_FALLBACK_ENABLED` | `bool` | `True` | When `True`, an FMP failure falls through to the existing provider chain (quotes/bars: FMP → Alpaca if keyed → yfinance; fundamentals: FMP → Yahoo statement-derived → yfinance `.info`), logging a `WARNING`. `False` makes the chain `[primary]` only. |
| `FMP_QUOTES_REALTIME` | `bool` | `False` | Whether FMP quotes may be labelled real-time (`is_stale=False`). Stays `False` because whether `/quote` is genuinely real-time on Starter could not be verified from the sandbox this integration was built in. |
| `FMP_BARS_ADJUSTMENT` | `str` | `"dividend-adjusted"` | Which `/historical-price-eod` variant bars are pulled from: `"dividend-adjusted"`, `"light"`, `"full"`, or `"non-split-adjusted"`. **Not cosmetic — see §4, the single highest-risk setting in this integration.** |
| `FMP_ANALYST_REFRESH_HOURS` | `int` | `24` | Hours before a symbol's cached analyst consensus is re-fetched. |
| `FMP_EARNINGS_REFRESH_HOURS` | `int` | `12` | Hours before a symbol's cached earnings rows are re-fetched. |
| `FMP_INSIDER_REFRESH_DAYS` | `int` | `7` | Days before a symbol's cached insider statistics are re-fetched. |
| `FMP_INSIDER_MIN_LAG_DAYS` | `int` | `45` | Minimum days a quarter must have been closed before its insider aggregate is consumed (Form 4s keep landing after quarter-end). A deliberate conservative judgment call, not a derived constant. |
| `FMP_ECON_INDICATORS` | `str` | `"unemploymentRate"` | Comma-separated `/economic-indicators` series names fetched when `FMP_MACRO_ENABLED=True` (e.g. `"unemploymentRate,GDP,CPI"`). A plain comma-separated string, not JSON — matches the `SENTIMENT_SOURCES` convention. |
| `FMP_MAX_SECONDS_PER_CYCLE` | `float` | `120.0` | Wall-clock budget for all FMP requests in one pipeline cycle. Once spent, remaining symbols degrade to `NaN` for that cycle rather than overrunning it. |

### Related, pre-existing settings whose descriptions changed (no default changed)

`MARKET_DATA_PROVIDER` and `FUNDAMENTALS_SOURCE` both gained `'fmp'` as an accepted value, and both descriptions now state explicitly that setting `FMP_API_KEY` alone never elects it.

---

## 3a. Options Matrix FMP health overlay (`FMP_OPTIONS_HEALTH_ENABLED`)

A fifth diagnostic overlay, added on top of the four described in §1 — same "diagnostic-only, never a `SignalModule`" treatment, scoped specifically to the webapp's Options Matrix screen rather than the general dashboard.

**What it gates.** `FMP_OPTIONS_HEALTH_ENABLED` (default `False`) is a single gate bundling three endpoints that are always fetched together for one overlay concept ("is this credit-spread candidate financially healthy"), matching the `FMP_SECTOR_SNAPSHOT_ENABLED`/`FMP_INSIDER_ENABLED` precedent of one flag per logically-bundled feature:

| Endpoint | `data/fmp_client.py` wrapper | `data/fmp_feeds_company.py` / `data/fmp_feeds_market.py` shape function | Fields produced |
|---|---|---|---|
| `/financial-scores` | `financial_scores(symbol)` | `fetch_financial_scores(symbol)` | `altman_z_score`, `piotroski_f_score` |
| `/ratios-ttm` | `ratios_ttm(symbol)` | `fetch_key_ratios_ttm(symbol)` | `net_debt_ebitda`, `fcf_yield` (also `debt_to_equity`, `pe_ratio`, unused by the overlay) |
| `/standard-deviation` | `standard_deviation(symbol)` | `fetch_realized_volatility(symbol)` | `hv_30` (30-day realized volatility) |

`Days_To_Earnings` / `Earnings_Risk` are **not** gated by this flag — they reuse the pre-existing `FMP_EARNINGS_ENABLED` earnings-calendar feed (§3 above) via the durable `earnings_events` store (`HistoricalStore.get_earnings_events(symbol, after=<today>, limit=1)`), the same read pattern `pipeline/production_steps.py::_apply_fmp_earnings` already uses. No second earnings fetch of its own.

**Where the data flows.** `reporting/options_snapshot.py::write_options_matrix` is the ONE production caller that fetches these (per-symbol, each of the three sub-fetches independently try/excepted so one failing never blanks a sibling's data for the same symbol — CONSTRAINT #6) and passes them as plain kwargs into `technical_options_engine.build_premium_directive`, which is otherwise a pure, no-I/O function — it never fetches anything itself, only echoes whatever the caller supplies onto the row (`Altman_Z_Score`, `Piotroski_F_Score`, `Net_Debt_EBITDA`, `FCF_Yield`, `Days_To_Earnings`, `Earnings_Risk`, `Realized_Vol_30D`). The hydrated row is written to `output/options_matrix.json`, read by the Pilots API's pure file-reader (`pilots/options.py` → `GET /options`, deliberately never importing the engine), and rendered as badges on the webapp's Options Matrix screen (`webapp/src/screens/OptionsMatrix.tsx`'s `DirectiveCard`).

**`Integrity_OK`'s dual meaning.** `Earnings_Risk` (an earnings event scheduled inside the directive's `target_dte` window) folds into the same `Integrity_OK` boolean that the strike-grid/delta-target structural check already reports — a structurally clean directive with earnings risk still reports `Integrity_OK=False`. This is intentional: the one production consumer that reads `Integrity_OK` as an execution gate, `execution/options_queue_builder.py::passes_premium_gate`, wants a single "safe to queue" signal and already excludes any `Integrity_OK=False` row regardless of cause, matching this overlay's purpose of keeping credit spreads out of execution right before an earnings print. `Integrity_Issues` still lists the earnings warning as its own entry (`"⚠️ Earnings Announcement scheduled in N days (within target DTE M)"`), distinguishable from a genuine structural violation string. Gravity `step_38_options_matrix_integrity_audit` is unaffected — its fixtures never pass `days_to_earnings`.

**`Realized_Vol_30D` is diagnostic-only, never a fallback tier.** `build_premium_directive`'s existing IVR fallback chain (`True_IVR` → `IVR_Proxy` → hardcoded `50.0` neutral default) was NOT extended with `realized_vol_30d` as a third tier ahead of `50.0`: FMP's `/standard-deviation` returns a single current reading with no historical series this function can rank it against locally, so any "percentile" derived from one data point would be fabricated, not measured (CONSTRAINT #4). It is surfaced purely as a passthrough field for the operator to eyeball alongside the proxy/chain IVR.

**Flag-off is byte-identical.** With the default `False`, `write_options_matrix` never imports `data/fmp_feeds_company.py`/`data/fmp_feeds_market.py`, makes zero extra network calls, and every one of the seven fields above stays `None`/`False` — proven by `tests/test_options_snapshot.py::TestWriteOptionsMatrixFmpFlagsOff`.

---

## 4. The manual eyeball gates before flipping any flag live

These six items are the plan's own verification checklist, reproduced here so an operator does not need the full planning document to follow it. **Do not enable a flag past what these checks have actually confirmed.**

1. **Run `scripts/verify_fmp_bars.py` on KO/JNJ/AAPL over 2y.** Max abs relative close diff `< 1e-4` → conventions match. Otherwise **do not set `FMP_BARS_ENABLED=true`.**
2. **Print `map_fundamentals` output for KO and JNJ beside today's Yahoo output.** `dividendYield` ≈ 0.031 (**not** 3.1); `debtToEquity` ≈ 150-ish (**not** 1.5); `trailingPE` `NaN` for any loss-maker.
3. **Confirm every universe symbol's FMP `sector` is a key in `data/sector_descriptions.yaml`.** A sector FMP returns that this platform doesn't recognize silently downgrades the forecast config to `{"days":60,"model":"MC"}` — check before trusting the fundamentals path for the live universe.
4. **After the first FMP-enabled cycle:** `SELECT source, COUNT(*) FROM fundamentals_history WHERE as_of = DATE('now') GROUP BY 1`. A row of `yahoo_computed` means the chain fell back on you — investigate before assuming FMP is actually serving.
5. **Before `FMP_BARS_ENABLED=true` on an existing DB: delete `price_bars` and re-backfill.** `price_bars` has a `(symbol, date)` primary key, so flipping the bars source on an existing database splices two different adjustment conventions into one series at the cutover date — no test catches this, and nothing will warn you at runtime.
6. **Spot-check one `earnings_events` row against the company's IR page** — `eps_actual` should be `null` for the next scheduled date and populated for the last reported one.

**Why item 1 is the hard gate for `scripts/verify_fmp_bars.py` specifically:** FMP's `/historical-price-eod/full` looks like the obvious bars source, and it is wrong. The incumbent yfinance path (`data/market_data.py::YFinanceProvider`) fetches bars via `Ticker.history(auto_adjust=True)` — split **and** dividend adjusted. FMP's `light` and `full` EOD variants are **split-only**. `dividend-adjusted` is the variant that actually matches today's data. A mismatch here corrupts every return series, every indicator, every GARCH fit, and every backtest — and it does so *plausibly*: nothing fails loudly, the numbers just quietly stop meaning what they did. This is why `scripts/verify_fmp_bars.py` exists as a standalone, hand-run gate rather than a CI test: it is genuinely network-dependent, and the decision it informs (trust this adjustment convention or not) is exactly the kind of judgment call that should not be silently automated past.

---

## 5. Rollout sequencing

The integration shipped in three waves on branch `claude/fmp-data-integration-20aac7`, one PR to `main` per wave:

- **Wave 0 — Scaffold (2 agents, parallel).** Nothing behavioral. Agent S1 built the HTTP seam (`data/fmp_client.py`: throttle/retry/breaker, `reset_fmp_rate_limiter`, `get_fmp_call_stats`) and all 25 settings keys at once. Agent S2 built the schema/provider surface: `SOURCE`/`IS_REALTIME` class attributes on `data/market_data.py`'s provider classes, every new `config.COLUMN_SCHEMA` column, all four new `data/historical_store.py` table DDLs, and the four `_apply_fmp_*` NaN-fill-only stub functions in `pipeline/production_steps.py` that wave-1 agents later filled in. Both had to land and merge green before wave 1 started, specifically so wave-1 agents would have disjoint file ownership.
- **Wave 1 — Five agents, fully parallel.** F1 built `data/fmp_fundamentals.py` (the pure, I/O-free scale-conversion module) and the `FMPProvider` class in `data/market_data.py` — this is the **MVP**: wave 0 plus F1 alone is the shippable milestone if the rest of wave 1 had to be cut short. F2 built `data/fmp_macro.py` (treasury rates + econ indicators into the existing `macro_history` table) and fixed a pre-existing dead code path in `data/historical_store.py::_resolve_data_engine`. F3 built `data/fmp_feeds_company.py` (analyst consensus/grades + earnings calendar/surprises). F4 built `data/fmp_feeds_market.py` (insider statistics + sector snapshots). F5 (this document, `scripts/verify_fmp_bars.py`, and `docs/architecture/data-layer.md`/`CLAUDE.md`'s FMP bullets) had no shared-file touchpoints with the other four.
- **Wave 2 — Quotes + bars (1 agent, serialized).** Deliberately kept out of wave 1 and merged only after it: this is the highest-corruption-risk change in the whole plan (the bars adjustment convention), and it edits the same `FMPProvider` class F1 introduces, so it could not safely merge concurrently with F1's work. Adds `get_latest_quote`, `get_intraday_bars`, the `_select_quote_provider` `'fmp'` branch, the quote/bars fallback chain, and the `FMP_BARS_ADJUSTMENT` wiring — gated on `scripts/verify_fmp_bars.py` having already passed.

**What's shippable at each stage:** wave 0 alone ships nothing behavioral (pure scaffolding). Wave 0 + F1 (fundamentals) is the MVP — a real, usable FMP fundamentals path with automatic fallback, no bars/quotes risk taken on at all. Wave 0 + all of wave 1 adds the four diagnostic feeds and the macro supplement on top of that, still with quotes/bars untouched. Only wave 2, gated by a passing `scripts/verify_fmp_bars.py` run, unlocks FMP-sourced quotes and bars.

---

## 6. Known risks

Reproduced from the integration plan's own "Risks" section, largely verbatim, so a future operator or engineer can understand the honest risk profile here without re-reading the full planning document.

**Could silently corrupt results:**
- Bars adjustment convention (§4 above — the highest-severity risk in this integration; it fails *plausibly*, not loudly).
- Mixed-source `price_bars` splicing at the adjustment-convention cutover date on an existing database — no test catches this (manual gate item 5 is the only defense).
- `dividendYield` percent-vs-fraction: the unit guard catches an obviously-wrong value like `2.57`, but a *fraction-shaped percent* (e.g. `0.8` meant as "0.8%") passes the guard undetected.
- `beta` definition drift if a future change "simplifies" to FMP's `/profile.beta` (a vendor 5-year-monthly number) instead of the platform's own `Cov(stock,SPY)/Var(SPY)` computation — this would move the `Beta` column, the Quality Score's `beta < 1.0` bump, and the low-vol factor well past the platform's 1e-5 drift budget.
- Basic-vs-diluted EPS feeding into the Graham number if a future change reads the wrong EPS field.
- `trailingPE` sign flip for loss-makers if the `NaN`-on-non-positive-EPS clamp is ever removed.
- `previousClose` leakage making every name's `Price` silently become yesterday's close, if those fields are ever mapped (the plan explicitly says not to map them — see the fundamentals field-mapping table in the planning document).
- A silent fallback masquerading as success — this is why the fallback chain's four observability mechanisms (the `_source` key, per-provider serve counters, and a `WARNING` on every fallback) all exist and are load-bearing, not optional.
- The 6-hour positive fundamentals-cache TTL pinning a stale (fallback-served) dict for hours after FMP recovers — **this is not a bug to fix**; shortening it would re-hammer every provider on every cycle. The lever is `clear_fundamentals_cache()`, not a shorter default TTL.
- Throttle serialization turning an 8-way-parallel fundamentals fetch into roughly 25 seconds of serial issuance per cycle at steady state.
- Module-global rate-limiter state leaking between tests as silent skips if `reset_fmp_rate_limiter()` is not called in test fixtures.

**Cannot be verified in this sandbox** (no live-market network access in this codebase's dev/CI environment):
- Every live response *shape and unit* claimed in the fundamentals field-mapping table — the field names and scales came from the FMP MCP connector probes plus FMP's documented conventions, not from responses read by the shipped implementation itself.
- Starter-tier entitlement for the three EOD variants beyond what was directly probed.
- Whether `/quote` is genuinely real-time on Starter (hence `FMP_QUOTES_REALTIME` defaults `False`).
- Actual 300/min rate-limit enforcement semantics (per-key or per-IP, burst-tolerant or strict) — the client's throttle defaults are a conservative ~240/min target, not a documented contract.
- Starter-vs-Premium status of `grades-summary`, `analyst-estimates`, and `economics-calendar` specifically — the 403/entitlement-detection path in `data/fmp_client.py` exists precisely so any of these degrade to `NaN` rather than a fabricated default if the plan boundary turns out to be different from what was probed.

**Not fixable on Starter, stated plainly** (see §2 above for the full explanation):
- `Institutional Velocity` stays hardcoded `0.0` — 13F is Ultimate-only, and Starter has no short-interest feed either.
- `data/etf_holdings.py`'s SEC N-PORT path and its 1–5 month staleness are unchanged by this integration.
