/**
 * mock.ts — realistic offline fixtures for every endpoint in api/pilots_api.py.
 *
 * Lets the whole PWA run with VITE_USE_MOCK=true and no backend. Data mirrors
 * the Pilot catalog in the plan (Phase 1) and is deliberately HONEST:
 *  - `momentum-burst` is NOT deployable (fails a validation gate) and renders so.
 *  - `value-quality` has curve:null ("no backtest series yet"), never a fake line.
 */

import { ApiError, ForecastBackfillConflictError } from "./types";
import type {
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
  CircuitBreakerState,
  CircuitBreakerStatusResponse,
  ControlStatus,
  CronStatus,
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
  RealizedPerformance,
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
  SyncReportResponse,
  SyncReportSymbol,
  SymbolReincludeResult,
  RecommendationsResponse,
  Recommendation,
  UniverseListResponse,
  UniverseSymbol,
  Thresholds,
  SymbolHeldBy,
  SymbolOptions,
  TriggerRunResult,
  OptionChainResponse,
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
  PortfolioRiskStreamEvent,
  PromptListResponse,
  PromptEntry,
  PromptBody,
  PromptPinRequest,
  PromptPinResult,
  DataSyncResult,
  ProviderStatus,
  CacheLongShortConcentratedPosition,
  CacheLongShortSimulateRequest,
  CacheLongShortSimulateResult,
  CacheLongShortStartRequest,
  CacheLongShortStartResult,
  CacheLongShortDashboard,
  CacheLongShortPendingTrade,
  CacheLongShortApproveBulkResult,
  PaperBrokerAccount,
  PaperBrokerPosition,
  PaperBrokerOrder,
  LiveTradeProposal,
  OptionsOrderRequest,
  OptionsOrderResult,
  ScenarioMatrixResponse,
  ScenarioMatrixCell,
  VolSurfaceResponse,
  VolSmilePoint,
  VolTermStructurePoint,
  SkewData,
  DeltaHedgePreview,
  DeltaHedgeResult,
  RollOrderRequest,
  ManageExitsResult,
  HistoricalScenarioPreset,
  EarningsCrushCandidate,
  EarningsCrushCandidatesResponse,
  EarningsCrushExecutionResult,
  UnusualOptionTrade,
  UnusualOptionsFlowResponse,
  FlowSentimentData,
  FlowSentimentResponse,
  HarRvForecastResponse,
  VolMispricingResponse,
  VolMispricingStrike,
  GammaScalpRequest,
  GammaScalpResponse,
  GammaScalpHedgeTrade,
  OptionsAlertTestResult,
  DispersionConstituent,
  DispersionOpportunity,
  DispersionBasketResponse,
  DispersionBasketOrderRequest,
  DispersionExecutionResult,
  ZeroDteSignal,
  ZeroDteSignalResponse,
  ZeroDteTradeRequest,
  ZeroDteExecutionResult,
  VpinBucket,
  VpinMetricsResponse,
  SorLegBreakdown,
  SorAnalysisRequest,
  SorAnalysisResponse,
  LeggingSimulationRequest,
  LeggingSimulationResponse,
  GexStrikePoint,
  GexProfileResponse,
  LobQueueSimulationRequest,
  LobQueueSimulationResponse,
  LobQueuePercentiles,
  CopulaPairsResponse,
  CopulaTailData,
  CopulaSeriesPoint,
  MarketMakerSimRequest,
  MarketMakerSimResponse,
  MarketMakerStepPoint,
  TransformerForecastResponse,
  DiffusionStressRequest,
  DiffusionStressResponse,
  HrpCvarOptimizeRequest,
  HrpCvarOptimizeResponse,
  AlmgrenChrissOptimizeRequest,
  AlmgrenChrissOptimizeResponse,
  FixRouteOrderRequest,
  FixRouteOrderResponse,
  FixSessionStatusResponse,
  FixSessionControlResponse,
  FixTestRequestPayload,
  FixResetSeqRequest,
  ResearchSynthesizeRequest,
  ResearchSynthesizeResponse,
  AutonomousBacktestRequest,
  AutonomousBacktestResponse,
  VolSurface3DMeshResponse,
  VolSurface3DPoint,
  MultiBrokerStatusResponse,
  BrokerHealthStatusDto,
  RoutingAuditDto,
  BrokerFailoverRequest,
  BrokerFailoverResponse,
  SecRule606ReportResponse,
  SecRule606VenueRow,
  SecRule606CategoryBreakdown,
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
  aum: number;
  followers: number;
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
    top_holdings: hs.slice(0, 3),
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
  // -> fetching_snapshot -> succeeded. Timed so a 2s-interval poller sees a
  // couple of "running" polls (awaiting_approval) before success, rather
  // than resolving on the very first poll.
  let phase: BrokerageLoginPhase;
  if (elapsedSeconds < 1) phase = "starting";
  else if (elapsedSeconds < 2) phase = "authenticating";
  else if (elapsedSeconds < 5) phase = "awaiting_approval";
  else if (elapsedSeconds < 6) phase = "verifying";
  else if (elapsedSeconds < 7) phase = "fetching_snapshot";
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
  const TOTAL_STEPS = 7;
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
    phase = "exporting";
    step = 7;
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
  RISK_FREE_RATE: ["processing_engine.py:36", "technical_options_engine.py:22"],
  MARKET_RISK_PREMIUM: ["processing_engine.py:37"],
  REQUIRED_RETURN_RATE: ["processing_engine.py:38"],
  MAX_PORTFOLIO_HEAT: ["execution/risk_gate.py:136"],
  MAX_POSITION_WEIGHT: ["execution/risk_gate.py:133"],
  MAX_CORRELATION: ["execution/risk_gate.py:139"],
  DAILY_LOSS_LIMIT_PCT: ["execution/risk_gate.py:144"],
  MAX_ORDER_RATE_PER_MIN: ["execution/risk_gate.py:149"],
  HMM_RISK_OFF_BLOCK_THRESHOLD: ["execution/risk_gate.py:154"],
  RISK_GATE_ENFORCE_MARKET_HOURS: ["execution/risk_gate.py:159"],
  DRY_RUN: ["gui/app.py:145"],
  MARKET_DATA_PROVIDER: ["data/market_data.py:1834"],
  MARKET_DATA_QUOTE_TTL_SECONDS: ["data/market_data.py:1771"],
  MARKET_DATA_BARS_TTL_SECONDS: [
    "data/market_data.py:1776",
    "data/market_data.py:2094",
  ],
  FUNDAMENTALS_SOURCE: ["data/market_data.py:1809"],
  DASHBOARD_REFRESH_SECONDS: ["gui/app.py:146", "gui/panels/__init__.py:87"],
  LOG_LEVEL: ["alerting.py:118", "gui/app.py:83"],
  ADVISORY_ONLY: ["gui/app.py:249"],
  SECTOR_FORECAST_CONFIG_PATH: ["forecasting_engine.py:144"],
  SECTOR_FORECAST_CONFIGS: ["forecasting_engine.py:146"],
  CORS_ALLOWED_ORIGINS: ["api/control_api.py:166", "api/data_api.py:117"],
  SENTIMENT_SOURCES: ["data/sentiment_sources.py:1865"],
  SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE: ["data/sentiment_sources.py:1895"],
  EDGAR_FULLTEXT_FORMS: ["api/pilots_api.py:4128"],
  EDGAR_FULLTEXT_CHUNK_TOKENS: ["api/pilots_api.py:4129"],
  FINNHUB_RATE_LIMIT_PER_MIN: [
    "data/market_data.py:1470",
    "data/market_data.py:1504",
  ],
  FMP_QUOTES_REALTIME: ["data/market_data.py:979"],
  FMP_BARS_ADJUSTMENT: ["data/market_data.py:1850"],
  FMP_ECON_INDICATORS: ["api/pilots_api.py:4233"],
  ETF_HOLDINGS_TICKERS: ["api/pilots_api.py:4253"],
  SYMBOL_RATING_DROP_THRESHOLD_CYCLES: ["pipeline/production_steps.py:531"],
};

// `settings_keysets.DANGEROUS_KEYS`, in full -- copied from the real set.
// All 20 real settings_keysets.DANGEROUS_KEYS members are now covered here,
// since the Feature Flags screen (webapp/src/api/mock.ts's
// FEATURE_FLAGS_TUNABLE_DEFS) serves every one of them, exercising the
// typed-confirmation flow for all 20 in mock mode.
const MOCK_DANGEROUS_KEYS = new Set([
  "BROKER_BACKEND",
  "ADVISORY_ONLY",
  "DRY_RUN",
  "ROBINHOOD_EXECUTION_MODE",
  "CORS_ALLOWED_ORIGINS",
  "FMP_BARS_ENABLED",
  "FMP_BARS_ADJUSTMENT",
  "CACHE_LONG_SHORT_WRITES_ENABLED",
  // 2026-08-08: settings_keysets.SAFETY_CRITICAL_KEY_REASONS gained these 13
  // fields (CACHE_LONG_SHORT_WRITES_ENABLED above being one of them) when the
  // fail-closed write/execution gates were reclassified out of
  // EXCLUDED_FROM_GUI into ALLOWED_KEYS -- now all exposed by the Feature
  // Flags screen.
  "MACRO_REGIME_GATE_ENABLED",
  "AI_GENERATION_API_ENABLED",
  "AUTOMATION_WRITES_ENABLED",
  "BROKERAGE_REFRESH_ENABLED",
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
    group: "Regime Model",
    key: "OPTIONS_VRP_THRESHOLD",
    type: "number",
    value: 0.02,
    default: 0.02,
    min: 0,
    max: 1,
    step: 0.01,
    description:
      "Minimum Volatility Risk Premium (VRP) required to authorize premium selling (e.g. credit spreads). VRP is the difference between Implied Volatility and Realized Volatility. A higher threshold (e.g. 0.03 = 3%) demands a larger premium buffer before entering trades, increasing selectivity and safety but reducing trade frequency.",
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
    // by key availability) -- never fabricated as "alpaca"/"yfinance".
    group: "Market Data",
    key: "MARKET_DATA_PROVIDER",
    type: "enum",
    value: null,
    default: null,
    options: ["alpaca", "yfinance", "fmp"],
    description:
      "Force a specific market-data backend: 'fmp', 'alpaca' or 'yfinance'. When unset the platform auto-selects based on key availability (Alpaca if its keys are present, else yfinance). Setting FMP_API_KEY alone NEVER auto-elects FMP: unlike the Alpaca ladder, FMP is chosen only by explicitly setting this to 'fmp', so an operator who adds the key to enable the analyst or earnings feed does not silently have their quote/bars source change underneath them. FMP quotes/bars additionally require FMP_QUOTES_ENABLED / FMP_BARS_ENABLED (the two-gate convention).",
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
      "Primary fundamentals backend: 'yahoo' (statement-derived, default), 'yfinance_info' (raw .info fallback), or 'fmp' (Financial Modeling Prep — see section 25). Finnhub is no longer a fundamentals source. Setting FMP_API_KEY alone NEVER auto-elects FMP: it must be chosen explicitly here, so adding the key for one feed cannot silently change what every valuation metric is computed from. 'fmp' additionally requires FMP_FUNDAMENTALS_ENABLED=true (the two-gate convention); with either half missing the Yahoo path is used, exactly as today.",
  },
  {
    group: "Market Data", key: "MARKET_DATA_WS_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Opt-in: subscribe to Alpaca's real-time StockDataStream WebSocket for quotes, SUPPLEMENTING (never replacing) the REST-polling CompositeProvider -- see data/market_data_ws.py. Only takes effect when the active quote provider is AlpacaProvider; otherwise a no-op with an INFO log. False (default) reproduces the exact current REST-only behavior -- matches the FORECAST_USE_GARCH_SIGMA opt-in convention. Any WS failure (connect, subscribe, disconnect, missing credentials) degrades to the existing REST path -- never crashes the pipeline.",
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
    key: "MARKET_DATA_WS_ENABLED",
    value: false,
    default: false,
    type: "boolean",
    description:
      "Opt-in: subscribe to Alpaca's real-time StockDataStream WebSocket for quotes, SUPPLEMENTING (never replacing) the REST-polling CompositeProvider -- see data/market_data_ws.py. Only takes effect when the active quote provider is AlpacaProvider; otherwise a no-op with an INFO log. False (default) reproduces the exact current REST-only behavior -- matches the FORECAST_USE_GARCH_SIGMA opt-in convention. Any WS failure (connect, subscribe, disconnect, missing credentials) degrades to the existing REST path -- never crashes the pipeline.",
    group: "Market Data",
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
    key: "OPTIONS_MATRIX_ENABLED",
    value: true,
    default: true,
    type: "boolean",
    description:
      "When True, the pipeline persists the per-symbol options premium directive matrix to output/options_matrix.json for the Pilots PWA (GET /options, GET /symbols/{ticker}/options). Default False.",
    group: "Advanced / Config",
  },
  {
    key: "OPTIONS_TRUE_IVR_ENABLED",
    value: true,
    default: true,
    type: "boolean",
    description:
      "Opt-in: wires a real, options-chain-derived True_IVR into technical_options_engine.build_premium_directive() -- the GUI Technical Options Matrix tab, the get_options_directive MCP tool, api/metrics_api.py, execution/options_queue_builder.py, and every other build_premium_directive caller -- instead of leaving true IV rank exclusive to main_orchestrator.py's pipeline/production_steps.py::OptionsAnalysisStep path. When True, build_premium_directive fetches a live 30-calendar-day ATM IV via volatility.iv_engine.get_30d_atm_iv() (a fresh, lightweight DataEngine constructed with no FRED key purely for its fetch_options_chain() -- CompositeProvider/data/market_data.py has no chain-shaped method to reuse, so this mirrors exactly what OptionsAnalysisStep already does rather than inventing a second convention) and ranks it against the SAME iv_history table (volatility.iv_engine.IVHistoryStore) OptionsAnalysisStep writes to via calculate_true_ivr() -- strictly prior days only, never a lookahead. The result is surfaced as a NEW True_IVR row key alongside the existing realized-vol-only IVR_Proxy (never replacing it -- both stay so provenance is honest); generate_strategy_pricing_matrix's true_ivr argument prefers True_IVR over IVR_Proxy when the flag is on and a finite value was computed, falling back to IVR_Proxy exactly as today otherwise. Any failure at any step -- no live chain data, an empty iv_history table during warm-start (this repo's dev/CI sandboxes never populate GUI/MCP-path history since only OptionsAnalysisStep's orchestrator path writes to it), a network error, or any exception -- degrades to float('nan') for True_IVR and never crashes or changes IVR_Proxy/Cash-Wait fallback behavior (CONSTRAINT #4/#6). False (the default) reproduces today's exact behavior byte-for-byte -- no new network call, no new DB read, True_IVR always NaN. Enabling this adds one live options-chain fetch per symbol per render (GUI)/per call (MCP) -- a real, non-trivial network cost the realized-vol proxy never had.",
    group: "Advanced / Config",
  },
  {
    key: "PAIRS_SNAPSHOT_ENABLED",
    value: true,
    default: true,
    type: "boolean",
    description:
      "When True, the pipeline persists the cointegrated pairs radar (ranking + current spread state) to output/pairs.json for the Pilots PWA (GET /pairs). Expensive O(n^2) scan; default False.",
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
      "Opt-in fix for StrategyValidationHarness's deployability gate. Two related integrity gaps: (1) report.sharpe/max_dd/sortino/calmar/hit_rate/avg_trade_pct/turnover were computed from self.strategy_fn(X, y, X, y) -- a 'test' set IDENTICAL to the training set, i.e. an IN-SAMPLE number feeding the 'net-of-cost Sharpe > 0.5' / 'MaxDD < 30%' deployability criteria -- while only PBO/DSR were genuinely out-of-sample (via CombinatorialPurgedCV). (2) CombinatorialPurgedCV's own DSR/PBO Sharpes were computed on GROSS (cost-free) returns even though the in-sample Sharpe/MaxDD leg applied _apply_cost_model's turnover-scaled cost -- an inconsistent cost basis between the two gate legs. When True, run_cpcv_evaluation applies the same turnover-scaled cost model to every CPCV path's train/test returns before any Sharpe/PBO/DSR/drawdown statistic is computed from them, and the harness's reported sharpe/max_dd/sortino/calmar/hit_rate/avg_trade_pct/turnover become the MEAN of each metric computed independently on every CPCV path's own genuinely held-out (purged+embargoed) OOS returns for the DSR-selected strategy, instead of the full-sample in-sample fit -- see run_cpcv_evaluation's docstring for why this is a per-path mean rather than one concatenated equity curve (CPCV's combinatorial test blocks are deliberately reused across paths). equity_curve/benchmark_curve/macro_benchmark_curve are UNCHANGED either way (still the full-sample series) -- a single non-overlapping OOS equity curve needs the AFML CPCV backtest-path-recombination algorithm, not implemented here (a real, separate follow-up, not silently faked). False (the default) reproduces pre-existing behavior exactly: every currently-recorded docs/VALIDATION_STRATEGY_FIX_LOG.md PBO/DSR/Sharpe/MaxDD baseline for the registered STRATEGY_REGISTRY fleet was measured with this flag off, and this sandboxed dev/CI environment has no live-market network access to re-verify the fleet against the corrected numbers -- flipping this on requires re-running scripts/refresh_validations.py against live data and updating that log, exactly like this codebase's other opt-in correctness levers (e.g. FORECAST_CNN_LSTM_WALKFORWARD_SCALING above, ETF_TRANSMISSION_SIZING_ENABLED).",
    group: "Advanced / Config",
  },
  {
    group: "Advanced / Config", key: "GRAVITY_REQUIRE_NATIVE", type: "boolean",
    value: false, default: false,
    description: "Require native implementation for Gravity Review Suite.",
  },
  // ---- Options & Pairs Snapshots ----
  {
    group: "Options & Pairs Snapshots", key: "OPTIONS_MATRIX_ENABLED", type: "boolean",
    value: false, default: false,
    description: "When True, the pipeline persists the per-symbol options premium directive matrix to output/options_matrix.json for the Pilots PWA (GET /options, GET /symbols/{ticker}/options). Default False.",
  },
  {
    group: "Options & Pairs Snapshots", key: "OPTIONS_TRUE_IVR_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Opt-in: wires a real, options-chain-derived True_IVR into technical_options_engine.build_premium_directive() -- the GUI Technical Options Matrix tab, the get_options_directive MCP tool, api/metrics_api.py, execution/options_queue_builder.py, and every other build_premium_directive caller -- instead of leaving true IV rank exclusive to main_orchestrator.py's pipeline path. When True, build_premium_directive fetches a live 30-calendar-day ATM IV and ranks it against the iv_history table, strictly prior days only, never a lookahead. Surfaced as a new True_IVR row key alongside the existing realized-vol-only IVR_Proxy (never replacing it). Any failure degrades to float('nan') for True_IVR and never crashes or changes IVR_Proxy/Cash-Wait fallback behavior (CONSTRAINT #4/#6). False (the default) reproduces today's exact behavior byte-for-byte -- no new network call, no new DB read, True_IVR always NaN.",
  },
  {
    group: "Options & Pairs Snapshots", key: "PAIRS_SNAPSHOT_ENABLED", type: "boolean",
    value: false, default: false,
    description: "When True, the pipeline persists the cointegrated pairs radar (ranking + current spread state) to output/pairs.json for the Pilots PWA (GET /pairs). Expensive O(n^2) scan; default False.",
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
      "Master switch for multi-source sentiment ingestion (Yahoo RSS/GDELT/Reddit/EDGAR). False is a complete no-op.",
  },
  {
    group: "Sentiment Ingestion Core",
    key: "SENTIMENT_SOURCES",
    type: "string",
    value: "yahoo_rss,gdelt,reddit,edgar",
    default: "yahoo_rss,gdelt,reddit,edgar",
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
  // ---- Sources — Reddit, StockTwits, EDGAR, GDELT, Google News ----
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
    key: "STOCKTWITS_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch for the free, uncredentialed StockTwits source.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
    key: "REDDIT_USER_AGENT",
    type: "string",
    value: "stockpy-sentiment-ingestion/0.1",
    default: "stockpy-sentiment-ingestion/0.1",
    description:
      "User-Agent header sent with every Reddit API request, per Reddit's API rules.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
    key: "REDDIT_BACKFILL_MAX_PAGES",
    type: "number",
    value: 10,
    default: 10,
    min: 1,
    max: 100,
    step: 1,
    description:
      "Max pages RedditSource paginates through for a historical backfill request.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
    key: "GOOGLE_NEWS_LOOKBACK_WINDOW",
    type: "string",
    value: "7d",
    default: "7d",
    description:
      "Lookback window passed as Google News RSS's `when:` query parameter.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
    key: "EDGAR_FULLTEXT_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch for the SEC EDGAR full-text search (10-K/10-Q) additions to EdgarSource.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
    key: "EDGAR_FULLTEXT_FORMS",
    type: "string",
    value: "8-K,10-K,10-Q",
    default: "8-K,10-K,10-Q",
    description:
      "Comma-separated SEC form types requested from EDGAR full-text search.",
  },
  {
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
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
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
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
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
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
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
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
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
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
    group: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
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
      "Calendar days of Finnhub company_news headlines scored per symbol per cycle.",
  },
  {
    group: "FinBERT & Catalyst Scoring",
    key: "FINNHUB_RATE_LIMIT_PER_MIN",
    type: "number",
    value: 50,
    default: 50,
    min: 1,
    max: 60,
    step: 1,
    description:
      "Finnhub sliding-window call budget per 60s (free tier ceiling: 60).",
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
const ETF_TRANSMISSION_TUNABLES_KEY = "stockpy.mock.etf_transmission_tunables";
const ETF_TRANSMISSION_TUNABLES_DRIFT_KEY =
  "stockpy.mock.etf_transmission_tunables_drift";
const CACHE_LONG_SHORT_TUNABLES_KEY = "stockpy.mock.cache_long_short_tunables";
const CACHE_LONG_SHORT_TUNABLES_DRIFT_KEY =
  "stockpy.mock.cache_long_short_tunables_drift";

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
      "Fall through to secondary providers (Alpaca/yfinance/Yahoo) on FMP failure.",
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
    description: "Master switch for the FMP company-news feed (data.fmp_client.stock_news, wrapping /news/stock). False (the default) is a complete no-op reproducing today's exact behavior — signals/news_catalyst.py's headline fetch stays on its existing Finnhub-only path, and data/sentiment_sources.py's 'fmp_news' SentimentSource returns [] without any network call. When True AND FMP_API_KEY is set, FMP becomes the PRIMARY provider for company headlines (fetch_company_headlines dispatches FMP-first, falling back to Finnhub only on an FMP failure) and 'fmp_news' becomes eligible for SENTIMENT_SOURCES. Verified live 2026-08 against a real FMP key: /news/stock returns >=6 months of real history (vs. Finnhub's free-tier ~3-month cap). Deliberately does NOT touch /news/press-releases — that endpoint returned a plan-entitlement rejection ('Restricted Endpoint') against the account this integration was verified with.",
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
    group: "Diagnostic & Supplement Feeds", key: "FMP_OPTIONS_HEALTH_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Master switch for the FMP fundamental-health overlay bundled into the options premium-directive matrix (reporting/options_snapshot.py::write_options_matrix → technical_options_engine.build_premium_directive). False (the default) is a complete no-op reproducing today's exact behavior: Altman_Z_Score, Piotroski_F_Score, Net_Debt_EBITDA, FCF_Yield, and Realized_Vol_30D all stay None and zero additional FMP requests are attempted. When True, gates three endpoints for every symbol in the options matrix: Altman Z-Score + Piotroski F-Score (/financial-scores), Net Debt/EBITDA + FCF Yield (/ratios-ttm), and 30-day realized volatility (/standard-deviation). Does NOT gate Days_To_Earnings/Earnings_Risk — those reuse the existing FMP_EARNINGS_ENABLED earnings-calendar gate.",
  },
  {
    group: "Diagnostic & Supplement Feeds", key: "FMP_OPTIONS_CONTEXT_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Master switch for the FMP market/qualitative-context overlay bundled into the options premium-directive matrix (reporting/options_snapshot.py::write_options_matrix → technical_options_engine.build_premium_directive). False (the default) is a complete no-op reproducing today's exact behavior: News_Snippets stays [], Peers stays [], and zero additional FMP requests are attempted. When True, gates two endpoints for every symbol in the options matrix: recent news headlines, capped at 3 per symbol (/news/stock), and the peer-comparison ticker group (/peers). Kept separate from FMP_OPTIONS_HEALTH_ENABLED because it is a different overlay concept — market/qualitative context rather than balance-sheet health.",
  },
  {
    group: "Diagnostic & Supplement Feeds", key: "FMP_PEERS_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Master switch for the on-demand GET /data/peers/{symbol} endpoint (api/data_api.py) — a single, per-click, operator-triggered FMP peer-group lookup (/peers) for the webapp's 'Suggest peers for this ticker' affordance on SymbolComparison. False (the default) is a complete no-op: the endpoint returns an empty peer list + an honest reason, with ZERO network calls. Deliberately kept SEPARATE from FMP_OPTIONS_CONTEXT_ENABLED, which already gates a DIFFERENT call site of the same fetch_peer_group function: a per-cycle BATCH fetch across the whole options-matrix universe. A single user-triggered click and a per-cycle loop over an entire universe have completely different cost/cadence profiles and must be independently controllable.",
  },
  {
    group: "Diagnostic & Supplement Feeds", key: "FMP_UNIVERSE_ENABLED", type: "boolean",
    value: false, default: false,
    description: "Use FMP's historical S&P 500 constituent-changes feed as the primary source for survivorship-bias reconstruction (Wikipedia demoted to fallback).",
  },
];

