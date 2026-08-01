import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer } from 'recharts';
import { Globe, TrendingDown, TrendingUp, Minus, Loader2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';
import type { MacroSentimentResponse } from '../api/types';
import DemoDataBadge from './DemoDataBadge';

export default function MacroSentimentDashboard() {
  const { data, loading, error } = useApi<MacroSentimentResponse>(() => api.getMacroSentiment(), []);

  const macroData = data?.macro_data || [];

  const renderTrendIcon = (trend: string) => {
    switch (trend) {
      case 'up': return <TrendingUp style={{ width: 16, height: 16, color: "var(--growth)" }} />;
      case 'down': return <TrendingDown style={{ width: 16, height: 16, color: "var(--decline)" }} />;
      default: return <Minus style={{ width: 16, height: 16, color: "var(--text-muted)" }} />;
    }
  };

  return (
    <div className="card card-pad" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--s-2)",
          marginBottom: "var(--s-6)",
          borderBottom: "1px solid var(--border)",
          paddingBottom: "var(--s-4)",
        }}
      >
        <Globe style={{ width: 20, height: 20, color: "#14b8a6" }} />
        <h3 style={{ margin: 0, fontWeight: 600, color: "var(--text-primary)" }}>Macroeconomic Sentiment</h3>
        {data?.is_synthetic && <DemoDataBadge />}
      </div>

      <div className="detail-grid" style={{ flex: 1, marginBottom: 0 }}>
        {loading ? (
          <div style={{ gridColumn: "1 / -1", width: "100%", display: "flex", alignItems: "center", justifyContent: "center", padding: "var(--s-8)" }}>
            <Loader2 className="icon-spin" style={{ width: 24, height: 24, color: "var(--text-muted)" }} />
          </div>
        ) : error ? (
          <div style={{ gridColumn: "1 / -1", width: "100%", textAlign: "center", color: "var(--decline)", padding: "var(--s-8)" }}>Failed to load macro sentiment</div>
        ) : macroData.length === 0 ? (
          <div style={{ gridColumn: "1 / -1", width: "100%", textAlign: "center", color: "var(--text-secondary)", padding: "var(--s-8)" }}>
            {data?.reason || 'No macro data available yet.'}
          </div>
        ) : (
          <>
            <div style={{ width: "100%", height: 300 }}>
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart cx="50%" cy="50%" outerRadius="80%" data={macroData}>
              <PolarGrid stroke="#334155" opacity={0.3} />
              <PolarAngleAxis dataKey="subject" tick={{ fill: '#64748b', fontSize: 11 }} />
              <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
              <Radar name="Current Sentiment" dataKey="value" stroke="#14b8a6" fill="#14b8a6" fillOpacity={0.3} />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        <div style={{ width: "100%", display: "flex", flexDirection: "column", justifyContent: "center" }}>
          <h4 style={{ fontSize: "var(--t-body)", fontWeight: 600, color: "var(--text-primary)", marginBottom: "var(--s-4)" }}>Key Drivers</h4>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
            {macroData.map((item, idx) => (
              <div key={idx} className="macro-driver-row">
                <span style={{ fontSize: "var(--t-body)", color: "var(--text-secondary)", fontWeight: 500 }}>{item.subject}</span>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--s-3)" }}>
                  <div className="macro-driver-track">
                    <div
                      className="macro-driver-fill"
                      style={{ width: `${item.value}%` }}
                    />
                  </div>
                  {renderTrendIcon(item.trend)}
                </div>
              </div>
            ))}
          </div>
        </div>
      </>
        )}
      </div>
    </div>
  );
}
