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
  AlertsFeed,
  AutomationSchedule,
  AutomationStatus,
  BrokerageConnectRequest,
  BrokerageConnectResult,
  BrokerageDisconnectResult,
  BrokerageStatus,
  Follow,
  FollowResult,
  ForecastSkill,
  IntervalUpdateResult,
  KillSwitchActionResult,
  LlmStatus,
  ModelRow,
  ObservabilitySummary,
  OptionsMatrix,
  PairsRadar,
  PerfRange,
  PerformanceResponse,
  PilotDetail,
  PilotSummary,
  Portfolio,
  PortfolioAttribution,
  CurvePoint,
  RealizedPerformance,
  RollingBeta,
  StrategyMatrix,
  StrategyHealthRow,
  StrategyModulesUpdate,
  StrategyModulesUpdateResult,
  SymbolDetail,
  SymbolOptions,
  TriggerRunResult,
} from "./types";

const BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8602"
).replace(/\/+$/, "");
const TOKEN = import.meta.env.VITE_API_TOKEN ?? "";

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

  let resp: Response;
  try {
    resp = await fetch(`${BASE_URL}${path}`, { ...init, headers });
  } catch (e) {
    const err = new ApiError(
      `Network error reaching Pilots API at ${BASE_URL}. Is it running (uvicorn api.pilots_api:app --port 8602)?`,
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
  getSymbol: (ticker: string) =>
    http<SymbolDetail>(`/symbols/${encodeURIComponent(ticker)}`),
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
  getObservabilitySummary: (range: PerfRange, horizon = 30) =>
    http<ObservabilitySummary>(
      `/observability/summary?range=${range}&horizon=${horizon}`
    ),
  getStrategyMatrix: () => http<StrategyMatrix>("/strategy/matrix"),
  getStrategyHealth: () => http<StrategyHealthRow[]>("/strategy/health"),
  setStrategyModules: (body: StrategyModulesUpdate) =>
    http<StrategyModulesUpdateResult>("/strategy/modules", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  getFollows: () => http<Follow[]>("/follows"),
  follow: (id: string, amount: number) =>
    http<FollowResult>(`/pilots/${encodeURIComponent(id)}/follow`, {
      method: "POST",
      body: JSON.stringify({ amount }),
    }),
  getAutomationStatus: () => http<AutomationStatus>("/automation/status"),
  getAutomationSchedule: () => http<AutomationSchedule>("/automation/schedule"),
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
  getBrokerageStatus: () => http<BrokerageStatus>("/brokerage/status"),
  getLlmStatus: () => http<LlmStatus>("/llm/status"),
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
