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
import type {
  BrokerageConnectRequest,
  BrokerageConnectResult,
  BrokerageDisconnectResult,
  BrokerageStatus,
  Follow,
  FollowResult,
  Holding,
  PerfRange,
  PerformanceResponse,
  PilotDetail,
  PilotSummary,
  Portfolio,
  CurvePoint,
  SymbolDetail,
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
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init?.body ? { "Content-Type": "application/json" } : {}),
  };
  if (TOKEN) headers["Authorization"] = `Bearer ${TOKEN}`;

  let resp: Response;
  try {
    resp = await fetch(`${BASE_URL}${path}`, { ...init, headers });
  } catch (e) {
    throw new ApiError(
      `Network error reaching Pilots API at ${BASE_URL}. Is it running (uvicorn api.pilots_api:app --port 8602)?`,
      0
    );
  }
  if (!resp.ok) {
    let msg = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body?.detail) msg = String(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(msg, resp.status);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
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
  getTrades: (id: string, limit = 20) =>
    http(`/pilots/${encodeURIComponent(id)}/trades?limit=${limit}`),
  getSymbol: (ticker: string) =>
    http<SymbolDetail>(`/symbols/${encodeURIComponent(ticker)}`),
  getPortfolio: () => http<Portfolio>("/portfolio"),
  getEquityCurve: (range: PerfRange) =>
    http<{ range: PerfRange; curve: CurvePoint[] | null }>(
      `/portfolio/equity-curve?range=${range}`
    ),
  getFollows: () => http<Follow[]>("/follows"),
  follow: (id: string, amount: number) =>
    http<FollowResult>(`/pilots/${encodeURIComponent(id)}/follow`, {
      method: "POST",
      body: JSON.stringify({ amount }),
    }),
  getBrokerageStatus: () => http<BrokerageStatus>("/brokerage/status"),
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

/** The single API surface every screen consumes. */
export const api = USE_MOCK ? mockApi : liveApi;

/** Small runtime banner metadata for the UI (mode label etc.). */
export const apiMeta = {
  useMock: USE_MOCK,
  baseUrl: BASE_URL,
  hasToken: Boolean(TOKEN),
  mockMode: MOCK_META.mode,
};

export { ApiError };
