/**
 * mock.ts — realistic offline fixtures for every endpoint in api/pilots_api.py.
 *
 * Lets the whole PWA run with VITE_USE_MOCK=true and no backend. Data mirrors
 * the Pilot catalog in the plan (Phase 1) and is deliberately HONEST:
 *  - `momentum-burst` is NOT deployable (fails a validation gate) and renders so.
 *  - `value-quality` has curve:null ("no backtest series yet"), never a fake line.
 */

import { ApiError } from "./types";
import type {
  AgenticDiscovery,
  AgenticStatus,
  AgentLoopStatus,
  AiChartResponse,
  AiCommentaryResponse,
  AiResearchResponse,
  AlertsFeed,
  AutomationSchedule,
  AutomationStatus,
  BrinsonFachlerResult,
  BrinsonFachlerRow,
  BrinsonFachlerSectorDetail,
  CommandManifest,
  CoverageStatus,
  DiscoveryCandidate,
  ExecutionQueue,
  ExecutionQueueParams,
  ScanConfig,
  ScanConfigRequest,
  ScanConfigResult,
  WatchResult,
  BrokerageConnectRequest,
  BrokerageConnectResult,
  BrokerageDisconnectResult,
  BrokerageRefreshResult,
  BrokerageStatus,
  CalibrationSummary,
  CircuitBreakerSummary,
  CircuitBreakerTrip,
  ControlStatus,
  CorrelationCluster,
  DecisionCreateRequest,
  DecisionCreateResult,
  DecisionEntry,
  EdgeByStrategy,
  EquityDrawdownCurve,
  EquityDrawdownPoint,
  FactorExposure,
  Follow,
  FollowResult,
  ForecastSkill,
  ForecastBackfillSummary,
  Headline,
  Holding,
  IntervalUpdateResult,
  ExecutionModeUpdateRequest,
  ExecutionModeUpdateResult,
  JobRecord,
  KillSwitchActionResult,
  LlmCapabilityRow,
  LlmProviderName,
  LlmProviderTelemetry,
  LlmSettingUpdateResult,
  LlmStatus,
  LogAggregation,
  LogAggregationEntry,
  MacroGateUpdateResult,
  ModelRow,
  ObservabilitySummary,
  OptionsDirective,
  OptionsMatrix,
  OptionsRecomputeRequest,
  OptionsRecomputeResult,
  PairsAnalyzeRequest,
  PairsAnalyzeResult,
  PairsRadar,
  PairsScanRequest,
  PairsScanResult,
  PerfRange,
  PerformanceResponse,
  PilotDetail,
  PilotSummary,
  PilotTrade,
  Portfolio,
  PortfolioAttribution,
  PortfolioForecastSkill,
  PortfolioHeatMetric,
  PortfolioRiskMetrics,
  RealizedPerformance,
  RegimeOverlay,
  RestartDaemonResult,
  RiskGateBlockEntry,
  RiskGateBlockLog,
  RealizedTrade,
  MetaLabelBin,
  MetaLabelDistribution,
  RollingBeta,
  RunRecord,
  SectorSelectionRow,
  SectorSelectionView,
  SectorSlice,
  StrategyHealthGate,
  StrategyHealthRow,
  StrategyHealthTrendPoint,
  GravityAuditStatus,
  StrategyMatrix,
  StrategyModulesUpdate,
  StrategyModulesUpdateResult,
  SystemTelemetry,
  ValidationTrendSnapshot,
  TunableField,
  TunableFieldType,
  TunablesResponse,
  TunablesUpdateResult,
  SymbolDetail,
  SymbolCompareRow,
  SymbolCompareResponse,
  UniverseResponse,
  SyncReportResponse,
  SyncReportSymbol,
  RecommendationsResponse,
  Recommendation,
  UniverseListResponse,
  UniverseSymbol,
  Thresholds,
  SymbolHeldBy,
  SymbolOptions,
  TriggerRunResult,
  Bar,
  Fundamentals,
  MacroHistorySeries,
  MacroSnapshot,
  QuotesResponse,
  SignalBreakdown,
  SignalImportance,
  SignalImportanceRow,
  SignalModuleScore,
  ForecastAttention,
  ForecastResult,
  SentimentDynamics,
  SentimentHistory,
  SizingCapAuditTrail,
  SizingCapEvent,
  EtfTransmissionSummary,
  HeartbeatSummary,
  StrategyPnlSummary,
  EquityCurveResponse,
  AiDisagreementsResponse,
  ReportFile,
  ReportManifest,
  ReportContent,
  DeadLetterQueueEntry,
  DeadLetterQueue,
  DeadLetterRetryResult,
  PromptListResponse,
  PromptEntry,
  PromptBody,
  PromptPinRequest,
  PromptPinResult,
  DataSyncResult,
  ProviderStatus,
} from "./types";

const SECTORS = [
  "Technology",
  "Financials",
  "Healthcare",
  "Consumer Disc.",
  "Energy",
  "Industrials",
  "Communication",
  "Utilities",
];

const NAMES: Record<string, string> = {
  AAPL: "Apple",
  MSFT: "Microsoft",
  NVDA: "NVIDIA",
  GOOGL: "Alphabet",
  AMZN: "Amazon",
  META: "Meta Platforms",
  JPM: "JPMorgan Chase",
  V: "Visa",
  UNH: "UnitedHealth",
  XOM: "Exxon Mobil",
  CAT: "Caterpillar",
  HD: "Home Depot",
  COST: "Costco",
  PG: "Procter & Gamble",
  DUK: "Duke Energy",
  T: "AT&T",
  MRK: "Merck",
  CVX: "Chevron",
  LMT: "Lockheed Martin",
  ADBE: "Adobe",
};

const SECTOR_OF: Record<string, string> = {
  AAPL: "Technology",
  MSFT: "Technology",
  NVDA: "Technology",
  ADBE: "Technology",
  GOOGL: "Communication",
  META: "Communication",
  T: "Communication",
  AMZN: "Consumer Disc.",
  HD: "Consumer Disc.",
  COST: "Consumer Disc.",
  JPM: "Financials",
  V: "Financials",
  UNH: "Healthcare",
  MRK: "Healthcare",
  XOM: "Energy",
  CVX: "Energy",
  CAT: "Industrials",
  LMT: "Industrials",
  PG: "Consumer Disc.",
  DUK: "Utilities",
};

function h(
  sharpe: number | null,
  dsr: number | null,
  pbo: number | null,
  dd: number | null,
  deployable: boolean,
  stress = true
): Headline {
  return {
    sharpe,
    dsr,
    pbo,
    max_drawdown: dd,
    deployable,
    stress_gate_passed: stress,
  };
}

function holdings(
  symbols: [string, number, number][] // [symbol, weight(raw), score]
): Holding[] {
  const total = symbols.reduce((s, [, w]) => s + w, 0);
  return symbols.map(([symbol, w, score]) => ({
    symbol,
    name: NAMES[symbol] ?? symbol,
    sector: SECTOR_OF[symbol] ?? "Other",
    weight: +(w / total).toFixed(4),
    score,
    price: +(50 + Math.random() * 400).toFixed(2),
  }));
}

function sectorAlloc(hs: Holding[]): SectorSlice[] {
  const m = new Map<string, number>();
  for (const x of hs) m.set(x.sector, (m.get(x.sector) ?? 0) + x.weight);
  return [...m.entries()]
    .map(([sector, weight]) => ({ sector, weight: +weight.toFixed(4) }))
    .sort((a, b) => b.weight - a.weight);
}

function trades(hs: Holding[]): PilotTrade[] {
  const sides = ["ENTER", "REWEIGHT", "EXIT"] as const;
  const out: PilotTrade[] = [];
  const now = Date.now();
  for (let i = 0; i < Math.min(6, hs.length); i++) {
    const holding = hs[i];
    const side = sides[i % 3];
    out.push({
      date: new Date(now - i * 86400000 * 2).toISOString().slice(0, 10),
      symbol: holding.symbol,
      side,
      weight_delta:
        side === "EXIT"
          ? -holding.weight
          : +(holding.weight * (side === "ENTER" ? 1 : 0.4)).toFixed(4),
      sector: holding.sector,
    });
  }
  return out;
}

// ---- Pilot catalog (mirrors pilots/catalog.py) ----
interface MockPilot {
  summary: PilotSummary;
  holdings: Holding[];
  hasCurve: boolean;
  curveDrift: number; // per-year drift for synthetic mock curve
  curveVol: number;
  // Whether a SEPARATE SPY (broad-market) macro overlay is available. false
  // models the honest "underlying already IS SPY → redundant → null" case.
  macroBenchmark: boolean;
}

const RAW: Array<{
  id: string;
  name: string;
  category: PilotSummary["category"];
  description: string;
  headline: Headline;
  long_only: boolean;
  aum: number;
  followers: number;
  hasCurve: boolean;
  drift: number;
  vol: number;
  syms: [string, number, number][];
  // Optional; defaults to true (a distinct SPY macro overlay is available).
  // Set false to model the honest redundancy case (underlying already IS SPY).
  macroBenchmark?: boolean;
}> = [
  {
    id: "trend-following",
    name: "Trend Follower",
    category: "Momentum",
    description:
      "Rides sustained multi-month price trends across large caps. Time-series momentum (Moskowitz/Ooi/Pedersen) — buys strength, cuts weakness.",
    headline: h(1.12, 0.972, 0.31, 0.19, true),
    long_only: false,
    aum: 184200,
    followers: 62,
    hasCurve: true,
    drift: 0.14,
    vol: 0.13,
    syms: [
      ["NVDA", 30, 0.82],
      ["MSFT", 24, 0.61],
      ["AAPL", 20, 0.48],
      ["CAT", 14, 0.4],
      ["LMT", 12, 0.33],
    ],
  },
  {
    id: "dip-buyer",
    name: "Dip Buyer",
    category: "Mean Reversion",
    description:
      "Connors-style RSI(2) mean reversion, long-only above the 200-day line. Buys short-term oversold dips in uptrending names; regime-gated off in stress.",
    headline: h(0.83, 0.961, 0.38, 0.14, true),
    long_only: true,
    aum: 97400,
    followers: 41,
    hasCurve: true,
    drift: 0.09,
    vol: 0.1,
    syms: [
      ["COST", 26, 0.7],
      ["HD", 22, 0.55],
      ["V", 20, 0.5],
      ["PG", 18, 0.42],
      ["UNH", 14, 0.36],
    ],
  },
  {
    id: "multifactor",
    name: "Multifactor",
    category: "Factor",
    description:
      "Fama-French-style multifactor tilt — Value, Quality, Low-Vol and Size, cross-sectionally z-scored. Diversified, low-turnover core sleeve.",
    headline: h(0.94, 0.958, 0.34, 0.16, true),
    long_only: true,
    aum: 251900,
    followers: 88,
    hasCurve: true,
    drift: 0.11,
    vol: 0.11,
    syms: [
      ["JPM", 18, 0.44],
      ["MRK", 16, 0.41],
      ["XOM", 15, 0.39],
      ["DUK", 14, 0.35],
      ["V", 13, 0.33],
      ["CVX", 12, 0.31],
      ["UNH", 12, 0.3],
    ],
  },
  {
    id: "macd-trend",
    name: "MACD Momentum",
    category: "Momentum",
    description:
      "MACD + Aroon trend confirmation with a chop filter to suppress false crossovers. Medium-horizon momentum with a volatility-aware corridor.",
    headline: h(1.01, 0.965, 0.29, 0.21, true),
    long_only: false,
    aum: 132600,
    followers: 54,
    hasCurve: true,
    drift: 0.12,
    vol: 0.14,
    // This Pilot's validation underlying IS SPY (single-name adapter), so a
    // separate SPY macro overlay would just duplicate the benchmark -> null
    // (honest redundancy case, mirrors the harness's []-persist rule).
    macroBenchmark: false,
    syms: [
      ["NVDA", 28, 0.78],
      ["META", 22, 0.6],
      ["AMZN", 20, 0.52],
      ["ADBE", 16, 0.44],
      ["GOOGL", 14, 0.4],
    ],
  },
  {
    id: "cross-sectional-momentum",
    name: "Momentum Leaders",
    category: "Momentum",
    description:
      "Jegadeesh-Titman cross-sectional momentum (12-1m). Ranks the universe and holds the top decile of relative strength, rebalanced monthly.",
    headline: h(1.05, 0.969, 0.33, 0.23, true),
    long_only: false,
    aum: 118300,
    followers: 47,
    hasCurve: true,
    drift: 0.13,
    vol: 0.15,
    syms: [
      ["NVDA", 26, 0.8],
      ["MSFT", 20, 0.58],
      ["META", 18, 0.5],
      ["AAPL", 16, 0.44],
      ["COST", 12, 0.36],
      ["V", 8, 0.3],
    ],
  },
  {
    id: "balanced-blend",
    name: "Balanced Blend",
    category: "Blend",
    description:
      "The full Stockpy signal ensemble at production weights — momentum, trend, factor and mean-reversion combined. The all-weather default Pilot.",
    // Ensemble of every module — no single validated backtest honestly represents
    // it, so validation_strategy_id=None -> curve:null (mirrors pilots/catalog.py).
    headline: h(null, null, null, null, false, false),
    long_only: false,
    aum: 402700,
    followers: 133,
    hasCurve: false,
    drift: 0,
    vol: 0,
    syms: [
      ["MSFT", 16, 0.6],
      ["NVDA", 15, 0.72],
      ["V", 13, 0.42],
      ["UNH", 12, 0.4],
      ["COST", 12, 0.44],
      ["JPM", 11, 0.38],
      ["HD", 11, 0.36],
      ["MRK", 10, 0.34],
    ],
  },
  {
    id: "value-quality",
    name: "Value + Quality",
    category: "Factor",
    description:
      "Concentrated Value and Quality tilt (cheap, profitable, well-capitalized). Backtest series pending point-in-time fundamentals — metrics shown honestly.",
    headline: h(null, null, null, null, false, false),
    long_only: true,
    aum: 38100,
    followers: 19,
    hasCurve: false, // curve:null — no fabricated line
    drift: 0,
    vol: 0,
    syms: [
      ["JPM", 22, 0.5],
      ["CVX", 20, 0.46],
      ["MRK", 18, 0.44],
      ["PG", 16, 0.4],
      ["XOM", 14, 0.38],
      ["DUK", 10, 0.32],
    ],
  },
  {
    id: "dividend-income",
    name: "Dividend Income",
    category: "Factor",
    description:
      "Tilts toward durable dividend payers with healthy, well-covered yields — an income-oriented quality screen. Backtest pending point-in-time fundamentals.",
    headline: h(null, null, null, null, false, false),
    long_only: true,
    aum: 71500,
    followers: 33,
    hasCurve: false,
    drift: 0,
    vol: 0,
    syms: [
      ["PG", 24, 0.5],
      ["DUK", 22, 0.46],
      ["T", 20, 0.42],
      ["XOM", 18, 0.38],
      ["MRK", 16, 0.34],
    ],
  },
  {
    id: "deep-value",
    name: "Deep Value",
    category: "Factor",
    description:
      "Screens for stocks trading cheap versus their Graham intrinsic value. Backtest pending point-in-time fundamentals — metrics shown honestly.",
    headline: h(null, null, null, null, false, false),
    long_only: true,
    aum: 44300,
    followers: 21,
    hasCurve: false,
    drift: 0,
    vol: 0,
    syms: [
      ["JPM", 24, 0.5],
      ["CVX", 22, 0.46],
      ["XOM", 20, 0.42],
      ["T", 18, 0.36],
      ["DUK", 16, 0.3],
    ],
  },
  {
    id: "regime-navigator",
    name: "Regime Navigator",
    category: "Macro",
    description:
      "Top-down macro regime read — leans defensive in Recession/Credit-Event regimes and rotates toward risk-on sectors when the systemic backdrop clears.",
    headline: h(null, null, null, null, false, false),
    long_only: false,
    aum: 58900,
    followers: 27,
    hasCurve: false,
    drift: 0,
    vol: 0,
    syms: [
      ["DUK", 24, 0.44],
      ["PG", 22, 0.4],
      ["LMT", 20, 0.38],
      ["UNH", 18, 0.34],
      ["XOM", 16, 0.3],
    ],
  },
  {
    id: "edge-garch",
    name: "Edge & Volatility",
    category: "Factor",
    description:
      "Per-symbol statistical edge ratio combined with a GARCH tail-risk volatility veto — rewards names with a favorable historical risk/reward profile, penalized in high-volatility regimes.",
    headline: h(0.88, 0.961, 0.35, 0.12, true),
    long_only: false,
    aum: 96700,
    followers: 44,
    hasCurve: true,
    drift: 0.1,
    vol: 0.09,
    // Validation underlying IS SPY (single-name adapter) -> SPY macro overlay
    // duplicates the benchmark -> null (honest redundancy case).
    macroBenchmark: false,
    syms: [
      ["MSFT", 24, 0.55],
      ["AAPL", 22, 0.5],
      ["V", 20, 0.44],
      ["PG", 18, 0.38],
      ["COST", 16, 0.34],
    ],
  },
  {
    id: "rsi-reversal",
    name: "RSI Reversal",
    category: "Mean Reversion",
    description:
      "Fades short-term extremes with the classic RSI(14) rule — buys oversold washouts and trims overbought spikes back toward the mean.",
    headline: h(0.62, 0.951, 0.41, 0.17, true),
    long_only: false,
    aum: 51200,
    followers: 24,
    hasCurve: true,
    drift: 0.06,
    vol: 0.12,
    macroBenchmark: false,
    syms: [
      ["HD", 24, 0.48],
      ["COST", 22, 0.44],
      ["V", 20, 0.4],
      ["UNH", 18, 0.36],
      ["AMZN", 16, 0.32],
    ],
  },
  {
    id: "relative-strength",
    name: "Relative Strength",
    category: "Momentum",
    description:
      "Favors the names outrunning the S&P 500 — a relative-strength tilt that holds the market's leaders and sidesteps the laggards.",
    headline: h(0.79, 0.957, 0.36, 0.22, true),
    long_only: false,
    aum: 88400,
    followers: 39,
    hasCurve: true,
    drift: 0.12,
    vol: 0.14,
    syms: [
      ["NVDA", 26, 0.8],
      ["MSFT", 22, 0.58],
      ["META", 18, 0.5],
      ["AAPL", 16, 0.44],
      ["AMZN", 12, 0.36],
      ["GOOGL", 8, 0.3],
    ],
  },
  {
    id: "news-catalyst",
    name: "News Catalyst",
    category: "Sentiment",
    description:
      "Reacts to fresh headline sentiment and earnings catalysts, dampening signals around scheduled events where the reaction is unpredictable.",
    headline: h(null, null, null, null, false, false),
    long_only: false,
    aum: 33800,
    followers: 18,
    hasCurve: false,
    drift: 0,
    vol: 0,
    syms: [
      ["NVDA", 26, 0.6],
      ["META", 22, 0.5],
      ["AMZN", 20, 0.44],
      ["AAPL", 18, 0.4],
      ["ADBE", 14, 0.34],
    ],
  },
  {
    id: "forecast-aligned",
    name: "Forecast Aligned",
    category: "Forecast",
    description:
      "Tilts toward names whose projected multi-horizon forecast points to meaningful upside, and away from those forecast to decline.",
    headline: h(null, null, null, null, false, false),
    long_only: false,
    aum: 41100,
    followers: 20,
    hasCurve: false,
    drift: 0,
    vol: 0,
    syms: [
      ["MSFT", 24, 0.55],
      ["NVDA", 22, 0.62],
      ["GOOGL", 20, 0.44],
      ["V", 18, 0.4],
      ["UNH", 16, 0.34],
    ],
  },
  {
    id: "risk-adjusted",
    name: "Risk-Adjusted",
    category: "Risk",
    description:
      "Rewards durable risk-adjusted performance — favoring high-Sortino names while penalizing deep, painful drawdowns.",
    headline: h(0.71, 0.953, 0.39, 0.11, true),
    long_only: false,
    aum: 36400,
    followers: 17,
    hasCurve: true,
    drift: 0.08,
    vol: 0.08,
    macroBenchmark: false,
    syms: [
      ["PG", 24, 0.46],
      ["COST", 22, 0.44],
      ["V", 20, 0.4],
      ["UNH", 18, 0.36],
      ["MRK", 16, 0.32],
    ],
  },
  {
    id: "momentum-burst",
    name: "Momentum Burst",
    category: "Momentum",
    description:
      "High-turnover short-horizon momentum. Fails the overfitting gate (PBO high, DSR below threshold) — shown as NOT deployable. Educational example of an honest fail.",
    headline: h(0.41, 0.72, 0.63, 0.34, false, true),
    long_only: false,
    aum: 12400,
    followers: 8,
    hasCurve: true,
    drift: 0.05,
    vol: 0.26,
    syms: [
      ["NVDA", 34, 0.7],
      ["META", 26, 0.55],
      ["AMZN", 22, 0.48],
      ["ADBE", 18, 0.4],
    ],
  },
];

const CATALOG: MockPilot[] = RAW.map((r) => {
  const hs = holdings(r.syms);
  const summary: PilotSummary = {
    id: r.id,
    name: r.name,
    category: r.category,
    description: r.description,
    headline: r.headline,
    holdings_count: hs.length,
    aum_proxy: r.aum,
    followers_proxy: r.followers,
    long_only: r.long_only,
  };
  return {
    summary,
    holdings: hs,
    hasCurve: r.hasCurve,
    curveDrift: r.drift,
    curveVol: r.vol,
    macroBenchmark: r.macroBenchmark ?? true,
  };
});

function findPilot(id: string): MockPilot | undefined {
  return CATALOG.find((p) => p.summary.id === id);
}

const RANGE_DAYS: Record<PerfRange, number> = {
  "1W": 7,
  "1M": 30,
  "3M": 91,
  "6M": 182,
  "1Y": 365,
  "2Y": 730,
};

