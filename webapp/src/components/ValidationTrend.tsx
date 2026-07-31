import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import type { ValidationTrendSnapshot } from "../api/types";
import { useApi } from "../hooks/useApi";
import { DeployableBadge, ErrorState, Loading, Select, Table } from "./ui";
import { chartAxisLine, chartAxisTick, chartGridProps, chartTooltipStyle } from "./charts";
import { seriesColor, theme } from "../theme";
import { fmtDate, fmtNum, fmtPct } from "../format";

/**
 * ValidationTrend — the CROSS-STRATEGY counterpart to `StrategyHealth`'s
 * per-Pilot cards, ported from the legacy Streamlit Command Center's Safety
 * tab (`gui/panels/gravity_audit.py::_render_validation_stress_regime_section`).
 *
 * `StrategyHealth`'s cards are scoped to catalog Pilots only (one card per
 * `pilots.catalog` entry, joined on `validation_strategy_id`); a strategy
 * validated by `validation.harness` but not yet wired to any Pilot is
 * invisible there. This component instead renders `GET
 * /strategy/validation-trend`'s strategy sections, each backed by every
 * `reports/*_validation_summary.json` on disk regardless of Pilot mapping:
 *
 * 1. A flat table of every validated strategy's current gate snapshot.
 * 2. A multi-strategy, metric-selectable run-over-run trend chart (PBO/DSR/
 *    Sharpe/Max Drawdown) — only strategies with >= 2 recorded harness runs
 *    are plotted (CONSTRAINT #4: never a fabricated single-point trend).
 *
 * (The endpoint also returns a macro-regime transition timeline; this
 * component deliberately does not render it — the operator-facing macro
 * regime control lives on the Mission Control tab's `MacroGateControl`,
 * where turning the gate on/off actually does something, and CLAUDE.md's
 * `MACRO_REGIME_GATE_ENABLED` bullet documents that control.)
 *
 * Each section degrades independently with its own honest `*_reason` string
 * when its underlying data doesn't exist yet — an empty section is never
 * silently hidden, it renders its `reason` (CONSTRAINT #4/#6).
 */

type MetricKey = "pbo" | "dsr" | "sharpe" | "max_drawdown";

const METRIC_LABELS: Record<MetricKey, string> = {
  pbo: "PBO",
  dsr: "DSR",
  sharpe: "Sharpe",
  max_drawdown: "Max Drawdown",
};

const METRIC_OPTIONS: { value: MetricKey; label: string }[] = (
  Object.keys(METRIC_LABELS) as MetricKey[]
).map((key) => ({ value: key, label: METRIC_LABELS[key] }));

function fmtGateNum(key: MetricKey, value: number | null): string {
  if (value == null) return "—";
  if (key === "max_drawdown") return fmtPct(value, 0, { fromFraction: true });
  return fmtNum(value, 2);
}

