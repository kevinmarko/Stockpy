import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';
import { PortfolioPositionView } from '../api/types';
import { theme, sectorColor } from '../theme';
import { fmtUsd } from '../format';
import { chartTooltipStyle } from './charts';

export function PortfolioPieChart({ positions }: { positions: PortfolioPositionView[] }) {
  // Build the chart data BEFORE the empty-state guard and check ITS length,
  // not the raw `positions` length -- a portfolio can have positions present
  // but every market_value null/zero/negative (e.g. quotes not yet resolved),
  // which would otherwise pass a `positions.length === 0` guard and render an
  // empty Pie region instead of an honest "nothing to show" state.
  const data = (positions ?? [])
    .filter(p => p.market_value && p.market_value > 0)
    .sort((a, b) => (b.market_value || 0) - (a.market_value || 0))
    .slice(0, 10) // Only show top 10 positions for visual clarity
    .map((p, i) => ({
      name: p.symbol,
      value: p.market_value as number,
      color: sectorColor(i),
    }));

  if (data.length === 0) return null;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", marginTop: 'var(--s-3)' }}>
      <div style={{ width: 148, height: 148, flex: "0 0 auto" }}>
        <ResponsiveContainer>
          <PieChart>
            <Pie
              data={data}
              cx="50%"
              cy="50%"
              innerRadius={44}
              outerRadius={68}
              paddingAngle={5}
              dataKey="value"
              stroke={theme.surface}
              strokeWidth={2}
              isAnimationActive={false}
            >
              {/* sectorColor(i) is this app's validated (light+dark,
                  contrast-checked) categorical ramp -- see theme.ts and
                  SectorDonut's use of the same helper -- rather than an
                  unthemed hex list that ignores the app's dark-first default
                  surface and won't adapt if the operator switches themes. */}
              {data.map((d, index) => (
                <Cell key={`cell-${index}`} fill={d.color} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={chartTooltipStyle}
              formatter={(value: any) => fmtUsd(Number(value))}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      {/* Direct-label legend, mirroring SectorDonut's own -- identity is
          never color-alone (a11y/legibility parity with the sibling donut). */}
      <ul
        style={{
          listStyle: "none",
          margin: 0,
          padding: 0,
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: "var(--s-1-5)",
          minWidth: 0,
        }}
      >
        {data.map((d) => (
          <li
            key={d.name}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--s-2)",
              fontSize: "var(--t-label)",
            }}
          >
            <span
              aria-hidden
              style={{
                width: 10,
                height: 10,
                borderRadius: 3,
                background: d.color,
                flex: "0 0 auto",
              }}
            />
            <span
              style={{
                color: theme.textSecondary,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {d.name}
            </span>
            <span
              className="num"
              style={{ marginLeft: "auto", color: theme.textPrimary, fontWeight: 600 }}
            >
              {fmtUsd(d.value, { compact: true })}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