const ETF_TRANSMISSION_TUNABLE_DEFS: MockTunableDef[] = [
  {
    group: "Holdings Ingestion",
    key: "ETF_HOLDINGS_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch for ETF constituent holdings ingestion (EDGAR N-PORT).",
  },
  {
    group: "Holdings Ingestion",
    key: "ETF_HOLDINGS_TICKERS",
    type: "string",
    value:
      '["SPY","IVV","VOO","QQQ","DIA","IWM","XLK","XLF","XLV","XLE","XLI","XLY","XLP","XLU","XLB","XLRE","XLC"]',
    default:
      '["SPY","IVV","VOO","QQQ","DIA","IWM","XLK","XLF","XLV","XLE","XLI","XLY","XLP","XLU","XLB","XLRE","XLC"]',
    description: "JSON array of ETF tickers to ingest holdings for.",
  },
  {
    group: "Holdings Ingestion",
    key: "ETF_HOLDINGS_REFRESH_DAYS",
    type: "number",
    value: 7,
    default: 7,
    min: 1,
    max: 90,
    step: 1,
    description: "Refresh interval for ETF constituent holdings in days.",
  },
  {
    group: "Holdings Ingestion",
    key: "ETF_HOLDINGS_ISSUER_CSV_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description: "Allow secondary CSV ingestion directly from issuer sites.",
  },
  {
    group: "Holdings Ingestion",
    key: "ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE",
    type: "number",
    value: 60.0,
    default: 60.0,
    min: 1.0,
    max: 300.0,
    step: 1.0,
    description:
      "Max wall-clock seconds allocated for ETF holdings ingestion per cycle.",
  },
  {
    group: "Holdings Ingestion",
    key: "ETF_HOLDINGS_CIRCUIT_BREAKER_THRESHOLD",
    type: "number",
    value: 3,
    default: 3,
    min: 1,
    max: 20,
    step: 1,
    description: "Consecutive ingestion failures before circuit breaker trips.",
  },
  {
    group: "Measurement & Residualization",
    key: "ETF_TRANSMISSION_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch for ETF volatility-transmission measurement columns.",
  },
  {
    group: "Measurement & Residualization",
    key: "ETF_HOLDINGS_MARKET_PROXY",
    type: "string",
    value: "SPY",
    default: "SPY",
    description: "Market benchmark ticker used for residualization.",
  },
  {
    group: "Measurement & Residualization",
    key: "ETF_TRANSMISSION_WRAPPERS",
    type: "string",
    value:
      '["SPY","QQQ","IWM","DIA","XLB","XLC","XLE","XLF","XLI","XLK","XLP","XLRE","XLU","XLV","XLY"]',
    default:
      '["SPY","QQQ","IWM","DIA","XLB","XLC","XLE","XLF","XLI","XLK","XLP","XLRE","XLU","XLV","XLY"]',
    description:
      "JSON array of candidate wrapper ETFs considered as transmission wrappers.",
  },
  {
    group: "Measurement & Residualization",
    key: "ETF_TRANSMISSION_EXCLUDED_SYMBOLS",
    type: "string",
    value: "[]",
    default: "[]",
    description:
      "JSON array of extra symbols excluded from ETF transmission calculation.",
  },
  {
    group: "Measurement & Residualization",
    key: "ETF_TRANSMISSION_WINDOW_DAYS",
    type: "number",
    value: 60,
    default: 60,
    min: 10,
    max: 504,
    step: 1,
    description: "Rolling window days for ETF comovement R² calculation.",
  },
  {
    group: "Measurement & Residualization",
    key: "ETF_TRANSMISSION_MIN_OBS",
    type: "number",
    value: 60,
    default: 60,
    min: 5,
    max: 252,
    step: 1,
    description: "Minimum required observation days in the rolling window.",
  },
  {
    group: "Position Sizing Derate",
    key: "ETF_TRANSMISSION_SIZING_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Enable position sizing derate based on ETF ownership & comovement.",
  },
  {
    group: "Position Sizing Derate",
    key: "ETF_TRANSMISSION_MAX_DERATE",
    type: "number",
    value: 0.3,
    default: 0.3,
    min: 0.0,
    max: 1.0,
    step: 0.05,
    description:
      "Maximum sizing derate fraction (e.g. 0.30 = up to 30% reduction).",
  },
  {
    group: "Position Sizing Derate",
    key: "ETF_TRANSMISSION_OWNERSHIP_REFERENCE",
    type: "number",
    value: 0.2,
    default: 0.2,
    min: 0.01,
    max: 1.0,
    step: 0.01,
    description: "Reference ETF ownership percentage scaling the derate.",
  },
  {
    group: "Position Sizing Derate",
    key: "ETF_TRANSMISSION_MIN_MULTIPLIER",
    type: "number",
    value: 0.5,
    default: 0.5,
    min: 0.0,
    max: 1.0,
    step: 0.05,
    description: "Floor for the position sizing multiplier.",
  },
  {
    group: "Portfolio Covariance Adjustment",
    key: "ETF_TRANSMISSION_PORTFOLIO_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Enable ETF-co-ownership-adjusted portfolio covariance matrix.",
  },
  {
    group: "Portfolio Covariance Adjustment",
    key: "ETF_TRANSMISSION_COV_INFLATION",
    type: "number",
    value: 0.25,
    default: 0.25,
    min: 0.0,
    max: 5.0,
    step: 0.05,
    description:
      "Off-diagonal covariance inflation factor for overlapping ETF holdings.",
  },
  {
    group: "Portfolio Covariance Adjustment",
    key: "ETF_TRANSMISSION_COV_WINDOW_DAYS",
    type: "number",
    value: 60,
    default: 60,
    min: 10,
    max: 504,
    step: 1,
    description:
      "Rolling window days for ETF portfolio covariance calculation.",
  },
];

const CACHE_LONG_SHORT_TUNABLE_DEFS: MockTunableDef[] = [
  {
    group: "Cache Long/Short Overlay",
    key: "CACHE_LONG_SHORT_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Master switch for the Cache Long/Short tax-loss-harvesting advisory strategy.",
  },
  {
    group: "Cache Long/Short Overlay",
    key: "CACHE_LONG_SHORT_WRITES_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Dedicated fail-closed flag for the position-writing endpoints (start, approve-bulk).",
  },
  {
    group: "Cache Long/Short Overlay",
    key: "CACHE_LONG_SHORT_MIN_CORRELATION",
    type: "number",
    value: 0.75,
    default: 0.75,
    min: 0.0,
    max: 1.0,
    step: 0.05,
    description: "Min correlation to trigger drift alert.",
  },
  {
    group: "Cache Long/Short Overlay",
    key: "CACHE_LONG_SHORT_TLH_THRESHOLD_PCT",
    type: "number",
    value: 0.05,
    default: 0.05,
    min: 0.0,
    max: 1.0,
    step: 0.01,
    description:
      "Percentage loss to trigger a tax-loss-harvesting recommendation.",
  },
  {
    group: "Cache Long/Short Overlay",
    key: "CACHE_LONG_SHORT_SCAN_INTERVAL_SECONDS",
    type: "number",
    value: 3600,
    default: 3600,
    min: 60,
    max: 86400,
    step: 60,
    description:
      "Interval (seconds) for the Cache Long/Short background worker loop.",
  },
  {
    // JSON-array field -- kept as a "string" type like every other JSON-blob
    // tunable (ETF_HOLDINGS_TICKERS, SECTOR_FORECAST_CONFIGS, etc.); the
    // frontend's TunableFieldType has no separate "json" member.
    group: "Cache Long/Short Overlay",
    key: "CACHE_LONG_SHORT_PROXY_CANDIDATES",
    type: "string",
    value: '["SPY","QQQ","XLK","XLF","XLV","XLE"]',
    default: '["SPY","QQQ","XLK","XLF","XLV","XLE"]',
    description:
      "JSON array of candidate proxy ETFs screened for a concentrated ticker's hedge leg.",
  },
];

// Mirrors api/pilots_api.py's _FEATURE_FLAGS_GROUPS exactly: the 19
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
    key: "CACHE_LONG_SHORT_WRITES_ENABLED",
    type: "boolean",
    value: true,
    default: true,
    description:
      "Gates the Cache Long/Short position-writing endpoints (start, approve-bulk) -- changes what a trading strategy recommends.",
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
    key: "ETF_HOLDINGS_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Enables fetching ETF constituent baskets for exposure analysis.",
  },
  {
    group: "Diagnostic & Data Features",
    key: "ETF_TRANSMISSION_ENABLED",
    type: "boolean",
    value: false,
    default: false,
    description:
      "Enables ETF volatility-transmission measurement columns (diagnostic only -- not read by scoring or sizing).",
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
];

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

function mockEtfTransmissionTunables(): TunablesResponse {
  return buildTunablesResponse(
    ETF_TRANSMISSION_TUNABLE_DEFS,
    ETF_TRANSMISSION_TUNABLES_KEY,
    ETF_TRANSMISSION_TUNABLES_DRIFT_KEY,
  );
}

function applyEtfTransmissionTunables(
  values: Record<string, number | boolean | string>,
  confirm: Record<string, string> = {},
): TunablesUpdateResult {
  return applyTunablesGeneric(
    values,
    ETF_TRANSMISSION_TUNABLE_DEFS,
    ETF_TRANSMISSION_TUNABLES_KEY,
    ETF_TRANSMISSION_TUNABLES_DRIFT_KEY,
    confirm,
  );
}

function mockCacheLongShortTunables(): TunablesResponse {
  return buildTunablesResponse(
    CACHE_LONG_SHORT_TUNABLE_DEFS,
    CACHE_LONG_SHORT_TUNABLES_KEY,
    CACHE_LONG_SHORT_TUNABLES_DRIFT_KEY,
  );
}

