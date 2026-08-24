/**
 * types.ts — TypeScript mirror of api/pilots_api.py response shapes.
 *
 * Sourced from the plan's "Phase 2 — API layer" endpoint contracts. When the
 * live backend lands, these are the single point to reconcile against the real
 * JSON. Nothing else in the app hard-codes a response shape.
 */

export type JobType =
  | "preflight"
  | "pytest"
  | "validation"
  | "verify"
  | "gravity"
  | "advisory"
  | "orchestrator"
  | "command"
  | "train_meta"
  | "train_lgbm";

export interface CronJob {
  title: string;
  description: string;
  schedule: string;
  command: string;
}

export interface CronStatus {
  jobs: CronJob[];
  error?: string;
}

export interface JobRecord {
  job_id: string;
  job_type: JobType;
  status: string;
  exit_code?: number | null;
  is_running?: boolean;
  cancellable: boolean;
  /** Only set for job_type === "command" -- the manifest command name that
   *  was launched (e.g. "validation.harness"). null/undefined for every
   *  other job type, which has exactly one instance so job_type alone is
   *  already an unambiguous label. */
  command_name?: string | null;
  /** ISO 8601 timestamp captured when the job was launched, for `timeAgo()`. */
  created_at?: string;
}

/**
 * POST /jobs params for `job_type: "command"` — executes a command composed
 * by the Commands screen's autocomplete bar via the backend's gated
 * `COMMAND_EXECUTION_ENABLED` path. `confirm` is sent `true` once the operator
 * has clicked through the Run control (plain Run for a non-high-stakes
 * command, or the confirmation dialog's "Yes, run it" for one flagged by
 * `commandParse.ts`'s `highStakesReason`) — the server re-derives whether
 * confirmation was actually required and is the enforcing authority either
 * way, this is not a trust-the-client flag.
 */
export interface CommandJobParams {
  command: string;
  subcommand?: string | null;
  args: string[];
  confirm?: boolean;
}

export interface RestartDaemonResult {
  restarting: boolean;
  message: string;
}

export type PilotCategory =
  | "Momentum"
  | "Mean Reversion"
  | "Factor"
  | "Blend"
  | "Macro"
  | "Risk"
  | "Sentiment"
  | "Forecast";

/**
 * Honest, PBO/DSR-gated backtest headline from reports/<id>_validation_summary.json.
 * `deployable` is `null` (not `false`) for a Pilot with no backtest yet at all
 * (`pilots/performance.py::pilot_headline` — cold start, same honesty class as
 * the other four fields) — distinct from a real backtest that failed a gate
 * (`false`). Treat both as "not deployable" for display; don't conflate them
 * with a strict `=== false` check.
 */
export interface Headline {
  sharpe: number | null;
  dsr: number | null;
  pbo: number | null;
  max_drawdown: number | null; // fraction, e.g. 0.18 = 18%
  deployable: boolean | null;
  stress_gate_passed?: boolean | null;
}

/** GET /pilots — marketplace list item. */
export interface PilotSummary {
  id: string;
  name: string;
  category: PilotCategory;
  description: string;
  headline: Headline;
  holdings_count: number;
  top_holdings: Holding[];
  aum_proxy: number; // derived from follows.json (honest, local)
  followers_proxy: number;
  long_only: boolean;
}

export interface Holding {
  symbol: string;
  name: string;
  sector: string;
  weight: number; // normalized target weight, fraction summing to ~1
  score: number; // blended signal score
  price: number | null; // null when no live quote
  action: string | null; // null when advisory data isn't available for this symbol
  buy_range: string | null;
  sell_range: string | null;
  conviction: number | null;
  /**
   * ML meta-labeler gate output (roughly [0,1]) this holding's position
   * sizing was scaled by. `0` is a real, meaningful value (a hard gate can
   * zero it); `null` means it wasn't computed this cycle — never a
   * fabricated default.
   */
  meta_label_composite: number | null;
}

export interface SectorSlice {
  sector: string;
  weight: number; // fraction
}

export type TradeSide = "ENTER" | "EXIT" | "REWEIGHT";

export interface PilotTrade {
  date: string; // ISO date
  symbol: string;
  side: TradeSide;
  weight_delta: number; // signed change in target weight
  sector?: string;
}

/**
 * `GET /pilots/{id}`'s news-coverage summary — how much archived headline
 * sentiment backs this Pilot's `news_catalyst`-weighted holdings. Not every
 * Pilot uses the news-catalyst signal, so this is `null` (never a fabricated
 * zero) for a Pilot whose strategy doesn't weight news at all — the same
 * generic, not-special-cased treatment the real backend applies to any Pilot.
 */
export interface NewsCoverage {
  archived_score_count: number;
  headline_volume_7d: number;
  universe_score_distribution: { positive: number; neutral: number; negative: number };
}

/** GET /pilots/{id} — full detail. */
export interface PilotDetail extends PilotSummary {
  holdings: Holding[];
  sector_allocation: SectorSlice[];
  recent_trades: PilotTrade[];
  as_of: string | null; // ISO timestamp of the snapshot the holdings came from
  news_coverage: NewsCoverage | null;
}

/** POST /pilots/{id}/simulate request body — hypothetical allocation size. */
export interface PilotSimulationRequest {
  allocation_amount: number;
}

/**
 * POST /pilots/{id}/simulate result — "what if I allocated $X to this
 * Pilot" projection. `current`/`projected` are genuinely DIFFERENT numbers
 * computed per-Pilot and per-allocation-size (the specific fabrication bug
 * this feature rebuild exists to fix was a mock that returned the identical
 * delta for every Pilot). `heat_pct_projected` is always `null` — projecting
 * portfolio-wide heat requires the full live portfolio context this
 * endpoint doesn't have, so it is never guessed. `reason` is non-null only
 * when the projection is degraded (e.g. no backtest series for this Pilot).
 */
export interface PilotSimulationResult {
  pilot_id: string;
  current: { sharpe_ratio: number | null; max_drawdown: number | null };
  projected: { sharpe_ratio: number | null; max_drawdown: number | null };
  heat_pct_current: number | null;
  heat_pct_projected: null;
  coverage: { symbols_covered: number; symbols_total: number };
  reason: string | null;
}

export type PerfRange = "1W" | "1M" | "3M" | "6M" | "1Y" | "2Y";

export interface CurvePoint {
  date: string; // ISO date
  value: number; // indexed equity (base 100) or cumulative return
}

/** GET /pilots/{id}/performance — metrics + curve|null (never fabricated). */
export interface PerformanceResponse {
  range: PerfRange;
  // null when the Pilot has no validation summary at all (`pilots/performance.py`
  // — the same cold-start case that leaves `curve`/`reason` unavailable too).
  metrics: Headline | null;
  curve: CurvePoint[] | null;
  benchmark: CurvePoint[] | null;
  // SEPARATE, explicitly-labeled SPY (broad-market) overlay — distinct from
  // `benchmark` (the strategy's own underlying). null when SPY was unavailable
  // or the underlying already IS SPY (redundant); never fabricated.
  macro_benchmark: CurvePoint[] | null;
  reason?: string; // present when curve is null ("no backtest series yet")
}

/** GET /portfolio — serialized AccountSnapshot. */
export interface PortfolioPositionView {
  symbol: string;
  qty: number;
  avg_cost: number;
  current_price: number | null;
  market_value: number | null;
  unrealized_pl: number | null;
  unrealized_pl_pct: number | null;
  name?: string;
}

export interface Portfolio {
  total_equity: number;
  buying_power: number;
  total_unrealized_pl: number;
  total_dividends: number;
  position_count: number;
  positions: PortfolioPositionView[];
  fetched_at: string | null;
  source: string; // "db" | "cache" | "live" | "unavailable"
  // Freshness fields emitted by GET /portfolio (api/pilots_api.py). Optional so
  // the mock (which omits them) still satisfies the type.
  is_stale?: boolean;
  age_hours?: number;
}

/** Execution mode surfaced to the UI so a follow is never presented as executed. */
export type ExecutionMode = "off" | "review" | "paper" | "live";

/** One planned BUY intent in a gated follow queue (preview only, never placed). */
export interface PlannedIntent {
  symbol: string;
  side: "BUY";
  target_notional: number;
  weight: number;
  conviction: number;
  allow_place: boolean; // structurally false unless mode==live & gates clear
}

export interface Follow {
  pilot_id: string;
  amount: number;
  created_at: string;
  updated_at: string;
  // Real vocabulary per `pilots/follows_store.py` (STATUS_ACTIVE/STATUS_CANCELLED):
  // "active" | "cancelled". GET /follows only ever returns "active" rows
  // (FollowsStore.list_active()) — "cancelled" is retained server-side but
  // filtered out of this list.
  status: string; // "active" | "cancelled"
}

/** POST /pilots/{id}/follow response. */
export interface FollowResult {
  follow: Follow;
  planned_intents: PlannedIntent[];
  mode: ExecutionMode;
  queue_written: boolean;
  notional_cap: number; // ROBINHOOD_MAX_NOTIONAL_PER_ORDER
  min_amount: number;
  sizing_path?: string;
  kelly_weight?: number;
  notice: string; // human-readable gating notice
}

/** GET /symbols/{ticker} — one row of the reverse cross-link "which Pilots hold this symbol." */
export interface SymbolHeldBy {
  pilot_id: string;
  name: string;
  weight: number; // this symbol's normalized target weight within that Pilot (fraction)
}

/**
 * One row of the tracked-symbol universe (`GET /universe`) that powers the
 * symbol autocomplete. `action` is the latest holding-aware advisory action
 * (falling back to the raw signal action) — `null` when the snapshot carries
 * neither (NEVER fabricated). It only decorates the suggestion; every `symbol`
 * resolves to a real `GET /symbols/{ticker}` detail page.
 */
export interface UniverseSymbol {
  symbol: string;
  action: string | null;
}

export interface UniverseResponse {
  symbols: UniverseSymbol[];
}

/**
 * One `GET /data/symbol-search` result row — mirrors
 * `data.fmp_screener.search_symbols`'s reshaped dict exactly. Independent of
 * the platform's tracked watchlist/pipeline universe (backed by FMP's real
 * `/search-name`/`/search-symbol`). Any field FMP didn't return is `null`,
 * never fabricated.
 */
export interface SymbolSearchResult {
  symbol: string;
  name: string | null;
  currency: string | null;
  exchange: string | null;
  exchange_full_name: string | null;
}

export interface SymbolSearchResponse {
  query: string;
  results: SymbolSearchResult[];
  reason: string | null;
}

/** All fields optional — only non-omitted ones are sent to `GET /data/screener`. */
export interface ScreenerFilters {
  sector?: string;
  industry?: string;
  marketCapMoreThan?: number;
  marketCapLowerThan?: number;
  priceMoreThan?: number;
  priceLowerThan?: number;
  betaMoreThan?: number;
  betaLowerThan?: number;
  dividendMoreThan?: number;
  dividendLowerThan?: number;
  volumeMoreThan?: number;
  exchange?: string;
  country?: string;
  isActivelyTrading?: boolean;
  excludeFunds?: boolean;
  limit?: number;
  page?: number;
}

/**
 * One `GET /data/screener` result row — mirrors
 * `data.fmp_screener.screen_companies`'s reshaped dict exactly (backed by
 * FMP's real `/search-company-screener`). Any field FMP didn't return is
 * `null`, never fabricated.
 */
export interface ScreenerResult {
  symbol: string;
  company_name: string | null;
  market_cap: number | null;
  sector: string | null;
  industry: string | null;
  beta: number | null;
  price: number | null;
  last_annual_dividend: number | null;
  volume: number | null;
  exchange: string | null;
  exchange_short_name: string | null;
  country: string | null;
  is_etf: boolean | null;
  is_fund: boolean | null;
  is_actively_trading: boolean | null;
}

export interface ScreenerResultsResponse {
  results: ScreenerResult[];
  reason: string | null;
}

/** `GET /data/screener/filters`'s sector/industry enum lists for the filter dropdowns. */
export interface ScreenerFilterOptions {
  sectors: string[];
  industries: string[];
}

/**
 * `POST /data/backfill/{symbol}` result — mirrors `api.data_api.trigger_symbol_backfill`'s
 * response exactly. A "spot data download": forces a full bar backfill for
 * one arbitrary symbol into local storage, rather than waiting for it to
 * happen lazily as a side effect of some other read. `status: "no_data"`
 * (never a fabricated `"ok"`) covers an unknown ticker, a provider outage,
 * or any other unexpected failure — `rows_persisted` is `0` and
 * `last_bar_date` is `null` in that case (CONSTRAINT #4).
 */
export interface SymbolBackfillResult {
  symbol: string;
  rows_persisted: number;
  last_bar_date: string | null;
  status: "ok" | "no_data";
}

/** One coverage-status bucket — mirrors data.portfolio_sync.CoverageStatus's values exactly. */
export type CoverageStatus = "full" | "stale" | "quotes_only" | "equity_only" | "uncovered" | "unknown";

/**
 * One entry of `GET /data/sync-report`'s `symbols` map — mirrors
 * `data.portfolio_sync.SymbolStatus.to_dict()` exactly. `quantity` is a real
 * `0.0` for a genuinely un-held symbol (not a null-worthy "unknown"); every
 * other numeric leaf is `null` when the live probe didn't resolve it (e.g. no
 * quote for an EQUITY_ONLY symbol) — never a fabricated 0.0 (CONSTRAINT #4).
 */
export interface SyncReportSymbol {
  symbol: string;
  coverage: CoverageStatus;
  held: boolean;
  quantity: number;
  avg_cost: number | null;
  current_price: number | null;
  cost_basis_delta_per_share: number | null;
  market_value: number | null;
  is_stale_quote: boolean;
  quote_source: string;
  has_fundamentals: boolean;
  forecast_available: boolean;
  watchlists: string[];
  diagnostic: string;
  /**
   * How many of this symbol's most recent per-cycle ratings
   * (`rating.symbol_rating_store.SymbolRatingStore.get_consecutive_bad_cycles`)
   * were consecutively BAD. Optional: older cached responses / the mock API
   * may not always populate this — `undefined`/`null` means "no rating
   * history available", not zero.
   */
  rating_consecutive_bad_cycles?: number | null;
  /**
   * True when the platform's symbol-rating auto-drop rule would currently
   * exclude this symbol (non-held, consecutive-BAD streak at/above
   * `SYMBOL_RATING_DROP_THRESHOLD_CYCLES`) — mirrors
   * `SymbolRatingStore.get_excluded_symbols`. Optional for the same reason
   * as `rating_consecutive_bad_cycles` above.
   */
  rating_excluded?: boolean;
}

/**
 * GET /data/sync-report — live portfolio & watchlist coverage-reconciliation
 * report (holdings ∪ Robinhood/file watchlists), computed fresh on every call
 * from `data.portfolio_sync.build_sync_report` — NOT read from a GUI-only
 * cache file, so this also works on a headless deploy where nobody has ever
 * run `streamlit run gui/app.py`. `symbols` is keyed by ticker; an empty map
 * is a genuine "nothing tracked yet" state (no held positions, no Robinhood/
 * file watchlists) — this live endpoint has no persisted-cache "cold start"
 * concept the way a GUI-cache reader would.
 */
export interface SyncReportResponse {
  generated_at: string;
  positions: string[];
  watchlists: Record<string, string[]>;
  symbols: Record<string, SyncReportSymbol>;
  provider_source: string;
  fundamentals_source: string;
}

/**
 * POST /universe/{symbol}/reinclude — manual escape hatch that breaks a
 * symbol's consecutive-BAD rating streak (`rating.symbol_rating_store.
 * SymbolRatingStore.reinclude`), undoing an automated symbol-rating
 * exclusion. Never places an order; downstream buy eligibility still runs
 * through the platform's normal scoring/sizing/risk-gate pipeline.
 */
export interface SymbolReincludeResult {
  symbol: string;
  reincluded: boolean;
}

/**
 * One symbol's latest quote from `GET /data/quotes?symbols=...`
 * (`api/data_api.py`, backed by `data.market_data.CompositeProvider`).
 * `is_stale` is `true` on every yfinance-sourced quote by design (~15 min
 * delayed feed); `false` only for a real-time source (Alpaca). Every numeric
 * leaf is `null` when the provider didn't return it (NEVER a fabricated 0 —
 * CONSTRAINT #4).
 */
export interface Quote {
  symbol: string;
  price: number | null;
  bid: number | null;
  ask: number | null;
  timestamp: string | null; // ISO 8601 UTC
  is_stale: boolean;
  source: string;
}

/**
 * `GET /data/quotes` response: keyed by the (uppercased) requested symbol.
 * A symbol the provider couldn't resolve for ANY reason (rate-limited,
 * delisted, network error, ...) is simply OMITTED from this dict — the
 * endpoint dead-letters per-symbol rather than failing the whole request or
 * returning a placeholder row (CONSTRAINT #4). Callers must treat a missing
 * key as "unreachable", never assume success.
 */
export type QuotesResponse = Record<string, Quote>;

/**
 * GET /recommendations — the platform's current BUY picks from the latest
 * snapshot, ranked by conviction (then score). One clickable "here's what we'd
 * buy" row per pick. Every numeric leaf is `null` when the snapshot couldn't
 * compute it (NEVER a fabricated 0 — CONSTRAINT #4): `conviction` is a [0,1]
 * fraction, `score` the composite signal score, `price` the last close (a
 * non-positive placeholder is nulled server-side), `buy_range` a pre-formatted
 * display string (e.g. "Buy Zone: $210.00 - $222.00").
 */
export interface Recommendation {
  symbol: string;
  action: string | null;
  conviction: number | null;
  score: number | null;
  buy_range: string | null;
  sector: string | null;
  price: number | null;
}

export interface RecommendationsResponse {
  recommendations: Recommendation[];
  count: number;
  /** Snapshot timestamp the picks reflect; `null` on a cold start. */
  as_of: string | null;
  /** Honest "nothing yet" note when `recommendations` is empty, else `null`. */
  reason: string | null;
}

/**
 * GET /data/universe — the operator's raw configured ticker universe
 * (`settings.DEFAULT_TICKERS`) from the data API. A plain string list, distinct
 * from the pilots `UniverseResponse` (which decorates each symbol with an
 * advisory action for autocomplete). This is what the Data Explorer's
 * add/remove control reads and PUTs back.
 */
export interface UniverseListResponse {
  symbols: string[];
  count: number;
}

/**
 * GET /thresholds — deployability-gate and position-sizing thresholds,
 * live-imported on the backend from `validation.thresholds` / `settings`
 * (never re-typed as literals there). Powers the education panels'
 * (`TabGuide`/`helpContent.ts`) live-value glossary entries so the PWA quotes
 * the SAME numbers the validation harness actually enforces. Config constants,
 * not persisted state — always available, no honest-empty case.
 */
export interface Thresholds {
  pbo_max: number;
  dsr_min: number;
  net_sharpe_min: number;
  max_drawdown_max: number;
  stress_max_drawdown: number;
  kelly_fraction: number;
  kelly_cap: number;
  /** Live settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER — USD cap per gated queue order (0 = unset). */
  robinhood_max_notional_per_order: number;
  /** Live settings.FOLLOW_MIN_AMOUNT — USD floor the Follow modal enforces. */
  follow_min_amount: number;
  /** Live settings.AGENTIC_MAX_CANDIDATES — cap on GET /agentic/discovery's candidate list. */
  agentic_max_candidates: number;
  /**
   * Live gui.help_content.MODEL_RETRAIN_WINDOW_DAYS — the same constant
   * ml.meta_labeling.MetaLabeler.needs_retrain() uses. Display-text only: the
   * Models screen's per-model `needs_retrain`/`age_days` flag (GET /models)
   * is already computed server-side against this same constant — this key
   * exists so static explainer copy can quote the window without a literal.
   */
  retrain_window_days: number;
}

/**
 * GET /symbols/{ticker} — grouped per-symbol data from the persisted state
 * snapshot, plus the reverse cross-link. Every factor/risk leaf the active
 * snapshot writer could not compute is `null` (NEVER a fabricated 0) so the UI
 * renders "—". `ranges.*` are pre-formatted display strings (e.g.
 * "Buy Zone: $210.00 - $222.00"), not tuples. `score_components` is nested in
 * `factors`. Single point of reconciliation against the live JSON.
 */
export interface SymbolDetail {
  symbol: string;
  as_of: string | null;
  reason: string | null;
  identity: {
    sector: string | null;
    price: number | null;
    action: string | null;
    shares: number | null;
  };
  advisory: {
    action: string | null;
    conviction: number | null;
    position_pct: number | null;
    rationale: string | null;
    kelly_target: number | null;
    score: number | null;
  };
  factors: {
    value_z: number | null;
    quality_z: number | null;
    lowvol_z: number | null;
    size_z: number | null;
    multifactor_composite: number | null;
    xsec_12_1m: number | null;
    xsec_momentum_rank: number | null;
    score_components: Record<string, number> | null;
  };
  ranges: {
    buy_range: string | null;
    sell_range: string | null;
  };
  risk: {
    news_sentiment: number | null;
    covar_proxy: number | null;
    realized_slippage: number | null;
    mfe: number | null;
    mae: number | null;
    edge_ratio: number | null;
    hmm_risk_on: number | null;
    macro_status: string | null;
  };
  /**
   * Position-sizing decomposition — Kelly Target before vs. after the HMM
   * regime multiplier + meta-label composite were applied (ports
   * `gui/panels/strategy_matrix.py::_render_regime_multiplier_impact`).
   * `0` is a real, meaningful value for every leaf here (e.g. a MetaLabeler
   * hard-gating a signal to `meta_label_composite: 0`) — never treat it as
   * falsy/absent. `null` means the active snapshot writer didn't compute it.
   */
  sizing: {
    kelly_target_pre_regime: number | null;
    kelly_target_post_regime: number | null;
    regime_multiplier: number | null;
    meta_label_composite: number | null;
    max_position_weight: number;
  };
  held_by_pilots: SymbolHeldBy[];
}

/**
 * GET /symbols/compare — one row of the symbol-vs-symbol comparison, the API
 * counterpart of the legacy Streamlit Strategy Matrix's "Symbol Comparison"
 * table (`gui/panels/strategy_matrix.py::_render_symbol_comparison`). Every
 * numeric/string leaf is `null` when the active snapshot writer never
 * computed it — NEVER a fabricated default (CONSTRAINT #4).
 * `meta_label_composite`/`regime_multiplier` are persisted by BOTH snapshot
 * writers (advisory and orchestrator), but `null` is still expected/honest —
 * not a bug — whenever the strategy engine didn't produce a value for that
 * symbol this cycle.
 *
 * `found: false` means the requested ticker isn't in the latest snapshot
 * (typo, or it rolled out of the tracked universe this cycle) — every other
 * leaf is `null` and `reason` explains why. This is NOT an error; the row
 * still renders (with dashes) alongside the symbols that did resolve.
 */
export interface SymbolCompareRow {
  symbol: string;
  found: boolean;
  reason: string | null;
  score: number | null;
  action: string | null;
  kelly_target: number | null;
  conviction: number | null;
  garch_vol: number | null;
  meta_label_composite: number | null;
  regime_multiplier: number | null;
  score_components: Record<string, number> | null;
  /**
   * Sector name straight off the same signals[] entry (mirrors
   * SymbolDetail's identity.sector). `sector_pe`/`sector_change_pct` are
   * attached from ONE bulk HistoricalStore.get_sector_snapshots() call per
   * request (never per symbol) -- diagnostic valuation CONTEXT, distinct
   * from the symbol's own individual P/E (fetched client-side per symbol
   * from GET /data/fundamentals/{symbol} -- see SymbolComparison.tsx).
   * `null` when the symbol has no sector, the sector has no snapshot (e.g.
   * FMP_SECTOR_SNAPSHOT_ENABLED is off), or the bulk fetch itself failed --
   * never a fabricated/neighboring-sector value (CONSTRAINT #4).
   */
  sector: string | null;
  sector_pe: number | null;
  sector_change_pct: number | null;
}

/**
 * GET /symbols/compare — 2-5 symbols side by side. `as_of` is the snapshot
 * timestamp the comparison reflects; `null` on a cold start (no snapshot
 * yet), in which case every row in `symbols` is honestly `found: false`.
 * `modules` is the sorted union of every FOUND symbol's `score_components`
 * keys — the shared x-axis for a grouped bar chart so a symbol whose
 * aggregator skipped a module this cycle still lines up against the others.
 */
export interface SymbolCompareResponse {
  as_of: string | null;
  symbols: SymbolCompareRow[];
  modules: string[];
}

