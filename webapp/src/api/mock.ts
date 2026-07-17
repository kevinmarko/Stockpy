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
  AlertsFeed,
  AutomationSchedule,
  AutomationStatus,
  BrokerageConnectRequest,
  BrokerageConnectResult,
  BrokerageDisconnectResult,
  BrokerageStatus,
  CorrelationCluster,
  EquityDrawdownCurve,
  EquityDrawdownPoint,
  FactorExposure,
  Follow,
  FollowResult,
  ForecastSkill,
  Headline,
  Holding,
  IntervalUpdateResult,
  KillSwitchActionResult,
  LlmStatus,
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
  PortfolioRiskMetrics,
  RealizedPerformance,
  RegimeOverlay,
  RiskGateBlockEntry,
  RiskGateBlockLog,
  RealizedTrade,
  RollingBeta,
  SectorSlice,
  StrategyMatrix,
  StrategyModulesUpdate,
  StrategyModulesUpdateResult,
  SymbolDetail,
  SymbolHeldBy,
  SymbolOptions,
  TriggerRunResult,
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
    macro_regime_gate_enabled: true,
    reason: null,
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

function mockObservabilitySummary(range: PerfRange, horizon: number): ObservabilitySummary {
  return {
    portfolio_risk: mockPortfolioRisk(),
    equity_curve: mockEquityDrawdownCurve(range),
    regime: mockRegimeOverlay(),
    forecast_skill: mockPortfolioForecastSkill(horizon),
    risk_gate_blocks: mockRiskGateBlocks(),
  };
}

async function delay<T>(v: T, ms = 260): Promise<T> {
  return new Promise((res) => setTimeout(() => res(v), ms));
}

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
    // models the real out-of-box state and keeps App.test.tsx dot-free.
    const noCall = (provider: "claude" | "gemini" | "openai") => ({
      provider,
      ok: null,
      error_kind: null,
      exception_type: null,
      http_status: null,
      checked_at: null,
      age_seconds: null,
      source: "none" as const,
    });
    const disabledRow = (
      key: string,
      label: string,
      trigger: "on_demand" | "scheduled",
      toggle_key: string,
      provider_keys: string[],
      active_provider: "claude" | "gemini" | "openai" | null
    ) => ({
      key,
      label,
      trigger,
      toggle_key,
      provider_keys,
      active_provider,
      invalid_provider: null,
      enabled: false,
      key_present: false,
      built: true,
      status: "disabled" as const,
    });
    return delay(
      {
        capabilities: [
          disabledRow(
            "claude_commentary",
            "Analyst rationale commentary",
            "on_demand",
            "LLM_COMMENTARY_ENABLED",
            ["ANTHROPIC_API_KEY"],
            "claude"
          ),
          disabledRow(
            "gemini_alerts",
            "Alert commentary",
            "scheduled",
            "LLM_COMMENTARY_ENABLED",
            ["GEMINI_API_KEY"],
            "gemini"
          ),
          disabledRow(
            "gemini_vision",
            "Gemini chart vision",
            "on_demand",
            "LLM_COMMENTARY_ENABLED",
            ["GEMINI_API_KEY"],
            null
          ),
          disabledRow(
            "gravity_ai_runner",
            "Gravity AI runner (Claude + Gemini)",
            "on_demand",
            "GRAVITY_AI_RUNNER_ENABLED",
            ["ANTHROPIC_API_KEY", "GEMINI_API_KEY"],
            null
          ),
          disabledRow(
            "opal_research",
            "Opal research agent",
            "on_demand",
            "OPAL_RESEARCH_ENABLED",
            ["OPENAI_API_KEY"],
            "openai"
          ),
        ],
        capabilities_source: "gui.ai_control_center.control_center_overview",
        providers: {
          claude: noCall("claude"),
          gemini: noCall("gemini"),
          openai: noCall("openai"),
        },
        providers_source: "llm.status_store.read_all",
        telemetry_note:
          "Verdicts are recorded from REAL LLM calls only — this platform never " +
          "probes a provider to test a key. A null last-call record means no LLM " +
          "call has been made with the current key, which is the EXPECTED state " +
          "when LLM commentary is off by default — it does NOT mean the key is broken.",
        attention: false,
        attention_reason: null,
      },
      80
    );
  },

  async connectBrokerage(
    creds: BrokerageConnectRequest
  ): Promise<BrokerageConnectResult> {
    // Simulated verification only — the mock never contacts a real broker and
    // never persists the credential strings themselves, only a boolean marker.
    const verified = Boolean(
      creds.username.trim() && creds.password.trim() && creds.mfa_secret.trim()
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

  async getPairs(): Promise<PairsRadar> {
    return delay(mockPairs());
  },

  async getObservabilitySummary(
    range: PerfRange,
    horizon = 30
  ): Promise<ObservabilitySummary> {
    return delay(mockObservabilitySummary(range, horizon));
  },

  async getStrategyMatrix(): Promise<StrategyMatrix> {
    return delay(mockStrategyMatrix());
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
};

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
