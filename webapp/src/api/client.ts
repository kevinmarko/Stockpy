/**
 * client.ts — typed API client for api/pilots_api.py.
 *
 * Swapping mock -> live is a ONE-FLAG change: set VITE_USE_MOCK=false (and point
 * VITE_API_BASE_URL / VITE_API_TOKEN at the running FastAPI service). Every screen
 * imports `api` from here and never talks to fetch/mock directly, so the live
 * cutover touches no component code.
 */

import { mockApi, MOCK_META } from "./mock";
import { ApiError, ForecastBackfillConflictError } from "./types";
import { readCacheEntry, writeCacheEntry } from "./offlineCache";
import type {
  AgenticDiscovery,
  AgenticStatus,
  AiChartResponse,
  AiCommentaryResponse,
  AiModelsResponse,
  AiResearchResponse,
  AlertsFeed,
  AutomationSchedule,
  AutomationStatus,
  BrinsonFachlerResult,
  BrinsonFachlerRow,
  BrokerageConnectRequest,
  BrokerageDisconnectResult,
  BrokerageLoginCancelResult,
  BrokerageLoginJob,
  BrokerageRefreshResult,
  BrokerageStatus,
  CalibrationSummary,
  ControlStatus,
  CronStatus,
  DecisionCreateRequest,
  DecisionCreateResult,
  DecisionEntry,
  EdgeByStrategy,
  Follow,
  FollowResult,
  ForecastSkill,
  ForecastBackfillSummary,
  ForecastBackfillJob,
  IntervalUpdateResult,
  ExecutionModeUpdateRequest,
  ExecutionModeUpdateResult,
  ExecutionQueueParams,
  JobRecord,
  KillSwitchActionResult,
  LiveTradeProposal,
  LlmSettingUpdateResult,
  LlmStatus,
  LogAggregation,
  MacroGateUpdateResult,
  ModelRow,
  ObservabilitySummary,
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
  Holding,
  Portfolio,
  PortfolioAttribution,
  RealizedPerformance,
  RollingBeta,
  RunRecord,
  SectorSelectionView,
  StrategyMatrix,
  StrategyHealthRow,
  GravityAuditStatus,
  StrategyModulesUpdate,
  StrategyModulesUpdateResult,
  ValidationTrendSnapshot,
  SentimentDynamics,
  SentimentHistory,
  TunablesResponse,
  TunablesUpdateResult,
  SettingsConfirmMap,
  SymbolDetail,
  SymbolCompareResponse,
  UniverseResponse,
  SyncReportResponse,
  SymbolReincludeResult,
  RecommendationsResponse,
  RestartDaemonResult,
  RlhfSummary,
  RlhfReviewSubmitRequest,
  RlhfReviewSubmitResult,
  RlhfSftExportResult,
  UniverseListResponse,
  Thresholds,
  SymbolOptions,
  TriggerRunResult,
  Bar,
  Fundamentals,
  MacroHistorySeries,
  MacroSnapshot,
  QuotesResponse,
  SignalBreakdown,
  SignalImportance,
  ForecastResult,
  CommandManifest,
  ExecutionQueue,
  ScanConfigRequest,
  ScanConfigResult,
  WatchResult,
  EquityCurveResponse,
  AiDisagreementsResponse,
  ReportManifest,
  ReportContent,
  DeadLetterQueue,
  DeadLetterRetryResult,
  PromptListResponse,
  PromptBody,
  PromptPinRequest,
  PromptPinResult,
  DataSyncResult,
  ProviderStatus,
  MacroSentimentResponse,
  OrderBookLadderResponse,
  ModelComparisonResponse,
  OptionsAnalyticsSummaryResponse,
  CacheLongShortConcentratedPosition,
  CacheLongShortSimulateRequest,
  CacheLongShortSimulateResult,
  CacheLongShortStartRequest,
  CacheLongShortStartResult,
  CacheLongShortDashboard,
  CacheLongShortPendingTrade,
  PaperBrokerAccount,
  PaperBrokerPosition,
  PaperBrokerOrder,
  PaperBrokerResetResult,
  StrategyOptionsCandidatesResponse,
  StrategyOptionsExecutionResult,
  PortfolioGreeks,
  CacheLongShortApproveBulkResult,

  OptionChainResponse,
  OptionsOrderRequest,
  OptionsOrderResult,
  OptionsBacktestParams,
  OptionsBacktestResponse,
  OptionsMetaModelStatus,
  OptionsMetaModelRetrainResult,
  PaperBrokerSettleExpiredResult,
  ScenarioMatrixResponse,
  VolSurfaceResponse,
  DeltaHedgePreview,
  DeltaHedgeResult,
  RollOrderRequest,
  ManageExitsResult,
  EarningsCrushCandidate,
  EarningsCrushCandidatesResponse,
  EarningsCrushExecutionResult,
  UnusualOptionsFlowResponse,
  FlowSentimentResponse,
  HarRvForecastResponse,
  VolMispricingResponse,
  GammaScalpRequest,
  GammaScalpResponse,
  OptionsAlertTestResult,
  DispersionBasketResponse,
  DispersionBasketOrderRequest,
  DispersionExecutionResult,
  ZeroDteSignalResponse,
  ZeroDteTradeRequest,
  ZeroDteExecutionResult,
  VpinMetricsResponse,
  SorAnalysisRequest,
  SorAnalysisResponse,
  LeggingSimulationRequest,
  LeggingSimulationResponse,
  GexProfileResponse,
  LobQueueSimulationRequest,
  LobQueueSimulationResponse,
  CopulaPairsResponse,
  MarketMakerSimRequest,
  MarketMakerSimResponse,
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
  MultiBrokerStatusResponse,
  BrokerFailoverRequest,
  BrokerFailoverResponse,
  SecRule606ReportResponse,
  CircuitBreakerStatusResponse,
} from "./types";

import { getEffectiveToken } from "../auth/apiToken";
import { config } from "../config/env";

// All five settings below are resolved and VALIDATED once in config/env.ts
// (trailing slashes stripped, protocol/query/fragment checked, and an
// empty-but-present value resolved to the documented default rather than to
// "" — which used to silently turn every fetch into a relative same-origin
// request). This module just reads the already-clean values.
const BASE_URL = config.apiBaseUrl;
// The Phase-4 data/metrics engines are SEPARATE FastAPI processes on their own
// ports (data_api :8603, metrics_api :8604) — they cannot be mounted into the
// Pilots API (its AST guard forbids the heavy-engine imports they require). So
// the client routes by path prefix to the right origin; each falls back to the
// Pilots base's host if unset (i.e. a single-origin reverse-proxy deployment
// where one host proxies /data/* and /metrics/* works with zero extra config).
const DATA_BASE_URL = config.dataApiBaseUrl;
const METRICS_BASE_URL = config.metricsApiBaseUrl;
// The Control API (orchestrator daemon: live status + stage-scoped run
// triggers) is ALSO a separate origin (:8601), not part of the Pilots API. The
// Pipeline Dashboard's /status, /run, /run/{id}/status and /pipeline/* calls
// must route here, or they 404 against the Pilots base in live mode. Falls back
// to the Pilots host if unset (single-origin reverse-proxy deployment).
const CONTROL_BASE_URL = config.controlApiBaseUrl;
// Resolved once at module load. On a non-loopback origin with nothing in
// sessionStorage yet, this is "" and every request goes out unauthenticated
// -- by design: <TokenGate> (rendered before the rest of the app on a
// non-loopback origin) stores a token then reloads the page, so this module
// re-evaluates fresh with the real value rather than needing every call site
// here to re-resolve the token on every request.
const TOKEN = getEffectiveToken();

