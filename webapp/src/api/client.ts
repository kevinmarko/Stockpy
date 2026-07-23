/**
 * client.ts — typed API client for api/pilots_api.py.
 *
 * Swapping mock -> live is a ONE-FLAG change: set VITE_USE_MOCK=false (and point
 * VITE_API_BASE_URL / VITE_API_TOKEN at the running FastAPI service). Every screen
 * imports `api` from here and never talks to fetch/mock directly, so the live
 * cutover touches no component code.
 */

import { mockApi, MOCK_META } from "./mock";
import { ApiError } from "./types";
import { readCacheEntry, writeCacheEntry } from "./offlineCache";
import type {
  AgenticDiscovery,
  AgenticStatus,
  AiChartResponse,
  AiCommentaryResponse,
  AiResearchResponse,
  AlertsFeed,
  AutomationSchedule,
  AutomationStatus,
  BrinsonFachlerResult,
  BrinsonFachlerRow,
  BrokerageConnectRequest,
  BrokerageConnectResult,
  BrokerageDisconnectResult,
  BrokerageStatus,
  CalibrationSummary,
  ControlStatus,
  DecisionCreateRequest,
  DecisionCreateResult,
  DecisionEntry,
  EdgeByStrategy,
  Follow,
  FollowResult,
  ForecastSkill,
  IntervalUpdateResult,
  ExecutionModeUpdateRequest,
  ExecutionModeUpdateResult,
  KillSwitchActionResult,
  LlmSettingUpdateResult,
  LlmStatus,
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
  PilotSummary,
  Portfolio,
  PortfolioAttribution,
  CurvePoint,
  RealizedPerformance,
  RollingBeta,
  RunRecord,
  StrategyMatrix,
  StrategyHealthRow,
  GravityAuditStatus,
  StrategyModulesUpdate,
  StrategyModulesUpdateResult,
  ValidationTrendSnapshot,
  SentimentDynamics,
  TunablesResponse,
  TunablesUpdateResult,
  SymbolDetail,
  SymbolCompareResponse,
  UniverseResponse,
  SyncReportResponse,
  RecommendationsResponse,
  UniverseListResponse,
  Thresholds,
  SymbolOptions,
  TriggerRunResult,
  Bar,
  Fundamentals,
  MacroSnapshot,
  QuotesResponse,
  SignalBreakdown,
  ForecastResult,
  CommandManifest,
  ExecutionQueue,
  ScanConfigRequest,
  ScanConfigResult,
  WatchResult,
} from "./types";

const BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8602"
).replace(/\/+$/, "");
// The Phase-4 data/metrics engines are SEPARATE FastAPI processes on their own
// ports (data_api :8603, metrics_api :8604) — they cannot be mounted into the
// Pilots API (its AST guard forbids the heavy-engine imports they require). So
// the client routes by path prefix to the right origin; each falls back to the
// Pilots base's host if unset (i.e. a single-origin reverse-proxy deployment
// where one host proxies /data/* and /metrics/* works with zero extra config).
const DATA_BASE_URL = (
  import.meta.env.VITE_DATA_API_BASE_URL ?? "http://localhost:8603"
).replace(/\/+$/, "");
const METRICS_BASE_URL = (
  import.meta.env.VITE_METRICS_API_BASE_URL ?? "http://localhost:8604"
).replace(/\/+$/, "");
// The Control API (orchestrator daemon: live status + stage-scoped run
// triggers) is ALSO a separate origin (:8601), not part of the Pilots API. The
// Pipeline Dashboard's /status, /run, /run/{id}/status and /pipeline/* calls
// must route here, or they 404 against the Pilots base in live mode. Falls back
// to the Pilots host if unset (single-origin reverse-proxy deployment).
const CONTROL_BASE_URL = (
  import.meta.env.VITE_CONTROL_API_BASE_URL ?? "http://localhost:8601"
).replace(/\/+$/, "");
const TOKEN = import.meta.env.VITE_API_TOKEN ?? "";

