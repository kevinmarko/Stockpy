import { useState } from "react";
import { api } from "../api/client";
import type { CoverageStatus, SyncReportResponse, SyncReportSymbol } from "../api/types";
import { useApi } from "../hooks/useApi";
import { Button, ErrorState, Loading, MetricBadge,  } from "./ui";
import { Toggle } from "./Toggle";
import { theme } from "../theme";
import { fmtNum, fmtSignedUsd, fmtUsd, timeAgo } from "../format";

/**
 * Portfolio-sync coverage-reconciliation diagnostic — the read-only PWA port
 * of `gui/panels/live_inventory.py`'s FULL/EQUITY_ONLY/UNCOVERED coverage
 * table, extended (webapp parity gap G8) with the same per-symbol detail the
 * Streamlit table shows: Held?, Qty, Avg Cost, Δ/share, Stale?, Source,
 * Forecast?, Fundamentals?, Lists, Diagnostic. Ticker add/remove itself is a
 * SEPARATE concern already covered by the sibling `UniverseManager` component
 * (`GET/PUT /data/universe`, which writes `DEFAULT_TICKERS` directly); this
 * only surfaces what market-data coverage each tracked symbol actually has.
 *
 * Reads `GET /data/sync-report`, which recomputes
 * `data.portfolio_sync.build_sync_report` live on every call — NOT a GUI-only
 * cache file — so this works on a headless deploy with nobody running
 * `streamlit run gui/app.py`. The endpoint returns the raw ticker-keyed
 * `SyncReport` shape; this component reshapes it into a sorted row list and
 * summary counts client-side.
 *
 * "Sync Now" (`POST /data/sync`) discovers the union of held positions +
 * file-backed watchlists and persists it to `DEFAULT_TICKERS` server-side —
 * gated behind `UNIVERSE_SYNC_ENABLED`; a 403 here means an operator hasn't
 * opted in yet, not a bug. On success this reloads the coverage report (the
 * POST's own echoed `report` is the same shape, but a plain reload keeps this
 * component's data flow identical to every other read-then-mutate screen in
 * this app rather than special-casing a merge).
 */

const COVERAGE_LABEL: Record<CoverageStatus, string> = {
  full: "Full",
  stale: "Stale",
  quotes_only: "Quotes only",
  equity_only: "Equity only",
  uncovered: "Uncovered",
  unknown: "Unknown",
};

const COVERAGE_BADGE_CLASS: Record<CoverageStatus, string> = {
  full: "badge-good",
  stale: "badge-warn",
  quotes_only: "badge-warn",
  equity_only: "badge-warn",
  uncovered: "badge-bad",
  unknown: "badge-neutral",
};

function CoverageBadge({ coverage }: { coverage: CoverageStatus }) {
  return (
    <span className={`badge ${COVERAGE_BADGE_CLASS[coverage] ?? "badge-neutral"}`}>
      {COVERAGE_LABEL[coverage] ?? coverage}
    </span>
  );
}

/**
 * "Sync Now" control — POST /data/sync. Kept as a self-contained
 * mutation+message block (mirroring PromptRegistry.tsx's SyncNowControl
 * pattern) rather than useMutation directly, so a 403 (feature flag off)
 * renders as an informative inline message instead of a generic error toast.
 */
function SyncNowControl({ onSynced }: { onSynced: () => void }) {
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const run = async () => {
    setPending(true);
    setMessage(null);
    setFailed(false);
    try {
      const result = await api.postDataSync();
      setMessage(result.note);
      onSynced();
    } catch (err: unknown) {
      setFailed(true);
      setMessage(err instanceof Error ? err.message : "Sync failed.");
    } finally {
      setPending(false);
    }
  };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", flexWrap: "wrap", marginBottom: "var(--s-2-5)" }}>
      <Button onClick={run} pending={pending} variant="neutral" data-testid="universe-sync-now">
        🔄 Sync Now
      </Button>
      {message && (
        <span
          data-testid="universe-sync-message"
          style={{ fontSize: "var(--t-caption)", color: failed ? theme.decline : theme.growth }}
        >
          {message}
        </span>
      )}
    </div>
  );
}

/** Compact yes/no glyph — never a bare boolean, and never blank for `false`. */
function boolGlyph(v: boolean): string {
  return v ? "✓" : "✗";
}

function DetailRow({ r }: { r: SyncReportSymbol }) {
  return (
    <div
      style={{
        marginTop: "var(--s-1)",
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
        gap: "var(--s-1) var(--s-2)",
        fontSize: "var(--t-micro)",
        color: theme.textMuted,
      }}
      data-testid={`universe-coverage-detail-${r.symbol}`}
    >
      <span>Held: {boolGlyph(r.held)}</span>
      <span>Qty: {r.held ? fmtNum(r.quantity, 4) : "—"}</span>
      <span>Avg cost: {fmtUsd(r.avg_cost)}</span>
      <span>Δ/share: {fmtSignedUsd(r.cost_basis_delta_per_share)}</span>
      <span>Stale: {r.is_stale_quote ? "yes" : "no"}</span>
      <span>Source: {r.quote_source || "—"}</span>
      <span>Forecast: {boolGlyph(r.forecast_available)}</span>
      <span>Fundamentals: {boolGlyph(r.has_fundamentals)}</span>
      <span style={{ gridColumn: "1 / -1" }}>
        Lists: {r.watchlists.length > 0 ? r.watchlists.join(", ") : "—"}
      </span>
      {r.diagnostic && (
        <span style={{ gridColumn: "1 / -1", color: theme.decline }}>Diagnostic: {r.diagnostic}</span>
      )}
    </div>
  );
}

