import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendsRescaledWindow, TrendsSeam, TrendsStitched } from "../api/types";
import { theme } from "../theme";
import { chartAxisLine, chartAxisTick, chartCursorProps, chartGridProps, chartTooltipStyle } from "./charts";
import { ChartEmpty, formatUtcDate, isoToUtcMs, mergeSeries } from "./TrendsRawWindowsChart";

export function TrendsStitchChart({
  rescaled,
  stitched,
  seams,
}: {
  rescaled: TrendsRescaledWindow[];
  stitched: TrendsStitched | null;
  seams: TrendsSeam[];
}) {
  const hasStitched = !!stitched && (stitched.points ?? []).some(([, v]) => v != null);
  const wins = (rescaled ?? []).filter((w) => w && (w.points ?? []).some(([, v]) => v != null));
  if (!hasStitched && wins.length === 0) {
    return <ChartEmpty testId="trends-stitch-empty">No stitched series to show</ChartEmpty>;
  }
  const series = [
    ...wins.map((w, i) => ({ key: `r${i}`, points: w.points })),
    ...(hasStitched ? [{ key: "stitched", points: stitched!.points }] : []),
  ];
  const rows = mergeSeries(series);
  const areas = (seams ?? [])
    .map((s) => ({ index: s.index, x1: isoToUtcMs(s.overlap_start), x2: isoToUtcMs(s.overlap_end) }))
    .filter((a) => Number.isFinite(a.x1) && Number.isFinite(a.x2));

  return (
    <div data-testid="trends-stitch-chart">
      <div style={{ width: "100%", height: 280 }}>
        <ResponsiveContainer>
          <LineChart data={rows} margin={{ top: 8, right: 6, left: 6, bottom: 0 }}>
            <CartesianGrid {...chartGridProps} />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              tickFormatter={formatUtcDate}
              tick={chartAxisTick}
              {...chartAxisLine}
              minTickGap={44}
            />
            {/* Rescaled values can exceed 100, so no fixed upper bound. */}
            <YAxis domain={[0, "auto"]} tick={chartAxisTick} {...chartAxisLine} width={38} />
            <Tooltip
              contentStyle={chartTooltipStyle}
              labelFormatter={formatUtcDate}
              cursor={chartCursorProps}
            />
            {areas.map((a) => (
              <ReferenceArea
                key={`seam-${a.index}`}
                x1={a.x1}
                x2={a.x2}
                fill={theme.caution}
                fillOpacity={0.14}
                stroke="none"
                ifOverflow="hidden"
              />
            ))}
            {wins.map((w, i) => (
              <Line
                key={`r${i}`}
                dataKey={`r${i}`}
                name={`Rescaled window ${w.index + 1}`}
                type="monotone"
                stroke={theme.textMuted}
                strokeWidth={1}
                strokeOpacity={0.7}
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
            {hasStitched && (
              <Line
                dataKey="stitched"
                name="Stitched"
                type="monotone"
                stroke={theme.accent}
                strokeWidth={2.5}
                dot={false}
                activeDot={{ r: 4, fill: theme.surface, stroke: theme.accent, strokeWidth: 2 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div style={{ color: theme.textMuted, fontSize: "var(--t-micro)", marginTop: "var(--s-1)" }}>
        Bold line: stitched series. Thin lines: rescaled windows. Shaded bands: overlap spans used to rescale.
      </div>
    </div>
  );
}

export default TrendsStitchChart;
