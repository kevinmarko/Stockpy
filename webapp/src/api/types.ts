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
  | "Trend";

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

/** Envelope used to distinguish "not run yet" (honest 404) from a hard error. */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}
