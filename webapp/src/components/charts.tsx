import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Line,
} from "recharts";
import type { CurvePoint, EquityDrawdownPoint, SectorSlice } from "../api/types";
import { sectorColor, theme } from "../theme";
import { fmtDate, fmtPct } from "../format";

/**
 * PerfLine — indexed equity curve. Single series colored by overall direction
 * (green up / red down), with an optional recessive benchmark line. 2px marks,
 * hairline grid, crosshair tooltip — per the dataviz skill.
 *
 * When `data` is null the caller renders an honest "no backtest series" panel;
 * this component only draws a real series.
 */
export function PerfLine({
  data,
  benchmark,
  macroBenchmark,
}: {
  data: CurvePoint[];
  benchmark?: CurvePoint[] | null;
  // SEPARATE, explicitly-labeled SPY (broad-market) overlay — distinct from
  // `benchmark` (the strategy's own underlying). Omitted/null renders no line.
  macroBenchmark?: CurvePoint[] | null;
}) {
  if (data.length === 0) return null;
  const first = data[0].value;
  const last = data[data.length - 1].value;
  const up = last >= first;
  const stroke = up ? theme.growth : theme.decline;
  const gradId = up ? "gradUp" : "gradDown";

  // merge benchmark + macro overlay onto the same date axis for one <AreaChart>
  const benchMap = new Map((benchmark ?? []).map((p) => [p.date, p.value]));
  const macroMap = new Map((macroBenchmark ?? []).map((p) => [p.date, p.value]));
  const merged = data.map((p) => ({
    date: p.date,
    value: p.value,
    bench: benchMap.get(p.date) ?? null,
    macro: macroMap.get(p.date) ?? null,
  }));

  const values = data.map((d) => d.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = (max - min) * 0.08 || 1;

  return (
    <div style={{ width: "100%", height: 200 }}>
      <ResponsiveContainer>
        <AreaChart
          data={merged}
          margin={{ top: 8, right: 6, left: 6, bottom: 0 }}
        >
          <defs>
            <linearGradient id="gradUp" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={theme.growth} stopOpacity={0.28} />
              <stop offset="100%" stopColor={theme.growth} stopOpacity={0} />
            </linearGradient>
            <linearGradient id="gradDown" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={theme.decline} stopOpacity={0.28} />
              <stop offset="100%" stopColor={theme.decline} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid
            vertical={false}
            stroke="rgba(255,255,255,0.06)"
            strokeDasharray="0"
          />
          <XAxis
            dataKey="date"
            tickFormatter={fmtDate}
            tick={{ fill: theme.textMuted, fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            minTickGap={44}
          />
          <YAxis
            domain={[min - pad, max + pad]}
            tick={{ fill: theme.textMuted, fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            width={34}
            tickFormatter={(v: number) => v.toFixed(0)}
          />
          <Tooltip
            contentStyle={{
              background: theme.surface3,
              border: `1px solid ${theme.borderStrong}`,
              borderRadius: 10,
              color: theme.textPrimary,
              fontSize: 12,
            }}
            labelFormatter={(l) => fmtDate(String(l))}
            formatter={(val: number, name: string) => [
              val.toFixed(2),
              name === "value"
                ? "Pilot"
                : name === "macro"
                  ? "S&P 500"
                  : "Benchmark",
            ]}
          />
          {benchmark && benchmark.length > 0 && (
            <Line
              type="monotone"
              dataKey="bench"
              stroke={theme.textMuted}
              strokeWidth={1.5}
              strokeDasharray="4 4"
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          )}
          {macroBenchmark && macroBenchmark.length > 0 && (
            <Line
              type="monotone"
              dataKey="macro"
              stroke={theme.accent}
              strokeWidth={1.5}
              strokeDasharray="2 3"
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          )}
          <Area
            type="monotone"
            dataKey="value"
            stroke={stroke}
            strokeWidth={2}
            fill={`url(#${gradId})`}
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * SectorDonut — sector allocation ring. Categorical palette (validated on the
 * dark surface), a 2px surface gap between slices, per-slice hover tooltip, and
 * a direct-label legend list so identity is never color-alone.
 */
export function SectorDonut({ slices }: { slices: SectorSlice[] }) {
  if (slices.length === 0) return null;
  const rows = slices.map((s, i) => ({ ...s, color: sectorColor(i) }));

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ width: 148, height: 148, flex: "0 0 auto" }}>
        <ResponsiveContainer>
          <PieChart>
            <Pie
              data={rows}
              dataKey="weight"
              nameKey="sector"
              innerRadius={44}
              outerRadius={68}
              paddingAngle={2}
              stroke={theme.surface}
              strokeWidth={2}
              isAnimationActive={false}
            >
              {rows.map((r) => (
                <Cell key={r.sector} fill={r.color} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                background: theme.surface3,
                border: `1px solid ${theme.borderStrong}`,
                borderRadius: 10,
                color: theme.textPrimary,
                fontSize: 12,
              }}
              formatter={(val: number, name: string) => [
                fmtPct(val, 1, { fromFraction: true }),
                name,
              ]}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul
        style={{
          listStyle: "none",
          margin: 0,
          padding: 0,
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: 6,
          minWidth: 0,
        }}
      >
        {rows.map((r) => (
          <li
            key={r.sector}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontSize: 12.5,
            }}
          >
            <span
              aria-hidden
              style={{
                width: 10,
                height: 10,
                borderRadius: 3,
                background: r.color,
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
              {r.sector}
            </span>
            <span
              className="num"
              style={{ marginLeft: "auto", color: theme.textPrimary, fontWeight: 600 }}
            >
              {fmtPct(r.weight, 0, { fromFraction: true })}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * DrawdownArea — running peak-to-trough drawdown % beneath a hairline zero
 * line. Always decline-toned (a drawdown is never a "good" direction, unlike
 * PerfLine which flips color by net series direction). `drawdown` values are
 * fractions <= 0 (e.g. -0.146 = -14.6%).
 */
export function DrawdownArea({ data }: { data: EquityDrawdownPoint[] }) {
  if (data.length === 0) return null;
  const rows = data.map((p) => ({ date: p.date, drawdown: p.drawdown }));
  const min = Math.min(0, ...rows.map((r) => r.drawdown));

  return (
    <div style={{ width: "100%", height: 120 }}>
      <ResponsiveContainer>
        <AreaChart data={rows} margin={{ top: 4, right: 6, left: 6, bottom: 0 }}>
          <defs>
            <linearGradient id="gradDrawdown" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={theme.decline} stopOpacity={0} />
              <stop offset="100%" stopColor={theme.decline} stopOpacity={0.32} />
            </linearGradient>
          </defs>
          <CartesianGrid
            vertical={false}
            stroke="rgba(255,255,255,0.06)"
            strokeDasharray="0"
          />
          <XAxis
            dataKey="date"
            tickFormatter={fmtDate}
            tick={{ fill: theme.textMuted, fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            minTickGap={44}
          />
          <YAxis
            domain={[min || -0.01, 0]}
            tick={{ fill: theme.textMuted, fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            width={38}
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
          />
          <Tooltip
            contentStyle={{
              background: theme.surface3,
              border: `1px solid ${theme.borderStrong}`,
              borderRadius: 10,
              color: theme.textPrimary,
              fontSize: 12,
            }}
            labelFormatter={(l) => fmtDate(String(l))}
            formatter={(val: number) => [fmtPct(val, 1, { fromFraction: true }), "Drawdown"]}
          />
          <Area
            type="monotone"
            dataKey="drawdown"
            stroke={theme.decline}
            strokeWidth={1.75}
            fill="url(#gradDrawdown)"
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Tiny inline sparkline for marketplace cards. */
export function Sparkline({
  data,
  positive,
}: {
  data: CurvePoint[];
  positive: boolean;
}) {
  if (data.length === 0) return null;
  const stroke = positive ? theme.growth : theme.decline;
  return (
    <div style={{ width: "100%", height: 40 }}>
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="spark" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.25} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area
            type="monotone"
            dataKey="value"
            stroke={stroke}
            strokeWidth={1.75}
            fill="url(#spark)"
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
