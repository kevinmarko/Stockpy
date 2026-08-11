import { api } from "../api/client";
import type { OptionsMatrix } from "../api/types";
import { useApi } from "../hooks/useApi";
import { EmptyState, ErrorState, Loading, Table } from "./ui";
import { fmtDateTime, fmtNum, fmtUsd } from "../format";
import { effectiveIvr } from "../optionsHonesty";
import { theme } from "../theme";

/**
 * Compact options-premium directive summary widget. Self-contained (fetches
 * its own data via `api.getOptions()`, no required props) so it can be
 * dropped into any Create Data App screen -- mirrors `EdgeByStrategyChart`/
 * `SymbolSignalOverlayChart`'s exact self-fetching pattern.
 *
 * Reads the SAME persisted matrix `screens/OptionsMatrix.tsx` renders in
 * full, condensed to a header row (regime/VIX/as-of) + one row per directive.
 * When `directives` is empty this honestly surfaces the server's `reason`
 * (e.g. "Options matrix not generated yet" or a VRP/IVR/VIX regime gate)
 * rather than rendering an empty table with no explanation (CONSTRAINT #4).
 */
export function OptionsDirectiveSummary() {
  const { data, loading, error, status, reload } = useApi<OptionsMatrix>(
    () => api.getOptions(),
    []
  );

  if (loading) return <Loading lines={3} />;
  if (error) return <ErrorState message={error} status={status} onRetry={reload} />;
  if (!data || data.directives.length === 0) {
    return (
      <EmptyState
        title="No options directives generated yet"
        hint={data?.reason ?? "The options matrix populates once the pipeline runs with premium selling enabled."}
      />
    );
  }

  return (
    <div data-testid="optionsDirective-widget">
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--s-4)",
          color: theme.textMuted,
          fontSize: "var(--t-caption)",
          marginBottom: "var(--s-2)",
        }}
      >
        <span>
          Regime: <strong style={{ color: theme.textPrimary }}>{data.market_regime ?? "—"}</strong>
        </span>
        <span>
          VIX: <strong style={{ color: theme.textPrimary }}>{fmtNum(data.vix, 1)}</strong>
        </span>
        <span>
          Target DTE: <strong style={{ color: theme.textPrimary }}>{data.target_dte ?? "—"}</strong>
        </span>
        <span>
          As of: <strong style={{ color: theme.textPrimary }}>{fmtDateTime(data.as_of)}</strong>
        </span>
      </div>

      <div style={{ overflowX: "auto" }}>
        <Table style={{ fontSize: "var(--t-label)", minWidth: 520 }}>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Strategy</th>
              <th>Action</th>
              <th className="num">Net Premium</th>
              <th className="num">IVR</th>
              <th>Integrity</th>
            </tr>
          </thead>
          <tbody>
            {data.directives.map((d, i) => {
              const ivr = effectiveIvr(d);
              const flagged = d.Integrity_OK !== true;
              return (
                <tr key={`${d.Symbol}-${i}`}>
                  <td>{d.Symbol}</td>
                  <td>{d.Strategy ?? "—"}</td>
                  <td>{d.Action ?? "—"}</td>
                  <td className="num">{fmtUsd(d.Net_Premium)}</td>
                  <td className="num">
                    {ivr.value == null ? "—" : `${fmtNum(ivr.value, 0)} (${ivr.isTrue ? "chain" : "proxy"})`}
                  </td>
                  <td style={{ color: flagged ? theme.caution : theme.growth }}>
                    {flagged ? "Flagged" : "OK"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </div>
    </div>
  );
}
