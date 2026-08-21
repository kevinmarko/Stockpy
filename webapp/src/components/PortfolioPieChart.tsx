import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';
import { PortfolioPositionView } from '../api/types';

const COLORS = ['#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884d8', '#82ca9d', '#ffc658', '#8dd1e1', '#a4de6c', '#d0ed57'];

export function PortfolioPieChart({ positions }: { positions: PortfolioPositionView[] }) {
  if (!positions || positions.length === 0) return null;

  // Map to data for recharts
  const data = positions
    .filter(p => p.market_value && p.market_value > 0)
    .sort((a, b) => (b.market_value || 0) - (a.market_value || 0))
    .slice(0, 10) // Only show top 10 positions for visual clarity
    .map(p => ({
      name: p.symbol,
      value: p.market_value
    }));

  return (
    <div style={{ width: '100%', height: 200, marginTop: 'var(--s-3)' }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={60}
            outerRadius={80}
            fill="#8884d8"
            paddingAngle={5}
            dataKey="value"
          >
            {data.map((_, index) => (
              <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
            ))}
          </Pie>
          <Tooltip formatter={(value: any) => `$${Number(value).toFixed(2)}`} />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
