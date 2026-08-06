import { useState, type MouseEvent } from "react";
import { api } from "../api/client";
import type { CoverageStatus, SyncReportResponse, SyncReportSymbol } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { useAutoRefresh } from "./AutoRefreshContext";
import { Button, ErrorState, Loading, MetricBadge } from "./ui";
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
 *
 * Symbol Rating (Part 3 of the Symbol Rating subsystem): each row's rating
 * fields (`rating_consecutive_bad_cycles`/`rating_excluded`, both optional —
 * absent means no rating history yet) come along for free on the same
 * `GET /data/sync-report` payload (`api/data_api.py` enriches it from
 * `rating.symbol_rating_store.SymbolRatingStore`, read-only). An excluded row
 * gets an "Excluded" badge (mirrors `COVERAGE_BADGE_CLASS`'s `badge-bad`
 * styling) plus a "Re-include" button that calls
 * `POST /universe/{symbol}/reinclude` and, on success, reloads the coverage
 * report — the exact same "mutate then plain reload" pattern `SyncNowControl`
 * uses above, rather than a bespoke client-side merge.
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

/** Concise rating-cycle label, or an em-dash when no rating history exists
 * yet — `rating_consecutive_bad_cycles` is `undefined`/`null` (never a
 * fabricated 0) for a symbol the rating engine hasn't scored yet. */
function ratingCyclesLabel(cycles: number | null | undefined): string {
  if (cycles == null) return "—";
  return `${cycles} cycle${cycles === 1 ? "" : "s"}`;
}

/** "Excluded" badge — mirrors `COVERAGE_BADGE_CLASS`'s existing
 * `badge-bad` styling rather than inventing new CSS. Only rendered when
 * `rating_excluded === true`. */
function ExcludedBadge() {
  return <span className="badge badge-bad" data-testid="universe-rating-excluded-badge">Excluded</span>;
}

/**
 * "Re-include" — POST /universe/{symbol}/reinclude. Self-contained
 * mutation+message block, matching `SyncNowControl`'s shape (pending state,
 * inline success/failure message, `onReincluded` reload callback) so an
 * auth-gate 403 or a store-write 503 renders as an informative inline
 * message here too, rather than a generic error toast.
 */
function ReincludeButton({ symbol, onReincluded }: { symbol: string; onReincluded: () => void }) {
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const run = async (e: MouseEvent) => {
    e.stopPropagation(); // don't toggle the row's expand/collapse
    setPending(true);
    setMessage(null);
    setFailed(false);
    try {
      await api.reincludeSymbol(symbol);
      onReincluded();
    } catch (err: unknown) {
      setFailed(true);
      setMessage(err instanceof Error ? err.message : "Re-include failed.");
    } finally {
      setPending(false);
    }
  };

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--s-1)" }}>
      <Button
        onClick={run}
        pending={pending}
        variant="neutral"
        data-testid={`universe-reinclude-${symbol}`}
      >
        Re-include
      </Button>
      {message && failed && (
        <span
          data-testid={`universe-reinclude-message-${symbol}`}
          style={{ fontSize: "var(--t-micro)", color: theme.decline }}
        >
          {message}
        </span>
      )}
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
      <span>Rating: {ratingCyclesLabel(r.rating_consecutive_bad_cycles)}</span>
      <span style={{ gridColumn: "1 / -1" }}>
        Lists: {r.watchlists.length > 0 ? r.watchlists.join(", ") : "—"}
      </span>
      {r.diagnostic && (
        <span style={{ gridColumn: "1 / -1", color: theme.decline }}>Diagnostic: {r.diagnostic}</span>
      )}
    </div>
  );
}

