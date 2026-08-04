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
  Cell
} from 'recharts';
import { Activity, Loader2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';
import { EmptyState } from './ui';
import ModelComparisonChart from './ModelComparisonChart';
import {
  chartAxisTick,
  chartAxisLine,
  chartGridProps,
  chartTooltipStyle,
} from './charts';
import { fmtPct } from '../format';

/**
 * ModelHealthPanel — per-symbol forecast-skill (inverse-RMSE trust weight)
 * bar chart, used from the Agentic Trading screen. When no `symbol` is
 * given it delegates entirely to the pre-existing, already-mounted
 * `ModelComparisonChart` (used on the Models screen) rather than
 * re-authoring the same universe-wide chart here -- keeping this file's
 * actual contribution scoped to the symbol-driven forecast-skill view.
 */
export function ModelHealthPanel({ symbol, compact }: { symbol?: string; compact?: boolean }) {
  // Only fetch forecast reliability if a symbol is provided. Hooks are
  // always called in the same order regardless of `symbol` -- the early
  // return below happens after every hook call, never before/between them.
  const { data: forecastData, loading: forecastLoading } = useApi(
    () => symbol ? api.getForecast(symbol) : Promise.resolve(null),
    [symbol]
  );

  const barData = useMemo(() => {
    if (!forecastData?.skill_weights) return [];
    return Object.entries(forecastData.skill_weights).map(([name, weight]) => ({ name, weight }));
  }, [forecastData]);

  // No symbol -> reuse the universe-wide comparison component wholesale
  // (same fetch, same honesty gate, same header) instead of duplicating it.
  if (!symbol) {
    return <ModelComparisonChart />;
  }

  const renderContent = () => {
    if (forecastLoading) {
      return (
        <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Loader2 className="icon-spin" style={{ width: 24, height: 24, color: "var(--text-muted)" }} />
        </div>
      );
    }

    if (barData.length === 0) {
      return (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
          <EmptyState
            title="No Model Health Data"
            hint={
              forecastData?.reason ??
              "No forecast skill weights yet for this symbol. Skill weighting begins once the pipeline has recorded enough predictions."
            }
          />
        </div>
      );
    }

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
          <Activity style={{ width: 20, height: 20, color: "var(--caution)" }} />
          <h3 style={{ margin: 0, fontWeight: 600, color: "var(--text-primary)" }}>
            {`Forecast Skill (${symbol})`}
          </h3>
        </div>
        {/* ForecastSkill carries no is_synthetic flag of its own (see
            api/types.ts) -- an empty/absent reliability curve already
            renders the honest EmptyState above, so there is nothing to
            badge here. */}
      </div>

      <div style={{ flex: 1, minHeight: 300 }}>
        {renderContent()}
      </div>
    </div>
  );
}
