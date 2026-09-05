import React, { useMemo } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer
} from 'recharts';
import type { TrendsCurve } from "../../api/types";
import { theme } from "../../theme";
import { chartAxisTick, chartAxisLine, chartTooltipStyle } from "../charts";

export type { TrendsCurve } from "../../api/types";

interface TrendsStitchChartProps {
  rawCurves: TrendsCurve[];
  stitchedCurve: TrendsCurve;
}

// These curves carry calendar-date bars (no meaningful intraday time), and the
// backend encodes each date as UTC midnight (pandas treats a tz-naive Timestamp
// as UTC). Formatting with the viewer's LOCAL timezone would shift every date
// back by one calendar day for any timezone behind UTC (e.g. all of the US) --
// format in UTC (and pin the locale, so this is deterministic in tests
// regardless of the runtime's default locale) instead so the displayed date
// always matches the real bar date. Exported for direct unit testing.
export const formatUtcDate = (tickItem: any): string => {
  if (!tickItem) return '';
  return new Date(tickItem).toLocaleDateString('en-US', { timeZone: 'UTC' });
};

export const TrendsStitchChart: React.FC<TrendsStitchChartProps> = ({ rawCurves, stitchedCurve }) => {
  const chartData = useMemo(() => {
    const map = new Map<number, any>();
    
    rawCurves.forEach(curve => {
      curve.data.forEach(([ts, val]) => {
        if (!map.has(ts)) {
          map.set(ts, { timestamp: ts });
        }
        map.get(ts)[curve.name] = val;
      });
    });
    
    stitchedCurve.data.forEach(([ts, val]) => {
      if (!map.has(ts)) {
        map.set(ts, { timestamp: ts });
      }
      map.get(ts)[stitchedCurve.name] = val;
    });
    
    return Array.from(map.values()).sort((a, b) => a.timestamp - b.timestamp);
  }, [rawCurves, stitchedCurve]);

  const dateFormatter = formatUtcDate;

  return (
    <div className="card card-pad">
      {/* Fixed pixel height (matching AccountPerformanceChart.tsx's own
          convention), not height:'100%' -- Recharts' ResponsiveContainer
          measures its own container via ResizeObserver and needs a DEFINITE
          (non-percentage) height somewhere in its immediate ancestry to
          resolve against. A percentage/class-based height (e.g. Tailwind's
          h-[400px], which this codebase has no generated CSS for) silently
          measures 0x0 and never renders (see PR #846). */}
      <div style={{ width: '100%', height: 400 }}>
        <ResponsiveContainer>
          <LineChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
            <XAxis
              dataKey="timestamp"
              tickFormatter={dateFormatter}
              type="number"
              domain={['dataMin', 'dataMax']}
              {...chartAxisLine}
              tick={chartAxisTick}
            />
            <YAxis
              {...chartAxisLine}
              tick={chartAxisTick}
            />
            <Tooltip
              labelFormatter={dateFormatter}
              contentStyle={chartTooltipStyle}
              itemStyle={{ color: theme.textSecondary }}
            />
            <Legend wrapperStyle={{ paddingTop: '10px', color: theme.textSecondary }} />

            {rawCurves.map((curve) => (
              <Line
                key={curve.name}
                type="monotone"
                dataKey={curve.name}
                stroke={theme.textMuted}
                strokeDasharray="5 5"
                strokeOpacity={0.4}
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            ))}

            <Line
              type="monotone"
              dataKey={stitchedCurve.name}
              stroke={theme.accent}
              strokeWidth={3}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
