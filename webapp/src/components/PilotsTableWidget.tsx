import { api } from "../api/client";
import type { PilotSummary } from "../api/types";
import { useApi } from "../hooks/useApi";
import { EmptyState, ErrorState, Loading, Table } from "./ui";
import { fmtNum, fmtPct } from "../format";

/**
 * Pilots table widget -- a real, read-only listing of every Pilot (name,
 * category, headline Sharpe, headline max drawdown). Self-contained
 * (fetches its own data via `api.listPilots()`, no required props) so it
 * can be dropped into any screen, following the exact pattern established
 * by EdgeByStrategyChart / SymbolSignalOverlayChart.
 *
 * Deliberately NOT the fuller Pilots table: no Simulate button, no
 * expand/holdings drill-down -- that richer version already exists in
 * `screens/StrategyInsights.tsx` and is intentionally out of scope here.
 */
export function PilotsTableWidget() {
  const { data, loading, error, status, reload } = useApi<PilotSummary[]>(
    () => api.listPilots(),
    []
  );

  if (loading) return <Loading lines={3} />;
  if (error) return <ErrorState message={error} status={status} onRetry={reload} />;
  if (!data || data.length === 0) {
    return <EmptyState title="No Pilots yet" hint="Pilots populate once the strategy registry has entries." />;
  }

  return (
    <div data-testid="pilotsTable-widget">
      <Table>
        <thead>
          <tr>
            <th>Pilot</th>
            <th>Category</th>
            <th className="num">Sharpe</th>
            <th className="num">Max DD</th>
          </tr>
        </thead>
        <tbody>
          {data.map((p) => (
            <tr key={p.id}>
              <td>{p.name}</td>
              <td>
                <span className="chip">{p.category}</span>
              </td>
              <td className="num">{fmtNum(p.headline.sharpe, 2)}</td>
              <td className="num">{fmtPct(p.headline.max_drawdown, 0, { fromFraction: true })}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}
