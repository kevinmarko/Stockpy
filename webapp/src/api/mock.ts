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
  DiscoveryCandidate,
  ExecutionQueue,
  ScanConfig,
  ScanConfigRequest,
  ScanConfigResult,
  WatchResult,
  BrokerageConnectRequest,
  BrokerageConnectResult,
  BrokerageDisconnectResult,
  BrokerageStatus,
  CalibrationSummary,
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
  Headline,
  Holding,
  IntervalUpdateResult,
  ExecutionModeUpdateRequest,
  ExecutionModeUpdateResult,
  KillSwitchActionResult,
  LlmCapabilityRow,
  LlmProviderName,
  LlmProviderTelemetry,
  LlmSettingUpdateResult,
  LlmStatus,
  MacroGateUpdateResult,
  ModelRow,
  ObservabilitySummary,
  OptionsDirective,
  OptionsMatrix,
  PairsRadar,
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
  RiskGateBlockEntry,
  RiskGateBlockLog,
  RealizedTrade,
  RollingBeta,
  RunRecord,
  SectorSlice,
  StrategyHealthGate,
  StrategyHealthRow,
  StrategyHealthTrendPoint,
  StrategyMatrix,
  StrategyModulesUpdate,
  StrategyModulesUpdateResult,
  TunableField,
  TunableFieldType,
  TunablesResponse,
  TunablesUpdateResult,
  SymbolDetail,
  SymbolCompareRow,
  SymbolCompareResponse,
  UniverseResponse,
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
  MacroSnapshot,
  SignalBreakdown,
  SignalModuleScore,
  ForecastResult,
  SentimentDynamics,
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
const STRATEGY_BASE: { name: string; weight: number; pinned: boolean; scored: number }[] = [
  { name: "macro_regime", weight: 45, pinned: false, scored: 20 },
  { name: "macd_momentum", weight: 20, pinned: false, scored: 20 },
  { name: "aroon_trend", weight: 15, pinned: false, scored: 20 },
  { name: "graham_value", weight: 20, pinned: false, scored: 18 },
  { name: "dividend_quality", weight: 15, pinned: false, scored: 12 },
  { name: "multifactor", weight: 15, pinned: false, scored: 19 },
  { name: "cross_sectional_momentum", weight: 15, pinned: false, scored: 20 },
  { name: "regime_multiplier", weight: 0, pinned: true, scored: 20 },
];

