import { useApi } from "../hooks/useApi";
import { api } from "../api/client";
import { Loading, ErrorState, EmptyState, Table, Chip } from "../components/ui";
import { theme } from "../theme";

const formatPct = (val: number | null) => {
  if (val == null) return "—";
  const sign = val > 0 ? "+" : "";
  return `${sign}${val.toFixed(1)}%`;
};

const formatDecimal = (val: number | null) => (val == null ? "—" : val.toFixed(2));

export default function Experiments() {
  const { data, error, loading, status, reload } = useApi(() => api.getExperiments(), []);

  if (loading) return <Loading />;
  if (error) {
    if (status === 404) {
      return (
        <EmptyState
          title="No Experiments"
          hint="No experiments are currently configured."
        />
      );
    }
    return <ErrorState message={error} status={status} onRetry={reload} />;
  }
  if (!data || data.experiments.length === 0) {
    return (
      <EmptyState
        title="No Experiments"
        hint="No experiments are currently configured."
      />
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
      <div>
        <h2 style={{ margin: "0 0 var(--s-1)", fontSize: "var(--t-title)" }}>Experiments</h2>
        <p style={{ color: theme.textSecondary, margin: 0, fontSize: "var(--t-body)" }}>
          A/B testing framework for evaluating strategy and pipeline changes.
        </p>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
        {data.experiments.map((exp) => (
          <div key={exp.id} className="card card-pad">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--s-3)" }}>
              <div>
                <h3 style={{ margin: "0 0 var(--s-1)", fontSize: "var(--t-subtitle)" }}>{exp.name}</h3>
                <p style={{ margin: 0, color: theme.textSecondary, fontSize: "var(--t-body)" }}>{exp.description}</p>
              </div>
              <Chip
                label={exp.state}
                tone={
                  exp.state === "running"
                    ? "growth"
                    : exp.state === "insufficient_data"
                    ? "caution"
                    : "muted"
                }
              />
            </div>

            <div style={{ marginBottom: "var(--s-3)" }}>
              <div style={{ fontSize: "var(--t-caption)", fontWeight: 600, marginBottom: "var(--s-1)", color: theme.textPrimary }}>Arms</div>
              <div style={{ display: "flex", gap: "var(--s-2)", flexWrap: "wrap" }}>
                {exp.arms.map((arm) => (
                  <div key={arm.id} style={{ fontSize: "var(--t-caption)", border: `1px solid ${theme.borderStrong}`, padding: "4px 8px", borderRadius: "var(--r-sm)", background: theme.surface2 }}>
                    <span style={{ fontWeight: 600 }}>{arm.name}</span> ({arm.weight}%)
                  </div>
                ))}
              </div>
            </div>

            {exp.state === "insufficient_data" && exp.reason && (
              <div style={{ background: theme.surface3, color: theme.textSecondary, padding: "var(--s-2)", borderRadius: "var(--r-sm)", fontSize: "var(--t-caption)", marginBottom: "var(--s-3)" }}>
                {exp.reason}
              </div>
            )}

            {exp.comparisons && exp.comparisons.length > 0 && (
              <div>
                <div style={{ fontSize: "var(--t-caption)", fontWeight: 600, marginBottom: "var(--s-2)", color: theme.textPrimary }}>Results</div>
                <div style={{ overflowX: "auto" }}>
                  <Table>
                    <thead>
                      <tr>
                        <th>Metric</th>
                        <th className="num">Control</th>
                        <th className="num">Treatment</th>
                        <th className="num">Delta</th>
                        <th className="num">p-value</th>
                        <th style={{ textAlign: "center" }}>Significant</th>
                      </tr>
                    </thead>
                    <tbody>
                      {exp.comparisons.map((comp) => (
                        <tr key={comp.metric_name}>
                          <td>{comp.metric_name}</td>
                          <td className="num">{formatDecimal(comp.control_value)}</td>
                          <td className="num">{formatDecimal(comp.treatment_value)}</td>
                          <td className="num">{formatPct(comp.relative_delta_pct)}</td>
                          <td className="num">{formatDecimal(comp.p_value)}</td>
                          <td style={{ textAlign: "center" }}>
                            {comp.significant == null ? (
                              "—"
                            ) : comp.significant ? (
                              <Chip label="Yes" tone="growth" />
                            ) : (
                              <Chip label="No" tone="muted" />
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </div>
              </div>
            )}
            
            {exp.comparisons == null && exp.state !== "insufficient_data" && (
                <div style={{ fontSize: "var(--t-caption)", color: theme.textMuted, marginTop: "var(--s-3)" }}>No comparison results available yet.</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
