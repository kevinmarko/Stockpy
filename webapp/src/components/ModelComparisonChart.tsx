import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import { GitBranch, Loader2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';
import type { ModelComparisonResponse } from '../api/types';
import DemoDataBadge from './DemoDataBadge';

export default function ModelComparisonChart() {
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
          borderBottom: "1px solid var(--border)",
          paddingBottom: "var(--s-4)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <GitBranch style={{ width: 20, height: 20, color: "var(--caution)" }} />
          <h3 style={{ margin: 0, fontWeight: 600, color: "var(--text-primary)" }}>Model Strategy Comparison</h3>
        </div>
        {responseData?.is_synthetic && <DemoDataBadge />}
      </div>

      <div style={{ flex: 1, minHeight: 300 }}>
        {loading ? (
          <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Loader2 className="icon-spin" style={{ width: 24, height: 24, color: "var(--text-muted)" }} />
          </div>
        ) : error ? (
          <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--decline)" }}>
            Failed to load model comparison
          </div>
        ) : data.length === 0 ? (
          // "SF-GARCH-LSTM"/"Bond-BERT" are undeployed ridge-regression
          // stand-ins with no tracked real return history to compare (see
          // api/metrics_api.py::get_model_comparison docstring) -- an empty
          // chart with no explanation would look like a loading glitch, so
          // this states the honest reason instead.
          <div
            style={{
              width: "100%",
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              textAlign: "center",
              fontSize: "var(--t-body)",
              color: "var(--text-muted)",
              padding: "0 var(--s-6)",
            }}
          >
            Unavailable — no deployed model has tracked return history to compare yet.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#334155" opacity={0.2} />
            <XAxis dataKey="name" tick={{ fill: '#64748b', fontSize: 12 }} tickLine={false} axisLine={{ stroke: '#334155', opacity: 0.5 }} />
            <YAxis tickFormatter={(val) => `${val}%`} tick={{ fill: '#64748b', fontSize: 12 }} tickLine={false} axisLine={false} />
            <Tooltip
              contentStyle={{ backgroundColor: 'rgba(15, 23, 42, 0.9)', borderColor: '#334155', borderRadius: '8px', color: '#f8fafc' }}
              itemStyle={{ fontSize: 13 }}
            />
            <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '10px' }} />
            <Line type="monotone" dataKey="SF-GARCH-LSTM" stroke="#3b82f6" strokeWidth={3} dot={{ r: 4, strokeWidth: 2 }} activeDot={{ r: 6 }} />
            <Line type="monotone" dataKey="Bond-BERT" stroke="#10b981" strokeWidth={2} dot={{ r: 3 }} />
            <Line type="monotone" dataKey="Benchmark (SPY)" stroke="#64748b" strokeWidth={2} strokeDasharray="5 5" dot={false} />
          </LineChart>
        </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
