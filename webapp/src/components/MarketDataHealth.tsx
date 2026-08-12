import { useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { ProviderStatus, UniverseResponse } from "../api/types";
import { useApi } from "../hooks/useApi";
import { Button, EmptyState, ErrorState, Loading, MetricBadge, Table } from "./ui";
import { theme } from "../theme";

// Safety cap on how many tracked symbols a single "Check connection" click
// probes -- the real tracked universe (held positions ∪ watchlist) is
// normally small, but this bounds worst case so a huge universe never turns
// one click into a multi-minute sequential fetch.
const MAX_CHECK_SYMBOLS = 25;
// Minimum gap between per-symbol requests -- mirrors
// gui/market_data_diagnostics.py's BatchQuoteFetcher default (100ms spacing),
// which exists so a full-universe check never fires a burst of simultaneous
// provider calls into a free-tier rate limit.
const STAGGER_MS = 120;
// Sliding-window connection-health thresholds -- mirrors
// gui/market_data_diagnostics.FetchHealthTracker's defaults exactly (window
// of the last 20 checks; Healthy >= 90% success, Degraded >= 50%, else Down).
const HEALTH_WINDOW = 20;
const HEALTHY_RATE = 0.9;
const DEGRADED_RATE = 0.5;

/**
 * Typed classification for a per-symbol check failure — mirrors
 * gui/market_data_diagnostics.ErrorCategory's operator-facing categories
 * (Rate Limited / Symbol Not Found / Network Timeout / Malformed Response /
 * Unknown), adapted to what a BROWSER CLIENT can actually observe:
 *
 * - `GET /data/quotes` dead-letters a per-symbol provider failure by simply
 *   OMITTING it from the response (always a 200) — the client genuinely
 *   cannot know WHY that symbol failed server-side (rate-limited upstream?
 *   delisted? malformed?), so that case is classified honestly as
 *   `no_data`, never guessed into a more specific bucket it has no evidence
 *   for (CONSTRAINT #4).
 * - A THROWN request (the whole `/data/quotes` call itself failed) DOES
 *   carry a real HTTP status via `ApiError.status`, which maps directly to
 *   `rate_limited` (429) / `unauthorized` (401/403) / `server_error` (5xx) /
 *   `network_error` (0 — client.ts's convention for "fetch() itself threw",
 *   e.g. offline, CORS, DNS) / `unknown_error` (anything else).
 */
type CheckStatus =
  | "reachable"
  | "stale"
  | "no_data"
  | "rate_limited"
  | "unauthorized"
  | "server_error"
  | "network_error"
  | "unknown_error";

interface SymbolCheck {
  symbol: string;
  status: CheckStatus;
  latencyMs: number | null;
  source: string | null;
}

function sleep(ms: number): Promise<void> {
  return new Promise((res) => setTimeout(res, ms));
}

/** Green under 300ms, amber under 800ms, red above -- a plain client-round-trip heuristic, not a live config value. */
function latencyColor(ms: number | null): string {
  if (ms == null) return theme.textMuted;
  if (ms < 300) return theme.growth;
  if (ms < 800) return theme.caution;
  return theme.decline;
}

const STATUS_META: Record<CheckStatus, { label: string; color: string }> = {
  reachable: { label: "OK", color: theme.growth },
  stale: { label: "Stale", color: theme.caution },
  no_data: { label: "No data returned", color: theme.decline },
  rate_limited: { label: "Rate limited", color: theme.decline },
  unauthorized: { label: "Unauthorized", color: theme.decline },
  server_error: { label: "Server error", color: theme.decline },
  network_error: { label: "Network unreachable", color: theme.decline },
  unknown_error: { label: "Unknown error", color: theme.decline },
};

function statusMeta(status: CheckStatus): { label: string; color: string } {
  return STATUS_META[status];
}

/** A whole-request failure counts as an "unsuccessful" check for the
 *  connection-health ledger regardless of its specific category. */
function isCheckSuccessful(status: CheckStatus): boolean {
  return status === "reachable" || status === "stale";
}

/** Classify a thrown request error into a CheckStatus. Only ever called for
 *  a request that actually THREW (the client.ts http() error path) — a
 *  per-symbol dead-letter (200 with the symbol omitted) is classified
 *  separately as `no_data`, not through this function. */
function classifyRequestError(err: unknown): CheckStatus {
  if (!(err instanceof ApiError)) return "unknown_error";
  if (err.status === 0) return "network_error";
  if (err.status === 429) return "rate_limited";
  if (err.status === 401 || err.status === 403) return "unauthorized";
  if (err.status >= 500) return "server_error";
  return "unknown_error";
}

/**
 * Market data connection diagnostic — a lightweight webapp analog of the
 * legacy Streamlit "Market Data Provider" tab (`gui/panels/market_data.py`):
 * provider/mode/TTL tiles (`GET /data/provider-status`), a connection-health
 * badge, typed per-symbol failure classification, and a latency table across
 * the tracked universe.
 *
 * Provider/mode/TTL come from the new `GET /data/provider-status` (webapp
 * parity gap G9); the connection-health badge + per-symbol check table are
 * derived ENTIRELY client-side from the existing
 * `GET /data/quotes?symbols=...` (`api/data_api.py`) — no backend change for
 * that half, unchanged from before this gap was closed.
 *
 * Differences from the legacy panel, and why:
 *  - Latency here is the CLIENT-OBSERVED round trip to `/data/quotes`
 *    (`performance.now()` around each call), not the legacy panel's
 *    server-side "quote timestamp to local ingestion" clock skew — the
 *    webapp has no access to that internal clock, and round-trip latency is
 *    the more directly actionable number for a remote/mobile client anyway.
 *  - The endpoint accepts a comma-separated symbol batch in one call; to
 *    keep a genuine PER-SYMBOL latency and honesty signal (a symbol
 *    silently omitted from the response means the provider fetch failed for
 *    it server-side — the endpoint's own dead-letter contract, CONSTRAINT
 *    #4), this checks one symbol per request, staggered by `STAGGER_MS` --
 *    the same throttling spirit as the legacy panel's `BatchQuoteFetcher`.
 *  - The connection-health badge mirrors `FetchHealthTracker` exactly (see
 *    constants above). A quote present but `is_stale` still counts as a
 *    successful connection — matches the legacy split between "did we get a
 *    response" and "is the data fresh"; only a genuinely FAILED check
 *    (no_data / rate_limited / unauthorized / server_error / network_error /
 *    unknown_error) counts as a failure.
 *  - Server-side connection-health tracking is deliberately NOT duplicated
 *    (see GET /data/provider-status's own docstring): this component's
 *    session-local tracker IS the intended design, not a stand-in for one.
 *
 * Never renders a fabricated all-green state: the mock fixture
 * (`api/mock.ts::getDataQuotes`) always includes an `is_stale` row and an
 * always-omitted ("no_data") row so both honesty branches render.
 */
export function MarketDataHealth() {
  const universe = useApi<UniverseResponse>(() => api.getUniverse(), []);
  const providerStatus = useApi<ProviderStatus>(() => api.getProviderStatus(), []);
  const [checking, setChecking] = useState(false);
  const [results, setResults] = useState<SymbolCheck[]>([]);
  const [progress, setProgress] = useState<{ i: number; n: number } | null>(null);
  // Rolling ok/fail ledger across clicks within this mount (mirrors
  // FetchHealthTracker persisting across Streamlit reruns within a session).
  // Deliberately NOT persisted to localStorage -- connection health is a live
  // signal that should reset each session, same rationale as the legacy
  // LatencySampleStore/FetchHealthTracker.
  const historyRef = useRef<boolean[]>([]);

  const allSymbols = universe.data?.symbols ?? [];
  const symbols = allSymbols.slice(0, MAX_CHECK_SYMBOLS).map((s) => s.symbol);
  const truncated = allSymbols.length > MAX_CHECK_SYMBOLS;

  const runCheck = async () => {
    if (symbols.length === 0 || checking) return;
    setChecking(true);
    setResults([]);
    for (let i = 0; i < symbols.length; i++) {
      const symbol = symbols[i];
      setProgress({ i: i + 1, n: symbols.length });
      const t0 = performance.now();
      let check: SymbolCheck;
      try {
        const res = await api.getDataQuotes([symbol]);
        const latencyMs = Math.round(performance.now() - t0);
        const q = res[symbol.toUpperCase()];
        check = q
          ? {
              symbol,
              status: q.is_stale ? "stale" : "reachable",
              latencyMs,
              source: q.source,
            }
          : { symbol, status: "no_data", latencyMs, source: null };
      } catch (err) {
        // Network-level failure (e.g. the data API is down entirely) or a
        // genuine non-2xx from a reachable server (rate limit, auth, 5xx) --
        // classified from the real HTTP status when available.
        check = {
          symbol,
          status: classifyRequestError(err),
          latencyMs: Math.round(performance.now() - t0),
          source: null,
        };
      }
      historyRef.current = [...historyRef.current, isCheckSuccessful(check.status)].slice(-HEALTH_WINDOW);
      setResults((prev) => [...prev, check]);
      if (i < symbols.length - 1) await sleep(STAGGER_MS);
    }
    setProgress(null);
    setChecking(false);
  };

  const history = historyRef.current;
  const total = history.length;
  const okCount = history.filter(Boolean).length;
  const rate = total === 0 ? null : okCount / total;
  const badge =
    rate === null
      ? { label: "No checks yet", color: theme.textMuted }
      : rate >= HEALTHY_RATE
        ? { label: `Healthy (${okCount}/${total} ok)`, color: theme.growth }
        : rate >= DEGRADED_RATE
          ? { label: `Degraded (${okCount}/${total} ok)`, color: theme.caution }
          : { label: `Down (${okCount}/${total} ok)`, color: theme.decline };

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }} data-testid="market-data-health">
      <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)` }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--s-2)", flexWrap: "wrap" }}>
          <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>Market data connection</h2>
          <span style={{ fontSize: "var(--t-caption)", fontWeight: 700, color: badge.color }} data-testid="md-health-badge">
            {badge.label}
          </span>
        </div>
        <p style={{ margin: "var(--s-1-5) 0 0", fontSize: "var(--t-body)", color: theme.textMuted }}>
          Checks the live quote feed for each tracked symbol and times the round trip — a quick
          read on whether the data layer feeding every screen is actually up.
        </p>
      </div>

      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
      {providerStatus.data && (
        <div
          data-testid="md-provider-tiles"
          style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", marginBottom: "var(--s-3)" }}
        >
          <MetricBadge label="Provider" value={providerStatus.data.provider} />
          <MetricBadge
            label="Mode"
            value={providerStatus.data.is_realtime ? "Real-time" : "Delayed (~15 min)"}
            good={providerStatus.data.is_realtime}
          />
          <MetricBadge label="Quote TTL" value={`${providerStatus.data.quote_ttl_seconds}s`} />
        </div>
      )}
      {!providerStatus.loading && providerStatus.data && !providerStatus.data.is_realtime && (
        <p style={{ fontSize: "var(--t-caption)", color: theme.textMuted, margin: "0 0 var(--s-3)" }} data-testid="md-delayed-note">
          yfinance is delayed by ~15 minutes and marked stale on every quote. Set
          ALPACA_API_KEY/ALPACA_SECRET_KEY in .env to upgrade to the free IEX real-time feed.
        </p>
      )}

      {universe.loading && <Loading lines={2} />}
      {!universe.loading && universe.error && (
        <ErrorState message={universe.error} status={universe.status} onRetry={universe.reload} />
      )}
      {!universe.loading && !universe.error && symbols.length === 0 && (
        <EmptyState
          title="No tracked symbols yet"
          hint="Add a symbol in Settings to check its connection."
        />
      )}
      {!universe.loading && !universe.error && symbols.length > 0 && (
        <>
          <Button onClick={runCheck} pending={checking}>
            Check connection
          </Button>
          {truncated && (
            <div style={{ marginTop: "var(--s-2)", fontSize: "var(--t-caption)", color: theme.textMuted }}>
              Showing the first {MAX_CHECK_SYMBOLS} of {allSymbols.length} tracked symbols.
            </div>
          )}
          {progress && (
            <div style={{ marginTop: "var(--s-2-5)", fontSize: "var(--t-caption)", color: theme.textMuted }} data-testid="md-progress">
              Checking {progress.i}/{progress.n}…
            </div>
          )}
          {results.length > 0 && (
            <div style={{ marginTop: "var(--s-3)", overflowX: "auto" }}>
              <Table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Status</th>
                    <th className="num">Latency</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r) => {
                    const meta = statusMeta(r.status);
                    return (
                      <tr key={r.symbol} data-testid={`md-row-${r.symbol}`}>
                        <td style={{ fontWeight: 700, color: theme.textPrimary }}>{r.symbol}</td>
                        <td style={{ color: meta.color, fontWeight: 600 }} data-testid={`md-status-${r.symbol}`}>
                          {meta.label}
                        </td>
                        <td className="num" style={{ color: latencyColor(r.latencyMs) }}>
                          {r.latencyMs == null ? "—" : `${r.latencyMs} ms`}
                        </td>
                        <td style={{ color: theme.textMuted }}>{r.source ?? "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </Table>
            </div>
          )}
        </>
      )}
      </div>
    </section>
  );
}
