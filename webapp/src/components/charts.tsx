import {
  Area,
  AreaChart,
  Bar as RBar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Line,
} from "recharts";
import type { Bar, CurvePoint, EquityDrawdownPoint, ForecastAttention, SectorSlice } from "../api/types";
import { sectorColor, theme } from "../theme";
import { fmtDate, fmtPct } from "../format";
import { InfoTip } from "./ui";

/**
 * Shared chart chrome — the grid/axis/tooltip config every Recharts chart in
 * this app should use, so a new chart never re-derives its own gridline
 * stroke or a subtly-different tooltip surface. Before these existed, 6
 * screens/components each hand-declared their own version of these four
 * things; 5 of them ended up disagreeing with what THIS file (and
 * index.css's own `.recharts-default-tooltip` override) already used.
 * Spread these directly onto the corresponding Recharts prop — they're
 * plain prop-shaped objects, not components.
 */
export const chartAxisTick = { fill: theme.textMuted, fontSize: 10 } as const;

/** Spread onto <XAxis>/<YAxis> — the axisLine/tickLine pair every axis in this file turns off. */
export const chartAxisLine = { axisLine: false, tickLine: false } as const;

/** Spread onto <CartesianGrid>. */
export const chartGridProps = {
  vertical: false,
  stroke: theme.chartGrid,
  strokeDasharray: "0",
} as const;

