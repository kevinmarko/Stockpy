import { useState, useEffect } from "react";
import { theme } from "../../theme";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";

export function TaxDashboard() {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    fetch("/api/v1/strategy/cache-long-short/dashboard")
      .then(res => res.json())
      .then(setData)
      .catch(console.error);
  }, []);

  if (!data) return <div>Loading dashboard...</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: 16 }}>
        <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
          <h3 style={{ margin: "0 0 8px", color: theme.textSecondary, fontSize: 14 }}>Tax Bank</h3>
          <div style={{ fontSize: 32, fontWeight: 700, color: theme.growth }}>+${data.tax_bank.toFixed(2)}</div>
          <div style={{ fontSize: 12, color: theme.textMuted, marginTop: 8 }}>Available losses to offset gains</div>
        </div>
        
        <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
          <h3 style={{ margin: "0 0 8px", color: theme.textSecondary, fontSize: 14 }}>Diversification Progress</h3>
          <div style={{ fontSize: 32, fontWeight: 700 }}>{data.diversification_progress.toFixed(1)}%</div>
          <div style={{ width: "100%", height: 6, background: theme.surface2, borderRadius: 3, marginTop: 12, overflow: "hidden" }}>
            <div style={{ width: `${(data.diversification_progress / data.diversification_target) * 100}%`, height: "100%", background: theme.accent }} />
          </div>
        </div>

        <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
          <h3 style={{ margin: "0 0 8px", color: theme.textSecondary, fontSize: 14 }}>Estimated Tax Saved</h3>
          <div style={{ fontSize: 32, fontWeight: 700, color: theme.growth }}>${data.estimated_tax_saved.toFixed(2)}</div>
          <div style={{ fontSize: 12, color: theme.textMuted, marginTop: 8 }}>Assuming 20% Capital Gains</div>
        </div>
      </div>

      <div className="card" style={{ padding: 24, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8 }}>
        <h3 style={{ margin: "0 0 16px" }}>Exposure Overview</h3>
        <div style={{ display: "flex", gap: 48 }}>
          <div>
            <div style={{ color: theme.textSecondary, marginBottom: 4 }}>Net Exposure</div>
            <div style={{ fontSize: 24, fontWeight: 600 }}>${data.net_exposure.toFixed(2)}</div>
          </div>
          <div>
            <div style={{ color: theme.textSecondary, marginBottom: 4 }}>Gross Exposure</div>
            <div style={{ fontSize: 24, fontWeight: 600 }}>${data.gross_exposure.toFixed(2)}</div>
          </div>
        </div>
        <div style={{ height: 250, width: "100%", marginTop: 24 }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data.history || []} margin={{ top: 10, right: 0, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="netExp" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={theme.accent} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={theme.accent} stopOpacity={0} />
                </linearGradient>
                <linearGradient id="grossExp" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={theme.textSecondary} stopOpacity={0.1} />
                  <stop offset="95%" stopColor={theme.textSecondary} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={theme.border} />
              <XAxis dataKey="date" tick={{ fill: theme.textMuted, fontSize: 12 }} tickLine={false} axisLine={false} minTickGap={30} />
              <YAxis tick={{ fill: theme.textMuted, fontSize: 12 }} tickLine={false} axisLine={false} tickFormatter={(val) => `$${val}`} />
              <Tooltip 
                contentStyle={{ background: theme.surface3, border: `1px solid ${theme.borderStrong}`, borderRadius: 8, color: theme.textPrimary }}
                itemStyle={{ color: theme.textPrimary }}
              />
              <Area type="monotone" dataKey="gross_exposure" name="Gross Exposure" stroke={theme.textSecondary} fillOpacity={1} fill="url(#grossExp)" />
              <Area type="monotone" dataKey="net_exposure" name="Net Exposure" stroke={theme.accent} fillOpacity={1} fill="url(#netExp)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