/**
 * UniverseCoverageIdle — rendered whenever the "robinhood" auto-refresh
 * category is off and the operator hasn't explicitly loaded the report yet.
 * `GET /data/sync-report` recomputes coverage live and can trigger a REAL
 * Robinhood login when the cached account snapshot is stale (see
 * ROBINHOOD_AUTO_REFRESH_ENABLED in CLAUDE.md) — unlike every other
 * diagnostic panel in this app, this one must NOT fetch on mount by default.
 * "Sync Now" (`POST /data/sync`) is a different, always-manual-only mutation
 * (discovers + persists `DEFAULT_TICKERS`) that stays reachable here too; a
 * successful sync arms the live view so the operator immediately sees what
 * they just synced.
 */
function UniverseCoverageIdle({ onLoad }: { onLoad: () => void }) {
  return (
    <div data-testid="universe-coverage" style={{ marginTop: "var(--s-4)" }}>
      <SyncNowControl onSynced={onLoad} />
      <div className="empty" data-testid="universe-coverage-idle" style={{ padding: "var(--s-4)" }}>
        <p style={{ margin: "0 0 var(--s-2-5)" }}>
          Coverage report not loaded. Fetching it can trigger a live
          Robinhood login.
        </p>
        <Button onClick={onLoad} variant="neutral" data-testid="universe-coverage-load">
          Load coverage report
        </Button>
        <p style={{ fontSize: "var(--t-caption)", color: theme.textMuted, margin: "var(--s-2-5) 0 0" }}>
          — or turn on Robinhood auto-refresh in Data Auto-Refresh below.
        </p>
      </div>
    </div>
  );
}

function UniverseCoverageLive() {
  const { data, loading, error, status, reload } = useApi<SyncReportResponse>(
    () => api.getSyncReport(),
    [],
  );
  useAutoPoll(reload, "robinhood", { hasError: error != null });
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
          onReincluded={reload}
        />
      )}
    </div>
  );
}

/**
 * UniverseCoverage — gates the fetch itself, not just a subsequent poll.
 * Opening this component (e.g. by visiting Settings) must never itself
 * trigger a live Robinhood login: the idle view renders (and mounts no
 * `useApi`/`useAutoPoll` at all) until the "robinhood" category is on, or the
 * operator explicitly arms it via "Load coverage report" / a manual sync.
 */
export function UniverseCoverage() {
  const { robinhoodRefreshEnabled } = useAutoRefresh();
  const [armed, setArmed] = useState(false);
  if (!robinhoodRefreshEnabled && !armed) {
    return <UniverseCoverageIdle onLoad={() => setArmed(true)} />;
  }
  return <UniverseCoverageLive />;
}

function UniverseCoverageBody({
  data,
  gapsOnly,
  onGapsOnlyChange,
  expanded,
  onToggleExpanded,
  onReincluded,
}: {
  data: SyncReportResponse;
  gapsOnly: boolean;
  onGapsOnlyChange: (v: boolean) => void;
  expanded: Set<string>;
  onToggleExpanded: (symbol: string) => void;
  onReincluded: () => void;
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
                    <span
                      style={{ fontSize: "var(--t-micro)", color: theme.textMuted, marginRight: "var(--s-1)" }}
                      data-testid={`universe-rating-cycles-${r.symbol}`}
                    >
                      {ratingCyclesLabel(r.rating_consecutive_bad_cycles)}
                    </span>
                    {r.rating_excluded === true && <ExcludedBadge />}
                    <CoverageBadge coverage={r.coverage} />
                    <span aria-hidden style={{ marginLeft: "var(--s-1)", color: theme.textMuted }}>
                      {isOpen ? "▲" : "▼"}
                    </span>
                  </div>
                </div>
                {r.rating_excluded === true && (
                  <div
                    style={{ marginTop: "var(--s-1)", display: "flex", alignItems: "center", gap: "var(--s-1)" }}
                    data-testid={`universe-reinclude-row-${r.symbol}`}
                  >
                    <ReincludeButton symbol={r.symbol} onReincluded={onReincluded} />
                  </div>
                )}
                {isOpen && <DetailRow r={r} />}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