/** GET /brokerage/status — whether local RH credentials are configured. */
export interface BrokerageStatus {
  connected: boolean;
  has_account_snapshot: boolean;
  /** Read-only mirror of the live `settings.ROBINHOOD_AUTO_REFRESH_ENABLED`
   *  server gate -- the same value `data/robinhood_portfolio.py` branches on
   *  for its Tier-3 login. No write path: this is `.env`-only, applies on
   *  next daemon restart. */
  auto_refresh_enabled: boolean;
}

/**
 * POST /brokerage/connect body. Sent only over a loopback connection to the
 * operator's own local backend — see api/pilots_api.py's module docstring for
 * the three independent server-side gates (BROKERAGE_CONNECT_ENABLED,
 * FOLLOW_API_TOKEN, loopback-only). Never persisted client-side.
 *
 * No `mfa_code` — Robinhood login is device-approval PUSH now (the operator
 * taps "approve" in the Robinhood mobile app), not a typed 6-digit TOTP code.
 * The backend confirms/denies this asynchronously; see `BrokerageLoginJob`.
 */
export interface BrokerageConnectRequest {
  username: string;
  password: string;
}

/** POST /brokerage/disconnect response. */
export interface BrokerageDisconnectResult {
  connected: boolean;
}

/** `POST /brokerage/{connect,refresh}` and `GET
 * /brokerage/login/status/{job_id}` all report the SAME job shape. */
export type BrokerageLoginMode = "connect" | "refresh";

/**
 * "running" is the only state where `phase` is meaningful; only "failed" |
 * "timeout" | "cancelled" ever carry a non-null `error_code`.
 */
export type BrokerageLoginState = "running" | "succeeded" | "failed" | "timeout" | "cancelled";

/** Only meaningful while `state === "running"`. */
export type BrokerageLoginPhase =
  | "starting"
  | "authenticating"
  | "awaiting_approval"
  | "verifying"
  | "fetching_snapshot"
  | "fetching_orders"
  | "done";

export type BrokerageLoginErrorCode =
  | null
  | "no_credentials"
  | "challenge_unsupported"
  | "auth_failed"
  | "child_start_failed"
  | "timeout"
  | "cancelled";

/**
 * `POST /brokerage/connect` / `POST /brokerage/refresh` (202) and `GET
 * /brokerage/login/status/{job_id}` all return this shape — an async
 * device-approval-push login job, polled to completion rather than verified
 * synchronously (the operator approves in the Robinhood mobile app; there is
 * no 6-digit code to submit). `seconds_remaining` counts down from the
 * server's own login deadline and is RE-SYNCED on every poll — never a
 * free-running client-side timer, so a wedged backend can't show a
 * plausible-looking countdown for a job that isn't actually progressing.
 *
 * `connected` is true only once RH_USERNAME/RH_PASSWORD have actually been
 * persisted server-side (only ever happens after a "connect" job reaches
 * `state: "succeeded"`); `has_account_snapshot` is independent of this job
 * (whether an account snapshot already exists in the DB at all).
 *
 * Honesty note: a "timeout" is NEVER presented as "you denied the login" —
 * the backend's login library has no separate code path for a denied push
 * vs. one simply never seen, so this type (and every UI reading it) must not
 * invent that distinction either.
 */
export interface BrokerageLoginJob {
  job_id: string;
  mode: BrokerageLoginMode;
  state: BrokerageLoginState;
  phase: BrokerageLoginPhase;
  error_code: BrokerageLoginErrorCode;
  seconds_remaining: number;
  connected: boolean;
  has_account_snapshot: boolean;
}

/**
 * POST /brokerage/login/cancel/{job_id} response — the job shape plus an
 * honest `cancelled` flag (`false` if the kill could not be confirmed
 * server-side, NOT assumed true just because the request itself succeeded).
 */
export interface BrokerageLoginCancelResult extends BrokerageLoginJob {
  cancelled: boolean;
}

/**
 * POST /brokerage/refresh response — starts an async live Robinhood
 * re-login + account-snapshot fetch bypassing the daily cache (the webapp
 * equivalent of `python3 main.py --refresh-account` / the Streamlit GUI's
 * "Force fresh login" checkbox), polled the same way `connectBrokerage`'s
 * job is (see `BrokerageLoginJob`) — no separate synchronous `Portfolio`
 * result anymore; re-fetch `GET /portfolio` once the job succeeds if the
 * refreshed figures themselves are needed.
 */
export type BrokerageRefreshResult = BrokerageLoginJob;

// ---------------------------------------------------------------------------
// GET /llm/status — LLM provider configuration + last-real-call telemetry.
// Never probes a provider; never carries a key, prefix, or fingerprint. A null
// verdict means "no call has been made with the current key yet" (the expected
// state with LLM commentary off by default), NOT "broken". All copy the UI
// renders from this is past-tense and timestamped.
// ---------------------------------------------------------------------------

export type LlmProviderName = "claude" | "gemini" | "openai";
export type LlmErrorKind =
  | "auth"
  | "rate_limit"
  | "network"
  | "timeout"
  | "schema"
  | "unknown";
/**
 * "last_call" — a current, claimable verdict.
 * "none"      — no call ever recorded for this provider.
 * "key_rotated" — a verdict exists but for a DIFFERENT key; every field is
 *   nulled (it isn't about the current key at all).
 * "expired"   — a TRANSIENT verdict older than the age bound; fields are
 *   RETAINED (same key, just old) and rendered muted, not as a current claim.
 */
export type LlmTelemetrySource = "last_call" | "none" | "key_rotated" | "expired";
export type LlmCapabilityStatus =
  | "ready"
  | "disabled"
  | "missing_key"
  | "invalid_key"
  | "not_built";

/** One provider's last-real-call verdict. All fields null when source != "last_call". */
export interface LlmProviderTelemetry {
  provider: LlmProviderName;
  ok: boolean | null;
  error_kind: LlmErrorKind | null;
  exception_type: string | null;
  http_status: number | null;
  checked_at: string | null;
  age_seconds: number | null;
  source: LlmTelemetrySource;
}

/** One AI capability's config + readiness row. */
export interface LlmCapabilityRow {
  key: string;
  label: string;
  trigger: "on_demand" | "scheduled";
  /** The .env key a toggle write (PUT /llm/setting) flips; null = read-only row. */
  toggle_key: string | null;
  /**
   * The .env key a provider-selector write (PUT /llm/setting) sets, when this
   * capability supports flexible per-job routing (either Claude or Gemini may
   * serve rationale/alert commentary, OpenAI or Gemini may serve Opal). null =
   * this capability has a fixed provider (Gravity runner, chart vision).
   */
  provider_selector_setting: string | null;
  provider_keys: string[];
  active_provider: LlmProviderName | null;
  /** Non-null ⇒ this provider's key was rejected on the last REAL call. */
  invalid_provider: LlmProviderName | null;
  enabled: boolean;
  key_present: boolean;
  built: boolean;
  status: LlmCapabilityStatus;
}

/** GET /llm/status full response. `attention` is server-computed. */
export interface LlmStatus {
  capabilities: LlmCapabilityRow[];
  capabilities_source: string;
  providers: Record<LlmProviderName, LlmProviderTelemetry>;
  providers_source: string;
  telemetry_note: string;
  attention: boolean;
  attention_reason: "invalid_key" | "missing_key" | null;
  /** Tracks LLM_WRITES_ENABLED -- false means PUT /llm/setting is disabled. */
  writable: boolean;
  writable_note: string;
}

/** Body for PUT /llm/setting. `key` is a toggle_key (bool) or a
 * provider_selector_setting (string provider name). */
export interface LlmSettingUpdate {
  key: string;
  value: boolean | string;
}

/** PUT /llm/setting result. `value` echoes the request body. */
export interface LlmSettingUpdateResult {
  written: string[];
  value: boolean | string;
  applies: "next_daemon_restart";
  note: string;
}

/** One AI model provider entry from GET /data/ai/models. */
export interface AiModelProvider {
  id: string;
  name: string;
  available: boolean;
  default_model: string;
  models: string[];
  base_url?: string;
}

/** GET /data/ai/models response. */
export interface AiModelsResponse {
  default_provider: string;
  default_model: string | null;
  providers: AiModelProvider[];
}

/** One message entry in chat history. */
export interface ChatHistoryMessage {
  role: 'user' | 'model' | 'assistant';
  content: string;
}

/** Request payload for POST /api/chat.
 *
 * Deliberately has NO client-suppliable base-URL field: the backend's
 * "local" provider always dispatches to the operator's own
 * settings.LOCAL_LLM_BASE_URL, never anything from the request body --
 * see api/data_api.py::ChatMessageRequest's comment for why a
 * client-controlled outbound URL there would be an SSRF/credential-relay
 * risk.
 */
export interface ChatMessageRequest {
  message: string;
  history?: ChatHistoryMessage[];
  context?: string;
  provider?: string;
  model?: string;
}

// ---------------------------------------------------------------------------
// Backend analytics surfaces (zero-PWA-presence gap) — one interface per
// api/pilots_api.py endpoint added in this effort. Every leaf the backend
// cannot compute is `null` (NEVER 0) so the UI renders "—".
// ---------------------------------------------------------------------------

/** GET /portfolio/realized — realized broker P&L (FIFO round-trips). */
export interface RealizedSummary {
  n_trades: number;
  total_realized_pnl: number; // genuine sum (0 over zero trades)
  win_rate: number | null; // fraction, null when no trades
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null; // null when no losing trades
  avg_return_pct: number | null;
  avg_holding_days: number | null;
  best_trade_pnl: number | null;
  worst_trade_pnl: number | null;
  gross_profit: number;
  gross_loss: number;
}

export interface RealizedTrade {
  symbol: string;
  quantity: number | null;
  entry_ts: string | null;
  exit_ts: string | null;
  entry_price: number | null;
  exit_price: number | null;
  realized_pnl: number | null;
  return_pct: number | null;
  holding_days: number | null;
}

export interface RealizedPerformance {
  summary: RealizedSummary;
  trades: RealizedTrade[];
  n_fills: number;
  available: boolean; // false when nothing is cached yet (honest cold-start)
}

/**
 * GET /portfolio/trade-history — full, paginated broker closed-trade
 * history (durable store, distinct from RealizedPerformance above, which is
 * cache-only and capped at 100 trades for the Portfolio screen's summary
 * panel). `summary` is computed over the FULL filtered history, not just
 * the returned page, so paging never changes the reported win rate /
 * profit factor. `available` is false only when nothing has been ingested
 * yet (an honest cold-start, not "you have no closed trades").
 */
export interface TradeHistoryPage {
  trades: RealizedTrade[];
  summary: RealizedSummary;
  total: number;
  limit: number;
  offset: number;
  symbols: string[]; // every distinct symbol with a persisted fill, for a filter control
  available: boolean;
  source: "durable_store";
  last_ingested_at: string | null;
}

/**
 * GET /portfolio/attribution — factor exposure section.
 * Position-size-weighted average Value/Quality/LowVol/Size/Composite z-score
 * (`signals/multifactor.py`) across HELD symbols matched in the latest
 * pipeline snapshot. A factor is `null` when zero matched holdings carry it
 * (never a fabricated 0 — CONSTRAINT #4).
 */
export interface FactorExposure {
  value_z: number | null;
  quality_z: number | null;
  lowvol_z: number | null;
  size_z: number | null;
  multifactor_composite: number | null;
}

export interface FactorExposureCoverage {
  held_count: number;
  matched_count: number;
  // Fraction of TOTAL held market value the exposure numbers actually
  // describe; null when total held value is zero/unknown.
  matched_value_pct: number | null;
  // Held symbols with no entry in the latest pipeline snapshot -- contribute
  // nothing to `exposures` (never zero-filled).
  unmatched_symbols: string[];
}

export interface PortfolioFactorExposure {
  as_of: string | null;
  exposures: FactorExposure;
  coverage: FactorExposureCoverage;
  reason: string | null; // e.g. "no held positions" / "no pipeline snapshot yet"
}

/**
 * One correlation cluster of held symbols (GET /portfolio/attribution).
 * `cluster_id === 0` / `insufficient_history === true` is
 * `research_engine.compute_correlation_clusters`'s "not enough return history"
 * bucket -- NOT a real correlation grouping; render it distinctly.
 */
export interface CorrelationCluster {
  cluster_id: number;
  symbols: string[];
  n_symbols: number;
  avg_intra_corr: number | null; // null for a singleton cluster (no intra pair)
  weight_pct: number | null; // fraction of total held market value in this cluster
  insufficient_history: boolean;
}

export interface PortfolioCorrelationClusters {
  clusters: CorrelationCluster[];
  lookback_days: number;
  reason: string | null; // e.g. "no held positions" / "no return history available..."
}

/** GET /portfolio/attribution — combined factor exposure + correlation clusters. */
export interface PortfolioAttribution {
  as_of: string | null;
  factor_exposure: PortfolioFactorExposure;
  correlation_clusters: PortfolioCorrelationClusters;
}

/**
 * POST /portfolio/attribution/brinson-fachler — one row of the operator-typed
 * sector matrix. All four numeric fields are PERCENT (e.g. `28.0` for 28%),
 * matching what an operator naturally types into a form -- the backend
 * (`pilots/brinson.py::build_brinson_fachler_frames`) does the `/100`
 * conversion to the fractions the engine's math needs. This is a MANUAL,
 * operator-entered matrix, not auto-derived from real holdings -- there is no
 * point-in-time sector-level benchmark return data anywhere in this platform.
 */
export interface BrinsonFachlerRow {
  sector: string;
  portfolio_weight_pct: number;
  portfolio_return_pct: number;
  benchmark_weight_pct: number;
  benchmark_return_pct: number;
}

/** One sector's Allocation/Selection/Interaction decomposition. Weights and
 * returns here are FRACTIONS (engine-native units), not percent -- distinct
 * from the request row's percent fields above. */
export interface BrinsonFachlerSectorDetail {
  weight_p: number;
  weight_b: number;
  return_p: number;
  return_b: number;
  allocation_effect: number;
  selection_effect: number;
  interaction_effect: number;
  total_attribution: number;
}

/**
 * Result of POST /portfolio/attribution/brinson-fachler. Field names mirror
 * `evaluation_engine.py::_calculate_brinson_fachler_compat`'s dict verbatim
 * (including the spaced keys) so the wire shape needs no renaming layer.
 * `validation_warnings` is server-computed but purely informational (weights
 * not summing to ~100%, negative weights, all-zero matrix) -- it never blocks
 * computation, only a structurally empty/blank-sector matrix does (422).
 */
export interface BrinsonFachlerResult {
  "Portfolio Return": number;
  "Benchmark Return": number;
  "Active Return": number;
  "Allocation Effect": number;
  "Selection Effect": number;
  "Interaction Effect": number;
  "Attribution Sum": number;
  "Sector Details": Record<string, BrinsonFachlerSectorDetail>;
  validation_warnings: string[];
}

/** GET /alerts — tail of the structured alert JSONL. */
export interface AlertEntry {
  timestamp: string | null;
  level: string | null; // INFO | WARNING | CRITICAL | ...
  message: string | null;
  extra: Record<string, unknown> | null;
}

export interface AlertsFeed {
  entries: AlertEntry[];
  reason: string | null; // present when entries is empty (honest why)
}

/** GET /symbols/{ticker}/forecast — forecast reliability + skill weights. */
export interface ReliabilityBin {
  model_name: string;
  horizon_days: number;
  bin_center: number | null;
  mean_pct_error: number | null; // null when too few samples in the bin
  count: number;
}

// Note: NOT the codebase's other "MAE" (Maximum Adverse Excursion, a trade
// -quality metric in Calibration.tsx/SymbolDetail.tsx's own StatRow) — this
// is the forecast error metric. Never render the bare acronym "MAE" for this
// field in the UI; spell out "mean absolute error" to avoid colliding with
// that other meaning on the same screen.
export interface ForecastModelError {
  model_name: string;
  n: number; // completed observations this figure is computed over
  rmse: number | null; // null only if the underlying aggregate wasn't finite
  mae: number | null;
}

export interface ForecastSkill {
  symbol: string;
  horizon_days: number;
  reliability_curve: ReliabilityBin[];
  skill_weights: Record<string, number>; // {model: normalized inverse-RMSE weight}
  error_by_model: ForecastModelError[]; // sorted by rmse ascending (best first)
  pending: number;
  completed: number;
  reason: string | null;
}

/**
 * GET /sector/selection — semantic Related Sector Selection ranking for one
 * target symbol: cosine similarity (SBERT) x Sector Heat Factor (SHF) per
 * candidate sector, ranked descending by `correlation_coefficient`.
 *
 * Every numeric field is `null`, never a fabricated value, whenever the
 * backend couldn't honestly compute it (CONSTRAINT #4) — `degraded_reason`
 * explains why: `"no_embedder"` (no similarity backend configured/available),
 * `"no_target_description"` / `"no_sector_description"` (nothing to embed),
 * `"embedding_failed"`, or the Sector Heat Factor side's
 * `"review_unavailable"` (investor-forum comment volume has never been
 * observed — SHF degrades to news-only volume) / `"no_volume_observed"`
 * (this sector's member tickers were never ingested at all — excluded from
 * ranking entirely). `rank`/`selected` are `null`/`false` for an unranked
 * row (its `correlation_coefficient` is `null`).
 */
export interface SectorSelectionRow {
  sector: string;
  cosine_similarity: number | null;
  ingestion_volume: number | null; // numNews + Review (pre-SHF), whatever volume WAS observed
  sector_heat_factor: number | null;
  correlation_coefficient: number | null;
  rank: number | null;
  selected: boolean;
  degraded_reason: string | null;
  /**
   * Dated FMP sector P/E + 1-day-change snapshot (data/historical_store.py's
   * get_sector_snapshots, populated only when
   * settings.FMP_SECTOR_SNAPSHOT_ENABLED) -- pure valuation-context
   * decoration bulk-attached by sector name, UNRELATED to the semantic
   * cosine-similarity ranking above (never feeds correlation_coefficient or
   * rank). `null` when this sector has no snapshot (feed disabled, or this
   * sector name wasn't covered) -- never a fabricated/neighboring value
   * (CONSTRAINT #4). `change_pct` is a FRACTION (0.0123 = +1.23%, not 1.23).
   */
  pe?: number | null;
  change_pct?: number | null;
}

export interface SectorSelectionView {
  target_symbol: string;
  as_of: string | null; // trading-day label (YYYY-MM-DD) the ranking was computed for
  top_n: number; // echoes the request's `n` -- NOT necessarily how many rows are `selected`
  rows: SectorSelectionRow[];
  embedder: string | null; // "sbert" | "openai" | "none" -- provenance of the similarity term
  pooling: string | null; // "max" | "mean" -- only meaningful when embedder === "sbert"
  // Honest explanation when `rows` is empty (nothing computed for this
  // symbol yet); null on a normal hit. NOT a 404 -- the symbol may simply
  // not have run through sector_selection_engine.py yet.
  reason: string | null;
}

/**
 * GET /symbols/{ticker}/rolling-beta — time-varying beta vs SPY
 * (Cov(returns, spy_returns) / Var(spy_returns) over a rolling window),
 * distinct from the single point-in-time static `Beta` figure elsewhere in the
 * platform. Computed on demand from HistoricalStore-cached daily bars
 * (pilots/rolling_beta.py); never fabricated/forward-filled (CONSTRAINT #4).
 */
export interface RollingBetaPoint {
  date: string; // ISO date (YYYY-MM-DD)
  beta: number;
}

export interface RollingBeta {
  symbol: string;
  window: number;
  series: RollingBetaPoint[];
  // Honest explanation when `series` is empty (insufficient cached history,
  // unknown symbol, no SPY history yet); null on a normal hit.
  reason: string | null;
}

/**
 * GET /models — ML model registry row (ml/registry.yaml).
 *
 * `age_days`/`needs_retrain` (webapp porting backlog rider 13b) are computed
 * server-side in `pilots/models.py` against the SAME
 * `gui.help_content.MODEL_RETRAIN_WINDOW_DAYS` constant `GET /thresholds`'
 * `retrain_window_days` surfaces for display text — never re-derive the flag
 * client-side from `trained_date` date math. Both are `null` when
 * `trained_date` itself is null/unparseable (CONSTRAINT #4: no fabricated
 * flag on a model with no dated training run).
 */
export interface ModelRow {
  name: string;
  role: string | null;
  trained_date: string | null;
  cpcv_dsr: number | null; // null for an un-validated model
  pbo: number | null;
  n_train: number | null;
  deployable: boolean | null;
  notes: string | null;
  age_days: number | null;
  needs_retrain: boolean | null;
  // Honest CPCV out-of-sample Sharpe/MaxDD (validation/metrics.py's
  // mean_oos_sharpe/mean_oos_max_dd -- the mean of each metric computed
  // independently per CPCV path). `cpcv_mean_oos_max_dd` is a POSITIVE
  // magnitude fraction (e.g. 0.28 = 28% drawdown), matching
  // validation/stress_scenarios.py::compute_max_drawdown's convention --
  // NOT the signed `PortfolioRiskMetrics.max_drawdown` convention used
  // elsewhere in this file. `null` for an un-validated model (never a
  // fabricated 0).
  cpcv_mean_oos_sharpe: number | null;
  cpcv_mean_oos_max_dd: number | null;
}

/**
 * One leg of a persisted options structure (technical_options_engine leg dict).
 * An Iron Condor carries 4 legs; a Covered Call carries 1. `Delta` is ABSENT
 * (→ undefined) on Iron Condor and both debit spreads — the engine builds those
 * legs without it — so never coerce a missing Delta to 0.
 */
export interface OptionsLeg {
  Side: "Short" | "Long";
  Type: "Put" | "Call";
  Strike: number | null;
  Price: number | null;
  Delta?: number | null;
}

/**
 * One options premium-selling directive (technical_options_engine.build_premium_directive,
 * persisted to output/options_matrix.json). Uncomputable numeric legs are `null`,
 * never 0. The `[key: string]: unknown` index signature keeps the type
 * forward-compatible with the writer, but every field the screen renders is
 * declared explicitly — otherwise the index signature widens it to `unknown`
 * and it won't render/map without a cast.
 *
 * `Legs[]` is the authoritative leg payload. `Short_Strike`/`Long_Strike` are a
 * lossy first-short/first-long projection (an Iron Condor's 4 legs collapse to
 * 2 here); render `Legs` for the full structure.
 *
 * `ATM_*` Greeks are always computed for a hypothetical at-the-money CALL at the
 * symbol's spot and σ, regardless of `Strategy` — they describe the symbol's ATM
 * sensitivity, not this structure's exposure.
 */
export interface OptionsDirective {
  Symbol: string;
  Price?: number | null;
  Stale?: boolean | null;
  Strategy?: string | null;
  Action?: string | null;
  Trend_Bias?: string | null;
  Sigma_GARCH?: number | null;
  IVR_Proxy?: number | null;
  /**
   * Opt-in real, options-chain-derived 30-day ATM IV rank (settings.
   * OPTIONS_TRUE_IVR_ENABLED — see technical_options_engine.build_premium_directive).
   * `null`/absent whenever the flag is off, the chain fetch failed, or the
   * iv_history table had no prior data for this symbol yet — in every such
   * case the directive/screen must fall back to `IVR_Proxy` (see
   * `optionsHonesty.effectiveIvr`), never silently claim a true IV rank.
   */
  True_IVR?: number | null;
  Aroon_Oscillator?: number | null;
  Coppock_Curve?: number | null;
  Net_Premium?: number | null;
  Realizable_Daily_Theta?: number | null;
  ATM_Delta?: number | null;
  ATM_Gamma?: number | null;
  ATM_Vega?: number | null;
  ATM_Theta_Daily?: number | null;
  Short_Strike?: number | null;
  Long_Strike?: number | null;
  Short_Delta?: number | null;
  Long_Delta?: number | null;
  Legs?: OptionsLeg[] | null;
  Integrity_OK?: boolean | null;
  Integrity_Issues?: string[] | null;
  Altman_Z_Score?: number | null;
  Piotroski_F_Score?: number | null;
  Net_Debt_EBITDA?: number | null;
  FCF_Yield?: number | null;
  Days_To_Earnings?: number | null;
  Earnings_Risk?: boolean | null;
  Realized_Vol_30D?: number | null;
  Analyst_Target_Consensus?: number | null;
  /**
   * Fraction, e.g. `0.12` means +12% upside vs. `Price` -- this codebase's
   * "percent as fraction" convention, NOT the `debtToEquity`-style ×100
   * convention (see the Footguns doc: these two are easy to confuse).
   */
  Analyst_Target_Upside?: number | null;
  /** Roughly in [-1, 1]; positive = more buy-rated. */
  Analyst_Grade_Score?: number | null;
  News_Snippets?: Array<{ title: string; url: string; published_date?: string; site?: string }> | null;
  Peers?: string[] | null;
  [key: string]: unknown;
}