export function UniverseCoverage() {
  const { data, loading, error, status, reload } = useApi<SyncReportResponse>(
    () => api.getSyncReport(),
    [],
  );
  const [gapsOnly, setGapsOnly] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggleExpanded = (symbol: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });
  };

  // SyncNowControl is mounted UNCONDITIONALLY, outside every loading/error/
  // empty branch below -- a `reload()` it triggers flips `loading` true for
  // the span of the re-fetch, and if this component's own root were gated on
  // `!loading` that transition would unmount SyncNowControl (and the
  // just-set "Sync complete" message it holds in local state) before the
  // operator ever saw it. Keeping it outside the gate is a genuine UX fix,
  // not just a test convenience.
  return (
    <div data-testid="universe-coverage" style={{ marginTop: "var(--s-4)" }}>
      <SyncNowControl onSynced={reload} />

      {loading && <Loading lines={2} />}
      {!loading && (error || !data) && (
        <ErrorState message={error ?? "No data"} status={status} onRetry={reload} />
      )}
      {!loading && !error && data && (
        <UniverseCoverageBody
          data={data}
          gapsOnly={gapsOnly}
          onGapsOnlyChange={setGapsOnly}
          expanded={expanded}
          onToggleExpanded={toggleExpanded}
        />
      )}
    </div>
  );
}

function UniverseCoverageBody({
  data,
  gapsOnly,
  onGapsOnlyChange,
  expanded,
  onToggleExpanded,
}: {
  data: SyncReportResponse;
  gapsOnly: boolean;
  onGapsOnlyChange: (v: boolean) => void;
  expanded: Set<string>;
  onToggleExpanded: (symbol: string) => void;
}) {
  // GET /data/sync-report returns the raw data.portfolio_sync.SyncReport
  // shape (a ticker-keyed map) — sort it into a stable display order here
  // rather than pushing that reshaping onto the backend.
  const rows: SyncReportSymbol[] = Object.values(data.symbols).sort((a, b) =>
    a.symbol.localeCompare(b.symbol),
  );

  if (rows.length === 0) {
    return (
      <div className="empty" data-testid="universe-coverage-empty">
        No symbols tracked yet — a held position or a Robinhood/watchlist-file
        entry will appear here once one exists, or click Sync Now to discover them.
      </div>
    );
  }

  const counts: Record<CoverageStatus, number> = {
    full: 0,
    stale: 0,
    quotes_only: 0,
    equity_only: 0,
    uncovered: 0,
    unknown: 0,
  };
  for (const r of rows) counts[r.coverage] += 1;

  const filtered = gapsOnly ? rows.filter((r) => r.coverage !== "full") : rows;

  return (
    <>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", marginBottom: "var(--s-2)" }}>
        <MetricBadge label="Symbols" value={String(rows.length)} />
        <MetricBadge label="Full" value={String(counts.full)} good />
        <MetricBadge
          label="Equity only"
          value={String(counts.equity_only)}
          good={counts.equity_only === 0}
        />
        <MetricBadge
          label="Uncovered"
          value={String(counts.uncovered)}
          good={counts.uncovered === 0}
        />
      </div>

      {data.generated_at && (
        <p style={{ fontSize: "var(--t-caption)", color: theme.textMuted, margin: "0 0 var(--s-2-5)" }}>
          Last checked {timeAgo(data.generated_at)}
          {data.provider_source && ` · ${data.provider_source}`}
        </p>
      )}

      <div style={{ marginBottom: "var(--s-2-5)" }}>
        <Toggle
          label="Coverage gaps only"
          checked={gapsOnly}
          onChange={onGapsOnlyChange}
          dataTestId="universe-coverage-gaps-only"
        />
      </div>

      {filtered.length === 0 ? (
        <div className="empty" data-testid="universe-coverage-no-gaps" style={{ padding: "var(--s-4)" }}>
          No coverage gaps — everything is FULL.
        </div>
      ) : (
        <div className="list">
          {filtered.map((r) => {
            const isOpen = expanded.has(r.symbol);
            return (
              <div key={r.symbol} data-testid={`universe-coverage-row-${r.symbol}`}>
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => onToggleExpanded(r.symbol)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onToggleExpanded(r.symbol);
                    }
                  }}
                  className="row"
                  data-testid={`universe-coverage-toggle-${r.symbol}`}
                  aria-expanded={isOpen}
                  style={{ cursor: "pointer" }}
                >
                  <div className="row-main">
                    <span className="row-title" style={{ fontWeight: 600 }}>
                      {r.symbol} {r.held && <span style={{ color: theme.textMuted, fontWeight: 400 }}>· held</span>}
                    </span>
                    {r.diagnostic && !isOpen && (
                      <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted, marginTop: "var(--s-0-5)" }}>
                        {r.diagnostic}
                      </div>
                    )}
                  </div>
                  <div className="row-end">
                    <CoverageBadge coverage={r.coverage} />
                    <span aria-hidden style={{ marginLeft: "var(--s-1)", color: theme.textMuted }}>
                      {isOpen ? "▲" : "▼"}
                    </span>
                  </div>
                </div>
                {isOpen && <DetailRow r={r} />}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