/** Pass as <Tooltip contentStyle={chartTooltipStyle}>. */
export const chartTooltipStyle = {
  background: theme.surface3,
  border: `1px solid ${theme.borderStrong}`,
  borderRadius: 10,
  color: theme.textPrimary,
  fontSize: "var(--t-caption)",
} as const;

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
  macroLabel = "S&P 500",
  macroSecondaryAxis = false,
  valueLabel = "Pilot",
  yTickDecimals = 0,
}: {
  data: CurvePoint[];
  benchmark?: CurvePoint[] | null;
  // SEPARATE, explicitly-labeled SPY (broad-market) overlay — distinct from
  // `benchmark` (the strategy's own underlying). Omitted/null renders no line.
  macroBenchmark?: CurvePoint[] | null;
  // Tooltip name for the `macroBenchmark` series -- defaults to "S&P 500"
  // (the original, only caller, PilotDetail.tsx's SPY overlay) so existing
  // usages are unaffected. Pass an explicit label when the series isn't
  // actually SPY (e.g. Portfolio.tsx's buying-power overlay, G14) so the
  // tooltip never mislabels the value.
  macroLabel?: string;
  // `macroBenchmark` defaults to sharing the primary (left) Y-axis, correct
  // when it's on the same scale as `data` (PilotDetail.tsx's indexed-to-100
  // SPY overlay). Set true when the two series are on genuinely different
  // scales (e.g. Portfolio.tsx's raw-dollar buying-power overlay vs. raw-
  // dollar equity, G14) so `macroBenchmark` gets its OWN right-side axis and
  // auto-scaled domain -- without this, a much-smaller series would render
  // clipped flat against the bottom of the primary axis's fixed domain.
  macroSecondaryAxis?: boolean;
  // Tooltip name for the primary series — defaults to "Pilot" (the original,
  // only caller) so existing usages are unaffected. Pass an explicit label for
  // non-Pilot series (e.g. "Beta") so the tooltip never mislabels the value.
  valueLabel?: string;
  // Y-axis tick decimal places — 0 fits a base-100 indexed curve; a series
  // with a narrow range (e.g. beta, typically 0-2) needs more precision.
  yTickDecimals?: number;
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

  const macroValues = (macroBenchmark ?? []).map((p) => p.value);
  const macroMin = macroValues.length ? Math.min(...macroValues) : 0;
  const macroMax = macroValues.length ? Math.max(...macroValues) : 0;
  const macroPad = (macroMax - macroMin) * 0.08 || 1;

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
          <CartesianGrid {...chartGridProps} />
          <XAxis
            dataKey="date"
            tickFormatter={fmtDate}
            tick={chartAxisTick}
            {...chartAxisLine}
            minTickGap={44}
          />
          <YAxis
            yAxisId="left"
            domain={[min - pad, max + pad]}
            tick={chartAxisTick}
            {...chartAxisLine}
            width={34}
            tickFormatter={(v: number) => v.toFixed(yTickDecimals)}
          />
          {macroSecondaryAxis && macroBenchmark && macroBenchmark.length > 0 && (
            <YAxis
              yAxisId="right"
              orientation="right"
              domain={[macroMin - macroPad, macroMax + macroPad]}
              tick={chartAxisTick}
              {...chartAxisLine}
              width={34}
              tickFormatter={(v: number) => v.toFixed(yTickDecimals)}
            />
          )}
          <Tooltip
            contentStyle={chartTooltipStyle}
            labelFormatter={(l) => fmtDate(String(l))}
            formatter={(val, name) => [
              Number(val).toFixed(2),
              name === "value"
                ? valueLabel
                : name === "macro"
                  ? macroLabel
                  : "Benchmark",
            ]}
          />
          {benchmark && benchmark.length > 0 && (
            <Line
              yAxisId="left"
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
              yAxisId={macroSecondaryAxis ? "right" : "left"}
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
            yAxisId="left"
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
    <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
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
              contentStyle={chartTooltipStyle}
              formatter={(val, name) => [
                fmtPct(Number(val), 1, { fromFraction: true }),
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
          gap: "var(--s-1-5)",
          minWidth: 0,
        }}
      >
        {rows.map((r) => (
          <li
            key={r.sector}
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
          <CartesianGrid {...chartGridProps} />
          <XAxis
            dataKey="date"
            tickFormatter={fmtDate}
            tick={chartAxisTick}
            {...chartAxisLine}
            minTickGap={44}
          />
          <YAxis
            domain={[min || -0.01, 0]}
            tick={chartAxisTick}
            {...chartAxisLine}
            width={38}
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
          />
          <Tooltip
            contentStyle={chartTooltipStyle}
            labelFormatter={(l) => fmtDate(String(l))}
            formatter={(val) => [fmtPct(Number(val), 1, { fromFraction: true }), "Drawdown"]}
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

/**
 * Candle — custom shape for one OHLC bar inside a Recharts `<Bar dataKey="range">`
 * whose value is the `[low, high]` pair. Recharts hands this shape `y = pixel(high)`
 * and `height = pixel(low) - pixel(high)` (see recharts Bar.js: for an array value
 * it maps `[scale(low), scale(high)]`), so we reconstruct the local value→pixel
 * transform from that known pair and place the open→close body inside it.
 *
 * Rows without a full OHLC payload (the "now" anchor + forecast rows carry no
 * o/h/l/c) render nothing — nulls are never drawn as a zero-height bar at the
 * axis floor (CONSTRAINT #4).
 */
function Candle(props: {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  payload?: {
    o?: number | null;
    h?: number | null;
    l?: number | null;
    c?: number | null;
  };
}) {
  const { x, y, width, height, payload } = props;
  const o = payload?.o;
  const h = payload?.h;
  const l = payload?.l;
  const c = payload?.c;
  if (
    o == null ||
    h == null ||
    l == null ||
    c == null ||
    typeof x !== "number" ||
    typeof y !== "number" ||
    typeof width !== "number" ||
    typeof height !== "number" ||
    Number.isNaN(y) ||
    Number.isNaN(height)
  ) {
    return null;
  }
  const span = h - l;
  // pixel(h) = y (top of the wick), pixel(l) = y + height (bottom of the wick).
  const pxOf = (v: number) => (span > 0 ? y + (height * (h - v)) / span : y);
  const cx = x + width / 2;
  const up = c >= o;
  const color = up ? theme.growth : theme.decline;
  const bodyTop = pxOf(Math.max(o, c));
  const bodyBottom = pxOf(Math.min(o, c));
  const bodyH = Math.max(1, bodyBottom - bodyTop); // >= 1px so a doji stays visible
  const bodyW = Math.max(1, width * 0.6);
  return (
    <g>
      {/* wick: high → low, at the band center */}
      <line x1={cx} y1={y} x2={cx} y2={y + height} stroke={color} strokeWidth={1} />
      {/* body: open → close */}
      <rect x={cx - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH} fill={color} />
    </g>
  );
}

/**
 * ForecastCandleChart — price history as candlesticks + a forward projection
 * line + a confidence cone that widens per horizon, all on ONE continuous date
 * axis. History and future share a single merged data array keyed by `date`.
 *
 * `bars`     — OHLCV history; rows with any null O/H/L/C are skipped, never
 *              plotted at 0 (CONSTRAINT #4).
 * `forecast` — one entry per horizon: `day` = calendar days AFTER the last bar,
 *              `mid` = projected close, `lower`/`upper` = the band (null → the
 *              projection point still draws but adds no cone at that horizon).
 * `height`   — chart height in px (default 260).
 *
 * Renders null when there is nothing priced AND no forecast (the caller shows
 * its own empty state).
 */
export function ForecastCandleChart({
  bars,
  forecast,
  height = 260,
}: {
  bars: Bar[];
  forecast: { day: number; mid: number; lower: number | null; upper: number | null }[];
  height?: number;
}) {
  const priced = bars.filter(
    (b) => b.Open != null && b.High != null && b.Low != null && b.Close != null
  );
  if (priced.length === 0 && forecast.length === 0) return null;

  type Row = {
    date: string;
    o?: number;
    h?: number;
    l?: number;
    c?: number;
    range?: [number, number];
    mid?: number | null;
    coneLower?: number | null;
    coneUpper?: number | null;
    coneBand?: number | null;
  };

  // 1) history candles (OHLC guaranteed non-null by the filter above)
  const rows: Row[] = priced.map((b) => ({
    date: b.date,
    o: b.Open as number,
    h: b.High as number,
    l: b.Low as number,
    c: b.Close as number,
    range: [b.Low as number, b.High as number],
  }));

  const last = priced.length > 0 ? priced[priced.length - 1] : null;
  const lastDate = last ? last.date : new Date().toISOString().slice(0, 10);
  const lastClose = last ? (last.Close as number) : null;

  // 2) "now" anchor so the projection + cone begin exactly at the last close
  //    (cone half-width 0 here → the fan opens from a point).
  if (lastClose != null) {
    rows.push({
      date: lastDate,
      mid: lastClose,
      coneLower: lastClose,
      coneUpper: lastClose,
      coneBand: 0,
    });
  }

  // 3) forecast rows, dated lastDate + `day` CALENDAR days (UTC, same ISO
  //    YYYY-MM-DD shape Bar.date uses).
  const sortedFc = [...forecast].sort((a, b) => a.day - b.day);
  const lastDateMs = new Date(lastDate).getTime();
  for (const f of sortedFc) {
    const date = new Date(lastDateMs + f.day * 86_400_000).toISOString().slice(0, 10);
    const hasBand = f.lower != null && f.upper != null;
    rows.push({
      date,
      mid: f.mid,
      coneLower: hasBand ? f.lower : null,
      coneUpper: hasBand ? f.upper : null,
      coneBand: hasBand ? (f.upper as number) - (f.lower as number) : null,
    });
  }

  // padded y-domain over every priced value: highs/closes/coneUpper/mid up top,
  // lows/coneLower/mid at the floor (same padding idiom as PerfLine).
  const hi: number[] = [];
  const lo: number[] = [];
  for (const b of priced) {
    hi.push(b.High as number);
    lo.push(b.Low as number);
  }
  if (lastClose != null) {
    hi.push(lastClose);
    lo.push(lastClose);
  }
  for (const f of sortedFc) {
    if (f.mid != null) {
      hi.push(f.mid);
      lo.push(f.mid);
    }
    if (f.upper != null) hi.push(f.upper);
    if (f.lower != null) lo.push(f.lower);
  }
  if (hi.length === 0 || lo.length === 0) return null;
  const max = Math.max(...hi);
  const min = Math.min(...lo);
  const pad = (max - min) * 0.08 || 1;

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <ComposedChart data={rows} margin={{ top: 8, right: 6, left: 6, bottom: 0 }}>
          <CartesianGrid {...chartGridProps} />
          <XAxis
            dataKey="date"
            tickFormatter={fmtDate}
            tick={chartAxisTick}
            {...chartAxisLine}
            minTickGap={44}
          />
          <YAxis
            domain={[min - pad, max + pad]}
            // Without this, recharts pulls the axis down to include a 0
            // baseline for the candlestick <Bar> series regardless of the
            // explicit domain above, squashing the real price range into a
            // thin band at the top of the chart.
            allowDataOverflow
            tick={chartAxisTick}
            {...chartAxisLine}
            width={44}
            tickFormatter={(v: number) => v.toFixed(0)}
          />
          <Tooltip
            contentStyle={chartTooltipStyle}
            labelFormatter={(l) => fmtDate(String(l))}
            formatter={(val, name, entry: { payload?: Row }) => {
              const p: Row = entry?.payload ?? { date: "" };
              if (name === "range")
                return [typeof p.c === "number" ? p.c.toFixed(2) : "—", "Close"];
              if (name === "mid")
                return [typeof val === "number" ? val.toFixed(2) : "—", "Forecast"];
              if (name === "coneLower")
                return [typeof val === "number" ? val.toFixed(2) : "—", "Cone low"];
              if (name === "coneBand")
                return [
                  typeof p.coneUpper === "number" ? p.coneUpper.toFixed(2) : "—",
                  "Cone high",
                ];
              return [typeof val === "number" ? val.toFixed(2) : "—", name];
            }}
          />
          {/* Confidence cone — stacked-area trick: an invisible baseline at
              `coneLower` plus a visible band of `coneBand = upper - lower`
              stacked on top. The band grows with horizon, so the cone fans out
              automatically. History rows carry no cone keys → break points
              (recharts treats a null raw value as a gap), so the band draws
              only across the anchor + forecast region. */}
          <Area
            dataKey="coneLower"
            stackId="cone"
            stroke="none"
            fill="transparent"
            connectNulls
            isAnimationActive={false}
            activeDot={false}
          />
          <Area
            dataKey="coneBand"
            stackId="cone"
            stroke={theme.accent}
            strokeOpacity={0.22}
            strokeWidth={1}
            fill={theme.accent}
            fillOpacity={0.12}
            connectNulls
            isAnimationActive={false}
            activeDot={false}
          />
          {/* Candles */}
          <RBar
            dataKey="range"
            shape={<Candle />}
            isAnimationActive={false}
            legendType="none"
          />
          {/* Forward projection */}
          <Line
            dataKey="mid"
            stroke={theme.accent}
            strokeWidth={2}
            dot={{ r: 2.5, fill: theme.accent, stroke: "none" }}
            connectNulls
            isAnimationActive={false}
          />
          {/* "Now" divider at the last real bar */}
          <ReferenceLine x={lastDate} stroke={theme.textMuted} strokeDasharray="3 3" />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * BERT-LLA attention-weight overlay (forecasting/bert_lla.py's
 * LLAAttention) -- one shaded cell per day in the "bert_lla" ablation's
 * lookback window, opacity scaled to that day's alpha relative to the
 * window's max (so the heaviest-weighted day is always clearly visible
 * regardless of how peaked or flat the distribution is this run).
 *
 * A plain flex strip, not a Recharts overlay on the price chart itself:
 * Recharts' <ReferenceArea> on a category axis needs a real x1≠x2 span to
 * render at all, and this component's lookback window (BERT_LLA_WINDOW_
 * SIZE, typically 22 days) rarely aligns 1:1 with the price chart's own
 * (independently-sized, range-toggle-driven) date axis -- a standalone
 * strip sidesteps that whole alignment problem while staying exactly as
 * legible. Renders nothing when `attention` is null/has no weights
 * (CONSTRAINT #4 -- no overlay when the model didn't run).
 */
export function AttentionHeatmapStrip({ attention }: { attention: ForecastAttention | null }) {
  if (!attention || attention.weights.length === 0) return null;
  const maxAlpha = Math.max(...attention.weights.map((w) => w.alpha), 1e-9);
  return (
    <div style={{ marginTop: "var(--s-2-5)" }} data-testid="attention-heatmap-strip">
      <div style={{ color: theme.textMuted, fontSize: "var(--t-micro)", marginBottom: "var(--s-1)" }}>
        {attention.model} attention ({attention.window_size}-day lookback)
      </div>
      <div style={{ display: "flex", gap: "var(--s-0-5)" }}>
        {attention.weights.map((w) => {
          const label = `${w.date}: alpha ${w.alpha.toFixed(3)}`;
          return (
            <InfoTip
              key={w.date}
              content={label}
              ariaLabel={label}
              triggerStyle={{
                flex: 1,
                minWidth: 0,
                height: 16,
                borderRadius: 2,
                background: theme.accent,
                opacity: 0.15 + (w.alpha / maxAlpha) * 0.85,
                border: "none",
                padding: 0,
                cursor: "pointer",
              }}
            />
          );
        })}
      </div>
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
