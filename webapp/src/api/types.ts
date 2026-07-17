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
  held_by_pilots: SymbolHeldBy[];
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
  mfa_secret: string;
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
}

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
