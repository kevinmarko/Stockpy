import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import { GitBranch, Loader2 } from 'lucide-react';
import { useNavigate } from 'react-router';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';
import type { ModelComparisonResponse } from '../api/types';
import DemoDataBadge from './DemoDataBadge';
import { theme, seriesColor } from '../theme';

export default function ModelComparisonChart() {
  const nav = useNavigate();
  const { data: responseData, loading, error } = useApi<ModelComparisonResponse>(() => api.getModelComparison(), []);
  const data = responseData?.data || [];

  return (
    <div className="card card-pad" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "var(--s-2)",
          marginBottom: "var(--s-6)",
          borderBottom: `1px solid ${theme.border}`,
          paddingBottom: "var(--s-4)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <GitBranch style={{ width: 20, height: 20, color: theme.caution }} />
          <h3 style={{ margin: 0, fontWeight: 600, color: theme.textPrimary }}>Model Strategy Comparison</h3>
        </div>
        {responseData?.is_synthetic && <DemoDataBadge />}
      </div>

      <div style={{ flex: 1, minHeight: 300 }} data-testid="model-comparison-chart">
        {loading ? (
          <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Loader2 className="icon-spin" style={{ width: 24, height: 24, color: theme.textMuted }} />
          </div>
        ) : error ? (
          <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: theme.decline }}>
            Failed to load model comparison
          </div>
        ) : data.length === 0 ? (
          // "SF-GARCH-LSTM"/"Bond-BERT" are undeployed ridge-regression
          // stand-ins with no tracked real return history to compare (see
          // api/metrics_api.py::get_model_comparison docstring) -- an empty
          // chart with no explanation would look like a loading glitch, so
          // this states the honest reason instead, with a decorative faint
          // squiggle (no real data plotted) and a link to the real, already-
          // shipped validation-history screen instead of a dead end.
          <div
            data-testid="model-comparison-empty"
            style={{
              width: "100%",
              height: "100%",
              position: "relative",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              textAlign: "center",
              gap: "var(--s-4)",
              padding: "0 var(--s-6)",
              overflow: "hidden",
            }}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 300 100"
              preserveAspectRatio="none"
              style={{ position: "absolute", inset: 0, width: "100%", height: "100%", opacity: 0.15 }}
            >
              <polyline
                points="0,70 40,55 80,62 120,35 160,48 200,20 240,32 300,10"
                fill="none"
                stroke={theme.textMuted}
                strokeWidth={2}
              />
              <polyline
                points="0,85 40,80 80,88 120,70 160,75 200,60 240,68 300,55"
                fill="none"
                stroke={theme.textMuted}
                strokeWidth={2}
                strokeDasharray="4 4"
              />
            </svg>
            <div style={{ position: "relative", fontSize: "var(--t-body)", color: theme.textMuted }}>
              Unavailable — no deployed model has tracked return history to compare yet.
            </div>
            <button
              type="button"
              className="btn btn-neutral"
              style={{ position: "relative" }}
              onClick={() => nav("/strategy-health")}
            >
              View Validation History
            </button>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={theme.chartGrid} />
            <XAxis dataKey="name" tick={{ fill: theme.textMuted, fontSize: 12 }} tickLine={false} axisLine={{ stroke: theme.border }} />
            <YAxis tickFormatter={(val) => `${val}%`} tick={{ fill: theme.textMuted, fontSize: 12 }} tickLine={false} axisLine={false} />
            <Tooltip
              contentStyle={{ backgroundColor: theme.surface3, borderColor: theme.borderStrong, borderRadius: '8px', color: theme.textPrimary }}
              itemStyle={{ fontSize: 13 }}
            />
            <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '10px' }} />
            <Line type="monotone" dataKey="SF-GARCH-LSTM" stroke={seriesColor(0)} strokeWidth={3} dot={{ r: 4, strokeWidth: 2 }} activeDot={{ r: 6 }} />
            <Line type="monotone" dataKey="Bond-BERT" stroke={seriesColor(1)} strokeWidth={2} dot={{ r: 3 }} />
            <Line type="monotone" dataKey="Benchmark (SPY)" stroke={theme.textMuted} strokeWidth={2} strokeDasharray="5 5" dot={false} />
          </LineChart>
        </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
