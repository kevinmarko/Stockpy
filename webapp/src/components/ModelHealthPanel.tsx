import { useMemo } from 'react';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  LabelList,
  LineChart,
  Line,
  Legend,
  Cell
} from 'recharts';
import { Activity, Loader2, GitBranch } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';
import DemoDataBadge from './DemoDataBadge';
import { EmptyState } from './ui';
import {
  chartAxisTick,
  chartAxisLine,
  chartGridProps,
  chartTooltipStyle,
} from './charts';
import { fmtPct } from '../format';

export function ModelHealthPanel({ symbol, compact }: { symbol?: string; compact?: boolean }) {
  // Always fetch model comparison for pipeline health flag & universe data
  const { data: compResponse, loading: compLoading } = useApi(() => api.getModelComparison(), []);
  
  // Only fetch forecast reliability if a symbol is provided
  const { data: forecastData, loading: forecastLoading } = useApi(
    () => symbol ? api.getForecast(symbol) : Promise.resolve(null),
    [symbol]
  );
  
  const loading = compLoading || (Boolean(symbol) && forecastLoading);
  const isSynthetic = compResponse?.is_synthetic || false;
  const compData = compResponse?.data || [];
  
  // Requirement 2: Honest EmptyState for synthetic / no data
  const hasNoData = isSynthetic || compData.length === 0;

  const barData = useMemo(() => {
    if (!forecastData?.skill_weights) return [];
    return Object.entries(forecastData.skill_weights).map(([name, weight]) => ({ name, weight }));
  }, [forecastData]);

  const renderContent = () => {
    if (loading) {
      return (
        <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Loader2 className="icon-spin" style={{ width: 24, height: 24, color: "var(--text-muted)" }} />
        </div>
      );
    }
    
    if (hasNoData) {
      return (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
          <EmptyState 
            title="No Model Health Data"
            hint="No model comparison data available yet. Forecast error tracking begins once the pipeline has recorded enough predictions." 
          />
        </div>
      );
    }

    // Requirement 3: BarChart for forecast reliability
    if (symbol && barData.length > 0) {
      return (
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={barData} margin={{ top: 20, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid {...chartGridProps} />
            <XAxis dataKey="name" tick={chartAxisTick} {...chartAxisLine} />
            <YAxis tick={chartAxisTick} {...chartAxisLine} tickFormatter={(val) => fmtPct(val, 0, { fromFraction: true })} />
            <Tooltip
              cursor={{ fill: 'rgba(51, 65, 85, 0.2)' }}
              contentStyle={chartTooltipStyle}
              formatter={(val: any) => [fmtPct(Number(val), 1, { fromFraction: true }), 'Trust Weight']}
            />
            <Bar dataKey="weight" fill="var(--accent)" radius={[4, 4, 0, 0]}>
              <LabelList 
                dataKey="weight" 
                position="top" 
                formatter={(val: any) => fmtPct(Number(val), 0, { fromFraction: true })} 
                fill="var(--text-secondary)" 
                fontSize={12} 
              />
              {barData.map((_, index) => (
                <Cell key={`cell-${index}`} fill={index % 2 === 0 ? "var(--accent)" : "var(--text-muted)"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      );
    }

    // Default: Universe-wide model comparison LineChart
    return (
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={compData} margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
          <CartesianGrid {...chartGridProps} />
          <XAxis dataKey="name" tick={chartAxisTick} {...chartAxisLine} />
          <YAxis tick={chartAxisTick} {...chartAxisLine} tickFormatter={(val) => `${val}%`} />
          <Tooltip contentStyle={chartTooltipStyle} />
          <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '10px' }} />
          <Line type="monotone" dataKey="SF-GARCH-LSTM" stroke="var(--accent)" strokeWidth={3} dot={{ r: 4, strokeWidth: 2 }} activeDot={{ r: 6 }} />
          <Line type="monotone" dataKey="Bond-BERT" stroke="var(--growth)" strokeWidth={2} dot={{ r: 3 }} />
          <Line type="monotone" dataKey="Benchmark (SPY)" stroke="var(--text-muted)" strokeWidth={2} strokeDasharray="5 5" dot={false} />
        </LineChart>
      </ResponsiveContainer>
    );
  };

  return (
    <div className={`card card-pad ${compact ? 'compact' : ''}`} style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "var(--s-2)",
          marginBottom: "var(--s-4)",
          paddingBottom: "var(--s-3)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          {symbol ? (
            <Activity style={{ width: 20, height: 20, color: "var(--caution)" }} />
          ) : (
            <GitBranch style={{ width: 20, height: 20, color: "var(--caution)" }} />
          )}
          <h3 style={{ margin: 0, fontWeight: 600, color: "var(--text-primary)" }}>
            {symbol ? `Forecast Skill (${symbol})` : "Model Strategy Comparison"}
          </h3>
        </div>
        {isSynthetic && <DemoDataBadge />}
      </div>

      <div style={{ flex: 1, minHeight: 300 }}>
        {renderContent()}
      </div>
    </div>
  );
}
