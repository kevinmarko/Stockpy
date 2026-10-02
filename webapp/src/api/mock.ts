/**
 * mock.ts — realistic offline fixtures for every endpoint in api/pilots_api.py.
 *
 * Lets the whole PWA run with VITE_USE_MOCK=true and no backend. Data mirrors
 * the Pilot catalog in the plan (Phase 1) and is deliberately HONEST:
 *  - `momentum-burst` is NOT deployable (fails a validation gate) and renders so.
 *  - `value-quality` has curve:null ("no backtest series yet"), never a fake line.
 */

import { ApiError, ForecastBackfillConflictError, JobConflictError, JobsListResponse } from "./types";
import type { StrategyReportCardSnapshot,
  RetrospectiveTradeRecord,
  BatchRetrospectiveInsightsResponse,
  BridgeReliabilityResponse,
  AgenticDiscovery,
  AgenticStatus,
  AgentLoopStatus,
  AiChartResponse,
  AiCommentaryResponse,
  AiModelsResponse,
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
  BrokerageDisconnectResult,
  BrokerageLoginCancelResult,
  BrokerageLoginJob,
  BrokerageLoginPhase,
  BrokerageRefreshResult,
  BrokerageStatus,
  CalibrationSummary,
  CircuitBreakerSummary,
  CircuitBreakerTrip,
  ControlStatus,
  CronStatus,
  CorrelationCluster,
  DecisionCreateRequest,
  DecisionCreateResult,
  DecisionEntry,
  EdgeByStrategy,
  EquityDrawdownCurve,
  EquityDrawdownPoint,
  ExplainTickerResponse,
  FactorExposure,
  ForecastSkill,
  ForecastBackfillSummary,
  ForecastBackfillJob,
  ForecastBackfillPhase,
  ForecastSkillBySymbol,
  ForecastSkillSymbolRow,
  LatencyHeatmap,
  LatencySample,
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
  PerfRange,
  PerformanceResponse,
  PilotDetail,
  PilotSimulationRequest,
  PilotSimulationResult,
  PilotSummary,
  PilotTrade,
  NewsCoverage,
  Portfolio,
  PortfolioAttribution,
  PortfolioForecastSkill,
  PortfolioHeatMetric,
  PortfolioRiskMetrics,
  RadarFeedResponse,
  RealizedPerformance,
  TradeHistoryPage,
  RegimeOverlay,
  RestartDaemonResult,
  RiskGateBlockEntry,
  RiskGateBlockLog,
  RlhfProposal,
  RlhfKpis,
  RlhfSummary,
  RlhfReviewSubmitRequest,
  RlhfReviewSubmitResult,
  RlhfSftExportResult,
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
  TunableLiveness,
  TunablesResponse,
  TunablesUpdateResult,
  SettingsReferenceResponse,
  SettingsReferenceField,
  AppliesState,
  AppliesSummary,
  SettingsConfirmMap,
  SymbolDetail,
  SymbolCompareRow,
  SymbolCompareResponse,
  UniverseResponse,
  SymbolSearchResponse,
  ScreenerFilters,
  ScreenerResult,
  ScreenerResultsResponse,
  ScreenerFilterOptions,
  SymbolBackfillResult,
  SyncReportResponse,
  SyncReportSymbol,
  SymbolReincludeResult,
  RecommendationsResponse,
  Recommendation,
  UniverseListResponse,
  UniverseSymbol,
  Thresholds,
  SymbolHeldBy,
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
  PaperBrokerAccount,
  PaperBrokerPosition,
  PaperBrokerOrder,
  PaperBrokerClosedTrade,
  LiveTradeProposal,
  EquityOrderRequest,
  EquityOrderResult,
  DigestPayload,
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

// Dated FMP sector P/E + 1-day-change snapshot fixture, keyed by the same
// sector names as SECTOR_OF -- mirrors data/historical_store.py's
// get_sector_snapshots() shape (fraction change_pct, not a percent number).
// "Utilities" is deliberately OMITTED so DUK exercises the honest "sector
// has no snapshot" null branch, matching this fixture's existing convention
// of using DUK for other honest-null cases (see getSymbolsCompare below).
const SECTOR_SNAPSHOT: Record<string, { pe: number; change_pct: number }> = {
  Technology: { pe: 31.4, change_pct: 0.0087 },
  Communication: { pe: 22.1, change_pct: -0.0032 },
  "Consumer Disc.": { pe: 26.8, change_pct: 0.0015 },
  Financials: { pe: 14.9, change_pct: 0.0041 },
  Healthcare: { pe: 19.3, change_pct: -0.0011 },
  Energy: { pe: 11.6, change_pct: -0.0128 },
  Industrials: { pe: 20.5, change_pct: 0.0023 },
};

function h(
  sharpe: number | null,
  dsr: number | null,
  pbo: number | null,
  dd: number | null,
  deployable: boolean,
  stress = true,
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
  symbols: [string, number, number, (number | null)?][], // [symbol, weight(raw), score, meta_label_composite_override?]
): Holding[] {
  const total = symbols.reduce((s, [, w]) => s + w, 0);

  // Deterministic BUY/HOLD split: the top 1-2 scored holdings in this
  // Pilot's list are the "BUY" conviction picks, everything else "HOLD" --
  // no Math.random() so mock data is stable across renders/reloads.
  const byScoreDesc = [...symbols].sort((a, b) => b[2] - a[2]);
  const buyCount = Math.min(2, byScoreDesc.length);
  const buySymbols = new Set(
    byScoreDesc.slice(0, buyCount).map(([symbol]) => symbol),
  );
  const maxScore = byScoreDesc[0]?.[2] ?? 0;
  const minScore = byScoreDesc[byScoreDesc.length - 1]?.[2] ?? 0;
  const scoreSpread = maxScore - minScore || 1;

  return symbols.map(([symbol, w, score, metaOverride]) => {
    const price = +(50 + Math.random() * 400).toFixed(2);
    const isBuy = buySymbols.has(symbol);
    // Normalize this holding's score within its Pilot's own score range,
    // then map onto a conviction band: BUY picks sit higher (0.75-0.90),
    // the rest lower (0.50-0.65) -- higher score => more conviction.
    const normalized = (score - minScore) / scoreSpread;
    const conviction = isBuy
      ? +(0.75 + normalized * 0.15).toFixed(2)
      : +(0.5 + normalized * 0.15).toFixed(2);
    // Deterministic meta-label composite default, mirroring conviction's
    // normalized-score derivation -- no Math.random(). `metaOverride ===
    // undefined` means the tuple omitted the 4th slot (use the derived
    // default); an explicit `null` override (trend-following's LMT) is
    // passed through unchanged to exercise the honest "not computed" render
    // path -- never fabricate a value where the real API would say null.
    const metaLabelComposite =
      metaOverride !== undefined
        ? metaOverride
        : +(0.55 + normalized * 0.35).toFixed(3);

    const buyLow = price * 0.94;
    const buyHigh = price * 0.98;
    const sellLow = price * 1.05;
    const sellHigh = price * 1.15;
    const stop = price * 0.9;

    return {
      symbol,
      name: NAMES[symbol] ?? symbol,
      sector: SECTOR_OF[symbol] ?? "Other",
      weight: +(w / total).toFixed(4),
      score,
      price,
      action: isBuy ? "BUY" : "HOLD",
      buy_range: `Buy Zone: $${buyLow.toFixed(2)} - $${buyHigh.toFixed(2)}`,
      sell_range: `Sell Zone: $${sellLow.toFixed(2)} - $${sellHigh.toFixed(2)} | Stop @ $${stop.toFixed(2)}`,
      conviction,
      meta_label_composite: metaLabelComposite,
    };
  });
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
  hasCurve: boolean;
  drift: number;
  vol: number;
  syms: [string, number, number, (number | null)?][];
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
    hasCurve: true,
    drift: 0.14,
    vol: 0.13,
    syms: [
      ["NVDA", 30, 0.82],
      ["MSFT", 24, 0.61],
      ["AAPL", 20, 0.48],
      ["CAT", 14, 0.4],
      // Explicit null exercises the honest "not computed this cycle" render
      // path for meta_label_composite (never fabricate a fallback value).
      ["LMT", 12, 0.33, null],
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
    top_holdings: hs.slice(0, 3),
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

/**
 * GET /pilots/{id}'s `news_coverage` — realistic non-null coverage only for
 * the one Pilot whose strategy actually weights `news_catalyst`
 * ("news-catalyst"); `null` for every other Pilot, matching the real
 * backend's generic, not-special-cased treatment (a Pilot whose strategy
 * doesn't use the news-catalyst signal genuinely has no coverage to report).
 */
function newsCoverageFor(id: string): NewsCoverage | null {
  if (id !== "news-catalyst") return null;
  return {
    archived_score_count: 47,
    headline_volume_7d: 9,
    universe_score_distribution: {
      positive: 0.41,
      neutral: 0.38,
      negative: 0.21,
    },
  };
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

// Symbol Screener fixture universe -- small, deterministic, NOT the tracked
// pipeline universe (this is the whole point: browse/filter/trade symbols
// independent of it). Mirrors data.fmp_screener's reshaped field names
// exactly (snake_case, matching ScreenerResult). One row ("DELISTEDCO") is
// deliberately isActivelyTrading:false to exercise that filter; two rows
// ("NODIVCO", "QQQ") deliberately carry null fields -- never an
// all-populated happy-path-only fixture.
const SCREENER_UNIVERSE: ScreenerResult[] = [
  { symbol: "AAPL", company_name: "Apple Inc.", sector: "Technology", industry: "Consumer Electronics", market_cap: 3_400_000_000_000, price: 227.5, beta: 1.09, last_annual_dividend: 1.0, volume: 54_000_000, exchange: "NASDAQ", exchange_short_name: "NASDAQ", country: "US", is_etf: false, is_fund: false, is_actively_trading: true },
  { symbol: "MSFT", company_name: "Microsoft Corporation", sector: "Technology", industry: "Software - Infrastructure", market_cap: 3_100_000_000_000, price: 415.2, beta: 0.9, last_annual_dividend: 3.0, volume: 20_000_000, exchange: "NASDAQ", exchange_short_name: "NASDAQ", country: "US", is_etf: false, is_fund: false, is_actively_trading: true },
  { symbol: "NVDA", company_name: "NVIDIA Corporation", sector: "Technology", industry: "Semiconductors", market_cap: 2_900_000_000_000, price: 118.1, beta: 1.68, last_annual_dividend: 0.04, volume: 250_000_000, exchange: "NASDAQ", exchange_short_name: "NASDAQ", country: "US", is_etf: false, is_fund: false, is_actively_trading: true },
  { symbol: "JNJ", company_name: "Johnson & Johnson", sector: "Healthcare", industry: "Drug Manufacturers - General", market_cap: 380_000_000_000, price: 158.4, beta: 0.5, last_annual_dividend: 4.8, volume: 6_500_000, exchange: "NYSE", exchange_short_name: "NYSE", country: "US", is_etf: false, is_fund: false, is_actively_trading: true },
  { symbol: "UNH", company_name: "UnitedHealth Group Inc.", sector: "Healthcare", industry: "Healthcare Plans", market_cap: 460_000_000_000, price: 505.3, beta: 0.6, last_annual_dividend: 8.4, volume: 3_100_000, exchange: "NYSE", exchange_short_name: "NYSE", country: "US", is_etf: false, is_fund: false, is_actively_trading: true },
  { symbol: "JPM", company_name: "JPMorgan Chase & Co.", sector: "Financial Services", industry: "Banks - Diversified", market_cap: 620_000_000_000, price: 215.6, beta: 1.1, last_annual_dividend: 4.6, volume: 8_200_000, exchange: "NYSE", exchange_short_name: "NYSE", country: "US", is_etf: false, is_fund: false, is_actively_trading: true },
  { symbol: "XOM", company_name: "Exxon Mobil Corporation", sector: "Energy", industry: "Oil & Gas Integrated", market_cap: 490_000_000_000, price: 112.8, beta: 0.85, last_annual_dividend: 3.8, volume: 15_000_000, exchange: "NYSE", exchange_short_name: "NYSE", country: "US", is_etf: false, is_fund: false, is_actively_trading: true },
  { symbol: "KO", company_name: "The Coca-Cola Company", sector: "Consumer Defensive", industry: "Beverages - Non-Alcoholic", market_cap: 280_000_000_000, price: 65.2, beta: 0.55, last_annual_dividend: 1.94, volume: 12_000_000, exchange: "NYSE", exchange_short_name: "NYSE", country: "US", is_etf: false, is_fund: false, is_actively_trading: true },
  { symbol: "NODIVCO", company_name: "NoDiv Growth Corp.", sector: "Technology", industry: "Software - Application", market_cap: 8_500_000_000, price: 42.1, beta: 1.9, last_annual_dividend: null, volume: 2_100_000, exchange: "NASDAQ", exchange_short_name: "NASDAQ", country: "US", is_etf: false, is_fund: false, is_actively_trading: true },
  { symbol: "DELISTEDCO", company_name: "Formerly Traded Inc.", sector: "Technology", industry: "Software - Infrastructure", market_cap: 1_200_000_000, price: 3.1, beta: 2.4, last_annual_dividend: null, volume: 0, exchange: "OTC", exchange_short_name: "OTC", country: "US", is_etf: false, is_fund: false, is_actively_trading: false },
  { symbol: "QQQ", company_name: "Invesco QQQ Trust", sector: null, industry: null, market_cap: null, price: 505.6, beta: 1.15, last_annual_dividend: 2.5, volume: 40_000_000, exchange: "NASDAQ", exchange_short_name: "NASDAQ", country: "US", is_etf: true, is_fund: false, is_actively_trading: true },
];

function matchesScreenerFilters(row: ScreenerResult, f: ScreenerFilters): boolean {
  if (f.sector && row.sector !== f.sector) return false;
  if (f.industry && row.industry !== f.industry) return false;
  if (f.marketCapMoreThan != null && (row.market_cap == null || row.market_cap < f.marketCapMoreThan)) return false;
  if (f.marketCapLowerThan != null && (row.market_cap == null || row.market_cap > f.marketCapLowerThan)) return false;
  if (f.priceMoreThan != null && (row.price == null || row.price < f.priceMoreThan)) return false;
  if (f.priceLowerThan != null && (row.price == null || row.price > f.priceLowerThan)) return false;
  if (f.betaMoreThan != null && (row.beta == null || row.beta < f.betaMoreThan)) return false;
  if (f.betaLowerThan != null && (row.beta == null || row.beta > f.betaLowerThan)) return false;
  if (f.dividendMoreThan != null && (row.last_annual_dividend == null || row.last_annual_dividend < f.dividendMoreThan)) return false;
  if (f.dividendLowerThan != null && (row.last_annual_dividend == null || row.last_annual_dividend > f.dividendLowerThan)) return false;
  if (f.volumeMoreThan != null && (row.volume == null || row.volume < f.volumeMoreThan)) return false;
  if (f.exchange && row.exchange !== f.exchange) return false;
  if (f.country && row.country !== f.country) return false;
  if (f.isActivelyTrading != null && row.is_actively_trading !== f.isActivelyTrading) return false;
  if (f.excludeFunds && (row.is_etf || row.is_fund)) return false;
  return true;
}

function synthCurve(
  id: string,
  range: PerfRange,
  drift: number,
  vol: number,
  base = 100,
) {
  const days = RANGE_DAYS[range];
  const step = days > 200 ? Math.ceil(days / 120) : 1;
  const rng = seeded([...id].reduce((a, c) => a + c.charCodeAt(0), 0) + days);
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

// Mock stand-in for a `watchlist.txt`/`WATCHLIST` env watchlist -- deliberately
// NARROWER than MOCK_DATA_UNIVERSE so the mock demonstrates, honestly, the
// exact "DEFAULT_TICKERS is configured but is not the effective per-cycle
// universe" case that `docs/known_issues/universe_count_reporting_mismatch.md`
// documents: `data.portfolio_sync.compute_tracked_universe()` uses
// DEFAULT_TICKERS only as a fallback when watchlist/discovery are both empty.
const MOCK_ACTIVE_WATCHLIST: string[] = ["AAPL", "MSFT"];

// ---- Mock symbol-rating state (rating.symbol_rating_store.SymbolRatingStore) ----
// A module-level mutable map — same pattern as MOCK_DATA_UNIVERSE above — so
// reincludeSymbol() behaves like a real read-modify-write within a session:
// calling it clears the symbol's entry, and the next getSyncReport() call
// honestly reflects that (rating_excluded: false, cycles reset to 0) instead
// of a static fixture the UI interaction can never actually change.
// Seeded with a realistic, MOSTLY-un-excluded spread. Only non-held symbols
// can legitimately be excluded (mirrors SymbolRatingStore.get_excluded_symbols'
// "never exclude a held symbol" rule) -- of getSyncReport's two non-held
// fixture rows (T, XOM), only XOM is over the default drop threshold (5); T
// has some bad cycles but not enough yet. Every held symbol (AAPL, MSFT,
// NVDA, ...) deliberately has NO entry here -- undefined, rendered as a dash
// by the UI, not a fabricated 0 -- since most of this codebase's rating
// history in practice belongs to non-held, screened-and-rejected candidates.
let MOCK_RATING_OVERRIDES: Record<
  string,
  { consecutive_bad_cycles: number; excluded: boolean }
> = {
  XOM: { consecutive_bad_cycles: 6, excluded: true },
  T: { consecutive_bad_cycles: 2, excluded: false },
};

/** Exposed for tests: reset the mock rating overrides between cases. */
export function __resetMockRatingOverrides() {
  MOCK_RATING_OVERRIDES = {
    XOM: { consecutive_bad_cycles: 6, excluded: true },
    T: { consecutive_bad_cycles: 2, excluded: false },
  };
}

const MOCK_MODE = "review" as const; // paper-first: nothing is ever placed



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

// ---- Local login-job simulation (async device-approval push login) --
// mirrors the POST /brokerage/{connect,refresh} -> poll GET
// /brokerage/login/status/{job_id} contract: no synchronous verify, no
// mfa_code, a believable "running through a few phases, then done" lifecycle
// driven purely off elapsed wall-clock time since the job started (works
// the same whether that's real time or vi.useFakeTimers()' fake clock). No
// dedicated UI control for forcing the timeout branch, same "flip it from
// devtools" convention as the other markers in this file:
//   localStorage.setItem("stockpy.mock.brokerage_login_timeout", "1")  // next login job times out instead of succeeding
//   localStorage.removeItem("stockpy.mock.brokerage_login_timeout")   // back to the happy path
const BROKERAGE_LOGIN_TIMEOUT_KEY = "stockpy.mock.brokerage_login_timeout";

function readBrokerageLoginTimeout(): boolean {
  try {
    return localStorage.getItem(BROKERAGE_LOGIN_TIMEOUT_KEY) === "1";
  } catch {
    return false;
  }
}

// Matches the real backend's default login deadline (see the API contract).
const BROKERAGE_LOGIN_DEADLINE_SECONDS = 180;

interface _MockLoginJob {
  mode: "connect" | "refresh";
  startedAt: number; // Date.now() at creation -- elapsed time drives phase/state below
  cancelled: boolean;
  simulateTimeout: boolean;
  // Refresh-only: true when the job started with nothing to refresh (mirrors
  // the honest "no usable .env credentials" failure the old synchronous
  // refreshBrokerage() used to throw for). Connect always has typed
  // credentials by the time the form's submit button is enabled, so this is
  // never set for a "connect" job.
  noCredentials: boolean;
}
let _mockLoginJobSeq = 0;
const _mockLoginJobs: Record<string, _MockLoginJob> = {};

/** Derives the CURRENT `BrokerageLoginJob` status for a tracked job purely
 *  from elapsed time -- no separate "advance the mock forward" call needed,
 *  so a real 2s-interval poll and a test's `vi.advanceTimersByTime` both
 *  just work. */
function _mockLoginJobStatus(
  jobId: string,
  job: _MockLoginJob,
): BrokerageLoginJob {
  const elapsedSeconds = (Date.now() - job.startedAt) / 1000;
  const secondsRemaining = Math.max(
    0,
    Math.round(BROKERAGE_LOGIN_DEADLINE_SECONDS - elapsedSeconds),
  );
  const connected = readBrokerageConnected();

  if (job.cancelled) {
    return {
      job_id: jobId,
      mode: job.mode,
      state: "cancelled",
      phase: "awaiting_approval",
      error_code: "cancelled",
      seconds_remaining: secondsRemaining,
      connected,
      has_account_snapshot: connected,
    };
  }

  if (job.noCredentials) {
    // A believable brief "starting" beat before the honest failure, rather
    // than a same-tick reject -- matches the real async shape (a job that
    // fails is still a job, discovered through a status poll).
    if (elapsedSeconds < 1) {
      return {
        job_id: jobId,
        mode: job.mode,
        state: "running",
        phase: "starting",
        error_code: null,
        seconds_remaining: secondsRemaining,
        connected,
        has_account_snapshot: connected,
      };
    }
    return {
      job_id: jobId,
      mode: job.mode,
      state: "failed",
      phase: "starting",
      error_code: "no_credentials",
      seconds_remaining: secondsRemaining,
      connected,
      has_account_snapshot: connected,
    };
  }

  if (job.simulateTimeout) {
    if (elapsedSeconds >= BROKERAGE_LOGIN_DEADLINE_SECONDS) {
      return {
        job_id: jobId,
        mode: job.mode,
        state: "timeout",
        phase: "awaiting_approval",
        error_code: "timeout",
        seconds_remaining: 0,
        connected,
        has_account_snapshot: connected,
      };
    }
    return {
      job_id: jobId,
      mode: job.mode,
      state: "running",
      phase: "awaiting_approval",
      error_code: null,
      seconds_remaining: secondsRemaining,
      connected,
      has_account_snapshot: connected,
    };
  }

  // Happy path: starting -> authenticating -> awaiting_approval -> verifying
  // -> fetching_snapshot -> fetching_orders -> succeeded. Timed so a
  // 2s-interval poller sees a couple of "running" polls (awaiting_approval)
  // before success, rather than resolving on the very first poll. Only a
  // "refresh" job goes through fetching_orders -- a "connect" job (mode
  // verification only) jumps straight from verifying to done, matching
  // data/robinhood_login_worker.py's real dispatch.
  let phase: BrokerageLoginPhase;
  if (elapsedSeconds < 1) phase = "starting";
  else if (elapsedSeconds < 2) phase = "authenticating";
  else if (elapsedSeconds < 5) phase = "awaiting_approval";
  else if (elapsedSeconds < 6) phase = "verifying";
  else if (job.mode === "refresh" && elapsedSeconds < 7) phase = "fetching_snapshot";
  else if (job.mode === "refresh" && elapsedSeconds < 8) phase = "fetching_orders";
  else phase = "done";

  if (phase !== "done") {
    return {
      job_id: jobId,
      mode: job.mode,
      state: "running",
      phase,
      error_code: null,
      seconds_remaining: secondsRemaining,
      connected,
      has_account_snapshot: connected,
    };
  }

  if (job.mode === "connect") writeBrokerageConnected(true);
  const nowConnected = readBrokerageConnected();
  return {
    job_id: jobId,
    mode: job.mode,
    state: "succeeded",
    phase: "done",
    error_code: null,
    seconds_remaining: secondsRemaining,
    connected: nowConnected,
    has_account_snapshot: true,
  };
}

interface _MockForecastBackfillJob {
  mode: "run";
  startedAt: number; // Date.now() at creation
  cancelled: boolean;
  // Deterministic "fails partway through" trigger -- same "flip it from
  // devtools" convention as BROKERAGE_LOGIN_TIMEOUT_KEY below (there's no
  // magic ticker/theta_c value here since a bad *value* would legitimately
  // belong in a 422 the real backend's own request validation would catch
  // before start_job() ever runs, not a mid-training failure):
  //   localStorage.setItem("stockpy.mock.forecast_backfill_failure", "1")  // next run fails partway through instead of succeeding
  //   localStorage.removeItem("stockpy.mock.forecast_backfill_failure")   // back to the happy path
  simulateFailure: boolean;
  // Deterministic "deadline SIGKILL mid-training" trigger, same convention.
  // Reproduces ml/forecast_backfill_job.py's _enforce_deadline path: a few
  // step-5 combos already trained (real partial_summary.trained/
  // metrics_so_far entries) before the kill, so the honest
  // "partial results were saved" branch of backfillFailureMessage() is
  // reachable by actually running the app, not only through the test suite:
  //   localStorage.setItem("stockpy.mock.forecast_backfill_timeout", "1")  // next run times out with partial results
  //   localStorage.removeItem("stockpy.mock.forecast_backfill_timeout")    // back to the happy path
  simulateTimeout: boolean;
}

const FORECAST_BACKFILL_FAILURE_KEY = "stockpy.mock.forecast_backfill_failure";
const FORECAST_BACKFILL_TIMEOUT_KEY = "stockpy.mock.forecast_backfill_timeout";

function readForecastBackfillFailure(): boolean {
  try {
    return localStorage.getItem(FORECAST_BACKFILL_FAILURE_KEY) === "1";
  } catch {
    return false;
  }
}

function readForecastBackfillTimeout(): boolean {
  try {
    return localStorage.getItem(FORECAST_BACKFILL_TIMEOUT_KEY) === "1";
  } catch {
    return false;
  }
}

/** Realistic partial checkpoint for the timeout-simulation branch below --
 *  a subset of mockForecastBackfill()'s own metrics, matching the exact
 *  {accuracy, auc, n_train, n_test, split_date, is_active} shape
 *  ml/forecast_backfill.py actually writes to `self.metrics[model_key]`. */
function mockForecastBackfillPartialSummary(): ForecastBackfillJob["partial_summary"] {
  const metrics_so_far = {
    timeseries_momentum_10d: {
      accuracy: 0.5215,
      auc: 0.542,
      n_train: 9480,
      n_test: 0,
      split_date: "CPCV",
      is_active: true,
    },
    timeseries_momentum_30d: {
      accuracy: 0.534,
      auc: 0.558,
      n_train: 9416,
      n_test: 0,
      split_date: "CPCV",
      is_active: true,
    },
    rsi2_mean_reversion_10d: {
      accuracy: 0.518,
      auc: 0.531,
      n_train: 6820,
      n_test: 0,
      split_date: "CPCV",
      is_active: true,
    },
  };
  return {
    trained: Object.keys(metrics_so_far).sort(),
    metrics_so_far,
  };
}

let _mockForecastBackfillJobSeq = 0;
const _mockForecastBackfillJobs: Record<string, _MockForecastBackfillJob> = {};

/** The currently-`"running"` mock job's id, or `null` -- mirrors the real
 *  backend's single-flight guard (`ml/forecast_backfill_job.py::start_job`
 *  returns `None` when `_active_job_id` is still `"running"`) so the mock's
 *  `runForecastBackfill` can 409 the same way, rather than always accepting
 *  a second concurrent "run" and leaving that whole path untestable against
 *  the mock/dev-server UI. */
function _findRunningForecastBackfillJobId(): string | null {
  for (const [jobId, job] of Object.entries(_mockForecastBackfillJobs)) {
    if (_mockForecastBackfillJobStatus(jobId, job).state === "running") {
      return jobId;
    }
  }
  return null;
}

function _mockForecastBackfillJobStatus(
  jobId: string,
  job: _MockForecastBackfillJob,
): ForecastBackfillJob {
  const elapsedSeconds = (Date.now() - job.startedAt) / 1000;
  const SECONDS_PER_PHASE = 2;
  const TOTAL_STEPS = 8;
  const TOTAL_SECONDS = TOTAL_STEPS * SECONDS_PER_PHASE;
  const secondsRemaining = Math.max(
    0,
    Math.round(TOTAL_SECONDS - elapsedSeconds),
  );

  // Time-derived phase/step, shared by every branch below -- including the
  // terminal ones -- so a cancelled/failed job honestly reports whatever
  // phase it had actually reached rather than resetting to the first phase.
  // Mirrors the real backend exactly: `cancel_job()` / the worker's own
  // failure path only ever flip state/error/error_type, never phase/step
  // (see `ml/forecast_backfill_job.py`). The real backend's initial 202
  // response ALSO has `phase: null` (nothing has been drained off the
  // child's events pipe yet) -- reproduced here as a brief `< 1s` window
  // rather than assigning a real phase from the very first status response,
  // which would hide the frontend's own `phase: null` handling from anyone
  // testing against the mock.
  let phase: ForecastBackfillPhase | null;
  let step: number;
  if (elapsedSeconds < 1) {
    phase = null;
    step = 0;
  } else if (elapsedSeconds < 2) {
    phase = "fetching_data";
    step = 1;
  } else if (elapsedSeconds < 4) {
    phase = "technical_features";
    step = 2;
  } else if (elapsedSeconds < 6) {
    phase = "primary_signals";
    step = 3;
  } else if (elapsedSeconds < 8) {
    phase = "meta_targets";
    step = 4;
  } else if (elapsedSeconds < 10) {
    phase = "backtraining";
    step = 5;
  } else if (elapsedSeconds < 12) {
    phase = "backfilling";
    step = 6;
  } else if (elapsedSeconds < 14) {
    phase = "registry_bridge";
    step = 7;
  } else if (elapsedSeconds < 16) {
    phase = "exporting";
    step = 8;
  } else {
    phase = "exporting";
    step = TOTAL_STEPS;
  }

  if (job.cancelled) {
    return {
      job_id: jobId,
      state: "cancelled",
      phase,
      step,
      total_steps: TOTAL_STEPS,
      error: "Forecast backfill run was cancelled.",
      error_type: "cancelled",
      summary: null,
      sample_rows: null,
      partial_summary: null,
      seconds_remaining: secondsRemaining,
    };
  }

  if (job.simulateFailure) {
    // Fails partway through rather than at t=0 -- a job that fails is still
    // a job, discovered through a status poll, matching both the real
    // subprocess-isolated worker's shape and _mockLoginJobStatus's
    // noCredentials precedent above (a believable "running" beat first).
    if (elapsedSeconds < 3) {
      return {
        job_id: jobId,
        state: "running",
        phase,
        step,
        total_steps: TOTAL_STEPS,
        error: null,
        error_type: null,
        summary: null,
        sample_rows: null,
        partial_summary: null,
        seconds_remaining: secondsRemaining,
      };
    }
    return {
      job_id: jobId,
      state: "failed",
      phase: "technical_features",
      step: 2,
      total_steps: TOTAL_STEPS,
      error:
        "Training data contained fewer than the minimum required samples for one or more horizons.",
      error_type: "value_error",
      summary: null,
      sample_rows: null,
      // Killed/failed during step 2 (technical_features), well before step 5
      // (backtraining) ever produces a progress event -- honestly null, not
      // fabricated, matching ml/forecast_backfill_job.py's own contract.
      partial_summary: null,
      seconds_remaining: 0,
    };
  }

  if (job.simulateTimeout) {
    // Reproduces ml/forecast_backfill_job.py's _enforce_deadline: a running
    // job that never reaches a "result" event before the deadline elapses is
    // SIGKILLed and flipped to state: "timeout" -- but a few step-5 combos
    // already trained (and were checkpointed via the on_combo_trained
    // callback) before the kill, so partial_summary is honestly non-empty
    // here, exercising backfillFailureMessage()'s "partial results were
    // saved" branch.
    if (elapsedSeconds < 10) {
      return {
        job_id: jobId,
        state: "running",
        phase,
        step,
        total_steps: TOTAL_STEPS,
        error: null,
        error_type: null,
        summary: null,
        sample_rows: null,
        // The real backend only starts populating partial_summary once
        // step-5 combos begin training (phase: "backtraining"), same as
        // ml/forecast_backfill_worker.py's on_combo_trained callback.
        partial_summary:
          elapsedSeconds >= 8 ? mockForecastBackfillPartialSummary() : null,
        seconds_remaining: secondsRemaining,
      };
    }
    return {
      job_id: jobId,
      state: "timeout",
      phase: "backtraining",
      step: 5,
      total_steps: TOTAL_STEPS,
      error: `Forecast backfill did not complete within the configured deadline.`,
      error_type: "timeout",
      summary: null,
      sample_rows: null,
      partial_summary: mockForecastBackfillPartialSummary(),
      seconds_remaining: 0,
    };
  }

  if (elapsedSeconds >= TOTAL_SECONDS) {
    return {
      job_id: jobId,
      state: "succeeded",
      phase: "exporting",
      step: TOTAL_STEPS,
      total_steps: TOTAL_STEPS,
      error: null,
      error_type: null,
      summary: mockForecastBackfill(),
      sample_rows: 11080,
      partial_summary: null,
      seconds_remaining: 0,
    };
  }

  return {
    job_id: jobId,
    state: "running",
    phase,
    step,
    total_steps: TOTAL_STEPS,
    error: null,
    error_type: null,
    summary: null,
    sample_rows: null,
    partial_summary: null,
    seconds_remaining: secondsRemaining,
  };
}

// ---- Local ROBINHOOD_AUTO_REFRESH_ENABLED server-gate simulation
// (localStorage) -- mirrors the real settings.ROBINHOOD_AUTO_REFRESH_ENABLED
// field GET /brokerage/status now echoes read-only (default True). No
// dedicated UI control (this is a read-only .env-only server setting in the
// real app too), same "flip it from devtools" convention as
// BROKERAGE_REFRESH_DEGRADED_KEY above:
//   localStorage.setItem("stockpy.mock.brokerage_auto_refresh_disabled", "1")  // simulate the server gate off
//   localStorage.removeItem("stockpy.mock.brokerage_auto_refresh_disabled")    // back to the default-True gate
const BROKERAGE_AUTO_REFRESH_DISABLED_KEY =
  "stockpy.mock.brokerage_auto_refresh_disabled";

function readBrokerageAutoRefreshEnabled(): boolean {
  try {
    return localStorage.getItem(BROKERAGE_AUTO_REFRESH_DISABLED_KEY) !== "1";
  } catch {
    return true;
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
  overrides: LlmMockOverrides,
): LlmCapabilityRow {
  const masterOn = overrides.toggles[toggleKey] ?? false;
  const activeProvider: LlmProviderName | null =
    providerChoice && providerChoice !== "none"
      ? (providerChoice as LlmProviderName)
      : null;
  const enabled = providerSelectorSetting
    ? masterOn && providerChoice !== "none"
    : masterOn;
  const providerKeys = activeProvider
    ? [LLM_PROVIDER_KEY_MAP[activeProvider]]
    : fixedProviderKeys;
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
      ov,
    ),
    llmRow(
      "gemini_alerts",
      "Alert commentary",
      "scheduled",
      "LLM_COMMENTARY_ENABLED",
      "LLM_COMMENTARY_ALERT_PROVIDER",
      providerVal("LLM_COMMENTARY_ALERT_PROVIDER", "gemini"),
      ["GEMINI_API_KEY"],
      ov,
    ),
    llmRow(
      "gemini_vision",
      "Gemini chart vision",
      "on_demand",
      "LLM_COMMENTARY_ENABLED",
      null,
      null,
      ["GEMINI_API_KEY"],
      ov,
    ),
    llmRow(
      "gravity_ai_runner",
      "Gravity AI runner (Claude + Gemini)",
      "on_demand",
      "GRAVITY_AI_RUNNER_ENABLED",
      null,
      null,
      ["ANTHROPIC_API_KEY", "GEMINI_API_KEY"],
      ov,
    ),
    llmRow(
      "opal_research",
      "Opal research agent",
      "on_demand",
      "OPAL_RESEARCH_ENABLED",
      "OPAL_RESEARCH_PROVIDER",
      providerVal("OPAL_RESEARCH_PROVIDER", "openai"),
      ["OPENAI_API_KEY"],
      ov,
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
    if (row.status === "missing_key" && attentionReason === null)
      attentionReason = "missing_key";
  }

  return {
    capabilities,
    capabilities_source: "shared.ai_control_center.control_center_overview",
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
    writable_note:
      "Toggle and provider writes persist to .env and apply on the next daemon restart.",
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
  {
    name: "macro_regime",
    weight: 45,
    pinned: false,
    scored: 20,
    versionHash: "a1b2c3d4e5f6",
    modifiedDaysAgo: 12,
  },
  {
    name: "macd_momentum",
    weight: 20,
    pinned: false,
    scored: 20,
    versionHash: "1a2b3c4d5e6f",
    modifiedDaysAgo: 40,
  },
  {
    name: "aroon_trend",
    weight: 15,
    pinned: false,
    scored: 20,
    versionHash: "9f8e7d6c5b4a",
    modifiedDaysAgo: 88,
  },
  {
    name: "graham_value",
    weight: 20,
    pinned: false,
    scored: 18,
    versionHash: "0d1e2f3a4b5c",
    modifiedDaysAgo: 5,
  },
  {
    name: "dividend_quality",
    weight: 15,
    pinned: false,
    scored: 12,
    versionHash: "6c5b4a39281f",
    modifiedDaysAgo: 61,
  },
  {
    name: "multifactor",
    weight: 15,
    pinned: false,
    scored: 19,
    versionHash: "3e4f5a6b7c8d",
    modifiedDaysAgo: 2,
  },
  {
    name: "cross_sectional_momentum",
    weight: 15,
    pinned: false,
    scored: 20,
    versionHash: "7a8b9c0d1e2f",
    modifiedDaysAgo: 30,
  },
  {
    name: "regime_multiplier",
    weight: 0,
    pinned: true,
    scored: 20,
    versionHash: "f1e2d3c4b5a6",
    modifiedDaysAgo: 200,
  },
];

function readStrategyOverrides(): {
  weights: Record<string, number>;
  disabled: string[];
} | null {
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
      last_modified: new Date(
        Date.now() - b.modifiedDaysAgo * 86_400_000,
      ).toISOString(),
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
// Mirrors api/pilots_api.py's REAL _TUNABLE_GROUPS exactly (same group names,
// same field set, including the "Advanced / Config" keys the backend
// previously omitted and the portfolio-gross-cap/escalation/audit/alert keys
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
// an operator forces "fmp"/"yfinance"). Accepted writes persist to
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

// ---------------------------------------------------------------------------
// Per-field liveness for the mock editors.
//
// These are NOT invented: `MOCK_CAPTURE_SITES` is copied verbatim from the real
// `docs/settings_liveness.json` artifact (generated by
// `scripts/settings_liveness.py`) for exactly the keys these fixtures serve, so
// demo mode shows the same "needs restart" set, with the same checkable
// `file:line` evidence, that the live backend reports. Every key absent from
// this map is `live_safe` in that same artifact and therefore applies
// immediately.
//
// `MOCK_DEMO_ONLY_STATES` below is the one deliberate exception -- see its own
// comment.
// ---------------------------------------------------------------------------
const MOCK_CAPTURE_SITES: Record<string, string[]> = {
  RISK_FREE_RATE: ["processing_engine.py:37", "technical_options_engine.py:24"],
  MARKET_RISK_PREMIUM: ["processing_engine.py:38"],
  REQUIRED_RETURN_RATE: ["processing_engine.py:39"],
  MAX_PORTFOLIO_HEAT: ["execution/risk_gate.py:153"],
  CORRELATION_CLUSTER_LOOKBACK_DAYS: ["api/pilots_api.py:4082"],
  MAX_POSITION_WEIGHT: ["execution/risk_gate.py:150"],
  MAX_CORRELATION: ["execution/risk_gate.py:156"],
  DAILY_LOSS_LIMIT_PCT: ["execution/dynamic_circuit_breaker.py:312", "execution/risk_gate.py:161"],
  MAX_ORDER_RATE_PER_MIN: ["execution/risk_gate.py:166"],
  HMM_RISK_OFF_BLOCK_THRESHOLD: ["execution/risk_gate.py:171"],
  RISK_GATE_ENFORCE_MARKET_HOURS: ["execution/risk_gate.py:176"],
  MARKET_DATA_PROVIDER: ["data/market_data.py:1922"],
  MARKET_DATA_QUOTE_TTL_SECONDS: ["data/market_data.py:1854"],
  MARKET_DATA_BARS_TTL_SECONDS: [
    "data/market_data.py:1859",
    "data/market_data.py:2287",
  ],
  FUNDAMENTALS_SOURCE: ["data/market_data.py:1892"],
  DASHBOARD_REFRESH_SECONDS: ["api/pilots_api.py:4194", "pilots/settings_domains.py:132"],
  SECTOR_FORECAST_CONFIG_PATH: ["forecasting_engine.py:165"],
  SECTOR_FORECAST_CONFIGS: ["forecasting_engine.py:167"],
  CORS_ALLOWED_ORIGINS: ["api/control_api.py:169", "api/data_api.py:131", "api/metrics_api.py:74", "api/pilots_api.py:342", "api/state_api.py:87"],
  SENTIMENT_SOURCES: ["data/sentiment_sources.py:1932"],
  SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE: ["data/sentiment_sources.py:1962"],
  EDGAR_FULLTEXT_FORMS: ["api/pilots_api.py:4839"],
  EDGAR_FULLTEXT_CHUNK_TOKENS: ["api/pilots_api.py:4840"],
  FMP_QUOTES_REALTIME: ["data/market_data.py:1011"],
  FMP_BARS_ADJUSTMENT: ["data/market_data.py:1942"],
  FMP_ECON_INDICATORS: ["api/pilots_api.py:4981"],
  SYMBOL_RATING_DROP_THRESHOLD_CYCLES: ["ml/forecast_backfill.py:142"],
};

// `settings_keysets.DANGEROUS_KEYS`, in full -- copied from the real set.
// Every real settings_keysets.DANGEROUS_KEYS member is covered here, since
// the Feature Flags screen (webapp/src/api/mock.ts's
// FEATURE_FLAGS_TUNABLE_DEFS) serves every one of them, exercising the
// typed-confirmation flow for all of them in mock mode.
const MOCK_DANGEROUS_KEYS = new Set([
  "BROKER_BACKEND",
  "ADVISORY_ONLY",
  "DRY_RUN",
  "ROBINHOOD_EXECUTION_MODE",
  "DAEMON_AGENTIC_QUEUE_MODE",
  "CORS_ALLOWED_ORIGINS",
  "FMP_BARS_ENABLED",
  "FMP_BARS_ADJUSTMENT",
  // 2026-08-08: settings_keysets.SAFETY_CRITICAL_KEY_REASONS gained these
  // fields when the fail-closed write/execution gates were reclassified out
  // of EXCLUDED_FROM_GUI into ALLOWED_KEYS -- now all exposed by the Feature
  // Flags screen.
  "MACRO_REGIME_GATE_ENABLED",
  "AI_GENERATION_API_ENABLED",
  "AUTOMATION_WRITES_ENABLED",
  "BROKERAGE_REFRESH_ENABLED",
  "ROBINHOOD_SCHEDULED_LOGIN_ENABLED",
  "COMMAND_EXECUTION_ENABLED",
  "DEAD_LETTER_RETRY_ENABLED",
  "GENERAL_SETTINGS_WRITES_ENABLED",
  "LLM_WRITES_ENABLED",
  "MACRO_GATE_WRITES_ENABLED",
  "MCP_OAUTH_ENABLED",
  "PROMPT_REGISTRY_WRITES_ENABLED",
  "RAG_QUERY_API_ENABLED",
  "STRATEGY_WRITES_ENABLED",
]);

// ---------------------------------------------------------------------------
// SYNTHESIZED demo states -- the ONE place this mock deviates from the real
// classifier, and it is deliberate and bounded.
//
// The real artifact classifies every key these five editors serve as either
// `live_safe` or `restart_required`; NEITHER `no_effect` NOR `env_pinned`
// occurs naturally here (`env_pinned` cannot, by construction -- it depends on
// the operator's live shell, which a browser fixture has no access to). Without
// these two entries, two of the UI's four badge states would be unreachable in
// demo mode and effectively unreviewable.
//
// So: these two fields are labelled here for DEMO COVERAGE and do not describe
// the real platform's behaviour for them. Everything else above does.
//   - LOG_LEVEL is shown env-pinned because `LOG_LEVEL=DEBUG python3 main.py`
//     is the single most plausible real shell export in this repo.
//   - REQUIRED_RETURN_RATE is shown no-effect purely to exercise that badge.
// ---------------------------------------------------------------------------
const MOCK_DEMO_ONLY_STATES: Record<string, "env_pinned" | "no_effect"> = {
  LOG_LEVEL: "env_pinned",
  REQUIRED_RETURN_RATE: "no_effect",
  // Unlike the two above, this ONE entry does describe real platform
  // behaviour: PROMPT_MAX_CHARS is a genuine no_op per
  // docs/settings_liveness.json (read nowhere in production code). Without
  // this override it falls through to the generic live_safe/restart_required
  // mock classification below, which would render it as "Applies now" --
  // the misleading "control that does nothing" trap `writable`'s no_op
  // exclusion (mockSettingsReference()) exists to prevent. (The boolean
  // no_op examples used here before -- OPTIONS_EARNINGS_CRUSH_ENABLED,
  // CIRCUIT_BREAKER_ENABLED -- were retired in 2026-09, step 4f.)
  PROMPT_MAX_CHARS: "no_effect",
};

function mockLiveness(key: string): TunableLiveness {
  const demo = MOCK_DEMO_ONLY_STATES[key];
  const sites = MOCK_CAPTURE_SITES[key] ?? [];
  const dangerous = MOCK_DANGEROUS_KEYS.has(key);

  if (demo === "env_pinned") {
    return {
      applies: "env_pinned",
      restart_reason:
        sites.length > 0
          ? `This value was read once, when its module was first imported (${sites[0]}).`
          : null,
      capture_sites: sites,
      env_pinned: true,
      dangerous,
      source: "env_file",
    };
  }
  if (demo === "no_effect") {
    return {
      applies: "no_effect",
      restart_reason: null,
      capture_sites: [],
      env_pinned: false,
      dangerous,
      source: "env_file",
    };
  }
  if (sites.length > 0) {
    return {
      applies: "next_daemon_restart",
      restart_reason: `This value was read once, when its module was first imported (${sites[0]}).`,
      capture_sites: sites,
      env_pinned: false,
      dangerous,
      source: "env_file",
    };
  }
  return {
    applies: "immediately",
    restart_reason: null,
    // `[]` is the MEASURED answer for a live-safe field -- the classifier
    // looked and found nothing capturing it -- never "we didn't check".
    capture_sites: [],
    env_pinned: false,
    dangerous,
    source: "runtime_store",
  };
}

const TUNABLE_DEFS: MockTunableDef[] = [
  // ---- Financial Constants ----
  {
    group: "Financial Constants",
    key: "RISK_FREE_RATE",
    type: "number",
    value: 0.045,
    default: 0.045,
    min: 0,
    max: 1,
    step: 0.005,
    description: null,
  },
  {
    group: "Financial Constants",
    key: "MARKET_RISK_PREMIUM",
    type: "number",
    value: 0.055,
    default: 0.055,
    min: 0,
    max: 1,
    step: 0.005,
    description: null,
  },
  {
    group: "Financial Constants",
    key: "REQUIRED_RETURN_RATE",
    type: "number",
    value: 0.08,
    default: 0.08,
    min: 0,
    max: 1,
    step: 0.005,
    description: null,
  },
  {
    group: "Financial Constants",
    key: "MAX_PORTFOLIO_HEAT",
    type: "number",
    value: 0.06,
    default: 0.06,
    min: 0,
    max: 1,
    step: 0.01,
    description: null,
  },
  {
    group: "Financial Constants",
    key: "MULTIFACTOR_MICROCAP_THRESHOLD",
    type: "number",
    value: 50000000.0,
    default: 50000000.0,
    description: "Market-cap floor (USD) below which multifactor ranking heavily penalizes a ticker.",
    min: 0.0,
    max: 1000000000000.0,
    step: 1000000.0,
  },
  {
    group: "Financial Constants",
    key: "CORRELATION_CLUSTER_LOOKBACK_DAYS",
    type: "number",
    value: 60,
    default: 60,
    description: "Lookback window in trading days for correlation clustering. 60 days (~1 quarter) balances responsiveness to regime changes against covariance noise.",
    min: 5,
    max: 500,
    step: 5,
  },
  {
    group: "Financial Constants",
    key: "CORRELATION_CLUSTER_THRESHOLD",
    type: "number",
    value: 0.4,
    default: 0.4,
    description: "Dendrogram cut-distance for cluster assignment. Uses the Lopez de Prado distance d=sqrt(0.5*(1-rho)). At 0.4, stocks with |correlation| > 0.68 merge into the same cluster. Lower = tighter (fewer, smaller clusters); higher = looser.",
    min: 0.0,
    max: 1.0,
    step: 0.05,
  },
  {
    group: "Financial Constants",
    key: "FEATURE_DRIFT_PSI_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description: "Enable Population Stability Index check for feature drift.",
  },
  // ---- Position Sizing ----
  {
    group: "Position Sizing",
    key: "KELLY_FRACTION",
    type: "number",
    value: 0.5,
    default: 0.5,
    min: 0,
    max: 1,
    step: 0.05,
    description: null,
  },
  {
    group: "Position Sizing",
    key: "KELLY_CAP",
    type: "number",
    value: 0.2,
    default: 0.2,
    min: 0,
    max: 1,
    step: 0.01,
    description: null,
  },
  {
    group: "Position Sizing",
    key: "VOL_TARGET",
    type: "number",
    value: 0.1,
    default: 0.1,
    min: 0,
    max: 1,
    step: 0.01,
    description: null,
  },
  {
    group: "Position Sizing",
    key: "MAX_LEVERAGE",
    type: "number",
    value: 2.0,
    default: 2.0,
    min: 0,
    max: 10,
    step: 0.1,
    description: null,
  },
  {
    group: "Position Sizing",
    key: "MAX_POSITION_WEIGHT",
    type: "number",
    value: 1.0,
    default: 1.0,
    min: 0,
    max: 5,
    step: 0.05,
    description: null,
  },
  // Portfolio-level gross exposure cap + cap-aware escalation + cap-event
  // audit/alerting (sizing/position_sizer.py, sizing/cap_audit_store.py) --
  // same "no description in settings.py" convention as the five sizing
  // fields above.
  {
    group: "Position Sizing",
    key: "MAX_PORTFOLIO_GROSS",
    type: "number",
    value: 3.0,
    default: 3.0,
    min: 0,
    max: 20,
    step: 0.1,
    description: null,
  },
  {
    group: "Position Sizing",
    key: "SIZING_CAP_ESCALATION_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description: null,
  },
  {
    group: "Position Sizing",
    key: "SIZING_CAP_ESCALATION_THRESHOLD_CYCLES",
    type: "number",
    value: 5,
    default: 5,
    min: 1,
    max: 100,
    step: 1,
    description: null,
  },
  {
    group: "Position Sizing",
    key: "SIZING_CAP_ESCALATION_FACTOR",
    type: "number",
    value: 0.5,
    default: 0.5,
    min: 0,
    max: 1,
    step: 0.05,
    description: null,
  },
  {
    group: "Position Sizing",
    key: "SIZING_CAP_AUDIT_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description: null,
  },
  {
    group: "Position Sizing",
    key: "SIZING_CAP_ALERT_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description: null,
  },
  {
    group: "Position Sizing",
    key: "SIZING_CAP_ALERT_THRESHOLD_PCT",
    type: "number",
    value: 0.3,
    default: 0.3,
    min: 0,
    max: 1,
    step: 0.05,
    description: null,
  },
  {
    group: "Position Sizing", key: "USE_DUAL_MOMENTUM_OVERLAY", type: "boolean",
    value: false, default: false,
    description: "When True, the Dual Momentum allocator pre-screens the ticker list each run. If the allocator selects the safe asset (BIL), tickers in the risky universes (SPY, VEU) have their Kelly Target set to 0.0.",
  },
  {
    group: "Position Sizing", key: "DUAL_MOMENTUM_SAFE_ASSET", type: "string",
    value: "BIL", default: "BIL",
    description: "Ticker used as the safe/defensive asset in the Dual Momentum overlay.",
  },
  {
    group: "Position Sizing", key: "DUAL_MOMENTUM_RISKY_ASSETS", type: "string",
    value: '["SPY", "VEU"]', default: '["SPY", "VEU"]',
    description: "Risky ETFs compared in the Dual Momentum cross-sectional filter.",
  },
  // ---- Symbol Rating (Tracked Universe auto-drop, rating/symbol_rating_store.py) ----
  {
    group: "Symbol Rating",
    key: "SYMBOL_RATING_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Compute and persist a per-symbol rating every cycle. Diagnostic-only -- no symbol is excluded by this flag alone.",
  },
  {
    group: "Symbol Rating",
    key: "SYMBOL_RATING_BAD_SCORE_THRESHOLD",
    type: "number",
    value: 35.0,
    default: 35.0,
    min: 0,
    max: 100,
    step: 1,
    description:
      "A symbol's score below this is rated BAD this cycle. Matches strategy_engine.py's own RISK REDUCE cutoff.",
  },
  {
    group: "Symbol Rating",
    key: "SYMBOL_RATING_AUTO_DROP_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Opt-in. When on, a non-held symbol rated BAD for SYMBOL_RATING_DROP_THRESHOLD_CYCLES cycles in a row is dropped from the Tracked Universe. A held position is never dropped.",
  },
  {
    group: "Symbol Rating",
    key: "SYMBOL_RATING_DROP_THRESHOLD_CYCLES",
    type: "number",
    value: 5,
    default: 5,
    min: 1,
    max: 100,
    step: 1,
    description: null,
  },
  // ---- Risk Gate ----
  {
    group: "Risk Gate",
    key: "MAX_CORRELATION",
    type: "number",
    value: 0.85,
    default: 0.85,
    min: 0,
    max: 1,
    step: 0.05,
    description:
      "Max absolute pairwise return correlation before a new position is blocked.",
  },
  {
    group: "Risk Gate",
    key: "DAILY_LOSS_LIMIT_PCT",
    type: "number",
    value: 0.02,
    default: 0.02,
    min: 0,
    max: 1,
    step: 0.005,
    description:
      "Halt new BUY orders when intraday P&L drops below this fraction of start-of-day equity.",
  },
  {
    group: "Risk Gate",
    key: "MAX_ORDER_RATE_PER_MIN",
    type: "number",
    value: 10,
    default: 10,
    min: 1,
    max: 1000,
    step: 1,
    description: "Maximum order submissions in any 60-second rolling window.",
  },
  {
    group: "Regime Model",
    key: "HMM_RISK_OFF_BLOCK_THRESHOLD",
    type: "number",
    value: 0.8,
    default: 0.8,
    min: 0,
    max: 1,
    step: 0.05,
    description:
      "Block new long orders when HMM risk-off probability exceeds this. The Gaussian HMM models the underlying market regime. A higher value means the system is less likely to block trades (more aggressive), while a lower value makes it more sensitive to volatility and bear market conditions, halting long entries sooner.",
  },
  {
    group: "Regime Model",
    key: "HMM_N_STATES",
    type: "number",
    value: 3,
    default: 3,
    min: 2,
    max: 10,
    step: 1,
    description:
      "Number of hidden states for the Gaussian HMM regime detector (bull/sideways/bear). A 3-state model typically classifies high, medium, and low volatility regimes. Changing this alters the fundamental clustering behavior of the regime model.",
  },
  {
    group: "Regime Model",
    key: "HMM_RETRAIN_FREQ_DAYS",
    type: "number",
    value: 7,
    default: 7,
    min: 1,
    max: 30,
    step: 1,
    description:
      "Minimum days between HMM refits; fit() calls within this window of the last real fit are no-ops. A lower number means the model adapts faster to sudden market shifts (like flash crashes), but increases computational overhead and may cause temporary over-sensitivity to noise.",
  },
  {
    group: "Risk Gate",
    key: "RISK_GATE_ENFORCE_MARKET_HOURS",
    type: "boolean",
    value: true,
    default: true,
    description: "Block orders outside NYSE RTH (09:30–16:00 ET).",
  },
  {
    group: "Risk Gate",
    key: "META_LABEL_MIN_CONFIDENCE",
    type: "number",
    value: 0.4,
    default: 0.4,
    min: 0,
    max: 1,
    step: 0.05,
    description:
      "Minimum meta-label probability for a primary signal to contribute to sizing. If predict_proba < META_LABEL_MIN_CONFIDENCE, the meta_label_composite is forced to 0.0 (position zeroed for the cycle).",
  },
  {
    group: "Risk Gate",
    key: "DRY_RUN",
    type: "boolean",
    value: false,
    default: false,
    description: "Log orders but do not submit to broker.",
  },
  {
    group: "Risk Gate", key: "EXECUTION_PRIORITY_QUEUE_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Opt-in: route OrderIntents through execution/priority_queue.py's leaky-bucket priority queue before submission, prioritizing risk-reducing (SELL/TRIM) intents over new BUYs when nearing the submission-rate budget. Does NOT replace or bypass MAX_ORDER_RATE_PER_MIN's hard cap (execution/risk_gate.py) or execution/kill_switch.py -- both remain the sole authorization gate, checked at submission exactly as before. False (default) preserves the exact current sequential per-row submission order -- matches the FORECAST_USE_GARCH_SIGMA opt-in convention.",
  },
  {
    group: "Risk Gate", key: "EXECUTION_QUEUE_LEAK_RATE_PER_SEC", type: "number",
    value: 2.0, default: 2.0, min: 0.0, max: 100.0, step: 0.5,
    description: "Leaky-bucket drain rate (order submissions/sec) when EXECUTION_PRIORITY_QUEUE_ENABLED=true. Only paces submission ordering within a single cycle's queue drain -- independent of MAX_ORDER_RATE_PER_MIN's separate 60s rolling-window cap.",
  },
  {
    group: "Risk Gate", key: "FLATTEN_ON_KILL", type: "boolean",
    value: false, default: false,
    description: "Log CRITICAL position-flatten reminder when kill switch activates.",
  },
  // ---- Forecasting ----
  {
    group: "Forecasting",
    key: "FORECAST_USE_GARCH_SIGMA",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Use the GJR-GARCH(1,1) volatility estimate (annualized, converted to daily via /sqrt(252)) as the Monte Carlo sigma instead of naive historical stdev. False restores the pre-GARCH log-return-std behavior.",
  },
  {
    group: "Forecasting",
    key: "FORECAST_PROPHET_WEIGHT",
    type: "number",
    value: 0.25,
    default: 0.25,
    min: 0,
    max: 1,
    step: 0.05,
    description:
      "Weight given to the Prophet 30-day forecast when blending it into the static ensemble at the 30-day horizon: final = base*(1-w) + prophet*w. 0.0 disables Prophet's influence on the blend.",
  },
  {
    group: "Forecasting",
    key: "FORECAST_SKILL_WEIGHTING_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Opt-in activation of inverse-RMSE skill-weighted multi-model forecast blending (ARIMA / Monte Carlo / Holt-Winters / CNN-LSTM weighted by recent realized accuracy via forecasting.forecast_tracker.ForecastTracker). When False (the default) the static sector-preference blend is used unchanged.",
  },
  {
    group: "Forecasting",
    key: "FORECAST_SKILL_WINDOW_DAYS",
    type: "number",
    value: 180,
    default: 180,
    min: 1,
    max: 3650,
    step: 1,
    description:
      "Rolling window (calendar days) over which per-model RMSE is computed for inverse-skill forecast blending. Increase for stability; decrease for faster adaptation.",
  },
  {
    group: "Forecasting",
    key: "FORECAST_MODEL_PERSISTENCE_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Opt-in: persist the trained CNN-LSTM (.keras + both MinMaxScalers) and Prophet model to disk per ticker instead of retraining from scratch every cycle.",
  },
  {
    group: "Forecasting",
    key: "FORECAST_MODEL_RETRAIN_DAYS",
    type: "number",
    value: 7,
    default: 7,
    min: 1,
    max: 3650,
    step: 1,
    description:
      "Days a persisted CNN-LSTM/Prophet model artifact remains valid before the next generate_forecast() call for that ticker triggers a fresh fit. Only consulted when FORECAST_MODEL_PERSISTENCE_ENABLED=True.",
  },
  {
    group: "Forecasting",
    key: "BETA_LOOKBACK_DAYS",
    type: "number",
    value: 504,
    default: 504,
    min: 1,
    max: 3650,
    step: 1,
    description:
      "Trailing calendar days of daily returns used to compute beta in the Yahoo-derived fundamentals engine (Cov(stock,SPY)/Var(SPY)). ~2 years.",
  },
  {
    group: "Forecasting", key: "BERT_LLA_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Master switch for the BERT-LLA multi-horizon forecaster (forecasting/bert_lla.py -- PyTorch dual-LSTM + self-attention, three registered ablations: lstm_baseline, lstm_attention, bert_lla). False (the default) is a complete no-op: ForecastingEngine.run_bert_lla_forecast() returns the zero sentinel without ever touching torch. Requires the optional torch package (already in requirements-optional.txt for local FinBERT inference) -- absent, the same zero-sentinel behavior applies regardless of this flag.",
  },
  {
    group: "Forecasting", key: "BERT_LLA_WINDOW_SIZE", type: "number",
    value: 22, default: 22, min: 1, max: 1000, step: 1,
    description: "Lookback window (trading days) BERT-LLA's LSTM layers consume, replacing the CNN-LSTM path's hardcoded LSTM_LOOKBACK=60 -- matches the source methodology's 22-trading-day window. Only consulted once BERT_LLA_ENABLED is True.",
  },
  {
    group: "Forecasting", key: "BERT_LLA_MIN_SENTIMENT_COVERAGE", type: "number",
    value: 0.5, default: 0.5, min: 0.0, max: 1.0, step: 0.05,
    description: "Hard gate for the 'bert_lla' ablation specifically (not lstm_baseline/lstm_attention, which consume no sentiment): the minimum fraction of rows in the feature window that must have an OBSERVED composite-sentiment-index reading (signals.sentiment_index) before training proceeds. Below this threshold, run_bert_lla_forecast returns the zero sentinel rather than training on a mostly mask-zeroed sentiment channel (CONSTRAINT #4) -- SENTIMENT_INGESTION_ENABLED defaults False and SENTIMENT_PIT_MIN_MONTHS=6 is this platform's own bar for trusting sentiment history, so this gate will bind for months after an operator first enables sentiment ingestion, by design. Only consulted once BERT_LLA_ENABLED is True.",
  },
  {
    group: "Forecasting", key: "BERT_LLA_BLEND_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Whether the 'bert_lla' ablation's price (not lstm_baseline/lstm_attention -- those are comparison-only and NEVER blend-eligible regardless of this flag) is added to ForecastingEngine's model_forecasts dict and therefore influences the live skill-weighted blended forecast. False (the default): bert_lla still RECORDS to forecast_errors for the webapp's model-comparison chart, but its error history accrues honestly before it can ever move a recommendation -- mirrors FORECAST_SKILL_WEIGHTING_ENABLED's 'measure first, act later' posture. Only consulted once BERT_LLA_ENABLED is True.",
  },
  {
    group: "Forecasting", key: "BERT_LLA_ABLATION_ENABLED", type: "boolean",
    value: false, default: false,
    description: "When True, generate_forecast() runs all three BERT-LLA ablations (lstm_baseline, lstm_attention, bert_lla) instead of just 'bert_lla' alone -- three PyTorch trainings per ticker per cycle instead of one. False (the default) keeps the marginal compute cost to a single model. Only consulted once BERT_LLA_ENABLED is True.",
  },
  {
    group: "Forecasting", key: "CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED", type: "boolean",
    value: true, default: true,
    description: "Fix for the CNN-LSTM/TensorFlow deadlock documented in docs/known_issues/cnn_lstm_tf_deadlock.md (issue #381) -- TensorFlow and pyarrow each ship an independently-compiled copy of the same Abseil sync primitive, and if pandas/pyarrow initialize first in a process, the first real multi-threaded TF eager op (a Conv1D/LSTM .fit()) deadlocks forever. When True (the default), ForecastingEngine.run_cnn_lstm_forecast runs the actual TF-touching work (model fit+predict, cached-model load+predict) in a persistent worker pool (repo-root cnn_lstm_process_pool.py / cnn_lstm_worker.py) launched via subprocess.Popen, so a fresh interpreter's import order can no longer matter -- protects every caller, not just the entry points with their own guarded import-order defense. Any subprocess failure degrades to the zero-result sentinel rather than crashing the pipeline (CONSTRAINT #6). Set False only to restore the legacy in-process path, which re-exposes the process-scope import-order hazard.",
  },
  {
    group: "Forecasting", key: "CNN_LSTM_PROCESS_POOL_WORKERS", type: "number",
    value: 1, default: 1, min: 1, max: 64, step: 1,
    description: "Worker-process count for the CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED pool (repo-root cnn_lstm_process_pool.py). Workers are persistent (survive across tickers/cycles, each pays the TensorFlow import cost only once) so CNN-LSTM fits queued from pipeline/production_steps.py's per-ticker ThreadPoolExecutor fan-out share this fixed-size pool rather than spawning a fresh interpreter per ticker. Keep small -- each worker holds a full TensorFlow process in memory.",
  },
  {
    group: "Forecasting", key: "CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS", type: "number",
    value: 300, default: 300, min: 1, max: 3600, step: 10,
    description: "Max seconds to wait for a single CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED fit-or-predict call before giving up and falling back to the zero-result sentinel (never blocks the pipeline indefinitely). 50 epochs with EarlyStopping(patience=5) on the modest window sizes this codebase trains on should complete well within the default.",
  },
  {
    group: "Forecasting", key: "FORECAST_CNN_LSTM_WALKFORWARD_SCALING", type: "boolean",
    value: false, default: false,
    description: "Opt-in, stricter alternative to ForecastingEngine.fit_scalers_on_train's single train/reserve MinMaxScaler split. When True, ForecastingEngine.run_cnn_lstm_forecast builds training windows via fit_scalers_walkforward_windows instead: each supervised window is scaled using only an expanding min/max computed from rows strictly at/before that window's own end. The final live inference window is unaffected either way. False (the default) reproduces pre-existing behavior exactly -- matches the FORECAST_USE_GARCH_SIGMA opt-in convention. Intended for high-fidelity walk-forward backtesting, not the live pipeline; costs more compute per fit.",
  },
  {
    group: "Forecasting", key: "LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Opt-in: LGBMCrossSectionalRanker.train() calls CombinatorialPurgedCV.split() directly on the (date, ticker) MultiIndex panel (PR #648's native MultiIndex support) instead of flattening to a date-only index first before purging/embargoing. Default False preserves today's exact flatten-path behavior for every existing caller -- train()'s own use_native_multiindex_cv kwarg always overrides this when explicitly passed. The native path additionally REQUIRES an explicit t1 (raises ValueError otherwise), while the flatten path keeps silently synthesizing a default t1 when none is supplied.",
  },
  // ---- Market Data ----
  {
    // Honest absent value: settings.py's real default IS None (auto-select
    // by key availability) -- never fabricated as "fmp"/"yfinance".
    group: "Market Data",
    key: "MARKET_DATA_PROVIDER",
    type: "enum",
    value: null,
    default: null,
    options: ["fmp", "yfinance"],
    description:
      "Force a specific market-data backend: 'fmp' or 'yfinance'. When unset the platform auto-selects based on key availability. Setting FMP_API_KEY alone NEVER auto-elects FMP: FMP is chosen only by explicitly setting this to 'fmp', so an operator who adds the key to enable the analyst or earnings feed does not silently have their quote/bars source change underneath them. FMP quotes/bars additionally require FMP_QUOTES_ENABLED / FMP_BARS_ENABLED (the two-gate convention).",
  },
  {
    group: "Market Data",
    key: "MARKET_DATA_QUOTE_TTL_SECONDS",
    type: "number",
    value: 30,
    default: 30,
    min: 0,
    max: 86400,
    step: 1,
    description:
      "In-process quote cache TTL in seconds (never persisted to disk).",
  },
  {
    group: "Market Data",
    key: "MARKET_DATA_BARS_TTL_SECONDS",
    type: "number",
    value: 900,
    default: 900,
    min: 0,
    max: 86400,
    step: 1,
    description:
      "In-process OHLCV intraday-bars cache TTL in seconds (never persisted to disk).",
  },
  {
    group: "Market Data",
    key: "FUNDAMENTALS_SOURCE",
    type: "enum",
    value: "yahoo",
    default: "yahoo",
    options: ["yahoo", "yfinance_info", "fmp"],
    description:
      "Primary fundamentals backend: 'yahoo' (statement-derived, default), 'yfinance_info' (raw .info fallback), or 'fmp' (Financial Modeling Prep — see section 25). Setting FMP_API_KEY alone NEVER auto-elects FMP: it must be chosen explicitly here, so adding the key for one feed cannot silently change what every valuation metric is computed from. 'fmp' additionally requires FMP_FUNDAMENTALS_ENABLED=true (the two-gate convention); with either half missing the Yahoo path is used, exactly as today.",
  },
  {
    group: "Market Data", key: "HISTORICAL_STORE_ENABLED", type: "boolean",
    value: true, default: true,
    description: "Master flag for HistoricalStore DB routing. When True, OHLCV bars and account snapshots are read from / written to quant_platform.db. First call for a symbol = full BARS_BACKFILL_DAYS backfill; subsequent calls = delta only. Set False to reproduce pre-Tier-2.3 behavior (all fetches go directly to the live provider).",
  },
  // ---- Runtime & Ops ----
  {
    group: "Runtime & Ops",
    key: "DASHBOARD_REFRESH_SECONDS",
    type: "number",
    value: 1800,
    default: 1800,
    min: 1,
    max: 86400,
    step: 1,
    description:
      "Auto-refresh interval for the Streamlit observability dashboard (seconds). Default 1800 = 30 min.",
  },
  {
    group: "Runtime & Ops",
    key: "PROGRESS_POLL_SECONDS",
    type: "number",
    value: 5,
    default: 5,
    min: 1,
    max: 3600,
    step: 1,
    description:
      "Poll interval (seconds) for the Launcher pipeline-progress indicator.",
  },
  {
    group: "Runtime & Ops",
    key: "LOG_LEVEL",
    type: "enum",
    value: "INFO",
    default: "INFO",
    options: ["DEBUG", "INFO", "WARNING", "ERROR"],
    description: null,
  },
  {
    group: "Runtime & Ops",
    key: "ADVISORY_REUSE_PIPELINE_COMPUTE",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Opt-in, OUTPUT-CHANGING: main_orchestrator.py's advisory overlay reuses run_pipeline's already-computed GARCH/forecast values for that ticker instead of independently refitting a second time. When False (the default), every advisory-overlay call refits independently, reproducing the exact pre-dedup behavior.",
  },
  {
    group: "Runtime & Ops",
    key: "ADVISORY_ONLY",
    type: "boolean",
    value: true,
    default: true,
    description:
      "When True, ALL broker order submission is suppressed. The pipeline still runs end-to-end (signals, sizing, HTML report, JSON payload) but order execution returns immediately. Set False ONLY when broker execution is intentionally re-enabled.",
  },
  {
    group: "Runtime & Ops", key: "ROBINHOOD_AUTO_REFRESH_ENABLED", type: "boolean",
    value: false, default: false,
    description: "When True, fetch_account_snapshot() automatically re-logs-in to Robinhood whenever the cached snapshot exceeds max_age_hours. Default False: device-approval login needs a human to tap approve, so an unattended background attempt can never succeed — live login only happens when explicitly forced (--refresh-account, or the webapp's Connect/Refresh flows); all other callers get the cached snapshot regardless of staleness.",
  },
  {
    group: "Runtime & Ops", key: "RUNTIME_FLAGS_REFRESH_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Periodically re-check output/runtime_flags.json for changes written by another process and apply them onto this daemon's live settings. False (default) preserves today's exact behavior -- a cross-process write only takes effect on next restart.",
  },
  {
    group: "Runtime & Ops", key: "RUNTIME_FLAGS_REFRESH_INTERVAL_SECONDS", type: "number",
    value: 30, default: 30, min: 1, max: 3600, step: 1,
    description: "Seconds between the orchestrator daemon's checks of output/runtime_flags.json for cross-process changes. Only consulted when RUNTIME_FLAGS_REFRESH_ENABLED is True.",
  },
  {
    group: "Runtime & Ops",
    key: "DAEMON_SHUTDOWN_TIMEOUT_SECONDS",
    type: "number",
    value: 25.0,
    default: 25.0,
    description: "Total seconds budgeted for the orchestrator daemon's graceful teardown (Control API + Pilots API drain, timer-thread join, final in-flight-run poll). Does not wait out an in-flight pipeline cycle. Must stay below the outer supervisor timeouts (launch_app.command, launchd ExitTimeOut, systemd TimeoutStopSec) or shutdown gets worse, not better.",
    min: 1.0,
    max: 300.0,
    step: 1.0,
  },
  {
    group: "Runtime & Ops",
    key: "PIPELINE_STALL_ALERT_SECONDS",
    type: "number",
    value: 3600,
    default: 3600,
    description: "Threshold (seconds) of no progress.json update while state='running' before the stall alert fires. Set well above DATA_FETCH_TASK_TIMEOUT_SECONDS's worst case and any legitimate single pipeline-stage duration -- still two orders of magnitude below the multi-hour/multi-day hang this was added to catch. Raised from the original 1800 to 3600 on 2026-08-27, in lockstep with PIPELINE_STEP_TIMEOUT_SECONDS's 900->1800 bump (see that field's own description for the incident that prompted it) -- kept at exactly 2x PIPELINE_STEP_TIMEOUT_SECONDS to preserve the original design margin: the per-step timeout should always fire, and let the daemon reschedule, well before this stall alert would ever need to.",
    min: 60,
    max: 86400,
    step: 60,
  },
  // ---- Advanced / Config (the 7 keys the real Streamlit tab's own
  // _SETTINGS_LAYOUT, gui/panels/settings_manager.py:36-77, already served) ----
  {
    group: "Advanced / Config",
    key: "SECTOR_FORECAST_CONFIG_PATH",
    type: "string",
    value: "forecasting/sector_configs.json",
    default: "forecasting/sector_configs.json",
    description:
      "Path to the committed per-sector forecast config artifact (model+horizon per sector, derived from an offline walk-forward backtest). Loaded once at ForecastingEngine init; the hardcoded default dict is used as fallback when the file is missing or invalid.",
  },
  {
    group: "Advanced / Config",
    key: "SECTOR_FORECAST_CONFIGS",
    type: "string",
    value: "{}",
    default: "{}",
    description:
      'Optional per-sector override merged OVER the artifact/hardcoded default. JSON dict in .env, e.g. {"Technology": {"days": 30, "model": "MC"}}. Empty dict (the default) leaves the artifact/hardcoded default unchanged (fully backward-compatible).',
  },
  {
    group: "Advanced / Config",
    key: "PROMPT_REGISTRY_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch. False (default) → baseline-only, zero network calls. Set True to enable remote manifest fetch and cache.",
  },
  {
    group: "Advanced / Config",
    key: "PROMPT_REGISTRY_BACKEND",
    type: "string",
    value: "http",
    default: "http",
    description:
      "Storage backend: 'http' (default, protected HTTPS endpoint), 'local' (LocalJSONStore from a file path), or 'firestore' (lazy import).",
  },
  {
    group: "Advanced / Config",
    key: "ORCHESTRATOR_DAEMON_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Route the desktop shell's always-on refresh loop and the Launcher tab's manual run trigger through the persistent orchestrator daemon instead of spawning a fresh subprocess per cycle. False (default) preserves today's exact subprocess behavior everywhere.",
  },
  {
    group: "Advanced / Config",
    key: "ORCHESTRATOR_EXTENDED_HOURS_ONLY",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Skip automatic interval-triggered pipeline cycles (daemon timer and main.py --interval) outside the 4am-8pm ET weekday window (engine.advisory_agent.is_extended_hours) -- not strict 9:30-16:00 RTH. Manual/on-demand triggers (webapp buttons, API calls) are never gated. No holiday calendar is applied (same known limitation as is_us_market_open); default True fixes previously-unconditional 24/7 automatic runs.",
  },
  {
    group: "Advanced / Config",
    key: "CORS_ALLOWED_ORIGINS",
    type: "string",
    value:
      '["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5173"]',
    default:
      '["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5173"]',
    description:
      'Allowed browser origins for the read-only State API / Pilots API CORS policy. JSON array in .env, e.g. ["http://localhost:3000", "https://app.example.com"].',
  },
  {
    key: "USE_DUAL_MOMENTUM_OVERLAY",
    value: true,
    default: true,
    type: "boolean",
    description:
      "When True, the Dual Momentum allocator pre-screens the ticker list each run. If the allocator selects the safe asset (BIL), tickers in the risky universes (SPY, VEU) have their Kelly Target set to 0.0.",
    group: "Position Sizing",
  },
  {
    key: "DUAL_MOMENTUM_SAFE_ASSET",
    value: "BIL",
    default: "BIL",
    type: "string",
    description:
      "Ticker used as the safe/defensive asset in the Dual Momentum overlay.",
    group: "Position Sizing",
  },
  {
    key: "DUAL_MOMENTUM_RISKY_ASSETS",
    value: '["SPY", "VEU"]',
    default: '["SPY", "VEU"]',
    type: "string",
    description:
      "Risky ETFs compared in the Dual Momentum cross-sectional filter.",
    group: "Position Sizing",
  },
  {
    key: "EXECUTION_PRIORITY_QUEUE_ENABLED",
    value: false,
    default: false,
    type: "boolean",
    description:
      "Opt-in: route OrderIntents through execution/priority_queue.py's leaky-bucket priority queue before submission, prioritizing risk-reducing (SELL/TRIM) intents over new BUYs when nearing the submission-rate budget. Does NOT replace or bypass MAX_ORDER_RATE_PER_MIN's hard cap (execution/risk_gate.py) or execution/kill_switch.py -- both remain the sole authorization gate, checked at submission exactly as before. False (default) preserves the exact current sequential per-row submission order -- matches the FORECAST_USE_GARCH_SIGMA opt-in convention.",
    group: "Risk Gate",
  },
  {
    key: "EXECUTION_QUEUE_LEAK_RATE_PER_SEC",
    value: 2.0,
    default: 2.0,
    type: "number",
    description:
      "Leaky-bucket drain rate (order submissions/sec) when EXECUTION_PRIORITY_QUEUE_ENABLED=true. Only paces submission ordering within a single cycle's queue drain -- independent of MAX_ORDER_RATE_PER_MIN's separate 60s rolling-window cap.",
    group: "Risk Gate",
  },
  {
    key: "FLATTEN_ON_KILL",
    value: false,
    default: false,
    type: "boolean",
    description:
      "Log CRITICAL position-flatten reminder when kill switch activates.",
    group: "Risk Gate",
  },
  {
    key: "BERT_LLA_ENABLED",
    value: true,
    default: true,
    type: "boolean",
    description:
      "Master switch for the BERT-LLA multi-horizon forecaster (forecasting/bert_lla.py -- PyTorch dual-LSTM + self-attention, three registered ablations: lstm_baseline, lstm_attention, bert_lla). False (the default) is a complete no-op: ForecastingEngine.run_bert_lla_forecast() returns the zero sentinel without ever touching torch. Requires the optional torch package (already in requirements-optional.txt for local FinBERT inference) -- absent, the same zero-sentinel behavior applies regardless of this flag.",
    group: "Forecasting",
  },
  {
    key: "BERT_LLA_WINDOW_SIZE",
    value: 22,
    default: 22,
    type: "number",
    description:
      "Lookback window (trading days) BERT-LLA's LSTM layers consume, replacing the CNN-LSTM path's hardcoded LSTM_LOOKBACK=60 -- matches the source methodology's 22-trading-day window. Only consulted once BERT_LLA_ENABLED is True.",
    group: "Forecasting",
  },
  {
    key: "BERT_LLA_MIN_SENTIMENT_COVERAGE",
    value: 0.5,
    default: 0.5,
    type: "number",
    description:
      "Hard gate for the 'bert_lla' ablation specifically (not lstm_baseline/lstm_attention, which consume no sentiment): the minimum fraction of rows in the feature window that must have an OBSERVED composite-sentiment-index reading (signals.sentiment_index) before training proceeds. Below this threshold, run_bert_lla_forecast returns the zero sentinel rather than training on a mostly mask-zeroed sentiment channel (CONSTRAINT #4) -- SENTIMENT_INGESTION_ENABLED defaults False and SENTIMENT_PIT_MIN_MONTHS=6 is this platform's own bar for trusting sentiment history, so this gate will bind for months after an operator first enables sentiment ingestion, by design. Only consulted once BERT_LLA_ENABLED is True.",
    group: "Forecasting",
  },
  {
    key: "BERT_LLA_BLEND_ENABLED",
    value: true,
    default: true,
    type: "boolean",
    description:
      "Whether the 'bert_lla' ablation's price (not lstm_baseline/lstm_attention -- those are comparison-only and NEVER blend-eligible regardless of this flag) is added to ForecastingEngine's model_forecasts dict and therefore influences the live skill-weighted blended forecast. False (the default): bert_lla still RECORDS to forecast_errors for the webapp's model-comparison chart, but its error history accrues honestly before it can ever move a recommendation -- mirrors FORECAST_SKILL_WEIGHTING_ENABLED's 'measure first, act later' posture. Only consulted once BERT_LLA_ENABLED is True.",
    group: "Forecasting",
  },
  {
    key: "BERT_LLA_ABLATION_ENABLED",
    value: true,
    default: true,
    type: "boolean",
    description:
      "When True, generate_forecast() runs all three BERT-LLA ablations (lstm_baseline, lstm_attention, bert_lla) instead of just 'bert_lla' alone -- three PyTorch trainings per ticker per cycle instead of one. False (the default) keeps the marginal compute cost to a single model. Only consulted once BERT_LLA_ENABLED is True.",
    group: "Forecasting",
  },
  {
    key: "CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED",
    value: true,
    default: true,
    type: "boolean",
    description:
      "Fix for the CNN-LSTM/TensorFlow deadlock documented in docs/known_issues/cnn_lstm_tf_deadlock.md (issue #381). Root cause: TensorFlow and pyarrow each ship an independently-compiled copy of the same Abseil sync primitive; whichever library's Python-level init runs first in the PROCESS wins that symbol, and if pandas/pyarrow initialize first, the first real multi-threaded TF eager op (a Conv1D/LSTM .fit()) deadlocks forever. Reordering forecasting_engine.py's own imports (always-on, unconditional) only helps when this module is the first thing in the whole process to touch pandas -- true in an isolated test script, false in main.py/main_orchestrator.py/pipeline/production_steps.py, which all import pandas before forecasting_engine is ever reached (those three files carry their own guarded `import tensorflow` before their own `import pandas` as a defense-in-depth second layer -- see Fix 2 in the doc -- but that convention is unenforced for any OTHER entry point, script, or notebook that happens to reach this code path). When True (the default), ForecastingEngine.run_cnn_lstm_forecast runs the actual TF-touching work (model fit+predict, and cached-model load+predict) in a persistent worker pool (repo-root cnn_lstm_process_pool.py) whose worker module (repo-root cnn_lstm_worker.py -- deliberately NOT inside forecasting/, since that package's __init__ eagerly imports pandas) imports tensorflow before anything else and runs as its own genuine OS process, launched via subprocess.Popen -- a fresh interpreter per worker means the parent process's import order can no longer matter, unlike the module-level reorder alone or the entry-point guards. This is what actually removes the process-scope constraint, rather than merely mitigating it by convention: it protects EVERY caller, known or not, not just the three files that remember the guard. As of 2026-08-04 (Round 8 of the known-issues doc), workers are launched with subprocess.Popen rather than multiprocessing -- a second, distinct deadlock (unrelated to the Abseil ODR collision above) was found in multiprocessing-managed worker processes specifically; see Round 8 for the full ablation matrix. All feature engineering / windowing / scaling stays in the parent process unchanged (pandas-only, never touches TF). Any subprocess failure (timeout, a dead/unresponsive worker, real training exception) is caught by run_cnn_lstm_forecast's existing outer try/except and degrades to the zero-result sentinel -- never crashes the pipeline (CONSTRAINT #6). This default flipped True on 2026-07-31 (Round 7 of the known-issues doc) once Round 6 (2026-07-27) verified subprocess isolation end-to-end against the real native deadlock on real production data in the actual macOS arm64 + Framework-Python environment the deadlock was originally confirmed on -- the earlier caveat about this being verified only against the mocked test suite no longer applies. Set False only to restore the legacy in-process path (byte-identical to this flag's original pre-2026-07-31 default); doing so re-exposes the process-scope import-order hazard for any entry point that doesn't carry its own guarded `import tensorflow` before `import pandas`/`import pyarrow`.",
    group: "Forecasting",
  },
  {
    key: "CNN_LSTM_PROCESS_POOL_WORKERS",
    value: 1,
    default: 1,
    type: "number",
    description:
      "Worker-process count for the CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED pool (repo-root cnn_lstm_process_pool.py). Workers are persistent (survive across tickers/cycles, each pays the TensorFlow import cost only once) so CNN-LSTM fits queued from pipeline/production_steps.py's per-ticker ThreadPoolExecutor fan-out share this fixed-size pool rather than spawning a fresh interpreter per ticker. Keep small -- each worker holds a full TensorFlow process in memory.",
    group: "Forecasting",
  },
  {
    key: "CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS",
    value: 300,
    default: 300,
    type: "number",
    description:
      "Max seconds to wait for a single CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED fit-or-predict call before giving up and falling back to the zero-result sentinel (never blocks the pipeline indefinitely -- the entire point of this fix is to replace an unbounded hang with a bounded, recoverable failure). 50 epochs with EarlyStopping(patience=5) on the modest window sizes this codebase trains on should complete well within the default.",
    group: "Forecasting",
  },
  {
    key: "FORECAST_CNN_LSTM_WALKFORWARD_SCALING",
    value: true,
    default: true,
    type: "boolean",
    description:
      "Opt-in, stricter alternative to ForecastingEngine.fit_scalers_on_train's single train/reserve MinMaxScaler split. That split is already leak-free for the live single-shot forecast (the emitted forecast never depends on future data relative to inference time), but an EARLY training window's scale still reflects statistics pooled from LATER rows within the train span via the one shared scaler. When True, ForecastingEngine.run_cnn_lstm_forecast builds training windows via fit_scalers_walkforward_windows instead: each supervised window is scaled using only an expanding min/max computed from rows strictly at/before that window's own end (vectorized via numpy cumulative min/max, not a per-window sklearn refit). The final live inference window is unaffected either way -- it still uses the train-span scaler, since at inference time 'now' truly is the most recent data available. False (the default) reproduces pre-existing behavior exactly -- matches the FORECAST_USE_GARCH_SIGMA opt-in convention. Intended for high-fidelity walk-forward backtesting, not the live pipeline; costs more compute per fit.",
    group: "Forecasting",
  },
  {
    key: "LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED",
    value: false,
    default: false,
    type: "boolean",
    description:
      "Opt-in: LGBMCrossSectionalRanker.train() calls CombinatorialPurgedCV.split() directly on the (date, ticker) MultiIndex panel (PR #648's native MultiIndex support) instead of flattening to a date-only index first before purging/embargoing. Default False preserves today's exact flatten-path behavior for every existing caller -- train()'s own use_native_multiindex_cv kwarg always overrides this when explicitly passed (True or False); this setting is only consulted when a caller leaves that kwarg unset (None). The native path additionally REQUIRES an explicit t1 (raises ValueError otherwise) -- CombinatorialPurgedCV cannot safely synthesize a default t1 across a MultiIndex -- while the flatten path keeps silently synthesizing a 'next row' default t1 when none is supplied, exactly as it always has.",
    group: "Forecasting",
  },
  {
    key: "HISTORICAL_STORE_ENABLED",
    value: true,
    default: true,
    type: "boolean",
    description:
      "Master flag for HistoricalStore DB routing. When True, OHLCV bars and account snapshots are read from / written to quant_platform.db. First call for a symbol = full BARS_BACKFILL_DAYS backfill; subsequent calls = delta only. Set False to reproduce pre-Tier-2.3 behavior (all fetches go directly to the live provider).",
    group: "Market Data",
  },
  {
    key: "ROBINHOOD_AUTO_REFRESH_ENABLED",
    value: true,
    default: true,
    type: "boolean",
    description:
      "When True, fetch_account_snapshot() automatically re-logs-in to Robinhood whenever the cached snapshot exceeds max_age_hours. Default False: device-approval login needs a human to tap approve, so an unattended background attempt can never succeed — live login only happens when explicitly forced (--refresh-account, or the webapp's Connect/Refresh flows); all other callers get the cached snapshot regardless of staleness.",
    group: "Runtime & Ops",
  },
  {
    key: "RUNTIME_FLAGS_REFRESH_ENABLED",
    value: false,
    default: false,
    type: "boolean",
    description:
      "Periodically re-check output/runtime_flags.json for changes written by another process and apply them onto this daemon's live settings. False (default) preserves today's exact behavior -- a cross-process write only takes effect on next restart.",
    group: "Runtime & Ops",
  },
  {
    key: "RUNTIME_FLAGS_REFRESH_INTERVAL_SECONDS",
    value: 30,
    default: 30,
    type: "number",
    description:
      "Seconds between the orchestrator daemon's checks of output/runtime_flags.json for cross-process changes. Only consulted when RUNTIME_FLAGS_REFRESH_ENABLED is True.",
    group: "Runtime & Ops",
  },
  {
    key: "GRAVITY_REQUIRE_NATIVE",
    value: false,
    default: false,
    type: "boolean",
    description: "Require native implementation for Gravity Review Suite.",
    group: "Advanced / Config",
  },
  {
    key: "META_LABELING_ENABLED",
    value: true,
    default: true,
    type: "boolean",
    description:
      "Enable startup registration of trained meta-labelers into global_meta_registry (ml/meta_bootstrap.py). No-op when no saved model exists; set False to disable meta-labeling entirely.",
    group: "Advanced / Config",
  },
  {
    key: "NEWS_HISTORY_CAPTURE_ENABLED",
    value: true,
    default: true,
    type: "boolean",
    description:
      "When True, NewsCatalystSignal.pre_compute() writes each cycle's live news-sentiment scores to HistoricalStore's news_history table (via HistoricalStore.save_news_sentiment()), forward-archiving real point-in-time history so a genuine backtest becomes possible after enough history accumulates. No backtest reads this table yet. Dead-lettered: any capture failure is logged and never crashes the pipeline. Set False to disable forward-going capture entirely.",
    group: "Advanced / Config",
  },
  {
    key: "PIT_CAPTURE_ENABLED",
    value: true,
    default: true,
    type: "boolean",
    description:
      "When True, the orchestrator writes TODAY's cross-sectional PIT feature snapshot to ml/data/cache/ (via ml.data.store.PITFeatureStore) right after signal pre_compute, so the ML training panel accumulates real point-in-time snapshots for future incremental retrains. Dead-lettered: any capture failure is logged and never crashes the pipeline. Set False to disable forward-going capture entirely.",
    group: "Advanced / Config",
  },
  {
    key: "SENTIMENT_AUDIT_ENABLED",
    value: true,
    default: true,
    type: "boolean",
    description:
      "When True, sentiment-ingestion sources write each ingested document to HistoricalStore's sentiment_ingestion_audit table (via HistoricalStore.save_sentiment_documents()) -- the per-document point-in-time archive underlying the credibility-weighted sentiment signal (Sentiment Pipeline Phase 2+). Same on/off shape as NEWS_HISTORY_CAPTURE_ENABLED. Dead-lettered: any capture failure is logged and never crashes the pipeline. Has no effect while SENTIMENT_INGESTION_ENABLED is False (nothing is ever fetched to archive in the first place).",
    group: "Advanced / Config",
  },
  {
    key: "SENTIMENT_DESENTENCIZE_ENABLED",
    value: false,
    default: false,
    type: "boolean",
    description:
      "When True, ingested document text has periods replaced with semicolons before FinBERT scoring (a real but marginal trick to discourage sentence-boundary truncation on run-on social posts). Off by default: it can corrupt numerics ($4.50), cashtags ($AAPL), and abbreviations (U.S.) -- see tests/test_sentiment_sources.py's desentencize-safety cases before enabling.",
    group: "Advanced / Config",
  },
  {
    key: "EXCURSION_INTRADAY_ENABLED",
    value: false,
    default: false,
    type: "boolean",
    description:
      "Opt-in (Phase-1 audit item B2): evaluation_engine.calculate_edge_ratio consumes hourly bars (MarketDataProvider.get_intraday_bars(..., interval='1h')) over the trade hold window instead of daily bars, for finer Maximum Favorable/Adverse Excursion (MFE/MAE) resolution on same-day or short holds. Daily bars are already genuine (not fabricated) and adequate for multi-day holds; this only adds intraday precision. Any hourly-fetch failure (provider error, unsupported interval, empty result) degrades to the existing daily-bar path rather than raising -- never blocks the excursion calculation. False (the default) reproduces pre-existing daily-only behavior exactly -- matches the FORECAST_USE_GARCH_SIGMA opt-in convention.",
    group: "Advanced / Config",
  },
  {
    key: "VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED",
    value: false,
    default: false,
    type: "boolean",
    description:
      "Opt-in fix for validation/metrics.py::deflated_sharpe_ratio's n_trials<=1 shortcut, which unconditionally returns 1.0 (a perfect deflated Sharpe) for any single-trial strategy instead of actually computing the DSR test statistic -- so a strategy with only one configuration always passes the 'DSR > 0.95' deployability gate regardless of how weak its observed Sharpe, skew, or kurtosis actually are. This bug is directly relied on today by 5 STRATEGY_REGISTRY strategies that hit DSR=1.000 exactly via this shortcut -- multifactor_lowvol_size, garch_vol_target, cross_sectional_momentum, relative_strength_xsec, timeseries_momentum (confirmed in docs/VALIDATION_STRATEGY_FIX_LOG.md) -- and are currently recorded deployable=True, so the corrected math ships opt-in rather than silently changing any currently-recorded verdict. False (the default) reproduces the pre-existing `return 1.0` shortcut byte-for-byte. True sets sr_0 = 0.0 (mathematically correct: with genuinely only one trial there is no multiple-testing selection-bias penalty to deflate for) and falls through to compute the REAL z_stat/norm.cdf from the actual sr_observed/skew/kurtosis/n_observations, instead of short-circuiting to a hardcoded perfect pass. Flipping this on requires a follow-up session with live-market data access to re-run scripts/refresh_validations.py against the 5 strategies named above and update docs/VALIDATION_STRATEGY_FIX_LOG.md before this can ever change what's actually live -- exactly like this codebase's other opt-in correctness levers (e.g. VALIDATION_HARNESS_OOS_GATE_ENABLED above).",
    group: "Advanced / Config",
  },
  {
    key: "VALIDATION_HARNESS_OOS_GATE_ENABLED",
    value: false,
    default: false,
    type: "boolean",
    description:
      "Opt-in fix for StrategyValidationHarness's deployability gate. Two related integrity gaps: (1) report.sharpe/max_dd/sortino/calmar/hit_rate/avg_trade_pct/turnover were computed from self.strategy_fn(X, y, X, y) -- a 'test' set IDENTICAL to the training set, i.e. an IN-SAMPLE number feeding the 'net-of-cost Sharpe > 0.5' / 'MaxDD < 30%' deployability criteria -- while only PBO/DSR were genuinely out-of-sample (via CombinatorialPurgedCV). (2) CombinatorialPurgedCV's own DSR/PBO Sharpes were computed on GROSS (cost-free) returns even though the in-sample Sharpe/MaxDD leg applied _apply_cost_model's turnover-scaled cost -- an inconsistent cost basis between the two gate legs. When True, run_cpcv_evaluation applies the same turnover-scaled cost model to every CPCV path's train/test returns before any Sharpe/PBO/DSR/drawdown statistic is computed from them, and the harness's reported sharpe/max_dd/sortino/calmar/hit_rate/avg_trade_pct/turnover become the MEAN of each metric computed independently on every CPCV path's own genuinely held-out (purged+embargoed) OOS returns for the DSR-selected strategy, instead of the full-sample in-sample fit -- see run_cpcv_evaluation's docstring for why this is a per-path mean rather than one concatenated equity curve (CPCV's combinatorial test blocks are deliberately reused across paths). equity_curve/benchmark_curve/macro_benchmark_curve are UNCHANGED either way (still the full-sample series) -- a single non-overlapping OOS equity curve needs the AFML CPCV backtest-path-recombination algorithm, not implemented here (a real, separate follow-up, not silently faked). False (the default) reproduces pre-existing behavior exactly: every currently-recorded docs/VALIDATION_STRATEGY_FIX_LOG.md PBO/DSR/Sharpe/MaxDD baseline for the registered STRATEGY_REGISTRY fleet was measured with this flag off, and this sandboxed dev/CI environment has no live-market network access to re-verify the fleet against the corrected numbers -- flipping this on requires re-running scripts/refresh_validations.py against live data and updating that log, exactly like this codebase's other opt-in correctness levers (e.g. FORECAST_CNN_LSTM_WALKFORWARD_SCALING above).",
    group: "Advanced / Config",
  },
  {
    group: "Advanced / Config", key: "GRAVITY_REQUIRE_NATIVE", type: "boolean",
    value: false, default: false,
    description: "Require native implementation for Gravity Review Suite.",
  },
  // ---- ML, Data Capture & Audit ----
  {
    group: "ML, Data Capture & Audit", key: "META_LABELING_ENABLED", type: "boolean",
    value: true, default: true,
    description: "Enable startup registration of trained meta-labelers into global_meta_registry (ml/meta_bootstrap.py). No-op when no saved model exists; set False to disable meta-labeling entirely.",
  },
  {
    group: "ML, Data Capture & Audit", key: "NEWS_HISTORY_CAPTURE_ENABLED", type: "boolean",
    value: true, default: true,
    description: "When True, NewsCatalystSignal.pre_compute() writes each cycle's live news-sentiment scores to HistoricalStore's news_history table (via HistoricalStore.save_news_sentiment()), forward-archiving real point-in-time history so a genuine backtest becomes possible after enough history accumulates. No backtest reads this table yet. Dead-lettered: any capture failure is logged and never crashes the pipeline. Set False to disable forward-going capture entirely.",
  },
  {
    group: "ML, Data Capture & Audit", key: "PIT_CAPTURE_ENABLED", type: "boolean",
    value: true, default: true,
    description: "When True, the orchestrator writes TODAY's cross-sectional PIT feature snapshot to ml/data/cache/ (via ml.data.store.PITFeatureStore) right after signal pre_compute, so the ML training panel accumulates real point-in-time snapshots for future incremental retrains. Dead-lettered: any capture failure is logged and never crashes the pipeline. Set False to disable forward-going capture entirely.",
  },
  {
    group: "ML, Data Capture & Audit", key: "SENTIMENT_AUDIT_ENABLED", type: "boolean",
    value: true, default: true,
    description: "When True, sentiment-ingestion sources write each ingested document to HistoricalStore's sentiment_ingestion_audit table (via HistoricalStore.save_sentiment_documents()) -- the per-document point-in-time archive underlying the credibility-weighted sentiment signal. Same on/off shape as NEWS_HISTORY_CAPTURE_ENABLED. Dead-lettered: any capture failure is logged and never crashes the pipeline. Has no effect while SENTIMENT_INGESTION_ENABLED is False (nothing is ever fetched to archive in the first place).",
  },
  {
    group: "ML, Data Capture & Audit", key: "SENTIMENT_DESENTENCIZE_ENABLED", type: "boolean",
    value: false, default: false,
    description: "When True, ingested document text has periods replaced with semicolons before FinBERT scoring (a real but marginal trick to discourage sentence-boundary truncation on run-on social posts). Off by default: it can corrupt numerics ($4.50), cashtags ($AAPL), and abbreviations (U.S.) -- see tests/test_sentiment_sources.py's desentencize-safety cases before enabling.",
  },
  {
    group: "ML, Data Capture & Audit", key: "EXCURSION_INTRADAY_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Opt-in (Phase-1 audit item B2): evaluation_engine.calculate_edge_ratio consumes hourly bars (MarketDataProvider.get_intraday_bars(..., interval='1h')) over the trade hold window instead of daily bars, for finer Maximum Favorable/Adverse Excursion (MFE/MAE) resolution on same-day or short holds. Daily bars are already genuine (not fabricated) and adequate for multi-day holds; this only adds intraday precision. Any hourly-fetch failure degrades to the existing daily-bar path rather than raising -- never blocks the excursion calculation. False (the default) reproduces pre-existing daily-only behavior exactly -- matches the FORECAST_USE_GARCH_SIGMA opt-in convention.",
  },
  // ---- Validation Gates ----
  {
    group: "Validation Gates", key: "VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Opt-in fix for validation/metrics.py::deflated_sharpe_ratio's n_trials<=1 shortcut, which unconditionally returns 1.0 (a perfect deflated Sharpe) for any single-trial strategy instead of actually computing the DSR test statistic -- so a strategy with only one configuration always passes the 'DSR > 0.95' deployability gate regardless of how weak its observed Sharpe, skew, or kurtosis actually are. This bug is directly relied on today by 5 STRATEGY_REGISTRY strategies that hit DSR=1.000 exactly via this shortcut (confirmed in docs/VALIDATION_STRATEGY_FIX_LOG.md) and are currently recorded deployable=True, so the corrected math ships opt-in rather than silently changing any currently-recorded verdict. False (the default) reproduces the pre-existing `return 1.0` shortcut byte-for-byte. True sets sr_0 = 0.0 and falls through to compute the REAL z_stat/norm.cdf from the actual sr_observed/skew/kurtosis/n_observations, instead of short-circuiting to a hardcoded perfect pass. Flipping this on requires a follow-up session with live-market data access to re-run scripts/refresh_validations.py against the 5 strategies named above and update docs/VALIDATION_STRATEGY_FIX_LOG.md before this can ever change what's actually live.",
  },
  {
    group: "Validation Gates", key: "VALIDATION_HARNESS_OOS_GATE_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Opt-in fix for StrategyValidationHarness's deployability gate: report.sharpe/max_dd/sortino/calmar/hit_rate/avg_trade_pct/turnover were computed from an in-sample 'test' set identical to the training set, while only PBO/DSR were genuinely out-of-sample (via CombinatorialPurgedCV); CPCV's own DSR/PBO Sharpes were also computed on gross (cost-free) returns, an inconsistent cost basis vs. the in-sample leg. When True, run_cpcv_evaluation applies the same turnover-scaled cost model to every CPCV path's train/test returns before any Sharpe/PBO/DSR/drawdown statistic is computed, and the harness's reported metrics become the MEAN of each metric computed independently on every CPCV path's own genuinely held-out (purged+embargoed) OOS returns, instead of the full-sample in-sample fit. equity_curve/benchmark_curve/macro_benchmark_curve are unchanged either way (still the full-sample series). False (the default) reproduces pre-existing behavior exactly: every currently-recorded docs/VALIDATION_STRATEGY_FIX_LOG.md PBO/DSR/Sharpe/MaxDD baseline for the registered STRATEGY_REGISTRY fleet was measured with this flag off, and this sandboxed dev/CI environment has no live-market network access to re-verify the fleet against the corrected numbers.",
  },
  // ---- RLHF Calibration ----
  {
    group: "RLHF Calibration", key: "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED", type: "boolean",
    value: false, default: false,
    description: "When True, a proposal whose confidence clears RLHF_CALIBRATION_CONFIDENCE_THRESHOLD is marked reviewed automatically (auto_approved=True, human_rating stays null -- never a fabricated rating) instead of waiting for a human. Default False: this changes what counts as 'reviewed' without a human in the loop, so it stays opt-in rather than defaulting on like RLHF_CALIBRATION_ENABLED.",
  },
  {
    group: "RLHF Calibration", key: "RLHF_CALIBRATION_CONFIDENCE_THRESHOLD", type: "number",
    value: 0.8, default: 0.8, min: 0.0, max: 1.0, step: 0.05,
    description: "Confidence [0,1] at or above which a new proposal is auto-approved (skips mandatory human review) when RLHF_CALIBRATION_AUTO_APPROVE_ENABLED is True.",
  },
  {
    group: "RLHF Calibration", key: "RLHF_CALIBRATION_AUTO_EXPORT_SFT_ENABLED", type: "boolean",
    value: false, default: false,
    description: "When True, a proposal that receives a 5-star human_rating is automatically appended to the SFT JSONL export the moment the review is submitted, instead of requiring a separate POST /rlhf/export-sft call. Default False (opt-in).",
  },
];

function readOverrides(
  storageKey: string,
): Record<string, number | boolean | string> {
  try {
    const raw = localStorage.getItem(storageKey);
    return raw
      ? (JSON.parse(raw) as Record<string, number | boolean | string>)
      : {};
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
  driftKey: string,
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
    field.liveness = mockLiveness(def.key);
    group.fields.push(field);
  }
  const driftKeys = readDrift(driftKey);
  // Roll the per-field states up exactly as `settings_meta.summarize_applies`
  // does: the shared state when they all agree, "mixed" when they don't.
  const counts: Record<AppliesState, number> = {
    immediately: 0,
    next_daemon_restart: 0,
    no_effect: 0,
    env_pinned: 0,
  };
  for (const g of groups) {
    for (const f of g.fields) {
      if (f.liveness) counts[f.liveness.applies] += 1;
    }
  }
  const present = (Object.keys(counts) as AppliesState[]).filter(
    (s) => counts[s] > 0,
  );
  const summary: AppliesSummary =
    present.length === 1
      ? present[0]
      : present.length === 0
        ? "next_daemon_restart"
        : "mixed";
  return {
    applies: summary,
    applies_counts: counts,
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
  driftKey: string,
  confirm: Record<string, string> = {},
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
        rejected[key] =
          `out_of_range: must be within [${def.min}, ${def.max}].`;
        continue;
      }
      written[key] = n;
    } else if (def.type === "boolean") {
      written[key] = Boolean(val);
    } else if (def.type === "enum") {
      if (def.options && !def.options.includes(String(val))) {
        rejected[key] =
          `invalid_option: must be one of ${def.options.join(", ")}.`;
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
  // ---- dangerous-key confirmation gate ----
  // Mirrors the real backend exactly, INCLUDING its ordering: this runs AFTER
  // type validation, so a malformed dangerous value reports its type problem
  // rather than having it masked by a confirmation complaint. It also runs
  // BEFORE anything is persisted below, so a refused key is never half-written.
  // Rejection is strictly per key -- an unconfirmed dangerous key must not stop
  // the ordinary keys in the same batch from being written.
  for (const key of Object.keys(written)) {
    if (!MOCK_DANGEROUS_KEYS.has(key)) continue;
    const echoed = confirm[key];
    if (echoed === undefined) {
      rejected[key] = "confirmation_required";
      delete written[key];
    } else if (echoed !== key) {
      rejected[key] = "confirmation_mismatch";
      delete written[key];
    }
  }

  // What actually happened to each written key. A field the classifier calls
  // live-safe is applied to the "running process"; everything else is a .env
  // write the running process won't see until it restarts.
  const perKeyApplies: Record<string, AppliesState> = {};
  for (const key of Object.keys(written)) {
    perKeyApplies[key] = mockLiveness(key).applies;
  }
  const appliedNow = Object.keys(perKeyApplies).filter(
    (k) => perKeyApplies[k] === "immediately",
  );
  const pending = Object.keys(perKeyApplies).filter(
    (k) => perKeyApplies[k] !== "immediately",
  );

  if (Object.keys(written).length > 0) {
    try {
      localStorage.setItem(
        overridesKey,
        JSON.stringify({ ...readOverrides(overridesKey), ...written }),
      );
      // Only a key that did NOT apply live is drifted: a live-applied key is
      // already in force in the running process, so reporting it as pending a
      // restart would be exactly the false claim this feature removes.
      const drift = new Set([...readDrift(driftKey), ...pending]);
      localStorage.setItem(driftKey, JSON.stringify([...drift]));
    } catch {
      /* ignore quota */
    }
  }

  const counts: Record<AppliesState, number> = {
    immediately: 0,
    next_daemon_restart: 0,
    no_effect: 0,
    env_pinned: 0,
  };
  for (const s of Object.values(perKeyApplies)) counts[s] += 1;
  const present = (Object.keys(counts) as AppliesState[]).filter(
    (s) => counts[s] > 0,
  );
  const summary: AppliesSummary =
    present.length === 1
      ? present[0]
      : present.length === 0
        ? "next_daemon_restart"
        : "mixed";

  let note: string;
  if (Object.keys(written).length === 0) {
    note = "Nothing was written.";
  } else if (appliedNow.length && !pending.length) {
    note =
      "Saved to .env and applied to the running process — no restart needed.";
  } else if (pending.length && !appliedNow.length) {
    note =
      "Saved to .env. The running process keeps the previous values until it restarts (POST /daemon/restart).";
  } else {
    note =
      `Saved to .env. ${appliedNow.length} applied to the running process immediately; ` +
      `${pending.length} take effect on the next restart (${pending.join(", ")}).`;
  }

  return {
    written,
    rejected,
    applies: summary,
    applies_counts: counts,
    per_key_applies: perKeyApplies,
    restart_required: pending.length > 0,
    restart_endpoint: "POST /daemon/restart",
    note,
  };
}

function mockTunables(): TunablesResponse {
  return buildTunablesResponse(TUNABLE_DEFS, TUNABLES_KEY, TUNABLES_DRIFT_KEY);
}

function applyTunables(
  values: Record<string, number | boolean | string>,
  confirm: Record<string, string> = {},
): TunablesUpdateResult {
  return applyTunablesGeneric(
    values,
    TUNABLE_DEFS,
    TUNABLES_KEY,
    TUNABLES_DRIFT_KEY,
    confirm,
  );
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
const SECTOR_SELECTION_TUNABLES_DRIFT_KEY =
  "stockpy.mock.sector_selection_tunables_drift";

const SENTIMENT_TUNABLE_DEFS: MockTunableDef[] = [
  // ---- Sentiment Ingestion Core ----
  {
    group: "Sentiment Ingestion Core",
    key: "SENTIMENT_INGESTION_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch for multi-source sentiment ingestion (Yahoo RSS/GDELT/EDGAR). False is a complete no-op.",
  },
  {
    group: "Sentiment Ingestion Core",
    key: "SENTIMENT_SOURCES",
    type: "string",
    value: "yahoo_rss,gdelt,edgar",
    default: "yahoo_rss,gdelt,edgar",
    description:
      "Comma-separated list of enabled sentiment-source provider names.",
  },
  {
    group: "Sentiment Ingestion Core",
    key: "SENTIMENT_COMMENT_SOURCES",
    type: "string",
    value: "reddit,stocktwits",
    default: "reddit,stocktwits",
    description:
      "Comma-separated subset of SENTIMENT_SOURCES classified as investor-forum comment sources.",
  },
  {
    group: "Sentiment Ingestion Core",
    key: "SENTIMENT_INGESTION_LOOKBACK_DAYS",
    type: "number",
    value: 1,
    default: 1,
    min: 1,
    max: 90,
    step: 1,
    description:
      "Calendar days of lookback each ingestion cycle requests from every enabled source.",
  },
  {
    group: "Sentiment Ingestion Core",
    key: "SENTIMENT_MAX_DOCUMENTS_PER_CYCLE",
    type: "number",
    value: 2000,
    default: 2000,
    min: 1,
    max: 20000,
    step: 1,
    description: "Per-cycle document budget shared across all symbols.",
  },
  {
    group: "Sentiment Ingestion Core",
    key: "SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE",
    type: "number",
    value: 60.0,
    default: 60.0,
    min: 1.0,
    max: 600.0,
    step: 1.0,
    description:
      "Hard wall-clock ceiling (seconds) for the entire per-cycle ingestion run.",
  },
  {
    group: "Sentiment Ingestion Core",
    key: "SENTIMENT_CIRCUIT_BREAKER_THRESHOLD",
    type: "number",
    value: 3,
    default: 3,
    min: 1,
    max: 20,
    step: 1,
    description:
      "Consecutive failures for a single source within one cycle before it's skipped for the rest of the cycle.",
  },
  // ---- Sources — StockTwits, EDGAR, GDELT, Google News ----
  {
    group: "Sources — StockTwits, EDGAR, GDELT, Google News",
    key: "STOCKTWITS_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch for the free, uncredentialed StockTwits source.",
  },
  {
    group: "Sources — StockTwits, EDGAR, GDELT, Google News",
    key: "GOOGLE_NEWS_LOOKBACK_WINDOW",
    type: "string",
    value: "7d",
    default: "7d",
    description:
      "Lookback window passed as Google News RSS's `when:` query parameter.",
  },
  {
    group: "Sources — StockTwits, EDGAR, GDELT, Google News",
    key: "EDGAR_FULLTEXT_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch for the SEC EDGAR full-text search (10-K/10-Q) additions to EdgarSource.",
  },
  {
    group: "Sources — StockTwits, EDGAR, GDELT, Google News",
    key: "EDGAR_FULLTEXT_FORMS",
    type: "string",
    value: "8-K,10-K,10-Q",
    default: "8-K,10-K,10-Q",
    description:
      "Comma-separated SEC form types requested from EDGAR full-text search.",
  },
  {
    group: "Sources — StockTwits, EDGAR, GDELT, Google News",
    key: "EDGAR_FULLTEXT_CHUNK_TOKENS",
    type: "number",
    value: 512,
    default: 512,
    min: 64,
    max: 4096,
    step: 64,
    description: "Maximum tokens per filing-text chunk for FinBERT scoring.",
  },
  {
    group: "Sources — StockTwits, EDGAR, GDELT, Google News",
    key: "GDELT_MIN_REQUEST_INTERVAL_SECONDS",
    type: "number",
    value: 5.0,
    default: 5.0,
    min: 0.0,
    max: 60.0,
    step: 0.5,
    description:
      "Minimum seconds between GDELT DOC API request issuance, shared process-wide.",
  },
  {
    group: "Sources — StockTwits, EDGAR, GDELT, Google News",
    key: "GDELT_MAX_RETRIES",
    type: "number",
    value: 2,
    default: 2,
    min: 0,
    max: 10,
    step: 1,
    description:
      "Retries after a GDELT HTTP 429/5xx before the request is given up on.",
  },
  {
    group: "Sources — StockTwits, EDGAR, GDELT, Google News",
    key: "GDELT_RETRY_BACKOFF_SECONDS",
    type: "number",
    value: 5.0,
    default: 5.0,
    min: 0.5,
    max: 60.0,
    step: 0.5,
    description: "Base seconds for the GDELT retry backoff.",
  },
  {
    group: "Sources — StockTwits, EDGAR, GDELT, Google News",
    key: "GDELT_COOLDOWN_THRESHOLD",
    type: "number",
    value: 3,
    default: 3,
    min: 1,
    max: 10,
    step: 1,
    description:
      "Consecutive failed GDELT requests after which calls are skipped outright for a cooldown period.",
  },
  {
    group: "Sources — StockTwits, EDGAR, GDELT, Google News",
    key: "GDELT_COOLDOWN_SECONDS",
    type: "number",
    value: 300.0,
    default: 300.0,
    min: 10.0,
    max: 3600.0,
    step: 10.0,
    description:
      "How long the GDELT cooldown stays open once the failure threshold is reached.",
  },
  // ---- FinBERT & Catalyst Scoring ----
  {
    group: "FinBERT & Catalyst Scoring",
    key: "FINBERT_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Use ProsusAI/FinBERT for headline sentiment when `transformers` is installed; falls back to a keyword lexicon otherwise.",
  },
  {
    group: "FinBERT & Catalyst Scoring",
    key: "FINBERT_BATCH_SIZE",
    type: "number",
    value: 16,
    default: 16,
    min: 1,
    max: 128,
    step: 1,
    description:
      "Headlines per forward pass when a real FinBERT pipeline is loaded.",
  },
  {
    group: "FinBERT & Catalyst Scoring",
    key: "FINBERT_SCORE_CACHE_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Cache FinBERT/lexicon headline scores by content hash so an unchanged headline is not re-scored.",
  },
  {
    group: "FinBERT & Catalyst Scoring",
    key: "NEWS_LOOKBACK_DAYS",
    type: "number",
    value: 7,
    default: 7,
    min: 1,
    max: 90,
    step: 1,
    description:
      "Calendar days of FMP company-news headlines scored per symbol per cycle.",
  },
  {
    group: "FinBERT & Catalyst Scoring",
    key: "SENTIMENT_SOCIAL_BLEND_WEIGHT",
    type: "number",
    value: 0.4,
    default: 0.4,
    min: 0.0,
    max: 1.0,
    step: 0.05,
    description:
      "Weight on the multi-source social sentiment component of the blended catalyst score.",
  },
  // ---- AI Credibility Verification ----
  {
    group: "AI Credibility Verification",
    key: "SENTIMENT_LLM_VERIFICATION_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "When True, borderline-credibility documents are verified via an LLM call instead of the heuristic placeholder.",
  },
  {
    group: "AI Credibility Verification",
    key: "SENTIMENT_LLM_VERIFICATION_PROVIDER",
    type: "enum",
    value: "none",
    default: "none",
    options: ["claude", "gemini", "openai", "none"],
    description: "Which LLM provider backs sentiment-document verification.",
  },
  {
    group: "AI Credibility Verification",
    key: "SENTIMENT_LLM_VERIFICATION_MAX_CALLS_PER_CYCLE",
    type: "number",
    value: 25,
    default: 25,
    min: 0,
    max: 500,
    step: 1,
    description:
      "Per-batch cap on real LLM calls made for credibility verification.",
  },
  // ---- Attention & Sector Heat ----
  {
    group: "Attention & Sector Heat",
    key: "SECTOR_HEAT_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch for the GDELT article-volume-based Sector Heat Factor attention feature.",
  },
  {
    group: "Attention & Sector Heat",
    key: "SECTOR_HEAT_SMOOTHING_SIGMA",
    type: "number",
    value: 1.0,
    default: 1.0,
    min: 0.1,
    max: 10.0,
    step: 0.1,
    description:
      "Gaussian smoothing sigma applied to the raw daily GDELT article-volume series.",
  },
  {
    group: "Attention & Sector Heat",
    key: "SECTOR_HEAT_LOOKBACK_DAYS",
    type: "number",
    value: 7,
    default: 7,
    min: 1,
    max: 90,
    step: 1,
    description:
      "Calendar days of GDELT article-volume history used to compute the Sector Heat Factor.",
  },
  {
    group: "Attention & Sector Heat",
    key: "WIKIPEDIA_ATTENTION_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch for the Wikipedia-pageviews-based retail-attention feature.",
  },
  {
    group: "Attention & Sector Heat",
    key: "WIKIPEDIA_ATTENTION_LOOKBACK_DAYS",
    type: "number",
    value: 30,
    default: 30,
    min: 1,
    max: 365,
    step: 1,
    description:
      "Calendar days of Wikipedia pageview history used to compute the attention baseline/z-score.",
  },
  {
    group: "Attention & Sector Heat",
    key: "PYTRENDS_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Best-effort optional Google Trends overlay on top of the Wikipedia-pageviews attention feature.",
  },
];

const SECTOR_SELECTION_TUNABLE_DEFS: MockTunableDef[] = [
  {
    group: "Related Sector Selection",
    key: "SECTOR_SELECTION_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch for the semantic Related Sector Selection feature's Gaussian-response Sector Heat term.",
  },
  {
    group: "Related Sector Selection",
    key: "SECTOR_SELECTION_TOP_N",
    type: "number",
    value: 3,
    default: 3,
    min: 1,
    max: 11,
    step: 1,
    description:
      "Default number of top-ranked related sectors selected per target symbol.",
  },
  {
    group: "Related Sector Selection",
    key: "SECTOR_SELECTION_W1",
    type: "number",
    value: 0.4,
    default: 0.4,
    min: 0.0,
    max: 1.0,
    step: 0.05,
    description:
      "Default news-volume weight, mirrored from the composite sentiment index.",
  },
  {
    group: "Related Sector Selection",
    key: "SECTOR_SELECTION_W2",
    type: "number",
    value: 0.1,
    default: 0.1,
    min: 0.0,
    max: 1.0,
    step: 0.05,
    description: "Default review-volume weight.",
  },
  {
    group: "Related Sector Selection",
    key: "SECTOR_SELECTION_HEAT_LOOKBACK_DAYS",
    type: "number",
    value: 22,
    default: 22,
    min: 1,
    max: 252,
    step: 1,
    description:
      "Trailing trading days of sentiment volume summed per candidate sector before min-max normalization.",
  },
  {
    group: "Related Sector Selection",
    key: "SECTOR_SELECTION_HEAT_A",
    type: "number",
    value: 0.8,
    default: 0.8,
    min: 0.0,
    max: 5.0,
    step: 0.05,
    description: "Gaussian amplitude 'a' in SHF = a * exp(-(x-b)^2 / (2c^2)).",
  },
  {
    group: "Related Sector Selection",
    key: "SECTOR_SELECTION_HEAT_B",
    type: "number",
    value: 1.0,
    default: 1.0,
    min: 0.0,
    max: 5.0,
    step: 0.05,
    description: "Gaussian center 'b' in SHF = a * exp(-(x-b)^2 / (2c^2)).",
  },
  {
    group: "Related Sector Selection",
    key: "SECTOR_SELECTION_HEAT_C",
    type: "number",
    value: 0.6,
    default: 0.6,
    min: 0.05,
    max: 5.0,
    step: 0.05,
    description: "Gaussian width 'c' in SHF = a * exp(-(x-b)^2 / (2c^2)).",
  },
  {
    group: "Related Sector Selection",
    key: "SECTOR_SIMILARITY_EMBEDDER",
    type: "enum",
    value: "sbert",
    default: "sbert",
    options: ["sbert", "openai", "none"],
    description: "Embedding backend for the semantic-similarity term.",
  },
  {
    group: "Related Sector Selection",
    key: "SECTOR_SIMILARITY_MODEL",
    type: "string",
    value: "sentence-transformers/all-MiniLM-L6-v2",
    default: "sentence-transformers/all-MiniLM-L6-v2",
    description:
      "Hugging Face model id loaded when SECTOR_SIMILARITY_EMBEDDER is 'sbert'.",
  },
  {
    group: "Related Sector Selection",
    key: "SECTOR_SIMILARITY_POOLING",
    type: "enum",
    value: "max",
    default: "max",
    options: ["max", "mean"],
    description: "Pooling strategy applied to SBERT token embeddings.",
  },
];

const FMP_TUNABLES_KEY = "stockpy.mock.fmp_tunables";
const FMP_TUNABLES_DRIFT_KEY = "stockpy.mock.fmp_tunables_drift";

const FMP_TUNABLE_DEFS: MockTunableDef[] = [
  {
    group: "Client & Resiliency",
    key: "FMP_BASE_URL",
    type: "string",
    value: "https://financialmodelingprep.com/stable",
    default: "https://financialmodelingprep.com/stable",
    description: "Financial Modeling Prep API base URL.",
  },
  {
    group: "Client & Resiliency",
    key: "FMP_TIMEOUT_SECONDS",
    type: "number",
    value: 10.0,
    default: 10.0,
    min: 1.0,
    max: 120.0,
    step: 1.0,
    description: "Per-request HTTP timeout in seconds.",
  },
  {
    group: "Client & Resiliency",
    key: "FMP_MIN_REQUEST_INTERVAL_SECONDS",
    type: "number",
    value: 0.25,
    default: 0.25,
    min: 0.0,
    max: 60.0,
    step: 0.05,
    description:
      "Minimum interval between requests in seconds (0.25 = 240 req/min).",
  },
  {
    group: "Client & Resiliency",
    key: "FMP_MAX_RETRIES",
    type: "number",
    value: 2,
    default: 2,
    min: 0,
    max: 10,
    step: 1,
    description: "Max retries on rate limit or server error.",
  },
  {
    group: "Client & Resiliency",
    key: "FMP_RETRY_BACKOFF_SECONDS",
    type: "number",
    value: 2.0,
    default: 2.0,
    min: 0.1,
    max: 60.0,
    step: 0.5,
    description: "Base backoff duration in seconds for retries.",
  },
  {
    group: "Client & Resiliency",
    key: "FMP_COOLDOWN_THRESHOLD",
    type: "number",
    value: 5,
    default: 5,
    min: 1,
    max: 20,
    step: 1,
    description: "Consecutive failures before opening the circuit breaker.",
  },
  {
    group: "Client & Resiliency",
    key: "FMP_COOLDOWN_SECONDS",
    type: "number",
    value: 300.0,
    default: 300.0,
    min: 1.0,
    max: 3600.0,
    step: 10.0,
    description: "Duration in seconds the circuit breaker remains open.",
  },
  {
    group: "Client & Resiliency",
    key: "FMP_FALLBACK_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Fall through to secondary providers (yfinance/Yahoo) on FMP failure.",
  },
  {
    group: "Client & Resiliency",
    key: "FMP_MAX_SECONDS_PER_CYCLE",
    type: "number",
    value: 120.0,
    default: 120.0,
    min: 1.0,
    max: 600.0,
    step: 1.0,
    description:
      "Maximum wall-clock seconds allowed for FMP calls in a single pipeline cycle.",
  },
  {
    group: "Primary Feeds",
    key: "FMP_QUOTES_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Use FMP as the quote provider (requires MARKET_DATA_PROVIDER=fmp).",
  },
  {
    group: "Primary Feeds",
    key: "FMP_QUOTES_REALTIME",
    type: "boolean",
    value: false,
    default: false,
    description: "Treat FMP quotes as real-time rather than delayed.",
  },
  {
    group: "Primary Feeds",
    key: "FMP_BARS_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Use FMP for historical OHLCV bars (requires MARKET_DATA_PROVIDER=fmp).",
  },
  {
    group: "Primary Feeds",
    key: "FMP_BARS_ADJUSTMENT",
    type: "enum",
    value: "dividend-adjusted",
    default: "dividend-adjusted",
    options: ["dividend-adjusted", "light", "full", "non-split-adjusted"],
    description: "Adjustment mode for historical EOD bars.",
  },
  {
    group: "Primary Feeds",
    key: "FMP_FUNDAMENTALS_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Use FMP for company fundamental data (requires FUNDAMENTALS_SOURCE=fmp).",
  },
  {
    group: "Diagnostic & Supplement Feeds",
    key: "FMP_ANALYST_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Fetch analyst consensus & price targets into diagnostic columns.",
  },
  {
    group: "Diagnostic & Supplement Feeds",
    key: "FMP_ANALYST_REFRESH_HOURS",
    type: "number",
    value: 24,
    default: 24,
    min: 1,
    max: 168,
    step: 1,
    description: "Refresh interval for analyst consensus data in hours.",
  },
  {
    group: "Diagnostic & Supplement Feeds",
    key: "FMP_EARNINGS_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description: "Fetch earnings calendar & surprises into diagnostic columns.",
  },
  {
    group: "Diagnostic & Supplement Feeds",
    key: "FMP_EARNINGS_REFRESH_HOURS",
    type: "number",
    value: 12,
    default: 12,
    min: 1,
    max: 168,
    step: 1,
    description: "Refresh interval for earnings data in hours.",
  },
  {
    group: "Diagnostic & Supplement Feeds",
    key: "FMP_MACRO_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Fetch treasury rates & economic indicators into macro_history.",
  },
  {
    group: "Diagnostic & Supplement Feeds",
    key: "FMP_ECON_INDICATORS",
    type: "string",
    value: "unemploymentRate",
    default: "unemploymentRate",
    description:
      "Comma-separated list of FMP economic indicator series to fetch.",
  },
  {
    group: "Diagnostic & Supplement Feeds",
    key: "FMP_INSIDER_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description: "Fetch insider trading statistics into diagnostic columns.",
  },
  {
    group: "Diagnostic & Supplement Feeds",
    key: "FMP_INSIDER_REFRESH_DAYS",
    type: "number",
    value: 7,
    default: 7,
    min: 1,
    max: 30,
    step: 1,
    description: "Refresh interval for insider trading data in days.",
  },
  {
    group: "Diagnostic & Supplement Feeds",
    key: "FMP_INSIDER_MIN_LAG_DAYS",
    type: "number",
    value: 45,
    default: 45,
    min: 0,
    max: 90,
    step: 1,
    description: "Minimum lag days required before analyzing insider trades.",
  },
  {
    group: "Diagnostic & Supplement Feeds",
    key: "FMP_SECTOR_SNAPSHOT_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description: "Fetch sector valuation & performance snapshots.",
  },
  {
    group: "Diagnostic & Supplement Feeds", key: "FMP_NEWS_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Master switch for the FMP company-news feed (data.fmp_client.stock_news, wrapping /news/stock). False (the default) is a complete no-op reproducing today's exact behavior — signals/news_catalyst.py's headline fetch returns no headlines, and data/sentiment_sources.py's 'fmp_news' SentimentSource returns [] without any network call. When True AND FMP_API_KEY is set, FMP becomes the PRIMARY provider for company headlines (fetch_company_headlines is FMP-only) and 'fmp_news' becomes eligible for SENTIMENT_SOURCES. Verified live 2026-08 against a real FMP key: /news/stock returns >=6 months of real history . Deliberately does NOT touch /news/press-releases — that endpoint returned a plan-entitlement rejection ('Restricted Endpoint') against the account this integration was verified with.",
  },
  {
    group: "Diagnostic & Supplement Feeds", key: "FMP_NEWS_PAGE_LIMIT", type: "number",
    value: 100, default: 100, min: 1, max: 1000, step: 1,
    description: "Articles requested per /news/stock page (the 'limit' query param). 100 matches the page size verified live 2026-08 against a real FMP key over a multi-day window. Only consulted when FMP_NEWS_ENABLED is True.",
  },
  {
    group: "Diagnostic & Supplement Feeds", key: "FMP_NEWS_MAX_PAGES", type: "number",
    value: 10, default: 10, min: 1, max: 1000, step: 1,
    description: "Hard ceiling on pages fetched per symbol per call into data.fmp_client.stock_news, bounding a wide backfill window (e.g. scripts/backfill_news_history.py --months 6) so a dense news day/symbol cannot loop indefinitely. Once the ceiling is reached the remaining (older) articles in the window are simply not fetched -- callers that need full coverage should narrow --months or accept the honest gap (CONSTRAINT #4: never a fabricated substitute for the missing pages, just fewer real rows). Only consulted when FMP_NEWS_ENABLED is True.",
  },
  {
    group: "Diagnostic & Supplement Feeds", key: "FMP_PEERS_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Master switch for the on-demand GET /data/peers/{symbol} endpoint (api/data_api.py) — a single, per-click, operator-triggered FMP peer-group lookup (/peers) for the webapp's 'Suggest peers for this ticker' affordance on SymbolComparison. False (the default) is a complete no-op: the endpoint returns an empty peer list + an honest reason, with ZERO network calls.",
  },
  {
    group: "Diagnostic & Supplement Feeds", key: "FMP_UNIVERSE_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Use FMP's historical S&P 500 constituent-changes feed as the primary source for survivorship-bias reconstruction (Wikipedia demoted to fallback).",
  },
];

// Mirrors api/pilots_api.py's _FEATURE_FLAGS_GROUPS exactly: the
// settings_keysets.DANGEROUS_KEYS + the 6 pilots/feature_flags.py
// WRITE_GATE_REASONS keys in one group, the 7 DIAGNOSTIC_FLAG_REASONS keys
// in the other. Values/defaults mirror the real settings.py defaults after
// the 2026-08-07 admin-gate default flip.
const FEATURE_FLAGS_TUNABLE_DEFS: MockTunableDef[] = [
  // -- Write & Execution Gates (settings_keysets.DANGEROUS_KEYS -- typed
  // confirmation required on write) --
  {
    group: "Write & Execution Gates",
    key: "ADVISORY_ONLY",
    type: "boolean",
    value: true,
    default: true,
    description:
      "The execution quarantine -- when True, ALL broker order submission is suppressed.",
  },
  {
    group: "Write & Execution Gates",
    key: "DRY_RUN",
    type: "boolean",
    value: false,
    default: false,
    description:
      "The second execution quarantine -- turning it off is what makes logged orders become submitted orders.",
  },
  {
    group: "Write & Execution Gates",
    key: "ROBINHOOD_EXECUTION_MODE",
    type: "enum",
    value: "off",
    default: "off",
    options: ["off", "review", "live"],
    description:
      "Moving this to 'live' is what lets the Robinhood execution bridge place real orders.",
  },
  {
    group: "Write & Execution Gates",
    key: "DAEMON_AGENTIC_QUEUE_MODE",
    type: "enum",
    value: "off",
    default: "off",
    options: ["off", "shadow", "primary"],
    description:
      "Which process writes the Robinhood execution queue. 'shadow' writes a comparison copy under OUTPUT_DIR/shadow/ only; 'primary' (step 5.3) hands the real queue to the daemon.",
  },
  {
    group: "Write & Execution Gates",
    key: "MACRO_REGIME_GATE_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "The recession/credit-event BUY veto (Sahm Rule, VIX, HY OAS). Setting it False bypasses that veto entirely.",
  },
  {
    group: "Write & Execution Gates",
    key: "FMP_BARS_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Read FMP_BARS_ADJUSTMENT before enabling -- an adjustment-convention mismatch corrupts every return series, indicator, and backtest.",
  },
  {
    group: "Write & Execution Gates",
    key: "FMP_BARS_ADJUSTMENT",
    type: "enum",
    value: "dividend-adjusted",
    default: "dividend-adjusted",
    options: ["dividend-adjusted", "light", "full", "non-split-adjusted"],
    description:
      "The single highest-risk value in the FMP integration -- 'full' looks like the obvious pick and is wrong (split-only, not dividend-adjusted).",
  },
  {
    group: "Write & Execution Gates",
    key: "CORS_ALLOWED_ORIGINS",
    type: "string",
    value:
      '["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5173"]',
    default:
      '["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5173"]',
    description:
      "Which browser origins the State API and Pilots API accept requests from.",
  },
  {
    group: "Write & Execution Gates",
    key: "AI_GENERATION_API_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Master gate for the three paid Claude/Gemini/Opal generation endpoints on the Data API.",
  },
  {
    group: "Write & Execution Gates",
    key: "AUTOMATION_WRITES_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates POST /automation/resume, which re-enables live order submission after ADVISORY_ONLY was previously engaged.",
  },
  {
    group: "Write & Execution Gates",
    key: "BROKERAGE_REFRESH_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates POST /brokerage/refresh -- a real live login against the operator's actual brokerage account, bypassing the daily cache.",
  },
  {
    group: "Write & Execution Gates",
    key: "ROBINHOOD_SCHEDULED_LOGIN_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Makes the orchestrator daemon start a real Robinhood device-approval login every weekday at ROBINHOOD_SCHEDULED_LOGIN_TIME_ET, pushing an approval prompt to the operator's phone.",
  },
  {
    group: "Write & Execution Gates",
    key: "COMMAND_EXECUTION_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "The highest-risk flag in this group -- enables the 'command' job type, which can execute the kill switch or arbitrary orchestrator flags.",
  },
  {
    group: "Write & Execution Gates",
    key: "DEAD_LETTER_RETRY_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates POST /dead-letter/retry, which spawns a real main.py subprocess for one symbol.",
  },
  {
    group: "Write & Execution Gates",
    key: "GENERAL_SETTINGS_WRITES_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates PUT /settings/tunables -- Kelly sizing, risk-gate, and forecasting knobs.",
  },
  {
    group: "Write & Execution Gates",
    key: "LLM_WRITES_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates PUT /llm/setting -- which LLM provider narrates a rationale, and whether Gravity AI / Opal research can fire.",
  },
  {
    group: "Write & Execution Gates",
    key: "MACRO_GATE_WRITES_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates PUT /observability/macro-gate, the write path for MACRO_REGIME_GATE_ENABLED itself.",
  },
  {
    group: "Write & Execution Gates",
    key: "MCP_OAUTH_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Whether investyo_mcp_server.py's OAuth authorization-server endpoints are live.",
  },
  {
    group: "Write & Execution Gates",
    key: "PROMPT_REGISTRY_WRITES_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates PUT /prompts/pin -- changes which prompt text the platform actually runs.",
  },
  {
    group: "Write & Execution Gates",
    key: "RAG_QUERY_API_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description: "Gates POST /rag/query, a paid external LLM call.",
  },
  {
    group: "Write & Execution Gates",
    key: "STRATEGY_WRITES_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates PUT /strategy/modules -- signal weights and the disabled-module set, which changes what the platform recommends.",
  },
  {
    group: "Write & Execution Gates",
    key: "BROKER_BACKEND",
    type: "enum",
    value: "fmp_paper",
    default: "fmp_paper",
    options: ["fmp_paper"],
    description:
      "The automated pipeline's broker is the local FMP paper ledger ('fmp_paper', SQLite-backed). Alpaca was removed; real money moves only through the Robinhood execution queue.",
  },
  {
    group: "Write & Execution Gates",
    key: "LIVE_TRADE_EXECUTION_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch for broker_live_execution_mcp.py's execute_live_trade/confirm_live_trade tool pair -- turning it on permits live order routing to an external broker.",
  },
  {
    group: "Write & Execution Gates",
    key: "LIVE_TRADE_APPROVAL_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Gates the only endpoints that can move a live-trade proposal's status to 'approved' (POST /pilots/execution/proposals/{id}/approve).",
  },
  {
    group: "Write & Execution Gates",
    key: "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Bridges simulated PaperAccountStore closed trades into the real transactions_store 'trades' ledger.",
  },
  {
    group: "Write & Execution Gates",
    key: "PAPER_PIPELINE_PROBE_WEIGHT",
    type: "number",
    value: 0.0,
    default: 0.0,
    min: 0,
    max: 0.05,
    step: 0.005,
    description:
      "Cold-start paper probe: fraction of paper equity per zero-Kelly BUY while the pipeline has fewer than 30 closed trades. 0 = off.",
  },
  {
    group: "Write & Execution Gates",
    key: "META_LABELING_BACKFILL_BRIDGE_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch for the Forecast Backfill screen's live meta-labeler bridge -- when True, screen-trained models can gate real position sizing if they clear the PBO/DSR deployability check.",
  },
  {
    group: "Write & Execution Gates",
    key: "MCP_OAUTH_MULTI_USER_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Switches the OAuth /login form from the single-passphrase check (MCP_OAUTH_PASSWORD) to per-user credentials.",
  },
  {
    group: "Write & Execution Gates",
    key: "FORECAST_BACKFILL_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Gates POST /pilots/forecast_backfill/run and POST /pilots/forecast_backfill/cancel/{job_id}, which run a full multi-day forecast-history backfill.",
  },
  {
    group: "Write & Execution Gates",
    key: "JULES_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Enables the Jules third-party autonomous coding-agent integration (data/jules_client.py) -- creates PRs against the connected repo.",
  },
  // -- Write gates NOT in DANGEROUS_KEYS (pilots/feature_flags.py's
  // WRITE_GATE_REASONS -- visible, no typed confirmation required) --
  {
    group: "Write & Execution Gates",
    key: "BROKERAGE_CONNECT_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates POST /brokerage/connect and /disconnect -- real brokerage-credential intake.",
  },
  {
    group: "Write & Execution Gates",
    key: "UNIVERSE_SYNC_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates POST /data/sync -- refreshes the tracked ticker universe from the configured sources.",
  },
  {
    group: "Write & Execution Gates",
    key: "AGENTIC_DISCOVERY_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates PUT /agentic/scan-config -- the Robinhood broker-scan configuration for the agentic-discovery skill.",
  },
  {
    group: "Write & Execution Gates",
    key: "JOBS_API_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates background job execution and SSE log-streaming endpoints on the orchestrator Control API.",
  },
  {
    group: "Write & Execution Gates",
    key: "PILOTS_API_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Whether the Pilots API is hosted inside the persistent orchestrator daemon process at all -- a process-startup switch, not a per-request guard.",
  },
  {
    group: "Write & Execution Gates",
    key: "RLHF_CALIBRATION_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates the RLHF Calibration Review Queue's write endpoints -- defaults on since every proposal is hypothetical/paper-only.",
  },
  {
    group: "Write & Execution Gates",
    key: "PAPER_BROKER_WRITES_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates POST /pilots/paper-broker/reset on the Pilots API -- wipes the local FMP paper account's positions/orders and reseeds cash.",
  },
  // -- Diagnostic & Data Features (read-only measurement/data-source
  // master switches, feed no scoring or sizing decision) --
  // NOTE: all 7 of these default to False in settings.py (each is a data
  // source / diagnostic feature, not an admin/write/execution gate, so
  // none qualify for the 2026-08-03 default-on convention) -- mirror that
  // here, not the mass-flip regression a prior commit briefly introduced.
  {
    group: "Diagnostic & Data Features",
    key: "SECTOR_HEAT_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Enables Sector Heat Factor computation from GDELT article volume.",
  },
  {
    group: "Diagnostic & Data Features",
    key: "WIKIPEDIA_ATTENTION_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Enables Attention Score computation from Wikipedia pageviews.",
  },
  {
    group: "Diagnostic & Data Features",
    key: "MARKET_DATA_LATENCY_TRACKING_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description: "Tracks and surfaces real-time market data feed latency.",
  },
  {
    group: "Diagnostic & Data Features",
    key: "SENTIMENT_INDEX_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description: "Computes composite sentiment index from news and reviews.",
  },
  {
    group: "Diagnostic & Data Features",
    key: "EDGAR_FULLTEXT_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description: "Enables full-text ingestion of 10-K/10-Q SEC filings.",
  },
  {
    group: "Diagnostic & Data Features",
    key: "PIPELINE_STALL_ALERT_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Fires a WARNING alert if a pipeline cycle stops updating progress.json while state='running' -- read-only, never cancels a run or restarts the daemon.",
  },
];

// Representative multi-domain sample for Settings Reference offline mock.
// Covers all 13 domains with diverse types, secret masking, and liveness states.
// Overrides key for `PUT /settings/reference` boolean toggles — a dedicated
// storage bucket, distinct from the per-editor override keys above, since
// this screen can write a field regardless of which (if any) dedicated
// editor also covers it.
const SETTINGS_REFERENCE_OVERRIDES_KEY = "stockpy_settings_reference_overrides";

function mockSettingsReference(): SettingsReferenceResponse {
  const overrides = readOverrides(SETTINGS_REFERENCE_OVERRIDES_KEY);
  const domains = [
    "Financial/Risk/Sizing",
    "Execution/Brokers",
    "Options Desk",
    "Market Data/DB",
    "Universe/Watchlist",
    "Forecasting/ML",
    "Sentiment/News/Attention",
    "AI/LLM/RAG",
    "Orchestrator/Daemon/Jobs",
    "Alerting/Observability",
    "Strategy Overlays",
    "Filesystem/Bootstrap",
    "RLHF",
  ];
  // `writable` is a computed pass below, not per-literal here, so it can
  // never drift from `type`/`category` the way a hand-typed boolean would —
  // exactly the class of bug the real backend's own `writable = key in
  // _REFERENCE_WRITE_INDEX` derivation exists to prevent (see api/pilots_api.py).
  const baseFields: Omit<SettingsReferenceField, "writable">[] = [
    {
      key: "ADVISORY_ONLY",
      category: "allowed",
      value: true,
      default: true,
      type: "boolean",
      description: "When True, ALL broker order submission is suppressed. The pipeline still runs end-to-end but order execution returns immediately.",
      domain: "Financial/Risk/Sizing",
      dangerous: true,
      liveness: mockLiveness("ADVISORY_ONLY"),
      // ADVISORY_ONLY is listed in BOTH _TUNABLE_GROUPS and (via
      // settings_keysets.DANGEROUS_KEYS) _FEATURE_FLAGS_GROUPS. The real
      // backend's _build_editable_at_index() checks /settings/feature-flags
      // before /settings/tunables, first-match-wins -- this must match.
      editable_at: "/settings/feature-flags",
    },
    {
      key: "KELLY_FRACTION",
      category: "allowed",
      value: 0.5,
      default: 0.5,
      type: "number",
      description: "Fractional Kelly sizing multiplier (e.g. 0.5 for half-Kelly) applied to reduce volatility and avoid overbetting.",
      domain: "Financial/Risk/Sizing",
      dangerous: false,
      liveness: mockLiveness("KELLY_FRACTION"),
      editable_at: "/settings/tunables",
    },
    {
      key: "BROKER_BACKEND",
      category: "allowed",
      value: "fmp_paper",
      default: "fmp_paper",
      type: "string",
      description: "The automated pipeline's broker is the local FMP paper ledger ('fmp_paper', SQLite-backed). Alpaca was removed; real money moves only through the Robinhood execution queue.",
      domain: "Execution/Brokers",
      dangerous: true,
      liveness: mockLiveness("BROKER_BACKEND"),
      // BROKER_BACKEND is also DANGEROUS_KEYS (so it's in
      // _FEATURE_FLAGS_GROUPS too), but it's literally defined in
      // _PAPER_BROKER_GROUPS, whose editor route is checked BEFORE
      // /settings/feature-flags in _build_editable_at_index() -- paper-broker
      // wins in the real backend, so it must win here too.
      editable_at: "/settings/paper-broker",
    },
    {
      key: "OPTIONS_RISK_FREE_RATE",
      category: "allowed",
      value: 0.045,
      default: 0.045,
      type: "number",
      description: "Annualized risk-free interest rate for options pricing and Greeks calculation.",
      domain: "Options Desk",
      dangerous: false,
      liveness: mockLiveness("OPTIONS_RISK_FREE_RATE"),
      editable_at: null,
    },
    {
      key: "PROMPT_MAX_CHARS",
      category: "allowed",
      value: 50000,
      default: 50000,
      type: "number",
      description: "Hard upper bound on prompt body size enforced by guardrails.validate_prompt(). Bodies exceeding this are rejected as a denial-of-service mitigation.",
      domain: "AI/LLM/RAG",
      dangerous: false,
      liveness: mockLiveness("PROMPT_MAX_CHARS"),
      editable_at: null,
    },
    {
      key: "FRED_API_KEY",
      category: "secret",
      value: "•••• (set)",
      default: "",
      type: "string",
      description: "FRED API key. Required for live macroeconomic data.",
      domain: "Market Data/DB",
      dangerous: false,
      liveness: mockLiveness("FRED_API_KEY"),
      editable_at: null,
    },
    {
      key: "WATCHLIST",
      category: "allowed",
      value: "SPY,QQQ,AAPL,NVDA,MSFT",
      default: "",
      type: "string",
      description: "Comma-separated list of symbols to include in the active tracking universe.",
      domain: "Universe/Watchlist",
      dangerous: false,
      liveness: mockLiveness("WATCHLIST"),
      editable_at: null,
    },
    {
      key: "FORECAST_USE_GARCH_SIGMA",
      category: "allowed",
      value: true,
      default: true,
      type: "boolean",
      description: "Use GARCH(1,1) conditional volatility for forecast confidence intervals.",
      domain: "Forecasting/ML",
      dangerous: false,
      liveness: mockLiveness("FORECAST_USE_GARCH_SIGMA"),
      editable_at: "/settings/tunables",
    },
    {
      key: "SENTIMENT_INGESTION_ENABLED",
      category: "allowed",
      value: false,
      default: false,
      type: "boolean",
      description: "Master switch for multi-source sentiment ingestion (Yahoo RSS/GDELT/EDGAR).",
      domain: "Sentiment/News/Attention",
      dangerous: false,
      liveness: mockLiveness("SENTIMENT_INGESTION_ENABLED"),
      editable_at: "/settings/sentiment",
    },
    {
      key: "LLM_COMMENTARY_ENABLED",
      category: "allowed",
      value: true,
      default: true,
      type: "boolean",
      description: "Enable automated LLM narration for daily advisory reports.",
      domain: "AI/LLM/RAG",
      dangerous: false,
      liveness: mockLiveness("LLM_COMMENTARY_ENABLED"),
      editable_at: null,
    },
    {
      key: "ORCHESTRATOR_DAEMON_ENABLED",
      category: "allowed",
      value: true,
      default: true,
      type: "boolean",
      description: "Run the continuous background pipeline timer daemon.",
      domain: "Orchestrator/Daemon/Jobs",
      dangerous: false,
      liveness: mockLiveness("ORCHESTRATOR_DAEMON_ENABLED"),
      editable_at: "/settings/tunables",
    },
    {
      key: "ALERT_EMAIL_ENABLED",
      category: "allowed",
      value: false,
      default: false,
      type: "boolean",
      description: "Send operational and trading alerts via SMTP email.",
      domain: "Alerting/Observability",
      dangerous: false,
      liveness: mockLiveness("ALERT_EMAIL_ENABLED"),
      editable_at: null,
    },
    {
      key: "HMM_N_STATES",
      category: "allowed",
      value: 3,
      default: 3,
      type: "number",
      description: "Number of hidden states for the Gaussian HMM regime detector (bull/sideways/bear).",
      domain: "Strategy Overlays",
      dangerous: false,
      liveness: mockLiveness("HMM_N_STATES"),
      editable_at: "/settings/tunables",
    },
    {
      key: "LOCAL_DATA_ROOT",
      category: "excluded",
      value: "~/.stockpy_local",
      default: "~/.stockpy_local",
      type: "string",
      description: "Root directory path for local SQLite databases, logs, and artifacts.",
      domain: "Filesystem/Bootstrap",
      dangerous: false,
      liveness: mockLiveness("LOCAL_DATA_ROOT"),
      editable_at: null,
    },
    {
      key: "RLHF_CALIBRATION_ENABLED",
      category: "allowed",
      value: true,
      default: true,
      type: "boolean",
      description: "Gates the RLHF Calibration Review Queue's write endpoints.",
      domain: "RLHF",
      dangerous: false,
      liveness: mockLiveness("RLHF_CALIBRATION_ENABLED"),
      editable_at: "/settings/feature-flags",
    },
  ];
  // `writable` mirrors the real backend's `key in _REFERENCE_WRITE_INDEX`
  // exactly (non-secret boolean only) -- computed, never hand-typed per
  // field, so it cannot drift the way the two editable_at literals above did.
  // Applies any override an earlier `updateSettingsReference` call persisted,
  // matching `buildTunablesResponse`'s read-overrides convention.
  const fields: SettingsReferenceField[] = baseFields.map((f) => ({
    ...f,
    value: f.key in overrides ? overrides[f.key] : f.value,
    // Mirrors the real backend's exclusion exactly: a no_op field (e.g.
    // PROMPT_MAX_CHARS, read nowhere in production) never gets
    // a live-looking Toggle -- that would imply the control does something
    // when it provably doesn't. `mockLiveness(key).applies === "no_effect"`
    // is this mock's equivalent of the real backend's `no_op` bucket check.
    writable: f.type === "boolean" && f.category === "allowed" && f.liveness.applies !== "no_effect",
  }));
  return {
    fields,
    total: fields.length,
    domains,
  };
}

function applySettingsReference(
  values: Record<string, boolean>,
  confirm: Record<string, string> = {},
): TunablesUpdateResult {
  // The write scope is exactly the writable rows `mockSettingsReference()`
  // itself would report -- reusing that function (rather than re-deriving a
  // second boolean/allowed check here) means this can never drift from what
  // the screen actually shows as toggleable.
  const writableKeys = new Set(
    mockSettingsReference().fields.filter((f) => f.writable).map((f) => f.key),
  );
  const written: Record<string, boolean> = {};
  const rejected: Record<string, string> = {};
  for (const [key, val] of Object.entries(values)) {
    if (!writableKeys.has(key)) {
      rejected[key] = "unknown_key";
      continue;
    }
    if (typeof val !== "boolean") {
      rejected[key] = "expected_boolean";
      continue;
    }
    written[key] = val;
  }
  // Dangerous-key confirmation gate -- identical ordering/semantics to
  // applyTunablesGeneric's (runs after type validation, per-key, never
  // whole-batch).
  for (const key of Object.keys(written)) {
    if (!MOCK_DANGEROUS_KEYS.has(key)) continue;
    const echoed = confirm[key];
    if (echoed === undefined) {
      rejected[key] = "confirmation_required";
      delete written[key];
    } else if (echoed !== key) {
      rejected[key] = "confirmation_mismatch";
      delete written[key];
    }
  }

  const perKeyApplies: Record<string, AppliesState> = {};
  for (const key of Object.keys(written)) {
    perKeyApplies[key] = mockLiveness(key).applies;
  }
  const appliedNow = Object.keys(perKeyApplies).filter((k) => perKeyApplies[k] === "immediately");
  const pending = Object.keys(perKeyApplies).filter((k) => perKeyApplies[k] !== "immediately");

  if (Object.keys(written).length > 0) {
    try {
      localStorage.setItem(
        SETTINGS_REFERENCE_OVERRIDES_KEY,
        JSON.stringify({ ...readOverrides(SETTINGS_REFERENCE_OVERRIDES_KEY), ...written }),
      );
    } catch {
      /* ignore quota */
    }
  }

  const counts: Record<AppliesState, number> = {
    immediately: 0,
    next_daemon_restart: 0,
    no_effect: 0,
    env_pinned: 0,
  };
  for (const s of Object.values(perKeyApplies)) counts[s] += 1;
  const present = (Object.keys(counts) as AppliesState[]).filter((s) => counts[s] > 0);
  const summary: AppliesSummary =
    present.length === 1 ? present[0] : present.length === 0 ? "next_daemon_restart" : "mixed";

  let note: string;
  if (Object.keys(written).length === 0) {
    note = "Nothing was written.";
  } else if (appliedNow.length && !pending.length) {
    note = "Saved to .env and applied to the running process — no restart needed.";
  } else if (pending.length && !appliedNow.length) {
    note =
      "Saved to .env. The running process keeps the previous values until it restarts (POST /daemon/restart).";
  } else {
    note =
      `Saved to .env. ${appliedNow.length} applied to the running process immediately; ` +
      `${pending.length} take effect on the next restart (${pending.join(", ")}).`;
  }

  return {
    written,
    rejected,
    applies: summary,
    applies_counts: counts,
    per_key_applies: perKeyApplies,
    restart_required: pending.length > 0,
    restart_endpoint: "POST /daemon/restart",
    note,
  };
}

function mockSentimentTunables(): TunablesResponse {
  return buildTunablesResponse(
    SENTIMENT_TUNABLE_DEFS,
    SENTIMENT_TUNABLES_KEY,
    SENTIMENT_TUNABLES_DRIFT_KEY,
  );
}

function applySentimentTunables(
  values: Record<string, number | boolean | string>,
  confirm: Record<string, string> = {},
): TunablesUpdateResult {
  return applyTunablesGeneric(
    values,
    SENTIMENT_TUNABLE_DEFS,
    SENTIMENT_TUNABLES_KEY,
    SENTIMENT_TUNABLES_DRIFT_KEY,
    confirm,
  );
}

function mockSectorSelectionTunables(): TunablesResponse {
  return buildTunablesResponse(
    SECTOR_SELECTION_TUNABLE_DEFS,
    SECTOR_SELECTION_TUNABLES_KEY,
    SECTOR_SELECTION_TUNABLES_DRIFT_KEY,
  );
}

function applySectorSelectionTunables(
  values: Record<string, number | boolean | string>,
  confirm: Record<string, string> = {},
): TunablesUpdateResult {
  return applyTunablesGeneric(
    values,
    SECTOR_SELECTION_TUNABLE_DEFS,
    SECTOR_SELECTION_TUNABLES_KEY,
    SECTOR_SELECTION_TUNABLES_DRIFT_KEY,
    confirm,
  );
}

function mockFmpTunables(): TunablesResponse {
  return buildTunablesResponse(
    FMP_TUNABLE_DEFS,
    FMP_TUNABLES_KEY,
    FMP_TUNABLES_DRIFT_KEY,
  );
}

function applyFmpTunables(
  values: Record<string, number | boolean | string>,
  confirm: Record<string, string> = {},
): TunablesUpdateResult {
  return applyTunablesGeneric(
    values,
    FMP_TUNABLE_DEFS,
    FMP_TUNABLES_KEY,
    FMP_TUNABLES_DRIFT_KEY,
    confirm,
  );
}

const FEATURE_FLAGS_TUNABLES_KEY = "stockpy.mock.feature_flags_tunables";
const FEATURE_FLAGS_TUNABLES_DRIFT_KEY =
  "stockpy.mock.feature_flags_tunables_drift";

function mockFeatureFlagsTunables(): TunablesResponse {
  return buildTunablesResponse(
    FEATURE_FLAGS_TUNABLE_DEFS,
    FEATURE_FLAGS_TUNABLES_KEY,
    FEATURE_FLAGS_TUNABLES_DRIFT_KEY,
  );
}

function applyFeatureFlagsTunables(
  values: Record<string, number | boolean | string>,
  confirm: Record<string, string> = {},
): TunablesUpdateResult {
  return applyTunablesGeneric(
    values,
    FEATURE_FLAGS_TUNABLE_DEFS,
    FEATURE_FLAGS_TUNABLES_KEY,
    FEATURE_FLAGS_TUNABLES_DRIFT_KEY,
    confirm,
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
  holdDays: number,
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
      trades.reduce((a, t) => a + (t.holding_days ?? 0), 0) /
      (trades.length || 1)
    ).toFixed(1),
    best_trade_pnl: pnls.length ? Math.max(...pnls) : null,
    worst_trade_pnl: pnls.length ? Math.min(...pnls) : null,
    gross_profit: gp,
    gross_loss: gl,
  };
}

// ---- Trade History fixture (durable-store, full paginated ledger) ----
// A larger, longer-history set than REALIZED_TRADES (which caps at 6 for the
// Portfolio screen's summary panel), plus one row with unresolvable
// return_pct/holding_days -- an honesty branch proving the UI renders "—"
// rather than "0"/"NaN" for a genuinely null field.
const TRADE_HISTORY_TRADES: RealizedTrade[] = [
  ...REALIZED_TRADES,
  rt("TSLA", 5, 210.0, 195.0, 30),
  rt("GOOGL", 12, 138.0, 151.2, 55),
  rt("PBF", 45, 19.08, 38.0, 27),
  rt("ARCC", 10, 18.65, 18.405, 8),
  rt("IVR", 220, 8.375, 8.625, 200),
  rt("CMCL", 17, 10.19, 25.0, 4),
  {
    symbol: "XYZ",
    quantity: null,
    entry_ts: new Date(Date.now() - 400 * 86400000).toISOString(),
    exit_ts: new Date(Date.now() - 370 * 86400000).toISOString(),
    entry_price: null,
    exit_price: null,
    realized_pnl: 0,
    return_pct: null,
    holding_days: null,
  },
];

// ---- Alerts feed fixture ----
function mockAlerts(): AlertsFeed {
  const now = Date.now();
  return {
    reason: null,
    entries: [
      {
        timestamp: new Date(now - 8 * 60000).toISOString(),
        level: "INFO",
        message:
          "Refresh complete — 6 symbols evaluated, 2 BUY / 3 HOLD / 1 SELL.",
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
  const rng = seeded(
    [...sym].reduce((a, c) => a + c.charCodeAt(0), 0) + horizon,
  );
  // BERT-LLA's three ablations only show up for AAPL in this fixture --
  // BERT_LLA_ENABLED defaults False in production (matching the attention
  // overlay fixture's own symbol choice above), so every OTHER symbol
  // honestly shows just the four models that are always potentially active.
  const models =
    sym === "AAPL"
      ? [
          "arima",
          "monte_carlo",
          "holt_winters",
          "cnn_lstm",
          "lstm_baseline",
          "lstm_attention",
          "bert_lla",
        ]
      : ["arima", "monte_carlo", "holt_winters", "cnn_lstm"];
  const curve = models.flatMap((m) =>
    [-0.3, -0.1, 0.1, 0.3].map((center) => ({
      model_name: m,
      horizon_days: horizon,
      bin_center: center,
      // some bins honestly null (too few samples)
      mean_pct_error: rng() < 0.2 ? null : +((rng() - 0.5) * 0.12).toFixed(4),
      count: Math.floor(rng() * 12) + 1,
    })),
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
      return {
        model_name: m,
        n: Math.floor(rng() * completed * 0.6) + 5,
        rmse,
        mae,
      };
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
        pe: +(15 + rng() * 20).toFixed(1),
        change_pct: +((rng() - 0.5) * 0.04).toFixed(4),
      };
    }
    if (i === 4) {
      // Honesty branch: this candidate sector has no FMP valuation snapshot
      // at all (feed disabled, or this sector name isn't covered) -- pe/
      // change_pct must be null, never a fabricated/neighboring value
      // (CONSTRAINT #4), independent of the similarity fields above (which
      // are still fully populated for this row).
      const cos = +(0.2 + rng() * 0.6).toFixed(3);
      const shf = +(0.3 + rng() * 0.5).toFixed(3);
      return {
        sector,
        cosine_similarity: cos,
        ingestion_volume: +(rng() * 60).toFixed(1),
        sector_heat_factor: shf,
        correlation_coefficient: +(cos * shf).toFixed(4),
        degraded_reason: "review_unavailable",
        pe: null,
        change_pct: null,
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
        pe: +(15 + rng() * 20).toFixed(1),
        change_pct: +((rng() - 0.5) * 0.04).toFixed(4),
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
      pe: +(15 + rng() * 20).toFixed(1),
      change_pct: +((rng() - 0.5) * 0.04).toFixed(4),
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
    date: new Date(now - (windowSize - 1 - i) * 86_400_000)
      .toISOString()
      .slice(0, 10),
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

// Rider 13b (Needs Retrain age flag): mirrors shared.help_content.
// MODEL_RETRAIN_WINDOW_DAYS (30) -- the mock has no live Python process to
// import from, so this is the fixture layer's honest snapshot of that
// constant, not an invented number. daysSinceTrained/age_days/needs_retrain
// below mirror pilots/models.py's own server-side computation exactly.
const MODEL_RETRAIN_WINDOW_DAYS = 30;

function daysSinceTrained(trainedDate: string): number {
  const then = new Date(`${trainedDate}T00:00:00Z`).getTime();
  return Math.floor((Date.now() - then) / 86_400_000);
}

// Fixture dates below are relative to "now" (not hard-coded literals) so the
// fresh-vs-stale badge states this file's tests exercise stay correct
// indefinitely, regardless of what day the suite actually runs on.
function daysAgoString(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}

// ---- ML registry fixture (honest: two un-validated / not-deployable; one
// stale -- exercises BOTH the fresh and "Needs Retrain" badge states) ----
const MODEL_FRESH_TRAINED_DATE = daysAgoString(15); // well inside the 30-day window
const MODEL_STALE_TRAINED_DATE = daysAgoString(45); // well outside the 30-day window

const MODELS: ModelRow[] = [
  {
    name: "lgbm_ranker",
    role: "cross_sectional_ranker",
    trained_date: MODEL_FRESH_TRAINED_DATE,
    cpcv_dsr: 0.0019,
    pbo: 0.267,
    n_train: 260,
    deployable: false,
    notes:
      "LightGBM LambdaRank — modest weight until validated at >200 OOS dates.",
    age_days: daysSinceTrained(MODEL_FRESH_TRAINED_DATE),
    needs_retrain:
      daysSinceTrained(MODEL_FRESH_TRAINED_DATE) >= MODEL_RETRAIN_WINDOW_DAYS,
    // Real cpcv_dsr/pbo above -> a real (if unimpressive) CPCV OOS Sharpe/
    // MaxDD too. max_dd is a POSITIVE magnitude fraction (0.28 = 28%),
    // matching compute_max_drawdown's convention -- see ModelRow's doc.
    cpcv_mean_oos_sharpe: 0.31,
    cpcv_mean_oos_max_dd: 0.28,
  },
  {
    name: "meta_labeler_timeseries_momentum",
    role: "meta_labeler",
    trained_date: MODEL_FRESH_TRAINED_DATE,
    cpcv_dsr: null,
    pbo: null,
    n_train: 3499,
    deployable: false,
    notes: "Binary classifier predicting P(timeseries_momentum correct).",
    age_days: daysSinceTrained(MODEL_FRESH_TRAINED_DATE),
    needs_retrain:
      daysSinceTrained(MODEL_FRESH_TRAINED_DATE) >= MODEL_RETRAIN_WINDOW_DAYS,
    // Un-validated (cpcv_dsr/pbo null above) -> both new fields stay null
    // too, matching this fixture's existing honesty pattern.
    cpcv_mean_oos_sharpe: null,
    cpcv_mean_oos_max_dd: null,
  },
  {
    // Deliberately trained well outside the 30-day window (unlike its two
    // siblings above) so the fixture exercises the "Needs Retrain" badge's
    // TRUE branch, not just the fresh/false one.
    name: "meta_labeler_cross_sectional_momentum",
    role: "meta_labeler",
    trained_date: MODEL_STALE_TRAINED_DATE,
    cpcv_dsr: null,
    pbo: null,
    n_train: 3460,
    deployable: false,
    notes: "Binary classifier predicting P(cross_sectional_momentum correct).",
    age_days: daysSinceTrained(MODEL_STALE_TRAINED_DATE),
    needs_retrain:
      daysSinceTrained(MODEL_STALE_TRAINED_DATE) >= MODEL_RETRAIN_WINDOW_DAYS,
    cpcv_mean_oos_sharpe: null,
    cpcv_mean_oos_max_dd: null,
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
    notes:
      "Registered but not yet trained -- no dated run to compute an age from.",
    age_days: null,
    needs_retrain: null,
    cpcv_mean_oos_sharpe: null,
    cpcv_mean_oos_max_dd: null,
  },
  {
    // ml/registry.yaml's meta_labeler_backfill_<signal_id> stubs: trained
    // (if at all) only by the Forecast Backfill screen's own job, never by
    // this screen's "Retrain Now" -> POST /jobs {job_type:"train_meta"}
    // path (that dispatches scripts/train_meta_labelers.py, which has never
    // heard of a "backfill_<signal_id>" identifier and rejects it). This
    // fixture exercises the honest "run a backfill" link Models.tsx renders
    // for a `meta_labeler_backfill_*` row instead of a Retrain Now button
    // that would 400.
    name: "meta_labeler_backfill_cross_sectional_momentum",
    role: "meta_labeler",
    trained_date: null,
    cpcv_dsr: null,
    pbo: null,
    n_train: 0,
    deployable: false,
    notes:
      "Forecast Backfill meta-labeler stub. Distinct from meta_labeler_cross_sectional_momentum. " +
      "AFML takes priority in meta_bootstrap.py if both exist and are deployable.",
    age_days: null,
    needs_retrain: null,
    cpcv_mean_oos_sharpe: null,
    cpcv_mean_oos_max_dd: null,
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

const HEALTH_GATE_DIRECTIONS: Record<
  StrategyHealthGate["key"],
  "above" | "below"
> = {
  pbo: "below",
  dsr: "above",
  sharpe: "above",
  max_drawdown: "below",
};

function healthGate(
  key: StrategyHealthGate["key"],
  value: number | null,
): StrategyHealthGate {
  const threshold = HEALTH_THRESHOLDS[key];
  const direction = HEALTH_GATE_DIRECTIONS[key];
  const passed =
    value == null || Number.isNaN(value)
      ? null
      : direction === "below"
        ? value < threshold
        : value > threshold;
  return {
    key,
    label: HEALTH_GATE_LABELS[key],
    value,
    threshold,
    direction,
    passed,
  };
}

/** Order matches the real backend's PBO/DSR/Sharpe/MaxDD gate ordering. */
function healthGates(
  sharpe: number | null,
  dsr: number | null,
  pbo: number | null,
  maxDrawdown: number | null,
): StrategyHealthGate[] {
  return [
    healthGate("pbo", pbo),
    healthGate("dsr", dsr),
    healthGate("sharpe", sharpe),
    healthGate("max_drawdown", maxDrawdown),
  ];
}

function healthTrend(
  points: [string, number, number, number, number, boolean][],
): StrategyHealthTrendPoint[] {
  return points.map(
    ([report_date, pbo, dsr, sharpe, max_drawdown, deployable]) => ({
      report_date,
      pbo,
      dsr,
      sharpe,
      max_drawdown,
      deployable,
    }),
  );
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
      {
        report_date: "2026-05-04",
        pbo: 0.34,
        dsr: 0.951,
        sharpe: 0.94,
        max_drawdown: 0.21,
        deployable: true,
      },
      {
        report_date: "2026-06-01",
        pbo: 0.24,
        dsr: 0.964,
        sharpe: 1.03,
        max_drawdown: 0.2,
        deployable: true,
      },
      {
        report_date: "2026-07-06",
        pbo: 0.31,
        dsr: 0.972,
        sharpe: 1.12,
        max_drawdown: 0.19,
        deployable: true,
      },
    ],
    multifactor_lowvol_size: [
      {
        report_date: "2026-06-10",
        pbo: 0.41,
        dsr: 0.89,
        sharpe: 0.42,
        max_drawdown: 0.27,
        deployable: false,
      },
      {
        report_date: "2026-06-28",
        pbo: 0.33,
        dsr: 0.91,
        sharpe: 0.52,
        max_drawdown: 0.24,
        deployable: false,
      },
      {
        report_date: "2026-07-14",
        pbo: 0.28,
        dsr: 0.93,
        sharpe: 0.61,
        max_drawdown: 0.22,
        deployable: false,
      },
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
    health_caption:
      "⚠ 1 model disagreement(s); Claude skipped=0 / Gemini skipped=0.",
    total_steps: 8,
    claude_passed: 8,
    claude_failed: 0,
    claude_skipped: 0,
    gemini_passed: 7,
    gemini_failed: 1,
    gemini_skipped: 0,
    disagreements: 1,
    steps: [
      {
        step_number: 1,
        step_title: "Data & Schema Integrity",
        claude: "✅ PASSED",
        gemini: "✅ PASSED",
        disagreement: false,
        score_claude: 92,
        score_gemini: 90,
        notes: "",
      },
      {
        step_number: 2,
        step_title: "Strategy & Signal Logic",
        claude: "✅ PASSED",
        gemini: "✅ PASSED",
        disagreement: false,
        score_claude: 88,
        score_gemini: 86,
        notes: "",
      },
      {
        step_number: 3,
        step_title: "Options Pricing Engine",
        claude: "✅ PASSED",
        gemini: "❌ FAILED",
        disagreement: true,
        score_claude: 85,
        score_gemini: 61,
        notes: "gemini flagged a delta-tolerance edge case Claude did not",
      },
      {
        step_number: 4,
        step_title: "Forecasting Engine",
        claude: "✅ PASSED",
        gemini: "✅ PASSED",
        disagreement: false,
        score_claude: 90,
        score_gemini: 91,
        notes: "",
      },
      {
        step_number: 5,
        step_title: "Macro Regime Engine",
        claude: "✅ PASSED",
        gemini: "✅ PASSED",
        disagreement: false,
        score_claude: 87,
        score_gemini: 89,
        notes: "",
      },
      {
        step_number: 6,
        step_title: "Sizing & Risk",
        claude: "✅ PASSED",
        gemini: "✅ PASSED",
        disagreement: false,
        score_claude: 93,
        score_gemini: 92,
        notes: "",
      },
      {
        step_number: 7,
        step_title: "Execution & Kill-Switch",
        claude: "✅ PASSED",
        gemini: "✅ PASSED",
        disagreement: false,
        score_claude: 95,
        score_gemini: 94,
        notes: "",
      },
      {
        step_number: 8,
        step_title: "LLM & Advisory Layer",
        claude: "✅ PASSED",
        gemini: "✅ PASSED",
        disagreement: false,
        score_claude: 84,
        score_gemini: 88,
        notes: "",
      },
    ],
  },
  legacy_audit: {
    available: true,
    all_passed: false,
    steps: [
      { step: "step_1_pandera_schema", passed: true, status: "PASSED" },
      { step: "step_2_lookahead_perturbation", passed: true, status: "PASSED" },
      {
        step: "step_3_5_discrepancy_analysis",
        passed: true,
        status: "Perfect Alignment",
      },
      {
        step: "step_4_signal_registry_health",
        passed: false,
        status: "FAILED",
      },
      { step: "step_7_simulation_impact", passed: true, status: "OK / OK" },
    ],
    reason: null,
  },
};




// Factor z-scores for a subset of PORTFOLIO's holdings, deliberately NOT
// covering every symbol -- DUK (held) has no entry, exercising the "held
// symbol never scored by the pipeline" honesty branch (unmatched_symbols).
// Plain numbers (not `FactorExposure`'s nullable fields) -- this fixture
// never has a missing factor for a matched symbol.
const ATTRIBUTION_FACTORS: Record<
  string,
  Record<keyof FactorExposure, number>
> = {
  AAPL: {
    value_z: -0.3,
    quality_z: 1.1,
    lowvol_z: 0.2,
    size_z: -1.8,
    multifactor_composite: 0.25,
  },
  MSFT: {
    value_z: -0.5,
    quality_z: 1.3,
    lowvol_z: 0.3,
    size_z: -1.9,
    multifactor_composite: 0.3,
  },
  NVDA: {
    value_z: -0.9,
    quality_z: 0.8,
    lowvol_z: -1.1,
    size_z: -1.6,
    multifactor_composite: 0.15,
  },
  V: {
    value_z: 0.4,
    quality_z: 1.6,
    lowvol_z: 0.6,
    size_z: -1.2,
    multifactor_composite: 0.55,
  },
  COST: {
    value_z: -0.2,
    quality_z: 1.2,
    lowvol_z: 0.9,
    size_z: -0.3,
    multifactor_composite: 0.5,
  },
};

const ATTRIBUTION_FACTOR_KEYS: (keyof FactorExposure)[] = [
  "value_z",
  "quality_z",
  "lowvol_z",
  "size_z",
  "multifactor_composite",
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
    PORTFOLIO.positions.map((p) => [p.symbol, p.market_value ?? 0]),
  );
  const heldSymbols = Object.keys(heldValues);
  const totalValue = Object.values(heldValues).reduce((a, b) => a + b, 0);

  const matched = heldSymbols.filter((s) => s in ATTRIBUTION_FACTORS).sort();
  const unmatched = heldSymbols
    .filter((s) => !(s in ATTRIBUTION_FACTORS))
    .sort();
  const matchedValue = matched.reduce((a, s) => a + heldValues[s], 0);

  const exposures = Object.fromEntries(
    ATTRIBUTION_FACTOR_KEYS.map((k) => {
      if (matchedValue <= 0) return [k, null];
      const sum = matched.reduce(
        (a, s) => a + ATTRIBUTION_FACTORS[s][k] * heldValues[s],
        0,
      );
      return [k, sum / matchedValue];
    }),
  ) as unknown as FactorExposure;

  const asOf = new Date(Date.now() - 5_400_000).toISOString();

  const clusters: CorrelationCluster[] = ATTRIBUTION_CLUSTER_GROUPS.map((g) => {
    const symbolsHeld = g.symbols.filter((s) => heldSymbols.includes(s));
    const clusterValue = symbolsHeld.reduce(
      (a, s) => a + (heldValues[s] ?? 0),
      0,
    );
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

// Sector names (post-trim, non-blank) that appear more than once. Mirrors
// pilots/brinson.py::_find_duplicate_sectors -- see that function's docstring
// for why a duplicate sector name must be rejected rather than silently
// computed (it produces a dict-key collision in Sector Details downstream).
function mockDuplicateSectors(rows: BrinsonFachlerRow[]): string[] {
  const counts = new Map<string, number>();
  for (const r of rows) {
    const name = r.sector.trim();
    if (!name) continue;
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  return [...counts.entries()]
    .filter(([, n]) => n > 1)
    .map(([name]) => name)
    .sort();
}

function mockValidateBrinsonFachlerRows(rows: BrinsonFachlerRow[]): string[] {
  const warnings: string[] = [];
  const validRows = rows.filter((r) => r.sector.trim() !== "");
  if (validRows.length === 0) return ["No rows with a non-blank sector name."];

  const dupes = mockDuplicateSectors(rows);
  if (dupes.length > 0) {
    warnings.push(
      `Duplicate sector name(s) found: ${dupes.join(", ")} — each sector must appear in exactly one row.`,
    );
  }

  const pSum = validRows.reduce((a, r) => a + (r.portfolio_weight_pct || 0), 0);
  const bSum = validRows.reduce((a, r) => a + (r.benchmark_weight_pct || 0), 0);

  if (Math.abs(pSum - 100) > 1) {
    warnings.push(
      `Portfolio weights sum to ${pSum.toFixed(2)}% (expected ~100%).`,
    );
  }
  if (Math.abs(bSum - 100) > 1) {
    warnings.push(
      `Benchmark weights sum to ${bSum.toFixed(2)}% (expected ~100%).`,
    );
  }
  if (validRows.some((r) => (r.portfolio_weight_pct || 0) < 0)) {
    warnings.push(
      "Negative values found in Portfolio Weight — long-only attribution typically requires non-negative weights.",
    );
  }
  if (validRows.some((r) => (r.benchmark_weight_pct || 0) < 0)) {
    warnings.push(
      "Negative values found in Benchmark Weight — long-only attribution typically requires non-negative weights.",
    );
  }
  if (pSum === 0 && bSum === 0) {
    warnings.push("All weights are zero — nothing to attribute.");
  }
  return warnings;
}

function mockComputeBrinsonFachler(
  rows: BrinsonFachlerRow[],
): BrinsonFachlerResult {
  const validRows = rows.filter((r) => r.sector.trim() !== "");
  if (validRows.length === 0) {
    throw new ApiError("No rows with a non-blank sector name.", 422);
  }

  // Hard reject (matches pilots/brinson.py::compute_brinson_fachler): a
  // duplicate sector name would silently collide in sectorDetails[s.sector]
  // below, exactly like the Python dict-comprehension bug this mirrors.
  const dupes = mockDuplicateSectors(rows);
  if (dupes.length > 0) {
    throw new ApiError(
      `Duplicate sector name(s) found: ${dupes.join(", ")}. Each sector must appear in exactly one row -- merge or rename the duplicates before submitting.`,
      422,
    );
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
      total_attribution: round6(
        allocationEffect + selectionEffect + interactionEffect,
      ),
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
    // Quiet by default (no live macro kill event in the demo) -- the
    // Models screen's macro-gate banner only lights up when this AND
    // macro_regime_gate_enabled above are both true, exactly like the real
    // gate/kill-switch combination check.
    macro_kill_switch: false,
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
    })),
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

// Same symbol universe mockRiskGateBlocks/mockCircuitBreakers/
// mockSizingCapEvents already use, so mock mode's cross-section stories stay
// consistent (a cap event on NVDA, a risk-gate block on AMD -- and now a
// forecast-skill row for each of them too).
const FORECAST_SKILL_SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMD"];

function mockForecastSkillBySymbol(horizon: number): ForecastSkillBySymbol {
  const models = ["arima", "monte_carlo", "holt_winters", "cnn_lstm"];
  const MC_NOMINAL_COVERAGE_PCT = 90.0;
  const rows: ForecastSkillSymbolRow[] = FORECAST_SKILL_SYMBOLS.map(
    (symbol, i) => {
      const rng = seeded(horizon * 7919 + symbol.charCodeAt(0) * 31 + i);
      // One symbol (the last) is deliberately cold-start -- zero history yet,
      // even though it's part of the requested universe -- to exercise the
      // "never silently omit a requested symbol" rendering path in mock mode
      // too, not just in the backend's own unit tests. Every *_pct/*_score
      // field this cold-start symbol carries stays honestly null, mirroring
      // pilots/observability.py's own "insufficient history" gate.
      if (i === FORECAST_SKILL_SYMBOLS.length - 1) {
        return {
          symbol,
          pending: 0,
          completed: 0,
          skill_weights: {},
          n_by_model: {},
          decay_pct: null,
          decay_reason: "No forecast history yet — run the pipeline to accumulate it.",
          mc_coverage_n: 0,
          mc_coverage_pct: null,
          mc_nominal_coverage_pct: MC_NOMINAL_COVERAGE_PCT,
          mc_interval_score: null,
          mc_coverage_reason: "No forecast history yet — run the pipeline to accumulate it.",
        };
      }
      const raw = models.map(() => 0.1 + rng());
      const tot = raw.reduce((a, b) => a + b, 0);
      const skill_weights: Record<string, number> = {};
      const n_by_model: Record<string, number> = {};
      models.forEach((m, j) => {
        skill_weights[m] = +(raw[j] / tot).toFixed(3);
        n_by_model[m] = Math.floor(rng() * 80) + 30;
      });
      const completed = Math.floor(rng() * 60) + 20;
      // decay_pct: positive = degrading, negative = improving (see the
      // helper's own docstring in pilots/observability.py). Symmetric
      // around 0 so both directions show up across the mocked universe.
      const decayPct = +((rng() - 0.5) * 20).toFixed(1);
      const mcCoverageN = Math.floor(rng() * 30) + 5;
      const mcCoveragePct = +(80 + rng() * 15).toFixed(1);
      const mcIntervalScore = +(5 + rng() * 25).toFixed(2);
      return {
        symbol,
        pending: Math.floor(rng() * 4) + 1,
        completed,
        skill_weights,
        n_by_model,
        decay_pct: decayPct,
        decay_reason: null,
        mc_coverage_n: mcCoverageN,
        mc_coverage_pct: mcCoveragePct,
        mc_nominal_coverage_pct: MC_NOMINAL_COVERAGE_PCT,
        mc_interval_score: mcIntervalScore,
        mc_coverage_reason: null,
      };
    },
  );
  return {
    horizon_days: horizon,
    window_days: 180,
    min_obs: 30,
    rows,
    reason: null,
  };
}

// The honest "no forecast history yet" degrade -- exported for the same
// reason as mockSizingCapAuditDisabled above:
// Observability.test.tsx's COLD_START fixture pins to this canonical shape
// rather than hand-rolling its own copy.
export function mockForecastSkillBySymbolEmpty(): ForecastSkillBySymbol {
  return {
    horizon_days: 30,
    window_days: 180,
    min_obs: 30,
    rows: [],
    reason: "No forecast history yet — run the pipeline to accumulate it.",
  };
}

// Mission Control's data-latency heatmap (market_data_latency.py's
// in-process ring buffer). Tracking is OFF by default in real deployments
// (MARKET_DATA_LATENCY_TRACKING_ENABLED=false) -- mockLatencyHeatmapDisabled
// is the honest cold-start shape most operators will actually see; the
// "on" fixture below is for exercising the populated-table rendering path
// in mock mode.
function mockLatencyHeatmap(): LatencyHeatmap {
  const now = Date.now();
  const rng = seeded(4242);
  const rows: LatencySample[] = FORECAST_SKILL_SYMBOLS.map((symbol, i) => {
    const latency = +(0.3 + rng() * (i === 1 ? 6 : 2)).toFixed(3); // MSFT deliberately slow
    const quoteTs = new Date(now - (i + 1) * 90_000 - latency * 1000);
    return {
      symbol,
      source: i % 2 === 0 ? "fmp" : "yfinance",
      quote_timestamp: quoteTs.toISOString(),
      ingested_at: new Date(quoteTs.getTime() + latency * 1000).toISOString(),
      latency_seconds: latency,
      is_stale: latency > 3,
    };
  }).sort(
    (a, b) =>
      new Date(b.ingested_at).getTime() - new Date(a.ingested_at).getTime(),
  );
  const latencies = rows.map((r) => r.latency_seconds).sort((a, b) => a - b);
  const mid = latencies[Math.floor(latencies.length / 2)];
  const worst = rows.reduce((w, r) =>
    r.latency_seconds > w.latency_seconds ? r : w,
  );
  return {
    tracking_enabled: true,
    count: rows.length,
    p50: mid,
    p95: latencies[latencies.length - 1],
    worst_symbol: worst.symbol,
    worst_p95: worst.latency_seconds,
    rows,
    reason: null,
  };
}

// The honest "tracking disabled" degrade -- exported for the same reason as
// the other mock*Disabled/*Empty helpers: Observability.test.tsx's
// COLD_START fixture pins to this canonical shape.
export function mockLatencyHeatmapDisabled(): LatencyHeatmap {
  return {
    tracking_enabled: false,
    count: 0,
    p50: null,
    p95: null,
    worst_symbol: null,
    worst_p95: null,
    rows: [],
    reason:
      "MARKET_DATA_LATENCY_TRACKING_ENABLED is False — latency samples are not recorded this process.",
  };
}

function mockRiskGateBlocks(): RiskGateBlockLog {
  const now = Date.now();
  const entries: RiskGateBlockEntry[] = [
    {
      ts: new Date(now - 40 * 60_000).toISOString(),
      check: "max_correlation",
      reason:
        "Correlation with the existing NVDA position (0.86) exceeds the 0.80 threshold.",
      symbol: "AMD",
      side: "buy",
      qty: 12,
      strategy_id: "cross-sectional-momentum",
    },
    {
      ts: new Date(now - 6 * 3600_000).toISOString(),
      check: "portfolio_heat",
      reason:
        "Adding this position would raise portfolio heat to 6.4%, above the 5% cap.",
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
  reason = "psutil is not available in this environment.",
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
      id: 3,
      timestamp: new Date(now - 30 * 60_000).toISOString(),
      cycle_id: "cycle-118",
      symbol: "NVDA",
      strategy_id: "timeseries_momentum",
      raw_weight: 0.32,
      final_weight: 0.2,
      binding_constraint: "kelly_cap",
      was_capped: true,
    },
    {
      id: 2,
      timestamp: new Date(now - 90 * 60_000).toISOString(),
      cycle_id: "cycle-117",
      symbol: "TSLA",
      strategy_id: null,
      raw_weight: 0.28,
      final_weight: 0.28,
      binding_constraint: null,
      was_capped: false,
    },
    {
      id: 1,
      timestamp: new Date(now - 150 * 60_000).toISOString(),
      cycle_id: "cycle-116",
      symbol: "SPY",
      strategy_id: "multifactor_lowvol_size",
      raw_weight: 4.1,
      final_weight: 3.0,
      binding_constraint: "portfolio_gross",
      was_capped: true,
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
    reason:
      "SIZING_CAP_AUDIT_ENABLED is False -- the durable cap-event log is not being written this run.",
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
      'The legacy Streamlit "Heartbeat Age Trend" sparkline is a 60-sample ring buffer held only in st.session_state -- never persisted to disk -- so there is no durable history for this endpoint to serve honestly. Only the current sample is real.',
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
      'The legacy Streamlit "Heartbeat Age Trend" sparkline is a 60-sample ring buffer held only in st.session_state -- never persisted to disk -- so there is no durable history for this endpoint to serve honestly. Only the current sample is real.',
    reason:
      "No heartbeat file yet -- output/heartbeat.txt is written only by main_orchestrator.py's async heartbeat task.",
  };
}

// ---- Strategy P&L (G7) ----
// One tagged-strategy row plus one strategy_id:null row (untagged trades) --
// exercises the "real money grouped under a null bucket" honesty path, not
// just an all-tagged happy path.
function mockStrategyPnlSummary(): StrategyPnlSummary {
  return {
    rows: [
      {
        strategy_id: "timeseries_momentum",
        realized_pnl: 842.15,
        trade_count: 11,
      },
      {
        strategy_id: "cross_sectional_momentum",
        realized_pnl: 213.4,
        trade_count: 4,
      },
      { strategy_id: null, realized_pnl: -58.2, trade_count: 2 },
    ],
    total_realized_pnl: 997.35,
    reason: null,
  };
}

// The honest "no closed trades yet" degrade -- exported for the same reason
// as mockSizingCapAuditDisabled above.
export function mockStrategyPnlEmpty(): StrategyPnlSummary {
  return {
    rows: [],
    total_realized_pnl: null,
    reason: "No closed trades in the transactions store yet.",
  };
}

function mockObservabilitySummary(
  range: PerfRange,
  horizon: number,
): ObservabilitySummary {
  return {
    portfolio_risk: mockPortfolioRisk(),
    portfolio_heat: mockPortfolioHeat(),
    equity_curve: mockEquityDrawdownCurve(range),
    regime: mockRegimeOverlay(),
    forecast_skill: mockPortfolioForecastSkill(horizon),
    forecast_skill_by_symbol: readObservabilityColdStart()
      ? mockForecastSkillBySymbolEmpty()
      : mockForecastSkillBySymbol(horizon),
    risk_gate_blocks: mockRiskGateBlocks(),
    circuit_breakers: mockCircuitBreakers(),
    system_telemetry: readObservabilityColdStart()
      ? mockSystemTelemetryUnavailable()
      : mockSystemTelemetry(),
    // Tracking defaults OFF in real deployments -- mock mode's cold-start
    // toggle mirrors that as the "clean" state, matching every other
    // opt-in-flag section here (sizing_cap_audit).
    latency_heatmap: readObservabilityColdStart()
      ? mockLatencyHeatmapDisabled()
      : mockLatencyHeatmap(),
    sizing_cap_audit: readObservabilityColdStart()
      ? mockSizingCapAuditDisabled()
      : mockSizingCapAuditTrail(),
    heartbeat: readObservabilityColdStart()
      ? mockHeartbeatNoData()
      : mockHeartbeatSummary(),
    strategy_pnl: readObservabilityColdStart()
      ? mockStrategyPnlEmpty()
      : mockStrategyPnlSummary(),
  };
}

// GET /observability/logs fixture -- deliberately mixes levels (INFO through
// CRITICAL) plus one unparseable traceback-continuation line, so mock mode
// exercises the tally KPI strip, the systemic/symbol-specific counts, AND
// the "kept but unparsed" rendering path, not just an all-INFO happy path.
function mockObservabilityLogs(limit: number): LogAggregation {
  const now = Date.now();
  const iso = (minsAgo: number) =>
    new Date(now - minsAgo * 60_000).toISOString();
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
    tally: {
      CRITICAL: 1,
      ERROR: 1,
      WARNING: 1,
      INFO: 2,
      DEBUG: 0,
      UNPARSED: 1,
    },
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
  logPath: string | null = "logs/investyo.log",
): LogAggregation {
  return {
    log_path: logPath,
    total_lines: 0,
    tally: {
      CRITICAL: 0,
      ERROR: 0,
      WARNING: 0,
      INFO: 0,
      DEBUG: 0,
      UNPARSED: 0,
    },
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
  error: string | null,
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
    "ForecastingEngine: insufficient bars for NVDA (need >=22, got 9)",
  ),
  // An interval-triggered run with no `mode` recorded -> honest "—" in the UI.
  controlRun(
    "orch-mock-5c88",
    "succeeded",
    undefined,
    305,
    44.2,
    "interval",
    null,
  ),
];

// GET /runs/history's durable fixture -- deliberately LONGER than
// CONTROL_RUN_HISTORY (the in-memory 10-run ring GET /status returns) to
// demonstrate the whole point of the durable table: history that outlives a
// daemon restart, not just "the same 4 runs again." Only terminal runs ever
// land here (see RunHistoryEntry's doc comment in types.ts) -- no "running"
// entries, unlike CONTROL_RUN_HISTORY which a test injects one into directly.
const RUN_HISTORY_DURABLE: RunRecord[] = [
  ...CONTROL_RUN_HISTORY,
  controlRun(
    "orch-mock-5b41",
    "succeeded",
    "full",
    365,
    39.7,
    "interval",
    null,
  ),
  controlRun(
    "orch-mock-5a02",
    "succeeded",
    "data",
    425,
    11.9,
    "interval",
    null,
  ),
  controlRun(
    "orch-mock-4f93",
    "failed",
    "full",
    488,
    22.3,
    "manual",
    "DataEngine: Robinhood login failed after 3 retries (session expired)",
  ),
  controlRun(
    "orch-mock-4e6c",
    "succeeded",
    "metrics",
    550,
    9.4,
    "interval",
    null,
  ),
  controlRun(
    "orch-mock-4d21",
    "succeeded",
    "full",
    612,
    43.1,
    "interval",
    null,
  ),
  controlRun(
    "orch-mock-4c05",
    "succeeded",
    "data",
    675,
    13.2,
    "interval",
    null,
  ),
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
 * In-memory RLHF proposal store backing the Agentic Trading screen's "RLHF
 * Review Queue" section (RlhfReviewQueue.tsx) -- NOT the unrelated
 * `/calibration` statistical-reliability screen. Proposals originate only
 * from an AI agent via an MCP tool + the API; submitRlhfReview/exportRlhfSft
 * mutate this array in place (mirrors MOCK_DECISION_LOG's logDecision
 * pattern above) so a review genuinely disappears from the pending queue on
 * refetch within the mock session, not persisted across a page reload.
 *
 * Deliberately exercises every honesty branch: id 1 is pending with a null
 * `price`/`extra_context`/`rsi`/`sentiment_score` (agent couldn't resolve a
 * live quote, attach context, or source either technical input); id 2 is
 * `auto_approved: true` / already `status: "reviewed"` /
 * `human_rating: null` (never appears in the pending list, never shows a
 * rating control -- see getRlhfSummary's pending-only filter below); id 3 is
 * a normal human-reviewed row with both a rating and a corrective comment,
 * already exported to the SFT dataset; id 4 is a second pending row so the
 * KPI counts aren't trivially 0/1; id 5 is reviewed, 5-starred, with no
 * correction needed, and NOT yet exported -- the row exportRlhfSft acts on.
 */
const MOCK_RLHF_PROPOSALS: RlhfProposal[] = [
  {
    id: 1,
    created_at: new Date(Date.now() - 45 * 60_000).toISOString(),
    symbol: "NVDA",
    action: "BUY",
    quantity: 10,
    price: null,
    rationale:
      "RSI(2) deeply oversold with price still above SMA_200 -- a classic Larry Connors mean-reversion setup, no earnings inside the expected holding window.",
    confidence: 0.68,
    rsi: null,
    sentiment_score: null,
    extra_context: null,
    status: "pending",
    human_rating: null,
    human_correction: null,
    reviewed_at: null,
    auto_approved: false,
    sft_exported: false,
  },
  {
    id: 2,
    created_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
    symbol: "TSLA",
    action: "SELL",
    quantity: 5,
    price: 248.31,
    rationale:
      "HMM regime flipped risk-off and momentum decayed below the strong-uptrend filter -- sizing down ahead of a possible macro kill-switch trip.",
    confidence: 0.91,
    rsi: 71.4,
    sentiment_score: -0.34,
    extra_context: { hmm_risk_on_probability: 0.22, vix: 24.8 },
    status: "reviewed",
    human_rating: null,
    human_correction: null,
    reviewed_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
    auto_approved: true,
    sft_exported: false,
  },
  {
    id: 3,
    created_at: new Date(Date.now() - 26 * 3600_000).toISOString(),
    symbol: "AAPL",
    action: "HOLD",
    quantity: null,
    price: 227.55,
    rationale:
      "Multifactor composite is neutral and there's no confirming catalyst -- staying flat rather than chasing a marginal signal.",
    confidence: 0.54,
    rsi: 49.1,
    sentiment_score: 0.05,
    extra_context: { multifactor_composite: 0.11 },
    status: "reviewed",
    human_rating: 4,
    human_correction:
      "Agreed with the hold, but the rationale undersells earnings-date risk -- should flag Days_To_Earnings explicitly next time.",
    reviewed_at: new Date(Date.now() - 25 * 3600_000).toISOString(),
    auto_approved: false,
    sft_exported: true,
  },
  {
    id: 4,
    created_at: new Date(Date.now() - 10 * 60_000).toISOString(),
    symbol: "MSFT",
    action: "BUY",
    quantity: 8,
    price: 412.02,
    rationale:
      "Cross-sectional 12-1M momentum rank in the top decile, low realized vol, quality factor z-score strongly positive.",
    confidence: 0.77,
    rsi: 58.9,
    sentiment_score: 0.21,
    extra_context: { xsec_momentum_rank: 0.94 },
    status: "pending",
    human_rating: null,
    human_correction: null,
    reviewed_at: null,
    auto_approved: false,
    sft_exported: false,
  },
  {
    id: 5,
    created_at: new Date(Date.now() - 30 * 3600_000).toISOString(),
    symbol: "GOOGL",
    action: "BUY",
    quantity: 4,
    price: 178.4,
    rationale:
      "Value + quality composite both strongly positive, sector rotation into Communication Services confirmed by Sector Heat Factor.",
    confidence: 0.83,
    rsi: 55.3,
    sentiment_score: 0.28,
    extra_context: null,
    status: "reviewed",
    human_rating: 5,
    human_correction: null,
    reviewed_at: new Date(Date.now() - 29 * 3600_000).toISOString(),
    auto_approved: false,
    sft_exported: false,
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
  command_count: 6,
  dead_letters: [],
  reason: null,
  // The live STRATEGY_REGISTRY from scripts/refresh_validations.py, kept in
  // sync with commandParse.ts's REGISTERED_STRATEGIES constant on purpose.
  strategy_registry: [
    "rsi2_mean_reversion",
    "timeseries_momentum",
    "macd_trend",
    "coppock_momentum",
    "multifactor_lowvol_size",
    "garch_vol_target",
    "cross_sectional_momentum",
    "relative_strength_xsec",
    "rsi14_extremes",
    "sortino_drawdown",
    "dividend_yield_edgar_pit",
    "deep_value_edgar_pit",
    "value_quality_edgar_pit",
    "macro_regime_pit",
    "forecast_direction_arima_hw",
    "signal_replay_balanced_blend",
    "sector_quality_rank",
    "lgbm_ranker",
    "pairs_trading",
    "aroon_trend",
  ],
  commands: [
    {
      name: "main.py",
      invocation: "python3 main.py",
      aliases: [],
      description:
        "Clean advisory orchestrator — one full cycle (or loop with --interval).",
      positionals: [],
      subcommands: [],
      options: [
        {
          name: "--interval",
          aliases: ["--interval"],
          description: "refresh cadence in seconds (0 = run once)",
          default: 0,
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: "SECONDS",
          takes_value: true,
        },
        {
          name: "--refresh-account",
          aliases: ["--refresh-account"],
          description: "force a fresh Robinhood login this run",
          default: false,
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: null,
          takes_value: false,
        },
        {
          name: "--agent",
          aliases: ["--agent"],
          description: null,
          default: false,
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: null,
          takes_value: false,
        },
      ],
    },
    {
      name: "validation.harness",
      invocation: "python -m validation.harness",
      aliases: [],
      description:
        "Run the strategy validation harness (PBO/DSR/Sharpe/MaxDD gates).",
      positionals: [],
      subcommands: [],
      options: [
        {
          name: "--strategy",
          aliases: ["--strategy"],
          description: "registered strategy name",
          default: null,
          choices: null,
          required: true,
          arg_kind: "required",
          metavar: null,
          takes_value: true,
        },
        {
          name: "--start",
          aliases: ["--start"],
          description: "backtest start date",
          default: "2020-01-01",
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: null,
          takes_value: true,
        },
        {
          name: "--end",
          aliases: ["--end"],
          description: "backtest end date",
          default: "2023-12-31",
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: null,
          takes_value: true,
        },
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
        {
          name: "--json",
          aliases: ["--json"],
          description: "machine-readable JSON output",
          default: false,
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: null,
          takes_value: false,
        },
        {
          name: "--skip",
          aliases: ["--skip"],
          description: "checks to skip",
          default: null,
          choices: null,
          required: false,
          arg_kind: "variadic",
          metavar: "CHECK",
          takes_value: true,
        },
        {
          name: "--fire-alerts",
          aliases: ["--fire-alerts"],
          description: "send alerts on failure",
          default: false,
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: null,
          takes_value: false,
        },
      ],
    },
    {
      name: "snapshot_diff.py",
      invocation: "python scripts/snapshot_diff.py",
      aliases: [],
      description: "Diff two state snapshots.",
      positionals: [
        {
          name: "prev",
          description: "earlier snapshot",
          default: null,
          choices: null,
          arg_kind: "optional",
          metavar: null,
        },
        {
          name: "curr",
          description: "later snapshot",
          default: null,
          choices: null,
          arg_kind: "optional",
          metavar: null,
        },
      ],
      subcommands: [],
      options: [
        {
          name: "--format",
          aliases: ["--format"],
          description: "output format",
          default: "markdown",
          choices: ["markdown", "json"],
          required: false,
          arg_kind: "optional",
          metavar: null,
          takes_value: true,
        },
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
            {
              name: "id",
              description: "prompt id",
              default: null,
              choices: null,
              arg_kind: "required",
              metavar: null,
            },
          ],
          subcommands: [],
          options: [
            {
              name: "--version",
              aliases: ["--version", "-v"],
              description: "pin a specific version",
              default: null,
              choices: null,
              required: false,
              arg_kind: "optional",
              metavar: null,
              takes_value: true,
            },
            {
              name: "--raw",
              aliases: ["--raw"],
              description: "print the raw template",
              default: false,
              choices: null,
              required: false,
              arg_kind: "optional",
              metavar: null,
              takes_value: false,
            },
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
    {
      name: "refresh_validations.py",
      invocation: "python -m scripts.refresh_validations",
      aliases: [],
      description: "Run walk-forward validation for registered strategies (monthly cadence).",
      positionals: [],
      subcommands: [],
      options: [
        {
          name: "--strategies",
          aliases: ["--strategies"],
          description: "Comma-separated strategy names to validate. Default: all.",
          default: null,
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: null,
          takes_value: true,
        },
        {
          name: "--start",
          aliases: ["--start"],
          description: "Backtest start date (default: 2005-01-01).",
          default: "2005-01-01",
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: "YYYY-MM-DD",
          takes_value: true,
        },
        {
          name: "--end",
          aliases: ["--end"],
          description: "Backtest end date (default: today).",
          default: null,
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: "YYYY-MM-DD",
          takes_value: true,
        },
        {
          name: "--output-dir",
          aliases: ["--output-dir"],
          description: "Directory for JSON report output (default: reports/).",
          default: "reports",
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: null,
          takes_value: true,
        },
        {
          name: "--n-cpcv-splits",
          aliases: ["--n-cpcv-splits"],
          description: "Number of CPCV splits (default: 10).",
          default: 10,
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: null,
          takes_value: true,
        },
        {
          name: "--n-test-splits",
          aliases: ["--n-test-splits"],
          description: "Walk-forward test splits (default: 2).",
          default: 2,
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: null,
          takes_value: true,
        },
        {
          name: "--workers",
          aliases: ["--workers", "-w"],
          description: "Number of concurrent workers for strategy validation (default: 1 for sequential).",
          default: 1,
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: null,
          takes_value: true,
        },
        {
          name: "--json",
          aliases: ["--json"],
          description:
            "Also print ONE machine-readable JSON line (the LAST line of stdout) mapping strategy_id -> {deployable, pbo, dsr, sharpe, max_drawdown[, error]}.",
          default: false,
          choices: null,
          required: false,
          arg_kind: "optional",
          metavar: null,
          takes_value: false,
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
 * against a genuine follow. `strategy`/`sources`/`proposed_price` are the
 * queue builder's real per-intent attribution fields (never guessed —
 * CONSTRAINT #4): AAPL carries all three so the expanded row's metadata and
 * SignalContributionPanel have something real-looking to show; TSLA omits
 * `sources` (a Pilot-follow intent has no underlying news/sentiment sources
 * of its own) to exercise the "field genuinely absent" rendering path too.
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
      strategy: "timeseries_momentum",
      sources: ["fmp_news", "edgar_8k"],
      proposed_price: 231.42,
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
      strategy: "trend_following_pilot_mirror",
      proposed_price: 214.9,
    },
  ],
};

// ---- Local scan-config store (localStorage) — a small localStorage-backed
// mock store; backs the Agentic Trading tab's Discovery section. Seeded
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
  {
    jobType: string;
    commandName: string | null;
    startedAt: number;
    createdAt: string;
    cancelled: boolean;
  }
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
      'every changed file. Respond in JSON: {"status": "PASSED/FAILED", ' +
      '"score": 0-100, "findings": []}. (mock remote body)',
  },
  "gravity.step_01": {
    unpinnedVersion: "1.1.0",
    unpinnedSource: "cache",
    cachedVersions: ["1.1.0", "1.0.0"],
    body:
      "Analyze the provided source code for Step 1. Verify vectorized " +
      "Pandas/NumPy operations and a relational database schema. Respond in " +
      'JSON: {"status": "PASSED/FAILED", "score": 0-100}. (mock cached body)',
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

/**
 * Honest cold-start fixture for GET /metrics/sentiment/{symbol}'s news-feed
 * fields: no news provider configured (FMP_NEWS_ENABLED is off or
 * FMP_API_KEY is unset), so there are no headlines and no earnings-catalyst read
 * at all — never a fabricated headline list or a guessed dampening state.
 * Deliberately independent of the Antigravity-agent `source` field (a
 * different, unrelated data path — see `source: "unavailable"` covered
 * inline in SentimentDynamics.test.tsx) so this fixture isolates the
 * news-provider-not-configured state on its own. Exported so both
 * SentimentDynamics.test.tsx and any other caller can exercise this real
 * state directly, alongside the populated "fmp" example getSentimentDynamics
 * returns by default.
 */
/**
 * The honest "nothing computed yet" shape for SentimentDynamics's five
 * FinBERT/Sector-Heat/Attention fields (source_breakdown/raw_sentiment_avg/
 * dampened_sentiment_score/attention_score/sector_heat_factor) — spread into
 * any fixture that isn't specifically exercising these fields, instead of
 * hand-copying the same five nulls into every fixture literal.
 */
export const emptySentimentDynamicsExtras = {
  source_breakdown: {},
  raw_sentiment_avg: null,
  dampened_sentiment_score: null,
  attention_score: null,
  sector_heat_factor: null,
} satisfies Pick<
  SentimentDynamics,
  | "source_breakdown"
  | "raw_sentiment_avg"
  | "dampened_sentiment_score"
  | "attention_score"
  | "sector_heat_factor"
>;

export const mockNoProviderSentimentFixture: SentimentDynamics = {
  ticker: "ZZZZ",
  date: new Date().toISOString(),
  sentiment_score: 0.15,
  sentiment_intensity: 0.72,
  credibility_score: 0.85,
  volatility_persistence: 0.94,
  source: "antigravity_agent",
  headlines: [],
  earnings_catalyst: null,
  provider_used: "none",
  ...emptySentimentDynamicsExtras,
};

/**
 * Honest empty/degraded "This Week's Digest" fixture -- the view-tracking
 * log had too little history to personalize this cycle, so the digest fell
 * back to a lower rung of the fallback ladder (§4 of the implementation
 * plan). Exported so a dedicated test can override the default happy-path
 * mock for exactly one call, mirroring `mockNoProviderSentimentFixture`'s
 * pattern above.
 */
export const mockDegradedDigestFixture: DigestPayload = {
  items: [
    // confidence_tier: "low" for both -- "Today's Radar" is the honest
    // fallback selection_type (personalization isn't active this cycle),
    // and the real backend's DigestItem.__post_init__ (pilots/digest_models.py)
    // can never pair it with anything but "low". This fixture previously
    // hand-typed "high"/"medium" here, exactly the fabrication this
    // degraded-state fixture exists to demonstrate NOT rendering.
    { symbol: "NVDA", reason: "Highest Multifactor Composite in the tracked universe today (Size Z +2.1, Quality Z +1.9).", selection_type: "Today's Radar", confidence_tier: "low" },
    { symbol: "MSFT", reason: "#2 by Multifactor Composite in the tracked universe today (Size Z +1.8, Quality Z +1.3).", selection_type: "Today's Radar", confidence_tier: "low" },
  ],
  generated_at: new Date().toISOString(),
  personalization_active: false,
  reason: "Not enough browsing history yet to personalize this digest -- showing today's top Radar picks instead.",
};

// =============================================================================
// Retrospective Learning Loop Mock Fixtures (R4, R5, R6)
// =============================================================================

export const MOCK_RETROSPECTIVE_TRADES: RetrospectiveTradeRecord[] = [
  // 1. Happy-path signal-driven trade
  {
    trade_id: 101,
    symbol: "AAPL",
    strategy_id: "trend_following",
    pilot_id: "pilot_alpha",
    experiment_arm: "arm_a",
    side: "BUY",
    qty: 100,
    entry_ts: "2026-09-01T10:00:00Z",
    entry_price: 150.0,
    exit_ts: "2026-09-05T15:30:00Z",
    exit_price: 162.5,
    commission: 0.0,
    realized_pnl: 1250.0,
    realized_pnl_pct: 0.0833,
    holding_period_days: 4.23,
    close_reason: "take_profit",
    provenance: "signal_driven",
    snapshot: {
      captured: true,
      decision_context_status: "captured",
      snapshot_id: "snap-aapl-101",
      trade_id: 101,
      symbol: "AAPL",
      strategy_id: "trend_following",
      entry_ts: "2026-09-01T10:00:00Z",
      entry_price: 150.0,
      side: "BUY",
      qty: 100,
      provenance: "signal_driven",
      conviction: 0.85,
      macro_regime: "BULLISH_TREND",
      signal_score: 0.78,
      raw_forecast: 0.045,
      key_indicators_json: JSON.stringify({ rsi: 58.2, macd: 1.45, adx: 28.6 }),
      decision_rationale: null,
    },
    bridge_status: "bridged",
    bridged_trade_id: 20101,
    bridge_error: null,
    bridged_at: "2026-09-05T15:30:01Z",
    excursion: {
      evaluation_status: "available",
      status: "available",
      bridge_reached: true,
      // Fractions of entry price (evaluation_engine.py's contract), never
      // dollar amounts -- MAE is always a positive magnitude.
      mae: 0.008,
      mfe: 0.0966,
      edge_ratio: 12.08,
      realized_slippage: 0.02,
      reason: null,
    },
    calibration: {
      status: "available",
      calibration_status: "available",
      conviction: 0.85,
      bin_range: [0.8, 1.0],
      bin_center: 0.9,
      bin_win_rate: 0.82,
      historical_bin_win_rate: 0.82,
      bin_trade_count: 22,
      calibration_error: 0.03,
      reason: null,
    },
    narrative: "Executed signal-driven BUY on AAPL with 0.85 conviction in BULLISH_TREND regime. Hold-period excursion reached MFE +9.7% vs MAE -0.8% (Edge Ratio: 12.08x).",
  },
  // 2. Manual discretionary trade (calibration not applicable)
  {
    trade_id: 102,
    symbol: "MSFT",
    strategy_id: null,
    pilot_id: null,
    side: "BUY",
    qty: 50,
    entry_ts: "2026-09-03T11:15:00Z",
    entry_price: 320.0,
    exit_ts: "2026-09-06T14:45:00Z",
    exit_price: 328.0,
    commission: 0.0,
    realized_pnl: 400.0,
    realized_pnl_pct: 0.025,
    holding_period_days: 3.15,
    close_reason: "manual_flatten",
    provenance: "manual",
    snapshot: {
      captured: true,
      decision_context_status: "captured",
      snapshot_id: "snap-msft-102",
      trade_id: 102,
      symbol: "MSFT",
      strategy_id: null,
      entry_ts: "2026-09-03T11:15:00Z",
      entry_price: 320.0,
      side: "BUY",
      qty: 50,
      provenance: "manual",
      conviction: null,
      macro_regime: null,
      signal_score: null,
      raw_forecast: null,
      key_indicators_json: null,
      decision_rationale: "Discretionary swing entry ahead of product announcement.",
    },
    bridge_status: "bridged",
    bridged_trade_id: 20102,
    bridge_error: null,
    bridged_at: "2026-09-06T14:45:01Z",
    excursion: {
      evaluation_status: "available",
      status: "available",
      bridge_reached: true,
      mae: 0.0125,
      mfe: 0.0703,
      edge_ratio: 5.63,
      realized_slippage: 0.01,
      reason: null,
    },
    calibration: {
      status: "not_applicable",
      calibration_status: "not_applicable",
      conviction: null,
      bin_range: null,
      bin_center: null,
      bin_win_rate: null,
      historical_bin_win_rate: null,
      // null -- never a fabricated 0 -- no calibration lookup was ever
      // attempted for a manual/not_applicable trade.
      bin_trade_count: null,
      calibration_error: null,
      reason: "Model calibration not applicable for manual or uncalibrated trades",
    },
    narrative: "Manual discretionary BUY on MSFT closed with +$400.00 realized PnL. Note: Discretionary swing entry ahead of product announcement.",
  },
  // 3. Pre-feature historical trade (snapshot not captured)
  {
    trade_id: 103,
    symbol: "NVDA",
    strategy_id: "breakout",
    pilot_id: null,
    side: "BUY",
    qty: 25,
    entry_ts: "2026-02-10T13:00:00Z",
    entry_price: 450.0,
    exit_ts: "2026-02-15T15:00:00Z",
    exit_price: 435.0,
    commission: 0.0,
    realized_pnl: -375.0,
    realized_pnl_pct: -0.0333,
    holding_period_days: 5.08,
    close_reason: "stop_loss",
    provenance: "unknown",
    snapshot: {
      captured: false,
      decision_context_status: "not_captured",
      provenance: "unknown",
      reason: "not captured",
    },
    bridge_status: "bridged",
    bridged_trade_id: 20103,
    bridge_error: null,
    bridged_at: "2026-02-15T15:00:01Z",
    excursion: {
      evaluation_status: "available",
      status: "available",
      bridge_reached: true,
      mae: 0.05,
      mfe: 0.013,
      edge_ratio: 0.26,
      realized_slippage: 0.05,
      reason: null,
    },
    calibration: {
      status: "not_applicable",
      calibration_status: "not_applicable",
      conviction: null,
      bin_range: null,
      bin_center: null,
      bin_win_rate: null,
      historical_bin_win_rate: null,
      bin_trade_count: null,
      calibration_error: null,
      reason: "Snapshot not captured",
    },
    narrative: "Executed BUY on NVDA; entry context not captured. Hold-period excursion reached MAE -5.0% vs MFE +1.3%.",
  },
  // 4. Failed bridge trade (excursion data unavailable)
  {
    trade_id: 104,
    symbol: "TSLA",
    strategy_id: "volatility_breakout",
    pilot_id: "pilot_beta",
    side: "BUY",
    qty: 40,
    entry_ts: "2026-08-20T09:45:00Z",
    entry_price: 210.0,
    exit_ts: "2026-08-22T16:00:00Z",
    exit_price: 218.0,
    commission: 0.0,
    realized_pnl: 320.0,
    realized_pnl_pct: 0.0381,
    holding_period_days: 2.26,
    close_reason: "target_reached",
    provenance: "signal_driven",
    snapshot: {
      captured: true,
      decision_context_status: "captured",
      snapshot_id: "snap-tsla-104",
      trade_id: 104,
      symbol: "TSLA",
      strategy_id: "volatility_breakout",
      entry_ts: "2026-08-20T09:45:00Z",
      entry_price: 210.0,
      side: "BUY",
      qty: 40,
      provenance: "signal_driven",
      conviction: 0.72,
      macro_regime: "HIGH_VOLATILITY",
    },
    bridge_status: "failed",
    bridged_trade_id: null,
    bridge_error: "Database lock contention during TransactionsStore write",
    bridged_at: null,
    excursion: {
      evaluation_status: "evaluation data unavailable",
      status: "evaluation data unavailable",
      bridge_reached: false,
      mae: null,
      mfe: null,
      edge_ratio: null,
      realized_slippage: null,
      reason: "Evaluation data unavailable: bridge status 'failed'",
    },
    calibration: {
      status: "available",
      calibration_status: "available",
      conviction: 0.72,
      bin_range: [0.6, 0.8],
      bin_center: 0.7,
      bin_win_rate: 0.68,
      historical_bin_win_rate: 0.68,
      bin_trade_count: 18,
      calibration_error: 0.04,
      reason: null,
    },
    narrative: "Executed signal-driven BUY on TSLA with 0.72 conviction. Evaluation data unavailable: bridge status 'failed'.",
  },
  // 5. Worst-Case Combined Failure (WP-H): manual + uncaptured + failed bridge
  {
    trade_id: 105,
    symbol: "GOOGL",
    strategy_id: null,
    pilot_id: null,
    side: "BUY",
    qty: 10,
    entry_ts: null,
    entry_price: 0,
    exit_ts: "2026-09-08T15:00:00Z",
    exit_price: 165.0,
    commission: 0.0,
    realized_pnl: 0.0,
    realized_pnl_pct: null,
    holding_period_days: null,
    close_reason: "unknown",
    // Provenance is "unknown" -- NEVER "manual" -- when captured is false.
    // The composer's own anti-fabrication gate forces this unconditionally
    // for a missing snapshot (it never trusts ANY provenance value absent a
    // genuinely captured snapshot, regardless of what a caller might have
    // separately believed about how the trade was placed) -- this IS the
    // worst-case scenario's whole point: three independent honest
    // "unavailable"/"not captured" signals, never a plausible-sounding guess.
    provenance: "unknown",
    snapshot: {
      captured: false,
      decision_context_status: "not_captured",
      provenance: "unknown",
      reason: "not captured",
    },
    bridge_status: "failed",
    bridged_trade_id: null,
    bridge_error: "Simulated bridge failure on legacy trade",
    bridged_at: null,
    excursion: {
      evaluation_status: "evaluation data unavailable",
      status: "evaluation data unavailable",
      bridge_reached: false,
      mae: null,
      mfe: null,
      edge_ratio: null,
      realized_slippage: null,
      reason: "Evaluation data unavailable: trade not bridged",
    },
    calibration: {
      status: "not_applicable",
      calibration_status: "not_applicable",
      conviction: null,
      bin_range: null,
      bin_center: null,
      bin_win_rate: null,
      historical_bin_win_rate: null,
      bin_trade_count: null,
      calibration_error: null,
      reason: "Model calibration not applicable for manual or uncalibrated trades",
    },
    narrative: "Trade executed with unrecorded provenance; entry context not captured and evaluation data unavailable (bridge failed).",
  },
];

export const MOCK_RETROSPECTIVE_INSIGHTS: BatchRetrospectiveInsightsResponse = {
  automated_cohort: {
    cohort_name: "Automated (Signal-Driven)",
    total_trades: 12,
    trade_count: 12,
    winning_trades: 9,
    losing_trades: 3,
    breakeven_trades: 0,
    win_rate: 0.75,
    total_realized_pnl: 4850.0,
    profit_factor: 2.85,
    mean_holding_period_days: 3.8,
    mean_edge_ratio: 4.12,
    // Fractions of entry price, always a positive magnitude for MAE
    // (evaluation_engine.py's F-02 fix) -- never a negative dollar amount.
    mean_mae: 0.095,
    mean_mfe: 0.42,
    symbols: ["AAPL", "NVDA", "TSLA"],
    calibration_brier_score: 0.118,
    strategies: {
      trend_following: {
        trades: 7,
        total_trades: 7,
        winning_trades: 6,
        losing_trades: 1,
        win_rate: 0.857,
        total_pnl: 3200.0,
        total_realized_pnl: 3200.0,
        mean_edge_ratio: 5.4,
      },
      volatility_breakout: {
        trades: 5,
        total_trades: 5,
        winning_trades: 3,
        losing_trades: 2,
        win_rate: 0.6,
        total_pnl: 1650.0,
        total_realized_pnl: 1650.0,
        mean_edge_ratio: 2.84,
      },
    },
  },
  signal_driven_cohort: {
    cohort_name: "Automated (Signal-Driven)",
    total_trades: 12,
    winning_trades: 9,
    losing_trades: 3,
    breakeven_trades: 0,
    win_rate: 0.75,
    total_realized_pnl: 4850.0,
    profit_factor: 2.85,
    mean_holding_period_days: 3.8,
    mean_edge_ratio: 4.12,
    mean_mae: 0.095,
    mean_mfe: 0.42,
    symbols: ["AAPL", "NVDA", "TSLA"],
  },
  manual_cohort: {
    cohort_name: "Manual (Discretionary)",
    total_trades: 6,
    trade_count: 6,
    winning_trades: 3,
    losing_trades: 3,
    breakeven_trades: 0,
    win_rate: 0.5,
    total_realized_pnl: 450.0,
    profit_factor: 1.15,
    mean_holding_period_days: 1.8,
    mean_edge_ratio: 1.75,
    mean_mae: 0.18,
    mean_mfe: 0.22,
    symbols: ["MSFT", "GOOGL"],
    calibration_status: "not_applicable",
  },
  unrecorded_cohort: {
    cohort_name: "Unrecorded (Pre-Feature / Missing Snapshot)",
    total_trades: 3,
    trade_count: 3,
    winning_trades: 1,
    losing_trades: 2,
    breakeven_trades: 0,
    win_rate: 0.333,
    total_realized_pnl: -250.0,
    profit_factor: 0.7,
    mean_holding_period_days: 4.5,
    mean_edge_ratio: 1.1,
    mean_mae: 0.21,
    mean_mfe: 0.18,
    symbols: ["NVDA", "SPY"],
    note: "Historical trades without entry snapshot; excluded from systematic model evaluation.",
  },
  contrastive_insights: [
    "Automated strategies achieved a 75.0% win rate (Edge Ratio: 4.12) over a 3.8-day average holding period (N=12), compared to manual discretionary trading's 50.0% win rate (Edge Ratio: 1.75) over a 1.8-day average holding period (N=6).",
    // Measurement only -- no causal attribution ("indicating wider loss
    // tolerance or delayed stop execution" was an unearned inference this
    // module has no statistical basis for; see pilots/retrospective_
    // insights.py's _build_contrastive_insights).
    "Manual trades measured a higher average adverse excursion than automated trades (MAE 18.0% vs 9.5%; N=6 manual, N=12 automated).",
    "Model conviction calibration is operating with a Brier score of 0.118 across 12 automated trades.",
  ],
  bridge_health: {
    bridge_enabled: true,
    total_closed_trades: 24,
    attempted_count: 21,
    bridged_count: 19,
    failed_count: 2,
    disabled_count: 0,
    excluded_count: 3,
    completeness_pct: 90.48,
    status: "degraded",
    last_failure: {
      trade_id: 104,
      symbol: "TSLA",
      timestamp: "2026-08-22T16:00:00Z",
      error: "Database lock contention during TransactionsStore write",
    },
  },
};

export const MOCK_BRIDGE_RELIABILITY: BridgeReliabilityResponse = MOCK_RETROSPECTIVE_INSIGHTS.bridge_health;

// ================= public mock API (shape-identical to client.ts) =================
export const mockApi = {
    getWeeklyDigest: async (): Promise<DigestPayload> => {
      await delay(300);
      return {
        items: [
          // confidence_tier here mirrors pilots/digest_models.py's real,
          // structurally-enforced mapping: "Today's Radar" -> "low",
          // "Sector Gap" -> "medium", "Personalized" -> "high" -- this
          // fixture previously (wrongly) paired "Today's Radar" with
          // "high".
          { symbol: "NVDA", reason: "Highest Multifactor Composite in the tracked universe today (Size Z +2.1, Quality Z +1.9).", selection_type: "Today's Radar", confidence_tier: "low" },
          { symbol: "PG", reason: "Added to balance Consumer Staples exposure.", selection_type: "Sector Gap", confidence_tier: "medium" },
          { symbol: "AAPL", reason: "Based on your recent viewing history.", selection_type: "Personalized", confidence_tier: "high" }
        ],
        generated_at: new Date().toISOString(),
        personalization_active: true,
        reason: null,
      };
    },
    getSignalsRadar: async (limit = 10): Promise<RadarFeedResponse> => {
      await delay(300);
      // A realistic mixed-honesty payload, matching the real live shape
      // observed in output/state_snapshot.json -- not every symbol has every
      // sub-factor z-score computed every cycle (SRET-style below has a real
      // composite but a missing lowvol_z), so this exercises the null-safe
      // reason-string rendering path even in mock mode.
      const all: RadarFeedResponse["items"] = [
        {
          symbol: "NVDA", rank: 1, multifactor_composite: 1.42,
          value_z: -0.3, quality_z: 1.9, lowvol_z: -0.8, size_z: 2.1,
          sector: "Technology", price: 118.4,
          reason: "Highest Multifactor Composite in the tracked universe today (Size Z +2.1, Quality Z +1.9).",
        },
        {
          symbol: "MSFT", rank: 2, multifactor_composite: 1.05,
          value_z: 0.4, quality_z: 1.3, lowvol_z: 0.6, size_z: 1.8,
          sector: "Technology", price: 415.2,
          reason: "#2 by Multifactor Composite in the tracked universe today (Size Z +1.8, Quality Z +1.3).",
        },
        {
          symbol: "JNJ", rank: 3, multifactor_composite: 0.87,
          value_z: 0.6, quality_z: 0.7, lowvol_z: null, size_z: 1.1,
          sector: "Healthcare", price: 156.9,
          reason: "#3 by Multifactor Composite in the tracked universe today (Size Z +1.1, Quality Z +0.7).",
        },
        {
          symbol: "AAPL", rank: 4, multifactor_composite: 0.63,
          value_z: -0.4, quality_z: 1.2, lowvol_z: 0.3, size_z: -1.9,
          sector: "Technology", price: 224.2,
          reason: "#4 by Multifactor Composite in the tracked universe today (Size Z -1.9, Quality Z +1.2).",
        },
        {
          symbol: "XOM", rank: 5, multifactor_composite: 0.5,
          value_z: 1.35, quality_z: -0.2, lowvol_z: 0.75, size_z: -1.55,
          sector: "Energy", price: 118.6,
          reason: "#5 by Multifactor Composite in the tracked universe today (Value Z +1.4, Size Z -1.6).",
        },
      ];
      const capped = Math.max(1, Math.min(limit, 50));
      return {
        as_of: new Date().toISOString(),
        items: all.slice(0, capped),
        reason: null,
      };
    },
    getStrategyReportCard: async (): Promise<StrategyReportCardSnapshot> => {
    await delay(600);
    return [
      {
        pilot_id: "trend-following",
        name: "Trend Follower",
        category: "Momentum",
        is_pilot: true,
        predicted: {
          sharpe: 1.12,
          max_drawdown: 0.19,
          pbo: 0.31,
          dsr: 1.8,
          deployable: true,
          reason: null,
          n_trials: 1500,
          is_options_selling: false,
          stress_gate_passed: null,
          report_date: new Date().toISOString(),
        },
        actual: {
          realized_sharpe_proxy: 0.95,
          max_cumulative_drawdown_usd: 12500,
          trade_count: 42,
          win_rate: 0.55,
          avg_realized_pnl_pct: 0.02,
          total_realized_pnl_usd: 15000,
          first_exit_ts: "2024-01-01T00:00:00Z",
          last_exit_ts: "2024-06-01T00:00:00Z",
          reason: null,
        },
      },
      {
        pilot_id: "iron-condor",
        name: "Iron Condor",
        category: "Retired",
        is_pilot: false,
        // A retired options Pilot (removed from pilots/catalog.py with the
        // options desk, 2026-09 step 4a). Historical paper trades still
        // attribute here; the backend's retired-bucket branch returns the
        // fully-nulled predicted shape with this EXACT reason string, never
        // a fabricated backtest.
        predicted: {
          sharpe: null,
          max_drawdown: null,
          pbo: null,
          dsr: null,
          deployable: null,
          reason: "retired options pilot (options desk removed 2026-09)",
          n_trials: null,
          is_options_selling: null,
          stress_gate_passed: null,
          report_date: null,
        },
        actual: {
          realized_sharpe_proxy: 1.35,
          max_cumulative_drawdown_usd: 8400,
          trade_count: 56,
          win_rate: 0.75,
          avg_realized_pnl_pct: 0.05,
          total_realized_pnl_usd: 25000,
          first_exit_ts: "2024-01-01T00:00:00Z",
          last_exit_ts: "2024-06-01T00:00:00Z",
          reason: null,
        },
      },
      {
        // Real Pilot with a genuine validated backtest but zero paper trades
        // yet -- `is_pilot: true` on purpose: a non-Pilot bucket row can
        // NEVER carry populated `predicted` metrics in the real backend (see
        // "Legacy Discretionary" below), so a deployable=true predicted side
        // only ever appears alongside `is_pilot: true`.
        pilot_id: "zero-trade",
        name: "Zero Trade Strategy",
        category: "Momentum",
        is_pilot: true,
        predicted: {
          sharpe: 1.5,
          max_drawdown: 0.1,
          pbo: 0.1,
          dsr: 2.0,
          deployable: true,
          reason: null,
          n_trials: 500,
          is_options_selling: false,
          stress_gate_passed: null,
          report_date: new Date().toISOString(),
        },
        actual: {
          realized_sharpe_proxy: null,
          max_cumulative_drawdown_usd: null,
          trade_count: 0,
          win_rate: null,
          avg_realized_pnl_pct: null,
          total_realized_pnl_usd: null,
          first_exit_ts: null,
          last_exit_ts: null,
          reason: "insufficient sample (n=0)",
        },
      },
      {
        // Non-Pilot bucket: a strategy_id seen in closed paper trades that
        // has no matching entry in pilots/catalog.py's list_pilots(). The
        // real backend hardcodes this EXACT reason string ("non-pilot
        // bucket") for every such row -- never a per-row custom explanation.
        pilot_id: "non-pilot-bucket",
        name: "Legacy Discretionary",
        category: "Other",
        is_pilot: false,
        predicted: {
          sharpe: null,
          max_drawdown: null,
          pbo: null,
          dsr: null,
          deployable: null,
          reason: "non-pilot bucket",
          n_trials: null,
          is_options_selling: null,
          stress_gate_passed: null,
          report_date: null,
        },
        actual: {
          realized_sharpe_proxy: 0.88,
          max_cumulative_drawdown_usd: 22000,
          trade_count: 315,
          win_rate: 0.65,
          avg_realized_pnl_pct: 0.03,
          total_realized_pnl_usd: 45000,
          first_exit_ts: "2023-01-01T00:00:00Z",
          last_exit_ts: "2024-06-01T00:00:00Z",
          reason: null,
        },
      },
      {
        // Real Pilot, real backtest, just launched -- below the live
        // honesty floor (n=7 < MIN_TRADES_FOR_VERDICT=10) on the actual
        // side. `is_pilot: true` for the same reason as "zero-trade" above.
        pilot_id: "new-strategy",
        name: "New Strategy",
        category: "Blend",
        is_pilot: true,
        predicted: {
          sharpe: 2.1,
          max_drawdown: 0.05,
          pbo: 0.15,
          dsr: 2.5,
          deployable: true,
          reason: null,
          n_trials: 800,
          is_options_selling: false,
          stress_gate_passed: null,
          report_date: new Date().toISOString(),
        },
        actual: {
          realized_sharpe_proxy: null,
          max_cumulative_drawdown_usd: null,
          trade_count: 7,
          win_rate: null,
          avg_realized_pnl_pct: null,
          total_realized_pnl_usd: null,
          first_exit_ts: null,
          last_exit_ts: null,
          reason: "insufficient sample (n=7)",
        },
      }
    ];
  },
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
      news_coverage: newsCoverageFor(id),
    };
    return delay(detail);
  },

  async getPerformance(
    id: string,
    range: PerfRange,
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
        macro_benchmark_note: null,
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
      // synthCurve always ends "today" for every series in mock mode, so
      // there's nothing realistic to disclose here -- see
      // pilots/performance.py::pilot_performance() for the live computation.
      macro_benchmark_note: null,
    });
  },

  async getHoldings(id: string): Promise<Holding[]> {
    const p = findPilot(id);
    if (!p) throw notFound(id);
    return delay(p.holdings);
  },

  /**
   * "What if I allocated $X to this Pilot" projection. `current`/`projected`
   * are deterministically derived from BOTH the Pilot id AND the requested
   * allocation amount (a small per-Pilot seeded random walk, scaled by
   * allocation size) so different Pilots — and different allocation sizes
   * for the same Pilot — genuinely produce different numbers. A mock that
   * returned the same delta for every Pilot would reproduce the exact
   * fabrication bug this feature rebuild exists to fix.
   */
  async simulatePilotAllocation(
    pilotId: string,
    payload: PilotSimulationRequest,
  ): Promise<PilotSimulationResult> {
    const p = findPilot(pilotId);
    if (!p) throw notFound(pilotId);
    const amount = payload.allocation_amount;
    const baseSharpe = p.summary.headline.sharpe;
    const baseDD = p.summary.headline.max_drawdown;
    const symbolsTotal = p.holdings.length;

    if (baseSharpe == null || baseDD == null) {
      // Honest degradation: no backtest series behind this Pilot at all —
      // the same case getPerformance's own curve:null branch reports.
      return delay<PilotSimulationResult>({
        pilot_id: pilotId,
        current: { sharpe_ratio: baseSharpe, max_drawdown: baseDD },
        projected: { sharpe_ratio: null, max_drawdown: null },
        heat_pct_current: null,
        heat_pct_projected: null,
        coverage: { symbols_covered: 0, symbols_total: symbolsTotal },
        reason:
          "No backtest series yet — this Pilot's validation report has no persisted return curve.",
      });
    }

    // Deterministic per Pilot id + allocation size, NOT a fixed delta.
    const rng = seeded(pilotId.length * 97 + Math.round(amount / 100) + 3);
    const sizeFactor = Math.min(1, Math.max(0, amount) / 50_000);
    const sharpeDelta = (rng() - 0.5) * 0.4 * sizeFactor;
    const ddDelta = (rng() - 0.35) * 0.06 * sizeFactor;
    const projectedSharpe = +(baseSharpe + sharpeDelta).toFixed(3);
    const projectedDD = Math.min(
      1,
      Math.max(0, +(baseDD + ddDelta).toFixed(3)),
    );
    // Occasionally an honest partial-coverage Pilot (a symbol missing a
    // live quote this cycle) rather than every Pilot reporting full coverage.
    const symbolsCovered = Math.max(0, symbolsTotal - (rng() < 0.3 ? 1 : 0));
    const heatCurrent = +(0.02 + rng() * 0.03).toFixed(4);

    return delay<PilotSimulationResult>({
      pilot_id: pilotId,
      current: { sharpe_ratio: baseSharpe, max_drawdown: baseDD },
      projected: { sharpe_ratio: projectedSharpe, max_drawdown: projectedDD },
      heat_pct_current: heatCurrent,
      heat_pct_projected: null,
      coverage: {
        symbols_covered: symbolsCovered,
        symbols_total: symbolsTotal,
      },
      reason: null,
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
    const ROWS: Record<
      string,
      { coverage: CoverageStatus; held: boolean; diagnostic: string }
    > = {
      AAPL: { coverage: "full", held: true, diagnostic: "" },
      MSFT: { coverage: "full", held: true, diagnostic: "" },
      NVDA: { coverage: "stale", held: true, diagnostic: "" },
      V: {
        coverage: "quotes_only",
        held: true,
        diagnostic: "fundamentals:empty",
      },
      COST: { coverage: "full", held: true, diagnostic: "" },
      // Held in Robinhood but no live quote — a real position with unknown
      // current price, matching data.portfolio_sync.SymbolStatus (avg_cost is
      // NaN only when not held, not when merely uncovered).
      DUK: {
        coverage: "equity_only",
        held: true,
        diagnostic: "quote:NotFoundError",
      },
      // On a watchlist only (never held) and unreachable on both legs.
      T: {
        coverage: "uncovered",
        held: false,
        diagnostic: "quote:NotFoundError,fundamentals:empty",
      },
      // Probe was skipped entirely (offline/degraded mode) — never a
      // fabricated FULL/UNCOVERED guess when the probe didn't actually run.
      XOM: { coverage: "unknown", held: false, diagnostic: "probe_skipped" },
    };

    const symbols: Record<string, SyncReportSymbol> = {};
    for (const symbol of Object.keys(ROWS).sort()) {
      const { coverage, held, diagnostic } = ROWS[symbol];
      // FULL/STALE/QUOTES_ONLY all mean the quote leg succeeded — only
      // fundamentals coverage (and, for STALE, freshness) differs.
      const covered =
        coverage === "full" ||
        coverage === "stale" ||
        coverage === "quotes_only";
      const rng = seeded([...symbol].reduce((a, c) => a + c.charCodeAt(0), 0));
      const position = PORTFOLIO.positions.find((p) => p.symbol === symbol);
      symbols[symbol] = {
        symbol,
        coverage,
        held,
        quantity: held ? (position?.qty ?? 10) : 0,
        avg_cost: held
          ? (position?.avg_cost ?? +(50 + rng() * 300).toFixed(2))
          : null,
        current_price: covered ? +(50 + rng() * 400).toFixed(2) : null,
        cost_basis_delta_per_share:
          covered && held ? +((rng() - 0.5) * 40).toFixed(2) : null,
        market_value: covered ? +(1000 + rng() * 9000).toFixed(2) : null,
        is_stale_quote: coverage === "stale",
        quote_source: covered ? "fmp" : "",
        has_fundamentals: coverage === "full" || coverage === "stale",
        forecast_available: covered,
        watchlists: held ? [] : ["file:watchlist.txt"],
        diagnostic,
        rating_consecutive_bad_cycles:
          MOCK_RATING_OVERRIDES[symbol]?.consecutive_bad_cycles ?? null,
        rating_excluded: MOCK_RATING_OVERRIDES[symbol]?.excluded ?? false,
      };
    }

    return delay({
      generated_at: new Date(Date.now() - 5_400_000).toISOString(),
      positions: PORTFOLIO.positions.map((p) => p.symbol),
      watchlists: { "file:watchlist.txt": ["T", "XOM"] },
      symbols,
      provider_source: "fmp",
      fundamentals_source: "yahoo_computed",
    });
  },

  async getExplainTicker(symbol: string): Promise<ExplainTickerResponse> {
    const sym = symbol.trim().toUpperCase();

    // Scenario: AAPL (Complete profile & tracking)
    if (sym === "AAPL") {
      return delay({
        symbol: "AAPL",
        company_profile: {
          available: true,
          company_name: "Apple Inc.",
          description:
            "Apple Inc. designs, manufactures, and markets smartphones, personal computers, tablets, wearables, and accessories, and sells a variety of related services.",
          sector: "Technology",
          industry: "Consumer Electronics",
          exchange: "NASDAQ",
          website: "https://www.apple.com",
          ceo: "Tim Cook",
          market_cap: 3450000000000,
          source: "fmp",
          reason: null,
        },
        tracking: {
          tracked: true,
          held: true,
          quantity: 40,
          avg_cost: 168.2,
          market_value: 8596.0,
          watchlists: ["core", "tech_megacap"],
          coverage_status: "full",
          rating_consecutive_bad_cycles: 0,
          rating_excluded: false,
          reasons: ["Held in portfolio (40 shares)", "In watchlists: core, tech_megacap"],
        },
        factor_breakdown: {
          available: true,
          as_of: new Date(Date.now() - 3600_000).toISOString(),
          multifactor: { value_z: 0.62, quality_z: 0.94, low_vol_z: 0.71, size_z: -0.28, composite: 0.68 },
          momentum: { xsec_12_1m: 0.24, xsec_momentum_rank: 0.85 },
          volatility_regime: { regime: "EXPANSION", hmm_risk_on_probability: 0.81, garch_vol: 0.19 },
          tactical: { action: "BUY", kelly_target: 0.08, buy_range: "$208 - $214", sell_range: "$228 - $236" },
          sentiment: { aggregate_score: 0.72 },
          raw_factors: { beta: 1.12, pe_ratio: 31.4, forward_pe: 27.8, gross_margin: 0.46 },
          reason: null,
        },
        price_history_status: {
          available: true,
          bar_count: 252,
          earliest_date: "2025-09-08",
          latest_date: "2026-09-05",
          latest_close: 214.9,
          status: "ok",
          reason: null,
        },
      });
    }

    // Scenario: NVDA (Disabled FMP profile)
    if (sym === "NVDA") {
      return delay({
        symbol: "NVDA",
        company_profile: {
          available: false,
          company_name: null,
          description: null,
          sector: null,
          industry: null,
          exchange: null,
          website: null,
          ceo: null,
          market_cap: null,
          source: null,
          // Live `api/data_api.py` always emits this exact generic template
          // regardless of cause (flag off, missing key, or a live fetch
          // failure all collapse to the identical string) — that
          // cause-blindness is deliberate (see the flag-off-vs-fetch-failure
          // fabrication-risk invariant), so the mock must not invent a
          // more specific, cause-revealing message here (verified against
          // the real endpoint — .claude/explain-this-ticker audit, 2026-09-12).
          reason: "FMP profile unavailable for NVDA",
        },
        tracking: {
          tracked: true,
          held: true,
          quantity: 22,
          avg_cost: 88.4,
          market_value: 2917.2,
          watchlists: ["semis", "ai_growth"],
          coverage_status: "stale",
          rating_consecutive_bad_cycles: 1,
          rating_excluded: false,
          reasons: ["Held in portfolio (22 shares)", "In watchlists: semis, ai_growth"],
        },
        factor_breakdown: {
          available: true,
          as_of: new Date(Date.now() - 7200_000).toISOString(),
          multifactor: { value_z: 0.35, quality_z: 0.88, low_vol_z: -0.41, size_z: 0.52, composite: 0.44 },
          momentum: { xsec_12_1m: 0.61, xsec_momentum_rank: 0.96 },
          volatility_regime: { regime: "RISK_OFF", hmm_risk_on_probability: 0.34, garch_vol: 0.44 },
          tactical: { action: "BUY", kelly_target: 0.05, buy_range: "$118 - $124", sell_range: "$145 - $152" },
          sentiment: { aggregate_score: 0.85 },
          raw_factors: { beta: 1.68, pe_ratio: 62.1, forward_pe: 42.0, gross_margin: 0.75 },
          reason: null,
        },
        price_history_status: {
          available: true,
          bar_count: 252,
          earliest_date: "2025-09-08",
          latest_date: "2026-09-05",
          latest_close: 132.6,
          status: "ok",
          reason: null,
        },
      });
    }

    // Scenario: XYZ (Untracked symbol)
    if (sym === "XYZ") {
      return delay({
        symbol: "XYZ",
        company_profile: {
          available: true,
          company_name: "XYZ Technologies Corp.",
          description: "XYZ Technologies Corp. is a hypothetical enterprise hardware vendor.",
          sector: "Technology",
          industry: "Enterprise Infrastructure",
          exchange: "NYSE",
          website: "https://www.example.com/xyz",
          ceo: "Jane Doe",
          market_cap: 1250000000,
          source: "fmp",
          reason: null,
        },
        tracking: {
          tracked: false,
          held: false,
          quantity: null,
          avg_cost: null,
          market_value: null,
          watchlists: [],
          // "untracked" is the exact literal `api/data_api.py`'s explain
          // endpoint emits for a symbol that is neither held nor
          // watchlisted — a real `CoverageStatus` enum member like
          // "uncovered" is reserved for a *tracked* symbol the data
          // providers can't cover, which is a different fact entirely.
          coverage_status: "untracked",
          rating_consecutive_bad_cycles: null,
          rating_excluded: false,
          // Live `api/data_api.py` always populates at least this one
          // reason for an untracked symbol (verified against the real
          // endpoint — see .claude/explain-this-ticker audit, 2026-09-12);
          // an empty array here would let the drawer's optional "Tracking
          // reasons:" bullet list silently disappear in mock mode only.
          reasons: ["Symbol is not currently held or included in any active watchlist"],
        },
        factor_breakdown: {
          available: false,
          as_of: null,
          multifactor: null,
          momentum: null,
          volatility_regime: null,
          tactical: null,
          sentiment: null,
          raw_factors: {},
          reason: "Symbol XYZ is not tracked in the quantitative pipeline",
        },
        price_history_status: {
          available: false,
          bar_count: 0,
          earliest_date: null,
          latest_date: null,
          latest_close: null,
          status: "no_data",
          reason: "No historical bars stored for XYZ",
        },
      });
    }

    // Scenario: COST (Missing DailySignals / factor breakdown)
    if (sym === "COST") {
      return delay({
        symbol: "COST",
        company_profile: {
          available: true,
          company_name: "Costco Wholesale Corporation",
          description:
            "Costco Wholesale Corporation operates membership warehouses that offer branded and private-label products in a range of merchandise categories.",
          sector: "Consumer Defensive",
          industry: "Discount Stores",
          exchange: "NASDAQ",
          website: "https://www.costco.com",
          ceo: "Ron Vachris",
          market_cap: 395000000000,
          source: "fmp",
          reason: null,
        },
        tracking: {
          tracked: true,
          held: true,
          quantity: 6,
          avg_cost: 712.0,
          market_value: 5336.4,
          watchlists: ["consumer", "defensive"],
          coverage_status: "full",
          rating_consecutive_bad_cycles: 0,
          rating_excluded: false,
          reasons: ["Held in portfolio (6 shares)", "In watchlists: consumer, defensive"],
        },
        factor_breakdown: {
          available: false,
          as_of: null,
          multifactor: null,
          momentum: null,
          volatility_regime: null,
          tactical: null,
          sentiment: null,
          raw_factors: {},
          reason: "No daily signals computed for this cycle",
        },
        price_history_status: {
          available: true,
          bar_count: 252,
          earliest_date: "2025-09-08",
          latest_date: "2026-09-05",
          latest_close: 889.4,
          status: "ok",
          reason: null,
        },
      });
    }

    // Scenario: XOM (Missing historical price bars)
    if (sym === "XOM") {
      return delay({
        symbol: "XOM",
        company_profile: {
          available: true,
          company_name: "Exxon Mobil Corporation",
          description:
            "Exxon Mobil Corporation explores for and produces crude oil and natural gas in North America, South America, Europe, Asia, and Africa.",
          sector: "Energy",
          industry: "Oil & Gas Integrated",
          exchange: "NYSE",
          website: "https://www.corporate.exxonmobil.com",
          ceo: "Darren Woods",
          market_cap: 460000000000,
          source: "fmp",
          reason: null,
        },
        tracking: {
          tracked: true,
          held: false,
          quantity: null,
          avg_cost: null,
          market_value: null,
          watchlists: ["energy", "dividend"],
          coverage_status: "unknown",
          rating_consecutive_bad_cycles: null,
          rating_excluded: false,
          reasons: ["In watchlists: energy, dividend"],
        },
        factor_breakdown: {
          available: true,
          as_of: new Date(Date.now() - 3600_000).toISOString(),
          multifactor: { value_z: 0.88, quality_z: 0.81, low_vol_z: 0.65, size_z: 0.44, composite: 0.71 },
          momentum: { xsec_12_1m: 0.11, xsec_momentum_rank: 0.54 },
          volatility_regime: { regime: "EXPANSION", hmm_risk_on_probability: 0.66, garch_vol: 0.21 },
          tactical: { action: "HOLD", kelly_target: 0.0, buy_range: "$102 - $106", sell_range: "$118 - $124" },
          sentiment: { aggregate_score: 0.55 },
          raw_factors: { beta: 0.78, pe_ratio: 14.2, forward_pe: 12.8, dividend_yield: 0.034 },
          reason: null,
        },
        price_history_status: {
          available: false,
          bar_count: 0,
          earliest_date: null,
          latest_date: null,
          latest_close: null,
          status: "no_data",
          reason: "Historical price bars unavailable in local store",
        },
      });
    }

    // Generic fallback for any other symbol
    const held = PORTFOLIO.positions.find((p) => p.symbol === sym);
    const knownName = NAMES[sym] ?? (SYMBOL_UNIVERSE.has(sym) ? `${sym} Corp.` : null);
    const tracked = Boolean(held || SYMBOL_UNIVERSE.has(sym));
    const reasons: string[] = [];
    if (held) reasons.push(`Held in portfolio (${held.qty} shares)`);
    if (SYMBOL_UNIVERSE.has(sym) && !held) reasons.push("In tracked universe");

    return delay({
      symbol: sym,
      company_profile: knownName
        ? {
            available: true,
            company_name: knownName,
            description: `${knownName} is a tracked entity in the quantitative universe.`,
            sector: SECTOR_OF[sym] ?? "Diversified",
            industry: "General",
            exchange: "US",
            website: null,
            ceo: null,
            market_cap: held && held.market_value != null ? held.market_value * 1000000 : null,
            source: "mock",
            reason: null,
          }
        : {
            available: false,
            company_name: null,
            description: null,
            sector: null,
            industry: null,
            exchange: null,
            website: null,
            ceo: null,
            market_cap: null,
            source: null,
            reason: `Company profile not found for ${sym}`,
          },
      tracking: {
        tracked,
        held: Boolean(held),
        quantity: held?.qty ?? null,
        avg_cost: held?.avg_cost ?? null,
        market_value: held?.market_value ?? null,
        watchlists: tracked ? ["default"] : [],
        coverage_status: held ? "full" : tracked ? "quotes_only" : "uncovered",
        rating_consecutive_bad_cycles: null,
        rating_excluded: false,
        reasons,
      },
      factor_breakdown: tracked
        ? {
            available: true,
            as_of: new Date(Date.now() - 3600_000).toISOString(),
            multifactor: { value_z: 0.5, quality_z: 0.5, low_vol_z: 0.5, size_z: 0.0, composite: 0.5 },
            momentum: { xsec_12_1m: 0.0, xsec_momentum_rank: 0.5 },
            volatility_regime: { regime: "EXPANSION", hmm_risk_on_probability: 0.5, garch_vol: 0.2 },
            tactical: { action: null, kelly_target: null, buy_range: null, sell_range: null },
            sentiment: { aggregate_score: null },
            raw_factors: {},
            reason: null,
          }
        : {
            available: false,
            as_of: null,
            multifactor: null,
            momentum: null,
            volatility_regime: null,
            tactical: null,
            sentiment: null,
            raw_factors: {},
            reason: `Symbol ${sym} is not tracked in the quantitative pipeline`,
          },
      price_history_status: {
        available: tracked,
        bar_count: tracked ? 252 : 0,
        earliest_date: tracked ? "2025-09-08" : null,
        latest_date: tracked ? "2026-09-05" : null,
        latest_close: held ? held.current_price : null,
        status: tracked ? "ok" : "no_data",
        reason: tracked ? null : `No historical bars stored for ${sym}`,
      },
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
      p.holdings.filter((x) => x.symbol === sym).map((x) => x.score),
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
        kelly_target:
          position_pct == null ? null : +(position_pct * 0.5).toFixed(4),
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
        score_components: {
          momentum: +rng().toFixed(3),
          trend: +rng().toFixed(3),
        },
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
              kelly_target_post_regime:
                position_pct == null ? null : +(position_pct * 0.5).toFixed(4),
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
      new Set(tickers.map((t) => t.trim().toUpperCase()).filter(Boolean)),
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
          sector: null,
          sector_pe: null,
          sector_change_pct: null,
        };
      }

      const rng = seeded([...sym].reduce((a, c) => a + c.charCodeAt(0), 0));
      const scores = CATALOG.flatMap((p) =>
        p.holdings.filter((x) => x.symbol === sym).map((x) => x.score),
      );
      const score = scores.length
        ? +(scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(3)
        : null;
      const conviction =
        score == null ? null : +(0.55 + score * 0.35).toFixed(2);
      const action = score != null && score >= 0.5 ? "BUY" : "HOLD";
      const kelly_target =
        score == null ? null : +(Math.max(score, 0) * 0.1).toFixed(4);

      // DUK deliberately carries no meta_label_composite/regime_multiplier —
      // both fields are null whenever the strategy engine didn't produce a
      // value for a symbol that cycle (see pilots/symbols.py::compare_symbols'
      // docstring); this fixture exercises that honest-null branch instead of
      // pretending every symbol always has them.
      const hasRegimeFields = sym !== "DUK";

      // sector_pe/sector_change_pct: bulk-attached by sector name, mirroring
      // the real backend's ONE-call-per-request pattern. DUK's sector
      // ("Utilities") is deliberately absent from SECTOR_SNAPSHOT, so it
      // exercises the honest "sector has no snapshot" null branch here too.
      const sector = SECTOR_OF[sym] ?? null;
      const sectorSnap = sector ? SECTOR_SNAPSHOT[sector] : undefined;

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
        regime_multiplier: hasRegimeFields
          ? +(0.8 + rng() * 0.4).toFixed(2)
          : null,
        score_components: {
          momentum: +rng().toFixed(3),
          trend: +rng().toFixed(3),
          value: +((rng() - 0.5) * 2).toFixed(3),
        },
        sector,
        sector_pe: sectorSnap?.pe ?? null,
        sector_change_pct: sectorSnap?.change_pct ?? null,
      };
    });

    const modules = Array.from(
      new Set(
        rows.flatMap((r) =>
          r.score_components ? Object.keys(r.score_components) : [],
        ),
      ),
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
      buying_power_curve: synthCurve(
        "account-buying-power",
        range,
        0.01,
        0.03,
        6100,
      ),
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
        errors: {
          generated_at: new Date(now - 5 * 60_000).toISOString(),
          entry_count: 0,
          entries: [],
        },
        advisory_only: true,
        dry_run: false,
        paper_trading: false,
      },
      120,
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
          note: "Parsed from the repo file — the intended schedule. This API never runs `crontab -l`, so it cannot confirm what is actually installed on the host; it may differ.",
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
              comment:
                "Weekly: Full EDGAR backfill sweep (Sundays at 06:00 UTC / 2 AM ET)",
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
      80,
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
      300,
    );
  },

  async postControlPipelineMetrics(): Promise<{
    run_id: string;
    state: string;
    mode: string;
  }> {
    return delay(
      { run_id: `orch-mock-${Date.now()}`, state: "queued", mode: "metrics" },
      300,
    );
  },

  async triggerRun(): Promise<TriggerRunResult> {
    const ks = readKillSwitch();
    if (ks.active) {
      return delay(
        {
          ok: false,
          run_id: null,
          state: null,
          error: "kill_switch_active",
          existing_run_id: null,
          kill_switch_reason: ks.reason,
        },
        150,
      );
    }
    return delay(
      {
        ok: true,
        run_id: `orch-mock-${Date.now()}`,
        state: "queued",
        error: null,
        existing_run_id: null,
        kill_switch_reason: null,
      },
      300,
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
      150,
    );
  },

  async setExecutionMode(
    req: ExecutionModeUpdateRequest,
  ): Promise<ExecutionModeUpdateResult> {
    // Mirrors api/pilots_api.py's _require_dangerous_confirmation: every
    // settings_keysets.DANGEROUS_KEYS field this write is about to touch
    // (ADVISORY_ONLY always; DRY_RUN too when mode != "advisory" -- PAPER_TRADING
    // is written but is NOT a DANGEROUS_KEYS member, so it needs no confirmation)
    // must be echoed in `confirm` mapped to its own name, or nothing is written
    // -- same all-or-nothing, same 422. Hardcoded rather than derived (this file
    // has no settings_keysets.py port) -- assumes these stay in DANGEROUS_KEYS,
    // which MOCK_DANGEROUS_KEYS above (copied from the same real set) also does.
    const dangerousKeys =
      req.mode === "advisory"
        ? ["ADVISORY_ONLY"]
        : ["ADVISORY_ONLY", "DRY_RUN"];
    const confirm = req.confirm ?? {};
    const missing = dangerousKeys.filter((k) => !(k in confirm));
    const mismatched = dangerousKeys.filter(
      (k) => k in confirm && confirm[k] !== k,
    );
    if (missing.length || mismatched.length) {
      throw new ApiError(
        `${missing.length ? "confirmation_required" : "confirmation_mismatch"}: this change touches ` +
          `safety-critical setting(s) (${dangerousKeys.join(", ")}) and requires typed confirmation.`,
        422,
      );
    }
    return delay(
      {
        written:
          req.mode === "advisory"
            ? ["ADVISORY_ONLY"]
            : ["ADVISORY_ONLY", "DRY_RUN", "PAPER_TRADING"],
        advisory_only: req.advisory_only,
        mode: req.mode,
        applies: "next_daemon_restart",
        note: "Execution mode updated.",
      },
      150,
    );
  },

  async getBrokerageStatus(): Promise<BrokerageStatus> {
    return delay(
      {
        connected: readBrokerageConnected(),
        has_account_snapshot: readBrokerageConnected(),
        auto_refresh_enabled: readBrokerageAutoRefreshEnabled(),
      },
      80,
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

  async putLlmSetting(
    key: string,
    value: boolean | string,
  ): Promise<LlmSettingUpdateResult> {
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
      150,
    );
  },

  async connectBrokerage(
    creds: BrokerageConnectRequest,
  ): Promise<BrokerageLoginJob> {
    // Never contacts a real broker and never persists the credential strings
    // themselves -- only a boolean "connected" marker (writeBrokerageConnected,
    // flipped once the job actually SUCCEEDS -- see _mockLoginJobStatus).
    // No synchronous verify anymore: the real POST /brokerage/connect always
    // 202s with a running job; username/password are trusted here purely to
    // seed the mock account, mirroring the real backend never rejecting the
    // POST itself for a bad password (that surfaces later, through a poll,
    // as state: "failed" / error_code: "auth_failed" -- not modeled here
    // since the submit button already requires both fields non-empty).
    void creds;
    const jobId = `mock-login-job-${++_mockLoginJobSeq}`;
    const job: _MockLoginJob = {
      mode: "connect",
      startedAt: Date.now(),
      cancelled: false,
      simulateTimeout: readBrokerageLoginTimeout(),
      noCredentials: false,
    };
    _mockLoginJobs[jobId] = job;
    return delay(_mockLoginJobStatus(jobId, job), 150);
  },

  async disconnectBrokerage(): Promise<BrokerageDisconnectResult> {
    writeBrokerageConnected(false);
    return delay({ connected: false }, 150);
  },

  async refreshBrokerage(): Promise<BrokerageRefreshResult> {
    // Honesty branch, ported from the old synchronous refreshBrokerage():
    // nothing is configured to log back into (never connected) -- mirrors
    // the real backend discovering it has no usable credentials, surfaced
    // through the FIRST status poll as state: "failed" / error_code:
    // "no_credentials" rather than rejecting this call itself (the real
    // POST /brokerage/refresh always 202s with a running job).
    const jobId = `mock-login-job-${++_mockLoginJobSeq}`;
    const job: _MockLoginJob = {
      mode: "refresh",
      startedAt: Date.now(),
      cancelled: false,
      simulateTimeout: readBrokerageLoginTimeout(),
      noCredentials: !readBrokerageConnected(),
    };
    _mockLoginJobs[jobId] = job;
    return delay(_mockLoginJobStatus(jobId, job), 150);
  },

  async getBrokerageLoginStatus(jobId: string): Promise<BrokerageLoginJob> {
    const job = _mockLoginJobs[jobId];
    if (!job) throw new ApiError("Unknown login job.", 404);
    return delay(_mockLoginJobStatus(jobId, job), 80);
  },

  async cancelBrokerageLogin(
    jobId: string,
  ): Promise<BrokerageLoginCancelResult> {
    const job = _mockLoginJobs[jobId];
    if (!job) throw new ApiError("Unknown login job.", 404);
    job.cancelled = true;
    return delay({ ..._mockLoginJobStatus(jobId, job), cancelled: true }, 100);
  },

  async getRealized(): Promise<RealizedPerformance> {
    return delay({
      summary: realizedSummary(REALIZED_TRADES),
      trades: REALIZED_TRADES,
      n_fills: REALIZED_TRADES.length * 2,
      available: true,
    });
  },

  async getTradeHistory(
    opts: { limit?: number; offset?: number; symbol?: string } = {}
  ): Promise<TradeHistoryPage> {
    const limit = opts.limit ?? 50;
    const offset = opts.offset ?? 0;
    const symbol = opts.symbol?.toUpperCase();
    // Newest-exit-first, matching the real backend's ordering.
    const sorted = [...TRADE_HISTORY_TRADES].sort(
      (a, b) => new Date(b.exit_ts ?? 0).getTime() - new Date(a.exit_ts ?? 0).getTime()
    );
    const filtered = symbol ? sorted.filter((t) => t.symbol === symbol) : sorted;
    return delay({
      trades: filtered.slice(offset, offset + limit),
      // Summary over the FULL filtered set, not just the page -- matches
      // the real backend's contract exactly (pilots/trade_history.py).
      summary: realizedSummary(filtered),
      total: filtered.length,
      limit,
      offset,
      symbols: Array.from(new Set(TRADE_HISTORY_TRADES.map((t) => t.symbol))).sort(),
      available: true,
      source: "durable_store",
      last_ingested_at: new Date(Date.now() - 2 * 3600000).toISOString(),
    });
  },

  async getPortfolioAttribution(
    _lookbackDays = 60,
  ): Promise<PortfolioAttribution> {
    return delay(mockPortfolioAttribution());
  },

  async getBrinsonFachlerAttribution(
    rows: BrinsonFachlerRow[],
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

  async getSectorSelection(
    target: string,
    n = 3,
  ): Promise<SectorSelectionView> {
    return delay(mockSectorSelection(target, n));
  },

  async getRollingBeta(ticker: string, window = 60): Promise<RollingBeta> {
    return delay(mockRollingBeta(ticker, window));
  },

  async getModels(): Promise<ModelRow[]> {
    return delay(MODELS);
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
      return delay(
        { available: false, reason: "missing_key", payload: null },
        400,
      );
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
      400,
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
        400,
      );
    }
    return delay(
      {
        available: true,
        reason: null,
        payload: {
          pattern_name: "ascending triangle",
          trend_direction: "bullish",
          support_levels: [
            "recent low near the 50-day average",
            "prior breakout zone",
          ],
          resistance_levels: ["swing high from the last rally"],
          narrative: `${sym} is consolidating in a tightening range with a flat resistance line and rising higher-lows underneath it — a classic ascending-triangle continuation setup. A close above the recent swing high would confirm the breakout; volume has been contracting into the apex, typical ahead of a resolution.`,
          confidence: "medium",
        },
        chart_png_base64: MOCK_CHART_PNG_BASE64,
      },
      400,
    );
  },

  async generateResearch(ticker: string): Promise<AiResearchResponse> {
    const sym = ticker.trim().toUpperCase();
    if (sym === "NVDA") {
      return delay(
        { available: false, reason: "disabled", payload: null },
        400,
      );
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
          sources_note:
            "Based on 4 FMP headlines from the past 7 days and the most recent earnings date.",
        },
      },
      400,
    );
  },

  // Equity-only Quick Trade ticket -- options-free counterpart to
  // postOptionsOrder's stock branch above (see EquityOrderTicket.tsx).
  // Shares its fill math with that branch via applyMockStockFill so the two
  // can't silently drift apart.
  async postPaperEquityOrder(req: EquityOrderRequest): Promise<EquityOrderResult> {
    console.log(`[mockApi] postPaperEquityOrder (${req.isLive ? 'LIVE' : 'PAPER'}):`, req);
    if (req.isLive) {
      return delay({
        ok: false,
        order_id: null,
        message: "Live order execution is disabled in Advisory-Only mode. Please use paper mode.",
      }, 500);
    }

    const symbol = (req.symbol || "").trim().toUpperCase();
    if (!symbol) {
      return delay({ ok: false, order_id: null, message: "Symbol is required." }, 200);
    }
    const side: 'BUY' | 'SELL' = req.side?.toLowerCase() === 'sell' ? 'SELL' : 'BUY';

    const result = applyMockStockFill({
      symbol,
      side,
      quantity: req.quantity,
      dollarAmount: req.dollar_amount,
      limitPrice: req.limit_price,
      orderIdPrefix: "eq_ord",
    });
    return delay(result, 600);
  },

  async getObservabilitySummary(
    range: PerfRange,
    horizon = 30,
  ): Promise<ObservabilitySummary> {
    return delay(mockObservabilitySummary(range, horizon));
  },

  async getObservabilityLogs(limit = 300): Promise<LogAggregation> {
    return delay(
      readObservabilityColdStart()
        ? mockEmptyLogAggregation("No log file yet at logs/investyo.log.")
        : mockObservabilityLogs(limit),
    );
  },

  async putMacroGate(
    enabled: boolean,
    _reason: string,
  ): Promise<MacroGateUpdateResult> {
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
      150,
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
            bin_low: 0.4,
            bin_high: 0.5,
            bin_center: 0.45,
            conviction_mean: 0.46,
            win_rate: 0.42,
            count: 12,
            perfect_calibration: 0.45,
          },
          {
            bin_low: 0.5,
            bin_high: 0.6,
            bin_center: 0.55,
            conviction_mean: 0.55,
            win_rate: 0.58,
            count: 18,
            perfect_calibration: 0.55,
          },
          {
            bin_low: 0.6,
            bin_high: 0.7,
            bin_center: 0.65,
            conviction_mean: 0.66,
            win_rate: 0.71,
            count: 9,
            perfect_calibration: 0.65,
          },
          {
            // under min_trades_per_bin -> win_rate null (insufficient data)
            bin_low: 0.9,
            bin_high: 1.0,
            bin_center: 0.95,
            conviction_mean: 0.95,
            win_rate: null,
            count: 2,
            perfect_calibration: 0.95,
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
            symbol: "AAPL",
            signal_ts: "2026-06-20T14:00:00Z",
            signal_action: "BUY",
            conviction: 0.72,
            action_taken: "acted",
            model_return: 0.055,
            actual_return: 0.028,
            days_held: 14,
            trade_id: 42,
            completed: true,
          },
          {
            symbol: "MSFT",
            signal_ts: "2026-06-22T14:00:00Z",
            signal_action: "STRONG BUY",
            conviction: 0.81,
            action_taken: "passed",
            model_return: 0.031,
            actual_return: null,
            days_held: null,
            trade_id: null,
            completed: true,
          },
          {
            // horizon not elapsed -> model_return null, not completed
            symbol: "NVDA",
            signal_ts: "2026-07-15T14:00:00Z",
            signal_action: "BUY",
            conviction: 0.66,
            action_taken: "passed",
            model_return: null,
            actual_return: null,
            days_held: null,
            trade_id: null,
            completed: false,
          },
        ],
        reason: null,
      },
      mfe_mae: {
        points: [
          {
            symbol: "AAPL",
            mfe: 0.082,
            mae: 0.031,
            edge_ratio: 2.65,
            conviction: 0.72,
            action: "BUY",
          },
          {
            symbol: "MSFT",
            mfe: 0.054,
            mae: 0.048,
            edge_ratio: 1.13,
            conviction: 0.81,
            action: "HOLD",
          },
          // honest null edge_ratio (MAE was 0 -> undefined ratio, not fabricated)
          {
            symbol: "XOM",
            mfe: 0.026,
            mae: 0.061,
            edge_ratio: null,
            conviction: null,
            action: "SELL",
          },
        ],
        reason: null,
      },
      recent_decisions: {
        decisions: [
          {
            symbol: "AAPL",
            action_taken: "acted",
            signal_action: "BUY",
            conviction: 0.72,
            notes: "took full size",
            timestamp: "2026-07-16T15:12:00Z",
            signal_ts: "2026-06-20T14:00:00Z",
            trade_id: 42,
          },
          {
            // unlinked: no trade matched within 24h -> trade_id null, never fabricated
            symbol: "MSFT",
            action_taken: "passed",
            signal_action: "STRONG BUY",
            conviction: 0.81,
            notes: "",
            timestamp: "2026-07-15T09:03:00Z",
            signal_ts: "2026-06-22T14:00:00Z",
            trade_id: null,
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
          strategy: "trend-following",
          n_trades: 8,
          mean_edge_ratio: 2.31,
          median_edge_ratio: 2.05,
          mean_mfe: 0.074,
          mean_mae: 0.033,
        },
        {
          strategy: "dip-buyer",
          n_trades: 5,
          mean_edge_ratio: 1.42,
          median_edge_ratio: 1.28,
          mean_mfe: 0.051,
          mean_mae: 0.041,
        },
        {
          strategy: "(untagged)",
          n_trades: 3,
          mean_edge_ratio: 0.88,
          median_edge_ratio: 0.9,
          mean_mfe: 0.029,
          mean_mae: 0.036,
        },
      ],
      reason: null,
    });
  },

  async logDecision(
    body: DecisionCreateRequest,
  ): Promise<DecisionCreateResult> {
    // Mock trade-link resolution: only an "acted" AAPL decision matches a
    // (mock) trade within 24h -> trade_id set, trade_linked true. Every other
    // case is honestly unlinked (trade_id null) — exercising BOTH render paths
    // ("linked to trade #N" vs "no trade match within 24h").
    const linked =
      body.action_taken === "acted" && body.symbol.toUpperCase() === "AAPL";
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

  async getDecisions(opts?: {
    symbol?: string;
    limit?: number;
  }): Promise<DecisionEntry[]> {
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

  async getExecutionQueue(
    params?: ExecutionQueueParams,
  ): Promise<ExecutionQueue> {
    let items = MOCK_EXECUTION_QUEUE.intents;
    if (params) {
      if (params.action && params.action !== "ALL") {
        items = items.filter(
          (i) => i.action.toUpperCase() === params.action?.toUpperCase(),
        );
      }
      if (params.follow_type && params.follow_type !== "ALL") {
        items = items.filter(
          (i) =>
            i.follow_type?.toLowerCase() === params.follow_type?.toLowerCase(),
        );
      }
      if (params.status_filter && params.status_filter !== "ALL") {
        if (params.status_filter === "Ready") {
          items = items.filter((i) => i.allow_place);
        } else if (params.status_filter === "Blocked") {
          items = items.filter((i) => !i.allow_place);
        }
      }
      if (params.min_conviction !== undefined && params.min_conviction > 0) {
        items = items.filter(
          (i) =>
            i.conviction !== null &&
            i.conviction >= (params.min_conviction ?? 0),
        );
      }
    }
    const available_follow_types = Array.from(
      new Set(
        MOCK_EXECUTION_QUEUE.intents
          .map((i) => i.follow_type)
          .filter((v): v is string => Boolean(v)),
      ),
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
    body: StrategyModulesUpdate,
  ): Promise<StrategyModulesUpdateResult> {
    // Persist so a subsequent GET reflects the change, and set the drift marker
    // (the .env write does not reach the "running process" until restart).
    try {
      localStorage.setItem(
        STRATEGY_KEY,
        JSON.stringify({ weights: body.weights, disabled: body.disabled }),
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

  async getCronStatus(): Promise<CronStatus> {
    return {
      jobs: [
        {
          title: "Daily: Full pipeline refresh + morning digest",
          description: "Runs the master orchestrator",
          schedule: "0 21 * * 1-5",
          command: "cd /opt/investyo && .venv/bin/python main_orchestrator.py",
        },
        {
          title: "Daily: Strategy validation staleness",
          description: "Fires a CRITICAL alert",
          schedule: "0 8 * * *",
          command:
            "cd /opt/investyo && .venv/bin/python scripts/preflight_check.py",
        },
      ],
    };
  },

  async getTunables(): Promise<TunablesResponse> {
    return delay(mockTunables());
  },

  async updateTunables(
    values: Record<string, number | boolean | string>,
    confirm: SettingsConfirmMap = {},
  ): Promise<TunablesUpdateResult> {
    return delay(applyTunables(values, confirm));
  },

  async getSentimentSettings(): Promise<TunablesResponse> {
    return delay(mockSentimentTunables());
  },

  async updateSentimentSettings(
    values: Record<string, number | boolean | string>,
    confirm: SettingsConfirmMap = {},
  ): Promise<TunablesUpdateResult> {
    return delay(applySentimentTunables(values, confirm));
  },

  async getSectorSelectionSettings(): Promise<TunablesResponse> {
    return delay(mockSectorSelectionTunables());
  },

  async updateSectorSelectionSettings(
    values: Record<string, number | boolean | string>,
    confirm: SettingsConfirmMap = {},
  ): Promise<TunablesUpdateResult> {
    return delay(applySectorSelectionTunables(values, confirm));
  },

  async getFmpSettings(): Promise<TunablesResponse> {
    return delay(mockFmpTunables());
  },

  async updateFmpSettings(
    values: Record<string, number | boolean | string>,
    confirm: SettingsConfirmMap = {},
  ): Promise<TunablesUpdateResult> {
    return delay(applyFmpTunables(values, confirm));
  },

  async getFeatureFlags(): Promise<TunablesResponse> {
    return delay(mockFeatureFlagsTunables());
  },

  async updateFeatureFlags(
    values: Record<string, any>,
    confirm?: SettingsConfirmMap,
  ): Promise<TunablesUpdateResult> {
    return delay(applyFeatureFlagsTunables(values, confirm ?? {}));
  },

  async getSettingsReference(): Promise<SettingsReferenceResponse> {
    return delay(mockSettingsReference());
  },

  async updateSettingsReference(
    values: Record<string, boolean>,
    confirm: SettingsConfirmMap = {},
  ): Promise<TunablesUpdateResult> {
    return delay(applySettingsReference(values, confirm));
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

  // On-demand FMP peer-comparison ticker group -- mirrors the real
  // GET /data/peers/{symbol} contract (settings.FMP_PEERS_ENABLED gate) with
  // a small fixed peer list for a couple of fixture symbols, and an honest
  // empty list + reason for anything else (never a fabricated peer group).
  async getPeers(
    symbol: string,
  ): Promise<{ symbol: string; peers: string[]; reason: string | null }> {
    const sym = symbol.toUpperCase().trim();
    const PEER_GROUPS: Record<string, string[]> = {
      AAPL: ["MSFT", "GOOGL", "AMZN"],
      MSFT: ["AAPL", "GOOGL", "ADBE"],
    };
    const peers = PEER_GROUPS[sym] ?? [];
    return delay({
      symbol: sym,
      peers,
      reason: peers.length ? null : "No peer data available for this symbol.",
    });
  },

  // Free-text name/ticker search over SCREENER_UNIVERSE (defined below, near
  // seeded()). An honest empty result + reason when nothing matches -- never
  // a fabricated hit.
  async getSymbolSearch(query: string, limit?: number): Promise<SymbolSearchResponse> {
    const q = query.trim().toLowerCase();
    const results = q
      ? SCREENER_UNIVERSE
          .filter((r) => r.symbol.toLowerCase().includes(q) || (r.company_name ?? "").toLowerCase().includes(q))
          .slice(0, limit ?? 20)
          .map((r) => ({
            symbol: r.symbol,
            name: r.company_name,
            currency: "USD",
            exchange: r.exchange,
            exchange_full_name: r.exchange_short_name,
          }))
      : [];
    return delay<SymbolSearchResponse>({
      query: query.trim(),
      results,
      reason: results.length ? null : "No matching symbols found.",
    });
  },

  // Sector/industry/market-cap/price/beta/dividend/volume screener over
  // SCREENER_UNIVERSE. An honest empty result + reason when a filter
  // combination matches nothing.
  async getScreenerResults(filters: ScreenerFilters): Promise<ScreenerResultsResponse> {
    const results = SCREENER_UNIVERSE.filter((r) => matchesScreenerFilters(r, filters));
    return delay<ScreenerResultsResponse>({
      results,
      reason: results.length ? null : "No symbols matched these filters.",
    });
  },

  // Sector/industry enums derived from the SAME fixture universe (never a
  // richer list than what the screener above can actually return).
  async getScreenerFilterOptions(): Promise<ScreenerFilterOptions> {
    const sectors = [...new Set(SCREENER_UNIVERSE.map((r) => r.sector).filter((s): s is string => !!s))].sort();
    const industries = [...new Set(SCREENER_UNIVERSE.map((r) => r.industry).filter((s): s is string => !!s))].sort();
    return delay<ScreenerFilterOptions>({ sectors, industries });
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

  async getMacroHistory(
    series = "VIXCLS",
    lookbackDays = 180,
  ): Promise<MacroHistorySeries> {
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
    return delay<MacroHistorySeries>({
      series_id: seriesId,
      points,
      reason: null,
    });
  },

  // Mirrors api/data_api.py::get_quotes's real per-symbol dead-letter
  // contract exactly: a symbol the provider can't resolve is simply OMITTED
  // from the response dict, never a fabricated placeholder row (CONSTRAINT
  // #4). "V" is the fixed honesty-fixture symbol for the "unreachable"
  // branch -- it's a real, always-present member of SYMBOL_UNIVERSE (a
  // PORTFOLIO position), so MarketDataHealth's tracked-universe check always
  // exercises it. Every OTHER symbol resolves, alternating realtime-labelled
  // (FMP, fresh) vs. delayed (yfinance, `is_stale: true` by design -- see
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
        timestamp: new Date(
          Date.now() - (delayed ? 15 * 60_000 : 2_000),
        ).toISOString(),
        is_stale: delayed,
        source: delayed ? "yfinance" : "fmp",
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
      {
        symbol: "NVDA",
        action: "STRONG BUY",
        conviction: 0.88,
        score: 118.4,
        buy_range: "Buy Zone: $118.00 - $126.00",
        sector: "Information Technology",
        price: 128.72,
      },
      {
        symbol: "AAPL",
        action: "BUY",
        conviction: 0.72,
        score: 96.8,
        buy_range: "Buy Zone: $210.00 - $222.00",
        sector: "Information Technology",
        price: 224.15,
      },
      {
        symbol: "JPM",
        action: "BUY",
        conviction: 0.64,
        score: 78.9,
        buy_range: "Buy Zone: $196.00 - $203.00",
        sector: "Financials",
        price: 205.6,
      },
      {
        symbol: "XOM",
        action: "BUY",
        conviction: 0.58,
        score: 71.2,
        buy_range: "Buy Zone: $106.00 - $111.00",
        sector: "Energy",
        price: 112.4,
      },
      {
        symbol: "ZZ",
        action: "BUY",
        conviction: null,
        score: null,
        buy_range: null,
        sector: null,
        price: null,
      },
    ];
    const recommendations = all.slice(0, Math.max(1, Math.min(limit, 200)));
    return delay<RecommendationsResponse>({
      recommendations,
      count: recommendations.length,
      as_of: "2026-07-11T21:05:00+00:00",
      reason: recommendations.length
        ? null
        : "No BUY-rated recommendations in the latest snapshot yet.",
    });
  },

  async getDataUniverse(): Promise<UniverseListResponse> {
    const isFallback = MOCK_ACTIVE_WATCHLIST.length === 0;
    const effective = isFallback
      ? [...MOCK_DATA_UNIVERSE].sort()
      : [...MOCK_ACTIVE_WATCHLIST].sort();
    const note = isFallback
      ? `DEFAULT_TICKERS (${MOCK_DATA_UNIVERSE.length} symbol(s)) is very likely the effective ` +
        "per-cycle universe right now (no watchlist/discovery symbols configured), " +
        "though held Robinhood positions -- not reflected here -- are unioned on " +
        "top of it at run time and are never suppressed by it."
      : `DEFAULT_TICKERS (${MOCK_DATA_UNIVERSE.length} symbol(s)) is NOT the effective per-cycle ` +
        `universe: ${effective.length} symbol(s) from your watchlist/discovery ` +
        "take precedence, and DEFAULT_TICKERS is not consulted at all this cycle. " +
        "Held Robinhood positions (not reflected here) may add further symbols on " +
        "top of this.";
    return delay<UniverseListResponse>({
      symbols: [...MOCK_DATA_UNIVERSE],
      count: MOCK_DATA_UNIVERSE.length,
      effective_symbols: effective,
      effective_count: effective.length,
      default_tickers_is_fallback: isFallback,
      note,
    });
  },

  async updateDataUniverse(
    symbols: string[],
  ): Promise<{ status: string; symbols: string[] }> {
    // Mirror the backend PUT: strip/upper/dedupe, then replace the whole list.
    const cleaned = Array.from(
      new Set(symbols.map((s) => s.trim().toUpperCase()).filter(Boolean)),
    );
    MOCK_DATA_UNIVERSE = cleaned;
    return delay({ status: "updated", symbols: [...cleaned] });
  },

  async reincludeSymbol(symbol: string): Promise<SymbolReincludeResult> {
    // Mirror the backend: SymbolRatingStore.reinclude() inserts a synthetic
    // GOOD event rather than deleting history, so the streak becomes 0 (not
    // "no history" / null). Mutates the SAME fixture state getSyncReport()
    // reads (MOCK_RATING_OVERRIDES) so a subsequent reload genuinely shows
    // the symbol no longer excluded -- this is the "make the mock actually
    // mutate its fixture" requirement, not just a canned success response.
    const sym = symbol.trim().toUpperCase();
    MOCK_RATING_OVERRIDES[sym] = { consecutive_bad_cycles: 0, excluded: false };
    return delay({ symbol: sym, reincluded: true });
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
      {
        name: "timeseries_momentum",
        score: 0.62,
        weight: 20,
        contribution: 12.4,
      },
      {
        name: "cross_sectional_momentum",
        score: 0.31,
        weight: 15,
        contribution: 4.65,
      },
      { name: "multifactor", score: -0.18, weight: 15, contribution: -2.7 },
      { name: "macd_momentum", score: 0.44, weight: 12, contribution: 5.28 },
      // honest null: this module didn't run for the symbol this cycle
      {
        name: "rsi2_mean_reversion",
        score: null,
        weight: 10,
        contribution: null,
      },
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
    const requested = symbols
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    const rng = seeded(
      requested.reduce((a, s) => a + s.length, requested.length * 13),
    );
    const names = [
      "timeseries_momentum",
      "cross_sectional_momentum",
      "multifactor",
      "macd_momentum",
      "rsi2_mean_reversion",
      // Honest empty row: a module that scored 0 of the requested symbols
      // this batch (e.g. news_catalyst with no news provider configured) —
      // never a fabricated 0, and never silently absent from the list.
      "news_catalyst",
    ];
    // Believable static settings.SIGNAL_WEIGHTS stand-ins -- not fetched
    // from the real config (the mock has no live Python process to import
    // from), but a plausible per-module weight so the tooltip's "Absolute
    // Config Weight" line has something real-looking to show.
    const CONFIG_WEIGHTS: Record<string, number> = {
      timeseries_momentum: 0.12,
      cross_sectional_momentum: 0.1,
      multifactor: 0.15,
      macd_momentum: 0.08,
      rsi2_mean_reversion: 0.06,
      news_catalyst: 0.05,
    };
    const rows: SignalImportanceRow[] = names.map((name) => {
      if (name === "news_catalyst") {
        return {
          name,
          mean_abs_contribution: null,
          n_symbols_scored: 0,
          normalized_contribution: null,
          config_weight: CONFIG_WEIGHTS[name] ?? null,
        };
      }
      return {
        name,
        mean_abs_contribution: +(rng() * 8).toFixed(2),
        n_symbols_scored: Math.max(1, requested.length - Math.floor(rng() * 2)),
        config_weight: CONFIG_WEIGHTS[name] ?? null,
      };
    });
    // normalized_contribution = each row's mean_abs_contribution divided by
    // the sum of all non-null mean_abs_contribution values, so the non-null
    // rows sum to ~1.0 -- computed AFTER the raw values above are fixed, not
    // interleaved with them, so the denominator is stable regardless of
    // rounding order.
    const totalContribution = rows.reduce(
      (sum, r) => sum + (r.mean_abs_contribution ?? 0),
      0,
    );
    for (const r of rows) {
      r.normalized_contribution =
        r.mean_abs_contribution == null || totalContribution <= 0
          ? null
          : +(r.mean_abs_contribution / totalContribution).toFixed(4);
    }
    rows.sort(
      (a, b) =>
        (b.mean_abs_contribution ?? -1) - (a.mean_abs_contribution ?? -1),
    );
    return delay<SignalImportance>({
      rows,
      n_symbols_requested: Math.min(requested.length, 25),
      n_symbols_scored:
        requested.length > 0 ? Math.max(1, requested.length - 1) : 0,
    });
  },

  async getSentimentDynamics(symbol: string): Promise<SentimentDynamics> {
    // Illustrative "available" example (this repo's USE_MOCK convention) —
    // the real endpoint can also return source: "unavailable" with all
    // three agent-derived fields null; see SentimentDynamics.test.tsx. Real
    // possible publishers only ("Reuters"/"Bloomberg"/"MarketWatch", or the
    // literal source string "fmp") — NEVER "SEC EDGAR" or any
    // EDGAR/Google-News-flavored publisher, since this data path
    // (signals/news_catalyst.py's FMP-only dispatcher)
    // structurally cannot return those.
    const sym = symbol.toUpperCase();
    return delay<SentimentDynamics>({
      ticker: sym,
      date: new Date().toISOString(),
      sentiment_score: 0.15,
      sentiment_intensity: 0.72,
      credibility_score: 0.85,
      volatility_persistence: 0.94,
      source: "antigravity_agent",
      headlines: [
        {
          title: `${sym} Guidance Beats Estimates as Demand Holds Up`,
          publisher: "Reuters",
          url: "https://example.com/news/1",
          published_at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
          score: 0.62,
          probabilities: { positive: 0.71, neutral: 0.22, negative: 0.07 },
        },
        {
          title: `Analysts Weigh In After ${sym}'s Latest Product Announcement`,
          publisher: "Bloomberg",
          url: "https://example.com/news/2",
          published_at: new Date(Date.now() - 18 * 3_600_000).toISOString(),
          score: 0.08,
          probabilities: { positive: 0.38, neutral: 0.47, negative: 0.15 },
        },
        {
          title: `Supply-Chain Concerns Weigh on ${sym} Shares`,
          publisher: "MarketWatch",
          url: null,
          published_at: new Date(Date.now() - 46 * 3_600_000).toISOString(),
          score: -0.34,
          probabilities: { positive: 0.11, neutral: 0.29, negative: 0.6 },
        },
      ],
      earnings_catalyst: {
        next_earnings_date: new Date(Date.now() + 3 * 86_400_000).toISOString(),
        hours_to_earnings: 72,
        status: "dampened",
        multiplier: 0.5,
      },
      provider_used: "fmp",
      source_breakdown: { Reuters: 1, Bloomberg: 1, MarketWatch: 1 },
      raw_sentiment_avg: 0.12,
      dampened_sentiment_score: 0.06,
      attention_score: 1.45,
      sector_heat_factor: 2.1,
    });
  },

  async getSentimentHistory(
    symbol: string,
    _lookbackDays = 180,
  ): Promise<SentimentHistory> {
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
      agent_loop: MOCK_AGENT_LOOP,
    });
  },

  async getAgenticDiscovery(): Promise<AgenticDiscovery> {
    const configs = readScanConfigs();
    // Always writable in the mock (matches mockStrategyMatrix's convention
    // above) so the demo can exercise the write flow with zero config.
    const writable = true;
    const note =
      "Scan configs are saved immediately and take effect on the agentic-discovery skill's next run.";
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
    const next =
      idx >= 0
        ? configs.map((c, i) => (i === idx ? row : c))
        : [...configs, row];
    writeScanConfigs(next);
    return delay(
      {
        scan_config: row,
        applies: "next_discovery_run",
        note: "Saved to output/scan_configs.json. Takes effect the next time the agentic-discovery skill runs a scan — it is not applied automatically.",
      },
      150,
    );
  },

  async watchCandidate(symbol: string): Promise<WatchResult> {
    const sym = (symbol ?? "").trim().toUpperCase();
    // Mirror the writer's strict validation → 422 invalid_symbol (thrown
    // synchronously, like getEquityFundamentals' bad-input branch above).
    if (!MOCK_SYMBOL_RE.test(sym)) {
      throw new ApiError(
        `invalid_symbol: '${symbol}' is not a valid ticker symbol.`,
        422,
      );
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
      150,
    );
  },

  // "Spot data download": any syntactically valid, non-delisted ticker gets
  // an honest `status: "ok"` with a fabricated-but-plausible row count --
  // this is a mock, there is no real HistoricalStore to persist into.
  // DELISTEDCO (also used by SCREENER_UNIVERSE's isActivelyTrading=false
  // fixture row) exercises the honest `status: "no_data"` branch so the mock
  // doesn't teach every caller that a backfill always succeeds.
  async triggerSymbolBackfill(symbol: string): Promise<SymbolBackfillResult> {
    const sym = (symbol ?? "").trim().toUpperCase();
    if (!MOCK_SYMBOL_RE.test(sym) || sym === "DELISTEDCO") {
      return delay({ symbol: sym, rows_persisted: 0, last_bar_date: null, status: "no_data" }, 150);
    }
    return delay(
      { symbol: sym, rows_persisted: 504, last_bar_date: "2026-08-21", status: "ok" },
      150,
    );
  },

  // ---- RLHF Calibration Review Queue ----
  async getRlhfSummary(limit = 50): Promise<RlhfSummary> {
    const cap = Math.max(1, Math.min(limit, 200));
    // Newest first -- matches rlhf_calibration_store.get_pending's real
    // `ORDER BY created_at DESC`, not fixture declaration order.
    const pending = MOCK_RLHF_PROPOSALS.filter((p) => p.status === "pending")
      .slice()
      .sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      )
      .slice(0, cap);
    const reviewed = MOCK_RLHF_PROPOSALS.filter((p) => p.status === "reviewed");
    const rated = reviewed.filter(
      (p): p is RlhfProposal & { human_rating: 1 | 2 | 3 | 4 | 5 } =>
        p.human_rating != null,
    );

    const distribution: Record<string, number> = {
      "1": 0,
      "2": 0,
      "3": 0,
      "4": 0,
      "5": 0,
    };
    for (const p of rated) {
      distribution[String(p.human_rating)] =
        (distribution[String(p.human_rating)] ?? 0) + 1;
    }

    const kpis: RlhfKpis = {
      pending_count: MOCK_RLHF_PROPOSALS.filter((p) => p.status === "pending")
        .length,
      reviewed_count: reviewed.length,
      average_human_rating: rated.length
        ? rated.reduce((sum, p) => sum + p.human_rating, 0) / rated.length
        : null,
      rating_distribution: distribution,
      auto_approved_count: MOCK_RLHF_PROPOSALS.filter((p) => p.auto_approved)
        .length,
      sft_exported_count: MOCK_RLHF_PROPOSALS.filter((p) => p.sft_exported)
        .length,
    };

    return delay({
      proposals: pending,
      kpis,
      writable: true,
      reason:
        pending.length === 0
          ? "No pending proposals -- the agent hasn't proposed a paper trade yet."
          : null,
    });
  },

  async submitRlhfReview(
    id: number,
    body: RlhfReviewSubmitRequest,
  ): Promise<RlhfReviewSubmitResult> {
    const proposal = MOCK_RLHF_PROPOSALS.find((p) => p.id === id);
    if (!proposal) {
      throw new ApiError(`not_found: no RLHF proposal with id ${id}.`, 404);
    }
    if (proposal.status === "reviewed") {
      throw new ApiError(
        `already_reviewed: proposal ${id} was already reviewed.`,
        409,
      );
    }
    if (body.human_rating < 1 || body.human_rating > 5) {
      throw new ApiError(
        "invalid_rating: human_rating must be between 1 and 5.",
        422,
      );
    }

    proposal.status = "reviewed";
    proposal.human_rating = body.human_rating;
    proposal.human_correction = body.human_correction?.trim() || null;
    proposal.reviewed_at = new Date().toISOString();
    // Export happens only via the explicit exportRlhfSft call below -- a
    // freshly reviewed proposal is never silently exported as a side effect
    // of the review itself.
    return delay({ ...proposal, sft_exported: proposal.sft_exported }, 150);
  },

  async exportRlhfSft(): Promise<RlhfSftExportResult> {
    const eligible = MOCK_RLHF_PROPOSALS.filter(
      (p) => p.status === "reviewed" && p.human_rating === 5 && !p.sft_exported,
    );
    for (const p of eligible) p.sft_exported = true;
    return delay(
      {
        exported_count: eligible.length,
        file: "output/rlhf_sft_export.jsonl",
        proposal_ids: eligible.map((p) => p.id),
      },
      200,
    );
  },

  async createJob(
    job_type: string,
    params?: Record<string, unknown>,
  ): Promise<JobRecord> {
    // job_type === "command" mirrors the backend's two HIGH_STAKES_COMMANDS
    // gates (see commandParse.ts) so the frontend can exercise the full
    // confirm/error flow offline, plus the app_shell.py hard-disallow.
    if (job_type === "command") {
      const command = typeof params?.command === "string" ? params.command : "";
      const args = Array.isArray(params?.args)
        ? (params.args as unknown[])
        : [];
      const confirmed = params?.confirm === true;

      if (command === "app_shell.py") {
        throw new ApiError("app_shell.py cannot be executed remotely.", 400);
      }
      if (
        command === "execution.kill_switch" &&
        (args.includes("--activate") || args.includes("--deactivate")) &&
        !confirmed
      ) {
        throw new ApiError(
          "confirmation required: this command activates/deactivates the global kill switch.",
          400,
        );
      }
      if (
        command === "main.py" &&
        args.includes("--refresh-account") &&
        !confirmed
      ) {
        throw new ApiError(
          "confirmation required: this command forces a fresh Robinhood login.",
          400,
        );
      }
    }

    const commandName =
      job_type === "command" && typeof params?.command === "string"
        ? params.command
        : null;
    const singleFlightKey =
      job_type === "train_lgbm" || job_type === "train_meta" ? "train" : null;

    for (const [id, rec] of Object.entries(_mockJobs)) {
      const elapsedMs = Date.now() - rec.startedAt;
      const isRunning = !rec.cancelled && elapsedMs < 30000;
      if (!isRunning) continue;

      if (job_type === "command") {
        if (rec.jobType === "command" && rec.commandName === commandName) {
          throw new JobConflictError(
            `Command '${commandName}' is already running (ID: ${id})`,
            id,
            rec.jobType,
            rec.commandName
          );
        }
      } else {
        const recKey =
          rec.jobType === "train_lgbm" || rec.jobType === "train_meta"
            ? "train"
            : null;
        if ((recKey || rec.jobType) === (singleFlightKey || job_type)) {
          throw new JobConflictError(
            `Job of type '${job_type}' conflicts with already-running job '${rec.jobType}' (ID: ${id})`,
            id,
            rec.jobType,
            rec.commandName
          );
        }
      }
    }

    const job_id = `mock-job-${Object.keys(_mockJobs).length + 1}`;
    const createdAt = new Date().toISOString();
    _mockJobs[job_id] = {
      jobType: job_type,
      commandName,
      startedAt: Date.now(),
      createdAt,
      cancelled: false,
    };
    return delay(
      {
        job_id,
        job_type: job_type as any,
        status: "running",
        cancellable: job_type !== "orchestrator",
        command_name: commandName,
        created_at: createdAt,
      },
      150,
    );
  },

  async listJobs(activeOnly?: boolean, limit?: number): Promise<JobsListResponse> {
    const jobs = Object.entries(_mockJobs).map(([job_id, job]) => {
      const cancellable = job.jobType !== "orchestrator";
      const status = job.cancelled
        ? "cancelled"
        : Date.now() - job.startedAt < 30000
          ? "running"
          : "success";
      return {
        job_id,
        job_type: job.jobType as any,
        status,
        exit_code: status === "running" ? null : status === "cancelled" ? -15 : 0,
        is_running: status === "running",
        cancellable,
        command_name: job.commandName,
        created_at: job.createdAt,
      } as JobRecord;
    });

    let filtered = jobs;
    if (activeOnly) {
      filtered = filtered.filter((j) => j.is_running);
    }
    
    // sort newest first
    filtered.sort((a, b) => new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime());
    
    if (limit) {
      filtered = filtered.slice(0, limit);
    }

    return delay({ jobs: filtered }, 200);
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
        : Date.now() - job.startedAt < 30000
          ? "running"
          : "success";
    return delay(
      {
        job_id,
        job_type: (job?.jobType ?? "preflight") as any,
        status,
        exit_code:
          status === "running" ? null : status === "cancelled" ? -15 : 0,
        is_running: status === "running",
        cancellable,
        command_name: job?.commandName ?? null,
        created_at: job?.createdAt ?? new Date().toISOString(),
      },
      100,
    );
  },

  async cancelJob(
    job_id: string,
  ): Promise<{ job_id: string; cancelled: boolean }> {
    const job = _mockJobs[job_id];
    if (!job) {
      return delay({ job_id, cancelled: false }, 100);
    }
    if (job.cancelled) {
      return delay({ job_id, cancelled: true }, 100);
    }
    const isRunning = Date.now() - job.startedAt < 2000;
    if (!isRunning) {
      return delay({ job_id, cancelled: false }, 100);
    }
    job.cancelled = true;
    return delay({ job_id, cancelled: true }, 100);
  },

  async restartDaemon(): Promise<RestartDaemonResult> {
    return delay(
      {
        restarting: true,
        message:
          "(mock) Process exiting in ~0.5s. No real process was restarted.",
      },
      150,
    );
  },

  // ---- G15: durable per-symbol Claude-vs-Gemini disagreement ----
  async getAiDisagreements(): Promise<AiDisagreementsResponse> {
    return delay(mockAiDisagreements());
  },

  async getAiModels(): Promise<AiModelsResponse> {
    return delay({
      default_provider: "gemini",
      default_model: "gemini-2.5-flash",
      providers: [
        {
          id: "gemini",
          name: "Google Gemini",
          available: true,
          default_model: "gemini-2.5-flash",
          models: [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-1.5-pro",
            "gemini-3.1-flash-live-preview",
          ],
        },
        {
          id: "anthropic",
          name: "Anthropic Claude",
          available: true,
          default_model: "claude-3-5-sonnet-20241022",
          models: [
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
          ],
        },
        {
          id: "openai",
          name: "OpenAI ChatGPT",
          available: true,
          default_model: "gpt-4o",
          models: ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
        },
        {
          id: "local",
          name: "Local / Open Source (Ollama, vLLM)",
          available: true,
          base_url: "http://localhost:11434/v1",
          default_model: "llama3.3",
          models: ["llama3.3", "deepseek-r1", "qwen2.5", "mistral"],
        },
      ],
    });
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
      200,
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
      const known =
        version === "baseline" || fx.cachedVersions.includes(version);
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
        id,
        version,
        found: true,
        body: fx.body,
        source: null,
        reason: null,
        cached_versions: fx.cachedVersions,
        has_baseline,
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
      const known =
        Boolean(fx) &&
        (req.version === "baseline" || fx.cachedVersions.includes(req.version));
      if (!known) {
        throw new ApiError(
          `Version '${req.version}' of '${id}' not found in the manifest, disk cache, or committed baseline.`,
          422,
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
      150,
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
      600,
    );
  },

  // ---- Market Data provider status (webapp parity gap G9) ----
  async getProviderStatus(): Promise<ProviderStatus> {
    return delay<ProviderStatus>({
      provider: "fmp",
      is_realtime: false,
      mode: "delayed",
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
        {
          subject: "Sahm Rule (Recession Signal)",
          value: 92,
          trend: "flat" as const,
        },
        {
          subject: "High-Yield OAS (Credit Stress)",
          value: 84,
          trend: "down" as const,
        },
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
  async getForecastBackfill() {
    return delay(mockForecastBackfill());
  },
  async runForecastBackfill(_params?: {
    tickers?: string[];
    start_date?: string;
    end_date?: string;
    use_fmp?: boolean;
    strategy_ids?: string[];
    theta_c?: number;
  }) {
    // Single-flight, mirrors the real backend's ml/forecast_backfill_job.py
    // guard: reject (409-equivalent) rather than silently starting a second
    // concurrent run -- guarded synchronously (before any job mutation), so
    // no timer advance is needed for the rejection itself to be observed.
    const existingJobId = _findRunningForecastBackfillJobId();
    if (existingJobId) {
      throw new ForecastBackfillConflictError(
        "A forecast backfill run is already in progress.",
        existingJobId,
      );
    }
    _mockForecastBackfillJobSeq++;
    const jobId = `fb-mock-${_mockForecastBackfillJobSeq}`;
    const job: _MockForecastBackfillJob = {
      mode: "run",
      startedAt: Date.now(),
      cancelled: false,
      simulateFailure: readForecastBackfillFailure(),
      simulateTimeout: readForecastBackfillTimeout(),
    };
    _mockForecastBackfillJobs[jobId] = job;
    return delay(_mockForecastBackfillJobStatus(jobId, job));
  },
  async getForecastBackfillJobStatus(jobId: string) {
    const job = _mockForecastBackfillJobs[jobId];
    if (!job) {
      throw new ApiError(`Job not found: ${jobId}`, 404);
    }
    return delay(_mockForecastBackfillJobStatus(jobId, job));
  },
  async cancelForecastBackfillJob(jobId: string) {
    const job = _mockForecastBackfillJobs[jobId];
    if (!job) {
      throw new ApiError(`Job not found: ${jobId}`, 404);
    }
    job.cancelled = true;
    return delay(_mockForecastBackfillJobStatus(jobId, job));
  },

  async getPaperBrokerAccount() {
    // Seed on first read too: tickets gate orders on this cash figure, so an
    // unseeded $0 account made the very first mock order impossible.
    ensureMockPaperAccountSeeded();
    return paperAccount;
  },
  async getPaperBrokerPositions() {
    return paperPositions;
  },
  async getPaperBrokerOrders(_limit?: number) {
    return paperOrders;
  },
  async getPaperBrokerClosedTrades(limit = 100, symbol?: string) {
    const rows = symbol ? paperClosedTrades.filter(t => t.symbol === symbol.toUpperCase()) : paperClosedTrades;
    return rows.slice(0, limit);
  },
  async getRetrospectiveTrade(tradeId: number): Promise<RetrospectiveTradeRecord> {
    const match = MOCK_RETROSPECTIVE_TRADES.find(t => t.trade_id === tradeId);
    if (!match) {
      throw new ApiError(`Paper trade ${tradeId} not found`, 404);
    }
    return match;
  },
  async getRetrospectiveInsights(params?: { limit?: number; symbol?: string; strategy_id?: string }): Promise<BatchRetrospectiveInsightsResponse> {
    let result = { ...MOCK_RETROSPECTIVE_INSIGHTS };
    if (params?.symbol) {
      const sym = params.symbol.toUpperCase();
      const filterCohort = (c: import("./types").CohortInsightMetrics) => {
        const hasSym = c.symbols.includes(sym);
        return {
          ...c,
          total_trades: hasSym ? c.total_trades : 0,
          trade_count: hasSym ? c.trade_count : 0,
          winning_trades: hasSym ? c.winning_trades : 0,
          losing_trades: hasSym ? c.losing_trades : 0,
          total_realized_pnl: hasSym ? c.total_realized_pnl : 0,
          symbols: hasSym ? [sym] : [],
        };
      };
      result = {
        ...result,
        automated_cohort: filterCohort(result.automated_cohort),
        manual_cohort: filterCohort(result.manual_cohort),
        unrecorded_cohort: filterCohort(result.unrecorded_cohort),
      };
    }
    return result;
  },
  async getBridgeReliability(): Promise<BridgeReliabilityResponse> {
    return MOCK_BRIDGE_RELIABILITY;
  },
  async resetPaperBroker(cash: number) {
    paperAccount = { equity: cash, cash: cash, buying_power: cash };
    paperAccountInitialized = true;
    paperPositions = [];
    paperOrders = [];
    paperClosedTrades = [];
    return { status: "reset", cash };
  },
  async getPaperBrokerSettings() {
    return buildTunablesResponse(
      PAPER_BROKER_TUNABLE_DEFS,
      "mock_paper_broker_tunables",
      "mock_paper_broker_drift",
    );
  },
  async updatePaperBrokerSettings(update: any, confirm?: any) {
    return applyTunablesGeneric(
      update,
      PAPER_BROKER_TUNABLE_DEFS,
      "mock_paper_broker_tunables",
      "mock_paper_broker_drift",
      confirm,
    );
  },

  // ---- Live Trade Approvals ----


  async getPendingLiveTrades() {
    return delay({
      proposals: mockLiveTradeProposals.filter(
        (p) => p.status === "pending_approval",
      ),
    });
  },
  async approveLiveTrade(token: string) {
    const proposal = mockLiveTradeProposals.find((p) => p.token === token);
    if (!proposal) {
      throw new ApiError("not_found", 404);
    }
    proposal.status = "approved";
    proposal.approved_at = new Date().toISOString();
    proposal.approved_by = "operator";
    return delay({ ...proposal });
  },
  async rejectLiveTrade(token: string) {
    const proposal = mockLiveTradeProposals.find((p) => p.token === token);
    if (!proposal) {
      throw new ApiError("not_found", 404);
    }
    proposal.status = "rejected";
    proposal.approved_at = new Date().toISOString();
    proposal.approved_by = "operator";
    return delay({ ...proposal });
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
  {
    name: "daily_report.html",
    kind: "daily_report",
    size: 48213,
    mtime: "2026-07-30T21:05:11+00:00",
  },
  {
    name: "daily_report_dashboard.html",
    kind: "dashboard",
    size: 1931842,
    mtime: "2026-07-30T06:02:47+00:00",
  },
  {
    name: "volatility_bands_dashboard.html",
    kind: "dashboard",
    size: 512340,
    mtime: "2026-07-30T06:02:51+00:00",
  },
  {
    name: "briefing_2026-07-30.md",
    kind: "briefing",
    size: 2104,
    mtime: "2026-07-30T12:00:03+00:00",
  },
  {
    name: "briefing_2026-07-29.md",
    kind: "briefing",
    size: 1987,
    mtime: "2026-07-29T12:00:04+00:00",
  },
  {
    name: "notebooklm_source.md",
    kind: "notebooklm_export",
    size: 3084,
    mtime: "2026-08-31T14:34:15+00:00",
  },
  {
    name: "trend_following_validation_summary.json",
    kind: "validation_summary",
    size: 918,
    mtime: "2026-07-28T18:22:10+00:00",
  },
  {
    name: "validation_trend-following_20260728183012.html",
    kind: "validation_html",
    size: 76004,
    mtime: "2026-07-28T18:30:12+00:00",
  },
  // Honesty branch: listed successfully (stat succeeded) but unreadable/
  // malformed at content-read time -- see MOCK_REPORT_CONTENT below.
  {
    name: "corrupt_validation_summary.json",
    kind: "validation_summary",
    size: 41,
    mtime: "2026-07-27T09:10:00+00:00",
  },
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
  "notebooklm_source.md": {
    name: "notebooklm_source.md",
    kind: "notebooklm_export",
    content_type: "markdown",
    text: "# Stockpy System Export\n**Generated At (UTC):** 2026-08-31T14:34:15+00:00\n\n## Macro Context\n- **VIX**: 15.5\n\n## Current Portfolio\n- **Total Equity**: $43,086.18\n",
    json: null,
    size: 3084,
    mtime: "2026-08-31T14:34:15+00:00",
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
    {
      symbol: "AAPL",
      advisory_action: "BUY",
      claude_verdict: "bullish",
      gemini_verdict: "bullish",
      disagreement: false,
    },
    {
      symbol: "NVDA",
      advisory_action: "STRONG BUY",
      claude_verdict: "bullish",
      gemini_verdict: "bearish",
      disagreement: true,
    },
    {
      symbol: "MSFT",
      advisory_action: "HOLD",
      claude_verdict: "neutral",
      gemini_verdict: null,
      disagreement: false,
    },
    {
      symbol: "DUK",
      advisory_action: "SELL",
      claude_verdict: null,
      gemini_verdict: null,
      disagreement: false,
    },
  ];
  const bothPresent = rows.filter(
    (r) => r.claude_verdict !== null && r.gemini_verdict !== null,
  ).length;
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
    summary: {
      total_symbols: 0,
      both_present: 0,
      agreements: 0,
      disagreements: 0,
    },
    reason:
      "No state snapshot yet — run the pipeline to populate the signal universe.",
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
  // Model keys mirror ml/forecast_backfill.py's real, registry-driven naming
  // ("{signals.registry.global_registry strategy name}_{horizon}d") rather
  // than the pre-refactor hardcoded "TSMOM"/"CSMOM" scheme -- keeping this
  // mock in sync with what the live backend actually returns.
  return {
    status: "completed",
    timestamp: new Date().toISOString(),
    horizons: [10, 30, 60, 90],
    metrics: {
      // Live meta-labeler bridge (ml/forecast_backfill_registry_bridge.py)
      // example rows -- one registered (gate cleared), one blocked (a
      // realistic DSR/PBO-deployability-gate rejection, the reason a
      // feature-compatible model still fails to register -- the feature-
      // compatibility gate itself now passes for all 6 eligible signals,
      // see docs/plans/FORECAST_BACKFILL_PLAN.md). Exercising both states
      // here is the only way mock-mode development (`npm run dev`, no
      // VITE_USE_MOCK=false) can render the "Live Registry"/"CPCV DSR"/
      // "PBO" columns and the blocked-with-reason styling at all.
      timeseries_momentum_10d: {
        accuracy: 0.5215,
        auc: 0.542,
        n_train: 9480,
        n_test: 0,
        split_date: "CPCV",
        is_active: true,
        cpcv_dsr: 0.968,
        pbo: 0.31,
        mean_oos_sharpe: 0.74,
        registry_key: "meta_labeler_backfill_timeseries_momentum",
        registered: true,
        skip_reason: null,
      },
      timeseries_momentum_30d: {
        accuracy: 0.534,
        auc: 0.558,
        n_train: 9416,
        n_test: 0,
        split_date: "CPCV",
        is_active: true,
      },
      timeseries_momentum_60d: {
        accuracy: 0.548,
        auc: 0.572,
        n_train: 9320,
        n_test: 0,
        split_date: "CPCV",
        is_active: true,
      },
      timeseries_momentum_90d: {
        accuracy: 0.562,
        auc: 0.591,
        n_train: 9224,
        n_test: 0,
        split_date: "CPCV",
        is_active: true,
      },
      rsi2_mean_reversion_10d: {
        accuracy: 0.518,
        auc: 0.531,
        n_train: 6820,
        n_test: 0,
        split_date: "CPCV",
        is_active: true,
      },
      rsi2_mean_reversion_30d: {
        accuracy: 0.541,
        auc: 0.564,
        n_train: 6754,
        n_test: 0,
        split_date: "CPCV",
        is_active: true,
      },
      rsi2_mean_reversion_60d: {
        accuracy: 0.559,
        auc: 0.583,
        n_train: 6658,
        n_test: 0,
        split_date: "CPCV",
        is_active: true,
      },
      rsi2_mean_reversion_90d: {
        accuracy: 0.574,
        auc: 0.605,
        n_train: 6562,
        n_test: 0,
        split_date: "CPCV",
        is_active: true,
      },
      // Illustrative "Diagnostic" (is_active: false) row -- `is_active` is
      // derived from BACKFILL_ELIGIBLE_SIGNAL_IDS membership (the 6 real
      // Forecast-Backfill-eligible signals), not a fixed 3-name list. This
      // model_type isn't one of them (macd_momentum declares no
      // meta_label_features at all -- it could never really reach this
      // engine's training path), which is exactly why it's the honest
      // "Diagnostic" example: a model_key this mock renders to exercise the
      // badge and bottom-of-table sort, distinct from what a real backend
      // could ever actually train.
      macd_momentum_10d: {
        accuracy: 0.504,
        auc: 0.508,
        n_train: 5210,
        n_test: 0,
        split_date: "CPCV",
        is_active: false,
      },
    },
    // Per-signal eligibility -- WP2's honest "why didn't this signal train"
    // reporting. timeseries_momentum/rsi2_mean_reversion mirror their
    // trained metrics rows above (a real backend never sets trained: true
    // without a corresponding metrics row); sector_quality_rank/
    // cross_sectional_momentum illustrate the genuinely-blocked case this
    // mock doesn't carry metrics rows for -- this fixture is illustrative,
    // not an exhaustive mirror of every eligible signal's live state.
    // (vrp_premium_selling / options_flow_sentiment are no longer eligible:
    // retired from live scoring in 2026-09, step 3d'.)
    eligibility: {
      timeseries_momentum: { declares_meta_label_features: true, trained: true, reason: null },
      rsi2_mean_reversion: { declares_meta_label_features: true, trained: true, reason: null },
      sector_quality_rank: {
        declares_meta_label_features: true,
        trained: false,
        reason: "insufficient_samples:0_for_90d",
      },
      cross_sectional_momentum: {
        declares_meta_label_features: true,
        trained: false,
        reason: "insufficient_samples:0_for_90d",
      },
    },
    tickers: ["AAPL", "MSFT", "AMZN", "NVDA", "JPM", "JNJ", "XOM", "WMT"],
    total_rows: 11080,
    csv_path: "output/agentic_forecast_backfill.csv",
  };
}

export const MOCK_META = {
  mode: MOCK_MODE,
  sectors: SECTORS,
};

// Mirrors the real api/pilots_api.py::_PAPER_BROKER_GROUPS exactly (field
// names, types, and defaults) -- this fixture previously invented fields
// (PAPER_BROKER_ENABLED, PAPER_BROKER_INITIAL_CASH, PAPER_BROKER_SLIPPAGE_BPS)
// that don't exist in settings.py at all, and gave BROKER_BACKEND a fake
// "PAPER"/"ROBINHOOD" enum with a "ROBINHOOD" option that doesn't
// actually work (BROKER_BACKEND recognizes only "fmp_paper" today; real money moves only through the Robinhood execution queue). A mock-mode operator exercising this screen was
// seeing a fictional, unsafe-looking control surface that bore no relation
// to what a real write would do.
const PAPER_BROKER_TUNABLE_DEFS: MockTunableDef[] = [
  {
    group: "Paper Broker Configuration",
    key: "BROKER_BACKEND",
    type: "string",
    value: "fmp_paper",
    default: "fmp_paper",
    description:
      "The automated pipeline's broker is the local FMP paper ledger ('fmp_paper', SQLite-backed). Alpaca was removed; real money moves only through the Robinhood execution queue.",
  },
  {
    group: "Paper Broker Configuration",
    key: "FMP_PAPER_STARTING_CASH",
    type: "number",
    value: 100000.0,
    default: 100000.0,
    min: 0,
    max: 10000000,
    step: 1000,
    description:
      "Starting cash balance seeded into a fresh paper trading account the first time it's constructed. Only takes effect when BROKER_BACKEND='fmp_paper'.",
  },
  {
    group: "Paper Broker Configuration",
    key: "PAPER_BROKER_WRITES_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates POST /pilots/paper-broker/reset. If false, resets are blocked.",
  },
];

let paperAccount: PaperBrokerAccount = { equity: 0, cash: 0, buying_power: 0 };
// Distinguishes "never seeded" from "legitimately drained to zero by
// trading" -- the account's cash/equity values alone can't tell those apart,
// since both are genuinely 0 in the drained case.
let paperAccountInitialized = false;

function ensureMockPaperAccountSeeded(): void {
  if (!paperAccountInitialized) {
    paperAccount = { equity: 100000, cash: 100000, buying_power: 100000 };
    paperAccountInitialized = true;
  }
}
let paperPositions: PaperBrokerPosition[] = [];
let paperOrders: PaperBrokerOrder[] = [];
let paperClosedTrades: PaperBrokerClosedTrade[] = MOCK_RETROSPECTIVE_TRADES.map(t => ({
  trade_id: t.trade_id,
  strategy_id: t.strategy_id,
  pilot_id: t.pilot_id,
  experiment_arm: t.experiment_arm ?? null,
  symbol: t.symbol,
  side: (t.side === "SELL" ? "SELL" : "BUY"),
  qty: t.qty,
  entry_ts: t.entry_ts,
  entry_price: t.entry_price,
  exit_ts: t.exit_ts ?? new Date().toISOString(),
  exit_price: t.exit_price,
  commission: t.commission,
  realized_pnl: t.realized_pnl,
  realized_pnl_pct: t.realized_pnl_pct,
  holding_period_days: t.holding_period_days,
  close_reason: t.close_reason,
  leg_group_id: null,
}));
let paperClosedTradeIdSeq = 105;

/**
 * Records a synthetic realized-PnL row when a mock position fully closes,
 * mirroring the real backend's paper_closed_trades write path
 * (data/paper_account_store.py::_record_closed_trade). `side` is the
 * POSITION's own opening side ("BUY" for a long, "SELL" for a short) --
 * not the closing fill's side -- matching get_full_closed_trades' contract.
 * `qty` is the closed quantity (always positive).
 */
function pushMockClosedTrade(params: {
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  entryPrice: number;
  exitPrice: number;
  commission?: number;
  closeReason?: string;
}) {
  const { symbol, side, qty, entryPrice, exitPrice, commission = 0, closeReason = "flatten" } = params;
  const grossPnl = side === "BUY" ? (exitPrice - entryPrice) * qty : (entryPrice - exitPrice) * qty;
  const realizedPnlPct =
    entryPrice > 1e-12
      ? side === "BUY"
        ? exitPrice / entryPrice - 1
        : entryPrice / exitPrice - 1
      : null; // CONSTRAINT #4: never fabricate 0 on a degenerate entry price
  const now = new Date().toISOString();
  paperClosedTradeIdSeq += 1;
  paperClosedTrades.unshift({
    trade_id: paperClosedTradeIdSeq,
    strategy_id: null,
    pilot_id: null,
    experiment_arm: null,
    symbol,
    side,
    qty,
    entry_ts: now,
    entry_price: entryPrice,
    exit_ts: now,
    exit_price: exitPrice,
    commission,
    realized_pnl: grossPnl - commission,
    realized_pnl_pct: realizedPnlPct,
    holding_period_days: 0,
    close_reason: closeReason,
    leg_group_id: null,
  });
}

/**
 * Deterministic per-symbol mock spot price -- same seed formula as
 * `getDataQuotes` below, so a market-order fill (no `limit_price` supplied)
 * uses a price consistent with what the rest of the mock surface would quote
 * for that symbol, instead of a flat fallback unrelated to the symbol.
 */
function mockStockQuotePrice(symbol: string): number {
  const sym = symbol.trim().toUpperCase();
  const rng = seeded(sym.charCodeAt(0) * 31 + sym.length * 7);
  const base = 40 + (sym.charCodeAt(sym.length - 1) % 40) * 5;
  return +(base + rng() * 20).toFixed(2);
}

/**
 * Shared equity paper-fill mutation, used by BOTH `postOptionsOrder`'s
 * `asset_type === "stock"` branch (the options desk's Quick Trade path,
 * pre-dating the equity/options split) and `postPaperEquityOrder` (the new,
 * options-free equity ticket) -- factored out so the two mock paths can't
 * drift apart. Mirrors `pilots/paper_equity_order.py::execute_equity_order`'s
 * fill math exactly: an explicit positive `limitPrice` prices the fill,
 * otherwise a live(-looking) quote; a missing/non-positive resolved price or
 * a SELL with insufficient inventory rejects the order rather than fabricating
 * a fill (CONSTRAINT #4); commission is $0.005/share, $1.00 minimum.
 */
function applyMockStockFill(params: {
  symbol: string;
  side: "BUY" | "SELL";
  quantity?: number;
  dollarAmount?: number;
  limitPrice?: number;
  orderIdPrefix: string;
}): { ok: boolean; order_id: string | null; message: string } {
  ensureMockPaperAccountSeeded();

  const orderSymbol = params.symbol.trim().toUpperCase();
  const fillPrice =
    params.limitPrice && params.limitPrice > 0
      ? params.limitPrice
      : mockStockQuotePrice(orderSymbol);

  if (!fillPrice || fillPrice <= 0) {
    return {
      ok: false,
      order_id: null,
      message: `No live quote available for ${orderSymbol}; order rejected rather than filled at a fabricated price.`,
    };
  }

  let qty: number;
  if (params.dollarAmount && params.dollarAmount > 0 && (!params.quantity || params.quantity <= 0)) {
    qty = +(params.dollarAmount / fillPrice).toFixed(4);
  } else {
    qty = params.quantity && params.quantity > 0 ? params.quantity : 1;
  }

  if (qty <= 0) {
    return { ok: false, order_id: null, message: "Calculated share quantity must be greater than zero." };
  }

  const orderSide = params.side;
  const existingPos = paperPositions.find((p) => p.symbol === orderSymbol);

  if (orderSide === "SELL" && (!existingPos || existingPos.qty < qty)) {
    return {
      ok: false,
      order_id: null,
      message: `Order rejected: Insufficient funds or inventory for SELL ${qty} ${orderSymbol}.`,
    };
  }

  const commission = Math.max(1.0, +(qty * 0.005).toFixed(2));
  const totalCost = orderSide === "SELL" ? qty * fillPrice - commission : qty * fillPrice + commission;

  if (orderSide === "BUY" && paperAccount.cash < totalCost) {
    return {
      ok: false,
      order_id: null,
      message: `Insufficient paper funds. Required: $${totalCost.toFixed(2)}, Available: $${paperAccount.cash.toFixed(2)}`,
    };
  }

  if (orderSide === "SELL") {
    const closingAvgCost = existingPos!.avg_cost;
    paperAccount.cash += totalCost;
    existingPos!.qty -= qty;
    existingPos!.market_value = Math.max(0, (existingPos!.market_value || 0) - qty * fillPrice);
    if (existingPos!.qty <= 0) {
      paperPositions = paperPositions.filter((p) => p.symbol !== orderSymbol);
      // Equity positions here are always long (no short-sale path), so the
      // position's own opening side is "BUY".
      pushMockClosedTrade({
        symbol: orderSymbol,
        side: "BUY",
        qty,
        entryPrice: closingAvgCost,
        exitPrice: fillPrice,
        commission,
      });
    }
  } else {
    paperAccount.cash -= totalCost;
    if (existingPos) {
      const prevTotal = existingPos.qty * existingPos.avg_cost;
      existingPos.qty += qty;
      existingPos.avg_cost = (prevTotal + qty * fillPrice) / existingPos.qty;
      existingPos.market_value = (existingPos.market_value || 0) + qty * fillPrice;
    } else {
      paperPositions.push({
        symbol: orderSymbol,
        qty,
        avg_cost: fillPrice,
        current_price: fillPrice,
        market_value: qty * fillPrice,
        unrealized_pl: 0,
        unrealized_pl_pct: 0,
        strategy_id: null,
        pilot_id: null,
        experiment_arm: null,
      });
    }
  }
  paperAccount.buying_power = paperAccount.cash;

  const orderId = `${params.orderIdPrefix}_${Date.now()}`;
  paperOrders.unshift({
    order_id: orderId,
    symbol: orderSymbol,
    side: orderSide,
    qty,
    price: fillPrice,
    status: "filled",
    filled_qty: qty,
    filled_avg_price: fillPrice,
    created_at: new Date().toISOString(),
    strategy_id: null,
    pilot_id: null,
    experiment_arm: null,
  });

  return {
    ok: true,
    order_id: orderId,
    message: `Paper stock order filled: ${orderSide} ${qty.toFixed(2)} shares of ${orderSymbol} at $${fillPrice.toFixed(2)} (Total: $${totalCost.toFixed(2)}).`,
  };
}

/**
 * Live-trade proposals awaiting human approve/reject -- the ONE place an
 * operator can act on a real order an MCP tool proposed. Module-level and
 * mutable (matches `paperAccount`/`paperPositions`/`paperOrders` above) so
 * approve/reject mutations in mock mode persist across a reload of the
 * pending list within the same session.
 *
 * Deliberately seeded with a mix of statuses (not just `pending_approval`)
 * so a mock-mode operator can see what a decided/expired/failed proposal
 * honestly looks like -- `getPendingLiveTrades` still only ever surfaces
 * the `pending_approval` rows, matching the real backend's `GET
 * /pilots/execution/pending` contract.
 */
let mockLiveTradeProposals: LiveTradeProposal[] = [
  {
    token: "ltp_8f2a1c",
    symbol: "AAPL",
    side: "BUY",
    qty: 25,
    order_type: "limit",
    limit_price: 228.5,
    strategy_id: "momentum_12_1",
    proposed_at: new Date(Date.now() - 4 * 60_000).toISOString(),
    expires_at: new Date(Date.now() + 26 * 60_000).toISOString(),
    status: "pending_approval",
    approved_at: null,
    approved_by: null,
    broker_order_id: null,
    error_message: null,
  },
  {
    token: "ltp_1d9e77",
    symbol: "MSFT",
    side: "SELL",
    qty: 10,
    order_type: "market",
    limit_price: null,
    strategy_id: "multifactor_lowvol_size",
    proposed_at: new Date(Date.now() - 12 * 60_000).toISOString(),
    expires_at: new Date(Date.now() + 18 * 60_000).toISOString(),
    status: "pending_approval",
    approved_at: null,
    approved_by: null,
    broker_order_id: null,
    error_message: null,
  },
  {
    token: "ltp_5b3f02",
    symbol: "NVDA",
    side: "BUY",
    qty: 5,
    order_type: "limit",
    limit_price: 132.75,
    strategy_id: "cross_sectional_momentum",
    proposed_at: new Date(Date.now() - 3600_000).toISOString(),
    expires_at: new Date(Date.now() - 3300_000).toISOString(),
    status: "expired",
    approved_at: null,
    approved_by: null,
    broker_order_id: null,
    error_message: null,
  },
  {
    token: "ltp_a04c19",
    symbol: "JNJ",
    side: "BUY",
    qty: 15,
    order_type: "limit",
    limit_price: 158.2,
    strategy_id: "garch_vol_target",
    proposed_at: new Date(Date.now() - 7200_000).toISOString(),
    expires_at: new Date(Date.now() - 6600_000).toISOString(),
    status: "executed",
    approved_at: new Date(Date.now() - 7000_000).toISOString(),
    approved_by: "operator",
    broker_order_id: "brk_ord_66211a",
    error_message: null,
  },
  {
    token: "ltp_c78d40",
    symbol: "TSLA",
    side: "SELL",
    qty: 8,
    order_type: "market",
    limit_price: null,
    strategy_id: "rsi2_mean_reversion",
    proposed_at: new Date(Date.now() - 9000_000).toISOString(),
    expires_at: new Date(Date.now() - 8400_000).toISOString(),
    status: "rejected",
    approved_at: new Date(Date.now() - 8900_000).toISOString(),
    approved_by: "operator",
    broker_order_id: null,
    error_message: null,
  },
];

/**
 * Exposed for tests (and any mock-mode operator who wants to see the
 * genuinely-quiet-queue empty state): replace the mock live-trade proposal
 * fixture wholesale. Call with `[]` to reach the honest "no pending
 * proposals" branch `LiveTradeApprovals.tsx` must render, matching the
 * `__resetMockDataUniverse`/`__resetMockRatingOverrides` convention above.
 */
export function __setMockLiveTradeProposals(proposals: LiveTradeProposal[]) {
  mockLiveTradeProposals = proposals;
}

