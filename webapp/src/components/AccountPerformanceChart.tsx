import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer
} from 'recharts';
import { CurvePoint } from '../api/types';

interface Props {
  data: CurvePoint[];
}

export default function AccountPerformanceChart({ data }: Props) {
  if (!data || data.length === 0) {
    return (
      <div
        className="empty"
        data-testid="equity-empty"
        style={{ padding: "var(--s-8) var(--s-2)", background: "var(--surface-2)", borderRadius: "var(--r-md)", height: '100%' }}
      >
        <div style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>
          No account performance data yet
        </div>
        <div style={{ marginTop: "var(--s-1-5)", fontSize: "var(--t-body)" }}>
          No curve data available. Run the Stockpy pipeline to accumulate an
          account equity history.
        </div>
      </div>
    );
  }

  // Format dates for display
  const formattedData = data.map(item => {
    const dateObj = new Date(item.date);
    return {
      ...item,
      displayDate: dateObj.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    };
  });

  return (
    <div style={{ width: '100%', height: '100%', minHeight: 200 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={formattedData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
          <XAxis
            dataKey="displayDate"
            axisLine={false}
            tickLine={false}
            tick={{ fill: '#888', fontSize: 12 }}
            minTickGap={30}
          />
          <YAxis
            domain={['auto', 'auto']}
            axisLine={false}
            tickLine={false}
            tick={{ fill: '#888', fontSize: 12 }}
            tickFormatter={(val) => `$${val.toLocaleString()}`}
          />
          <Tooltip
            formatter={(value: any) => [`$${Number(value).toFixed(2)}`, 'Equity']}
            labelStyle={{ color: '#333', fontWeight: 'bold' }}
            contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke="#10b981"
            strokeWidth={2.5}
            dot={false}
            activeDot={{ r: 5, fill: '#10b981', stroke: '#fff', strokeWidth: 2 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
