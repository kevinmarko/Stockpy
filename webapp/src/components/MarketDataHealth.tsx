import { useRef, useState } from "react";
import { api } from "../api/client";
import type { UniverseResponse } from "../api/types";
import { useApi } from "../hooks/useApi";
import { Button, EmptyState, ErrorState, Loading } from "./ui";
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

type CheckStatus = "reachable" | "stale" | "unreachable";

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

function statusMeta(status: CheckStatus): { label: string; color: string } {
  if (status === "reachable") return { label: "OK", color: theme.growth };
  if (status === "stale") return { label: "Stale", color: theme.caution };
  return { label: "Unreachable", color: theme.decline };
}

/**
 * Market data connection diagnostic — a lightweight webapp analog of the
 * legacy Streamlit "Market Data Provider" tab (`gui/panels/market_data.py`):
 * a connection-health badge and a per-symbol latency table across the
 * tracked universe. Derived ENTIRELY client-side from the existing
 * `GET /data/quotes?symbols=...` (`api/data_api.py`) — no backend change.
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
 *    #4), this checks one symbol per request, staggered by `STAGGER_MS` —
 *    the same throttling spirit as the legacy panel's `BatchQuoteFetcher`.
 *  - The connection-health badge mirrors `FetchHealthTracker` exactly (see
 *    constants above). A quote present but `is_stale` still counts as a
 *    successful connection — matches the legacy split between "did we get a
 *    response" and "is the data fresh"; only a symbol missing from the
 *    response counts as a failure.
 *
 * Never renders a fabricated all-green state: the mock fixture
 * (`api/mock.ts::getDataQuotes`) always includes an `is_stale` row and an
 * always-omitted ("unreachable") row so both honesty branches render.
 */
export function MarketDataHealth() {
  const universe = useApi<UniverseResponse>(() => api.getUniverse(), []);
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
          : { symbol, status: "unreachable", latencyMs, source: null };
      } catch {
        // Network-level failure (e.g. the data API is down entirely) -- the
        // real endpoint itself never throws per-symbol, but the fetch call
        // wrapping it can (offline, CORS, 5xx). Same honest bucket.
        check = { symbol, status: "unreachable", latencyMs: Math.round(performance.now() - t0), source: null };
      }
      historyRef.current = [...historyRef.current, check.status !== "unreachable"].slice(-HEALTH_WINDOW);
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
    <section className="card card-pad" style={{ marginTop: 16 }} data-testid="market-data-health">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <h2 style={{ fontSize: 15, margin: 0 }}>Market data connection</h2>
        <span style={{ fontSize: 12, fontWeight: 700, color: badge.color }} data-testid="md-health-badge">
          {badge.label}
        </span>
      </div>
      <p style={{ margin: "6px 0 10px", fontSize: 13, color: theme.textMuted }}>
        Checks the live quote feed for each tracked symbol and times the round trip — a quick
        read on whether the data layer feeding every screen is actually up.
      </p>

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
            <div style={{ marginTop: 8, fontSize: 12, color: theme.textMuted }}>
              Showing the first {MAX_CHECK_SYMBOLS} of {allSymbols.length} tracked symbols.
            </div>
          )}
          {progress && (
            <div style={{ marginTop: 10, fontSize: 12, color: theme.textMuted }} data-testid="md-progress">
              Checking {progress.i}/{progress.n}…
            </div>
          )}
          {results.length > 0 && (
            <div style={{ marginTop: 12, overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: "left", color: theme.textMuted, fontSize: 11, textTransform: "uppercase" }}>
                    <th style={{ padding: "4px 6px" }}>Symbol</th>
                    <th style={{ padding: "4px 6px" }}>Status</th>
                    <th style={{ padding: "4px 6px", textAlign: "right" }}>Latency</th>
                    <th style={{ padding: "4px 6px" }}>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r) => {
                    const meta = statusMeta(r.status);
                    return (
                      <tr
                        key={r.symbol}
                        style={{ borderTop: `1px solid ${theme.border}` }}
                        data-testid={`md-row-${r.symbol}`}
                      >
                        <td style={{ padding: "6px", fontWeight: 700, color: theme.textPrimary }}>{r.symbol}</td>
                        <td style={{ padding: "6px", color: meta.color, fontWeight: 600 }}>{meta.label}</td>
                        <td
                          style={{
                            padding: "6px",
                            textAlign: "right",
                            fontVariantNumeric: "tabular-nums",
                            color: latencyColor(r.latencyMs),
                          }}
                        >
                          {r.latencyMs == null ? "—" : `${r.latencyMs} ms`}
                        </td>
                        <td style={{ padding: "6px", color: theme.textMuted }}>{r.source ?? "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}
