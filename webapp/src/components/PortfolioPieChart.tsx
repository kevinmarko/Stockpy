import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';
import { PortfolioPositionView } from '../api/types';
import { theme, sectorColor } from '../theme';
import { fmtUsd } from '../format';
import { chartTooltipStyle } from './charts';

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
            paddingAngle={5}
            dataKey="value"
            stroke={theme.surface}
            strokeWidth={2}
          >
            {/* sectorColor(i) is this app's validated (light+dark,
                contrast-checked) categorical ramp -- see theme.ts and
                SectorDonut's use of the same helper -- rather than an
                unthemed hex list that ignores the app's dark-first default
                surface and won't adapt if the operator switches themes. */}
            {data.map((_, index) => (
              <Cell key={`cell-${index}`} fill={sectorColor(index)} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={chartTooltipStyle}
            formatter={(value: any) => fmtUsd(Number(value))}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