/** Route a request path to its owning service's base URL by prefix. */
function baseFor(path: string): string {
  if (path.startsWith("/data/")) return DATA_BASE_URL;
  if (path.startsWith("/metrics/")) return METRICS_BASE_URL;
  // Control API (:8601): daemon status, stage-scoped run triggers, the
  // background job runner, and daemon restart. Note "/automation/run" is a
  // PILOTS endpoint and correctly does NOT match here.
  if (
    path === "/status" ||
    path.startsWith("/run") ||
    path.startsWith("/pipeline/") ||
    path.startsWith("/jobs") ||
    path.startsWith("/daemon/") ||
    path.startsWith("/ws/training/")
  ) {
    return CONTROL_BASE_URL;
  }
  // AI chat streaming, live chat WebSocket, and live-tick WS all live on the
  // Data API (:8603) alongside the other "/data/*" Phase-6 endpoints, but
  // keep their own top-level paths ("/api/chat", "/ws/ticks/*", "/ws/chat/*")
  // rather than a "/data/" prefix, so they need an explicit match here.
  if (
    path === "/api/chat" ||
    path.startsWith("/ws/ticks/") ||
    path.startsWith("/ws/chat/") ||
    path.startsWith("/ws/risk/") ||
    path.startsWith("/risk/")
  ) {
    return DATA_BASE_URL;
  }
  return BASE_URL;
}

/**
 * Full ws:// (or wss:// on an https origin) URL for the real-time portfolio risk & Greek endpoint.
 */
export function portfolioRiskWsUrl(tokenOverride?: string): string {
  const httpBase = baseFor("/ws/risk/portfolio");
  const wsBase = httpBase.replace(/^https:/, "wss:").replace(/^http:/, "ws:");
  const params = new URLSearchParams();
  const token = tokenOverride || TOKEN;
  if (token) params.set("token", token);
  const qs = params.toString();
  return `${wsBase}/ws/risk/portfolio${qs ? `?${qs}` : ""}`;
}

/**
 * Full ws:// (or wss:// on an https origin) URL for the Gemini Live chat endpoint.
 * The browser WebSocket API cannot set an Authorization header, so the token
 * travels as a `?token=` query param.
 */
export function liveChatWsUrl(tokenOverride?: string): string {
  const httpBase = baseFor("/ws/chat/live");
  const wsBase = httpBase.replace(/^https:/, "wss:").replace(/^http:/, "ws:");
  const params = new URLSearchParams();
  const token = tokenOverride || TOKEN;
  if (token) params.set("token", token);
  const qs = params.toString();
  return `${wsBase}/ws/chat/live${qs ? `?${qs}` : ""}`;
}

/**
 * Full URL for the streaming chat endpoint. A dedicated helper (matching
 * jobStreamUrl's precedent) because this is a raw `fetch()` + ReadableStream
 * consumer, not a `http<T>()` JSON call — it still needs to resolve against
 * the Data API's base URL rather than the webapp's own origin.
 */
export function chatUrl(): string {
  return `${baseFor("/api/chat")}/api/chat`;
}

/**
 * Full ws:// (or wss:// on an https origin) URL for the live-tick endpoint.
 * The browser WebSocket API cannot set an Authorization header, so the token
 * travels as a `?token=` query param — same convention as jobStreamUrl.
 */
export function liveTickWsUrl(symbol: string): string {
  const httpBase = baseFor("/ws/ticks/");
  const wsBase = httpBase.replace(/^https:/, "wss:").replace(/^http:/, "ws:");
  const params = new URLSearchParams();
  if (TOKEN) params.set("token", TOKEN);
  const qs = params.toString();
  return `${wsBase}/ws/ticks/${encodeURIComponent(symbol.toUpperCase())}${qs ? `?${qs}` : ""}`;
}

/**
 * Full URL (including auth) for a job's SSE log stream. A dedicated helper
 * rather than inlining `${CONTROL_BASE_URL}/jobs/...` at the call site,
 * because the browser's native EventSource can't set an Authorization
 * header — the token has to travel as a `?token=` query param instead, and
 * this is the one place that needs to know that.
 */
export function jobStreamUrl(jobId: string, offset = 0): string {
  const params = new URLSearchParams({ offset: String(offset) });
  if (TOKEN) params.set("token", TOKEN);
  return `${baseFor("/jobs")}/jobs/${encodeURIComponent(jobId)}/stream?${params.toString()}`;
}

/**
 * Full ws:// (or wss:// on an https origin) URL for the training-status
 * broadcast endpoint on the Control API (orchestrator daemon) -- a
 * DIFFERENT origin from the Data API's own /ws/ticks/* used by
 * liveTickWsUrl (see baseFor's routing). Same auth convention as
 * liveTickWsUrl/jobStreamUrl: the token travels as a `?token=` query param
 * since the browser WebSocket API can't set an Authorization header.
 */
export function trainingStatusWsUrl(): string {
  const httpBase = baseFor("/ws/training/status");
  const wsBase = httpBase.replace(/^https:/, "wss:").replace(/^http:/, "ws:");
  const params = new URLSearchParams();
  if (TOKEN) params.set("token", TOKEN);
  const qs = params.toString();
  return `${wsBase}/ws/training/status${qs ? `?${qs}` : ""}`;
}

// Default to MOCK unless explicitly told to go live. This means a fresh checkout
// runs fully offline with zero config; flip VITE_USE_MOCK=false to hit the API.
//
// Parsed strictly in config/env.ts against a closed vocabulary (true/false/
// 1/0/yes/no/on/off). Anything else is a hard config error rather than a
// silent fall-back to mock — the old `!== "false"` test meant `VITE_USE_MOCK=0`
// or a typo left the app rendering fabricated data while the operator believed
// it was live. Stays a top-level `export const` (not a property read) because
// tests spread it over a module mock: see components/LogStream.test.tsx.
export const USE_MOCK = config.useMock;

async function http<T>(
  path: string,
  init?: RequestInit & { method?: string }
): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  // Only idempotent reads are ever cached/served-from-cache — a POST (follow,
  // connectBrokerage, ...) must never be silently satisfied by a stale value.
  const cacheable = method === "GET";
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init?.body ? { "Content-Type": "application/json" } : {}),
  };

  if (TOKEN) headers["Authorization"] = `Bearer ${TOKEN}`;

  const base = baseFor(path);
  let resp: Response;
  try {
    resp = await fetch(`${base}${path}`, { ...init, headers });
  } catch (e) {
    const err = new ApiError(
      `Network error reaching the API at ${base}. Is the owning service running (Pilots :8602, data :8603, metrics :8604)?`,
      0
    );
    // Offline fallback (Web App Resilience gap): the network is genuinely
    // unreachable, not just a server-side error — if we have a previously
    // cached response for this exact GET, attach it so useApi can render it
    // instead of an empty/error screen. See api/offlineCache.ts.
    if (cacheable) {
      const cached = readCacheEntry<T>(path);
      if (cached) {
        err.cachedData = cached.data;
        err.cachedAt = cached.cachedAt;
      }
    }
    throw err;
  }
  if (!resp.ok) {
    let msg = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body?.detail) msg = String(body.detail);
    } catch {
      /* non-JSON error body */
    }
    // A reachable server's own error response is a genuine failure, never
    // masked by stale cache data (only a network-unreachable GET falls back).
    throw new ApiError(msg, resp.status);
  }
  if (resp.status === 204) return undefined as T;
  const data = (await resp.json()) as T;
  if (cacheable) writeCacheEntry(path, data);
  return data;
}