/** Route a request path to its owning service's base URL by prefix. */
function baseFor(path: string): string {
  if (path.startsWith("/data/")) return DATA_BASE_URL;
  if (path.startsWith("/metrics/")) return METRICS_BASE_URL;
  // Control API (:8601): daemon status + stage-scoped run triggers. Note
  // "/automation/run" is a PILOTS endpoint and correctly does NOT match here.
  if (
    path === "/status" ||
    path.startsWith("/run") ||
    path.startsWith("/pipeline/")
  ) {
    return CONTROL_BASE_URL;
  }
  return BASE_URL;
}

// Default to MOCK unless explicitly told to go live. This means a fresh checkout
// runs fully offline with zero config; flip VITE_USE_MOCK=false to hit the API.
export const USE_MOCK =
  (import.meta.env.VITE_USE_MOCK ?? "true").toLowerCase() !== "false";

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
    http<{ range: PerfRange; curve: CurvePoint[] | null }>(
      `/portfolio/equity-curve?range=${range}`
    ),
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
  getModels: () => http<ModelRow[]>("/models"),
  getOptions: () => http<OptionsMatrix>("/options"),
  getSymbolOptions: (ticker: string) =>
    http<SymbolOptions>(`/symbols/${encodeURIComponent(ticker)}/options`),
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
  getObservabilitySummary: (range: PerfRange, horizon = 30) =>
    http<ObservabilitySummary>(
      `/observability/summary?range=${range}&horizon=${horizon}`
    ),
  putMacroGate: (enabled: boolean, reason: string) =>
    http<MacroGateUpdateResult>("/observability/macro-gate", {
      method: "PUT",
      body: JSON.stringify({ enabled, reason }),
    }),
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
  getExecutionQueue: () => http<ExecutionQueue>("/execution-queue"),
  // ---- Data API (data_api.py, :8603) + Metrics API (metrics_api.py, :8604) ----
  // Routed by path prefix (see baseFor); these are the Phase-4 Data Explorer,
  // Signal Breakdown, and Forecast Viewer screens' data sources.
  getDataBars: (symbol: string, lookbackDays = 252) =>
    http<Bar[]>(
      `/data/bars/${encodeURIComponent(symbol)}?lookback_days=${lookbackDays}`
    ),
  getDataFundamentals: (symbol: string) =>
    http<Fundamentals>(`/data/fundamentals/${encodeURIComponent(symbol)}`),
  getMacro: () => http<MacroSnapshot>("/data/macro"),
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
  getSignalBreakdown: (symbol: string) =>
    http<SignalBreakdown>(`/metrics/signals/${encodeURIComponent(symbol)}`),
  getSentimentDynamics: (symbol: string) =>
    http<SentimentDynamics>(`/metrics/sentiment/${encodeURIComponent(symbol)}`),
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
  setStrategyModules: (body: StrategyModulesUpdate) =>
    http<StrategyModulesUpdateResult>("/strategy/modules", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  // ---- General runtime tunables editor (pilots base, :8602) ----
  // Read the allowlisted, non-secret settings grouped for display; write only
  // the changed keys back. The PUT does NOT reach the running process — see
  // TunablesResponse.applies ("next_daemon_restart").
  getTunables: () => http<TunablesResponse>("/settings/tunables"),
  updateTunables: (values: Record<string, number | boolean | string>) =>
    http<TunablesUpdateResult>("/settings/tunables", {
      method: "PUT",
      body: JSON.stringify({ values }),
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
    http<BrokerageConnectResult>("/brokerage/connect", {
      method: "POST",
      body: JSON.stringify(creds),
    }),
  disconnectBrokerage: () =>
    http<BrokerageDisconnectResult>("/brokerage/disconnect", {
      method: "POST",
      body: JSON.stringify({}),
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
