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

/** Honest, PBO/DSR-gated backtest headline from reports/<id>_validation_summary.json. */
export interface Headline {
  sharpe: number | null;
  dsr: number | null;
  pbo: number | null;
  max_drawdown: number | null; // fraction, e.g. 0.18 = 18%
  deployable: boolean;
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
  metrics: Headline;
  curve: CurvePoint[] | null;
  benchmark: CurvePoint[] | null;
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
  status: string; // "pending" | "queued" | "cancelled"
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

/** Envelope used to distinguish "not run yet" (honest 404) from a hard error. */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}
