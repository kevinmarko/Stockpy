import { useState, useEffect } from "react";
import { theme } from "../../theme";
import { api } from "../../api/client";
import type { CacheLongShortDashboard } from "../../api/types";

export function TaxDashboard() {
  const [data, setData] = useState<CacheLongShortDashboard | null>(null);

  useEffect(() => {
    api.getClsDashboard()
      .then(setData)
      .catch(console.error);
  }, []);

  if (!data) return <div>Loading dashboard...</div>;
  if (data.status === "disabled") return <div style={{ color: theme.textSecondary }}>Cache Long/Short is currently disabled in settings.</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: 16 }}>
        <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
          <h3 style={{ margin: "0 0 8px", color: theme.textSecondary, fontSize: 14 }}>Tax Bank</h3>
          <div style={{ fontSize: 32, fontWeight: 700, color: theme.growth }}>+${data.tax_bank?.toFixed(2) || "0.00"}</div>
          <div style={{ fontSize: 12, color: theme.textMuted, marginTop: 8 }}>Available losses to offset gains</div>
        </div>
        
        <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
          <h3 style={{ margin: "0 0 8px", color: theme.textSecondary, fontSize: 14 }}>Estimated Tax Saved</h3>
          <div style={{ fontSize: 32, fontWeight: 700, color: theme.growth }}>${((data.tax_bank || 0) * 0.20).toFixed(2)}</div>
          <div style={{ fontSize: 12, color: theme.textMuted, marginTop: 8 }}>Assuming 20% Capital Gains</div>
        </div>
      </div>

      <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
        <h3 style={{ margin: "0 0 16px" }}>Exposure Overview</h3>
        <div style={{ display: "flex", gap: 48 }}>
          <div>
            <div style={{ color: theme.textSecondary, marginBottom: 4 }}>Long Exposure</div>
            <div style={{ fontSize: 24, fontWeight: 600 }}>${data.exposure?.long_exposure?.toFixed(2) || "0.00"}</div>
          </div>
          <div>
            <div style={{ color: theme.textSecondary, marginBottom: 4 }}>Short Exposure</div>
            <div style={{ fontSize: 24, fontWeight: 600 }}>${data.exposure?.short_exposure?.toFixed(2) || "0.00"}</div>
          </div>
          <div>
            <div style={{ color: theme.textSecondary, marginBottom: 4 }}>Net Exposure</div>
            <div style={{ fontSize: 24, fontWeight: 600 }}>${data.exposure?.net_exposure?.toFixed(2) || "0.00"}</div>
          </div>
          <div>
            <div style={{ color: theme.textSecondary, marginBottom: 4 }}>Gross Exposure</div>
            <div style={{ fontSize: 24, fontWeight: 600 }}>${data.exposure?.gross_exposure?.toFixed(2) || "0.00"}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