/** GET /options — the full persisted options matrix. */
export interface OptionsMatrix {
  as_of: string | null;
  target_dte?: number | null;
  vix?: number | null;
  market_regime?: string | null;
  directives: OptionsDirective[];
  reason: string | null;
}

/** GET /symbols/{ticker}/options — one directive (or null) for a symbol. */
export interface SymbolOptions {
  symbol: string;
  directive: OptionsDirective | null;
  reason: string | null;
}

/** GET /pairs — one cointegrated pair row + current spread state. */
export interface PairRow {
  ticker1: string;
  ticker2: string;
  p_value: number | null;
  half_life: number | null;
  z_score: number | null;
  beta: number | null;
  rolling_p: number | null;
  position: number | null;
  signal: string; // advisory display label
}

export interface PairsRadar {
  as_of: string | null;
  universe: string[];
  pairs: PairRow[];
  reason: string | null;
}

// ---------------------------------------------------------------------------
// On-demand Options / Pairs recompute (webapp porting backlog items 8a/8b) —
// api/data_api.py POSTs, distinct from GET /options / GET /pairs above (which
// only ever serve the LAST PIPELINE-WRITTEN artifact). These are synchronous,
// request-scoped, operator-triggered computations against parameters/symbols
// the operator chooses, capped to a small size (see each request type's docs).
// ---------------------------------------------------------------------------

/** Body for POST /data/pairs/analyze — one named pair. */
export interface PairsAnalyzeRequest {
  symbol_y: string;
  symbol_x: string;
}

/**
 * POST /data/pairs/analyze response. Shaped like `PairRow` (`ticker1` = Y /
 * `ticker2` = X) plus a `found`/`reason` honesty envelope — `found: false`
 * (insufficient history, no cointegration, a degenerate pair) is an honest,
 * common, EXPECTED 200, not an error. `z_score_series` backs the frontend's
 * own mini chart (the server renders nothing itself).
 */
export interface PairsAnalyzeResult {
  ticker1: string;
  ticker2: string;
  found: boolean;
  reason: string | null;
  p_value: number | null;
  half_life: number | null;
  half_life_tradeable: boolean | null;
  z_score: number | null;
  beta: number | null;
  rolling_p: number | null;
  position: number | null;
  signal: string;
  aligned_bars: number;
  z_score_series: { date: string; z_score: number }[];
}

/** Body for POST /data/pairs/scan — an operator-chosen symbol list (2-15
 * after de-dup; 422 with a stable tag outside that range). */
export interface PairsScanRequest {
  symbols: string[];
  p_threshold?: number;
  max_pairs?: number;
}

/**
 * POST /data/pairs/scan response. `pairs` rows match `PairRow` exactly;
 * `missing` lists symbols that failed to fetch (dead-lettered, not aborted).
 * An honest empty `pairs: []` + `reason` is a valid 200 — statistical
 * arbitrage candidates are genuinely rare.
 */
export interface PairsScanResult {
  pairs: PairRow[];
  missing: string[];
  aligned_symbols: number;
  aligned_bars: number;
  reason: string | null;
}

/**
 * Body for POST /data/options/recompute — a capped symbol list (1-8 after
 * de-dup; 422 with a stable tag outside that range) plus the same directive
 * controls `gui/panels/options_matrix.py` exposes. Every field is optional;
 * an omitted field uses the engine's own default (so an empty-but-symbols
 * request reproduces the pipeline writer's defaults byte-for-byte).
 */
export interface OptionsRecomputeRequest {
  symbols: string[];
  target_dte?: number;
  delta_target_scale?: number;
  ivr_sell_threshold?: number;
  ivr_buy_threshold?: number;
  risk_free_rate_pct?: number | null;
  strike_grid?: number;
  delta_tolerance?: number;
}

/**
 * POST /data/options/recompute response. `directives` rows match
 * `OptionsDirective` exactly (reuses the same card/detail-sheet rendering as
 * `GET /options`). A symbol that failed to compute still gets an
 * error-shaped row in `directives` (never aborts the batch) AND its message
 * in `errors`. `vix`/`market_regime` are the macro state actually forwarded
 * into the VRP regime gate for this compute (from the latest persisted
 * snapshot, or the neutral default when none exists).
 */
export interface OptionsRecomputeResult {
  directives: OptionsDirective[];
  errors: string[];
  vix: number | null;
  market_regime: string | null;
  target_dte: number;
}

/**
 * GET /automation/status — the "did the pipeline run?" composite. Every
 * sub-object is honest about WHERE it came from (`source`/`*_source` fields)
 * rather than silently blending sources: after a daemon restart the
 * in-memory run history is gone, and this shape says so explicitly instead
 * of rendering a blank or fabricated run record.
 */
export interface DaemonInfo {
  alive: boolean;
  source: "control_api" | "daemon_json" | "none";
  pid: number | null;
  /**
   * Machine-checked liveness probe (an `os.kill(pid, 0)` existence check on
   * the backend), distinct from -- and more trustworthy than -- the
   * `daemon_json` fallback's own self-reported state: a daemon killed with
   * SIGKILL can never correct its own on-disk record, so a stale file can
   * say "running" long after the process is gone. `null` = we could not
   * determine it (no pid to probe on the `control_api` path, or an
   * unparseable/absent pid on the `daemon_json` path) -- never render
   * `null` as "dead". `false` on the `daemon_json` path is the only
   * positive evidence the process is actually gone; `true` there means "a
   * process with that pid exists" (rendered as "process alive, API not
   * responding"), which is not quite the same claim as "the daemon is
   * healthy" (a very unlucky pid reuse could in principle produce a false
   * `true`) but is the honest signal available without a heavier
   * dependency (e.g. psutil) on the backend.
   */
  pid_alive: boolean | null;
  port: number | null;
  started_at: string | null;
  interval_seconds: number | null;
  is_running: boolean | null;
  current_run_id: string | null;
  engines_warm: boolean | null;
}

export interface RunRecord {
  run_id: string;
  state: "queued" | "running" | "succeeded" | "failed";
  /**
   * Pipeline stage-scope of the run. Present on Control-triggered runs
   * (`api/control_api.py`): "full" = the whole cycle (POST /run), "data" =
   * data-fetch stages only, "metrics" = indicator/forecast/signal precompute
   * only. Absent (`undefined`) on records that predate the `mode` param or on
   * the `pilots_api` /automation/status path — render "—" for an absent mode,
   * never a fabricated default (CONSTRAINT #4).
   */
  mode?: "full" | "data" | "metrics" | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  error: string | null;
  reason: string;
  progress: ProgressState | null;
}

export interface ProgressState {
  run_id: string | null;
  state: string;
  stage: string;
  stage_index: number;
  stage_total: number;
  symbols_done: number;
  symbols_total: number;
  percent: number;
  message: string;
  started_at: string;
  updated_at: string;
  age_seconds: number;
  is_terminal: boolean;
  /** A "running" progress file untouched for 15+ minutes -- a dead run left
   * behind by a crash, not a live one. Never render it as still in-flight. */
  stale: boolean;
}

export interface DeadLetterReport {
  generated_at: string | null;
  entry_count: number; // TRUE total, even when `entries` is capped
  entries: Array<Record<string, unknown>>;
}

export interface AutomationStatus {
  daemon: DaemonInfo;
  last_run: RunRecord | null;
  /** "daemon_memory" when a real run record exists; "state_snapshot" when
   * the daemon has never triggered a run this process lifetime (nothing is
   * synthesized in that case -- fall back to pipeline.snapshot_age_seconds). */
  last_run_source: "daemon_memory" | "state_snapshot";
  pipeline: {
    snapshot_age_seconds: number | null;
    snapshot_age_source: "timestamp" | "mtime" | "missing";
    /** null in advisory mode BY DESIGN -- see heartbeat_note. Never render
     * this alone as "engine down". */
    heartbeat_age_seconds: number | null;
    heartbeat_note: string;
  };
  progress: ProgressState | null;
  kill_switch: { active: boolean; reason: string | null };
  errors: DeadLetterReport;
  advisory_only: boolean;
  dry_run: boolean;
  alpaca_paper: boolean;
}

/**
 * GET /status (api/control_api.py — the orchestrator daemon's Control API,
 * port 8601). The Pipeline Dashboard's live daemon snapshot. `run_history` is
 * the daemon's bounded, most-recent-first RunRecord ring (reuses the same
 * `RunRecord` shape AutomationStatus does; a Control-triggered run additionally
 * carries `mode`). Deliberately DISTINCT from GET /automation/status
 * (pilots_api.py), which composes this plus four other sources — this is the
 * raw daemon status the dashboard's trigger buttons act against.
 *
 * A discriminated union, not one interface with `boolean` fields: when no
 * `OrchestratorDaemon` has attached to the Control API process (get_daemon()
 * is None — a real, reachable state, not hypothetical: startup window, mid
 * restart, or the Control API served standalone), `get_status()` returns the
 * bare `{"daemon_alive": false}` and every other key is genuinely absent from
 * the response, not merely null. Modeling that shape as `daemon_alive:
 * boolean` plus a dozen always-required fields was a type-level lie — it let
 * a consumer read `data.run_history` without ever being told the field might
 * not exist. Consumers MUST narrow on `daemon_alive` before touching any
 * other field.
 */
export interface ControlStatusOffline {
  daemon_alive: false;
}

export interface ControlStatusOnline {
  daemon_alive: true;
  is_running: boolean;
  current_run_id: string | null;
  interval_seconds: number | null;
  engines_warm: boolean;
  started_at: string | null;
  last_run: RunRecord | null;
  run_history: RunRecord[];
  kill_switch_active: boolean;
  kill_switch_reason: string | null;
  advisory_only: boolean;
  dry_run: boolean;
}

export type ControlStatus = ControlStatusOffline | ControlStatusOnline;

/**
 * GET /runs/history (api/control_api.py) — durable run history read from
 * the daemon's `pipeline_runs` DB table (desktop/run_history_store.py),
 * independent of `ControlStatus.run_history`'s in-memory 10-run ring.
 * Survives a daemon restart; only terminal (succeeded/failed) runs are
 * ever written here — a run still `running` never appears, by design.
 */
export type RunHistoryEntry = RunRecord;

/** GET /automation/schedule — interval drift display + read-only cron. */
export interface CronEntry {
  schedule: string; // "0 21 * * 1-5"
  command: string;
  comment: string;
}

export interface AutomationSchedule {
  interval: {
    running_value: number | null;
    configured_value: number;
    /** running_value disagrees with configured_value -- a .env edit hasn't
     * reached the live daemon yet (it applies on next restart). */
    drift: boolean;
    writable: boolean;
    note: string;
  };
  cron: {
    source: string; // "deploy/crontab.txt"
    /** Always null -- this API parses the repo file, never `crontab -l`
     * (that would be a subprocess call from an API, the same RCE-adjacent
     * surface cron/systemd WRITING was excluded for). It cannot confirm
     * what's actually installed on the host. */
    installed: null;
    note: string;
    entries: CronEntry[];
  };
}

/**
 * POST /automation/run's result. Mirrors gui/daemon_client.py's own
 * TriggerResponse contract on the Python side: a documented RUNTIME outcome
 * (queued, already running, kill-switch-paused, daemon unreachable) is
 * returned as data here, NEVER thrown -- only a genuine config/auth problem
 * with THIS request (this API's own FOLLOW_API_TOKEN gate, a network error)
 * throws ApiError the normal way. `error` is a stable tag, not a message, so
 * the UI can branch on it without string-matching.
 */
export interface TriggerRunResult {
  ok: boolean;
  run_id: string | null;
  state: string | null;
  error:
    | "already_running"
    | "kill_switch_active"
    | "unavailable"
    | null;
  /** Populated only for the already_running case. */
  existing_run_id: string | null;
  /** Populated only for the kill_switch_active case. */
  kill_switch_reason: string | null;
}

/** POST /automation/pause / POST /automation/resume. */
export interface KillSwitchActionResult {
  active: boolean;
  reason: string | null;
}

/** PUT /automation/schedule/interval. */
export interface IntervalUpdateResult {
  configured_value: number;
  written: string;
  applies: "next_daemon_restart" | "immediately";
}

export interface ExecutionModeUpdateRequest {
  mode: "live" | "paper" | "simulation" | "advisory";
  advisory_only: boolean;
  /**
   * Typed field-name confirmation for any `settings_keysets.DANGEROUS_KEYS`
   * field this call is about to write (ADVISORY_ONLY always; DRY_RUN too
   * when `mode !== "advisory"`) -- same `SettingsConfirmMap` contract
   * `PUT /settings/tunables` uses for the same fields. Missing or
   * mismatched -> the backend rejects the whole request with 422 and writes
   * nothing.
   */
  confirm?: SettingsConfirmMap;
}

export interface ExecutionModeUpdateResult {
  written: string[];
  advisory_only: boolean;
  mode: "live" | "paper" | "simulation" | "advisory";
  applies: "next_daemon_restart" | "immediately";
  note: string;
}

/** Provenance of a strategy-module row (GET /strategy/matrix). */
export type StrategyModuleSource = "weights" | "snapshot" | "both";

/** One signal module's weight/enablement row (GET /strategy/matrix). */
export interface StrategyModuleRow {
  name: string;
  /** Configured SIGNAL_WEIGHTS value; null when the module has no configured weight. */
  weight: number | null;
  /** Regime-resolved weight; null when overrides are active but the regime is unknown. */
  effective_weight: number | null;
  /** The regime effective_weight was resolved for; null when it applies to every regime. */
  effective_weight_regime: string | null;
  enabled: boolean;
  source: StrategyModuleSource;
  contributed_last_run: boolean;
  /** Symbols scored last run; null when there is no snapshot yet. */
  symbols_scored: number | null;
  /** Structurally pinned to weight 0.0 (e.g. regime_multiplier). */
  pinned_zero: boolean;
  /** sha256-prefix (12 hex chars) fingerprint of signals/<name>.py; null when the module has no file on disk. */
  version_hash: string | null;
  /** ISO-8601 UTC mtime of signals/<name>.py; null alongside version_hash. */
  last_modified: string | null;
}

/** One fixed [lo, hi) bucket of the meta-label confidence histogram. */
export interface MetaLabelBin {
  lo: number;
  hi: number;
  count: number;
}

/**
 * Portfolio-wide distribution of `meta_label_composite` (GET /strategy/matrix,
 * ports `gui/panels/strategy_matrix.py::_render_meta_label_distribution`).
 * `bins` are FIXED over [0, 1] (20 bins) rather than auto-ranged, so a
 * degenerate all-1.0 dataset (the common case with no MetaLabelers
 * registered) still renders as an honest spike on a full-width axis instead
 * of a meaningless single bar.
 *
 * `all_unity: true` is the EXPECTED, correct state pre-Stage-4 (no
 * MetaLabelers registered in `ml.meta_labeling.global_meta_registry` → every
 * module's `meta_label_proba` defaults to 1.0, a multiplicative no-op) — the
 * UI must explain this, not present it as broken. `n_gated` counts symbols
 * with a genuine `0.0` (hard-gated below `min_confidence`) — distinct from
 * `missing` (the writer never computed a value for that symbol at all).
 */
export interface MetaLabelDistribution {
  bins: MetaLabelBin[];
  count: number;
  missing: number;
  n_gated: number;
  all_unity: boolean;
  min: number | null;
  max: number | null;
  min_confidence: number;
  reason: string | null;
}

/** GET /strategy/matrix — the signal-module weight/enablement matrix. */
export interface StrategyMatrix {
  as_of: string | null;
  market_regime: string | null;
  regime_overrides_active: boolean;
  weights_source: string;
  modules: StrategyModuleRow[];
  disabled: string[];
  max_weight: number;
  /** Tracks STRATEGY_WRITES_ENABLED — false means PUT /strategy/modules is disabled. */
  writable: boolean;
  note: string;
  /** Whether an .env write is pending against the running (in-process) values. */
  env_drift: { detected: boolean; keys: string[]; note: string };
  reason: string | null;
  meta_label: MetaLabelDistribution;
}

/** Body for PUT /strategy/modules. `weights` must cover EVERY known module. */
export interface StrategyModulesUpdate {
  weights: Record<string, number>;
  disabled: string[];
}

/** PUT /strategy/modules result. `configured_weights` echoes the request body. */
export interface StrategyModulesUpdateResult {
  written: string[];
  configured_weights: Record<string, number>;
  disabled: string[];
  applies: "next_daemon_restart";
  note: string;
}

// ---------------------------------------------------------------------------
// GET/PUT /settings/tunables — the general runtime-settings editor. Reads the
// platform's allowlisted, non-secret tunables grouped for display, and writes
// only the changed keys back.
//
// `applies` used to be the single literal "next_daemon_restart" for every
// field on every screen. That was true when a write could only ever land in
// `.env`, and stopped being true once the backend gained a runtime override
// store — so it is now resolved PER FIELD (`TunableField.liveness`) and merely
// SUMMARISED at the top level, where it can also be "mixed".
// ---------------------------------------------------------------------------

/** Widget kind for one tunable field. Enum fields additionally carry `options`. */
export type TunableFieldType = "number" | "boolean" | "enum" | "string";

/**
 * What actually happens to the RUNNING process when one field is saved.
 * Mirrors `pilots/settings_meta.py`'s four states exactly.
 *
 * - `immediately`         — pushed onto the live process; no restart needed.
 * - `next_daemon_restart` — written to `.env`; the process keeps the old value.
 * - `no_effect`           — nothing reads this field; writing it does nothing.
 * - `env_pinned`          — a real shell export wins over both `.env` and the
 *                           runtime store, so a write cannot take effect at all
 *                           until that export is removed.
 */
export type AppliesState =
  | "immediately"
  | "next_daemon_restart"
  | "no_effect"
  | "env_pinned";

/**
 * A screen-level or request-level rollup of many fields' {@link AppliesState}.
 * `"mixed"` when the fields disagree — deliberately NOT collapsed to the
 * most-alarming or most-optimistic member, because either would misdescribe
 * most of the set.
 */
export type AppliesSummary = AppliesState | "mixed";

/** Where the currently-active value came from. */
export type SettingSource = "runtime_store" | "env_file";

/**
 * Per-field liveness/safety metadata — the honest answer to "if I change this
 * and press Save, what happens?" (`pilots/settings_meta.py::field_metadata`).
 */
export interface TunableLiveness {
  applies: AppliesState;
  /**
   * Operator-readable sentence explaining WHY a restart is needed, or `null`
   * for a field that needs none. Never a filler string (CONSTRAINT #4).
   */
  restart_reason: string | null;
  /**
   * `file:line` sites where the running process captured this value, so the
   * restart claim is checkable rather than trusted. `[]` — not omitted — for a
   * field with none; that empty list is the MEASURED answer, not "unknown".
   */
  capture_sites: string[];
  /** A real shell export currently pins this field. */
  env_pinned: boolean;
  /**
   * Writing this field requires an explicit confirmation
   * (`settings_keysets.DANGEROUS_KEYS`). The UI raises a confirm dialog, but
   * the gate itself is enforced SERVER-SIDE — this flag drives affordance, not
   * safety.
   */
  dangerous: boolean;
  source: SettingSource;
}

/**
 * The confirmation map sent alongside a PUT. Each dangerous key must map to
 * its OWN NAME (`{ ADVISORY_ONLY: "ADVISORY_ONLY" }`); anything else is
 * rejected `confirmation_mismatch`, and omission is `confirmation_required`.
 *
 * Echoing the name (rather than a blanket boolean) is deliberate: confirming
 * one dangerous field can never implicitly confirm a second one in the same
 * batch.
 */
export type SettingsConfirmMap = Record<string, string>;

/** One editable runtime setting (GET /settings/tunables). */
export interface TunableField {
  key: string;
  /**
   * Current live value. `null` when the setting is absent/unreadable — NEVER a
   * fabricated default (CONSTRAINT #4). A number field's input renders empty,
   * not 0, in that case.
   */
  value: number | boolean | string | null;
  type: TunableFieldType;
  /** The platform's fallback value; `null` when not applicable. */
  default: number | boolean | string | null;
  /** `null` when the settings field has no pydantic `Field(description=...)`. */
  description: string | null;
  /** number fields only. */
  min?: number;
  max?: number;
  step?: number;
  /** enum fields only — the allowed values. */
  options?: string[];
  /**
   * Per-field liveness/safety metadata. The backend always sends this; it is
   * optional here only so that a response from an OLDER backend still parses.
   * Consumers must go through `resolveLiveness()` rather than reading it
   * directly, which supplies the same conservative fallback the backend itself
   * uses for a field it cannot classify (`next_daemon_restart`, no capture
   * sites, not dangerous) instead of leaving `undefined` to be handled ad hoc
   * at each use site.
   */
  liveness?: TunableLiveness;
}

/** A named cluster of related tunables (GET /settings/tunables). */
export interface TunableGroup {
  name: string;
  fields: TunableField[];
}

/** GET /settings/tunables — every editable runtime setting, grouped. */
export interface TunablesResponse {
  /**
   * Rollup of every served field's `liveness.applies` — `"mixed"` when they
   * disagree, which is the common case. Not a per-field claim: read
   * `field.liveness.applies` for that.
   */
  applies: AppliesSummary;
  /** How many fields sit in each state. Absent from an older backend. */
  applies_counts?: Record<AppliesState, number>;
  groups: TunableGroup[];
  /**
   * Whether an `.env` write is pending against the running (in-process)
   * values — mirrors `StrategyMatrix.env_drift`'s shape exactly (GET
   * /strategy/matrix). A `.env` write does NOT reach the live `settings`
   * singleton, so after a successful PUT this stays `detected: true` until the
   * daemon/pipeline restarts.
   */
  env_drift: { detected: boolean; keys: string[]; note: string };
}

/**
 * PUT /settings/tunables result. `written` echoes accepted key→value; `rejected`
 * maps a key to the reason it was refused (out of range, unknown, type
 * mismatch, or a missing/mismatched dangerous-key confirmation). Rejections are
 * surfaced, never swallowed.
 *
 * Reason tags the UI branches on: `unknown_key`, `forbidden_key`,
 * `expected_boolean`, `expected_number`, `expected_integer`, `expected_string`,
 * `invalid_option`, `out_of_range`, `invalid_json`, `confirmation_required`,
 * `confirmation_mismatch`.
 */
export interface TunablesUpdateResult {
  written: Record<string, number | boolean | string>;
  rejected: Record<string, string>;
  /** Rollup of `per_key_applies` — `"mixed"` when the written keys disagree. */
  applies: AppliesSummary;
  /**
   * The ACTUAL outcome per written key, not the a-priori prediction the GET
   * made: whether each one reached the running process or only `.env`.
   * Absent from an older backend.
   */
  per_key_applies?: Record<string, AppliesState>;
  applies_counts?: Record<AppliesState, number>;
  /** True only if at least one written key did NOT apply live. */
  restart_required?: boolean;
  restart_endpoint?: string;
  /** One honest sentence about what this write actually did. */
  note?: string;
}

// ---------------------------------------------------------------------------
// GET /strategy/health — catalog-wide deployability-gate breakdown. A bird's-
// eye view across EVERY Pilot of WHY its underlying validated strategy is or
// isn't deployable (the actual per-gate value vs. required threshold), not
// just the pass/fail badge Headline already surfaces for one Pilot at a time.
// ---------------------------------------------------------------------------

/** One deployability gate (PBO/DSR/Sharpe/MaxDD) for one Pilot's strategy. */
export interface StrategyHealthGate {
  key: "pbo" | "dsr" | "sharpe" | "max_drawdown";
  label: string;
  /** null when the underlying summary field is absent — never fabricated. */
  value: number | null;
  /** Read live from validation/thresholds.py — never re-typed on this side. */
  threshold: number;
  direction: "above" | "below";
  /**
   * null (unknown) when `value` is null/non-numeric — NEVER guessed. Distinct
   * from `false` (a real, known gate failure).
   */
  passed: boolean | null;
}

/** One past validation run's headline metrics (reports/history/*.jsonl row). */
export interface StrategyHealthTrendPoint {
  report_date: string | null;
  pbo: number | null;
  dsr: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  deployable: boolean | null;
}

/**
 * One Pilot's deployability-gate breakdown (GET /strategy/health).
 *
 * `gates` is `[]` and `deployable`/`is_options_selling`/`stress_gate_passed`/
 * `report_date` are all `null` (with an honest `reason`) when the Pilot has no
 * validated backtest (`strategy_id: null`) or its summary file is missing/
 * unreadable — NEVER a fabricated gate result (CONSTRAINT #4). `trend` is a
 * best-effort run-over-run series; an empty array is the honest "no history
 * yet" case, not an error.
 */