function readStrategyOverrides(): { weights: Record<string, number>; disabled: string[] } | null {
  try {
    const raw = localStorage.getItem(STRATEGY_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
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
  };
}

// ---- General runtime tunables editor fixture (GET/PUT /settings/tunables) ----
// Mirrors api/pilots_api.py's REAL _TUNABLE_GROUPS exactly (same 7 group names,
// same ~39-key field set, including the 7 "Advanced / Config" keys the backend
// previously omitted) -- every field the mock's TUNABLE_DEFS below matches the
// live backend field-for-field, no orphans either direction. Values/defaults/
// descriptions are pulled from settings.py's real pydantic Field(description=)
// (verified via `python3 -c "from settings import Settings; ..."`), not
// invented placeholders -- 10 fields genuinely have no description in
// settings.py (RISK_FREE_RATE, MARKET_RISK_PREMIUM, REQUIRED_RETURN_RATE,
// MAX_PORTFOLIO_HEAT, KELLY_FRACTION, KELLY_CAP, VOL_TARGET, MAX_LEVERAGE,
// MAX_POSITION_WEIGHT, LOG_LEVEL) and stay `null` here, never fabricated
// (CONSTRAINT #4). MARKET_DATA_PROVIDER is honestly `value: null, default:
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
    value: null, default: null, options: ["alpaca", "yfinance"],
    description: "Force a specific market-data backend: 'alpaca' or 'yfinance'. When unset the platform auto-selects based on key availability.",
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
    value: "yahoo", default: "yahoo", options: ["yahoo", "yfinance_info"],
    description: "Primary fundamentals backend: 'yahoo' (statement-derived, default) or 'yfinance_info' (raw .info fallback). Finnhub is no longer a fundamentals source.",
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

function readTunableOverrides(): Record<string, number | boolean | string> {
  try {
    const raw = localStorage.getItem(TUNABLES_KEY);
    return raw ? (JSON.parse(raw) as Record<string, number | boolean | string>) : {};
  } catch {
    return {};
  }
}

function readTunablesDrift(): string[] {
  try {
    const raw = localStorage.getItem(TUNABLES_DRIFT_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function mockTunables(): TunablesResponse {
  const ov = readTunableOverrides();
  const groups: TunablesResponse["groups"] = [];
  for (const def of TUNABLE_DEFS) {
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
  const driftKeys = readTunablesDrift();
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

function applyTunables(
  values: Record<string, number | boolean | string>
): TunablesUpdateResult {
  const written: Record<string, number | boolean | string> = {};
  const rejected: Record<string, string> = {};
  const byKey = new Map(TUNABLE_DEFS.map((d) => [d.key, d]));
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
      localStorage.setItem(TUNABLES_KEY, JSON.stringify({ ...readTunableOverrides(), ...written }));
      // A .env write does NOT reach the running process until restart --
      // mark every written key as drifted (mirrors STRATEGY_DRIFT_KEY above).
      const drift = new Set([...readTunablesDrift(), ...Object.keys(written)]);
      localStorage.setItem(TUNABLES_DRIFT_KEY, JSON.stringify([...drift]));
    } catch {
      /* ignore quota */
    }
  }
  return { written, rejected, applies: "next_daemon_restart" };
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
      pending: 0,
      completed: 0,
      reason: "No forecast history yet — run the pipeline to accumulate it.",
    };
  }
  const rng = seeded([...sym].reduce((a, c) => a + c.charCodeAt(0), 0) + horizon);
  const models = ["arima", "monte_carlo", "holt_winters", "cnn_lstm"];
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
  return {
    symbol: sym,
    horizon_days: horizon,
    reliability_curve: curve,
    skill_weights,
    pending: Math.floor(rng() * 5),
    completed: Math.floor(rng() * 60) + 20,
    reason: null,
  };
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

// ---- ML registry fixture (honest: two un-validated / not-deployable) ----
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
  },
  {
    name: "meta_labeler_cross_sectional_momentum",
    role: "meta_labeler",
    trained_date: "2026-07-06",
    cpcv_dsr: null,
    pbo: null,
    n_train: 3460,
    deployable: false,
    notes: "Binary classifier predicting P(cross_sectional_momentum correct).",
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

function mockObservabilitySummary(range: PerfRange, horizon: number): ObservabilitySummary {
  return {
    portfolio_risk: mockPortfolioRisk(),
    portfolio_heat: mockPortfolioHeat(),
    equity_curve: mockEquityDrawdownCurve(range),
    regime: mockRegimeOverlay(),
    forecast_skill: mockPortfolioForecastSkill(horizon),
    risk_gate_blocks: mockRiskGateBlocks(),
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
 * actual output shape.
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
      rationale: "Advisory risk-reduce exit.",
      client_order_id: "advisory-TSLA-sell-1",
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
      // those two fields are ONLY ever populated by the advisory snapshot
      // writer, not the richer main_orchestrator one (see
      // pilots/symbols.py::compare_symbols' docstring); this fixture exercises
      // that honest-null branch instead of pretending every symbol always has
      // them.
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

  async getEquityCurve(range: PerfRange) {
    return delay({
      range,
      curve: synthCurve("account-equity", range, 0.1, 0.08, 44000),
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
              schedule: "0 6 * * 0",
              command:
                "cd /opt/investyo && .venv/bin/python scripts/backfill_edgar_fundamentals.py --tickers all >> /opt/investyo/logs/edgar_backfill.log 2>&1",
              comment: "Weekly: Full EDGAR backfill sweep (Sundays at 06:00 UTC / 2 AM ET)",
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

  async getControlRunStatus(runId: string): Promise<RunRecord> {
    const known = CONTROL_RUN_HISTORY.find((r) => r.run_id === runId);
    if (known) return delay(known, 80);
    // An unknown id is treated as a just-triggered run still in flight: honest
    // nulls for finished_at/duration (never fabricated) while it runs.
    return delay(
      {
        run_id: runId,
        state: "running",
        mode: "full",
        started_at: new Date(Date.now() - 4_000).toISOString(),
        finished_at: null,
        duration_seconds: null,
        error: null,
        reason: "manual",
        progress: null,
      },
      80
    );
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

  async getObservabilitySummary(
    range: PerfRange,
    horizon = 30
  ): Promise<ObservabilitySummary> {
    return delay(mockObservabilitySummary(range, horizon));
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

  async getExecutionQueue(): Promise<ExecutionQueue> {
    return delay(MOCK_EXECUTION_QUEUE);
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

  async getForecastResult(symbol: string): Promise<ForecastResult> {
    if (symbol.toUpperCase() === "ZZZZ") throw notFoundSymbol(symbol); // 404 branch
    const base = 100 + symbol.charCodeAt(0);
    const mid10 = base * 1.01;
    const mid30 = base * 1.03;
    const mid60 = base * 1.05;
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
};

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function notFound(id: string) {
  return new ApiError(`Pilot '${id}' not found (run the pipeline first).`, 404);
}

function notFoundSymbol(sym: string) {
  return new ApiError(`No such symbol '${sym}' in the latest snapshot.`, 404);
}

export const MOCK_META = {
  mode: MOCK_MODE,
  notionalCap: NOTIONAL_CAP,
  minAmount: MIN_AMOUNT,
  sectors: SECTORS,
};
