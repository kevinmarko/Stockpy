import { useState } from "react";
import { api } from "../api/client";
import type { CoverageStatus, SyncReportResponse, SyncReportSymbol } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading, MetricBadge } from "./ui";
import { theme } from "../theme";
import { timeAgo } from "../format";

/**
 * Portfolio-sync coverage-reconciliation diagnostic — the read-only PWA port
 * of `gui/panels/live_inventory.py`'s FULL/EQUITY_ONLY/UNCOVERED coverage
 * table. Ticker add/remove itself is a SEPARATE concern already covered by
 * the sibling `UniverseManager` component (`GET/PUT /data/universe`); this
 * only surfaces what market-data coverage each tracked symbol actually has.
 *
 * Reads `GET /data/sync-report`, which recomputes
 * `data.portfolio_sync.build_sync_report` live on every call — NOT a GUI-only
 * cache file — so this works on a headless deploy with nobody running
 * `streamlit run gui/app.py`. The endpoint returns the raw ticker-keyed
 * `SyncReport` shape; this component reshapes it into a sorted row list and
 * summary counts client-side. There is no "Sync Now" button here: this
 * component never triggers a live probe itself, it only reads what the last
 * request computed.
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

export function UniverseCoverage() {
  const { data, loading, error, status, reload } = useApi<SyncReportResponse>(
    () => api.getSyncReport(),
    [],
  );
  const [gapsOnly, setGapsOnly] = useState(false);

  if (loading) return <Loading lines={2} />;
  if (error || !data) {
    return <ErrorState message={error ?? "No data"} status={status} onRetry={reload} />;
  }

  // GET /data/sync-report returns the raw data.portfolio_sync.SyncReport
  // shape (a ticker-keyed map) — sort it into a stable display order here
  // rather than pushing that reshaping onto the backend.
  const rows: SyncReportSymbol[] = Object.values(data.symbols).sort((a, b) =>
    a.symbol.localeCompare(b.symbol),
  );

  if (rows.length === 0) {
    return (
      <div className="empty" data-testid="universe-coverage-empty" style={{ marginTop: 12 }}>
        No symbols tracked yet — a held position or a Robinhood/watchlist-file
        entry will appear here once one exists.
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
    <div data-testid="universe-coverage" style={{ marginTop: 16 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
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
        <p style={{ fontSize: 12, color: theme.textMuted, margin: "0 0 10px" }}>
          Last checked {timeAgo(data.generated_at)}
          {data.provider_source && ` · ${data.provider_source}`}
        </p>
      )}

      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, marginBottom: 10 }}>
        <input
          type="checkbox"
          checked={gapsOnly}
          onChange={(e) => setGapsOnly(e.target.checked)}
          data-testid="universe-coverage-gaps-only"
        />
        Coverage gaps only
      </label>

      {filtered.length === 0 ? (
        <div className="empty" data-testid="universe-coverage-no-gaps" style={{ padding: 16 }}>
          No coverage gaps — everything is FULL.
        </div>
      ) : (
        <div className="list">
          {filtered.map((r) => (
            <div key={r.symbol} className="row" data-testid={`universe-coverage-row-${r.symbol}`}>
              <div className="row-main">
                <span className="row-title" style={{ fontWeight: 600 }}>
                  {r.symbol}
                </span>
                {r.diagnostic && (
                  <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
                    {r.diagnostic}
                  </div>
                )}
              </div>
              <div className="row-end">
                <CoverageBadge coverage={r.coverage} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