export interface StrategyHealthRow {
  pilot_id: string;
  pilot_name: string;
  strategy_id: string | null;
  deployable: boolean | null;
  gates: StrategyHealthGate[];
  is_options_selling: boolean | null;
  stress_gate_passed: boolean | null;
  report_date: string | null;
  trend: StrategyHealthTrendPoint[];
  reason: string | null;
}

// ---------------------------------------------------------------------------
// GET /strategy/validation-trend — the CROSS-STRATEGY counterpart to
// GET /strategy/health. `strategy/health` is scoped to catalog Pilots only
// (joined on Pilot.validation_strategy_id); a strategy validated by
// `validation.harness` but not yet wired to any Pilot never appears there.
// This endpoint reads EVERY reports/*_validation_summary.json on disk
// regardless of Pilot mapping, plus a macro-regime transition timeline (a
// data domain `strategy/health` never touches). Ports
// gui/panels/gravity_audit.py::_render_validation_stress_regime_section.
// Each of the three sections degrades independently server-side
// (pilots/validation_trend.py) with its own honest `*_reason` string — never
// fabricated (CONSTRAINT #4).
// ---------------------------------------------------------------------------

export interface ValidationTrendStrategyRow {
  strategy_id: string;
  deployable: boolean | null;
  pbo: number | null;
  dsr: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  is_options_selling: boolean | null;
  stress_gate_passed: boolean | null;
  report_date: string | null;
}

export interface ValidationTrendPoint {
  report_date: string | null;
  pbo: number | null;
  dsr: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  deployable: boolean | null;
}

export interface RegimeTransitionPoint {
  timestamp: string;
  market_regime: string;
}

export interface ValidationTrendSnapshot {
  strategies: ValidationTrendStrategyRow[];
  strategies_reason: string | null;
  // Keyed by strategy_id; only strategies with >= 2 recorded harness runs
  // appear (CONSTRAINT #4 — never a fabricated single-point trend).
  trend: Record<string, ValidationTrendPoint[]>;
  trend_reason: string | null;
  // TRANSITIONS only (rows where the regime differs from the immediately
  // preceding rotated snapshot), not every raw snapshot.
  regime_timeline: RegimeTransitionPoint[];
  n_rotated_snapshots: number;
  regime_reason: string | null;
}

// ---------------------------------------------------------------------------
// GET /gravity/audit-status — read-only port of the retired Streamlit Command
// Center's Safety tab AI Gravity audit runner (Claude auditor + Gemini
// cross-checker) + legacy structural Gravity Review Suite. Deliberately NO
// trigger endpoint for either side — both are real-cost / multi-minute
// operations with no incremental-progress channel over request/response HTTP
// (see the backend endpoint's own docstring for the full reasoning). Every
// leaf degrades to `null`/an honest `reason` rather than a fabricated verdict
// (CONSTRAINT #4); the composite 200s even when neither audit has ever run
// (CONSTRAINT #6).
// ---------------------------------------------------------------------------

export type GravityAiRunnerStatus = "disabled" | "missing_key" | "partial_key" | "ready";
export type GravityAiHealth = "clean" | "warn" | "fail" | "empty";

/** One step's Claude-vs-Gemini verdict pair from the last AI Gravity audit run. */
export interface GravityAiAuditStep {
  step_number: number | null;
  step_title: string;
  /** Pre-formatted badge string, e.g. "✅ PASSED" / "❌ FAILED" / "—". */
  claude: string;
  gemini: string;
  disagreement: boolean;
  score_claude: number | null;
  score_gemini: number | null;
  /** " · "-joined operator-facing notes; "" when there are none. */
  notes: string;
}

/**
 * AI Gravity audit runner summary (`GET /gravity/audit-status`'s `ai_audit`).
 * `status` mirrors the desktop panel's 4-state classifier: `"disabled"` (master
 * switch off), `"missing_key"` (switch on, neither provider key set),
 * `"partial_key"` (exactly one key set — the runner records the other side as
 * skipped), `"ready"` (both keys + switch on). `steps` is `[]` when no audit
 * has run yet, regardless of `status`.
 */
export interface GravityAiAuditSummary {
  status: GravityAiRunnerStatus;
  enabled: boolean;
  generated_at: string | null;
  health: GravityAiHealth;
  health_caption: string;
  total_steps: number;
  claude_passed: number;
  claude_failed: number;
  claude_skipped: number;
  gemini_passed: number;
  gemini_failed: number;
  gemini_skipped: number;
  disagreements: number;
  steps: GravityAiAuditStep[];
}

/** One step's pass/fail from the legacy structural Gravity Review Suite. */
export interface GravityLegacyAuditStep {
  step: string;
  passed: boolean;
  status: string;
}

/**
 * Legacy structural Gravity Review Suite last-run status
 * (`GET /gravity/audit-status`'s `legacy_audit`). `available` is `false` (with
 * an honest `reason`) when no run has ever completed, the log is unreadable,
 * or a run is currently in progress (its trailing verdict JSON isn't written
 * until the process exits) — `all_passed`/`steps` are never guessed in that
 * case.
 */
export interface GravityLegacyAuditStatus {
  available: boolean;
  all_passed: boolean | null;
  steps: GravityLegacyAuditStep[];
  reason: string | null;
}

export interface GravityAuditStatus {
  ai_audit: GravityAiAuditSummary;
  legacy_audit: GravityLegacyAuditStatus;
}

// ---------------------------------------------------------------------------
// GET /observability/summary — Mission Control composite: portfolio risk
// metrics, the account equity curve + drawdown, the current macro-regime
// overlay, portfolio-wide forecast skill, and the risk-gate block log. Every
// section degrades independently server-side (pilots/observability.py) — one
// section's cold start never blocks the other four. Every leaf the backend
// cannot compute is `null`, never a fabricated 0 (CONSTRAINT #4).
// ---------------------------------------------------------------------------

/** Sharpe/Calmar/MaxDD/MaxDD-duration/CAGR over the full account equity history. */
export interface PortfolioRiskMetrics {
  sharpe_ratio: number | null;
  calmar_ratio: number | null;
  max_drawdown: number | null; // fraction, <= 0 (0 = never dipped, a real value)
  max_drawdown_duration_days: number | null;
  cagr: number | null; // fraction
  n_snapshots: number;
  min_snapshots_required: number;
  reason: string | null; // present when n_snapshots < min_snapshots_required
}

/**
 * Live "Portfolio Heat" — aggregate adverse open-position P&L as a fraction
 * of total account equity, against the configured `max_portfolio_heat`
 * ceiling. Sourced server-side from the latest persisted account snapshot
 * (same two inputs — per-position unrealized P&L, account equity —
 * execution/risk_gate.py's live pre-trade gate reads). `heat_pct`/`over_limit`
 * are `null` when no account snapshot is persisted yet, or its total equity
 * is missing/non-positive (never a fabricated 0 — CONSTRAINT #4).
 */
export interface PortfolioHeatMetric {
  heat_pct: number | null; // fraction, e.g. 0.032 = 3.2%
  max_portfolio_heat: number | null; // the configured ceiling (settings.MAX_PORTFOLIO_HEAT)
  over_limit: boolean | null;
  n_positions: number;
  as_of: string | null; // ISO timestamp of the account snapshot this reads
  reason: string | null; // present when heat_pct is null
}

/** One point of the account equity + drawdown series. */
export interface EquityDrawdownPoint {
  date: string; // ISO date
  equity: number;
  drawdown: number; // fraction, <= 0 (against the all-time running peak)
}

export interface EquityDrawdownCurve {
  range: PerfRange;
  points: EquityDrawdownPoint[];
  reason: string | null; // present when points is empty
}

/** Current macro-regime telemetry from the persisted state snapshot. */
export interface RegimeOverlay {
  as_of: string | null;
  market_regime: string | null;
  vix: number | null;
  sahm_rule: number | null;
  high_yield_oas: number | null;
  yield_curve: number | null;
  hmm_risk_on_probability: number | null;
  kill_switch_active: boolean | null;
  macro_regime_gate_enabled: boolean | null;
  // The real, DTO-sourced macro kill-switch verdict (Sahm Rule >= 0.5, VIX
  // > 30, or HY OAS > 6% -- see PreTradeRiskGate.macro_kill_switch_check).
  // A DIFFERENT mechanism from `kill_switch_active` above (the operator's
  // manual global kill-switch FILE) -- never conflate the two. New BUY
  // orders are actually paused for macro reasons only when BOTH this field
  // AND `macro_regime_gate_enabled` are `true`. `null` when unknown (no
  // state snapshot yet).
  macro_kill_switch: boolean | null;
  reason: string | null; // present when no state snapshot exists yet
  /** Tracks MACRO_GATE_WRITES_ENABLED -- false means PUT /observability/macro-gate
   * is disabled server-side (403). Mirrors LlmStatus.writable. */
  macro_gate_writable: boolean;
  macro_gate_writable_note: string;
}

/** Body for PUT /observability/macro-gate. `reason` is required (fat-finger
 * guard, not a security control) -- mirrors PauseRequest/ResumeRequest. */
export interface MacroGateUpdate {
  enabled: boolean;
  reason: string;
}

/** PUT /observability/macro-gate result. `enabled` echoes the request body. */
export interface MacroGateUpdateResult {
  written: string[];
  enabled: boolean;
  applies: "next_daemon_restart";
  note: string;
}

/**
 * One scored headline (`signals/news_catalyst.py`'s FMP/Finnhub-sourced
 * company news, FinBERT-scored) surfaced on the Sentiment Dynamics screen.
 * `publisher` is always a real wire/outlet name FMP or Finnhub actually
 * returned, or the literal source string ("fmp"/"finnhub") as a fallback —
 * NEVER "SEC EDGAR" or any other publisher this data path cannot reach (SEC
 * filings are ingested by a structurally different module for a different
 * signal). `url`/`published_at` are `null` on a genuine gap, never fabricated.
 */
export interface HeadlineSentimentItem {
  title: string;
  publisher: string;
  url: string | null;
  published_at: string | null;
  score: number; // FinBERT-scored, roughly [-1, 1]
  probabilities: { positive: number; neutral: number; negative: number };
}

/**
 * Earnings-proximity dampening state for one symbol's live news score
 * (`signals/news_catalyst.py::_earnings_proximity_multiplier`). `"suppressed"`
 * = fully zeroed (0.0x) inside the pre-earnings blackout window;
 * `"dampened"` = halved (0.5x) — this covers BOTH the multi-day run-up
 * before earnings (beyond the blackout window but still close) AND the
 * ~24h window immediately after the print, when the reaction is still
 * fresh/noisy; `"normal"` = full-strength (1.0x), including when no earnings
 * date is currently scheduled at all.
 */
export interface EarningsCatalystStatus {
  next_earnings_date: string | null;
  hours_to_earnings: number | null;
  status: "normal" | "suppressed" | "dampened";
  multiplier: number;
}

/**
 * GET /metrics/sentiment/{symbol} — Sentiment Dynamics: Antigravity-agent
 * news sentiment plus GJR-GARCH asymmetric-volatility persistence, plus the
 * real scored-headline feed and earnings-proximity dampening state.
 *
 * Honesty contract: `source` distinguishes real Antigravity-agent output
 * ("antigravity_agent") from an honest cold-start/unconfigured-agent
 * degradation ("unavailable" — sentiment_score/sentiment_intensity/
 * credibility_score are all `null`, never a guessed number).
 * `volatility_persistence` is computed independently via a real per-request
 * GJR-GARCH fit over price history, so it can be a real number even when
 * `source === "unavailable"` (or `null` itself on insufficient history).
 * `headlines`/`earnings_catalyst`/`provider_used` are REQUIRED (not `?:`
 * optional) — an honest empty state is an explicit `[]`/`null`/`"none"`,
 * never an absent key.
 */
export interface SentimentDynamics {
  ticker: string;
  date: string;
  sentiment_score: number | null;
  sentiment_intensity: number | null;
  credibility_score: number | null;
  volatility_persistence: number | null;
  source: "antigravity_agent" | "unavailable";
  headlines: HeadlineSentimentItem[];
  earnings_catalyst: EarningsCatalystStatus | null;
  provider_used: "fmp" | "finnhub" | "none";
  source_breakdown: Record<string, number>;
  raw_sentiment_avg: number | null;
  dampened_sentiment_score: number | null;
  attention_score: number | null;
  sector_heat_factor: number | null;
}

/**
 * GET /data/sentiment/{symbol}/history — archived daily news-sentiment score
 * history from `HistoricalStore`'s `news_history` table
 * (`data/historical_store.py::get_news_sentiment_history`). A DIFFERENT
 * number from `SentimentDynamics.sentiment_score` above: that one is a
 * live, point-in-time Antigravity-agent read with no history; this is the
 * FinBERT/lexicon score `signals/news_catalyst.py` archives every pipeline
 * cycle, going back only as far as the archive itself (see `reason` /
 * `points.length` — the archive started 2026-07, so most symbols will have
 * only a few weeks of points, not enough for a lead-lag claim).
 *
 * `score: null` on a point means a genuine fetch/scoring failure or zero
 * headlines that day — never a fabricated neutral 0 (CONSTRAINT #4). A
 * chart built from this series must render that as a real gap, not a
 * plotted zero.
 */
export interface SentimentHistoryPoint {
  date: string; // ISO date (YYYY-MM-DD)
  score: number | null;
}

export interface SentimentHistory {
  symbol: string;
  points: SentimentHistoryPoint[];
  reason: string | null; // non-null only when points is empty
}

/** Portfolio-wide (all-symbol) forecast reliability + skill weights for one horizon. */
export interface PortfolioForecastSkill {
  horizon_days: number;
  window_days: number;
  min_obs: number;
  reliability_curve: ReliabilityBin[];
  skill_weights: Record<string, number>; // {model: normalized inverse-RMSE weight}
  pending: number;
  completed: number;
  reason: string | null;
}

/**
 * One symbol's forecast-skill row at ObservabilitySummary's currently-selected
 * horizon (`pilots/observability.py::forecast_skill_by_symbol_summary`) — the
 * per-symbol breakdown the portfolio-wide PortfolioForecastSkill above
 * doesn't carry. `skill_weights` is `{}` (never a fabricated equal split)
 * when this symbol has no forecast history in the window yet — a symbol
 * requested via the last pipeline snapshot's signals is never silently
 * omitted from `rows` just because it has zero completed forecasts so far.
 */
export interface ForecastSkillSymbolRow {
  symbol: string;
  pending: number;
  completed: number;
  skill_weights: Record<string, number>;
}

export interface ForecastSkillBySymbol {
  horizon_days: number;
  window_days: number;
  min_obs: number;
  rows: ForecastSkillSymbolRow[];
  reason: string | null; // present when rows is empty
}

/**
 * One quote-latency sample (`market_data_latency.py::LatencySample`) — the
 * end-to-end gap between a provider's own quote timestamp and local
 * ingestion time, recorded automatically on every real (non-cache-hit)
 * fetch through `data.market_data.CompositeProvider.get_latest_quote`.
 */
export interface LatencySample {
  symbol: string;
  source: string;
  quote_timestamp: string;
  ingested_at: string;
  latency_seconds: number;
  is_stale: boolean;
}

/**
 * `GET /observability/summary`'s `latency_heatmap` section
 * (`pilots/observability.py::latency_heatmap_summary`). `tracking_enabled`
 * mirrors `MARKET_DATA_LATENCY_TRACKING_ENABLED` (default `false`) —
 * distinct from `rows` being empty, since tracking can be ON with zero
 * samples yet (no quote fetched since this API process started). Samples
 * are an IN-PROCESS ring buffer only — never persisted to disk — so they
 * reset on every API restart; `rows`/`count`/`p50`/`p95` describe only
 * "since this process last started", never a fabricated cross-restart trend
 * (the same honesty framing `HeartbeatSummary` already uses).
 */
export interface LatencyHeatmap {
  tracking_enabled: boolean;
  count: number;
  p50: number | null;
  p95: number | null;
  worst_symbol: string | null;
  worst_p95: number | null;
  rows: LatencySample[];
  reason: string | null;
}

/** One entry from output/risk_gate_blocks.jsonl (execution/risk_gate.py). */
export interface RiskGateBlockEntry {
  ts: string | null;
  check: string | null;
  reason: string | null;
  symbol: string | null;
  side: string | null;
  qty: number | null;
  strategy_id: string | null;
}

export interface RiskGateBlockLog {
  entries: RiskGateBlockEntry[];
  // Always equal to entries.length today (pilots/observability.py returns at
  // most `n` rows, default 100, and count is that same list's length) — NOT
  // an uncapped true-total distinct from `entries` the way
  // DeadLetterReport.entry_count is. Kept as its own field for parity with
  // that shape and in case the backend later caps entries below count.
  count: number;
  reason: string | null;
}

/**
 * One derived circuit-breaker trip — the merged kill-switch + risk-gate-block
 * severity view (`gui/circuit_breakers.py`, ported from the legacy Streamlit
 * `gui/panels/gravity_audit.py::_render_circuit_breaker_dashboard`). Unlike
 * `RiskGateBlockEntry` (the raw, undeduped JSONL tail), each trip here is
 * already classified by severity and deduped to the most recent one per
 * (check, strategy) within the composite's `window_hours` — the kill switch,
 * when active, always sorts first.
 */
export interface CircuitBreakerTrip {
  name: string; // stable breaker id, e.g. "global_kill_switch", "portfolio_heat"
  severity: "CRITICAL" | "WARNING";
  summary: string; // one-line operator-facing description
  triggered_at: string | null; // ISO timestamp; null when the record carries none
  threshold: number | null; // the configured limit; null when not recorded (CONSTRAINT #4)
  observed: number | null; // the value that crossed it; null when not recorded
}

export interface CircuitBreakerCounts {
  critical: number;
  warning: number;
  total: number;
}

export interface CircuitBreakerSummary {
  trips: CircuitBreakerTrip[];
  counts: CircuitBreakerCounts; // feeds the KPI strip
  window_hours: number;
  reason: string | null; // present when trips is empty
}

/**
 * Host + current-process CPU/memory/disk snapshot (`gui/observability_telemetry
 * .collect_system_telemetry`, ported from the legacy Streamlit Observability
 * tab's "System Telemetry" section). Unlike every other `ObservabilitySummary`
 * section, this is NOT a read of a persisted artifact -- host resource usage
 * is inherently point-in-time, so `sampled_at` is "now", not a historical
 * series. `psutil_available: false` (e.g. the dependency is missing in this
 * environment) nulls every metric rather than fabricating a 0 (CONSTRAINT #4).
 */
export interface SystemTelemetry {
  psutil_available: boolean;
  cpu_percent: number | null; // 0-100
  cpu_count_logical: number | null;
  load_avg_1m: number | null; // POSIX only -- null on platforms without getloadavg
  memory_percent: number | null; // 0-100
  memory_used_bytes: number | null;
  memory_total_bytes: number | null;
  disk_percent: number | null; // 0-100, of the platform root volume
  disk_used_bytes: number | null;
  disk_total_bytes: number | null;
  process_rss_bytes: number | null; // this API process's resident memory
  process_cpu_percent: number | null;
  process_threads: number | null;
  sampled_at: string | null; // ISO timestamp of this sample
  reason: string | null; // present when psutil_available is false
}

/**
 * One durable position-sizing guardrail event (`sizing/cap_audit_store.py`'s
 * `sizing_cap_events` table, via `CapAuditStore._row_to_dict`). Distinct from
 * the per-cycle `Sizing_Was_Capped`/`Sizing_Binding_Constraint` columns
 * already surfaced elsewhere -- this is the DURABLE history across cycles.
 */
export interface SizingCapEvent {
  id: number;
  timestamp: string | null;
  cycle_id: string | null;
  symbol: string;
  strategy_id: string | null;
  raw_weight: number | null;
  final_weight: number | null;
  binding_constraint: string | null;
  was_capped: boolean;
}

/**
 * GET /observability/summary's `sizing_cap_audit` key -- the last N (default
 * 100) durable sizing-cap events, ported from the legacy Streamlit
 * Observability tab's "Sizing Cap-Event Audit Trail" section
 * (`gui/panels/observability.py::_render_observability_sizing_cap_audit`).
 * `events` is empty (never fabricated) when `SIZING_CAP_AUDIT_ENABLED` is
 * off or nothing has been recorded yet -- `reason` explains which.
 */
export interface SizingCapAuditTrail {
  events: SizingCapEvent[];
  count: number;
  capped_count: number;
  audit_enabled: boolean;
  escalation_enabled: boolean;
  escalation_threshold_cycles: number;
  escalation_factor: number | null;
  reason: string | null;
}

/**
 * One symbol's ETF volatility-transmission telemetry (Ben-David, Franzoni &
 * Moussawi 2018) -- mirrors `gui.observability_panel_helpers
 * .etf_transmission_rows`'s output shape exactly (that pure helper is reused
 * server-side, not reimplemented).
 */
export interface EtfTransmissionRow {
  symbol: string;
  etf_ownership_pct: number | null;
  etf_comovement_r2: number | null;
  etf_primary_wrapper: string | null;
  etf_transmission_multiplier: number | null;
}

/**
 * GET /observability/summary's `etf_transmission` key -- read-only per-symbol
 * ETF volatility-transmission diagnostic view, ported from the legacy
 * Streamlit Observability tab's "ETF Volatility Transmission" section. Three
 * INDEPENDENT master switches: `measurement_enabled` gates whether `rows` can
 * be non-empty at all; `sizing_enabled`/`portfolio_enabled` describe whether
 * the measured multiplier actually derates Kelly sizing / feeds the portfolio
 * covariance overlay elsewhere in the platform (this view never writes).
 * `rows` excludes any symbol with zero ETF-transmission fields (an
 * all-disabled cycle renders an empty list, not a wall of "--").
 */
export interface EtfTransmissionSummary {
  rows: EtfTransmissionRow[];
  measurement_enabled: boolean;
  sizing_enabled: boolean;
  portfolio_enabled: boolean;
  reason: string | null;
}

/**
 * GET /observability/summary's `heartbeat` key -- the CURRENT orchestrator
 * heartbeat age (seconds since `output/heartbeat.txt` was last written by
 * `main_orchestrator.py`'s async heartbeat task) + a freshness label
 * (`gui.observability_panel_helpers.heartbeat_status`).
 *
 * Deliberately carries NO trend/history: the legacy Streamlit "Heartbeat Age
 * Trend" sparkline is a 60-sample ring buffer held only in
 * `st.session_state` -- never persisted to disk -- so there is nothing
 * durable for this stateless HTTP endpoint to honestly serve as a series
 * (CONSTRAINT #4: no fabricated single-point "trend"). `history_available`
 * is always `false`; `history_note` explains why, so the UI can render that
 * honestly instead of a one-point sparkline that implies more than it shows.
 */
export interface HeartbeatSummary {
  age_seconds: number | null;
  status: string | null; // e.g. "🟢 Fresh" / "🟡 Slow" / "🔴 Stale" / "⚪ No heartbeat"
  history_available: false;
  history_note: string;
  reason: string | null; // present when age_seconds is null
}

/** One strategy's realized P&L bucket (`strategy_id: null` is a REAL bucket
 * for untagged trades -- never dropped, never merged into another row). */
export interface StrategyPnlRow {
  strategy_id: string | null;
  realized_pnl: number | null;
  trade_count: number;
}

/**
 * GET /observability/summary's `strategy_pnl` key -- realized P&L grouped by
 * strategy from `transactions_store.TransactionsStore`. This is the
 * FUNCTIONAL replacement for the legacy Streamlit "Strategy P&L" section,
 * which is dead code against real data (it groups by a `strategy_id` column
 * that has never existed on the `Trade` model, and reads a `realized_pnl`
 * column that was never stored -- see `pilots/observability.py
 * ::strategy_pnl_summary`'s docstring for the full honesty note). Rows are
 * sorted most-profitable-first.
 */
export interface StrategyPnlSummary {
  rows: StrategyPnlRow[];
  total_realized_pnl: number | null;
  reason: string | null;
}

export interface ObservabilitySummary {
  portfolio_risk: PortfolioRiskMetrics;
  portfolio_heat: PortfolioHeatMetric;
  equity_curve: EquityDrawdownCurve;
  regime: RegimeOverlay;
  forecast_skill: PortfolioForecastSkill;
  forecast_skill_by_symbol: ForecastSkillBySymbol;
  risk_gate_blocks: RiskGateBlockLog;
  latency_heatmap: LatencyHeatmap;
  circuit_breakers: CircuitBreakerSummary;
  system_telemetry: SystemTelemetry;
  sizing_cap_audit: SizingCapAuditTrail;
  etf_transmission: EtfTransmissionSummary;
  heartbeat: HeartbeatSummary;
  strategy_pnl: StrategyPnlSummary;
}