// ---- Live client (shape-identical to mockApi) ----
const liveApi = {
  health: () => http<{ status: string }>("/health"),
  listPilots: () => http<PilotSummary[]>("/pilots"),
  getPilot: (id: string) =>
    http<PilotDetail>(`/pilots/${encodeURIComponent(id)}`),
  getPerformance: (id: string, range: PerfRange) =>
    http<PerformanceResponse>(
      `/pilots/${encodeURIComponent(id)}/performance?range=${range}`
    ),
  getHoldings: (id: string) =>
    http<Holding[]>(`/pilots/${encodeURIComponent(id)}/holdings`),
  simulatePilotAllocation: (pilotId: string, payload: PilotSimulationRequest) =>
    http<PilotSimulationResult>(`/pilots/${encodeURIComponent(pilotId)}/simulate`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getUniverse: () => http<UniverseResponse>("/universe"),
  // Ranked BUY picks from the latest snapshot (pilots base, :8602).
  getRecommendations: (limit = 25) =>
    http<RecommendationsResponse>(`/recommendations?limit=${limit}`),
  getThresholds: () => http<Thresholds>("/thresholds"),
  getSymbol: (ticker: string) =>
    http<SymbolDetail>(`/symbols/${encodeURIComponent(ticker)}`),
  // Symbol-vs-symbol comparison (2-5 tickers); server de-dupes/upper-cases,
  // so the raw list is passed through as-is.
  getSymbolsCompare: (tickers: string[]) =>
    http<SymbolCompareResponse>(
      `/symbols/compare?symbols=${encodeURIComponent(tickers.join(","))}`
    ),
  getPortfolio: () => http<Portfolio>("/portfolio"),
  getEquityCurve: (range: PerfRange) =>
    http<EquityCurveResponse>(`/portfolio/equity-curve?range=${range}`),
  getRealized: () => http<RealizedPerformance>("/portfolio/realized"),
  getPortfolioAttribution: (lookbackDays = 60) =>
    http<PortfolioAttribution>(
      `/portfolio/attribution?lookback_days=${lookbackDays}`
    ),
  // Manual-input calculator (POST-with-a-body, but a stateless read-tier
  // endpoint -- nothing is persisted). Distinct from getPortfolioAttribution
  // above, which is auto-derived from real holdings.
  getBrinsonFachlerAttribution: (rows: BrinsonFachlerRow[]) =>
    http<BrinsonFachlerResult>("/portfolio/attribution/brinson-fachler", {
      method: "POST",
      body: JSON.stringify({ rows }),
    }),
  getAlerts: (limit = 50) => http<AlertsFeed>(`/alerts?limit=${limit}`),
  getForecast: (ticker: string, horizon = 30) =>
    http<ForecastSkill>(
      `/symbols/${encodeURIComponent(ticker)}/forecast?horizon=${horizon}`
    ),
  getRollingBeta: (ticker: string, window = 60) =>
    http<RollingBeta>(
      `/symbols/${encodeURIComponent(ticker)}/rolling-beta?window=${window}`
    ),
  // `n` re-derives `selected` server-side from the already-persisted rank
  // ordering (no recompute) -- cheap enough to refetch on every N-slider
  // change rather than re-ranking client-side.
  getSectorSelection: (target: string, n = 3) =>
    http<SectorSelectionView>(
      `/sector/selection?target=${encodeURIComponent(target)}&n=${n}`
    ),
  getModels: () => http<ModelRow[]>("/models"),
  getOptions: () => http<OptionsMatrix>("/options"),
  getSymbolOptions: (ticker: string) =>
    http<SymbolOptions>(`/symbols/${encodeURIComponent(ticker)}/options`),
  getOptionsChain: (ticker: string, expiration?: string) =>
    http<OptionChainResponse>(
      `/data/options/chain/${encodeURIComponent(ticker)}${expiration ? `?expiration=${encodeURIComponent(expiration)}` : ""}`
    ),
  getPairs: () => http<PairsRadar>("/pairs"),
  // ---- On-demand Options/Pairs recompute (data base, :8603) — webapp porting
  // backlog items 8a/8b. Distinct from getOptions/getPairs above (which only
  // ever serve the last PIPELINE-WRITTEN artifact): these POSTs recompute
  // synchronously against operator-chosen parameters/symbols, capped small.
  // A 422 (too few/many symbols, identical Y/X) throws ApiError the normal
  // way via http()'s shared error path -- callers enforce the cap client-side
  // (matching SymbolComparison.tsx's precedent) so this is rarely hit live.
  analyzePairs: (req: PairsAnalyzeRequest) =>
    http<PairsAnalyzeResult>("/data/pairs/analyze", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  scanPairs: (req: PairsScanRequest) =>
    http<PairsScanResult>("/data/pairs/scan", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  recomputeOptions: (req: OptionsRecomputeRequest) =>
    http<OptionsRecomputeResult>("/data/options/recompute", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  postOptionsOrder: (req: OptionsOrderRequest) =>
    http<OptionsOrderResult>("/brokerage/options/order", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  getObservabilitySummary: (range: PerfRange, horizon = 30) =>
    http<ObservabilitySummary>(
      `/observability/summary?range=${range}&horizon=${horizon}`
    ),
  putMacroGate: (enabled: boolean, reason: string) =>
    http<MacroGateUpdateResult>("/observability/macro-gate", {
      method: "PUT",
      body: JSON.stringify({ enabled, reason }),
    }),
  getObservabilityLogs: (limit = 300) =>
    http<LogAggregation>(`/observability/logs?limit=${limit}`),
  getStrategyMatrix: () => http<StrategyMatrix>("/strategy/matrix"),
  getStrategyHealth: () => http<StrategyHealthRow[]>("/strategy/health"),
  getValidationTrend: () => http<ValidationTrendSnapshot>("/strategy/validation-trend"),
  // Read-only -- deliberately no trigger endpoint (see GravityAuditStatus's
  // doc comment in types.ts / the backend endpoint's own docstring for why).
  getGravityAuditStatus: () => http<GravityAuditStatus>("/gravity/audit-status"),
  // ---- Recommendation Tracking & Calibration (default pilots base, :8602) ----
  getCalibrationSummary: (horizon = 30) =>
    http<CalibrationSummary>(`/calibration/summary?horizon=${horizon}`),
  getEdgeByStrategy: () =>
    http<EdgeByStrategy>("/calibration/edge-by-strategy"),
  logDecision: (body: DecisionCreateRequest) =>
    http<DecisionCreateResult>("/decisions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // Standalone, paginated, symbol-filterable read -- distinct from
  // getCalibrationSummary's bundled, fixed-size recent-decisions preview.
  // Used by SymbolDetail's per-symbol decision journal section.
  getDecisions: (opts?: { symbol?: string; limit?: number }) => {
    const params = new URLSearchParams();
    if (opts?.symbol) params.set("symbol", opts.symbol);
    params.set("limit", String(opts?.limit ?? 20));
    return http<DecisionEntry[]>(`/decisions?${params.toString()}`);
  },
  getCommands: () => http<CommandManifest>("/commands"),
  getExecutionQueue: (params?: ExecutionQueueParams) => {
    const q = new URLSearchParams();
    if (params?.action && params.action !== "ALL") q.set("action", params.action);
    if (params?.follow_type && params.follow_type !== "ALL") q.set("follow_type", params.follow_type);
    if (params?.status_filter && params.status_filter !== "ALL") q.set("status_filter", params.status_filter);
    if (params?.min_conviction !== undefined && params.min_conviction > 0)
      q.set("min_conviction", String(params.min_conviction));
    const queryStr = q.toString();
    return http<ExecutionQueue>(`/execution-queue${queryStr ? `?${queryStr}` : ""}`);
  },
  // ---- Data API (data_api.py, :8603) + Metrics API (metrics_api.py, :8604) ----
  // Routed by path prefix (see baseFor); these are the Phase-4 Data Explorer,
  // Signal Breakdown, and Forecast Viewer screens' data sources.
  getDataBars: (symbol: string, lookbackDays = 252) =>
    http<Bar[]>(
      `/data/bars/${encodeURIComponent(symbol)}?lookback_days=${lookbackDays}`
    ),
  getDataFundamentals: (symbol: string) =>
    http<Fundamentals>(`/data/fundamentals/${encodeURIComponent(symbol)}`),
  // On-demand FMP peer-comparison ticker group (settings.FMP_PEERS_ENABLED,
  // default False -> {peers: [], reason: "..."} with zero network calls).
  // Powers SymbolComparison.tsx's "Suggest peers for this ticker" affordance.
  // A DIFFERENT gate/call-site from the options-matrix's own batched peer
  // fetch (FMP_OPTIONS_CONTEXT_ENABLED) -- this one is a single per-click
  // user-triggered lookup.
  getPeers: (symbol: string) =>
    http<{ symbol: string; peers: string[]; reason: string | null }>(
      `/data/peers/${encodeURIComponent(symbol)}`
    ),
  getMacro: () => http<MacroSnapshot>("/data/macro"),
  getMacroHistory: (series = "VIXCLS", lookbackDays = 180) =>
    http<MacroHistorySeries>(
      `/data/macro/history?series=${encodeURIComponent(series)}&lookback_days=${lookbackDays}`
    ),
  // The operator's configured universe (settings.DEFAULT_TICKERS) — read + PUT
  // (full-list replace). The Data Explorer's add/remove control does a
  // read-modify-write against these two (data base, :8603).
  getDataUniverse: () => http<UniverseListResponse>("/data/universe"),
  updateDataUniverse: (symbols: string[]) =>
    http<{ status: string; symbols: string[] }>("/data/universe", {
      method: "PUT",
      body: JSON.stringify(symbols),
    }),
  // Latest quote(s) for a comma-separated symbol list (data base, :8603). The
  // Market Data connection diagnostic (Data Explorer) calls this ONE symbol
  // at a time so it can time each round trip independently with
  // `performance.now()` and build a genuine per-symbol latency/health picture
  // -- see components/MarketDataHealth.tsx for the full rationale.
  getDataQuotes: (symbols: string[]) =>
    http<QuotesResponse>(
      `/data/quotes?symbols=${encodeURIComponent(symbols.join(","))}`
    ),
  // Live portfolio & watchlist coverage-reconciliation report — computed
  // fresh on every call from data.portfolio_sync.build_sync_report (data
  // base, :8603). Distinct from getDataUniverse's plain add/remove list:
  // this is the FULL/EQUITY_ONLY/UNCOVERED market-data coverage breakdown.
  getSyncReport: () => http<SyncReportResponse>("/data/sync-report"),
  // Manual escape hatch to undo an automated symbol-rating exclusion
  // (pilots base, :8602 — NOT under "/data/", so baseFor() routes it to
  // BASE_URL, not DATA_BASE_URL). require_command_token-gated on the server.
  reincludeSymbol: (symbol: string) =>
    http<SymbolReincludeResult>(`/universe/${encodeURIComponent(symbol)}/reinclude`, {
      method: "POST",
    }),
  getSignalBreakdown: (symbol: string) =>
    http<SignalBreakdown>(`/metrics/signals/${encodeURIComponent(symbol)}`),
  getSignalImportance: (symbols: string[]) =>
    http<SignalImportance>(
      `/metrics/signals/importance?symbols=${symbols.map(encodeURIComponent).join(",")}`
    ),
  getSentimentDynamics: (symbol: string) =>
    http<SentimentDynamics>(`/metrics/sentiment/${encodeURIComponent(symbol)}`),
  getSentimentHistory: (symbol: string, lookbackDays = 180) =>
    http<SentimentHistory>(
      `/data/sentiment/${encodeURIComponent(symbol)}/history?lookback_days=${lookbackDays}`
    ),
  // ---- On-demand AI generation (data base, :8603) — operator-triggered only,
  // never auto-run. Each POST returns an honest available/reason/payload
  // envelope (llm/schemas.py-backed); a non-2xx still throws ApiError the
  // normal way via http()'s shared error path.
  generateCommentary: (symbol: string) =>
    http<AiCommentaryResponse>(`/data/ai/commentary/${encodeURIComponent(symbol)}`, {
      method: "POST",
    }),
  generateChart: (symbol: string) =>
    http<AiChartResponse>(`/data/ai/chart/${encodeURIComponent(symbol)}`, {
      method: "POST",
    }),
  generateResearch: (symbol: string) =>
    http<AiResearchResponse>(`/data/ai/research/${encodeURIComponent(symbol)}`, {
      method: "POST",
    }),
  getForecastResult: (symbol: string) =>
    http<ForecastResult>(`/metrics/forecast/${encodeURIComponent(symbol)}`),
  getTransformerForecast: (symbol: string) =>
    http<TransformerForecastResponse>(
      `/pilots/options/ai/transformer-forecast?symbol=${encodeURIComponent(symbol)}`
    ),
  runDiffusionStressTest: (req: DiffusionStressRequest) =>
    http<DiffusionStressResponse>("/pilots/options/ai/diffusion-stress-test", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  optimizeHrpCvar: (req: HrpCvarOptimizeRequest) =>
    http<HrpCvarOptimizeResponse>("/pilots/portfolio/optimize/hrp-cvar", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  optimizeAlmgrenChriss: (req: AlmgrenChrissOptimizeRequest) =>
    http<AlmgrenChrissOptimizeResponse>("/pilots/execution/optimize/almgren-chriss", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  // Currently unused: no screen/component calls this yet -- fully wired
  // (types, client, mock fixture) but available for a future UI wire-up,
  // not dead from disuse.
  routeFixOrder: (req: FixRouteOrderRequest) =>
    http<FixRouteOrderResponse>("/pilots/execution/fix/route", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  getFixSessionStatus: () =>
    http<FixSessionStatusResponse>("/pilots/execution/fix/session/status"),
  sendFixTestRequest: (req?: FixTestRequestPayload) =>
    http<FixSessionControlResponse>("/pilots/execution/fix/session/test-request", {
      method: "POST",
      body: JSON.stringify(req || {}),
    }),
  resetFixSequence: (req: FixResetSeqRequest) =>
    http<FixSessionControlResponse>("/pilots/execution/fix/session/reset-seq", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  reconnectFixSession: () =>
    http<FixSessionControlResponse>("/pilots/execution/fix/session/reconnect", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  setStrategyModules: (body: StrategyModulesUpdate) =>
    http<StrategyModulesUpdateResult>("/strategy/modules", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  // ---- General runtime tunables editor (pilots base, :8602) ----
  // Read the allowlisted, non-secret settings grouped for display; write only
  // the changed keys back.
  //
  // Whether a PUT reaches the RUNNING process is now per key, not a blanket
  // "no": see each field's `liveness.applies` on the GET, and the write's own
  // `per_key_applies` for what actually happened.
  //
  // `confirm` echoes each DANGEROUS_KEYS field's own name back
  // (`{ ADVISORY_ONLY: "ADVISORY_ONLY" }`). Omitting it for such a key rejects
  // that key with `confirmation_required` — per key, so the rest of the batch
  // still writes.
  getCronStatus: () => http<CronStatus>("/system/cron-status"),
  getTunables: () => http<TunablesResponse>("/settings/tunables"),
  updateTunables: (
    values: Record<string, number | boolean | string>,
    confirm: SettingsConfirmMap = {},
  ) =>
    http<TunablesUpdateResult>("/settings/tunables", {
      method: "PUT",
      body: JSON.stringify({ values, confirm }),
    }),
  getSentimentSettings: () => http<TunablesResponse>("/settings/sentiment"),
  updateSentimentSettings: (
    values: Record<string, number | boolean | string>,
    confirm: SettingsConfirmMap = {},
  ) =>
    http<TunablesUpdateResult>("/settings/sentiment", {
      method: "PUT",
      body: JSON.stringify({ values, confirm }),
    }),
  getCacheLongShortSettings: () => http<TunablesResponse>("/settings/cache-long-short"),
  updateCacheLongShortSettings: (
    values: Record<string, number | boolean | string>,
    confirm: SettingsConfirmMap = {},
  ) =>
    http<TunablesUpdateResult>("/settings/cache-long-short", {
      method: "PUT",
      body: JSON.stringify({ values, confirm }),
    }),
  getSectorSelectionSettings: () => http<TunablesResponse>("/settings/sector-selection"),
  updateSectorSelectionSettings: (
    values: Record<string, number | boolean | string>,
    confirm: SettingsConfirmMap = {},
  ) =>
    http<TunablesUpdateResult>("/settings/sector-selection", {
      method: "PUT",
      body: JSON.stringify({ values, confirm }),
    }),
  getFmpSettings: () => http<TunablesResponse>("/settings/fmp"),
  updateFmpSettings: (
    values: Record<string, number | boolean | string>,
    confirm: SettingsConfirmMap = {},
  ) =>
    http<TunablesUpdateResult>("/settings/fmp", {
      method: "PUT",
      body: JSON.stringify({ values, confirm }),
    }),
  
  getFeatureFlags: () => http<TunablesResponse>("/settings/feature-flags"),
  updateFeatureFlags: (values: Record<string, any>, confirm?: import("./types").SettingsConfirmMap) =>
    http<TunablesUpdateResult>("/settings/feature-flags", {
      method: "PUT",
      body: JSON.stringify({ values, confirm }),
    }),

  getEtfTransmissionSettings: () => http<TunablesResponse>("/settings/etf-transmission"),
  updateEtfTransmissionSettings: (
    values: Record<string, number | boolean | string>,
    confirm: SettingsConfirmMap = {},
  ) =>
    http<TunablesUpdateResult>("/settings/etf-transmission", {
      method: "PUT",
      body: JSON.stringify({ values, confirm }),
    }),
  getFollows: () => http<Follow[]>("/follows"),
  follow: (id: string, amount: number) =>
    http<FollowResult>(`/pilots/${encodeURIComponent(id)}/follow`, {
      method: "POST",
      body: JSON.stringify({ amount }),
    }),
  getAutomationStatus: () => http<AutomationStatus>("/automation/status"),
  getAutomationSchedule: () => http<AutomationSchedule>("/automation/schedule"),
  // ---- Control API (orchestrator daemon, port 8601) — the Pipeline Dashboard's
  // live daemon status + stage-scoped run triggers. A non-2xx (409 already
  // running / 423 kill-switch-paused / 401/403 auth) throws ApiError the normal
  // way; the screen branches on ApiError.status to render each honestly.
  getControlStatus: () => http<ControlStatus>("/status"),
  postControlRun: () =>
    http<{ run_id: string; state: string }>("/run", { method: "POST" }),
  postControlPipelineData: () =>
    http<{ run_id: string; state: string; mode: string }>("/pipeline/data", {
      method: "POST",
    }),
  postControlPipelineMetrics: () =>
    http<{ run_id: string; state: string; mode: string }>("/pipeline/metrics", {
      method: "POST",
    }),
  /**
   * GET /runs/history — durable run history read from the daemon's
   * pipeline_runs DB table (desktop/run_history_store.py), independent of
   * GET /status's in-memory run_history ring. Survives a daemon restart.
   * Routes to CONTROL_BASE_URL via baseFor's `/run` prefix match.
   */
  getRunHistory: (limit = 50) =>
    http<RunRecord[]>(`/runs/history?limit=${limit}`),
  /**
   * POST /automation/run. Mirrors gui/daemon_client.py's own non-raising
   * TriggerResponse contract: a documented RUNTIME outcome (queued, already
   * running, kill-switch-paused, daemon unreachable) resolves as data here,
   * NEVER throws -- only a genuine config/auth problem with THIS request
   * (this API's own FOLLOW_API_TOKEN gate returning 401/403, or a network
   * failure) throws ApiError the normal way, same as every other endpoint.
   * Deliberately bypasses the shared `http()` helper (a bare fetch instead)
   * because http()'s generic error path does `String(body.detail)`, which
   * would mangle the STRUCTURED detail objects the 409/423 responses carry
   * (`{detail, run_id}` / `{detail, kill_switch_reason}`) into "[object Object]".
   */
  triggerRun: async (): Promise<TriggerRunResult> => {
    const headers: Record<string, string> = {};
    if (TOKEN) headers["Authorization"] = `Bearer ${TOKEN}`;

    let resp: Response;
    try {
      resp = await fetch(`${BASE_URL}/automation/run`, { method: "POST", headers });
    } catch {
      return {
        ok: false, run_id: null, state: null, error: "unavailable",
        existing_run_id: null, kill_switch_reason: null,
      };
    }

    let body: { detail?: unknown; run_id?: string; state?: string } | null = null;
    try {
      body = await resp.json();
    } catch {
      /* non-JSON body */
    }

    if (resp.status === 202) {
      return {
        ok: true, run_id: body?.run_id ?? null, state: body?.state ?? null,
        error: null, existing_run_id: null, kill_switch_reason: null,
      };
    }
    if (resp.status === 409) {
      const detail = body?.detail as { run_id?: string } | undefined;
      return {
        ok: false, run_id: null, state: null, error: "already_running",
        existing_run_id: detail?.run_id ?? null, kill_switch_reason: null,
      };
    }
    if (resp.status === 423) {
      const detail = body?.detail as { kill_switch_reason?: string } | undefined;
      return {
        ok: false, run_id: null, state: null, error: "kill_switch_active",
        existing_run_id: null, kill_switch_reason: detail?.kill_switch_reason ?? null,
      };
    }
    if (resp.status === 503) {
      return {
        ok: false, run_id: null, state: null, error: "unavailable",
        existing_run_id: null, kill_switch_reason: null,
      };
    }
    // 401/403 (this API's own auth gate) or anything else undocumented is a
    // genuine configuration problem for THIS request, not a documented
    // daemon-runtime outcome -- surface it like every other endpoint's error.
    const detailStr = typeof body?.detail === "string" ? body.detail : undefined;
    throw new ApiError(detailStr ?? `${resp.status} ${resp.statusText}`, resp.status);
  },
  pauseAutomation: (reason: string) =>
    http<KillSwitchActionResult>("/automation/pause", {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  resumeAutomation: (reason: string) =>
    http<KillSwitchActionResult>("/automation/resume", {
      method: "POST",
      body: JSON.stringify({ confirm: true, reason }),
    }),
  setAutomationInterval: (seconds: number) =>
    http<IntervalUpdateResult>("/automation/schedule/interval", {
      method: "PUT",
      body: JSON.stringify({ interval_seconds: seconds }),
    }),
  setExecutionMode: (req: ExecutionModeUpdateRequest) =>
    http<ExecutionModeUpdateResult>("/automation/execution-mode", {
      method: "PUT",
      body: JSON.stringify(req),
    }),
  getBrokerageStatus: () => http<BrokerageStatus>("/brokerage/status"),
  getLlmStatus: () => http<LlmStatus>("/llm/status"),
  putLlmSetting: (key: string, value: boolean | string) =>
    http<LlmSettingUpdateResult>("/llm/setting", {
      method: "PUT",
      body: JSON.stringify({ key, value }),
    }),
  connectBrokerage: (creds: BrokerageConnectRequest) =>
    http<BrokerageLoginJob>("/brokerage/connect", {
      method: "POST",
      body: JSON.stringify(creds),
    }),
  disconnectBrokerage: () =>
    http<BrokerageDisconnectResult>("/brokerage/disconnect", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  // No body -- the backend re-authenticates with whatever RH_USERNAME/
  // RH_PASSWORD is already configured server-side.
  refreshBrokerage: () =>
    http<BrokerageRefreshResult>("/brokerage/refresh", { method: "POST" }),
  getBrokerageLoginStatus: (jobId: string) =>
    http<BrokerageLoginJob>(`/brokerage/login/status/${encodeURIComponent(jobId)}`),
  cancelBrokerageLogin: (jobId: string) =>
    http<BrokerageLoginCancelResult>(`/brokerage/login/cancel/${encodeURIComponent(jobId)}`, {
      method: "POST",
    }),
  // ---- Agentic Trading tab ----
  getAgenticStatus: () => http<AgenticStatus>("/agentic/status"),
  getAgenticDiscovery: () => http<AgenticDiscovery>("/agentic/discovery"),
  putScanConfig: (req: ScanConfigRequest) =>
    http<ScanConfigResult>("/agentic/scan-config", {
      method: "PUT",
      body: JSON.stringify(req),
    }),
  watchCandidate: (symbol: string) =>
    http<WatchResult>("/agentic/watch", {
      method: "POST",
      body: JSON.stringify({ symbol }),
    }),
  // ---- RLHF Calibration Review Queue (nested inside Agentic Trading; pilots
  // base, :8602) — rating an AI-proposed hypothetical paper trade, never a
  // live order. See types.ts's RlhfProposal/RlhfSummary doc comments.
  getRlhfSummary: (limit = 50) => http<RlhfSummary>(`/rlhf/summary?limit=${limit}`),
  submitRlhfReview: (id: number, body: RlhfReviewSubmitRequest) =>
    http<RlhfReviewSubmitResult>(`/rlhf/proposals/${id}/review`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  exportRlhfSft: () => http<RlhfSftExportResult>("/rlhf/export-sft", { method: "POST" }),
  // ---- Job Execution & Streaming ----
  createJob: (job_type: string, params?: Record<string, unknown>) =>
    http<JobRecord>("/jobs", {
      method: "POST",
      body: JSON.stringify({ job_type, params }),
    }),
  getJobStatus: (job_id: string) => http<JobRecord>(`/jobs/${job_id}`),
  cancelJob: (job_id: string) =>
    http<{ job_id: string; cancelled: boolean }>(`/jobs/${job_id}/cancel`, {
      method: "POST",
    }),
  restartDaemon: () =>
    http<RestartDaemonResult>("/daemon/restart", {
      method: "POST",
    }),
  // ---- G15: durable per-symbol Claude-vs-Gemini disagreement (data base, :8603) ----
  getAiDisagreements: () => http<AiDisagreementsResponse>("/data/ai/disagreements"),
  // ---- AI Models listing (data base, :8603) ----
  getAiModels: () => http<AiModelsResponse>("/data/ai/models"),
  // ---- Report Library (G5) + Dead-Letter Queue (G6) ----
  getReports: () => http<ReportManifest>("/reports"),
  getReport: (name: string) =>
    http<ReportContent>(`/reports/${encodeURIComponent(name)}`),
  getDeadLetter: () => http<DeadLetterQueue>("/dead-letter"),
  retryDeadLetter: (symbol: string) =>
    http<DeadLetterRetryResult>("/dead-letter/retry", {
      method: "POST",
      body: JSON.stringify({ symbol }),
    }),
  // ---- Prompt Registry (pilots base, :8602) — webapp parity gap G4 ----
  getPrompts: () => http<PromptListResponse>("/prompts"),
  getPrompt: (id: string, version?: string) =>
    http<PromptBody>(
      `/prompts/${encodeURIComponent(id)}${version ? `?version=${encodeURIComponent(version)}` : ""}`
    ),
  putPromptPin: (req: PromptPinRequest) =>
    http<PromptPinResult>("/prompts/pin", {
      method: "PUT",
      body: JSON.stringify(req),
    }),
  // ---- Universe sync write (data base, :8603) — webapp parity gap G8 ----
  postDataSync: () => http<DataSyncResult>("/data/sync", { method: "POST" }),
  // ---- Market Data provider status (data base, :8603) — webapp parity gap G9 ----
  getProviderStatus: () => http<ProviderStatus>("/data/provider-status"),
  // ---- Phase 6 additions ----
  getMacroSentiment: () => http<MacroSentimentResponse>("/data/macro/sentiment"),
  getOrderBookLadder: (symbol: string) =>
    http<OrderBookLadderResponse>(`/data/ladder/${encodeURIComponent(symbol)}`),
  getModelComparison: () => http<ModelComparisonResponse>("/metrics/models/comparison"),
  getOptionsAnalytics: (symbol: string) =>
    http<OptionsAnalyticsSummaryResponse>(`/metrics/options/analytics/${encodeURIComponent(symbol)}`),
  getForecastBackfill: () => http<ForecastBackfillSummary>("/pilots/forecast_backfill"),
  /**
   * POST /pilots/forecast_backfill/run. Deliberately bypasses the shared
   * http() helper (a bespoke fetch instead), mirroring triggerRun()'s own
   * precedent above: http()'s generic error path does `String(body.detail)`,
   * which would mangle the STRUCTURED 409 body
   * (`{"detail": {"detail": "...", "job_id": "<id>"}}`) the backend's
   * single-flight guard (`ml/forecast_backfill_job.py::start_job`) returns
   * into the literal string "[object Object]" and lose the existing job's
   * id entirely. A 409 here throws `ForecastBackfillConflictError`
   * (carrying that job_id) instead of a generic ApiError, so
   * useBackfillJob's start() can catch it specifically and start polling
   * the already-running job -- see api/pilots_api.py's
   * run_forecast_backfill_endpoint docstring for why the id is included in
   * the 409 body at all. Every other non-2xx (or a network failure) still
   * throws the normal ApiError, same as every http()-backed endpoint.
   */
  runForecastBackfill: async (params?: { tickers?: string[]; start_date?: string; end_date?: string; use_fmp?: boolean; strategy_ids?: string[]; theta_c?: number }): Promise<ForecastBackfillJob> => {
    const path = "/pilots/forecast_backfill/run";
    const headers: Record<string, string> = {
      Accept: "application/json",
      "Content-Type": "application/json",
    };
    if (TOKEN) headers["Authorization"] = `Bearer ${TOKEN}`;

    const base = baseFor(path);
    let resp: Response;
    try {
      resp = await fetch(`${base}${path}`, {
        method: "POST",
        headers,
        body: JSON.stringify(params ?? {}),
      });
    } catch {
      throw new ApiError(
        `Network error reaching the API at ${base}. Is the owning service running (Pilots :8602)?`,
        0
      );
    }

    let body: { detail?: unknown } | null = null;
    try {
      body = await resp.json();
    } catch {
      /* non-JSON body */
    }

    if (resp.status === 409) {
      const detail = body?.detail as { detail?: string; job_id?: string } | undefined;
      throw new ForecastBackfillConflictError(
        detail?.detail ?? "A forecast backfill run is already in progress.",
        detail?.job_id ?? null
      );
    }
    if (!resp.ok) {
      const detailStr = typeof body?.detail === "string" ? body.detail : undefined;
      throw new ApiError(detailStr ?? `${resp.status} ${resp.statusText}`, resp.status);
    }
    return body as unknown as ForecastBackfillJob;
  },
  getForecastBackfillJobStatus: (jobId: string) =>
    http<ForecastBackfillJob>(`/pilots/forecast_backfill/status/${encodeURIComponent(jobId)}`),
  cancelForecastBackfillJob: (jobId: string) =>
    http<ForecastBackfillJob>(`/pilots/forecast_backfill/cancel/${encodeURIComponent(jobId)}`, {
      method: "POST",
    }),
    
  // ---- Cache Long/Short ----
  getClsConcentratedPositions: () =>
    http<{ positions: CacheLongShortConcentratedPosition[] }>("/pilots/cache-long-short/concentrated-positions"),
  getClsDashboard: () =>
    http<CacheLongShortDashboard>("/pilots/cache-long-short/dashboard"),
  getClsPendingApprovals: () =>
    http<CacheLongShortPendingTrade[]>("/pilots/cache-long-short/pending-approvals"),
  simulateCls(req: CacheLongShortSimulateRequest): Promise<CacheLongShortSimulateResult> {
    return http<CacheLongShortSimulateResult>("/data/cache-long-short/simulate", {
      method: "POST",
      body: JSON.stringify(req),
    });
  },
  startCls: (req: CacheLongShortStartRequest) =>
    http<CacheLongShortStartResult>("/pilots/cache-long-short/start", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  approveClsBulk: (lotIds: number[]) =>
    http<CacheLongShortApproveBulkResult>("/pilots/cache-long-short/approve-bulk", {
      method: "POST",
      body: JSON.stringify({ lot_ids: lotIds }),
    }),
  // ---- Paper Broker ----
  getPaperBrokerAccount: () => http<PaperBrokerAccount>("/pilots/paper-broker/account"),
  getPaperBrokerPositions: () => http<PaperBrokerPosition[]>("/pilots/paper-broker/positions"),
  getPaperBrokerOrders: (limit = 100) => http<PaperBrokerOrder[]>(`/pilots/paper-broker/orders?limit=${limit}`),
  resetPaperBroker: (cash: number) =>
    http<PaperBrokerResetResult>("/pilots/paper-broker/reset", {
      method: "POST",
      body: JSON.stringify({ cash }),
    }),
  getPaperBrokerSettings: () => http<TunablesResponse>("/settings/paper-broker"),
  updatePaperBrokerSettings: (
    values: Record<string, number | boolean | string>,
    confirm: import("./types").SettingsConfirmMap = {},
  ) =>
    http<TunablesUpdateResult>("/settings/paper-broker", {
      method: "PUT",
      body: JSON.stringify({ values, confirm }),
    }),
  getStrategyOptionsCandidates: (symbols?: string[]) => {
    const q = symbols && symbols.length > 0 ? `?symbols=${encodeURIComponent(symbols.join(","))}` : "";
    return http<StrategyOptionsCandidatesResponse>(`/pilots/paper-broker/strategy-options/candidates${q}`);
  },
  executeStrategyOptions: (symbols?: string[], dryRun = false, maxNotional?: number) =>
    http<StrategyOptionsExecutionResult>("/pilots/paper-broker/strategy-options/execute", {
      method: "POST",
      body: JSON.stringify({ symbols, dry_run: dryRun, max_notional: maxNotional }),
    }),
  getPaperBrokerGreeks: () => http<PortfolioGreeks>("/pilots/paper-broker/greeks"),
  runOptionsBacktest: (params: OptionsBacktestParams) =>
    http<OptionsBacktestResponse>("/pilots/options/backtest", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  getOptionsMetaModelStatus: () => http<OptionsMetaModelStatus>("/pilots/options/meta-model/status"),
  retrainOptionsMetaModel: () =>
    http<OptionsMetaModelRetrainResult>("/pilots/options/meta-model/retrain", {
      method: "POST",
    }),
  settleExpiredPaperOptions: () =>
    http<PaperBrokerSettleExpiredResult>("/pilots/paper-broker/settle-expired", {
      method: "POST",
    }),
  getVolSurface: (symbol: string, expiration?: string) =>
    http<VolSurfaceResponse>(
      `/pilots/options/vol-surface?symbol=${encodeURIComponent(symbol)}${expiration ? `&expiration=${encodeURIComponent(expiration)}` : ""}`
    ),
  getScenarioMatrix: (params?: { spot_shifts?: number[]; iv_shifts?: number[]; days_forward?: number }) =>
    http<ScenarioMatrixResponse>("/pilots/paper-broker/scenario-matrix", {
      method: "POST",
      body: params ? JSON.stringify(params) : undefined,
    }),
  getDeltaHedgePreview: () =>
    http<DeltaHedgePreview>("/pilots/paper-broker/delta-hedge/preview"),
  executeDeltaHedge: (params?: { target_delta?: number; confirm?: boolean }) =>
    http<DeltaHedgeResult>("/pilots/paper-broker/delta-hedge/execute", {
      method: "POST",
      body: params ? JSON.stringify(params) : undefined,
    }),
  managePaperOptionsExits: (params?: { force?: boolean }) =>
    http<ManageExitsResult>("/pilots/paper-broker/manage-exits", {
      method: "POST",
      body: params ? JSON.stringify(params) : undefined,
    }),
  rollPaperOptionPosition: (request: RollOrderRequest) =>
    http<OptionsOrderResult>("/pilots/paper-broker/roll", {
      method: "POST",
      body: JSON.stringify(request),
    }),
  getEarningsCrushCandidates: (symbols?: string[]) => {
    const q = symbols && symbols.length > 0 ? `?symbols=${encodeURIComponent(symbols.join(","))}` : "";
    return http<EarningsCrushCandidatesResponse>(`/pilots/options/earnings-crush/candidates${q}`);
  },
  executeEarningsCrushTrade: (candidate: EarningsCrushCandidate | { symbol: string; strategy?: string; wing_multiplier?: number }) =>
    http<EarningsCrushExecutionResult>("/pilots/options/earnings-crush/execute", {
      method: "POST",
      body: JSON.stringify(candidate),
    }),
  getUnusualOptionsFlow: (params?: { symbol?: string; min_vol_oi?: number; min_notional?: number }) => {
    const q = new URLSearchParams();
    if (params?.symbol) q.set("symbol", params.symbol);
    if (params?.min_vol_oi != null) q.set("min_vol_oi", String(params.min_vol_oi));
    if (params?.min_notional != null) q.set("min_notional", String(params.min_notional));
    const qs = q.toString() ? `?${q.toString()}` : "";
    return http<UnusualOptionsFlowResponse>(`/pilots/options/flow/unusual${qs}`);
  },
  getOptionsFlowSentiment: (symbol: string) =>
    http<FlowSentimentResponse>(`/pilots/options/flow/sentiment?symbol=${encodeURIComponent(symbol)}`),
  getHarRvForecast: (symbol: string) =>
    http<HarRvForecastResponse>(`/pilots/options/forecast/har-rv?symbol=${encodeURIComponent(symbol)}`),
  getVolMispricing: (symbol: string, expiration?: string) =>
    http<VolMispricingResponse>(
      `/pilots/options/forecast/mispricing?symbol=${encodeURIComponent(symbol)}${expiration ? `&expiration=${encodeURIComponent(expiration)}` : ""}`
    ),
  simulateGammaScalping: (request: GammaScalpRequest) =>
    http<GammaScalpResponse>("/pilots/options/gamma-scalp/simulate", {
      method: "POST",
      body: JSON.stringify(request),
    }),
  testOptionsAlert: (params?: { alert_type?: string; symbol?: string; dry_run?: boolean }) =>
    http<OptionsAlertTestResult>("/pilots/options/alerts/test", {
      method: "POST",
      body: params ? JSON.stringify(params) : undefined,
    }),
  getDispersionOpportunities: (index_symbol?: string) => {
    const q = index_symbol ? `?index_symbol=${encodeURIComponent(index_symbol)}` : "";
    return http<DispersionBasketResponse>(`/pilots/options/dispersion/opportunities${q}`);
  },
  executeDispersionBasket: (request: DispersionBasketOrderRequest | { opportunity_id?: string; index_symbol: string; regime?: string; basket_size_usd?: number }) =>
    http<DispersionExecutionResult>("/pilots/options/dispersion/execute", {
      method: "POST",
      body: JSON.stringify(request),
    }),
  getZeroDteSignals: (symbol?: string) => {
    const q = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
    return http<ZeroDteSignalResponse>(`/pilots/options/zero-dte/signals${q}`);
  },
  executeZeroDteTrade: (request: ZeroDteTradeRequest | { symbol: string; option_type: "CALL" | "PUT"; strike: number; contracts: number; entry_price?: number }) =>
    http<ZeroDteExecutionResult>("/pilots/options/zero-dte/execute", {
      method: "POST",
      body: JSON.stringify(request),
    }),
  getVpinMetrics: (symbol: string) =>
    http<VpinMetricsResponse>(`/pilots/options/vpin/metrics?symbol=${encodeURIComponent(symbol)}`),
  analyzeOptionsRouting: (request: SorAnalysisRequest) =>
    http<SorAnalysisResponse>("/pilots/options/sor/analyze", {
      method: "POST",
      body: JSON.stringify(request),
    }),
  simulateOptionsLegging: (request: LeggingSimulationRequest) =>
    http<LeggingSimulationResponse>("/pilots/options/sor/simulate-legging", {
      method: "POST",
      body: JSON.stringify(request),
    }),
  getOptionsGexProfile: (symbol: string) =>
    http<GexProfileResponse>(`/pilots/options/gex/profile?symbol=${encodeURIComponent(symbol)}`),
  simulateLobQueue: (request: LobQueueSimulationRequest) =>
    http<LobQueueSimulationResponse>("/pilots/options/lob/simulate-queue", {
      method: "POST",
      body: JSON.stringify(request),
    }),
  getCopulaPairsAnalysis: (pair?: string) => {
    const q = pair ? `?pair=${encodeURIComponent(pair)}` : "";
    return http<CopulaPairsResponse>(`/pilots/options/copula/pairs${q}`);
  },
  simulateMarketMakerAgent: (request: MarketMakerSimRequest) =>
    http<MarketMakerSimResponse>("/pilots/options/market-maker/simulate", {
      method: "POST",
      body: JSON.stringify(request),
    }),

  // ---- Tier D: AI Research Copilot, 3D Vol, Multi-Broker & SEC 606 ----

  synthesizeQuantResearch: (request: ResearchSynthesizeRequest) =>
    http<ResearchSynthesizeResponse>("/pilots/ai/research/synthesize", {
      method: "POST",
      body: JSON.stringify(request),
    }),
  runAutonomousBacktest: (request: AutonomousBacktestRequest) =>
    http<AutonomousBacktestResponse>("/pilots/ai/backtest/autonomous", {
      method: "POST",
      body: JSON.stringify(request),
    }),
  // Currently unused: no screen/component calls this yet -- fully wired
  // (types, client, mock fixture, and a real api/pilots_api.py route) but
  // available for a future UI wire-up, not dead from disuse.
  getVolSurface3DMesh: (symbol?: string) => {
    const q = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
    return http<VolSurface3DMeshResponse>(`/pilots/options/vol-surface/3d-mesh${q}`);
  },
  getMultiBrokerStatus: () =>
    http<MultiBrokerStatusResponse>("/pilots/execution/brokers/status"),
  triggerBrokerFailover: (request: BrokerFailoverRequest) =>
    http<BrokerFailoverResponse>("/pilots/execution/brokers/failover", {
      method: "POST",
      body: JSON.stringify(request),
    }),
  getSecRule606Report: (params?: { year?: number; quarter?: number; is_option?: boolean }) => {
    const q = new URLSearchParams();
    if (params?.year != null) q.set("year", String(params.year));
    if (params?.quarter != null) q.set("quarter", String(params.quarter));
    if (params?.is_option != null) q.set("is_option", String(params.is_option));
    const qs = q.toString() ? `?${q.toString()}` : "";
    return http<SecRule606ReportResponse>(`/pilots/execution/sec-606/report${qs}`);
  },

  // ---- Live Trade Approvals ----


  getPendingLiveTrades: () => http<{ proposals: LiveTradeProposal[] }>("/pilots/execution/pending"),
  approveLiveTrade: (token: string) =>
    http<LiveTradeProposal>(`/pilots/execution/${encodeURIComponent(token)}/approve`, { method: "POST" }),
  rejectLiveTrade: (token: string) =>
    http<LiveTradeProposal>(`/pilots/execution/${encodeURIComponent(token)}/reject`, { method: "POST" }),

  // ---- Dynamic Circuit Breaker ----
  getCircuitBreakerStatus: () => http<CircuitBreakerStatusResponse>("/risk/circuit-breaker/status"),
};

/**
 * The single API surface every screen consumes.
 *
 * The `: typeof liveApi` annotation is load-bearing: `api = USE_MOCK ? mockApi
 * : liveApi` would otherwise let `mockApi` and `liveApi` drift out of shape
 * silently (a mock method with the wrong return type, or a missing/extra
 * method, would typecheck). Annotating the union to `liveApi`'s shape makes
 * `tsc --noEmit` reject any such drift in the one place both are in scope.
 * (A real bug once shipped from exactly this gap — see docs/AUTOPILOT_PLAN.md.)
 */
export const api: typeof liveApi = USE_MOCK ? mockApi : liveApi;

/** Small runtime banner metadata for the UI (mode label etc.). */
export const apiMeta = {
  useMock: USE_MOCK,
  baseUrl: BASE_URL,
  hasToken: Boolean(TOKEN),
  mockMode: MOCK_META.mode,
};

export { ApiError };
