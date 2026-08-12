import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import type { RealizedPerformance } from "../../api/types";
import { EmptyState, ErrorState, Loading, StaleDataNotice, Tile } from "../ui";
import { fmtNum, fmtPct, fmtSignedUsd } from "../../format";

/**
 * Pilots Manager's realized-performance glance card -- GET /portfolio/realized,
 * the same endpoint Portfolio.tsx's "Realized performance" section already
 * renders (see that screen for the canonical tiles pattern this mirrors).
 * `available: false` (nothing cached yet) or zero closed trades both degrade
 * to an honest empty state -- never a hardcoded-looking "$0.00"/"0%"/"0.00"
 * that could be mistaken for a real measured zero (CONSTRAINT #4).
 */
export function PerformanceMetricsCard() {
  const { data, loading, error, status, stale, cachedAt, reload } = useApi<RealizedPerformance>(
    () => api.getRealized(),
    []
  );

  const hasData = !!data && data.available && data.summary.n_trades > 0;

  return (
    <div className="card card-pad">
      <div className="card-header">
        <h3 className="card-title" style={{ margin: 0 }}>
          Performance Metrics
        </h3>
      </div>
      <div className="card-content" style={{ marginTop: "var(--s-2)" }}>
        {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}
        {loading && <Loading lines={2} />}
        {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
        {!loading && !error && !hasData && (
          <EmptyState
            title="No realized trades yet"
            hint="Performance metrics populate once at least one Robinhood round-trip trade has closed."
          />
        )}
        {!loading && !error && hasData && data && (
          <div className="tiles" data-testid="performance-metrics-tiles">
            <Tile
              label="Realized P&L"
              value={fmtSignedUsd(data.summary.total_realized_pnl)}
              tone={data.summary.total_realized_pnl >= 0 ? "pos" : "neg"}
            />
            <Tile label="Win rate" value={fmtPct(data.summary.win_rate, 0, { fromFraction: true })} />
            <Tile label="Profit factor" value={fmtNum(data.summary.profit_factor, 2)} />
            <Tile label="Trades" value={data.summary.n_trades} />
          </div>
        )}
      </div>
    </div>
  );
}
