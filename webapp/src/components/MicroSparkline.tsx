import { theme } from "../theme";

/**
 * MicroSparkline — a bare-bones SVG `<polyline>` sparkline, deliberately kept
 * separate from `charts.tsx`'s (Recharts-based) `Sparkline` component rather
 * than merged or renamed to match it. The two solve different problems:
 *
 * - `charts.tsx`'s `Sparkline` wraps Recharts' `ResponsiveContainer` +
 *   `AreaChart` + `Tooltip` over `CurvePoint[]` (date/value pairs) — full
 *   chart chrome (hover tooltip, responsive sizing) for marketplace cards
 *   where a handful of instances mount at once.
 * - `MicroSparkline` takes a plain `number[]` and draws it with zero
 *   dependencies beyond a `<svg>`/`<polyline>`. It's intentionally
 *   lighter-weight so it's safe to mount many at once inside dense,
 *   potentially-virtualized table cells (e.g. via `DataTable`'s existing
 *   `Column.render` prop) — a full Recharts chart per row/cell would be far
 *   too expensive there.
 *
 * NOT currently wired into any screen — there's no real per-row historical
 * time series in the one table that exists today (`DataTable`'s consumers).
 * This is a ready-to-use primitive for whenever a table screen with real
 * trend data adopts it; do not fabricate a consumer for it.
 */
interface MicroSparklineProps {
  data: number[];
  color?: string;
  width?: number;
  height?: number;
  strokeWidth?: number;
}

export function MicroSparkline({
  data,
  color,
  width = 80,
  height = 24,
  strokeWidth = 2,
}: MicroSparklineProps) {
  if (!data || data.length === 0) return null;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1; // avoid division by zero

  const xStep = width / Math.max(1, data.length - 1);
  const yRatio = (height - strokeWidth * 2) / range;

  const points = data
    .map((val, i) => {
      const x = i * xStep;
      const y = height - strokeWidth - (val - min) * yRatio;
      return `${x},${y}`;
    })
    .join(" ");

  const isPositive = data[data.length - 1] >= data[0];
  const defaultColor = isPositive ? theme.growth : theme.decline;
  const strokeColor = color ?? defaultColor;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ overflow: "visible" }}>
      <polyline
        fill="none"
        stroke={strokeColor}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points}
      />
    </svg>
  );
}
