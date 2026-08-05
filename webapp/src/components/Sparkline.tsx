import { theme } from "../theme";

interface SparklineProps {
  data: number[];
  color?: string;
  width?: number;
  height?: number;
  strokeWidth?: number;
}

export function Sparkline({
  data,
  color,
  width = 80,
  height = 24,
  strokeWidth = 2,
}: SparklineProps) {
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