/**
 * GET /portfolio/equity-curve — account equity curve + a PARALLEL buying-power
 * series (G14 -- the webapp port of the legacy Streamlit Analytics tab's
 * buying-power overlay checkbox). Both series independently drop a point
 * whose own value is missing/non-finite rather than truncating the OTHER
 * series to match (CONSTRAINT #4) -- `buying_power_curve` can legitimately
 * have fewer points than `curve`, or vice-versa. `buying_power_curve` is
 * `[]` -- never fabricated -- on the same cold-start conditions as `curve`.
 */
export interface EquityCurveResponse {
  range: PerfRange;
  curve: CurvePoint[];
  buying_power_curve: CurvePoint[];
}

// ---------------------------------------------------------------------------
// GET /observability/logs — bounded, parsed tail of logs/investyo.log. Kept
// as its own endpoint (not a key on ObservabilitySummary above) since a log
// tail is a meaningfully heavier payload than that composite's other
// (scalar) sections and is naturally an on-demand view. See
// pilots/observability.py::log_aggregation for the full backend contract.
// ---------------------------------------------------------------------------

export const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
export type LogLevel = (typeof LOG_LEVELS)[number];

/** One parsed line from logs/investyo.log. `level`/`timestamp` are `null` for
 * an unparseable continuation line (e.g. a traceback frame) -- `parsed` is
 * `false` in that case and `raw` still carries the original text so the
 * operator never loses context. */
export interface LogAggregationEntry {
  timestamp: string | null;
  level: LogLevel | null;
  logger_name: string | null;
  message: string;
  raw: string;
  parsed: boolean;
}

export type LogLevelTally = Record<LogLevel | "UNPARSED", number>;

/**
 * `total_lines`/`tally`/`systemic_count`/`symbol_specific_count` reflect the
 * FULL last-1000-line read tail regardless of the `limit` query param, which
 * only trims `entries` (the most recent `limit`, oldest-first). Deliberately
 * excludes the legacy Streamlit panel's per-symbol message drilldown --
 * `symbol_specific_count` is a count only, not a breakdown by ticker (a
 * scope-narrowing decision for this low-priority, mobile-facing view).
 */
export interface LogAggregation {
  log_path: string | null;
  total_lines: number;
  tally: LogLevelTally;
  systemic_count: number;
  symbol_specific_count: number;
  entries: LogAggregationEntry[];
  returned_count: number;
  reason: string | null; // present when entries is empty
}

// ---------------------------------------------------------------------------
// Phase-4 Data Explorer / Signal Breakdown / Forecast Viewer
// (data_api.py :8603, metrics_api.py :8604)
// ---------------------------------------------------------------------------

/** GET /data/bars/{symbol} — one daily OHLCV row (`[]` when no bars). */
export interface Bar {
  date: string; // ISO date
  Open: number | null;
  High: number | null;
  Low: number | null;
  Close: number | null;
  Volume: number | null;
}

/**
 * GET /data/fundamentals/{symbol} — a yfinance `.info`-shaped metric dict.
 * Keys are provider-defined (trailingPE, priceToBook, returnOnEquity, ...);
 * a value is `null` when the provider omitted/couldn't compute it (never a
 * fabricated 0 — CONSTRAINT #4). 404 when the symbol has no coverage at all.
 */
export type Fundamentals = Record<string, number | string | null>;

/**
 * GET /data/macro — raw current-snapshot macro dict from `fetch_macro_raw`
 * (VIXCLS, T10Y2Y, Sahm, credit spread, ...). Keys are source-defined and a
 * value may be `null`; the screen labels the ones it knows and lists the rest.
 */
export type MacroSnapshot = Record<string, number | string | null>;

/**
 * GET /data/macro/history?series=VIXCLS — daily historical values for one
 * FRED macro series from `HistoricalStore`'s `macro_history` cache
 * (`data/historical_store.py::get_macro`). Distinct from `MacroSnapshot`
 * above, which is a current-snapshot scalar only. A gap day (FRED didn't
 * publish, e.g. a market holiday) is a real `null`, never a carried-forward
 * value.
 */
export interface MacroHistoryPoint {
  date: string; // ISO date (YYYY-MM-DD)
  value: number | null;
}

export interface MacroHistorySeries {
  series_id: string;
  points: MacroHistoryPoint[];
  reason: string | null; // non-null only when points is empty
}

/** One signal module's contribution within a symbol's blended score. */
export interface SignalModuleScore {
  name: string;
  // `score` is the module's raw [-1,1] (long-only modules [0,1]) output;
  // `null` when the module didn't run for this symbol (never fabricated 0).
  score: number | null;
  weight: number;
  // contribution = score * weight; `null` when score is null.
  contribution: number | null;
}

/**
 * GET /metrics/signals/{symbol} — per-module breakdown of a symbol's blended
 * signal. `action`/`conviction` come from `engine.advisory.evaluate`;
 * `final_score` + `modules` from a direct `SignalAggregator.aggregate`.
 * Any field is `null` on a cold start / no bars (honest, never fabricated).
 */
export interface SignalBreakdown {
  symbol: string;
  action: "BUY" | "SELL" | "HOLD" | null;
  conviction: number | null;
  final_score: number | null;
  modules: SignalModuleScore[];
}

/**
 * One signal module's universe-wide driver weight — see
 * `SignalImportance` below for the full honesty contract. NOT a SHAP value.
 */
export interface SignalImportanceRow {
  name: string;
  // mean(|score * weight|) across symbols where this module scored — `null`
  // when n_symbols_scored is 0 (never a fabricated 0, which would read as
  // "definitely unimportant" rather than "no data this batch").
  mean_abs_contribution: number | null;
  n_symbols_scored: number;
  // `mean_abs_contribution` normalized so every non-null row in a batch
  // sums to ~1.0 -- a relative "share of total contribution" figure the
  // absolute mean_abs_contribution alone doesn't give. Optional/undefined
  // on a backend that hasn't been updated to serve it yet; `null` under the
  // same condition mean_abs_contribution is null (nothing to normalize).
  normalized_contribution?: number | null;
  // The static settings.SIGNAL_WEIGHTS entry for this module -- a DIFFERENT
  // number from mean_abs_contribution (a measured, symbol-averaged
  // score*weight): this is the configured absolute weight itself. Optional/
  // undefined on a backend that hasn't been updated to serve it yet.
  config_weight?: number | null;
}

/**
 * GET /metrics/signals/importance?symbols=A,B,C — universe-wide signal-
 * module driver weights (`api/metrics_api.py::_signal_importance`).
 *
 * Deliberately NOT SHAP / feature importance: `mean_abs_contribution` is a
 * linear, configured-weight decomposition (mean(|score * weight|) using the
 * same `settings.SIGNAL_WEIGHTS` every per-symbol breakdown uses) — it
 * captures no feature interactions and no marginal contribution. See the
 * "signal driver weight" glossary entry for the reader-facing version of
 * this same disclaimer; never relabel this UI as SHAP.
 *
 * Every registered module appears in `rows`, even one that scored zero
 * symbols in this batch (`mean_abs_contribution: null`) — an honest empty
 * row, not a silently missing one. `n_symbols_requested` may be less than
 * the caller's symbol list length if the server capped it (each symbol is
 * a real per-symbol compute, not a persisted read).
 */
export interface SignalImportance {
  rows: SignalImportanceRow[];
  n_symbols_requested: number;
  n_symbols_scored: number;
}

/**
 * One day's attention weight from forecasting/bert_lla.py's LLAAttention
 * layer, over the "bert_lla" ablation's lookback window.
 */
export interface ForecastAttentionWeight {
  date: string; // ISO date (YYYY-MM-DD)
  alpha: number; // in [0, 1]; every window's alphas sum to ~1.0
}

/**
 * BERT-LLA attention-weight overlay for one forecast request — which days in
 * the lookback window the "bert_lla" ablation weighted most heavily. `null`
 * whenever `settings.BERT_LLA_ENABLED` is off, torch isn't installed, the
 * sentiment-coverage gate blocked training, or the model otherwise didn't
 * run this request — never a fabricated weight series (CONSTRAINT #4).
 */
export interface ForecastAttention {
  model: string; // "bert_lla"
  window_size: number;
  weights: ForecastAttentionWeight[];
}

/**
 * GET /metrics/forecast/{symbol} — multi-horizon blended forecast + Monte
 * Carlo bands from `ForecastingEngine.generate_forecast`. Every price field
 * may be `null` (NaN→null); the backend 404s when there are no bars at all,
 * so a rendered response always has *some* horizon populated.
 */
export interface ForecastResult {
  Forecast_10: number | null;
  Forecast_30: number | null;
  Forecast_60: number | null;
  Forecast_90: number | null;
  ARIMA: number | null;
  MC_Lower: number | null;
  MC_Upper: number | null;
  // Per-horizon confidence band (price levels). A band is `null` when the
  // matching `Forecast_{h}` horizon didn't converge — a null horizon has no
  // band (never a fabricated 0 — CONSTRAINT #4). Bands widen with horizon.
  Forecast_10_Lower: number | null;
  Forecast_10_Upper: number | null;
  Forecast_30_Lower: number | null;
  Forecast_30_Upper: number | null;
  Forecast_60_Lower: number | null;
  Forecast_60_Upper: number | null;
  Forecast_90_Lower: number | null;
  Forecast_90_Upper: number | null;
  attention: ForecastAttention | null;
  // Prophet overlay (present only when Prophet ran); index signature carries
  // any additional model columns the engine emits without silently dropping
  // them. Widened to include ForecastAttention so `attention` above (a
  // named, non-numeric property) still satisfies the index signature.
  [key: string]: number | null | ForecastAttention;
}

// ---------------------------------------------------------------------------
// On-demand AI generation (data_api.py :8603, llm/schemas.py) — POST
// /data/ai/commentary/{symbol}, /data/ai/chart/{symbol}, /data/ai/research/{symbol}.
// Each call is operator-triggered (never automatic), qualitative-only
// (CONSTRAINT #4 — no field here is a fabricated numeric price target or
// score), and independently honest: `available: false` always carries a
// specific `reason`, `payload` is `null` in that case, never a partial guess.
// ---------------------------------------------------------------------------

/**
 * Claude analyst-grade narrative for a single symbol (llm/schemas.py
 * `AnalystRationale`). `key_risks` is 1-3 short bullets when present.
 */
export interface AnalystRationalePayload {
  headline: string;
  why_now: string;
  key_risks: string[];
  invalidation: string;
}

/** POST /data/ai/commentary/{symbol} response. */
export interface AiCommentaryResponse {
  available: boolean;
  reason: "disabled" | "missing_key" | "generation_failed" | null;
  payload: AnalystRationalePayload | null;
}

/**
 * Gemini Vision chart-pattern interpretation (llm/schemas.py
 * `ChartPatternRead`). `support_levels` / `resistance_levels` are qualitative
 * descriptions (never numeric), each list capped at 3 items.
 */
export interface ChartPatternPayload {
  pattern_name: string;
  trend_direction: "bullish" | "bearish" | "neutral";
  support_levels: string[];
  resistance_levels: string[];
  narrative: string;
  confidence: "low" | "medium" | "high";
}

/**
 * POST /data/ai/chart/{symbol} response. `chart_png_base64` may be non-null
 * even when `available` is `false` (the chart rendered fine but the AI read
 * failed, e.g. `reason: "generation_failed"`) — render the image whenever
 * `chart_png_base64` is present, independent of `available`/`payload`.
 */
export interface AiChartResponse {
  available: boolean;
  reason:
    | "disabled"
    | "missing_key"
    | "no_bars"
    | "chart_render_failed"
    | "generation_failed"
    | null;
  payload: ChartPatternPayload | null;
  chart_png_base64: string | null;
}

/**
 * Opal (OpenAI/Gemini) grounded research brief (llm/schemas.py
 * `ResearchBrief`). `catalysts`/`risk_factors`/`recent_developments` are
 * PLAIN STRING lists (each item a short bullet, NOT nested objects) drawn
 * from real retrieved news/earnings/macro — may be empty when the grounding
 * packet yielded none, never fabricated to fill the list.
 */
export interface ResearchBriefPayload {
  thesis_context: string;
  catalysts: string[];
  risk_factors: string[];
  recent_developments: string[];
  data_confidence: "low" | "medium" | "high";
  sources_note: string;
}

/** POST /data/ai/research/{symbol} response. */
export interface AiResearchResponse {
  available: boolean;
  reason: "disabled" | "generation_failed" | null;
  payload: ResearchBriefPayload | null;
}

// ---------------------------------------------------------------------------
// Recommendation Tracking & Calibration (pilots/calibration.py) — GET
// /calibration/summary, GET /calibration/edge-by-strategy, POST /decisions.
// ---------------------------------------------------------------------------

/**
 * One conviction bin of the reliability diagram. `win_rate` is `null` when the
 * bin has fewer than `min_trades_per_bin` trades (insufficient sample — never a
 * fabricated rate, CONSTRAINT #4). `perfect_calibration` == `bin_center` (the
 * y=x reference for that bin).
 */
export interface CalibrationBin {
  bin_low: number | null;
  bin_high: number | null;
  bin_center: number | null;
  conviction_mean: number | null;
  win_rate: number | null;
  count: number;
  perfect_calibration: number | null;
}

/** GET /calibration/summary -> calibration section. */
export interface Calibration {
  bins: CalibrationBin[];
  total: number;
  overall_win_rate: number | null;
  calibration_error: number | null;
  n_scored_bins: number;
  n_bins: number;
  min_trades_per_bin: number;
  reason: string | null;
}

/** One logged BUY signal's model-vs-operator comparison. Returns are fractions. */
export interface RecTrackingRow {
  symbol: string;
  signal_ts: string | null;
  signal_action: string | null;
  conviction: number | null;
  action_taken: string | null;
  model_return: number | null;
  actual_return: number | null;
  days_held: number | null;
  trade_id: number | null;
  completed: boolean;
}

/** GET /calibration/summary -> recommendation_tracking section. */
export interface RecommendationTracking {
  horizon_days: number;
  model_return: number | null;
  operator_return: number | null;
  delta: number | null;
  n_signals: number;
  n_acted: number;
  n_completed: number;
  n_with_exit: number;
  rows: RecTrackingRow[];
  reason: string | null;
}

/** One current-signal MFE/MAE point (fractions of entry price). */
export interface MfeMaePoint {
  symbol: string;
  mfe: number;
  mae: number;
  edge_ratio: number | null;
  conviction: number | null;
  action: string;
}

/** GET /calibration/summary -> mfe_mae section. */
export interface MfeMaeView {
  points: MfeMaePoint[];
  reason: string | null;
}

/** One row of the operator decision journal. `trade_id` null == unlinked. */
export interface DecisionEntry {
  symbol: string | null;
  action_taken: string | null;
  signal_action: string | null;
  conviction: number | null;
  notes: string;
  timestamp: string | null;
  signal_ts: string;
  trade_id: number | null;
}

/** GET /calibration/summary -> recent_decisions section. */
export interface RecentDecisions {
  decisions: DecisionEntry[];
  reason: string | null;
}

/** GET /calibration/summary — composite for the Calibration screen. */
export interface CalibrationSummary {
  calibration: Calibration;
  recommendation_tracking: RecommendationTracking;
  mfe_mae: MfeMaeView;
  recent_decisions: RecentDecisions;
}

/** One strategy's aggregated edge-ratio row. NaN aggregates -> null. */
export interface EdgeByStrategyRow {
  strategy: string;
  n_trades: number;
  mean_edge_ratio: number | null;
  median_edge_ratio: number | null;
  mean_mfe: number | null;
  mean_mae: number | null;
}

/** GET /calibration/edge-by-strategy — the heavier, lazy-loaded recompute. */
export interface EdgeByStrategy {
  rows: EdgeByStrategyRow[];
  reason: string | null;
}

/** POST /decisions request body. */
export interface DecisionCreateRequest {
  symbol: string;
  action_taken: "acted" | "passed" | "modified";
  signal_action: string;
  conviction: number | null;
  notes: string;
  signal_ts?: string;
}

/** POST /decisions response — the created entry, with the resolved trade link. */
export interface DecisionCreateResult {
  symbol: string;
  action_taken: string;
  signal_action: string;
  conviction: number | null;
  notes: string;
  timestamp: string;
  signal_ts: string;
  trade_id: number | null;
  trade_linked: boolean;
}

/** How an argument is supplied — mirrors cli_introspect's arg_kind. */
export type ArgKind = "required" | "optional" | "variadic";

/** An optional/flag argument of a CLI command (from the command manifest). */
export interface CommandOption {
  name: string; // canonical, e.g. "--interval"
  aliases: string[]; // every option string, e.g. ["-v", "--version"]
  description: string | null;
  default: string | number | boolean | null;
  choices: string[] | null;
  required: boolean;
  arg_kind: ArgKind;
  metavar: string | null;
  takes_value: boolean; // false for store_true/false/count/const flags
}

/** A positional argument of a CLI command. */
export interface CommandArg {
  name: string;
  description: string | null;
  default: string | number | boolean | null;
  choices: string[] | null;
  arg_kind: ArgKind;
  metavar: string | null;
}

/** A CLI command — a top-level entry point, or one subcommand (recursive). */
export interface CommandSpec {
  name: string; // typed name, e.g. "main.py" / "validation.harness" / "get"
  invocation: string; // full run prefix, e.g. "python -m validation.harness"
  aliases: string[]; // subcommand aliases (top-level commands: [])
  description: string | null;
  options: CommandOption[];
  positionals: CommandArg[];
  subcommands: CommandSpec[];
}

/**
 * GET /commands — the CLI command manifest that powers the command bar's
 * autocomplete + validation. `commands` is empty (with a `reason`) on a cold
 * start where the manifest hasn't been generated yet — never a fabricated list
 * (CONSTRAINT #4).
 */
export interface CommandManifest {
  generated_at: string | null;
  command_count: number;
  dead_letters?: string[];
  /**
   * Live strategy names from scripts/refresh_validations.py's STRATEGY_REGISTRY,
   * generated into the manifest by scripts/build_command_manifest.py. Optional
   * for backward compat with older mocks/manifests (mirrors `dead_letters?`) --
   * consumers should fall back to commandParse.ts's REGISTERED_STRATEGIES
   * constant when this is absent or empty.
   */
  strategy_registry?: string[];
  /**
   * Live options-strategy names from validation/options_harness.py's
   * STANDARD_OPTIONS_STRATEGIES, generated into the manifest by
   * scripts/build_command_manifest.py. Distinct from `strategy_registry`
   * above: `validation.harness`'s bulk (`--strategies`) mode only ever gives
   * real, name-specific results for options strategies -- see that module's
   * `main()` docstring -- so it needs its own registry, not the equity/
   * cross-sectional STRATEGY_REGISTRY names. Optional for backward compat
   * with older mocks/manifests; consumers should fall back to
   * commandParse.ts's REGISTERED_OPTIONS_STRATEGIES constant when absent.
   */
  options_strategy_registry?: string[];
  commands: CommandSpec[];
  reason: string | null;
}

/** One proposed order from the gated Robinhood execution queue. */
export interface ExecutionQueueIntent {
  symbol: string;
  action: "BUY" | "SELL" | string;
  side: string;
  qty: number | null;
  target_notional: number | null;
  conviction: number | null;
  gate_allowed: boolean;
  gate_reasons: string[];
  allow_place: boolean;
  rationale: string;
  /**
   * Optional — a queue entry that hasn't been assigned an idempotent order
   * ID yet (e.g. blocked pre-gate) may omit it; never fabricate one
   * client-side (CONSTRAINT #4). Existing callers already fall back to
   * `${symbol}-${side}` for list keys, so this stayed safe to widen.
   */
  client_order_id?: string | null;
  /**
   * Real per-intent attribution (never guessed from `rationale` free text —
   * CONSTRAINT #4): `"advisory"` for the base advisory engine, `"composed"`
   * when netted across more than one Pilot follow, or a real followed
   * Pilot's `pilot_id`.
   */
  follow_type?: string;
  /** Real strategy/signal-module attribution string, when the queue builder has one. */
  strategy?: string;
  /** Real contributing signal-source names (news/sentiment/etc.), when available. */
  sources?: string[];
  /** The quote price the intent was proposed against — null/absent when unpriced. */
  proposed_price?: number | null;
}

/** Query parameters for GET /execution-queue */
export interface ExecutionQueueParams {
  action?: string;
  follow_type?: string;
  status_filter?: string;
  min_conviction?: number;
}

/**
 * GET /execution-queue — a READ-ONLY view of `output/execution_queue.json`.
 * This is not an order-placement API: per execution/queue_builder.py's module
 * contract, only a live Claude Code agent session (the robinhood-execution
 * skill) ever calls the Robinhood MCP's place_equity_order tool. `intents` is
 * empty (with a `reason`) on a cold start — never a fabricated queue
 * (CONSTRAINT #4).
 */
export interface ExecutionQueue {
  generated_at: string | null;
  mode: "off" | "review" | "live" | string;
  kill_switch_active: boolean;
  max_notional_per_order: number;
  n_intents: number;
  n_placeable: number;
  stale: boolean;
  age_seconds: number | null;
  intents: ExecutionQueueIntent[];
  /**
   * Every distinct real `follow_type` value present in the UNFILTERED queue
   * (regardless of the currently-applied filters) — lets a caller build a
   * filter control without hardcoding pilot names.
   */
  available_follow_types?: string[];
  reason: string | null;
}

// ---------------------------------------------------------------------------
// Agentic Trading tab — GET /agentic/status, GET /agentic/discovery,
// PUT /agentic/scan-config.
// ---------------------------------------------------------------------------

/** GET /agentic/status -> queue sub-section. Mirrors ExecutionQueue's summary
 *  fields (never the full intents list -- that's ExecutionQueueSection's job). */
export interface AgenticQueueSummary {
  mode: "off" | "review" | "live" | string;
  generated_at: string | null;
  n_intents: number;
  n_placeable: number;
  stale: boolean;
  age_seconds: number | null;
}

/** GET /agentic/status -> follows sub-section (active Pilot follows only). */
export interface AgenticFollowsSummary {
  n_active: number;
  total_amount: number;
}

/** GET /agentic/status -> agent_loop sub-section, from
 *  engine/advisory_agent.py's persisted AgentState (output/agent_state.json).
 *  `reason` is set (and the numeric fields are honest zeros, not fabricated)
 *  when the advisory-loop agent hasn't completed a cycle yet. */
export interface AgentLoopStatus {
  cycle_count: number;
  last_cycle_iso: string | null;
  backlog_count: number;
  reason: string | null;
}

/**
 * GET /agentic/status — composite "what is the agent doing" answer for the
 * Agentic Trading tab's header. Read-only; never places an order (see
 * ExecutionQueue's docstring for why this API can't and doesn't).
 */
export interface AgenticStatus {
  mode: "off" | "review" | "live" | string;
  advisory_only: boolean;
  kill_switch: { active: boolean; reason: string | null };
  queue: AgenticQueueSummary;
  follows: AgenticFollowsSummary;
  agent_loop: AgentLoopStatus;
}

/**
 * One scan-discovered candidate (output/scan_candidates.json, written by the
 * `.claude/skills/agentic-discovery/` Claude Code skill — this API never
 * contacts the Robinhood MCP itself). `action`/`conviction` are null when the
 * skill couldn't cross-reference the symbol against the advisory engine —
 * never a fabricated score (CONSTRAINT #4).
 */
export interface DiscoveryCandidate {
  symbol: string;
  scan_name: string | null;
  scan_reason: string | null;
  action: string | null;
  conviction: number | null;
  discovered_at: string | null;
}

