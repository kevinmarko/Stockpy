/**
 * types.ts — TypeScript mirror of api/pilots_api.py response shapes.
 *
 * Sourced from the plan's "Phase 2 — API layer" endpoint contracts. When the
 * live backend lands, these are the single point to reconcile against the real
 * JSON. Nothing else in the app hard-codes a response shape.
 */

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

/** GET /pilots/{id} — full detail. */
export interface PilotDetail extends PilotSummary {
  holdings: Holding[];
  sector_allocation: SectorSlice[];
  recent_trades: PilotTrade[];
  as_of: string | null; // ISO timestamp of the snapshot the holdings came from
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
}

/**
 * POST /brokerage/connect body. Sent only over a loopback connection to the
 * operator's own local backend — see api/pilots_api.py's module docstring for
 * the three independent server-side gates (BROKERAGE_CONNECT_ENABLED,
 * FOLLOW_API_TOKEN, loopback-only). Never persisted client-side.
 */
export interface BrokerageConnectRequest {
  username: string;
  password: string;
  /** Current 6-digit authenticator-app code. Verified once, never persisted. */
  mfa_code: string;
}

/** POST /brokerage/connect response. Never echoes credential values. */
export interface BrokerageConnectResult {
  connected: boolean;
  verified: boolean;
  has_account_snapshot: boolean;
}

/** POST /brokerage/disconnect response. */
export interface BrokerageDisconnectResult {
  connected: boolean;
}

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

export interface ForecastSkill {
  symbol: string;
  horizon_days: number;
  reliability_curve: ReliabilityBin[];
  skill_weights: Record<string, number>; // {model: normalized inverse-RMSE weight}
  pending: number;
  completed: number;
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

/** GET /models — ML model registry row (ml/registry.yaml). */
export interface ModelRow {
  name: string;
  role: string | null;
  trained_date: string | null;
  cpcv_dsr: number | null; // null for an un-validated model
  pbo: number | null;
  n_train: number | null;
  deployable: boolean | null;
  notes: string | null;
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
 */
export interface ControlStatus {
  daemon_alive: boolean;
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
// only the changed keys back. Like every other .env-write surface in this PWA
// the write does NOT reach the running process (settings is a process-lifetime
// singleton) — hence `applies: "next_daemon_restart"`.
// ---------------------------------------------------------------------------

/** Widget kind for one tunable field. Enum fields additionally carry `options`. */
export type TunableFieldType = "number" | "boolean" | "enum" | "string";

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
}

/** A named cluster of related tunables (GET /settings/tunables). */
export interface TunableGroup {
  name: string;
  fields: TunableField[];
}

/** GET /settings/tunables — every editable runtime setting, grouped. */
export interface TunablesResponse {
  applies: "next_daemon_restart";
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
 * mismatch). Rejections are surfaced, never swallowed.
 */
export interface TunablesUpdateResult {
  written: Record<string, number | boolean | string>;
  rejected: Record<string, string>;
  applies: "next_daemon_restart";
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
 * GET /metrics/sentiment/{symbol} — Sentiment Dynamics: Antigravity-agent
 * news sentiment plus GJR-GARCH asymmetric-volatility persistence.
 *
 * Honesty contract: `source` distinguishes real Antigravity-agent output
 * ("antigravity_agent") from an honest cold-start/unconfigured-agent
 * degradation ("unavailable" — sentiment_score/sentiment_intensity/
 * credibility_score are all `null`, never a guessed number).
 * `volatility_persistence` is computed independently via a real per-request
 * GJR-GARCH fit over price history, so it can be a real number even when
 * `source === "unavailable"` (or `null` itself on insufficient history).
 */
export interface SentimentDynamics {
  ticker: string;
  date: string;
  sentiment_score: number | null;
  sentiment_intensity: number | null;
  credibility_score: number | null;
  volatility_persistence: number | null;
  source: "antigravity_agent" | "unavailable";
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

export interface ObservabilitySummary {
  portfolio_risk: PortfolioRiskMetrics;
  portfolio_heat: PortfolioHeatMetric;
  equity_curve: EquityDrawdownCurve;
  regime: RegimeOverlay;
  forecast_skill: PortfolioForecastSkill;
  risk_gate_blocks: RiskGateBlockLog;
  circuit_breakers: CircuitBreakerSummary;
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
 * GET /metrics/forecast/{symbol} — multi-horizon blended forecast + Monte
 * Carlo bands from `ForecastingEngine.generate_forecast`. Every field is a
 * price level and may be `null` (NaN→null); the backend 404s when there are
 * no bars at all, so a rendered response always has *some* horizon populated.
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
  // Prophet overlay (present only when Prophet ran); index signature carries
  // any additional model columns the engine emits without silently dropping them.
  [key: string]: number | null;
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
  client_order_id: string;
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