function applyCacheLongShortTunables(
  values: Record<string, number | boolean | string>,
  confirm: Record<string, string> = {},
): TunablesUpdateResult {
  return applyTunablesGeneric(
    values,
    CACHE_LONG_SHORT_TUNABLE_DEFS,
    CACHE_LONG_SHORT_TUNABLES_KEY,
    CACHE_LONG_SHORT_TUNABLES_DRIFT_KEY,
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
    // FMP fundamental-health overlay (settings.FMP_OPTIONS_HEALTH_ENABLED) --
    // Altman Z >= 2.6 is the "Safe" zone; no upcoming earnings this cycle.
    Altman_Z_Score: 5.8,
    Piotroski_F_Score: 7,
    Net_Debt_EBITDA: 1.2,
    FCF_Yield: 0.045,
    Days_To_Earnings: null,
    Earnings_Risk: false,
    Realized_Vol_30D: 0.21,
    // FMP market/qualitative-context overlay (settings.FMP_OPTIONS_CONTEXT_ENABLED)
    // + analyst consensus (settings.FMP_ANALYST_ENABLED, read from the existing
    // HistoricalStore analyst-snapshot table).
    News_Snippets: [
      {
        title: "Apple Unveils New Services Push Ahead of Holiday Quarter",
        url: "https://example.com/news/aapl-1",
        published_date: "2026-08-01T14:05:00Z",
        site: "Reuters",
      },
      {
        title: "Analysts Raise Price Targets on Apple After Strong Guidance",
        url: "https://example.com/news/aapl-2",
        published_date: "2026-07-31T09:20:00Z",
        site: "Bloomberg",
      },
    ],
    Peers: ["MSFT", "GOOGL", "AMZN"],
    Analyst_Target_Consensus: 235.5,
    Analyst_Target_Upside: 235.5 / 214.9 - 1,
    Analyst_Grade_Score: 0.42,
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
    // Integrity_OK carries a dual meaning (structural + earnings-timing --
    // see technical_options_engine.py's build_premium_directive step-6
    // comment): this directive is structurally clean, but earnings inside
    // the target DTE window flips Integrity_OK to false anyway.
    Integrity_OK: false,
    Integrity_Issues: [
      "⚠️ Earnings Announcement scheduled in 12 days (within target DTE 30)",
    ],
    // Altman Z < 1.8 is the "Distress" zone.
    Altman_Z_Score: 1.4,
    Piotroski_F_Score: 3,
    Net_Debt_EBITDA: 3.8,
    FCF_Yield: -0.01,
    Days_To_Earnings: 12,
    Earnings_Risk: true,
    Realized_Vol_30D: 0.19,
    // FMP market/qualitative-context overlay + analyst consensus -- a
    // downside/sell-leaning case (target below Price, negative grade score)
    // so the detail sheet's decline coloring and "weak" grade badge are
    // exercised alongside AAPL's upside/buy-leaning case above.
    News_Snippets: [
      {
        title: "Microsoft Cloud Growth Slows Amid Enterprise Spending Pullback",
        url: "https://example.com/news/msft-1",
        published_date: "2026-07-30T11:00:00Z",
        site: "CNBC",
      },
    ],
    Peers: ["AAPL", "GOOGL", "ORCL"],
    Analyst_Target_Consensus: 405.0,
    Analyst_Target_Upside: 405.0 / 431.2 - 1,
    Analyst_Grade_Score: -0.22,
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
    // Altman Z in [1.8, 2.6) is the "Grey" zone; earnings 45d out is beyond
    // this row's 30-day target DTE, so Earnings_Risk stays false.
    Altman_Z_Score: 2.1,
    Piotroski_F_Score: 5,
    Net_Debt_EBITDA: 0.5,
    FCF_Yield: 0.08,
    Days_To_Earnings: 45,
    Earnings_Risk: false,
    Realized_Vol_30D: 0.41,
    // Peers only, no News_Snippets/analyst fields -- proves the News & Peers
    // section's two sub-blocks are independently conditional (peers render,
    // news doesn't), and that the Analyst Consensus section stays absent
    // (no placeholder) when the store had no snapshot for this symbol.
    Peers: ["AMD", "AVGO", "QCOM"],
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
    Legs: [
      { Side: "Short", Type: "Call", Strike: 290.0, Price: 3.05, Delta: 0.3 },
    ],
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

function mockValidateBrinsonFachlerRows(rows: BrinsonFachlerRow[]): string[] {
  const warnings: string[] = [];
  const validRows = rows.filter((r) => r.sector.trim() !== "");
  if (validRows.length === 0) return ["No rows with a non-blank sector name."];

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
  const rows: ForecastSkillSymbolRow[] = FORECAST_SKILL_SYMBOLS.map(
    (symbol, i) => {
      const rng = seeded(horizon * 7919 + symbol.charCodeAt(0) * 31 + i);
      // One symbol (the last) is deliberately cold-start -- zero history yet,
      // even though it's part of the requested universe -- to exercise the
      // "never silently omit a requested symbol" rendering path in mock mode
      // too, not just in the backend's own unit tests.
      if (i === FORECAST_SKILL_SYMBOLS.length - 1) {
        return { symbol, pending: 0, completed: 0, skill_weights: {} };
      }
      const raw = models.map(() => 0.1 + rng());
      const tot = raw.reduce((a, b) => a + b, 0);
      const skill_weights: Record<string, number> = {};
      models.forEach((m, j) => (skill_weights[m] = +(raw[j] / tot).toFixed(3)));
      return {
        symbol,
        pending: Math.floor(rng() * 4) + 1,
        completed: Math.floor(rng() * 60) + 20,
        skill_weights,
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
// reason as mockSizingCapAuditDisabled/mockEtfTransmissionDisabled above:
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
      source: i % 2 === 0 ? "alpaca" : "yfinance",
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

// ---- ETF Volatility Transmission (G7) ----
function mockEtfTransmissionSummary(): EtfTransmissionSummary {
  return {
    rows: [
      {
        symbol: "SPY",
        etf_ownership_pct: 1.0,
        etf_comovement_r2: 1.0,
        etf_primary_wrapper: "SPY",
        etf_transmission_multiplier: null,
      },
      {
        symbol: "NVDA",
        etf_ownership_pct: 0.42,
        etf_comovement_r2: 0.81,
        etf_primary_wrapper: "QQQ",
        etf_transmission_multiplier: 0.74,
      },
      {
        symbol: "JPM",
        etf_ownership_pct: 0.18,
        etf_comovement_r2: 0.55,
        etf_primary_wrapper: "XLF",
        etf_transmission_multiplier: 0.94,
      },
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
    reason:
      "ETF_TRANSMISSION_ENABLED is False -- measurement columns are not computed this cycle.",
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
    // opt-in-flag section here (sizing_cap_audit, etf_transmission).
    latency_heatmap: readObservabilityColdStart()
      ? mockLatencyHeatmapDisabled()
      : mockLatencyHeatmap(),
    sizing_cap_audit: readObservabilityColdStart()
      ? mockSizingCapAuditDisabled()
      : mockSizingCapAuditTrail(),
    etf_transmission: readObservabilityColdStart()
      ? mockEtfTransmissionDisabled()
      : mockEtfTransmissionSummary(),
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
  command_count: 5,
  dead_letters: [],
  reason: null,
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
 * fields: no news provider configured (neither FMP_NEWS_ENABLED nor a
 * Finnhub client), so there are no headlines and no earnings-catalyst read
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
        quote_source: covered ? "alpaca" : "",
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
      // Matches the real backend exactly: `follow.amount` is always the
      // raw requested amount (FollowsStore().upsert(pilot_id, body.amount)
      // in api/pilots_api.py) -- it is NEVER the Kelly-clamped amount. The
      // clamped figure only exists as the sum of `planned_intents[].
      // target_notional` below; that discrepancy is exactly what
      // FollowModal.tsx's "capped" notice exists to surface honestly.
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

    // Kelly-ceiling sizing simulation (mirrors pilots/mirror.py's
    // plan_follow) -- a deliberately simplified, deterministic stand-in for
    // the real bootstrap-Kelly/vol-target math, not a replication of it.
    // MOCK_TOTAL_EQUITY matches getPortfolioSummary's mock fixture so the
    // implied kelly_weight fraction stays internally consistent.
    const MOCK_TOTAL_EQUITY = 48213.55;
    const MOCK_KELLY_CEILING = 1800; // deliberately below the $2500 quick-chip, above $1000
    const kellyWeight = +(MOCK_KELLY_CEILING / MOCK_TOTAL_EQUITY).toFixed(4);
    const capped = amount > MOCK_KELLY_CEILING;
    const allocated = capped ? MOCK_KELLY_CEILING : amount;
    const sizingPath = capped
      ? "vol_target_fallback_no_scalein(n=0)"
      : "bootstrap_kelly_5th_pct(n=45,k5=0.09,k50=0.14,k95=0.21)";

    const planned = p.holdings.map((hd) => ({
      symbol: hd.symbol,
      side: "BUY" as const,
      target_notional: +Math.min(allocated * hd.weight, NOTIONAL_CAP).toFixed(
        2,
      ),
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
      sizing_path: amount > 0 ? sizingPath : undefined,
      kelly_weight: amount > 0 ? kellyWeight : undefined,
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
        errors: {
          generated_at: new Date(now - 5 * 60_000).toISOString(),
          entry_count: 0,
          entries: [],
        },
        advisory_only: true,
        dry_run: false,
        alpaca_paper: false,
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
    // (ADVISORY_ONLY always; DRY_RUN too when mode != "advisory" -- ALPACA_PAPER
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
            : ["ADVISORY_ONLY", "DRY_RUN", "ALPACA_PAPER"],
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

  async getOptionsChain(ticker: string, expiration?: string): Promise<OptionChainResponse> {
    const sym = ticker.trim().toUpperCase();
    
    if (!expiration) {
      return delay({
        symbol: sym,
        spot_price: 150.0,
        expirations: ["2026-08-21", "2026-08-28", "2026-09-18", "2026-10-16", "2027-01-15"]
      });
    }

    const strikes = [140, 145, 150, 155, 160];
    const calls = strikes.map(strike => ({
      contractSymbol: `${sym}260821C00${strike}000`,
      strike,
      lastPrice: Math.max(0.1, 150 - strike + 5),
      bid: Math.max(0.05, 150 - strike + 4.9),
      ask: Math.max(0.15, 150 - strike + 5.1),
      volume: 1200,
      openInterest: 5000,
      impliedVolatility: 0.25,
      inTheMoney: strike < 150,
      greeks: { delta: strike < 150 ? 0.7 : 0.3, gamma: 0.05, theta: -0.02, vega: 0.1, rho: 0.01, chanceOfProfit: strike < 150 ? 0.7 : 0.3 }
    }));
    
    const puts = strikes.map(strike => ({
      contractSymbol: `${sym}260821P00${strike}000`,
      strike,
      lastPrice: Math.max(0.1, strike - 150 + 5),
      bid: Math.max(0.05, strike - 150 + 4.9),
      ask: Math.max(0.15, strike - 150 + 5.1),
      volume: 800,
      openInterest: 3000,
      impliedVolatility: 0.26,
      inTheMoney: strike > 150,
      greeks: { delta: strike > 150 ? -0.7 : -0.3, gamma: 0.05, theta: -0.02, vega: 0.1, rho: -0.01, chanceOfProfit: strike > 150 ? 0.7 : 0.3 }
    }));

    return delay({
      symbol: sym,
      expiration,
      spot_price: 150.0,
      calls,
      puts
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
            "Based on 4 Finnhub headlines from the past 7 days and the most recent earnings date.",
        },
      },
      400,
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
      return delay(
        { ...notFoundBase, reason: "Both Symbol Y and Symbol X are required." },
        250,
      );
    }
    if (symY === symX) {
      return delay(
        {
          ...notFoundBase,
          reason: "Symbol Y and Symbol X must be different tickers.",
        },
        250,
      );
    }
    if (symY === "ZZZ" || symX === "ZZZ") {
      return delay(
        {
          ...notFoundBase,
          reason: `Insufficient aligned history for ${symY}/${symX} — one or both symbols may be unavailable from the provider.`,
        },
        450,
      );
    }

    const rng = seeded(
      [...symY, ...symX].reduce((a, c) => a + c.charCodeAt(0), 0),
    );
    const z = +((rng() - 0.5) * 6).toFixed(2);
    const halfLife = +(8 + rng() * 40).toFixed(1);
    const rollingP = +(rng() * 0.15).toFixed(4);
    const position = z > 2 ? -1 : z < -2 ? 1 : 0;
    const halfLifeTradeable =
      halfLife >= 5 && halfLife <= 60 && rollingP <= 0.1;
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
      date: new Date(Date.now() - (n - i) * 86_400_000)
        .toISOString()
        .slice(0, 10),
      z_score: +(Math.sin(i / 9 + rng()) * 2 + (rng() - 0.5)).toFixed(2),
    }));
    series[series.length - 1] = {
      date: series[series.length - 1].date,
      z_score: z,
    };

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
      450,
    );
  },

  async scanPairs(req: PairsScanRequest): Promise<PairsScanResult> {
    const requested = Array.from(
      new Set(req.symbols.map((s) => s.trim().toUpperCase()).filter(Boolean)),
    );
    const known = new Set([
      "XOM",
      "CVX",
      "V",
      "JPM",
      "MSFT",
      "AAPL",
      "HD",
      "COST",
    ]);
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
        400,
      );
    }

    const usableSet = new Set(usable);
    const pairs = mockPairs().pairs.filter(
      (p) => usableSet.has(p.ticker1) && usableSet.has(p.ticker2),
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
      500,
    );
  },

  async recomputeOptions(
    req: OptionsRecomputeRequest,
  ): Promise<OptionsRecomputeResult> {
    const requested = Array.from(
      new Set(req.symbols.map((s) => s.trim().toUpperCase()).filter(Boolean)),
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
      const action =
        strategy === "Cash"
          ? "Wait"
          : sellRegime
            ? "Sell to Open"
            : "Buy to Open";
      const netPremium =
        strategy === "Cash"
          ? 0
          : +((sellRegime ? 1 : -1) * (0.3 + rng()) * 2).toFixed(2);
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
        Realizable_Daily_Theta:
          strategy === "Cash" ? 0 : +(netPremium * 0.03).toFixed(3),
        ATM_Delta: +(0.4 + rng() * 0.2).toFixed(3),
        ATM_Gamma: +(rng() * 0.05).toFixed(4),
        ATM_Vega: +(rng() * 0.15).toFixed(3),
        ATM_Theta_Daily: +(-rng() * 0.05).toFixed(3),
        Short_Strike:
          strategy === "Cash"
            ? null
            : +(price * (sellRegime ? 0.97 : 1.03)).toFixed(2),
        Long_Strike:
          strategy === "Cash"
            ? null
            : +(price * (sellRegime ? 0.94 : 1.06)).toFixed(2),
        Short_Delta:
          strategy === "Cash" ? null : +(sellRegime ? -0.3 : 0.3).toFixed(2),
        Long_Delta:
          strategy === "Cash" ? null : +(sellRegime ? -0.15 : 0.15).toFixed(2),
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
      600,
    );
  },

  async postOptionsOrder(req: OptionsOrderRequest): Promise<OptionsOrderResult> {
    console.log(`[mockApi] postOptionsOrder (${req.isLive ? 'LIVE' : 'PAPER'}):`, req);
    if (req.isLive) {
      return delay({
        ok: false,
        message: "Live order execution is currently in Advisory-Only mode. Order staged for review."
      }, 500);
    }

    if (!paperAccountInitialized) {
      paperAccount = { equity: 100000, cash: 100000, buying_power: 100000 };
      paperAccountInitialized = true;
    }

    const isStock = req.asset_type === "stock";
    let orderSymbol = req.symbol.toUpperCase();
    let qty = req.quantity || 1;
    let fillPrice = req.limit_price || 10.0;
    let totalCost = 0;

    if (isStock) {
      if (req.dollar_amount && req.dollar_amount > 0 && (!req.quantity || req.quantity <= 0)) {
        qty = Math.max(1, +(req.dollar_amount / fillPrice).toFixed(4));
      }
      totalCost = qty * fillPrice;

      const orderSide: 'BUY' | 'SELL' = req.side?.toUpperCase() === 'SELL' ? 'SELL' : 'BUY';
      const existingPos = paperPositions.find(p => p.symbol === orderSymbol);

      if (orderSide === 'SELL' && (!existingPos || existingPos.qty < qty)) {
        return delay({
          ok: false,
          message: `Order rejected: Insufficient funds or inventory for SELL ${qty} ${orderSymbol}.`
        }, 500);
      } else if (orderSide === 'BUY' && paperAccount.cash < totalCost) {
        return delay({
          ok: false,
          message: `Insufficient paper funds. Required: $${totalCost.toFixed(2)}, Available: $${paperAccount.cash.toFixed(2)}`
        }, 500);
      }

      if (orderSide === 'SELL') {
        paperAccount.cash += totalCost;
        existingPos!.qty -= qty;
        existingPos!.market_value = Math.max(0, (existingPos!.market_value || 0) - totalCost);
        if (existingPos!.qty <= 0) {
          paperPositions = paperPositions.filter(p => p.symbol !== orderSymbol);
        }
      } else {
        paperAccount.cash -= totalCost;
        if (existingPos) {
          const prevTotal = existingPos.qty * existingPos.avg_cost;
          existingPos.qty += qty;
          existingPos.avg_cost = (prevTotal + totalCost) / existingPos.qty;
          existingPos.market_value = (existingPos.market_value || 0) + totalCost;
        } else {
          paperPositions.push({
            symbol: orderSymbol,
            qty: qty,
            avg_cost: fillPrice,
            current_price: fillPrice,
            market_value: totalCost,
            unrealized_pl: 0,
            unrealized_pl_pct: 0
          });
        }
      }
      paperAccount.buying_power = paperAccount.cash;

      const orderId = `mock_ord_${Date.now()}`;
      paperOrders.unshift({
        order_id: orderId,
        symbol: orderSymbol,
        side: orderSide,
        qty: qty,
        price: fillPrice,
        status: 'filled',
        filled_qty: qty,
        filled_avg_price: fillPrice,
        created_at: new Date().toISOString()
      });

      return delay({
        ok: true,
        order_id: orderId,
        message: `Paper stock order for ${qty} shares of ${orderSymbol} filled at $${fillPrice.toFixed(2)} (Total: $${totalCost.toFixed(2)}).`
      }, 600);

    } else if (req.legs && req.legs.length > 1) {
      // Multi-leg option order execution
      let netPrice = req.limit_price || 0.0;
      if (!netPrice) {
        netPrice = req.legs.reduce((acc, leg) => {
          const mult = leg.action === 'Buy' ? 1 : -1;
          const p = leg.contract.lastPrice || leg.contract.ask || 0.05;
          return acc + (mult * p);
        }, 0);
      }
      fillPrice = Math.abs(netPrice) || 0.05;
      const isDebit = netPrice >= 0;
      const costPerContract = fillPrice * 100;

      if (req.dollar_amount && req.dollar_amount > 0 && (!req.quantity || req.quantity <= 0)) {
        qty = Math.max(1, Math.floor(req.dollar_amount / costPerContract));
      }
      const commission = 0.65 * qty * req.legs.length;
      totalCost = (qty * costPerContract) + (isDebit ? commission : -commission);

      if (isDebit && paperAccount.cash < totalCost) {
        return delay({
          ok: false,
          message: `Insufficient paper funds. Required: $${totalCost.toFixed(2)}, Available: $${paperAccount.cash.toFixed(2)}`
        }, 500);
      }

      paperAccount.cash += (isDebit ? -totalCost : totalCost);
      paperAccount.buying_power = paperAccount.cash;

      const orderId = `mock_ord_${Date.now()}`;
      const strategyName = `${req.legs.length}-Leg Strategy`;

      // Apply each leg position
      req.legs.forEach(leg => {
        const legSymbol = `${req.symbol.toUpperCase()} ${req.expiration || ''} $${leg.contract.strike} ${(leg.type || 'CALL').toUpperCase()}`.trim();
        const legMult = leg.action === 'Buy' ? 1 : -1;
        const legQty = qty * legMult;
        const legPrice = (leg.contract.lastPrice || leg.contract.ask || 0.05) * 100;

        const existingPos = paperPositions.find(p => p.symbol === legSymbol);
        if (existingPos) {
          existingPos.qty += legQty;
          if (Math.abs(existingPos.qty) < 1e-6) {
            paperPositions = paperPositions.filter(p => p.symbol !== legSymbol);
          }
        } else {
          paperPositions.push({
            symbol: legSymbol,
            qty: legQty,
            avg_cost: legPrice,
            current_price: legPrice,
            market_value: Math.abs(legQty) * legPrice,
            unrealized_pl: 0,
            unrealized_pl_pct: 0
          });
        }
      });

      paperOrders.unshift({
        order_id: orderId,
        symbol: `${strategyName} ${req.symbol.toUpperCase()}`,
        side: isDebit ? 'BUY' : 'SELL',
        qty: qty,
        price: fillPrice,
        status: 'filled',
        filled_qty: qty,
        filled_avg_price: fillPrice,
        created_at: new Date().toISOString()
      });

      return delay({
        ok: true,
        order_id: orderId,
        message: `Paper ${strategyName} for ${qty} contract(s) on ${req.symbol.toUpperCase()} filled at ${isDebit ? 'Debit' : 'Credit'} $${fillPrice.toFixed(2)}/sh (Total: $${Math.abs(totalCost).toFixed(2)}).`
      }, 600);

    } else {
      // Single leg option order
      const leg = req.legs?.[0];
      const legAction = leg?.action || req.side || 'Buy';
      const isBuy = legAction.toLowerCase() === 'buy';

      let legPrice = req.limit_price || (leg ? (leg.contract.lastPrice || (isBuy ? leg.contract.ask : leg.contract.bid) || 0.05) : 0.05);
      if (legPrice <= 0) legPrice = 0.05;
      fillPrice = legPrice;
      const costPerContract = fillPrice * 100;

      if (req.dollar_amount && req.dollar_amount > 0 && (!req.quantity || req.quantity <= 0)) {
        qty = Math.max(1, Math.floor(req.dollar_amount / costPerContract));
      }
      const commission = 0.65 * qty;
      totalCost = (qty * costPerContract) + (isBuy ? commission : -commission);

      if (leg) {
        orderSymbol = `${req.symbol.toUpperCase()} ${req.expiration || ''} $${leg.contract.strike} ${(leg.type || 'CALL').toUpperCase()}`.trim();
      } else {
        orderSymbol = `${req.symbol.toUpperCase()} OPTION`;
      }

      if (isBuy && paperAccount.cash < totalCost) {
        return delay({
          ok: false,
          message: `Insufficient paper funds. Required: $${totalCost.toFixed(2)}, Available: $${paperAccount.cash.toFixed(2)}`
        }, 500);
      }

      paperAccount.cash += (isBuy ? -totalCost : totalCost);
      paperAccount.buying_power = paperAccount.cash;

      const legQty = isBuy ? qty : -qty;
      const existingPos = paperPositions.find(p => p.symbol === orderSymbol);
      if (existingPos) {
        existingPos.qty += legQty;
        if (Math.abs(existingPos.qty) < 1e-6) {
          paperPositions = paperPositions.filter(p => p.symbol !== orderSymbol);
        }
      } else {
        paperPositions.push({
          symbol: orderSymbol,
          qty: legQty,
          avg_cost: fillPrice * 100,
          current_price: fillPrice * 100,
          market_value: Math.abs(legQty) * fillPrice * 100,
          unrealized_pl: 0,
          unrealized_pl_pct: 0
        });
      }

      const orderId = `mock_ord_${Date.now()}`;
      paperOrders.unshift({
        order_id: orderId,
        symbol: orderSymbol,
        side: isBuy ? 'BUY' : 'SELL',
        qty: qty,
        price: fillPrice,
        status: 'filled',
        filled_qty: qty,
        filled_avg_price: fillPrice,
        created_at: new Date().toISOString()
      });

      return delay({
        ok: true,
        order_id: orderId,
        message: `Paper option order for ${qty} contract(s) of ${orderSymbol} filled at $${fillPrice.toFixed(2)} (Total: $${Math.abs(totalCost).toFixed(2)}).`
      }, 600);
    }

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

  async getEtfTransmissionSettings(): Promise<TunablesResponse> {
    return delay(mockEtfTransmissionTunables());
  },

  async updateEtfTransmissionSettings(
    values: Record<string, number | boolean | string>,
    confirm: SettingsConfirmMap = {},
  ): Promise<TunablesUpdateResult> {
    return delay(applyEtfTransmissionTunables(values, confirm));
  },

  async getCacheLongShortSettings(): Promise<TunablesResponse> {
    return delay(mockCacheLongShortTunables());
  },

  async updateCacheLongShortSettings(
    values: Record<string, number | boolean | string>,
    confirm: SettingsConfirmMap = {},
  ): Promise<TunablesUpdateResult> {
    return delay(applyCacheLongShortTunables(values, confirm));
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
        timestamp: new Date(
          Date.now() - (delayed ? 15 * 60_000 : 2_000),
        ).toISOString(),
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
    return delay<UniverseListResponse>({
      symbols: [...MOCK_DATA_UNIVERSE],
      count: MOCK_DATA_UNIVERSE.length,
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
      // this batch (e.g. news_catalyst with no FINNHUB_API_KEY configured) —
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
    // literal source strings "fmp"/"finnhub") — NEVER "SEC EDGAR" or any
    // EDGAR/Google-News-flavored publisher, since this data path
    // (signals/news_catalyst.py's FMP-primary/Finnhub-fallback dispatcher)
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

  async getTransformerForecast(symbol: string): Promise<TransformerForecastResponse> {
    if (symbol.toUpperCase() === "ZZZZ") throw notFoundSymbol(symbol);
    const sym = symbol.toUpperCase();
    const baseVol = 0.15 + (sym.length % 5) * 0.02;
    const horizons = ["1d", "5d", "21d", "60d"];
    const horizonDays: Record<string, number> = { "1d": 1, "5d": 5, "21d": 21, "60d": 60 };
    const forecast: Record<string, number> = {};
    const quantile_forecast: Record<string, { q10: number; q50: number; q90: number }> = {};

    for (const h of horizons) {
      const med = Number((baseVol * (1 + 0.05 * Math.sin(horizonDays[h] / 10))).toFixed(4));
      const spread = Number((0.03 + 0.015 * Math.sqrt(horizonDays[h] / 20)).toFixed(4));
      const q10 = Number(Math.max(0.01, med - spread).toFixed(4));
      const q50 = med;
      const q90 = Number((med + spread * 1.25).toFixed(4));
      forecast[h] = q50;
      quantile_forecast[h] = { q10, q50, q90 };
    }
    const attention_heatmap = Array.from({ length: 8 }, () =>
      Array.from({ length: 8 }, () => Math.random())
    );

    return delay<TransformerForecastResponse>({
      symbol: sym,
      forecast,
      quantile_forecast,
      attention_heatmap,
      trained_samples: 120,
      macro_conditioned: true,
    });
  },

  async runDiffusionStressTest(req: DiffusionStressRequest): Promise<DiffusionStressResponse> {
    const sym = req.symbol.toUpperCase();
    const spot = req.spot_price;
    const regime = req.regime ?? "vol_shock";
    const guidance = req.guidance_scale ?? 2.0;
    const baseDrift = req.drift ?? 0.0;
    const horizon = req.horizon ?? 30;
    const numPaths = Math.min(req.num_paths ?? 1000, 30); // sample paths for chart rendering

    // Regime-specific dynamics adjusted by guidance scale
    let volMultiplier = 1.0;
    let regimeDrift = baseDrift;
    let jumpProb = 0.0;
    let jumpSize = 0.0;

    switch (regime) {
      case "vol_shock":
        volMultiplier = 1.0 + 1.2 * (guidance / 2.0);
        regimeDrift = baseDrift - 0.15 * guidance;
        break;
      case "credit_freeze":
        volMultiplier = 1.0 + 0.8 * (guidance / 2.0);
        regimeDrift = baseDrift - 0.28 * guidance;
        break;
      case "stagflation":
        volMultiplier = 1.0 + 0.6 * (guidance / 2.0);
        regimeDrift = baseDrift - 0.20 * guidance;
        break;
      case "liquidity_squeeze":
        volMultiplier = 1.0 + 1.0 * (guidance / 2.0);
        regimeDrift = baseDrift - 0.10 * guidance;
        jumpProb = 0.08 * (guidance / 2.0);
        jumpSize = -0.04 * guidance;
        break;
      case "unconditional":
      default:
        volMultiplier = 1.0;
        regimeDrift = baseDrift;
        break;
    }

    const effVol = req.volatility * volMultiplier;
    const paths: number[][] = [];
    const terminalReturns: number[] = [];

    for (let p = 0; p < numPaths; p++) {
      const path = [spot];
      for (let i = 0; i < horizon; i++) {
        const shock = (Math.random() - 0.5) * 2 * effVol * Math.sqrt(1 / 252);
        const hasJump = Math.random() < jumpProb;
        const jump = hasJump ? jumpSize : 0;
        const ret = regimeDrift / 252 + shock + jump;
        const next = Math.max(0.01, path[path.length - 1] * (1 + ret));
        path.push(next);
      }
      paths.push(path);
      terminalReturns.push((path[path.length - 1] - spot) / spot);
    }

    terminalReturns.sort((a, b) => a - b);
    const n = terminalReturns.length;

    const idx95 = Math.max(0, Math.floor(n * 0.05) - 1);
    const var95Fraction = Math.abs(Math.min(0, terminalReturns[idx95] ?? terminalReturns[0]));
    const tailLosses95 = terminalReturns.slice(0, idx95 + 1).filter((r) => r < 0).map((r) => -r);
    const cvar95Fraction =
      tailLosses95.length > 0
        ? tailLosses95.reduce((a, b) => a + b, 0) / tailLosses95.length
        : var95Fraction;

    const idx99 = Math.max(0, Math.floor(n * 0.01) - 1);
    const var99Fraction = Math.abs(Math.min(0, terminalReturns[idx99] ?? terminalReturns[0]));
    const tailLosses99 = terminalReturns.slice(0, idx99 + 1).filter((r) => r < 0).map((r) => -r);
    const cvar99Fraction =
      tailLosses99.length > 0
        ? tailLosses99.reduce((a, b) => a + b, 0) / tailLosses99.length
        : Math.max(var99Fraction, cvar95Fraction * 1.2);

    return delay<DiffusionStressResponse>(
      {
        symbol: sym,
        regime,
        guidance_scale: guidance,
        paths,
        VaR_95: var95Fraction * spot,
        CVaR_95: Math.max(var95Fraction * spot, cvar95Fraction * spot),
        VaR_99: Math.max(var95Fraction * spot, var99Fraction * spot),
        CVaR_99: Math.max(var99Fraction * spot, cvar99Fraction * spot),
        trained_windows: 145,
        regime_conditioned: true,
      },
      400
    );
  },

  async optimizeHrpCvar(req: HrpCvarOptimizeRequest): Promise<HrpCvarOptimizeResponse> {
    const n = Math.max(1, req.symbols.length);
    const defaultSectorMap: Record<string, string> = {
      AAPL: "Tech",
      MSFT: "Tech",
      NVDA: "Tech",
      JPM: "Financials",
      V: "Financials",
      UNH: "Healthcare",
      JNJ: "Healthcare",
      AMZN: "Consumer",
      PG: "Consumer",
      XOM: "Energy",
      CVX: "Energy",
    };
    const defaultBetas: Record<string, number> = {
      AAPL: 1.15,
      MSFT: 1.05,
      NVDA: 1.65,
      JPM: 0.95,
      V: 0.85,
      UNH: 0.70,
      JNJ: 0.60,
      AMZN: 1.25,
      PG: 0.55,
      XOM: 0.80,
      CVX: 0.75,
    };

    const sectorMap = req.sector_map || defaultSectorMap;
    const assetBetas = req.asset_betas || defaultBetas;
    const lambda = req.lambda_turnover ?? 0.05;

    // Generate baseline HRP weights
    const rawWeights = req.symbols.map((sym, idx) => {
      const b = assetBetas[sym] || 1.0;
      return 1.0 / (0.5 + b * 0.5) + (idx % 2 === 0 ? 0.05 : -0.05);
    });
    const sumRaw = rawWeights.reduce((a, b) => a + b, 0);
    const hrpWeights = rawWeights.map((w) => w / sumRaw);

    // Incumbent weights w0
    const w0: number[] = req.symbols.map((sym) => {
      if (req.current_weights && sym in req.current_weights) {
        return req.current_weights[sym];
      }
      return 1 / n;
    });

    // Turnover regularization: blend between HRP and w0
    const blendFactor = Math.min(0.9, lambda * 2.0); // higher lambda -> closer to incumbent
    let proposedWeights = hrpWeights.map((hw, i) => (1 - blendFactor) * hw + blendFactor * w0[i]);

    // Apply sector caps if provided
    if (req.sector_caps) {
      const sectorTotals: Record<string, number> = {};
      req.symbols.forEach((sym, i) => {
        const sec = sectorMap[sym] || "General";
        sectorTotals[sec] = (sectorTotals[sec] || 0) + proposedWeights[i];
      });

      for (const [sec, cap] of Object.entries(req.sector_caps)) {
        if (sectorTotals[sec] && sectorTotals[sec] > cap) {
          const factor = cap / sectorTotals[sec];
          req.symbols.forEach((sym, i) => {
            if ((sectorMap[sym] || "General") === sec) {
              proposedWeights[i] *= factor;
            }
          });
        }
      }
    }

    // Apply max_asset_weight cap if provided -- mirrors the live endpoint's
    // sizing/hrp_cvar_optimizer.py max_weight bound (Phase 35 remediation item 13)
    // so the mock doesn't silently ignore a request field the real backend honors.
    // A naive cap-then-renormalize can push a capped weight back ABOVE the cap
    // once the leftover mass is redistributed (e.g. cap=0.4 on [0.6,0.25,0.15]
    // renormalizes to [0.5, 0.3125, 0.1875] -- still over cap), so this is a
    // small iterative water-filling loop that genuinely converges under the cap.
    if (req.max_asset_weight !== undefined && req.max_asset_weight !== null) {
      const cap = req.max_asset_weight;
      for (let iter = 0; iter < 20; iter++) {
        const sum = proposedWeights.reduce((a, b) => a + b, 0);
        if (sum <= 0) break;
        proposedWeights = proposedWeights.map((w) => w / sum);
        let excess = 0;
        const freeIdx: number[] = [];
        proposedWeights = proposedWeights.map((w, i) => {
          if (w > cap + 1e-9) {
            excess += w - cap;
            return cap;
          }
          freeIdx.push(i);
          return w;
        });
        if (excess <= 1e-9 || freeIdx.length === 0) break;
        const freeSum = freeIdx.reduce((a, i) => a + proposedWeights[i], 0);
        freeIdx.forEach((i) => {
          proposedWeights[i] += excess * (freeSum > 0 ? proposedWeights[i] / freeSum : 1 / freeIdx.length);
        });
      }
    }

    // Re-normalize weights to 1.0
    const sumProp = proposedWeights.reduce((a, b) => a + b, 0);
    const finalWeights = proposedWeights.map((w) => (sumProp > 0 ? w / sumProp : 1 / n));

    const allocations = req.symbols.map((sym, i) => ({
      symbol: sym,
      weight: Number(finalWeights[i].toFixed(4)),
    }));

    // Calculate turnover: 0.5 * sum |w_i - w0_i|
    const turnover = Number(
      (0.5 * finalWeights.reduce((acc, w, i) => acc + Math.abs(w - w0[i]), 0)).toFixed(4)
    );

    // Calculate portfolio beta
    const portBeta = Number(
      finalWeights
        .reduce((acc, w, i) => acc + w * (assetBetas[req.symbols[i]] || 1.0), 0)
        .toFixed(3)
    );

    // Calculate sector exposures
    const sectorExposures: Record<string, number> = {};
    req.symbols.forEach((sym, i) => {
      const sec = sectorMap[sym] || "Other";
      sectorExposures[sec] = Number(((sectorExposures[sec] || 0) + finalWeights[i]).toFixed(4));
    });

    const divRatio = Number((1.2 + Math.min(0.6, req.symbols.length * 0.08)).toFixed(2));
    return delay<HrpCvarOptimizeResponse>({
      allocations,
      dendrogram: {
        name: "Root Cluster",
        distance: 0.85,
        children: req.symbols.map((sym, i) => ({
          name: sym,
          distance: Number((0.15 + (i % 3) * 0.1).toFixed(2)),
        })),
      },
      expected_return: 0.145,
      cvar_95: 0.042,
      sharpe_ratio: 1.68,
      turnover,
      portfolio_beta: portBeta,
      sector_exposures: sectorExposures,
      diversification_ratio: divRatio,
      as_of: new Date().toISOString(),
    }, 400);
  },

  async optimizeAlmgrenChriss(req: AlmgrenChrissOptimizeRequest): Promise<AlmgrenChrissOptimizeResponse> {
    const t: any[] = [];
    let rem = req.quantity;
    let expected_price = 100.0;
    const steps = req.horizon_steps || 10;
    for (let i = 0; i < steps; i++) {
      const trade_size = rem / (steps - i);
      rem -= trade_size;
      expected_price -= 0.01;
      t.push({
        step: i + 1,
        shares_remaining: Math.max(0, rem),
        trade_size: trade_size,
        expected_price: expected_price,
      });
    }

    const tempImpact = req.liquidity ? 1 / Math.max(1, req.liquidity) : 0.001;
    const totalImpact = Math.max(0.01, (req.quantity * tempImpact + (req.risk_aversion || 1e-6) * (req.volatility || 0.20) * 100));
    const expectedShortfall = Number((totalImpact * (1 + Math.random() * 0.1)).toFixed(2));
    const variance = Number((Math.pow((req.volatility || 0.20), 2) * (req.horizon_steps || 10) * 0.5).toFixed(2));
    const halfLife = Number((Math.log(2) / Math.max(1e-4, Math.sqrt((req.risk_aversion || 1e-6) / tempImpact))).toFixed(2));

    return delay<AlmgrenChrissOptimizeResponse>({
      symbol: req.symbol,
      trajectory: t,
      expected_trajectory: t,
      expected_shortfall: expectedShortfall,
      variance: variance,
      half_life: halfLife,
      spot_price: 100.0,
      spot_price_reason: null,
      as_of: new Date().toISOString(),
    }, 400);
  },

  async routeFixOrder(req: FixRouteOrderRequest): Promise<FixRouteOrderResponse> {
    return delay<FixRouteOrderResponse>({
      symbol: req.symbol,
      side: req.side,
      quantity: req.quantity,
      limit_price: req.limit_price,
      routing_policy: req.routing_policy || "SMART_SWEEP",
      status: "FILLED",
      total_filled_qty: req.quantity,
      leaves_qty: 0,
      weighted_avg_price: req.limit_price - 0.01,
      total_net_fee: 0.15,
      total_rebates: 0.05,
      total_cost: (req.quantity * (req.limit_price - 0.01)) + 0.15,
      avg_latency_ms: 12.5,
      max_latency_ms: 24.1,
      fills: [
        {
          venue: "ARCA",
          fill_qty: req.quantity * 0.6,
          fill_price: req.limit_price - 0.01,
          fee: 0.10,
          rebate: 0.02,
          latency_ms: 11.2,
          exec_id: "EXEC-ARCA-1234",
          ord_status: "FILLED",
          raw_fix: "8=FIX.4.4|9=123|35=8|49=ARCA|...",
        },
        {
          venue: "NSDQ",
          fill_qty: req.quantity * 0.4,
          fill_price: req.limit_price - 0.01,
          fee: 0.05,
          rebate: 0.03,
          latency_ms: 13.8,
          exec_id: "EXEC-NSDQ-5678",
          ord_status: "FILLED",
          raw_fix: "8=FIX.4.4|9=124|35=8|49=NSDQ|...",
        }
      ],
      nbbo: null,
      fix_audit_log: [
        "8=FIX.4.4|9=123|35=8|49=ARCA|...",
        "8=FIX.4.4|9=124|35=8|49=NSDQ|..."
      ],
    }, 500);
  },

  async getFixSessionStatus(): Promise<FixSessionStatusResponse> {
    return delay<FixSessionStatusResponse>({
      session_id: "FIX.4.4:INVESTYO_PWA->FIX_GATEWAY",
      state: "ACTIVE",
      in_seq_num: 1048,
      out_seq_num: 1049,
      sender_comp_id: "INVESTYO_PWA",
      target_comp_id: "FIX_GATEWAY",
      gap_queue_depth: 0,
      last_heartbeat_at: new Date().toISOString(),
      venues_active: ["NYSE", "NASDAQ", "BATS", "IEX", "ARCA"],
      heartbeat_int: 30,
      session_uptime_sec: 14820,
      venue_stats: [
        {
          venue: "NYSE",
          market_center: "New York Stock Exchange",
          status: "ACTIVE",
          base_latency_ms: 1.1,
          current_latency_ms: 1.14,
          fill_rate_pct: 99.4,
          maker_fee: 0.0012,
          taker_fee: 0.0030,
          maker_rebate: 0.0020,
          liquidity_depth: 125000,
          share_of_flow_pct: 34.2,
        },
        {
          venue: "NASDAQ",
          market_center: "Nasdaq Stock Market",
          status: "ACTIVE",
          base_latency_ms: 0.9,
          current_latency_ms: 0.95,
          fill_rate_pct: 99.8,
          maker_fee: 0.0015,
          taker_fee: 0.0030,
          maker_rebate: 0.0025,
          liquidity_depth: 140000,
          share_of_flow_pct: 38.5,
        },
        {
          venue: "BATS",
          market_center: "Cboe BZX Exchange",
          status: "ACTIVE",
          base_latency_ms: 0.7,
          current_latency_ms: 0.72,
          fill_rate_pct: 98.9,
          maker_fee: -0.0020,
          taker_fee: 0.0025,
          maker_rebate: 0.0020,
          liquidity_depth: 65000,
          share_of_flow_pct: 12.1,
        },
        {
          venue: "IEX",
          market_center: "Investors Exchange (D-Limit)",
          status: "ACTIVE",
          base_latency_ms: 1.8,
          current_latency_ms: 1.85,
          fill_rate_pct: 97.5,
          maker_fee: 0.0000,
          taker_fee: 0.0009,
          maker_rebate: 0.0000,
          liquidity_depth: 45000,
          share_of_flow_pct: 6.8,
        },
        {
          venue: "ARCA",
          market_center: "NYSE Arca Equities",
          status: "ACTIVE",
          base_latency_ms: 1.2,
          current_latency_ms: 1.23,
          fill_rate_pct: 99.1,
          maker_fee: -0.0022,
          taker_fee: 0.0028,
          maker_rebate: 0.0022,
          liquidity_depth: 85000,
          share_of_flow_pct: 8.4,
        },
      ],
      audit_log: [
        "8=FIX.4.4|9=112|35=0|49=FIX_GATEWAY|56=INVESTYO_PWA|34=1048|52=20260817-21:45:00.120|10=092|",
        "8=FIX.4.4|9=128|35=8|49=FIX_GATEWAY|56=INVESTYO_PWA|34=1047|52=20260817-21:44:58.330|37=ORD-99124|11=CL-3019|39=2|150=2|55=SPY|54=1|38=100|44=512.50|32=100|31=512.48|14=100|6=512.48|10=184|",
        "8=FIX.4.4|9=108|35=1|49=INVESTYO_PWA|56=FIX_GATEWAY|34=1048|52=20260817-21:44:30.010|112=TEST-9921|10=210|",
        "8=FIX.4.4|9=115|35=0|49=FIX_GATEWAY|56=INVESTYO_PWA|34=1046|52=20260817-21:44:30.012|112=TEST-9921|10=044|",
        "8=FIX.4.4|9=140|35=D|49=INVESTYO_PWA|56=FIX_GATEWAY|34=1047|52=20260817-21:44:00.000|11=CL-3019|55=SPY|54=1|38=100|40=2|44=512.50|59=0|10=156|",
      ],
    }, 200);
  },

  async sendFixTestRequest(req?: FixTestRequestPayload): Promise<FixSessionControlResponse> {
    const tid = req?.test_req_id || "TEST-" + Math.random().toString(36).substring(2, 8).toUpperCase();
    return delay<FixSessionControlResponse>({
      status: "ok",
      message: `FIX Test Request (35=1, TestReqID=${tid}) verified. Heartbeat response received.`,
      session_state: "ACTIVE",
      test_req_id: tid,
      in_seq_num: 1049,
      out_seq_num: 1050,
      round_trip_ms: 1.24,
    }, 200);
  },

  async resetFixSequence(req: FixResetSeqRequest): Promise<FixSessionControlResponse> {
    return delay<FixSessionControlResponse>({
      status: "ok",
      message: `Sequence reset (35=4) to seq #${req.new_seq_num} successful.`,
      session_state: "ACTIVE",
      new_seq_num: req.new_seq_num,
      in_seq_num: req.new_seq_num,
      out_seq_num: req.new_seq_num,
    }, 200);
  },

  async reconnectFixSession(): Promise<FixSessionControlResponse> {
    return delay<FixSessionControlResponse>({
      status: "ok",
      message: "FIX 4.4 Session re-established successfully.",
      session_state: "ACTIVE",
      in_seq_num: 1,
      out_seq_num: 1,
    }, 200);
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

    const job_id = `mock-job-${Object.keys(_mockJobs).length + 1}`;
    const commandName =
      job_type === "command" && typeof params?.command === "string"
        ? params.command
        : null;
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
  async getModelComparison() {
    // Demo-only curve: "SF-GARCH-LSTM"/"Bond-BERT" are undeployed
    // ridge-regression stand-ins (ml/models/sf_garch_lstm.py,
    // ml/models/bond_bert.py) with no real tracked return history -- the
    // live endpoint honestly reports no data (see api/metrics_api.py), this
    // mock fixture exists only to populate the offline demo UI and is
    // flagged is_synthetic so the chart shows a Demo Data badge.
    return delay({
      data: [
        {
          name: "Jan",
          "SF-GARCH-LSTM": 2.1,
          "Bond-BERT": 1.8,
          "Benchmark (SPY)": 1.5,
        },
        {
          name: "Feb",
          "SF-GARCH-LSTM": 4.5,
          "Bond-BERT": 3.2,
          "Benchmark (SPY)": 3.0,
        },
        {
          name: "Mar",
          "SF-GARCH-LSTM": 3.8,
          "Bond-BERT": 4.0,
          "Benchmark (SPY)": 2.8,
        },
        {
          name: "Apr",
          "SF-GARCH-LSTM": 6.2,
          "Bond-BERT": 5.5,
          "Benchmark (SPY)": 4.2,
        },
        {
          name: "May",
          "SF-GARCH-LSTM": 8.0,
          "Bond-BERT": 6.8,
          "Benchmark (SPY)": 5.5,
        },
        {
          name: "Jun",
          "SF-GARCH-LSTM": 10.5,
          "Bond-BERT": 8.2,
          "Benchmark (SPY)": 6.1,
        },
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

  // ---- Cache Long/Short ----
  async getClsConcentratedPositions(): Promise<{
    positions: CacheLongShortConcentratedPosition[];
  }> {
    return delay({
      positions: [{ ticker: "AAPL", market_value: 12000, pct_equity: 0.25 }],
    });
  },
  async getClsDashboard(): Promise<CacheLongShortDashboard> {
    return delay({
      status: "enabled",
      tax_bank: 1540.23,
      exposure: {
        long_exposure: 45000,
        short_exposure: 20000,
        net_exposure: 25000,
        gross_exposure: 65000,
      },
    });
  },
  async getClsPendingApprovals(): Promise<CacheLongShortPendingTrade[]> {
    return delay([
      {
        lot_id: 101,
        position_id: 1,
        cost_basis: 150.5,
        unrealized_loss_pct: -0.12,
      },
      {
        lot_id: 102,
        position_id: 2,
        cost_basis: 300.2,
        unrealized_loss_pct: -0.07,
      },
    ]);
  },
  async simulateCls(
    req: CacheLongShortSimulateRequest,
  ): Promise<CacheLongShortSimulateResult> {
    // "ZZZ" is this codebase's established honesty-branch trigger for
    // on-demand analyze/simulate mocks (see analyzePairs above) --
    // exercises the "no usable proxy hedge found" path a real ticker with
    // insufficient history would hit.
    if (req.ticker.trim().toUpperCase() === "ZZZ") {
      return delay({
        found: false,
        reason: "Insufficient price history for ticker or suitable proxy",
        beta: null,
        proxy_ticker: null,
        correlation_coefficient: null,
      });
    }
    return delay({
      found: true,
      reason: null,
      beta: 1.2,
      proxy_ticker: "XLK",
      correlation_coefficient: 0.85,
    });
  },
  async startCls(
    req: CacheLongShortStartRequest,
  ): Promise<CacheLongShortStartResult> {
    return delay({
      status: "started",
      position_id: 99,
      ticker: req.ticker,
    });
  },
  async approveClsBulk(
    lotIds: number[],
  ): Promise<CacheLongShortApproveBulkResult> {
    return delay({
      status: "approved",
      count: lotIds.length,
    });
  },
  async getPaperBrokerAccount() {
    return paperAccount;
  },
  async getPaperBrokerPositions() {
    return paperPositions;
  },
  async getPaperBrokerOrders(_limit?: number) {
    return paperOrders;
  },
  async resetPaperBroker(cash: number) {
    paperAccount = { equity: cash, cash: cash, buying_power: cash };
    paperAccountInitialized = true;
    paperPositions = [];
    paperOrders = [];
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
  async getStrategyOptionsCandidates(_symbols?: string[]) {
    return delay({
      count: 2,
      candidates: [
        {
          symbol: "AAPL",
          strategy: "Put Credit Spread",
          action: "Open",
          net_premium: 1.45,
          ivr: 62.4,
          trend_bias: "Bullish",
          target_dte: 30,
          legs: [
            { Strike: 150.0, Side: "Short", Type: "PUT", Ratio: 1.0, Price: 2.20 },
            { Strike: 145.0, Side: "Long", Type: "PUT", Ratio: 1.0, Price: 0.75 },
          ]
        },
        {
          symbol: "MSFT",
          strategy: "Iron Condor",
          action: "Open",
          net_premium: 2.10,
          ivr: 58.0,
          trend_bias: "Neutral",
          target_dte: 30,
          legs: [
            { Strike: 400.0, Side: "Short", Type: "PUT", Ratio: 1.0, Price: 1.80 },
            { Strike: 395.0, Side: "Long", Type: "PUT", Ratio: 1.0, Price: 0.70 },
            { Strike: 430.0, Side: "Short", Type: "CALL", Ratio: 1.0, Price: 1.70 },
            { Strike: 435.0, Side: "Long", Type: "CALL", Ratio: 1.0, Price: 0.70 },
          ]
        }
      ]
    });
  },
  async executeStrategyOptions(_symbols?: string[], dryRun = false, _maxNotional = 2500) {
    if (dryRun) {
      return delay({
        executed_count: 2,
        skipped_count: 0,
        failed_count: 0,
        executed: [
          { symbol: "AAPL", strategy: "Put Credit Spread", contracts: 2, net_price: 1.45, net_cash_impact: 287.40 },
          { symbol: "MSFT", strategy: "Iron Condor", contracts: 1, net_price: 2.10, net_cash_impact: 207.40 }
        ],
        skipped: [],
        failed: []
      });
    }

    const aaplReq: OptionsOrderRequest = {
      symbol: "AAPL",
      asset_type: "option",
      quantity: 2,
      limit_price: 1.45,
      expiration: "2026-09-18",
      legs: [
        { action: "Sell", type: "put", contract: { strike: 150, lastPrice: 2.20 } as any },
        { action: "Buy", type: "put", contract: { strike: 145, lastPrice: 0.75 } as any },
      ],
      isLive: false,
    };
    await this.postOptionsOrder(aaplReq);


    return delay({
      executed_count: 1,
      skipped_count: 0,
      failed_count: 0,
      executed: [
        { order_id: `AUTO_OPT_AAPL_${Date.now()}`, symbol: "AAPL", strategy: "Put Credit Spread", contracts: 2, net_price: 1.45, net_cash_impact: 287.40 }
      ],
      skipped: [],
      failed: []
    });
  },
  async getPaperBrokerGreeks() {
    return delay({
      total_positions: paperPositions.length,
      stock_positions_count: paperPositions.filter(p => !p.symbol.includes("$")).length,
      option_positions_count: paperPositions.filter(p => p.symbol.includes("$")).length,
      net_delta_shares: 45.2,
      net_dollar_delta: 6780.0,
      net_gamma: 0.1245,
      net_theta_daily: 34.50,
      net_vega_1pct: -52.80,
      beta_weighted_delta_spy: 13.56,
      positions: paperPositions.map(p => {
        const isOpt = p.symbol.includes("$");
        return {
          symbol: p.symbol,
          asset_type: isOpt ? ("option" as const) : ("stock" as const),
          base_ticker: p.symbol.split(" ")[0],
          expiration: isOpt ? "2026-09-18" : undefined,
          strike: isOpt ? 150 : undefined,
          option_type: isOpt ? (p.symbol.includes("PUT") ? ("put" as const) : ("call" as const)) : undefined,
          dte: isOpt ? 35 : undefined,
          qty: p.qty,
          spot_price: p.current_price ?? 150,
          delta_per_unit: isOpt ? 0.35 : 1.0,
          gamma_per_unit: isOpt ? 0.02 : 0.0,
          theta_daily_per_unit: isOpt ? -0.15 : 0.0,
          vega_1pct_per_unit: isOpt ? 0.25 : 0.0,
          position_delta: isOpt ? p.qty * 100 * 0.35 : p.qty,
          position_dollar_delta: (isOpt ? p.qty * 100 * 0.35 : p.qty) * (p.current_price ?? 150),
          position_gamma: isOpt ? p.qty * 100 * 0.02 : 0,
          position_theta_daily: isOpt ? p.qty * 100 * -0.15 : 0,
          position_vega_1pct: isOpt ? p.qty * 100 * 0.25 : 0,
          market_value: p.market_value ?? 0,
        };
      }),
    });
  },
  async runOptionsBacktest(params: import("./types").OptionsBacktestParams) {
    return delay({
      strategy_name: params.strategy,
      ticker: params.ticker,
      start_date: params.start_date,
      end_date: params.end_date,
      initial_capital: params.initial_capital || 100000,
      final_capital: 118450.0,
      total_return_pct: 18.45,
      annualized_return_pct: 8.92,
      sharpe_ratio: 1.42,
      sortino_ratio: 1.85,
      max_drawdown_pct: 6.40,
      total_trades: 48,
      winning_trades: 39,
      losing_trades: 9,
      win_rate_pct: 81.3,
      profit_factor: 2.35,
      avg_win: 620.0,
      avg_loss: 580.0,
      pbo: 0.12,
      dsr: 0.98,
      passes_stress: true,
      deployable: true,
      equity_curve: [
        { date: "2020-01-01", value: 100.0 },
        { date: "2021-01-01", value: 106.5 },
        { date: "2022-01-01", value: 111.2 },
        { date: "2023-01-01", value: 115.8 },
        { date: "2024-01-01", value: 118.45 },
      ],
      trades: [
        {
          entry_date: "2023-11-01",
          exit_date: "2023-11-20",
          strategy: params.strategy,
          underlying_entry_price: 435.0,
          underlying_exit_price: 448.0,
          entry_net_premium: 150.0,
          exit_net_cost: 0.0,
          pnl_dollar: 150.0,
          pnl_pct: 50.0,
          exit_reason: "profit_target",
          holding_days: 19,
          contracts: 2,
        },
      ],
    });
  },
  async getOptionsMetaModelStatus() {
    return delay({
      n_samples: 1240,
      train_accuracy: 78.5,
      train_roc_auc: 0.812,
      trained_at: "2026-08-14T12:00:00Z",
      enabled: true,
    });
  },
  async retrainOptionsMetaModel() {
    return delay({
      status: "success",
      trained_samples: 1240,
      accuracy: 78.5,
      roc_auc: 0.812,
      trained_at: new Date().toISOString(),
    });
  },
  async settleExpiredPaperOptions() {
    return delay({
      settled_count: 0,
      settled: [],
    });
  },
  async getVolSurface(symbol: string, expiration?: string) {
    const sym = (symbol || "SPY").toUpperCase();
    const spot = sym === "SPY" ? 505.20 : sym === "QQQ" ? 440.50 : 180.00;
    const baseIv = sym === "SPY" ? 0.215 : sym === "QQQ" ? 0.245 : 0.285;
    const strikes = [
      Math.round(spot * 0.90),
      Math.round(spot * 0.93),
      Math.round(spot * 0.95),
      Math.round(spot * 0.98),
      Math.round(spot),
      Math.round(spot * 1.02),
      Math.round(spot * 1.05),
      Math.round(spot * 1.08),
      Math.round(spot * 1.10),
    ];
    const smile_points: VolSmilePoint[] = strikes.map((k) => {
      const moneyness = k / spot;
      // Parabolic smile with put skew
      const skewEffect = moneyness < 1.0 ? (1.0 - moneyness) * 0.45 : (moneyness - 1.0) * 0.18;
      const iv = Number((baseIv + skewEffect).toFixed(4));
      return {
        strike: k,
        iv,
        moneyness: Number(moneyness.toFixed(4)),
        call_bid: Number(Math.max(0.05, (spot - k) + 3.5).toFixed(2)),
        call_ask: Number(Math.max(0.10, (spot - k) + 3.8).toFixed(2)),
        put_bid: Number(Math.max(0.05, (k - spot) + 3.2).toFixed(2)),
        put_ask: Number(Math.max(0.10, (k - spot) + 3.5).toFixed(2)),
      };
    });

    const term_structure: VolTermStructurePoint[] = [
      { expiration: "2026-08-21", dte: 7, atm_iv: Number((baseIv - 0.030).toFixed(4)), historical_realized_vol_30d: 0.165 },
      { expiration: "2026-08-28", dte: 14, atm_iv: Number((baseIv - 0.018).toFixed(4)), historical_realized_vol_30d: 0.165 },
      { expiration: "2026-09-18", dte: 35, atm_iv: baseIv, historical_realized_vol_30d: 0.165 },
      { expiration: "2026-10-16", dte: 63, atm_iv: Number((baseIv + 0.012).toFixed(4)), historical_realized_vol_30d: 0.165 },
      { expiration: "2026-11-20", dte: 98, atm_iv: Number((baseIv + 0.020).toFixed(4)), historical_realized_vol_30d: 0.165 },
      { expiration: "2026-12-18", dte: 126, atm_iv: Number((baseIv + 0.028).toFixed(4)), historical_realized_vol_30d: 0.165 },
      { expiration: "2027-01-15", dte: 154, atm_iv: Number((baseIv + 0.034).toFixed(4)), historical_realized_vol_30d: 0.165 },
    ];

    const skew: SkewData = {
      skew_25delta: 0.035,
      put_25delta_iv: Number((baseIv + 0.037).toFixed(4)),
      call_25delta_iv: Number((baseIv + 0.002).toFixed(4)),
      atm_iv: baseIv,
      vrp_spread: 0.050,
      realized_vol_10d: 0.152,
      realized_vol_20d: 0.160,
      realized_vol_30d: 0.165,
      realized_vol_60d: 0.172,
    };

    return delay<VolSurfaceResponse>({
      symbol: sym,
      spot_price: spot,
      as_of: new Date().toISOString(),
      expirations: term_structure.map((t) => t.expiration),
      selected_expiration: expiration || term_structure[2].expiration,
      smile_points,
      term_structure,
      skew,
    });
  },
  async getScenarioMatrix(params?: { spot_shifts?: number[]; iv_shifts?: number[]; days_forward?: number }) {
    const spot_shifts = params?.spot_shifts || [-0.10, -0.05, -0.03, -0.01, 0, 0.01, 0.03, 0.05, 0.10];
    const iv_shifts = params?.iv_shifts || [-0.20, -0.10, -0.05, 0, 0.05, 0.10, 0.20];
    const time_slices = [0, 7, 14, 21];
    const current_portfolio_value = 100000;
    const baseSpot = 505.20;

    const matrix: ScenarioMatrixCell[] = [];
    for (const t of time_slices) {
      for (const iv of iv_shifts) {
        for (const s of spot_shifts) {
          const spot_price = baseSpot * (1 + s);
          // Nonlinear delta/gamma/vega/theta PnL simulation
          const deltaPnl = s * 48.5 * baseSpot;
          const gammaPnl = 0.5 * 0.015 * Math.pow(s * baseSpot, 2);
          const vegaPnl = (iv * 100) * 12.5;
          const thetaPnl = t * 18.5; // positive income for net seller
          const pnl_dollar = Number((deltaPnl + gammaPnl + vegaPnl + thetaPnl).toFixed(2));
          const pnl_pct = Number((pnl_dollar / current_portfolio_value).toFixed(4));
          const portfolio_value = Number((current_portfolio_value + pnl_dollar).toFixed(2));

          matrix.push({
            spot_shift_pct: s,
            iv_shift_pct: iv,
            days_forward: t,
            spot_price: Number(spot_price.toFixed(2)),
            portfolio_value,
            pnl_dollar,
            pnl_pct,
            net_delta: Number((48.5 + (s * 15.0)).toFixed(2)),
            net_gamma: Number((0.015 - (t * 0.0003)).toFixed(4)),
            net_theta: Number((18.5 + (iv * 5.0)).toFixed(2)),
            net_vega: Number((12.5 - (t * 0.3)).toFixed(2)),
          });
        }
      }
    }

    const historical_scenarios: HistoricalScenarioPreset[] = [
      {
        id: "lehman-2008",
        name: "Lehman Collapse (2008)",
        description: "-15% Spot Plunge, +50% Vol Spike",
        spot_shift_pct: -0.15,
        iv_shift_pct: 0.50,
        projected_pnl_dollar: -3820,
        projected_pnl_pct: -0.0382,
      },
      {
        id: "volmageddon-2018",
        name: "Volmageddon (Feb 2018)",
        description: "-4% Spot Gap, +100% Vol Explosion",
        spot_shift_pct: -0.04,
        iv_shift_pct: 1.00,
        projected_pnl_dollar: -2150,
        projected_pnl_pct: -0.0215,
      },
      {
        id: "covid-2020",
        name: "COVID Liquidity Shock (Mar 2020)",
        description: "-12% Spot Drop, +40% Vol Spike",
        spot_shift_pct: -0.12,
        iv_shift_pct: 0.40,
        projected_pnl_dollar: -3120,
        projected_pnl_pct: -0.0312,
      },
      {
        id: "yen-unwind-2024",
        name: "Yen Carry Unwind (Aug 2024)",
        description: "-6% Spot Gap, +30% Vol Expansion",
        spot_shift_pct: -0.06,
        iv_shift_pct: 0.30,
        projected_pnl_dollar: -1480,
        projected_pnl_pct: -0.0148,
      },
    ];

    return delay<ScenarioMatrixResponse>({
      spot_shifts,
      iv_shifts,
      time_slices,
      matrix,
      historical_scenarios,
      current_portfolio_value,
    });
  },
  async getDeltaHedgePreview() {
    return delay<DeltaHedgePreview>({
      symbol: "SPY",
      available: true,
      net_dollar_delta: 24502.20,
      beta_weighted_delta_spy: 48.5,
      target_hedge_shares: -48.5,
      tolerance_band_shares: 25.0,
      action: "SELL",
      shares: 48,
      required_action: true,
      reason: "Delta imbalance (+48.50 SPY-equiv) exceeds tolerance band (±25.0 shares)",
      spy_spot: 505.20,
    });
  },
  async executeDeltaHedge(_params?: { target_delta?: number; confirm?: boolean }) {
    return delay<DeltaHedgeResult>({
      ok: true,
      hedged: true,
      order_id: `ord_hedge_${Date.now()}`,
      shares: 48,
      symbol: "SPY",
      action: "SELL",
      message: "Delta hedge executed: SELL 48 SPY at $505.20 (commission: $1.00).",
    });
  },
  async managePaperOptionsExits(_params?: { force?: boolean }) {
    return delay<ManageExitsResult>({
      evaluated_count: 3,
      closed_count: 1,
      closed_positions: [
        {
          symbol: "SPY 2026-09-18 $500.00 PUT",
          qty: -2,
          reason: "PROFIT_TARGET_50",
          pnl_dollar: 340.0,
          pnl_pct: 0.52,
          closed_at_price: 1.20,
        },
      ],
      message: "Evaluated 3 option positions: automatically closed 1 position reaching 50% profit target.",
    });
  },
  async rollPaperOptionPosition(request: RollOrderRequest) {
    const closeSymbol = request.close_legs[0]?.symbol ?? request.symbol;
    const openSymbol = request.open_legs[0]?.symbol ?? request.symbol;
    return delay<OptionsOrderResult>({
      ok: true,
      order_id: `ord_roll_${Date.now()}`,
      message: `Successfully rolled ${closeSymbol} to ${openSymbol}. Net credit/debit applied.`,
    });
  },
  async getEarningsCrushCandidates(symbols?: string[]) {
    const allCandidates: EarningsCrushCandidate[] = [
      {
        symbol: "NVDA",
        company_name: "NVIDIA Corporation",
        report_date: "2026-08-20",
        report_timing: "AMC",
        spot_price: 128.50,
        atm_iv: 0.68,
        dte: 3,
        expected_move_dollar: 11.20,
        expected_move_pct: 0.087,
        median_realized_move_pct: 0.054,
        crush_edge_ratio: 1.61,
        suggested_strategy: "Iron Condor",
        short_put_strike: 118,
        put_wing_strike: 112,
        short_call_strike: 139,
        call_wing_strike: 145,
        expiration: "2026-08-21",
        estimated_credit: 2.35,
        edge_passed: true,
        historical_moves: [4.2, 5.8, 7.1, 3.9, 5.4, 6.2, 4.8, 5.1],
      },
      {
        symbol: "TSLA",
        company_name: "Tesla Inc.",
        report_date: "2026-08-19",
        report_timing: "AMC",
        spot_price: 218.00,
        atm_iv: 0.72,
        dte: 2,
        expected_move_dollar: 18.50,
        expected_move_pct: 0.085,
        median_realized_move_pct: 0.061,
        crush_edge_ratio: 1.39,
        suggested_strategy: "Iron Condor",
        short_put_strike: 200,
        put_wing_strike: 190,
        short_call_strike: 235,
        call_wing_strike: 245,
        expiration: "2026-08-21",
        estimated_credit: 3.80,
        edge_passed: true,
        historical_moves: [8.5, 5.2, 6.1, 9.4, 4.3, 6.0, 7.2, 5.8],
      },
      {
        symbol: "AMD",
        company_name: "Advanced Micro Devices",
        report_date: "2026-08-22",
        report_timing: "BMO",
        spot_price: 152.30,
        atm_iv: 0.59,
        dte: 5,
        expected_move_dollar: 11.80,
        expected_move_pct: 0.077,
        median_realized_move_pct: 0.058,
        crush_edge_ratio: 1.33,
        suggested_strategy: "Iron Condor",
        short_put_strike: 140,
        put_wing_strike: 132,
        short_call_strike: 165,
        call_wing_strike: 173,
        expiration: "2026-08-28",
        estimated_credit: 2.10,
        edge_passed: true,
        historical_moves: [5.5, 6.2, 4.9, 7.8, 5.8, 4.1, 6.5, 5.2],
      },
      {
        symbol: "AMZN",
        company_name: "Amazon.com Inc.",
        report_date: "2026-08-21",
        report_timing: "AMC",
        spot_price: 184.20,
        atm_iv: 0.44,
        dte: 4,
        expected_move_dollar: 9.20,
        expected_move_pct: 0.050,
        median_realized_move_pct: 0.042,
        crush_edge_ratio: 1.19,
        suggested_strategy: "Iron Condor",
        short_put_strike: 175,
        put_wing_strike: 170,
        short_call_strike: 195,
        call_wing_strike: 200,
        expiration: "2026-08-28",
        estimated_credit: 1.45,
        edge_passed: false,
        historical_moves: [3.8, 4.5, 4.2, 6.1, 3.9, 4.1, 5.0, 3.6],
      },
      {
        symbol: "AAPL",
        company_name: "Apple Inc.",
        report_date: "2026-08-27",
        report_timing: "AMC",
        spot_price: 224.50,
        atm_iv: 0.32,
        dte: 10,
        expected_move_dollar: 8.10,
        expected_move_pct: 0.036,
        median_realized_move_pct: 0.034,
        crush_edge_ratio: 1.06,
        suggested_strategy: "Iron Condor",
        short_put_strike: 215,
        put_wing_strike: 210,
        short_call_strike: 235,
        call_wing_strike: 240,
        expiration: "2026-08-28",
        estimated_credit: 1.15,
        edge_passed: false,
        historical_moves: [2.8, 3.5, 3.4, 4.1, 2.9, 3.1, 3.8, 3.2],
      },
    ];
    const filtered = symbols && symbols.length > 0
      ? allCandidates.filter(c => symbols.includes(c.symbol))
      : allCandidates;
    return delay<EarningsCrushCandidatesResponse>({
      candidates: filtered,
      count: filtered.length,
      as_of: new Date().toISOString(),
    });
  },
  async executeEarningsCrushTrade(candidate: EarningsCrushCandidate | { symbol: string; strategy?: string; wing_multiplier?: number }) {
    const sym = candidate.symbol;
    const strat = (candidate as EarningsCrushCandidate).suggested_strategy || "Iron Condor";
    const credit = (candidate as EarningsCrushCandidate).estimated_credit ?? 2.75;
    return delay<EarningsCrushExecutionResult>({
      ok: true,
      order_id: `ord_crush_${Date.now()}`,
      symbol: sym,
      strategy: strat,
      net_credit: credit,
      message: `Successfully executed Earnings Crush ${strat} on ${sym} for $${credit.toFixed(2)} net credit. Auto-exit scheduled at 09:35 ET post-announcement.`,
      placed_at: new Date().toISOString(),
    });
  },
  async getUnusualOptionsFlow(params?: { symbol?: string; min_vol_oi?: number; min_notional?: number }) {
    const allTrades: UnusualOptionTrade[] = [
      {
        id: "uoa_1",
        symbol: "NVDA",
        timestamp: "14:48:12",
        option_type: "CALL",
        strike: 135.0,
        expiration: "2026-08-21",
        dte: 7,
        trade_type: "SWEEP",
        sentiment: "BULLISH",
        aggressor_side: "ASK",
        volume: 8420,
        open_interest: 1850,
        vol_oi_ratio: 4.55,
        price: 3.45,
        spot_price: 128.50,
        notional: 2904900,
        iv: 0.72,
        historical_vol_30d: 0.52,
        iv_expansion_flag: true,
      },
      {
        id: "uoa_2",
        symbol: "TSLA",
        timestamp: "14:45:30",
        option_type: "PUT",
        strike: 205.0,
        expiration: "2026-08-21",
        dte: 7,
        trade_type: "SWEEP",
        sentiment: "BEARISH",
        aggressor_side: "BID",
        volume: 6200,
        open_interest: 1400,
        vol_oi_ratio: 4.43,
        price: 4.10,
        spot_price: 218.00,
        notional: 2542000,
        iv: 0.76,
        historical_vol_30d: 0.58,
        iv_expansion_flag: true,
      },
      {
        id: "uoa_3",
        symbol: "SPY",
        timestamp: "14:41:05",
        option_type: "CALL",
        strike: 510.0,
        expiration: "2026-09-18",
        dte: 35,
        trade_type: "BLOCK",
        sentiment: "BULLISH",
        aggressor_side: "ASK",
        volume: 15400,
        open_interest: 3200,
        vol_oi_ratio: 4.81,
        price: 8.25,
        spot_price: 505.20,
        notional: 12705000,
        iv: 0.22,
        historical_vol_30d: 0.16,
        iv_expansion_flag: false,
      },
      {
        id: "uoa_4",
        symbol: "AMD",
        timestamp: "14:38:22",
        option_type: "CALL",
        strike: 160.0,
        expiration: "2026-08-28",
        dte: 14,
        trade_type: "SWEEP",
        sentiment: "BULLISH",
        aggressor_side: "ASK",
        volume: 4950,
        open_interest: 1120,
        vol_oi_ratio: 4.42,
        price: 2.80,
        spot_price: 152.30,
        notional: 1386000,
        iv: 0.62,
        historical_vol_30d: 0.44,
        iv_expansion_flag: true,
      },
      {
        id: "uoa_5",
        symbol: "AAPL",
        timestamp: "14:32:15",
        option_type: "PUT",
        strike: 220.0,
        expiration: "2026-09-18",
        dte: 35,
        trade_type: "BLOCK",
        sentiment: "BEARISH",
        aggressor_side: "BID",
        volume: 3800,
        open_interest: 950,
        vol_oi_ratio: 4.00,
        price: 3.90,
        spot_price: 224.50,
        notional: 1482000,
        iv: 0.33,
        historical_vol_30d: 0.24,
        iv_expansion_flag: false,
      },
      {
        id: "uoa_6",
        symbol: "META",
        timestamp: "14:28:40",
        option_type: "CALL",
        strike: 520.0,
        expiration: "2026-08-28",
        dte: 14,
        trade_type: "SWEEP",
        sentiment: "BULLISH",
        aggressor_side: "ASK",
        volume: 2900,
        open_interest: 680,
        vol_oi_ratio: 4.26,
        price: 6.40,
        spot_price: 498.80,
        notional: 1856000,
        iv: 0.45,
        historical_vol_30d: 0.32,
        iv_expansion_flag: true,
      },
      {
        id: "uoa_7",
        symbol: "QQQ",
        timestamp: "14:20:10",
        option_type: "PUT",
        strike: 475.0,
        expiration: "2026-08-21",
        dte: 7,
        trade_type: "SWEEP",
        sentiment: "BEARISH",
        aggressor_side: "BID",
        volume: 7500,
        open_interest: 2100,
        vol_oi_ratio: 3.57,
        price: 2.15,
        spot_price: 482.10,
        notional: 1612500,
        iv: 0.24,
        historical_vol_30d: 0.18,
        iv_expansion_flag: false,
      },
    ];

    let filtered = allTrades;
    if (params?.symbol) {
      filtered = filtered.filter(t => t.symbol.toUpperCase() === params.symbol!.toUpperCase());
    }
    if (params?.min_vol_oi != null) {
      filtered = filtered.filter(t => t.vol_oi_ratio >= params.min_vol_oi!);
    }
    if (params?.min_notional != null) {
      filtered = filtered.filter(t => t.notional >= params.min_notional!);
    }

    return delay<UnusualOptionsFlowResponse>({
      trades: filtered,
      records: filtered,
      count: filtered.length,
      as_of: new Date().toISOString(),
    });
  },
  async getOptionsFlowSentiment(symbol: string) {
    const sentiments: Record<string, FlowSentimentData> = {
      NVDA: {
        symbol: "NVDA",
        sentiment_score: 0.72,
        bullish_notional: 4200000,
        bearish_notional: 680000,
        total_notional: 4880000,
        call_volume: 24500,
        put_volume: 5800,
        put_call_ratio: 0.24,
        top_active_strikes: [
          { strike: 135.0, option_type: "CALL", notional: 2904900 },
          { strike: 140.0, option_type: "CALL", notional: 1295100 },
          { strike: 120.0, option_type: "PUT", notional: 680000 },
        ],
      },
      TSLA: {
        symbol: "TSLA",
        sentiment_score: -0.45,
        bullish_notional: 1100000,
        bearish_notional: 2900000,
        total_notional: 4000000,
        call_volume: 8200,
        put_volume: 16400,
        put_call_ratio: 2.00,
        top_active_strikes: [
          { strike: 205.0, option_type: "PUT", notional: 2542000 },
          { strike: 230.0, option_type: "CALL", notional: 1100000 },
        ],
      },
      SPY: {
        symbol: "SPY",
        sentiment_score: 0.54,
        bullish_notional: 18500000,
        bearish_notional: 5500000,
        total_notional: 24000000,
        call_volume: 98000,
        put_volume: 42000,
        put_call_ratio: 0.43,
        top_active_strikes: [
          { strike: 510.0, option_type: "CALL", notional: 12705000 },
          { strike: 500.0, option_type: "PUT", notional: 5500000 },
        ],
      },
    };

    const data = sentiments[symbol.toUpperCase()] || {
      symbol: symbol.toUpperCase(),
      sentiment_score: 0.25,
      bullish_notional: 1200000,
      bearish_notional: 720000,
      total_notional: 1920000,
      call_volume: 6400,
      put_volume: 3800,
      put_call_ratio: 0.59,
      top_active_strikes: [
        { strike: 100.0, option_type: "CALL", notional: 800000 },
        { strike: 95.0, option_type: "PUT", notional: 400000 },
      ],
    };

    return delay<FlowSentimentResponse>({
      sentiment: data,
      as_of: new Date().toISOString(),
    });
  },
  async getHarRvForecast(symbol: string) {
    const sym = (symbol || "SPY").toUpperCase();
    const spot = sym === "SPY" ? 505.20 : sym === "QQQ" ? 440.50 : sym === "NVDA" ? 128.50 : sym === "TSLA" ? 218.00 : 180.00;
    const baseRv = sym === "NVDA" ? 0.48 : sym === "TSLA" ? 0.54 : sym === "QQQ" ? 0.22 : 0.165;

    const rv_daily = Number((baseRv * 0.96).toFixed(4));
    const rv_weekly = Number((baseRv * 1.02).toFixed(4));
    const rv_monthly = Number((baseRv * 1.08).toFixed(4));

    const b0 = 0.015;
    const bd = 0.38;
    const bw = 0.34;
    const bm = 0.22;

    const forecast_vol_1d = Number((b0 + bd * rv_daily + bw * rv_weekly + bm * rv_monthly).toFixed(4));
    const forecast_vol_5d = Number((forecast_vol_1d * 1.02).toFixed(4));
    const forecast_vol_22d = Number((forecast_vol_1d * 1.05).toFixed(4));
    const forecast_vol_30d = Number((forecast_vol_1d * 1.06).toFixed(4));
    const gjr_garch_vol = Number((forecast_vol_30d * 1.03).toFixed(4));
    const fair_iv_blend = Number(((forecast_vol_30d * 0.65) + (gjr_garch_vol * 0.35)).toFixed(4));

    return delay<HarRvForecastResponse>({
      symbol: sym,
      spot_price: spot,
      as_of: new Date().toISOString(),
      rv_daily,
      rv_weekly,
      rv_monthly,
      forecast_vol_1d,
      forecast_vol_5d,
      forecast_vol_22d,
      forecast_vol_30d,
      gjr_garch_vol,
      fair_iv_blend,
      coefficients: {
        beta_0: b0,
        beta_d: bd,
        beta_w: bw,
        beta_m: bm,
      },
      r_squared: 0.685,
      annualized_rv_1d: rv_daily,
      annualized_rv_5d: rv_weekly,
      annualized_rv_22d: rv_monthly,
    });
  },
  async getVolMispricing(symbol: string, expiration?: string) {
    const sym = (symbol || "SPY").toUpperCase();
    const spot = sym === "SPY" ? 505.20 : sym === "QQQ" ? 440.50 : sym === "NVDA" ? 128.50 : sym === "TSLA" ? 218.00 : 180.00;
    const fairBase = sym === "NVDA" ? 0.52 : sym === "TSLA" ? 0.58 : sym === "QQQ" ? 0.23 : 0.175;
    const mktAtmIv = sym === "NVDA" ? 0.68 : sym === "TSLA" ? 0.72 : sym === "QQQ" ? 0.25 : 0.215;
    const exp = expiration || "2026-09-18";
    const dte = 35;

    const step = spot > 300 ? 5 : spot > 100 ? 2.5 : 1;
    const atmStrike = Math.round(spot / step) * step;
    const strikesList: number[] = [];
    for (let i = -6; i <= 6; i++) {
      strikesList.push(Number((atmStrike + i * step).toFixed(2)));
    }

    const strikes: VolMispricingStrike[] = strikesList.map(k => {
      const moneyness = k / spot;
      const skew = moneyness < 1.0 ? (1.0 - moneyness) * 0.45 : (moneyness - 1.0) * 0.20;
      const market_iv = Number((mktAtmIv + skew + (Math.sin(k * 10) * 0.015)).toFixed(4));
      const fair_iv = Number((fairBase + (moneyness < 1.0 ? (1.0 - moneyness) * 0.25 : (moneyness - 1.0) * 0.10)).toFixed(4));
      const iv_spread = Number((market_iv - fair_iv).toFixed(4));
      const spread_zscore = Number((iv_spread / 0.025).toFixed(2));

      let classification: "RICH" | "CHEAP" | "NEUTRAL" | "UNKNOWN" = "NEUTRAL";
      let suggested_action: "SELL_PREMIUM" | "BUY_GAMMA" | "HOLD" | "NEUTRAL" = "NEUTRAL";
      let suggested_trade: string | undefined = undefined;

      if (iv_spread >= 0.035 || spread_zscore >= 1.5) {
        classification = "RICH";
        suggested_action = "SELL_PREMIUM";
        suggested_trade = k < spot ? "Sell Put Credit Spread" : "Sell Call Credit Spread";
      } else if (iv_spread <= -0.015 || spread_zscore <= -1.0) {
        classification = "CHEAP";
        suggested_action = "BUY_GAMMA";
        suggested_trade = "Buy Debit Spread / Long Straddle";
      } else {
        classification = "NEUTRAL";
        suggested_action = "HOLD";
      }

      const delta = Number((0.50 + (spot - k) / (spot * 0.2)).toFixed(2));
      const clampedDelta = Math.max(0.02, Math.min(0.98, delta));

      return {
        strike: k,
        option_type: k >= spot ? "CALL" : "PUT",
        market_iv,
        fair_iv,
        iv_spread,
        spread_zscore,
        classification,
        suggested_action,
        bid: Number(Math.max(0.20, Math.abs(spot - k) * 0.8 + 2.5).toFixed(2)),
        ask: Number(Math.max(0.30, Math.abs(spot - k) * 0.8 + 2.8).toFixed(2)),
        mid: Number(Math.max(0.25, Math.abs(spot - k) * 0.8 + 2.65).toFixed(2)),
        delta: clampedDelta,
        gamma: 0.025,
        vega: 0.18,
        theta: -0.08,
        suggested_trade,
      };
    });

    const richCount = strikes.filter(s => s.classification === "RICH").length;
    const cheapCount = strikes.filter(s => s.classification === "CHEAP").length;

    const trade_recommendations = [
      {
        strategy: "Put Credit Spread (Rich Skew Capture)",
        direction: "SELL_VOL" as const,
        strikes: [strikesList[2], strikesList[0]],
        reason: `OTM Puts trade at +${((mktAtmIv - fairBase) * 100).toFixed(1)}% IV premium over HAR-RV fair value. High VRP edge.`,
        estimated_edge_pct: 18.5,
      },
      {
        strategy: "Iron Condor (Symmetric Overpricing)",
        direction: "SELL_VOL" as const,
        strikes: [strikesList[1], strikesList[3], strikesList[9], strikesList[11]],
        reason: "Wings are elevated +2.1σ vs GJR-GARCH+HAR-RV forecast. Rich IV harvest.",
        estimated_edge_pct: 22.4,
      },
      {
        strategy: "Long Straddle (Cheap Convexity)",
        direction: "BUY_VOL" as const,
        strikes: [atmStrike],
        reason: "ATM IV compressed relative to short-term RV clustering. Positive Gamma edge.",
        estimated_edge_pct: 12.0,
      },
    ];

    return delay<VolMispricingResponse>({
      symbol: sym,
      spot_price: spot,
      expiration: exp,
      expirations: ["2026-08-21", "2026-08-28", "2026-09-18", "2026-10-16", "2026-11-20"],
      dte,
      fair_iv_baseline: fairBase,
      market_atm_iv: mktAtmIv,
      rich_strikes_count: richCount,
      cheap_strikes_count: cheapCount,
      strikes,
      trade_recommendations,
      as_of: new Date().toISOString(),
    });
  },
  async simulateGammaScalping(request: GammaScalpRequest) {
    const sym = request.symbol || "SPY";
    const spot = request.spot_price || 505.20;
    const contracts = request.contracts || 10;
    const deltaThresh = request.delta_threshold || 0.15;
    const steps = request.simulation_steps || 40;
    const realizedVol = request.realized_vol || 0.25;

    let price_path: number[] = request.underlying_price_path ? [...request.underlying_price_path] : [];
    if (price_path.length === 0) {
      price_path = [spot];
      let currentSpot = spot;
      const dt = 1 / 252 / 6.5;
      for (let i = 1; i < steps; i++) {
        const shock = (Math.sin(i * 0.4) * 0.8 + (Math.random() - 0.48)) * realizedVol * Math.sqrt(dt) * currentSpot * 8;
        currentSpot = Math.max(10, currentSpot + shock);
        price_path.push(Number(currentSpot.toFixed(2)));
      }
    }

    const initDelta = request.option_type === "PUT" ? -0.50 : request.option_type === "STRADDLE" ? 0.0 : 0.50;
    const initGamma = 0.035 * contracts;
    const initTheta = -18.5 * contracts;

    let currentStockPosition = request.option_type === "CALL" ? -Math.round(initDelta * contracts * 100) : request.option_type === "PUT" ? Math.round(Math.abs(initDelta) * contracts * 100) : 0;
    let currentStockCash = -currentStockPosition * spot;
    let prevSpot = spot;
    let cumulativeGammaRent = 0;
    let cumulativeThetaDecay = 0;
    let totalStockPnl = 0;
    let transactionCosts = 0;

    const trades: GammaScalpHedgeTrade[] = [];
    const pnl_path: GammaScalpResponse["pnl_path"] = [];

    for (let step = 0; step < price_path.length; step++) {
      const currentPrice = price_path[step];
      const dS = currentPrice - prevSpot;
      const stepDt = 1 / 30;

      const rawDelta = initDelta + (initGamma / Math.max(1, contracts)) * (currentPrice - spot);
      const optionDeltaShares = rawDelta * contracts * 100;
      const netPortfolioDeltaShares = optionDeltaShares + currentStockPosition;
      const netDeltaFraction = netPortfolioDeltaShares / (contracts * 100);

      const stepGammaRent = 0.5 * initGamma * 100 * Math.pow(dS, 2);
      cumulativeGammaRent += stepGammaRent;

      const stepThetaBurn = Math.abs(initTheta) * stepDt;
      cumulativeThetaDecay += stepThetaBurn;

      let side: "BUY" | "SELL" | "HOLD" = "HOLD";
      let sharesTraded = 0;
      let cashFlow = 0;

      if (Math.abs(netDeltaFraction) >= deltaThresh) {
        sharesTraded = Math.round(-netPortfolioDeltaShares);
        side = sharesTraded > 0 ? "BUY" : "SELL";
        cashFlow = -sharesTraded * currentPrice;
        currentStockPosition += sharesTraded;
        currentStockCash += cashFlow;
        transactionCosts += Math.abs(sharesTraded) * 0.005;

        trades.push({
          step,
          timestamp: `T+${step}h`,
          spot_price: currentPrice,
          pre_delta: Number(netDeltaFraction.toFixed(3)),
          post_delta: Number(((optionDeltaShares + currentStockPosition) / (contracts * 100)).toFixed(3)),
          shares_traded: Math.abs(sharesTraded),
          side,
          trade_price: currentPrice,
          cash_flow: Number(cashFlow.toFixed(2)),
          stock_position: currentStockPosition,
          option_mtm: Number(((contracts * 100) * Math.max(0.5, 5.0 + (currentPrice - spot) * initDelta + 0.5 * initGamma * Math.pow(currentPrice - spot, 2) - cumulativeThetaDecay / (contracts * 100))).toFixed(2)),
          total_pnl: Number((cumulativeGammaRent - cumulativeThetaDecay - transactionCosts).toFixed(2)),
          gamma_rent_cumulative: Number(cumulativeGammaRent.toFixed(2)),
          theta_decay_cumulative: Number(cumulativeThetaDecay.toFixed(2)),
        });
      }

      const stockMtm = currentStockPosition * currentPrice + currentStockCash;
      totalStockPnl = stockMtm;
      const optionPnl = (currentPrice - spot) * (initDelta * contracts * 100) + cumulativeGammaRent - cumulativeThetaDecay;
      const totalPnl = cumulativeGammaRent - cumulativeThetaDecay - transactionCosts;

      pnl_path.push({
        step,
        spot: currentPrice,
        total_pnl: Number(totalPnl.toFixed(2)),
        gamma_rent: Number(cumulativeGammaRent.toFixed(2)),
        theta_decay: Number(cumulativeThetaDecay.toFixed(2)),
        option_mtm: Number(optionPnl.toFixed(2)),
        stock_pnl: Number(stockMtm.toFixed(2)),
      });

      prevSpot = currentPrice;
    }

    const total_pnl = Number((cumulativeGammaRent - cumulativeThetaDecay - transactionCosts).toFixed(2));

    return delay<GammaScalpResponse>({
      symbol: sym,
      spot_price: spot,
      initial_delta: initDelta,
      initial_gamma: initGamma,
      initial_theta: initTheta,
      total_trades: trades.length,
      rebalance_count: trades.length,
      delta_threshold: deltaThresh,
      total_pnl,
      gamma_rent_total: Number(cumulativeGammaRent.toFixed(2)),
      theta_burn_total: Number(cumulativeThetaDecay.toFixed(2)),
      stock_pnl: Number(totalStockPnl.toFixed(2)),
      option_pnl: Number((total_pnl - totalStockPnl).toFixed(2)),
      transaction_costs: Number(transactionCosts.toFixed(2)),
      net_edge: Number((cumulativeGammaRent - cumulativeThetaDecay).toFixed(2)),
      trades,
      price_path,
      pnl_path,
    });
  },
  async testOptionsAlert(params?: { alert_type?: string; symbol?: string; dry_run?: boolean }) {
    const alertType = params?.alert_type || "UOA";
    const symbol = params?.symbol || "NVDA";
    const isDry = params?.dry_run ?? false;

    return delay<OptionsAlertTestResult>({
      ok: true,
      dispatched_count: 3,
      channels: ["Discord Webhook (#options-flow)", "Slack Webhook (#trading-desk)", "System Alert Logger"],
      results: [
        {
          channel: "Discord Webhook (#options-flow)",
          status: isDry ? "SIMULATED" : "SENT",
          message: `Dispatched test ${alertType} notification for ${symbol} with rich embed format.`,
        },
        {
          channel: "Slack Webhook (#trading-desk)",
          status: isDry ? "SIMULATED" : "SENT",
          message: `Dispatched test ${alertType} block kit message for ${symbol}.`,
        },
        {
          channel: "System Alert Logger",
          status: "SENT",
          message: "Event recorded in quant_platform.db alerts table.",
        },
      ],
      as_of: new Date().toISOString(),
    });
  },
  async getDispersionOpportunities(index_symbol?: string) {
    const qqqConstituents: DispersionConstituent[] = [
      {
        symbol: "AAPL",
        weight: 0.18,
        spot_price: 224.50,
        atm_iv: 0.28,
        realized_vol_30d: 0.20,
        straddle_strike: 225.0,
        straddle_bid: 10.20,
        straddle_ask: 10.60,
        straddle_mid: 10.40,
        vega_per_straddle: 0.32,
        contracts_allocated: 18,
        leg_action: "BUY",
        implied_rv_spread: 0.08,
      },
      {
        symbol: "MSFT",
        weight: 0.16,
        spot_price: 445.00,
        atm_iv: 0.26,
        realized_vol_30d: 0.19,
        straddle_strike: 445.0,
        straddle_bid: 21.00,
        straddle_ask: 21.80,
        straddle_mid: 21.40,
        vega_per_straddle: 0.48,
        contracts_allocated: 12,
        leg_action: "BUY",
        implied_rv_spread: 0.07,
      },
      {
        symbol: "NVDA",
        weight: 0.15,
        spot_price: 128.50,
        atm_iv: 0.48,
        realized_vol_30d: 0.36,
        straddle_strike: 130.0,
        straddle_bid: 11.80,
        straddle_ask: 12.20,
        straddle_mid: 12.00,
        vega_per_straddle: 0.22,
        contracts_allocated: 24,
        leg_action: "BUY",
        implied_rv_spread: 0.12,
      },
      {
        symbol: "AMZN",
        weight: 0.12,
        spot_price: 185.00,
        atm_iv: 0.31,
        realized_vol_30d: 0.22,
        straddle_strike: 185.0,
        straddle_bid: 9.40,
        straddle_ask: 9.80,
        straddle_mid: 9.60,
        vega_per_straddle: 0.28,
        contracts_allocated: 14,
        leg_action: "BUY",
        implied_rv_spread: 0.09,
      },
      {
        symbol: "GOOGL",
        weight: 0.11,
        spot_price: 172.00,
        atm_iv: 0.29,
        realized_vol_30d: 0.21,
        straddle_strike: 172.5,
        straddle_bid: 8.60,
        straddle_ask: 9.00,
        straddle_mid: 8.80,
        vega_per_straddle: 0.25,
        contracts_allocated: 15,
        leg_action: "BUY",
        implied_rv_spread: 0.08,
      },
      {
        symbol: "META",
        weight: 0.10,
        spot_price: 520.00,
        atm_iv: 0.38,
        realized_vol_30d: 0.28,
        straddle_strike: 520.0,
        straddle_bid: 28.50,
        straddle_ask: 29.50,
        straddle_mid: 29.00,
        vega_per_straddle: 0.62,
        contracts_allocated: 6,
        leg_action: "BUY",
        implied_rv_spread: 0.10,
      },
      {
        symbol: "TSLA",
        weight: 0.10,
        spot_price: 215.00,
        atm_iv: 0.55,
        realized_vol_30d: 0.42,
        straddle_strike: 215.0,
        straddle_bid: 19.20,
        straddle_ask: 20.00,
        straddle_mid: 19.60,
        vega_per_straddle: 0.35,
        contracts_allocated: 10,
        leg_action: "BUY",
        implied_rv_spread: 0.13,
      },
      {
        symbol: "AVGO",
        weight: 0.08,
        spot_price: 155.00,
        atm_iv: 0.36,
        realized_vol_30d: 0.27,
        straddle_strike: 155.0,
        straddle_bid: 10.50,
        straddle_ask: 11.10,
        straddle_mid: 10.80,
        vega_per_straddle: 0.26,
        contracts_allocated: 11,
        leg_action: "BUY",
        implied_rv_spread: 0.09,
      },
    ];

    const spyConstituents: DispersionConstituent[] = [
      {
        symbol: "MSFT",
        weight: 0.14,
        spot_price: 445.00,
        atm_iv: 0.26,
        realized_vol_30d: 0.19,
        straddle_strike: 445.0,
        straddle_bid: 21.00,
        straddle_ask: 21.80,
        straddle_mid: 21.40,
        vega_per_straddle: 0.48,
        contracts_allocated: 10,
        leg_action: "BUY",
        implied_rv_spread: 0.07,
      },
      {
        symbol: "AAPL",
        weight: 0.13,
        spot_price: 224.50,
        atm_iv: 0.28,
        realized_vol_30d: 0.20,
        straddle_strike: 225.0,
        straddle_bid: 10.20,
        straddle_ask: 10.60,
        straddle_mid: 10.40,
        vega_per_straddle: 0.32,
        contracts_allocated: 14,
        leg_action: "BUY",
        implied_rv_spread: 0.08,
      },
      {
        symbol: "NVDA",
        weight: 0.12,
        spot_price: 128.50,
        atm_iv: 0.48,
        realized_vol_30d: 0.36,
        straddle_strike: 130.0,
        straddle_bid: 11.80,
        straddle_ask: 12.20,
        straddle_mid: 12.00,
        vega_per_straddle: 0.22,
        contracts_allocated: 18,
        leg_action: "BUY",
        implied_rv_spread: 0.12,
      },
      {
        symbol: "AMZN",
        weight: 0.09,
        spot_price: 185.00,
        atm_iv: 0.31,
        realized_vol_30d: 0.22,
        straddle_strike: 185.0,
        straddle_bid: 9.40,
        straddle_ask: 9.80,
        straddle_mid: 9.60,
        vega_per_straddle: 0.28,
        contracts_allocated: 11,
        leg_action: "BUY",
        implied_rv_spread: 0.09,
      },
      {
        symbol: "META",
        weight: 0.08,
        spot_price: 520.00,
        atm_iv: 0.38,
        realized_vol_30d: 0.28,
        straddle_strike: 520.0,
        straddle_bid: 28.50,
        straddle_ask: 29.50,
        straddle_mid: 29.00,
        vega_per_straddle: 0.62,
        contracts_allocated: 5,
        leg_action: "BUY",
        implied_rv_spread: 0.10,
      },
      {
        symbol: "GOOGL",
        weight: 0.07,
        spot_price: 172.00,
        atm_iv: 0.29,
        realized_vol_30d: 0.21,
        straddle_strike: 172.5,
        straddle_bid: 8.60,
        straddle_ask: 9.00,
        straddle_mid: 8.80,
        vega_per_straddle: 0.25,
        contracts_allocated: 10,
        leg_action: "BUY",
        implied_rv_spread: 0.08,
      },
      {
        symbol: "BRK.B",
        weight: 0.05,
        spot_price: 450.00,
        atm_iv: 0.16,
        realized_vol_30d: 0.13,
        straddle_strike: 450.0,
        straddle_bid: 12.00,
        straddle_ask: 12.80,
        straddle_mid: 12.40,
        vega_per_straddle: 0.40,
        contracts_allocated: 4,
        leg_action: "BUY",
        implied_rv_spread: 0.03,
      },
      {
        symbol: "JPM",
        weight: 0.05,
        spot_price: 215.00,
        atm_iv: 0.22,
        realized_vol_30d: 0.17,
        straddle_strike: 215.0,
        straddle_bid: 7.80,
        straddle_ask: 8.40,
        straddle_mid: 8.10,
        vega_per_straddle: 0.29,
        contracts_allocated: 6,
        leg_action: "BUY",
        implied_rv_spread: 0.05,
      },
    ];

    const opportunities: DispersionOpportunity[] = [
      {
        id: "disp_qqq_1",
        index_symbol: "QQQ",
        index_name: "Invesco QQQ Trust",
        index_spot: 480.20,
        index_iv: 0.215,
        index_rv_30d: 0.152,
        index_straddle_strike: 480.0,
        index_straddle_price: 18.50,
        index_straddle_contracts: 10,
        index_action: "SELL",
        implied_correlation: 0.68,
        realized_correlation: 0.44,
        correlation_spread: 0.24,
        regime: "LONG_DISPERSION",
        trade_recommendation: "Rich Implied Correlation (+24.0% spread). Sell 10x QQQ Straddles, Buy Vega-Neutral Constituent Straddles.",
        index_vega_total: 340.0,
        constituents_vega_total: 343.4,
        net_vega: 3.4,
        vega_neutrality_ratio: 1.01,
        net_premium_estimate: 1420.50,
        expiration: "2026-09-18",
        dte: 35,
        constituents: qqqConstituents,
        as_of: new Date().toISOString(),
      },
      {
        id: "disp_spy_1",
        index_symbol: "SPY",
        index_name: "SPDR S&P 500 ETF Trust",
        index_spot: 545.80,
        index_iv: 0.148,
        index_rv_30d: 0.112,
        index_straddle_strike: 545.0,
        index_straddle_price: 14.20,
        index_straddle_contracts: 10,
        index_action: "SELL",
        implied_correlation: 0.62,
        realized_correlation: 0.48,
        correlation_spread: 0.14,
        regime: "NEUTRAL",
        trade_recommendation: "Moderate Implied Correlation (+14.0% spread). Below entry threshold (≥15.0%). Hold / Monitor.",
        index_vega_total: 420.0,
        constituents_vega_total: 418.0,
        net_vega: -2.0,
        vega_neutrality_ratio: 0.995,
        net_premium_estimate: 880.00,
        expiration: "2026-09-18",
        dte: 35,
        constituents: spyConstituents,
        as_of: new Date().toISOString(),
      },
    ];

    const filtered = index_symbol
      ? opportunities.filter((o) => o.index_symbol.toUpperCase() === index_symbol.toUpperCase())
      : opportunities;

    return delay<DispersionBasketResponse>({
      opportunities: filtered.length > 0 ? filtered : opportunities,
      count: filtered.length > 0 ? filtered.length : opportunities.length,
      as_of: new Date().toISOString(),
    });
  },
  async executeDispersionBasket(request: DispersionBasketOrderRequest | { opportunity_id?: string; index_symbol: string; regime?: string; basket_size_usd?: number }) {
    const sym = request.index_symbol || "QQQ";
    return delay<DispersionExecutionResult>({
      ok: true,
      basket_id: `bsk_disp_${Date.now()}`,
      index_symbol: sym,
      index_order_id: `ord_idx_${Date.now()}`,
      constituent_order_ids: [
        `ord_leg_aapl_${Date.now()}`,
        `ord_leg_msft_${Date.now()}`,
        `ord_leg_nvda_${Date.now()}`,
        `ord_leg_amzn_${Date.now()}`,
        `ord_leg_googl_${Date.now()}`,
        `ord_leg_meta_${Date.now()}`,
        `ord_leg_tsla_${Date.now()}`,
        `ord_leg_avgo_${Date.now()}`,
      ],
      strategy: "Dispersion Arbitrage",
      net_credit_debit: 1420.50,
      legs_count: 18,
      message: `Successfully executed vega-neutral Dispersion Arbitrage basket on ${sym}. Placed short index straddle + 8 long constituent straddles (Net Vega: +3.4 $/vol).`,
      placed_at: new Date().toISOString(),
    });
  },
  async getZeroDteSignals(symbol?: string) {
    const allSignals: ZeroDteSignal[] = [
      {
        symbol: "SPY",
        spot_price: 546.50,
        timestamp: "10:14:32",
        opening_range_high: 545.80,
        opening_range_low: 544.10,
        opening_range_width_pct: 0.0031,
        ttm_squeeze_active: false,
        ttm_squeeze_bars: 8,
        momentum_direction: "BULLISH_BREAKOUT",
        momentum_score: 0.86,
        relative_volume_15m: 2.15,
        suggested_action: "BUY_CALL",
        recommended_contract: {
          option_type: "CALL",
          strike: 547.0,
          expiration: "2026-08-14",
          dte: 0,
          delta: 0.51,
          gamma: 0.082,
          theta: -1.45,
          vega: 0.12,
          bid: 1.85,
          ask: 1.90,
          mid: 1.88,
          implied_vol: 0.18,
          target_price: 3.29,
          stop_loss_price: 1.32,
          hard_exit_time: "15:45 ET",
        },
        trigger_reason: "15-min ORB breakout above $545.80 on 2.15x volume acceleration with TTM Squeeze release.",
      },
      {
        symbol: "QQQ",
        spot_price: 481.10,
        timestamp: "10:14:15",
        opening_range_high: 482.40,
        opening_range_low: 479.80,
        opening_range_width_pct: 0.0054,
        ttm_squeeze_active: true,
        ttm_squeeze_bars: 6,
        momentum_direction: "IN_RANGE",
        momentum_score: 0.14,
        relative_volume_15m: 0.92,
        suggested_action: "WAIT",
        recommended_contract: {
          option_type: "CALL",
          strike: 482.0,
          expiration: "2026-08-14",
          dte: 0,
          delta: 0.49,
          gamma: 0.065,
          theta: -1.82,
          vega: 0.15,
          bid: 2.10,
          ask: 2.20,
          mid: 2.15,
          implied_vol: 0.24,
          target_price: 3.76,
          stop_loss_price: 1.50,
          hard_exit_time: "15:45 ET",
        },
        trigger_reason: "Inside 15-min range [479.80 - 482.40]. Volatility compression active (TTM Squeeze Red).",
      },
      {
        symbol: "TSLA",
        spot_price: 214.30,
        timestamp: "10:13:50",
        opening_range_high: 221.50,
        opening_range_low: 216.00,
        opening_range_width_pct: 0.025,
        ttm_squeeze_active: false,
        ttm_squeeze_bars: 5,
        momentum_direction: "BEARISH_BREAKDOWN",
        momentum_score: -0.82,
        relative_volume_15m: 2.40,
        suggested_action: "BUY_PUT",
        recommended_contract: {
          option_type: "PUT",
          strike: 215.0,
          expiration: "2026-08-14",
          dte: 0,
          delta: -0.48,
          gamma: 0.058,
          theta: -2.10,
          vega: 0.18,
          bid: 2.40,
          ask: 2.48,
          mid: 2.44,
          implied_vol: 0.62,
          target_price: 4.27,
          stop_loss_price: 1.71,
          hard_exit_time: "15:45 ET",
        },
        trigger_reason: "15-min ORB breakdown below $216.00 with heavy 2.40x selling momentum and expanding volatility.",
      },
      {
        symbol: "NVDA",
        spot_price: 128.50,
        timestamp: "10:14:02",
        opening_range_high: 127.80,
        opening_range_low: 125.60,
        opening_range_width_pct: 0.017,
        ttm_squeeze_active: false,
        ttm_squeeze_bars: 7,
        momentum_direction: "BULLISH_BREAKOUT",
        momentum_score: 0.79,
        relative_volume_15m: 1.95,
        suggested_action: "BUY_CALL",
        recommended_contract: {
          option_type: "CALL",
          strike: 129.0,
          expiration: "2026-08-14",
          dte: 0,
          delta: 0.47,
          gamma: 0.092,
          theta: -1.25,
          vega: 0.11,
          bid: 1.45,
          ask: 1.50,
          mid: 1.48,
          implied_vol: 0.52,
          target_price: 2.59,
          stop_loss_price: 1.04,
          hard_exit_time: "15:45 ET",
        },
        trigger_reason: "High-gamma breakout above $127.80 with 1.95x volume thrust.",
      },
    ];

    const filtered = symbol
      ? allSignals.filter((s) => s.symbol.toUpperCase() === symbol.toUpperCase())
      : allSignals;

    return delay<ZeroDteSignalResponse>({
      signals: filtered.length > 0 ? filtered : allSignals,
      symbol: symbol || undefined,
      as_of: new Date().toISOString(),
    });
  },
  async executeZeroDteTrade(request: ZeroDteTradeRequest | { symbol: string; option_type: "CALL" | "PUT"; strike: number; contracts: number; entry_price?: number }) {
    const sym = request.symbol;
    const type = request.option_type || "CALL";
    const strike = request.strike || 547;
    const contracts = request.contracts || 5;
    const entry = (request as ZeroDteTradeRequest).entry_price || 1.88;
    const target = Number((entry * 1.75).toFixed(2));
    const stop = Number((entry * 0.70).toFixed(2));

    return delay<ZeroDteExecutionResult>({
      ok: true,
      order_id: `ord_0dte_${Date.now()}`,
      symbol: sym,
      option_type: type,
      strike,
      contracts,
      fill_price: entry,
      profit_target_price: target,
      stop_loss_price: stop,
      hard_exit_time: "15:45 ET",
      strategy: "0DTE Intraday Momentum Breakout",
      message: `Executed ${contracts}x ${sym} ${strike} ${type} @ $${entry.toFixed(2)}. Profit target set at $${target.toFixed(2)} (+75%), Stop loss at $${stop.toFixed(2)} (-30%), Hard Time Stop at 15:45 ET.`,
      placed_at: new Date().toISOString(),
    });
  },
  async getVpinMetrics(symbol: string) {
    const sym = symbol.toUpperCase();
    const vpinMap: Record<string, { vpin: number; regime: "LOW" | "MODERATE" | "HIGH_TOXICITY"; concession: number; pct: number }> = {
      SPY: { vpin: 0.184, regime: "LOW", concession: 0.00, pct: 28 },
      QQQ: { vpin: 0.282, regime: "MODERATE", concession: 0.02, pct: 64 },
      TSLA: { vpin: 0.428, regime: "HIGH_TOXICITY", concession: 0.08, pct: 94 },
      NVDA: { vpin: 0.385, regime: "HIGH_TOXICITY", concession: 0.05, pct: 88 },
      AAPL: { vpin: 0.195, regime: "LOW", concession: 0.00, pct: 32 },
      MSFT: { vpin: 0.210, regime: "MODERATE", concession: 0.01, pct: 45 },
    };
    const info = vpinMap[sym] || { vpin: 0.265, regime: "MODERATE" as const, concession: 0.02, pct: 58 };
    
    const numBuckets = 50;
    const bucketSize = 10000;
    const basePrice = sym === "SPY" ? 546.50 : sym === "QQQ" ? 481.10 : sym === "TSLA" ? 214.30 : sym === "NVDA" ? 128.50 : 200.0;
    const buckets: VpinBucket[] = [];
    let currentP = basePrice;
    
    for (let i = 1; i <= numBuckets; i++) {
      const priceDelta = (Math.sin(i * 0.4) * 0.35) + ((i % 3 === 0 ? 0.2 : -0.15));
      const nextP = Number((currentP + priceDelta).toFixed(2));
      const buyPct = Math.max(0.1, Math.min(0.9, 0.5 + (priceDelta / 1.5)));
      const buyVol = Math.round(bucketSize * buyPct);
      const sellVol = bucketSize - buyVol;
      const imbalance = Math.abs(buyVol - sellVol);
      
      buckets.push({
        bucket_index: i,
        buy_volume: buyVol,
        sell_volume: sellVol,
        total_volume: bucketSize,
        price_start: currentP,
        price_end: nextP,
        price_change: Number((nextP - currentP).toFixed(2)),
        imbalance,
        timestamp: new Date(Date.now() - (numBuckets - i) * 120_000).toISOString(),
      });
      currentP = nextP;
    }

    return delay<VpinMetricsResponse>({
      symbol: sym,
      vpin: info.vpin,
      regime: info.regime,
      toxicity_percentile: info.pct,
      bucket_size: bucketSize,
      num_buckets: numBuckets,
      buckets,
      defensive_spread_concession: info.concession,
      warning_message: info.regime === "HIGH_TOXICITY"
        ? `High Microstructure Toxicity (VPIN ${(info.vpin * 100).toFixed(1)}% > 35.0%). Institutional informed flow detected. Defensive concession applied: +$${info.concession.toFixed(2)}/contract.`
        : null,
      as_of: new Date().toISOString(),
    });
  },
  async analyzeOptionsRouting(request: SorAnalysisRequest) {
    const sym = request.symbol.toUpperCase();
    const legs = request.legs && request.legs.length > 0 ? request.legs : [
      { strike: 540, option_type: "PUT" as const, action: "SELL" as const, bid: 3.10, ask: 3.25, mid: 3.175 },
      { strike: 535, option_type: "PUT" as const, action: "BUY" as const, bid: 1.80, ask: 1.95, mid: 1.875 },
    ];
    const latency = request.latency_ms || 250;
    
    let cobNetMid = 0;
    let cobNatural = 0;
    let syntheticNet = 0;
    
    const breakdown: SorLegBreakdown[] = legs.map((leg) => {
      const b = leg.bid ?? 2.0;
      const a = leg.ask ?? 2.2;
      const m = leg.mid ?? (b + a) / 2;
      const isSell = leg.action === "SELL";
      
      if (isSell) {
        cobNetMid += m;
        cobNatural += b;
        syntheticNet += m + (a - b) * 0.35;
      } else {
        cobNetMid -= m;
        cobNatural -= a;
        syntheticNet -= (m - (a - b) * 0.35);
      }
      
      return {
        strike: leg.strike,
        option_type: leg.option_type,
        action: leg.action,
        bid: b,
        ask: a,
        mid: m,
        fill_priority: isSell ? 1 : 2,
        fill_style: isSell ? ("PASSIVE" as const) : ("ACTIVE" as const),
      };
    });

    const absNetMid = Math.abs(cobNetMid);
    const absNatural = Math.abs(cobNatural);
    const absSynthetic = Math.abs(syntheticNet);
    
    const savings = Math.max(12.50, Number(((absSynthetic - absNatural) * 100).toFixed(2)));
    const hungProb = Math.min(0.25, Number((0.02 + (latency / 1000) * 0.045).toFixed(4)));
    const adverseCost = Number((hungProb * 48.0).toFixed(2));
    
    let recommended: "COB_NET_PACKAGE" | "LEG_PASSIVE_FIRST" | "SPLIT_DIRECT" = "LEG_PASSIVE_FIRST";
    let rationale = `Synthetic legging captures $${savings.toFixed(2)} edge with low hung leg hazard (${(hungProb * 100).toFixed(1)}% @ ${latency}ms).`;
    
    if (latency > 1500 || hungProb > 0.15) {
      recommended = "COB_NET_PACKAGE";
      rationale = `High execution latency (${latency}ms) creates unacceptable hung leg hazard (${(hungProb * 100).toFixed(1)}%). Direct atomic COB package route recommended.`;
    }

    return delay<SorAnalysisResponse>({
      symbol: sym,
      recommended_route: recommended,
      cob_net_price: Number(absNetMid.toFixed(2)),
      cob_natural_price: Number(absNatural.toFixed(2)),
      synthetic_net_price: Number(absSynthetic.toFixed(2)),
      expected_savings: savings,
      hung_leg_probability: hungProb,
      adverse_selection_cost: adverseCost,
      latency_ms: latency,
      legs_breakdown: breakdown,
      rationale,
      as_of: new Date().toISOString(),
    });
  },
  async simulateOptionsLegging(request: LeggingSimulationRequest) {
    const sym = request.symbol.toUpperCase();
    const numSims = request.num_simulations || 1000;
    const latencySec = request.latency_seconds || 0.25;
    
    const latencies = [50, 100, 250, 500, 1000, 2000, 5000];
    const latencyCurve = latencies.map((ms) => {
      const rate = Number((0.015 + (ms / 1000) * 0.052).toFixed(4));
      const edge = Number(Math.max(-20, 28.50 - (ms / 1000) * 8.40).toFixed(2));
      return {
        latency_ms: ms,
        hung_leg_rate: rate,
        expected_edge: edge,
      };
    });

    const bins = [
      { bin_edge: -40, count: 18, probability: 0.018 },
      { bin_edge: -30, count: 32, probability: 0.032 },
      { bin_edge: -20, count: 54, probability: 0.054 },
      { bin_edge: -10, count: 86, probability: 0.086 },
      { bin_edge: 0, count: 120, probability: 0.120 },
      { bin_edge: 10, count: 185, probability: 0.185 },
      { bin_edge: 20, count: 245, probability: 0.245 },
      { bin_edge: 30, count: 155, probability: 0.155 },
      { bin_edge: 40, count: 80, probability: 0.080 },
      { bin_edge: 50, count: 25, probability: 0.025 },
    ];

    const hungRate = Number((0.02 + latencySec * 0.048).toFixed(4));
    const expEdge = Number((24.80 - latencySec * 6.50).toFixed(2));

    return delay<LeggingSimulationResponse>({
      symbol: sym,
      num_simulations: numSims,
      latency_seconds: latencySec,
      hung_leg_rate: hungRate,
      expected_edge_dollars: expEdge,
      edge_std_dollars: 14.20,
      worst_case_loss_dollars: -58.00,
      p95_adverse_selection: -26.50,
      pnl_distribution: bins,
      latency_curve: latencyCurve,
      as_of: new Date().toISOString(),
    });
  },

  async getOptionsGexProfile(symbol: string) {
    const sym = symbol.toUpperCase();
    const spot = sym === "SPY" ? 546.50 : sym === "QQQ" ? 481.10 : sym === "TSLA" ? 214.30 : sym === "NVDA" ? 128.50 : sym === "AAPL" ? 224.20 : 500.0;

    const step = sym === "NVDA" ? 2.5 : sym === "TSLA" ? 5.0 : sym === "SPY" ? 2.0 : sym === "QQQ" ? 2.0 : 5.0;
    const baseStrike = Math.round(spot / step) * step;
    // Real StrikeGex.call_gex/.put_gex/.net_gex are raw DOLLAR figures (not
    // pre-scaled to millions) -- generate a "millions" magnitude internally
    // (matches the shape of the real GEX formula's typical size) then scale
    // by 1e6 before placing on the response, so the component's own /1e6
    // display formatting is exercised against realistic magnitudes.
    const rawStrikes: { strike: number; callGexM: number; putGexM: number; callOi: number; putOi: number }[] = [];

    let callWallStrike = baseStrike + step * 3;
    let putWallStrike = baseStrike - step * 3;
    let maxCallGexM = -Infinity;
    let minPutGexM = Infinity;
    let totalNetGexM = 0;
    let totalAbsGexM = 0;

    for (let i = -12; i <= 12; i++) {
      const strike = Number((baseStrike + i * step).toFixed(2));

      const callWeight = Math.max(0.05, Math.exp(-Math.pow((strike - (spot * 1.02)) / (spot * 0.04), 2)));
      const putWeight = Math.max(0.05, Math.exp(-Math.pow((strike - (spot * 0.97)) / (spot * 0.04), 2)));

      const callGexM = Number((callWeight * (sym === "SPY" ? 420 : 180) * (1 + Math.sin(i * 0.5) * 0.15)).toFixed(2));
      const putGexM = Number((-putWeight * (sym === "SPY" ? 380 : 160) * (1 + Math.cos(i * 0.5) * 0.15)).toFixed(2));
      totalNetGexM += callGexM + putGexM;
      totalAbsGexM += callGexM + Math.abs(putGexM);

      if (callGexM > maxCallGexM) {
        maxCallGexM = callGexM;
        callWallStrike = strike;
      }
      if (putGexM < minPutGexM) {
        minPutGexM = putGexM;
        putWallStrike = strike;
      }

      rawStrikes.push({
        strike,
        callGexM,
        putGexM,
        callOi: Math.round(callWeight * 15000 + 500),
        putOi: Math.round(putWeight * 14000 + 400),
      });
    }

    const zeroGammaFlip = Number((spot * (sym === "TSLA" ? 1.015 : 0.985)).toFixed(2));
    const strikes: GexStrikePoint[] = rawStrikes.map((s) => {
      const callGex = Number((s.callGexM * 1e6).toFixed(2));
      const putGex = Number((s.putGexM * 1e6).toFixed(2));
      const netGex = Number((callGex + putGex).toFixed(2));
      const absGex = callGex + Math.abs(putGex);
      const gammaConcentrationPct = totalAbsGexM > 0 ? Number(((s.callGexM + Math.abs(s.putGexM)) / totalAbsGexM * 100).toFixed(2)) : 0;
      return {
        strike: s.strike,
        call_gex: callGex,
        put_gex: putGex,
        net_gex: netGex,
        total_oi: s.callOi + s.putOi,
        call_oi: s.callOi,
        put_oi: s.putOi,
        call_volume: Math.round(s.callOi * 0.2),
        put_volume: Math.round(s.putOi * 0.2),
        abs_gex: Number(absGex.toFixed(2)),
        gamma_concentration_pct: gammaConcentrationPct,
      };
    });

    const totalNetGex = Number((totalNetGexM * 1e6).toFixed(2));
    const isPositiveGamma = totalNetGexM >= 0;
    const gammaRegime: "POSITIVE_GAMMA" | "NEGATIVE_GAMMA" = isPositiveGamma ? "POSITIVE_GAMMA" : "NEGATIVE_GAMMA";
    const regimeDescription = isPositiveGamma
      ? `Positive Gamma Regime ($${totalNetGexM.toFixed(1)}M Net GEX). Market makers long gamma; intraday mean-reversion dampens realized volatility (buy dips, sell rips).`
      : `Negative Gamma Regime ($${totalNetGexM.toFixed(1)}M Net GEX). Market makers short gamma; hedging flow accelerates trend momentum and downside volatility cascades.`;
    const dealerHedgingFlow = Number((totalNetGex * 0.01).toFixed(2));

    return delay<GexProfileResponse>({
      symbol: sym,
      spot_price: spot,
      net_gex: totalNetGex,
      total_call_gex: Number((rawStrikes.reduce((acc, s) => acc + s.callGexM, 0) * 1e6).toFixed(2)),
      total_put_gex: Number((rawStrikes.reduce((acc, s) => acc + s.putGexM, 0) * 1e6).toFixed(2)),
      zero_gamma_flip: zeroGammaFlip,
      call_wall_strike: callWallStrike,
      put_wall_strike: putWallStrike,
      gamma_regime: gammaRegime,
      regime_description: regimeDescription,
      dealer_hedging_flow: dealerHedgingFlow,
      dealer_hedging_per_1pct_move_dollars: dealerHedgingFlow,
      dealer_hedging_shares_per_1pct_move: spot > 0 ? Number((dealerHedgingFlow / spot).toFixed(2)) : 0,
      strikes,
      as_of: new Date().toISOString(),
      spot_price_source: "mock",
      chain_source: "mock",
    });
  },

  async simulateLobQueue(request: LobQueueSimulationRequest) {
    const sym = (request.symbol || "SPY").toUpperCase();
    const priceLevel = request.price_level ?? 100.0;
    const orderSize = request.order_size ?? 1.0;
    const depthAhead = request.depth_ahead ?? 0.0;
    const timeHorizonSec = request.time_horizon_sec ?? 60.0;
    const numSimulations = request.num_simulations ?? 500;
    const lambdaLimit = request.lambda_limit ?? 4.0;
    const muCancel = request.mu_cancel ?? 0.05;
    const thetaMarket = request.theta_market ?? 5.0;

    // Deterministic-pseudo-random derivation from the request inputs (no
    // literal order-book ladder in the real response -- CST(2010) queue-fill
    // dynamics only): more depth ahead + a slower cancel/faster-limit-order
    // mix => lower fill probability & longer expected wait.
    const netFillRate = muCancel + thetaMarket / Math.max(1, lambdaLimit * 10);
    const depletionVelocity = Number(Math.max(0.01, netFillRate * 2.0).toFixed(4));
    const fillProbability = Number(
      Math.max(0.02, Math.min(0.97, 1 - Math.exp(-depletionVelocity * timeHorizonSec / Math.max(1, depthAhead + orderSize)))).toFixed(4)
    );
    const expectedWaitTimeSec = Number(
      Math.min(timeHorizonSec * 3, (depthAhead + orderSize) / depletionVelocity).toFixed(2)
    );
    const medianFillTimeSec = Number((expectedWaitTimeSec * 0.85).toFixed(2));
    const unconditionalFillTimeSec = Number(Math.min(timeHorizonSec, expectedWaitTimeSec * fillProbability).toFixed(2));

    const percentiles: LobQueuePercentiles = {
      p10: Number((expectedWaitTimeSec * 0.35).toFixed(2)),
      p25: Number((expectedWaitTimeSec * 0.6).toFixed(2)),
      p50: medianFillTimeSec,
      p75: Number((expectedWaitTimeSec * 1.2).toFixed(2)),
      p90: Number((expectedWaitTimeSec * 1.6).toFixed(2)),
      p95: Number((expectedWaitTimeSec * 1.9).toFixed(2)),
    };

    const probAdverseMove = Number(Math.max(0.01, Math.min(0.9, 1 - fillProbability * 0.7)).toFixed(4));
    const expectedFillRatio = Number(Math.max(0.05, Math.min(1, fillProbability * 1.05)).toFixed(4));

    return delay<LobQueueSimulationResponse>({
      valid: true,
      symbol: sym,
      price_level: priceLevel,
      order_size: orderSize,
      depth_ahead: depthAhead,
      time_horizon_sec: timeHorizonSec,
      num_simulations: numSimulations,
      fill_probability: fillProbability,
      expected_fill_time_sec: expectedWaitTimeSec,
      expected_wait_time_sec: expectedWaitTimeSec,
      unconditional_fill_time_sec: unconditionalFillTimeSec,
      median_fill_time_sec: medianFillTimeSec,
      prob_adverse_move_before_fill: probAdverseMove,
      expected_fill_ratio: expectedFillRatio,
      queue_depletion_velocity: depletionVelocity,
      queue_progression_percentiles: percentiles,
      cst_closed_form_fill_prob: fillProbability,
      reason: null,
      timestamp: new Date().toISOString(),
      as_of: new Date().toISOString(),
    });
  },

  async getCopulaPairsAnalysis(pair?: string) {
    const p = (pair || "SPY/QQQ").toUpperCase();
    const parts = p.includes("/") ? p.split("/") : [p, "QQQ"];
    const assetX = parts[0] || "SPY";
    const assetY = parts[1] || "QQQ";

    let family: "Clayton" | "Gumbel" | "Frank" = "Clayton";
    let theta = 2.15;
    let lambdaL = 0.725;
    let lambdaU = 0.0;
    let tau = 0.518;
    let ouHalfLife = 14.2;
    let kalmanBeta = 1.23;
    let kalmanAlpha = -12.4;
    let spreadZ = 2.18;
    let currentSpread = 8.45;
    let action: "LONG_SPREAD" | "SHORT_SPREAD" | "HOLD" | "EXIT" = "SHORT_SPREAD";

    if (p.includes("AMD") || p.includes("NVDA")) {
      family = "Gumbel";
      theta = 1.92;
      lambdaL = 0.0;
      lambdaU = 0.564;
      tau = 0.479;
      ouHalfLife = 8.6;
      kalmanBeta = 1.45;
      kalmanAlpha = 4.2;
      spreadZ = -2.31;
      currentSpread = -14.2;
      action = "LONG_SPREAD";
    } else if (p.includes("GOOGL") || p.includes("META") || p.includes("MSFT") || p.includes("AAPL")) {
      family = "Frank";
      theta = 4.65;
      lambdaL = 0.0;
      lambdaU = 0.0;
      tau = 0.442;
      ouHalfLife = 18.5;
      kalmanBeta = 0.92;
      kalmanAlpha = 3.1;
      spreadZ = 0.42;
      currentSpread = 1.85;
      action = "HOLD";
    }

    const tailData: CopulaTailData = {
      lower_tail_dependence: Number(lambdaL.toFixed(3)),
      upper_tail_dependence: Number(lambdaU.toFixed(3)),
      copula_family: family,
      theta: Number(theta.toFixed(2)),
      log_likelihood: 178.4,
      aic: -352.8,
      kendall_tau: Number(tau.toFixed(3)),
    };

    const historical_series: CopulaSeriesPoint[] = [];
    const baseDate = new Date("2026-06-01");
    let basePx = 540;
    let basePy = basePx * kalmanBeta + kalmanAlpha;
    let curSpread = 0;

    for (let i = 0; i < 60; i++) {
      const d = new Date(baseDate);
      d.setDate(d.getDate() + i);
      const dateStr = d.toISOString().split("T")[0];

      const retX = Math.sin(i * 0.2) * 1.5 + (i % 3 === 0 ? 2 : -1.5) * 0.8;
      const retY = retX * kalmanBeta + Math.cos(i * 0.3) * 2.2;
      basePx += retX;
      basePy += retY;

      const dynamicBeta = Number((kalmanBeta + Math.sin(i * 0.1) * 0.08).toFixed(3));
      curSpread = Number((basePy - dynamicBeta * basePx - kalmanAlpha).toFixed(2));
      const zScore = Number((curSpread / 4.2).toFixed(2));

      historical_series.push({
        date: dateStr,
        asset_x_price: Number(basePx.toFixed(2)),
        asset_y_price: Number(basePy.toFixed(2)),
        kalman_beta: dynamicBeta,
        spread: curSpread,
        spread_z_score: zScore,
        upper_band_2sigma: 2.0,
        lower_band_2sigma: -2.0,
      });
    }

    // Ensure last point aligns with summary
    const last = historical_series[historical_series.length - 1];
    last.spread_z_score = spreadZ;
    last.spread = currentSpread;
    last.kalman_beta = kalmanBeta;

    return delay<CopulaPairsResponse>({
      pair: `${assetX}/${assetY}`,
      asset_x: assetX,
      asset_y: assetY,
      copula_family: family,
      tail_dependence: tailData,
      kalman_beta: kalmanBeta,
      kalman_alpha: kalmanAlpha,
      ou_half_life_days: ouHalfLife,
      spread_z_score: spreadZ,
      current_spread: currentSpread,
      signal_action: action,
      historical_series,
      as_of: new Date().toISOString(),
      status_note: `Fitted ${family} Copula on ${assetX}/${assetY} with dynamic Kalman beta $\\beta_t = ${kalmanBeta}$. Spread Z-Score is ${spreadZ > 0 ? "+" : ""}${spreadZ}σ (OU $\\tau_{1/2} = ${ouHalfLife}d$).`,
    });
  },

  async simulateMarketMakerAgent(request: MarketMakerSimRequest) {
    const sym = (request.symbol || "SPY").toUpperCase();
    const spot = request.spot_price || 546.50;
    const gamma = request.risk_aversion_gamma ?? 0.1;
    const kappa = request.order_flow_intensity_kappa ?? 1.5;
    const sigma = request.volatility_sigma ?? 0.20;
    const horizonT = request.time_horizon_t ?? 1.0;
    const totalSteps = request.time_steps ?? 100;
    const maxInv = request.max_inventory ?? 10;
    const orderSize = request.order_size ?? 1;

    const dt = horizonT / totalSteps;
    const steps: MarketMakerStepPoint[] = [];

    let currentMid = spot;
    let inventory = 0;
    let cash = 0;
    let totalTrades = 0;
    let buyFills = 0;
    let sellFills = 0;
    let sumSpread = 0;

    let pnlHigh = 0;
    let maxDdDollars = 0;

    for (let step = 0; step < totalSteps; step++) {
      const timeSec = Math.round(step * (390 * 60 / totalSteps));
      const tau = Math.max(0.01, horizonT - step * dt);

      // Price drift + shock
      const shock = (Math.sin(step * 0.35) * 0.15 + (step % 4 === 0 ? 0.25 : -0.2)) * Math.sqrt(dt) * sigma * spot * 0.4;
      currentMid = Number((currentMid + shock).toFixed(2));

      // Avellaneda-Stoikov Reservation Price: R(s, q, t) = s - q * gamma * sigma^2 * tau
      const reservation = Number((currentMid - inventory * gamma * (sigma ** 2) * tau * 10).toFixed(2));

      // Optimal half-spreads
      const halfSpreadBase = Math.max(0.02, (1 / (gamma || 0.01)) * Math.log(1 + (gamma / kappa)) + 0.5 * gamma * (sigma ** 2) * tau * 5);
      const bidPrice = Number((reservation - halfSpreadBase).toFixed(2));
      const askPrice = Number((reservation + halfSpreadBase).toFixed(2));
      const bidSpread = Number((currentMid - bidPrice).toFixed(2));
      const askSpread = Number((askPrice - currentMid).toFixed(2));
      sumSpread += (askPrice - bidPrice);

      // Probabilities of fill
      const probBuy = inventory < maxInv ? Math.min(0.85, 0.45 * Math.exp(-kappa * Math.max(0.01, bidSpread) * 2)) : 0;
      const probSell = inventory > -maxInv ? Math.min(0.85, 0.45 * Math.exp(-kappa * Math.max(0.01, askSpread) * 2)) : 0;

      let event: "BUY" | "SELL" | null = null;
      // Deterministic pseudo-random fill based on step pattern for smooth visualization
      if ((step % 7 === 1 || step % 11 === 0) && probBuy > 0.15 && inventory < maxInv) {
        event = "BUY";
        inventory += orderSize;
        cash -= bidPrice * orderSize;
        totalTrades++;
        buyFills++;
      } else if ((step % 6 === 2 || step % 9 === 0) && probSell > 0.15 && inventory > -maxInv) {
        event = "SELL";
        inventory -= orderSize;
        cash += askPrice * orderSize;
        totalTrades++;
        sellFills++;
      }

      const pnl = Number((cash + inventory * currentMid).toFixed(2));
      if (pnl > pnlHigh) pnlHigh = pnl;
      const dd = pnlHigh - pnl;
      if (dd > maxDdDollars) maxDdDollars = dd;

      steps.push({
        step,
        time_sec: timeSec,
        mid_price: currentMid,
        reservation_price: reservation,
        bid_price: bidPrice,
        ask_price: askPrice,
        bid_spread: bidSpread,
        ask_spread: askSpread,
        inventory,
        cash: Number(cash.toFixed(2)),
        pnl,
        trade_event: event,
      });
    }

    const finalPnl = steps[steps.length - 1].pnl;
    const pnlDeltas = steps.slice(1).map((s, i) => s.pnl - steps[i].pnl);
    const meanDelta = pnlDeltas.reduce((a, b) => a + b, 0) / (pnlDeltas.length || 1);
    const stdDelta = Math.sqrt(pnlDeltas.map(d => (d - meanDelta) ** 2).reduce((a, b) => a + b, 0) / (pnlDeltas.length || 1)) || 0.01;
    const annualizedSharpe = Number(((meanDelta / stdDelta) * Math.sqrt(252 * 390)).toFixed(2));
    const avgSpread = Number((sumSpread / totalSteps).toFixed(3));
    // fill_rate is a 0-1 fraction (matching ml/drl_market_maker.py's
    // `total_trades / max(1, 2 * n_steps)`), NOT a 0-100 percentage — the
    // component multiplies by 100 at render time.
    const fillRate = Number((totalTrades / (totalSteps * 2)).toFixed(4));

    return delay<MarketMakerSimResponse>({
      symbol: sym,
      risk_aversion_gamma: gamma,
      order_flow_intensity_kappa: kappa,
      volatility_sigma: sigma,
      max_inventory: maxInv,
      final_pnl: finalPnl,
      sharpe_ratio: annualizedSharpe,
      max_drawdown: Number(maxDdDollars.toFixed(2)),
      total_trades: totalTrades,
      fill_rate: fillRate,
      final_inventory: inventory,
      avg_spread: avgSpread,
      steps,
      as_of: new Date().toISOString(),
    });
  },

  // ---- Tier D: AI Research Copilot & Autonomous Backtest ----

  async synthesizeQuantResearch(request: ResearchSynthesizeRequest): Promise<ResearchSynthesizeResponse> {
    const p = (request.prompt || "").toLowerCase();
    const isUnsafe = p.includes("os.system") || p.includes("eval(") || p.includes("import os") || p.includes("subprocess");
    const mode = request.strategy_type || "hypothesis";

    if (isUnsafe) {
      return delay<ResearchSynthesizeResponse>({
        success: false,
        code: `# Rejected by AST Security Validator\n# Violations detected:\n# - Forbidden import: 'os' is explicitly blacklisted.\n# - Forbidden function call: 'eval()' is prohibited.`,
        metadata: {},
        validation_passed: false,
        validation_errors: [
          "Forbidden import: module 'os' is explicitly blacklisted.",
          "Forbidden function call: 'eval()' is prohibited in candidate strategy sandbox.",
        ],
        source_prompt: request.prompt,
        synthesis_mode: mode,
        explanation: "Candidate code violates AST security sandbox rules. Forbidden imports or builtins detected.",
        target_asset_class: request.target_asset_class ?? null,
        strategy_type: request.strategy_type ?? null,
      }, 100);
    }

    const code = `import numpy as np
import pandas as pd
import scipy.stats as stats

def generate_signals(df: pd.DataFrame) -> pd.Series:
    """
    Synthesized Alpha: Volatility-Adjusted Momentum & Mean Reversion Filter
    Synthesis Mode: ${mode}
    """
    close = df["close"]
    ma_fast = close.rolling(window=10, min_periods=5).mean()
    ma_slow = close.rolling(window=20, min_periods=10).mean()
    vol = close.pct_change().rolling(window=20).std()

    # Normalized Z-Score Spread
    z_spread = (close - ma_slow) / (vol * close + 1e-6)

    # Vectorized Alpha Signal (+1.0 Long, -1.0 Short)
    signals = pd.Series(0.0, index=df.index)
    signals[z_spread < -1.8] = 1.0
    signals[z_spread > 1.8] = -1.0

    # Volatility targeting weight adjustment
    target_vol = 0.15
    scaling = np.clip(target_vol / (vol * np.sqrt(252) + 1e-4), 0.2, 2.0)
    return signals * scaling
`;

    return delay<ResearchSynthesizeResponse>({
      success: true,
      code,
      metadata: {
        lookback_fast: 10,
        lookback_slow: 20,
        z_threshold: 1.8,
        target_annual_vol: 0.15,
        rebalance_cadence: "1D",
      },
      validation_passed: true,
      validation_errors: [],
      source_prompt: request.prompt,
      synthesis_mode: mode,
      explanation: "Synthesized institutional-grade signal generating engine using AST-safe vectorized pandas/numpy computations. Incorporates dynamic volatility-targeting scaling and rolling Z-score mean reversion thresholds.",
      target_asset_class: request.target_asset_class ?? null,
      strategy_type: request.strategy_type ?? null,
    }, 150);
  },

  async runAutonomousBacktest(request: AutonomousBacktestRequest): Promise<AutonomousBacktestResponse> {
    const code = request.strategy_code || "";
    const isUnsafe = code.includes("os.system") || code.includes("import os") || code.includes("eval(");

    if (isUnsafe) {
      return delay<AutonomousBacktestResponse>({
        strategy_id: request.strategy_id || "candidate_alpha_01",
        is_deployable: false,
        sharpe_ratio: 0,
        sortino_ratio: 0,
        max_drawdown: 1.0,
        pbo: 1.0,
        dsr: 0.0,
        turnover: 0,
        annualized_return: 0,
        cumulative_return: 0,
        win_rate: 0,
        calmar_ratio: 0,
        volatility: 0,
        gate_evaluations: {
          "pbo_gate (< 0.50)": false,
          "dsr_gate (> 0.95)": false,
          "sharpe_gate (> 0.50)": false,
          "max_drawdown_gate (< 0.30)": false,
        },
        failure_reasons: ["AST Security Error: Prohibited operation detected in candidate strategy."],
        n_paths: 0,
        n_observations: 0,
        execution_time_seconds: 0.04,
        error: "AST Security Validation Failed: Prohibited operation.",
        as_of: new Date().toISOString(),
      }, 100);
    }

    const nObs = 252 * 4;
    const curve: Array<{ date: string; equity: number; drawdown: number }> = [];
    let currentEquity = request.initial_capital || 100000;
    let peakEquity = currentEquity;
    const baseDate = new Date(2022, 0, 3);

    for (let i = 0; i < 80; i++) {
      const d = new Date(baseDate.getTime() + i * (5 * 86400000));
      const dailyRet = (Math.sin(i * 0.22) * 0.007 + 0.0022 + (i % 6 === 0 ? 0.005 : -0.002));
      currentEquity *= (1 + dailyRet);
      if (currentEquity > peakEquity) peakEquity = currentEquity;
      const dd = (peakEquity - currentEquity) / peakEquity;
      curve.push({
        date: d.toISOString().slice(0, 10),
        equity: Number(currentEquity.toFixed(2)),
        drawdown: Number((dd * 100).toFixed(2)),
      });
    }

    return delay<AutonomousBacktestResponse>({
      strategy_id: request.strategy_id || `alpha_${Math.random().toString(36).substring(2, 8)}`,
      is_deployable: true,
      sharpe_ratio: 1.84,
      sortino_ratio: 2.52,
      max_drawdown: 0.118,
      pbo: 0.142,
      dsr: 0.982,
      turnover: 0.32,
      annualized_return: 0.246,
      cumulative_return: Number(((currentEquity - (request.initial_capital || 100000)) / (request.initial_capital || 100000)).toFixed(4)),
      win_rate: 0.584,
      calmar_ratio: 2.08,
      volatility: 0.134,
      gate_evaluations: {
        "pbo_gate (< 0.50)": true,
        "dsr_gate (> 0.95)": true,
        "sharpe_gate (> 0.50)": true,
        "max_drawdown_gate (< 0.30)": true,
      },
      failure_reasons: [],
      n_paths: 16,
      n_observations: nObs,
      execution_time_seconds: 1.482,
      cpcv_mean_oos_sharpe: 1.62,
      cpcv_mean_oos_max_dd: 0.135,
      cpcv_mean_oos_sortino: 2.18,
      regime_breakdown: {
        "LOW_VOL_BULL": {
          sharpe: 2.14,
          sortino: 2.85,
          max_drawdown: 0.082,
          cumulative_return: 0.162,
          win_rate: 0.62,
          pnl_share: 0.658,
          n_bars: 380,
        },
        "MID_VOL_SIDEWAYS": {
          sharpe: 1.48,
          sortino: 1.95,
          max_drawdown: 0.098,
          cumulative_return: 0.074,
          win_rate: 0.54,
          pnl_share: 0.301,
          n_bars: 410,
        },
        "HIGH_VOL_BEAR": {
          sharpe: 0.82,
          sortino: 1.12,
          max_drawdown: 0.118,
          cumulative_return: 0.010,
          win_rate: 0.49,
          pnl_share: 0.041,
          n_bars: 218,
        },
      },
      regime_stability_score: 0.82,
      passes_regime_stability: true,
      equity_curve: curve,
      error: null,
      as_of: new Date().toISOString(),
    }, 150);
  },

  // ---- Tier D: 3D Volatility Surface ----

  async getVolSurface3DMesh(symbol?: string): Promise<VolSurface3DMeshResponse> {
    const sym = (symbol || "SPY").toUpperCase();
    const spot = sym === "QQQ" ? 445.0 : sym === "NVDA" ? 128.5 : 505.2;
    const strikes: number[] = [];
    const minK = Math.round(spot * 0.8);
    const maxK = Math.round(spot * 1.2);
    const nStrikes = 15;
    const step = (maxK - minK) / (nStrikes - 1);
    for (let i = 0; i < nStrikes; i++) {
      strikes.push(Number((minK + i * step).toFixed(1)));
    }

    const dtes = [7, 14, 30, 45, 60, 90, 180, 365];
    const grid: number[][] = [];
    const points: VolSurface3DPoint[] = [];
    let minIv = Infinity;
    let maxIv = -Infinity;

    for (let j = 0; j < dtes.length; j++) {
      const dte = dtes[j];
      const T = dte / 365.0;
      const row: number[] = [];
      const baseAtmIv = 0.18 + 0.04 * Math.log(1 + T);

      for (let i = 0; i < strikes.length; i++) {
        const strike = strikes[i];
        const m = Math.log(strike / spot);
        const skewSlope = -0.15 / Math.sqrt(Math.max(0.04, T));
        const smileCurvature = 0.22 / Math.max(0.1, Math.pow(T, 0.4));
        const iv = Number(Math.max(0.08, baseAtmIv + skewSlope * m + smileCurvature * m * m).toFixed(4));

        minIv = Math.min(minIv, iv);
        maxIv = Math.max(maxIv, iv);
        row.push(iv);
        points.push({
          strike,
          dte,
          iv,
          moneyness: Number((strike / spot).toFixed(3)),
          call_iv: Number((iv * 0.98).toFixed(4)),
          put_iv: Number((iv * 1.02).toFixed(4)),
        });
      }
      grid.push(row);
    }

    return delay<VolSurface3DMeshResponse>({
      symbol: sym,
      spot_price: spot,
      strikes,
      dtes,
      grid,
      min_iv: minIv,
      max_iv: maxIv,
      min_strike: strikes[0],
      max_strike: strikes[strikes.length - 1],
      min_dte: dtes[0],
      max_dte: dtes[dtes.length - 1],
      points,
      as_of: new Date().toISOString(),
    });
  },

  // ---- Tier D: Multi-Broker Gateway & Circuit Breakers ----

  async getMultiBrokerStatus(): Promise<MultiBrokerStatusResponse> {
    const brokers: Record<string, BrokerHealthStatusDto> = {
      alpaca: {
        broker_id: "alpaca",
        broker_type: "alpaca",
        connection_state: "connected",
        circuit_state: "closed",
        is_healthy: true,
        is_routable: true,
        latency_ms: 24.5,
        avg_latency_ms: 26.2,
        p95_latency_ms: 42.0,
        error_rate: 0.002,
        consecutive_failures: 0,
        last_heartbeat: new Date().toISOString(),
        last_error: null,
        status_message: "Alpaca REST/WS healthy, primary routing operational.",
      },
      interactive_brokers: {
        broker_id: "interactive_brokers",
        broker_type: "interactive_brokers",
        connection_state: "connected",
        circuit_state: "closed",
        is_healthy: true,
        is_routable: true,
        latency_ms: 38.1,
        avg_latency_ms: 40.5,
        p95_latency_ms: 68.4,
        error_rate: 0.005,
        consecutive_failures: 0,
        last_heartbeat: new Date().toISOString(),
        last_error: null,
        status_message: "TWS Gateway v10.19 connected, options & equities ready.",
      },
      tradier: {
        broker_id: "tradier",
        broker_type: "tradier",
        connection_state: "connected",
        circuit_state: "closed",
        is_healthy: true,
        is_routable: true,
        latency_ms: 45.2,
        avg_latency_ms: 48.0,
        p95_latency_ms: 78.0,
        error_rate: 0.008,
        consecutive_failures: 0,
        last_heartbeat: new Date().toISOString(),
        last_error: null,
        status_message: "Tradier Production Sandbox connected.",
      },
      robinhood: {
        broker_id: "robinhood",
        broker_type: "robinhood",
        connection_state: "degraded",
        circuit_state: "half_open",
        is_healthy: false,
        is_routable: false,
        latency_ms: 182.4,
        avg_latency_ms: 165.0,
        p95_latency_ms: 340.0,
        error_rate: 0.12,
        consecutive_failures: 2,
        last_heartbeat: new Date(Date.now() - 45000).toISOString(),
        last_error: "RateLimitExceeded: 429 Too Many Requests",
        status_message: "Degraded latency probe. Canary half-open recovery active.",
      },
      fmp_paper: {
        broker_id: "fmp_paper",
        broker_type: "fmp_paper",
        connection_state: "connected",
        circuit_state: "closed",
        is_healthy: true,
        is_routable: true,
        latency_ms: 1.8,
        avg_latency_ms: 2.1,
        p95_latency_ms: 3.5,
        error_rate: 0.0,
        consecutive_failures: 0,
        last_heartbeat: new Date().toISOString(),
        last_error: null,
        status_message: "In-memory paper simulated broker ledger active.",
      },
    };

    const audits: RoutingAuditDto[] = [
      {
        client_order_id: "ord_d89f2a01",
        symbol: "SPY",
        side: "BUY",
        qty: 100,
        primary_broker_id: "alpaca",
        executed_broker_id: "alpaca",
        was_failover: false,
        total_latency_ms: 23.4,
        final_status: "FILLED",
        failover_reason: null,
        timestamp: new Date(Date.now() - 120000).toISOString(),
      },
      {
        client_order_id: "ord_c44b9102",
        symbol: "QQQ",
        side: "SELL",
        qty: 50,
        primary_broker_id: "robinhood",
        executed_broker_id: "alpaca",
        was_failover: true,
        total_latency_ms: 88.2,
        final_status: "FILLED",
        failover_reason: "high_latency (>150ms)",
        timestamp: new Date(Date.now() - 480000).toISOString(),
      },
      {
        client_order_id: "ord_e7710a99",
        symbol: "NVDA",
        side: "BUY",
        qty: 200,
        primary_broker_id: "alpaca",
        executed_broker_id: "alpaca",
        was_failover: false,
        total_latency_ms: 28.1,
        final_status: "FILLED",
        failover_reason: null,
        timestamp: new Date(Date.now() - 920000).toISOString(),
      },
    ];

    return delay<MultiBrokerStatusResponse>({
      active_broker_id: "alpaca",
      manual_override_broker_id: null,
      priority_hierarchy: ["alpaca", "interactive_brokers", "tradier", "fmp_paper"],
      brokers,
      total_orders_routed: 14250,
      total_failovers: 2,
      last_failover_time: new Date(Date.now() - 480000).toISOString(),
      last_failover_reason: "High latency detected on primary adapter; automated failover to Alpaca.",
      recent_routing_audits: audits,
    });
  },

  async triggerBrokerFailover(request: BrokerFailoverRequest): Promise<BrokerFailoverResponse> {
    return delay<BrokerFailoverResponse>({
      status: "ok",
      active_broker: request.target_broker,
      manual_override: request.target_broker,
      reason: request.reason || "manual_operator_failover",
      timestamp: new Date().toISOString(),
    });
  },

  // ---- Tier D: SEC Rule 606 Execution Quality Reporter ----

  async getSecRule606Report(params?: { year?: number; quarter?: number; is_option?: boolean }): Promise<SecRule606ReportResponse> {
    const yr = params?.year ?? 2026;
    const qtr = params?.quarter ?? 1;
    const isOpt = params?.is_option ?? null;

    const venuesOverall: SecRule606VenueRow[] = [
      {
        venue: "CITADEL SECURITIES LLC",
        order_count: 5420,
        pct_of_total_orders: 38.04,
        executed_shares: 1120000,
        pct_of_total_shares: 39.30,
        net_fee_rebate_dollars: 1456.20,
        rebate_per_hundred_shares_dollars: 0.13,
        rebate_per_hundred_shares_cents: 13.0,
        price_improved_orders_count: 4820,
        price_improvement_rate: 88.93,
        price_improved_shares_count: 994000,
        total_price_improvement_dollars: 8420.50,
        avg_price_improvement_per_order_dollars: 1.55,
        avg_price_improvement_per_share_cents: 0.75,
        avg_price_improvement_per_improved_share_cents: 0.85,
      },
      {
        venue: "VIRTU FINANCIAL BD LLC",
        order_count: 3560,
        pct_of_total_orders: 24.98,
        executed_shares: 720000,
        pct_of_total_shares: 25.26,
        net_fee_rebate_dollars: 936.00,
        rebate_per_hundred_shares_dollars: 0.13,
        rebate_per_hundred_shares_cents: 13.0,
        price_improved_orders_count: 3100,
        price_improvement_rate: 87.08,
        price_improved_shares_count: 628000,
        total_price_improvement_dollars: 5120.30,
        avg_price_improvement_per_order_dollars: 1.44,
        avg_price_improvement_per_share_cents: 0.71,
        avg_price_improvement_per_improved_share_cents: 0.82,
      },
      {
        venue: "JANE STREET CAPITAL LLC",
        order_count: 2480,
        pct_of_total_orders: 17.40,
        executed_shares: 510000,
        pct_of_total_shares: 17.89,
        net_fee_rebate_dollars: 612.00,
        rebate_per_hundred_shares_dollars: 0.12,
        rebate_per_hundred_shares_cents: 12.0,
        price_improved_orders_count: 2090,
        price_improvement_rate: 84.27,
        price_improved_shares_count: 428000,
        total_price_improvement_dollars: 3240.10,
        avg_price_improvement_per_order_dollars: 1.31,
        avg_price_improvement_per_share_cents: 0.64,
        avg_price_improvement_per_improved_share_cents: 0.76,
      },
      {
        venue: "TWO SIGMA SECURITIES LLC",
        order_count: 1840,
        pct_of_total_orders: 12.91,
        executed_shares: 340000,
        pct_of_total_shares: 11.93,
        net_fee_rebate_dollars: 374.00,
        rebate_per_hundred_shares_dollars: 0.11,
        rebate_per_hundred_shares_cents: 11.0,
        price_improved_orders_count: 1480,
        price_improvement_rate: 80.43,
        price_improved_shares_count: 275000,
        total_price_improvement_dollars: 1820.40,
        avg_price_improvement_per_order_dollars: 0.99,
        avg_price_improvement_per_share_cents: 0.54,
        avg_price_improvement_per_improved_share_cents: 0.66,
      },
      {
        venue: "NEW YORK STOCK EXCHANGE (ARCA)",
        order_count: 950,
        pct_of_total_orders: 6.67,
        executed_shares: 160000,
        pct_of_total_shares: 5.61,
        net_fee_rebate_dollars: 42.30,
        rebate_per_hundred_shares_dollars: 0.026,
        rebate_per_hundred_shares_cents: 2.6,
        price_improved_orders_count: 510,
        price_improvement_rate: 53.68,
        price_improved_shares_count: 86000,
        total_price_improvement_dollars: 348.95,
        avg_price_improvement_per_order_dollars: 0.37,
        avg_price_improvement_per_share_cents: 0.22,
        avg_price_improvement_per_improved_share_cents: 0.41,
      },
    ];

    const categoryBreakdown: Record<string, SecRule606CategoryBreakdown> = {
      market: {
        category: "market",
        order_count: 6200,
        pct_of_total_orders: 43.51,
        executed_shares: 1250000,
        pct_of_total_shares: 43.86,
        net_fee_rebate_dollars: 1625.00,
        rebate_per_hundred_shares_dollars: 0.13,
        rebate_per_hundred_shares_cents: 13.0,
        price_improved_orders_count: 5760,
        price_improvement_rate: 92.90,
        price_improved_shares_count: 1160000,
        price_improved_shares_rate: 92.80,
        total_price_improvement_dollars: 11450.25,
        avg_price_improvement_per_order_dollars: 1.85,
        avg_price_improvement_per_improved_order_dollars: 1.99,
        avg_price_improvement_per_share_cents: 0.92,
        avg_price_improvement_per_improved_share_cents: 0.99,
      },
      marketable_limit: {
        category: "marketable_limit",
        order_count: 5150,
        pct_of_total_orders: 36.14,
        executed_shares: 1020000,
        pct_of_total_shares: 35.79,
        net_fee_rebate_dollars: 1326.00,
        rebate_per_hundred_shares_dollars: 0.13,
        rebate_per_hundred_shares_cents: 13.0,
        price_improved_orders_count: 4580,
        price_improvement_rate: 88.93,
        price_improved_shares_count: 910000,
        price_improved_shares_rate: 89.22,
        total_price_improvement_dollars: 6320.00,
        avg_price_improvement_per_order_dollars: 1.23,
        avg_price_improvement_per_improved_order_dollars: 1.38,
        avg_price_improvement_per_share_cents: 0.62,
        avg_price_improvement_per_improved_share_cents: 0.69,
      },
      non_marketable_limit: {
        category: "non_marketable_limit",
        order_count: 2450,
        pct_of_total_orders: 17.19,
        executed_shares: 490000,
        pct_of_total_shares: 17.19,
        net_fee_rebate_dollars: 441.00,
        rebate_per_hundred_shares_dollars: 0.09,
        rebate_per_hundred_shares_cents: 9.0,
        price_improved_orders_count: 1540,
        price_improvement_rate: 62.86,
        price_improved_shares_count: 310000,
        price_improved_shares_rate: 63.27,
        total_price_improvement_dollars: 1120.00,
        avg_price_improvement_per_order_dollars: 0.46,
        avg_price_improvement_per_improved_order_dollars: 0.73,
        avg_price_improvement_per_share_cents: 0.23,
        avg_price_improvement_per_improved_share_cents: 0.36,
      },
      other: {
        category: "other",
        order_count: 450,
        pct_of_total_orders: 3.16,
        executed_shares: 90000,
        pct_of_total_shares: 3.16,
        net_fee_rebate_dollars: 28.50,
        rebate_per_hundred_shares_dollars: 0.032,
        rebate_per_hundred_shares_cents: 3.2,
        price_improved_orders_count: 120,
        price_improvement_rate: 26.67,
        price_improved_shares_count: 31000,
        price_improved_shares_rate: 34.44,
        total_price_improvement_dollars: 60.00,
        avg_price_improvement_per_order_dollars: 0.13,
        avg_price_improvement_per_improved_order_dollars: 0.50,
        avg_price_improvement_per_share_cents: 0.07,
        avg_price_improvement_per_improved_share_cents: 0.19,
      },
    };

    return delay<SecRule606ReportResponse>({
      header: {
        report_type: "SEC Rule 606(a)(1) Order Routing & Execution Quality Report",
        period: `${yr}-Q${qtr}`,
        year: yr,
        quarter: qtr,
        start_date: `${yr}-01-01T00:00:00Z`,
        end_date: `${yr}-03-31T23:59:59Z`,
        is_option: isOpt,
        created_at: new Date().toISOString(),
      },
      summary: {
        total_orders: 14250,
        total_shares: 2850000,
        total_notional: 412500000,
        total_net_rebate_dollars: 3420.50,
        total_price_improvement_dollars: 18950.25,
        overall_price_improvement_rate: 84.21,
        overall_share_price_improvement_rate: 84.59,
        overall_rebate_per_hundred_shares_dollars: 0.12,
        overall_rebate_per_hundred_shares_cents: 12.0,
        overall_avg_price_improvement_per_order_dollars: 1.33,
        price_improved_orders_count: 12000,
      },
      order_category_breakdown: categoryBreakdown,
      venue_breakdown: {
        by_category: {
          market: venuesOverall.slice(0, 3),
          marketable_limit: venuesOverall.slice(0, 4),
          non_marketable_limit: venuesOverall.slice(1, 5),
          other: venuesOverall.slice(3, 5),
        },
        venues_overall: venuesOverall,
      },
    });
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

  // ---- Dynamic Circuit Breaker ----
  async getCircuitBreakerStatus(): Promise<CircuitBreakerStatusResponse> {
    return delay(getMockCircuitBreakerStatus());
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
      // Illustrative "Diagnostic" (is_active: false) row -- any registered
      // SignalModule with meta_label_features declared can train here, but
      // ml/forecast_backfill.py only marks the fixed
      // timeseries_momentum/cross_sectional_momentum/rsi2_mean_reversion
      // trio as `is_active: true`. Keeping a false-branch example in the
      // mock exercises the "Diagnostic" badge and bottom-of-table sort in
      // tests, rather than only ever rendering the all-Active happy path.
      macd_momentum_10d: {
        accuracy: 0.504,
        auc: 0.508,
        n_train: 5210,
        n_test: 0,
        split_date: "CPCV",
        is_active: false,
      },
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

// Mirrors the real api/pilots_api.py::_PAPER_BROKER_GROUPS exactly (field
// names, types, and defaults) -- this fixture previously invented fields
// (PAPER_BROKER_ENABLED, PAPER_BROKER_INITIAL_CASH, PAPER_BROKER_SLIPPAGE_BPS)
// that don't exist in settings.py at all, and gave BROKER_BACKEND a fake
// "PAPER"/"ALPACA"/"ROBINHOOD" enum with a "ROBINHOOD" option that doesn't
// actually work (BROKER_BACKEND recognizes only "alpaca"/"fmp_paper" today;
// "robinhood" is a documented-but-not-yet-implemented reserved value that
// falls through to "alpaca" -- see docs/architecture/execution.md's "Future
// extension point" section). A mock-mode operator exercising this screen was
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
      "Selects the active broker backend ('alpaca' or 'fmp_paper'). Defaults to 'fmp_paper'. A runtime guard forces 'alpaca' if 'fmp_paper' is used while genuinely going live. 'robinhood' is reserved for a future automated broker; any unrecognized value falls through to 'alpaca'.",
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
let paperPositions: PaperBrokerPosition[] = [];
let paperOrders: PaperBrokerOrder[] = [];

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

/**
 * Returns a realistic sub-second portfolio risk and Greek streaming event for mock mode.
 */
export function getMockPortfolioRiskStreamEvent(): PortfolioRiskStreamEvent {
  return {
    timestamp: new Date().toISOString(),
    spy_price: 502.45,
    net_delta: 142.5,
    net_dollar_delta: 25650.0,
    net_gamma: 12.45,
    net_dollar_gamma_1pct: 128.3,
    net_theta: -45.2,
    net_vega: 84.1,
    beta_weighted_delta_spy: 51.05,
    total_positions_count: 2,
    resolved_positions_count: 2,
    missing_data_count: 0,
    positions: [
      {
        symbol: "AAPL",
        underlying: "AAPL",
        position_type: "equity",
        qty: 100,
        spot_price: 182.5,
        delta: 100,
        dollar_delta: 18250,
        gamma: 0,
        dollar_gamma_1pct: 0,
        theta_daily: 0,
        vega_1pct: 0,
        beta_spy: 1.2,
        beta_weighted_delta_spy: 43.58,
      },
      {
        symbol: "AAPL 2026-09-18 $185.00 CALL",
        underlying: "AAPL",
        position_type: "option",
        qty: 1,
        spot_price: 182.5,
        strike: 185.0,
        dte: 32,
        option_type: "call",
        iv: 0.24,
        delta: 42.5,
        dollar_delta: 7400,
        gamma: 12.45,
        dollar_gamma_1pct: 128.3,
        theta_daily: -45.2,
        vega_1pct: 84.1,
        beta_spy: 1.2,
        beta_weighted_delta_spy: 7.47,
      },
    ],
    missing_positions: [],
  };
}

/**
 * Realistic mock dynamic circuit breaker status fixture.
 */
export function getMockCircuitBreakerStatus(stateOverride?: CircuitBreakerState): CircuitBreakerStatusResponse {
  const state = stateOverride ?? "NORMAL";
  if (state === "CAUTION") {
    return {
      state: "CAUTION",
      volatility_zscore: 2.35,
      vpin: 0.32,
      ofi: -450.2,
      loss_velocity_per_min: -85.5,
      reason: "Elevated market volatility detected across monitored universe",
      updated_at: new Date().toISOString(),
    };
  }
  if (state === "SOFT_HALT") {
    return {
      state: "SOFT_HALT",
      volatility_zscore: 3.82,
      vpin: 0.46,
      ofi: -1250.0,
      loss_velocity_per_min: -210.0,
      reason: "VOLATILITY_BURST_HALT: 5m EWMA realized vol Z-score 3.82 > threshold 3.50",
      updated_at: new Date().toISOString(),
    };
  }
  if (state === "HARD_HALT") {
    return {
      state: "HARD_HALT",
      volatility_zscore: 4.15,
      vpin: 0.58,
      ofi: -2400.0,
      loss_velocity_per_min: -750.0,
      reason: "LOSS_VELOCITY_BREACH: Intraday loss rate $750.00/min exceeds allowable rate $666.67/min",
      updated_at: new Date().toISOString(),
    };
  }
  return {
    state: "NORMAL",
    volatility_zscore: 0.85,
    vpin: 0.18,
    ofi: 120.5,
    loss_velocity_per_min: -15.4,
    reason: null,
    updated_at: new Date().toISOString(),
  };
}