// Deterministic pseudo-random for reproducible mock curves.
function seeded(seed: number): () => number {
  let s = seed % 2147483647;
  if (s <= 0) s += 2147483646;
  return () => {
    s = (s * 16807) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

function synthCurve(
  id: string,
  range: PerfRange,
  drift: number,
  vol: number,
  base = 100
) {
  const days = RANGE_DAYS[range];
  const step = days > 200 ? Math.ceil(days / 120) : 1;
  const rng = seeded(
    [...id].reduce((a, c) => a + c.charCodeAt(0), 0) + days
  );
  const dailyDrift = drift / 252;
  const dailyVol = vol / Math.sqrt(252);
  let v = base;
  const out: { date: string; value: number }[] = [];
  const now = Date.now();
  for (let i = days; i >= 0; i -= step) {
    const shock = (rng() - 0.5) * 2 * dailyVol * step;
    v = v * (1 + dailyDrift * step + shock);
    out.push({
      date: new Date(now - i * 86400000).toISOString().slice(0, 10),
      value: +v.toFixed(2),
    });
  }
  return out;
}

// ---- Portfolio fixture ----
const PORTFOLIO: Portfolio = {
  total_equity: 48213.55,
  buying_power: 6120.4,
  total_unrealized_pl: 3182.19,
  total_dividends: 412.66,
  position_count: 6,
  source: "cache",
  fetched_at: new Date(Date.now() - 3600_000).toISOString(),
  is_stale: false,
  age_hours: 1,
  positions: [
    pos("AAPL", 40, 168.2, 214.9),
    pos("MSFT", 18, 372.5, 431.2),
    pos("NVDA", 22, 88.4, 132.6),
    pos("V", 30, 241.1, 279.8),
    pos("COST", 6, 712.0, 889.4),
    pos("DUK", 55, 96.3, 91.2),
  ],
};

function pos(symbol: string, qty: number, avg: number, price: number) {
  const mv = qty * price;
  const pl = (price - avg) * qty;
  return {
    symbol,
    name: NAMES[symbol] ?? symbol,
    qty,
    avg_cost: avg,
    current_price: price,
    market_value: +mv.toFixed(2),
    unrealized_pl: +pl.toFixed(2),
    unrealized_pl_pct: +((price / avg - 1) * 100).toFixed(2),
  };
}

// The set of tickers the mock symbol-detail endpoint recognizes: the union of
// every Pilot's holdings and every open portfolio position. A ticker outside
// this set is a legitimate 404 (mirrors the backend, where a symbol absent from
// the persisted snapshot returns _UNKNOWN_SYMBOL_DETAIL).
const SYMBOL_UNIVERSE: Set<string> = new Set<string>([
  ...CATALOG.flatMap((p) => p.holdings.map((x) => x.symbol)),
  ...PORTFOLIO.positions.map((p) => p.symbol),
]);

// ---- Mock configured universe (settings.DEFAULT_TICKERS) --------------------
// A module-level mutable list so getDataUniverse/updateDataUniverse behave like
// a real read-modify-write within a session (and across a test's add→remove
// steps). Seeded with the same defaults settings.py ships.
let MOCK_DATA_UNIVERSE: string[] = ["AAPL", "MSFT", "JNJ", "AGNC"];

/** Exposed for tests: reset the mock universe between cases. */
export function __resetMockDataUniverse() {
  MOCK_DATA_UNIVERSE = ["AAPL", "MSFT", "JNJ", "AGNC"];
}

// ---- Local follows store (persisted to localStorage so the mock feels live) ----
const FOLLOWS_KEY = "stockpy.mock.follows";

function readFollows(): Follow[] {
  try {
    const raw = localStorage.getItem(FOLLOWS_KEY);
    return raw ? (JSON.parse(raw) as Follow[]) : [];
  } catch {
    return [];
  }
}
function writeFollows(fs: Follow[]) {
  try {
    localStorage.setItem(FOLLOWS_KEY, JSON.stringify(fs));
  } catch {
    /* ignore quota */
  }
}

const MOCK_MODE = "review" as const; // paper-first: nothing is ever placed
const NOTIONAL_CAP = 2500;
const MIN_AMOUNT = 100;

// A real (if trivial) 1x1 transparent PNG, base64-encoded — stands in for the
// live endpoint's actual rendered chart image so <img src="data:image/png;..."/>
// has something real to decode in the mock, without needing a chart library
// here just to produce fixture bytes.
const MOCK_CHART_PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";

// ---- Local brokerage-connect simulation (localStorage; never stores the
// actual credential strings — only a boolean "connected" marker, matching the
// real backend's honesty posture of never echoing/persisting secrets client-side) ----
const BROKERAGE_KEY = "stockpy.mock.brokerage";

function readBrokerageConnected(): boolean {
  try {
    return localStorage.getItem(BROKERAGE_KEY) === "1";
  } catch {
    return false;
  }
}
function writeBrokerageConnected(connected: boolean) {
  try {
    if (connected) localStorage.setItem(BROKERAGE_KEY, "1");
    else localStorage.removeItem(BROKERAGE_KEY);
  } catch {
    /* ignore quota */
  }
}

// ---- Local brokerage-refresh degraded-fetch simulation (localStorage) so
// the "live login failed, falling back to the last cached snapshot" honest
// branch (fetch_account_snapshot's own internal stale-cache fallback -- see
// api/pilots_api.py's refresh_brokerage docstring) is reachable by actually
// running the app with USE_MOCK=true, not only through a hand-crafted test
// spy override. No dedicated UI control, same reasoning as
// OBSERVABILITY_COLD_START_KEY below -- flip it from the browser devtools
// console instead:
//   localStorage.setItem("stockpy.mock.brokerage_refresh_degraded", "1")  // next refresh is stale
//   localStorage.removeItem("stockpy.mock.brokerage_refresh_degraded")    // back to a fresh refresh
const BROKERAGE_REFRESH_DEGRADED_KEY = "stockpy.mock.brokerage_refresh_degraded";

function readBrokerageRefreshDegraded(): boolean {
  try {
    return localStorage.getItem(BROKERAGE_REFRESH_DEGRADED_KEY) === "1";
  } catch {
    return false;
  }
}

// ---- Local kill-switch simulation (localStorage) so pause/resume have a
// visible, persistent round-trip effect in the demo, same convention as the
// brokerage-connect marker above. ----
const KILL_SWITCH_KEY = "stockpy.mock.kill_switch";
const KILL_SWITCH_REASON_KEY = "stockpy.mock.kill_switch_reason";

function readKillSwitch(): { active: boolean; reason: string | null } {
  try {
    return {
      active: localStorage.getItem(KILL_SWITCH_KEY) === "1",
      reason: localStorage.getItem(KILL_SWITCH_REASON_KEY),
    };
  } catch {
    return { active: false, reason: null };
  }
}
function writeKillSwitch(active: boolean, reason: string | null) {
  try {
    if (active) {
      localStorage.setItem(KILL_SWITCH_KEY, "1");
      if (reason) localStorage.setItem(KILL_SWITCH_REASON_KEY, reason);
    } else {
      localStorage.removeItem(KILL_SWITCH_KEY);
      localStorage.removeItem(KILL_SWITCH_REASON_KEY);
    }
  } catch {
    /* ignore quota */
  }
}

// ---- Local macro-regime-gate simulation (localStorage) so the Observability
// screen's toggle (PUT /observability/macro-gate) has a visible, persistent
// round-trip effect in the demo, same convention as the kill-switch marker
// above. `null` (key absent) means "use the default" (true, matching
// settings.MACRO_REGIME_GATE_ENABLED's own default) rather than defaulting to
// false, which would misrepresent the real out-of-box posture. ----
const MACRO_GATE_KEY = "stockpy.mock.macro_regime_gate_enabled";

function readMacroGateEnabled(): boolean {
  try {
    const raw = localStorage.getItem(MACRO_GATE_KEY);
    return raw === null ? true : raw === "1";
  } catch {
    return true;
  }
}
function writeMacroGateEnabled(enabled: boolean) {
  try {
    localStorage.setItem(MACRO_GATE_KEY, enabled ? "1" : "0");
  } catch {
    /* ignore quota */
  }
}

// ---- Local Observability cold-start simulation (localStorage) so the
// System Telemetry / Log Aggregation sections' honest-empty branches
// (psutil unavailable / no log file yet -- see mockSystemTelemetryUnavailable
// / mockEmptyLogAggregation below) are reachable by actually running the app
// with USE_MOCK=true, not only through the test suite. Unlike every other
// localStorage-backed simulation in this file, there is no real WRITE
// endpoint this piggybacks off of -- system_telemetry/log_aggregation are
// read-only diagnostics -- so there's no UI control for it either; flip it
// from the browser devtools console instead:
//   localStorage.setItem("stockpy.mock.observability_cold_start", "1")  // reload
//   localStorage.removeItem("stockpy.mock.observability_cold_start")    // back to happy path
const OBSERVABILITY_COLD_START_KEY = "stockpy.mock.observability_cold_start";

function readObservabilityColdStart(): boolean {
  try {
    return localStorage.getItem(OBSERVABILITY_COLD_START_KEY) === "1";
  } catch {
    return false;
  }
}

// ---- Local configured-interval simulation (localStorage) so a Save in the
// demo visibly reflects on the next GET /automation/schedule read. ----
const INTERVAL_KEY = "stockpy.mock.automation_interval";

function readMockInterval(): number {
  try {
    const raw = localStorage.getItem(INTERVAL_KEY);
    return raw != null ? Number(raw) : 300;
  } catch {
    return 300;
  }
}
function writeMockInterval(seconds: number) {
  try {
    localStorage.setItem(INTERVAL_KEY, String(seconds));
  } catch {
    /* ignore quota */
  }
}

// ---- Local AI Control Center simulation (localStorage) so a toggle flip or
// provider-selector change in the demo is visible on the next GET /llm/status
// read within the mock session, same convention as the interval/strategy
// simulations above. Mirrors gui/ai_control_center.py's CAPABILITIES registry:
// LLM_COMMENTARY_ENABLED gates THREE capabilities at once (claude_commentary,
// gemini_alerts, gemini_vision); GRAVITY_AI_RUNNER_ENABLED and
// OPAL_RESEARCH_ENABLED each gate one. Three capabilities additionally carry a
// provider_selector_setting ("claude"/"gemini"/"openai"/"none" — "none" counts
// as disabled, matching the real backend's `_is_enabled`). ----
const LLM_SETTINGS_KEY = "stockpy.mock.llm_settings";

interface LlmMockOverrides {
  toggles: Record<string, boolean>;
  providers: Record<string, string>;
}

const LLM_TOGGLE_KEYS = new Set([
  "LLM_COMMENTARY_ENABLED",
  "GRAVITY_AI_RUNNER_ENABLED",
  "OPAL_RESEARCH_ENABLED",
]);
const LLM_PROVIDER_SELECTOR_KEYS = new Set([
  "LLM_COMMENTARY_RATIONALE_PROVIDER",
  "LLM_COMMENTARY_ALERT_PROVIDER",
  "OPAL_RESEARCH_PROVIDER",
]);

function readLlmOverrides(): LlmMockOverrides {
  try {
    const raw = localStorage.getItem(LLM_SETTINGS_KEY);
    if (!raw) return { toggles: {}, providers: {} };
    const parsed = JSON.parse(raw);
    return { toggles: parsed.toggles ?? {}, providers: parsed.providers ?? {} };
  } catch {
    return { toggles: {}, providers: {} };
  }
}

function writeLlmOverride(key: string, value: boolean | string) {
  const ov = readLlmOverrides();
  if (LLM_TOGGLE_KEYS.has(key)) {
    ov.toggles[key] = Boolean(value);
  } else if (LLM_PROVIDER_SELECTOR_KEYS.has(key)) {
    ov.providers[key] = String(value);
  }
  try {
    localStorage.setItem(LLM_SETTINGS_KEY, JSON.stringify(ov));
  } catch {
    /* ignore quota */
  }
}

const LLM_PROVIDER_KEY_MAP: Record<LlmProviderName, string> = {
  claude: "ANTHROPIC_API_KEY",
  gemini: "GEMINI_API_KEY",
  openai: "OPENAI_API_KEY",
};

function llmNoCallTelemetry(provider: LlmProviderName): LlmProviderTelemetry {
  return {
    provider,
    ok: null,
    error_kind: null,
    exception_type: null,
    http_status: null,
    checked_at: null,
    age_seconds: null,
    source: "none",
  };
}

/**
 * Builds one capability row from live mock overrides. `key_present` is always
 * `false` in the mock (there is no key-entry surface in this PWA) — so
 * enabling a capability here honestly lands on `missing_key`, exactly the
 * state a real operator hits after flipping a toggle before setting the
 * provider's key in `.env`. This is deliberate, not an oversight: it
 * exercises the real "enabled but unconfigured" UI branch instead of always
 * rendering a clean, unrealistic `ready` state.
 */
function llmRow(
  key: string,
  label: string,
  trigger: "on_demand" | "scheduled",
  toggleKey: string,
  providerSelectorSetting: string | null,
  providerChoice: string | null, // live override or default; null = fixed-provider capability
  fixedProviderKeys: string[],
  overrides: LlmMockOverrides
): LlmCapabilityRow {
  const masterOn = overrides.toggles[toggleKey] ?? false;
  const activeProvider: LlmProviderName | null =
    providerChoice && providerChoice !== "none"
      ? (providerChoice as LlmProviderName)
      : null;
  const enabled = providerSelectorSetting ? masterOn && providerChoice !== "none" : masterOn;
  const providerKeys = activeProvider ? [LLM_PROVIDER_KEY_MAP[activeProvider]] : fixedProviderKeys;
  return {
    key,
    label,
    trigger,
    toggle_key: toggleKey,
    provider_selector_setting: providerSelectorSetting,
    provider_keys: providerKeys,
    active_provider: activeProvider,
    invalid_provider: null,
    enabled,
    key_present: false,
    built: true,
    status: enabled ? "missing_key" : "disabled",
  };
}

function mockLlmStatus(): LlmStatus {
  const ov = readLlmOverrides();
  const providerVal = (k: string, def: string) => ov.providers[k] ?? def;

  const capabilities: LlmCapabilityRow[] = [
    llmRow(
      "claude_commentary",
      "Analyst rationale commentary",
      "on_demand",
      "LLM_COMMENTARY_ENABLED",
      "LLM_COMMENTARY_RATIONALE_PROVIDER",
      providerVal("LLM_COMMENTARY_RATIONALE_PROVIDER", "claude"),
      ["ANTHROPIC_API_KEY"],
      ov
    ),
    llmRow(
      "gemini_alerts",
      "Alert commentary",
      "scheduled",
      "LLM_COMMENTARY_ENABLED",
      "LLM_COMMENTARY_ALERT_PROVIDER",
      providerVal("LLM_COMMENTARY_ALERT_PROVIDER", "gemini"),
      ["GEMINI_API_KEY"],
      ov
    ),
    llmRow(
      "gemini_vision",
      "Gemini chart vision",
      "on_demand",
      "LLM_COMMENTARY_ENABLED",
      null,
      null,
      ["GEMINI_API_KEY"],
      ov
    ),
    llmRow(
      "gravity_ai_runner",
      "Gravity AI runner (Claude + Gemini)",
      "on_demand",
      "GRAVITY_AI_RUNNER_ENABLED",
      null,
      null,
      ["ANTHROPIC_API_KEY", "GEMINI_API_KEY"],
      ov
    ),
    llmRow(
      "opal_research",
      "Opal research agent",
      "on_demand",
      "OPAL_RESEARCH_ENABLED",
      "OPAL_RESEARCH_PROVIDER",
      providerVal("OPAL_RESEARCH_PROVIDER", "openai"),
      ["OPENAI_API_KEY"],
      ov
    ),
  ];

  // Mirrors api/pilots_api.py's GET /llm/status attention logic: at least one
  // ENABLED capability misconfigured; invalid_key (unreachable in the mock --
  // there is no key-entry surface) would outrank missing_key.
  let attentionReason: "invalid_key" | "missing_key" | null = null;
  for (const row of capabilities) {
    if (!row.enabled) continue;
    if (row.status === "invalid_key") {
      attentionReason = "invalid_key";
      break;
    }
    if (row.status === "missing_key" && attentionReason === null) attentionReason = "missing_key";
  }

  return {
    capabilities,
    capabilities_source: "gui.ai_control_center.control_center_overview",
    providers: {
      claude: llmNoCallTelemetry("claude"),
      gemini: llmNoCallTelemetry("gemini"),
      openai: llmNoCallTelemetry("openai"),
    },
    providers_source: "llm.status_store.read_all",
    telemetry_note:
      "Verdicts are recorded from REAL LLM calls only — this platform never " +
      "probes a provider to test a key. A null last-call record means no LLM " +
      "call has been made with the current key, which is the EXPECTED state " +
      "when LLM commentary is off by default — it does NOT mean the key is broken.",
    attention: attentionReason !== null,
    attention_reason: attentionReason,
    // Always writable in the mock (matches mockStrategyMatrix's convention
    // below) so the demo can exercise the write flow with zero config.
    writable: true,
    writable_note: "Toggle and provider writes persist to .env and apply on the next daemon restart.",
  };
}

// ---- Local strategy-matrix simulation. A Save persists weights/disabled to
// localStorage AND sets a drift marker, so a subsequent GET honestly reports
// env_drift.detected=true (a real .env write does NOT reach the running process
// until restart — the mock mirrors that staleness rather than pretending the
// write took effect live). ----
const STRATEGY_KEY = "stockpy.mock.strategy_modules";
const STRATEGY_DRIFT_KEY = "stockpy.mock.strategy_drift";

// Base module table (a representative subset of the real 17). regime_multiplier
// is pinned to weight 0 and cannot be edited.
const STRATEGY_BASE: {
  name: string;
  weight: number;
  pinned: boolean;
  scored: number;
  // Version registry (backlog item #13a): a fixed 12-hex-char fingerprint +
  // an age-in-days for last_modified. All eight of these are real, currently
  // registered signals/*.py modules, so a real hash/mtime is the honest
  // fixture (CONSTRAINT #4) -- versionHash: null is reserved for a module
  // with no file on disk, which none of these currently are.
  versionHash: string;
  modifiedDaysAgo: number;
}[] = [
  { name: "macro_regime", weight: 45, pinned: false, scored: 20, versionHash: "a1b2c3d4e5f6", modifiedDaysAgo: 12 },
  { name: "macd_momentum", weight: 20, pinned: false, scored: 20, versionHash: "1a2b3c4d5e6f", modifiedDaysAgo: 40 },
  { name: "aroon_trend", weight: 15, pinned: false, scored: 20, versionHash: "9f8e7d6c5b4a", modifiedDaysAgo: 88 },
  { name: "graham_value", weight: 20, pinned: false, scored: 18, versionHash: "0d1e2f3a4b5c", modifiedDaysAgo: 5 },
  { name: "dividend_quality", weight: 15, pinned: false, scored: 12, versionHash: "6c5b4a39281f", modifiedDaysAgo: 61 },
  { name: "multifactor", weight: 15, pinned: false, scored: 19, versionHash: "3e4f5a6b7c8d", modifiedDaysAgo: 2 },
  { name: "cross_sectional_momentum", weight: 15, pinned: false, scored: 20, versionHash: "7a8b9c0d1e2f", modifiedDaysAgo: 30 },
  { name: "regime_multiplier", weight: 0, pinned: true, scored: 20, versionHash: "f1e2d3c4b5a6", modifiedDaysAgo: 200 },
];

function readStrategyOverrides(): { weights: Record<string, number>; disabled: string[] } | null {
  try {
    const raw = localStorage.getItem(STRATEGY_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

// Honest fixture (CONSTRAINT #4): reflects the platform's REAL current state,
// not a fabricated pretty spread. As of this writing zero MetaLabelers are
// registered in ml.meta_labeling.global_meta_registry, so meta_label_proba
// defaults to 1.0 (a multiplicative no-op) for every module -> every symbol's
// meta_label_composite is a genuine 1.0. Mirrors the backend's fixed [0,1]
// 20-bin logic exactly: a value of 1.0 lands in the LAST bin (index 19, range
// [0.95, 1.0]), never spread across a fabricated distribution.
function mockMetaLabelDistribution(): MetaLabelDistribution {
  const symbolCount = 20; // matches STRATEGY_BASE's per-module `scored` count
  const binWidth = 1 / 20;
  const bins: MetaLabelBin[] = Array.from({ length: 20 }, (_, i) => ({
    lo: Math.round(i * binWidth * 10000) / 10000,
    hi: Math.round((i + 1) * binWidth * 10000) / 10000,
    count: i === 19 ? symbolCount : 0,
  }));
  return {
    bins,
    count: symbolCount,
    missing: 0,
    n_gated: 0,
    all_unity: true,
    min: 1.0,
    max: 1.0,
    min_confidence: 0.4, // settings.META_LABEL_MIN_CONFIDENCE default
    reason: null,
  };
}

function mockStrategyMatrix(): StrategyMatrix {
  const ov = readStrategyOverrides();
  const disabled = ov?.disabled ?? [];
  let drift = false;
  try {
    drift = localStorage.getItem(STRATEGY_DRIFT_KEY) === "1";
  } catch {
    /* ignore */
  }
  const modules = STRATEGY_BASE.map((b) => {
    const weight = ov?.weights?.[b.name] ?? b.weight;
    return {
      name: b.name,
      weight,
      effective_weight: weight, // no regime overrides in the mock -> effective == configured
      effective_weight_regime: null,
      enabled: !disabled.includes(b.name),
      source: "both" as const,
      contributed_last_run: true,
      symbols_scored: b.scored,
      pinned_zero: b.pinned,
      version_hash: b.versionHash,
      last_modified: new Date(Date.now() - b.modifiedDaysAgo * 86_400_000).toISOString(),
    };
  });
  return {
    as_of: new Date(Date.now() - 5_400_000).toISOString(),
    market_regime: "RISK ON",
    regime_overrides_active: false,
    weights_source: "running_process_settings",
    modules,
    disabled,
    max_weight: 100,
    writable: true,
    note: "Writes persist to .env and apply on the next daemon/pipeline launch.",
    env_drift: drift
      ? {
          detected: true,
          keys: ["SIGNAL_WEIGHTS"],
          note:
            "An .env write is pending — the API and daemon are still running the " +
            "previous values. Restart to apply.",
        }
      : { detected: false, keys: [], note: "" },
    reason: null,
    meta_label: mockMetaLabelDistribution(),
  };
}

// ---- General runtime tunables editor fixture (GET/PUT /settings/tunables) ----
// Mirrors api/pilots_api.py's REAL _TUNABLE_GROUPS exactly (same 7 group names,
// same ~46-key field set, including the 7 "Advanced / Config" keys the backend
// previously omitted and the 7 portfolio-gross-cap/escalation/audit/alert keys
// added alongside MAX_POSITION_WEIGHT in "Position Sizing") -- every field the
// mock's TUNABLE_DEFS below matches the live backend field-for-field, no
// orphans either direction. Values/defaults/descriptions are pulled from
// settings.py's real pydantic Field(description=) (verified via
// `python3 -c "from settings import Settings; ..."`), not invented placeholders
// -- 17 fields genuinely have no description in settings.py (RISK_FREE_RATE,
// MARKET_RISK_PREMIUM, REQUIRED_RETURN_RATE, MAX_PORTFOLIO_HEAT, KELLY_FRACTION,
// KELLY_CAP, VOL_TARGET, MAX_LEVERAGE, MAX_POSITION_WEIGHT, MAX_PORTFOLIO_GROSS,
// SIZING_CAP_ESCALATION_ENABLED, SIZING_CAP_ESCALATION_THRESHOLD_CYCLES,
// SIZING_CAP_ESCALATION_FACTOR, SIZING_CAP_AUDIT_ENABLED, SIZING_CAP_ALERT_ENABLED,
// SIZING_CAP_ALERT_THRESHOLD_PCT, LOG_LEVEL) and stay `null` here, never
// fabricated (CONSTRAINT #4). MARKET_DATA_PROVIDER is honestly `value: null, default:
// null` too -- its real settings.py default IS None (auto-select; unset until
// an operator forces "alpaca"/"yfinance"). Accepted writes persist to
// localStorage so a later GET reflects them AND marks those keys as env_drift
// (a real .env write does not reach the running process until restart --
// mirrors mockStrategyMatrix's STRATEGY_DRIFT_KEY convention above). A value
// out of its declared bounds is rejected with a reason rather than silently
// written. `kind: "json"` fields (SECTOR_FORECAST_CONFIGS, CORS_ALLOWED_ORIGINS)
// surface as TunableFieldType "string" (a JSON blob is still a string on the
// wire) -- the screen's own content-sniffing renders them as a textarea.
const TUNABLES_KEY = "stockpy.mock.tunables";
const TUNABLES_DRIFT_KEY = "stockpy.mock.tunables_drift";

interface MockTunableDef {
  group: string;
  key: string;
  type: TunableFieldType;
  value: number | boolean | string | null;
  default: number | boolean | string | null;
  description: string | null;
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
}

const TUNABLE_DEFS: MockTunableDef[] = [
  // ---- Financial Constants ----
  {
    group: "Financial Constants", key: "RISK_FREE_RATE", type: "number",
    value: 0.045, default: 0.045, min: 0, max: 1, step: 0.005,
    description: null,
  },
  {
    group: "Financial Constants", key: "MARKET_RISK_PREMIUM", type: "number",
    value: 0.055, default: 0.055, min: 0, max: 1, step: 0.005,
    description: null,
  },
  {
    group: "Financial Constants", key: "REQUIRED_RETURN_RATE", type: "number",
    value: 0.08, default: 0.08, min: 0, max: 1, step: 0.005,
    description: null,
  },
  {
    group: "Financial Constants", key: "MAX_PORTFOLIO_HEAT", type: "number",
    value: 0.06, default: 0.06, min: 0, max: 1, step: 0.01,
    description: null,
  },
  // ---- Position Sizing ----
  {
    group: "Position Sizing", key: "KELLY_FRACTION", type: "number",
    value: 0.5, default: 0.5, min: 0, max: 1, step: 0.05,
    description: null,
  },
  {
    group: "Position Sizing", key: "KELLY_CAP", type: "number",
    value: 0.2, default: 0.2, min: 0, max: 1, step: 0.01,
    description: null,
  },
  {
    group: "Position Sizing", key: "VOL_TARGET", type: "number",
    value: 0.1, default: 0.1, min: 0, max: 1, step: 0.01,
    description: null,
  },
  {
    group: "Position Sizing", key: "MAX_LEVERAGE", type: "number",
    value: 2.0, default: 2.0, min: 0, max: 10, step: 0.1,
    description: null,
  },
  {
    group: "Position Sizing", key: "MAX_POSITION_WEIGHT", type: "number",
    value: 1.0, default: 1.0, min: 0, max: 5, step: 0.05,
    description: null,
  },
  // Portfolio-level gross exposure cap + cap-aware escalation + cap-event
  // audit/alerting (sizing/position_sizer.py, sizing/cap_audit_store.py) --
  // same "no description in settings.py" convention as the five sizing
  // fields above.
  {
    group: "Position Sizing", key: "MAX_PORTFOLIO_GROSS", type: "number",
    value: 3.0, default: 3.0, min: 0, max: 20, step: 0.1,
    description: null,
  },
  {
    group: "Position Sizing", key: "SIZING_CAP_ESCALATION_ENABLED", type: "boolean",
    value: false, default: false,
    description: null,
  },
  {
    group: "Position Sizing", key: "SIZING_CAP_ESCALATION_THRESHOLD_CYCLES", type: "number",
    value: 5, default: 5, min: 1, max: 100, step: 1,
    description: null,
  },
  {
    group: "Position Sizing", key: "SIZING_CAP_ESCALATION_FACTOR", type: "number",
    value: 0.5, default: 0.5, min: 0, max: 1, step: 0.05,
    description: null,
  },
  {
    group: "Position Sizing", key: "SIZING_CAP_AUDIT_ENABLED", type: "boolean",
    value: true, default: true,
    description: null,
  },
  {
    group: "Position Sizing", key: "SIZING_CAP_ALERT_ENABLED", type: "boolean",
    value: false, default: false,
    description: null,
  },
  {
    group: "Position Sizing", key: "SIZING_CAP_ALERT_THRESHOLD_PCT", type: "number",
    value: 0.30, default: 0.30, min: 0, max: 1, step: 0.05,
    description: null,
  },
  // ---- Risk Gate ----
  {
    group: "Risk Gate", key: "MAX_CORRELATION", type: "number",
    value: 0.85, default: 0.85, min: 0, max: 1, step: 0.05,
    description: "Max absolute pairwise return correlation before a new position is blocked.",
  },
  {
    group: "Risk Gate", key: "DAILY_LOSS_LIMIT_PCT", type: "number",
    value: 0.02, default: 0.02, min: 0, max: 1, step: 0.005,
    description: "Halt new BUY orders when intraday P&L drops below this fraction of start-of-day equity.",
  },
  {
    group: "Risk Gate", key: "MAX_ORDER_RATE_PER_MIN", type: "number",
    value: 10, default: 10, min: 1, max: 1000, step: 1,
    description: "Maximum order submissions in any 60-second rolling window.",
  },
  {
    group: "Risk Gate", key: "HMM_RISK_OFF_BLOCK_THRESHOLD", type: "number",
    value: 0.8, default: 0.8, min: 0, max: 1, step: 0.05,
    description: "Block new long orders when HMM risk-off probability exceeds this.",
  },
  {
    group: "Risk Gate", key: "RISK_GATE_ENFORCE_MARKET_HOURS", type: "boolean",
    value: true, default: true,
    description: "Block orders outside NYSE RTH (09:30–16:00 ET).",
  },
  {
    group: "Risk Gate", key: "META_LABEL_MIN_CONFIDENCE", type: "number",
    value: 0.4, default: 0.4, min: 0, max: 1, step: 0.05,
    description: "Minimum meta-label probability for a primary signal to contribute to sizing. If predict_proba < META_LABEL_MIN_CONFIDENCE, the meta_label_composite is forced to 0.0 (position zeroed for the cycle).",
  },
  {
    group: "Risk Gate", key: "DRY_RUN", type: "boolean",
    value: false, default: false,
    description: "Log orders but do not submit to broker.",
  },
  // ---- Forecasting ----
  {
    group: "Forecasting", key: "FORECAST_USE_GARCH_SIGMA", type: "boolean",
    value: true, default: true,
    description: "Use the GJR-GARCH(1,1) volatility estimate (annualized, converted to daily via /sqrt(252)) as the Monte Carlo sigma instead of naive historical stdev. False restores the pre-GARCH log-return-std behavior.",
  },
  {
    group: "Forecasting", key: "FORECAST_PROPHET_WEIGHT", type: "number",
    value: 0.25, default: 0.25, min: 0, max: 1, step: 0.05,
    description: "Weight given to the Prophet 30-day forecast when blending it into the static ensemble at the 30-day horizon: final = base*(1-w) + prophet*w. 0.0 disables Prophet's influence on the blend.",
  },
  {
    group: "Forecasting", key: "FORECAST_SKILL_WEIGHTING_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Opt-in activation of inverse-RMSE skill-weighted multi-model forecast blending (ARIMA / Monte Carlo / Holt-Winters / CNN-LSTM weighted by recent realized accuracy via forecasting.forecast_tracker.ForecastTracker). When False (the default) the static sector-preference blend is used unchanged.",
  },
  {
    group: "Forecasting", key: "FORECAST_SKILL_WINDOW_DAYS", type: "number",
    value: 180, default: 180, min: 1, max: 3650, step: 1,
    description: "Rolling window (calendar days) over which per-model RMSE is computed for inverse-skill forecast blending. Increase for stability; decrease for faster adaptation.",
  },
  {
    group: "Forecasting", key: "FORECAST_MODEL_PERSISTENCE_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Opt-in: persist the trained CNN-LSTM (.keras + both MinMaxScalers) and Prophet model to disk per ticker instead of retraining from scratch every cycle.",
  },
  {
    group: "Forecasting", key: "FORECAST_MODEL_RETRAIN_DAYS", type: "number",
    value: 7, default: 7, min: 1, max: 3650, step: 1,
    description: "Days a persisted CNN-LSTM/Prophet model artifact remains valid before the next generate_forecast() call for that ticker triggers a fresh fit. Only consulted when FORECAST_MODEL_PERSISTENCE_ENABLED=True.",
  },
  {
    group: "Forecasting", key: "BETA_LOOKBACK_DAYS", type: "number",
    value: 504, default: 504, min: 1, max: 3650, step: 1,
    description: "Trailing calendar days of daily returns used to compute beta in the Yahoo-derived fundamentals engine (Cov(stock,SPY)/Var(SPY)). ~2 years.",
  },
  // ---- Market Data ----
  {
    // Honest absent value: settings.py's real default IS None (auto-select
    // by key availability) -- never fabricated as "alpaca"/"yfinance".
    group: "Market Data", key: "MARKET_DATA_PROVIDER", type: "enum",
    value: null, default: null, options: ["alpaca", "yfinance", "fmp"],
    description: "Force a specific market-data backend: 'fmp', 'alpaca' or 'yfinance'. When unset the platform auto-selects based on key availability (Alpaca if its keys are present, else yfinance). Setting FMP_API_KEY alone NEVER auto-elects FMP: unlike the Alpaca ladder, FMP is chosen only by explicitly setting this to 'fmp', so an operator who adds the key to enable the analyst or earnings feed does not silently have their quote/bars source change underneath them. FMP quotes/bars additionally require FMP_QUOTES_ENABLED / FMP_BARS_ENABLED (the two-gate convention).",
  },
  {
    group: "Market Data", key: "MARKET_DATA_QUOTE_TTL_SECONDS", type: "number",
    value: 30, default: 30, min: 0, max: 86400, step: 1,
    description: "In-process quote cache TTL in seconds (never persisted to disk).",
  },
  {
    group: "Market Data", key: "MARKET_DATA_BARS_TTL_SECONDS", type: "number",
    value: 900, default: 900, min: 0, max: 86400, step: 1,
    description: "In-process OHLCV intraday-bars cache TTL in seconds (never persisted to disk).",
  },
  {
    group: "Market Data", key: "FUNDAMENTALS_SOURCE", type: "enum",
    value: "yahoo", default: "yahoo", options: ["yahoo", "yfinance_info", "fmp"],
    description: "Primary fundamentals backend: 'yahoo' (statement-derived, default), 'yfinance_info' (raw .info fallback), or 'fmp' (Financial Modeling Prep — see section 25). Finnhub is no longer a fundamentals source. Setting FMP_API_KEY alone NEVER auto-elects FMP: it must be chosen explicitly here, so adding the key for one feed cannot silently change what every valuation metric is computed from. 'fmp' additionally requires FMP_FUNDAMENTALS_ENABLED=true (the two-gate convention); with either half missing the Yahoo path is used, exactly as today.",
  },
  // ---- Runtime & Ops ----
  {
    group: "Runtime & Ops", key: "DASHBOARD_REFRESH_SECONDS", type: "number",
    value: 1800, default: 1800, min: 1, max: 86400, step: 1,
    description: "Auto-refresh interval for the Streamlit observability dashboard (seconds). Default 1800 = 30 min.",
  },
  {
    group: "Runtime & Ops", key: "PROGRESS_POLL_SECONDS", type: "number",
    value: 5, default: 5, min: 1, max: 3600, step: 1,
    description: "Poll interval (seconds) for the Launcher pipeline-progress indicator.",
  },
  {
    group: "Runtime & Ops", key: "LOG_LEVEL", type: "enum",
    value: "INFO", default: "INFO", options: ["DEBUG", "INFO", "WARNING", "ERROR"],
    description: null,
  },
  {
    group: "Runtime & Ops", key: "ADVISORY_REUSE_PIPELINE_COMPUTE", type: "boolean",
    value: false, default: false,
    description: "Opt-in, OUTPUT-CHANGING: main_orchestrator.py's advisory overlay reuses run_pipeline's already-computed GARCH/forecast values for that ticker instead of independently refitting a second time. When False (the default), every advisory-overlay call refits independently, reproducing the exact pre-dedup behavior.",
  },
  {
    group: "Runtime & Ops", key: "ADVISORY_ONLY", type: "boolean",
    value: true, default: true,
    description: "When True, ALL broker order submission is suppressed. The pipeline still runs end-to-end (signals, sizing, HTML report, JSON payload) but order execution returns immediately. Set False ONLY when broker execution is intentionally re-enabled.",
  },
  // ---- Advanced / Config (the 7 keys the real Streamlit tab's own
  // _SETTINGS_LAYOUT, gui/panels/settings_manager.py:36-77, already served) ----
  {
    group: "Advanced / Config", key: "SECTOR_FORECAST_CONFIG_PATH", type: "string",
    value: "forecasting/sector_configs.json", default: "forecasting/sector_configs.json",
    description: "Path to the committed per-sector forecast config artifact (model+horizon per sector, derived from an offline walk-forward backtest). Loaded once at ForecastingEngine init; the hardcoded default dict is used as fallback when the file is missing or invalid.",
  },
  {
    group: "Advanced / Config", key: "SECTOR_FORECAST_CONFIGS", type: "string",
    value: "{}", default: "{}",
    description: 'Optional per-sector override merged OVER the artifact/hardcoded default. JSON dict in .env, e.g. {"Technology": {"days": 30, "model": "MC"}}. Empty dict (the default) leaves the artifact/hardcoded default unchanged (fully backward-compatible).',
  },
  {
    group: "Advanced / Config", key: "PROMPT_REGISTRY_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Master switch. False (default) → baseline-only, zero network calls. Set True to enable remote manifest fetch and cache.",
  },
  {
    group: "Advanced / Config", key: "PROMPT_REGISTRY_BACKEND", type: "string",
    value: "http", default: "http",
    description: "Storage backend: 'http' (default, protected HTTPS endpoint), 'local' (LocalJSONStore from a file path), or 'firestore' (lazy import).",
  },
  {
    group: "Advanced / Config", key: "ORCHESTRATOR_DAEMON_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Route the desktop shell's always-on refresh loop and the Launcher tab's manual run trigger through the persistent orchestrator daemon instead of spawning a fresh subprocess per cycle. False (default) preserves today's exact subprocess behavior everywhere.",
  },
  {
    group: "Advanced / Config", key: "PILOTS_API_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Host the Pilots API inside the persistent orchestrator daemon process, alongside the existing Control API. False (default) preserves today's exact behavior -- pilots_api.py remains a manually-launched standalone service.",
  },
  {
    group: "Advanced / Config", key: "CORS_ALLOWED_ORIGINS", type: "string",
    value: '["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5173"]',
    default: '["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5173"]',
    description: 'Allowed browser origins for the read-only State API / Pilots API CORS policy. JSON array in .env, e.g. ["http://localhost:3000", "https://app.example.com"].',
  },
];

function readOverrides(storageKey: string): Record<string, number | boolean | string> {
  try {
    const raw = localStorage.getItem(storageKey);
    return raw ? (JSON.parse(raw) as Record<string, number | boolean | string>) : {};
  } catch {
    return {};
  }
}

function readDrift(storageKey: string): string[] {
  try {
    const raw = localStorage.getItem(storageKey);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

// Shared by every mock `/settings/*` editor (general tunables, sentiment,
// sector-selection) -- each passes its own defs list + a dedicated pair of
// localStorage keys so their overrides/drift never collide.
function buildTunablesResponse(
  defs: MockTunableDef[],
  overridesKey: string,
  driftKey: string
): TunablesResponse {
  const ov = readOverrides(overridesKey);
  const groups: TunablesResponse["groups"] = [];
  for (const def of defs) {
    let group = groups.find((g) => g.name === def.group);
    if (!group) {
      group = { name: def.group, fields: [] };
      groups.push(group);
    }
    const field: TunableField = {
      key: def.key,
      value: def.key in ov ? ov[def.key] : def.value,
      type: def.type,
      default: def.default,
      description: def.description,
    };
    if (def.min !== undefined) field.min = def.min;
    if (def.max !== undefined) field.max = def.max;
    if (def.step !== undefined) field.step = def.step;
    if (def.options !== undefined) field.options = def.options;
    group.fields.push(field);
  }
  const driftKeys = readDrift(driftKey);
  return {
    applies: "next_daemon_restart",
    groups,
    env_drift: driftKeys.length
      ? {
          detected: true,
          keys: driftKeys,
          note:
            "An .env write is pending — the API and daemon are still running the " +
            "previous values. Restart to apply.",
        }
      : { detected: false, keys: [], note: "" },
  };
}

function applyTunablesGeneric(
  values: Record<string, number | boolean | string>,
  defs: MockTunableDef[],
  overridesKey: string,
  driftKey: string
): TunablesUpdateResult {
  const written: Record<string, number | boolean | string> = {};
  const rejected: Record<string, string> = {};
  const byKey = new Map(defs.map((d) => [d.key, d]));
  for (const [key, val] of Object.entries(values)) {
    const def = byKey.get(key);
    if (!def) {
      rejected[key] = "unknown_key: not a recognized tunable.";
      continue;
    }
    if (def.type === "number") {
      const n = typeof val === "number" ? val : Number(val);
      if (!Number.isFinite(n)) {
        rejected[key] = "type_mismatch: expected a number.";
        continue;
      }
      if (
        (def.min !== undefined && n < def.min) ||
        (def.max !== undefined && n > def.max)
      ) {
        rejected[key] = `out_of_range: must be within [${def.min}, ${def.max}].`;
        continue;
      }
      written[key] = n;
    } else if (def.type === "boolean") {
      written[key] = Boolean(val);
    } else if (def.type === "enum") {
      if (def.options && !def.options.includes(String(val))) {
        rejected[key] = `invalid_option: must be one of ${def.options.join(", ")}.`;
        continue;
      }
      written[key] = String(val);
    } else {
      // "string" (including JSON-blob fields, e.g. CORS_ALLOWED_ORIGINS) --
      // the mock doesn't re-validate JSON shape server-side; that's the real
      // backend's job (invalid_json), exercised in the Python test suite.
      written[key] = String(val);
    }
  }
  if (Object.keys(written).length > 0) {
    try {
      localStorage.setItem(overridesKey, JSON.stringify({ ...readOverrides(overridesKey), ...written }));
      // A .env write does NOT reach the running process until restart --
      // mark every written key as drifted (mirrors STRATEGY_DRIFT_KEY above).
      const drift = new Set([...readDrift(driftKey), ...Object.keys(written)]);
      localStorage.setItem(driftKey, JSON.stringify([...drift]));
    } catch {
      /* ignore quota */
    }
  }
  return { written, rejected, applies: "next_daemon_restart" };
}

function mockTunables(): TunablesResponse {
  return buildTunablesResponse(TUNABLE_DEFS, TUNABLES_KEY, TUNABLES_DRIFT_KEY);
}

function applyTunables(
  values: Record<string, number | boolean | string>
): TunablesUpdateResult {
  return applyTunablesGeneric(values, TUNABLE_DEFS, TUNABLES_KEY, TUNABLES_DRIFT_KEY);
}

// ---------------------------------------------------------------------------
// Dedicated Sentiment & Sector Selection tunables (webapp /settings/sentiment,
// /settings/sector-selection) -- mirrors api/pilots_api.py's _SENTIMENT_GROUPS
// / _SECTOR_SELECTION_GROUPS exactly (same keys, types, bounds, real
// settings.py defaults). Every key here is a REAL settings.py field, verified
// against Settings.model_fields on the backend side -- see that module's
// _SENTIMENT_GROUPS comment for why a fabricated key would be a silent no-op
// were it ever written for real. "Sector Selection" is data/sector_selection_
// heat.py's semantic-similarity feature backing SectorSelection.tsx -- NOT a
// momentum/value/volatility factor rotation.
const SENTIMENT_TUNABLES_KEY = "stockpy.mock.sentiment_tunables";
const SENTIMENT_TUNABLES_DRIFT_KEY = "stockpy.mock.sentiment_tunables_drift";
const SECTOR_SELECTION_TUNABLES_KEY = "stockpy.mock.sector_selection_tunables";
const SECTOR_SELECTION_TUNABLES_DRIFT_KEY = "stockpy.mock.sector_selection_tunables_drift";

const SENTIMENT_TUNABLE_DEFS: MockTunableDef[] = [
  // ---- Sentiment Ingestion Core ----
  {
    group: "Sentiment Ingestion Core", key: "SENTIMENT_INGESTION_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Master switch for multi-source sentiment ingestion (Yahoo RSS/GDELT/Reddit/EDGAR). False is a complete no-op.",
  },
  {
    group: "Sentiment Ingestion Core", key: "SENTIMENT_SOURCES", type: "string",
    value: "yahoo_rss,gdelt,reddit,edgar", default: "yahoo_rss,gdelt,reddit,edgar",
    description: "Comma-separated list of enabled sentiment-source provider names.",
  },
  {
    group: "Sentiment Ingestion Core", key: "SENTIMENT_COMMENT_SOURCES", type: "string",
    value: "reddit,stocktwits", default: "reddit,stocktwits",
    description: "Comma-separated subset of SENTIMENT_SOURCES classified as investor-forum comment sources.",
  },
  {
    group: "Sentiment Ingestion Core", key: "SENTIMENT_INGESTION_LOOKBACK_DAYS", type: "number",
    value: 1, default: 1, min: 1, max: 90, step: 1,
    description: "Calendar days of lookback each ingestion cycle requests from every enabled source.",
  },
  {
    group: "Sentiment Ingestion Core", key: "SENTIMENT_MAX_DOCUMENTS_PER_CYCLE", type: "number",
    value: 2000, default: 2000, min: 1, max: 20000, step: 1,
    description: "Per-cycle document budget shared across all symbols.",
  },
  {
    group: "Sentiment Ingestion Core", key: "SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE", type: "number",
    value: 60.0, default: 60.0, min: 1.0, max: 600.0, step: 1.0,
    description: "Hard wall-clock ceiling (seconds) for the entire per-cycle ingestion run.",
  },
  {
    group: "Sentiment Ingestion Core", key: "SENTIMENT_CIRCUIT_BREAKER_THRESHOLD", type: "number",
    value: 3, default: 3, min: 1, max: 20, step: 1,
    description: "Consecutive failures for a single source within one cycle before it's skipped for the rest of the cycle.",
  },
  // ---- Sources — Reddit, StockTwits, EDGAR, GDELT, Google News ----
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News", key: "STOCKTWITS_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Master switch for the free, uncredentialed StockTwits source.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News", key: "REDDIT_USER_AGENT", type: "string",
    value: "stockpy-sentiment-ingestion/0.1", default: "stockpy-sentiment-ingestion/0.1",
    description: "User-Agent header sent with every Reddit API request, per Reddit's API rules.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News", key: "REDDIT_BACKFILL_MAX_PAGES", type: "number",
    value: 10, default: 10, min: 1, max: 100, step: 1,
    description: "Max pages RedditSource paginates through for a historical backfill request.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News", key: "GOOGLE_NEWS_LOOKBACK_WINDOW", type: "string",
    value: "7d", default: "7d",
    description: "Lookback window passed as Google News RSS's `when:` query parameter.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News", key: "EDGAR_FULLTEXT_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Master switch for the SEC EDGAR full-text search (10-K/10-Q) additions to EdgarSource.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News", key: "EDGAR_FULLTEXT_FORMS", type: "string",
    value: "8-K,10-K,10-Q", default: "8-K,10-K,10-Q",
    description: "Comma-separated SEC form types requested from EDGAR full-text search.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News", key: "EDGAR_FULLTEXT_CHUNK_TOKENS", type: "number",
    value: 512, default: 512, min: 64, max: 4096, step: 64,
    description: "Maximum tokens per filing-text chunk for FinBERT scoring.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News", key: "GDELT_MIN_REQUEST_INTERVAL_SECONDS", type: "number",
    value: 5.0, default: 5.0, min: 0.0, max: 60.0, step: 0.5,
    description: "Minimum seconds between GDELT DOC API request issuance, shared process-wide.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News", key: "GDELT_MAX_RETRIES", type: "number",
    value: 2, default: 2, min: 0, max: 10, step: 1,
    description: "Retries after a GDELT HTTP 429/5xx before the request is given up on.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News", key: "GDELT_RETRY_BACKOFF_SECONDS", type: "number",
    value: 5.0, default: 5.0, min: 0.5, max: 60.0, step: 0.5,
    description: "Base seconds for the GDELT retry backoff.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News", key: "GDELT_COOLDOWN_THRESHOLD", type: "number",
    value: 3, default: 3, min: 1, max: 10, step: 1,
    description: "Consecutive failed GDELT requests after which calls are skipped outright for a cooldown period.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News", key: "GDELT_COOLDOWN_SECONDS", type: "number",
    value: 300.0, default: 300.0, min: 10.0, max: 3600.0, step: 10.0,
    description: "How long the GDELT cooldown stays open once the failure threshold is reached.",
  },
  // ---- FinBERT & Catalyst Scoring ----
  {
    group: "FinBERT & Catalyst Scoring", key: "FINBERT_ENABLED", type: "boolean",
    value: true, default: true,
    description: "Use ProsusAI/FinBERT for headline sentiment when `transformers` is installed; falls back to a keyword lexicon otherwise.",
  },
  {
    group: "FinBERT & Catalyst Scoring", key: "FINBERT_BATCH_SIZE", type: "number",
    value: 16, default: 16, min: 1, max: 128, step: 1,
    description: "Headlines per forward pass when a real FinBERT pipeline is loaded.",
  },
  {
    group: "FinBERT & Catalyst Scoring", key: "FINBERT_SCORE_CACHE_ENABLED", type: "boolean",
    value: true, default: true,
    description: "Cache FinBERT/lexicon headline scores by content hash so an unchanged headline is not re-scored.",
  },
  {
    group: "FinBERT & Catalyst Scoring", key: "NEWS_LOOKBACK_DAYS", type: "number",
    value: 7, default: 7, min: 1, max: 90, step: 1,
    description: "Calendar days of Finnhub company_news headlines scored per symbol per cycle.",
  },
  {
    group: "FinBERT & Catalyst Scoring", key: "FINNHUB_RATE_LIMIT_PER_MIN", type: "number",
    value: 50, default: 50, min: 1, max: 60, step: 1,
    description: "Finnhub sliding-window call budget per 60s (free tier ceiling: 60).",
  },
  {
    group: "FinBERT & Catalyst Scoring", key: "SENTIMENT_SOCIAL_BLEND_WEIGHT", type: "number",
    value: 0.4, default: 0.4, min: 0.0, max: 1.0, step: 0.05,
    description: "Weight on the multi-source social sentiment component of the blended catalyst score.",
  },
  // ---- AI Credibility Verification ----
  {
    group: "AI Credibility Verification", key: "SENTIMENT_LLM_VERIFICATION_ENABLED", type: "boolean",
    value: false, default: false,
    description: "When True, borderline-credibility documents are verified via an LLM call instead of the heuristic placeholder.",
  },
  {
    group: "AI Credibility Verification", key: "SENTIMENT_LLM_VERIFICATION_PROVIDER", type: "enum",
    value: "none", default: "none", options: ["claude", "gemini", "openai", "none"],
    description: "Which LLM provider backs sentiment-document verification.",
  },
  {
    group: "AI Credibility Verification", key: "SENTIMENT_LLM_VERIFICATION_MAX_CALLS_PER_CYCLE", type: "number",
    value: 25, default: 25, min: 0, max: 500, step: 1,
    description: "Per-batch cap on real LLM calls made for credibility verification.",
  },
  // ---- Attention & Sector Heat ----
  {
    group: "Attention & Sector Heat", key: "SECTOR_HEAT_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Master switch for the GDELT article-volume-based Sector Heat Factor attention feature.",
  },
  {
    group: "Attention & Sector Heat", key: "SECTOR_HEAT_SMOOTHING_SIGMA", type: "number",
    value: 1.0, default: 1.0, min: 0.1, max: 10.0, step: 0.1,
    description: "Gaussian smoothing sigma applied to the raw daily GDELT article-volume series.",
  },
  {
    group: "Attention & Sector Heat", key: "SECTOR_HEAT_LOOKBACK_DAYS", type: "number",
    value: 7, default: 7, min: 1, max: 90, step: 1,
    description: "Calendar days of GDELT article-volume history used to compute the Sector Heat Factor.",
  },
  {
    group: "Attention & Sector Heat", key: "WIKIPEDIA_ATTENTION_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Master switch for the Wikipedia-pageviews-based retail-attention feature.",
  },
  {
    group: "Attention & Sector Heat", key: "WIKIPEDIA_ATTENTION_LOOKBACK_DAYS", type: "number",
    value: 30, default: 30, min: 1, max: 365, step: 1,
    description: "Calendar days of Wikipedia pageview history used to compute the attention baseline/z-score.",
  },
  {
    group: "Attention & Sector Heat", key: "PYTRENDS_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Best-effort optional Google Trends overlay on top of the Wikipedia-pageviews attention feature.",
  },
];

const SECTOR_SELECTION_TUNABLE_DEFS: MockTunableDef[] = [
  {
    group: "Related Sector Selection", key: "SECTOR_SELECTION_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Master switch for the semantic Related Sector Selection feature's Gaussian-response Sector Heat term.",
  },
  {
    group: "Related Sector Selection", key: "SECTOR_SELECTION_TOP_N", type: "number",
    value: 3, default: 3, min: 1, max: 11, step: 1,
    description: "Default number of top-ranked related sectors selected per target symbol.",
  },
  {
    group: "Related Sector Selection", key: "SECTOR_SELECTION_W1", type: "number",
    value: 0.4, default: 0.4, min: 0.0, max: 1.0, step: 0.05,
    description: "Default news-volume weight, mirrored from the composite sentiment index.",
  },
  {
    group: "Related Sector Selection", key: "SECTOR_SELECTION_W2", type: "number",
    value: 0.1, default: 0.1, min: 0.0, max: 1.0, step: 0.05,
    description: "Default review-volume weight.",
  },
  {
    group: "Related Sector Selection", key: "SECTOR_SELECTION_HEAT_LOOKBACK_DAYS", type: "number",
    value: 22, default: 22, min: 1, max: 252, step: 1,
    description: "Trailing trading days of sentiment volume summed per candidate sector before min-max normalization.",
  },
  {
    group: "Related Sector Selection", key: "SECTOR_SELECTION_HEAT_A", type: "number",
    value: 0.8, default: 0.8, min: 0.0, max: 5.0, step: 0.05,
    description: "Gaussian amplitude 'a' in SHF = a * exp(-(x-b)^2 / (2c^2)).",
  },
  {
    group: "Related Sector Selection", key: "SECTOR_SELECTION_HEAT_B", type: "number",
    value: 1.0, default: 1.0, min: 0.0, max: 5.0, step: 0.05,
    description: "Gaussian center 'b' in SHF = a * exp(-(x-b)^2 / (2c^2)).",
  },
  {
    group: "Related Sector Selection", key: "SECTOR_SELECTION_HEAT_C", type: "number",
    value: 0.6, default: 0.6, min: 0.05, max: 5.0, step: 0.05,
    description: "Gaussian width 'c' in SHF = a * exp(-(x-b)^2 / (2c^2)).",
  },
  {
    group: "Related Sector Selection", key: "SECTOR_SIMILARITY_EMBEDDER", type: "enum",
    value: "sbert", default: "sbert", options: ["sbert", "openai", "none"],
    description: "Embedding backend for the semantic-similarity term.",
  },
  {
    group: "Related Sector Selection", key: "SECTOR_SIMILARITY_MODEL", type: "string",
    value: "sentence-transformers/all-MiniLM-L6-v2", default: "sentence-transformers/all-MiniLM-L6-v2",
    description: "Hugging Face model id loaded when SECTOR_SIMILARITY_EMBEDDER is 'sbert'.",
  },
  {
    group: "Related Sector Selection", key: "SECTOR_SIMILARITY_POOLING", type: "enum",
    value: "max", default: "max", options: ["max", "mean"],
    description: "Pooling strategy applied to SBERT token embeddings.",
  },
];

function mockSentimentTunables(): TunablesResponse {
  return buildTunablesResponse(SENTIMENT_TUNABLE_DEFS, SENTIMENT_TUNABLES_KEY, SENTIMENT_TUNABLES_DRIFT_KEY);
}

function applySentimentTunables(
  values: Record<string, number | boolean | string>
): TunablesUpdateResult {
  return applyTunablesGeneric(values, SENTIMENT_TUNABLE_DEFS, SENTIMENT_TUNABLES_KEY, SENTIMENT_TUNABLES_DRIFT_KEY);
}

function mockSectorSelectionTunables(): TunablesResponse {
  return buildTunablesResponse(
    SECTOR_SELECTION_TUNABLE_DEFS,
    SECTOR_SELECTION_TUNABLES_KEY,
    SECTOR_SELECTION_TUNABLES_DRIFT_KEY
  );
}

function applySectorSelectionTunables(
  values: Record<string, number | boolean | string>
): TunablesUpdateResult {
  return applyTunablesGeneric(
    values,
    SECTOR_SELECTION_TUNABLE_DEFS,
    SECTOR_SELECTION_TUNABLES_KEY,
    SECTOR_SELECTION_TUNABLES_DRIFT_KEY
  );
}

// ---- Realized broker P&L fixture (FIFO round-trips) ----
const REALIZED_TRADES: RealizedTrade[] = [
  rt("NVDA", 10, 82.4, 132.6, 41),
  rt("AAPL", 20, 172.1, 168.9, 12),
  rt("MSFT", 8, 351.2, 431.0, 63),
  rt("V", 15, 245.0, 279.8, 88),
  rt("COST", 3, 640.0, 889.4, 120),
  rt("DUK", 40, 99.1, 91.2, 22),
];

function rt(
  symbol: string,
  quantity: number,
  entry: number,
  exit: number,
  holdDays: number
): RealizedTrade {
  const pnl = +((exit - entry) * quantity).toFixed(2);
  const now = Date.now();
  return {
    symbol,
    quantity,
    entry_ts: new Date(now - (holdDays + 5) * 86400000).toISOString(),
    exit_ts: new Date(now - 5 * 86400000).toISOString(),
    entry_price: entry,
    exit_price: exit,
    realized_pnl: pnl,
    return_pct: +(((exit - entry) / entry) * 100).toFixed(2),
    holding_days: holdDays,
  };
}

function realizedSummary(trades: RealizedTrade[]) {
  const pnls = trades.map((t) => t.realized_pnl ?? 0);
  const wins = pnls.filter((p) => p > 0);
  const losses = pnls.filter((p) => p < 0);
  const gp = +wins.reduce((a, b) => a + b, 0).toFixed(2);
  const gl = +losses.reduce((a, b) => a + b, 0).toFixed(2);
  return {
    n_trades: trades.length,
    total_realized_pnl: +pnls.reduce((a, b) => a + b, 0).toFixed(2),
    win_rate: trades.length ? +(wins.length / trades.length).toFixed(4) : null,
    avg_win: wins.length ? +(gp / wins.length).toFixed(2) : null,
    avg_loss: losses.length ? +(gl / losses.length).toFixed(2) : null,
    profit_factor: losses.length ? +(gp / Math.abs(gl)).toFixed(3) : null,
    avg_return_pct: +(
      trades.reduce((a, t) => a + (t.return_pct ?? 0), 0) / (trades.length || 1)
    ).toFixed(2),
    avg_holding_days: +(
      trades.reduce((a, t) => a + (t.holding_days ?? 0), 0) / (trades.length || 1)
    ).toFixed(1),
    best_trade_pnl: pnls.length ? Math.max(...pnls) : null,
    worst_trade_pnl: pnls.length ? Math.min(...pnls) : null,
    gross_profit: gp,
    gross_loss: gl,
  };
}

// ---- Alerts feed fixture ----
function mockAlerts(): AlertsFeed {
  const now = Date.now();
  return {
    reason: null,
    entries: [
      {
        timestamp: new Date(now - 8 * 60000).toISOString(),
        level: "INFO",
        message: "Refresh complete — 6 symbols evaluated, 2 BUY / 3 HOLD / 1 SELL.",
        extra: { type: "run_summary", symbols: 6 },
      },
      {
        timestamp: new Date(now - 52 * 60000).toISOString(),
        level: "WARNING",
        message: "Portfolio heat 6.1% exceeds the 5% soft cap.",
        extra: { type: "risk", heat: 0.061 },
      },
      {
        timestamp: new Date(now - 3 * 3600000).toISOString(),
        level: "CRITICAL",
        message: "HMM regime flipped to risk-off (risk_on_probability 0.22).",
        extra: { type: "regime", risk_on: 0.22 },
      },
      {
        timestamp: new Date(now - 26 * 3600000).toISOString(),
        level: "INFO",
        message: "Fill: bought 4 NVDA @ $131.90 (paper).",
        extra: { type: "fill", symbol: "NVDA" },
      },
    ],
  };
}

// ---- Forecast reliability fixture ----
function mockForecast(ticker: string, horizon = 30): ForecastSkill {
  const sym = ticker.toUpperCase();
  if (!SYMBOL_UNIVERSE.has(sym)) {
    return {
      symbol: sym,
      horizon_days: horizon,
      reliability_curve: [],
      skill_weights: {},
      error_by_model: [],
      pending: 0,
      completed: 0,
      reason: "No forecast history yet — run the pipeline to accumulate it.",
    };
  }
  const rng = seeded([...sym].reduce((a, c) => a + c.charCodeAt(0), 0) + horizon);
  // BERT-LLA's three ablations only show up for AAPL in this fixture --
  // BERT_LLA_ENABLED defaults False in production (matching the attention
  // overlay fixture's own symbol choice above), so every OTHER symbol
  // honestly shows just the four models that are always potentially active.
  const models =
    sym === "AAPL"
      ? ["arima", "monte_carlo", "holt_winters", "cnn_lstm", "lstm_baseline", "lstm_attention", "bert_lla"]
      : ["arima", "monte_carlo", "holt_winters", "cnn_lstm"];
  const curve = models.flatMap((m) =>
    [-0.3, -0.1, 0.1, 0.3].map((center) => ({
      model_name: m,
      horizon_days: horizon,
      bin_center: center,
      // some bins honestly null (too few samples)
      mean_pct_error: rng() < 0.2 ? null : +((rng() - 0.5) * 0.12).toFixed(4),
      count: Math.floor(rng() * 12) + 1,
    }))
  );
  const raw = models.map(() => 0.1 + rng());
  const tot = raw.reduce((a, b) => a + b, 0);
  const skill_weights: Record<string, number> = {};
  models.forEach((m, i) => (skill_weights[m] = +(raw[i] / tot).toFixed(3)));
  const completed = Math.floor(rng() * 60) + 20;
  // Per-model RMSE/MAE, sorted ascending (best model first) -- matches the
  // real endpoint's contract (forecasting/forecast_tracker.py::get_error_by_model).
  // MAE is always <= RMSE here (mirrors the real relationship: RMSE penalizes
  // large errors more, so RMSE >= MAE for any non-uniform error distribution).
  const error_by_model = models
    .map((m) => {
      const rmse = +(1 + rng() * 8).toFixed(2);
      const mae = +(rmse * (0.7 + rng() * 0.25)).toFixed(2);
      return { model_name: m, n: Math.floor(rng() * completed * 0.6) + 5, rmse, mae };
    })
    .sort((a, b) => a.rmse - b.rmse);
  return {
    symbol: sym,
    horizon_days: horizon,
    reliability_curve: curve,
    skill_weights,
    error_by_model,
    pending: Math.floor(rng() * 5),
    completed,
    reason: null,
  };
}

// ---- Semantic Related Sector Selection fixture ----
// Deliberately an HONESTY fixture, not a happy path: one row fully
// populated, one with cosine_similarity null (no sector description), one
// with sector_heat_factor/correlation_coefficient null (no volume observed
// at all -- excluded from ranking), and every fully-computed row carries
// degraded_reason="review_unavailable" -- the REALISTIC default state for a
// typical deployment (the investor-forum comment channel isn't active by
// default), so the screen's persistent degradation banner has something
// real to render even in the common case.
const SECTOR_SELECTION_CANDIDATES = [
  "New Energy",
  "Automotive Parts",
  "Autonomous Driving",
  "Lithium Battery",
  "Charging Post",
  "Semiconductor",
];

function mockSectorSelection(target: string, n = 3): SectorSelectionView {
  const sym = target.toUpperCase();
  if (!SYMBOL_UNIVERSE.has(sym)) {
    return {
      target_symbol: sym,
      as_of: null,
      top_n: n,
      rows: [],
      embedder: null,
      pooling: null,
      reason: "No sector selection has been computed for this symbol yet.",
    };
  }

  const rng = seeded([...sym].reduce((a, c) => a + c.charCodeAt(0), 0));
  type RawRow = Omit<SectorSelectionRow, "rank" | "selected">;
  const raw: RawRow[] = SECTOR_SELECTION_CANDIDATES.map((sector, i) => {
    if (i === 2) {
      // Honesty branch: no sector description available -> similarity unavailable.
      return {
        sector,
        cosine_similarity: null,
        ingestion_volume: +(rng() * 40).toFixed(1),
        sector_heat_factor: +(rng() * 0.8).toFixed(3),
        correlation_coefficient: null,
        degraded_reason: "no_sector_description",
      };
    }
    if (i === 5) {
      // Honesty branch: this sector's member tickers were never ingested at all.
      return {
        sector,
        cosine_similarity: +(0.2 + rng() * 0.6).toFixed(3),
        ingestion_volume: null,
        sector_heat_factor: null,
        correlation_coefficient: null,
        degraded_reason: "no_volume_observed",
      };
    }
    const cos = +(0.2 + rng() * 0.6).toFixed(3);
    const shf = +(0.3 + rng() * 0.5).toFixed(3);
    return {
      sector,
      cosine_similarity: cos,
      ingestion_volume: +(rng() * 60).toFixed(1),
      sector_heat_factor: shf,
      correlation_coefficient: +(cos * shf).toFixed(4),
      degraded_reason: "review_unavailable",
    };
  });

  const ranked = [...raw].sort((a, b) => {
    const av = a.correlation_coefficient;
    const bv = b.correlation_coefficient;
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return bv - av;
  });

  let rankCounter = 0;
  const rows: SectorSelectionRow[] = ranked.map((r) => {
    if (r.correlation_coefficient == null) {
      return { ...r, rank: null, selected: false };
    }
    rankCounter += 1;
    return { ...r, rank: rankCounter, selected: rankCounter <= n };
  });

  return {
    target_symbol: sym,
    as_of: "2026-07-26",
    top_n: n,
    rows,
    embedder: "sbert",
    pooling: "max",
    reason: null,
  };
}

// ---- BERT-LLA attention-weight overlay fixture ----
// Attention concentrated around a couple of "event" days (earnings-like
// spikes) rather than uniform across the window -- a flat/uniform alpha
// series would look like a bug (the whole point of attention is that it
// ISN'T uniform), so the fixture deliberately peaks at two days.
function mockBertLlaAttention(symbol: string): ForecastAttention {
  const windowSize = 22;
  const rng = seeded([...symbol].reduce((a, c) => a + c.charCodeAt(0), 0));
  const eventDay1 = 5 + Math.floor(rng() * 5); // early-window spike
  const eventDay2 = 14 + Math.floor(rng() * 5); // late-window spike
  const raw: number[] = [];
  for (let i = 0; i < windowSize; i++) {
    const distTo1 = Math.abs(i - eventDay1);
    const distTo2 = Math.abs(i - eventDay2);
    const base = 0.3 + rng() * 0.2;
    const spike = 4.0 * Math.exp(-0.5 * Math.min(distTo1, distTo2));
    raw.push(base + spike);
  }
  const total = raw.reduce((a, b) => a + b, 0);
  const now = Date.now();
  const weights = raw.map((v, i) => ({
    date: new Date(now - (windowSize - 1 - i) * 86_400_000).toISOString().slice(0, 10),
    alpha: +(v / total).toFixed(4),
  }));
  return { model: "bert_lla", window_size: windowSize, weights };
}

// ---- Rolling beta vs SPY fixture ----
// A mean-reverting daily walk around a symbol-specific mean beta -- looks like
// a real drifting-but-anchored beta series, not white noise or a flat line.
function mockRollingBeta(ticker: string, window = 60): RollingBeta {
  const sym = ticker.toUpperCase();
  const win = Math.max(5, Math.min(252, Math.trunc(window) || 60));
  if (!SYMBOL_UNIVERSE.has(sym)) {
    return {
      symbol: sym,
      window: win,
      series: [],
      reason: "No cached price history for this symbol yet.",
    };
  }
  const rng = seeded([...sym].reduce((a, c) => a + c.charCodeAt(0), 0) + win);
  const meanBeta = 0.5 + rng() * 1.3; // symbol-specific mean, roughly 0.5-1.8
  const days = 220;
  const now = Date.now();
  let beta = meanBeta;
  const series: { date: string; beta: number }[] = [];
  for (let i = days; i >= 0; i--) {
    beta += (rng() - 0.5) * 0.06 + (meanBeta - beta) * 0.08;
    series.push({
      date: new Date(now - i * 86400000).toISOString().slice(0, 10),
      beta: +beta.toFixed(3),
    });
  }
  return { symbol: sym, window: win, series, reason: null };
}

// Rider 13b (Needs Retrain age flag): mirrors gui.help_content.
// MODEL_RETRAIN_WINDOW_DAYS (30) -- the mock has no live Python process to
// import from, so this is the fixture layer's honest snapshot of that
// constant, not an invented number. daysSinceTrained/age_days/needs_retrain
// below mirror pilots/models.py's own server-side computation exactly.
const MODEL_RETRAIN_WINDOW_DAYS = 30;

function daysSinceTrained(trainedDate: string): number {
  const then = new Date(`${trainedDate}T00:00:00Z`).getTime();
  return Math.floor((Date.now() - then) / 86_400_000);
}

// ---- ML registry fixture (honest: two un-validated / not-deployable; one
// stale -- exercises BOTH the fresh and "Needs Retrain" badge states) ----
const MODELS: ModelRow[] = [
  {
    name: "lgbm_ranker",
    role: "cross_sectional_ranker",
    trained_date: "2026-07-06",
    cpcv_dsr: 0.0019,
    pbo: 0.267,
    n_train: 260,
    deployable: false,
    notes: "LightGBM LambdaRank — modest weight until validated at >200 OOS dates.",
    age_days: daysSinceTrained("2026-07-06"),
    needs_retrain: daysSinceTrained("2026-07-06") >= MODEL_RETRAIN_WINDOW_DAYS,
  },
  {
    name: "meta_labeler_timeseries_momentum",
    role: "meta_labeler",
    trained_date: "2026-07-06",
    cpcv_dsr: null,
    pbo: null,
    n_train: 3499,
    deployable: false,
    notes: "Binary classifier predicting P(timeseries_momentum correct).",
    age_days: daysSinceTrained("2026-07-06"),
    needs_retrain: daysSinceTrained("2026-07-06") >= MODEL_RETRAIN_WINDOW_DAYS,
  },
  {
    // Deliberately trained well outside the 30-day window (unlike its two
    // siblings above) so the fixture exercises the "Needs Retrain" badge's
    // TRUE branch, not just the fresh/false one.
    name: "meta_labeler_cross_sectional_momentum",
    role: "meta_labeler",
    trained_date: "2026-05-20",
    cpcv_dsr: null,
    pbo: null,
    n_train: 3460,
    deployable: false,
    notes: "Binary classifier predicting P(cross_sectional_momentum correct).",
    age_days: daysSinceTrained("2026-05-20"),
    needs_retrain: daysSinceTrained("2026-05-20") >= MODEL_RETRAIN_WINDOW_DAYS,
  },
  {
    // A newly-registered model with no training run yet -- trained_date null
    // is a real, valid state (pilots/models.py never fabricates an age/flag
    // for it): age_days/needs_retrain must both be null, not a guessed value.
    name: "cnn_lstm_price_forecaster",
    role: "forecast_overlay",
    trained_date: null,
    cpcv_dsr: null,
    pbo: null,
    n_train: null,
    deployable: false,
    notes: "Registered but not yet trained -- no dated run to compute an age from.",
    age_days: null,
    needs_retrain: null,
  },
];

// ---- Strategy Health (deployability-gate breakdown) fixture ----
// Hand-written to exercise every honesty branch pilots/strategy_health.py can
// produce, not just the clean-pass happy path:
//   - all four gates pass (trend-following) with a run-over-run trend
//   - all four gates pass, no history persisted yet (dip-buyer) -> trend: []
//   - a single failing gate blocks an otherwise-clean strategy (edge-garch:
//     Max Drawdown fails; PBO/DSR/Sharpe all pass)
//   - options-selling: every numeric gate passes but the SEPARATE tail-
//     scenario stress gate fails (premium-harvester) -> still not deployable
//   - a genuinely uncomputed gate value (regime-navigator: max_drawdown is
//     null) -> that gate's `passed` stays null (unknown), never guessed
//   - every gate fails (momentum-burst) -- shown honestly, never softened
//   - no validated backtest at all (balanced-blend: strategy_id null)
//   - a real strategy_id whose summary file hasn't been generated yet
//     (forecast-aligned) -- a DIFFERENT honest reason than "no backtest"
const HEALTH_THRESHOLDS: Record<StrategyHealthGate["key"], number> = {
  pbo: 0.5,
  dsr: 0.95,
  sharpe: 0.5,
  max_drawdown: 0.3,
};

const HEALTH_GATE_LABELS: Record<StrategyHealthGate["key"], string> = {
  pbo: "Probability of Backtest Overfitting",
  dsr: "Deflated Sharpe Ratio",
  sharpe: "Net Sharpe Ratio",
  max_drawdown: "Max Drawdown",
};

const HEALTH_GATE_DIRECTIONS: Record<StrategyHealthGate["key"], "above" | "below"> = {
  pbo: "below",
  dsr: "above",
  sharpe: "above",
  max_drawdown: "below",
};

function healthGate(key: StrategyHealthGate["key"], value: number | null): StrategyHealthGate {
  const threshold = HEALTH_THRESHOLDS[key];
  const direction = HEALTH_GATE_DIRECTIONS[key];
  const passed =
    value == null || Number.isNaN(value)
      ? null
      : direction === "below"
        ? value < threshold
        : value > threshold;
  return { key, label: HEALTH_GATE_LABELS[key], value, threshold, direction, passed };
}

/** Order matches the real backend's PBO/DSR/Sharpe/MaxDD gate ordering. */
function healthGates(
  sharpe: number | null,
  dsr: number | null,
  pbo: number | null,
  maxDrawdown: number | null
): StrategyHealthGate[] {
  return [
    healthGate("pbo", pbo),
    healthGate("dsr", dsr),
    healthGate("sharpe", sharpe),
    healthGate("max_drawdown", maxDrawdown),
  ];
}

function healthTrend(
  points: [string, number, number, number, number, boolean][]
): StrategyHealthTrendPoint[] {
  return points.map(([report_date, pbo, dsr, sharpe, max_drawdown, deployable]) => ({
    report_date,
    pbo,
    dsr,
    sharpe,
    max_drawdown,
    deployable,
  }));
}

const STRATEGY_HEALTH_ROWS: StrategyHealthRow[] = [
  {
    pilot_id: "trend-following",
    pilot_name: "Trend Follower",
    strategy_id: "timeseries_momentum",
    deployable: true,
    gates: healthGates(1.12, 0.972, 0.31, 0.19),
    is_options_selling: false,
    stress_gate_passed: true, // gate does not apply to non-options strategies -> trivially true
    report_date: "2026-07-11",
    trend: healthTrend([
      ["2026-05-04", 0.34, 0.951, 0.94, 0.21, true],
      ["2026-06-01", 0.24, 0.964, 1.03, 0.2, true],
      ["2026-07-06", 0.31, 0.972, 1.12, 0.19, true],
    ]),
    reason: null,
  },
  {
    pilot_id: "dip-buyer",
    pilot_name: "Dip Buyer",
    strategy_id: "rsi2_mean_reversion",
    deployable: true,
    gates: healthGates(0.83, 0.961, 0.38, 0.14),
    is_options_selling: false,
    stress_gate_passed: true,
    report_date: "2026-07-09",
    trend: [], // honest "no run-over-run history persisted yet"
    reason: null,
  },
  {
    pilot_id: "edge-garch",
    pilot_name: "Edge & Volatility",
    strategy_id: "garch_vol_target",
    // PBO/DSR/Sharpe all pass; Max Drawdown alone genuinely fails -> the
    // whole strategy is not deployable. A realistic "one gate blocks it" case.
    deployable: false,
    gates: healthGates(0.62, 0.958, 0.44, 0.34),
    is_options_selling: false,
    stress_gate_passed: true,
    report_date: "2026-07-08",
    trend: [],
    reason: null,
  },
  {
    pilot_id: "premium-harvester",
    pilot_name: "Premium Harvester",
    strategy_id: "short_vol_condor_pit",
    // All FOUR numeric gates pass, but the options-selling tail-scenario
    // stress gate fails (a real Lehman/Volmageddon-style blow-up) -> not
    // deployable despite the clean headline numbers. The stress gate is a
    // SEPARATE, additional requirement for options-selling strategies.
    deployable: false,
    gates: healthGates(1.34, 0.981, 0.11, 0.09),
    is_options_selling: true,
    stress_gate_passed: false,
    report_date: "2026-07-05",
    trend: [],
    reason: null,
  },
  {
    pilot_id: "regime-navigator",
    pilot_name: "Regime Navigator",
    strategy_id: "macro_regime_pit",
    // Max Drawdown was genuinely uncomputable for this run -> that gate's
    // `passed` stays null (unknown, never guessed); the strategy fails closed
    // (not deployable) because of it, same as the real harness's own AND gate.
    deployable: false,
    gates: healthGates(0.58, 0.957, 0.42, null),
    is_options_selling: false,
    stress_gate_passed: true,
    report_date: "2026-07-02",
    trend: [],
    reason: null,
  },
  {
    pilot_id: "momentum-burst",
    pilot_name: "Momentum Burst",
    strategy_id: "momentum_burst_intraday",
    // Every gate genuinely fails -> not deployable, shown honestly, never
    // loosened to force a green badge.
    deployable: false,
    gates: healthGates(0.41, 0.72, 0.63, 0.34),
    is_options_selling: false,
    stress_gate_passed: true,
    report_date: "2026-06-20",
    trend: [],
    reason: null,
  },
  {
    pilot_id: "balanced-blend",
    pilot_name: "Balanced Blend",
    // Ensemble of all 17 signal modules -- no single validated backtest
    // honestly represents it (mirrors pilots/catalog.py's own documented
    // caveat), so there is no strategy_id at all.
    strategy_id: null,
    deployable: null,
    gates: [],
    is_options_selling: null,
    stress_gate_passed: null,
    report_date: null,
    trend: [],
    reason: "no validated backtest for this pilot",
  },
  {
    pilot_id: "forecast-aligned",
    pilot_name: "Forecast Aligned",
    // Has a real validation_strategy_id, but the summary file itself hasn't
    // been generated on this install yet -- a DEAD-LETTER degrade, distinct
    // from "no validated backtest" above (different, honest reason text).
    strategy_id: "forecast_direction_arima_hw",
    deployable: null,
    gates: [],
    is_options_selling: null,
    stress_gate_passed: null,
    report_date: null,
    trend: [],
    reason:
      "no validation summary found for 'forecast_direction_arima_hw' (run the validation pipeline first)",
  },
];

// ---- Validation Trend (cross-strategy snapshot + trend + regime timeline) ----
// Deliberately includes TWO strategies with no pilots.catalog Pilot pointing
// at them (multifactor_lowvol_size, cross_sectional_momentum) -- the exact
// gap GET /strategy/health can never close, since it only iterates catalog
// Pilots. Both are real STRATEGY_REGISTRY names from
// docs/VALIDATION_STRATEGY_FIX_LOG.md's 2026-07 fix pass.
const VALIDATION_TREND_SNAPSHOT: ValidationTrendSnapshot = {
  strategies: [
    {
      strategy_id: "cross_sectional_momentum",
      deployable: true,
      pbo: 0.22,
      dsr: 0.961,
      sharpe: 0.78,
      max_drawdown: 0.18,
      is_options_selling: false,
      stress_gate_passed: true,
      report_date: "2026-07-15",
    },
    {
      strategy_id: "garch_vol_target",
      deployable: false,
      pbo: 0.62,
      dsr: 0.958,
      sharpe: 0.44,
      max_drawdown: 0.34,
      is_options_selling: false,
      stress_gate_passed: true,
      report_date: "2026-07-08",
    },
    {
      strategy_id: "multifactor_lowvol_size",
      // Not deployable yet (DSR just under the 0.95 bar) -- no Pilot has
      // been wired to this strategy_id, so it is INVISIBLE on
      // GET /strategy/health entirely. This is the row that demonstrates
      // this section's whole reason for existing.
      deployable: false,
      pbo: 0.28,
      dsr: 0.93,
      sharpe: 0.61,
      max_drawdown: 0.22,
      is_options_selling: false,
      stress_gate_passed: true,
      report_date: "2026-07-14",
    },
    {
      strategy_id: "short_vol_condor_pit",
      deployable: false,
      pbo: 0.11,
      dsr: 0.981,
      sharpe: 1.34,
      max_drawdown: 0.09,
      is_options_selling: true,
      stress_gate_passed: false,
      report_date: "2026-07-05",
    },
    {
      strategy_id: "timeseries_momentum",
      deployable: true,
      pbo: 0.31,
      dsr: 0.972,
      sharpe: 1.12,
      max_drawdown: 0.19,
      is_options_selling: false,
      stress_gate_passed: true,
      report_date: "2026-07-11",
    },
  ],
  strategies_reason: null,
  trend: {
    timeseries_momentum: [
      { report_date: "2026-05-04", pbo: 0.34, dsr: 0.951, sharpe: 0.94, max_drawdown: 0.21, deployable: true },
      { report_date: "2026-06-01", pbo: 0.24, dsr: 0.964, sharpe: 1.03, max_drawdown: 0.2, deployable: true },
      { report_date: "2026-07-06", pbo: 0.31, dsr: 0.972, sharpe: 1.12, max_drawdown: 0.19, deployable: true },
    ],
    multifactor_lowvol_size: [
      { report_date: "2026-06-10", pbo: 0.41, dsr: 0.89, sharpe: 0.42, max_drawdown: 0.27, deployable: false },
      { report_date: "2026-06-28", pbo: 0.33, dsr: 0.91, sharpe: 0.52, max_drawdown: 0.24, deployable: false },
      { report_date: "2026-07-14", pbo: 0.28, dsr: 0.93, sharpe: 0.61, max_drawdown: 0.22, deployable: false },
    ],
    // garch_vol_target, short_vol_condor_pit, cross_sectional_momentum: only
    // 0-1 recorded runs so far -- honestly omitted, not fabricated
    // (CONSTRAINT #4). Mirrors STRATEGY_HEALTH_ROWS's own
    // dip-buyer/edge-garch "trend: []" precedent.
  },
  trend_reason: null,
  regime_timeline: [
    { timestamp: "2026-05-12T14:00:00+00:00", market_regime: "RISK ON" },
    { timestamp: "2026-06-03T09:30:00+00:00", market_regime: "NEUTRAL" },
    { timestamp: "2026-06-19T11:15:00+00:00", market_regime: "RISK OFF" },
    { timestamp: "2026-07-02T08:00:00+00:00", market_regime: "RISK ON" },
  ],
  n_rotated_snapshots: 47,
  regime_reason: null,
};

// ---- AI Gravity audit + legacy structural Gravity Review Suite fixture ----
// Exercises the interesting honesty branches: a "ready" AI-runner status with
// ONE real Claude/Gemini disagreement (so the disagreement badge + warn health
// band both have something to render), and a legacy-suite log with one
// genuinely failing step (so the screen's fail-closed styling is exercised
// too, not just an all-green happy path).
const GRAVITY_AUDIT_STATUS_MOCK: GravityAuditStatus = {
  ai_audit: {
    status: "ready",
    enabled: true,
    generated_at: "2026-07-20T14:32:07+00:00",
    health: "warn",
    health_caption: "⚠ 1 model disagreement(s); Claude skipped=0 / Gemini skipped=0.",
    total_steps: 8,
    claude_passed: 8,
    claude_failed: 0,
    claude_skipped: 0,
    gemini_passed: 7,
    gemini_failed: 1,
    gemini_skipped: 0,
    disagreements: 1,
    steps: [
      { step_number: 1, step_title: "Data & Schema Integrity", claude: "✅ PASSED", gemini: "✅ PASSED", disagreement: false, score_claude: 92, score_gemini: 90, notes: "" },
      { step_number: 2, step_title: "Strategy & Signal Logic", claude: "✅ PASSED", gemini: "✅ PASSED", disagreement: false, score_claude: 88, score_gemini: 86, notes: "" },
      { step_number: 3, step_title: "Options Pricing Engine", claude: "✅ PASSED", gemini: "❌ FAILED", disagreement: true, score_claude: 85, score_gemini: 61, notes: "gemini flagged a delta-tolerance edge case Claude did not" },
      { step_number: 4, step_title: "Forecasting Engine", claude: "✅ PASSED", gemini: "✅ PASSED", disagreement: false, score_claude: 90, score_gemini: 91, notes: "" },
      { step_number: 5, step_title: "Macro Regime Engine", claude: "✅ PASSED", gemini: "✅ PASSED", disagreement: false, score_claude: 87, score_gemini: 89, notes: "" },
      { step_number: 6, step_title: "Sizing & Risk", claude: "✅ PASSED", gemini: "✅ PASSED", disagreement: false, score_claude: 93, score_gemini: 92, notes: "" },
      { step_number: 7, step_title: "Execution & Kill-Switch", claude: "✅ PASSED", gemini: "✅ PASSED", disagreement: false, score_claude: 95, score_gemini: 94, notes: "" },
      { step_number: 8, step_title: "LLM & Advisory Layer", claude: "✅ PASSED", gemini: "✅ PASSED", disagreement: false, score_claude: 84, score_gemini: 88, notes: "" },
    ],
  },
  legacy_audit: {
    available: true,
    all_passed: false,
    steps: [
      { step: "step_1_pandera_schema", passed: true, status: "PASSED" },
      { step: "step_2_lookahead_perturbation", passed: true, status: "PASSED" },
      { step: "step_3_5_discrepancy_analysis", passed: true, status: "Perfect Alignment" },
      { step: "step_4_signal_registry_health", passed: false, status: "FAILED" },
      { step: "step_7_simulation_impact", passed: true, status: "OK / OK" },
    ],
    reason: null,
  },
};

// ---- Options premium matrix fixture ----
// Hand-written to exercise every honesty branch the screen must handle. The
// previous seeded fixture emitted only clean Put Credit Spreads with
// Integrity_OK=true and a non-zero theta, so it could not surface a single one:
//   - Iron Condor: 4 Legs, no per-leg Delta -> Short_Delta/Long_Delta null
//   - Call Debit Spread: Realizable_Daily_Theta 0.0 is a DEFAULT, not a value
//     (the engine only assigns theta on credit structures); Net_Premium < 0
//   - Covered Call: 1 leg, no long leg -> Long_Strike null
//   - Cash/Wait: Net_Premium 0.0 is a REAL zero (no position, no premium)
//   - Integrity_OK=false + Integrity_Issues (off-grid / delta-tolerance)
//   - error stub: Strategy null, the writer's per-symbol dead-letter row
// ATM_* Greeks are for a hypothetical ATM CALL regardless of Strategy (engine
// invariant) — present on actionable rows, null on Cash/error rows.
const OPTIONS_DIRECTIVES: OptionsDirective[] = [
  {
    Symbol: "AAPL",
    Price: 214.9,
    Stale: false,
    Strategy: "Put Credit Spread",
    Action: "Sell to Open",
    Trend_Bias: "Bullish",
    Sigma_GARCH: 0.243,
    IVR_Proxy: 58.4,
    Aroon_Oscillator: 64.3,
    Coppock_Curve: 11.2,
    Net_Premium: 1.24,
    Realizable_Daily_Theta: 0.031,
    ATM_Delta: 0.512,
    ATM_Gamma: 0.021,
    ATM_Vega: 0.184,
    ATM_Theta_Daily: -0.052,
    Short_Strike: 204.0,
    Long_Strike: 199.0,
    Short_Delta: -0.3,
    Long_Delta: -0.15,
    Legs: [
      { Side: "Short", Type: "Put", Strike: 204.0, Price: 2.68, Delta: -0.3 },
      { Side: "Long", Type: "Put", Strike: 199.0, Price: 1.44, Delta: -0.15 },
    ],
    Integrity_OK: true,
    Integrity_Issues: [],
  },
  {
    // 4 legs, engine omits per-leg Delta -> Short_Delta/Long_Delta null.
    Symbol: "MSFT",
    Price: 431.2,
    Stale: false,
    Strategy: "Iron Condor",
    Action: "Sell to Open",
    Trend_Bias: "Neutral",
    Sigma_GARCH: 0.201,
    IVR_Proxy: 51.7,
    Aroon_Oscillator: -7.1,
    Coppock_Curve: 3.4,
    Net_Premium: 2.06,
    Realizable_Daily_Theta: 0.048,
    ATM_Delta: 0.503,
    ATM_Gamma: 0.011,
    ATM_Vega: 0.221,
    ATM_Theta_Daily: -0.061,
    Short_Strike: 410.0,
    Long_Strike: 405.0,
    Short_Delta: null,
    Long_Delta: null,
    Legs: [
      { Side: "Short", Type: "Put", Strike: 410.0, Price: 3.1 },
      { Side: "Long", Type: "Put", Strike: 405.0, Price: 1.9 },
      { Side: "Short", Type: "Call", Strike: 452.0, Price: 3.4 },
      { Side: "Long", Type: "Call", Strike: 457.0, Price: 2.1 },
    ],
    Integrity_OK: true,
    Integrity_Issues: [],
  },
  {
    // Debit spread: theta is the initializer default 0.0, NOT a measurement.
    // Net_Premium negative = debit. Stale quote. Legs omit Delta.
    Symbol: "NVDA",
    Price: 132.6,
    Stale: true,
    Strategy: "Call Debit Spread",
    Action: "Buy to Open",
    Trend_Bias: "Bullish",
    Sigma_GARCH: 0.462,
    IVR_Proxy: 24.1,
    Aroon_Oscillator: 78.6,
    Coppock_Curve: 22.8,
    Net_Premium: -2.15,
    Realizable_Daily_Theta: 0.0,
    ATM_Delta: 0.537,
    ATM_Gamma: 0.033,
    ATM_Vega: 0.142,
    ATM_Theta_Daily: -0.071,
    Short_Strike: 140.0,
    Long_Strike: 132.5,
    Short_Delta: null,
    Long_Delta: null,
    Legs: [
      { Side: "Long", Type: "Call", Strike: 132.5, Price: 6.4 },
      { Side: "Short", Type: "Call", Strike: 140.0, Price: 4.25 },
    ],
    Integrity_OK: true,
    Integrity_Issues: [],
  },
  {
    // Covered Call: 1 short leg, no long leg -> Long_Strike null. Theta default.
    Symbol: "V",
    Price: 279.8,
    Stale: false,
    Strategy: "Covered Call",
    Action: "Sell to Open",
    Trend_Bias: "Neutral",
    Sigma_GARCH: 0.176,
    IVR_Proxy: 44.2,
    Aroon_Oscillator: 14.3,
    Coppock_Curve: -1.9,
    Net_Premium: 3.05,
    Realizable_Daily_Theta: 0.0,
    ATM_Delta: 0.498,
    ATM_Gamma: 0.014,
    ATM_Vega: 0.163,
    ATM_Theta_Daily: -0.044,
    Short_Strike: 290.0,
    Long_Strike: null,
    Short_Delta: 0.3,
    Long_Delta: null,
    Legs: [{ Side: "Short", Type: "Call", Strike: 290.0, Price: 3.05, Delta: 0.3 }],
    Integrity_OK: true,
    Integrity_Issues: [],
  },
  {
    // Cash/Wait: Net_Premium 0.0 is a REAL zero. No legs, no ATM greeks.
    Symbol: "XOM",
    Price: 118.4,
    Stale: false,
    Strategy: "Cash",
    Action: "Wait",
    Trend_Bias: "Bearish",
    Sigma_GARCH: 0.229,
    IVR_Proxy: 33.5,
    Aroon_Oscillator: -42.9,
    Coppock_Curve: -8.7,
    Net_Premium: 0.0,
    Realizable_Daily_Theta: 0.0,
    ATM_Delta: null,
    ATM_Gamma: null,
    ATM_Vega: null,
    ATM_Theta_Daily: null,
    Short_Strike: null,
    Long_Strike: null,
    Short_Delta: null,
    Long_Delta: null,
    Legs: [],
    Integrity_OK: true,
    Integrity_Issues: [],
  },
  {
    // Failing integrity: off-grid strike + delta out of tolerance.
    Symbol: "KO",
    Price: 62.35,
    Stale: false,
    Strategy: "Put Credit Spread",
    Action: "Sell to Open",
    Trend_Bias: "Bullish",
    Sigma_GARCH: 0.153,
    IVR_Proxy: 61.2,
    Aroon_Oscillator: 35.7,
    Coppock_Curve: 6.1,
    Net_Premium: 0.42,
    Realizable_Daily_Theta: 0.012,
    ATM_Delta: 0.506,
    ATM_Gamma: 0.041,
    ATM_Vega: 0.088,
    ATM_Theta_Daily: -0.019,
    Short_Strike: 59.37,
    Long_Strike: 57.0,
    Short_Delta: -0.41,
    Long_Delta: -0.15,
    Legs: [
      { Side: "Short", Type: "Put", Strike: 59.37, Price: 0.71, Delta: -0.41 },
      { Side: "Long", Type: "Put", Strike: 57.0, Price: 0.29, Delta: -0.15 },
    ],
    Integrity_OK: false,
    Integrity_Issues: [
      "Short leg strike 59.37 is not on the $0.50 grid",
      "Short leg delta -0.41 exceeds tolerance of target -0.30 (±0.05)",
    ],
  },
  {
    // Writer's per-symbol dead-letter row: Strategy null, error captured.
    Symbol: "ZZZ",
    Price: null,
    Stale: false,
    Strategy: null,
    Action: null,
    Trend_Bias: null,
    Sigma_GARCH: null,
    IVR_Proxy: null,
    Aroon_Oscillator: null,
    Coppock_Curve: null,
    Net_Premium: null,
    Realizable_Daily_Theta: null,
    ATM_Delta: null,
    ATM_Gamma: null,
    ATM_Vega: null,
    ATM_Theta_Daily: null,
    Short_Strike: null,
    Long_Strike: null,
    Short_Delta: null,
    Long_Delta: null,
    Legs: [],
    Integrity_OK: false,
    Integrity_Issues: ["insufficient bars to compute directive"],
  },
];

const OPTIONS_BY_SYMBOL: Record<string, OptionsDirective> = Object.fromEntries(
  OPTIONS_DIRECTIVES.map((d) => [d.Symbol, d]),
);

function mockOptionsMatrix(): OptionsMatrix {
  return {
    as_of: new Date(Date.now() - 5_400_000).toISOString(),
    target_dte: 30,
    vix: 15.2,
    market_regime: "RISK ON",
    directives: OPTIONS_DIRECTIVES,
    reason: null,
  };
}

// ---- Pairs radar fixture ----
function mockPairs(): PairsRadar {
  const rows = [
    ["XOM", "CVX"],
    ["V", "JPM"],
    ["MSFT", "AAPL"],
    ["HD", "COST"],
  ].map(([t1, t2]) => {
    const rng = seeded([...t1, ...t2].reduce((a, c) => a + c.charCodeAt(0), 0));
    const z = +((rng() - 0.5) * 6).toFixed(2);
    return {
      ticker1: t1,
      ticker2: t2,
      p_value: +(rng() * 0.05).toFixed(4),
      half_life: +(8 + rng() * 40).toFixed(1),
      z_score: z,
      beta: +(0.5 + rng()).toFixed(3),
      rolling_p: +(rng() * 0.1).toFixed(4),
      position: z > 2 ? -1 : z < -2 ? 1 : 0,
      signal:
        Math.abs(z) > 4
          ? "STOP — |z|>4"
          : Math.abs(z) > 2
            ? z > 0
              ? "ENTER SHORT spread"
              : "ENTER LONG spread"
            : "Flat — no entry (|z|<2)",
    };
  });
  return {
    as_of: new Date(Date.now() - 5_400_000).toISOString(),
    universe: ["XOM", "CVX", "V", "JPM", "MSFT", "AAPL", "HD", "COST"],
    pairs: rows,
    reason: null,
  };
}

// Factor z-scores for a subset of PORTFOLIO's holdings, deliberately NOT
// covering every symbol -- DUK (held) has no entry, exercising the "held
// symbol never scored by the pipeline" honesty branch (unmatched_symbols).
// Plain numbers (not `FactorExposure`'s nullable fields) -- this fixture
// never has a missing factor for a matched symbol.
const ATTRIBUTION_FACTORS: Record<string, Record<keyof FactorExposure, number>> = {
  AAPL: { value_z: -0.3, quality_z: 1.1, lowvol_z: 0.2, size_z: -1.8, multifactor_composite: 0.25 },
  MSFT: { value_z: -0.5, quality_z: 1.3, lowvol_z: 0.3, size_z: -1.9, multifactor_composite: 0.3 },
  NVDA: { value_z: -0.9, quality_z: 0.8, lowvol_z: -1.1, size_z: -1.6, multifactor_composite: 0.15 },
  V: { value_z: 0.4, quality_z: 1.6, lowvol_z: 0.6, size_z: -1.2, multifactor_composite: 0.55 },
  COST: { value_z: -0.2, quality_z: 1.2, lowvol_z: 0.9, size_z: -0.3, multifactor_composite: 0.5 },
};

const ATTRIBUTION_FACTOR_KEYS: (keyof FactorExposure)[] = [
  "value_z", "quality_z", "lowvol_z", "size_z", "multifactor_composite",
];

// Hand-grouped clusters over PORTFOLIO's six holdings: mega-cap tech
// co-moves; the payments/staples pair moves together more loosely; DUK (a
// single utility) is a genuine singleton -- avg_intra_corr null, no pair to
// correlate against.
const ATTRIBUTION_CLUSTER_GROUPS: {
  id: number;
  symbols: string[];
  avg_intra_corr: number | null;
}[] = [
  { id: 1, symbols: ["AAPL", "MSFT", "NVDA"], avg_intra_corr: 0.71 },
  { id: 2, symbols: ["V", "COST"], avg_intra_corr: 0.38 },
  { id: 3, symbols: ["DUK"], avg_intra_corr: null },
];

function mockPortfolioAttribution(): PortfolioAttribution {
  // PORTFOLIO's fixture positions always carry a real market_value; the `?? 0`
  // only satisfies PortfolioPositionView's nullable typing (a real account
  // position can lack a live quote) and is never exercised here.
  const heldValues: Record<string, number> = Object.fromEntries(
    PORTFOLIO.positions.map((p) => [p.symbol, p.market_value ?? 0])
  );
  const heldSymbols = Object.keys(heldValues);
  const totalValue = Object.values(heldValues).reduce((a, b) => a + b, 0);

  const matched = heldSymbols.filter((s) => s in ATTRIBUTION_FACTORS).sort();
  const unmatched = heldSymbols.filter((s) => !(s in ATTRIBUTION_FACTORS)).sort();
  const matchedValue = matched.reduce((a, s) => a + heldValues[s], 0);

  const exposures = Object.fromEntries(
    ATTRIBUTION_FACTOR_KEYS.map((k) => {
      if (matchedValue <= 0) return [k, null];
      const sum = matched.reduce(
        (a, s) => a + ATTRIBUTION_FACTORS[s][k] * heldValues[s],
        0
      );
      return [k, sum / matchedValue];
    })
  ) as unknown as FactorExposure;

  const asOf = new Date(Date.now() - 5_400_000).toISOString();

  const clusters: CorrelationCluster[] = ATTRIBUTION_CLUSTER_GROUPS
    .map((g) => {
      const symbolsHeld = g.symbols.filter((s) => heldSymbols.includes(s));
      const clusterValue = symbolsHeld.reduce((a, s) => a + (heldValues[s] ?? 0), 0);
      return {
        cluster_id: g.id,
        symbols: [...symbolsHeld].sort(),
        n_symbols: symbolsHeld.length,
        avg_intra_corr: g.avg_intra_corr,
        weight_pct: totalValue > 0 ? clusterValue / totalValue : null,
        insufficient_history: false,
      };
    })
    .filter((c) => c.n_symbols > 0)
    .sort((a, b) => (b.weight_pct ?? 0) - (a.weight_pct ?? 0));

  return {
    as_of: asOf,
    factor_exposure: {
      as_of: asOf,
      exposures,
      coverage: {
        held_count: heldSymbols.length,
        matched_count: matched.length,
        matched_value_pct: totalValue > 0 ? matchedValue / totalValue : null,
        unmatched_symbols: unmatched,
      },
      reason: null,
    },
    correlation_clusters: {
      clusters,
      lookback_days: 60,
      reason: null,
    },
  };
}

// ---- Manual-input Brinson-Fachler calculator (mock mirrors the real math,
// not a canned fixture -- this is a genuine client-editable calculator, so
// mock/live parity means the ARITHMETIC matches, not just the shape).
// Reimplements evaluation_engine.py::_calculate_brinson_fachler_compat and
// pilots/brinson.py::validate_brinson_fachler_rows in TS. Keep in sync with
// those two if either changes.

function round6(n: number): number {
  return Math.round(n * 1e6) / 1e6;
}

function mockValidateBrinsonFachlerRows(rows: BrinsonFachlerRow[]): string[] {
  const warnings: string[] = [];
  const validRows = rows.filter((r) => r.sector.trim() !== "");
  if (validRows.length === 0) return ["No rows with a non-blank sector name."];

  const pSum = validRows.reduce((a, r) => a + (r.portfolio_weight_pct || 0), 0);
  const bSum = validRows.reduce((a, r) => a + (r.benchmark_weight_pct || 0), 0);

  if (Math.abs(pSum - 100) > 1) {
    warnings.push(`Portfolio weights sum to ${pSum.toFixed(2)}% (expected ~100%).`);
  }
  if (Math.abs(bSum - 100) > 1) {
    warnings.push(`Benchmark weights sum to ${bSum.toFixed(2)}% (expected ~100%).`);
  }
  if (validRows.some((r) => (r.portfolio_weight_pct || 0) < 0)) {
    warnings.push(
      "Negative values found in Portfolio Weight — long-only attribution typically requires non-negative weights."
    );
  }
  if (validRows.some((r) => (r.benchmark_weight_pct || 0) < 0)) {
    warnings.push(
      "Negative values found in Benchmark Weight — long-only attribution typically requires non-negative weights."
    );
  }
  if (pSum === 0 && bSum === 0) {
    warnings.push("All weights are zero — nothing to attribute.");
  }
  return warnings;
}

function mockComputeBrinsonFachler(rows: BrinsonFachlerRow[]): BrinsonFachlerResult {
  const validRows = rows.filter((r) => r.sector.trim() !== "");
  if (validRows.length === 0) {
    throw new ApiError("No rows with a non-blank sector name.", 422);
  }

  let rP = 0;
  let rB = 0;
  const sectorDetails: Record<string, BrinsonFachlerSectorDetail> = {};
  const perSector = validRows.map((row) => {
    const wP = (row.portfolio_weight_pct || 0) / 100;
    const retP = (row.portfolio_return_pct || 0) / 100;
    const wB = (row.benchmark_weight_pct || 0) / 100;
    const retB = (row.benchmark_return_pct || 0) / 100;
    rP += wP * retP;
    rB += wB * retB;
    return { sector: row.sector, wP, retP, wB, retB };
  });

  let totalAlloc = 0;
  let totalSelect = 0;
  let totalInter = 0;
  for (const s of perSector) {
    const allocationEffect = (s.wP - s.wB) * (s.retB - rB);
    const selectionEffect = s.wB * (s.retP - s.retB);
    const interactionEffect = (s.wP - s.wB) * (s.retP - s.retB);
    totalAlloc += allocationEffect;
    totalSelect += selectionEffect;
    totalInter += interactionEffect;
    sectorDetails[s.sector] = {
      weight_p: round6(s.wP),
      weight_b: round6(s.wB),
      return_p: round6(s.retP),
      return_b: round6(s.retB),
      allocation_effect: round6(allocationEffect),
      selection_effect: round6(selectionEffect),
      interaction_effect: round6(interactionEffect),
      total_attribution: round6(allocationEffect + selectionEffect + interactionEffect),
    };
  }

  return {
    "Portfolio Return": rP,
    "Benchmark Return": rB,
    "Active Return": rP - rB,
    "Allocation Effect": totalAlloc,
    "Selection Effect": totalSelect,
    "Interaction Effect": totalInter,
    "Attribution Sum": totalAlloc + totalSelect + totalInter,
    "Sector Details": sectorDetails,
    validation_warnings: mockValidateBrinsonFachlerRows(rows),
  };
}

// ---- Observability / Mission Control fixture ----
// Portfolio-level risk stats: a healthy, plausible track record (not
// deployable-badge territory — this is account risk, not a strategy gate).
function mockPortfolioRisk(): PortfolioRiskMetrics {
  return {
    sharpe_ratio: 1.18,
    calmar_ratio: 2.4,
    max_drawdown: -0.146,
    max_drawdown_duration_days: 34,
    cagr: 0.187,
    n_snapshots: 87,
    min_snapshots_required: 20,
    reason: null,
  };
}

// Drawdown is derived FROM the same synthesized equity series (running-peak
// math), not an independent random series — keeps the fixture internally
// consistent the way the real endpoint's numbers are.
function mockEquityDrawdownCurve(range: PerfRange): EquityDrawdownCurve {
  const raw = synthCurve("account-equity-drawdown", range, 0.12, 0.09, 44000);
  let peak = -Infinity;
  const points: EquityDrawdownPoint[] = raw.map((p) => {
    peak = Math.max(peak, p.value);
    const drawdown = peak > 0 ? (p.value - peak) / peak : 0;
    return { date: p.date, equity: p.value, drawdown: +drawdown.toFixed(4) };
  });
  return { range, points, reason: null };
}

function mockRegimeOverlay(): RegimeOverlay {
  // kill_switch_active reflects the SAME mock kill-switch state Settings'
  // pause/resume automation controls (readKillSwitch/writeKillSwitch, shared
  // with getAutomationStatus) rather than a hardcoded false — so pausing
  // automation actually flips the "Kill switch ACTIVE" badge here too,
  // making that honesty branch reachable in a live mock session, not just
  // via a test-only override.
  const ks = readKillSwitch();
  return {
    as_of: new Date(Date.now() - 5 * 60_000).toISOString(),
    market_regime: "RISK ON",
    vix: 14.8,
    sahm_rule: 0.13,
    high_yield_oas: 3.21,
    yield_curve: 0.42,
    hmm_risk_on_probability: 0.78,
    kill_switch_active: ks.active,
    macro_regime_gate_enabled: readMacroGateEnabled(),
    reason: null,
    // Always writable in the mock (matches mockLlmStatus's convention above)
    // so the demo can exercise the write flow with zero config.
    macro_gate_writable: true,
    macro_gate_writable_note:
      "Writes persist to .env and apply on the next daemon/pipeline launch.",
  };
}

function mockPortfolioForecastSkill(horizon: number): PortfolioForecastSkill {
  const rng = seeded(horizon * 7919 + 13);
  const models = ["arima", "monte_carlo", "holt_winters", "cnn_lstm"];
  const curve = models.flatMap((m) =>
    [-0.3, -0.1, 0.1, 0.3].map((center) => ({
      model_name: m,
      horizon_days: horizon,
      bin_center: center,
      // Some bins honestly null (too few samples in that bucket) — matches
      // the per-symbol mockForecast's same convention.
      mean_pct_error: rng() < 0.15 ? null : +((rng() - 0.5) * 0.1).toFixed(4),
      count: Math.floor(rng() * 40) + 5,
    }))
  );
  const raw = models.map(() => 0.1 + rng());
  const tot = raw.reduce((a, b) => a + b, 0);
  const skill_weights: Record<string, number> = {};
  models.forEach((m, i) => (skill_weights[m] = +(raw[i] / tot).toFixed(3)));
  return {
    horizon_days: horizon,
    window_days: 180,
    min_obs: 30,
    reliability_curve: curve,
    skill_weights,
    pending: Math.floor(rng() * 12) + 2,
    completed: Math.floor(rng() * 300) + 120,
    reason: null,
  };
}

function mockRiskGateBlocks(): RiskGateBlockLog {
  const now = Date.now();
  const entries: RiskGateBlockEntry[] = [
    {
      ts: new Date(now - 40 * 60_000).toISOString(),
      check: "max_correlation",
      reason: "Correlation with the existing NVDA position (0.86) exceeds the 0.80 threshold.",
      symbol: "AMD",
      side: "buy",
      qty: 12,
      strategy_id: "cross-sectional-momentum",
    },
    {
      ts: new Date(now - 6 * 3600_000).toISOString(),
      check: "portfolio_heat",
      reason: "Adding this position would raise portfolio heat to 6.4%, above the 5% cap.",
      symbol: "TSLA",
      side: "buy",
      qty: 5,
      strategy_id: "trend-following",
    },
  ];
  return { entries, count: entries.length, reason: null };
}

// Comfortably under the 6% default MAX_PORTFOLIO_HEAT ceiling — a healthy
// steady-state reading, not the alarming edge case (see over_limit tests for
// that branch).
function mockPortfolioHeat(): PortfolioHeatMetric {
  const maxHeat = 0.06;
  const heatPct = 0.021;
  return {
    heat_pct: heatPct,
    max_portfolio_heat: maxHeat,
    over_limit: heatPct > maxHeat,
    n_positions: 4,
    as_of: new Date(Date.now() - 20 * 60_000).toISOString(),
    reason: null,
  };
}

// Deliberately mixes a CRITICAL and two WARNING trips (rather than an
// all-clear fixture) so the severity chips/KPI strip exercise both colors by
// default, matching mockRiskGateBlocks' own AMD/TSLA scenario above (same
// checks, same symbols) -- these are the deduped/classified projection of
// that same underlying block log, not independent data. The third trip
// deliberately has threshold/observed/triggered_at ALL null -- a real,
// legitimate shape (e.g. gui/circuit_breakers.py's max_position_size check
// carries no threshold field, and a kill-switch sentinel with no readable
// mtime carries no triggered_at) -- so mock mode actually demonstrates the
// null-guard rendering path, not just the fully-populated one.
function mockCircuitBreakers(): CircuitBreakerSummary {
  const now = Date.now();
  const trips: CircuitBreakerTrip[] = [
    {
      name: "portfolio_heat",
      severity: "CRITICAL",
      summary: "Portfolio heat exceeded 5%",
      triggered_at: new Date(now - 6 * 3600_000).toISOString(),
      threshold: 0.05,
      observed: 0.064,
    },
    {
      name: "max_correlation",
      severity: "WARNING",
      summary: "Correlation cap blocked AMD",
      triggered_at: new Date(now - 40 * 60_000).toISOString(),
      threshold: 0.8,
      observed: 0.86,
    },
    {
      name: "max_position_size",
      severity: "WARNING",
      summary: "Position size limit blocked NVDA",
      triggered_at: null,
      threshold: null,
      observed: null,
    },
  ];
  return {
    trips,
    counts: { critical: 1, warning: 2, total: 3 },
    window_hours: 24,
    reason: null,
  };
}

// A healthy-but-not-idle host: comfortably below the 75%/90% CPU and 90%
// memory warning thresholds Observability.tsx's legacy-panel-derived
// annotations key off of, but not an all-zero fixture either -- exercises
// the normal rendering path. Unlike mockCircuitBreakers' one-null-trip-among-
// several mixing above, the "psutil unavailable" honesty branch is a
// WHOLE-OBJECT degrade (pilots/observability.py::_empty_system_telemetry --
// psutil_available:false, every metric null), so it can't be folded into
// this happy-path default without blanking the tiles the "renders system
// telemetry tiles from the mock" test below asserts on. See
// mockSystemTelemetryUnavailable() immediately below for the canonical
// mock-owned copy of that shape.
function mockSystemTelemetry(): SystemTelemetry {
  return {
    psutil_available: true,
    cpu_percent: 18.4,
    cpu_count_logical: 10,
    load_avg_1m: 2.3,
    memory_percent: 61.2,
    memory_used_bytes: 10_500_000_000,
    memory_total_bytes: 17_179_869_184,
    disk_percent: 42.7,
    disk_used_bytes: 211_000_000_000,
    disk_total_bytes: 494_353_338_368,
    process_rss_bytes: 182_000_000,
    process_cpu_percent: 3.1,
    process_threads: 6,
    sampled_at: new Date().toISOString(),
    reason: null,
  };
}

// The honest "psutil unavailable" degrade -- an exact mirror of
// pilots/observability.py::_empty_system_telemetry's shape (every metric
// null, psutil_available:false, reason set). Exported (matching the
// __resetMockDataUniverse convention above) for two consumers: (1)
// mockObservabilitySummary below, gated behind the
// stockpy.mock.observability_cold_start devtools toggle (see
// readObservabilityColdStart above) so this branch is reachable by actually
// running the app, not only through tests; (2) Observability.test.tsx's
// cold-start case, so that test is pinned to this canonical, mock-owned copy
// instead of a hand-rolled object that could silently drift from the real
// backend's shape -- closing the gap where PR #427 added mockSystemTelemetry
// with this honest branch reachable only via a test-only hand-built object.
export function mockSystemTelemetryUnavailable(
  reason = "psutil is not available in this environment."
): SystemTelemetry {
  return {
    psutil_available: false,
    cpu_percent: null,
    cpu_count_logical: null,
    load_avg_1m: null,
    memory_percent: null,
    memory_used_bytes: null,
    memory_total_bytes: null,
    disk_percent: null,
    disk_used_bytes: null,
    disk_total_bytes: null,
    process_rss_bytes: null,
    process_cpu_percent: null,
    process_threads: null,
    sampled_at: null,
    reason,
  };
}

// ---- Sizing Cap-Event Audit Trail (G7) ----
// A mix of capped and uncapped events, plus one with no strategy_id (the
// global-aggregate sizing path) -- exercises the "not every event is a
// cap event" and "strategy_id can be null" rendering paths, not just a
// wall-to-wall capped happy path.
function mockSizingCapEvents(): SizingCapEvent[] {
  const now = Date.now();
  return [
    {
      id: 3, timestamp: new Date(now - 30 * 60_000).toISOString(), cycle_id: "cycle-118",
      symbol: "NVDA", strategy_id: "timeseries_momentum", raw_weight: 0.32, final_weight: 0.20,
      binding_constraint: "kelly_cap", was_capped: true,
    },
    {
      id: 2, timestamp: new Date(now - 90 * 60_000).toISOString(), cycle_id: "cycle-117",
      symbol: "TSLA", strategy_id: null, raw_weight: 0.28, final_weight: 0.28,
      binding_constraint: null, was_capped: false,
    },
    {
      id: 1, timestamp: new Date(now - 150 * 60_000).toISOString(), cycle_id: "cycle-116",
      symbol: "SPY", strategy_id: "multifactor_lowvol_size", raw_weight: 4.10, final_weight: 3.0,
      binding_constraint: "portfolio_gross", was_capped: true,
    },
  ];
}

function mockSizingCapAuditTrail(): SizingCapAuditTrail {
  const events = mockSizingCapEvents();
  return {
    events,
    count: events.length,
    capped_count: events.filter((e) => e.was_capped).length,
    audit_enabled: true,
    escalation_enabled: true,
    escalation_threshold_cycles: 5,
    escalation_factor: 0.5,
    reason: null,
  };
}

// The honest "audit disabled" degrade -- exported (matching the
// mockSystemTelemetryUnavailable convention above) so both mock-mode devtools
// toggling and the co-located screen test are pinned to the same
// canonical shape.
export function mockSizingCapAuditDisabled(): SizingCapAuditTrail {
  return {
    events: [],
    count: 0,
    capped_count: 0,
    audit_enabled: false,
    escalation_enabled: false,
    escalation_threshold_cycles: 5,
    escalation_factor: 0.5,
    reason: "SIZING_CAP_AUDIT_ENABLED is False -- the durable cap-event log is not being written this run.",
  };
}

// ---- ETF Volatility Transmission (G7) ----
function mockEtfTransmissionSummary(): EtfTransmissionSummary {
  return {
    rows: [
      { symbol: "SPY", etf_ownership_pct: 1.0, etf_comovement_r2: 1.0, etf_primary_wrapper: "SPY", etf_transmission_multiplier: null },
      { symbol: "NVDA", etf_ownership_pct: 0.42, etf_comovement_r2: 0.81, etf_primary_wrapper: "QQQ", etf_transmission_multiplier: 0.74 },
      { symbol: "JPM", etf_ownership_pct: 0.18, etf_comovement_r2: 0.55, etf_primary_wrapper: "XLF", etf_transmission_multiplier: 0.94 },
    ],
    measurement_enabled: true,
    sizing_enabled: true,
    portfolio_enabled: false,
    reason: null,
  };
}

// The honest "measurement disabled" degrade -- exported for the same reason
// as mockSizingCapAuditDisabled above.
export function mockEtfTransmissionDisabled(): EtfTransmissionSummary {
  return {
    rows: [],
    measurement_enabled: false,
    sizing_enabled: false,
    portfolio_enabled: false,
    reason: "ETF_TRANSMISSION_ENABLED is False -- measurement columns are not computed this cycle.",
  };
}

// ---- Heartbeat Age (G7) ----
// A "Fresh" (<60s) sample by default so mock mode exercises the normal
// rendering path; mockHeartbeatNoData below is the honest cold-start degrade.
function mockHeartbeatSummary(): HeartbeatSummary {
  return {
    age_seconds: 24.0,
    status: "🟢 Fresh",
    history_available: false,
    history_note:
      "The legacy Streamlit \"Heartbeat Age Trend\" sparkline is a 60-sample ring buffer held only in st.session_state -- never persisted to disk -- so there is no durable history for this endpoint to serve honestly. Only the current sample is real.",
    reason: null,
  };
}

// The honest "no heartbeat file yet" degrade -- exported for the same reason
// as mockSizingCapAuditDisabled above.
export function mockHeartbeatNoData(): HeartbeatSummary {
  return {
    age_seconds: null,
    status: "⚪ No heartbeat",
    history_available: false,
    history_note:
      "The legacy Streamlit \"Heartbeat Age Trend\" sparkline is a 60-sample ring buffer held only in st.session_state -- never persisted to disk -- so there is no durable history for this endpoint to serve honestly. Only the current sample is real.",
    reason: "No heartbeat file yet -- output/heartbeat.txt is written only by main_orchestrator.py's async heartbeat task.",
  };
}

// ---- Strategy P&L (G7) ----
// One tagged-strategy row plus one strategy_id:null row (untagged trades) --
// exercises the "real money grouped under a null bucket" honesty path, not
// just an all-tagged happy path.
function mockStrategyPnlSummary(): StrategyPnlSummary {
  return {
    rows: [
      { strategy_id: "timeseries_momentum", realized_pnl: 842.15, trade_count: 11 },
      { strategy_id: "cross_sectional_momentum", realized_pnl: 213.40, trade_count: 4 },
      { strategy_id: null, realized_pnl: -58.20, trade_count: 2 },
    ],
    total_realized_pnl: 997.35,
    reason: null,
  };
}

// The honest "no closed trades yet" degrade -- exported for the same reason
// as mockSizingCapAuditDisabled above.
export function mockStrategyPnlEmpty(): StrategyPnlSummary {
  return { rows: [], total_realized_pnl: null, reason: "No closed trades in the transactions store yet." };
}

function mockObservabilitySummary(range: PerfRange, horizon: number): ObservabilitySummary {
  return {
    portfolio_risk: mockPortfolioRisk(),
    portfolio_heat: mockPortfolioHeat(),
    equity_curve: mockEquityDrawdownCurve(range),
    regime: mockRegimeOverlay(),
    forecast_skill: mockPortfolioForecastSkill(horizon),
    risk_gate_blocks: mockRiskGateBlocks(),
    circuit_breakers: mockCircuitBreakers(),
    system_telemetry: readObservabilityColdStart()
      ? mockSystemTelemetryUnavailable()
      : mockSystemTelemetry(),
    sizing_cap_audit: readObservabilityColdStart()
      ? mockSizingCapAuditDisabled()
      : mockSizingCapAuditTrail(),
    etf_transmission: readObservabilityColdStart()
      ? mockEtfTransmissionDisabled()
      : mockEtfTransmissionSummary(),
    heartbeat: readObservabilityColdStart() ? mockHeartbeatNoData() : mockHeartbeatSummary(),
    strategy_pnl: readObservabilityColdStart() ? mockStrategyPnlEmpty() : mockStrategyPnlSummary(),
  };
}

// GET /observability/logs fixture -- deliberately mixes levels (INFO through
// CRITICAL) plus one unparseable traceback-continuation line, so mock mode
// exercises the tally KPI strip, the systemic/symbol-specific counts, AND
// the "kept but unparsed" rendering path, not just an all-INFO happy path.
function mockObservabilityLogs(limit: number): LogAggregation {
  const now = Date.now();
  const iso = (minsAgo: number) => new Date(now - minsAgo * 60_000).toISOString();
  const all: LogAggregationEntry[] = [
    {
      timestamp: iso(58),
      level: "INFO",
      logger_name: "main_orchestrator",
      message: "Cycle started (universe=42 symbols)",
      raw: `${iso(58)}  INFO      main_orchestrator — Cycle started (universe=42 symbols)`,
      parsed: true,
    },
    {
      timestamp: iso(52),
      level: "WARNING",
      logger_name: "data_engine",
      message: "Dead-lettered HKIT at stage=strategy: insufficient bars",
      raw: `${iso(52)}  WARNING   data_engine — Dead-lettered HKIT at stage=strategy: insufficient bars`,
      parsed: true,
    },
    {
      timestamp: iso(41),
      level: "ERROR",
      logger_name: "strategy_engine",
      message: "for symbol NVDA: model missing, skipping",
      raw: `${iso(41)}  ERROR     strategy_engine — for symbol NVDA: model missing, skipping`,
      parsed: true,
    },
    {
      timestamp: iso(40),
      level: null,
      logger_name: null,
      message: '  File "strategy_engine.py", line 214, in evaluate_security',
      raw: '  File "strategy_engine.py", line 214, in evaluate_security',
      parsed: false,
    },
    {
      timestamp: iso(12),
      level: "CRITICAL",
      logger_name: "macro_engine",
      message: "FRED unavailable, macro fetch aborted",
      raw: `${iso(12)}  CRITICAL  macro_engine — FRED unavailable, macro fetch aborted`,
      parsed: true,
    },
    {
      timestamp: iso(2),
      level: "INFO",
      logger_name: "main_orchestrator",
      message: "Cycle finished in 38.2s",
      raw: `${iso(2)}  INFO      main_orchestrator — Cycle finished in 38.2s`,
      parsed: true,
    },
  ];
  const entries = all.slice(-limit);
  return {
    log_path: "logs/investyo.log",
    total_lines: all.length,
    tally: { CRITICAL: 1, ERROR: 1, WARNING: 1, INFO: 2, DEBUG: 0, UNPARSED: 1 },
    systemic_count: 1,
    symbol_specific_count: 2,
    entries,
    returned_count: entries.length,
    reason: null,
  };
}

// The honest "no log file yet" degrade -- an exact mirror of
// pilots/observability.py::_empty_log_aggregation's shape (zeroed tally,
// empty entries, reason set). Unlike mockObservabilityLogs' own null-guard
// demonstration above (one unparsed traceback line mixed into an otherwise
// populated tail -- a per-ENTRY null-guard), this is a WHOLE-RESPONSE degrade
// (no log file has been written yet at all), so it can't be folded into the
// happy-path default without emptying the list the "renders the log
// aggregation KPI strip and entries from the mock" test asserts on. Exported
// (matching the __resetMockDataUniverse convention above) for two consumers:
// (1) the getObservabilityLogs mock API method below, gated behind the
// stockpy.mock.observability_cold_start devtools toggle (see
// readObservabilityColdStart above) so this branch is reachable by actually
// running the app, not only through tests; (2) Observability.test.tsx's
// empty-log-tail case, so that test is pinned to this canonical, mock-owned
// copy instead of a hand-rolled object that could silently drift from the
// real backend's shape -- closing the gap where PR #427 added
// mockObservabilityLogs with this honest branch reachable only via a
// test-only hand-built object.
export function mockEmptyLogAggregation(
  reason: string,
  logPath: string | null = "logs/investyo.log"
): LogAggregation {
  return {
    log_path: logPath,
    total_lines: 0,
    tally: { CRITICAL: 0, ERROR: 0, WARNING: 0, INFO: 0, DEBUG: 0, UNPARSED: 0 },
    systemic_count: 0,
    symbol_specific_count: 0,
    entries: [],
    returned_count: 0,
    reason,
  };
}

// ---- Control API (orchestrator daemon) fixture ----
// An IDLE daemon (is_running:false, current_run_id:null) with a populated,
// most-recent-first run history. Hand-written to exercise the Pipeline
// Dashboard's honesty branches, not just a clean happy path:
//   - varied `mode` (full / data / metrics) rendered as distinct badges
//   - a FAILED run carrying a real `error` string (never softened)
//   - a record with NO `mode` (an interval run predating the param) -> the
//     screen renders "—", never a fabricated "FULL"
//   - terminal records carry finished_at + duration; a null duration only ever
//     appears on a non-terminal (running/queued) record — see the running
//     fixture the test injects, never fabricated here
function controlRun(
  run_id: string,
  state: RunRecord["state"],
  mode: RunRecord["mode"],
  minsAgo: number,
  durationSeconds: number | null,
  reason: string,
  error: string | null
): RunRecord {
  const now = Date.now();
  const started = now - minsAgo * 60_000;
  const terminal = state === "succeeded" || state === "failed";
  return {
    run_id,
    state,
    mode,
    started_at: new Date(started).toISOString(),
    finished_at:
      terminal && durationSeconds != null
        ? new Date(started + durationSeconds * 1000).toISOString()
        : null,
    duration_seconds: terminal ? durationSeconds : null,
    error,
    reason,
    progress: null,
  };
}

const CONTROL_RUN_HISTORY: RunRecord[] = [
  controlRun("orch-mock-5f2a", "succeeded", "full", 5, 41.8, "manual", null),
  controlRun("orch-mock-5e19", "succeeded", "data", 62, 12.4, "manual", null),
  controlRun(
    "orch-mock-5d07",
    "failed",
    "metrics",
    128,
    6.1,
    "manual",
    "ForecastingEngine: insufficient bars for NVDA (need >=22, got 9)"
  ),
  // An interval-triggered run with no `mode` recorded -> honest "—" in the UI.
  controlRun("orch-mock-5c88", "succeeded", undefined, 305, 44.2, "interval", null),
];

// GET /runs/history's durable fixture -- deliberately LONGER than
// CONTROL_RUN_HISTORY (the in-memory 10-run ring GET /status returns) to
// demonstrate the whole point of the durable table: history that outlives a
// daemon restart, not just "the same 4 runs again." Only terminal runs ever
// land here (see RunHistoryEntry's doc comment in types.ts) -- no "running"
// entries, unlike CONTROL_RUN_HISTORY which a test injects one into directly.
const RUN_HISTORY_DURABLE: RunRecord[] = [
  ...CONTROL_RUN_HISTORY,
  controlRun("orch-mock-5b41", "succeeded", "full", 365, 39.7, "interval", null),
  controlRun("orch-mock-5a02", "succeeded", "data", 425, 11.9, "interval", null),
  controlRun(
    "orch-mock-4f93",
    "failed",
    "full",
    488,
    22.3,
    "manual",
    "DataEngine: Robinhood login failed after 3 retries (session expired)"
  ),
  controlRun("orch-mock-4e6c", "succeeded", "metrics", 550, 9.4, "interval", null),
  controlRun("orch-mock-4d21", "succeeded", "full", 612, 43.1, "interval", null),
  controlRun("orch-mock-4c05", "succeeded", "data", 675, 13.2, "interval", null),
];

function mockControlStatus(): ControlStatus {
  return {
    daemon_alive: true,
    is_running: false,
    current_run_id: null,
    interval_seconds: 300,
    engines_warm: true,
    started_at: new Date(Date.now() - 6 * 3600_000).toISOString(),
    last_run: CONTROL_RUN_HISTORY[0],
    run_history: CONTROL_RUN_HISTORY,
    kill_switch_active: false,
    kill_switch_reason: null,
    advisory_only: true,
    dry_run: false,
  };
}

async function delay<T>(v: T, ms = 260): Promise<T> {
  return new Promise((res) => setTimeout(() => res(v), ms));
}

// In-memory decision journal -- logDecision pushes into it, getDecisions
// reads from it, so a logged decision is genuinely visible on re-fetch within
// the mock session (not persisted across a page reload -- matches this
// module's other ephemeral, non-localStorage mock state).
const MOCK_DECISION_LOG: DecisionEntry[] = [
  {
    symbol: "AAPL",
    action_taken: "acted",
    signal_action: "BUY",
    conviction: 0.72,
    notes: "Sized to half -- position already large.",
    timestamp: new Date(Date.now() - 3 * 86_400_000).toISOString(),
    signal_ts: new Date(Date.now() - 3 * 86_400_000).toISOString(),
    trade_id: 42,
  },
];

/**
 * Honest fixture for the CLI command manifest (GET /commands). Deliberately
 * exercises every branch the command bar must handle: a required option
 * (`validation.harness --strategy`), a variadic option (`preflight --skip`), a
 * flag with no value (`--json`), an option with `choices` (`snapshot_diff
 * --format`), a `null` description, and a subcommand command with an alias and
 * a required positional (`prompt_registry get <id>`). Mirrors the real shape
 * emitted by scripts/build_command_manifest.py.
 */
const MOCK_COMMAND_MANIFEST: CommandManifest = {
  generated_at: "2026-07-17T12:00:00+00:00",
  command_count: 5,
  dead_letters: [],
  reason: null,
  commands: [
    {
      name: "main.py",
      invocation: "python3 main.py",
      aliases: [],
      description: "Clean advisory orchestrator — one full cycle (or loop with --interval).",
      positionals: [],
      subcommands: [],
      options: [
        { name: "--interval", aliases: ["--interval"], description: "refresh cadence in seconds (0 = run once)", default: 0, choices: null, required: false, arg_kind: "optional", metavar: "SECONDS", takes_value: true },
        { name: "--refresh-account", aliases: ["--refresh-account"], description: "force a fresh Robinhood login this run", default: false, choices: null, required: false, arg_kind: "optional", metavar: null, takes_value: false },
        { name: "--agent", aliases: ["--agent"], description: null, default: false, choices: null, required: false, arg_kind: "optional", metavar: null, takes_value: false },
      ],
    },
    {
      name: "validation.harness",
      invocation: "python -m validation.harness",
      aliases: [],
      description: "Run the strategy validation harness (PBO/DSR/Sharpe/MaxDD gates).",
      positionals: [],
      subcommands: [],
      options: [
        { name: "--strategy", aliases: ["--strategy"], description: "registered strategy name", default: null, choices: null, required: true, arg_kind: "required", metavar: null, takes_value: true },
        { name: "--start", aliases: ["--start"], description: "backtest start date", default: "2020-01-01", choices: null, required: false, arg_kind: "optional", metavar: null, takes_value: true },
        { name: "--end", aliases: ["--end"], description: "backtest end date", default: "2023-12-31", choices: null, required: false, arg_kind: "optional", metavar: null, takes_value: true },
      ],
    },
    {
      name: "preflight_check.py",
      invocation: "python scripts/preflight_check.py",
      aliases: [],
      description: "Pre-live readiness gate (exit 0 = all pass).",
      positionals: [],
      subcommands: [],
      options: [
        { name: "--json", aliases: ["--json"], description: "machine-readable JSON output", default: false, choices: null, required: false, arg_kind: "optional", metavar: null, takes_value: false },
        { name: "--skip", aliases: ["--skip"], description: "checks to skip", default: null, choices: null, required: false, arg_kind: "variadic", metavar: "CHECK", takes_value: true },
        { name: "--fire-alerts", aliases: ["--fire-alerts"], description: "send alerts on failure", default: false, choices: null, required: false, arg_kind: "optional", metavar: null, takes_value: false },
      ],
    },
    {
      name: "snapshot_diff.py",
      invocation: "python scripts/snapshot_diff.py",
      aliases: [],
      description: "Diff two state snapshots.",
      positionals: [
        { name: "prev", description: "earlier snapshot", default: null, choices: null, arg_kind: "optional", metavar: null },
        { name: "curr", description: "later snapshot", default: null, choices: null, arg_kind: "optional", metavar: null },
      ],
      subcommands: [],
      options: [
        { name: "--format", aliases: ["--format"], description: "output format", default: "markdown", choices: ["markdown", "json"], required: false, arg_kind: "optional", metavar: null, takes_value: true },
      ],
    },
    {
      name: "prompt_registry",
      invocation: "python -m prompt_registry",
      aliases: [],
      description: "Manage the LLM prompt registry.",
      positionals: [],
      options: [],
      subcommands: [
        {
          name: "get",
          invocation: "python -m prompt_registry get",
          aliases: ["g"],
          description: "fetch one prompt",
          positionals: [
            { name: "id", description: "prompt id", default: null, choices: null, arg_kind: "required", metavar: null },
          ],
          subcommands: [],
          options: [
            { name: "--version", aliases: ["--version", "-v"], description: "pin a specific version", default: null, choices: null, required: false, arg_kind: "optional", metavar: null, takes_value: true },
            { name: "--raw", aliases: ["--raw"], description: "print the raw template", default: false, choices: null, required: false, arg_kind: "optional", metavar: null, takes_value: false },
          ],
        },
        {
          name: "list",
          invocation: "python -m prompt_registry list",
          aliases: [],
          description: "show all prompts",
          positionals: [],
          subcommands: [],
          options: [],
        },
      ],
    },
  ],
};

/**
 * Honest fixture for GET /execution-queue. Exercises: a placeable order
 * (allow_place=true, no gate_reasons), a blocked order (allow_place=false,
 * gate_reasons populated), a null `qty` (BUY sized by target_notional only —
 * the queue never fabricates a share count without a live quote), and
 * `mode: "review"` (the queue is populated but nothing can be placed without
 * ROBINHOOD_EXECUTION_MODE=live) — mirrors execution/queue_builder.py's
 * actual output shape. `follow_type` mirrors the REAL two attribution
 * buckets the backend derives from execution/queue_builder.py's `"strategy"`
 * label (never a guessed/free-text category — CONSTRAINT #4): AAPL is a base
 * advisory-engine intent, TSLA is attributed to the "trend-following" mock
 * Pilot (a real id in MOCK_PILOTS below) to demonstrate the Strategy filter
 * against a genuine follow.
 */
const MOCK_EXECUTION_QUEUE: ExecutionQueue = {
  generated_at: new Date(Date.now() - 5 * 60_000).toISOString(),
  mode: "review",
  kill_switch_active: false,
  max_notional_per_order: 500,
  n_intents: 2,
  n_placeable: 1,
  stale: false,
  age_seconds: 300,
  reason: null,
  intents: [
    {
      symbol: "AAPL",
      action: "BUY",
      side: "buy",
      qty: null,
      target_notional: 250,
      conviction: 0.8,
      gate_allowed: true,
      gate_reasons: [],
      allow_place: true,
      rationale: "Strong momentum, low realized vol, HMM risk-on regime.",
      client_order_id: "advisory-AAPL-buy-1",
      follow_type: "advisory",
    },
    {
      symbol: "TSLA",
      action: "SELL",
      side: "sell",
      qty: 3,
      target_notional: 600,
      conviction: 0.6,
      gate_allowed: false,
      gate_reasons: ["macro_kill_switch"],
      allow_place: false,
      rationale: "Pilot follow (trend-following) risk-reduce exit.",
      client_order_id: "follow-trend-following-TSLA-sell-1",
      follow_type: "trend-following",
    },
  ],
};

// ---- Local scan-config store (localStorage) — mirrors the follows-store
// pattern above; backs the Agentic Trading tab's Discovery section. Seeded
// with one enabled config so the demo shows a populated Discovery section by
// default; a fresh browser with a cleared localStorage still degrades
// honestly (readScanConfigs falls back to this same seed, not an empty
// list — there's no server round-trip to distinguish "never configured" from
// "cleared" in the mock, so the seed doubles as both). ----
const SCAN_CONFIG_KEY = "stockpy.mock.scan_configs";

const DEFAULT_SCAN_CONFIGS: ScanConfig[] = [
  {
    name: "high_momentum_breakout",
    filters: { min_price: 5, min_volume: 1_000_000, rsi_min: 50, rsi_max: 70 },
    enabled: true,
    created_at: new Date(Date.now() - 86_400_000).toISOString(),
    updated_at: new Date(Date.now() - 86_400_000).toISOString(),
  },
];

function readScanConfigs(): ScanConfig[] {
  try {
    const raw = localStorage.getItem(SCAN_CONFIG_KEY);
    return raw ? (JSON.parse(raw) as ScanConfig[]) : DEFAULT_SCAN_CONFIGS;
  } catch {
    return DEFAULT_SCAN_CONFIGS;
  }
}
function writeScanConfigs(cs: ScanConfig[]) {
  try {
    localStorage.setItem(SCAN_CONFIG_KEY, JSON.stringify(cs));
  } catch {
    /* ignore quota */
  }
}

// ---- Local watchlist simulation (localStorage) so a repeated "Watch" of the
// same candidate honestly returns already_present, mirroring the real
// pilots.watchlist_writer dedup. The mock has no WATCHLIST-env concept, so the
// 409 precedence branch is not simulated here (exercised in the Python tests). --
const WATCHLIST_KEY = "stockpy.mock.watchlist";
// Same conservative ticker shape as pilots/watchlist_writer.py's _SYMBOL_RE.
const MOCK_SYMBOL_RE = /^[A-Z]{1,6}([.\-][A-Z]{1,4})?$/;
function readWatched(): string[] {
  try {
    const raw = localStorage.getItem(WATCHLIST_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}
function writeWatched(syms: string[]) {
  try {
    localStorage.setItem(WATCHLIST_KEY, JSON.stringify(syms));
  } catch {
    /* ignore quota */
  }
}

/**
 * Honest fixture for GET /agentic/discovery. Exercises a scored candidate
 * (action/conviction populated from an advisory cross-reference) alongside
 * one the agentic-discovery skill couldn't cross-reference — action/conviction
 * null, never a fabricated score (CONSTRAINT #4) — mirroring
 * pilots/discovery.py's `_sanitize_candidate`.
 */
const MOCK_DISCOVERY_CANDIDATES: DiscoveryCandidate[] = [
  {
    symbol: "NVDA",
    scan_name: "high_momentum_breakout",
    scan_reason: "Price > 20SMA, volume > 2x avg, RSI(14) 58",
    action: "BUY",
    conviction: 0.71,
    discovered_at: new Date(Date.now() - 3_600_000).toISOString(),
  },
  {
    symbol: "PLTR",
    scan_name: "high_momentum_breakout",
    scan_reason: "Price > 20SMA, volume > 2x avg, RSI(14) 63",
    action: null,
    conviction: null,
    discovered_at: new Date(Date.now() - 3_600_000).toISOString(),
  },
];

/** Honest fixture for GET /agentic/status -> agent_loop. A populated,
 *  mid-cycle advisory-loop agent state (engine/advisory_agent.py). */
const MOCK_AGENT_LOOP: AgentLoopStatus = {
  cycle_count: 42,
  last_cycle_iso: new Date(Date.now() - 8 * 60_000).toISOString(),
  backlog_count: 1,
  reason: null,
};

// In-memory job bookkeeping for createJob/getJobStatus/cancelJob — just
// enough state so the mock's status-polling story (running -> success, or
// running -> cancelled) is believable rather than always-terminal.
const _mockJobs: Record<
  string,
  { jobType: string; commandName: string | null; startedAt: number; createdAt: string; cancelled: boolean }
> = {};

// ---------------------------------------------------------------------------
// Prompt Registry (webapp parity gap G4) — GET /prompts, GET /prompts/{id},
// PUT /prompts/pin. Uses the SAME real baseline prompt IDs the backend's
// prompt_registry/baseline/*.md ships (master_preprompt, gravity.system,
// gravity.step_01..07) so a screen exercised against the mock looks
// shape-identical to a real, unconfigured (baseline-only) registry.
// ---------------------------------------------------------------------------

interface _MockPromptFixture {
  /** Resolution state with NO pin set: version + source GET /prompts would
   *  report. "remote"/"cache" ids also carry a believable cachedVersions
   *  list; a pure "baseline" id has none (never synced, never pinned). */
  unpinnedVersion: string;
  unpinnedSource: "remote" | "cache" | "baseline";
  cachedVersions: string[]; // newest first; [] for baseline-only ids
  body: string;
}

const _MOCK_PROMPT_FIXTURES: Record<string, _MockPromptFixture> = {
  master_preprompt: {
    unpinnedVersion: "baseline",
    unpinnedSource: "baseline",
    cachedVersions: [],
    body:
      "You are the InvestYo advisory assistant. Ground every claim in the " +
      "provided DTOs; never fabricate a price, signal, or metric that isn't " +
      "present in the data. (mock baseline body — real text lives in " +
      "prompt_registry/baseline/master_preprompt.md)",
  },
  "gravity.system": {
    unpinnedVersion: "2.1.0",
    unpinnedSource: "remote",
    cachedVersions: ["2.1.0", "2.0.0", "1.0.0"],
    body:
      "You are Gravity, the AI code auditor for the InvestYo quant platform. " +
      "Verify vectorization, lookahead-bias freedom, and honest degradation on " +
      "every changed file. Respond in JSON: {\"status\": \"PASSED/FAILED\", " +
      "\"score\": 0-100, \"findings\": []}. (mock remote body)",
  },
  "gravity.step_01": {
    unpinnedVersion: "1.1.0",
    unpinnedSource: "cache",
    cachedVersions: ["1.1.0", "1.0.0"],
    body:
      "Analyze the provided source code for Step 1. Verify vectorized " +
      "Pandas/NumPy operations and a relational database schema. Respond in " +
      "JSON: {\"status\": \"PASSED/FAILED\", \"score\": 0-100}. (mock cached body)",
  },
  "gravity.step_02": {
    unpinnedVersion: "baseline",
    unpinnedSource: "baseline",
    cachedVersions: [],
    body: "Analyze the provided source code for Step 2. (mock baseline body)",
  },
  "gravity.step_03": {
    unpinnedVersion: "baseline",
    unpinnedSource: "baseline",
    cachedVersions: [],
    body: "Analyze the provided source code for Step 3. (mock baseline body)",
  },
  "gravity.step_04": {
    unpinnedVersion: "baseline",
    unpinnedSource: "baseline",
    cachedVersions: [],
    body: "Analyze the provided source code for Step 4. (mock baseline body)",
  },
  "gravity.step_05": {
    unpinnedVersion: "baseline",
    unpinnedSource: "baseline",
    cachedVersions: [],
    body: "Analyze the provided source code for Step 5. (mock baseline body)",
  },
  "gravity.step_06": {
    unpinnedVersion: "baseline",
    unpinnedSource: "baseline",
    cachedVersions: [],
    body: "Analyze the provided source code for Step 6. (mock baseline body)",
  },
  "gravity.step_07": {
    unpinnedVersion: "baseline",
    unpinnedSource: "baseline",
    cachedVersions: [],
    body: "Analyze the provided source code for Step 7. (mock baseline body)",
  },
};

// In-memory pin map -- mutated by putPromptPin, read by getPrompts/getPrompt,
// so a pin set within a mock session is genuinely visible on re-fetch within
// that session (mirrors MOCK_DECISION_LOG's convention). Seeded with ONE
// pre-existing pin so the "already pinned" row/badge renders on first load
// without requiring an interaction first — an honesty-fixture requirement,
// not just a nicety (a screen that only ever sees an all-unpinned registry
// can't be checked against the pinned-row rendering path at all).
const _MOCK_PROMPT_PINS: Record<string, string> = {
  "gravity.system": "2.0.0",
};

const MOCK_PROMPT_REGISTRY_ENABLED = true;
// Mirrors settings.PROMPT_REGISTRY_WRITES_ENABLED — true here so the mock
// exercises the pin/clear-pin write UI by default (the more interesting
// path); co-located tests cover the writable:false / disabled-pin-UI branch
// by overriding this via a mocked api module rather than a second fixture.
const MOCK_PROMPT_REGISTRY_WRITABLE = true;

// ================= public mock API (shape-identical to client.ts) =================
export const mockApi = {
  async health() {
    return delay({ status: "ok", mock: true }, 60);
  },

  async listPilots(): Promise<PilotSummary[]> {
    return delay(CATALOG.map((p) => p.summary));
  },

  async getPilot(id: string): Promise<PilotDetail> {
    const p = findPilot(id);
    if (!p) throw notFound(id);
    const detail: PilotDetail = {
      ...p.summary,
      holdings: p.holdings,
      sector_allocation: sectorAlloc(p.holdings),
      recent_trades: trades(p.holdings),
      as_of: new Date(Date.now() - 5400_000).toISOString(),
    };
    return delay(detail);
  },

  async getPerformance(
    id: string,
    range: PerfRange
  ): Promise<PerformanceResponse> {
    const p = findPilot(id);
    if (!p) throw notFound(id);
    if (!p.hasCurve) {
      return delay({
        range,
        metrics: p.summary.headline,
        curve: null,
        benchmark: null,
        macro_benchmark: null,
        reason:
          "No backtest series yet — this Pilot's validation report has no persisted return curve.",
      });
    }
    return delay({
      range,
      metrics: p.summary.headline,
      curve: synthCurve(id, range, p.curveDrift, p.curveVol),
      benchmark: synthCurve("SPY-benchmark", range, 0.09, 0.09),
      // SEPARATE, distinctly-drifted SPY (broad-market) overlay — null when the
      // Pilot's underlying already IS SPY (redundant), never fabricated.
      macro_benchmark: p.macroBenchmark
        ? synthCurve("SPY-macro", range, 0.08, 0.1)
        : null,
    });
  },

  async getUniverse(): Promise<UniverseResponse> {
    // The tracked universe = the same union the mock symbol-detail endpoint
    // recognizes, so every autocomplete suggestion resolves to a real detail
    // page (mirrors the backend's snapshot signals[]). `action` decorates only
    // some rows on purpose — the rest are `null` so the UI's undecorated path is
    // exercised too (honesty fixture, never a fabricated action for all).
    const ACTIONS: Record<string, string> = {
      AAPL: "BUY",
      MSFT: "HOLD",
      NVDA: "STRONG BUY",
      COST: "HOLD",
      DUK: "SELL",
    };
    const symbols: UniverseSymbol[] = [...SYMBOL_UNIVERSE]
      .sort()
      .map((symbol) => ({ symbol, action: ACTIONS[symbol] ?? null }));
    return delay({ symbols });
  },

  async getSyncReport(): Promise<SyncReportResponse> {
    // A realistic multi-symbol fixture spanning all SIX
    // data.portfolio_sync.CoverageStatus values (not just FULL/EQUITY_ONLY/
    // UNCOVERED), so the component's badge styling and "Coverage gaps only"
    // filter both have something honest to show for every state. Mirrors the
    // shape GET /data/sync-report actually returns: a ticker-keyed map, not a
    // pre-sorted array with server-computed counts (the component reshapes it
    // client-side, same as the live endpoint forces it to).
    const ROWS: Record<string, { coverage: CoverageStatus; held: boolean; diagnostic: string }> = {
      AAPL: { coverage: "full", held: true, diagnostic: "" },
      MSFT: { coverage: "full", held: true, diagnostic: "" },
      NVDA: { coverage: "stale", held: true, diagnostic: "" },
      V: { coverage: "quotes_only", held: true, diagnostic: "fundamentals:empty" },
      COST: { coverage: "full", held: true, diagnostic: "" },
      // Held in Robinhood but no live quote — a real position with unknown
      // current price, matching data.portfolio_sync.SymbolStatus (avg_cost is
      // NaN only when not held, not when merely uncovered).
      DUK: { coverage: "equity_only", held: true, diagnostic: "quote:NotFoundError" },
      // On a watchlist only (never held) and unreachable on both legs.
      T: { coverage: "uncovered", held: false, diagnostic: "quote:NotFoundError,fundamentals:empty" },
      // Probe was skipped entirely (offline/degraded mode) — never a
      // fabricated FULL/UNCOVERED guess when the probe didn't actually run.
      XOM: { coverage: "unknown", held: false, diagnostic: "probe_skipped" },
    };

    const symbols: Record<string, SyncReportSymbol> = {};
    for (const symbol of Object.keys(ROWS).sort()) {
      const { coverage, held, diagnostic } = ROWS[symbol];
      // FULL/STALE/QUOTES_ONLY all mean the quote leg succeeded — only
      // fundamentals coverage (and, for STALE, freshness) differs.
      const covered = coverage === "full" || coverage === "stale" || coverage === "quotes_only";
      const rng = seeded([...symbol].reduce((a, c) => a + c.charCodeAt(0), 0));
      const position = PORTFOLIO.positions.find((p) => p.symbol === symbol);
      symbols[symbol] = {
        symbol,
        coverage,
        held,
        quantity: held ? position?.qty ?? 10 : 0,
        avg_cost: held ? position?.avg_cost ?? +(50 + rng() * 300).toFixed(2) : null,
        current_price: covered ? +(50 + rng() * 400).toFixed(2) : null,
        cost_basis_delta_per_share: covered && held ? +((rng() - 0.5) * 40).toFixed(2) : null,
        market_value: covered ? +(1000 + rng() * 9000).toFixed(2) : null,
        is_stale_quote: coverage === "stale",
        quote_source: covered ? "alpaca" : "",
        has_fundamentals: coverage === "full" || coverage === "stale",
        forecast_available: covered,
        watchlists: held ? [] : ["file:watchlist.txt"],
        diagnostic,
      };
    }

    return delay({
      generated_at: new Date(Date.now() - 5_400_000).toISOString(),
      positions: PORTFOLIO.positions.map((p) => p.symbol),
      watchlists: { "file:watchlist.txt": ["T", "XOM"] },
      symbols,
      provider_source: "alpaca",
      fundamentals_source: "yahoo_computed",
    });
  },

  async getThresholds(): Promise<Thresholds> {
    // Mirrors validation/thresholds.py + settings.py's real current defaults —
    // the mock has no live Python process to import from, so these are the
    // fixture layer's honest snapshot of those values, not an invented number.
    return delay({
      pbo_max: 0.5,
      dsr_min: 0.95,
      net_sharpe_min: 0.5,
      max_drawdown_max: 0.3,
      stress_max_drawdown: 0.5,
      kelly_fraction: 0.5,
      kelly_cap: 0.2,
      robinhood_max_notional_per_order: 0.0,
      follow_min_amount: 100.0,
      agentic_max_candidates: 25,
      retrain_window_days: MODEL_RETRAIN_WINDOW_DAYS,
    });
  },

  async getSymbol(ticker: string): Promise<SymbolDetail> {
    const sym = ticker.trim().toUpperCase();
    if (!SYMBOL_UNIVERSE.has(sym)) throw notFoundSymbol(sym);

    // Reverse cross-link — scan the real CATALOG: every Pilot whose holdings
    // include this symbol, reading its normalized weight, sorted weight-desc.
    const held_by_pilots: SymbolHeldBy[] = CATALOG.map((p) => {
      const hd = p.holdings.find((x) => x.symbol === sym);
      return hd
        ? { pilot_id: p.summary.id, name: p.summary.name, weight: hd.weight }
        : null;
    })
      .filter((x): x is SymbolHeldBy => x !== null)
      .sort((a, b) => b.weight - a.weight);

    // Deterministic per-symbol pseudo-values (stable across navigations).
    const rng = seeded([...sym].reduce((a, c) => a + c.charCodeAt(0), 0));
    const price = +(50 + rng() * 400).toFixed(2);

    // Aggregate signal = mean of this symbol's blended score across holders.
    const scores = CATALOG.flatMap((p) =>
      p.holdings.filter((x) => x.symbol === sym).map((x) => x.score)
    );
    const score = scores.length
      ? +(scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(3)
      : null;
    const position_pct = held_by_pilots.length
      ? +held_by_pilots[0].weight.toFixed(4)
      : null;
    const held = PORTFOLIO.positions.find((p) => p.symbol === sym);
    const conviction = score == null ? null : +(0.55 + score * 0.35).toFixed(2);
    const action = score != null && score >= 0.5 ? "BUY" : "HOLD";

    const detail: SymbolDetail = {
      symbol: sym,
      as_of: new Date(Date.now() - 5_400_000).toISOString(),
      reason: null,
      identity: {
        sector: SECTOR_OF[sym] ?? null,
        price,
        action,
        shares: held ? held.qty : null,
      },
      advisory: {
        action,
        conviction,
        position_pct,
        rationale: held_by_pilots.length
          ? `Held by ${held_by_pilots.length} Pilot(s); largest allocation in ${held_by_pilots[0].name}.`
          : "Portfolio position with no active Pilot signal.",
        kelly_target: position_pct == null ? null : +(position_pct * 0.5).toFixed(4),
        score,
      },
      factors: {
        // HONEST nulls — point-in-time fundamentals & cross-sectional inputs the
        // advisory snapshot writer does not carry (mirrors the backend fixture).
        value_z: null,
        quality_z: null,
        xsec_12_1m: null,
        xsec_momentum_rank: null,
        lowvol_z: +((rng() - 0.5) * 2).toFixed(3),
        size_z: +((rng() - 0.5) * 2).toFixed(3),
        multifactor_composite: +((rng() - 0.5) * 1.5).toFixed(3),
        score_components: { momentum: +rng().toFixed(3), trend: +rng().toFixed(3) },
      },
      ranges: {
        buy_range: `Buy Zone: $${(price * 0.97).toFixed(2)} - $${price.toFixed(2)}`,
        sell_range: `Sell Zone: $${(price * 1.08).toFixed(2)} - $${(price * 1.12).toFixed(2)}`,
      },
      risk: {
        // HONEST nulls — no news feed, and realized/excursion metrics need
        // post-fill trade history (matches the advisory writer / backend fixture).
        news_sentiment: null,
        realized_slippage: null,
        mfe: null,
        mae: null,
        edge_ratio: null,
        macro_status: null,
        covar_proxy: +(rng() * 0.5).toFixed(3),
        hmm_risk_on: +(0.5 + rng() * 0.5).toFixed(2),
      },
      // DUK exercises the honest-null branch (mirrors getSymbolsCompare's
      // hasRegimeFields convention) — the strategy engine didn't produce a
      // sizing decomposition for it this cycle. meta_label_composite is a
      // genuine 1.0 for every other symbol (the platform's real current
      // state: no MetaLabelers registered), never a fabricated spread.
      sizing:
        sym === "DUK"
          ? {
              kelly_target_pre_regime: null,
              kelly_target_post_regime: null,
              regime_multiplier: null,
              meta_label_composite: null,
              max_position_weight: 1.0,
            }
          : {
              kelly_target_pre_regime:
                position_pct == null ? null : +(position_pct * 0.55).toFixed(4),
              kelly_target_post_regime: position_pct == null ? null : +(position_pct * 0.5).toFixed(4),
              regime_multiplier: +(0.8 + rng() * 0.4).toFixed(3),
              meta_label_composite: 1.0,
              max_position_weight: 1.0,
            },
      held_by_pilots,
    };
    return delay(detail);
  },

  async getSymbolsCompare(tickers: string[]): Promise<SymbolCompareResponse> {
    // Mirrors the real endpoint's own validation (2-5 symbols after
    // upper-case + de-dupe) so the mock/live parity gate exercises the error
    // path too, not just the happy path.
    const deduped = Array.from(
      new Set(tickers.map((t) => t.trim().toUpperCase()).filter(Boolean))
    );
    if (deduped.length < 2) {
      throw new ApiError("Select at least 2 symbols to compare.", 422);
    }
    if (deduped.length > 5) {
      throw new ApiError("Select at most 5 symbols to compare.", 422);
    }

    const rows: SymbolCompareRow[] = deduped.map((sym) => {
      if (!SYMBOL_UNIVERSE.has(sym)) {
        // Honest "not tracked" row — never a hard failure for the whole
        // request over one bad ticker (mirrors the backend contract).
        return {
          symbol: sym,
          found: false,
          reason: "Not tracked in the latest snapshot.",
          score: null,
          action: null,
          kelly_target: null,
          conviction: null,
          garch_vol: null,
          meta_label_composite: null,
          regime_multiplier: null,
          score_components: null,
        };
      }

      const rng = seeded([...sym].reduce((a, c) => a + c.charCodeAt(0), 0));
      const scores = CATALOG.flatMap((p) =>
        p.holdings.filter((x) => x.symbol === sym).map((x) => x.score)
      );
      const score = scores.length
        ? +(scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(3)
        : null;
      const conviction = score == null ? null : +(0.55 + score * 0.35).toFixed(2);
      const action = score != null && score >= 0.5 ? "BUY" : "HOLD";
      const kelly_target = score == null ? null : +(Math.max(score, 0) * 0.1).toFixed(4);

      // DUK deliberately carries no meta_label_composite/regime_multiplier —
      // both fields are null whenever the strategy engine didn't produce a
      // value for a symbol that cycle (see pilots/symbols.py::compare_symbols'
      // docstring); this fixture exercises that honest-null branch instead of
      // pretending every symbol always has them.
      const hasRegimeFields = sym !== "DUK";

      return {
        symbol: sym,
        found: true,
        reason: null,
        score,
        action,
        kelly_target,
        conviction,
        garch_vol: +(0.15 + rng() * 0.35).toFixed(3),
        meta_label_composite: hasRegimeFields ? 1.0 : null,
        regime_multiplier: hasRegimeFields ? +(0.8 + rng() * 0.4).toFixed(2) : null,
        score_components: {
          momentum: +rng().toFixed(3),
          trend: +rng().toFixed(3),
          value: +((rng() - 0.5) * 2).toFixed(3),
        },
      };
    });

    const modules = Array.from(
      new Set(
        rows.flatMap((r) => (r.score_components ? Object.keys(r.score_components) : []))
      )
    ).sort();

    return delay({
      as_of: new Date(Date.now() - 5_400_000).toISOString(),
      symbols: rows,
      modules,
    });
  },

  async getPortfolio(): Promise<Portfolio> {
    return delay(PORTFOLIO);
  },

  async getEquityCurve(range: PerfRange): Promise<EquityCurveResponse> {
    return delay({
      range,
      curve: synthCurve("account-equity", range, 0.1, 0.08, 44000),
      // Buying power drifts far more slowly than equity and dips on new
      // positions -- a distinct (near-flat, lower-vol) series, not a scaled
      // copy of the equity curve, so the overlay toggle visibly shows a
      // DIFFERENT line (G14).
      buying_power_curve: synthCurve("account-buying-power", range, 0.01, 0.03, 6100),
    });
  },

  async getFollows(): Promise<Follow[]> {
    return delay(readFollows(), 80);
  },

  async follow(id: string, amount: number): Promise<FollowResult> {
    const p = findPilot(id);
    if (!p) throw notFound(id);
    const now = new Date().toISOString();
    const existing = readFollows();
    const prior = existing.find((f) => f.pilot_id === id);
    const follow: Follow = {
      pilot_id: id,
      amount,
      created_at: prior?.created_at ?? now,
      updated_at: now,
      // Matches the real `pilots/follows_store.py` vocabulary ("active" |
      // "cancelled") — the mock previously used "queued", which the real
      // backend never emits.
      status: amount <= 0 ? "cancelled" : "active",
    };
    const next = existing.filter((f) => f.pilot_id !== id);
    if (amount > 0) next.push(follow);
    writeFollows(next);

    const planned = p.holdings.map((hd) => ({
      symbol: hd.symbol,
      side: "BUY" as const,
      target_notional: +Math.min(amount * hd.weight, NOTIONAL_CAP).toFixed(2),
      weight: hd.weight,
      conviction: +(0.55 + hd.score * 0.35).toFixed(2),
      allow_place: false, // mock is review-mode; nothing is ever placeable
    }));

    return delay({
      follow,
      planned_intents: amount > 0 ? planned : [],
      mode: MOCK_MODE,
      queue_written: amount > 0,
      notional_cap: NOTIONAL_CAP,
      min_amount: MIN_AMOUNT,
      notice:
        "This creates a gated, paper-first order queue that you must confirm. No order is placed automatically.",
    });
  },

  async getAutomationStatus(): Promise<AutomationStatus> {
    const now = Date.now();
    return delay(
      {
        daemon: {
          alive: true,
          source: "control_api",
          pid: null,
          pid_alive: null, // consistent with pid: null on this branch, mirroring the live path's invariant
          port: 8601,
          started_at: new Date(now - 6 * 3600_000).toISOString(),
          interval_seconds: 300,
          is_running: false,
          current_run_id: null,
          engines_warm: true,
        },
        last_run: {
          run_id: "orch-mock-0417",
          state: "succeeded",
          started_at: new Date(now - 5 * 60_000 - 40_000).toISOString(),
          finished_at: new Date(now - 5 * 60_000).toISOString(),
          duration_seconds: 40.2,
          error: null,
          reason: "interval",
          progress: null,
        },
        last_run_source: "daemon_memory",
        pipeline: {
          snapshot_age_seconds: 300,
          snapshot_age_source: "timestamp",
          heartbeat_age_seconds: null,
          heartbeat_note:
            "heartbeat.txt is written only by main_orchestrator.py; advisory runs (main.py) never write it, so null here does not mean the engine is down — see pipeline.snapshot_age_seconds for the cross-mode liveness signal.",
        },
        progress: null,
        kill_switch: readKillSwitch(),
        errors: { generated_at: new Date(now - 5 * 60_000).toISOString(), entry_count: 0, entries: [] },
        advisory_only: true,
        dry_run: false,
  alpaca_paper: false,
      },
      120
    );
  },

  async getAutomationSchedule(): Promise<AutomationSchedule> {
    const configured = readMockInterval();
    return delay(
      {
        interval: {
          running_value: 300,
          configured_value: configured,
          drift: configured !== 300,
          writable: true,
          note: "Writes persist to .env and apply on the daemon's next restart.",
        },
        cron: {
          source: "deploy/crontab.txt",
          installed: null,
          note:
            "Parsed from the repo file — the intended schedule. This API never runs `crontab -l`, so it cannot confirm what is actually installed on the host; it may differ.",
          entries: [
            {
              schedule: "0 21 * * 1-5",
              command:
                "cd /opt/investyo && .venv/bin/python scripts/daily_briefing.py >> /opt/investyo/logs/daily_briefing.log 2>&1",
              comment:
                "Daily: Full pipeline refresh (weekdays, 1 hour after market close) Fetches latest price bars, EDGAR filings, macro indicators, and computes composite signals for the active universe.",
            },
            {
              schedule: "0 8 * * *",
              command:
                "cd /opt/investyo && .venv/bin/python scripts/preflight_check.py --validation-staleness-only >> /opt/investyo/logs/validation_staleness.log 2>&1",
              comment:
                "Daily: Strategy validation staleness/deployability alert (08:00 UTC)",
            },
            {
              schedule: "0 6 * * 0",
              command:
                "cd /opt/investyo && .venv/bin/python scripts/backfill_edgar_fundamentals.py --tickers all >> /opt/investyo/logs/edgar_backfill.log 2>&1",
              comment: "Weekly: Full EDGAR backfill sweep (Sundays at 06:00 UTC / 2 AM ET)",
            },
            {
              schedule: "0 7 3 * *",
              command:
                "cd /opt/investyo && ./scripts/refresh_validations.sh >> /opt/investyo/logs/validations.log 2>&1",
              comment:
                "Monthly: Strategy validation harness re-run (3rd of month, 07:00 UTC)",
            },
          ],
        },
      },
      80
    );
  },

  async getControlStatus(): Promise<ControlStatus> {
    return delay(mockControlStatus(), 120);
  },

  async getRunHistory(limit = 50): Promise<RunRecord[]> {
    return delay(RUN_HISTORY_DURABLE.slice(0, limit), 140);
  },

  async postControlRun(): Promise<{ run_id: string; state: string }> {
    return delay({ run_id: `orch-mock-${Date.now()}`, state: "queued" }, 300);
  },

  async postControlPipelineData(): Promise<{
    run_id: string;
    state: string;
    mode: string;
  }> {
    return delay(
      { run_id: `orch-mock-${Date.now()}`, state: "queued", mode: "data" },
      300
    );
  },

  async postControlPipelineMetrics(): Promise<{
    run_id: string;
    state: string;
    mode: string;
  }> {
    return delay(
      { run_id: `orch-mock-${Date.now()}`, state: "queued", mode: "metrics" },
      300
    );
  },

  async triggerRun(): Promise<TriggerRunResult> {
    const ks = readKillSwitch();
    if (ks.active) {
      return delay(
        {
          ok: false, run_id: null, state: null, error: "kill_switch_active",
          existing_run_id: null, kill_switch_reason: ks.reason,
        },
        150
      );
    }
    return delay(
      { ok: true, run_id: `orch-mock-${Date.now()}`, state: "queued", error: null, existing_run_id: null, kill_switch_reason: null },
      300
    );
  },

  async pauseAutomation(reason: string): Promise<KillSwitchActionResult> {
    writeKillSwitch(true, reason);
    return delay({ active: true, reason }, 150);
  },

  async resumeAutomation(_reason: string): Promise<KillSwitchActionResult> {
    writeKillSwitch(false, null);
    return delay({ active: false, reason: null }, 150);
  },

  async setAutomationInterval(seconds: number): Promise<IntervalUpdateResult> {
    writeMockInterval(seconds);
    return delay(
      {
        configured_value: seconds,
        written: String(seconds),
        applies: "next_daemon_restart",
      },
      150
    );
  },

  async setExecutionMode(req: ExecutionModeUpdateRequest): Promise<ExecutionModeUpdateResult> {
    return delay(
      {
        written:
          req.mode === "advisory"
            ? ["ADVISORY_ONLY"]
            : ["ADVISORY_ONLY", "DRY_RUN", "ALPACA_PAPER"],
        advisory_only: req.advisory_only,
        mode: req.mode,
        applies: "next_daemon_restart",
        note: "Execution mode updated.",
      },
      150
    );
  },

  async getBrokerageStatus(): Promise<BrokerageStatus> {
    return delay(
      {
        connected: readBrokerageConnected(),
        has_account_snapshot: readBrokerageConnected(),
      },
      80
    );
  },

  async getLlmStatus(): Promise<LlmStatus> {
    // The HONEST default posture: LLM_COMMENTARY_ENABLED / OPAL_RESEARCH_ENABLED
    // / GRAVITY_AI_RUNNER_ENABLED all default False (settings.py), so every
    // capability is `disabled`, no provider has a recorded call (`source:
    // "none"`), and there is nothing to warn about (`attention: false`). This
    // models the real out-of-box state and keeps App.test.tsx dot-free. A
    // toggle/provider write (putLlmSetting, below) persists to localStorage so
    // this reflects the change on the next read within the mock session --
    // see mockLlmStatus() and the LLM_* helpers above.
    return delay(mockLlmStatus(), 80);
  },

  async putLlmSetting(key: string, value: boolean | string): Promise<LlmSettingUpdateResult> {
    writeLlmOverride(key, value);
    return delay(
      {
        written: [key],
        value,
        applies: "next_daemon_restart",
        note:
          "Written to .env. settings is not patched in-process — this API " +
          "and any already-launched pipeline still use the previous value " +
          "until restarted.",
      },
      150
    );
  },

  async connectBrokerage(
    creds: BrokerageConnectRequest
  ): Promise<BrokerageConnectResult> {
    // Simulated verification only — the mock never contacts a real broker and
    // never persists the credential strings themselves, only a boolean marker.
    const verified = Boolean(
      creds.username.trim() && creds.password.trim() && creds.mfa_code.trim()
    );
    if (!verified) {
      throw new ApiError("Could not verify Robinhood credentials.", 401);
    }
    writeBrokerageConnected(true);
    return delay({ connected: true, verified: true, has_account_snapshot: false }, 500);
  },

  async disconnectBrokerage(): Promise<BrokerageDisconnectResult> {
    writeBrokerageConnected(false);
    return delay({ connected: false }, 150);
  },

  async refreshBrokerage(): Promise<BrokerageRefreshResult> {
    // Honesty branch: nothing is configured to log back into — mirrors the
    // real backend's 502 when fetch_account_snapshot has neither a fresh
    // live fetch nor any cached snapshot to fall back on (e.g. never
    // connected). A longer delay than the other brokerage calls simulates a
    // real login round-trip rather than a local cache read.
    if (!readBrokerageConnected()) {
      throw new ApiError("Could not refresh the Robinhood account snapshot.", 502);
    }
    if (readBrokerageRefreshDegraded()) {
      // Honesty branch: fetch_account_snapshot's own internal fallback —
      // the live login failed, so a real (if stale) PREVIOUSLY cached
      // snapshot was returned instead of a fresh one. fetched_at/age_hours
      // are deliberately NOT reset to "now", unlike the healthy branch below.
      return delay({ ...PORTFOLIO, is_stale: true, source: "live" }, 900);
    }
    return delay(
      {
        ...PORTFOLIO,
        fetched_at: new Date().toISOString(),
        is_stale: false,
        age_hours: 0,
        source: "live",
      },
      900
    );
  },

  async getRealized(): Promise<RealizedPerformance> {
    return delay({
      summary: realizedSummary(REALIZED_TRADES),
      trades: REALIZED_TRADES,
      n_fills: REALIZED_TRADES.length * 2,
      available: true,
    });
  },

  async getPortfolioAttribution(_lookbackDays = 60): Promise<PortfolioAttribution> {
    return delay(mockPortfolioAttribution());
  },

  async getBrinsonFachlerAttribution(
    rows: BrinsonFachlerRow[]
  ): Promise<BrinsonFachlerResult> {
    // Throws ApiError(..., 422) synchronously on structurally bad input --
    // matches the live endpoint's honesty contract (a 422 shows the server's
    // error message inline, not a generic failure).
    return delay(mockComputeBrinsonFachler(rows));
  },

  async getAlerts(limit = 50): Promise<AlertsFeed> {
    const feed = mockAlerts();
    return delay({ ...feed, entries: feed.entries.slice(0, limit) });
  },

  async getForecast(ticker: string, horizon = 30): Promise<ForecastSkill> {
    return delay(mockForecast(ticker, horizon));
  },

  async getSectorSelection(target: string, n = 3): Promise<SectorSelectionView> {
    return delay(mockSectorSelection(target, n));
  },

  async getRollingBeta(ticker: string, window = 60): Promise<RollingBeta> {
    return delay(mockRollingBeta(ticker, window));
  },

  async getModels(): Promise<ModelRow[]> {
    return delay(MODELS);
  },

  async getOptions(): Promise<OptionsMatrix> {
    return delay(mockOptionsMatrix());
  },

  async getSymbolOptions(ticker: string): Promise<SymbolOptions> {
    const sym = ticker.trim().toUpperCase();
    const directive = OPTIONS_BY_SYMBOL[sym] ?? null;
    return delay({
      symbol: sym,
      directive,
      reason: directive ? null : "No options directive for this symbol yet.",
    });
  },

  // ---- On-demand AI generation (data base, :8603) ----
  // Deliberately keyed off `NVDA` for the honest `available: false` branch of
  // ALL THREE (a different `reason` each time) so a single symbol exercises
  // every disabled/error rendering path; every other symbol gets the
  // available:true happy path. Never automatic — only called from a Generate
  // button click (see SymbolDetail.tsx).
  async generateCommentary(ticker: string): Promise<AiCommentaryResponse> {
    const sym = ticker.trim().toUpperCase();
    if (sym === "NVDA") {
      return delay({ available: false, reason: "missing_key", payload: null }, 400);
    }
    return delay(
      {
        available: true,
        reason: null,
        payload: {
          headline: `Mean-reversion entry on a healthy uptrend for ${sym}.`,
          why_now: `${sym} pulled back to its rising 50-day average on below-average volume while the broader regime stays risk-on — the kind of shallow, orderly dip the signal is designed to buy rather than a breakdown to avoid.`,
          key_risks: [
            "A broad market risk-off shift would compress conviction across the whole book, not just this name.",
            "Elevated implied volatility ahead of the next earnings print could reprice the setup quickly.",
          ],
          invalidation: `A daily close below the 200-day SMA invalidates the uptrend thesis for ${sym}.`,
        },
      },
      400
    );
  },

  async generateChart(ticker: string): Promise<AiChartResponse> {
    const sym = ticker.trim().toUpperCase();
    if (sym === "NVDA") {
      // The chart itself rendered fine — only the AI narrative failed. The
      // image must still render on the card even though available is false.
      return delay(
        {
          available: false,
          reason: "generation_failed",
          payload: null,
          chart_png_base64: MOCK_CHART_PNG_BASE64,
        },
        400
      );
    }
    return delay(
      {
        available: true,
        reason: null,
        payload: {
          pattern_name: "ascending triangle",
          trend_direction: "bullish",
          support_levels: ["recent low near the 50-day average", "prior breakout zone"],
          resistance_levels: ["swing high from the last rally"],
          narrative: `${sym} is consolidating in a tightening range with a flat resistance line and rising higher-lows underneath it — a classic ascending-triangle continuation setup. A close above the recent swing high would confirm the breakout; volume has been contracting into the apex, typical ahead of a resolution.`,
          confidence: "medium",
        },
        chart_png_base64: MOCK_CHART_PNG_BASE64,
      },
      400
    );
  },

  async generateResearch(ticker: string): Promise<AiResearchResponse> {
    const sym = ticker.trim().toUpperCase();
    if (sym === "NVDA") {
      return delay({ available: false, reason: "disabled", payload: null }, 400);
    }
    return delay(
      {
        available: true,
        reason: null,
        payload: {
          thesis_context: `${sym}'s setup is grounded in a mix of steady demand trends and a favorable macro backdrop, with no major red flags in the most recently retrieved news or earnings coverage.`,
          catalysts: [
            "Q3 earnings call scheduled in the next few weeks",
            "Analyst day presentation flagged for early next month",
          ],
          risk_factors: [
            "Input cost commentary in the most recent earnings call flagged margin pressure",
          ],
          recent_developments: [
            "Reported quarterly results modestly ahead of consensus estimates",
            "Announced a new product line extension covered by several trade outlets",
          ],
          data_confidence: "medium",
          sources_note: "Based on 4 Finnhub headlines from the past 7 days and the most recent earnings date.",
        },
      },
      400
    );
  },

  async getPairs(): Promise<PairsRadar> {
    return delay(mockPairs());
  },

  // ---- On-demand Options/Pairs recompute (webapp porting backlog 8a/8b) ----
  // "ZZZ" is this file's existing dead-letter/no-data convention (see the
  // OPTIONS_DIRECTIVES fixture row above) -- reused here so a symbol/pair
  // typo exercises the SAME honest degrade path a real unresolved ticker
  // would hit against the live API, not a happy-path-only fixture.
  async analyzePairs(req: PairsAnalyzeRequest): Promise<PairsAnalyzeResult> {
    const symY = req.symbol_y.trim().toUpperCase();
    const symX = req.symbol_x.trim().toUpperCase();
    const notFoundBase = {
      ticker1: symY,
      ticker2: symX,
      found: false as const,
      p_value: null,
      half_life: null,
      half_life_tradeable: null,
      z_score: null,
      beta: null,
      rolling_p: null,
      position: null,
      signal: "No signal — insufficient history",
      aligned_bars: 0,
      z_score_series: [],
    };
    if (!symY || !symX) {
      return delay({ ...notFoundBase, reason: "Both Symbol Y and Symbol X are required." }, 250);
    }
    if (symY === symX) {
      return delay(
        { ...notFoundBase, reason: "Symbol Y and Symbol X must be different tickers." },
        250
      );
    }
    if (symY === "ZZZ" || symX === "ZZZ") {
      return delay(
        {
          ...notFoundBase,
          reason: `Insufficient aligned history for ${symY}/${symX} — one or both symbols may be unavailable from the provider.`,
        },
        450
      );
    }

    const rng = seeded([...symY, ...symX].reduce((a, c) => a + c.charCodeAt(0), 0));
    const z = +((rng() - 0.5) * 6).toFixed(2);
    const halfLife = +(8 + rng() * 40).toFixed(1);
    const rollingP = +(rng() * 0.15).toFixed(4);
    const position = z > 2 ? -1 : z < -2 ? 1 : 0;
    const halfLifeTradeable = halfLife >= 5 && halfLife <= 60 && rollingP <= 0.1;
    const signal =
      rollingP > 0.1
        ? "No signal — not cointegrated (ADF p>0.10)"
        : Math.abs(z) > 4
          ? "STOP — |z|>4 (exit spread)"
          : Math.abs(z) > 2
            ? z > 0
              ? "ENTER SHORT spread"
              : "ENTER LONG spread"
            : "Flat — no entry (|z|<2)";
    const n = 90;
    const series = Array.from({ length: n }, (_, i) => ({
      date: new Date(Date.now() - (n - i) * 86_400_000).toISOString().slice(0, 10),
      z_score: +(Math.sin(i / 9 + rng()) * 2 + (rng() - 0.5)).toFixed(2),
    }));
    series[series.length - 1] = { date: series[series.length - 1].date, z_score: z };

    return delay(
      {
        ticker1: symY,
        ticker2: symX,
        found: true,
        reason: null,
        p_value: +(rng() * 0.05).toFixed(4),
        half_life: halfLife,
        half_life_tradeable: halfLifeTradeable,
        z_score: z,
        beta: +(0.5 + rng()).toFixed(3),
        rolling_p: rollingP,
        position,
        signal,
        aligned_bars: 240,
        z_score_series: series,
      },
      450
    );
  },

  async scanPairs(req: PairsScanRequest): Promise<PairsScanResult> {
    const requested = Array.from(
      new Set(req.symbols.map((s) => s.trim().toUpperCase()).filter(Boolean))
    );
    const known = new Set(["XOM", "CVX", "V", "JPM", "MSFT", "AAPL", "HD", "COST"]);
    const missing = requested.filter((s) => !known.has(s));
    const usable = requested.filter((s) => known.has(s));

    if (usable.length < 2) {
      return delay(
        {
          pairs: [],
          missing,
          aligned_symbols: usable.length,
          aligned_bars: usable.length > 0 ? 240 : 0,
          reason:
            "Insufficient aligned history to scan — need at least two symbols with ~60+ overlapping daily bars after the inner-join.",
        },
        400
      );
    }

    const usableSet = new Set(usable);
    const pairs = mockPairs().pairs.filter(
      (p) => usableSet.has(p.ticker1) && usableSet.has(p.ticker2)
    );
    return delay(
      {
        pairs,
        missing,
        aligned_symbols: usable.length,
        aligned_bars: 240,
        reason:
          pairs.length > 0
            ? null
            : "No cointegrated pairs found for this universe at the selected p-value with a 5–60 day half-life.",
      },
      500
    );
  },

  async recomputeOptions(req: OptionsRecomputeRequest): Promise<OptionsRecomputeResult> {
    const requested = Array.from(
      new Set(req.symbols.map((s) => s.trim().toUpperCase()).filter(Boolean))
    );
    const directives: OptionsDirective[] = [];
    const errors: string[] = [];
    for (const sym of requested) {
      const existing = OPTIONS_BY_SYMBOL[sym];
      if (existing) {
        directives.push(existing);
        if (existing.Strategy == null) {
          errors.push(`${sym}: insufficient bars to compute directive`);
        }
        continue;
      }
      // Unknown symbol (not one of the 5 pre-baked fixture rows) -- synthesize
      // a plausible directive so the recompute form works for any ticker, not
      // just the fixed matrix. Deterministic per-symbol seed, not random.
      const rng = seeded([...sym].reduce((a, c) => a + c.charCodeAt(0), 0));
      const price = +(40 + rng() * 350).toFixed(2);
      const sigma = +(0.15 + rng() * 0.35).toFixed(3);
      const ivrProxy = +(rng() * 100).toFixed(1);
      const bullish = rng() > 0.45;
      const sellRegime = ivrProxy > (req.ivr_sell_threshold ?? 50);
      const strategy = sellRegime
        ? bullish
          ? "Put Credit Spread"
          : "Iron Condor"
        : bullish
          ? "Call Debit Spread"
          : "Cash";
      const action = strategy === "Cash" ? "Wait" : sellRegime ? "Sell to Open" : "Buy to Open";
      const netPremium = strategy === "Cash" ? 0 : +((sellRegime ? 1 : -1) * (0.3 + rng()) * 2).toFixed(2);
      directives.push({
        Symbol: sym,
        Price: price,
        Stale: false,
        Strategy: strategy,
        Action: action,
        Trend_Bias: bullish ? "Bullish" : "Bearish",
        Sigma_GARCH: sigma,
        IVR_Proxy: ivrProxy,
        Aroon_Oscillator: +((rng() - 0.5) * 100).toFixed(1),
        Coppock_Curve: +((rng() - 0.5) * 20).toFixed(1),
        Net_Premium: netPremium,
        Realizable_Daily_Theta: strategy === "Cash" ? 0 : +(netPremium * 0.03).toFixed(3),
        ATM_Delta: +(0.4 + rng() * 0.2).toFixed(3),
        ATM_Gamma: +(rng() * 0.05).toFixed(4),
        ATM_Vega: +(rng() * 0.15).toFixed(3),
        ATM_Theta_Daily: +(-rng() * 0.05).toFixed(3),
        Short_Strike: strategy === "Cash" ? null : +(price * (sellRegime ? 0.97 : 1.03)).toFixed(2),
        Long_Strike: strategy === "Cash" ? null : +(price * (sellRegime ? 0.94 : 1.06)).toFixed(2),
        Short_Delta: strategy === "Cash" ? null : +(sellRegime ? -0.3 : 0.3).toFixed(2),
        Long_Delta: strategy === "Cash" ? null : +(sellRegime ? -0.15 : 0.15).toFixed(2),
        Legs: [],
        Integrity_OK: true,
        Integrity_Issues: [],
      });
    }
    return delay(
      {
        directives,
        errors,
        vix: 15.2,
        market_regime: "RISK ON",
        target_dte: req.target_dte ?? 30,
      },
      600
    );
  },

  async getObservabilitySummary(
    range: PerfRange,
    horizon = 30
  ): Promise<ObservabilitySummary> {
    return delay(mockObservabilitySummary(range, horizon));
  },

  async getObservabilityLogs(limit = 300): Promise<LogAggregation> {
    return delay(
      readObservabilityColdStart()
        ? mockEmptyLogAggregation("No log file yet at logs/investyo.log.")
        : mockObservabilityLogs(limit)
    );
  },

  async putMacroGate(enabled: boolean, _reason: string): Promise<MacroGateUpdateResult> {
    writeMacroGateEnabled(enabled);
    return delay(
      {
        written: ["MACRO_REGIME_GATE_ENABLED"],
        enabled,
        applies: "next_daemon_restart",
        note:
          "Written to .env. settings is not patched in-process — this API " +
          "and any already-launched pipeline still use the previous value " +
          "until restarted.",
      },
      150
    );
  },

  async getStrategyMatrix(): Promise<StrategyMatrix> {
    return delay(mockStrategyMatrix());
  },

  async getStrategyHealth(): Promise<StrategyHealthRow[]> {
    return delay(STRATEGY_HEALTH_ROWS);
  },

  async getValidationTrend(): Promise<ValidationTrendSnapshot> {
    return delay(VALIDATION_TREND_SNAPSHOT);
  },

  async getGravityAuditStatus(): Promise<GravityAuditStatus> {
    return delay(GRAVITY_AUDIT_STATUS_MOCK);
  },

  // ---- Recommendation Tracking & Calibration ----
  // Honest fixture: exercises EVERY null/empty branch the screen must handle —
  // an under-min calibration bin (win_rate: null), an incomplete rec-tracking
  // row (model/actual_return null, trade_id null), an MFE/MAE point with a
  // null edge_ratio, and a decision journal entry with an unlinked trade
  // (trade_id: null). None of these are fabricated defaults (CONSTRAINT #4).
  async getCalibrationSummary(horizon = 30): Promise<CalibrationSummary> {
    return delay<CalibrationSummary>({
      calibration: {
        bins: [
          {
            bin_low: 0.4, bin_high: 0.5, bin_center: 0.45, conviction_mean: 0.46,
            win_rate: 0.42, count: 12, perfect_calibration: 0.45,
          },
          {
            bin_low: 0.5, bin_high: 0.6, bin_center: 0.55, conviction_mean: 0.55,
            win_rate: 0.58, count: 18, perfect_calibration: 0.55,
          },
          {
            bin_low: 0.6, bin_high: 0.7, bin_center: 0.65, conviction_mean: 0.66,
            win_rate: 0.71, count: 9, perfect_calibration: 0.65,
          },
          {
            // under min_trades_per_bin -> win_rate null (insufficient data)
            bin_low: 0.9, bin_high: 1.0, bin_center: 0.95, conviction_mean: 0.95,
            win_rate: null, count: 2, perfect_calibration: 0.95,
          },
        ],
        total: 41,
        // count-weighted over the 3 scored bins
        overall_win_rate: (0.42 * 12 + 0.58 * 18 + 0.71 * 9) / 39,
        // mean(|0.42-0.45|, |0.58-0.55|, |0.71-0.65|) = 0.04
        calibration_error: (0.03 + 0.03 + 0.06) / 3,
        n_scored_bins: 3,
        n_bins: 10,
        min_trades_per_bin: 5,
        reason: null,
      },
      recommendation_tracking: {
        horizon_days: horizon,
        model_return: 0.041,
        operator_return: 0.028,
        delta: -0.013,
        n_signals: 3,
        n_acted: 1,
        n_completed: 2,
        n_with_exit: 1,
        rows: [
          {
            symbol: "AAPL", signal_ts: "2026-06-20T14:00:00Z", signal_action: "BUY",
            conviction: 0.72, action_taken: "acted", model_return: 0.055,
            actual_return: 0.028, days_held: 14, trade_id: 42, completed: true,
          },
          {
            symbol: "MSFT", signal_ts: "2026-06-22T14:00:00Z", signal_action: "STRONG BUY",
            conviction: 0.81, action_taken: "passed", model_return: 0.031,
            actual_return: null, days_held: null, trade_id: null, completed: true,
          },
          {
            // horizon not elapsed -> model_return null, not completed
            symbol: "NVDA", signal_ts: "2026-07-15T14:00:00Z", signal_action: "BUY",
            conviction: 0.66, action_taken: "passed", model_return: null,
            actual_return: null, days_held: null, trade_id: null, completed: false,
          },
        ],
        reason: null,
      },
      mfe_mae: {
        points: [
          { symbol: "AAPL", mfe: 0.082, mae: 0.031, edge_ratio: 2.65, conviction: 0.72, action: "BUY" },
          { symbol: "MSFT", mfe: 0.054, mae: 0.048, edge_ratio: 1.13, conviction: 0.81, action: "HOLD" },
          // honest null edge_ratio (MAE was 0 -> undefined ratio, not fabricated)
          { symbol: "XOM", mfe: 0.026, mae: 0.061, edge_ratio: null, conviction: null, action: "SELL" },
        ],
        reason: null,
      },
      recent_decisions: {
        decisions: [
          {
            symbol: "AAPL", action_taken: "acted", signal_action: "BUY", conviction: 0.72,
            notes: "took full size", timestamp: "2026-07-16T15:12:00Z",
            signal_ts: "2026-06-20T14:00:00Z", trade_id: 42,
          },
          {
            // unlinked: no trade matched within 24h -> trade_id null, never fabricated
            symbol: "MSFT", action_taken: "passed", signal_action: "STRONG BUY", conviction: 0.81,
            notes: "", timestamp: "2026-07-15T09:03:00Z",
            signal_ts: "2026-06-22T14:00:00Z", trade_id: null,
          },
        ],
        reason: null,
      },
    });
  },

  async getEdgeByStrategy(): Promise<EdgeByStrategy> {
    return delay<EdgeByStrategy>({
      rows: [
        {
          strategy: "trend-following", n_trades: 8, mean_edge_ratio: 2.31,
          median_edge_ratio: 2.05, mean_mfe: 0.074, mean_mae: 0.033,
        },
        {
          strategy: "dip-buyer", n_trades: 5, mean_edge_ratio: 1.42,
          median_edge_ratio: 1.28, mean_mfe: 0.051, mean_mae: 0.041,
        },
        {
          strategy: "(untagged)", n_trades: 3, mean_edge_ratio: 0.88,
          median_edge_ratio: 0.9, mean_mfe: 0.029, mean_mae: 0.036,
        },
      ],
      reason: null,
    });
  },

  async logDecision(body: DecisionCreateRequest): Promise<DecisionCreateResult> {
    // Mock trade-link resolution: only an "acted" AAPL decision matches a
    // (mock) trade within 24h -> trade_id set, trade_linked true. Every other
    // case is honestly unlinked (trade_id null) — exercising BOTH render paths
    // ("linked to trade #N" vs "no trade match within 24h").
    const linked = body.action_taken === "acted" && body.symbol.toUpperCase() === "AAPL";
    const entry = {
      symbol: body.symbol.toUpperCase(),
      action_taken: body.action_taken,
      signal_action: body.signal_action,
      conviction: body.conviction,
      notes: body.notes,
      timestamp: new Date().toISOString(),
      signal_ts: body.signal_ts ?? "",
      trade_id: linked ? 42 : null,
    };
    MOCK_DECISION_LOG.unshift(entry);
    return delay<DecisionCreateResult>({ ...entry, trade_linked: linked }, 150);
  },

  async getDecisions(opts?: { symbol?: string; limit?: number }): Promise<DecisionEntry[]> {
    let rows = MOCK_DECISION_LOG;
    if (opts?.symbol) {
      const sym = opts.symbol.toUpperCase();
      rows = rows.filter((r) => r.symbol === sym);
    }
    return delay(rows.slice(0, opts?.limit ?? 20));
  },

  async getCommands(): Promise<CommandManifest> {
    return delay(MOCK_COMMAND_MANIFEST);
  },

  async getExecutionQueue(params?: ExecutionQueueParams): Promise<ExecutionQueue> {
    let items = MOCK_EXECUTION_QUEUE.intents;
    if (params) {
      if (params.action && params.action !== "ALL") {
        items = items.filter((i) => i.action.toUpperCase() === params.action?.toUpperCase());
      }
      if (params.follow_type && params.follow_type !== "ALL") {
        items = items.filter((i) => i.follow_type?.toLowerCase() === params.follow_type?.toLowerCase());
      }
      if (params.status_filter && params.status_filter !== "ALL") {
        if (params.status_filter === "Ready") {
          items = items.filter((i) => i.allow_place);
        } else if (params.status_filter === "Blocked") {
          items = items.filter((i) => !i.allow_place);
        }
      }
      if (params.min_conviction !== undefined && params.min_conviction > 0) {
        items = items.filter((i) => i.conviction !== null && i.conviction >= (params.min_conviction ?? 0));
      }
    }
    const available_follow_types = Array.from(
      new Set(
        MOCK_EXECUTION_QUEUE.intents
          .map((i) => i.follow_type)
          .filter((v): v is string => Boolean(v))
      )
    ).sort();
    return delay({
      ...MOCK_EXECUTION_QUEUE,
      n_intents: items.length,
      n_placeable: items.filter((i) => i.allow_place).length,
      intents: items,
      available_follow_types,
    });
  },

  async setStrategyModules(
    body: StrategyModulesUpdate
  ): Promise<StrategyModulesUpdateResult> {
    // Persist so a subsequent GET reflects the change, and set the drift marker
    // (the .env write does not reach the "running process" until restart).
    try {
      localStorage.setItem(
        STRATEGY_KEY,
        JSON.stringify({ weights: body.weights, disabled: body.disabled })
      );
      localStorage.setItem(STRATEGY_DRIFT_KEY, "1");
    } catch {
      /* ignore quota */
    }
    return delay({
      written: ["SIGNAL_WEIGHTS", "DISABLED_SIGNAL_MODULES"],
      configured_weights: body.weights,
      disabled: [...body.disabled].sort(),
      applies: "next_daemon_restart",
      note:
        "Written to .env. settings is not patched in-process — this API, the " +
        "running daemon, and any already-launched pipeline still use the " +
        "previous values until restarted.",
    });
  },

  async getTunables(): Promise<TunablesResponse> {
    return delay(mockTunables());
  },

  async updateTunables(
    values: Record<string, number | boolean | string>
  ): Promise<TunablesUpdateResult> {
    return delay(applyTunables(values));
  },

  async getSentimentSettings(): Promise<TunablesResponse> {
    return delay(mockSentimentTunables());
  },

  async updateSentimentSettings(
    values: Record<string, number | boolean | string>
  ): Promise<TunablesUpdateResult> {
    return delay(applySentimentTunables(values));
  },

  async getSectorSelectionSettings(): Promise<TunablesResponse> {
    return delay(mockSectorSelectionTunables());
  },

  async updateSectorSelectionSettings(
    values: Record<string, number | boolean | string>
  ): Promise<TunablesUpdateResult> {
    return delay(applySectorSelectionTunables(values));
  },

  // ---- Phase-4 Data Explorer / Signal Breakdown / Forecast Viewer ----
  // "ZZZZ" is the honest cold-start / no-coverage fixture symbol across all
  // three: [] bars, 404 fundamentals/forecast, all-null signal breakdown.
  async getDataBars(symbol: string, lookbackDays = 252): Promise<Bar[]> {
    if (symbol.toUpperCase() === "ZZZZ") return delay([]); // empty-state branch
    const n = Math.min(lookbackDays, 120);
    const rng = seeded(symbol.length * 7 + 13);
    const bars: Bar[] = [];
    let close = 100 + symbol.charCodeAt(0);
    const start = Date.now() - n * 86_400_000;
    for (let i = 0; i < n; i++) {
      close = Math.max(1, close * (1 + (rng() - 0.48) * 0.03));
      const open = close * (1 + (rng() - 0.5) * 0.01);
      const high = Math.max(open, close) * (1 + rng() * 0.01);
      const low = Math.min(open, close) * (1 - rng() * 0.01);
      bars.push({
        date: new Date(start + i * 86_400_000).toISOString().slice(0, 10),
        Open: round2(open),
        High: round2(high),
        Low: round2(low),
        Close: round2(close),
        // one honest null-volume row so the table exercises "—", not "0"
        Volume: i === n - 1 ? null : Math.round(1e6 + rng() * 5e6),
      });
    }
    return delay(bars);
  },

  async getDataFundamentals(symbol: string): Promise<Fundamentals> {
    if (symbol.toUpperCase() === "ZZZZ") throw notFoundSymbol(symbol); // 404 branch
    return delay<Fundamentals>({
      shortName: `${symbol.toUpperCase()} Mock Corp`,
      sector: "Technology",
      trailingPE: 24.6,
      priceToBook: 7.1,
      returnOnEquity: 0.34,
      dividendYield: 0.0057,
      debtToEquity: 152.0,
      trailingEps: 6.42,
      // honest null: this symbol's provider didn't compute a payout ratio
      payoutRatio: null,
    });
  },

  async getMacro(): Promise<MacroSnapshot> {
    return delay<MacroSnapshot>({
      VIXCLS: 17.3,
      T10Y2Y: -0.38,
      sahm_rule: 0.13,
      high_yield_oas: 3.42,
      // honest null: FRED hadn't published today's real yield yet
      real_yield_10y: null,
    });
  },

  async getMacroHistory(series = "VIXCLS", lookbackDays = 180): Promise<MacroHistorySeries> {
    const seriesId = series.trim().toUpperCase();
    // macro_history has been backfilled for much longer than news_history
    // (the sentiment archive only started 2026-07 -- see getSentimentHistory
    // below), so a full 180-day VIX series is an honest fixture, not an
    // overstatement of real coverage.
    const rng = seeded(seriesId.length * 11 + lookbackDays);
    const days = Math.min(lookbackDays, 180);
    const points: MacroHistorySeries["points"] = [];
    let vix = 16.5;
    const now = Date.now();
    for (let i = days; i >= 0; i--) {
      vix += (rng() - 0.5) * 1.4 + (16.5 - vix) * 0.06; // mean-reverting walk
      vix = Math.max(9, vix);
      const date = new Date(now - i * 86_400_000).toISOString().slice(0, 10);
      // One honest gap day (FRED hadn't published yet / market holiday) —
      // never a carried-forward or fabricated value.
      const gap = i === 3;
      points.push({ date, value: gap ? null : +vix.toFixed(2) });
    }
    return delay<MacroHistorySeries>({ series_id: seriesId, points, reason: null });
  },

  // Mirrors api/data_api.py::get_quotes's real per-symbol dead-letter
  // contract exactly: a symbol the provider can't resolve is simply OMITTED
  // from the response dict, never a fabricated placeholder row (CONSTRAINT
  // #4). "V" is the fixed honesty-fixture symbol for the "unreachable"
  // branch -- it's a real, always-present member of SYMBOL_UNIVERSE (a
  // PORTFOLIO position), so MarketDataHealth's tracked-universe check always
  // exercises it. Every OTHER symbol resolves, alternating realtime
  // (Alpaca, fresh) vs. delayed (yfinance, `is_stale: true` by design -- see
  // CLAUDE.md's Market-data layer note) by a deterministic hash so at least
  // one stale row is always present too, never an all-green fixture.
  async getDataQuotes(symbols: string[]): Promise<QuotesResponse> {
    const out: QuotesResponse = {};
    for (const raw of symbols) {
      const sym = raw.trim().toUpperCase();
      if (!sym || sym === "V") continue; // dead-lettered: provider fetch failed
      const rng = seeded(sym.charCodeAt(0) * 31 + sym.length * 7);
      const delayed = sym.charCodeAt(0) % 2 === 1; // odd leading char -> yfinance (delayed feed)
      const base = 40 + (sym.charCodeAt(sym.length - 1) % 40) * 5;
      const price = +(base + rng() * 20).toFixed(2);
      out[sym] = {
        symbol: sym,
        price,
        bid: +(price - 0.05).toFixed(2),
        ask: +(price + 0.05).toFixed(2),
        timestamp: new Date(Date.now() - (delayed ? 15 * 60_000 : 2_000)).toISOString(),
        is_stale: delayed,
        source: delayed ? "yfinance" : "alpaca",
      };
    }
    // Realistic per-call timing variance: deterministic per the first
    // requested symbol (so a test asserting on a specific symbol's latency
    // bucket is reproducible) rather than the module's flat 260ms default --
    // the whole point of this fixture is to exercise the client's own
    // performance.now() measurement with a genuinely varying number.
    const primary = symbols[0]?.trim().toUpperCase() ?? "";
    const jitter = seeded(primary.length * 17 + 3);
    const ms = 40 + Math.round(jitter() * 220);
    return delay(out, ms);
  },

  async getRecommendations(limit = 25): Promise<RecommendationsResponse> {
    // Ranked BUY picks, conviction-descending. The last row is the honest-null
    // fixture (no conviction/score/price/buy_range/sector) so the UI's "—" path
    // is exercised, never a fabricated 0 (CONSTRAINT #4).
    const all: Recommendation[] = [
      { symbol: "NVDA", action: "STRONG BUY", conviction: 0.88, score: 118.4, buy_range: "Buy Zone: $118.00 - $126.00", sector: "Information Technology", price: 128.72 },
      { symbol: "AAPL", action: "BUY", conviction: 0.72, score: 96.8, buy_range: "Buy Zone: $210.00 - $222.00", sector: "Information Technology", price: 224.15 },
      { symbol: "JPM", action: "BUY", conviction: 0.64, score: 78.9, buy_range: "Buy Zone: $196.00 - $203.00", sector: "Financials", price: 205.6 },
      { symbol: "XOM", action: "BUY", conviction: 0.58, score: 71.2, buy_range: "Buy Zone: $106.00 - $111.00", sector: "Energy", price: 112.4 },
      { symbol: "ZZ", action: "BUY", conviction: null, score: null, buy_range: null, sector: null, price: null },
    ];
    const recommendations = all.slice(0, Math.max(1, Math.min(limit, 200)));
    return delay<RecommendationsResponse>({
      recommendations,
      count: recommendations.length,
      as_of: "2026-07-11T21:05:00+00:00",
      reason: recommendations.length ? null : "No BUY-rated recommendations in the latest snapshot yet.",
    });
  },

  async getDataUniverse(): Promise<UniverseListResponse> {
    return delay<UniverseListResponse>({
      symbols: [...MOCK_DATA_UNIVERSE],
      count: MOCK_DATA_UNIVERSE.length,
    });
  },

  async updateDataUniverse(symbols: string[]): Promise<{ status: string; symbols: string[] }> {
    // Mirror the backend PUT: strip/upper/dedupe, then replace the whole list.
    const cleaned = Array.from(
      new Set(symbols.map((s) => s.trim().toUpperCase()).filter(Boolean))
    );
    MOCK_DATA_UNIVERSE = cleaned;
    return delay({ status: "updated", symbols: [...cleaned] });
  },

  async getSignalBreakdown(symbol: string): Promise<SignalBreakdown> {
    const s = symbol.toUpperCase();
    if (s === "ZZZZ") {
      // cold-start honesty: no bars → all-null, empty modules (never fabricated)
      return delay<SignalBreakdown>({
        symbol: s,
        action: null,
        conviction: null,
        final_score: null,
        modules: [],
      });
    }
    const modules: SignalModuleScore[] = [
      { name: "timeseries_momentum", score: 0.62, weight: 20, contribution: 12.4 },
      { name: "cross_sectional_momentum", score: 0.31, weight: 15, contribution: 4.65 },
      { name: "multifactor", score: -0.18, weight: 15, contribution: -2.7 },
      { name: "macd_momentum", score: 0.44, weight: 12, contribution: 5.28 },
      // honest null: this module didn't run for the symbol this cycle
      { name: "rsi2_mean_reversion", score: null, weight: 10, contribution: null },
    ];
    return delay<SignalBreakdown>({
      symbol: s,
      action: "BUY",
      conviction: 0.58,
      final_score: 20,
      modules,
    });
  },

  async getSignalImportance(symbols: string[]): Promise<SignalImportance> {
    // Deterministic per the request's symbol set so the fixture is stable
    // across re-renders, but varies if the caller's universe changes.
    const requested = symbols.map((s) => s.trim().toUpperCase()).filter(Boolean);
    const rng = seeded(requested.reduce((a, s) => a + s.length, requested.length * 13));
    const names = [
      "timeseries_momentum",
      "cross_sectional_momentum",
      "multifactor",
      "macd_momentum",
      "rsi2_mean_reversion",
      // Honest empty row: a module that scored 0 of the requested symbols
      // this batch (e.g. news_catalyst with no FINNHUB_API_KEY configured) —
      // never a fabricated 0, and never silently absent from the list.
      "news_catalyst",
    ];
    const rows: SignalImportanceRow[] = names.map((name) => {
      if (name === "news_catalyst") {
        return { name, mean_abs_contribution: null, n_symbols_scored: 0 };
      }
      return {
        name,
        mean_abs_contribution: +(rng() * 8).toFixed(2),
        n_symbols_scored: Math.max(1, requested.length - Math.floor(rng() * 2)),
      };
    });
    rows.sort((a, b) => (b.mean_abs_contribution ?? -1) - (a.mean_abs_contribution ?? -1));
    return delay<SignalImportance>({
      rows,
      n_symbols_requested: Math.min(requested.length, 25),
      n_symbols_scored: requested.length > 0 ? Math.max(1, requested.length - 1) : 0,
    });
  },

  async getSentimentDynamics(symbol: string): Promise<SentimentDynamics> {
    // Illustrative "available" example (this repo's USE_MOCK convention) —
    // the real endpoint can also return source: "unavailable" with all
    // three agent-derived fields null; see SentimentDynamics.test.tsx.
    return delay<SentimentDynamics>({
      ticker: symbol.toUpperCase(),
      date: new Date().toISOString(),
      sentiment_score: 0.15,
      sentiment_intensity: 0.72,
      credibility_score: 0.85,
      volatility_persistence: 0.94,
      source: "antigravity_agent",
    });
  },

  async getSentimentHistory(symbol: string, _lookbackDays = 180): Promise<SentimentHistory> {
    const sym = symbol.toUpperCase();
    if (!SYMBOL_UNIVERSE.has(sym)) {
      return delay<SentimentHistory>({
        symbol: sym,
        points: [],
        reason: `No archived sentiment history for ${sym} yet.`,
      });
    }
    // HONEST fixture depth: news_history is a forward-archive that only
    // started 2026-07 (see HistoricalStore's DDL comment and
    // pilots/catalog.py) -- real coverage today is a few weeks at most, not
    // the full lookback window a caller might request. Mocking a full
    // 180-day series here would misrepresent production reality and hide
    // the honest "not enough data for a lead-lag claim yet" UI path this
    // chart exists to exercise.
    const rng = seeded(sym.length * 7 + 3);
    const daysArchived = 18 + Math.floor(rng() * 8); // ~18-25 archived days
    const points: SentimentHistory["points"] = [];
    const now = Date.now();
    for (let i = daysArchived; i >= 0; i--) {
      const date = new Date(now - i * 86_400_000).toISOString().slice(0, 10);
      // A handful of honest gap days (a real fetch failure or zero
      // headlines that day) -- never a fabricated neutral 0.
      const gap = i === 5 || i === 11;
      const score = gap ? null : +((rng() - 0.45) * 0.9).toFixed(3);
      points.push({ date, score });
    }
    return delay<SentimentHistory>({ symbol: sym, points, reason: null });
  },

  async getForecastResult(symbol: string): Promise<ForecastResult> {
    if (symbol.toUpperCase() === "ZZZZ") throw notFoundSymbol(symbol); // 404 branch
    const sym = symbol.toUpperCase();
    const base = 100 + symbol.charCodeAt(0);
    const mid10 = base * 1.01;
    const mid30 = base * 1.03;
    const mid60 = base * 1.05;
    // HONEST fixture: BERT_LLA_ENABLED defaults False in production, so
    // `attention` is null for every symbol EXCEPT one deliberately-
    // populated case (AAPL) -- lets the heatmap overlay be visually
    // verified/tested without misrepresenting the actual default state.
    const attention: ForecastAttention | null =
      sym === "AAPL" ? mockBertLlaAttention(sym) : null;
    return delay<ForecastResult>({
      Forecast_10: round2(mid10),
      Forecast_30: round2(mid30),
      Forecast_60: round2(mid60),
      // honest null: the h=90 fit didn't converge this run
      Forecast_90: null,
      ARIMA: round2(base * 1.028),
      MC_Lower: round2(base * 0.94),
      MC_Upper: round2(base * 1.12),
      // Confidence bands WIDEN with horizon (±2% @10d → ±5% @30d → ±8% @60d)
      // so the cone visibly fans out. h=90 has no band (its mid is null).
      Forecast_10_Lower: round2(mid10 * 0.98),
      Forecast_10_Upper: round2(mid10 * 1.02),
      Forecast_30_Lower: round2(mid30 * 0.95),
      Forecast_30_Upper: round2(mid30 * 1.05),
      Forecast_60_Lower: round2(mid60 * 0.92),
      Forecast_60_Upper: round2(mid60 * 1.08),
      // null horizon → null band (never a fabricated 0 — CONSTRAINT #4)
      Forecast_90_Lower: null,
      Forecast_90_Upper: null,
      attention,
    });
  },

  // ---- Agentic Trading tab ----
  async getAgenticStatus(): Promise<AgenticStatus> {
    const activeFollows = readFollows().filter((f) => f.status === "active");
    return delay({
      mode: MOCK_EXECUTION_QUEUE.mode,
      advisory_only: false,
      kill_switch: readKillSwitch(),
      queue: {
        mode: MOCK_EXECUTION_QUEUE.mode,
        generated_at: MOCK_EXECUTION_QUEUE.generated_at,
        n_intents: MOCK_EXECUTION_QUEUE.n_intents,
        n_placeable: MOCK_EXECUTION_QUEUE.n_placeable,
        stale: MOCK_EXECUTION_QUEUE.stale,
        age_seconds: MOCK_EXECUTION_QUEUE.age_seconds,
      },
      follows: {
        n_active: activeFollows.length,
        total_amount: activeFollows.reduce((sum, f) => sum + f.amount, 0),
      },
      agent_loop: MOCK_AGENT_LOOP,
    });
  },

  async getAgenticDiscovery(): Promise<AgenticDiscovery> {
    const configs = readScanConfigs();
    // Always writable in the mock (matches mockStrategyMatrix's convention
    // above) so the demo can exercise the write flow with zero config.
    const writable = true;
    const note = "Scan configs are saved immediately and take effect on the agentic-discovery skill's next run.";
    if (!configs.some((c) => c.enabled)) {
      return delay({
        generated_at: null,
        candidates: [],
        scan_configs: configs,
        reason:
          "No scan candidates yet, and no scan configs are enabled. Add a scan config, then run the agentic-discovery skill.",
        writable,
        note,
      });
    }
    return delay({
      generated_at: new Date(Date.now() - 3_600_000).toISOString(),
      candidates: MOCK_DISCOVERY_CANDIDATES,
      scan_configs: configs,
      reason: null,
      writable,
      note,
    });
  },

  async putScanConfig(req: ScanConfigRequest): Promise<ScanConfigResult> {
    const now = new Date().toISOString();
    const configs = readScanConfigs();
    const idx = configs.findIndex((c) => c.name === req.name);
    const row: ScanConfig = {
      name: req.name,
      filters: req.filters,
      enabled: req.enabled,
      created_at: idx >= 0 ? configs[idx].created_at : now,
      updated_at: now,
    };
    const next = idx >= 0 ? configs.map((c, i) => (i === idx ? row : c)) : [...configs, row];
    writeScanConfigs(next);
    return delay(
      {
        scan_config: row,
        applies: "next_discovery_run",
        note:
          "Saved to output/scan_configs.json. Takes effect the next time the agentic-discovery skill runs a scan — it is not applied automatically.",
      },
      150
    );
  },

  async watchCandidate(symbol: string): Promise<WatchResult> {
    const sym = (symbol ?? "").trim().toUpperCase();
    // Mirror the writer's strict validation → 422 invalid_symbol (thrown
    // synchronously, like getEquityFundamentals' bad-input branch above).
    if (!MOCK_SYMBOL_RE.test(sym)) {
      throw new ApiError(`invalid_symbol: '${symbol}' is not a valid ticker symbol.`, 422);
    }
    const watched = readWatched();
    const already = watched.includes(sym);
    if (!already) writeWatched([...watched, sym]);
    return delay(
      {
        symbol: sym,
        added: already ? [] : [sym],
        already_present: already ? [sym] : [],
        watchlist_file: "watchlist.txt",
        applies: "next_pipeline_run",
        note: already
          ? `${sym} is already on the watchlist.`
          : "Added to watchlist.txt — the pipeline will evaluate it on the next run. No order was placed.",
      },
      150
    );
  },

  async createJob(job_type: string, params?: Record<string, unknown>): Promise<JobRecord> {
    // job_type === "command" mirrors the backend's two HIGH_STAKES_COMMANDS
    // gates (see commandParse.ts) so the frontend can exercise the full
    // confirm/error flow offline, plus the app_shell.py hard-disallow.
    if (job_type === "command") {
      const command = typeof params?.command === "string" ? params.command : "";
      const args = Array.isArray(params?.args) ? (params.args as unknown[]) : [];
      const confirmed = params?.confirm === true;

      if (command === "app_shell.py") {
        throw new ApiError("app_shell.py cannot be executed remotely.", 400);
      }
      if (command === "execution.kill_switch" && (args.includes("--activate") || args.includes("--deactivate")) && !confirmed) {
        throw new ApiError("confirmation required: this command activates/deactivates the global kill switch.", 400);
      }
      if (command === "main.py" && args.includes("--refresh-account") && !confirmed) {
        throw new ApiError("confirmation required: this command forces a fresh Robinhood login.", 400);
      }
    }

    const job_id = `mock-job-${Object.keys(_mockJobs).length + 1}`;
    const commandName = job_type === "command" && typeof params?.command === "string" ? params.command : null;
    const createdAt = new Date().toISOString();
    _mockJobs[job_id] = { jobType: job_type, commandName, startedAt: Date.now(), createdAt, cancelled: false };
    return delay({
      job_id,
      job_type: job_type as any,
      status: "running",
      cancellable: job_type !== "orchestrator",
      command_name: commandName,
      created_at: createdAt,
    }, 150);
  },

  async getJobStatus(job_id: string): Promise<JobRecord> {
    const job = _mockJobs[job_id];
    // A believable "running for a couple seconds, then done" lifecycle, so
    // Console.tsx's status-polling loop has something real to demonstrate
    // even against the mock backend rather than reporting terminal on the
    // very first poll.
    const cancellable = job ? job.jobType !== "orchestrator" : true;
    const status = !job
      ? "success"
      : job.cancelled
        ? "cancelled"
        : Date.now() - job.startedAt < 2000
          ? "running"
          : "success";
    return delay({
      job_id,
      job_type: (job?.jobType ?? "preflight") as any,
      status,
      exit_code: status === "running" ? null : status === "cancelled" ? -15 : 0,
      is_running: status === "running",
      cancellable,
      command_name: job?.commandName ?? null,
      created_at: job?.createdAt ?? new Date().toISOString(),
    }, 100);
  },

  async cancelJob(job_id: string): Promise<{ job_id: string; cancelled: boolean }> {
    const job = _mockJobs[job_id];
    if (job) job.cancelled = true;
    return delay({ job_id, cancelled: true }, 100);
  },

  async restartDaemon(): Promise<RestartDaemonResult> {
    return delay(
      {
        restarting: true,
        message: "(mock) Process exiting in ~0.5s. No real process was restarted.",
      },
      150
    );
  },

  // ---- G15: durable per-symbol Claude-vs-Gemini disagreement ----
  async getAiDisagreements(): Promise<AiDisagreementsResponse> {
    return delay(mockAiDisagreements());
  },

  // ---- Report Library (G5) + Dead-Letter Queue (G6) ----
  async getReports(): Promise<ReportManifest> {
    return delay(MOCK_REPORT_MANIFEST);
  },

  async getReport(name: string): Promise<ReportContent> {
    const found = MOCK_REPORT_CONTENT[name];
    if (!found) {
      throw new ApiError(`No report named '${name}'.`, 404);
    }
    return delay(found);
  },

  async getDeadLetter(): Promise<DeadLetterQueue> {
    return delay(MOCK_DEAD_LETTER);
  },

  async retryDeadLetter(symbol: string): Promise<DeadLetterRetryResult> {
    const sym = symbol.trim().toUpperCase();
    return delay(
      {
        symbol: sym,
        pid: 51234,
        log_path: `output/gui_retry.log`,
        applies: "immediately",
        note: `(mock) Retry launched for ${sym} (advisory-only — no orders placed).`,
      },
      200
    );
  },

  // ---- Prompt Registry (webapp parity gap G4) ----
  async getPrompts(): Promise<PromptListResponse> {
    const prompts: PromptEntry[] = Object.keys(_MOCK_PROMPT_FIXTURES)
      .sort()
      .map((id) => {
        const fx = _MOCK_PROMPT_FIXTURES[id];
        const pinned = _MOCK_PROMPT_PINS[id] ?? null;
        return {
          id,
          resolved_version: pinned ?? fx.unpinnedVersion,
          source: pinned ? "pin" : fx.unpinnedSource,
          pinned_version: pinned,
          cached_version_count: fx.cachedVersions.length,
        };
      });
    return delay<PromptListResponse>({
      enabled: MOCK_PROMPT_REGISTRY_ENABLED,
      prompts,
      reason: null,
      writable: MOCK_PROMPT_REGISTRY_WRITABLE,
      note: MOCK_PROMPT_REGISTRY_WRITABLE
        ? "Pins persist to .env and apply on the next daemon restart."
        : "Pin writes are disabled (PROMPT_REGISTRY_WRITES_ENABLED=false).",
    });
  },

  async getPrompt(id: string, version?: string): Promise<PromptBody> {
    const fx = _MOCK_PROMPT_FIXTURES[id];
    if (!fx) {
      return delay<PromptBody>({
        id,
        version: version ?? null,
        found: false,
        body: null,
        source: null,
        reason: `No body available for '${id}' in the registry, cache, or committed baseline.`,
        cached_versions: [],
        has_baseline: false,
      });
    }
    // Every fixture id here has a real committed baseline file (they mirror
    // prompt_registry/baseline/*.md's exact set) -- true for every entry,
    // never fabricated for an id that wouldn't actually have one.
    const has_baseline = true;
    if (version) {
      const known = version === "baseline" || fx.cachedVersions.includes(version);
      if (!known) {
        return delay<PromptBody>({
          id,
          version,
          found: false,
          body: null,
          source: null,
          reason: `Version '${version}' of '${id}' not found in the manifest, disk cache, or committed baseline.`,
          cached_versions: fx.cachedVersions,
          has_baseline,
        });
      }
      // A specific-version lookup does not re-derive provenance (matches the
      // real endpoint's contract — source is only populated for the
      // full-resolution-chain lookup below).
      return delay<PromptBody>({
        id, version, found: true, body: fx.body, source: null, reason: null,
        cached_versions: fx.cachedVersions, has_baseline,
      });
    }
    const pinned = _MOCK_PROMPT_PINS[id] ?? null;
    return delay<PromptBody>({
      id,
      version: pinned ?? fx.unpinnedVersion,
      found: true,
      body: fx.body,
      source: pinned ? "pin" : fx.unpinnedSource,
      reason: null,
      cached_versions: fx.cachedVersions,
      has_baseline,
    });
  },

  async putPromptPin(req: PromptPinRequest): Promise<PromptPinResult> {
    const id = req.prompt_id.trim();
    if (!id) throw new ApiError("prompt_id must not be empty.", 422);

    if (req.version === null) {
      delete _MOCK_PROMPT_PINS[id];
    } else {
      const fx = _MOCK_PROMPT_FIXTURES[id];
      const known = Boolean(fx) && (req.version === "baseline" || fx.cachedVersions.includes(req.version));
      if (!known) {
        throw new ApiError(
          `Version '${req.version}' of '${id}' not found in the manifest, disk cache, or committed baseline.`,
          422
        );
      }
      _MOCK_PROMPT_PINS[id] = req.version;
    }

    return delay<PromptPinResult>(
      {
        prompt_id: id,
        version: req.version,
        pins: { ..._MOCK_PROMPT_PINS },
        applies: "next_daemon_restart",
        note:
          req.version === null
            ? `Pin cleared for '${id}'. Saved to .env; effective on next daemon restart.`
            : `Pinned '${id}' -> '${req.version}'. Saved to .env; effective on next daemon restart.`,
      },
      150
    );
  },

  // ---- Universe sync write (webapp parity gap G8) ----
  async postDataSync(): Promise<DataSyncResult> {
    // Reuses the SAME sync-report fixture data getSyncReport() returns, so a
    // "Sync Now" click in the mock renders a believable, internally
    // consistent report rather than a second, drifted fixture. Safe to call
    // as `mockApi.getSyncReport()` here: by the time any mockApi method is
    // actually invoked the object literal below has fully constructed, so
    // this sibling reference resolves normally (not a TDZ hazard — only the
    // *definition*, not the *call*, happens during object construction).
    const report = await mockApi.getSyncReport();
    const default_tickers = Object.keys(report.symbols).sort();
    return delay<DataSyncResult>(
      {
        report,
        default_tickers,
        applies: "next_daemon_restart",
        note: `(mock) Synced ${default_tickers.length} symbol(s). Submitted to DEFAULT_TICKERS in .env; effective on next daemon restart.`,
      },
      600
    );
  },

  // ---- Market Data provider status (webapp parity gap G9) ----
  async getProviderStatus(): Promise<ProviderStatus> {
    return delay<ProviderStatus>({
      provider: "alpaca",
      is_realtime: true,
      mode: "real_time",
      quote_ttl_seconds: 30,
      fundamentals_source: "yahoo_computed",
    });
  },

  // ---- Phase 6 additions ----
  // Mirrors the shape api/data_api.py::get_macro_sentiment actually returns
  // (VIX/Sahm/credit-spread/yield-curve/regime health scores) -- not the
  // old fictional CPI/PMI/Employment categories the live endpoint never
  // computed.
  async getMacroSentiment() {
    return delay({
      macro_data: [
        { subject: "VIX (Volatility)", value: 78, trend: "up" as const },
        { subject: "Sahm Rule (Recession Signal)", value: 92, trend: "flat" as const },
        { subject: "High-Yield OAS (Credit Stress)", value: 84, trend: "down" as const },
        { subject: "Yield Curve (10Y-2Y)", value: 61, trend: "flat" as const },
        { subject: "Market Regime", value: 100, trend: "flat" as const },
      ],
      is_synthetic: false,
      reason: null,
    });
  },
  async getOrderBookLadder(symbol: string) {
    const sym = symbol.toUpperCase();
    const current_price = sym === "SPY" ? 450.0 : 150.0;
    return delay({
      symbol: sym,
      current_price,
      bids: [
        { price: current_price - 0.05, size: 1200, type: "bid" as const },
        { price: current_price - 0.1, size: 850, type: "bid" as const },
        { price: current_price - 0.15, size: 2100, type: "bid" as const },
      ],
      asks: [
        { price: current_price + 0.05, size: 900, type: "ask" as const },
        { price: current_price + 0.1, size: 1500, type: "ask" as const },
        { price: current_price + 0.15, size: 600, type: "ask" as const },
      ],
      is_synthetic: true,
    });
  },
  async getModelComparison() {
    // Demo-only curve: "SF-GARCH-LSTM"/"Bond-BERT" are undeployed
    // ridge-regression stand-ins (ml/models/sf_garch_lstm.py,
    // ml/models/bond_bert.py) with no real tracked return history -- the
    // live endpoint honestly reports no data (see api/metrics_api.py), this
    // mock fixture exists only to populate the offline demo UI and is
    // flagged is_synthetic so the chart shows a Demo Data badge.
    return delay({
      data: [
        { name: "Jan", "SF-GARCH-LSTM": 2.1, "Bond-BERT": 1.8, "Benchmark (SPY)": 1.5 },
        { name: "Feb", "SF-GARCH-LSTM": 4.5, "Bond-BERT": 3.2, "Benchmark (SPY)": 3.0 },
        { name: "Mar", "SF-GARCH-LSTM": 3.8, "Bond-BERT": 4.0, "Benchmark (SPY)": 2.8 },
        { name: "Apr", "SF-GARCH-LSTM": 6.2, "Bond-BERT": 5.5, "Benchmark (SPY)": 4.2 },
        { name: "May", "SF-GARCH-LSTM": 8.0, "Bond-BERT": 6.8, "Benchmark (SPY)": 5.5 },
        { name: "Jun", "SF-GARCH-LSTM": 10.5, "Bond-BERT": 8.2, "Benchmark (SPY)": 6.1 },
      ],
      is_synthetic: true,
    });
  },
  async getOptionsAnalytics(symbol: string) {
    return delay({
      symbol: symbol.toUpperCase(),
      net_dealer_premium: -45.2,
      regime: "Negative Gamma (Volatile)",
      intraday_series: [
        { time: "9:00 AM", hour: 9, theta: 0.0, gamma: 3.68 },
        { time: "12:00 PM", hour: 12, theta: 12.5, gamma: 10.0 },
        { time: "4:00 PM", hour: 16, theta: 100.0, gamma: 73.89 },
      ],
      is_synthetic: true,
    });
  },
  async getForecastBackfill() {
    return delay(mockForecastBackfill());
  },
  async runForecastBackfill() {
    return delay({
      status: "success",
      summary: mockForecastBackfill(),
      sample_rows: 11080,
    });
  },
};

// ---------------------------------------------------------------------------
// Report Library (G5) + Dead-Letter Queue (G6) fixtures.
//
// Honesty branches covered here: an empty-with-content briefing/summary/html
// happy path, PLUS one manifest row (`corrupt_validation_summary.json`) whose
// listing succeeds (size/mtime present — the file existed when globbed) but
// whose CONTENT read fails (`json: null`, a `reason` string) — the same
// "matched the manifest, failed at read time" shape the real backend returns
// on a race or a malformed JSON file (CONSTRAINT #6, never a 500). A totally
// unknown name throws a 404 ApiError from `getReport` above, covering the
// "not in the manifest at all" branch. `ReportLibrary.test.tsx` additionally
// overrides `api.getReports`/`api.getReport` per-test for the cold-start
// (empty manifest) and hard-error branches, per this file's established
// per-test-override convention (see Commands.test.tsx).
// ---------------------------------------------------------------------------
const MOCK_REPORTS: ReportFile[] = [
  { name: "daily_report.html", kind: "daily_report", size: 48213, mtime: "2026-07-30T21:05:11+00:00" },
  { name: "daily_report_dashboard.html", kind: "dashboard", size: 1931842, mtime: "2026-07-30T06:02:47+00:00" },
  { name: "volatility_bands_dashboard.html", kind: "dashboard", size: 512340, mtime: "2026-07-30T06:02:51+00:00" },
  { name: "briefing_2026-07-30.md", kind: "briefing", size: 2104, mtime: "2026-07-30T12:00:03+00:00" },
  { name: "briefing_2026-07-29.md", kind: "briefing", size: 1987, mtime: "2026-07-29T12:00:04+00:00" },
  { name: "trend_following_validation_summary.json", kind: "validation_summary", size: 918, mtime: "2026-07-28T18:22:10+00:00" },
  { name: "validation_trend-following_20260728183012.html", kind: "validation_html", size: 76004, mtime: "2026-07-28T18:30:12+00:00" },
  // Honesty branch: listed successfully (stat succeeded) but unreadable/
  // malformed at content-read time -- see MOCK_REPORT_CONTENT below.
  { name: "corrupt_validation_summary.json", kind: "validation_summary", size: 41, mtime: "2026-07-27T09:10:00+00:00" },
];

const MOCK_REPORT_MANIFEST: ReportManifest = {
  generated_at: "2026-07-30T21:05:12+00:00",
  reports: MOCK_REPORTS,
  reason: null,
};

const MOCK_REPORT_CONTENT: Record<string, ReportContent> = {
  "daily_report.html": {
    name: "daily_report.html",
    kind: "daily_report",
    content_type: "html",
    text: "<html><body><h1>InvestYo Daily Report — 2026-07-30</h1><p>(mock content)</p></body></html>",
    json: null,
    size: 48213,
    mtime: "2026-07-30T21:05:11+00:00",
    reason: null,
  },
  "daily_report_dashboard.html": {
    name: "daily_report_dashboard.html",
    kind: "dashboard",
    content_type: "html",
    text: "<html><body><h1>Orchestrator Dashboard (mock, real file is ~1.9MB)</h1></body></html>",
    json: null,
    size: 1931842,
    mtime: "2026-07-30T06:02:47+00:00",
    reason: null,
  },
  "volatility_bands_dashboard.html": {
    name: "volatility_bands_dashboard.html",
    kind: "dashboard",
    content_type: "html",
    text: "<html><body><h1>Volatility Bands Dashboard (mock)</h1></body></html>",
    json: null,
    size: 512340,
    mtime: "2026-07-30T06:02:51+00:00",
    reason: null,
  },
  "briefing_2026-07-30.md": {
    name: "briefing_2026-07-30.md",
    kind: "briefing",
    content_type: "markdown",
    text: "# Daily Briefing — 2026-07-30\n\n## Portfolio\n- 3 positions held, 0 dead-lettered symbols.\n\n## Signals\n- NVDA: BUY, conviction 0.71\n- AAPL: HOLD\n",
    json: null,
    size: 2104,
    mtime: "2026-07-30T12:00:03+00:00",
    reason: null,
  },
  "briefing_2026-07-29.md": {
    name: "briefing_2026-07-29.md",
    kind: "briefing",
    content_type: "markdown",
    text: "# Daily Briefing — 2026-07-29\n\n## Portfolio\n- 3 positions held, 1 dead-lettered symbol (ZZZZ, strategy stage).\n",
    json: null,
    size: 1987,
    mtime: "2026-07-29T12:00:04+00:00",
    reason: null,
  },
  "trend_following_validation_summary.json": {
    name: "trend_following_validation_summary.json",
    kind: "validation_summary",
    content_type: "json",
    text: null,
    json: {
      strategy_id: "timeseries_momentum",
      deployable: true,
      pbo: 0.18,
      dsr: 0.972,
      sharpe: 1.14,
      max_drawdown: 0.176,
      report_date: "2026-07-28",
    },
    size: 918,
    mtime: "2026-07-28T18:22:10+00:00",
    reason: null,
  },
  "validation_trend-following_20260728183012.html": {
    name: "validation_trend-following_20260728183012.html",
    kind: "validation_html",
    content_type: "html",
    text: "<html><body><h1>Validation Report — timeseries_momentum (mock)</h1></body></html>",
    json: null,
    size: 76004,
    mtime: "2026-07-28T18:30:12+00:00",
    reason: null,
  },
  "corrupt_validation_summary.json": {
    name: "corrupt_validation_summary.json",
    kind: "validation_summary",
    content_type: "json",
    text: null,
    json: null,
    size: 41,
    mtime: "2026-07-27T09:10:00+00:00",
    reason: "Could not parse corrupt_validation_summary.json.",
  },
};

const MOCK_DEAD_LETTER_ENTRIES: DeadLetterQueueEntry[] = [
  {
    symbol: "ZZZZ",
    stage: "strategy",
    error: "ValueError: insufficient history for RSI(14)",
    timestamp: "2026-07-30T12:03:41+00:00",
  },
];

const MOCK_DEAD_LETTER: DeadLetterQueue = {
  run_id: "run-2026-07-30T12:00:00+00:00",
  generated_at: "2026-07-30T12:05:22+00:00",
  entries: MOCK_DEAD_LETTER_ENTRIES,
  is_clean: false,
  reason: null,
  retry_enabled: true,
};

// ---- G15: durable per-symbol Claude-vs-Gemini disagreement ----
// Mixed on purpose: one clear agreement (AAPL), one clear disagreement
// (NVDA), one Claude-only (MSFT -- gemini_verdict null, never fabricated),
// and one symbol with NEITHER side cached (DUK -- both verdicts null,
// disagreement false) so mock mode exercises every honesty branch, not just
// a wall-to-wall happy path.
function mockAiDisagreements(): AiDisagreementsResponse {
  const rows = [
    { symbol: "AAPL", advisory_action: "BUY", claude_verdict: "bullish", gemini_verdict: "bullish", disagreement: false },
    { symbol: "NVDA", advisory_action: "STRONG BUY", claude_verdict: "bullish", gemini_verdict: "bearish", disagreement: true },
    { symbol: "MSFT", advisory_action: "HOLD", claude_verdict: "neutral", gemini_verdict: null, disagreement: false },
    { symbol: "DUK", advisory_action: "SELL", claude_verdict: null, gemini_verdict: null, disagreement: false },
  ];
  const bothPresent = rows.filter((r) => r.claude_verdict !== null && r.gemini_verdict !== null).length;
  const disagreements = rows.filter((r) => r.disagreement).length;
  return {
    rows,
    summary: {
      total_symbols: rows.length,
      both_present: bothPresent,
      agreements: bothPresent - disagreements,
      disagreements,
    },
    reason: null,
  };
}

// The honest "no snapshot yet" degrade -- exported for the same reason as
// mockSizingCapAuditDisabled above (co-located test parity).
export function mockAiDisagreementsEmpty(): AiDisagreementsResponse {
  return {
    rows: [],
    summary: { total_symbols: 0, both_present: 0, agreements: 0, disagreements: 0 },
    reason: "No state snapshot yet — run the pipeline to populate the signal universe.",
  };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function notFound(id: string) {
  return new ApiError(`Pilot '${id}' not found (run the pipeline first).`, 404);
}

function notFoundSymbol(sym: string) {
  return new ApiError(`No such symbol '${sym}' in the latest snapshot.`, 404);
}

export function mockForecastBackfill(): ForecastBackfillSummary {
  return {
    status: "completed",
    timestamp: new Date().toISOString(),
    horizons: [10, 30, 60, 90],
    metrics: {
      TSMOM_10d: { accuracy: 0.5215, auc: 0.5420, n_train: 9480, n_test: 2370, split_date: "2024-01-15" },
      TSMOM_30d: { accuracy: 0.5340, auc: 0.5580, n_train: 9416, n_test: 2354, split_date: "2024-01-15" },
      TSMOM_60d: { accuracy: 0.5480, auc: 0.5720, n_train: 9320, n_test: 2330, split_date: "2024-01-15" },
      TSMOM_90d: { accuracy: 0.5620, auc: 0.5910, n_train: 9224, n_test: 2306, split_date: "2024-01-15" },
      CSMOM_10d: { accuracy: 0.5180, auc: 0.5310, n_train: 9480, n_test: 2370, split_date: "2024-01-15" },
      CSMOM_30d: { accuracy: 0.5410, auc: 0.5640, n_train: 9416, n_test: 2354, split_date: "2024-01-15" },
      CSMOM_60d: { accuracy: 0.5590, auc: 0.5830, n_train: 9320, n_test: 2330, split_date: "2024-01-15" },
      CSMOM_90d: { accuracy: 0.5740, auc: 0.6050, n_train: 9224, n_test: 2306, split_date: "2024-01-15" },
    },
    tickers: ["AAPL", "MSFT", "AMZN", "NVDA", "JPM", "JNJ", "XOM", "WMT"],
    total_rows: 11080,
    csv_path: "output/agentic_forecast_backfill.csv",
  };
}

export const MOCK_META = {
  mode: MOCK_MODE,
  notionalCap: NOTIONAL_CAP,
  minAmount: MIN_AMOUNT,
  sectors: SECTORS,
};


