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
    <div className="w-full bg-zinc-900 border border-zinc-800 rounded-lg p-4 shadow-sm h-[400px]">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
          <XAxis 
            dataKey="timestamp" 
            tickFormatter={dateFormatter} 
            type="number"
            domain={['dataMin', 'dataMax']}
            stroke="#a1a1aa"
            tick={{ fill: '#a1a1aa', fontSize: 12, fontFamily: '"JetBrains Mono", monospace' }}
          />
          <YAxis 
            stroke="#a1a1aa" 
            tick={{ fill: '#a1a1aa', fontSize: 12, fontFamily: '"JetBrains Mono", monospace' }}
          />
          <Tooltip 
            labelFormatter={dateFormatter}
            contentStyle={{ backgroundColor: '#18181b', borderColor: '#27272a', color: '#f4f4f5', fontFamily: '"DM Sans", sans-serif' }}
            itemStyle={{ color: '#e4e4e7', fontFamily: '"DM Sans", sans-serif' }}
          />
          <Legend wrapperStyle={{ paddingTop: '10px', fontFamily: '"DM Sans", sans-serif', color: '#e4e4e7' }} />
          
          {rawCurves.map((curve) => (
            <Line
              key={curve.name}
              type="monotone"
              dataKey={curve.name}
              stroke="#a1a1aa"
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
            stroke="#3b82f6"
            strokeWidth={3}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};