/** One operator-defined Robinhood broker-scan config (output/scan_configs.json). */
export interface ScanConfig {
  name: string;
  filters: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

/** GET /agentic/discovery — the Discovery section's data. Empty `candidates`
 *  + an honest `reason` when no scan has run yet (CONSTRAINT #4). `writable`
 *  tracks AGENTIC_DISCOVERY_ENABLED -- false means PUT /agentic/scan-config
 *  is disabled (mirrors StrategyMatrix's `writable`). */
export interface AgenticDiscovery {
  generated_at: string | null;
  candidates: DiscoveryCandidate[];
  scan_configs: ScanConfig[];
  reason: string | null;
  writable: boolean;
  note: string;
}

/** Body for PUT /agentic/scan-config. Create/replace ONE named scan config. */
export interface ScanConfigRequest {
  name: string;
  filters: Record<string, unknown>;
  enabled: boolean;
}

/** PUT /agentic/scan-config response. `scan_config` echoes the store's
 *  returned row (with resolved timestamps), not the raw request body. */
export interface ScanConfigResult {
  scan_config: ScanConfig;
  applies: "next_discovery_run";
  note: string;
}

/** POST /agentic/watch response. Echoes the writer's own result — `added` vs
 *  `already_present` (never a fabricated success). `applies` is
 *  "next_pipeline_run": the symbol enters the universe on the next run, and
 *  NO order is placed. A 409 (`watchlist_env_precedence`) or 422
 *  (`invalid_symbol`) surfaces as an `ApiError` with the stable tag in its
 *  message, per the endpoint's honest-failure contract. */
export interface WatchResult {
  symbol: string;
  added: string[];
  already_present: string[];
  watchlist_file: string;
  applies: "next_pipeline_run";
  note: string;
}

// ---------------------------------------------------------------------------
// RLHF Calibration Review Queue — nests INSIDE the Agentic Trading screen
// (RlhfReviewQueue.tsx), NOT a standalone route and NOT the unrelated
// `/calibration` statistical-reliability screen. An AI trading agent
// proposes a hypothetical paper trade via an MCP tool + the API; a human
// operator reviews it here and submits a 1-5 star rating plus an optional
// corrective comment, feeding an eventual SFT (supervised fine-tuning)
// export. There is deliberately no webapp-side "create proposal" form.
// ---------------------------------------------------------------------------

/**
 * One AI-proposed hypothetical paper trade awaiting (or having received) a
 * human rating. Every field the backend can legitimately omit is `| null`
 * (CONSTRAINT #4 — never a fabricated default): `price`/`quantity` when the
 * agent couldn't resolve a live quote, `rsi`/`sentiment_score` when that
 * technical/sentiment input wasn't available, `extra_context` when the agent
 * attached no additional structured context. `auto_approved` proposals are
 * already `status: "reviewed"` with `human_rating: null` — they never appear
 * in a pending queue and were never rated by a human.
 */
export interface RlhfProposal {
  id: number;
  created_at: string; // ISO timestamp
  symbol: string;
  action: "BUY" | "SELL" | "HOLD";
  quantity: number | null;
  price: number | null;
  rationale: string;
  confidence: number; // [0,1] fraction, NOT a percent
  rsi: number | null;
  sentiment_score: number | null;
  extra_context: Record<string, unknown> | null;
  status: "pending" | "reviewed";
  human_rating: 1 | 2 | 3 | 4 | 5 | null;
  human_correction: string | null;
  reviewed_at: string | null;
  auto_approved: boolean;
  sft_exported: boolean;
}

/**
 * GET /rlhf/summary -> kpis. `average_human_rating` is `null` (never a
 * fabricated 0) until at least one proposal has actually been rated by a
 * human. `rating_distribution` is keyed "1".."5".
 */
export interface RlhfKpis {
  pending_count: number;
  reviewed_count: number;
  average_human_rating: number | null;
  rating_distribution: Record<string, number>;
  auto_approved_count: number;
  sft_exported_count: number;
}

/**
 * GET /rlhf/summary?limit=50 — the RLHF Review Queue's composite. `proposals`
 * is the PENDING queue only (already-reviewed proposals still count toward
 * `kpis`, just not this list). `writable` tracks
 * `settings.RLHF_CALIBRATION_ENABLED` server-side (mirrors AgenticDiscovery's
 * `writable`); `reason` is set when `proposals` is empty and there's a reason
 * worth surfacing (e.g. "no proposals yet").
 */
export interface RlhfSummary {
  proposals: RlhfProposal[];
  kpis: RlhfKpis;
  writable: boolean;
  reason: string | null;
}

/** Body for POST /rlhf/proposals/{id}/review. */
export interface RlhfReviewSubmitRequest {
  human_rating: 1 | 2 | 3 | 4 | 5;
  human_correction?: string;
}

/**
 * POST /rlhf/proposals/{id}/review response — the updated proposal plus
 * whether it triggered an SFT export as a side effect (`sft_exported` is
 * already one of RlhfProposal's own fields; it's called out again here since
 * that's the specific thing this response is confirming). A 404
 * (`not_found`) or 409 (`already_reviewed`) surfaces as an `ApiError` with
 * the stable tag in its message, per this endpoint's documented failure
 * contract (a 422 `invalid_rating` is prevented client-side by the star
 * control never submitting anything outside 1-5).
 */
export type RlhfReviewSubmitResult = RlhfProposal & { sft_exported: boolean };

/** POST /rlhf/export-sft response. No request body. */
export interface RlhfSftExportResult {
  exported_count: number;
  file: string;
  proposal_ids: number[];
}

// ---------------------------------------------------------------------------
// GET /data/ai/disagreements — G15: durable per-symbol Claude-vs-Gemini
// verdict comparison. The legacy Streamlit AI Insights tab's "Aggregate
// Claude vs Gemini disagreement" table is built from TWO st.session_state
// mirrors that only exist within one browser session -- there is no durable
// backend source for THAT exact table. This endpoint answers the same
// question from a genuinely durable source instead: the on-disk LLM
// commentary cache (llm/cache.py's output/llm_commentary_cache.json) both
// the Claude-analyst-note and Gemini-chart-pattern buttons already write
// through. See pilots-parity plan G15 / api/data_api.py::get_ai_disagreements
// for the full honesty note. Distinct from StrategyHealth's existing
// GravityAiAuditStatus.ai_audit (a durably-computed AGGREGATE disagreement
// COUNT from the structural Gravity audit) -- this is a PER-SYMBOL table
// from real analyst/chart calls, a different thing entirely.
// ---------------------------------------------------------------------------

/** One symbol's Claude-vs-Gemini comparison. `claude_verdict`/`gemini_verdict`
 * are `null` -- never fabricated -- whenever that side was never generated
 * for the symbol (or its cache entry has since been cleared). `disagreement`
 * is `true` only when BOTH sides are present and differ. */
export interface AiDisagreementRow {
  symbol: string;
  advisory_action: string;
  claude_verdict: string | null;
  gemini_verdict: string | null;
  disagreement: boolean;
}

export interface AiDisagreementSummary {
  total_symbols: number;
  both_present: number;
  agreements: number;
  disagreements: number;
}

export interface AiDisagreementsResponse {
  rows: AiDisagreementRow[];
  summary: AiDisagreementSummary;
  reason: string | null; // present when rows is empty
}

// ---------------------------------------------------------------------------
// Prompt Registry (webapp parity gap G4) — GET /prompts, GET /prompts/{id},
// PUT /prompts/pin (api/pilots_api.py, backed by pilots/prompt_registry.py).
// ---------------------------------------------------------------------------

/**
 * One row of `GET /prompts` — a registered Prompt Registry entry's resolved
 * version/source/pin/cache state (`pilots/prompt_registry.py::list_prompts`).
 * `resolved_version`/`source` are `null` only when NOTHING resolves for this
 * id anywhere (no pin, no remote manifest, no disk cache, no committed
 * baseline) — in practice every committed baseline id always resolves, so
 * this is a genuinely rare state, not the common case (CONSTRAINT #4: never
 * fabricate a version when nothing actually resolved).
 */
export interface PromptEntry {
  id: string;
  resolved_version: string | null;
  source: "pin" | "remote" | "cache" | "baseline" | null;
  pinned_version: string | null;
  cached_version_count: number;
}

/** `GET /prompts` response. `reason` is non-null only when the registry
 *  itself could not be constructed, or no prompt IDs are known at all (e.g.
 *  an empty/corrupt committed baseline directory) — never on the common
 *  path. `enabled` mirrors `settings.PROMPT_REGISTRY_ENABLED`; a disabled
 *  registry still lists every baseline id (all resolve `source: "baseline"`),
 *  it just never attempted a remote fetch. `writable` tracks
 *  `PROMPT_REGISTRY_WRITES_ENABLED` (mirrors `StrategyMatrix.writable`) — the
 *  pin/clear-pin UI should disable itself rather than let the operator hit a
 *  surprise 403. */
export interface PromptListResponse {
  enabled: boolean;
  prompts: PromptEntry[];
  reason: string | null;
  writable: boolean;
  note: string;
}

/**
 * `GET /prompts/{id}?version=...` response — the resolved body for one
 * prompt ID, either via the full resolution chain (no `version` requested)
 * or one specific version (a cached version string, or the literal
 * `"baseline"`). `found: false` is an honest, structurally-expected outcome
 * (unknown id or version) — the endpoint never 404s for this (CONSTRAINT #4).
 * `source` is populated only for a full-resolution-chain lookup; a
 * specific-version lookup does not re-derive provenance (always `null`).
 * `cached_versions` (newest first) and `has_baseline` are populated on EVERY
 * call regardless of `found`/`version` — a diff-version picker needs the
 * full set of resolvable versions for this id up front, not just whichever
 * single version this particular call resolved.
 */
export interface PromptBody {
  id: string;
  version: string | null;
  found: boolean;
  body: string | null;
  source: "pin" | "remote" | "cache" | "baseline" | null;
  reason: string | null;
  cached_versions: string[];
  has_baseline: boolean;
}

/** Body for `PUT /prompts/pin`. `version: null` CLEARS any existing pin for
 *  `prompt_id` (resolves to remote latest / disk cache / baseline again on
 *  the next daemon restart) rather than pinning it. */
export interface PromptPinRequest {
  prompt_id: string;
  version: string | null;
}

/** `PUT /prompts/pin` response. `pins` echoes the FULL new
 *  `PROMPT_REGISTRY_PINS` map (the request's pin value merged onto the live
 *  `settings.PROMPT_REGISTRY_PINS` snapshot) — NEVER a stale post-write
 *  re-read of `settings` (which would return the OLD values and read as a
 *  failed write). `applies` is always `"next_daemon_restart"` — there is no
 *  live setter for `.env`-sourced config in this codebase. */
export interface PromptPinResult {
  prompt_id: string;
  version: string | null;
  pins: Record<string, string>;
  applies: "next_daemon_restart";
  note: string;
}

// ---------------------------------------------------------------------------
// Live Inventory sync write (webapp parity gap G8) + Market Data provider
// status (webapp parity gap G9) — api/data_api.py.
// ---------------------------------------------------------------------------

/**
 * `POST /data/sync` response — the HTTP port of the Streamlit Live Inventory
 * tab's "Sync Now" button. `default_tickers` is the full discovered universe
 * SUBMITTED for persistence to `DEFAULT_TICKERS` (a best-effort `.env` write
 * — the endpoint cannot confirm it actually landed, see its own docstring).
 * `report` is the SAME shape `GET /data/sync-report` returns, computed fresh
 * by this same call (never a stale re-read).
 */
export interface DataSyncResult {
  report: SyncReportResponse;
  default_tickers: string[];
  applies: "next_daemon_restart";
  note: string;
}

/**
 * `GET /data/provider-status` response — the active market-data provider,
 * delivery mode, and quote TTL (the HTTP port of the Streamlit Market Data
 * tab's provider/mode/TTL tiles). Connection-health sliding-window tracking
 * is DELIBERATELY NOT part of this response — it stays entirely client-side
 * (`components/MarketDataHealth.tsx`'s own session-local tracker); see that
 * endpoint's docstring for why a server-side tracker would be a confusing
 * second signal rather than a duplicate of the same one.
 */
export interface ProviderStatus {
  provider: string;
  is_realtime: boolean;
  mode: "real_time" | "delayed";
  quote_ttl_seconds: number;
  fundamentals_source: string;
}

/** Envelope used to distinguish "not run yet" (honest 404) from a hard error. */
export class ApiError extends Error {
  status: number;
  /**
   * Populated by client.ts's `http()` when a GET fails because the network is
   * unreachable (status 0) AND a previously cached response exists for that
   * path (see api/offlineCache.ts). `undefined` for every other error —
   * a reachable server's own 4xx/5xx is never masked by stale cache data.
   */
  cachedData?: unknown;
  cachedAt?: string;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Thrown by client.ts's `runForecastBackfill` (and mock.ts's equivalent)
 * when `POST /pilots/forecast_backfill/run` 409s because a run is already
 * in progress (`ml/forecast_backfill_job.py`'s single-flight guard,
 * surfaced by `api/pilots_api.py`'s `run_forecast_backfill_endpoint` as a
 * structured body: `{"detail": {"detail": "...", "job_id": "<id>"}}`).
 * Carries the EXISTING job's id so `useBackfillJob`'s `start()` can seed
 * `activeJobId` from it and poll that job's real progress instead of
 * surfacing a dead-end error — the backend endpoint's own stated reason for
 * including the id in the 409 body at all. A distinct subclass (rather than
 * a generic `ApiError`) so callers can `instanceof`-branch on it exactly
 * like `TriggerRunResult`'s `already_running` tag lets `RunNowButton`
 * branch on `POST /automation/run`'s 409, without needing a second return
 * shape for this endpoint's otherwise-`ForecastBackfillJob`-returning
 * success path.
 */
export class ForecastBackfillConflictError extends ApiError {
  existingJobId: string | null;
  constructor(message: string, existingJobId: string | null) {
    super(message, 409);
    this.name = "ForecastBackfillConflictError";
    this.existingJobId = existingJobId;
  }
}

// ---------------------------------------------------------------------------
// Report Library (GET /reports, GET /reports/{name}) + Dead-Letter Queue
// (GET /dead-letter, POST /dead-letter/retry) — parity gaps G5/G6.
// ---------------------------------------------------------------------------

/**
 * A report file's category — mirrors `gui/panels/reports_library.py`'s four
 * sections. `validation_summary` (parsed JSON) is rendered distinctly from
 * `validation_html` (a full harness HTML report) even though both live in
 * the same `reports/` directory.
 */
export type ReportKind =
  | "daily_report"
  | "dashboard"
  | "briefing"
  | "validation_summary"
  | "validation_html";

/** One row of `GET /reports`' manifest. `size`/`mtime` are `null` only on a
 * stat race (the file existed when globbed, vanished by the time it was
 * stat'd) — CONSTRAINT #4, never a fabricated 0/timestamp. */
export interface ReportFile {
  name: string;
  kind: ReportKind;
  size: number | null;
  mtime: string | null;
}

/**
 * `GET /reports` — every generated report file the Streamlit Report Library
 * tab enumerates: the daily report, the two orchestrator dashboards, daily
 * briefings, and validation reports. `reason` is set only when `reports` is
 * empty (CONSTRAINT #4 — never a fabricated list). `GET /reports/{name}`
 * resolves `name` back to one of these rows ONLY — the server never joins a
 * client-supplied string onto a filesystem path (see that endpoint's own
 * doc comment on `client.ts`).
 */
export interface ReportManifest {
  generated_at: string | null;
  reports: ReportFile[];
  reason: string | null;
}

/**
 * `GET /reports/{name}` — content for one report. Exactly one of
 * `text`/`json` is populated per `content_type`: `"html"`/`"markdown"`
 * kinds carry `text`; a validation summary carries a parsed `json` object.
 * `reason` is set (with both `text` and `json` left `null`) when the name
 * matched the manifest but the file failed to read/parse at content time (a
 * race, corrupt JSON) — CONSTRAINT #6, the server degrades honestly rather
 * than 500ing. A `name` absent from the manifest entirely surfaces as a 404
 * `ApiError`, not this shape — SECURITY: the server resolves `name` only by
 * exact match against its own server-built manifest, never by joining the
 * raw client string onto a path (mirrors `pilots.commands.resolve_command`'s
 * identical discipline for `POST /jobs`).
 */
export interface ReportContent {
  name: string;
  kind: ReportKind;
  content_type: "html" | "markdown" | "json";
  text: string | null;
  json: unknown | null;
  size: number | null;
  mtime: string | null;
  reason: string | null;
}

/**
 * One failed-symbol record from the last pipeline run's dead-letter queue
 * (`output/dead_letter.json`, written by `main_orchestrator.run_pipeline`).
 * NOTE: distinct from (and richer/typed vs.) the generic
 * `Record<string, unknown>[]` entries `AutomationStatus.errors` (its own
 * `DeadLetterReport` type, above) carries — that field is a capped,
 * generic-shaped error tail for the Data & Automation status view;
 * `GET /dead-letter` below is the Launcher tab's dead-letter QUEUE, scoped
 * to symbol/stage/error/timestamp specifically for the per-symbol Retry
 * control, so it gets its own, differently-named type here rather than
 * widening that one.
 */
export interface DeadLetterQueueEntry {
  symbol: string;
  stage: string;
  error: string;
  timestamp: string;
}

/**
 * `GET /dead-letter`. `is_clean` is `null` (NOT `true`) when no run has
 * completed yet — "no run yet" is not the same claim as "the last run was
 * clean" (CONSTRAINT #4). `retry_enabled` tracks
 * `settings.DEAD_LETTER_RETRY_ENABLED` so the PWA can hide/disable the Retry
 * control before the operator hits a 403 on `POST /dead-letter/retry`
 * (mirrors `StrategyMatrix`'s `writable` convention).
 */
export interface DeadLetterQueue {
  run_id: string | null;
  generated_at: string | null;
  entries: DeadLetterQueueEntry[];
  is_clean: boolean | null;
  reason: string | null;
  retry_enabled: boolean;
}

/**
 * `POST /dead-letter/retry` response. Re-runs `main.py` (advisory-only — no
 * orders) for exactly one symbol via the same subprocess launcher the
 * Streamlit Launcher tab's dead-letter Retry button already uses. Does not
 * wait for the run to finish — returns immediately with the spawned PID/log
 * path so the caller can poll/tail it. `applies: "immediately"` describes
 * the subprocess launch itself, never any order submission (there is none).
 */
export interface DeadLetterRetryResult {
  symbol: string;
  pid: number;
  log_path: string;
  applies: "immediately";
  note: string;
}

export interface MacroIndicatorItem {
  subject: string;
  value: number;
  trend: "up" | "down" | "flat";
}

export interface MacroSentimentResponse {
  // Real telemetry (VIX, Sahm Rule, High-Yield OAS, yield curve, market
  // regime) from output/state_snapshot.json, normalized against this
  // codebase's own kill-switch/regime thresholds — see
  // api/data_api.py::get_macro_sentiment. Empty when no snapshot exists yet
  // (see `reason`), not fabricated. Market Regime is present only when the
  // snapshot's regime string is a recognized value.
  macro_data: MacroIndicatorItem[];
  // False now that this reads real macro telemetry; kept (rather than
  // removed) for symmetry with the other synthetic-data endpoints and so a
  // future genuinely-fabricated variant would still have a place to signal it.
  is_synthetic: boolean;
  // Non-null (e.g. "No state snapshot yet — run the pipeline first.") when
  // macro_data is empty because there's nothing to read yet.
  reason: string | null;
}

export interface OrderBookLevel {
  price: number;
  size: number;
  type: "bid" | "ask";
}

export interface OrderBookLadderResponse {
  symbol: string;
  // Real quote when available (CompositeProvider), a fixed fallback otherwise.
  current_price: number;
  // Depth (bid/ask SIZES) is always synthetic -- no L2/consolidated order
  // book feed is wired in this codebase. is_synthetic covers the ladder as
  // a whole, not just current_price.
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  is_synthetic: boolean;
}

export interface ModelComparisonRow {
  name: string;
  [modelName: string]: string | number;
}

export interface ModelComparisonResponse {
  // Always empty from the live backend today -- "SF-GARCH-LSTM"/"Bond-BERT"
  // are undeployed ridge-regression stand-ins (see ml/models/sf_garch_lstm.py
  // / ml/models/bond_bert.py) with no tracked real return history to compare,
  // so this honestly reports no data rather than a fabricated curve.
  data: ModelComparisonRow[];
  is_synthetic: boolean;
}

export interface IntradayThetaPoint {
  time: string;
  hour: number;
  theta: number;
  gamma: number;
}

export interface OptionsAnalyticsSummaryResponse {
  symbol: string;
  net_dealer_premium: number | null;
  regime: string | null;
  intraday_series: IntradayThetaPoint[];
  is_synthetic: boolean;
}

export interface ForecastBackfillModelMetrics {
  accuracy: number;
  auc: number;
  n_train: number;
  n_test: number;
  split_date: string;
  is_active?: boolean;
}

export interface ForecastBackfillSummary {
  status?: string;
  timestamp?: string | null;
  horizons: number[];
  metrics: Record<string, ForecastBackfillModelMetrics>;
  tickers: string[];
  total_rows?: number;
  csv_path?: string;
  message?: string;
  /** Non-empty iff one or more tickers had no real FMP/provider data and were
   * dropped from the run. They are permanently removed from the watchlist after
   * 3 consecutive failures. */
  dropped_tickers?: string[];
}

export type ForecastBackfillJobState = "running" | "succeeded" | "failed" | "timeout" | "cancelled";

export type ForecastBackfillPhase =
  | "fetching_data"
  | "technical_features"
  | "primary_signals"
  | "meta_targets"
  | "backtraining"
  | "backfilling"
  | "exporting";

/** Only meaningful once `state` is a terminal failure state -- mirrors
 *  `ml/forecast_backfill_job.py`'s `BackfillErrorType` exactly. `"timeout"`/
 *  `"cancelled"` only ever accompany their same-named `state`; a `state:
 *  "failed"` job's `error_type` is always `"value_error"` (bad request
 *  parameters) or `"unexpected"` (a genuine training-time exception). */
export type ForecastBackfillErrorType = "value_error" | "unexpected" | "timeout" | "cancelled" | null;

/** Checkpoint snapshot from the last `{"event": "progress", ...}` NDJSON
 *  event the backend drained off the worker's events pipe before the job
 *  reached a terminal state -- mirrors `ml/forecast_backfill_job.py`'s
 *  `BackfillJobState.partial_summary` exactly (emitted after each step-5
 *  "backtraining" combo finishes training and its model is saved).
 *  `trained` and the keys of `metrics_so_far` are always the same set --
 *  `trained` is `sorted(metrics_so_far.keys())` on the backend. Always the
 *  LAST progress event received, never accumulated across events (the
 *  worker's own `metrics_so_far` is already the full cumulative snapshot at
 *  emit time). A deadline SIGKILL (`_enforce_deadline`) never touches this
 *  field, so whatever it last held survives the kill unchanged -- the whole
 *  point of the checkpoint. */
export interface ForecastBackfillPartialSummary {
  trained: string[];
  metrics_so_far: Record<string, ForecastBackfillModelMetrics>;
}

export interface ForecastBackfillJob {
  job_id: string;
  state: ForecastBackfillJobState;
  phase: ForecastBackfillPhase | null;
  step: number;
  total_steps: number;
  error: string | null;
  error_type: ForecastBackfillErrorType;
  summary: ForecastBackfillSummary | null;
  sample_rows: number | null;
  /** `null` when no `progress` event has ever been observed (e.g. the job
   *  was killed during steps 1-4, before any step-5 combo finished) --
   *  never fabricated. See `ForecastBackfillPartialSummary`. */
  partial_summary: ForecastBackfillPartialSummary | null;
  seconds_remaining: number;
}

export interface CacheLongShortConcentratedPosition {
  ticker: string;
  market_value: number;
  pct_equity: number;
}

export interface CacheLongShortSimulateRequest {
  ticker: string;
  allocation: number;
}

export interface CacheLongShortSimulateResult {
  found: boolean;
  reason: string | null;
  beta: number | null;
  proxy_ticker: string | null;
  correlation_coefficient: number | null;
}

export interface CacheLongShortStartRequest {
  ticker: string;
  proxy_ticker: string;
  allocation: number;
  correlation_coefficient: number;
}

export interface CacheLongShortStartResult {
  status: string;
  position_id: number;
  ticker: string;
}

export interface CacheLongShortDashboard {
  status: "enabled" | "disabled";
  tax_bank?: number;
  exposure?: {
    long_exposure: number;
    short_exposure: number;
    net_exposure: number;
    gross_exposure: number;
  };
}

export interface CacheLongShortPendingTrade {
  lot_id: number;
  position_id: number;
  cost_basis: number;
  unrealized_loss_pct: number | null;
}

export interface CacheLongShortApproveBulkResult {
  status: string;
  count: number;
}


export interface PaperBrokerAccount {
  equity: number;
  cash: number;
  buying_power: number;
}

export interface PaperBrokerPosition {
  symbol: string;
  qty: number;
  avg_cost: number;
  current_price: number | null;
  market_value: number | null;
  unrealized_pl: number | null;
  unrealized_pl_pct: number | null;
  strategy_id: string | null;
  pilot_id: string | null;
  experiment_arm: string | null;
}

export interface PaperBrokerOrder {
  order_id: string;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  price: number;
  status: "filled" | "pending" | "cancelled" | "rejected";
  filled_qty: number;
  filled_avg_price: number | null;
  created_at: string;
  strategy_id: string | null;
  pilot_id: string | null;
  experiment_arm: string | null;
}

// Realized-PnL history for a flattened/expired/rolled paper position --
// read path for paper_closed_trades (see data/paper_account_store.py's
// PaperClosedTrade / get_full_closed_trades). realized_pnl_pct and
// holding_period_days are genuinely nullable (CONSTRAINT #4 -- never
// fabricated to 0/0.0 on a degenerate avg_entry_price).
export interface PaperBrokerClosedTrade {
  trade_id: number;
  strategy_id: string | null;
  pilot_id: string | null;
  experiment_arm: string | null;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  entry_ts: string | null;
  entry_price: number;
  exit_ts: string;
  exit_price: number;
  commission: number;
  realized_pnl: number;
  realized_pnl_pct: number | null;
  holding_period_days: number | null;
  close_reason: string;
  leg_group_id: string | null;
}

export interface PaperBrokerResetResult {
  status: string;
  cash: number;
}

export interface StrategyOptionCandidate {
  symbol: string;
  strategy: string;
  action: string;
  net_premium: number | null;
  ivr: number | null;
  trend_bias: string;
  target_dte: number;
  legs: any[];
  // Stage 4 ML Meta-Labeler inference features -- null when unresolvable
  // (e.g. no macro/vrp context passed, or no short leg on this directive).
  // Not rendered anywhere today; present for mock/live parity.
  vrp?: number | null;
  vix?: number | null;
  short_delta?: number | null;
  credit_to_width_ratio?: number | null;
}

export interface StrategyOptionsCandidatesResponse {
  count: number;
  candidates: StrategyOptionCandidate[];
}

export interface StrategyOptionsExecutionResult {
  executed_count: number;
  skipped_count: number;
  failed_count: number;
  executed: Array<{
    order_id?: string;
    symbol: string;
    strategy: string;
    contracts: number;
    net_price: number;
    net_cash_impact: number;
    legs?: string[];
  }>;
  skipped: Array<{
    symbol: string;
    reason: string;
  }>;
  failed: Array<{
    symbol: string;
    reason: string;
  }>;
}

// `pilots/options_risk.py::calculate_position_greeks` returns every field
// below as `None` (not merely omitted) whenever a live spot quote for the
// position's underlying couldn't be resolved (`missing_data: true`) -- the
// position is still included in `PortfolioGreeks.positions` in that case
// (see `calculate_portfolio_greeks`'s `else` branch), so every numeric field
// here MUST tolerate `null` on live data. `positions_with_missing_data` on
// the parent `PortfolioGreeks` already names these symbols for the banner;
// `missing_data` lets a consumer of a single row detect it directly too.
export interface PositionGreekBreakdown {
  symbol: string;
  asset_type: "stock" | "option";
  base_ticker: string;
  expiration?: string;
  strike?: number;
  option_type?: "call" | "put";
  dte?: number;
  qty: number;
  spot_price: number | null;
  delta_per_unit: number | null;
  gamma_per_unit: number | null;
  theta_daily_per_unit: number | null;
  vega_1pct_per_unit: number | null;
  position_delta: number | null;
  position_dollar_delta: number | null;
  position_gamma: number | null;
  position_theta_daily: number | null;
  position_vega_1pct: number | null;
  market_value: number | null;
  missing_data?: boolean;
}

export interface PortfolioGreeks {
  total_positions: number;
  stock_positions_count: number;
  option_positions_count: number;
  net_delta_shares: number;
  net_dollar_delta: number;
  net_gamma: number;
  net_theta_daily: number;
  net_vega_1pct: number;
  beta_weighted_delta_spy: number;
  positions_with_missing_data?: string[];
  beta_excluded_symbols?: string[];
  positions: PositionGreekBreakdown[];
}




/**
 * A real live-trade order an MCP tool proposed for human approval. This
 * webapp screen (LiveTradeApprovals.tsx) IS the enforcement surface -- there
 * is no MCP-callable equivalent to approve/reject one.
 */
export interface LiveTradeProposal {
  token: string;
  symbol: string;
  side: string;
  qty: number;
  order_type: string;
  limit_price: number | null;
  strategy_id: string;
  proposed_at: string;
  expires_at: string;
  status: "pending_approval" | "approved" | "rejected" | "expired" | "executed" | "failed";
  approved_at: string | null;
  approved_by: string | null;
  broker_order_id: string | null;
  error_message: string | null;
}

export interface OptionContract {
  contractSymbol: string;
  strike: number;
  lastPrice: number;
  bid: number;
  ask: number;
  // `null` when the contract's volume/open-interest is genuinely unreported
  // (common for far-OTM/illiquid strikes) -- never fabricated to `0`, which
  // would be indistinguishable from a verified-zero reading.
  volume: number | null;
  openInterest: number | null;
  // `null` when the provider couldn't compute IV for this contract (common
  // for illiquid/wide-spread strikes) -- `api/data_api.py`'s `_clean_nan`
  // nulls any NaN float in the response, and yfinance's own IV solver
  // routinely fails to converge on thin contracts. Never fabricated to `0`.
  impliedVolatility: number | null;
  inTheMoney: boolean;
  greeks: {
    delta: number;
    gamma: number;
    theta: number;
    vega: number;
    rho: number;
    chanceOfProfit: number;
  };
}

export interface OptionChainResponse {
  symbol: string;
  expiration?: string;
  // Omitted entirely (not merely `null`) on the "list expirations" shape of
  // `GET /data/options/chain/{symbol}` (no `expiration` query param) -- that
  // response is just `{symbol, expirations}`. Only present once a specific
  // expiration has been requested and the backend resolved a live spot quote.
  spot_price?: number;
  expirations?: string[];
  calls?: OptionContract[];
  puts?: OptionContract[];
}

export interface OptionsOrderRequest {
  symbol: string;
  asset_type?: 'option' | 'stock';
  side?: 'buy' | 'sell';
  quantity?: number;
  dollar_amount?: number;
  order_type?: 'market' | 'limit';
  limit_price?: number;
  expiration?: string;
  legs?: {
    contract: OptionContract;
    type: 'call' | 'put';
    action: 'Buy' | 'Sell';
  }[];
  isLive: boolean;
}

export interface OptionsOrderResult {
  ok: boolean;
  order_id?: string;
  message: string;
}

export interface OptionsBacktestParams {
  strategy: string;
  ticker: string;
  start_date: string;
  end_date: string;
  initial_capital?: number;
}

export interface OptionsTradeLogItem {
  entry_date: string;
  exit_date: string;
  strategy: string;
  underlying_entry_price: number;
  underlying_exit_price: number;
  entry_net_premium: number;
  exit_net_cost: number;
  pnl_dollar: number;
  pnl_pct: number;
  exit_reason: string;
  holding_days: number;
  contracts: number;
}

export interface OptionsBacktestResponse {
  strategy_name: string;
  ticker: string;
  start_date: string;
  end_date: string;
  initial_capital: number;
  final_capital: number;
  total_return_pct: number;
  annualized_return_pct: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  max_drawdown_pct: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate_pct: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
  pbo: number;
  dsr: number;
  passes_stress: boolean;
  deployable: boolean;
  equity_curve: { date: string; value: number }[];
  trades: OptionsTradeLogItem[];
}

export interface OptionsMetaModelStatus {
  n_samples: number;
  train_accuracy: number;
  train_roc_auc: number;
  trained_at: string | null;
  enabled: boolean;
  // In-sample only -- train() has no purged/held-out evaluation. See
  // docs/known_issues/options_meta_labeler_serving_time_gaps.md.
  metrics_are_in_sample?: boolean;
}

export interface OptionsMetaModelRetrainResult {
  status: string;
  trained_samples: number;
  accuracy: number;
  roc_auc: number;
  trained_at: string;
  metrics_are_in_sample?: boolean;
}

export interface PaperBrokerSettleExpiredResult {
  settled_count: number;
  settled: any[];
}

export interface ScenarioMatrixCell {
  spot_shift_pct: number;
  iv_shift_pct: number;
  days_forward: number;
  spot_price?: number;
  portfolio_value: number;
  pnl_dollar: number;
  pnl_pct: number;
  net_delta: number;
  net_gamma: number;
  net_theta: number;
  net_vega: number;
}

export interface HistoricalScenarioPreset {
  id: string;
  name: string;
  description: string;
  spot_shift_pct: number;
  iv_shift_pct: number;
  projected_pnl_dollar: number;
  projected_pnl_pct: number;
}

export interface ScenarioMatrixResponse {
  spot_shifts: number[];
  iv_shifts: number[];
  time_slices: number[];
  matrix: ScenarioMatrixCell[];
  historical_scenarios?: HistoricalScenarioPreset[];
  current_portfolio_value: number;
}

export interface VolSmilePoint {
  strike: number;
  iv: number;
  moneyness: number;
  call_bid?: number;
  call_ask?: number;
  put_bid?: number;
  put_ask?: number;
}

export interface VolTermStructurePoint {
  expiration: string;
  dte: number;
  atm_iv: number;
  historical_realized_vol_30d?: number;
}

export interface SkewData {
  // Backend (pilots/volatility_surface.py::to_vol_surface_response) omits any of these
  // that its degenerate-input guards (compute_25delta_skew) or a missing smile fit
  // produced as None -- never fabricated. Treat every field here as possibly absent.
  skew_25delta?: number;
  put_25delta_iv?: number;
  call_25delta_iv?: number;
  atm_iv?: number;
  vrp_spread?: number;
  realized_vol_10d?: number;
  realized_vol_20d?: number;
  realized_vol_30d?: number;
  realized_vol_60d?: number;
}

export interface VolSurfaceResponse {
  symbol: string;
  spot_price: number;
  as_of: string;
  expirations: string[];
  selected_expiration?: string;
  smile_points: VolSmilePoint[];
  term_structure: VolTermStructurePoint[];
  skew: SkewData;
}

// Matches `pilots/options_hedging.py::get_delta_hedge_preview`'s real return
// shape exactly -- the previous version of this interface (net_delta_shares,
// spy_spot_price, required_hedge_shares, hedge_symbol, estimated_cost,
// is_within_tolerance, action including "NONE") named fields the backend
// never returns at all, which produced a live `undefined.toFixed()` crash
// on this screen. `shares`/`target_hedge_shares` are always non-negative
// resp. signed real numbers (never missing) when `available` is true --
// `calculate_delta_hedge_order` always resolves them from computed
// portfolio Greeks, no live-quote gap in that case.
//
// `available: false` is the honest-refusal state (CONSTRAINT #4): the
// backend could not resolve a live SPY quote and refuses to fabricate one
// (it no longer falls back to a hardcoded 500.0) -- every Greek/hedge field
// that would otherwise be derived from that price is `null` rather than a
// fake number. Modeled as a discriminated union on `available` so a caller
// that checks it first gets real TS narrowing instead of needing `!`
// assertions everywhere.
export interface DeltaHedgePreviewAvailable {
  symbol: string;
  available: true;
  net_dollar_delta: number;
  beta_weighted_delta_spy: number;
  /** Signed shares needed to zero out beta-weighted SPY delta (not an order size -- use `shares` for that). */
  target_hedge_shares: number;
  tolerance_band_shares: number;
  /** "HOLD" means no rebalance is needed (deadband not exceeded) -- there is no "NONE" value. */
  action: "HOLD" | "BUY" | "SELL";
  /** Non-negative order size to execute; 0 when action is "HOLD". */
  shares: number;
  required_action: boolean;
  reason: string;
  spy_spot: number;
}

export interface DeltaHedgePreviewUnavailable {
  symbol: string;
  available: false;
  net_dollar_delta: null;
  beta_weighted_delta_spy: null;
  target_hedge_shares: null;
  tolerance_band_shares: number;
  action: "HOLD";
  shares: 0;
  required_action: false;
  reason: string;
  spy_spot: null;
}

export type DeltaHedgePreview = DeltaHedgePreviewAvailable | DeltaHedgePreviewUnavailable;

// Matches `pilots/options_hedging.py::execute_delta_hedge`'s real return
// shape -- there is no top-level `price`/`side` field (only `action`, and a
// fill price nested under `fill.fill_price`); most fields are absent on the
// rare "PaperAccountStore unavailable" failure path, so `message` is the
// only field this screen may treat as always-present.
export interface DeltaHedgeResult {
  ok: boolean;
  hedged?: boolean;
  action?: "HOLD" | "BUY" | "SELL";
  shares?: number;
  symbol?: string;
  order_id?: string | null;
  reason?: string;
  message?: string;
}

/** One leg of a roll's close/open list -- mirrors api/pilots_api.py's
 * RollOrderRequest.close_legs/open_legs (List[Dict[str, Any]], read by
 * PaperAccountStore.apply_roll_fill). `symbol` is the full option-leg
 * symbol string ("{TICKER} {YYYY-MM-DD} ${STRIKE} {CALL|PUT}"). */
export interface RollOrderLeg {
  symbol: string;
  side: "buy" | "sell";
  qty: number;
}

/** Matches api/pilots_api.py's RollOrderRequest exactly -- `symbol` plus the
 * explicit close/open leg lists PaperAccountStore.apply_roll_fill requires,
 * not a same-symbol target_expiration/target_strike shorthand the backend
 * has no field for. */
export interface RollOrderRequest {
  symbol: string;
  close_legs: RollOrderLeg[];
  open_legs: RollOrderLeg[];
  limit_price?: number;
  contracts?: number;
  order_type?: string;
  is_live?: boolean;
}

export interface ClosedExitPosition {
  symbol: string;
  qty: number;
  reason: "PROFIT_TARGET_50" | "STOP_LOSS_200" | "DTE_EXPIRY_21" | "MANUAL";
  pnl_dollar: number;
  pnl_pct: number;
  closed_at_price: number;
}

export interface ManageExitsResult {
  evaluated_count: number;
  closed_count: number;
  closed_positions: ClosedExitPosition[];
  message: string;
}

export interface EarningsCrushCandidate {
  symbol: string;
  company_name?: string;
  report_date: string;
  report_timing?: "AMC" | "BMO" | "DURING_HOURS";
  spot_price: number;
  atm_iv: number;
  dte: number;
  expected_move_dollar: number;
  expected_move_pct: number;
  median_realized_move_pct: number;
  crush_edge_ratio: number;
  suggested_strategy: string;
  call_wing_strike?: number;
  put_wing_strike?: number;
  short_call_strike?: number;
  short_put_strike?: number;
  expiration?: string;
  estimated_credit?: number;
  edge_passed?: boolean;
  historical_moves?: number[];
}

export interface EarningsCrushCandidatesResponse {
  candidates: EarningsCrushCandidate[];
  count: number;
  as_of?: string;
  degraded?: boolean;
  symbols_errored?: string[];
}

export interface EarningsCrushExecutionResult {
  ok: boolean;
  order_id?: string;
  symbol: string;
  strategy: string;
  net_credit?: number;
  message: string;
  placed_at?: string;
}

export interface UnusualOptionTrade {
  id?: string;
  contract_symbol?: string;
  symbol: string;
  timestamp: string;
  option_type: "CALL" | "PUT" | "call" | "put" | string;
  strike: number;
  expiration: string;
  dte?: number;
  trade_type?: "SWEEP" | "BLOCK" | "SPLIT" | "ask_sweep" | "bid_sweep" | "mid_block" | "block" | string;
  aggressiveness?: string;
  sentiment: "BULLISH" | "BEARISH" | "NEUTRAL" | string;
  aggressor_side?: "ASK" | "BID" | "MID" | string;
  volume: number;
  open_interest: number;
  vol_oi_ratio: number;
  price: number;
  trade_price?: number;
  spot_price?: number;
  notional: number;
  underlying_notional?: number;
  iv?: number;
  implied_volatility?: number;
  hv_30?: number;
  historical_vol_30d?: number;
  iv_burst_score?: number;
  iv_expansion_flag?: boolean;
  price_is_estimated?: boolean;
  spot_price_is_estimated?: boolean;
}

export interface UnusualOptionsFlowResponse {
  trades: UnusualOptionTrade[];
  records?: UnusualOptionTrade[];
  count: number;
  as_of?: string;
  degraded?: boolean;
  symbols_fetch_failed?: string[];
}

export interface FlowSentimentData {
  symbol: string;
  sentiment_score: number;
  bullish_notional: number;
  bearish_notional: number;
  total_notional: number;
  call_volume: number;
  put_volume: number;
  put_call_ratio: number;
  top_active_strikes?: { strike: number; option_type: "CALL" | "PUT"; notional: number }[];
}

export interface FlowSentimentResponse {
  sentiment: FlowSentimentData;
  as_of?: string;
}

export interface HarRvForecastResponse {
  symbol: string;
  // pilots/har_volatility.py::to_har_rv_forecast_response only sets this when a real
  // trailing close price was resolved (never a fabricated quote -- CONSTRAINT #4);
  // absent on the parametric-fallback (offline/thin-history) path.
  spot_price?: number;
  as_of: string;
  rv_daily: number;
  rv_weekly: number;
  rv_monthly: number;
  forecast_vol_1d: number;
  forecast_vol_5d: number;
  forecast_vol_22d: number;
  forecast_vol_30d: number;
  gjr_garch_vol?: number;
  fair_iv_blend: number;
  coefficients: {
    beta_0: number;
    beta_d: number;
    beta_w: number;
    beta_m: number;
  };
  r_squared?: number;
  annualized_rv_1d?: number;
  annualized_rv_5d?: number;
  annualized_rv_22d?: number;
}

export interface VolMispricingStrike {
  strike: number;
  option_type: "CALL" | "PUT";
  market_iv: number;
  fair_iv: number;
  iv_spread: number;
  // pilots/vol_mispricing.py's StrikeMispricingRecord has no z-score field -- never
  // fabricated (CONSTRAINT #4), so this is always absent from the live response.
  spread_zscore?: number;
  // Real values: "RICH" | "CHEAP" | "NEUTRAL", plus "UNKNOWN" when the spread itself
  // was uncomputable (see classify_strike_mispricing()) -- NOT "FAIR".
  classification: "RICH" | "CHEAP" | "NEUTRAL" | "UNKNOWN";
  suggested_action: "SELL_PREMIUM" | "BUY_GAMMA" | "HOLD" | "NEUTRAL";
  bid?: number;
  ask?: number;
  mid?: number;
  delta?: number;
  gamma?: number;
  vega?: number;
  theta?: number;
  suggested_trade?: string;
}

export interface VolMispricingResponse {
  symbol: string;
  spot_price: number;
  expiration: string;
  expirations: string[];
  dte: number;
  // pilots/vol_mispricing.py's baseline_fair_iv/market_atm_iv are genuinely
  // Optional[float] -- uncomputable on a degenerate/empty chain (CONSTRAINT #4: never
  // fabricated as 0).
  fair_iv_baseline?: number;
  market_atm_iv?: number;
  rich_strikes_count: number;
  cheap_strikes_count: number;
  strikes: VolMispricingStrike[];
  trade_recommendations: {
    strategy: string;
    direction: "SELL_VOL" | "BUY_VOL";
    strikes: number[];
    reason: string;
    estimated_edge_pct: number;
  }[];
  as_of: string;
}

export interface GammaScalpRequest {
  symbol: string;
  spot_price: number;
  option_type: "CALL" | "PUT" | "STRADDLE";
  strike: number;
  expiration?: string;
  dte?: number;
  iv?: number;
  contracts: number;
  delta_threshold: number;
  simulation_steps?: number;
  drift?: number;
  realized_vol?: number;
  underlying_price_path?: number[];
}

export interface GammaScalpHedgeTrade {
  step: number;
  timestamp: string;
  spot_price: number;
  pre_delta: number;
  post_delta: number;
  shares_traded: number;
  side: "BUY" | "SELL" | "HOLD";
  trade_price: number;
  cash_flow: number;
  stock_position: number;
  option_mtm: number;
  total_pnl: number;
  gamma_rent_cumulative: number;
  theta_decay_cumulative: number;
}

export interface GammaScalpResponse {
  symbol: string;
  spot_price: number;
  initial_delta: number;
  initial_gamma: number;
  initial_theta: number;
  total_trades: number;
  rebalance_count: number;
  delta_threshold: number;
  total_pnl: number;
  gamma_rent_total: number;
  theta_burn_total: number;
  stock_pnl: number;
  option_pnl: number;
  transaction_costs: number;
  net_edge: number;
  trades: GammaScalpHedgeTrade[];
  price_path: number[];
  pnl_path: {
    step: number;
    spot: number;
    total_pnl: number;
    gamma_rent: number;
    theta_decay: number;
    option_mtm: number;
    stock_pnl: number;
  }[];
}

export interface OptionsAlertTestResult {
  ok: boolean;
  dispatched_count: number;
  channels: string[];
  results: {
    channel: string;
    status: "SENT" | "SIMULATED" | "FAILED";
    message?: string;
  }[];
  as_of?: string;
}

export interface DispersionConstituent {
  symbol: string;
  weight: number;
  spot_price: number;
  atm_iv: number;
  // Not computed by the backend (only the cross-sectional `realized_correlation` is) --
  // always null on a real response. See pilots/dispersion_trading.py::_opportunity_to_frontend_card.
  realized_vol_30d: number | null;
  straddle_strike: number;
  straddle_bid: number | null;
  straddle_ask: number | null;
  straddle_mid: number;
  vega_per_straddle: number;
  contracts_allocated: number;
  leg_action: "BUY" | "SELL";
  implied_rv_spread?: number | null;
}

export interface DispersionOpportunity {
  id: string;
  index_symbol: string;
  index_name?: string | null;
  index_spot: number;
  index_iv: number;
  // Not computed by the backend -- always null on a real response.
  index_rv_30d: number | null;
  index_straddle_strike: number;
  index_straddle_price: number;
  index_straddle_contracts: number;
  index_action: "SELL" | "BUY";
  implied_correlation: number;
  realized_correlation: number;
  correlation_spread: number;
  regime: "LONG_DISPERSION" | "SHORT_DISPERSION" | "NEUTRAL";
  trade_recommendation: string;
  index_vega_total: number;
  constituents_vega_total: number;
  net_vega: number;
  vega_neutrality_ratio: number;
  net_premium_estimate: number;
  expiration: string;
  dte: number;
  constituents: DispersionConstituent[];
  as_of?: string | null;
}

export interface DispersionBasketResponse {
  opportunities: DispersionOpportunity[];
  count: number;
  as_of?: string;
}

export interface DispersionBasketOrderRequest {
  opportunity_id?: string;
  index_symbol: string;
  regime?: string;
  basket_size_usd?: number;
  constituents?: string[];
  notes?: string;
}

export interface DispersionExecutionResult {
  ok: boolean;
  basket_id?: string;
  index_symbol: string;
  index_order_id?: string;
  constituent_order_ids?: string[];
  strategy: string;
  net_credit_debit: number;
  legs_count: number;
  message: string;
  placed_at?: string;
}

export interface ZeroDteContract {
  option_type: "CALL" | "PUT";
  strike: number;
  expiration: string;
  dte: number;
  delta: number;
  gamma?: number | null;
  theta?: number | null;
  vega?: number | null;
  bid: number;
  ask: number;
  mid: number;
  // Not computed by the backend on a candidate 0DTE contract -- always null on a real response.
  implied_vol: number | null;
  target_price: number;
  stop_loss_price: number;
  hard_exit_time: string;
}

export interface ZeroDteSignal {
  symbol: string;
  spot_price: number;
  timestamp: string;
  // Only populated when a real 15-minute opening range was computed; this repo has no
  // intraday/1-minute bar source today, so these are null on every real response
  // (see pilots/zero_dte_engine.py::get_0dte_signals's own docstring).
  opening_range_high: number | null;
  opening_range_low: number | null;
  opening_range_width_pct: number | null;
  ttm_squeeze_active: boolean;
  // Not computed by the backend -- always null on a real response.
  ttm_squeeze_bars: number | null;
  momentum_direction: "BULLISH_BREAKOUT" | "BEARISH_BREAKDOWN" | "IN_RANGE";
  momentum_score: number | null;
  // Not computed by the backend -- always null on a real response.
  relative_volume_15m: number | null;
  suggested_action: "BUY_CALL" | "BUY_PUT" | "WAIT";
  recommended_contract?: ZeroDteContract | null;
  trigger_reason?: string;
}

export interface ZeroDteSignalResponse {
  signals: ZeroDteSignal[];
  symbol?: string;
  as_of?: string;
}

export interface ZeroDteTradeRequest {
  symbol: string;
  signal_id?: string;
  option_type: "CALL" | "PUT";
  strike: number;
  contracts: number;
  entry_price?: number;
  profit_target_pct?: number;
  stop_loss_pct?: number;
  hard_exit_time?: string;
}

export interface ZeroDteExecutionResult {
  ok: boolean;
  order_id?: string;
  symbol: string;
  option_type: "CALL" | "PUT";
  strike: number;
  contracts: number;
  fill_price: number;
  profit_target_price: number;
  stop_loss_price: number;
  hard_exit_time: string;
  strategy: string;
  message: string;
  placed_at?: string;
}

export interface VpinBucket {
  bucket_index: number;
  buy_volume: number;
  sell_volume: number;
  total_volume: number;
  price_start: number;
  price_end: number;
  price_change: number;
  imbalance: number;
  timestamp?: string;
}

export interface VpinMetricsResponse {
  symbol: string;
  // `null` when `data_available` is false -- the backend fetches real underlying bars to
  // compute this (a bar-level BVC approximation, not a fabricated/synthetic value) and is
  // honest about it when that fetch fails rather than substituting a plausible-looking number.
  vpin: number | null;
  regime: "LOW" | "MODERATE" | "HIGH_TOXICITY" | null;
  // Not computed by the backend (no historical VPIN distribution to rank against) -- always
  // null/omitted on a real response.
  toxicity_percentile?: number | null;
  bucket_size: number;
  num_buckets: number;
  buckets: VpinBucket[];
  defensive_spread_concession?: number | null;
  warning_message?: string | null;
  as_of?: string;
  // Whether `vpin` reflects a real bar-level BVC approximation computed from live market data
  // (see `data_source`) or is honestly unavailable (see `reason`) -- added so the UI can never
  // mistake a missing measurement for a real one.
  data_available?: boolean;
  data_source?: "bar_level_bvc_approximation" | null;
  reason?: string | null;
}

export interface SorLeg {
  symbol?: string;
  option_type: "CALL" | "PUT";
  strike: number;
  expiration?: string;
  action: "BUY" | "SELL";
  ratio?: number;
  bid?: number;
  ask?: number;
  mid?: number;
}

export interface SorLegBreakdown {
  strike: number;
  option_type: "CALL" | "PUT";
  action: "BUY" | "SELL";
  bid: number;
  ask: number;
  mid: number;
  fill_priority: number;
  fill_style: "PASSIVE" | "ACTIVE";
}

export interface SorAnalysisRequest {
  symbol: string;
  spot_price?: number;
  legs: SorLeg[];
  latency_ms?: number;
  order_size?: number;
}

export interface SorAnalysisResponse {
  symbol: string;
  recommended_route: "COB_NET_PACKAGE" | "LEG_PASSIVE_FIRST" | "SPLIT_DIRECT";
  cob_net_price: number;
  cob_natural_price: number;
  synthetic_net_price: number;
  expected_savings: number;
  hung_leg_probability: number;
  adverse_selection_cost: number;
  latency_ms: number;
  legs_breakdown: SorLegBreakdown[];
  rationale: string;
  as_of?: string;
}

export interface LeggingSimulationRequest {
  symbol: string;
  spot_price?: number;
  volatility?: number;
  latency_seconds?: number;
  num_simulations?: number;
  legs?: {
    strike: number;
    option_type: "CALL" | "PUT";
    action: "BUY" | "SELL";
    bid?: number;
    ask?: number;
    mid?: number;
  }[];
}

export interface LeggingSimulationResponse {
  symbol: string;
  num_simulations: number;
  latency_seconds: number;
  hung_leg_rate: number;
  expected_edge_dollars: number;
  edge_std_dollars: number;
  worst_case_loss_dollars: number;
  p95_adverse_selection: number;
  pnl_distribution: {
    bin_edge: number;
    count: number;
    probability: number;
  }[];
  latency_curve: {
    latency_ms: number;
    hung_leg_rate: number;
    expected_edge: number;
  }[];
  as_of?: string;
}

export interface GexStrikePoint {
  strike: number;
  call_gex: number;
  put_gex: number;
  net_gex: number;
  total_oi?: number;
  call_oi?: number;
  put_oi?: number;
  call_volume?: number;
  put_volume?: number;
  abs_gex?: number;
  gamma_concentration_pct?: number;
}

export interface GexProfileResponse {
  symbol: string;
  spot_price: number;
  net_gex: number;
  total_call_gex?: number;
  total_put_gex?: number;
  // null when the backend could not resolve a real spot price/options chain for the symbol
  // (calculate_gex_profile's degenerate-spot-price path) -- never fabricated.
  zero_gamma_flip: number | null;
  call_wall_strike: number | null;
  put_wall_strike: number | null;
  gamma_regime: "POSITIVE_GAMMA" | "NEGATIVE_GAMMA" | "PIN_RISK_HIGH";
  regime_description: string;
  dealer_hedging_flow: number;
  dealer_hedging_per_1pct_move_dollars?: number;
  dealer_hedging_shares_per_1pct_move?: number;
  strikes: GexStrikePoint[];
  as_of?: string;
  spot_price_source?: string;
  chain_source?: string;
}

export interface LobQueueSimulationRequest {
  symbol?: string;
  price_level: number;
  order_size: number;
  depth_ahead: number;
  lambda_limit?: number;
  mu_cancel?: number;
  theta_market?: number;
  time_horizon_sec?: number;
  num_simulations?: number;
}

export interface LobQueuePercentiles {
  p10: number | null;
  p25: number | null;
  p50: number | null;
  p75: number | null;
  p90: number | null;
  p95: number | null;
}

export interface LobQueueSimulationResponse {
  valid: boolean;
  symbol: string;
  price_level: number;
  order_size: number;
  depth_ahead: number;
  time_horizon_sec: number;
  num_simulations: number;
  fill_probability: number;
  expected_fill_time_sec: number | null;
  // Same underlying value as `expected_fill_time_sec` (an alias the backend also echoes under
  // this key) -- null whenever no simulated path filled within the time horizon (e.g. a large
  // queue relative to time_horizon_sec/theta_market), never fabricated.
  expected_wait_time_sec: number | null;
  unconditional_fill_time_sec: number;
  median_fill_time_sec: number | null;
  prob_adverse_move_before_fill: number;
  expected_fill_ratio: number;
  queue_depletion_velocity: number;
  queue_progression_percentiles: LobQueuePercentiles;
  cst_closed_form_fill_prob?: number | null;
  reason?: string | null;
  timestamp: string;
  as_of?: string;
}

export type CopulaFamily = "Clayton" | "Gumbel" | "Frank" | "Gaussian" | "Student-t";

export interface CopulaTailData {
  lower_tail_dependence: number;
  upper_tail_dependence: number;
  copula_family: CopulaFamily;
  theta: number;
  log_likelihood: number;
  aic: number;
  kendall_tau: number;
}

export interface CopulaSeriesPoint {
  date: string;
  asset_x_price: number;
  asset_y_price: number;
  kalman_beta: number;
  spread: number;
  spread_z_score: number;
  upper_band_2sigma: number;
  lower_band_2sigma: number;
}

export interface CopulaPairsResponse {
  pair: string;
  asset_x: string;
  asset_y: string;
  copula_family: CopulaFamily;
  tail_dependence: CopulaTailData;
  kalman_beta: number;
  kalman_alpha: number;
  ou_half_life_days: number;
  spread_z_score: number;
  current_spread: number;
  signal_action: "LONG_SPREAD" | "SHORT_SPREAD" | "HOLD" | "EXIT";
  historical_series: CopulaSeriesPoint[];
  as_of?: string;
  status_note?: string;
  is_synthetic?: boolean;
}

export interface MarketMakerStepPoint {
  step: number;
  time_sec: number;
  mid_price: number;
  reservation_price: number;
  bid_price: number;
  ask_price: number;
  bid_spread: number;
  ask_spread: number;
  inventory: number;
  cash: number;
  pnl: number;
  trade_event?: "BUY" | "SELL" | null;
}

export interface MarketMakerSimRequest {
  symbol: string;
  spot_price?: number;
  risk_aversion_gamma?: number;
  order_flow_intensity_kappa?: number;
  volatility_sigma?: number;
  time_horizon_t?: number;
  time_steps?: number;
  max_inventory?: number;
  order_size?: number;
}

export interface MarketMakerSimResponse {
  symbol: string;
  risk_aversion_gamma: number;
  order_flow_intensity_kappa: number;
  volatility_sigma: number;
  max_inventory: number;
  final_pnl: number;
  sharpe_ratio: number;
  max_drawdown: number;
  total_trades: number;
  /** 0-1 fraction (matches ml/drl_market_maker.py's `total_trades / max(1, 2 * n_steps)`), NOT a 0-100 percentage. Multiply by 100 at render time. */
  fill_rate: number;
  final_inventory: number;
  avg_spread: number;
  steps: MarketMakerStepPoint[];
  as_of?: string;
}

export interface TransformerForecastResponse {
  symbol: string;
  forecast: Record<string, number>;
  quantile_forecast?: Record<string, { q10: number; q50: number; q90: number }>;
  attention_heatmap: number[][];
  trained_samples?: number;
  macro_conditioned?: boolean;
}

export interface DiffusionStressRequest {
  symbol: string;
  spot_price: number;
  volatility: number;
  drift?: number;
  num_paths?: number;
  horizon?: number;
  regime?: "unconditional" | "vol_shock" | "credit_freeze" | "stagflation" | "liquidity_squeeze";
  guidance_scale?: number;
}

export interface DiffusionStressResponse {
  symbol: string;
  regime?: string;
  guidance_scale?: number;
  paths: number[][];
  VaR_95: number;
  CVaR_95: number;
  VaR_99?: number;
  CVaR_99?: number;
  trained_windows?: number;
  /** True when real per-window macro-regime labels were derived and passed
   * into training (Phase 34 remediation item 11); false/absent means the
   * model trained unconditionally (e.g. macro-regime derivation failed or
   * degraded -- see api/pilots_api.py's _derive_diffusion_regime_labels). */
  regime_conditioned?: boolean;
}

export interface HrpCvarOptimizeRequest {
  symbols: string[];
  target_return?: number;
  risk_aversion?: number;
  current_weights?: Record<string, number>;
  lambda_turnover?: number;
  sector_caps?: Record<string, number>;
  target_beta_range?: [number, number] | number[];
  sector_map?: Record<string, string>;
  asset_betas?: Record<string, number>;
  max_asset_weight?: number;
}

export interface HrpCvarClusterNode {
  name: string;
  children?: HrpCvarClusterNode[];
  distance?: number;
}

export interface HrpCvarAllocation {
  symbol: string;
  weight: number;
}

export interface HrpCvarOptimizeResponse {
  allocations: HrpCvarAllocation[];
  dendrogram: HrpCvarClusterNode;
  expected_return: number;
  cvar_95: number;
  sharpe_ratio: number;
  turnover: number;
  portfolio_beta: number;
  sector_exposures: Record<string, number>;
  diversification_ratio: number;
  /** Whether the SLSQP solve actually converged ("optimal") or fell back to the
   * clipped/normalized initial HRP guess ("fallback", e.g. an infeasible sector-cap
   * or beta-range combination). See sizing/hrp_cvar_optimizer.py's
   * optimize_turnover_regularized_hrp_cvar -- this was previously computed but never
   * returned by the API, making a non-convergent solve indistinguishable from a
   * clean optimum (2026-08 math-audit finding). */
  status: "optimal" | "fallback";
  /** Whether HRP quasi-diagonalization itself (the clustering step, independent of
   * the SLSQP solve above) fell back to equal-weight. */
  hrp_fallback?: boolean;
  as_of?: string;
}

export interface AlmgrenChrissOptimizeRequest {
  symbol: string;
  quantity: number;
  risk_aversion?: number;
  volatility?: number;
  liquidity?: number;
  horizon_steps?: number;
}

export interface AlmgrenChrissTrajectoryPoint {
  step: number;
  shares_remaining: number;
  trade_size: number;
  // Null when no live quote was available for the requested symbol -- the
  // impact-adjusted price is never fabricated off a placeholder base price
  // (CONSTRAINT #4). See `spot_price`/`spot_price_reason` on the parent
  // response for why.
  expected_price: number | null;
}

export interface AlmgrenChrissOptimizeResponse {
  symbol: string;
  trajectory: AlmgrenChrissTrajectoryPoint[];
  expected_trajectory: AlmgrenChrissTrajectoryPoint[];
  expected_shortfall: number;
  variance: number;
  half_life: number;
  // Real current spot price used as the base for every trajectory point's
  // `expected_price`; null when no live quote was available for `symbol`
  // (see `spot_price_reason` for why).
  spot_price?: number | null;
  spot_price_reason?: string | null;
  as_of?: string;
}

export interface FixRouteFill {
  venue: string;
  fill_qty: number;
  fill_price: number;
  fee: number;
  rebate: number;
  latency_ms: number;
  exec_id: string;
  ord_status: string;
  raw_fix: string;
}

export interface FixRouteOrderRequest {
  symbol: string;
  side: string;
  quantity: number;
  limit_price: number;
  routing_policy?: string;
}

export interface FixRouteOrderResponse {
  symbol: string;
  side: string;
  quantity: number;
  limit_price: number;
  routing_policy: string;
  status: string;
  total_filled_qty: number;
  leaves_qty: number;
  weighted_avg_price: number;
  total_net_fee: number;
  total_rebates: number;
  total_cost: number;
  avg_latency_ms: number;
  max_latency_ms: number;
  fills: FixRouteFill[];
  nbbo: any;
  fix_audit_log: string[];
}

export type FixSessionState =
  | 'DISCONNECTED'
  | 'CONNECTING'
  | 'LOGON_SENT'
  | 'LOGON_RECEIVED'
  | 'ACTIVE'
  | 'RESEND_REQUESTED'
  | 'GAP_FILL_PROCESSING'
  | 'LOGOUT_SENT'
  | 'SUSPENDED';

export interface FixVenueRoutingStat {
  venue: string;
  market_center?: string;
  status: 'ACTIVE' | 'DEGRADED' | 'HALTED';
  base_latency_ms: number;
  current_latency_ms?: number;
  // api/pilots_api.py's GET /pilots/execution/fix/session/status builds this
  // from MultiVenueAggregator.get_venues_info(), which has no per-venue
  // execution history in this stateless aggregator -- fill_rate_pct is
  // always sent as `null`, exactly like current_latency_ms/share_of_flow_pct
  // below (CONSTRAINT #4: honestly unknown, never fabricated).
  fill_rate_pct?: number;
  maker_fee: number;
  taker_fee: number;
  maker_rebate?: number;
  liquidity_depth: number;
  share_of_flow_pct?: number;
}

export interface FixSessionStatusResponse {
  session_id: string;
  state: FixSessionState;
  in_seq_num: number;
  out_seq_num: number;
  sender_comp_id: string;
  target_comp_id: string;
  gap_queue_depth: number;
  last_heartbeat_at: string | null;
  venues_active: string[];
  heartbeat_int?: number;
  session_uptime_sec?: number;
  venue_stats?: FixVenueRoutingStat[];
  audit_log?: string[];
}

export interface FixSessionControlResponse {
  status: 'ok' | 'error' | string;
  message: string;
  session_state: FixSessionState;
  in_seq_num?: number;
  out_seq_num?: number;
  test_req_id?: string;
  round_trip_ms?: number;
  new_seq_num?: number;
}

export interface FixTestRequestPayload {
  test_req_id?: string;
}

export interface FixResetSeqRequest {
  new_seq_num: number;
  gap_fill?: boolean;
}

// ============================================================================
// Tier D: AI Research Copilot & Autonomous Backtesting Types
// ============================================================================

export interface ResearchSynthesizeRequest {
  prompt: string;
  strategy_type?: string;
  target_asset_class?: string;
}

export interface ResearchSynthesizeResponse {
  success: boolean;
  code: string;
  metadata: Record<string, any>;
  validation_passed: boolean;
  validation_errors: string[];
  source_prompt: string;
  synthesis_mode: string;
  explanation: string;
  target_asset_class?: string | null;
  strategy_type?: string | null;
}

export interface AutonomousBacktestRequest {
  strategy_code: string;
  strategy_id?: string;
  symbols?: string[];
  start_date?: string;
  end_date?: string;
  initial_capital?: number;
  cpcv_folds?: number;
  purge_window?: number;
  embargo_window?: number;
  transaction_cost_bps?: number;
  apply_trend_gate?: boolean;
}

export interface AutonomousBacktestResponse {
  strategy_id: string;
  is_deployable: boolean;
  // The backend (validation/autonomous_backtest_runner.py's to_dict()) emits
  // `null` for every one of these whenever the underlying float is NaN --
  // both on the AST-compile-failure path (sharpe_ratio/sortino_ratio/
  // max_drawdown/annualized_return/cumulative_return/calmar_ratio/volatility
  // are all explicitly float('nan') there) and on genuinely reachable
  // degenerate-math paths in the success path (e.g. sortino_ratio/
  // calmar_ratio go NaN on zero downside-deviation / zero max drawdown).
  // Never assume these are populated -- CONSTRAINT #4.
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
  max_drawdown: number | null;
  pbo: number | null;
  dsr: number | null;
  turnover: number | null;
  annualized_return: number | null;
  cumulative_return: number | null;
  win_rate: number | null;
  calmar_ratio: number | null;
  volatility: number | null;
  gate_evaluations: Record<string, boolean>;
  failure_reasons: string[];
  n_paths: number;
  n_observations: number;
  execution_time_seconds: number;
  cpcv_mean_oos_sharpe?: number | null;
  cpcv_mean_oos_max_dd?: number | null;
  cpcv_mean_oos_sortino?: number | null;
  regime_breakdown?: Record<string, {
    sharpe: number;
    sortino: number;
    max_drawdown: number;
    cumulative_return: number;
    win_rate: number;
    pnl_share: number;
    n_bars: number;
  }>;
  regime_stability_score?: number | null;
  passes_regime_stability?: boolean;
  equity_curve?: Array<{ date: string; equity: number; drawdown: number }>;
  error?: string | null;
  as_of?: string;
}

// ============================================================================
// Tier D: 3D Volatility Surface Types
// ============================================================================

export interface VolSurface3DPoint {
  strike: number;
  dte: number;
  iv: number;
  moneyness?: number;
  expiration?: string;
  call_iv?: number;
  put_iv?: number;
}

export interface VolSurface3DMeshResponse {
  symbol: string;
  spot_price: number;
  strikes: number[];
  dtes: number[];
  grid: number[][]; // grid[dteIdx][strikeIdx] = iv
  min_iv: number;
  max_iv: number;
  min_strike: number;
  max_strike: number;
  min_dte: number;
  max_dte: number;
  points?: VolSurface3DPoint[];
  as_of?: string;
}

// ============================================================================
// Tier D: Multi-Broker Gateway & Circuit Breaker Types
// ============================================================================

export interface BrokerHealthStatusDto {
  broker_id: string;
  broker_type: string;
  connection_state: "connected" | "degraded" | "failing" | "disconnected" | "maintenance";
  circuit_state: "closed" | "open" | "half_open";
  is_healthy: boolean;
  is_routable: boolean;
  latency_ms: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  error_rate: number;
  consecutive_failures: number;
  last_heartbeat?: string | null;
  last_error?: string | null;
  status_message: string;
}

export interface RoutingAuditDto {
  client_order_id: string;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  primary_broker_id: string;
  executed_broker_id?: string | null;
  was_failover: boolean;
  total_latency_ms: number;
  final_status: string;
  failover_reason?: string | null;
  timestamp: string;
}

export interface MultiBrokerStatusResponse {
  // execution/multi_broker_gateway.py's GatewayStatusSnapshot.active_broker_id
  // is Optional[str] -- it is genuinely None whenever resolve_active_broker()
  // raises NoHealthyBrokerError (no manual override, no candidate in the
  // priority hierarchy, and no fallback connected broker). That is exactly
  // the worst-case state this screen exists to surface, so it must never be
  // papered over with a fabricated default broker id (CONSTRAINT #4).
  active_broker_id: string | null;
  manual_override_broker_id?: string | null;
  priority_hierarchy: string[];
  brokers: Record<string, BrokerHealthStatusDto>;
  total_orders_routed: number;
  total_failovers: number;
  last_failover_time?: string | null;
  last_failover_reason?: string | null;
  recent_routing_audits?: RoutingAuditDto[];
}

export interface BrokerFailoverRequest {
  target_broker: string;
  reason?: string;
}

export interface BrokerFailoverResponse {
  status: string;
  active_broker: string;
  manual_override: string;
  reason: string;
  timestamp: string;
}

// ============================================================================
// Tier D: SEC Rule 606 Execution Quality Report Types
// ============================================================================

export interface SecRule606VenueRow {
  venue: string;
  // execution/sec_rule_606_reporter.py's SecRule606Reporter._compute_report()
  // builds TWO differently-shaped venue-row dicts for what the frontend
  // treats as one type: the by-category rows (venue_breakdown.by_category)
  // use order_count/executed_shares and omit pct_of_total_shares entirely,
  // while the venues_overall rows use total_orders/total_shares instead
  // (that naming is also depended on by generate_markdown_summary/CSV
  // export, so it isn't a simple backend rename). order_count/
  // executed_shares/pct_of_total_shares are optional here because a raw
  // row may carry them under the alternate name (or, for pct_of_total_shares,
  // not at all) -- SecRule606ReportView.tsx normalizes every row into a
  // fully-populated shape before rendering, computing pct_of_total_shares
  // from the period total when the backend omitted it, rather than
  // null-guarding into a permanent "--" for live data.
  order_count?: number;
  total_orders?: number;
  pct_of_category_orders?: number;
  pct_of_total_orders: number;
  executed_shares?: number;
  total_shares?: number;
  pct_of_category_shares?: number;
  pct_of_total_shares?: number;
  net_fee_rebate_dollars: number;
  rebate_per_hundred_shares_dollars: number;
  rebate_per_hundred_shares_cents: number;
  price_improved_orders_count: number;
  price_improvement_rate: number;
  price_improved_shares_count?: number;
  total_price_improvement_dollars: number;
  avg_price_improvement_per_order_dollars: number;
  avg_price_improvement_per_share_cents: number;
  avg_price_improvement_per_improved_share_cents?: number;
}

export interface SecRule606CategoryBreakdown {
  category: string;
  order_count: number;
  pct_of_total_orders: number;
  executed_shares: number;
  pct_of_total_shares: number;
  net_fee_rebate_dollars: number;
  rebate_per_hundred_shares_dollars: number;
  rebate_per_hundred_shares_cents: number;
  price_improved_orders_count: number;
  price_improvement_rate: number;
  price_improved_shares_count: number;
  price_improved_shares_rate: number;
  total_price_improvement_dollars: number;
  avg_price_improvement_per_order_dollars: number;
  avg_price_improvement_per_improved_order_dollars: number;
  avg_price_improvement_per_share_cents: number;
  avg_price_improvement_per_improved_share_cents: number;
}

export interface SecRule606ReportResponse {
  header: {
    report_type: string;
    period: string;
    year?: number | null;
    quarter?: number | null;
    start_date: string;
    end_date: string;
    is_option?: boolean | null;
    created_at: string;
  };
  summary: {
    total_orders: number;
    total_shares: number;
    total_notional: number;
    total_net_rebate_dollars: number;
    total_price_improvement_dollars: number;
    overall_price_improvement_rate: number;
    overall_share_price_improvement_rate: number;
    overall_rebate_per_hundred_shares_dollars: number;
    overall_rebate_per_hundred_shares_cents: number;
    overall_avg_price_improvement_per_order_dollars: number;
    price_improved_orders_count: number;
  };
  order_category_breakdown: Record<string, SecRule606CategoryBreakdown>;
  venue_breakdown: {
    by_category: Record<string, SecRule606VenueRow[]>;
    venues_overall: SecRule606VenueRow[];
  };
}

/**
 * Real-time Greek and risk telemetry for a single equity or option leg.
 */
export interface PositionRiskGreeks {
  symbol: string;
  underlying: string;
  position_type: 'equity' | 'option';
  qty: number;
  spot_price: number;
  strike?: number | null;
  dte?: number | null;
  option_type?: 'call' | 'put' | null;
  iv?: number | null;
  delta: number;
  dollar_delta: number;
  gamma: number;
  dollar_gamma_1pct: number;
  theta_daily: number;
  vega_1pct: number;
  beta_spy: number;
  beta_weighted_delta_spy: number;
}

/**
 * Sub-second portfolio risk and aggregate Greeks event pushed via WebSocket (/ws/risk/portfolio).
 */
export interface PortfolioRiskStreamEvent {
  timestamp: string;
  spy_price: number;
  net_delta: number;
  net_dollar_delta: number;
  net_gamma: number;
  net_dollar_gamma_1pct: number;
  net_theta: number;
  net_vega: number;
  beta_weighted_delta_spy: number;
  total_positions_count: number;
  resolved_positions_count: number;
  missing_data_count: number;
  positions: PositionRiskGreeks[];
  missing_positions: string[];
}

/**
 * Dynamic circuit breaker operational state.
 */
export type CircuitBreakerState = "NORMAL" | "CAUTION" | "SOFT_HALT" | "HARD_HALT";

/**
 * GET /risk/circuit-breaker/status response shape.
 */
export interface CircuitBreakerStatusResponse {
  state: CircuitBreakerState;
  volatility_zscore: number;
  vpin: number;
  ofi: number;
  loss_velocity_per_min: number;
  reason: string | null;
  updated_at: string;
}



