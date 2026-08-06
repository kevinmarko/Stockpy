import { theme } from "../../theme";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { Loading, ErrorState, Notice } from "../ui";

export function TaxDashboard() {
  const { data, loading, error, status, reload } = useApi(() => api.getClsDashboard(), []);

  if (loading) return <Loading />;
  if (error) return <ErrorState message={error} status={status} onRetry={reload} />;
  if (!data) return null;

  if (data.status === "disabled") {
    return (
      <Notice variant="info">
        Cache Long/Short is currently disabled (CACHE_LONG_SHORT_ENABLED=false).
      </Notice>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: 16 }}>
        <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
          <h3 style={{ margin: "0 0 8px", color: theme.textSecondary, fontSize: 14 }}>Tax Bank</h3>
          <div style={{ fontSize: 32, fontWeight: 700, color: theme.growth }}>+${(data.tax_bank ?? 0).toFixed(2)}</div>
          <div style={{ fontSize: 12, color: theme.textMuted, marginTop: 8 }}>Available losses to offset gains</div>
        </div>

        <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
          <h3 style={{ margin: "0 0 8px", color: theme.textSecondary, fontSize: 14 }}>Estimated Tax Saved</h3>
          <div style={{ fontSize: 32, fontWeight: 700, color: theme.growth }}>${((data.tax_bank ?? 0) * 0.20).toFixed(2)}</div>
          <div style={{ fontSize: 12, color: theme.textMuted, marginTop: 8 }}>Assuming 20% Capital Gains</div>
        </div>
      </div>

      <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
        <h3 style={{ margin: "0 0 16px" }}>Exposure Overview</h3>
        <div style={{ display: "flex", gap: 48, flexWrap: "wrap" }}>
          <div>
            <div style={{ color: theme.textSecondary, marginBottom: 4 }}>Long Exposure</div>
            <div style={{ fontSize: 24, fontWeight: 600 }}>${(data.exposure?.long_exposure ?? 0).toFixed(2)}</div>
          </div>
          <div>
            <div style={{ color: theme.textSecondary, marginBottom: 4 }}>Short Exposure</div>
            <div style={{ fontSize: 24, fontWeight: 600 }}>${(data.exposure?.short_exposure ?? 0).toFixed(2)}</div>
          </div>
          <div>
            <div style={{ color: theme.textSecondary, marginBottom: 4 }}>Net Exposure</div>
            <div style={{ fontSize: 24, fontWeight: 600 }}>${(data.exposure?.net_exposure ?? 0).toFixed(2)}</div>
          </div>
          <div>
            <div style={{ color: theme.textSecondary, marginBottom: 4 }}>Gross Exposure</div>
            <div style={{ fontSize: 24, fontWeight: 600 }}>${(data.exposure?.gross_exposure ?? 0).toFixed(2)}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