export function ValidationTrend() {
  const { data, loading, error, status, reload } = useApi<ValidationTrendSnapshot>(
    () => api.getValidationTrend(),
    []
  );
  const [metric, setMetric] = useState<MetricKey>("dsr");

  const strategiesWithTrend = useMemo(
    () => (data ? Object.keys(data.trend).sort() : []),
    [data]
  );

  // Merge every strategy's trend points onto one shared date axis so recharts
  // can render N lines on a single <LineChart>. A strategy missing a point on
  // a given date simply has no key that day (connectNulls bridges the gap).
  const chartData = useMemo(() => {
    if (!data || strategiesWithTrend.length === 0) return [];
    const dateSet = new Set<string>();
    strategiesWithTrend.forEach((sid) => {
      data.trend[sid].forEach((p) => {
        if (p.report_date) dateSet.add(p.report_date);
      });
    });
    const dates = Array.from(dateSet).sort();
    return dates.map((date) => {
      const row: Record<string, string | number> = { date };
      strategiesWithTrend.forEach((sid) => {
        const point = data.trend[sid].find((p) => p.report_date === date);
        const v = point?.[metric];
        if (v != null) row[sid] = v;
      });
      return row;
    });
  }, [data, strategiesWithTrend, metric]);

  if (loading) return <Loading lines={3} />;
  if (error || !data) {
    return <ErrorState message={error ?? "No data"} status={status} onRetry={reload} />;
  }

  return (
    <>
      <section
        className="card card-pad"
        style={{ marginBottom: "var(--s-4)" }}
        data-testid="validation-trend-strategies"
      >
        <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-1)" }}>All validated strategies</h2>
        <p style={{ margin: "0 0 var(--s-3)", fontSize: "var(--t-body)", color: theme.textMuted }}>
          Every validated strategy on disk, including ones not yet wired to a Pilot above.
        </p>
        {data.strategies.length === 0 ? (
          <div className="empty" data-testid="validation-trend-strategies-empty">
            {data.strategies_reason ?? "No validated strategies yet."}
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <Table>
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Status</th>
                  <th className="num">PBO</th>
                  <th className="num">DSR</th>
                  <th className="num">Sharpe</th>
                  <th className="num">Max DD</th>
                  <th>Stress gate</th>
                  <th>Report date</th>
                </tr>
              </thead>
              <tbody>
                {data.strategies.map((s) => (
                  <tr key={s.strategy_id} data-testid={`validation-trend-row-${s.strategy_id}`}>
                    <td style={{ fontWeight: 600 }}>{s.strategy_id}</td>
                    <td>
                      <DeployableBadge deployable={s.deployable} />
                    </td>
                    <td className="num">{fmtGateNum("pbo", s.pbo)}</td>
                    <td className="num">{fmtGateNum("dsr", s.dsr)}</td>
                    <td className="num">{fmtGateNum("sharpe", s.sharpe)}</td>
                    <td className="num">
                      {fmtGateNum("max_drawdown", s.max_drawdown)}
                    </td>
                    <td>
                      {!s.is_options_selling
                        ? "n/a"
                        : s.stress_gate_passed == null
                          ? "—"
                          : s.stress_gate_passed
                            ? "✓ passed"
                            : "✗ failed"}
                    </td>
                    <td style={{ color: theme.textMuted }}>{s.report_date ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
        )}
      </section>

      <section
        className="card card-pad"
        style={{ marginBottom: "var(--s-4)" }}
        data-testid="validation-trend-chart"
      >
        <div
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-1)", gap: "var(--s-2)" }}
        >
          <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Validation trend across strategies</h2>
          {strategiesWithTrend.length > 0 && (
            <div style={{ minWidth: 120 }}>
              <Select
                label="Trend metric"
                hideLabel
                value={metric}
                onChange={(e) => setMetric(e.target.value as MetricKey)}
                options={METRIC_OPTIONS}
                testId="validation-trend-metric-select"
              />
            </div>
          )}
        </div>
        <p style={{ margin: "0 0 var(--s-3)", fontSize: "var(--t-body)", color: theme.textMuted }}>
          One point per harness run; a strategy needs at least 2 recorded runs before it appears.
        </p>
        {strategiesWithTrend.length === 0 ? (
          <div className="empty" data-testid="validation-trend-chart-empty">
            {data.trend_reason ?? "No run-over-run history yet."}
          </div>
        ) : (
          <div style={{ height: 240 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
                <CartesianGrid {...chartGridProps} />
                <XAxis dataKey="date" tickFormatter={fmtDate} tick={chartAxisTick} {...chartAxisLine} />
                <YAxis tick={chartAxisTick} {...chartAxisLine} />
                <Tooltip
                  contentStyle={chartTooltipStyle}
                  labelStyle={{ color: theme.textSecondary, fontSize: "var(--t-micro)" }}
                  itemStyle={{ fontSize: "var(--t-micro)" }}
                  labelFormatter={(d) => fmtDate(String(d))}
                />
                <Legend wrapperStyle={{ fontSize: "var(--t-micro)" }} />
                {strategiesWithTrend.map((sid, index) => (
                  <Line
                    key={sid}
                    type="monotone"
                    dataKey={sid}
                    // Beyond SERIES_PALETTE's 3 validated hues, a strategy folds
                    // to theme.textMuted rather than cycling colors (CVD-unsafe
                    // repeats would make two DIFFERENT strategies look like the
                    // same line) — the Legend above and per-point Tooltip still
                    // name every strategy, so identity is never lost, just color.
                    stroke={seriesColor(index)}
                    strokeWidth={2}
                    dot={false}
                    connectNulls
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>
    </>
  );
}
