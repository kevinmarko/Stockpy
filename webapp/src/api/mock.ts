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
  Follow,
  FollowResult,
  Headline,
  Holding,
  PerfRange,
  PerformanceResponse,
  PilotDetail,
  PilotSummary,
  PilotTrade,
  Portfolio,
  SectorSlice,
  SymbolDetail,
  SymbolHeldBy,
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
    id: "volatility-edge",
    name: "Volatility Edge",
    category: "Risk",
    description:
      "Times market exposure off a forward volatility forecast — leans in when risk is cheap and de-risks hard into turbulent, fat-tailed regimes.",
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

  async getHoldings(id: string): Promise<Holding[]> {
    const p = findPilot(id);
    if (!p) throw notFound(id);
    return delay(p.holdings);
  },

  async getTrades(id: string, limit = 20): Promise<PilotTrade[]> {
    const p = findPilot(id);
    if (!p) throw notFound(id);
    return delay(trades(p.holdings).slice(0, limit));
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
